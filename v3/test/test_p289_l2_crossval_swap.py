"""P289: L2 cross-validation must use alt_segments when provided.

When Qwen is the primary engine, segments[].text contains Qwen text.
L2 must accept alt_segments (Whisper raw segments) and use them for
primary_windows, so it actually cross-validates two different sources.

Run: cd v2 && .venv/bin/python -m pytest test/test_p289_l2_crossval_swap.py -v
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from types import SimpleNamespace


def _make_seg(id=0, start=0.0, end=1.0, text="", whisper_text=""):
    seg = SimpleNamespace(
        id=id, start=start, end=end, text=text,
        sensevoice_tag=None, filtered=False,
        filter_reason=None, uncertain=False, words=[],
        whisper_text=whisper_text,
    )
    return seg


def _qc(*triples):
    return [{"text": t, "t_start": s, "t_end": e} for (t, s, e) in triples if t]


class TestP289AltSegments(unittest.TestCase):
    """L2 must use alt_segments for primary windows when provided."""

    def test_without_alt_segments_uses_seg_text(self):
        """Default behavior: alt_segments=None → primary_windows filled from seg.text."""
        from backend.core.hallucination_filter import _layer2_cross_validate

        seg = _make_seg(0, 0, 10, text="the quick brown fox jumps over the lazy dog")
        qwen = _qc(("the quick brown fox jumps over the lazy dog", 0, 10))
        result, stats = _layer2_cross_validate([seg], language="en", qwen_chunks=qwen)
        self.assertIn(0, stats)
        self.assertEqual(stats[0]["status"], "GREEN")

    def test_with_alt_segments_uses_alt_text(self):
        """P289 core fix: alt_segments provided → primary_windows from alt_segments."""
        from backend.core.hallucination_filter import _layer2_cross_validate

        seg = _make_seg(0, 0, 10, text="千元组装后的日语文本没有空格")
        alt = [_make_seg(0, 0, 10, text="the real whisper text matches qwen chunks")]
        qwen = _qc(("the real whisper text matches qwen chunks", 0, 10))

        result, stats = _layer2_cross_validate(
            [seg], language="en", qwen_chunks=qwen, alt_segments=alt)

        self.assertIn(0, stats)
        self.assertEqual(
            stats[0]["status"], "GREEN",
            f"Expected GREEN (Whisper matches Qwen), got {stats[0]}")

    def test_alt_segments_different_from_qwen_yields_yellow(self):
        """When alt_segments (Whisper) disagrees with Qwen, L2 flags it."""
        from backend.core.hallucination_filter import _layer2_cross_validate

        seg = _make_seg(0, 0, 10, text="千元的日语文本")
        alt = [_make_seg(0, 0, 10, text="completely different whisper content here now")]
        qwen = _qc(("the qwen chunks have entirely unrelated text output", 0, 10))

        result, stats = _layer2_cross_validate(
            [seg], language="en", qwen_chunks=qwen, alt_segments=alt)

        self.assertIn(0, stats)
        self.assertIn(stats[0]["status"], ("YELLOW", "LOW_DATA"),
                      f"Expected YELLOW or LOW_DATA, got {stats[0]}")

    def test_alt_segments_none_backwards_compatible(self):
        """Passing alt_segments=None must not change existing behavior."""
        from backend.core.hallucination_filter import _layer2_cross_validate

        seg = _make_seg(0, 0, 10, text="hello world testing one two three four five")
        qwen = _qc(("hello world testing one two three four five", 0, 10))

        result_default, stats_default = _layer2_cross_validate(
            [seg], language="en", qwen_chunks=qwen)

        result_explicit, stats_explicit = _layer2_cross_validate(
            [seg], language="en", qwen_chunks=qwen, alt_segments=None)

        self.assertEqual(stats_default[0]["status"], stats_explicit[0]["status"])
        self.assertAlmostEqual(
            stats_default[0]["score"], stats_explicit[0]["score"], places=3)

    def test_filter_hallucinations_passes_alt_segments(self):
        """filter_hallucinations must accept and forward alt_segments."""
        from backend.core.hallucination_filter import filter_hallucinations

        seg = _make_seg(0, 0, 10, text="千元组装文本和千元原始chunks一样")
        alt = [_make_seg(0, 0, 10, text="whisper produced different output for same audio")]
        qwen = _qc(("whisper produced different output for same audio", 0, 10))

        result = filter_hallucinations(
            [seg], source_lang="en", qwen_chunks=qwen,
            alt_segments=alt, skip_l1=True)
        self.assertTrue(len(result) > 0)

    def test_japanese_qwen_primary_crossval(self):
        """Japanese: Qwen primary with Whisper alt_segments must cross-validate."""
        from backend.core.hallucination_filter import _layer2_cross_validate

        seg = _make_seg(0, 0, 10, text="後輩たちの練習見てね先輩としてさ")
        alt = [_make_seg(0, 0, 10, text="後輩たちの練習見てね先輩としてさ")]
        qwen = _qc(("後輩 たち の 練習 見 て ね 先輩 として さ", 0, 10))

        result, stats = _layer2_cross_validate(
            [seg], language="ja", qwen_chunks=qwen, alt_segments=alt)

        self.assertIn(0, stats)
        self.assertNotEqual(
            stats[0]["status"], "EMPTY",
            "Japanese window should have data from both sides")


if __name__ == "__main__":
    unittest.main()
