"""Persistent user preferences at ~/.video-subber/config.json"""
import copy, json, sys
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
    "model": DEFAULT_MODEL,
    "style": {
        "font": "Microsoft YaHei" if sys.platform == "win32" else "PingFang SC",
        "size": 72,
        "color": "white",
        "outlineColor": "black",
        "outlineWidth": 2,
        "bgColor": "transparent",
    },
    "apiKeys": [],
    "savePath": "~/Downloads/Video-Subber",
    # 前端保存的每个键都要在这里有默认值（与 index.html 的 State.settings 相同，测试锁住）：
    # 缺一个，这一项就又会被当成“用户改过”存盘，以后改默认值老用户收不到（P472）。
    "subtitles": "no",
    "outputMode": "burn",
    "contextAware": False,
    "speakerDetect": False,
    "transcriptionTier": "standard",
}

# 配置文件格式版本（P472）。有版本号的文件只存用户改过的项；没有版本号的是旧版本
# 整份写盘的快照，里面的值分不清是用户选的还是当时的默认值。
CONFIG_VERSION = 2
# 旧快照里的 42 是 2026-06-05 到 G12 第三批 B 之间的默认字号，读取时当作没改过（只迁移一次：
# 下次保存就带上版本号，之后手动选的 42 会被保留）。
_LEGACY_DEFAULT_SIZE = 42
_MISSING = object()


def load_config() -> dict:
    """读用户配置：文件里只有改过的项，其余逐项补默认值（`style` 里也逐项补）。

    对任何文件内容都不抛错——抛错会让设置接口报 500，前端照样放行保存，
    下一次保存就会把 API Key 覆盖掉。返回深拷贝，不带版本号。
    """
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):   # 没有文件、读不了、不是 JSON、编码不对
        data = {}
    if not isinstance(data, dict):
        data = {}
    legacy = data.pop("configVersion", None) is None
    style = data.pop("style", None)
    style = style if isinstance(style, dict) else {}
    if legacy and style.get("size") == _LEGACY_DEFAULT_SIZE:
        del style["size"]

    config = copy.deepcopy(DEFAULT_CONFIG)
    config.update(data)
    config["style"].update(style)
    # 老配置里残留的已删型号（如 medium）兜底为 large-v2，避免下游校验报错。
    config["model"] = coerce_model(config.get("model"))
    return config


def save_config(config: dict):
    """只把与默认值不同的项写盘（`style` 逐项比），再由这里写上版本号（P472）。

    整份覆盖旧文件：改回默认值的项就从文件里消失；以后默认值变了，
    没改过这一项的用户自动跟上。请求里带来的版本号不算数。
    """
    data = {key: value for key, value in config.items()
            if key not in ("configVersion", "style") and DEFAULT_CONFIG.get(key, _MISSING) != value}
    style = config.get("style")
    if isinstance(style, dict):
        changed = {key: value for key, value in style.items()
                   if DEFAULT_CONFIG["style"].get(key, _MISSING) != value}
        if changed:
            data["style"] = changed
    data["configVersion"] = CONFIG_VERSION
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
