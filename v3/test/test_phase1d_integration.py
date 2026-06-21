"""Phase 1D integration tests: uncertain flag end-to-end + aligner real data.

Tests the full data flow that Bug 2 and Bug 3 broke:
  hallucination filter sets uncertain=True
  → sentence_splitter propagates it
  → subtitler outputs [?]
"""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class DummySeg:
    """Minimal segment mirroring what transcriber_v2.py and worker.py use."""
    def __init__(self, text, start=0.0, end=1.0, uncertain=False,
                 speaker_change=False, sensevoice_tag=None, qwen_text=None):
        self.id = 0
        self.text = text
        self.start = start
        self.end = end
        self.uncertain = uncertain
        self.speaker_change = speaker_change
        self.sensevoice_tag = sensevoice_tag
        self.qwen_text = qwen_text
        self.words = []
        self.zh = text  # fallback for _split_segment in subtitler.py
        self.filtered = False
        self.filter_reason = None


class TestUncertainEndToEnd(unittest.TestCase):
    """uncertain flag survives hallucination filter → subtitler output."""

    def test_uncertain_survives_word_align(self):
        """word_align_segments preserves uncertain from parent segment."""
        from backend.core.sentence_splitter import word_align_segments
        from backend.transcriber_v2 import Segment

        seg = Segment(id=1, start=0.0, end=2.0, text="hello world")
        seg.uncertain = True

        result = word_align_segments([seg])
        # Should have at least one output segment with uncertain preserved
        has_uncertain = any(getattr(s, 'uncertain', False) for s in result)
        self.assertTrue(has_uncertain,
                        "uncertain=True must survive word_align_segments")

    def test_uncertain_survives_llm_split(self):
        """llm_split_segments preserves uncertain from parent segment."""
        # This test verifies the function signature and that it doesn't
        # reset uncertain. The actual splitting behavior depends on LLM output.
        from backend.core.sentence_splitter import llm_split_segments
        from backend.transcriber_v2 import Segment

        seg = Segment(id=1, start=0.0, end=3.0, text="hello world")
        seg.zh = "你好世界"
        seg.uncertain = True

        # llm_split_segments with empty pre_splits (no splitting needed)
        result = llm_split_segments([seg], pre_splits={})
        has_uncertain = any(getattr(s, 'uncertain', False) for s in result)
        self.assertTrue(has_uncertain,
                        "uncertain=True must survive llm_split_segments")

    def test_uncertain_shows_in_subtitle_text(self):
        """[?] appears in subtitle output when uncertain=True."""
        from backend.core.subtitler import _collect_entries, _annotate_speaker_changes

        seg = DummySeg("hello world", start=0.0, end=2.0, uncertain=True)

        entries = _collect_entries([seg])
        annotated = _annotate_speaker_changes(entries)

        found_qm = any("[?]" in text for _, _, text in annotated)
        self.assertTrue(found_qm,
                        "[?] must appear in annotated subtitle text when uncertain=True")

    def test_no_question_mark_when_clean(self):
        """No [?] when uncertain=False."""
        from backend.core.subtitler import _collect_entries, _annotate_speaker_changes

        seg = DummySeg("clean text", start=0.0, end=1.0, uncertain=False)

        entries = _collect_entries([seg])
        annotated = _annotate_speaker_changes(entries)

        found_qm = any("[?]" in text for _, _, text in annotated)
        self.assertFalse(found_qm,
                         "[?] must NOT appear when uncertain=False")

    def test_split_segment_preserves_uncertain(self):
        """When a segment gets split, all sub-segments inherit uncertain."""
        from backend.core.sentence_splitter import word_align_segments
        from backend.transcriber_v2 import Segment

        # A long English segment that word_align_segments might split
        seg = Segment(id=1, start=0.0, end=6.0,
                      text="This is a very long sentence that might get split into multiple parts by the word align function")
        seg.words = [
            {"word": "This", "start": 0.0, "end": 0.3, "probability": 0.9},
            {"word": "is", "start": 0.3, "end": 0.5, "probability": 0.9},
            {"word": "a", "start": 0.5, "end": 0.6, "probability": 0.9},
        ]
        seg.uncertain = True

        result = word_align_segments([seg])
        # All resulting segments should have uncertain=True
        for s in result:
            self.assertTrue(getattr(s, 'uncertain', False),
                            f"Split segment at {s.start} must inherit uncertain=True")


class TestAlignerTimestampsReal(unittest.TestCase):
    """apply_aligner_timestamps with real data, not mocks."""

    def test_applies_correct_start_end(self):
        """start/end are set from the first and last aligned word."""
        from backend.core.qwen_aligner import apply_aligner_timestamps
        from backend.transcriber_v2 import Segment

        seg = Segment(id=0, start=0.0, end=5.0, text="hello world")
        seg.text = "hello world"  # Bug 1: reads seg.text

        aligned_words = [
            {"word": "hello", "start": 0.12, "end": 0.85},
            {"word": "world", "start": 0.85, "end": 2.10},
        ]

        result = apply_aligner_timestamps([seg], aligned_words)
        self.assertEqual(result[0].start, 0.12)
        self.assertEqual(result[0].end, 2.10)

    def test_multi_segment_allocation(self):
        """Multiple segments each get their own aligned word ranges."""
        from backend.core.qwen_aligner import apply_aligner_timestamps
        from backend.transcriber_v2 import Segment

        seg_a = Segment(id=0, start=0.0, end=2.0, text="good morning")
        seg_b = Segment(id=1, start=2.0, end=4.0, text="everyone")
        seg_c = Segment(id=2, start=4.0, end=6.0, text="today")

        aligned_words = [
            {"word": "good", "start": 0.1, "end": 0.6},
            {"word": "morning", "start": 0.6, "end": 1.5},
            {"word": "everyone", "start": 1.8, "end": 3.2},
            {"word": "today", "start": 3.5, "end": 4.8},
        ]

        result = apply_aligner_timestamps([seg_a, seg_b, seg_c], aligned_words)

        # Each segment gets different timestamps
        self.assertNotEqual(result[0].start, 0.0, "start should be updated")
        self.assertNotEqual(result[0].end, 2.0, "end should be updated")
        # Start of seg_a < start of seg_b < start of seg_c
        self.assertLess(result[0].start, result[1].start)
        self.assertLess(result[1].start, result[2].start)

    def test_empty_text_skipped(self):
        """Segments with empty text keep original timestamps."""
        from backend.core.qwen_aligner import apply_aligner_timestamps
        from backend.transcriber_v2 import Segment

        seg = Segment(id=0, start=3.0, end=5.0, text="   ")  # whitespace only

        aligned_words = [
            {"word": "hello", "start": 0.0, "end": 1.0},
        ]

        result = apply_aligner_timestamps([seg], aligned_words)
        # Should keep original timestamps (no matching words)
        self.assertEqual(result[0].start, 3.0, "Empty text keeps original start")
        self.assertEqual(result[0].end, 5.0, "Empty text keeps original end")


if __name__ == '__main__':
    unittest.main()
