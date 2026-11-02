"""Model download manager — ModelScope download + local ready manifest."""
from __future__ import annotations

import json
import logging
import multiprocessing
import re
import shutil
import threading
import time
from pathlib import Path

from backend.core.whisper_runner import MLX_WHISPER_MODELS, choose_whisper_backend

_log = logging.getLogger("model_manager")

# V3.09: 三模型方案。快→慢、低准确度→高准确度 依次排列（前端圆点同序）。
# 下载源按平台分流：Mac(mlx) 下 mlx-community 版，Windows/Linux(faster) 下 faster-whisper 版。
_FASTER_FALLBACK = {
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
}
_SIZES = {"large-v3-turbo": "~1.6GB", "large-v2": "~2.9GB", "large-v3": "~2.9GB"}

MODEL_ROOT = Path.home() / ".video-subber" / "models" / "whisper"
MANIFEST_NAME = "manifest.json"

# 只作为旧缓存兼容读取，不再作为 P308 后的新下载目标。
CACHE_DIR = Path.home() / ".cache" / "huggingface" / "hub"
MODELSCOPE_CACHE_DIR = Path.home() / ".cache" / "modelscope" / "hub"

_MODEL_WEIGHT_SUFFIXES = (".bin", ".safetensors", ".npz", ".pt", ".model")
_DISK_SPACE_BUFFER = 1 * 1024 ** 3
_SPEED_STALE_AFTER = 3.0

# 内存下载状态：model_id → {state, process, stop_event}。
_downloads: dict[str, dict] = {}
_manager = None


class DownloadStopped(RuntimeError):
    """用户点击停止下载后，下载子进程内部抛出的中断信号。"""


def _get_manager():
    global _manager
    if _manager is None:
        _manager = multiprocessing.Manager()
    return _manager


def _download_state(download_entry):
    """同时兼容新的子进程状态包装和旧测试里的普通 dict。"""
    if not download_entry:
        return {}
    return download_entry.get("state", download_entry)


def _faster_repo(mid: str) -> str:
    """faster 端下载源取自 faster-whisper 内置表，与 WhisperModel(name) 实际加载源一致。"""
    try:
        from faster_whisper.utils import _MODELS
        if mid in _MODELS:
            return _MODELS[mid]
    except Exception:
        pass
    return _FASTER_FALLBACK[mid]


def _build_models(backend: str) -> dict:
    """按 Whisper 后端选下载源。mlx 复用 whisper_runner 映射，faster 读库内置表。"""
    if backend == "mlx":
        return {mid: {"name": mid, "repo": MLX_WHISPER_MODELS[mid], "size": _SIZES[mid]} for mid in _SIZES}
    return {mid: {"name": mid, "repo": _faster_repo(mid), "size": _SIZES[mid]} for mid in _SIZES}


MODELS = _build_models(choose_whisper_backend())


def _model_storage_dir(model_id: str, backend: str | None = None) -> Path:
    """本项目自己的 ready 模型目录。只有这个目录写 manifest。"""
    return MODEL_ROOT / (backend or choose_whisper_backend()) / model_id


def _manifest_path(model_dir: Path) -> Path:
    return model_dir / MANIFEST_NAME


def _friendly_error(raw: str) -> str:
    """把下载的原始技术错误翻译成用户能看懂的人话。"""
    low = (raw or "").lower()
    if "proxy" in low:
        return "网络代理连接失败，请关闭代理软件后重试，或检查代理设置"
    if "timeout" in low or "timed out" in low:
        return "下载超时，请检查网络后重试"
    if "space" in low or "errno 28" in low:
        return "磁盘空间不足，请清理后重试"
    if "permission" in low or "denied" in low or "errno 13" in low:
        return "没有写入权限，无法保存模型文件"
    if "connection" in low or "max retries" in low or "newconnectionerror" in low or "refused" in low:
        return "无法连接到下载服务器，请检查网络后重试"
    if "modelscope" in low and ("no module" in low or "import" in low):
        return "缺少 ModelScope 下载组件，请先安装依赖后重试"
    return "下载失败，请检查网络后重试"


def _parse_size_to_bytes(size_str: str) -> int:
    """'~1.6GB' → 字节数。用于下载前磁盘检查和进度百分比。"""
    m = re.search(r"[\d.]+", size_str or "")
    if not m:
        return 0
    return int(float(m.group()) * 1024 ** 3)


def _dir_size_bytes(path: Path) -> int:
    """目录下所有文件的字节总和；保留给测试和人工排查磁盘占用使用。"""
    total = 0
    if path.exists():
        for f in path.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                pass
    return total


