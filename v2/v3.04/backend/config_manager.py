"""Persistent user preferences at ~/.video-subber/config.json"""
import json, sys
from pathlib import Path

CONFIG_PATH = Path.home() / ".video-subber" / "config.json"

DEFAULT_CONFIG = {
    "translationMode": "speed",
    "translationPriority": ["google", "bing", "youdao"],
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
            data = {**DEFAULT_CONFIG, **data}
        except (json.JSONDecodeError, IOError):
            return dict(DEFAULT_CONFIG)
    else:
        return dict(DEFAULT_CONFIG)

    # Decrypt API keys that were encrypted at rest
    from .key_manager import decrypt_api_key
    for k in data.get("apiKeys", []):
        if "key" in k and k["key"]:
            try:
                k["key"] = decrypt_api_key(k["key"])
            except Exception:
                pass
    return data


def save_config(config: dict):
    # Encrypt API keys before writing to disk
    from .key_manager import encrypt_api_key
    config = dict(config)
    if "apiKeys" in config:
        config["apiKeys"] = [
            {**k, "key": encrypt_api_key(k["key"])} if k.get("key") else k
            for k in config["apiKeys"]
        ]
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
