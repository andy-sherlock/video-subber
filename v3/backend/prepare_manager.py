"""一键准备 + 一键修复 — /api/prepare、/api/repair 的后端实现（C3.5 ㉔+㉕+㉗）。

prepare：检测四模型（注册表判定），缺哪个下哪个，固定顺序串行下载；
幂等（重复调用只补缺失项）；可停止保留 partial；ffmpeg/yt-dlp 只检测不安装，
缺失归 unfixable。

repair：文件级修复为主方案 —— manifest v2 按 size+sha256 定位坏文件 →
先删坏文件（SDK 不自愈同尺寸坏文件）→ snapshot_download(allow_patterns=[坏文件])
只补 → 重算被补文件哈希复验（allow_patterns 写错文件名 SDK 静默 0 文件）。
整模型重下为兜底：manifest 缺失 / 旧 v1 无 files / 复验仍坏 / 坏的是旧共享缓存
（只读不删，全量下到自管目录）。
"""
from __future__ import annotations

import logging
import multiprocessing
import shutil
import threading
import time
from pathlib import Path

from backend import env_check, model_manager, model_manifest, model_registry

_log = logging.getLogger("prepare_manager")

MODEL_ORDER = model_registry.MODEL_KEYS

# 一键准备的进行中状态（主进程内存；子进程状态挂在 states 里）。
# 顶层 error 目前恒为 None（预留给队列级致命错误位，当前无写入方）；单个模型下载
# 失败写入 states[key]["error"]，经 get_prepare_status 的 models[key].error 曝光给前端。
_prepare = {
    "running": False, "stop": False, "queue": [], "index": 0,
    "current": None, "states": {}, "unfixable": [], "error": None,
    "whisper_model_id": None,
}
# P318（Codex 审查阻塞 2）：prepare / repair / download / delete 的"查状态 +
# 置状态"必须在同一把锁内完成，否则慢校验期间有并发写同一目录的窗口。
_MODEL_OP_LOCK = threading.Lock()
_prepare_lock = _MODEL_OP_LOCK  # 别名，历史调用点沿用

# 一键修复的进行中状态（同一进度通道向前端曝光）
_repair_state: dict = {}

_POLL_INTERVAL_S = 0.3


def _start_thread(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True).start()


def is_model_op_in_progress() -> bool:
    """是否有一键准备或修复正在进行。P2：delete 与这两者互斥，避免并发写同目录。"""
    return bool(_prepare["running"] or _repair_state.get("repairing"))


def delete_model_if_idle(model_id: str) -> dict:
    """delete 的检查+删除必须和 prepare/repair 启动共用同一把锁。"""
    with _MODEL_OP_LOCK:
        if _prepare["running"] or _repair_state.get("repairing"):
            return {"error": "正在准备或修复模型，请等操作完成后再删除模型"}
        return model_manager.delete_model(model_id)


def _snapshot_download(repo: str, target: Path, allow_patterns=None,
                       state: dict | None = None, expected_bytes: int = 0,
                       stop_event=None) -> None:
    """ModelScope snapshot_download 的统一入口（测试在此打桩）。"""
    from modelscope.hub.snapshot_download import snapshot_download

    kwargs = {}
    if state is not None:
        callback = model_manager._make_progress_callback(
            state, expected_bytes, stop_event)
        kwargs["progress_callbacks"] = [callback]
    if allow_patterns:
        kwargs["allow_patterns"] = list(allow_patterns)
    snapshot_download(model_id=repo, local_dir=str(target), **kwargs)


# ═══════════════════════════════════════════════════════════════════
# ⑤ /api/prepare
# ═══════════════════════════════════════════════════════════════════

def assess_environment(whisper_model_id: str | None = None) -> dict:
    """四模型缺失/就绪 + 只检测不安装的外部组件（ffmpeg / yt-dlp）。"""
    missing, ready = [], []
    for key in MODEL_ORDER:
        if model_registry.is_model_resolvable(key, whisper_model_id):
            ready.append(key)
        else:
            missing.append(key)
    unfixable = []
    if not env_check.check_ffmpeg().get("installed"):
        unfixable.append({"key": "ffmpeg",
                          "reason": "缺少视频处理组件 ffmpeg，需要安装"})
    if not env_check.check_ytdlp().get("ready"):
        unfixable.append({"key": "ytdlp",
                          "reason": "缺少在线视频下载组件 yt-dlp，需要安装"})
    return {"missing": missing, "ready": ready, "unfixable": unfixable}


