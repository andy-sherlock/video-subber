"""TDD tests for P171: LOW_DATA window status when stem count < MIN_STEMS.

P251: Qwen text reaches L2 via a qwen_chunks list, not per-segment qwen_text.

Run: cd v3 && .venv/bin/python -m pytest test/test_p171_low_data.py -v
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest


def _make_seg(start, end, text, sensevoice_tag="Speech"):
    class Seg:
        def __init__(self):
            self.start = start
            self.end = end
            self.text = text
            self.sensevoice_tag = sensevoice_tag
            self.filtered = False
            self.uncertain = False
            self.filter_reason = None
    return Seg()


def _qc(text, start, end):
    return [{"text": text, "t_start": start, "t_end": end}] if text else []


class TestLowDataWindow(unittest.TestCase):
    """P171: Windows with fewer than MIN_STEMS (5) should get LOW_DATA status."""

    def test_few_stems_gives_low_data(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        seg = _make_seg(0, 5, "hi ok")
        segs, stats = _layer2_cross_validate([seg], "en", qwen_chunks=_qc("hi ok", 0, 5))
        self.assertEqual(stats[0]["status"], "LOW_DATA")

    def test_low_data_marks_uncertain(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        seg = _make_seg(0, 5, "hi ok")
        segs, stats = _layer2_cross_validate([seg], "en", qwen_chunks=_qc("hi ok", 0, 5))
        self.assertTrue(segs[0].uncertain)

    def test_enough_stems_gives_green(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        text = "the quick brown fox jumps over the lazy dog runs fast"
        seg = _make_seg(0, 10, text)
        segs, stats = _layer2_cross_validate([seg], "en", qwen_chunks=_qc(text, 0, 10))
        self.assertEqual(stats[0]["status"], "GREEN")

    def test_enough_stems_mismatch_gives_yellow(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        w_text = "the quick brown fox jumps over lazy dog runs fast"
        q_text = "alpha beta gamma delta epsilon zeta eta theta iota kappa"
        seg = _make_seg(0, 10, w_text)
        segs, stats = _layer2_cross_validate([seg], "en", qwen_chunks=_qc(q_text, 0, 10))
        self.assertEqual(stats[0]["status"], "YELLOW")

    def test_window_stats_include_counts(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        text = "hello world test data sample"
        seg = _make_seg(0, 10, text)
        segs, stats = _layer2_cross_validate([seg], "en", qwen_chunks=_qc(text, 0, 10))
        self.assertIn("w_count", stats[0])
        self.assertIn("q_count", stats[0])
        self.assertIn("shared", stats[0])

    def test_chinese_few_chars_low_data(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        seg = _make_seg(0, 5, "你好")
        segs, stats = _layer2_cross_validate([seg], "zh", qwen_chunks=_qc("你好", 0, 5))
        self.assertEqual(stats[0]["status"], "LOW_DATA")


if __name__ == "__main__":
    unittest.main()
