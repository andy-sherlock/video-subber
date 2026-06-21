"""TDD: P229 — L2 dynamic threshold for BGM segments.

RED phase: tests fail before code is written.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest


class FakeSegForL2:
    def __init__(self, id=0, start=0.0, end=1.0, text="hello",
                 qwen_text=None, sensevoice_tag=None, uncertain=False,
                 bgm_override=False):
        self.id = id
        self.start = start
        self.end = end
        self.text = text
        self.qwen_text = qwen_text
        self.sensevoice_tag = sensevoice_tag
        self.uncertain = uncertain
        self.filtered = False
        self.filter_reason = None
        self.bgm_override = bgm_override


class TestP229BGMDynamicThreshold(unittest.TestCase):
    """P229: L2 should use a lower threshold (0.20) for windows
    containing segments that were L1 SV-override (BGM background).
    """

    def test_all_bgm_window_uses_low_threshold(self):
        """All segments in window have bgm_override=True → use 0.20 threshold."""
        from backend.core.hallucination_filter import _layer2_cross_validate

        segs = [
            FakeSegForL2(id=0, start=0.0, end=5.0,
                         text="hello world the cat sat on the mat",
                         bgm_override=True,
                         qwen_text="hello world the cat sat on the mat"),
            FakeSegForL2(id=1, start=5.0, end=10.0,
                         text="good morning dogs run very fast now",
                         bgm_override=True,
                         qwen_text="good morning dogs run very fast now"),
        ]

        results, stats = _layer2_cross_validate(segs, language="en")

        yellow_count = sum(1 for s in results if getattr(s, 'uncertain', False))
        self.assertEqual(yellow_count, 0, "Perfect match should stay GREEN regardless")

    def test_mixed_bgm_and_normal_window_uses_low_threshold(self):
        """Window with at least one BGM segment → use the lower threshold."""
        from backend.core.hallucination_filter import _layer2_cross_validate

        segs = [
            FakeSegForL2(id=0, start=0.0, end=5.0,
                         text="hello world this is a test sentence",
                         bgm_override=True,
                         qwen_text="different words completely unrelated stuff"),
            FakeSegForL2(id=1, start=5.0, end=10.0,
                         text="world peace is very important today",
                         sensevoice_tag="Speech",
                         qwen_text="world peace is very important today"),
        ]

        results, stats = _layer2_cross_validate(segs, language="en")
        self.assertIsNotNone(results)

    def test_no_bgm_window_uses_standard_threshold(self):
        """All segments have Speech tag → use standard 0.35 threshold."""
        from backend.core.hallucination_filter import _layer2_cross_validate

        # Need ≥5 stems to avoid LOW_DATA (MIN_STEMS=5)
        segs = [
            FakeSegForL2(id=0, start=0.0, end=5.0,
                         text="the cat sat on the big blue mat",
                         sensevoice_tag="Speech",
                         qwen_text="the cat sat on the big blue mat"),
            FakeSegForL2(id=1, start=5.0, end=10.0,
                         text="dogs are running very fast today",
                         sensevoice_tag="Speech",
                         qwen_text="dogs are running very fast today"),
        ]

        results, stats = _layer2_cross_validate(segs, language="en")

        yellow_count = sum(1 for s in results if getattr(s, 'uncertain', False))
        self.assertEqual(yellow_count, 0, "Perfect match should be GREEN")

    def test_low_overlap_bgm_window_not_yellow(self):
        """A BGM window with borderline overlap (0.20-0.35) should stay GREEN,
        not YELLOW, because lower threshold applies."""
        from backend.core.hallucination_filter import _layer2_cross_validate

        # "testing words now the cat sat on mat" vs "different words here the dog ran away"
        # Share "words" (1 stem), total ~7 stems each → ~0.14 overlap
        # Under 0.20 threshold → YELLOW.
        # Actually need overlap >0.20 for GREEN. Let's craft:
        # "testing my words now the cat sat" vs "different my words here the dog ran"
        # Share "my", "words" (2 stems), total ~6-7 stems each → ~0.29-0.33
        segs = [
            FakeSegForL2(id=0, start=0.0, end=5.0,
                         text="testing my words now the cat sat",
                         bgm_override=True,
                         qwen_text="different my words here the dog ran"),
        ]

        results, stats = _layer2_cross_validate(segs, language="en")

        # BGM override → threshold=0.20. Overlap ~2/6≈0.33 > 0.20 → GREEN
        is_uncertain = getattr(results[0], 'uncertain', False)
        self.assertFalse(is_uncertain,
                         "Borderline overlap with BGM should be GREEN under 0.20 threshold")


if __name__ == "__main__":
    unittest.main()
