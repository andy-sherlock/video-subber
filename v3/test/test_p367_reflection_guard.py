"""P367：反思修正只能接纳范围内的干净目标语言建议。"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.translation.reflection import apply_reflection, parse_reflection_result


def test_reflection_rejects_english_echo():
    assert parse_reflection_result(
        "[0] Good morning", source_texts=["Good morning"]
    ) == {}


def test_reflection_rejects_dash_residue():
    assert parse_reflection_result(
        "[0] 早上好 --- 备选", source_texts=["Good morning"]
    ) == {}


def test_reflection_rejects_review_comment():
    assert parse_reflection_result(
        "[0] 建议：译成早安", source_texts=["Good morning"]
    ) == {}


def test_reflection_duplicate_number_invalidates_whole_response():
    assert parse_reflection_result(
        "[0] 早上好\n[0] 早安", source_texts=["Good morning"]
    ) == {}


def test_reflection_rejects_out_of_range_number():
    assert parse_reflection_result(
        "[1] 越界修正", source_texts=["only one"]
    ) == {}


def test_reflection_clean_correction_really_applies():
    corrections = parse_reflection_result(
        "[0] 早安", source_texts=["Good morning"]
    )

    assert corrections == {0: "早安"}
    assert apply_reflection(["早上好"], corrections) == ["早安"]


def test_reflection_uses_same_proper_name_exception():
    assert parse_reflection_result(
        "[0] Taylor", source_texts=["Taylor"]
    ) == {0: "Taylor"}


def test_rejected_reflection_keeps_existing_translation():
    corrections = parse_reflection_result(
        "[0] 建议：Taylor", source_texts=["Taylor"]
    )

    assert apply_reflection(["Taylor"], corrections) == ["Taylor"]
