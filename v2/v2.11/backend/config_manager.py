"""Persistent user preferences at ~/.video-subber/config.json"""
import json, sys
from pathlib import Path

CONFIG_PATH = Path.home() / ".video-subber" / "config.json"

DEFAULT_CONFIG = {
    "translationMode": "speed",
    "translationPriority": ["google", "baidu"],
    "model": "medium",
    "style": {
        "font": "Microsoft YaHei" if sys.platform == "win32" else "PingFang SC",
        "size": 20,
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
            return {**DEFAULT_CONFIG, **data}
        except (json.JSONDecodeError, IOError):
            pass
    return dict(DEFAULT_CONFIG)


def save_config(config: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