def start_prepare(whisper_model_id: str | None = None) -> dict:
    """检测四模型，缺哪个下哪个。幂等：进行中时重复调用不重复入队。"""
    with _MODEL_OP_LOCK:
        if _repair_state.get("repairing"):
            return {"error": "正在修复模型，请等修复完成后再准备"}
        # 问题2：上一轮修复已结束（repairing=False）但 error/状态残留，清掉，否则本轮
        # 准备结束时前端会误弹"修复失败"。收口点②保证 repair 线程若还在跑则 repairing
        # 一定为 True（原子落态最后一步才翻），故此处清空不会丢正在进行的修复状态。
        if _repair_state and not _repair_state.get("repairing"):
            _repair_state.clear()
        assessment = assess_environment(whisper_model_id)
        if _prepare["running"]:
            remaining = _prepare["queue"][_prepare["index"]:]
            return {"queued": list(remaining),
                    "already_ready": assessment["ready"],
                    "unfixable": assessment["unfixable"], "running": True}
        if not assessment["missing"]:
            _prepare["unfixable"] = assessment["unfixable"]
            return {"queued": [], "already_ready": assessment["ready"],
                    "unfixable": assessment["unfixable"], "running": False}
        _prepare.update({
            "running": True, "stop": False,
            "queue": list(assessment["missing"]), "index": 0,
            "current": None, "states": {},
            "unfixable": assessment["unfixable"], "error": None,
            "whisper_model_id":
                whisper_model_id or model_registry.current_whisper_model(),
        })
    _start_thread(_run_prepare)
    return {"queued": assessment["missing"],
            "already_ready": assessment["ready"],
            "unfixable": assessment["unfixable"], "running": True}


def _run_prepare() -> None:
    """准备线程：按固定顺序串行下载缺失模型。单项失败记录后继续下一项。"""
    try:
        for i, key in enumerate(_prepare["queue"]):
            if _prepare["stop"]:
                break
            _prepare["index"] = i
            _prepare["current"] = key
            try:
                _download_one(key)
            except Exception as e:
                _log.warning("一键准备：%s 下载失败: %s", key, e)
    finally:
        _prepare["running"] = False
        _prepare["current"] = None


def _download_one(key: str) -> None:
    """下载一个模型并等它结束。whisper 走 model_manager 现有机制。"""
    # P319：stop 可能刚好落在项间——进程启动前再查一次，避免下一项白下几 GB
    if _prepare["stop"]:
        return
    if key == "whisper":
        mid = _prepare["whisper_model_id"]
        result = model_manager.start_download(mid)
        if result.get("error"):
            raise RuntimeError(result["error"])
        if result.get("already_downloaded"):
            return
        # P319：stop 可能落在入口检查之后、start_download 之后——再查一次并取消，
        # 否则 Whisper 分支会继续下载。
        if _prepare["stop"]:
            model_manager.cancel_download(mid)
            return
        _prepare["states"][key] = model_manager._downloads.get(mid)
        while True:
            entry = model_manager._downloads.get(mid)
            state = model_manager._download_state(entry)
            process = entry.get("process") if entry else None
            if not state.get("downloading") and (
                    process is None or not process.is_alive()):
                break
            time.sleep(_POLL_INTERVAL_S)
        return

    state = model_manager._get_manager().dict({
        "downloading": True, "progress": 0,
        "speed_bytes_per_sec": 0, "speed_text": "等待网络",
        "speed_updated_at": time.monotonic(),
    })
    stop_event = multiprocessing.Event()
    process = multiprocessing.Process(
        target=_download_registry_model, args=(key, state, stop_event),
        daemon=True)
    # P319：先登记再启动，且启动前后各查一次 stop——保证 stop_prepare 总能找到
    # 这个进程来终止它，不会出现"已登记但没人取消"或"没登记就启动"的空档。
    _prepare["states"][key] = {
        "state": state, "process": process, "stop_event": stop_event}
    if _prepare["stop"]:
        stop_event.set()
        state["downloading"] = False
        state["cancelled"] = True
        return
    process.start()
    if _prepare["stop"]:
        stop_event.set()
    while process.is_alive():
        time.sleep(_POLL_INTERVAL_S)
    process.join()


