"""Tests for Phase 1C: LLM sentence splitting merged into translation step.

Run: cd v3 && .venv/bin/python -m unittest discover -s test -p "test_*.py" -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
import re


class FakeSegment:
    """Minimal segment for testing split logic."""
    def __init__(self, id=0, start=0.0, end=1.0, text="hello"):
        self.id = id
        self.start = start
        self.end = end
        self.text = text
        self.zh = ""
        self.speaker_change = False
        self.uncertain = False
        self.words = []


class TestSubNumberParsing(unittest.TestCase):
    """Tests for [N.M] parsing from LLM translation output."""

    def setUp(self):
        self.pattern_single = re.compile(r"\[(\d+)\]\s*(.+)")
        self.pattern_sub = re.compile(r"\[(\d+)\.(\d+)\]\s*(.+)")

    def test_single_number_parsed(self):
        m = self.pattern_single.search("[0] 你好世界")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "0")
        self.assertEqual(m.group(2), "你好世界")

    def test_sub_number_not_matched_by_single_pattern(self):
        """Single pattern [N] should NOT match [0.1] because ] follows digits directly."""
        m = self.pattern_single.search("[0.1] 前半句")
        self.assertIsNone(m, "[0.1] should not be matched by single-number pattern")

    def test_sub_number_matched_by_sub_pattern(self):
        m = self.pattern_sub.search("[0.1] 前半句")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "0")
        self.assertEqual(m.group(2), "1")
        self.assertEqual(m.group(3), "前半句")

    def test_sub_number_matched_by_combined_pattern(self):
        pattern = re.compile(r"\[(\d+(?:\.\d+)?)\]\s*(.+)")
        m = pattern.search("[0.1] 前半句")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "0.1")
        self.assertEqual(m.group(2), "前半句")

    def test_combined_pattern_matches_single(self):
        pattern = re.compile(r"\[(\d+(?:\.\d+)?)\]\s*(.+)")
        m = pattern.search("[5] 译文")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "5")
        self.assertEqual(m.group(2), "译文")

    def test_combined_pattern_handles_multiple_sub_numbers(self):
        pattern = re.compile(r"\[(\d+(?:\.\d+)?)\]\s*(.+)")
        m = pattern.search("[3.2] 第二子句")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "3.2")
        self.assertEqual(m.group(2), "第二子句")

    def test_sub_check_parsing_order(self):
        """Sub-number must be checked BEFORE single-number pattern."""
        raw = "[0.1] 这是前半句\n[0.2] 这是后半句\n[1] 正常翻译"
        sub_pattern = re.compile(r"\[(\d+)\.(\d+)\]\s*(.+)")
        single_pattern = re.compile(r"\[(\d+)\]\s*(.+)")

        sub_splits = {}
        translated = {}

        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            # Check sub first
            m_sub = sub_pattern.search(line)
            if m_sub:
                orig_idx = int(m_sub.group(1))
                sub_text = m_sub.group(3).strip()
                if orig_idx not in sub_splits:
                    sub_splits[orig_idx] = []
                sub_splits[orig_idx].append(sub_text)
                continue
            m = single_pattern.search(line)
            if m:
                idx = int(m.group(1))
                translated[idx] = m.group(2).strip()

        self.assertEqual(sub_splits, {0: ["这是前半句", "这是后半句"]})
        self.assertEqual(translated, {1: "正常翻译"})


class TestBuildSplitSegments(unittest.TestCase):
    """Tests for _build_split_segments helper."""

    def test_two_way_split(self):
        from backend.core.sentence_splitter import _build_split_segments
        seg = FakeSegment(id=5, start=0.0, end=2.0, text="hello")
        parts = ["前半", "后半"]
        results = _build_split_segments(seg, parts)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].zh, "前半")
        self.assertEqual(results[1].zh, "后半")
        self.assertEqual(results[0].start, 0.0)
        self.assertEqual(results[1].end, 2.0)
        # Duration proportional to char count (2+2=4, each gets 1.0s)
        self.assertAlmostEqual(results[0].end, 1.0, places=2)
        self.assertAlmostEqual(results[1].start, 1.0, places=2)

    def test_three_way_split(self):
        from backend.core.sentence_splitter import _build_split_segments
        seg = FakeSegment(id=1, start=0.0, end=3.0, text="test")
        parts = ["短", "中等长度", "更长的部分"]
        results = _build_split_segments(seg, parts)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[2].end, 3.0)
        # IDs: seg.id * 100 + j
        self.assertEqual(results[0].id, 100)
        self.assertEqual(results[1].id, 101)
        self.assertEqual(results[2].id, 102)

    def test_single_part_no_split(self):
        from backend.core.sentence_splitter import _build_split_segments
        seg = FakeSegment(id=0, start=0.0, end=1.0, text="hi")
        parts = ["单一段"]
        results = _build_split_segments(seg, parts)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].zh, "单一段")
        self.assertEqual(results[0].start, 0.0)
        self.assertEqual(results[0].end, 1.0)

    def test_speaker_change_preserved_on_first(self):
        from backend.core.sentence_splitter import _build_split_segments
        seg = FakeSegment(id=0, start=0.0, end=2.0, text="hi")
        seg.speaker_change = True
        parts = ["A", "B"]
        results = _build_split_segments(seg, parts)
        self.assertTrue(results[0].speaker_change)
        self.assertFalse(results[1].speaker_change)


class TestApplyPrecomputedSplits(unittest.TestCase):
    """Tests for llm_split_segments with pre_splits arg."""

    def test_pre_splits_applied(self):
        """When pre_splits is provided, those segments get split without LLM call."""
        from backend.core.sentence_splitter import llm_split_segments
        seg0 = FakeSegment(id=0, start=0.0, end=2.0, text="line one")
        seg0.zh = "一条很长的中文翻译超过二十字需要拆分"  # >20 chars
        seg1 = FakeSegment(id=1, start=2.0, end=3.0, text="line two")
        seg1.zh = "正常长度"
        seg2 = FakeSegment(id=2, start=3.0, end=5.0, text="line three")
        seg2.zh = "另一条很长的中文翻译也需要拆分一下"  # >20 chars

        segments = [seg0, seg1, seg2]
        pre_splits = {
            0: ["一条很长的中文翻译", "超过二十字需要拆分"],
            2: ["另一条很长的中文翻译", "也需要拆分一下"],
        }

        result = llm_split_segments(segments, pre_splits=pre_splits)
        # seg0 -> 2 parts, seg1 -> 1 part, seg2 -> 2 parts = 5 total
        self.assertEqual(len(result), 5)
        self.assertEqual(result[0].zh, "一条很长的中文翻译")
        self.assertEqual(result[1].zh, "超过二十字需要拆分")
        self.assertEqual(result[2].zh, "正常长度")  # seg1 unchanged
        self.assertEqual(result[3].zh, "另一条很长的中文翻译")
        self.assertEqual(result[4].zh, "也需要拆分一下")

    def test_pre_splits_empty_dict_noop(self):
        """Empty pre_splits should fall through to normal behavior."""
        from backend.core.sentence_splitter import llm_split_segments
        seg = FakeSegment(id=0, start=0.0, end=1.0, text="ok")
        seg.zh = "短文本"
        result = llm_split_segments([seg], pre_splits={})
        # With empty pre_splits and no LLM, falls to rule-based.
        # Short text -> no split.
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].zh, "短文本")

    def test_pre_splits_none_noop(self):
        """None pre_splits should behave like no pre_splits arg."""
        from backend.core.sentence_splitter import llm_split_segments
        seg = FakeSegment(id=0, start=0.0, end=1.0, text="ok")
        seg.zh = "短文本"
        result = llm_split_segments([seg], pre_splits=None)
        self.assertEqual(len(result), 1)

    def test_pre_splits_with_rule_fallback_for_unsplit(self):
        """Segments NOT in pre_splits that are long should get rule-based split."""
        from backend.core.sentence_splitter import llm_split_segments
        seg = FakeSegment(id=0, start=0.0, end=2.0, text="long text")
        seg.zh = "这是一条很长的中文翻译超过二十个字需要规则断句来处理它"
        # pre_splits only covers id=0 but id=0 is in pre_splits with 1 part? No...
        # Let me test: segment too long, not in pre_splits -> rule-based fallback
        seg2 = FakeSegment(id=1, start=2.0, end=3.0, text="ok")
        seg2.zh = "短"
        pre_splits = {1: ["a", "b"]}  # id=1 has pre-splits but id=0 doesn't
        result = llm_split_segments([seg, seg2], pre_splits=pre_splits)
        # seg (id=0) is long, not in pre_splits -> rule-based: should get split
        # seg2 (id=1) in pre_splits -> gets pre_splits
        # Total should be > 2
        self.assertGreater(len(result), 2)


class TestSubSplitReturnValue(unittest.TestCase):
    """Tests that sub_splits are properly returned from translation parsing."""

    def test_parsing_single_and_sub_mixed(self):
        """Simulate LLM output with both [N] and [N.M] lines."""
        # Simulate what _translate_whole_with_markers does
        pattern_sub = re.compile(r"\[(\d+)\.(\d+)\]\s*(.+)")
        pattern_single = re.compile(r"\[(\d+)\]\s*(.+)")

        raw = (
            "[0] 这是一个正常的翻译\n"
            "[1.1] 这个翻译比较长需要拆成两部分\n"
            "[1.2] 这是第二部分确保每句不超过二十字\n"
            "[2] 第三条翻译\n"
            "[3.1] 第四条也超长了\n"
            "[3.2] 所以也拆成两个子句\n"
            "[3.3] 第三个子句也很重要\n"
        )
        texts = ["a"] * 4  # 4 segments, indices 0-3
        translated = ["[翻译失败]"] * 4
        sub_splits = {}

        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            m_sub = pattern_sub.search(line)
            if m_sub:
                orig_idx = int(m_sub.group(1))
                sub_text = m_sub.group(3).strip()
                if orig_idx not in sub_splits:
                    sub_splits[orig_idx] = []
                sub_splits[orig_idx].append(sub_text)
                continue
            m = pattern_single.search(line)
            if m:
                idx = int(m.group(1))
                zh = m.group(2).strip()
                if 0 <= idx < 4 and zh:
                    translated[idx] = zh

        # Finalize
        final_sub_splits = {}
        for idx, parts in sorted(sub_splits.items()):
            if len(parts) >= 2:
                translated[idx] = " ".join(parts)
                final_sub_splits[idx] = parts
            elif len(parts) == 1:
                if translated[idx] == "[翻译失败]":
                    translated[idx] = parts[0]

        self.assertEqual(translated[0], "这是一个正常的翻译")
        self.assertEqual(translated[1], "这个翻译比较长需要拆成两部分 这是第二部分确保每句不超过二十字")
        self.assertEqual(translated[2], "第三条翻译")
        self.assertEqual(translated[3], "第四条也超长了 所以也拆成两个子句 第三个子句也很重要")

        self.assertEqual(len(final_sub_splits), 2)
        self.assertEqual(final_sub_splits[1], ["这个翻译比较长需要拆成两部分", "这是第二部分确保每句不超过二十字"])
        self.assertEqual(final_sub_splits[3], ["第四条也超长了", "所以也拆成两个子句", "第三个子句也很重要"])

    def test_no_sub_numbers_produced(self):
        """If LLM doesn't produce sub-numbers, sub_splits stays empty."""
        raw = (
            "[0] 翻译一\n"
            "[1] 翻译二\n"
            "[2] 翻译三\n"
        )
        texts = ["a", "b", "c"]
        translated = ["[翻译失败]"] * 3
        sub_splits = {}
        pattern_sub = re.compile(r"\[(\d+)\.(\d+)\]\s*(.+)")
        pattern_single = re.compile(r"\[(\d+)\]\s*(.+)")

        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            m_sub = pattern_sub.search(line)
            if m_sub:
                orig_idx = int(m_sub.group(1))
                sub_text = m_sub.group(3).strip()
                if orig_idx not in sub_splits:
                    sub_splits[orig_idx] = []
                sub_splits[orig_idx].append(sub_text)
                continue
            m = pattern_single.search(line)
            if m:
                idx = int(m.group(1))
                zh = m.group(2).strip()
                if 0 <= idx < 3 and zh:
                    translated[idx] = zh

        # No sub-numbers -> sub_splits stays empty
        self.assertEqual(sub_splits, {})


