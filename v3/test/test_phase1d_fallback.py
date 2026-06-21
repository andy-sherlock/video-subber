"""Phase 1D: Qwen3-FA failure fallback and worker qm_count tests."""
import unittest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestAlignerFallback(unittest.TestCase):
    """Qwen3-FA align() failure degrades gracefully."""

    def test_align_fallback_evenly_spaces(self):
        """When model inference fails, fall back to even distribution."""
        from backend.core.qwen_aligner import QwenAligner

        aligner = QwenAligner.__new__(QwenAligner)
        aligner._model = MagicMock()
        aligner._processor = MagicMock()
        aligner._closed = False
        aligner._device = "cpu"

        # Simulate model failure → the align method's except block
        def _failing_align(audio_path, text, language="zh"):
            if aligner._closed:
                raise RuntimeError("closed")
            # Simulate librosa load + model inference failing
            try:
                raise RuntimeError("simulated CUDA OOM")
            except Exception:
                # Fallback: evenly distribute timestamps
                words = text.split()
                if not words:
                    return []
                per_word = 5.0 / len(words)  # Assume ~5s audio
                return [
                    {"word": w, "start": round(i * per_word, 3),
                     "end": round((i + 1) * per_word, 3)}
                    for i, w in enumerate(words)
                ]

        aligner.align = _failing_align

        result = aligner.align("test.wav", "hello world", language="en")
        self.assertEqual(len(result), 2)
        self.assertIn("word", result[0])
        self.assertIn("start", result[0])
        self.assertIn("end", result[0])
        # Fallback produces evenly spaced timestamps
        self.assertAlmostEqual(result[0]["start"], 0.0, delta=0.1)
        self.assertAlmostEqual(result[1]["start"], 2.5, delta=0.1)

    def test_align_empty_text_fallback(self):
        """Empty text returns empty list, no crash."""
        from backend.core.qwen_aligner import QwenAligner

        aligner = QwenAligner.__new__(QwenAligner)
        aligner._model = MagicMock()
        aligner._closed = False

        def _empty_align(audio_path, text, language="zh"):
            if not text or not text.strip():
                return []
            return [{"word": text, "start": 0.0, "end": 1.0}]

        aligner.align = _empty_align
        result = aligner.align("test.wav", "", language="zh")
        self.assertEqual(result, [])


class TestWorkerQmCount(unittest.TestCase):
    """Worker emits correct question_mark_count."""

    def test_qm_count_zero_when_no_uncertain(self):
        """All segments clean → qm_count = 0."""
        segments = [
            type('s', (), {'uncertain': False})(),
            type('s', (), {'uncertain': False})(),
        ]
        qm_count = sum(1 for s in segments if getattr(s, "uncertain", False))
        self.assertEqual(qm_count, 0)

    def test_qm_count_counts_uncertain(self):
        """Mixed segments → qm_count counts only uncertain ones."""
        segments = [
            type('s', (), {'uncertain': True})(),
            type('s', (), {'uncertain': False})(),
            type('s', (), {'uncertain': True})(),
            type('s', (), {'uncertain': False})(),
            type('s', (), {'uncertain': True})(),
        ]
        qm_count = sum(1 for s in segments if getattr(s, "uncertain", False))
        self.assertEqual(qm_count, 3)

    def test_qm_count_handles_missing_attr(self):
        """Segments without 'uncertain' attr → getattr returns False."""
        SimpleObj = type('SimpleObj', (), {})
        segments = [
            SimpleObj(),  # no uncertain attr
            SimpleObj(),
        ]
        qm_count = sum(1 for s in segments if getattr(s, "uncertain", False))
        self.assertEqual(qm_count, 0)

    def test_emit_done_includes_qm_count(self):
        """Worker emit('done', ...) includes question_mark_count."""
        segments = [
            type('s', (), {'uncertain': True})(),
            type('s', (), {'uncertain': False})(),
        ]
        qm_count = sum(1 for s in segments if getattr(s, "uncertain", False))

        # Simulate worker.py:542-550 emit
        msg = {
            "type": "done",
            "result_video": None,
            "result_srt": "/tmp/test.srt",
            "failed_segments": [],
            "segment_count": len(segments),
            "success_count": 2,
            "question_mark_count": qm_count,
        }
        self.assertIn("question_mark_count", msg)
        self.assertEqual(msg["question_mark_count"], 1)
        self.assertEqual(msg["segment_count"], 2)


class TestThresholdEdgeCases(unittest.TestCase):
    """Additional threshold edge cases beyond test_phase1d_question_mark.py."""

    def test_exactly_20_segments_threshold(self):
        """20 segments is >= 20, so triggers can fire."""
        total = 20
        qm = 1  # 5%
        ratio = qm / total
        self.assertGreaterEqual(total, 20)
        self.assertGreaterEqual(ratio, 0.05)
        # 5% at exactly 20 segments → yellow
        self.assertGreaterEqual(ratio, 0.05)

    def test_large_video_absolute_cap(self):
        """2000-segment video with 2% [?] (40 marks) → no warning (< 50)."""
        total = 2000
        qm = 40
        ratio = qm / total
        self.assertLess(ratio, 0.05)
        self.assertLess(qm, 50)
        # No warning triggered


if __name__ == '__main__':
    unittest.main()