def _compute_progress(downloaded_bytes: int, expected_bytes: int) -> int:
    """真实百分比 = 已下载 ÷ 预估总量。封顶 99%，留到下载器返回才置 100。"""
    if expected_bytes <= 0:
        return 0
    return min(99, int(downloaded_bytes / expected_bytes * 100))


def _format_speed(bytes_per_second: float | int | None) -> str:
    """把每秒字节数格式化成前端显示文案。"""
    speed = float(bytes_per_second or 0)
    if speed <= 0:
        return "等待网络"
    mb = speed / 1024 / 1024
    if mb >= 1:
        return f"{mb:.1f} MB/s"
    return f"{speed / 1024:.0f} KB/s"


def _has_required_model_files(model_dir: Path) -> bool:
    """ready 至少需要 config.json 和一个 Whisper 权重文件。"""
    if not model_dir.is_dir():
        return False
    if not (model_dir / "config.json").is_file():
        return False
    for path in model_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in _MODEL_WEIGHT_SUFFIXES:
            return True
    return False


def _read_manifest(model_dir: Path) -> dict | None:
    try:
        return json.loads(_manifest_path(model_dir).read_text(encoding="utf-8"))
    except Exception:
        return None


def _manifest_matches(model_dir: Path, model_id: str, backend: str) -> bool:
    manifest = _read_manifest(model_dir)
    if not isinstance(manifest, dict):
        return False
    metadata_matches = (
        manifest.get("status") == "ready"
        and manifest.get("model_id") == model_id
        and manifest.get("backend") == backend
        and manifest.get("repo") == MODELS[model_id]["repo"]
    )
    if not metadata_matches:
        return False
    from backend import model_manifest
    return (
        model_manifest.manifest_well_formed(manifest)
        and model_manifest.structure_ok(model_dir, manifest)
    )


def is_model_ready(model_id: str, backend: str | None = None) -> bool:
    """只检查本项目 ready 目录。无 manifest 的半下载不能 ready。"""
    if model_id not in MODELS:
        return False
    selected_backend = backend or choose_whisper_backend()
    model_dir = _model_storage_dir(model_id, selected_backend)
    return _manifest_matches(model_dir, model_id, selected_backend) and _has_required_model_files(model_dir)


def is_model_loadable(model_id: str, backend: str | None = None) -> bool:
    """恢复/预检用的判定：与 worker 实际加载同一把尺子（自管 ready 或旧缓存回退）。

    is_model_ready 只认自管目录，用它做恢复检查会把「只在旧缓存」的模型
    误判成缺失（B3）；加载路径 resolve_whisper_model_path 走 _find_ready_model_dir，
    检查口径必须与加载口径一致。
    """
    if model_id not in MODELS:
        return False
    return _find_ready_model_dir(model_id, backend) is not None


def write_ready_manifest(model_id: str, model_dir: Path, backend: str | None = None) -> None:
    """原子写入 ready manifest：先写临时文件，再 rename。

    C3.5 (㉗): 升级为 v2 —— 附带逐文件 size+sha256 指纹（files 字段），
    供 /api/repair 文件级定位坏文件。旧字段保留，_manifest_matches 不受影响。
    """
    from backend import model_manifest

    selected_backend = backend or choose_whisper_backend()
    model_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "ready",
        "version": 2,
        "model_id": model_id,
        "backend": selected_backend,
        "repo": MODELS[model_id]["repo"],
        "created_at": int(time.time()),
        "files": model_manifest.build_files_list(model_dir),
    }
    tmp = model_dir / f"{MANIFEST_NAME}.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_manifest_path(model_dir))


def _legacy_hf_model_dir(model_id: str) -> Path | None:
    """兼容 P308 前已经下载好的 Hugging Face cache，只读，不写。"""
    repo = MODELS[model_id]["repo"]
    candidate = CACHE_DIR / ("models--" + repo.replace("/", "--"))
    snapshots = candidate / "snapshots"
    if snapshots.is_dir():
        for snap in snapshots.iterdir():
            if snap.is_dir() and _has_required_model_files(snap):
                return snap
    if _has_required_model_files(candidate):
        return candidate
    return None


def _legacy_modelscope_model_dir(model_id: str) -> Path | None:
    """兼容旧 ModelScope cache。不同版本目录略有差异，所以只做本地文件查找。"""
    repo = MODELS[model_id]["repo"]
    if not MODELSCOPE_CACHE_DIR.exists():
        return None

    owner, name = repo.split("/", 1)
    candidates = [
        MODELSCOPE_CACHE_DIR / repo,
        MODELSCOPE_CACHE_DIR / owner / name,
        MODELSCOPE_CACHE_DIR / "models" / owner / name,
    ]
    for candidate in candidates:
        if _has_required_model_files(candidate):
            return candidate

    for config_path in MODELSCOPE_CACHE_DIR.rglob("config.json"):
        candidate = config_path.parent
        low = str(candidate).lower()
        if name.lower() in low and _has_required_model_files(candidate):
            return candidate
    return None


