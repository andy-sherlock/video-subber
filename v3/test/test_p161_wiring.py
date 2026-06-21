"""TDD tests for P161: transcription pipeline wiring fix.

Written BEFORE code changes. Each test must FAIL against current code.

Subtask ②: L1 must NOT filter when aligner_result is None/empty.
           (Currently the fallback path filters single-signal non-human.)
Subtask ④: filter_hallucinations must work correctly when called with
           aligner data from standard tier (filter side).
"""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class DummySeg:
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
        self.id = 0
        self.speaker_change = False
        self.zh = ""


class TestP161_NoFallback(unittest.TestCase):
    """P183: L1 single-signal fallback added.

    When aligner data is unavailable (None or empty), L1 now trusts
    SenseVoice alone for non-human tags. Non-Speech SV tags result in
    filtering even without FA confirmation.
    """

    def test_nonhuman_tag_no_aligner_filtered(self):
        """music tag + no aligner → L1 filters (P183: trust SV alone)."""
        from backend.core.hallucination_filter import _layer1_dual_signal

        seg = DummySeg("some text", start=1.0, end=3.0,
                       sensevoice_tag="music")
        result = _layer1_dual_signal(seg, aligner_result=None)

        self.assertTrue(seg.filtered,
                        "P183: L1 should filter non-Speech via SV alone")
        self.assertTrue(result)

    def test_nonhuman_tag_empty_aligner_filtered(self):
        """noise tag + empty aligner → L1 filters (P183: trust SV alone)."""
        from backend.core.hallucination_filter import _layer1_dual_signal

        seg = DummySeg("noise text", start=0.0, end=2.0,
                       sensevoice_tag="noise")
        result = _layer1_dual_signal(seg, aligner_result=[])

        self.assertTrue(seg.filtered,
                        "P183: L1 should filter with empty aligner via SV")
        self.assertTrue(result)

    def test_speech_tag_no_aligner_still_passes(self):
        """Speech tag + no aligner → still passes (regression check)."""
        from backend.core.hallucination_filter import _layer1_dual_signal

        seg = DummySeg("hello world", start=0.0, end=2.0,
                       sensevoice_tag="Speech")
        result = _layer1_dual_signal(seg, aligner_result=None)

        self.assertFalse(seg.filtered)
        self.assertFalse(result)

    def test_no_tag_no_aligner_skips(self):
        """No sensevoice_tag → L1 skips entirely (unchanged behavior)."""
        from backend.core.hallucination_filter import _layer1_dual_signal

        seg = DummySeg("text", sensevoice_tag=None)
        result = _layer1_dual_signal(seg, aligner_result=None)

        self.assertFalse(seg.filtered)
        self.assertFalse(result)

    def test_dual_signal_still_works_with_data(self):
        """Dual-signal path still works when aligner data exists."""
        from backend.core.hallucination_filter import _layer1_dual_signal

        seg = DummySeg("music noise", start=1.0, end=3.0,
                       sensevoice_tag="music")
        aligner_result = [
            {"word": "far away", "start": 10.0, "end": 10.5},
        ]
        result = _layer1_dual_signal(seg, aligner_result=aligner_result)

        self.assertTrue(seg.filtered,
                        "Both signals agree non-human → should discard")

    def test_dual_signal_sv_wrong_clears_tag(self):
        """SV says music but aligner found words → SV wrong, clear tag."""
        from backend.core.hallucination_filter import _layer1_dual_signal

        seg = DummySeg("real speech", start=1.0, end=3.0,
                       sensevoice_tag="music")
        aligner_result = [
            {"word": "real", "start": 1.2, "end": 1.8},
        ]
        result = _layer1_dual_signal(seg, aligner_result=aligner_result)

        self.assertFalse(seg.filtered)
        self.assertIsNone(seg.sensevoice_tag)


class TestP161_Integration(unittest.TestCase):
    """Integration: non-human segments survive to filter when not pre-trimmed."""

    def test_nonhuman_segment_reaches_filter_with_aligner(self):
        """Non-human tagged segment + aligner confirms speech → kept."""
        from backend.core.hallucination_filter import filter_hallucinations

        seg = DummySeg("real words here spoken clearly",
                       start=0.0, end=3.0, sensevoice_tag="music")

        aligner_result = [
            {"word": "real", "start": 0.1, "end": 0.5},
            {"word": "words", "start": 0.6, "end": 1.0},
        ]

        result = filter_hallucinations([seg], source_lang="en",
                                       aligner_result=aligner_result)

        self.assertEqual(len(result), 1,
                         "Segment should survive: aligner overrides SV")
        self.assertIsNone(result[0].sensevoice_tag)

    def test_nonhuman_no_aligner_filtered_by_l1(self):
        """Non-human + no aligner → L1 filters via SV alone (P183 fix)."""
        from backend.core.hallucination_filter import filter_hallucinations

        seg = DummySeg("this is actual speech that sensevoice mistagged",
                       start=0.0, end=3.0, sensevoice_tag="noise")
        seg.qwen_text = "this is actual speech"

        result = filter_hallucinations([seg], source_lang="en",
                                       aligner_result=None)

        self.assertEqual(len(result), 0,
                         "P183: without aligner, L1 trusts SV alone → filtered")


if __name__ == '__main__':
    unittest.main()