def _download_registry_model(model_key: str, state: dict,
                             stop_event=None) -> None:
    """子进程：下载一个注册表模型到自管目录，成功后写 manifest v2。"""
    entry = model_registry.get_entry(model_key)
    target = Path(entry["local_dir"])
    try:
        target.mkdir(parents=True, exist_ok=True)
        _snapshot_download(entry["repo"], target, state=state,
                           expected_bytes=entry["size_bytes"],
                           stop_event=stop_event)
        if stop_event is not None and stop_event.is_set():
            raise model_manager.DownloadStopped("用户已停止下载")
        if not (target / entry["probe_file"]).is_file():
            raise RuntimeError("模型文件不完整：缺少关键文件")
        model_registry.write_manifest_v2(model_key, model_dir=target)
        state["progress"] = 100
        state["downloading"] = False
        state["speed_bytes_per_sec"] = 0
        state["speed_text"] = ""
        state.pop("error", None)
    except model_manager.DownloadStopped:
        # 停止保留 partial（P312 语义），不写 ready manifest
        state["downloading"] = False
        state["cancelled"] = True
        state["speed_bytes_per_sec"] = 0
        state["speed_text"] = "已停止"
        state.pop("error", None)
    except Exception as e:
        _log.warning("模型下载失败 %s: %s", model_key, e)
        state["downloading"] = False
        state["speed_bytes_per_sec"] = 0
        state["speed_text"] = ""
        state["error"] = model_manager._friendly_error(str(e))


def stop_prepare() -> dict:
    """停止当前准备下载，保留 partial 供下次续传。"""
    with _prepare_lock:
        if not _prepare["running"]:
            return {"ok": True, "not_running": True}
        _prepare["stop"] = True
        current = _prepare["current"]
    if current == "whisper":
        model_manager.cancel_download(_prepare["whisper_model_id"])
    elif current:
        entry = _prepare["states"].get(current)
        if entry:
            state = entry.get("state")
            if state is not None:
                state["downloading"] = False
                state["cancelled"] = True
                state["speed_bytes_per_sec"] = 0
                state["speed_text"] = "已停止"
            stop_event = entry.get("stop_event")
            if stop_event is not None:
                stop_event.set()
            process = entry.get("process")
            if process is not None and process.is_alive():
                process.terminate()
                process.join(timeout=2)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=1)
    return {"ok": True, "stopping": True,
            "message": "已停止准备，已下载部分已保留"}


def _model_runtime_status(key: str, whisper_model_id: str | None) -> dict:
    """单个模型的状态行（供四模型状态通道）。"""
    entry = model_registry.get_entry(key, whisper_model_id)
    info = {
        "display_name": entry["display_name"],
        "size_bytes": entry["size_bytes"],
        "ready": model_registry.is_model_resolvable(key, whisper_model_id),
        "downloading": False, "progress": 0,
        "speed_text": "", "error": None,
    }
    if key == "whisper":
        mid = entry["whisper_model_id"]
        status = model_manager.get_model_status().get(mid, {})
        info.update({
            "downloading": status.get("downloading", False),
            "progress": status.get("progress", 0),
            "speed_text": status.get("speed_text", ""),
            "error": status.get("error"),
        })
    else:
        state = model_manager._download_state(_prepare["states"].get(key))
        if state:
            info.update({
                "downloading": state.get("downloading", False),
                "progress": state.get("progress", 0),
                "speed_text": state.get("speed_text", ""),
                "error": state.get("error"),
            })
    if info["ready"]:
        info["progress"] = 100
    return info


