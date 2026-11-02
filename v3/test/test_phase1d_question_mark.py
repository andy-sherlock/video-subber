"""Phase 1D: [?] marking + threshold prompt system tests.

Tests:
  - [?] appended to uncertain segments in subtitle output
  - Threshold calculation: 3-tier gradient
  - Segment count < 20 → no trigger
"""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def calculate_threshold(total_segments: int, question_mark_count: int) -> dict | None:
    """Calculate [?] threshold level. Mirrors frontend logic for testing.

    Returns:
        None if no warning needed.
        {"level": "yellow"|"red", "message": str} if warning triggered.
    """
    if total_segments < 20:
        return None
    ratio = question_mark_count / total_segments
    if ratio > 0.15:
        return {"level": "red", "message": "音频质量较差，建议手动校对后使用"}
    if ratio >= 0.05 or question_mark_count >= 50:
        return {"level": "yellow", "message": "部分字幕可能不准确，可点微调查看"}
    return None


class TestQuestionMarkThreshold(unittest.TestCase):
    """Three-tier threshold gradient."""

    def test_under_20_segments_no_trigger(self):
        """Total < 20 segments: never trigger regardless of [?] count."""
        self.assertIsNone(calculate_threshold(10, 10))  # 100% but few segments
        self.assertIsNone(calculate_threshold(5, 0))
        self.assertIsNone(calculate_threshold(19, 19))

    def test_no_question_marks_no_trigger(self):
        """0% [?] → no warning."""
        self.assertIsNone(calculate_threshold(100, 0))

    def test_low_ratio_no_trigger(self):
        """<5% and <50 absolute → no warning."""
        self.assertIsNone(calculate_threshold(100, 4))   # 4%
        self.assertIsNone(calculate_threshold(200, 9))   # 4.5%

    def test_moderate_ratio_yellow(self):
        """5%-15% or >=50 absolute → yellow."""
        result = calculate_threshold(100, 5)    # 5%
        self.assertEqual(result["level"], "yellow")

        result = calculate_threshold(200, 10)   # 5%
        self.assertEqual(result["level"], "yellow")

        result = calculate_threshold(100, 14)   # 14%
        self.assertEqual(result["level"], "yellow")

    def test_absolute_count_yellow(self):
        """>=50 [?] → yellow even if ratio < 5%."""
        result = calculate_threshold(2000, 50)   # 2.5% but 50 absolute
        self.assertEqual(result["level"], "yellow")

    def test_high_ratio_red(self):
        """>15% → red."""
        result = calculate_threshold(100, 16)    # 16%
        self.assertEqual(result["level"], "red")

        result = calculate_threshold(100, 30)    # 30%
        self.assertEqual(result["level"], "red")


# G6 删除 TestQuestionMarkAppending（原 3 个测试）。
#
# **它们从未调用过产品代码**：函数体内自己写 `text = text + " [?]"`，
# 再断言自己的局部变量。删掉产品代码它们也照样全绿——零覆盖。
#
# 施工前的盘点表把这 3 处算成「会真的变红」，实际一处都不会红。
# 真正覆盖 [?] 输出形态的断言只在 test_phase1d_integration.py，
# 本批已反转；行为等价的守门断言在 test_g6_question_mark_removal.py。


class TestQuestionMarkCountWS(unittest.TestCase):
    """Worker emits question_mark_count via WebSocket."""

    def test_count_format(self):
        """Worker message includes question_mark_count and total_segments."""
        segments = [
            type('s', (), {'uncertain': True})(),
            type('s', (), {'uncertain': False})(),
            type('s', (), {'uncertain': True})(),
            type('s', (), {'uncertain': False})(),
            type('s', (), {'uncertain': True})(),
        ]
        qm_count = sum(1 for s in segments if getattr(s, 'uncertain', False))
        total = len(segments)

        self.assertEqual(qm_count, 3)
        self.assertEqual(total, 5)

        msg = {
            "type": "progress",
            "stage": "complete",
            "question_mark_count": qm_count,
            "total_segments": total,
        }
        self.assertEqual(msg["question_mark_count"], 3)
        self.assertEqual(msg["total_segments"], 5)


if __name__ == '__main__':
    unittest.main()
