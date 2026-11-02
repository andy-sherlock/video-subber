"""G11/P444 第四刀：断句和转录必须读同一份语言决定。

事故前：转录侧硬编码 "auto"，断句侧读 config 的 sourceLanguage/language——
两个互不相通的来源，可以静默不一致，且没有任何人检查。
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _load_worker(monkeypatch):
    from test.test_p390_worker_integration import _load_worker_without_audio_models
    return _load_worker_without_audio_models(monkeypatch)


def test_sentence_groups_no_longer_accept_a_language_argument(monkeypatch):
    """合同：断句改为从 receipt 读同一份决定，**不再接收 language 参数**。"""
    worker = _load_worker(monkeypatch)

    params = inspect.signature(worker._prepare_sentence_groups).parameters
    assert "language" not in params, (
        "断句还在接收外部语言参数——那就是第二个语言来源")


def test_config_language_keys_are_gone(monkeypatch):
    """合同：删 config 的 sourceLanguage 与 language 两个键。

    **两个都删**——留下名字更模糊的 `language` 就是留后门。
    """
    source = Path("backend/worker.py").read_text(encoding="utf-8")
    assert 'config.get("sourceLanguage")' not in source, "断句侧仍在读 config 语言"
    assert '"sourceLanguage": "translate"' not in source, "SETTING_STAGE 仍有该键"

    worker = _load_worker(monkeypatch)
    stage_map_src = inspect.getsource(worker._step_transcribe.__module__ and worker)
    # SETTING_STAGE 里两个语言键都不得再出现
    import re
    block = re.search(r"SETTING_STAGE = \{(.*?)\n    \}", stage_map_src, re.S)
    assert block, "找不到 SETTING_STAGE"
    assert "sourceLanguage" not in block.group(1)
    assert '"language"' not in block.group(1)