def get_prepare_status() -> dict:
    """四模型状态：整体进度、当前项、百分比、网速（㉔ 合同一进度通道）。"""
    whisper_model_id = (_prepare.get("whisper_model_id")
                        or model_registry.current_whisper_model())
    models = {}
    total_size = 0
    done_size = 0.0
    for key in MODEL_ORDER:
        info = _model_runtime_status(key, whisper_model_id)
        models[key] = info
        total_size += info["size_bytes"]
        done_size += info["size_bytes"] * (info["progress"] / 100.0)

    current = _prepare["current"]
    current_info = models.get(current) if current else None
    return {
        "running": _prepare["running"],
        "current_item": current_info["display_name"] if current_info else "",
        "item_index": (_prepare["index"] + 1) if _prepare["running"] else 0,
        "total": len(_prepare["queue"]) if _prepare["queue"] else 0,
        "percent": current_info["progress"] if current_info else 0,
        "speed_text": current_info["speed_text"] if current_info else "",
        "overall_percent": int(done_size / total_size * 100) if total_size else 0,
        "unfixable": list(_prepare["unfixable"]),
        "models": models,
        "repair": dict(_repair_state) if _repair_state else None,
    }


# ═══════════════════════════════════════════════════════════════════
# ⑥ /api/repair：文件级优先 + 整模型兜底
# ═══════════════════════════════════════════════════════════════════

def _plan_repair(target: str, whisper_model_id: str | None = None) -> dict:
    """定位坏文件并决定修复模式。同步执行（含全量 sha256，秒级~分钟级）。"""
    entry = model_registry.get_entry(target, whisper_model_id)
    target_dir = Path(entry["local_dir"])
    manifest = model_manifest.read_manifest(target_dir)
    if (manifest and model_manifest.manifest_paths_safe(manifest)
            and model_manifest.manifest_well_formed(manifest)):
        # P317/P318：manifest 路径不安全（../ 篡改）或缺字段（无 size/sha256）→
        # 不可信，落下面整模型兜底，不做文件级 unlink，也不会 KeyError 打断。
        # 文件级主方案：size 先行，size 对上再算 sha256
        bad = model_manifest.find_corrupt_files(target_dir, manifest)
        if not bad:
            return {"status": "already_ready", "entry": entry,
                    "mode": None, "bad_files": [], "download_size_bytes": 0}
        sizes = {f["path"]: f["size"] for f in manifest["files"]}
        return {"mode": "file", "entry": entry, "bad_files": bad,
                "download_size_bytes": sum(sizes[p] for p in bad)}
    # 兜底：manifest 缺失 / 旧 v1 无 files / 坏的是旧共享缓存（只读不删）
    # → 全量下到自管目录 + 写 manifest v2
    return {"mode": "full", "entry": entry, "bad_files": [],
            "download_size_bytes": entry["size_bytes"]}


def _write_manifest_for(entry: dict) -> None:
    if entry["model_key"] == "whisper":
        model_manager.write_ready_manifest(
            entry["whisper_model_id"], Path(entry["local_dir"]))
    else:
        model_registry.write_manifest_v2(
            entry["model_key"], model_dir=Path(entry["local_dir"]))


