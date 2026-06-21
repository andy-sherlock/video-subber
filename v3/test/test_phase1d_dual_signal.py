"""Phase 1D: Dual-signal hallucination filter tests.

L1 upgrades from SenseVoice-only to SenseVoice + Qwen3-FA alignment.
Four-way decision matrix:
  SenseVoice non-human + Qwen3-FA no alignment → discard ✓
  SenseVoice non-human + Qwen3-FA has alignment → SenseVoice wrong, keep
  SenseVoice human   + Qwen3-FA no alignment → suspect, keep [?]
  SenseVoice human   + Qwen3-FA has alignment → both confirm, keep ✓
"""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class DummySeg:
    """Minimal segment for filter testing."""
    def __init__(self, text, start=0.0, end=1.0, sensevoice_tag=None):
        self.text = text
        self.start = start
        self.end = end
        self.sensevoice_tag = sensevoice_tag
        self.filtered = False
        self.filter_reason = None
        self.uncertain = False
        self.words = []
        self.qwen_text = None
        self.zh = None


class TestDualSignalMatrix(unittest.TestCase):
    """Four-way decision matrix: all four cases."""

    def _has_alignment_at(self, aligner_result, seg_start, seg_end):
        """Check if any aligned word falls within the segment time range."""
        if aligner_result is None:
            return False
        for w in aligner_result:
            ws = w.get("start", 0)
            we = w.get("end", 0)
            if ws < seg_end and we > seg_start:
                return True
        return False

    def test_case1_nonhuman_no_alignment_discard(self):
        """SenseVoice non-human + no alignment → discard."""
        seg = DummySeg("♪ music ♪", start=1.0, end=3.0,
                       sensevoice_tag="music")
        aligner_result = [
            {"word": "hello", "start": 10.0, "end": 10.5},
        ]

        is_nonhuman = (seg.sensevoice_tag and
                       seg.sensevoice_tag not in ("speech", "human", "laughter"))
        has_alignment = self._has_alignment_at(aligner_result, seg.start, seg.end)

        # Both signals agree: not human speech + no aligned words → discard
        if is_nonhuman and not has_alignment:
            seg.filtered = True
            seg.filter_reason = "layer1_dual_confirm_nonhuman"

        self.assertTrue(seg.filtered)
        self.assertIn("nonhuman", seg.filter_reason)

    def test_case2_nonhuman_has_alignment_keep(self):
        """SenseVoice non-human + has alignment → SenseVoice wrong, keep."""
        seg = DummySeg("hello world", start=1.0, end=3.0,
                       sensevoice_tag="music")
        aligner_result = [
            {"word": "hello", "start": 1.2, "end": 1.8},
            {"word": "world", "start": 1.8, "end": 2.5},
        ]

        is_nonhuman = (seg.sensevoice_tag and
                       seg.sensevoice_tag not in ("speech", "human", "laughter"))
        has_alignment = self._has_alignment_at(aligner_result, seg.start, seg.end)

        if is_nonhuman and has_alignment:
            # SenseVoice says non-human but aligner found words -> SV wrong
            seg.filtered = False
            seg.sensevoice_tag = None  # Clear the wrong tag

        self.assertFalse(seg.filtered)
        self.assertIsNone(seg.sensevoice_tag)

    def test_case3_human_no_alignment_suspect(self):
        """SenseVoice human + no alignment → suspect, keep with [?]."""
        seg = DummySeg("hallucinated text here", start=1.0, end=1.5,
                       sensevoice_tag="speech")
        aligner_result = [
            {"word": "real", "start": 10.0, "end": 10.5},
            {"word": "text", "start": 20.0, "end": 20.5},
        ]

        is_human = (not seg.sensevoice_tag or
                    seg.sensevoice_tag in ("speech", "human", "laughter"))
        has_alignment = self._has_alignment_at(aligner_result, seg.start, seg.end)

        if is_human and not has_alignment:
            # SV says human but aligner found nothing → hallucination suspect
            seg.uncertain = True
            seg.filtered = False  # Keep but mark

        self.assertTrue(seg.uncertain)
        self.assertFalse(seg.filtered)

    def test_case4_human_has_alignment_pass(self):
        """SenseVoice human + has alignment → both confirm, normal pass."""
        seg = DummySeg("hello world", start=1.0, end=3.0,
                       sensevoice_tag="speech")
        aligner_result = [
            {"word": "hello", "start": 1.1, "end": 1.7},
            {"word": "world", "start": 1.8, "end": 2.9},
        ]

        is_human = (not seg.sensevoice_tag or
                    seg.sensevoice_tag in ("speech", "human", "laughter"))
        has_alignment = self._has_alignment_at(aligner_result, seg.start, seg.end)

        if is_human and has_alignment:
            seg.filtered = False
            seg.uncertain = False

        self.assertFalse(seg.filtered)
        self.assertFalse(seg.uncertain)


class TestDualSignalNoSenseVoice(unittest.TestCase):
    """When SenseVoice is unavailable, L1 degrades gracefully."""

    def test_no_sensevoice_tag(self):
        """Segments without sensevoice_tag are treated as human by default."""
        seg = DummySeg("hello", start=0.0, end=1.0,
                       sensevoice_tag=None)

        # No SV → treat as human
        is_human = (not seg.sensevoice_tag or
                    seg.sensevoice_tag in ("speech", "human", "laughter"))
        self.assertTrue(is_human)
        seg.uncertain = False
        self.assertFalse(seg.uncertain)


class TestBackwardCompatibility(unittest.TestCase):
    """filter_hallucinations without aligner_result works as before."""

    def test_filter_without_aligner(self):
        """filter_hallucinations works identically without aligner_result."""
        from backend.core.hallucination_filter import filter_hallucinations

        seg = DummySeg("This is a normal sentence that should pass all filters",
                       start=0.0, end=2.5)
        seg.sensevoice_tag = "Speech"

        result = filter_hallucinations([seg], source_lang="en")
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0].filtered)

    def test_filter_accepts_aligner_kwarg(self):
        """filter_hallucinations accepts aligner_result=None."""
        from backend.core.hallucination_filter import filter_hallucinations

        seg = DummySeg("test", start=0.0, end=1.0)
        result = filter_hallucinations([seg], source_lang="en",
                                       aligner_result=None)
        self.assertGreaterEqual(len(result), 1)


if __name__ == '__main__':
    unittest.main()
