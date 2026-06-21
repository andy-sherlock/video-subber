"""TDD: P228 — FA timestamp assignment via time-range matching.

RED phase: tests fail before code is written.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest


class FakeSegment:
    def __init__(self, id=0, start=0.0, end=1.0, text="hello"):
        self.id = id
        self.start = start
        self.end = end
        self.text = text


class TestP228TimestampTimeRange(unittest.TestCase):
    """P228: apply_aligner_timestamps should match aligned words by time range,
    not by word count.
    """

    def test_words_in_range_assigned_to_segment(self):
        """Aligned words whose timestamps fall within a segment's time range
        should be assigned to that segment, updating its start/end."""
        from backend.core.qwen_aligner import apply_aligner_timestamps

        seg = FakeSegment(id=0, start=10.0, end=15.0, text="hello world")
        seg.zh = "你好世界"
        seg_original_start = seg.start
        seg_original_end = seg.end

        # 3 aligned words: first two inside segment, third outside
        aligned = [
            {"word": "hello", "start": 10.2, "end": 10.6},
            {"word": "world", "start": 10.8, "end": 11.2},
            {"word": "extra", "start": 16.0, "end": 16.4},  # outside range
        ]

        result = apply_aligner_timestamps([seg], aligned)

        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].start, 10.2, places=2)
        self.assertAlmostEqual(result[0].end, 11.2, places=2)

    def test_no_alignment_words_in_range_leaves_segment_unchanged(self):
        """When no aligned words fall in a segment's time range,
        the segment keeps its original Whisper timestamps."""
        from backend.core.qwen_aligner import apply_aligner_timestamps

        seg = FakeSegment(id=0, start=10.0, end=15.0, text="hello")
        seg.zh = "你好"
        orig_start = seg.start
        orig_end = seg.end

        # All words outside the segment range
        aligned = [
            {"word": "other", "start": 5.0, "end": 5.5},
            {"word": "stuff", "start": 20.0, "end": 20.5},
        ]

        result = apply_aligner_timestamps([seg], aligned)

        self.assertEqual(result[0].start, orig_start)
        self.assertEqual(result[0].end, orig_end)

    def test_empty_aligned_words_noop(self):
        """Empty aligned words list should leave all segments unchanged."""
        from backend.core.qwen_aligner import apply_aligner_timestamps

        seg = FakeSegment(id=0, start=10.0, end=15.0, text="hello")
        seg.zh = "你好"

        result = apply_aligner_timestamps([seg], [])

        self.assertEqual(result[0].start, 10.0)
        self.assertEqual(result[0].end, 15.0)

    def test_none_aligned_words_noop(self):
        """None aligned words should leave all segments unchanged."""
        from backend.core.qwen_aligner import apply_aligner_timestamps

        seg = FakeSegment(id=0, start=10.0, end=15.0, text="hello")
        seg.zh = "你好"

        result = apply_aligner_timestamps([seg], None)

        self.assertEqual(result[0].start, 10.0)
        self.assertEqual(result[0].end, 15.0)

    def test_split_segment_receives_correct_words(self):
        """Each segment only gets words whose timestamps fall within its range."""
        from backend.core.qwen_aligner import apply_aligner_timestamps

        seg1 = FakeSegment(id=0, start=10.0, end=15.0, text="hello")
        seg1.zh = "你好"
        seg2 = FakeSegment(id=1, start=18.0, end=22.0, text="world")
        seg2.zh = "世界"

        aligned = [
            {"word": "hello", "start": 10.5, "end": 10.9},
            {"word": "extra", "start": 14.0, "end": 14.3},
            # gap — no words between 15 and 18
            {"word": "world", "start": 19.0, "end": 19.5},
        ]

        result = apply_aligner_timestamps([seg1, seg2], aligned)

        self.assertAlmostEqual(result[0].start, 10.5, places=2)
        self.assertAlmostEqual(result[0].end, 14.3, places=2)
        self.assertAlmostEqual(result[1].start, 19.0, places=2)
        self.assertAlmostEqual(result[1].end, 19.5, places=2)

    def test_overlapping_word_assigned_to_both_segments(self):
        """A word whose time overlaps two segments should be considered
        for whichever segment's range it falls into."""
        from backend.core.qwen_aligner import apply_aligner_timestamps

        seg1 = FakeSegment(id=0, start=10.0, end=15.0, text="A")
        seg1.zh = "甲"
        seg2 = FakeSegment(id=1, start=14.0, end=20.0, text="B")
        seg2.zh = "乙"

        aligned = [
            {"word": "shared", "start": 14.5, "end": 15.0},  # overlaps both
        ]

        result = apply_aligner_timestamps([seg1, seg2], aligned)

        # seg1: word at 14.5 falls in [10, 15) → yes
        # seg2: word at 14.5 falls in [14, 20) → yes
        self.assertAlmostEqual(result[0].start, 14.5, places=2)
        self.assertAlmostEqual(result[0].end, 15.0, places=2)
        self.assertAlmostEqual(result[1].start, 14.5, places=2)
        self.assertAlmostEqual(result[1].end, 15.0, places=2)

    def test_single_word_segment(self):
        """Segment with a single aligned word gets that word's timestamps."""
        from backend.core.qwen_aligner import apply_aligner_timestamps

        seg = FakeSegment(id=0, start=5.0, end=10.0, text="ok")
        seg.zh = "好的"

        aligned = [
            {"word": "ok", "start": 6.0, "end": 6.5},
        ]

        result = apply_aligner_timestamps([seg], aligned)

        self.assertAlmostEqual(result[0].start, 6.0, places=2)
        self.assertAlmostEqual(result[0].end, 6.5, places=2)


if __name__ == "__main__":
    unittest.main()
