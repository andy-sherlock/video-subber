"""Persistent user preferences at ~/.video-subber/config.json"""
import json, sys
from pathlib import Path

CONFIG_PATH = Path.home() / ".video-subber" / "config.json"

# V3.09: Whisper 三模型方案的唯一权威来源。worker 校验白名单也引用这里。
VALID_MODELS = ("large-v3-turbo", "large-v2", "large-v3")
DEFAULT_MODEL = "large-v2"
# 已删除的旧型号——老 config.json / 旧 localStorage 里若残留，加载时兜底为默认。
LEGACY_MODELS = ("tiny", "base", "small", "medium")


def coerce_model(model):
    """已删除的旧型号兜底为默认 large-v2；其余原样放行，让校验拦截真正的非法值。"""
    if model in LEGACY_MODELS:
        return DEFAULT_MODEL
    return model


DEFAULT_CONFIG = {
    "translationMode": "speed",
    "translationPriority": ["google", "bing", "youdao"],
    "model": DEFAULT_MODEL,
    "style": {
        "font": "Microsoft YaHei" if sys.platform == "win32" else "PingFang SC",
        "size": 42,
        "color": "white",
        "outlineColor": "black",
        "outlineWidth": 2,
        "bgColor": "transparent",
    },
    "apiKeys": [],
    "savePath": "~/Downloads/Video-Subber",
    # Note: tier intentionally omitted — source of truth is URL param > localStorage > "free"
}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            data = {**DEFAULT_CONFIG, **data}
        except (json.JSONDecodeError, IOError):
            return dict(DEFAULT_CONFIG)
    else:
        return dict(DEFAULT_CONFIG)

    # 老配置里残留的已删型号（如 medium）兜底为 large-v2，避免下游校验报错。
    data["model"] = coerce_model(data.get("model"))
    return data


def save_config(config: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
