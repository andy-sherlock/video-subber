"""P273: Qwen Japanese assembly must split without punctuation.

Qwen Japanese output often has no 。！？ punctuation. Assembly must use
Japanese sentence endings plus small pauses, while keeping word timestamps.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest


class TestP273QwenJapaneseAssembly(unittest.TestCase):
    def test_words_to_segment_keeps_word_timestamps(self):
        from backend.transcriber_v2 import _words_to_segment

        seg = _words_to_segment([
            {"text": "何するの", "start": 10.0, "end": 10.4},
            {"text": "やめて", "start": 10.6, "end": 11.0},
        ], 7, "")

        self.assertEqual(seg.text, "何するのやめて")
        self.assertEqual(seg.words, [
            {"word": "何するの", "text": "何するの", "start": 10.0, "end": 10.4},
            {"word": "やめて", "text": "やめて", "start": 10.6, "end": 11.0},
        ])

    def test_strong_japanese_ender_breaks_on_micro_pause(self):
        from backend.transcriber_v2 import _assemble_qwen_primary

        words = [
            {"text": "だめだよ", "start": 0.0, "end": 0.6},
            {"text": "何するの", "start": 0.76, "end": 1.2},
        ]

        result = _assemble_qwen_primary(words, [], "ja")

        self.assertEqual([s.text for s in result], ["だめだよ", "何するの"])

    def test_weak_japanese_ender_needs_longer_pause(self):
        from backend.transcriber_v2 import _assemble_qwen_primary

        words = [
            {"text": "何するの", "start": 0.0, "end": 0.6},
            {"text": "やめて", "start": 0.8, "end": 1.2},
        ]

        result = _assemble_qwen_primary(words, [], "ja")

        self.assertEqual([s.text for s in result], ["何するのやめて"])

    def test_weak_japanese_ender_breaks_after_point_three_pause(self):
        from backend.transcriber_v2 import _assemble_qwen_primary

        words = [
            {"text": "何するの", "start": 0.0, "end": 0.6},
            {"text": "やめて", "start": 0.91, "end": 1.2},
        ]

        result = _assemble_qwen_primary(words, [], "ja")

        self.assertEqual([s.text for s in result], ["何するの", "やめて"])

    def test_kore_does_not_break_on_re_micro_pause(self):
        from backend.transcriber_v2 import _assemble_qwen_primary

        words = [
            {"text": "これ", "start": 0.0, "end": 0.4},
            {"text": "見て", "start": 0.6, "end": 1.0},
        ]

        result = _assemble_qwen_primary(words, [], "ja")

        self.assertEqual([s.text for s in result], ["これ見て"])

    def test_english_does_not_use_japanese_micro_pause_rule(self):
        from backend.transcriber_v2 import _assemble_qwen_primary

        words = [
            {"text": "yo", "start": 0.0, "end": 0.4},
            {"text": "world", "start": 0.56, "end": 0.8},
        ]

        result = _assemble_qwen_primary(words, [], "en")

        self.assertEqual([s.text for s in result], ["yo world"])


if __name__ == "__main__":
    unittest.main()
