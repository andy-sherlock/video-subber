"""G12 第二批收尾 ②：无障碍标记一律保留。

**这批要防的错误**：2026-09-02 的产品裁决「无障碍字幕保留」没有代码落点——
它写在文档里，靠人每次记得。于是同一条裁决被执行了两遍、两遍结论相反：

    括号描述 [Music]  → 2026-09-04 收尾改成只标记 ✅   真实素材开火 **0 次**
    音符     ♪♪♪      → 仍在删除                ❌   真实素材开火 **22 次**

**修的是从没开过火的那条，留下的是开火 22 次的那条。**

按「防护优先于补救」：
  · 病的来源：**自己种的**（同一条规则在代码里有两份，一份忘了改）
  · 防护层次：**第 1 层**——规则收进 `is_accessibility_marker()`，
    两条分支都必须经过它，「只改一处忘了另一处」在结构上写不出来
  · 引入的新病：无
  · 补救方案：不需要

下面的 22 次开火数据来自 26 个真实任务的日志（`~/.video-subber/logs`）：
    ♪♪♪ 11 次 · 🎵 6 次 · ♪♪ 5 次
"""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class DummySeg:
    def __init__(self, text, start=0.0, end=2.0):
        self.text = text
        self.start = start
        self.end = end
        self.sensevoice_tag = None
        self.filtered = False
        self.filter_reason = None
        self.uncertain = False
        self.words = []
        self.id = 0
        self.speaker_change = False
        self.zh = ""


class TestAccessibilityMarkerRule(unittest.TestCase):
    """判定函数本身。这是唯一一份规则。"""

    def test_music_symbols_are_markers(self):
        """真实素材里删过的那 22 条，全部判为无障碍标记。"""
        from backend.core.hallucination_filter import is_accessibility_marker
        for t in ("♪♪♪", "🎵", "♪♪", "♫", "🎶"):
            self.assertTrue(is_accessibility_marker(t), f"{t!r} 是音乐标记，必须保留")

    def test_bracket_descriptions_are_markers(self):
        """括号描述——收尾第一版只放过了这一类。"""
        from backend.core.hallucination_filter import is_accessibility_marker
        for t in ("[Music]", "(笑)", "（掌声）", "【背景音效】", "[APPLAUSE]"):
            self.assertTrue(is_accessibility_marker(t), f"{t!r} 是括号描述，必须保留")

    def test_pure_punctuation_is_not_a_marker(self):
        """纯标点不是无障碍标记——它不携带信息，删掉不影响任何观众。

        **这条守的是边界**：把「无障碍标记」放宽成「所有纯符号」等于
        把第 2 类删除权整个交出去，那不是本次的意图。
        """
        from backend.core.hallucination_filter import is_accessibility_marker
        for t in ("...", "???", "——", "。。。", "!!!", "、、、"):
            self.assertFalse(is_accessibility_marker(t), f"{t!r} 是纯标点，不是无障碍标记")

    def test_real_speech_is_not_a_marker(self):
        """真字幕不受影响，含音符的歌词也不受影响。"""
        from backend.core.hallucination_filter import is_accessibility_marker
        for t in ("咋样啊，海龟帮？", "the music was playing",
                  "♪ Never gonna give you up ♪", "Music"):
            self.assertFalse(is_accessibility_marker(t), f"{t!r} 含实义文字，不是标记")

    def test_empty_is_not_a_marker(self):
        """空文字走空文字那条分支，不该被无障碍规则截胡。"""
        from backend.core.hallucination_filter import is_accessibility_marker
        for t in ("", "   ", None):
            self.assertFalse(is_accessibility_marker(t))


