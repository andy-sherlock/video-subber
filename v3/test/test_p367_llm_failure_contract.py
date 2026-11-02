"""P367: LLM backend exposes stable failure kinds and shared prompt rules."""

from pathlib import Path
import logging
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.translation import base as base_module
from backend.translation.base import LLMBackend


class FakeLLMBackend(LLMBackend):
    def __init__(self, raw: str = "", error: BaseException | None = None):
        self.raw = raw
        self.error = error
        self.prompts: list[str] = []

    @property
    def name(self) -> str:
        return "fake-llm"

    def _call_api_raw(self, prompt: str, max_tokens: int = 8192) -> tuple[str, dict]:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.raw, {
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }


def test_missing_number_reports_numbering_format():
    result = FakeLLMBackend("[0] 甲").translate_batch(["one", "two"])

    assert result.translated == ["[翻译失败]", "[翻译失败]"]
    assert result.failure_kind == "numbering_format"


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("[0] 甲\n[0] 乙", id="duplicate"),
        pytest.param("[0] 甲\n[2] 乙", id="out-of-range"),
        pytest.param("未编号的前导文字\n[0] 甲\n[1] 乙", id="structural"),
    ],
)
def test_bad_numbering_variants_report_numbering_format(raw):
    result = FakeLLMBackend(raw).translate_batch(["one", "two"])

    assert result.translated == ["[翻译失败]", "[翻译失败]"]
    assert result.failure_kind == "numbering_format"


def test_empty_numbered_translation_reports_empty_translation():
    result = FakeLLMBackend("[0]   ").translate_batch(["one"])

    assert result.translated == ["[翻译失败]"]
    assert result.failure_kind == "empty_translation"


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("[0] 建议：改成更口语\n[1] 乙", id="review-comment"),
        pytest.param("[0] 甲 --- 污染\n[1] 乙", id="dash-separator"),
        pytest.param("[0] [0] [1]\n[1] 乙", id="control-number-residue"),
    ],
)
def test_polluted_numbered_response_reports_quality_rejected(raw):
    result = FakeLLMBackend(raw).translate_batch(["one", "two"])

    assert result.translated == ["[翻译失败]", "[翻译失败]"]
    assert result.failure_kind == "quality_rejected"


@pytest.mark.parametrize(
    ("source", "raw"),
    [
        pytest.param(
            ["Hello, how are you?", "This is a watch."],
            "[0] Hello, how are you?\n[1] This is a watch.",
            id="source-copied",
        ),
        pytest.param(
            ["Good evening."],
            "[0] Good night.",
            id="wrong-target-language",
        ),
    ],
)
def test_non_chinese_sentence_batch_reports_quality_rejected(source, raw):
    result = FakeLLMBackend(raw).translate_batch(source, target_lang="zh")

    assert result.translated == ["[翻译失败]"] * len(source)
    assert result.failure_kind == "quality_rejected"


def test_pure_proper_name_lines_can_remain_unchanged():
    result = FakeLLMBackend("[0] Taylor\n[1] Airbnb").translate_batch(
        ["Taylor", "Airbnb"], target_lang="zh"
    )

    assert result.translated == ["Taylor", "Airbnb"]
    assert result.failure_kind is None


@pytest.mark.parametrize("word", ["You", "What", "Yeah"])
def test_common_english_subtitle_word_cannot_use_proper_name_exemption(word):
    result = FakeLLMBackend(f"[0] {word}").translate_batch([word], target_lang="zh")

    assert result.translated == ["[翻译失败]"]
    assert result.failure_kind == "quality_rejected"


def test_empty_zero_count_batch_preserves_existing_parser_semantics():
    assert base_module._parse_numbered_batch("", 0) == []


class FakeStatusError(RuntimeError):
    def __init__(self, status_code: int):
        super().__init__("fake API failure")
        self.status_code = status_code


@pytest.mark.parametrize(
    ("error", "expected_kind"),
    [
        pytest.param(TimeoutError("fake timeout"), "timeout", id="timeout"),
        pytest.param(ConnectionError("fake connection"), "connection", id="connection"),
        pytest.param(FakeStatusError(429), "rate_limit", id="rate-limit"),
    ],
)
def test_translate_batch_api_exception_uses_shared_classifier(error, expected_kind):
    result = FakeLLMBackend(error=error).translate_batch(["hello"])

    assert result.translated == ["[翻译失败]"]
    assert result.failure_kind == expected_kind
    assert not hasattr(result, "error")


def test_translate_raw_success_and_failure_have_public_failure_contract():
    success = FakeLLMBackend("你好").translate_raw("fake prompt")
    failure = FakeLLMBackend(error=FakeStatusError(503)).translate_raw("fake prompt")

    assert success.translated == ["你好"]
    assert success.failure_kind is None
    assert failure.translated == [""]
    assert failure.failure_kind == "service_error"
    assert not hasattr(failure, "error")


def test_translate_raw_empty_response_reports_empty_translation():
    result = FakeLLMBackend("").translate_raw("fake prompt")

    assert result.translated == [""]
    assert result.failure_kind == "empty_translation"


def test_valid_numbered_response_succeeds_and_runtime_prompt_has_shared_rules():
    backend = FakeLLMBackend("[0] 你好\n[1] 这是表")

    result = backend.translate_batch(
        ["Hello", "What time is it on your watch?"],
        target_lang="zh",
        context="口语对话",
    )

    assert result.translated == ["你好", "这是表"]
    assert result.failure_kind is None
    prompt = backend.prompts[-1]
    assert "口语、正式或幽默风格" in prompt
    assert "根据上下文处理多义词" in prompt
    assert "专有名词合理保留原文或音译" in prompt
    assert "clock/watch" in prompt
    assert "询问时间时通常译为“表”" in prompt
    assert "明确指实体钟、钟楼或报时装置" in prompt
    assert "仅按 [编号] 译文 格式输出。除编号和译文外，不要输出解释、分隔符或 Markdown。" in prompt


def test_shared_rules_constant_contains_semantics_only():
    rules = getattr(base_module, "SHARED_TRANSLATION_RULES", None)

    assert isinstance(rules, str)
    assert "口语、正式或幽默风格" in rules
    assert "根据上下文处理多义词" in rules
    assert "专有名词" in rules
    assert "clock/watch" in rules
    for forbidden in ("[编号]", "摘要", "术语表", "目标语言", "输出格式"):
        assert forbidden not in rules


def test_debug_log_records_category_and_caps_raw_summary(caplog):
    raw = "未编号 " + ("甲" * 350) + "DO_NOT_LOG_THIS_TAIL"
    caplog.set_level(logging.DEBUG, logger="backend.translation")

    result = FakeLLMBackend(raw).translate_batch(["one"])

    assert result.failure_kind == "numbering_format"
    debug_messages = [
        record.getMessage() for record in caplog.records if record.levelno == logging.DEBUG
    ]
    assert any("numbering_format" in message for message in debug_messages)
    assert any("未编号" in message for message in debug_messages)
    assert all("DO_NOT_LOG_THIS_TAIL" not in message for message in debug_messages)
    assert "DO_NOT_LOG_THIS_TAIL" not in repr(result)


if __name__ == "__main__":
    pytest.main([__file__])
