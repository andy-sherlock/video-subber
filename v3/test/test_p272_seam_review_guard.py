"""P272: seam review must not write LLM explanations into subtitles."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class DummyLLM:
    name = "dummy"

    def __init__(self, raw):
        self.raw = raw

    def translate_raw(self, prompt, max_tokens=4096):
        from backend.translation.base import TranslationResult

        return TranslationResult(
            translated=[self.raw],
            backend_name=self.name,
        )


def test_seam_review_rejects_version_explanation_text():
    from backend.translation.orchestrator import _review_seam

    reviewed = _review_seam(
        translated=["前文", "原译文", "后文"],
        overlap_start=1,
        overlap_end=2,
        version_a={1: "好吧"},
        version_b={1: "行吧"},
        terminology={},
        llm=DummyLLM("[1] 版本A是好吧，版本B是行吧，我建议选B，因为更自然。"),
    )

    assert reviewed is None


def test_seam_review_accepts_plain_subtitle_text():
    from backend.translation.orchestrator import _review_seam

    reviewed = _review_seam(
        translated=["前文", "原译文", "后文"],
        overlap_start=1,
        overlap_end=2,
        version_a={1: "好吧"},
        version_b={1: "行吧"},
        terminology={},
        llm=DummyLLM("[1] 行吧"),
    )

    assert reviewed == {1: "行吧"}


def _review(raw, source="Good morning"):
    from backend.translation.orchestrator import _review_seam

    return _review_seam(
        translated=["前文", "原译文", "后文"],
        overlap_start=1,
        overlap_end=2,
        version_a={1: "早上好"},
        version_b={1: "早安"},
        terminology={},
        llm=DummyLLM(raw),
        source_texts=["before", source, "after"],
    )


def test_seam_review_rejects_english_source_echo():
    assert _review("[1] Good morning") is None


def test_seam_review_rejects_dash_residue():
    assert _review("[1] 早上好 --- 备选") is None


def test_seam_review_duplicate_number_invalidates_whole_response():
    assert _review("[1] 早上好\n[1] 早安") is None


def test_seam_review_rejects_outside_number():
    assert _review("[0] 越界建议") is None


def test_seam_review_rejects_review_comment():
    assert _review("[1] 建议：译成早安") is None


def test_seam_review_clean_suggestion_really_applies():
    assert _review("[1] 早安") == {1: "早安"}


def test_seam_review_uses_same_proper_name_exception():
    assert _review("[1] Taylor", source="Taylor") == {1: "Taylor"}
