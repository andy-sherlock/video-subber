"""TDD tests for rewritten _layer2_cross_validate (P160 B+A architecture).

P251: Qwen text reaches L2 via a qwen_chunks list ({text, t_start, t_end}),
not per-segment qwen_text. Tests build chunks with _qc() instead.

Run: cd v3 && .venv/bin/python -m pytest test/test_p160_layer2.py -v
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest


class FakeSegment:
    def __init__(self, id=0, start=0.0, end=1.0, text="", sensevoice_tag=None):
        self.id = id
        self.start = start
        self.end = end
        self.text = text
        self.sensevoice_tag = sensevoice_tag
        self.filtered = False
        self.filter_reason = None
        self.uncertain = False
        self.words = []


def _make_seg(id=0, start=0.0, end=1.0, text="", **kw):
    return FakeSegment(id=id, start=start, end=end, text=text, **kw)


def _qc(*triples):
    """Build a qwen_chunks list from (text, t_start, t_end) triples."""
    return [{"text": t, "t_start": s, "t_end": e} for (t, s, e) in triples if t]


class TestLayer2DualConfirm(unittest.TestCase):
    """L2 never auto-deletes; it only annotates windows."""

    def test_both_agree_nonhuman_red(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        seg = _make_seg(0, 5, 8, "hello", sensevoice_tag="Breath")
        result, _ = _layer2_cross_validate([seg], language="en", qwen_chunks=[])
        self.assertEqual(result[0].uncertain, False)

    def test_nonhuman_with_qwen_text_not_auto_deleted(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        seg = _make_seg(0, 5, 8, "hello", sensevoice_tag="BGM")
        result, _ = _layer2_cross_validate([seg], language="ru",
                                           qwen_chunks=_qc(("привет", 5, 8)))
        # Must NOT auto-delete — L2 only flags, never filters.
        self.assertFalse(result[0].filtered)

    def test_speech_tag_continues_to_window(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        seg = _make_seg(0, 5, 8, "hello", sensevoice_tag="Speech")
        result, _ = _layer2_cross_validate([seg], language="en",
                                           qwen_chunks=_qc(("hello", 5, 8)))
        self.assertFalse(result[0].filtered)

    def test_missing_sensevoice_tag_defaults_to_speech(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        seg = _make_seg(0, 5, 8, "hello")
        result, _ = _layer2_cross_validate([seg], language="en",
                                           qwen_chunks=_qc(("hello", 5, 8)))
        self.assertFalse(result[0].filtered)


class TestLayer2ChineseWindowMatch(unittest.TestCase):
    """Chinese: character-level comparison in 30s windows."""

    def test_identical_text_green(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        seg = _make_seg(0, 5, 8, "你好世界欢迎来到这里")
        result, stats = _layer2_cross_validate(
            [seg], language="zh", qwen_chunks=_qc(("你好世界欢迎来到这里", 5, 8)))
        win = stats[0]
        self.assertEqual(win["status"], "GREEN",
                         f"Expected GREEN for identical text, got {win}")

    def test_different_text_yellow(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        seg = _make_seg(0, 5, 8, "你好世界欢迎来到")
        result, stats = _layer2_cross_validate(
            [seg], language="zh", qwen_chunks=_qc(("再见朋友们谢谢大家", 5, 8)))
        win = stats[0]
        self.assertEqual(win["status"], "YELLOW",
                         f"Expected YELLOW for different text, got {win}")

    def test_empty_window(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        result, stats = _layer2_cross_validate([], language="zh")
        self.assertEqual(stats, {})

    def test_both_empty_text_empty_window(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        seg = _make_seg(0, 5, 8, "")
        result, stats = _layer2_cross_validate([seg], language="zh", qwen_chunks=[])
        self.assertEqual(stats[0]["status"], "EMPTY")


class TestLayer2WindowAssignment(unittest.TestCase):
    """Segments land in the correct 30s window."""

    def test_segment_in_single_window(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        seg = _make_seg(0, 10, 15, "test")
        result, stats = _layer2_cross_validate(
            [seg], language="en", qwen_chunks=_qc(("test", 10, 15)))
        # 10-15s → window 0 (0-30s)
        self.assertIn(0, stats)
        self.assertNotEqual(stats[0]["status"], "EMPTY")

    def test_two_windows_produced(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        s1 = _make_seg(0, 5, 8, "hello")
        s2 = _make_seg(1, 35, 38, "world")
        result, stats = _layer2_cross_validate(
            [s1, s2], language="en",
            qwen_chunks=_qc(("hello", 5, 8), ("world", 35, 38)))
        # 5-8s → window 0, 35-38s → window 1
        self.assertIn(0, stats)
        self.assertIn(1, stats)


class TestLayer2ThresholdPerLanguage(unittest.TestCase):
    """Each language uses its own stem-overlap threshold."""

    def test_chinese_uses_higher_threshold(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        # 6 unique chars each, 0 shared → score 0.0, threshold 0.55 → YELLOW
        seg = _make_seg(0, 5, 8, "你我他她它们")
        result, stats = _layer2_cross_validate(
            [seg], language="zh", qwen_chunks=_qc(("天地人山水云", 5, 8)))
        self.assertEqual(stats[0]["status"], "YELLOW")


if __name__ == "__main__":
    unittest.main()
