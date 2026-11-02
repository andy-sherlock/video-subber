"""G12 第二批收尾 ③：L3 的两条时长分支交出删除权。

**为什么改**：这两条曾是删除分支，26 次开火**一次都没记过删了什么**
（`dur <= 0` 那条根本没有日志行）。而本批所有判断都建立在「打开看」上面——
关键词 12:0、循环句式 2:0、L5 0:1、SenseVoice 3492:0，每一条都是先看内容再定。
唯独这两条查不了。

补上 `text=` 日志后重跑两支真实素材，**五次开火全部现形**：

    10 分钟片      'Five, seven,'            dur=0.000  真人数数
                   'We apologize for that.'  dur=0.080  真实空乘广播
                   'a bit.'                  dur=0.000  真话
    32 分钟片      'Chto ma?'                dur=0.000  含糊但有人声
                   'thank you.'              dur=0.000  落在成品 708.6→711.8 的空档，
                                                        前后是「你很有艺术气质」
                                                        「我来自罗马尼亚」

**五比零。**

**更要紧的是它和段三自相矛盾**：段三证明「零时长 = 模型不知道它什么时候说的，
不等于它没说」，据此保下 449 个零时长词；这里却把由这些词组成的段整条删掉。
同一个信号，在词那层救，在段这层杀。

**接管者**（删掉这条，没有失败变得没人管）：
  · 成串幻觉 → 词级游程判据。真实素材实测 `'oh' ×20 起1.44s 整条删除`，
    **用户报的那两条假字幕正是它抓的，不是这里。**
  · 0 秒字幕 → `display_timeline.py:341` 的 0.84 秒补救 + 合并，
    并如实报「N 条字幕显示偏短」。

**已知并接受的残留**：零时长且只有两个同词的段（`'oh, oh'`）不再被删——
词级判据要求同一瞬间 ≥3 个同词。P459「单条成对的幻觉删不掉」的延伸，
12 支真实任务里 0 次出现。
"""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class DummySeg:
    def __init__(self, text, start=0.0, end=1.0):
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


class TestRealDeletionsSurvive(unittest.TestCase):
    """五句真话，逐句取自 2026-09-04 两支真实素材的 debug.log。"""

    # (text, start, end, 出处)
    REAL_DELETED = [
        ("Five, seven,", 68.45, 68.45, "10 分钟片 · dur=0.000"),
        ("We apologize for that.", 81.97, 82.05, "10 分钟片 · dur=0.080"),
        ("a bit.", 432.62, 432.62, "10 分钟片 · dur=0.000"),
        ("Chto ma?", 54.55, 54.55, "32 分钟片 · dur=0.000"),
        ("thank you.", 710.64, 710.64, "32 分钟片 · dur=0.000"),
    ]

    def test_all_five_real_sentences_survive(self):
        from backend.core.hallucination_filter import filter_hallucinations
        for text, start, end, where in self.REAL_DELETED:
            with self.subTest(text=text, where=where):
                seg = DummySeg(text, start=start, end=end)
                result = filter_hallucinations([seg], source_lang="en")
                self.assertEqual(len(result), 1,
                                 f"{text!r} 是真人说的话（{where}），不得删除")
                self.assertFalse(seg.filtered)

    def test_they_are_marked_uncertain(self):
        """留下但要标存疑——时长异常仍然是「值得怀疑」，只是不够格删除。"""
        from backend.core.hallucination_filter import filter_hallucinations
        for text, start, end, where in self.REAL_DELETED:
            with self.subTest(text=text, where=where):
                seg = DummySeg(text, start=start, end=end)
                filter_hallucinations([seg], source_lang="en")
                self.assertTrue(seg.uncertain,
                                f"{text!r} 应标 uncertain，用户有权知道这条不确定")


class TestNoSegmentLevelDurationDelete(unittest.TestCase):
    """盯住函数本身：`_layer3_speech_rate` 不许再置 `filtered`。

    这一条是结构性的。它红了就说明有人把时长删除权加回来了。
    """

    def test_layer3_never_sets_filtered(self):
        from backend.core.hallucination_filter import _layer3_speech_rate
        cases = [
            DummySeg("This should not exist", start=5.0, end=5.0),      # dur = 0
            DummySeg("Hello world impossible", start=0.0, end=0.05),    # dur < 0.1
            DummySeg("negative duration somehow", start=9.0, end=8.0),  # dur < 0
            DummySeg("这是一个非常长的中文句子测试幻觉检测", start=0.0, end=1.0),  # 语速异常
        ]
        for seg in cases:
            with self.subTest(text=seg.text[:20]):
                _layer3_speech_rate(seg, source_lang="en")
                self.assertFalse(seg.filtered,
                                 "L3 已交出删除权——一条时长分支都不许置 filtered")


class TestOriginalBugStaysFixed(unittest.TestCase):
    """**回归闸门**：交出时长删除权后，用户报的那两条假字幕不许回来。

    真实素材实测：`'oh' ×20 起1.44s（整条删除）` 是**词级游程判据**做的，
    不是 `dur <= 0`。这条测试证明接管者确实接得住。
    """

    def test_word_level_criterion_still_catches_the_oh_run(self):
        from backend.core.hallucination_filter import find_impossible_instant_runs
        # 用户报的形状：同一个零宽瞬间挤了一串 oh
        words = [{"text": "oh", "start": 1.44, "end": 1.44} for _ in range(9)]
        words += [{"text": "oh", "start": 2.0 + i * 0.3, "end": 2.2 + i * 0.3}
                  for i in range(11)]
        doomed = find_impossible_instant_runs(words)
        self.assertEqual(len(doomed), len(words),
                         "整条 oh 游程都该被词级判据带走——它才是接管者")

    def test_real_speech_with_repeats_is_not_caught(self):
        """反向守卫：正常语速下重复三次的真话不得被词级判据误伤。

        实测分界是 9 对 1——真话的同瞬间最长连号是 1。
        """
        from backend.core.hallucination_filter import find_impossible_instant_runs
        words = [{"text": "really", "start": 1.0 + i * 0.4, "end": 1.3 + i * 0.4}
                 for i in range(3)]
        self.assertEqual(find_impossible_instant_runs(words), set())


class TestZeroDurationDoesNotReachDeliverable(unittest.TestCase):
    """0 秒字幕由显示层兜住，不是靠过滤器删掉。

    `display_timeline.MIN_DISPLAY_S = 0.84`：不足的向后延长，延不动的合并进邻居，
    并如实记进 `below_min_display` 报给用户。
    """

    def test_min_display_repair_exists(self):
        from backend.core.display_timeline import MIN_DISPLAY_S
        self.assertGreater(MIN_DISPLAY_S, 0,
                           "过滤器交出时长删除权的前提是显示层有 0.84 秒补救")


if __name__ == "__main__":
    unittest.main()
