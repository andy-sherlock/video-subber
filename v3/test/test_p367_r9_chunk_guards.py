"""P367 R9：上下文滑窗解析、合并和接缝写回守卫。"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.translation.base import FAILED_TRANSLATION, TranslationResult
from backend.translation import orchestrator


class RawLLM:
    name = "raw"

    def __init__(self, raw):
        self.raw = raw

    def translate_raw(self, prompt, max_tokens=16384):
        return TranslationResult(translated=[self.raw], backend_name=self.name)


def _translate_chunk(raw):
    _, result, _ = orchestrator._translate_one_chunk(
        [(0, "one"), (1, "two")], "", RawLLM(raw), 0, 1
    )
    return result


def test_duplicate_number_invalidates_whole_chunk_response():
    assert _translate_chunk("[0] 甲\n[0] 乙\n[1] 丙") == {}


def test_outside_number_is_rejected_without_losing_valid_inside_line():
    assert _translate_chunk("[99] 越界\n[0] 甲") == {0: "甲"}


def test_dirty_and_review_lines_are_rejected_individually():
    assert _translate_chunk("[0] 甲 --- 残留\n[1] 建议：译成乙") == {}


def test_missing_number_does_not_invalidate_valid_subset_this_round():
    assert _translate_chunk("[0] 甲") == {0: "甲"}


def test_chunk_merge_rechecks_membership_before_writing(monkeypatch):
    def fake_translate_one_chunk(chunk, prompt_prefix, llm, ci, total):
        return ci, {0: "甲", 1: "越界写回"}, None

    monkeypatch.setattr(
        orchestrator, "_translate_one_chunk", fake_translate_one_chunk
    )
    translated = [FAILED_TRANSLATION, FAILED_TRANSLATION]

    chunk_results, _ = orchestrator._translate_chunks_with_prompt(
        [[(0, "one")]],
        ["one", "two"],
        translated,
        "",
        RawLLM(""),
    )

    assert chunk_results == [{0: "甲", 1: "越界写回"}]
    assert translated == ["甲", FAILED_TRANSLATION]


def test_seam_writeback_rechecks_current_overlap_membership(monkeypatch):
    monkeypatch.setattr(
        orchestrator,
        "_review_seam",
        lambda *args, **kwargs: {0: "区外污染", 2: "合法修正"},
    )
    chunks = [
        [(0, "zero"), (1, "one"), (2, "two"), (3, "three")],
        [(1, "one"), (2, "two"), (3, "three"), (4, "four")],
    ]
    translated = ["零", "一", "二", "三", "四"]

    orchestrator._review_translation_seams(
        chunks,
        [{1: "一A", 2: "二A", 3: "三A"}, {1: "一B", 2: "二B", 3: "三B"}],
        translated,
        {},
        RawLLM(""),
        None,
        source_texts=["zero", "one", "two", "three", "four"],
    )

    assert translated == ["零", "一", "合法修正", "三", "四"]
