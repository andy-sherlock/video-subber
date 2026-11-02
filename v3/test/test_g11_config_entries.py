"""G11：三个配置入口必须丢弃两个已废弃的语言字段。

合同施工项 2：**不扩张成完整白名单，只清这两个已废弃字段**。
⑥ 安全审查已确认该决定在本地回环单用户威胁模型下安全。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_LEGACY = {"sourceLanguage": "ja", "language": "ko"}


def test_normalize_job_config_drops_both_language_keys():
    from backend.main import _normalize_job_config

    cfg = _normalize_job_config({**_LEGACY, "model": "large-v2"})
    assert "sourceLanguage" not in cfg
    assert "language" not in cfg, "留下名字更模糊的 language 就是留后门"
    assert cfg["model"] == "large-v2", "只清这两个，别的字段不许动"


def test_saved_config_drops_both_language_keys(monkeypatch):
    """PUT /api/config：旧配置文件里带着这两个键时，不许再写回磁盘。"""
    from backend import main

    saved = {}
    monkeypatch.setattr(
        "backend.config_manager.save_config",
        lambda payload: saved.update(payload))

    import asyncio
    asyncio.run(main.save_config({**_LEGACY, "outputMode": "srt"}))

    assert "sourceLanguage" not in saved
    assert "language" not in saved
    assert saved["outputMode"] == "srt"


def test_upload_entry_drops_both_language_keys():
    """上传入口同样要清——三个入口一个都不能漏。"""
    import inspect
    from backend import main

    src = inspect.getsource(main._prepare_upload_job)
    assert "_normalize_job_config" in src or "_DROPPED_LANGUAGE_KEYS" in src, (
        "上传入口没有走清理，废弃字段会从这里漏进流水线")
