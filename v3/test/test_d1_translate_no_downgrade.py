"""D-1 A1 / A5 / C7 / C8 / C3：逐段翻译付费路线不再「出错就放弃、转免费」。

  · 网络类出错 → 付费请求函数原地等、恢复后接着翻，免费引擎 0 次；
  · 格式修复（整批 → 半批 → 逐句）修不好的句子保留原文，记数，
    不以「免费补全」身份回报（回调第 4 个参数不是 True）；
  · 全部 Key 被拒 → 抛出，任务出错；
  · 上下文感知路线不经过付费请求函数（B14）。
"""

from pathlib import Path
import logging
import sys
import urllib.error
from email.message import Message

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.translation import orchestrator, paid_request
from backend.translation.base import FAILED_TRANSLATION, TranslationResult
from backend.translation.paid_request import PaidAIUnavailable


def _http_error(code):
    return urllib.error.HTTPError("https://api.example.test", code, "x", Message(), None)


class Waits(list):
    def __call__(self, seconds):
        self.append(seconds)


@pytest.fixture
def waits(monkeypatch):
    recorder = Waits()
    monkeypatch.setattr(paid_request, "_wait", recorder)
    monkeypatch.setattr(orchestrator.time, "sleep", lambda _s: None)
    return recorder


class PaidScript:
    """付费后端：translate_batch / translate_raw 按剧本返回或抛出。"""

    def __init__(self, name, batch_script=(), raw_script=()):
        self.name = name
        self.batch_script = list(batch_script)
        self.raw_script = list(raw_script)
        self.batch_calls = 0
        self.raw_calls = 0

    def translate_batch(self, texts, source_lang="auto", target_lang="zh", context=""):
        self.batch_calls += 1
        step = self.batch_script.pop(0)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step(texts)
        return step

    def translate_raw(self, prompt, max_tokens=8192):
        self.raw_calls += 1
        step = self.raw_script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


class FreeCounter:
    name = "google"

    def __init__(self):
        self.calls = 0

    def translate_batch(self, texts, **kwargs):
        self.calls += 1
        return TranslationResult(translated=["免费" for _ in texts], backend_name=self.name)


def _good(texts):
    return TranslationResult(translated=[f"译文{i}" for i in range(len(texts))], backend_name="paid")


def _fail(kind, n):
    return TranslationResult(translated=[FAILED_TRANSLATION] * n, backend_name="paid", failure_kind=kind)


TEXTS = ["Hello there.", "How are you?"]


def _run(paid, free=None, progress=None, items=None, stats=None):
    return orchestrator._translate_llm_per_batch(
        list(TEXTS),
        paid,
        [free] if free else [],
        progress_callback=progress or (lambda *a: None),
        item_result_callback=(lambda *args: items.append(args)) if items is not None else None,
        translation_stats_callback=(lambda s: stats.append(dict(s))) if stats is not None else None,
    )


def test_network_error_waits_and_resumes_without_free_engine(waits):
    paid = PaidScript("DeepSeek(x)", batch_script=[ConnectionError("down"), ConnectionError("down"), _good])
    free = FreeCounter()
    progress = []
    results, failed, *_ = _run([paid], free, progress=lambda pct, msg: progress.append(msg))
    assert results == ["译文0", "译文1"] and failed == []
    assert free.calls == 0
    assert list(waits) == [5, 15]
    assert "正在等待连接 DeepSeek，恢复后自动继续" in progress


def test_returned_network_failure_kind_also_waits(waits):
    paid = PaidScript("DeepSeek(x)", batch_script=[_fail("insufficient_balance", 2), _good])
    free = FreeCounter()
    progress = []
    results, *_ = _run([paid], free, progress=lambda pct, msg: progress.append(msg))
    assert results == ["译文0", "译文1"]
    assert free.calls == 0 and list(waits) == [5]
    assert "DeepSeek 余额不足，充值后会自动继续" in progress


def test_first_key_down_second_key_used_no_wait(waits):
    a = PaidScript("A", batch_script=[ConnectionError("down")])
    b = PaidScript("B", batch_script=[_good])
    results, *_ = _run([a, b], FreeCounter())
    assert results == ["译文0", "译文1"]
    assert list(waits) == [] and b.batch_calls == 1


def test_all_keys_rejected_raises(waits):
    a = PaidScript("A", batch_script=[_http_error(401)])
    b = PaidScript("B", batch_script=[_fail("auth", 2)])
    free = FreeCounter()
    with pytest.raises(PaidAIUnavailable):
        _run([a, b], free)
    assert free.calls == 0