def _find_ready_model_dir(model_id: str, backend: str | None = None) -> Path | None:
    selected_backend = backend or choose_whisper_backend()
    own_dir = _model_storage_dir(model_id, selected_backend)
    if is_model_ready(model_id, selected_backend):
        return own_dir

    legacy = _legacy_modelscope_model_dir(model_id)
    if legacy:
        return legacy
    return _legacy_hf_model_dir(model_id)


def _detect_model_dir(model_id: str) -> Path | None:
    """旧函数名保留给已有调用方；语义升级为“找到可加载的本地模型目录”。"""
    if model_id not in MODELS:
        return None
    return _find_ready_model_dir(model_id)


def resolve_whisper_model_path(model_id: str, backend: str | None = None) -> str:
    """给 Whisper 底层库的唯一入口：只返回 ready 本地目录，不返回 repo 名。"""
    if model_id not in MODELS:
        raise RuntimeError(f"未知 Whisper 模型：{model_id}")
    model_dir = _find_ready_model_dir(model_id, backend)
    if not model_dir:
        raise RuntimeError(f"请先下载模型：{MODELS[model_id]['name']}")
    return str(model_dir)


def _has_enough_disk_space(target_dir: Path, expected_bytes: int) -> bool:
    """下载前检查目标磁盘空间，预估大小外加 1GB 缓冲。"""
    probe = target_dir
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    usage = shutil.disk_usage(probe)
    return usage.free >= expected_bytes + _DISK_SPACE_BUFFER


def _make_progress_callback(state: dict, expected_bytes: int, stop_event=None):
    """ModelScope 的 progress_callbacks 适配成前端百分比。"""
    from modelscope.hub.callback import ProgressCallback

    class ModelDownloadProgress(ProgressCallback):
        _lock = threading.Lock()
        _downloaded = 0
        _last_bytes = 0
        _last_at = None

        def update(self, size: int):
            if stop_event is not None and stop_event.is_set():
                raise DownloadStopped("用户已停止下载")
            try:
                inc = int(size or 0)
            except (TypeError, ValueError):
                inc = 0
            if inc <= 0:
                return
            with self._lock:
                now = time.monotonic()
                self.__class__._downloaded += inc
                downloaded = self.__class__._downloaded
                pct = _compute_progress(self.__class__._downloaded, expected_bytes)
                if pct > state.get("progress", 0):
                    state["progress"] = pct
                state["downloaded_bytes"] = downloaded
                last_at = self.__class__._last_at
                if last_at is None:
                    self.__class__._last_at = now
                    self.__class__._last_bytes = downloaded
                    state["speed_bytes_per_sec"] = 0
                    state["speed_text"] = _format_speed(0)
                    state["speed_updated_at"] = now
                    return
                elapsed = now - last_at
                if elapsed >= 0.5:
                    delta = max(0, downloaded - self.__class__._last_bytes)
                    speed = delta / elapsed if elapsed > 0 else 0
                    state["speed_bytes_per_sec"] = speed
                    state["speed_text"] = _format_speed(speed)
                    state["speed_updated_at"] = now
                    self.__class__._last_at = now
                    self.__class__._last_bytes = downloaded

        def end(self):
            return None

    return ModelDownloadProgress


def get_model_status() -> dict:
    """返回三个 Whisper 模型的下载状态。"""
    result = {}
    for mid, info in MODELS.items():
        dl_entry = _downloads.get(mid)
        dl = _download_state(dl_entry)
        process = dl_entry.get("process") if dl_entry and "process" in dl_entry else None
        if dl.get("downloading") and process is not None and not process.is_alive():
            dl["downloading"] = False
        if dl.get("downloading") and dl.get("speed_bytes_per_sec", 0) > 0:
            updated_at = dl.get("speed_updated_at")
            if updated_at is not None and time.monotonic() - updated_at > _SPEED_STALE_AFTER:
                dl["speed_bytes_per_sec"] = 0
                dl["speed_text"] = _format_speed(0)
        own_ready = is_model_ready(mid)
        downloaded = own_ready or _find_ready_model_dir(mid) is not None
        result[mid] = {
            "name": info["name"],
            "size": info["size"],
            "downloaded": downloaded,
            "managed": own_ready,
            "downloading": dl is not None and dl.get("downloading", False),
            "progress": 100 if downloaded else (dl.get("progress", 0) if dl else 0),
            "error": dl.get("error") if dl else None,
            "speed_bytes_per_sec": dl.get("speed_bytes_per_sec", 0) if dl else 0,
            "speed_text": dl.get("speed_text", "") if dl else "",
            "stopping": dl.get("stopping", False) if dl else False,
        }
    return result