class TestMusicSymbolsSurviveTheFilter(unittest.TestCase):
    """端到端：过滤器不再删掉音符。**这是本次真正要保证的结果。**"""

    def test_music_note_run_is_marked_not_deleted(self):
        """♪♪♪ —— 真实素材开火 11 次，此前每次都被删。"""
        from backend.core.hallucination_filter import filter_hallucinations
        seg = DummySeg("♪♪♪", start=10.0, end=12.0)
        result = filter_hallucinations([seg], source_lang="en")
        self.assertEqual(len(result), 1, "音符必须留在成品里")
        self.assertFalse(seg.filtered)
        self.assertTrue(seg.uncertain, "留下但要标存疑，用户有权知道这条不确定")

    def test_music_emoji_is_marked_not_deleted(self):
        """🎵 —— 真实素材开火 6 次。"""
        from backend.core.hallucination_filter import filter_hallucinations
        seg = DummySeg("🎵", start=5.0, end=7.0)
        result = filter_hallucinations([seg], source_lang="en")
        self.assertEqual(len(result), 1)
        self.assertTrue(seg.uncertain)

    def test_bracket_still_survives(self):
        """括号描述保持收尾时的行为不变（回归保护）。"""
        from backend.core.hallucination_filter import filter_hallucinations
        seg = DummySeg("[Music]", start=3.0, end=5.0)
        result = filter_hallucinations([seg], source_lang="en")
        self.assertEqual(len(result), 1)
        self.assertTrue(seg.uncertain)

    def test_pure_punctuation_still_deleted(self):
        """纯标点仍然删除——**删除权只收窄，不整个交出去**。

        这条是本次改动的反向闸门：如果有人把无障碍判定放宽到「所有纯符号」，
        这条会红。
        """
        from backend.core.hallucination_filter import filter_hallucinations
        seg = DummySeg("...", start=3.0, end=5.0)
        result = filter_hallucinations([seg], source_lang="en")
        self.assertEqual(len(result), 0, "纯标点物理上不可能是字幕，仍应删除")

    def test_real_speech_untouched(self):
        """真字幕一个字都不动。"""
        from backend.core.hallucination_filter import filter_hallucinations
        seg = DummySeg("the music was playing in the background", start=1.0, end=4.0)
        result = filter_hallucinations([seg], source_lang="en")
        self.assertEqual(len(result), 1)
        self.assertFalse(seg.filtered)
        self.assertFalse(seg.uncertain)


class TestOneRuleOnePlace(unittest.TestCase):
    """**第 1 层防护的证明**：两条分支走同一个函数。

    这一条不是在测行为，是在测**结构**。它红的时候说明规则又变成两份了。
    """

    def test_both_shapes_carry_the_same_reason(self):
        """音符和括号描述打的是**同一个** `filter_reason`。

        那个字符串在生产代码里只出现一次，所以两边拿到同一个值
        = 两边确实经过同一段代码。规则要是散成两份，两个 reason 会不一样。
        """
        from backend.core.hallucination_filter import filter_hallucinations
        note = DummySeg("♪♪♪", start=10.0, end=12.0)
        bracket = DummySeg("[Music]", start=3.0, end=5.0)
        filter_hallucinations([note], source_lang="en")
        filter_hallucinations([bracket], source_lang="en")
        self.assertEqual(note.filter_reason, "layer4_accessibility_uncertain")
        self.assertEqual(bracket.filter_reason, note.filter_reason,
                         "两种形状必须走同一条代码路径")

    def test_removing_the_rule_makes_the_note_die(self):
        """把判定函数换掉，音符立刻被删 —— 证明它真的经过这个函数。

        **只用音符做变异，不用括号描述。** 括号描述含字母数字，
        走不到纯符号删除分支，去掉规则它也不会死——拿它做变异测不出东西。
        （2026-09-04 本条第一版就是这么写的，红了，改的是测试不是代码。）
        """
        from backend.core import hallucination_filter as hf

        original = hf.is_accessibility_marker
        try:
            hf.is_accessibility_marker = lambda text: False
            note = DummySeg("♪♪♪", start=10.0, end=12.0)
            self.assertEqual(len(hf.filter_hallucinations([note], source_lang="en")), 0,
                             "去掉规则后音符应当被删")
        finally:
            hf.is_accessibility_marker = original


if __name__ == "__main__":
    unittest.main()