def test_format_failure_unfixable_keeps_source_and_never_calls_free(waits, caplog):
    # 整批编号错 → 整批纠正仍错 → 两个半批仍错 → 逐句返回空（连续两次空 → 停）
    paid = PaidScript(
        "DeepSeek(x)",
        batch_script=[_fail("numbering_format", 2)] * 4,
        raw_script=[TranslationResult(translated=[""], backend_name="p")] * 4,
    )
    free = FreeCounter()
    items, stats = [], []
    with caplog.at_level(logging.INFO, logger="backend.translation"):
        results, failed, _tokens, _splits, _fail_by = _run([paid], free, items=items, stats=stats)
    assert free.calls == 0
    assert results == TEXTS            # 保留原文
    assert failed == []                # 不再进入「部分完成」
    assert all(args[3] is False for args in items)  # 不以「免费补全」身份回报
    assert {args[0] for args in items if args[1] in TEXTS} == {0, 1}
    assert stats[-1]["kept_original_count"] == 2
    log = "\n".join(r.getMessage() for r in caplog.records)
    assert "付费成功 0 条" in log and "保留原文 2 条" in log


def test_success_log_counts_paid_only(waits, caplog):
    paid = PaidScript("DeepSeek(x)", batch_script=[_good])
    with caplog.at_level(logging.INFO, logger="backend.translation"):
        _run([paid], FreeCounter())
    log = "\n".join(r.getMessage() for r in caplog.records)
    assert "付费成功 2 条" in log and "保留原文 0 条" in log


def test_network_error_during_half_batch_recovery_waits_and_keeps_fixed_half(waits):
    """验收 5：半批格式修好、另一半断网 → 格式修复没被删、断网没被格式修复吞掉。"""
    def first_half_ok(texts):
        return TranslationResult(translated=["前半"], backend_name="p")

    paid = PaidScript(
        "DeepSeek(x)",
        batch_script=[
            _fail("numbering_format", 2),   # 初次整批
            _fail("numbering_format", 2),   # 整批纠正
            first_half_ok,                  # 前半批修好
            ConnectionError("down"),        # 后半批断网
            lambda texts: TranslationResult(translated=["后半"], backend_name="p"),
        ],
    )
    free = FreeCounter()
    results, failed, *_ = _run([paid], free)
    assert results == ["前半", "后半"] and failed == []
    assert list(waits) == [5] and free.calls == 0


def test_per_item_retry_network_error_waits(waits):
    paid = PaidScript(
        "DeepSeek(x)",
        batch_script=[lambda texts: TranslationResult(translated=["好的译文", "x"], backend_name="p", failure_kind=None)],
        raw_script=[ConnectionError("down"), TranslationResult(translated=["你好吗？"], backend_name="p")],
    )
    # 第二句「x」过不了质量检查 → 逐句重试 → 断网 → 等 → 成功
    results, *_ = _run([paid], FreeCounter())
    assert results[1] == "你好吗？"
    assert list(waits) == [5]


def test_context_aware_route_does_not_use_paid_request(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("上下文感知路线不得经过付费请求函数（B14）")

    monkeypatch.setattr(paid_request, "call_paid", forbidden)
    monkeypatch.setattr(orchestrator, "call_paid", forbidden, raising=False)

    class Ctx:
        name = "ctx"

        def translate_raw(self, prompt, max_tokens=8192):
            return TranslationResult(translated=["[0] 你好\n[1] 你好吗"], backend_name="ctx")

        def translate_batch(self, texts, **kwargs):
            return TranslationResult(translated=["你好"] * len(texts), backend_name="ctx")

    orchestrator._translate_llm_context_aware(list(TEXTS), [Ctx()], [])


def test_disconnect_during_recovery_waits_on_same_vendor_not_second_key(waits):
    """合同增量 5：格式修复做到一半断网 → 只等同一家，不交给第二个 Key。"""
    first = PaidScript(
        "DeepSeek(x)",
        batch_script=[
            _fail("numbering_format", 2),   # 初次整批
            ConnectionError("down"),        # 整批纠正时断网
            _good,                          # 恢复后整批纠正成功
        ],
    )
    second = PaidScript("GPT(y)", batch_script=[_good])
    results, failed, *_ = _run([first, second], FreeCounter())
    assert results == ["译文0", "译文1"] and failed == []
    assert second.batch_calls == 0
    assert list(waits) == [5]
