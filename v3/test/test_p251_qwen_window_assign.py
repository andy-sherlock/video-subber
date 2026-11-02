"""TDD regression tests for P251: Qwen-ASR text drops out of L2 cross-validation.

Bug: align_by_similarity mapped Qwen sentences 1:1 onto Whisper segments, so
~75% of segments got qwen_text=None and L2 windows became NO_QWEN (silent
pass). Fix: Qwen-ASR hands L2 a list of {text, t_start, t_end} chunks; L2
assigns each sentence to the best-matching 30s window by stem similarity
(monotonic cursor, search limited to the chunk's time span). No data dropped.
Pure text logic — identical on MLX (Mac) and PyTorch (Windows).

Run: cd v3 && .venv/bin/python -m pytest test/test_p251_qwen_window_assign.py -v
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest

from backend.core.hallucination_filter import (
    _assign_qwen_to_windows,
    _assign_by_timestamps,
    _has_timestamp_segments,
    _qwen_segments_as_alignment,
    _qwen_is_reliable,
    _split_for_l2,
    _layer1_dual_signal,
    _layer2_cross_validate,
    filter_hallucinations,
)


class FakeSeg:
    def __init__(self, text, start, end, filtered=False):
        self.text = text
        self.start = start
        self.end = end
        self.filtered = filtered
        self.uncertain = False
        self.l2_status = None


class TestSplitForL2(unittest.TestCase):
    def test_splits_on_punctuation(self):
        self.assertEqual(_split_for_l2("a. b! c?"), ["a", "b", "c"])

    def test_handles_none_and_empty(self):
        self.assertEqual(_split_for_l2(None), [])
        self.assertEqual(_split_for_l2("   "), [])


class TestAssignQwenToWindows(unittest.TestCase):
    def test_sentences_land_in_matching_windows(self):
        whisper_windows = [
            "the quick brown fox jumps",      # window 0 (0-30s)
            "lazy dog sleeps under a tree",   # window 1 (30-60s)
        ]
        chunks = [{"text": "quick brown fox jumped. dog sleeping under the tree.",
                   "t_start": 0, "t_end": 60}]
        out = _assign_qwen_to_windows(chunks, whisper_windows, 30, "en")
        self.assertIn("fox", out[0])
        self.assertIn("dog", out[1])

    def test_no_sentence_dropped(self):
        # The core P251 regression: every Qwen sentence must survive, even when
        # there are far more sentences than windows.
        whisper_windows = ["alpha beta gamma", "delta epsilon zeta"]
        chunks = [{"text": "alpha one. beta two. gamma three. delta four. epsilon five.",
                   "t_start": 0, "t_end": 60}]
        out = _assign_qwen_to_windows(chunks, whisper_windows, 30, "en")
        joined = " ".join(out)
        for word in ("alpha", "beta", "gamma", "delta", "epsilon"):
            self.assertIn(word, joined, f"{word} was dropped")

    def test_cursor_is_monotonic(self):
        # window 0 and window 2 both look like "dog"; the 3rd sentence matches
        # window 0 best, but the cursor already passed window 1, so it must land
        # forward (window 2), never jump back to window 0.
        whisper_windows = ["dog dog dog", "cat cat cat", "dog dog dog"]
        chunks = [{"text": "dog here. cat here. dog again.", "t_start": 0, "t_end": 90}]
        out = _assign_qwen_to_windows(chunks, whisper_windows, 30, "en")
        self.assertIn("dog again", out[2])
        self.assertNotIn("dog again", out[0])

    def test_chunk_time_bounds_limit_search(self):
        # A chunk covering only 30-60s must not place text into window 0.
        whisper_windows = ["morning text here", "afternoon text here"]
        chunks = [{"text": "afternoon something here.", "t_start": 30, "t_end": 60}]
        out = _assign_qwen_to_windows(chunks, whisper_windows, 30, "en")
        self.assertEqual(out[0], "")
        self.assertIn("afternoon", out[1])

    def test_empty_chunks(self):
        self.assertEqual(_assign_qwen_to_windows(None, ["a", "b"], 30, "en"), ["", ""])
        self.assertEqual(_assign_qwen_to_windows([], ["a", "b"], 30, "en"), ["", ""])

    def test_chunk_time_exceeds_timeline_no_crash(self):
        # bill-gates review: a chunk whose time bounds overrun the window count
        # must clamp into range, not IndexError on w_stems[w].
        out = _assign_qwen_to_windows(
            [{"text": "x y z.", "t_start": 9999, "t_end": 99999}], ["a", "b"], 30, "en")
        self.assertEqual(len(out), 2)
        self.assertIn("x y z", out[1])  # clamped into the last window

    def test_reversed_chunk_time_no_crash(self):
        out = _assign_qwen_to_windows(
            [{"text": "a.", "t_start": 100, "t_end": 5}], ["a", "b"], 30, "en")
        self.assertEqual(len(out), 2)

    def test_huge_sentence_count_no_overflow(self):
        # 50 sentences, 3 windows, monotonic cursor piling toward the end → no overflow.
        many = ". ".join(f"dog{i}" for i in range(50)) + "."
        out = _assign_qwen_to_windows(
            [{"text": many, "t_start": 0, "t_end": 90}], ["dog", "cat", "dog"], 30, "en")
        self.assertEqual(len(out), 3)


class TestTimestampAssignment(unittest.TestCase):
    """MLX path: segments carry start/end timestamps → precise window index."""

    def test_has_timestamp_segments_true(self):
        chunks = [{"text": "hi", "segments": [{"text": "hi", "start": 0, "end": 1}],
                   "t_start": 0, "t_end": 30}]
        self.assertTrue(_has_timestamp_segments(chunks))

    def test_has_timestamp_segments_false_when_empty(self):
        chunks = [{"text": "hi", "segments": [], "t_start": 0, "t_end": 30}]
        self.assertFalse(_has_timestamp_segments(chunks))

    def test_has_timestamp_segments_false_when_missing(self):
        chunks = [{"text": "hi", "t_start": 0, "t_end": 30}]
        self.assertFalse(_has_timestamp_segments(chunks))

    def test_segments_placed_by_start_time(self):
        chunks = [{
            "text": "hello world",
            "segments": [
                {"text": "hello", "start": 5.0, "end": 5.5},
                {"text": "world", "start": 35.0, "end": 35.5},
            ],
            "t_start": 0, "t_end": 60,
        }]
        out = _assign_by_timestamps(chunks, num_windows=2, window_size=30)
        self.assertIn("hello", out[0])
        self.assertIn("world", out[1])

    def test_segment_at_boundary_lands_in_next(self):
        chunks = [{
            "text": "exactly at boundary",
            "segments": [{"text": "boundary", "start": 30.0, "end": 30.5}],
            "t_start": 0, "t_end": 60,
        }]
        out = _assign_by_timestamps(chunks, num_windows=2, window_size=30)
        self.assertEqual(out[0], "")
        self.assertIn("boundary", out[1])

    def test_segment_clamped_to_last_window(self):
        chunks = [{
            "text": "overflow",
            "segments": [{"text": "overflow", "start": 999.0, "end": 999.5}],
            "t_start": 0, "t_end": 1000,
        }]
        out = _assign_by_timestamps(chunks, num_windows=2, window_size=30)
        self.assertIn("overflow", out[1])

    def test_dispatch_routes_to_timestamps_when_segments(self):
        whisper_windows = ["something", "else"]
        chunks = [{
            "text": "dog cat",
            "segments": [
                {"text": "dog", "start": 0.0, "end": 0.5},
                {"text": "cat", "start": 31.0, "end": 31.5},
            ],
            "t_start": 0, "t_end": 60,
        }]
        out = _assign_qwen_to_windows(chunks, whisper_windows, 30, "en")
        self.assertIn("dog", out[0])
        self.assertIn("cat", out[1])

    def test_dispatch_routes_to_similarity_without_segments(self):
        whisper_windows = ["the quick brown fox", "lazy dog sleeps"]
        chunks = [{"text": "quick brown fox. lazy dog.", "t_start": 0, "t_end": 60}]
        out = _assign_qwen_to_windows(chunks, whisper_windows, 30, "en")
        self.assertIn("fox", out[0])
        self.assertIn("dog", out[1])

    def test_l2_green_with_timestamp_segments(self):
        segs = [FakeSeg("the quick brown fox jumps over", 0, 5)]
        chunks = [{
            "text": "the quick brown fox jumps over",
            "segments": [
                {"text": "the quick brown fox jumps over", "start": 0, "end": 5},
            ],
            "t_start": 0, "t_end": 30,
        }]
        out, stats = _layer2_cross_validate(segs, "en", qwen_chunks=chunks)
        self.assertEqual(stats[0]["status"], "GREEN")


class TestCollapseDetection(unittest.TestCase):
    def test_repetition_collapse_detected(self):
        chunks = [{"text": "對不起。對不起。對不起。對不起。對不起。對不起。",
                   "t_start": 0, "t_end": 60}]
        self.assertFalse(_qwen_is_reliable(chunks))

    def test_healthy_qwen_passes(self):
        chunks = [{"text": "first idea. second point. third thing. fourth note. fifth item.",
                   "t_start": 0, "t_end": 60}]
        self.assertTrue(_qwen_is_reliable(chunks))

    def test_too_little_data_passes(self):
        chunks = [{"text": "only two. sentences here.", "t_start": 0, "t_end": 30}]
        self.assertTrue(_qwen_is_reliable(chunks))

    def test_none_passes(self):
        self.assertTrue(_qwen_is_reliable(None))


class TestLayer2WithChunks(unittest.TestCase):
    def test_matching_qwen_makes_window_green(self):
        segs = [FakeSeg("the quick brown fox jumps over", 0, 5)]
        chunks = [{"text": "the quick brown fox jumps over.", "t_start": 0, "t_end": 30}]
        out, stats = _layer2_cross_validate(segs, "en", qwen_chunks=chunks)
        self.assertEqual(stats[0]["status"], "GREEN")
        self.assertFalse(out[0].uncertain)

    def test_no_qwen_chunks_yields_no_qwen_status(self):
        segs = [FakeSeg("the quick brown fox jumps over", 0, 5)]
        out, stats = _layer2_cross_validate(segs, "en", qwen_chunks=None)
        self.assertEqual(stats[0]["status"], "NO_QWEN")

    def test_mismatch_flags_uncertain(self):
        # Whisper says one thing, Qwen heard something totally different → YELLOW.
        segs = [FakeSeg("subscribe to my channel thanks for watching", 0, 5)]
        chunks = [{"text": "and that is the fundamental point here.", "t_start": 0, "t_end": 30}]
        out, stats = _layer2_cross_validate(segs, "en", qwen_chunks=chunks)
        self.assertEqual(stats[0]["status"], "YELLOW")
        self.assertTrue(out[0].uncertain)


class TestQwenSegmentsAsAlignment(unittest.TestCase):
    """Step 8: Qwen segments replace external FA for L1 cross-validation."""

    def test_flatten_segments_from_chunks(self):
        chunks = [{
            "text": "hello world",
            "segments": [
                {"text": "hello", "start": 0.5, "end": 0.8},
                {"text": "world", "start": 1.0, "end": 1.5},
            ],
            "t_start": 0, "t_end": 30,
        }]
        alignment = _qwen_segments_as_alignment(chunks)
        self.assertEqual(len(alignment), 2)
        self.assertEqual(alignment[0]["start"], 0.5)
        self.assertEqual(alignment[1]["text"], "world")

    def test_flatten_empty_chunks(self):
        self.assertEqual(_qwen_segments_as_alignment(None), [])
        self.assertEqual(_qwen_segments_as_alignment([]), [])

    def test_flatten_chunks_without_segments(self):
        chunks = [{"text": "hi", "t_start": 0, "t_end": 30}]
        self.assertEqual(_qwen_segments_as_alignment(chunks), [])

    def test_l1_uses_qwen_segments_when_no_fa(self):
        """L1 uses Qwen segments for alignment when external FA is absent."""
        seg = FakeSeg("some speech here", 0, 5)
        seg.sensevoice_tag = "Music"
        qwen_chunks = [{
            "text": "some speech here",
            "segments": [{"text": "some speech", "start": 0.5, "end": 4.5}],
            "t_start": 0, "t_end": 30,
        }]
        # With Qwen segments as alignment: SV=Music + alignment=present → SV override
        results = filter_hallucinations(
            [seg], "en", aligner_result=None, qwen_chunks=qwen_chunks)
        self.assertEqual(len(results), 1, "L1 should keep segment (SV overridden by Qwen alignment)")
        self.assertFalse(results[0].filtered)

    def test_l1_dual_confirm_marks_with_qwen_segments(self):
        """断言翻转：SV 判非人声 + 该时段无 Qwen 词 → **只标记**。

        本条守的是 P251 的接线——Qwen 的词级时间戳能当 L1 的对齐证据用。
        接线没变，变的只是处置：G12 第二批收尾后 L1 交出删除权。

        真实日志：SenseVoice 判非人声被对齐器推翻 3492 次、真删 0 次。
        「两个信号一致」在这里是假象——第二个信号只是「这段没有对齐词」，
        它不是独立证据，是第一个信号的缺席。**缺席不是证据。**
        """
        seg = FakeSeg("subscribe", 100, 105)
        seg.sensevoice_tag = "Music"
        qwen_chunks = [{
            "text": "hello world",
            "segments": [{"text": "hello", "start": 0.5, "end": 1.0}],
            "t_start": 0, "t_end": 30,
        }]
        results = filter_hallucinations(
            [seg], "en", aligner_result=None, qwen_chunks=qwen_chunks)
        self.assertEqual(len(results), 1, "L1 不得再拥有删除权")
        self.assertTrue(seg.uncertain)


if __name__ == "__main__":
    unittest.main()