def start_download(model_id: str) -> dict:
    """启动一个模型下载子进程，并返回初始状态。"""
    if model_id not in MODELS:
        return {"error": f"Unknown model: {model_id}"}

    if _find_ready_model_dir(model_id):
        return {"ok": True, "already_downloaded": True}

    existing_entry = _downloads.get(model_id)
    existing = _download_state(existing_entry)
    if existing and existing.get("downloading"):
        return {"ok": True, "already_downloading": True, "progress": existing.get("progress", 0)}

    target = _model_storage_dir(model_id)
    expected = _parse_size_to_bytes(MODELS[model_id]["size"])
    if not _has_enough_disk_space(target.parent, expected):
        return {"error": "磁盘空间不足，请清理后重试"}

    state = _get_manager().dict({
        "downloading": True,
            "progress": 0,
            "speed_bytes_per_sec": 0,
            "speed_text": "等待网络",
            "speed_updated_at": time.monotonic(),
        })
    stop_event = multiprocessing.Event()
    process = multiprocessing.Process(
        target=_download_model,
        args=(model_id, state, stop_event),
        daemon=True,
    )
    _downloads[model_id] = {"state": state, "process": process, "stop_event": stop_event}
    process.start()

    return {"ok": True, "progress": 0}


def cancel_download(model_id: str) -> dict:
    """停止进行中的模型下载，保留 partial 文件供下次续传。"""
    entry = _downloads.get(model_id)
    dl = _download_state(entry)
    if dl and dl.get("downloading"):
        dl["stopping"] = True
        dl["downloading"] = False
        dl["speed_bytes_per_sec"] = 0
        dl["speed_text"] = "已停止"
        stop_event = entry.get("stop_event") if entry else None
        if stop_event is not None:
            stop_event.set()
        process = entry.get("process") if entry else None
        if process is not None and process.is_alive():
            process.terminate()
            process.join(timeout=2)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(timeout=1)
        dl["cancelled"] = True
        dl["stopping"] = False
        return {"ok": True, "stopping": True, "message": "已停止下载，下次会继续"}
    return {"ok": True, "not_downloading": True}


def delete_model(model_id: str, backend: str | None = None) -> dict:
    """只删除本应用自管 ready 模型目录，不删除共享缓存。"""
    if model_id not in MODELS:
        return {"error": f"Unknown model: {model_id}"}
    dl = _download_state(_downloads.get(model_id))
    if dl.get("downloading"):
        return {"error": "模型正在下载中，请先停止下载"}
    target = _model_storage_dir(model_id, backend)
    if not target.exists():
        return {"ok": True, "deleted": False}
    try:
        shutil.rmtree(target)
    except PermissionError:
        return {"error": "模型文件正在被占用。请关闭正在处理的任务，或重启 App 后再试。"}
    except OSError as e:
        return {"error": f"删除模型失败：{getattr(e, 'strerror', None) or e}"}
    return {"ok": True, "deleted": True}


def _download_model(model_id: str, state: dict, stop_event=None):
    """子进程下载模型到本项目 ready 目录。"""
    repo = MODELS[model_id]["repo"]
    target = _model_storage_dir(model_id)
    expected = _parse_size_to_bytes(MODELS[model_id]["size"])
    try:
        from modelscope.hub.snapshot_download import snapshot_download

        target.mkdir(parents=True, exist_ok=True)
        progress_callback = _make_progress_callback(state, expected, stop_event)
        snapshot_download(
            model_id=repo,
            local_dir=str(target),
            progress_callbacks=[progress_callback],
        )
        if stop_event is not None and stop_event.is_set():
            raise DownloadStopped("用户已停止下载")
        if not _has_required_model_files(target):
            raise RuntimeError("模型文件不完整：缺少 config.json 或权重文件")
        write_ready_manifest(model_id, target)
        state["progress"] = 100
        state["downloading"] = False
        state["speed_bytes_per_sec"] = 0
        state["speed_text"] = ""
        state.pop("error", None)

    except DownloadStopped:
        state["downloading"] = False
        state["cancelled"] = True
        state["speed_bytes_per_sec"] = 0
        state["speed_text"] = "已停止"
        state.pop("error", None)

    except Exception as e:
        _log.warning("Model download failed for %s: %s", model_id, e)
        state["downloading"] = False
        state["speed_bytes_per_sec"] = 0
        state["speed_text"] = ""
        state["error"] = _friendly_error(str(e))
