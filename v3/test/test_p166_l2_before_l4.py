"""TDD tests for P166: L2 runs before L4, l2_status annotation.

  1. Filter order: L1→L2→L4→L5→L3
  2. L2 annotates each segment with l2_status='GREEN'/'YELLOW'
  3. L4 skips keyword rule for l2_status='GREEN' segments

P251: Qwen text reaches L2 via a qwen_chunks list, not per-segment qwen_text.
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
        self.id = 0
        self.speaker_change = False
        self.zh = ""


def _qc(*triples):
    return [{"text": t, "t_start": s, "t_end": e} for (t, s, e) in triples if t]


class TestP166_L2Status(unittest.TestCase):
    """L2 must set l2_status on surviving segments."""

    def test_green_status_set(self):
        """Matching Whisper/Qwen text → l2_status='GREEN'."""
        from backend.core.hallucination_filter import filter_hallucinations

        text = "the quick brown fox jumps over the lazy dog"
        seg = DummySeg(text, start=0.0, end=3.0)
        result = filter_hallucinations([seg], source_lang="en",
                                       qwen_chunks=_qc((text, 0.0, 3.0)))

        self.assertEqual(len(result), 1)
        self.assertTrue(hasattr(result[0], "l2_status"),
                        "L2 should set l2_status attribute")
        self.assertEqual(result[0].l2_status, "GREEN")

    def test_yellow_status_set(self):
        """Very different Whisper/Qwen text → l2_status='YELLOW'."""
        from backend.core.hallucination_filter import filter_hallucinations

        seg = DummySeg("completely different text from whisper model output",
                       start=0.0, end=3.0)
        result = filter_hallucinations(
            [seg], source_lang="en",
            qwen_chunks=_qc(("something entirely unrelated from qwen asr", 0.0, 3.0)))

        self.assertEqual(len(result), 1)
        self.assertTrue(hasattr(result[0], "l2_status"))
        self.assertEqual(result[0].l2_status, "YELLOW")


class TestP166_KeywordSkipOnGreen(unittest.TestCase):
    """L4 keyword rule should be skipped for GREEN segments."""

    def test_keyword_survives_with_green(self):
        """Segment contains 'music' keyword but L2 confirms GREEN → keep."""
        from backend.core.hallucination_filter import filter_hallucinations

        text = "the music was playing in the background today"
        seg = DummySeg(text, start=0.0, end=3.0)
        result = filter_hallucinations([seg], source_lang="en",
                                       qwen_chunks=_qc((text, 0.0, 3.0)))

        self.assertEqual(len(result), 1,
                         "Keyword segment should survive when L2 is GREEN")
        self.assertFalse(result[0].filtered)

    def test_keyword_no_longer_filtered_without_qwen(self):
        """G12 第二批段一：关键词删除权已收回，本条断言随之翻转。

        P166 原本的关注点是「L2 的 GREEN 状态能不能豁免 L4」。关键词分支整个删掉后，
        这个关注点由同类 `test_structural_rules_still_apply_on_green`（括号描述）
        继续守着——**结构性规则仍然不受 GREEN 豁免**，那条测试未改动。
        """
        from backend.core.hallucination_filter import filter_hallucinations

        seg = DummySeg("music", start=0.0, end=1.0)
        result = filter_hallucinations([seg], source_lang="en")

        self.assertEqual(len(result), 1)
        self.assertFalse(seg.filtered)

    def test_structural_rules_still_apply_on_green(self):
        """本条守的是「GREEN 不豁免结构性规则」，用例改成真正的结构性内容。

        G12 第二批收尾：括号描述已交出删除权（与产品裁决「无障碍字幕保留」
        对齐），不再是结构性删除项。**纯符号才是。** 本条的意图——
        L2 判 GREEN 不能让结构性规则失效——一点没变。

        2026-09-04 再次修正用例：`♪♪♪♪` 也交出了删除权（音符同样是无障碍标记，
        真实素材开火 22 次）。**改用纯标点。** 两次改的都是用例，不是意图。
        """
        from backend.core.hallucination_filter import filter_hallucinations

        seg = DummySeg("......", start=0.0, end=1.0)
        result = filter_hallucinations(
            [seg], source_lang="en",
            qwen_chunks=_qc(("music was playing clearly in window", 0.0, 1.0)))

        self.assertEqual(len(result), 0,
                         "纯符号是结构性不可能，GREEN 也不得豁免")


class TestP166_OrderChange(unittest.TestCase):
    """L2 must run before L4 in the new order."""

    def test_l2_annotates_before_l4_filters(self):
        """If L2 runs first, a GREEN keyword segment survives L4."""
        from backend.core.hallucination_filter import filter_hallucinations

        t0 = "the music was playing loudly in the concert hall"
        t1 = "hello world this is normal speech output today"
        segs = [
            DummySeg(t0, start=0.0, end=3.0),
            DummySeg(t1, start=3.0, end=6.0),
        ]
        segs[0].id = 0
        segs[1].id = 1

        result = filter_hallucinations(
            segs, source_lang="en",
            qwen_chunks=_qc((t0, 0.0, 3.0), (t1, 3.0, 6.0)))

        self.assertEqual(len(result), 2,
                         "Both segments should survive: L2 GREEN protects keyword")


if __name__ == '__main__':
    unittest.main()
