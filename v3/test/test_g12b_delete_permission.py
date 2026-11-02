"""G12 第二批 段一：收回五层过滤器的删除权。

**为什么要收权**：过滤器的删除结果三个月从未进过成品（断句从过滤前的原始数据
重拼），所以它的误删一直被另一个 bug 挡着，没伤到用户。段二会把它接通——
接通之前必须先把删除权收窄，否则已经在发生的误删会当场对用户生效。

**两处已发生的误删（真实任务，非构造）**：

1. `fc441fb713`：`_layer4_regex` 的关键词子串匹配删掉了**五句正常日语**，
   理由只是句子里含「音楽」两个字。日志 5 行 `L4 关键词: kw='音楽'`。
2. `80703166ff`：`_layer5_dedup` 全片唯一一次开火，删的是真人连说的
   第二句 `Canada.`（100.02 秒）。正常素材上的开火准确率 0/1。

**三处已发生的误删（真实任务，非构造）**——第三处是闸门 2 真跑当场抓到的：

3. `_layer4_regex` 的**循环句式**分支删掉了 `'Canada. Canada.'`（真人连说两次）。
   全库审计这三条文本重复分支删掉的**全部**内容：`'Canada. Canada.'`、
   `'Oh yeah. Oh yeah.'`、`'Meow Meow'`（**全是真话**），
   外加两处 `'oh, oh, oh…'`（幻觉，但 `start == end`，**L3 照样删**）。

**收权后仍保留删除权的白名单（段级四类，可穷举，没有例外）**：
空文字、纯符号、`dur < 0.1 且词数 > 2`、`dur <= 0`。
第五类是词级的「≥3 个同词落在同一零宽瞬间」，由
`test_g12b_evidence_preservation.py` 守着。

**共同性质：这些内容物理上不可能是字幕**，不是「看起来可疑」。

**收窄轨迹**：10 类 →（闸门 2 真跑）5 类 →（收尾）**4 类**。
最后两刀是括号描述（与产品裁决「无障碍字幕保留」冲突）和 L1 的两条丢弃分支
（最后一处「单一弱证据持有删除权」）。

Run: cd v3 && .venv/bin/python -m pytest test/test_g12b_delete_permission.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest


class FakeSegment:
    """与 test_hallucination_filter.py 的 FakeSegment 保持一致。"""

    def __init__(self, id=0, start=0.0, end=1.0, text="hello",
                 sensevoice_tag=None, qwen_text=None, words=None):
        self.id = id
        self.start = start
        self.end = end
        self.text = text
        self.speaker_change = False
        self.uncertain = False
        self.zh = ""
        self.words = words or []
        self.sensevoice_tag = sensevoice_tag
        self.qwen_text = qwen_text
        self.filtered = False
        self.filter_reason = None


def _seg(id, start, end, text):
    return FakeSegment(id=id, start=start, end=end, text=text)


def _texts(segments):
    return [s.text for s in segments]


# ══════════════════════════════════════════════════════════════════
# A1 · 关键词常量必须整个消失
# ══════════════════════════════════════════════════════════════════

class TestA1KeywordConstantGone(unittest.TestCase):

    def test_hallucination_keywords_constant_does_not_exist(self):
        """留一个「只标记」的关键词分支，等于留一条能被加回删除权的路。

        12 次开火 0 次正确——不是收窄，是删掉。
        """
        from backend.core import hallucination_filter

        self.assertFalse(
            hasattr(hallucination_filter, "HALLUCINATION_KEYWORDS"),
            "HALLUCINATION_KEYWORDS 仍然存在；只要它还在，删除权就随时能被加回来",
        )


# ══════════════════════════════════════════════════════════════════
# R1（回归）· fc441fb713 的五句日语必须存活
# ══════════════════════════════════════════════════════════════════

class TestR1JapaneseSentencesSurvive(unittest.TestCase):
    """真实任务 fc441fb713 被 L4 关键词删掉的五句，逐句取自 debug.log。"""

    # (start, text) —— 时长统一给足，避免 L3 语速分支干扰
    REAL_DELETED = [
        (531.94, "私たちは、映画、音楽、シリーズで学校で。"),
        (585.16, "私は、シリーズを見て、音楽を聴いて、"),
        (663.28, "音楽を聴くこと、それらの種類のものが、"),
        (704.14, "音楽を聴くこと、"),
        (1690.00, "音楽を聴く"),
    ]

    def test_all_five_real_japanese_sentences_survive(self):
        from backend.core.hallucination_filter import filter_hallucinations

        segments = [
            _seg(i, start, start + 4.0, text)
            for i, (start, text) in enumerate(self.REAL_DELETED)
        ]
        result = filter_hallucinations(segments, source_lang="ja")

        self.assertEqual(
            _texts(result),
            [text for _start, text in self.REAL_DELETED],
            "含「音楽」的正常日语被删除；这五句是真人说的话（fc441fb713 实测）",
        )

    def test_music_marker_and_lyrics_survive(self):
        """音乐标记与歌词保留（2026-09-02 产品裁决）。

        **用例形状取自真实日志，不是构造的**：`long47` 的 7 次 L4 关键词删除是
        `text='Music'`（裸词，无方括号）与 6 条 `♪ ... ♪` 歌词。

        `[Music]` 这种带方括号的形式由 `TestA7BracketMarksOnly` 覆盖。
        """
        from backend.core.hallucination_filter import filter_hallucinations

        segments = [
            _seg(0, 0.0, 4.0, "Music"),
            _seg(1, 2142.90, 2146.90, "♪ Tumbleweed shadow uptight to you ♪"),
            _seg(2, 2149.46, 2152.46, "♪ Tell me baby, where are you going? ♪"),
        ]
        result = filter_hallucinations(segments, source_lang="en")

        self.assertEqual(len(result), 3, "音乐标记与歌词应保留，产品已裁决")


# ══════════════════════════════════════════════════════════════════
# R2（回归）· 80703166ff 真人连说两句 Canada. 必须都活着
# ══════════════════════════════════════════════════════════════════

class TestR2AdjacentDuplicateSurvives(unittest.TestCase):

    def test_two_adjacent_identical_segments_both_survive(self):
        """真实任务 80703166ff：L5 全片唯一一次开火，删的就是这第二句。

        时间取自 transcript.json 与 debug.log:105。
        """
        from backend.core.hallucination_filter import filter_hallucinations

        segments = [
            _seg(0, 99.464, 99.864, "Canada."),
            _seg(1, 100.020, 100.420, "Canada."),
        ]
        result = filter_hallucinations(segments, source_lang="en")

        self.assertEqual(
            _texts(result), ["Canada.", "Canada."],
            "真人连说两次被 L5 删掉第二次；两条只有 2 条，不满足「连续 ≥3 条」",
        )

    def test_two_adjacent_duplicates_are_not_even_marked(self):
        """2 条不到 3 条，连可疑都不该标——否则前端存疑比例会虚高。"""
        from backend.core.hallucination_filter import filter_hallucinations

        segments = [
            _seg(0, 99.464, 99.864, "Canada."),
            _seg(1, 100.020, 100.420, "Canada."),
        ]
        filter_hallucinations(segments, source_lang="en")

        self.assertFalse(any(s.uncertain for s in segments))


# ══════════════════════════════════════════════════════════════════
# A3 · L5 只标记，永不删除
# ══════════════════════════════════════════════════════════════════

class TestA3Layer5MarksNeverDeletes(unittest.TestCase):

    def test_three_identical_in_a_row_are_marked_but_all_kept(self):
        from backend.core.hallucination_filter import filter_hallucinations

        segments = [
            _seg(0, 10.0, 11.0, "Oh"),
            _seg(1, 11.5, 12.5, "Oh"),
            _seg(2, 13.0, 14.0, "Oh"),
        ]
        result = filter_hallucinations(segments, source_lang="en")

        self.assertEqual(len(result), 3, "L5 收权后只标记，不得删除任何一条")
        self.assertTrue(all(s.uncertain for s in result),
                        "连续 3 条相同应全部标为可疑")

    def test_near_identical_above_similarity_threshold_is_marked(self):
        """相似度 ≥0.9 保持（第一批 ⑤ D 节实测：完全相同 25 处 vs ≥0.9 26 处）。"""
        from backend.core.hallucination_filter import filter_hallucinations

        segments = [
            _seg(0, 10.0, 11.0, "thank you for watching"),
            _seg(1, 11.5, 12.5, "thank you for watching."),
            _seg(2, 13.0, 14.0, "thank you for watching"),
        ]
        result = filter_hallucinations(segments, source_lang="en")

        self.assertEqual(len(result), 3)
        self.assertTrue(all(s.uncertain for s in result))

    def test_layer5_never_sets_filtered(self):
        """直接盯住 `_layer5_dedup` 本身：它的返回里不许有 filtered=True。"""
        from backend.core.hallucination_filter import _layer5_dedup

        segments = [_seg(i, i * 2.0, i * 2.0 + 1.0, "Oh") for i in range(6)]
        kept = _layer5_dedup(segments)

        self.assertEqual(len(kept), 6, "L5 不得丢弃任何一条")
        self.assertFalse(any(s.filtered for s in segments),
                         "L5 不得置 filtered=True——删除权已收回")

    def test_different_adjacent_texts_untouched(self):
        from backend.core.hallucination_filter import filter_hallucinations

        segments = [
            _seg(0, 10.0, 11.0, "Have you ever been to Canada?"),
            _seg(1, 11.5, 13.0, "No, I have not been to Canada."),
        ]
        result = filter_hallucinations(segments, source_lang="en")

        self.assertEqual(len(result), 2)
        self.assertFalse(any(s.uncertain for s in result))


# ══════════════════════════════════════════════════════════════════
# A6（闸门 2 真跑补漏）· L4 的三条文本重复分支也只能标记
# ══════════════════════════════════════════════════════════════════

class TestA6TextRepetitionMarksOnly(unittest.TestCase):
    """P445 早就写明「只能标记可疑：普通词语、**重复句**、语速异常」。

    段一第一版只做了 L5（段级重复），**L4 里三条文本重复分支一条没动**，
    理由是「开火次数少」——只数次数，没看删了什么。段二接通交付流之后，
    这条漏洞对用户生效了。

    **全库审计（这三条分支删掉的全部内容）**：

    | 删了什么 | 判断 |
    |---|---|
    | `'Canada. Canada.'` | **真话**（真人连说两次，闸门 2 真跑三次全部命中） |
    | `'Oh yeah. Oh yeah.'` | **真话** |
    | `'Meow Meow'` | **真话**（学猫叫） |
    | `'oh, oh, oh, …'` ×2 | 幻觉——但它们 `start == end`，**L3 照样删** |

    循环句式 2 次开火 2 次删真话，0 次正确；重复模式抓到的幻觉由 L3 兜住。
    **文本长得像重复是弱证据，弱证据没有删除权。**
    """

    def test_cyclic_sentence_is_marked_not_deleted(self):
        """闸门 2 真跑三次全部命中的那条：真人连说两次 `Canada.`。"""
        from backend.core.hallucination_filter import filter_hallucinations

        seg = _seg(0, 99.464, 100.504, "Canada. Canada.")
        result = filter_hallucinations([seg], source_lang="en")

        self.assertEqual(len(result), 1, "真人连说两次被 L4 循环句式删掉")
        self.assertTrue(seg.uncertain, "该标可疑")

    def test_real_speech_oh_yeah_survives(self):
        from backend.core.hallucination_filter import filter_hallucinations

        seg = _seg(0, 1551.71, 1553.20, "Oh yeah. Oh yeah.")
        result = filter_hallucinations([seg], source_lang="en")

        self.assertEqual(len(result), 1)

    def test_repeated_words_are_marked_not_deleted(self):
        """`Meow Meow`——真人学猫叫。"""
        from backend.core.hallucination_filter import filter_hallucinations

        seg = _seg(0, 2801.49, 2802.60, "Meow Meow")
        result = filter_hallucinations([seg], source_lang="en")

        self.assertEqual(len(result), 1)
        self.assertTrue(seg.uncertain)

    def test_repeated_pattern_is_marked_not_deleted(self):
        from backend.core.hallucination_filter import filter_hallucinations

        seg = _seg(0, 10.0, 14.0, "ha ha ha ha ha")
        result = filter_hallucinations([seg], source_lang="en")

        self.assertEqual(len(result), 1)
        self.assertTrue(seg.uncertain)

    def test_zero_duration_hallucination_run_is_taken_by_word_criterion(self):
        """接管者换人了：**成串幻觉由词级游程判据接管，不再是 L3 的 `dur <= 0`**。

        2026-09-04：L3 两条时长分支交出删除权（五次开火五次删真话，
        见 `test_g12b_duration_marks_only.py`）。本条随之改写。

        **原来的写法测不出接管**：它用的是**没有 words 的段**，而词级判据
        按词工作。真实流水线里这两种情况都不漏：

          · 有 words（正常情况）→ 词级判据整条摘除。真实素材实测
            `'oh' ×20 起1.44s（整条删除）`，**用户报的两条假字幕正是它抓的**
          · 无 words（Whisper 兜底段）→ 文字从来不进交付词流，进不了成品，
            留着只标存疑

        所以本条改成直接盯住接管者本身。
        """
        from backend.core.hallucination_filter import find_impossible_instant_runs

        words = [{"text": "oh", "start": 7.28, "end": 7.28} for _ in range(9)]
        doomed = find_impossible_instant_runs(words)

        self.assertEqual(len(doomed), len(words),
                         "零宽瞬间挤 9 个 oh 是物理不可能，整条游程都该被摘除")


# ══════════════════════════════════════════════════════════════════
# A4 · L3 一行不动（锁定测试，不是 TDD 新行为）
# ══════════════════════════════════════════════════════════════════

class TestA4Layer3MarksOnly(unittest.TestCase):
    """**2026-09-04 断言翻转**：L3 三条分支现在全部只标记，一条都不删。

    原类名是 `TestA4Layer3Unchanged`（「L3 一行不动」），当时的判断是
    「时长异常是物理不可能，不必收权」。**那个判断建立在没看过内容的基础上**——
    这两条分支 26 次开火从没记过删了什么。补上 `text=` 日志重跑两支真实素材，
    五次开火全部现形，**五次全是真话**：

        'Five, seven,' · 'We apologize for that.' · 'a bit.'
        'Chto ma?' · 'thank you.'

    详见 `test_g12b_duration_marks_only.py`。本类改为守「不许再删」。
    """

    def test_zero_duration_segment_is_marked_not_deleted(self):
        """断言翻转：`dur <= 0` 不再删除。

        接管者是**词级游程判据**（同一瞬间 ≥3 个同词），
        真实素材实测 `'oh' ×20 起1.44s（整条删除）`。
        0 秒字幕由 `display_timeline` 的 0.84 秒补救兜住。
        """
        from backend.core.hallucination_filter import filter_hallucinations

        segments = [_seg(0, 7.28, 7.28, "oh oh oh"), _seg(1, 10.0, 12.0, "real speech here")]
        result = filter_hallucinations(segments, source_lang="en")

        self.assertEqual(_texts(result), ["oh oh oh", "real speech here"])
        self.assertTrue(segments[0].uncertain, "留下但要标存疑")

    def test_impossible_duration_is_marked_not_deleted(self):
        """断言翻转：`dur < 0.1 且词数 > 2` 不再删除。

        这正是 `'We apologize for that.'`（dur=0.080，真实空乘广播）
        被删掉的那条分支。
        """
        from backend.core.hallucination_filter import filter_hallucinations

        segments = [
            _seg(0, 1.00, 1.05, "one two three four"),
            _seg(1, 10.0, 12.0, "real speech here"),
        ]
        result = filter_hallucinations(segments, source_lang="en")

        self.assertEqual(_texts(result), ["one two three four", "real speech here"])
        self.assertTrue(segments[0].uncertain)

    def test_fast_speech_is_only_marked_never_deleted(self):
        from backend.core.hallucination_filter import filter_hallucinations

        seg = _seg(0, 0.0, 1.0, "one two three four five six seven eight nine ten")
        result = filter_hallucinations([seg], source_lang="en")

        self.assertEqual(len(result), 1, "语速异常只标记，不删除")
        self.assertTrue(seg.uncertain)


# ══════════════════════════════════════════════════════════════════
# A7 · 括号描述也只能标记（清掉残留一）
# ══════════════════════════════════════════════════════════════════

class TestA7BracketMarksOnly(unittest.TestCase):
    """`_RE_BRACKET` 把整条被括号包住的字幕整条删掉。

    它与 2026-09-02 的产品裁决「无障碍字幕保留」**字面冲突**——
    白名单里写着「括号描述可删」，产品裁决写着「无障碍标记要留」，
    而白名单是给未来的人看的。

    真实素材开火 **0 次**（模型吐的是裸词 `Music`，那条已随关键词分支删除）。
    「从来没开过火」不是留着它的理由，**是修它没有风险的理由**。
    """

    def test_music_annotation_survives(self):
        from backend.core.hallucination_filter import filter_hallucinations

        seg = _seg(0, 0.0, 4.0, "[Music]")
        result = filter_hallucinations([seg], source_lang="en")

        self.assertEqual(len(result), 1, "无障碍字幕保留（2026-09-02 产品裁决）")
        self.assertTrue(seg.uncertain)

    def test_bracket_description_survives_in_cjk(self):
        from backend.core.hallucination_filter import filter_hallucinations

        seg = _seg(0, 0.0, 4.0, "[背景音效]")
        result = filter_hallucinations([seg], source_lang="zh")

        self.assertEqual(len(result), 1)
        self.assertTrue(seg.uncertain)

    def test_pure_symbols_still_deleted(self):
        """反向守卫：纯符号仍然删——那是结构性不可能，不是无障碍标记。

        2026-09-04 换例子：`♪♪♪♪` 也交出了删除权（音符与括号描述同属
        无障碍标记，真实素材开火 22 次）。**改用纯标点，意图与断言未动。**
        音符的新行为由 `test_g12b_accessibility_markers.py` 守。
        """
        from backend.core.hallucination_filter import filter_hallucinations

        segments = [_seg(0, 0.0, 4.0, "......"), _seg(1, 10.0, 14.0, "real speech here")]
        result = filter_hallucinations(segments, source_lang="en")

        self.assertEqual(_texts(result), ["real speech here"])


# ══════════════════════════════════════════════════════════════════
# A8 · L1 的两条丢弃分支也只能标记（清掉残留二）
# ══════════════════════════════════════════════════════════════════

class TestA8Layer1MarksOnly(unittest.TestCase):
    """**这是最后一处「单一弱证据持有删除权」。**

    `layer1_sv_only_nonhuman` 干的是：对齐器不可用时，**只凭一个 SenseVoice
    声音标签就删掉整条字幕**。P445 原文点名过这个形状，本批段一记为残留。

    留着它，「单一弱证据没有删除权」这句话在代码里就是假的。
    **一条原则留着一个例外，那不叫原则，叫口号。**

    真实素材开火 0 次（日志里只有 `L1 SV误判清除` 1804 次、
    `L1 疑似幻觉 [?]` 18 次、`L1 短词豁免` 6 次，全是标记或清除，无一次丢弃）。
    """

    def test_single_signal_nonhuman_is_marked_not_deleted(self):
        """对齐器不可用 + 声音标签说非人声 → 只标记。"""
        from backend.core.hallucination_filter import filter_hallucinations

        seg = _seg(0, 0.0, 4.0, "some text under music")
        seg.sensevoice_tag = "BGM"
        result = filter_hallucinations([seg], source_lang="en")

        self.assertEqual(len(result), 1, "单一声音标签不得拥有删除权")
        self.assertTrue(seg.uncertain)

    def test_dual_confirm_nonhuman_is_marked_not_deleted(self):
        """有对齐数据但本段没有对齐词 + 声音标签非人声 → 仍只标记。

        原实现这里对 CJK 已经只标记，对其他语言却删除——同一件事两套行为。
        """
        from backend.core.hallucination_filter import filter_hallucinations

        seg = _seg(0, 100.0, 104.0, "applause sound here")
        seg.sensevoice_tag = "Applause"
        alignment = [{"text": "elsewhere", "start": 900.0, "end": 900.4}]
        result = filter_hallucinations([seg], source_lang="en",
                                       aligner_result=alignment)

        self.assertEqual(len(result), 1)
        self.assertTrue(seg.uncertain)

    def test_speech_tag_with_alignment_is_untouched(self):
        """反向守卫：正常人声段不许被标记，否则存疑比例会虚高。"""
        from backend.core.hallucination_filter import filter_hallucinations

        seg = _seg(0, 10.0, 14.0, "this is normal speech")
        seg.sensevoice_tag = "Speech"
        seg.words = [{"text": "this", "start": 10.0, "end": 10.5}]
        alignment = [{"text": "this", "start": 10.0, "end": 10.5}]
        result = filter_hallucinations([seg], source_lang="en",
                                       aligner_result=alignment)

        self.assertEqual(len(result), 1)
        self.assertFalse(seg.uncertain)

    def test_no_branch_in_layer1_sets_filtered(self):
        """直接盯住 L1 本身：它的任何分支都不许置 filtered。"""
        import inspect
        from backend.core import hallucination_filter

        source = inspect.getsource(hallucination_filter._layer1_dual_signal)
        self.assertNotIn("seg.filtered = True", source,
                         "L1 仍有分支持有删除权——单一弱证据原则在代码里就是假的")


# ══════════════════════════════════════════════════════════════════
# R5 · 删除权白名单穷举
# ══════════════════════════════════════════════════════════════════

class TestR5DeletionWhitelistExhaustive(unittest.TestCase):
    """段一之后，能被直接删除的只有这四类；第五类由段三追加。

    任何不属于白名单的输入都必须存活——这条测试是白名单的守门员。
    """

    def test_whitelisted_classes_are_deleted(self):
        """白名单必须**可穷举**，否则「只此几类」是空话。

        本条覆盖模块 docstring 表格里的**段级两类**。第 3 类（≥3 同词落在同一瞬间）
        是词级判据，由 `test_g12b_evidence_preservation.py` 守着。

        ⚠️ **收窄轨迹**：8 类 →（闸门 2 真跑）5 类 →（收尾）4 类
        →（2026-09-04 读完真实内容）**段级 2 类 + 词级 1 类**。
        最后一刀砍掉的是 L3 的两条时长分支——26 次开火从没记过删了什么，
        补日志重跑后五次开火五次删真话。

        **段级只剩两条：一条看有没有字，一条看是不是符号。**
        白名单里最后一个阈值（`dur < 0.1` 的 `0.1` 与 `2`）也随之消失。
        """
        from backend.core.hallucination_filter import filter_hallucinations

        segments = [
            _seg(0, 1.0, 3.0, "   "),                      # 1 空文字
            _seg(1, 4.0, 6.0, "..."),                      # 2 纯符号（非无障碍标记）
            _seg(2, 30.0, 33.0, "this is real speech"),    # 应存活
        ]
        result = filter_hallucinations(segments, source_lang="en")

        self.assertEqual(_texts(result), ["this is real speech"],
                         "白名单内的两类都必须被删；多一条少一条都是缺口")

    def test_duration_classes_are_no_longer_deletable(self):
        """反向守卫：时长异常**不在**白名单里了。

        这一条红了，说明有人把时长删除权加回来——而它删的是
        `'We apologize for that.'` 这种真话。
        """
        from backend.core.hallucination_filter import filter_hallucinations

        segments = [
            _seg(0, 22.00, 22.05, "one two three four"),   # 旧第 3 类
            _seg(1, 23.0, 23.0, "zero duration"),          # 旧第 4 类
        ]
        result = filter_hallucinations(segments, source_lang="en")

        self.assertEqual(len(result), 2, "时长异常已交出删除权，两条都得活着")
        self.assertTrue(all(s.uncertain for s in result), "但要标存疑")

    def test_cjk_repetition_is_marked_not_deleted(self):
        """CJK 重复只标记不删除。

        原来这条靠 `_mark_cjk_repetition_uncertain` 的 CJK 专用豁免实现；
        闸门 2 真跑之后三条重复分支**对所有语言都只标记**，豁免函数随之删除
        （它成了本次改动制造的孤儿）。行为不变，路径变简单了。
        """
        from backend.core.hallucination_filter import filter_hallucinations

        seg = _seg(0, 10.0, 14.0, "哈哈哈哈哈哈")
        result = filter_hallucinations([seg], source_lang="zh")

        self.assertEqual(len(result), 1, "CJK 重复保留为疑似，不删")
        self.assertTrue(seg.uncertain)

    def test_non_whitelisted_content_always_survives(self):
        """这些都曾经或可能被删——收权后一条都不许少。"""
        from backend.core.hallucination_filter import filter_hallucinations

        cases = [
            "I love music",                     # 含关键词的正常英语
            "私たちは、音楽を聴きます。",          # 含关键词的正常日语
            "Music",                            # 裸音乐标记（产品裁决保留，真实形状）
            "♪ Tumbleweed shadow uptight ♪",    # 歌词（产品裁决保留，真实形状）
            "谢谢观看我的视频，下次再见",           # 含「谢谢观看」的正常中文
            "Canada.",                          # 单条重复词
        ]
        segments = [_seg(i, i * 5.0, i * 5.0 + 4.0, t) for i, t in enumerate(cases)]
        result = filter_hallucinations(segments, source_lang="auto")

        self.assertEqual(_texts(result), cases,
                         "白名单之外的内容被删除；弱证据没有删除权")


# ══════════════════════════════════════════════════════════════════
# 收尾：两条残留已解决，一条已裁决为选择
# ══════════════════════════════════════════════════════════════════
#
# **残留一（括号描述）已解决** —— `TestA7BracketMarksOnly`。
#   它与产品裁决「无障碍字幕保留」字面冲突，改为只标记。
#
# **残留二（L1 两条丢弃分支）已解决** —— `TestA8Layer1MarksOnly`。
#   最后一处「单一弱证据持有删除权」。留着它，那句原则在代码里就是假的。
#   `test_no_branch_in_layer1_sets_filtered` 直接读源码盯住这件事。
#
# **明确接受的选择（不是欠账）**：单条成对的片尾幻觉删不掉，
#   例如 `'Thank you for watching Thank you for watching'`。
#   区分它和 `'Canada. Canada.'` 只能靠内容或阈值——前者是本批以 12:0
#   废掉的关键词匹配，后者是本批一直拒绝引入的东西。
#   这种形状在 12 个真实任务里**从未出现过**。
#   **重开条件**：日志里真的出现，且用户报告它进了成品。
#   ⚠️ 不要把它写成待办——写成待办，它会在问题库的两百多条里烂掉，
#   然后某天有人翻出来，重新给这个项目装回一条会删真话的判据。

if __name__ == "__main__":
    unittest.main()
