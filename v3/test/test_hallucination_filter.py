"""TDD tests for five-layer hallucination filter.

Run: python3 -m pytest v2/test/test_hallucination_filter.py -v
Or:  cd v2 && python3 -m pytest test/test_hallucination_filter.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest


class FakeSegment:
    """Minimal segment for testing (mirrors transcriber_v2.Segment)."""
    def __init__(self, id=0, start=0.0, end=1.0, text="hello",
                 sensevoice_tag=None, qwen_text=None, words=None):
        self.id = id
        self.start = start
        self.end = end
        self.text = text
        self.speaker_change = False
        self.uncertain = False
        self.zh = ""
        self.words = words or []
        self.sensevoice_tag = sensevoice_tag
        self.qwen_text = qwen_text
        self.filtered = False
        self.filter_reason = None


def _make_seg(id=0, start=0.0, end=1.0, text="hello", **kwargs):
    return FakeSegment(id=id, start=start, end=end, text=text, **kwargs)


class TestLayer1SenseVoice(unittest.TestCase):
    """Layer 1 (HARD): SenseVoice sound classification."""

    def test_speech_tag_passes(self):
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="Hello world", sensevoice_tag="Speech")
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 1)
        self.assertFalse(seg.filtered)

    def test_bgm_tag_discards(self):
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="some music text", sensevoice_tag="BGM")
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 0)
        self.assertTrue(seg.filtered)

    def test_laughter_tag_discards(self):
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="haha", sensevoice_tag="Laughter")
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 0)
        self.assertTrue(seg.filtered)

    def test_applause_tag_discards(self):
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="clapping", sensevoice_tag="Applause")
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 0)

    def test_none_tag_skips_layer(self):
        """When sensevoice_tag is None, layer 1 does NOT filter."""
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="Normal speech", sensevoice_tag=None)
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 1)
        self.assertFalse(seg.filtered)


class TestLayer2CrossValidation(unittest.TestCase):
    """Layer 2 (SOFT): Whisper vs Qwen3-ASR cross-validation."""

    def test_similar_texts_no_uncertainty(self):
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="Hello world how are you", start=0.0, end=3.0,
                        qwen_text="Hello world how are you")
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 1)
        self.assertFalse(seg.uncertain)

    def test_very_different_texts_marks_uncertain(self):
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="Hello world how are you today", start=0.0, end=3.0)
        result = filter_hallucinations([seg], qwen_chunks=[
            {"text": "Completely different sentence here now",
             "t_start": 0.0, "t_end": 3.0}])
        self.assertEqual(len(result), 1)
        self.assertTrue(seg.uncertain)

    def test_none_qwen_text_skips_layer(self):
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="Hello world", qwen_text=None)
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 1)
        self.assertFalse(seg.uncertain)

    def test_partially_similar_no_uncertainty(self):
        """Similarity >= 0.3 should not mark uncertain."""
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="The quick brown fox jumps", start=0.0, end=3.0,
                        qwen_text="The quick brown fox runs")
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 1)
        self.assertFalse(seg.uncertain)


class TestLayer3SpeechRate(unittest.TestCase):
    """Layer 3 (SOFT): Speech rate anomaly detection."""

    def test_normal_rate_passes(self):
        from backend.core.hallucination_filter import filter_hallucinations
        # 10 chars in 1 second = 10 chars/s, well under any threshold
        seg = _make_seg(text="Hello test", start=0.0, end=1.0)
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 1)
        self.assertFalse(seg.uncertain)

    def test_chinese_high_rate_marks_uncertain(self):
        from backend.core.hallucination_filter import filter_hallucinations
        # 20 chars in 1 second = 20 chars/s, above Chinese threshold of 15
        seg = _make_seg(text="这是一个非常长的中文句子测试幻觉检测系统的效果好不好", start=0.0, end=1.0)
        result = filter_hallucinations([seg], source_lang="zh")
        self.assertEqual(len(result), 1)
        self.assertTrue(seg.uncertain)

    def test_japanese_short_fragment_does_not_show_user_uncertain(self):
        from backend.core.hallucination_filter import filter_hallucinations
        # 2 Japanese chars in 0.08s is a bad cut, not enough evidence for a
        # user-visible [?]. It should remain available for later merging/QA.
        seg = _make_seg(text="好き", start=0.0, end=0.08)
        result = filter_hallucinations([seg], source_lang="ja")
        self.assertEqual(len(result), 1)
        self.assertFalse(seg.filtered)
        self.assertFalse(seg.uncertain)

    def test_english_high_rate_marks_uncertain(self):
        from backend.core.hallucination_filter import filter_hallucinations
        # 30 chars in 1 second = 30 chars/s, above English threshold of 25
        seg = _make_seg(text="This is a very long sentence that goes on and on", start=0.0, end=1.0)
        result = filter_hallucinations([seg], source_lang="en")
        self.assertEqual(len(result), 1)
        self.assertTrue(seg.uncertain)

    def test_impossible_duration_discards(self):
        """Duration < 0.1s with text > 5 chars is physically impossible."""
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="Hello world impossible", start=0.0, end=0.05)
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 0)
        self.assertTrue(seg.filtered)
        self.assertEqual(seg.filter_reason, "layer3_impossible_duration")

    def test_zero_duration_discards(self):
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="This should not exist", start=5.0, end=5.0)
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 0)
        self.assertTrue(seg.filtered)


class TestLayer4RegexPatterns(unittest.TestCase):
    """Layer 4 (HARD): Regex pattern matching."""

    def test_cjk_repeated_syllable_marks_uncertain(self):
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="哈哈哈哈哈哈")
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 1)
        self.assertFalse(seg.filtered)
        self.assertTrue(seg.uncertain)
        self.assertEqual(seg.filter_reason, "layer4_cjk_repetition_uncertain")

    def test_repeated_pattern_na(self):
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="na na na na na na")
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 0)
        self.assertTrue(seg.filtered)

    def test_bracket_description_discards(self):
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="[Music]")
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 0)
        self.assertTrue(seg.filtered)

    def test_parenthesis_description_discards(self):
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="(Applause)")
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 0)
        self.assertTrue(seg.filtered)

    def test_cyclic_sentence_discards(self):
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="Thank you for watching Thank you for watching")
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 0)
        self.assertTrue(seg.filtered)

    def test_hallucination_keyword_discards(self):
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="♪ Some music playing ♪")
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 0)
        self.assertTrue(seg.filtered)

    def test_normal_text_passes(self):
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="This is a normal sentence about programming")
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 1)
        self.assertFalse(seg.filtered)

    def test_short_repetition_passes(self):
        """Short repetition (< 3 times) should pass."""
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="haha that's funny")
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 1)


class TestLayer5Dedup(unittest.TestCase):
    """Layer 5 (HARD): Hallucination chain dedup."""

    def test_consecutive_duplicates_keep_first(self):
        from backend.core.hallucination_filter import filter_hallucinations
        segs = [
            _make_seg(id=0, text="Thank you", start=0.0, end=1.0),
            _make_seg(id=1, text="Thank you", start=1.0, end=2.0),
            _make_seg(id=2, text="Thank you", start=2.0, end=3.0),
        ]
        result = filter_hallucinations(segs)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].text, "Thank you")
        self.assertFalse(result[0].filtered)
        self.assertTrue(segs[1].filtered)
        self.assertTrue(segs[2].filtered)

    def test_non_consecutive_duplicates_kept(self):
        from backend.core.hallucination_filter import filter_hallucinations
        segs = [
            _make_seg(id=0, text="Hello", start=0.0, end=1.0),
            _make_seg(id=1, text="World", start=1.0, end=2.0),
            _make_seg(id=2, text="Hello", start=2.0, end=3.0),
        ]
        result = filter_hallucinations(segs)
        self.assertEqual(len(result), 3)

    def test_single_segment_passes(self):
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="Unique text")
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 1)


class TestProcessingOrder(unittest.TestCase):
    """Integration: verify processing order Layer1→4→5→2→3."""

    def test_dual_signal_filter_prevents_soft_marking(self):
        """Segment discarded by L1 dual-signal should not get uncertain from L2."""
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(
            text="completely wrong text",
            sensevoice_tag="BGM",
            qwen_text="totally different"
        )
        aligner = [{"word": "far away", "start": 99.0, "end": 99.5}]
        result = filter_hallucinations([seg], aligner_result=aligner)
        self.assertEqual(len(result), 0)
        self.assertTrue(seg.filtered)
        self.assertFalse(seg.uncertain)

    def test_layer4_before_layer5(self):
        """Layer 4 regex should catch before dedup runs."""
        from backend.core.hallucination_filter import filter_hallucinations
        segs = [
            _make_seg(id=0, text="[Music]", start=0.0, end=1.0),
            _make_seg(id=1, text="[Music]", start=1.0, end=2.0),
        ]
        result = filter_hallucinations(segs)
        self.assertEqual(len(result), 0)
        # Both should be caught by layer 4 regex, not layer 5 dedup
        self.assertEqual(segs[0].filter_reason, "layer4_regex")
        self.assertEqual(segs[1].filter_reason, "layer4_regex")


class TestBackwardCompatibility(unittest.TestCase):
    """When sensevoice_tag=None and qwen_text=None, behavior matches old filter."""

    def test_empty_text_discarded(self):
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="")
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 0)

    def test_keyword_music_discarded(self):
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="音乐 playing in background")
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 0)

    def test_only_symbols_discarded(self):
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="♪♪♪♪")
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 0)

    def test_normal_chinese_passes(self):
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="今天天气真好啊", start=0.0, end=2.0)
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 1)
        self.assertFalse(seg.filtered)
        self.assertFalse(seg.uncertain)

    def test_normal_russian_passes(self):
        from backend.core.hallucination_filter import filter_hallucinations
        seg = _make_seg(text="Привет мир как дела сегодня", start=0.0, end=2.0)
        result = filter_hallucinations([seg])
        self.assertEqual(len(result), 1)

    def test_empty_list_returns_empty(self):
        from backend.core.hallucination_filter import filter_hallucinations
        result = filter_hallucinations([])
        self.assertEqual(result, [])


class TestMultipleSegments(unittest.TestCase):
    """Integration: mixed scenarios with multiple segments."""

    def test_mixed_filtering(self):
        from backend.core.hallucination_filter import filter_hallucinations
        segs = [
            _make_seg(id=0, text="Normal speech here", start=0.0, end=2.0, sensevoice_tag="Speech"),
            _make_seg(id=1, text="[Music]", start=2.0, end=4.0, sensevoice_tag="BGM"),
            _make_seg(id=2, text="Another normal segment", start=4.0, end=6.0, sensevoice_tag="Speech"),
            _make_seg(id=3, text="哈哈哈哈哈哈哈", start=6.0, end=7.0),
        ]
        result = filter_hallucinations(segs)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].text, "Normal speech here")
        self.assertEqual(result[1].text, "Another normal segment")
        self.assertEqual(result[2].text, "哈哈哈哈哈哈哈")
        self.assertTrue(result[2].uncertain)

    def test_ids_renumbered(self):
        """After filtering, segment IDs should be renumbered 0, 1, 2..."""
        from backend.core.hallucination_filter import filter_hallucinations
        segs = [
            _make_seg(id=0, text="Keep this", start=0.0, end=2.0),
            _make_seg(id=1, text="[Music]", start=2.0, end=3.0),
            _make_seg(id=2, text="Also keep this", start=3.0, end=5.0),
        ]
        result = filter_hallucinations(segs)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].id, 0)
        self.assertEqual(result[1].id, 1)


class TestExtractQwenSentences(unittest.TestCase):
    """P256: merge word-level Qwen timestamps into sentence-level."""

    def test_single_sentence(self):
        from backend.core.hallucination_filter import _extract_qwen_sentences
        chunks = [{"text": "Hello world", "t_start": 0.0, "t_end": 2.0,
                   "segments": [
                       {"text": "Hello", "start": 0.0, "end": 0.8},
                       {"text": "world", "start": 0.8, "end": 1.5},
                   ]}]
        result = _extract_qwen_sentences(chunks)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]["start"], 0.0)
        self.assertAlmostEqual(result[0]["end"], 1.5)
        self.assertIn("Hello", result[0]["text"])
        self.assertIn("world", result[0]["text"])

    def test_two_sentences_split_by_period(self):
        from backend.core.hallucination_filter import _extract_qwen_sentences
        chunks = [{"text": "Good morning. How are you", "t_start": 0.0, "t_end": 5.0,
                   "segments": [
                       {"text": "Good", "start": 0.0, "end": 0.5},
                       {"text": "morning.", "start": 0.5, "end": 1.2},
                       {"text": "How", "start": 1.5, "end": 1.8},
                       {"text": "are", "start": 1.8, "end": 2.0},
                       {"text": "you", "start": 2.0, "end": 2.5},
                   ]}]
        result = _extract_qwen_sentences(chunks)
        self.assertEqual(len(result), 2)
        self.assertAlmostEqual(result[0]["start"], 0.0)
        self.assertAlmostEqual(result[0]["end"], 1.2)
        self.assertAlmostEqual(result[1]["start"], 1.5)
        self.assertAlmostEqual(result[1]["end"], 2.5)

    def test_chinese_sentences(self):
        from backend.core.hallucination_filter import _extract_qwen_sentences
        chunks = [{"text": "你好世界。今天天气真好", "t_start": 0.0, "t_end": 4.0,
                   "segments": [
                       {"text": "你好", "start": 0.0, "end": 0.8},
                       {"text": "世界。", "start": 0.8, "end": 1.5},
                       {"text": "今天", "start": 1.8, "end": 2.3},
                       {"text": "天气", "start": 2.3, "end": 2.8},
                       {"text": "真好", "start": 2.8, "end": 3.5},
                   ]}]
        result = _extract_qwen_sentences(chunks)
        self.assertEqual(len(result), 2)
        self.assertAlmostEqual(result[0]["end"], 1.5)
        self.assertAlmostEqual(result[1]["start"], 1.8)

    def test_empty_input(self):
        from backend.core.hallucination_filter import _extract_qwen_sentences
        self.assertEqual(_extract_qwen_sentences([]), [])
        self.assertEqual(_extract_qwen_sentences(None), [])

    def test_no_segments_returns_empty(self):
        from backend.core.hallucination_filter import _extract_qwen_sentences
        chunks = [{"text": "Hello", "t_start": 0.0, "t_end": 1.0}]
        self.assertEqual(_extract_qwen_sentences(chunks), [])

    def test_multiple_chunks(self):
        from backend.core.hallucination_filter import _extract_qwen_sentences
        chunks = [
            {"text": "First chunk.", "t_start": 0.0, "t_end": 2.0,
             "segments": [{"text": "First", "start": 0.0, "end": 0.5},
                          {"text": "chunk.", "start": 0.5, "end": 1.0}]},
            {"text": "Second chunk", "t_start": 5.0, "t_end": 7.0,
             "segments": [{"text": "Second", "start": 5.0, "end": 5.5},
                          {"text": "chunk", "start": 5.5, "end": 6.0}]},
        ]
        result = _extract_qwen_sentences(chunks)
        self.assertEqual(len(result), 2)
        self.assertAlmostEqual(result[0]["start"], 0.0)
        self.assertAlmostEqual(result[1]["start"], 5.0)


class TestBuildSmartBoundaries(unittest.TestCase):
    """P256: smart window boundaries based on Qwen sentence timestamps."""

    def _make_chunks_with_segments(self, sentences):
        """Helper: create qwen_chunks from a list of (text, start, end)."""
        chunks = []
        for text, start, end in sentences:
            chunks.append({
                "text": text, "t_start": start, "t_end": end,
                "segments": [{"text": text, "start": start, "end": end}]
            })
        return chunks

    def test_no_straddling_sentence_keeps_fixed_30s(self):
        from backend.core.hallucination_filter import _build_smart_boundaries
        chunks = self._make_chunks_with_segments([
            ("Hello world", 5.0, 8.0),
            ("Nice day", 35.0, 38.0),
            ("Goodbye", 65.0, 68.0),
        ])
        bounds = _build_smart_boundaries(chunks, 90.0)
        self.assertEqual(len(bounds), 4)  # [0, 30, 60, 90]
        self.assertAlmostEqual(bounds[0], 0.0)
        self.assertAlmostEqual(bounds[1], 30.0)
        self.assertAlmostEqual(bounds[2], 60.0)
        self.assertAlmostEqual(bounds[3], 90.0)

    def test_sentence_straddling_boundary_left_majority(self):
        """Sentence 28-31s straddles 30s: left_ratio=2/3 >= 0.5 → slide to 31s."""
        from backend.core.hallucination_filter import _build_smart_boundaries
        chunks = self._make_chunks_with_segments([
            ("Early sentence", 5.0, 8.0),
            ("Straddling sentence", 28.0, 31.0),
            ("Later sentence", 45.0, 48.0),
        ])
        bounds = _build_smart_boundaries(chunks, 90.0)
        self.assertAlmostEqual(bounds[1], 31.0)
        self.assertAlmostEqual(bounds[2], 60.0)

    def test_sentence_straddling_boundary_right_majority(self):
        """Sentence 29.5-35s straddles 30s: left_ratio=0.5/5.5 < 0.5 → slide to 29.5s."""
        from backend.core.hallucination_filter import _build_smart_boundaries
        chunks = self._make_chunks_with_segments([
            ("Early sentence", 5.0, 8.0),
            ("Straddling sentence", 29.5, 35.0),
            ("Later sentence", 50.0, 53.0),
        ])
        bounds = _build_smart_boundaries(chunks, 90.0)
        self.assertAlmostEqual(bounds[1], 29.5)

    def test_slide_blocked_by_min_20s(self):
        """Sliding would make window < 20s → keep original 30s."""
        from backend.core.hallucination_filter import _build_smart_boundaries
        # If boundary slides to 42s, first window = 0-42=42s, second = 42-60=18s < 20s
        chunks = self._make_chunks_with_segments([
            ("Early", 5.0, 8.0),
            ("Straddling at 30", 25.0, 42.0),  # left_ratio=5/17 < 0.5 → want to slide to 25s
        ])
        # slide to 25 would make window [0,25]=25s OK, [25,60]=35s OK — should slide
        # Let's test a case where it actually blocks:
        # boundary at 30, sentence 12-31s: left_ratio=18/19 >= 0.5 → slide to 31
        # that makes window [0,31]=31s OK. Now boundary at 60, sentence 50-61:
        # left_ratio=10/11 >= 0.5 → slide to 61. window [31,61]=30s OK.
        # Need a scenario where resulting window < 20s
        # boundary at 30, sentence 28-50: left=2/22 < 0.5 → slide to 28
        # window [0,28]=28s OK, window [28,60]=32s OK — still fine
        # boundary at 30, sentence 11-31: left=19/20 → slide to 31
        # window [0,31]=31s OK
        # Let's force it: two boundaries close together after sliding
        # 0..30..60..90 → sentence at 28-31 slides 30→31,
        # sentence at 48-61 slides 60→48, window [31,48]=17s < 20s → block
        chunks2 = self._make_chunks_with_segments([
            ("s1", 5.0, 8.0),
            ("straddle30", 28.0, 31.0),  # left=2/3 → slide 30→31
            ("straddle60", 48.0, 61.0),  # left=12/13 → slide 60→61
            # window [31,61]=30s, OK. That doesn't trigger < 20.
            # Need: slide 30→31, and slide 60→49
            # sentence at 49-61, left=(60-49)/(61-49)=11/12 >= 0.5 → slide to 61 not 49
            # sentence at 59-80, left=1/21 < 0.5 → slide to 59
            # window [31,59]=28s OK
        ])
        # Actually let me construct a proper case:
        # boundary 30: sentence 28-31, slide → 31
        # boundary 60: sentence 40-61, left_ratio=(60-40)/(61-40)=20/21 ≥ 0.5 → slide to 61
        # window [31,61]=30 OK.
        # For < 20: need slide to make a 19s gap or less.
        # boundary 30: sentence 28-31, slide → 31
        # boundary 60: sentence 49-61, left_ratio=(60-49)/(61-49)=11/12 ≥ 0.5 → slide to 61
        # window [31,61]=30 OK. Still fine.
        # The only way to get < 20 is if two slides push towards each other.
        # boundary 30: sentence 28-31, slide → 31 (pushed right)
        # boundary 60: sentence 48-61, left=12/13 → slide to 61 (pushed right)
        # These push same direction. Need opposite:
        # boundary 30: sentence 28-42 → left=2/14 < 0.5 → slide to 28
        # boundary 60: sentence 42-61 → left=18/19 ≥ 0.5 → slide to 61
        # [28,61]=33 OK
        # Hmm. For opposite push:
        # boundary 30 slides right → 35, boundary 60 slides left → 48
        # [35,48]=13 < 20. Let's make this case:
        # boundary 30: sentence 28-35, left=2/7 < 0.5 → slide to 28. Actually this is left.
        # I need left majority to push right.
        # sentence 25-35: left_ratio=(30-25)/(35-25)=5/10=0.5 → slide to 35 (right)
        # sentence 48-62: left_ratio=(60-48)/(62-48)=12/14=0.857 → slide to 62 (right)
        # [35,62]=27 OK. Still need opposite.
        # For left slide on 60: left_ratio < 0.5
        # sentence 59.5-80: left=0.5/20.5=0.024 → slide to 59.5
        # Now: boundary 30 → 35, boundary 60 → 59.5. [35,59.5]=24.5 OK.
        # Hmm, it's hard to make < 20 with independent slides.
        # Actually, let me think about this differently. The constraint check should
        # check AFTER all slides whether any resulting window < 20 or > 40.
        # If so, revert that particular slide.
        # Let me create a simple case:
        # Total 50s video → boundaries [0, 30, 50]
        # sentence 28-42: straddles 30, left=2/14 < 0.5 → slide to 28
        # [0,28]=28s OK, [28,50]=22s OK
        # sentence 25-35: straddles 30, left=5/10=0.5 → slide to 35
        # [0,35]=35s OK, [35,50]=15s < 20 → BLOCK, keep 30
        pass  # tested inline below

    def test_slide_blocked_by_min_constraint(self):
        """If sliding creates a window < 20s, the slide is blocked."""
        from backend.core.hallucination_filter import _build_smart_boundaries
        # 50s video: boundaries [0, 30, 50]
        # sentence 25-35 straddles 30: left=5/10=0.5 → wants to slide to 35
        # but [35,50]=15s < 20s → block, keep at 30
        chunks = [{"text": "a", "t_start": 25.0, "t_end": 35.0,
                   "segments": [{"text": "a", "start": 25.0, "end": 35.0}]}]
        bounds = _build_smart_boundaries(chunks, 50.0)
        self.assertAlmostEqual(bounds[1], 30.0)  # not slid

    def test_slide_blocked_by_max_constraint(self):
        """If sliding creates a window > 40s, the slide is blocked."""
        from backend.core.hallucination_filter import _build_smart_boundaries
        # 90s video: boundaries [0, 30, 60, 90]
        # sentence 18-31 straddles 30: left=12/13 ≥ 0.5 → wants to slide to 31
        # [0,31]=31s OK, [31,60]=29s OK → fine, this slides
        # Actually for > 40: need a slide that makes a window > 40s.
        # sentence 59-80 straddles 60: left=1/21 < 0.5 → slide to 59
        # [30,59]=29s OK, [59,90]=31s OK. Fine.
        # Need: boundary at 30, sentence 10-31 → left=20/21 → slide to 31
        # [0,31]=31 OK. Need first window > 40.
        # 100s video: [0,30,60,90,100]
        # sentence 55-61 straddles 60: left=5/6 → slide to 61
        # sentence 29-31 straddles 30: left=1/2=0.5 → slide to 31
        # [31,61]=30 OK
        # For > 40: slide 30 far left.
        # 90s: sentence 10-31: left=20/21 → slide to 31
        #   [0,31]=31 OK
        # sentence 58-61: left=2/3 → slide to 61
        #   [31,61]=30 OK
        # Hard to exceed 40 because slides are bounded by sentence extent.
        # Actually: if we slide 30→15 (sentence 15-31 straddles 30, left=15/16 → slide 31),
        # wait no, left majority slides RIGHT to sentence end.
        # left < 0.5 slides LEFT to sentence start.
        # sentence 29.9-60 straddles 30: left=0.1/30.1 < 0.5 → slide to 29.9
        # [0,29.9]=29.9, [29.9,60]=30.1 OK
        # For > 40 from right slide:
        # sentence 28-71 straddles 30 AND 60. Let me handle one boundary at a time.
        # The sentence straddles 30: left=2/43 < 0.5 → slide to 28
        # [0,28]=28, [28,60]=32 OK
        # The same sentence straddles 60: left=32/43 ≥ 0.5 → slide to 71
        # [28,71]=43 > 40 → BLOCK, keep 60
        chunks = [{"text": "long sentence", "t_start": 28.0, "t_end": 71.0,
                   "segments": [{"text": "long sentence", "start": 28.0, "end": 71.0}]}]
        bounds = _build_smart_boundaries(chunks, 90.0)
        # boundary 30 slides to 28 (left=2/43 < 0.5)
        # boundary 60 tries to slide to 71 but [28,71]=43 > 40 → blocked, stays 60
        self.assertAlmostEqual(bounds[1], 28.0)
        self.assertAlmostEqual(bounds[2], 60.0)  # blocked

    def test_multiple_boundaries_slide_independently(self):
        """Each boundary slides based on its own straddling sentence."""
        from backend.core.hallucination_filter import _build_smart_boundaries
        chunks = self._make_chunks_with_segments([
            ("s1", 5.0, 8.0),
            ("straddle30", 28.0, 32.0),   # left=2/4=0.5 → slide to 32
            ("mid", 45.0, 48.0),
            ("straddle60", 58.0, 63.0),   # left=2/5=0.4 → slide to 58
        ])
        bounds = _build_smart_boundaries(chunks, 90.0)
        self.assertAlmostEqual(bounds[1], 32.0)
        self.assertAlmostEqual(bounds[2], 58.0)

    def test_short_video_single_window(self):
        """Video < 30s → single window [0, total_duration]."""
        from backend.core.hallucination_filter import _build_smart_boundaries
        chunks = self._make_chunks_with_segments([("Hello", 2.0, 4.0)])
        bounds = _build_smart_boundaries(chunks, 25.0)
        self.assertEqual(len(bounds), 2)
        self.assertAlmostEqual(bounds[0], 0.0)
        self.assertAlmostEqual(bounds[1], 25.0)

    def test_no_timestamps_fallback_fixed(self):
        """Chunks without segments → fall back to fixed 30s boundaries."""
        from backend.core.hallucination_filter import _build_smart_boundaries
        chunks = [{"text": "no segments here", "t_start": 0.0, "t_end": 90.0}]
        bounds = _build_smart_boundaries(chunks, 90.0)
        self.assertEqual(len(bounds), 4)
        self.assertAlmostEqual(bounds[1], 30.0)
        self.assertAlmostEqual(bounds[2], 60.0)

    def test_empty_chunks_fallback_fixed(self):
        from backend.core.hallucination_filter import _build_smart_boundaries
        bounds = _build_smart_boundaries([], 90.0)
        self.assertEqual(len(bounds), 4)
        bounds2 = _build_smart_boundaries(None, 90.0)
        self.assertEqual(len(bounds2), 4)


class TestAssignByTimestampsWithBoundaries(unittest.TestCase):
    """P256: _assign_by_timestamps respects dynamic boundaries."""

    def test_with_boundaries_places_correctly(self):
        from backend.core.hallucination_filter import _assign_by_timestamps
        chunks = [{"segments": [
            {"text": "early", "start": 5.0, "end": 6.0},
            {"text": "mid", "start": 32.0, "end": 33.0},
            {"text": "late", "start": 62.0, "end": 63.0},
        ]}]
        boundaries = [0.0, 31.0, 58.0, 90.0]
        result = _assign_by_timestamps(chunks, 3, 30, boundaries=boundaries)
        self.assertIn("early", result[0])   # 5s → window [0, 31)
        self.assertIn("mid", result[1])     # 32s → window [31, 58)
        self.assertIn("late", result[2])    # 62s → window [58, 90)

    def test_without_boundaries_uses_fixed(self):
        from backend.core.hallucination_filter import _assign_by_timestamps
        chunks = [{"segments": [
            {"text": "word", "start": 5.0, "end": 6.0},
        ]}]
        result = _assign_by_timestamps(chunks, 3, 30)
        self.assertIn("word", result[0])  # 5 // 30 = 0

    def test_boundary_edge_word_goes_to_next_window(self):
        """Word at exactly a boundary goes to the next window."""
        from backend.core.hallucination_filter import _assign_by_timestamps
        chunks = [{"segments": [
            {"text": "exact", "start": 31.0, "end": 32.0},
        ]}]
        boundaries = [0.0, 31.0, 60.0, 90.0]
        result = _assign_by_timestamps(chunks, 3, 30, boundaries=boundaries)
        self.assertIn("exact", result[1])  # 31.0 → window [31, 60)


class TestL2SmartBoundariesEndToEnd(unittest.TestCase):
    """P256: end-to-end L2 cross-validate with smart window boundaries."""

    def test_straddling_sentence_unified_in_window(self):
        """A sentence straddling 30s should end up in ONE window, not split."""
        from backend.core.hallucination_filter import _layer2_cross_validate
        # Whisper segments: one at 28-32s with text that matches Qwen
        segs = [
            _make_seg(id=0, text="early words here today",
                      start=2.0, end=5.0),
            _make_seg(id=1, text="straddling sentence right here now",
                      start=28.0, end=32.0),
            _make_seg(id=2, text="later words over there too",
                      start=40.0, end=45.0),
        ]
        # Qwen: same sentence at 28-31s (straddles 30s boundary)
        qwen_chunks = [{
            "text": "early words here today. straddling sentence right here now. later words over there too",
            "t_start": 0.0, "t_end": 50.0,
            "segments": [
                {"text": "early", "start": 2.0, "end": 2.5},
                {"text": "words", "start": 2.5, "end": 3.0},
                {"text": "here", "start": 3.0, "end": 3.5},
                {"text": "today.", "start": 3.5, "end": 4.0},
                {"text": "straddling", "start": 28.0, "end": 28.8},
                {"text": "sentence", "start": 28.8, "end": 29.3},
                {"text": "right", "start": 29.3, "end": 29.7},
                {"text": "here", "start": 29.7, "end": 30.0},
                {"text": "now.", "start": 30.0, "end": 30.5},
                {"text": "later", "start": 40.0, "end": 40.5},
                {"text": "words", "start": 40.5, "end": 41.0},
                {"text": "over", "start": 41.0, "end": 41.5},
                {"text": "there", "start": 41.5, "end": 42.0},
                {"text": "too", "start": 42.0, "end": 42.5},
            ]
        }]
        result, stats = _layer2_cross_validate(
            segs, language="en", qwen_chunks=qwen_chunks)
        # The straddling sentence should be in ONE window (not split across two)
        # Window boundary should have shifted from 30 to ~30.5 (sentence ends at 30.5)
        # or to 28.0 (sentence starts at 28.0), depending on occupancy
        # left_ratio = (30-28)/(30.5-28) = 2/2.5 = 0.8 ≥ 0.5 → slide to 30.5
        # All three segs should survive and not be marked uncertain
        # (because Qwen text matches Whisper text well)
        for seg in result:
            self.assertFalse(seg.uncertain,
                             f"seg '{seg.text}' should not be uncertain")

    def test_no_timestamps_falls_back_to_fixed(self):
        """Without Qwen timestamps, L2 uses fixed 30s windows (regression)."""
        from backend.core.hallucination_filter import _layer2_cross_validate
        segs = [
            _make_seg(id=0, text="Hello world test speech five words",
                      start=2.0, end=5.0),
        ]
        qwen_chunks = [{"text": "Hello world test speech five words",
                        "t_start": 0.0, "t_end": 5.0}]
        result, stats = _layer2_cross_validate(
            segs, language="en", qwen_chunks=qwen_chunks)
        # Should still work with fixed windows
        self.assertEqual(len(result), 1)
        self.assertIn(0, stats)


if __name__ == "__main__":
    unittest.main()
