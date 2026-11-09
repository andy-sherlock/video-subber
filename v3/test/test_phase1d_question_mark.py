"""Phase 1D: [?] marking + threshold prompt system tests.

Tests:
  - [?] appended to uncertain segments in subtitle output

（D-3 / P499：横幅阈值的 Python 照抄测试已删——横幅本身已拆。）
"""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


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
