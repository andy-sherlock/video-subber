"""Translation content gate: reject LLM responses that copy source text.

Reproduces job 0a73f157b2: DeepSeek returned Russian verbatim for all 170
segments, but "170/170 success" was reported because the system only checked
numbered format, not whether the content was actually translated.

Rule: if the returned Chinese text contains < 10% CJK characters, it is not
a valid Chinese translation and should be treated as failure.
"""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestContentGate(unittest.TestCase):

    def test_pure_russian_verbatim_rejected(self):
        """Russian text with zero Chinese chars → reject as untranslated."""
        zh = "Саша, привет. Я к тебе по делу."
        cjk = sum(1 for c in zh if '一' <= c <= '鿿')
        ratio = cjk / max(len(zh), 1)
        self.assertEqual(cjk, 0,
                         "Russian-only text should have zero CJK chars")
        self.assertLess(ratio, 0.1,
                        "Ratio < 10%% → should be marked as [翻译失败]")

    def test_mixed_russian_chinese_rejected(self):
        """95% Russian + one Chinese word → still < 10% → reject."""
        zh = "Саша, привет. 你好. Я к тебе по делу."
        cjk = sum(1 for c in zh if '一' <= c <= '鿿')
        ratio = cjk / max(len(zh), 1)
        self.assertLess(ratio, 0.1,
                        "With 2/40 chars CJK, ratio ~5%% → still < 10%%")

    def test_full_chinese_accepted(self):
        """Full Chinese translation → CJK ratio > 10% → accept."""
        zh = "萨沙，你好。我是来找你谈事情的。能借我点钱吗？"
        cjk = sum(1 for c in zh if '一' <= c <= '鿿')
        ratio = cjk / max(len(zh), 1)
        self.assertGreater(ratio, 0.1,
                           "Full Chinese text must have CJK ratio > 10%%")

    def test_empty_string_is_failure(self):
        """Empty or whitespace-only string → reject."""
        zh = "   "
        cjk = sum(1 for c in zh if '一' <= c <= '鿿')
        ratio = cjk / max(len(zh), 1)
        self.assertEqual(ratio, 0.0,
                         "Empty/whitespace → ratio 0 → reject")

    def test_edge_10_percent_threshold(self):
        """Exactly 10% CJK → accepted (>= 10%)."""
        # 10 chars total, 1 Chinese char = 10%
        zh = "A2345678好"
        cjk = sum(1 for c in zh if '一' <= c <= '鿿')
        ratio = cjk / max(len(zh), 1)
        self.assertAlmostEqual(ratio, 0.1, places=1,
                               msg="1/10 = 10%% → should be at threshold")
        self.assertGreaterEqual(ratio, 0.1,
                                ">= 10%% → accept")


if __name__ == '__main__':
    unittest.main()
