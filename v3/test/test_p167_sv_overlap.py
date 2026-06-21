"""TDD tests for P167: SenseVoice containment → overlap matching.

Written BEFORE code changes. Tests verify boundary segments get tagged.
"""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class DummySeg:
    def __init__(self, text, start=0.0, end=1.0):
        self.text = text
        self.start = start
        self.end = end
        self.sensevoice_tag = None
        self.filtered = False
        self.filter_reason = None
        self.uncertain = False
        self.words = []
        self.qwen_text = None
        self.id = 0
        self.speaker_change = False
        self.zh = ""


def _apply_sv_tags(segments, sv_events):
    """Simulate the SenseVoice tag application from transcriber_v2.py."""
    for seg in segments:
        seg.sensevoice_tag = None
        for ev in sv_events:
            if ev["start_s"] <= seg.start and seg.end <= ev["end_s"]:
                seg.sensevoice_tag = ev["event"]
                break


def _apply_sv_tags_overlap(segments, sv_events):
    """Expected behavior: use overlap instead of containment."""
    for seg in segments:
        seg.sensevoice_tag = None
        best_overlap = 0.0
        best_event = None
        for ev in sv_events:
            overlap_start = max(seg.start, ev["start_s"])
            overlap_end = min(seg.end, ev["end_s"])
            overlap = max(0.0, overlap_end - overlap_start)
            if overlap > best_overlap:
                best_overlap = overlap
                best_event = ev["event"]
        seg_dur = seg.end - seg.start
        if best_overlap > 0 and seg_dur > 0 and best_overlap / seg_dur >= 0.5:
            seg.sensevoice_tag = best_event


class TestP167_OverlapMatching(unittest.TestCase):
    """SenseVoice tag assignment should use overlap, not containment."""

    def test_boundary_segment_gets_tagged(self):
        """Segment straddling event boundary should still be tagged."""
        seg = DummySeg("some music text", start=4.5, end=7.0)
        sv_events = [
            {"start_s": 5.0, "end_s": 10.0, "event": "music"},
        ]

        _apply_sv_tags([seg], sv_events)

        # Current containment: NOT tagged (4.5 < 5.0)
        # After overlap fix: tagged as music (2.0s overlap out of 2.5s = 80%)
        self.assertIsNone(seg.sensevoice_tag,
                          "Current containment should miss boundary segment")

        # Verify the overlap version works
        seg2 = DummySeg("some music text", start=4.5, end=7.0)
        _apply_sv_tags_overlap([seg2], sv_events)
        self.assertEqual(seg2.sensevoice_tag, "music",
                         "Overlap should tag boundary segment")

    def test_fully_contained_still_tagged(self):
        """Segment fully inside event should still be tagged (regression)."""
        seg = DummySeg("speech text", start=6.0, end=8.0)
        sv_events = [
            {"start_s": 5.0, "end_s": 10.0, "event": "Speech"},
        ]

        _apply_sv_tags([seg], sv_events)
        self.assertEqual(seg.sensevoice_tag, "Speech")

    def test_no_overlap_not_tagged(self):
        """Segment with zero overlap should not be tagged."""
        seg = DummySeg("text", start=0.0, end=2.0)
        sv_events = [
            {"start_s": 5.0, "end_s": 10.0, "event": "music"},
        ]

        _apply_sv_tags_overlap([seg], sv_events)
        self.assertIsNone(seg.sensevoice_tag)

    def test_minimal_overlap_not_tagged(self):
        """Segment with <50% overlap should not be tagged."""
        seg = DummySeg("text", start=0.0, end=6.0)
        sv_events = [
            {"start_s": 5.0, "end_s": 10.0, "event": "music"},
        ]

        _apply_sv_tags_overlap([seg], sv_events)
        # 1.0s overlap / 6.0s duration = 16.7% < 50%
        self.assertIsNone(seg.sensevoice_tag)

    def test_best_overlap_wins(self):
        """Segment overlapping two events gets the one with more overlap."""
        seg = DummySeg("text", start=9.0, end=12.0)
        sv_events = [
            {"start_s": 5.0, "end_s": 10.0, "event": "music"},
            {"start_s": 10.0, "end_s": 15.0, "event": "Speech"},
        ]

        _apply_sv_tags_overlap([seg], sv_events)
        # music: 1.0s overlap (9-10), Speech: 2.0s overlap (10-12)
        self.assertEqual(seg.sensevoice_tag, "Speech",
                         "Speech has more overlap and should win")


if __name__ == '__main__':
    unittest.main()
