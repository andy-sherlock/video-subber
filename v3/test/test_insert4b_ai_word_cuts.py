"""插入④ 施工 B：字幕换屏时间对不上（P541 + P538 + P542 显示层）。

方案：`pipelines/方案-时间错位-AI报前后词-第四版-2026-10-26.md`；合同 C1–C18 见
`pipelines/bug-pipeline-2026-10-26-时间错位施工.md` ⑤.5。AI 全用替身，不联网。
"""

import re

from backend.core import display_timeline as dt
from backend.core import sentence_splitter as ss
from backend.transcriber_v2 import Segment
from backend.translation.base import TranslationResult

M = dt.SPLIT_MARK


def _w(word, start, end):
    return {"word": word, "start": start, "end": end}


def _seg(i, zh, words, text=None):
    s = Segment(id=i, start=words[0]["start"], end=words[-1]["end"],
                text=text if text is not None else " ".join(w["word"] for w in words))
    s.zh = zh
    s.words = list(words)
    return s


class _Backend:
    """按调用顺序回答事先写好的回答；记下每次的提示词和 max_tokens。"""
    name = "stub"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts, self.max_tokens = [], []

    def translate_raw(self, prompt, max_tokens=4096):
        self.prompts.append(prompt)
        self.max_tokens.append(max_tokens)
        raw = self.answers.pop(0) if self.answers else ""
        return TranslationResult(translated=[raw], backend_name=self.name,
                                 tokens_used={"output_tokens": 100})


def _split(segs, backend):
    return ss.llm_split_segments(segs, llm_backend=backend, on_wait=lambda *a: None)


# 新西兰片形状的长句（自编）：两段，刀在 this ｜ your 之间
ZH = "我们很乐意说明一下这件事情，您预计的出发时间是十点二十五分"
WORDS = [_w("we", 0.0, 0.3), _w("are", 0.3, 0.5), _w("happy", 0.5, 0.9), _w("to", 0.9, 1.0),
         _w("explain", 1.0, 1.5), _w("this,", 1.5, 1.9), _w("your", 2.4, 2.6),
         _w("departure", 2.6, 3.1), _w("is", 3.1, 3.2), _w("ten", 3.2, 3.5),
         _w("twenty-five.", 3.5, 4.2)]


# ── C3 D3：回答上限 8192，每批写死 80 ──

def test_c3_split_limit_8192_and_batch_fixed_at_80():
    assert ss._SPLIT_MAX_TOKENS == 8192
    assert ss._SPLIT_BATCH == 80
    b = _Backend("")
    _split([_seg(0, ZH, WORDS)], b)
    assert b.max_tokens == [8192]


# ── C11 D14：识别时间标错的词，一个函数 ──

def test_c11_bad_word_time_is_zero_length_and_far_from_previous():
    ws = [_w("Bob's", 10.0, 10.3), _w("Cove.", 20.16, 20.16), _w("ok", 20.2, 20.5)]
    assert dt._word_time_bad(ws, 1)                 # 零长度、离前一词 9.86 秒
    assert not dt._word_time_bad(ws, 0)             # 第一个词不算
    assert not dt._word_time_bad(ws, 2)             # 有长度
    near = [_w("a", 0.0, 0.5), _w("b", 0.9, 0.9)]   # 零长度但只隔 0.4 秒：大多正常
    assert not dt._word_time_bad(near, 1)


# ── C9 D11：不到 16 字、但有 ≥1 秒停顿的短句也送 AI ──

def test_c9_short_sentence_with_one_second_pause_is_sent():
    ws = [_w("well.", 0.0, 0.4), _w("I", 1.6, 1.7), _w("got", 1.7, 2.0)]
    assert ss._is_long_zh_segment(_seg(0, "好吧我拿到了", ws))


def test_c9_short_sentence_with_shorter_pause_is_not_sent():
    ws = [_w("well.", 0.0, 0.4), _w("I", 1.3, 1.4), _w("got", 1.4, 1.7)]
    assert not ss._is_long_zh_segment(_seg(0, "好吧我拿到了", ws))