class TestRuleBasedSplit(unittest.TestCase):
    """Tests for _rule_based_split function."""

    def test_short_text_not_split(self):
        from backend.core.sentence_splitter import _rule_based_split
        seg = FakeSegment(id=0, start=0.0, end=1.0, text="hello")
        seg.zh = "短文本"
        result = _rule_based_split(seg)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].zh, "短文本")

    def test_long_text_rule_split(self):
        from backend.core.sentence_splitter import _rule_based_split
        seg = FakeSegment(id=3, start=0.0, end=2.0, text="hi")
        seg.zh = "这是一条非常长的中文字幕文本需要被规则断句来分割成两半"
        result = _rule_based_split(seg)
        # Expect split into 2 parts
        self.assertEqual(len(result), 2)
        combined = result[0].zh + result[1].zh
        self.assertEqual(combined, seg.zh)
        # First segment gets seg.id * 100 + 0
        self.assertEqual(result[0].id, 300)

    def test_exactly_20_chars_not_split(self):
        from backend.core.sentence_splitter import _rule_based_split
        seg = FakeSegment(id=0, start=0.0, end=1.0, text="ok")
        seg.zh = "一二三四五六七八九十0123456789"  # exactly 20 chars
        result = _rule_based_split(seg)
        self.assertEqual(len(result), 1)

    def test_failed_translation_not_split(self):
        from backend.core.sentence_splitter import _rule_based_split
        seg = FakeSegment(id=0, start=0.0, end=1.0, text="fail")
        seg.zh = "[翻译失败]"
        # len("[翻译失败]") = 6, which is <= 20
        # _rule_based_split uses len(zh), "[翻译失败]" is 6 chars
        self.assertEqual(len("[翻译失败]"), 6)
        result = _rule_based_split(seg)
        self.assertEqual(len(result), 1)


class TestSplitZhSimple(unittest.TestCase):
    """Tests for split_zh_simple function."""

    def test_basic_split(self):
        from backend.core.sentence_splitter import split_zh_simple
        result = split_zh_simple("你好。世界！")
        self.assertEqual(result, ["你好。", "世界！"])

    def test_no_punctuation(self):
        from backend.core.sentence_splitter import split_zh_simple
        result = split_zh_simple("没有标点的长句")
        self.assertEqual(result, ["没有标点的长句"])

    def test_empty_string(self):
        from backend.core.sentence_splitter import split_zh_simple
        result = split_zh_simple("")
        self.assertEqual(result, [""])

    def test_english_question(self):
        from backend.core.sentence_splitter import split_zh_simple
        result = split_zh_simple("Hello? World!")
        self.assertEqual(result, ["Hello?", "World!"])


if __name__ == "__main__":
    unittest.main()
