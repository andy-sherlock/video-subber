"""D-1 ⑦.5 审查发现的修复（用户裁决修 R1 / R2 / R3 / R4 / R5 / R7）。

R1 某个 Key「请求被拒 / 认不出的错」→ 换下一个 Key，全部不行才出错；这一轮已判死的 Key 不再重试。
R2 平台回 200 但答复缺正文（Gemini 安全拦截、Claude 空 content）→ 算空答复（格式类），不让任务停。
R3 等过之后请求成功 → 进度文字换回正常的。
R4 门口测试每个 Key 最多 15 秒，超时报「连不上」。
R5 OpenAI 429 + insufficient_quota → 余额不足（不是「太忙」）。
R7 一批里已经试过的 Key 不再整批重发。
"""

from email.message import Message
from io import BytesIO
from pathlib import Path
import json
import sys
import time
import urllib.error

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.translation import orchestrator, paid_request
from backend.translation.base import TranslationResult, classify_translation_failure
from backend.translation.paid_request import PaidAIUnavailable, call_paid


def _http_error(code, body=b""):
    return urllib.error.HTTPError("https://api.example.test", code, "x", Message(), BytesIO(body))


class Script:
    def __init__(self, name, script):
        self.name = name
        self.script = list(script)
        self.calls = 0

    def run(self):
        self.calls += 1
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step()
        return step


def _ok(text="好"):
    return TranslationResult(translated=[text], backend_name="x")


class Waits(list):
    def __call__(self, seconds):
        self.append(seconds)


# ── R1 ─────────────────────────────

def test_r1_request_rejected_on_first_key_tries_next_key():
    a = Script("Claude(x)", [_http_error(400)])
    b = Script("DeepSeek(x)", [_ok("乙")])
    backend, result = call_paid([a, b], lambda k: k.run(), sleep=Waits(), on_wait=lambda *a: None)
    assert backend is b and result.translated == ["乙"]


def test_r1_unknown_on_first_key_tries_next_key():
    a = Script("Gemini(x)", [ValueError("unknown url type: ''")])
    b = Script("DeepSeek(x)", [_ok()])
    backend, _ = call_paid([a, b], lambda k: k.run(), sleep=Waits(), on_wait=lambda *a: None)
    assert backend is b


def test_r1_dead_key_not_retried_after_wait():
    a = Script("Claude(x)", [_http_error(400)])
    b = Script("DeepSeek(x)", [ConnectionError("down"), _ok()])
    waits = Waits()
    backend, _ = call_paid([a, b], lambda k: k.run(), sleep=waits, on_wait=lambda *a: None)
    assert backend is b and list(waits) == [5]
    assert a.calls == 1


def test_r1_all_keys_dead_raises_first_kind():
    a = Script("A", [_http_error(401)])
    b = Script("B", [_http_error(400)])
    with pytest.raises(PaidAIUnavailable) as info:
        call_paid([a, b], lambda k: k.run(), sleep=Waits(), on_wait=lambda *a: None)
    assert info.value.kind == "auth"


def test_r1_all_keys_request_rejected_raises_request_rejected():
    a = Script("A", [_http_error(404)])
    with pytest.raises(PaidAIUnavailable) as info:
        call_paid([a], lambda k: k.run(), sleep=Waits(), on_wait=lambda *a: None)
    assert info.value.kind == "request_rejected"


# ── R2 ─────────────────────────────

class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_r2_gemini_safety_block_is_empty_answer_not_unknown(monkeypatch):
    from backend.translation.gemini import GeminiBackend

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=120: _FakeResponse({"candidates": [{"finishReason": "SAFETY"}]}),
    )
    backend = GeminiBackend(api_key="k")
    raw = backend.translate_raw("hello")
    assert raw.failure_kind == "empty_translation"
    batch = backend.translate_batch(["hello", "world"])
    assert batch.failure_kind not in (None, "unknown")


def test_r2_claude_empty_content_is_empty_answer(monkeypatch):
    from backend.translation.claude import ClaudeBackend

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=120: _FakeResponse({"content": []}),
    )
    raw = ClaudeBackend(api_key="k").translate_raw("hello")
    assert raw.failure_kind == "empty_translation"


# ── R3 ─────────────────────────────

def test_r3_on_resume_called_once_after_recovery():
    a = Script("DeepSeek(x)", [ConnectionError("down"), ConnectionError("down"), _ok()])
    resumed = []
    call_paid([a], lambda k: k.run(), sleep=Waits(), on_resume=lambda: resumed.append(1), on_wait=lambda *a: None)
    assert resumed == [1]


def test_r3_on_resume_not_called_without_wait():
    a = Script("DeepSeek(x)", [_ok()])
    resumed = []
    call_paid([a], lambda k: k.run(), sleep=Waits(), on_resume=lambda: resumed.append(1), on_wait=lambda *a: None)
    assert resumed == []


