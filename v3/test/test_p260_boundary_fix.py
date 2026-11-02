"""TDD tests for P260/P265 双引擎组装逻辑.

验证 _smart_assemble (Mode B: Whisper 骨架模式) 的词窗口分配逻辑：
- 跨窗口边界的词通过最大重叠匹配被正确分配
- 窗口外的词不会污染窗口内容，而是被 orphan recovery 回收
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import unittest


class FakeWhisperSeg:
    def __init__(self, s, e, t):
        self.start = s; self.end = e; self.text = t


class TestSmartAssembleWhisperMode(unittest.TestCase):
    """P265: _smart_assemble Whisper 骨架模式 (Mode B) 词分配测试."""

    def test_word_spanning_boundary_is_included(self):
        """词从 t=1.8 到 t=2.3，窗口 [2.0, 3.0]——有重叠，应匹配到窗口."""
        from backend.transcriber_v2 import (
            _assemble_qwen_words_on_whisper_boundaries,
            _flatten_qwen_words,
        )

        qwen_chunks = [{
            "text": "hello world",
            "t_start": 1.0,
            "t_end": 3.0,
            "segments": [
                {"text": "hello", "start": 1.0, "end": 1.9},
                {"text": "world", "start": 1.8, "end": 2.3},
            ],
        }]

        whisper_segs = [FakeWhisperSeg(2.0, 3.0, "placeholder")]

        result = _assemble_qwen_words_on_whisper_boundaries(
            _flatten_qwen_words(qwen_chunks), whisper_segs, "en"
        )

        # "world" has overlap with window → matched; "hello" has no overlap → orphan recovery
        window_texts = [s.text for s in result]
        self.assertTrue(any("world" in t for t in window_texts),
                        "跨窗口边界的词应通过最大重叠匹配被包含")

    def test_word_entirely_before_window_does_not_pollute(self):
        """窗口 [2.0, 3.0]，词在 [1.0, 1.5]——无重叠，不应出现在窗口段的文本里."""
        from backend.transcriber_v2 import (
            _assemble_qwen_words_on_whisper_boundaries,
            _flatten_qwen_words,
        )

        qwen_chunks = [{
            "text": "early",
            "t_start": 1.0, "t_end": 2.0,
            "segments": [
                {"text": "early", "start": 1.0, "end": 1.5},
            ],
        }]

        whisper_segs = [FakeWhisperSeg(2.0, 3.0, "placeholder")]

        result = _assemble_qwen_words_on_whisper_boundaries(
            _flatten_qwen_words(qwen_chunks), whisper_segs, "en"
        )

        # The window segment should fall back to Whisper text
        window_seg = [s for s in result if s.start >= 2.0]
        self.assertTrue(len(window_seg) >= 1)
        self.assertEqual(window_seg[0].text, "placeholder",
                         "词完全在窗口之前，窗口应回退到 Whisper 文本")
        # "early" should be recovered as orphan, not lost
        all_texts = " ".join(s.text for s in result)
        self.assertIn("early", all_texts,
                      "窗口外的词应被 orphan recovery 回收，不应丢弃")

    def test_word_entirely_after_window_does_not_pollute(self):
        """窗口 [2.0, 3.0]，词在 [3.5, 4.0]——无重叠，不应出现在窗口段的文本里."""
        from backend.transcriber_v2 import (
            _assemble_qwen_words_on_whisper_boundaries,
            _flatten_qwen_words,
        )

        qwen_chunks = [{
            "text": "late",
            "t_start": 3.0, "t_end": 5.0,
            "segments": [
                {"text": "late", "start": 3.5, "end": 4.0},
            ],
        }]

        whisper_segs = [FakeWhisperSeg(2.0, 3.0, "placeholder")]

        result = _assemble_qwen_words_on_whisper_boundaries(
            _flatten_qwen_words(qwen_chunks), whisper_segs, "en"
        )

        window_seg = [s for s in result if s.start >= 2.0 and s.start < 3.0]
        self.assertTrue(len(window_seg) >= 1)
        self.assertEqual(window_seg[0].text, "placeholder",
                         "词完全在窗口之后，窗口应回退到 Whisper 文本")
        all_texts = " ".join(s.text for s in result)
        self.assertIn("late", all_texts,
                      "窗口外的词应被 orphan recovery 回收，不应丢弃")


class TestP284EnglishQwenUsesWhisperBoundaries(unittest.TestCase):
    """P284: 英文 Qwen 主路径也必须使用 Whisper 边界和参考稿."""

    def test_english_qwen_primary_does_not_merge_next_whisper_sentence(self):
        from backend.transcriber_v2 import _smart_assemble

        qwen_chunks = [{
            "text": "Hey Taylor what time is it Oh gosh I don't know I wish I had claw",
            "t_start": 0.0,
            "t_end": 3.4,
            "segments": [
                {"text": "Hey", "start": 0.00, "end": 0.15},
                {"text": "Taylor", "start": 0.16, "end": 0.40},
                {"text": "what", "start": 0.45, "end": 0.60},
                {"text": "time", "start": 0.61, "end": 0.78},
                {"text": "is", "start": 0.79, "end": 0.90},
                {"text": "it", "start": 0.91, "end": 1.05},
                {"text": "Oh", "start": 1.29, "end": 1.40},
                {"text": "gosh", "start": 1.41, "end": 1.65},
                {"text": "I", "start": 1.66, "end": 1.75},
                {"text": "don't", "start": 1.76, "end": 2.00},
                {"text": "know", "start": 2.01, "end": 2.20},
                {"text": "I", "start": 2.21, "end": 2.30},
                {"text": "wish", "start": 2.31, "end": 2.50},
                {"text": "I", "start": 2.51, "end": 2.60},
                {"text": "had", "start": 2.61, "end": 2.76},
                {"text": "claw", "start": 2.77, "end": 3.10},
            ],
        }]
        whisper_segs = [
            FakeWhisperSeg(0.0, 1.18, "Hey Taylor what time is it?"),
            FakeWhisperSeg(1.20, 3.40, "Oh gosh I don't know I wish I had clock"),
        ]

        result = _smart_assemble(qwen_chunks, whisper_segs, "en")

        self.assertGreaterEqual(len(result), 2)
        self.assertEqual(result[0].text, "Hey Taylor what time is it")
        self.assertNotIn("Oh", result[0].text)
        self.assertEqual(result[0].whisper_text, "Hey Taylor what time is it?")
        self.assertIn("claw", result[1].text)
        self.assertIn("clock", result[1].whisper_text)

    def test_japanese_qwen_primary_keeps_existing_assembly_path(self):
        from backend.transcriber_v2 import _smart_assemble

        qwen_chunks = [{
            "text": "何するの やめて",
            "t_start": 0.0,
            "t_end": 1.2,
            "segments": [
                {"text": "何するの", "start": 0.0, "end": 0.6},
                {"text": "やめて", "start": 0.8, "end": 1.2},
            ],
        }]
        whisper_segs = [
            FakeWhisperSeg(0.0, 0.7, "何するの"),
            FakeWhisperSeg(0.7, 1.3, "やめて"),
        ]

        result = _smart_assemble(qwen_chunks, whisper_segs, "ja")

        self.assertEqual([s.text for s in result], ["何するのやめて"])


class TestP284MergeKeepsWhisperText(unittest.TestCase):
    def test_merging_short_fragment_keeps_reference_text(self):
        from backend.transcriber_v2 import Segment, merge_short_subtitle_fragments

        first = Segment(0, 0.0, 0.2, "Oh")
        first.whisper_text = "Oh"
        second = Segment(1, 0.3, 3.0, "gosh I had claw")
        second.whisper_text = "gosh I had clock"

        result = merge_short_subtitle_fragments([first, second], "en")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].text, "Oh gosh I had claw")
        self.assertEqual(result[0].whisper_text, "Oh gosh I had clock")


if __name__ == "__main__":
    unittest.main()
