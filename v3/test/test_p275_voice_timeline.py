"""P275: voice timeline from SenseVoice events."""
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── _build_voice_timeline tests ──

class TestBuildVoiceTimeline:
    def _build(self, sv_events, audio_duration):
        from backend.transcriber_v2 import _build_voice_timeline
        return _build_voice_timeline(sv_events, audio_duration)

    def test_empty_events(self):
        result = self._build([], 60.0)
        assert result["voice_regions"] == []
        assert result["non_voice_regions"] == [(0.0, 60.0)]
        assert result["voice_ratio"] == pytest.approx(0.0)

    def test_none_events(self):
        result = self._build(None, 60.0)
        assert result["voice_regions"] == []
        assert result["non_voice_regions"] == [(0.0, 60.0)]

    def test_all_speech(self):
        events = [{"start_s": 0.0, "end_s": 60.0, "event": "Speech"}]
        result = self._build(events, 60.0)
        assert result["voice_regions"] == [(0.0, 60.0)]
        assert result["non_voice_regions"] == []
        assert result["voice_ratio"] == pytest.approx(1.0)

    def test_all_music(self):
        events = [{"start_s": 0.0, "end_s": 60.0, "event": "Music"}]
        result = self._build(events, 60.0)
        assert result["voice_regions"] == []
        assert result["non_voice_regions"] == [(0.0, 60.0)]

    def test_mixed_events(self):
        events = [
            {"start_s": 0.0, "end_s": 10.0, "event": "Music"},
            {"start_s": 10.0, "end_s": 30.0, "event": "Speech"},
            {"start_s": 30.0, "end_s": 40.0, "event": "Applause"},
            {"start_s": 40.0, "end_s": 55.0, "event": "Speech"},
        ]
        result = self._build(events, 60.0)
        assert result["voice_regions"] == [(10.0, 30.0), (40.0, 55.0)]
        assert result["non_voice_regions"] == [(0.0, 10.0), (30.0, 40.0), (55.0, 60.0)]
        assert result["voice_ratio"] == pytest.approx(35.0 / 60.0, abs=0.01)

    def test_overlapping_speech_merged(self):
        events = [
            {"start_s": 5.0, "end_s": 15.0, "event": "Speech"},
            {"start_s": 12.0, "end_s": 25.0, "event": "Speech"},
        ]
        result = self._build(events, 30.0)
        assert result["voice_regions"] == [(5.0, 25.0)]
        assert result["non_voice_regions"] == [(0.0, 5.0), (25.0, 30.0)]

    def test_adjacent_speech_merged(self):
        """Gap <= 1s should be merged."""
        events = [
            {"start_s": 5.0, "end_s": 15.0, "event": "Speech"},
            {"start_s": 15.8, "end_s": 25.0, "event": "Speech"},
        ]
        result = self._build(events, 30.0)
        assert result["voice_regions"] == [(5.0, 25.0)]

    def test_non_adjacent_speech_separate(self):
        """Gap > 1s should NOT be merged."""
        events = [
            {"start_s": 5.0, "end_s": 15.0, "event": "Speech"},
            {"start_s": 17.0, "end_s": 25.0, "event": "Speech"},
        ]
        result = self._build(events, 30.0)
        assert result["voice_regions"] == [(5.0, 15.0), (17.0, 25.0)]

    def test_unsorted_events(self):
        events = [
            {"start_s": 20.0, "end_s": 30.0, "event": "Speech"},
            {"start_s": 5.0, "end_s": 15.0, "event": "Speech"},
        ]
        result = self._build(events, 30.0)
        assert result["voice_regions"] == [(5.0, 15.0), (20.0, 30.0)]

    def test_zero_duration(self):
        result = self._build([], 0.0)
        assert result["voice_regions"] == []
        assert result["non_voice_regions"] == []
        assert result["voice_ratio"] == pytest.approx(0.0)


class TestVoiceTimelineQuality:
    def test_reports_uncovered_voice_region_and_non_voice_short_fragment(self):
        from backend.transcriber_v2 import (
            Segment,
            summarize_voice_timeline_quality,
        )

        segments = [
            Segment(0, 1.0, 1.1, "あ"),
            Segment(1, 10.0, 11.0, "こんにちは"),
        ]
        voice_timeline = {
            "voice_regions": [(10.0, 12.0), (20.0, 24.0)],
            "non_voice_regions": [(0.0, 10.0), (12.0, 20.0)],
            "voice_ratio": 0.3,
        }

        summary = summarize_voice_timeline_quality(segments, voice_timeline)

        assert summary["voice_region_count"] == 2
        assert summary["uncovered_voice_region_count"] == 1
        assert summary["non_voice_short_fragment_count"] == 1
        assert summary["longest_uncovered_voice_region"] == (20.0, 24.0)