def test_c9_pause_made_by_bad_word_time_does_not_count():
    ws = [_w("Bob's", 0.0, 0.3), _w("Cove.", 10.16, 10.16)]
    assert not ss._is_long_zh_segment(_seg(0, "鲍勃湾", ws))


# ── C10 D13：送 AI 的英文里标出停顿 ──

def test_c10_pause_mark_before_word_after_half_second_gap():
    line = ss._english_with_pauses(_seg(0, ZH, WORDS))
    assert line == "we are happy to explain this, ⏸0.5秒 your departure is ten twenty-five."


def test_c10_pause_mark_follows_refined_wording():
    ws = [_w("um", 0.0, 0.2), _w("so", 0.2, 0.4), _w("we", 0.4, 0.6), _w("go", 1.6, 1.8)]
    line = ss._english_with_pauses(_seg(0, "我们走", ws, text="So we go."))
    assert line == "So we ⏸1.0秒 go."


def test_c10_no_mark_for_short_gap_or_bad_word():
    ws = [_w("a", 0.0, 0.2), _w("b", 0.5, 0.6), _w("c", 9.0, 9.0)]
    assert ss._english_with_pauses(_seg(0, "甲乙丙", ws)) == "a b c"


def test_c10_prompt_carries_pause_marks():
    b = _Backend("")
    _split([_seg(0, ZH, WORDS)], b)
    assert "英文：we are happy to explain this, ⏸0.5秒 your departure" in b.prompts[0]


# ── C2 D2+D12：新格式「段 ‖ 刀前词 ｜ 刀后词」 ──

def test_c2_prompt_has_word_rules_and_period_example():
    b = _Backend("")
    _split([_seg(0, ZH, WORDS)], b)
    p = b.prompts[0]
    assert "‖ 刀前词 ｜ 刀后词" in p
    assert "thing、it、that" in p and "but、and、so、my" in p      # D12 两句说明
    assert "⏸" in p and "停顿超过 1 秒" in p                        # D13
    assert "不要输出英文" not in p
    assert "我们先去了海边。天气特别好 ‖ nice ｜ Then" in p          # 段里有句号的例子


def test_c2_answer_words_are_kept_on_segment():
    b = _Backend("[0.1] 我们很乐意说明一下这件事情， ‖ this ｜ your\n[0.2] 您预计的出发时间是十点二十五分")
    out = _split([_seg(0, ZH, WORDS)], b)
    assert len(out) == 1 and out[0].zh.count(M) == 1
    assert list(out[0].ai_cut_words.values()) == [("this", "your")]


def test_c2_missing_words_are_recorded_as_none():
    b = _Backend("[0.1] 我们很乐意说明一下这件事情，\n[0.2] 您预计的出发时间是十点二十五分")
    out = _split([_seg(0, ZH, WORDS)], b)
    assert out[0].zh.count(M) == 1
    assert list(out[0].ai_cut_words.values()) == [None]


def test_c2_words_on_last_part_are_ignored():
    b = _Backend("[0.1] 我们很乐意说明一下这件事情 ‖ this ｜ your\n[0.2] 您预计的出发时间是十点二十五分 ‖ x ｜ y")
    out = _split([_seg(0, ZH, WORDS)], b)
    assert list(out[0].ai_cut_words.values()) == [("this", "your")]


# ── C17 R1：词对按刀所在位置记，只这一份 ──

def test_c17_cut_words_keyed_by_position_of_mark():
    b = _Backend("[0.1] 我们很乐意说明一下这件事情， ‖ this ｜ your\n[0.2] 您预计的出发时间是十点二十五分")
    seg = _split([_seg(0, ZH, WORDS)], b)[0]
    head = seg.zh[:seg.zh.index(M)]
    assert seg.ai_cut_words == {dt._cut_key(head): ("this", "your")}


def test_c17_segment_has_no_cut_words_by_default():
    assert Segment(id=0, start=0.0, end=1.0, text="x").ai_cut_words is None


