"""G2 · C1 + C2：润色 accepted 计数器只数「真改动」，不数「赋值次数」。

P423 实测：七个任务共 255 次「采纳」，其中 218 次（85%）模型原样抄回原文。
日语任务 3739d2e686 日志写 accepted=120，成品与模型原始输出相似度 1.0000、
改动区块 0——**这把尺子在撒谎**。

本批所有效果验收都要靠这个数字，所以它必须先修好（施工顺序第 2 步）。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest

from backend.worker import _apply_refiner_corrections


class DummySeg:
    def __init__(self, id, text):
        self.id = id
        self.text = text
        self.uncertain = True


class TestG2C1AcceptedCountsRealChanges(unittest.TestCase):
    def test_identical_fix_not_counted(self):
        """模型原样抄回 → 不算一次采纳。"""
        segments = [DummySeg(1, "the fudge stack is ready")]
        corrections = [{"seg_id": 1, "fixed_text": "the fudge stack is ready"}]

        accepted = _apply_refiner_corrections(segments, corrections, "single")

        self.assertEqual(accepted, 0)

    def test_real_change_still_counted(self):
        """真改了 → 照常计数。修尺子不能把尺子修没了。"""
        segments = [DummySeg(1, "the fudge stack is ready")]
        corrections = [{"seg_id": 1, "fixed_text": "the fudge sack is ready"}]

        accepted = _apply_refiner_corrections(segments, corrections, "single")

        self.assertEqual(accepted, 1)
        self.assertEqual(segments[0].text, "the fudge sack is ready")

    def test_identical_fix_still_clears_uncertain(self):
        """原样抄回不计数，但「模型看过这段、认为没问题」仍是有效结论。

        uncertain 的清除条件是「模型审过」，不是「模型改过」。
        C1 只动计数，不许顺手改掉这个语义。
        """
        segments = [DummySeg(1, "the fudge stack is ready")]
        corrections = [{"seg_id": 1, "fixed_text": "the fudge stack is ready"}]

        _apply_refiner_corrections(segments, corrections, "single")

        self.assertFalse(segments[0].uncertain)

    def test_mixed_batch_counts_only_changed(self):
        """一批里真改 1 条、抄回 2 条 → accepted == 1。"""
        segments = [DummySeg(1, "aaa"), DummySeg(2, "bbb"), DummySeg(3, "ccc")]
        corrections = [
            {"seg_id": 1, "fixed_text": "aaa"},
            {"seg_id": 2, "fixed_text": "bbb!"},
            {"seg_id": 3, "fixed_text": "ccc"},
        ]

        accepted = _apply_refiner_corrections(segments, corrections, "single")

        self.assertEqual(accepted, 1)


if __name__ == "__main__":
    unittest.main()
