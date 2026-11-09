"""P367 中断 #13：统一专名接纳与连续空返回熔断真行为。"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.translation.base import FAILED_TRANSLATION, TranslationResult
from backend.translation.orchestrator import (
    _is_valid_translation_for_source,
    _translate_llm_per_batch,
)
# P500 施工 ⑤：整片翻译（_translate_one_chunk）与「≤10 句才补」（_retry_failed_with_llm）已删，
# 测它们的三条随之删除；上下文感知改走 _translate_llm_per_batch，失败句由下面的逐条测试覆盖。


class RecoveryBackend:
    def __init__(self, name, batch_results, raw_results=()):
        self.name = name
        self.batch_results = list(batch_results)
        self.raw_results = list(raw_results)
        self.batch_calls = []
        self.raw_calls = []

    def translate_batch(self, texts, source_lang="auto", target_lang="zh", context=""):
        self.batch_calls.append(list(texts))
        return self.batch_results.pop(0)

    def translate_raw(self, prompt, max_tokens=512):
        self.raw_calls.append(prompt)
        return self.raw_results.pop(0)


def _result(texts, failure_kind=None, backend_name="fake"):
    return TranslationResult(
        translated=list(texts),
        backend_name=backend_name,
        failure_kind=failure_kind,
    )


def test_source_guard_rejects_calendar_and_common_subtitle_words():
    rejected = [
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
        "Sunday", "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
        "Right", "Sure", "Wow", "Oh", "Cool", "Nice", "Fine", "Stop",
        "Wait", "Look", "Listen", "Come", "Go", "Man", "God", "Damn",
        "Good Morning", "Good Night", "Bye",
    ]

    assert all(
        not _is_valid_translation_for_source(text, text)
        for text in rejected
    )


def test_source_guard_still_allows_real_single_and_multiword_names():
    assert _is_valid_translation_for_source("Taylor", "Taylor")
    assert _is_valid_translation_for_source("Airbnb", "Airbnb")
    assert _is_valid_translation_for_source("Taylor Swift", "Taylor Swift")


def test_second_consecutive_empty_switches_to_next_llm():
    first = RecoveryBackend(
        "first",
        [_result([FAILED_TRANSLATION, FAILED_TRANSLATION], "empty_translation")],
        [_result([""], "empty_translation")],
    )
    second = RecoveryBackend(
        "second",
        [_result(["甲", "乙"], backend_name="second")],
    )

    translated, failed, _, _, failure_by_index = _translate_llm_per_batch(
        ["one", "two"], [first, second], progress_callback=lambda *a: None
    )

    assert translated == ["甲", "乙"]
    assert failed == []
    assert len(first.raw_calls) == 1
    assert len(second.batch_calls) == 1
    assert failure_by_index == {}


def test_one_empty_then_valid_raw_stays_on_current_llm():
    first = RecoveryBackend(
        "first",
        [_result([FAILED_TRANSLATION, FAILED_TRANSLATION], "empty_translation")],
        [
            _result(["甲"], backend_name="first"),
            _result(["乙"], backend_name="first"),
        ],
    )
    second = RecoveryBackend(
        "second",
        [_result(["不应调用", "不应调用"], backend_name="second")],
    )

    translated, failed, _, _, failure_by_index = _translate_llm_per_batch(
        ["one", "two"], [first, second], progress_callback=lambda *a: None
    )

    assert translated == ["甲", "乙"]
    assert failed == []
    assert len(first.raw_calls) == 2
    assert second.batch_calls == []
    assert failure_by_index == {}


def test_r3_retries_every_failed_line_with_full_batch_context_and_shared_rules():
    backend = RecoveryBackend(
        "first",
        # P404：初次协议失败后，同一后端先走整批纠正 + 前后两半，
        # 三级批量恢复都失败，才轮到 R3 逐条重试。
        [
            _result([FAILED_TRANSLATION, FAILED_TRANSLATION], "numbering_format"),
            _result([FAILED_TRANSLATION, FAILED_TRANSLATION], "numbering_format"),
            _result([FAILED_TRANSLATION], "numbering_format"),
            _result([FAILED_TRANSLATION], "numbering_format"),
        ],
        [
            _result(["甲"], backend_name="first"),
            _result(["乙"], backend_name="first"),
        ],
    )

    translated, failed, _, _, failure_by_index = _translate_llm_per_batch(
        ["first source", "second source"], [backend], progress_callback=lambda *a: None
    )

    assert translated == ["甲", "乙"]
    assert failed == []
    assert len(backend.raw_calls) == 2
    assert all("first source" in prompt for prompt in backend.raw_calls)
    assert all("second source" in prompt for prompt in backend.raw_calls)
    assert "只翻译第 0 条" in backend.raw_calls[0]
    assert "只翻译第 1 条" in backend.raw_calls[1]
    assert all("clock/watch" in prompt for prompt in backend.raw_calls)
    assert failure_by_index == {}


def test_r3_rejects_dirty_or_review_text_and_keeps_lines_failed():
    backend = RecoveryBackend(
        "first",
        # P404：初次协议失败后，同一后端先走整批纠正 + 前后两半，
        # 三级批量恢复都失败，才轮到 R3 逐条重试。
        [
            _result([FAILED_TRANSLATION, FAILED_TRANSLATION], "numbering_format"),
            _result([FAILED_TRANSLATION, FAILED_TRANSLATION], "numbering_format"),
            _result([FAILED_TRANSLATION], "numbering_format"),
            _result([FAILED_TRANSLATION], "numbering_format"),
        ],
        [
            _result(["甲 --- 残留"], backend_name="first"),
            _result(["建议：译成乙"], backend_name="first"),
        ],
    )

    translated, failed, _, _, failure_by_index = _translate_llm_per_batch(
        ["first source", "second source"], [backend], progress_callback=lambda *a: None
    )

    # D-1 A5（合同变更）：脏译文仍被拒；修不好的句子保留原文，不再记为失败句。
    assert translated == ["first source", "second source"]
    assert failed == []
    assert len(backend.raw_calls) == 2
    assert failure_by_index == {0: "quality_rejected", 1: "quality_rejected"}


def test_api_level_batch_failure_switches_without_individual_retries():
    first = RecoveryBackend(
        "first",
        [_result([FAILED_TRANSLATION], "timeout")],
        [_result(["不应调用"], backend_name="first")],
    )
    second = RecoveryBackend(
        "second",
        [_result(["成功"], backend_name="second")],
    )

    translated, failed, _, _, failure_by_index = _translate_llm_per_batch(
        ["source"], [first, second], progress_callback=lambda *a: None
    )

    assert translated == ["成功"]
    assert failed == []
    assert first.raw_calls == []
    assert failure_by_index == {}


def test_paid_route_keeps_source_and_never_calls_free_engines():
    """D-1 A5（合同变更）：付费逐段路线修不好的句子保留原文，免费引擎 0 次。

    8 月这里有两条测试（R5：免费引擎按优先级逐条补缺、连接失败熔断免费引擎；
    空返回只跳过当前句）。它们测的「付费路线里的免费补缺」已整段删除，
    改为本条：付费路线不再调用任何免费引擎。
    """
    llm = RecoveryBackend(
        "llm",
        [
            _result([FAILED_TRANSLATION, FAILED_TRANSLATION], "numbering_format"),
            _result([FAILED_TRANSLATION, FAILED_TRANSLATION], "numbering_format"),
            _result([FAILED_TRANSLATION], "numbering_format"),
            _result([FAILED_TRANSLATION], "numbering_format"),
        ],
        [
            _result(["建议：仍失败"], backend_name="llm"),
            _result(["建议：仍失败"], backend_name="llm"),
        ],
    )
    translated, failed, _, _, _failure_by_index = _translate_llm_per_batch(
        ["one", "two"], [llm], progress_callback=lambda *a: None
    )

    assert translated == ["one", "two"]
    assert failed == []


def test_batch_eight_shape_retries_first_40_then_keeps_source():
    """批 8 形状（69 条全碎）：付费逐条最多补 40 条，剩余 29 条保留原文。

    D-1 A5（合同变更）：8 月定案是第 41 条起交给免费引擎接住；本批付费路线
    不再调用免费引擎，改为保留原文、零最终失败。

    ⚠️ 语义反转（P404，2026-08-02 用户裁决）：本测试原名 `..._without_cap`，
    锁的是「失败多少条就逐条重发多少条」，源头是 London 任务 754d144731
    真实事故——第 8 批 69 条全灭，成品 SRT 从 20:21 到片尾连续 69 行 [翻译失败]。
    P404 定案给付费逐条恢复加了 `_MAX_PROTOCOL_SINGLE_RETRIES = 40` 硬上限，
    于是本测试从「守护：不许有条目掉出付费路径」变为
    「许可：允许第 41 条起掉出付费路径，但必须由免费引擎接住、零最终失败」。
    """
    source = ["Yeah." if index % 3 else "What" for index in range(69)]
    llm = RecoveryBackend(
        "llm",
        # 初次 + 整批纠正 + 前后两半，四次批量全部协议失败
        [
            _result([FAILED_TRANSLATION] * 69, "numbering_format"),
            _result([FAILED_TRANSLATION] * 69, "numbering_format"),
            _result([FAILED_TRANSLATION] * 35, "numbering_format"),
            _result([FAILED_TRANSLATION] * 34, "numbering_format"),
        ],
        [_result([f"译文{index}"], backend_name="llm") for index in range(69)],
    )
    translated, failed, _, _, failure_by_index = _translate_llm_per_batch(
        source, [llm], progress_callback=lambda *a: None
    )

    assert len(llm.raw_calls) == 40, "付费逐条恢复的硬上限是 40 条"
    assert translated[:40] == [f"译文{index}" for index in range(40)]
    assert translated[40:] == source[40:], "第 41 条起保留原文"
    assert failed == [], "最终不允许留下任何失败条目"
    assert sorted(failure_by_index) == list(range(40, 69))
