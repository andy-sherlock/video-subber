"""D-1 C3 / C5 / 增 1–4：付费请求函数——换 Key、原地等、分类、原因文字。

付费模式下每一次真实请求都经过 `call_paid`：
  · 连不上 / 超时 / 5xx / 429 / 402 → 先按用户排序换 Key，全不通才原地等
    （5 / 15 / 30 秒，之后每 180 秒；平台给的 Retry-After 更长就照平台的）；
  · 某个 Key 被拒（401 / 403）→ 换下一个；全部被拒 → 任务出错；
  · 请求被拒（其他 4xx）、认不出的错 → 任务出错，认不出的错把原始异常写进日志；
  · AI 回复格式类问题（编号不对、空回答等）不归它管，原样交回调用方。
"""

from email.message import Message
from pathlib import Path
import logging
import sys
import urllib.error

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.translation.base import (
    TranslationResult,
    classify_translation_failure,
    retry_after_seconds,
)
from backend.translation.paid_request import (
    PaidAIUnavailable,
    call_paid,
    wait_message,
)


def _http_error(code, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("https://api.example.test", code, "x", headers, None)


class ScriptBackend:
    """按剧本逐次返回：异常就抛，TranslationResult 就返回。"""

    def __init__(self, name, script):
        self.name = name
        self.script = list(script)
        self.calls = 0

    def run(self):
        self.calls += 1
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def _ok(text="好"):
    return TranslationResult(translated=[text], backend_name="x")


def _fail(kind, retry_after=None):
    return TranslationResult(
        translated=[""], backend_name="x", failure_kind=kind, retry_after=retry_after
    )


class Sleeps:
    def __init__(self):
        self.seconds = []

    def __call__(self, value):
        self.seconds.append(value)


def _call(backends, sleep=None, waits=None):
    sleep = sleep or Sleeps()
    return call_paid(
        backends,
        lambda backend: backend.run(),
        sleep=sleep,
        on_wait=(lambda kind, name, seconds: waits.append((kind, name, seconds)))
        if waits is not None else (lambda *a: None),
    ), sleep


# ── 分类（增 1 / 增 3） ─────────────────────────────

def test_402_is_insufficient_balance_not_service_error():
    assert classify_translation_failure(_http_error(402)) == "insufficient_balance"


@pytest.mark.parametrize("code", [400, 404, 413, 422])
def test_other_4xx_is_request_rejected(code):
    assert classify_translation_failure(_http_error(code)) == "request_rejected"


@pytest.mark.parametrize("code,kind", [(401, "auth"), (403, "auth"), (429, "rate_limit"), (503, "service_error")])
def test_existing_kinds_unchanged(code, kind):
    assert classify_translation_failure(_http_error(code)) == kind


def test_new_kinds_are_public_detail_kinds():
    from backend.main import _DETAIL_FAILURE_KINDS

    assert {"insufficient_balance", "request_rejected"} <= _DETAIL_FAILURE_KINDS


# ── Retry-After（增 2） ─────────────────────────────

def test_retry_after_seconds_reads_header():
    assert retry_after_seconds(_http_error(429, 60)) == 60
    assert retry_after_seconds(_http_error(429)) is None
    assert retry_after_seconds(ValueError("x")) is None


def test_translate_batch_keeps_retry_after_on_failure(monkeypatch):
    from backend.translation.deepseek import DeepSeekBackend

    backend = DeepSeekBackend(api_key="k")

    def boom(*args, **kwargs):
        raise _http_error(429, 45)

    monkeypatch.setattr(backend, "_call_api_raw", boom)
    result = backend.translate_batch(["hello"])
    assert result.failure_kind == "rate_limit"
    assert result.retry_after == 45

    raw = backend.translate_raw("hello")
    assert raw.failure_kind == "rate_limit"
    assert raw.retry_after == 45


# ── 换 Key / 等待（C3） ─────────────────────────────

def test_success_first_try_no_wait():
    a = ScriptBackend("DeepSeek(deepseek-chat)", [_ok("甲")])
    (backend, result), sleep = _call([a])
    assert result.translated == ["甲"] and backend is a
    assert sleep.seconds == []


def test_first_key_broken_second_key_used_without_waiting():
    a = ScriptBackend("A", [ConnectionError("down")])
    b = ScriptBackend("B", [_ok()])
    (backend, _), sleep = _call([a, b])
    assert backend is b
    assert sleep.seconds == []


def test_all_keys_down_waits_5_15_30_then_every_180_and_restarts_from_first_key():
    script = [ConnectionError("down")] * 5 + [_ok()]
    a = ScriptBackend("A", script)
    waits = []
    (backend, _), sleep = _call([a], waits=waits)
    assert backend is a
    assert sleep.seconds == [5, 15, 30, 180, 180]
    assert [w[0] for w in waits] == ["connection"] * 10
    # P514：每次等之前汇报一次（秒数），睡醒后、再请求前再汇报一次（0）
    assert [w[2] for w in waits] == [5, 0, 15, 0, 30, 0, 180, 0, 180, 0]


def test_each_round_tries_keys_in_user_order_again():
    a = ScriptBackend("A", [ConnectionError("x"), _ok("从A")])
    b = ScriptBackend("B", [ConnectionError("y")])
    (backend, result), sleep = _call([a, b])
    assert backend is a and result.translated == ["从A"]
    assert sleep.seconds == [5]
    assert (a.calls, b.calls) == (2, 1)


def test_longer_retry_after_from_platform_wins():
    a = ScriptBackend("A", [_fail("rate_limit", retry_after=60), _ok()])
    _, sleep = _call([a])
    assert sleep.seconds == [60]


def test_shorter_retry_after_does_not_shorten_schedule():
    a = ScriptBackend("A", [_fail("rate_limit", retry_after=2), _ok()])
    _, sleep = _call([a])
    assert sleep.seconds == [5]


def test_retry_after_on_raised_http_error_is_honoured():
    a = ScriptBackend("A", [_http_error(503, 40), _ok()])
    _, sleep = _call([a])
    assert sleep.seconds == [40]


@pytest.mark.parametrize("kind", ["connection", "timeout", "service_error", "rate_limit", "insufficient_balance"])
def test_recoverable_kinds_wait(kind):
    a = ScriptBackend("A", [_fail(kind), _ok()])
    waits = []
    _, sleep = _call([a], waits=waits)
    assert sleep.seconds == [5]
    assert waits == [(kind, "A", 5), (kind, "A", 0)], "P514：睡醒后先汇报再请求"


def test_one_key_rejected_other_key_down_still_waits():
    a = ScriptBackend("A", [_http_error(401), _http_error(401)])
    b = ScriptBackend("B", [ConnectionError("x"), _ok()])
    (backend, _), sleep = _call([a, b])
    assert backend is b and sleep.seconds == [5]


def test_all_keys_rejected_raises_without_waiting():
    a = ScriptBackend("A", [_http_error(401)])
    b = ScriptBackend("B", [_fail("auth")])
    sleep = Sleeps()
    with pytest.raises(PaidAIUnavailable) as info:
        call_paid([a, b], lambda backend: backend.run(), sleep=sleep, on_wait=lambda *a: None)
    assert info.value.kind == "auth"
    assert sleep.seconds == []


def test_request_rejected_on_every_key_raises():
    # ⑦.5 R1（用户裁决）：请求被拒先换下一个 Key，全部被拒才出错。
    a = ScriptBackend("A", [_http_error(400)])
    b = ScriptBackend("B", [_http_error(404)])
    with pytest.raises(PaidAIUnavailable) as info:
        call_paid([a, b], lambda backend: backend.run(), sleep=Sleeps(), on_wait=lambda *a: None)
    assert info.value.kind == "request_rejected"
    assert (a.calls, b.calls) == (1, 1)


def test_unknown_error_raises_and_logs_original_exception(caplog):
    a = ScriptBackend("A", [KeyError("choices")])
    with caplog.at_level(logging.ERROR):
        with pytest.raises(PaidAIUnavailable) as info:
            call_paid([a], lambda backend: backend.run(), sleep=Sleeps(), on_wait=lambda *a: None)
    assert info.value.kind == "unknown"
    record = next(r for r in caplog.records if r.exc_info)
    assert record.exc_info[0] is KeyError


def test_unavailable_message_has_no_friendly_error_trigger_words():
    """worker._friendly_error 按子串改写（含 401/403/429/Timeout 会被换成别的话）。"""
    for kind in ("auth", "request_rejected", "unknown"):
        a = ScriptBackend("DeepSeek(deepseek-chat)", [_http_error(401) if kind == "auth" else _http_error(400) if kind == "request_rejected" else KeyError("x")])
        with pytest.raises(PaidAIUnavailable) as info:
            call_paid([a], lambda backend: backend.run(), sleep=Sleeps(), on_wait=lambda *a: None)
        text = str(info.value)
        assert text and not any(w in text for w in ("401", "403", "429", "Timeout", "timeout"))


@pytest.mark.parametrize("kind", ["numbering_format", "empty_translation", "quality_rejected"])
def test_format_failures_are_returned_to_caller_untouched(kind):
    a = ScriptBackend("A", [_fail(kind)])
    b = ScriptBackend("B", [_ok()])
    (backend, result), sleep = _call([a, b])
    assert backend is a and result.failure_kind == kind
    assert b.calls == 0 and sleep.seconds == []


def test_plain_string_result_passes_through():
    """断句 / 润色用 translate_raw_json，成功时返回字符串。"""
    a = ScriptBackend("A", ['{"a":1}'])
    (backend, result), _ = _call([a])
    assert result == '{"a":1}'


# ── 原因文字（C5） ─────────────────────────────

def test_wait_messages_name_platform_and_reason():
    assert wait_message("connection", "DeepSeek(deepseek-chat)") == "正在等待连接 DeepSeek，恢复后自动继续"
    assert wait_message("timeout", "DeepSeek(x)") == "正在等待连接 DeepSeek，恢复后自动继续"
    assert wait_message("insufficient_balance", "DeepSeek(x)") == "DeepSeek 余额不足，充值后会自动继续"
    assert wait_message("service_error", "DeepSeek(x)") == "DeepSeek 服务器出错，恢复后自动继续"
    assert wait_message("rate_limit", "DeepSeek(x)") == "DeepSeek 太忙，稍后自动继续"
    assert wait_message("insufficient_balance", "DeepSeek(x)") != wait_message("service_error", "DeepSeek(x)")
