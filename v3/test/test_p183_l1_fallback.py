"""TDD regression tests for P183: L1 single-signal fallback on FA failure.

Bug: In _layer1_dual_signal(), when aligner_result is None/empty, it returns False
(skip L1) without checking SenseVoice tags. Non-human audio tagged by SenseVoice
(e.g., "Music", "BGM", "Laughter") is NOT filtered when FA is unavailable.

Run: cd v3 && .venv/bin/python -m pytest test/test_p183_l1_fallback.py -v
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest


class FakeSegment:
    """Minimal segment matching what _layer1_dual_signal reads."""
    def __init__(self, text="hello", sensevoice_tag=None, words=None):
        self.text = text
        self.sensevoice_tag = sensevoice_tag
        self.words = words or []
        self.filtered = False
        self.filter_reason = None
        self.uncertain = False
        self.start = 0.0
        self.end = 1.0


class TestL1SingleSignalFallback(unittest.TestCase):
    """Verify L1 still filters non-Speech audio when FA is unavailable."""

    def test_fa_none_sv_nonhuman_should_discard(self):
        """FA=None + SV=Music → must discard. Currently BUG: returns False (keep)."""
        from backend.core.hallucination_filter import _layer1_dual_signal

        seg = FakeSegment(text="♪", sensevoice_tag="Music")

        result = _layer1_dual_signal(seg, aligner_result=None)

        self.assertTrue(
            result,
            "P183 BUG: L1 should filter non-Speech when FA=None, "
            f"but returned {result} (kept segment with tag={seg.sensevoice_tag!r})"
        )
        self.assertTrue(seg.filtered, "seg.filtered should be True after L1 filtering")

    def test_fa_empty_sv_nonhuman_should_discard(self):
        """FA=[] + SV=Applause → must discard. Empty aligner same as None."""
        from backend.core.hallucination_filter import _layer1_dual_signal

        seg = FakeSegment(text="clap", sensevoice_tag="Applause")

        result = _layer1_dual_signal(seg, aligner_result=[])

        self.assertTrue(
            result,
            "P183 BUG: L1 should filter non-Speech when FA=[], "
            f"but returned {result}"
        )

    def test_fa_none_sv_speech_should_keep(self):
        """FA=None + SV=Speech → keep (not filtered)."""
        from backend.core.hallucination_filter import _layer1_dual_signal

        seg = FakeSegment(text="hello world", sensevoice_tag="Speech")

        result = _layer1_dual_signal(seg, aligner_result=None)

        self.assertFalse(result, "Speech with FA=None should not be filtered")
        self.assertFalse(seg.filtered)

    def test_fa_none_sv_tag_none_skip(self):
        """FA=None + SV tag=None → skip L1 (no SV data at all)."""
        from backend.core.hallucination_filter import _layer1_dual_signal

        seg = FakeSegment(text="test", sensevoice_tag=None)

        result = _layer1_dual_signal(seg, aligner_result=None)

        self.assertFalse(result, "No SV tag at all should skip L1")


if __name__ == "__main__":
    unittest.main()
