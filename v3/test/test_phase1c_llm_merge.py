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

    # ── G5-1 类型 C：被测对象已按合同删除 ──────────────────────────
    # 原断言（已作废，留档）：
    #   test_two_way_split   —— 断言 ["前半","后半"] 各得 1.0 秒
    #                           （"Duration proportional to char count"）
    #   test_three_way_split —— 断言拆出 3 条、id 为 seg.id*100+j
    # 这两条断言的正是 R-11 禁止的行为：按译文字数比例凭空分配词级时间。
    # 那个时间跟视频里谁在哪一秒说话没有任何关系，是 P401/P402/P414/P415
    # 的来源之一。拆分现已交给 display_timeline，用真实原词边界切。

    def test_no_char_ratio_time_allocation(self):
        """译文分片合回一条，绝不按字数比例造时间。"""
        from backend.core.sentence_splitter import _build_split_segments
        seg = FakeSegment(id=5, start=0.0, end=2.0, text="hello")
        # G12-B 收尾补充（P475）：正文一律取片段自己的译文，分片只提供断开位置。
        # 生产里只有译文够长的片段才会走到这里，假片段要带上分片来自的那条译文。
        seg.zh = "前半后半"
        results = _build_split_segments(seg, ["前半", "后半"])
        self.assertEqual(len(results), 1, "不得产生按字数分时间的伪造分屏")
        self.assertEqual(results[0].start, 0.0)
        self.assertEqual(results[0].end, 2.0)
        self.assertIn("前半", results[0].zh)
        self.assertIn("后半", results[0].zh)

    def test_original_words_preserved_for_downstream_mapping(self):
        """原词表必须留给 display_timeline 做真实边界映射（合同 §10.1）。"""
        from backend.core.sentence_splitter import _build_split_segments
        seg = FakeSegment(id=1, start=0.0, end=3.0, text="test")
        seg.words = [{"word": "test", "start": 0.0, "end": 3.0}]
        results = _build_split_segments(seg, ["短", "中等长度", "更长的部分"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].words, seg.words, "原词表被清空了")

    def test_single_part_no_split(self):
        from backend.core.sentence_splitter import _build_split_segments
        seg = FakeSegment(id=0, start=0.0, end=1.0, text="hi")
        seg.zh = "单一段"   # P475：正文取片段自己的译文（见上一条测试的说明）
        parts = ["单一段"]
        results = _build_split_segments(seg, parts)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].zh, "单一段")
        self.assertEqual(results[0].start, 0.0)
        self.assertEqual(results[0].end, 1.0)

    def test_speaker_change_preserved(self):
        """原断言：拆出的第一条保留 speaker_change、第二条为 False。
        合回一条之后，该标记必须留在这唯一一条上。"""
        from backend.core.sentence_splitter import _build_split_segments
        seg = FakeSegment(id=0, start=0.0, end=2.0, text="hi")
        seg.speaker_change = True
        results = _build_split_segments(seg, ["A", "B"])
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].speaker_change)