def test_c17_rejected_answer_leaves_no_cut_words():
    b = _Backend("[0.1] 完全不同的文字\n[0.2] 也不一样")
    seg = _split([_seg(0, ZH, WORDS)], b)[0]
    assert M not in seg.zh and seg.ai_cut_words is None


# ── C5 D5+D7：每份片子最多回问一次（超 18 字段再切 + 漏写词的刀补词） ──

LONG = "我们今天早上很早就起床然后开车去了很远的海边看日出，天气特别好"   # 第一段 24 字
LONG_WORDS = [_w(x, i * 0.4, i * 0.4 + 0.35) for i, x in enumerate(
    "we got up early this morning and drove far to the beach for the sunrise the weather was great".split())]
FIRST = "[0.1] 我们今天早上很早就起床然后开车去了很远的海边看日出，\n[0.2] 天气特别好"   # 这一刀漏写词
REASK = "[0.1] 我们今天早上很早就起床 ‖ morning ｜ and\n[0.2] 然后开车去了很远的海边看日出\n[补0] sunrise ｜ the"


def test_c5_one_reask_carries_both_parts():
    b = _Backend(FIRST, REASK)
    _split([_seg(0, LONG, LONG_WORDS)], b)
    assert len(b.prompts) == 2
    assert "第一部分" in b.prompts[1] and "第二部分" in b.prompts[1]
    assert "[补0] 我们今天早上很早就起床然后开车去了很远的海边看日出， ‖ 天气特别好" in b.prompts[1]
    assert b.max_tokens[1] == 8192


def test_c5_reask_resplits_long_part_and_fills_missing_words():
    seg = _split([_seg(0, LONG, LONG_WORDS)], _Backend(FIRST, REASK))[0]
    pieces = seg.zh.split(M)
    assert pieces == ["我们今天早上很早就起床", "然后开车去了很远的海边看日出，", "天气特别好"]
    keys = [dt._cut_key(M.join(pieces[:k + 1])) for k in range(2)]
    assert seg.ai_cut_words == {keys[0]: ("morning", "and"), keys[1]: ("sunrise", "the")}


def test_c5_no_reask_when_nothing_needs_it():
    b = _Backend("[0.1] 我们很乐意说明一下这件事情， ‖ this ｜ your\n[0.2] 您预计的出发时间是十点二十五分")
    _split([_seg(0, ZH, WORDS)], b)
    assert len(b.prompts) == 1


def test_c5_failed_reask_keeps_first_pass():
    seg = _split([_seg(0, LONG, LONG_WORDS)], _Backend(FIRST, ""))[0]
    assert seg.zh.split(M) == ["我们今天早上很早就起床然后开车去了很远的海边看日出，", "天气特别好"]
    assert list(seg.ai_cut_words.values()) == [None]


def test_c5_reask_that_changes_text_is_not_used():
    bad = "[0.1] 我们今天很早起床 ‖ morning ｜ and\n[0.2] 然后去海边"
    seg = _split([_seg(0, LONG, LONG_WORDS)], _Backend(FIRST, bad))[0]
    assert seg.zh.count(M) == 1


# ── C18 R8：记账 ──

def test_c18_log_counts_reask(caplog):
    caplog.set_level("INFO", logger=ss.__name__)
    _split([_seg(0, LONG, LONG_WORDS)], _Backend(FIRST, REASK))
    text = caplog.text
    assert "AI 拆句回问：超 18 字再切 1 段，采用 1 段；漏写词 1 刀，补到 1 刀" in text


# ══ 排版这一侧 ══════════════════════════════════════════════════

def _aiseg(i, parts, words, pairs, text=None, start=None, end=None):
    """一条 AI 拆过的句子：中文带记号，每刀词对按位置挂好（和拆句产出的一样）。"""
    s = _seg(i, M.join(parts), words, text)
    if start is not None:
        s.start = start
    if end is not None:
        s.end = end
    s.ai_cut_words = {dt._cut_key(M.join(parts[:k + 1])): pair for k, pair in enumerate(pairs)}
    return s


