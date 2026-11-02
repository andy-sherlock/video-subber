"""TDD regression tests for P250: L3 speech-rate uses char count for all langs.

Bug: _layer3_speech_rate() uses len(seg.text) (character count) as the speech
unit for every language. For English/Russian one word averages 5-6 chars, so
cps is overestimated 5-6x and the per-language threshold (en=25 chars/sec ≈ 4.5
words/sec) fires on normal speech. Fix: count_speech_units() counts CJK by
character (1 char = 1 syllable) and others by whitespace-split words, with the
threshold table re-expressed in speech-units/sec.

Run: cd v3 && .venv/bin/python -m pytest test/test_p250_speech_rate_units.py -v
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest

from backend.core.hallucination_filter import (
    _layer3_speech_rate,
    count_speech_units,
)


class FakeSegment:
    def __init__(self, text, start, end):
        self.text = text
        self.start = start
        self.end = end
        self.filtered = False
        self.filter_reason = None
        self.uncertain = False


class TestCountSpeechUnits(unittest.TestCase):
    def test_english_counts_words(self):
        self.assertEqual(count_speech_units("hello world foo", "en"), 3)

    def test_russian_counts_words(self):
        self.assertEqual(count_speech_units("привет как дела", "ru"), 3)

    def test_chinese_counts_chars(self):
        # CJK: one char = one syllable, matches original len() behaviour
        self.assertEqual(count_speech_units("我觉得不错", "zh"), 5)

    def test_japanese_counts_chars(self):
        self.assertEqual(count_speech_units("これはいい", "ja"), 5)

    def test_auto_falls_back_to_words(self):
        self.assertEqual(count_speech_units("hello world", "auto"), 2)

    def test_empty(self):
        self.assertEqual(count_speech_units("   ", "en"), 0)


class TestEnglishNormalSpeech(unittest.TestCase):
    def test_normal_english_not_flagged(self):
        # 8 words / 1.6s = 5 words/sec = normal speech.
        # Old code: 43 chars / 1.6s = 26.9 cps > 25 → WRONGLY flagged.
        seg = FakeSegment("I think we should try this approach instead", 0.0, 1.6)
        _layer3_speech_rate(seg, "en")
        self.assertFalse(seg.uncertain, "normal-speed English must not be flagged")
        self.assertFalse(seg.filtered)

    def test_hallucination_english_flagged(self):
        # 10 repeated words / 1.0s = 10 words/sec > threshold → flagged.
        seg = FakeSegment("yes yes yes yes yes yes yes yes yes yes", 0.0, 1.0)
        _layer3_speech_rate(seg, "en")
        self.assertTrue(seg.uncertain, "repetition hallucination must be flagged")


class TestChineseUnchanged(unittest.TestCase):
    def test_normal_chinese_not_flagged(self):
        # 9 chars / 1.5s = 6 chars/sec < 15 → not flagged (unchanged behaviour).
        seg = FakeSegment("我觉得这个方案不错", 0.0, 1.5)
        _layer3_speech_rate(seg, "zh")
        self.assertFalse(seg.uncertain)

    def test_hallucination_chinese_flagged(self):
        # 16 chars / 1.0s = 16 chars/sec > 15 → flagged (unchanged behaviour).
        seg = FakeSegment("对对对对对对对对对对对对对对对对", 0.0, 1.0)
        _layer3_speech_rate(seg, "zh")
        self.assertTrue(seg.uncertain)


class TestImpossibleDuration(unittest.TestCase):
    def test_short_english_word_not_killed(self):
        # "Apple!" = 1 word, 6 chars, dur < 0.1s.
        # Old code: text_len=6 > 5 → WRONGLY filtered. Fixed: units=1, not > 2.
        seg = FakeSegment("Apple!", 0.0, 0.05)
        _layer3_speech_rate(seg, "en")
        self.assertFalse(seg.filtered, "single short word must not be killed")

    def test_impossible_multiword_marks_only(self):
        """断言翻转（2026-09-04）：多词零时长只标记，不删除。

        P250 关心的是**计数口径**（`count_speech_units` 按词还是按字），
        那部分一个字没改。变的只是「数出来之后怎么处置」——从删除改成标记。

        这条分支 26 次开火从没记过删了什么；补日志重跑真实素材后，
        五次开火五次删真话，其中就有 `'We apologize for that.'`（dur=0.080）。
        """
        seg = FakeSegment("this is totally impossible", 0.0, 0.05)
        _layer3_speech_rate(seg, "en")
        self.assertFalse(seg.filtered, "时长异常已交出删除权")
        self.assertTrue(seg.uncertain, "但要标存疑")
        self.assertEqual(seg.filter_reason, "layer3_impossible_duration_uncertain")


if __name__ == "__main__":
    unittest.main()