def test_r3_worker_breaks_restores_progress_text(monkeypatch):
    from backend import worker

    messages = []
    monkeypatch.setattr(worker, "stderr_progress", lambda stage, pct, msg, *a, **k: messages.append(msg))
    monkeypatch.setattr(paid_request, "_wait", lambda s: None)

    class Llm:
        name = "DeepSeek(x)"
        calls = 0

        def translate_raw_json(self, prompt, system_prompt=None):
            Llm.calls += 1
            if Llm.calls == 1:
                raise ConnectionError("down")
            return json.dumps({"break_after_indices": [0]})

    words = [{"word": w, "text": w, "start": i * 0.5, "end": i * 0.5 + 0.3}
             for i, w in enumerate(["One.", "two", "three."])]
    worker._collect_llm_primary_breaks("One. two three.", words, "en", [Llm()])
    assert messages[0] == "正在等待连接 DeepSeek，恢复后自动继续"
    assert messages[-1] == "AI 断句中…"


def test_r3_per_batch_translation_restores_batch_text(monkeypatch):
    monkeypatch.setattr(paid_request, "_wait", lambda s: None)
    monkeypatch.setattr(orchestrator.time, "sleep", lambda s: None)

    class Api:
        name = "DeepSeek(x)"
        calls = 0

        def translate_batch(self, texts, **kw):
            Api.calls += 1
            if Api.calls == 1:
                raise ConnectionError("down")
            return TranslationResult([f"译{i}" for i in range(len(texts))], self.name)

    messages = []
    orchestrator._translate_llm_per_batch(["a b c", "d e f"], [Api()], [],
                                          progress_callback=lambda p, m: messages.append(m))
    wait_at = messages.index("正在等待连接 DeepSeek，恢复后自动继续")
    # P514：睡醒后会再发一次同样的等待文字（先汇报再请求），跳过它看恢复后的那条
    after = [m for m in messages[wait_at:] if m != messages[wait_at]]
    assert after[0].startswith("LLM 逐段翻译…")


# ── R4 ─────────────────────────────

def test_r4_gate_probe_gives_up_after_deadline(monkeypatch):
    from fastapi.testclient import TestClient
    from backend import main

    class Hang:
        name = "GPT(x)"

        def translate_raw_json(self, prompt, system_prompt=None):
            time.sleep(1.0)
            return '{"ok": true}'

        def translate_raw(self, prompt, max_tokens=8192):
            time.sleep(1.0)
            return _ok()

    monkeypatch.setattr(main, "_GATE_PROBE_SECONDS", 0.2)
    monkeypatch.setattr(orchestrator, "_build_llm_backends", lambda entries: [Hang()])
    started = time.monotonic()
    body = TestClient(main.app).post("/api/llm-test", json={"apiKeys": [{"platform": "gpt", "key": "k"}]}).json()
    assert time.monotonic() - started < 0.9
    assert body["ok"] is False and body["kind"] == "timeout" and body["translate_only"] is False


def test_r4_default_deadline_is_fifteen_seconds():
    from backend import main

    assert main._GATE_PROBE_SECONDS == 15


# ── R5 ─────────────────────────────

def test_r5_openai_insufficient_quota_is_balance():
    body = json.dumps({"error": {"type": "insufficient_quota", "code": "insufficient_quota"}}).encode()
    assert classify_translation_failure(_http_error(429, body)) == "insufficient_balance"


def test_r5_plain_429_is_still_rate_limit():
    body = json.dumps({"error": {"type": "requests", "code": "rate_limit_exceeded"}}).encode()
    assert classify_translation_failure(_http_error(429, body)) == "rate_limit"
    assert classify_translation_failure(_http_error(429)) == "rate_limit"


# ── R7 ─────────────────────────────

def test_r7_key_already_tried_this_batch_is_not_resent_whole_batch(monkeypatch):
    monkeypatch.setattr(paid_request, "_wait", lambda s: None)
    monkeypatch.setattr(orchestrator.time, "sleep", lambda s: None)

    class Down:
        name = "A"
        batch_calls = 0

        def translate_batch(self, texts, **kw):
            Down.batch_calls += 1
            raise ConnectionError("down")

    class Picky:
        name = "B"
        batch_calls = 0

        def translate_batch(self, texts, **kw):
            Picky.batch_calls += 1
            return TranslationResult(["好的译文", "x"], self.name)   # 第二句过不了质量检查

        def translate_raw(self, prompt, max_tokens=512):
            return TranslationResult(["--- 仍不合格"], self.name)

    orchestrator._translate_llm_per_batch(["Hello there.", "How are you?"], [Down(), Picky()], [],
                                          progress_callback=lambda *a: None)
    assert Picky.batch_calls == 1
