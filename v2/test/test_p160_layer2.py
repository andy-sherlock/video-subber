"""TDD tests for rewritten _layer2_cross_validate (P160 B+A architecture).

Run: cd v2 && .venv/bin/python -m pytest test/test_p160_layer2.py -v
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest


class FakeSegment:
    def __init__(self, id=0, start=0.0, end=1.0, text="",
                 sensevoice_tag=None, qwen_text=None):
        self.id = id
        self.start = start
        self.end = end
        self.text = text
        self.sensevoice_tag = sensevoice_tag
        self.qwen_text = qwen_text
        self.filtered = False
        self.filter_reason = None
        self.uncertain = False
        self.words = []


def _make_seg(id=0, start=0.0, end=1.0, text="", **kw):
    return FakeSegment(id=id, start=start, end=end, text=text, **kw)


class TestLayer2DualConfirm(unittest.TestCase):
    """SenseVoice + Qwen dual-confirm before window comparison."""

    def test_both_agree_nonhuman_red(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        seg = _make_seg(0, 5, 8, "hello", sensevoice_tag="Breath", qwen_text="")
        result, _ = _layer2_cross_validate([seg], language="en")
        self.assertEqual(result[0].uncertain, False)

    def test_nonhuman_with_qwen_text_not_auto_deleted(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        seg = _make_seg(0, 5, 8, "hello", sensevoice_tag="BGM",
                        qwen_text="привет")
        result, _ = _layer2_cross_validate([seg], language="ru")
        # Must NOT auto-delete — disagreement between SV and Qwen
        self.assertFalse(result[0].filtered)

    def test_speech_tag_continues_to_window(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        seg = _make_seg(0, 5, 8, "hello", sensevoice_tag="Speech",
                        qwen_text="hello")
        result, _ = _layer2_cross_validate([seg], language="en")
        # Speech should pass through (GREEN or YELLOW, not RED)
        self.assertFalse(result[0].filtered)

    def test_missing_sensevoice_tag_defaults_to_speech(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        seg = _make_seg(0, 5, 8, "hello", qwen_text="hello")
        result, _ = _layer2_cross_validate([seg], language="en")
        self.assertFalse(result[0].filtered)


class TestLayer2ChineseWindowMatch(unittest.TestCase):
    """Chinese: character-level comparison in 30s windows."""

    def test_identical_text_green(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        seg = _make_seg(0, 5, 8, "你好世界欢迎来到这里", qwen_text="你好世界欢迎来到这里")
        result, stats = _layer2_cross_validate([seg], language="zh")
        win = stats[0]
        self.assertEqual(win["status"], "GREEN",
                         f"Expected GREEN for identical text, got {win}")

    def test_different_text_yellow(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        seg = _make_seg(0, 5, 8, "你好世界欢迎来到", qwen_text="再见朋友们谢谢大家")
        result, stats = _layer2_cross_validate([seg], language="zh")
        win = stats[0]
        self.assertEqual(win["status"], "YELLOW",
                         f"Expected YELLOW for different text, got {win}")

    def test_empty_window(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        result, stats = _layer2_cross_validate([], language="zh")
        self.assertEqual(stats, {})

    def test_both_empty_text_empty_window(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        seg = _make_seg(0, 5, 8, "", qwen_text="")
        result, stats = _layer2_cross_validate([seg], language="zh")
        self.assertEqual(stats[0]["status"], "EMPTY")


class TestLayer2WindowAssignment(unittest.TestCase):
    """Segments land in the correct 30s window."""

    def test_segment_in_single_window(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        seg = _make_seg(0, 10, 15, "test", qwen_text="test")
        result, stats = _layer2_cross_validate([seg], language="en")
        # 10-15s → window 0 (0-30s)
        self.assertIn(0, stats)
        self.assertNotEqual(stats[0]["status"], "EMPTY")

    def test_two_windows_produced(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        s1 = _make_seg(0, 5, 8, "hello", qwen_text="hello")
        s2 = _make_seg(1, 35, 38, "world", qwen_text="world")
        result, stats = _layer2_cross_validate([s1, s2], language="en")
        # 5-8s → window 0, 35-38s → window 1
        self.assertIn(0, stats)
        self.assertIn(1, stats)


class TestLayer2ThresholdPerLanguage(unittest.TestCase):
    """Each language uses its own stem-overlap threshold."""

    def test_chinese_uses_higher_threshold(self):
        from backend.core.hallucination_filter import _layer2_cross_validate
        # 6 unique chars each, 0 shared → score 0.0, threshold 0.55 → YELLOW
        seg = _make_seg(0, 5, 8, "你我他她它们", qwen_text="天地人山水云")
        result, stats = _layer2_cross_validate([seg], language="zh")
        self.assertEqual(stats[0]["status"], "YELLOW")


if __name__ == "__main__":
    unittest.main()