def _times(entries):
    return [(round(e.start, 2), round(e.end, 2), e.text) for e in entries]


PARTS = ["我们很乐意说明一下这件事情，", "您预计的出发时间是十点二十五分"]


# ── C6 A2+D6：在原始识别词里找挨着的两个词；找不到到润色英文里找、换回原词 ──

def test_c6_cut_lands_on_word_after_cut():
    out = dt.build_display_timeline([_aiseg(0, PARTS, WORDS, [("this", "your")])])
    assert _times(out) == [(0.0, 1.9, "我们很乐意说明一下这件事情"), (2.4, 4.2, "您预计的出发时间是十点二十五分")]


def test_c6_refined_words_are_mapped_back_to_raw_words():
    ws = [_w("I", 0.0, 0.2), _w("wanna", 0.2, 0.6), _w("go", 1.5, 1.8), _w("home.", 1.8, 2.6)]
    j = dt._ai_cut_word_index(ws, "I want to go home.", ("to", "go"))
    assert j == (2, "润色换回")


def test_c6_inner_dash_words_are_compared_apart():
    ws = [_w("two", 0.0, 0.3), _w("s'more—chocolate", 0.3, 1.5), _w("things.", 1.5, 2.0)]
    seg = dt._layout_segments([_seg(0, "x", ws)], polish_marks=False)[0]
    j, src = dt._ai_cut_word_index(seg.words, "two s'more—chocolate things.", ("s'more", "chocolate"))
    assert src == "原词" and dt._w_text(seg.words[j]).startswith("chocolate")


def test_c6_pair_found_twice_is_not_found():
    ws = [_w("so", 0.0, 0.2), _w("we", 0.2, 0.4), _w("so", 0.5, 0.7), _w("we", 0.7, 0.9)]
    assert dt._ai_cut_word_index(ws, "so we so we", ("so", "we")) == (None, "重复")


# ── C8 D10+R3：只猜没找到的那一刀；猜错整句退回（去记号） ──

THREE = ["我们很乐意说明一下", "这件事情，您预计的", "出发时间是十点二十五分"]


def test_c8_only_missing_cut_is_guessed_between_found_neighbours():
    out = dt.build_display_timeline([_aiseg(0, ["我们很乐意", "说明一下这件事情，", "您预计的出发时间是十点二十五分"],
                                            WORDS, [("happy", "to"), ("this", "your")])])
    rep = dt.TimelineReport()
    starts = [e.start for e in out]
    assert 0.9 in starts and 2.4 in starts        # 两把找到的刀都在原位
    out2 = dt.build_display_timeline([_aiseg(0, ["我们很乐意", "说明一下这件事情，", "您预计的出发时间是十点二十五分"],
                                             WORDS, [None, ("this", "your")])], report=rep)
    assert 2.4 in [e.start for e in out2]          # 找到的那刀留下
    # ⑦.5 审查补：C18 要求「没找到」也记账（没写词 / 找不到 / 重复 三种原因各记各的）
    assert rep.ai_cut_sources == {"原词": 1, "没写词": 1, "程序猜": 1}


def test_c18_cut_whose_words_are_not_in_english_is_counted():
    rep = dt.TimelineReport()
    dt.build_display_timeline([_aiseg(0, PARTS, WORDS, [("zebra", "lion")])], report=rep)
    assert rep.ai_cut_sources.get("找不到") == 1


def test_c8_inverted_cuts_fall_back_without_marks():
    rep = dt.TimelineReport()
    seg = _aiseg(0, ["我们很乐意说明一下这件事情，", "您预计的出发", "时间是十点二十五分"], WORDS,
                 [("this", "your"), ("happy", "to")])          # 第二刀在第一刀前面
    _e, _r, drafts, spans = dt._layout_pass_full([seg], dt._Rules())
    assert all(M not in d[2] for d in drafts) and spans == []
    dt.build_display_timeline([seg], report=rep)
    assert rep.ai_cut_sources.get("整句退回") == 1


