"""模型注册表 — 四模型 ready 判定/下载/修复/运行时加载的唯一依据（C3.5 ㉕+㉖+㉘）。

硬规则（㉖）：worker 及所有 runner 不允许硬编码模型仓库名；加载前必须通过
resolve_model_path(model_key) 解析本地路径。当前解析规则：
  非 Whisper 三模型只认自管 ready 目录；Whisper 暂时沿用 model_manager 的旧缓存兼容。
  缺失时直接抛 ModelNotReady 引导一键准备。
禁止运行时静默联网下载（P207 教训：兜底必须可见）。

口径（㉘）：产品是 Windows 端。whisper 沿用 model_manager 的平台分流机制
（_build_models/choose_whisper_backend），其余三个模型双平台同仓库。
预计大小为 ㉗/㉘ 实测值，不是估算。
"""
from __future__ import annotations

import time
from pathlib import Path

from backend import model_manager, model_manifest

MODELS_ROOT = Path.home() / ".video-subber" / "models"

# prepare 队列固定顺序（㉔ 合同一）。qwen_fa 是后端注册表里的模型文件 key；
# /api/env 对前端继续暴露 qwen_aligner，避免改动既有前端合同。
MODEL_KEYS = ("whisper", "qwen_asr", "qwen_fa", "sensevoice")

# 三个非 Whisper 模型：双平台同仓库。probe_file 用于下载完成后的关键文件存在性检查。
_STATIC_ENTRIES = {
    "qwen_asr": {
        "model_key": "qwen_asr",
        "display_name": "识别增强组件",
        "repo": "Qwen/Qwen3-ASR-1.7B",
        "size_bytes": int(4.38 * 1024 ** 3),
        "probe_file": "config.json",
    },
    "qwen_fa": {
        "model_key": "qwen_fa",
        "display_name": "时间轴对齐组件",
        "repo": "Qwen/Qwen3-ForcedAligner-0.6B",
        "size_bytes": int(1.71 * 1024 ** 3),
        "probe_file": "config.json",
    },
    "sensevoice": {
        "model_key": "sensevoice",
        "display_name": "人声分类组件",
        "repo": "iic/SenseVoiceSmall",
        "size_bytes": int(0.88 * 1024 ** 3),
        "probe_file": "model.pt",
    },
}


class ModelNotReady(RuntimeError):
    """模型不在可用本地路径，需要走一键准备。"""


def current_whisper_model() -> str:
    """当前选定的 Whisper 型号（用户配置，兜底默认值）。"""
    try:
        from backend.config_manager import load_config
        return load_config().get("model") or "large-v2"
    except Exception:
        return "large-v2"


def get_entry(model_key: str, whisper_model_id: str | None = None) -> dict:
    """返回注册表条目。whisper 条目委托 model_manager 的平台分流。"""
    if model_key == "whisper":
        mid = whisper_model_id or current_whisper_model()
        if mid not in model_manager.MODELS:
            raise KeyError(f"未知 Whisper 模型：{mid}")
        info = model_manager.MODELS[mid]
        local_dir = model_manager._model_storage_dir(mid)
        return {
            "model_key": "whisper",
            "display_name": "语音识别模型",
            "repo": info["repo"],
            "size_bytes": model_manager._parse_size_to_bytes(info["size"]),
            "local_dir": local_dir,
            "manifest_path": local_dir / model_manifest.MANIFEST_NAME,
            "probe_file": "config.json",
            "whisper_model_id": mid,
        }
    if model_key not in _STATIC_ENTRIES:
        raise KeyError(f"未知模型：{model_key}")
    entry = dict(_STATIC_ENTRIES[model_key])
    entry["local_dir"] = MODELS_ROOT / model_key
    entry["manifest_path"] = entry["local_dir"] / model_manifest.MANIFEST_NAME
    return entry


def write_manifest_v2(model_key: str, model_dir: Path | None = None) -> None:
    """下载/修复成功后写 manifest v2（含逐文件 size+sha256）。"""
    entry = get_entry(model_key)
    target = Path(model_dir or entry["local_dir"])
    payload = {
        "status": "ready",
        "version": 2,
        "model_key": model_key,
        "repo": entry["repo"],
        "created_at": int(time.time()),
        "files": model_manifest.build_files_list(target),
    }
    model_manifest.write_manifest(target, payload)


def is_model_ready(model_key: str, whisper_model_id: str | None = None) -> bool:
    """自管目录是否 ready。ready = manifest 匹配 + 结构校验通过（㉕：包可导入 ≠ ready）。"""
    if model_key == "whisper":
        mid = whisper_model_id or current_whisper_model()
        return model_manager.is_model_ready(mid)
    entry = get_entry(model_key)
    manifest = model_manifest.read_manifest(entry["local_dir"])
    if not manifest or manifest.get("status") != "ready":
        return False
    if manifest.get("repo") != entry["repo"]:
        return False
    return model_manifest.structure_ok(entry["local_dir"], manifest)


def resolve_model_path(model_key: str,
                       whisper_model_id: str | None = None) -> str:
    """运行时加载的唯一入口：只返回本地目录，绝不返回 repo 名。

    P320（Codex 审查阻塞 4）：qwen_asr/qwen_fa/sensevoice 只认自管 ready 目录，
    不再回退旧共享缓存——旧缓存无 manifest 无法校验，本产品未发布也无老用户。
    未就绪直接抛 ModelNotReady 引导一键准备，禁止运行时静默联网下载。
    （Whisper 仍走 model_manager 的 P308 旧缓存回退，暂不动，留版本收尾统一评估。）
    """
    if model_key == "whisper":
        mid = whisper_model_id or current_whisper_model()
        return model_manager.resolve_whisper_model_path(mid)
    entry = get_entry(model_key)
    if is_model_ready(model_key):
        return str(entry["local_dir"])
    raise ModelNotReady(
        f"{entry['display_name']}未就绪，请先在主界面执行一键准备")


def is_model_resolvable(model_key: str,
                        whisper_model_id: str | None = None) -> bool:
    """运行时能否离线加载。prepare/env 与真实加载共用这一判定。"""
    try:
        resolve_model_path(model_key, whisper_model_id)
        return True
    except Exception:
        return False
