"""DeepSeek 当前型号兼容：旧配置不能让断句和翻译继续调用已停用型号。"""

import json
import sys
import urllib.request
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_deepseek_default_uses_current_flash_model():
    from backend.translation.deepseek import DeepSeekBackend

    backend = DeepSeekBackend(api_key="test-key")

    assert backend._model == "deepseek-v4-flash"


def test_orchestrator_honors_configured_current_deepseek_model():
    from backend.translation.orchestrator import _build_llm_backends

    backends = _build_llm_backends([
        {
            "platform": "deepseek",
            "key": "test-key",
            "model": "deepseek-v4-pro",
        }
    ])

    assert len(backends) == 1
    assert backends[0]._model == "deepseek-v4-pro"


def test_orchestrator_migrates_retired_deepseek_chat_to_flash():
    from backend.translation.orchestrator import _build_llm_backends

    backends = _build_llm_backends([
        {
            "platform": "deepseek",
            "key": "test-key",
            "model": "deepseek-chat",
        }
    ])

    assert len(backends) == 1
    assert backends[0]._model == "deepseek-v4-flash"


def test_deepseek_translation_request_keeps_legacy_non_thinking_behavior():
    from backend.translation.deepseek import DeepSeekBackend

    backend = DeepSeekBackend(api_key="test-key")
    body = json.loads(backend._build_body("Translate this.", max_tokens=321))

    assert body["thinking"] == {"type": "disabled"}
    assert body["max_tokens"] == 321


def test_deepseek_json_request_is_non_thinking_and_output_bounded(monkeypatch):
    from backend.translation.deepseek import DeepSeekBackend

    captured = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": '{"ok": true}'}}],
            }).encode()

    def _urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    backend = DeepSeekBackend(api_key="test-key")

    assert json.loads(backend.translate_raw_json("Return json.")) == {"ok": True}
    assert captured["body"]["thinking"] == {"type": "disabled"}
    assert captured["body"]["max_tokens"] == 8192
    assert captured["timeout"] == 120

