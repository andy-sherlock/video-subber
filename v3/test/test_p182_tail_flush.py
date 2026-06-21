"""P182: pipeline chunk coverage tests (updated for independent Qwen mode).

Original P182 tested the producer-consumer queue flush. Since Qwen now runs
independently (no queue, no Whisper segment dependency), these tests verify
that run_qwen_asr's time-based chunking covers the full audio — no trailing
audio is dropped.

Run: cd v2 && .venv/bin/python -m pytest test/test_p182_tail_flush.py -v
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from backend.core.qwen_asr_runner import _chunk_by_time, _chunk_segments


class TestChunkByTime(unittest.TestCase):
    """Fixed-interval chunking covers full audio with no gaps or drops."""

    def test_short_audio_single_chunk(self):
        chunks = _chunk_by_time(60.0, max_duration=300)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], (0.0, 60.0))

    def test_exact_boundary(self):
        chunks = _chunk_by_time(600.0, max_duration=300)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], (0.0, 300.0))
        self.assertEqual(chunks[1], (300.0, 600.0))

    def test_trailing_remainder_not_dropped(self):
        """P182 core regression: trailing audio must not be dropped."""
        chunks = _chunk_by_time(620.0, max_duration=300)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[-1], (600.0, 620.0))
        # Verify full coverage
        self.assertEqual(chunks[0][0], 0.0)
        self.assertEqual(chunks[-1][1], 620.0)

    def test_very_short_audio(self):
        chunks = _chunk_by_time(0.5, max_duration=300)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], (0.0, 0.5))


class TestChunkSegmentsBackcompat(unittest.TestCase):
    """Segment-based chunking still works when segments are provided."""

    class FakeSeg:
        def __init__(self, start, end):
            self.start = start
            self.end = end

    def test_segments_chunked_at_boundaries(self):
        segs = [self.FakeSeg(i * 10, (i + 1) * 10) for i in range(50)]
        chunks = _chunk_segments(segs, max_duration=300)
        self.assertTrue(len(chunks) >= 1)
        # All segment indices covered
        covered = set()
        for start_idx, end_idx, _ts, _te in chunks:
            covered.update(range(start_idx, end_idx))
        self.assertEqual(covered, set(range(50)))


if __name__ == "__main__":
    unittest.main()
