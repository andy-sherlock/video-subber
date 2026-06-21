"""P276: standard mode language routing and environment visibility."""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_standard_router_uses_qwen_for_high_confidence_japanese():
    from backend.transcriber_v2 import choose_standard_primary_engine

    assert choose_standard_primary_engine("ja", 0.95) == "qwen"


def test_standard_router_keeps_whisper_for_low_confidence():
    from backend.transcriber_v2 import choose_standard_primary_engine

    assert choose_standard_primary_engine("ja", 0.20) == "whisper"


def test_standard_router_keeps_whisper_for_non_qwen_primary_language():
    from backend.transcriber_v2 import choose_standard_primary_engine

    assert choose_standard_primary_engine("fr", 0.95) == "whisper"


def test_standard_primary_state_respects_whisper_choice():
    from backend.transcriber_v2 import choose_transcription_primary

    primary, keep_qwen = choose_transcription_primary(
        transcription_tier="standard",
        standard_primary="whisper",
        detected_lang="ja",
        qwen_chunks=[{"text": "dummy"}],
    )

    assert primary == "whisper"
    assert keep_qwen is False


def test_filter_hallucinations_can_skip_l2_when_no_second_signal(caplog):
    from backend.core.hallucination_filter import filter_hallucinations

    class Segment:
        def __init__(self):
            self.id = 0
            self.start = 0.0
            self.end = 1.0
            self.text = "あれ"
            self.filtered = False
            self.filter_reason = None
            self.uncertain = False

    with caplog.at_level(logging.INFO):
        result = filter_hallucinations(
            [Segment()],
            source_lang="ja",
            qwen_chunks=None,
            skip_l1=True,
            skip_l2=True,
        )

    assert len(result) == 1
    assert "L2 跳过" in caplog.text


def test_sensevoice_unavailable_warning_names_funasr(caplog):
    from backend.transcriber_v2 import log_sensevoice_unavailable

    with caplog.at_level(logging.WARNING):
        log_sensevoice_unavailable()

    assert "funasr" in caplog.text
    assert "人声地图" in caplog.text


def test_short_japanese_fragment_merges_into_neighbor():
    from backend.transcriber_v2 import Segment, merge_short_subtitle_fragments

    segments = [
        Segment(0, 0.00, 0.12, "だ"),
        Segment(1, 0.14, 1.10, "ろう"),
    ]

    result = merge_short_subtitle_fragments(segments, "ja")

    assert len(result) == 1
    assert result[0].text == "だろう"
    assert result[0].start == 0.00
    assert result[0].end == 1.10