def _execute_repair(plan: dict, state: dict | None = None) -> None:
    """修复线程体。文件级失败自动转整模型兜底。"""
    if state is None:
        state = _repair_state
    entry = plan["entry"]
    target = Path(entry["local_dir"])
    state.update({"repairing": True, "target": entry["model_key"],
                  "mode": plan["mode"], "ready": False, "error": None,
                  "fallback": False})
    try:
        if plan["mode"] == "file":
            manifest = model_manifest.read_manifest(target)
            # 必须先删坏文件：实测同尺寸坏文件 SDK 会跳过不重下（㉗ SDK 行为 2）
            # P317：safe_member 保证只删模型目录内的文件
            for rel in plan["bad_files"]:
                path = model_manifest.safe_member(target, rel)
                if path.exists():
                    path.unlink()
            _snapshot_download(entry["repo"], target,
                               allow_patterns=list(plan["bad_files"]),
                               state=state,
                               expected_bytes=plan["download_size_bytes"])
            # 必须复验：allow_patterns 写错文件名 SDK 静默 0 文件（㉗ SDK 行为 3）
            still_bad = model_manifest.find_corrupt_files(
                target, manifest, only=plan["bad_files"])
            if not still_bad:
                # 收口点②：先备好 ready/error，最后一步才翻 repairing:False，
                # 堵住 start_prepare 在中间窗口看到"没在修复"就清状态的缝隙。
                state["ready"] = True
                state["error"] = None
                state["repairing"] = False
                return
            _log.warning("文件级修复复验仍坏 %s，转整模型兜底", still_bad)
            state["fallback"] = True
            # 问题1：回调只在新百分比更大时更新（model_manager 进度只增不减），
            # 文件级已把进度推到高位，不归零则兜底的低百分比永远盖不过它，横幅卡死。
            state["progress"] = 0
            state["downloaded_bytes"] = 0
            state["speed_bytes_per_sec"] = 0
            state["speed_text"] = "等待网络"
        # 整模型兜底：删自管目录（若有）→ 全量重下 → 写 manifest v2
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        _snapshot_download(entry["repo"], target, state=state,
                           expected_bytes=entry["size_bytes"])
        _write_manifest_for(entry)
        ready = model_registry.is_model_ready(
            entry["model_key"], entry.get("whisper_model_id"))
        # 收口点②：先备 ready/error，最后翻 repairing:False。
        state["ready"] = ready
        state["error"] = (None if ready
                          else "修复后校验未通过，请检查网络后重试")
        state["repairing"] = False
    except Exception as e:
        _log.warning("修复失败 %s: %s", entry["model_key"], e)
        # 收口点②：先备 error，最后翻 repairing:False。
        state["ready"] = False
        state["error"] = model_manager._friendly_error(str(e))
        state["repairing"] = False


def repair_model(target: str | None = None,
                 whisper_model_id: str | None = None) -> dict:
    """POST /api/repair。前端显式传 target；缺省仅当坏项唯一才执行（㉕）。"""
    if target is not None and target not in MODEL_ORDER:
        return {"error": f"未知模型：{target}"}

    # P318：查互斥 + 占位在同一把锁内完成，占位先于 _plan_repair 的慢 sha256，
    # 否则慢校验期间第二个请求会穿过闸门并发写同目录。
    with _MODEL_OP_LOCK:
        if _prepare["running"] or _repair_state.get("repairing"):
            return {"error": "正在下载/修复中，请等当前操作完成"}

        if target is None:
            broken = [k for k in MODEL_ORDER
                      if not model_registry.is_model_resolvable(k, whisper_model_id)]
            if not broken:
                return {"status": "already_ready"}
            if len(broken) > 1:
                candidates = []
                for key in broken:
                    entry = model_registry.get_entry(key, whisper_model_id)
                    candidates.append({
                        "model_key": key,
                        "display_name": entry["display_name"],
                        "reason": "缺失或损坏",
                        "download_size_bytes": entry["size_bytes"],
                    })
                return {"status": "ambiguous", "candidates": candidates}
            target = broken[0]

        _repair_state.clear()
        _repair_state.update({"repairing": True, "target": target, "mode": None})

    # 锁外跑慢校验；此时 repairing 已置位，任何新请求都会被上面的闸门挡住。
    # P318：慢校验或起线程若抛异常，必须清 _repair_state，否则占位永久卡 repairing，
    # 后续 prepare/repair 被永久拒绝。
    try:
        plan = _plan_repair(target, whisper_model_id)
        if plan.get("status") == "already_ready":
            _repair_state.clear()
            return {"status": "already_ready", "target": target,
                    "download_size_bytes": 0}
        _repair_state["mode"] = plan["mode"]
        _start_thread(_execute_repair, plan, _repair_state)
    except Exception as e:
        _log.warning("修复启动失败 %s: %s", target, e)
        _repair_state.clear()
        return {"error": model_manager._friendly_error(str(e))}
    return {"target": target,
            "download_size_bytes": plan["download_size_bytes"],
            "job_id": f"repair-{target}", "mode": plan["mode"]}