def test_c8_all_found_means_no_guess():
    rep = dt.TimelineReport()
    dt.build_display_timeline([_aiseg(0, PARTS, WORDS, [("this", "your")])], report=rep)
    assert rep.ai_cut_sources == {"原词": 1}


# ── C16 R2：没有词证据的句子（预拆分）走现在的做法，记号保留 ──

def test_c16_precomputed_split_keeps_marks_and_old_path():
    seg = _seg(0, M.join(PARTS), WORDS)          # ai_cut_words 是 None
    rep = dt.TimelineReport()
    _e, _r, drafts, spans = dt._layout_pass_full([seg], dt._Rules())
    assert spans == [] and any(M in d[2] for d in drafts)
    dt.build_display_timeline([seg], report=rep)
    assert rep.ai_cut_sources == {}


# ── C7 D9：每一屏在最后一个英文词说完时结束（所有屏） ──

def test_c7_program_screen_ends_when_last_word_ends():
    ws = [_w("we", 0.0, 0.4), _w("went", 0.4, 1.0), _w("home", 1.0, 2.0), _w("today.", 2.0, 3.0)]
    seg = _seg(0, "我们今天回家了", ws)
    seg.end = 5.0
    nxt = _seg(1, "好的", [_w("ok.", 6.0, 7.0)])
    out = dt.build_display_timeline([seg, nxt])
    assert round(out[0].end, 2) == 3.0


def test_c7_bad_last_word_does_not_keep_screen_up():
    ws = [_w("to", 0.0, 0.5), _w("Bob's", 0.5, 2.6), _w("Cove.", 12.66, 12.66)]
    seg = _seg(0, "去了鲍勃湾", ws)
    out = dt.build_display_timeline([seg, _seg(1, "好的", [_w("ok.", 14.0, 15.0)])])
    assert round(out[0].end, 2) == 2.6


# ── C4 D4+U2：任何屏都不推后下一屏；第二轮合并保留 ──

def test_c4_push_functions_are_gone():
    for name in ("_borrow_reading_time", "_borrow_pushing_next", "_push_later",
                 "_can_borrow_from_next", "_do_borrow"):
        assert not hasattr(dt, name), name


def _rows(*rows):
    return [(s, e, t, False, [_w("x", s, e)]) for s, e, t in rows]


def test_c4_next_screen_is_not_pushed_later():
    rows = _rows((0.0, 0.5, "好的。"), (0.5, 7.4, "我们接下来要去很远的地方看看风景"))
    for rules in (dt._Rules(pref=False), dt._Rules(pref=True)):
        dt._RULES = rules
        out = dt._repair_times(rows, dt.TimelineReport())
        assert out[1][0] == 0.5
    dt._RULES = dt._Rules()


def test_c4_short_screen_vetoed_by_old_gate_is_merged_in_second_round():
    rows = _rows((0.0, 0.5, "我们到了机场。"), (0.5, 3.5, "接下来要去办理登机手续"))
    dt._RULES = dt._Rules(pref=True)
    out = dt._repair_times(rows, dt.TimelineReport())
    dt._RULES = dt._Rules()
    assert len(out) == 1 and out[0][0] == 0.0 and out[0][1] == 3.5


# ── C19（2026-10-27 合同增量，D4 例外）：为凑 0.84 秒，最多把下一屏推后 0.15 秒（E1 10-28 由 0.1 改）──

def _repair_both(rows):
    outs = []
    for rules in (dt._Rules(pref=False), dt._Rules(pref=True)):
        dt._RULES = rules
        outs.append(dt._repair_times([tuple(r) for r in rows], dt.TimelineReport()))
    dt._RULES = dt._Rules()
    return outs


def test_c19_short_screen_just_under_084_gets_its_own_screen():
    rows = _rows((0.0, 0.80, "谢谢。"), (0.80, 3.0, "天哪，你是怎么学英语的？"))
    for out in _repair_both(rows):
        assert [(round(r[0], 2), round(r[1], 2), r[2]) for r in out] == [
            (0.0, 0.84, "谢谢。"), (0.84, 3.0, "天哪，你是怎么学英语的？")]


