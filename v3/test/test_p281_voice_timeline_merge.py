"""P281: use the voice timeline to handle isolated short subtitles."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_short_fragment_merges_across_wider_gap_inside_same_voice_region():
    from backend.transcriber_v2 import Segment, merge_short_subtitle_fragments

    segments = [
        Segment(0, 10.0, 11.0, "それで"),
        Segment(1, 12.4, 12.5, "ね"),
    ]
    voice_timeline = {
        "voice_regions": [(9.5, 13.0)],
        "non_voice_regions": [(0.0, 9.5), (13.0, 20.0)],
        "voice_ratio": 0.2,
    }

    result = merge_short_subtitle_fragments(
        segments, "ja", voice_timeline=voice_timeline)

    assert len(result) == 1
    assert result[0].text == "それでね"
    assert result[0].start == 10.0
    assert result[0].end == 12.5


def test_short_fragment_in_non_voice_region_is_kept_internal_only_not_user_uncertain():
    from backend.transcriber_v2 import Segment, merge_short_subtitle_fragments

    segments = [
        Segment(0, 1.0, 1.1, "あ"),
        Segment(1, 5.0, 6.0, "こんにちは"),
    ]
    voice_timeline = {
        "voice_regions": [(4.5, 6.5)],
        "non_voice_regions": [(0.0, 4.5), (6.5, 10.0)],
        "voice_ratio": 0.2,
    }

    result = merge_short_subtitle_fragments(
        segments, "ja", voice_timeline=voice_timeline)

    assert len(result) == 2
    assert result[0].text == "あ"
    assert result[0].uncertain is False
    assert result[0].filter_reason == "voice_timeline_non_voice_short_fragment"