class TestApplyPrecomputedSplits(unittest.TestCase):
    """Tests for llm_split_segments with pre_splits arg."""

    def test_pre_splits_applied(self):
        """G5-1 类型 C：pre_splits 不再在这一层产生分屏。

        原断言（已作废，留档）：seg0→2 条、seg1→1 条、seg2→2 条 = 5 条，
        且逐条断言 result[i].zh 等于各分片。它依赖的是「按字数比例给每个
        分片算 start/end」——R-11 禁止的伪造时间。

        新合同：这一层只保证译文内容不丢、原时间不改；真正的分屏由
        display_timeline 用真实原词边界完成。"""
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

        result = llm_split_segments(segments, pre_splits=pre_splits, on_wait=lambda *a: None)
        self.assertEqual(len(result), 3, "不得在这一层产生按字数分时间的分屏")
        joined = "".join(r.zh.replace(" ", "") for r in result)
        for piece in ("一条很长的中文翻译", "超过二十字需要拆分", "正常长度",
                      "另一条很长的中文翻译", "也需要拆分一下"):
            self.assertIn(piece, joined, f"译文丢了「{piece}」")
        self.assertEqual([r.start for r in result], [0.0, 2.0, 3.0])
        self.assertEqual([r.end for r in result], [2.0, 3.0, 5.0])

    def test_pre_splits_empty_dict_noop(self):
        """Empty pre_splits should fall through to normal behavior."""
        from backend.core.sentence_splitter import llm_split_segments
        seg = FakeSegment(id=0, start=0.0, end=1.0, text="ok")
        seg.zh = "短文本"
        result = llm_split_segments([seg], pre_splits={}, on_wait=lambda *a: None)
        # With empty pre_splits and no LLM, falls to rule-based.
        # Short text -> no split.
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].zh, "短文本")

    def test_pre_splits_none_noop(self):
        """None pre_splits should behave like no pre_splits arg."""
        from backend.core.sentence_splitter import llm_split_segments
        seg = FakeSegment(id=0, start=0.0, end=1.0, text="ok")
        seg.zh = "短文本"
        result = llm_split_segments([seg], pre_splits=None, on_wait=lambda *a: None)
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
        result = llm_split_segments([seg, seg2], pre_splits=pre_splits, on_wait=lambda *a: None)
        # G5-1 类型 C。原断言：len(result) > 2 —— 长句在这一层被规则断句拆开。
        # 现在规则断句返回原条不拆（它只会字符中点硬切 + 按字数造时间，
        # 两件事都被 R-11 禁止），拆分交给 display_timeline 的真实原词边界。
        self.assertEqual(len(result), 2, "这一层不得再产生伪造分屏")
        self.assertIn("规则断句", result[0].zh, "长句译文不得丢失")

    def test_collapsed_pre_split_uses_same_rule_fallback_at_any_index(self):
        """P354：异常预切折叠成一条后，不能因为原字幕位置不同而结果不同。"""
        from backend.core.sentence_splitter import llm_split_segments

        long_zh = "这是第一句需要保留。这里是第二句也需要保留。最后一句仍然需要保留。"
        noisy_parts = ["这是第一句需要保留", "这里是第二句", "也需要保留", "最后一句仍然需要保留"]

        at_zero = FakeSegment(id=0, start=0.0, end=0.6, text="same")
        at_zero.zh = long_zh
        zero_result = llm_split_segments([at_zero], pre_splits={0: noisy_parts}, on_wait=lambda *a: None)

        prefix = FakeSegment(id=0, start=0.0, end=0.2, text="prefix")
        prefix.zh = "短"
        at_one = FakeSegment(id=1, start=0.2, end=0.8, text="same")
        at_one.zh = long_zh
        one_result = llm_split_segments([prefix, at_one], pre_splits={1: noisy_parts}, on_wait=lambda *a: None)[1:]

        # G5-1 类型 C：原断言 `assertGreater(len(zero_result), 1, "折叠回长句后
        # 仍应走安全规则断句")` 已作废——规则断句不再自行拆分（它只会字符中点
        # 硬切 + 按字数造时间）。P354 真正要保的是**位置无关性**，那一条不变。
        self.assertEqual(len(zero_result), 1, "这一层不得再产生伪造分屏")
        self.assertEqual(
            [seg.zh for seg in zero_result],
            [seg.zh for seg in one_result],
            "相同字幕不能因为位于第 0 条或第 1 条而得到不同结果",
        )
        for seg in zero_result:
            for piece in noisy_parts:
                pass
        self.assertTrue(
            all(p in "".join(x.zh for x in zero_result) for p in noisy_parts),
            "折叠回长句时译文不得丢失",
        )


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
        # G5-1 类型 C。原断言：拆成 2 条、拼回等于原文、首条 id 为 seg.id*100+0。
        # 旧实现在没有逗号时用 `split_points = [len(zh) // 2]` 从字符中点硬切
        # （「商店」→「商 / 店」），再按字数比例算切点时间。两件事都被 R-11 禁止。
        self.assertEqual(len(result), 1, "规则断句不得再自行拆分")
        self.assertEqual(result[0].zh, seg.zh, "译文必须原样保留")
        self.assertEqual((result[0].start, result[0].end), (seg.start, seg.end))

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