def test_c19_e1_needs_012_second_gets_its_own_screen():
    # E1（2026-10-28，用户看 ⑩ 试排视频后定）：上限 0.1 → 0.15。英文 0.72 秒的短句（0.08 秒一格的 9 格）
    # 要推 0.12 秒——新西兰 19:42「挺酷的」就是这种
    rows = _rows((0.0, 0.72, "挺酷的。"), (0.72, 3.0, "你会以为这里只是个游客陷阱。"))
    for out in _repair_both(rows):
        assert [(round(r[0], 2), round(r[1], 2), r[2]) for r in out] == [
            (0.0, 0.84, "挺酷的。"), (0.84, 3.0, "你会以为这里只是个游客陷阱。")]


def test_c19_needs_more_than_015_second_so_still_merged():
    rows = _rows((0.0, 0.64, "谢谢。"), (0.64, 3.0, "天哪，你是怎么学英语的？"))
    for out in _repair_both(rows):
        assert len(out) == 1 and out[0][0] == 0.0


def test_c19_pushed_screen_does_not_push_the_next_one():
    # 同一轮里「推完下一屏仍 ≥0.84」已挡住连锁；跨两轮时靠 pushed 记号：
    # 第一轮被推过的屏，后来又变短了（重拆、封顶），也不再去推它后面的。
    fixed = [[0.0, 0.80, "好的。", False, []], [0.80, 3.0, "我们接下来要去机场办理登机手续", False, []]]
    dt._nudge_next_for_min(fixed, {id(fixed[0]): fixed[0]})
    assert fixed[1][0] == 0.80 and fixed[0][1] == 0.80
    fresh = [list(r) for r in fixed]
    pushed = {}
    dt._nudge_next_for_min(fresh, pushed)
    assert round(fresh[1][0], 2) == 0.84 and id(fresh[1]) in pushed


def test_c19_never_pushes_a_new_speaker():
    rows = [(0.0, 0.80, "谢谢。", False, [_w("x", 0.0, 0.80)]),
            (0.80, 3.0, "天哪，你是怎么学英语的？", True, [_w("x", 0.80, 3.0)])]
    for out in _repair_both(rows):
        assert any(abs(r[0] - 0.80) < 1e-9 for r in out)


def test_c19_not_when_next_screen_would_become_too_short():
    rows = _rows((0.0, 0.80, "谢谢。"), (0.80, 1.66, "天哪。"))
    for out in _repair_both(rows):
        assert all(abs(r[0] - 0.84) > 1e-9 for r in out)



def test_c19_half_sentence_is_not_pushed_out_on_its_own():
    # 第 6 条（用户 2026-10-27 看 A9 视频后定）：识别把一句从中间断开，「朋友」是半截句，
    # 不推后下一屏让它单独成屏，照旧并回前一屏
    rows = _rows((0.0, 1.9, "因为我们就像，我们是班里最好的"), (1.9, 2.70, "朋友"), (2.70, 4.5, "但她走了。"))
    for out in _repair_both(rows):
        assert all(r[2] != "朋友" for r in out)
        assert all(abs(r[0] - 2.74) > 1e-9 for r in out)


def test_c19_screen_after_an_unfinished_line_is_not_pushed_out():
    # 第 6 条后半（A9 真实情形）：「朋友。」结尾有句号（程序按英文补的），但前一屏「…最好的」话没说完，
    # 它不是完整一句，不推
    rows = _rows((0.0, 1.9, "因为我们就像，我们是班里最好的"), (1.9, 2.70, "朋友。"), (2.70, 4.5, "但她走了。"))
    for out in _repair_both(rows):
        assert all(r[2] != "朋友。" for r in out)
        assert all(abs(r[0] - 2.74) > 1e-9 for r in out)

# ── C12 D16：合并门槛 20、硬上限 25 ──

def _fixed(*rows):
    return [[s, e, t, False, [_w("x", s, e)]] for s, e, t in rows]


def test_c12_old_rule_window_kept_when_at_most_20():
    f = _fixed((0.0, 2.0, "今天我们一起去了海边，然后"), (2.0, 2.5, "看了日出。"), (2.5, 4.0, "天气特别好，我们"))
    assert dt._best_merge_window(f, 1) == (0, 1)


def test_c12_one_side_allowed_when_old_rule_over_20():
    f = _fixed((0.0, 2.0, "今天早上我们一大早就开车去了很远的海边，然后"), (2.0, 2.5, "看了日出。"),
               (2.5, 4.0, "天气特别好，我们"))
    assert dt._best_merge_window(f, 1) == (1, 2)


def test_c12_nothing_over_25():
    f = _fixed((0.0, 2.0, "今天早上我们一大早就一起开车去了非常非常远的海边，然后"), (2.0, 2.5, "看了日出。"),
               (2.5, 4.0, "天气特别好我们在海边待了很久很久一直待到了中午，"))
    assert dt._best_merge_window(f, 1) is None


# ── C1 D1+D17、C13 D18：碰到 AI 句的屏一行显示，不改拆、不重拆 ──

def test_c1_program_half_plus_ai_half_screen_is_not_split():
    prog = _seg(0, "接下来还有一些户外活动", [_w("there", 0.0, 0.2), _w("are", 0.2, 0.3), _w("some", 0.3, 0.4),
                                         _w("activities", 0.4, 0.5)], text="there are some activities")
    ws = [_w("but", 0.5, 0.8), _w("easy", 0.8, 1.6), _w("ones", 1.6, 2.4), _w("we", 2.6, 2.8),
          _w("talk", 2.8, 3.2), _w("tomorrow.", 3.2, 4.0)]
    ai = _aiseg(1, ["但是比较轻松的那种", "我们明天再说吧"], ws, [("ones", "we")])
    out = dt.build_display_timeline([prog, ai])
    assert ("接下来还有一些户外活动 但是比较轻松的那种" in [e.text for e in out])
    assert [round(e.start, 2) for e in out] == [0.0, 2.6]


def test_c13_ai_screen_keeps_space_only_where_punctuation_was():
    assert dt._one_line_screen("我们去了海边。然后去吃了午饭还挺好吃", False) == "我们去了海边 然后去吃了午饭还挺好吃"
    assert dt._one_line_screen("和住在这里的本地人聊了聊还挺酷的呀", False) == "和住在这里的本地人聊了聊还挺酷的呀"


# ── C14 U1：两趟同一套新规则 ──

def test_c14_baseline_and_final_pass_place_ai_cuts_the_same():
    seg = _aiseg(0, PARTS, WORDS, [("this", "your")])
    base, _ = dt._layout_pass([seg], dt._Rules(pref=False))
    final, _ = dt._layout_pass([seg], dt._Rules(pref=True))
    dt._RULES = dt._Rules()
    assert [(e.start, e.end) for e in base] == [(e.start, e.end) for e in final] == [(0.0, 1.9), (2.4, 4.2)]


# ── C15 U3：程序句合并后照旧重拆；碰到 AI 句的不重拆 ──

LONG3 = "我们今天去了很远很远的地方看了非常多的风景。然后我们又去吃了一顿非常非常好吃的晚饭"
LONG3_WORDS = [_w(x, i * 0.3, i * 0.3 + 0.25) for i, x in enumerate(
    "today we went somewhere really far away and saw lots of scenery. then we went and had a really great dinner".split())]


def test_c15_program_row_over_two_lines_is_resplit():
    out = dt._resplit_over_two_lines([[0.0, 6.0, LONG3, False, LONG3_WORDS]])
    assert len(out) == 2


def test_c15_row_touching_ai_sentence_is_not_resplit():
    out = dt._resplit_over_two_lines([[0.0, 6.0, LONG3, False, LONG3_WORDS]], [(3.0, 6.0)])
    assert len(out) == 1
