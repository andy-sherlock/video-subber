"""G12 第三批 B 收尾（P466 / P468 / P469 / P470 / P471 + 72 号字）。

唯一施工依据：`pipelines/P466-P468-P471-锁定方案-2026-09-16.md`。
每条测试的名字都对着锁定方案第 5 节的条款编号（A1–A9、B1–B3、D1）和
管道文件 `bug-pipeline-2026-09-16-G12-batch3B-收尾.md` 里的合同增量（#1–#4）。
"""
import logging
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core import display_timeline as dt
from backend.core import sentence_splitter as ss
from backend.transcriber_v2 import Segment
from backend.translation.base import TranslationResult


def _seg(i, start, end, text, zh, words=None, speaker_change=False):
    s = Segment(id=i, start=start, end=end, text=text)
    s.zh = zh
    s.speaker_change = speaker_change
    if words is None:
        toks = text.split() or ["w"]
        step = (end - start) / len(toks)
        words = [{"word": t, "start": start + k * step, "end": start + (k + 1) * step}
                 for k, t in enumerate(toks)]
    s.words = words
    return s


# ══════════════════════════════════════════════════════════════════
# D1（P470）：模型只删改了标点时，按模型每段字数在原文上切，正文一律取原文
# ══════════════════════════════════════════════════════════════════

class _ReplyBackend:
    """只回放一段固定回复的替身。不联网、不调任何模型。"""

    name = "reply-stub"

    def __init__(self, reply):
        self.reply = reply

    def translate_raw(self, prompt, max_tokens=4096):
        return TranslationResult(translated=[self.reply], backend_name=self.name)


def _split_with_reply(zh, reply, caplog=None):
    seg = _seg(0, 0.0, 6.0, "delirious no sleep who knows how long but rented a car", zh)
    if caplog is not None:
        caplog.set_level(logging.DEBUG, logger="backend.core.sentence_splitter")
    out = ss._llm_batch_split([seg], _ReplyBackend(reply), on_wait=lambda *a: None)
    assert len(out) == 1, "每条输入句子对应一组输出"
    return seg, out[0]



def _without_jieba(monkeypatch):
    """G12 第三批 B（排版 §14.3）：分词工具不可用 → 不在两个汉字之间换行（等于原来的换行）。"""
    for cache in (dt._jieba_bounds, dt._jieba_tokens, dt._pos_tags):
        cache.cache_clear()
    monkeypatch.setattr(dt, "_jieba", lambda: None)

def test_d1_deleted_comma_is_accepted_with_original_text(caplog):
    """锁定方案 D1 必测例：模型在断开处删了逗号 → 采用断点，正文仍是原文。

    新西兰同一视频、同样的提示，8 月拒 4/97 句，9 月拒 91/97 句，全是这个形状。"""
    zh = "神志不清，没睡觉，不知道多久了，但租了辆车"
    seg, parts = _split_with_reply(
        zh, "[0.1] 神志不清，没睡觉，不知道多久了\n[0.2] 但租了辆车", caplog)

    assert len(parts) == 1
    out = parts[0]
    # G12-B 收尾补充（P475）：断开处记内部记号，不再插空格——空格会留在观众看到的字幕里。
    assert out.zh == "神志不清，没睡觉，不知道多久了，" + dt.SPLIT_MARK + "但租了辆车", \
        f"断点没有被采用，或正文不是原文：{out.zh!r}"
    assert out.zh.replace(dt.SPLIT_MARK, "") == zh, "正文必须逐字等于原文（只多断开处一个内部记号）"
    assert "text_changed" not in caplog.text


def test_d1_changed_character_is_rejected(caplog):
    """D1：模型改了字（不只是标点）→ 照旧整句拒绝，退回不拆。"""
    zh = "神志不清，没睡觉，不知道多久了，但租了辆车"
    seg, parts = _split_with_reply(
        zh, "[0.1] 神志不清，没睡觉，不知道多久了\n[0.2] 但租了一辆车", caplog)

    assert parts == [seg], "改了字的回复不许被采用"
    assert seg.zh == zh
    assert "kind=text_changed" in caplog.text


def test_d1_closing_punct_stays_left_and_opening_punct_goes_right():
    """D1：收尾标点（，。”等）留在前一段，开头标点（“等）跟着后一段。"""
    zh = "我们到了之后，“这里真漂亮”她说，然后我们就进去了"
    seg, parts = _split_with_reply(
        zh, "[0.1] 我们到了之后\n[0.2] 这里真漂亮她说\n[0.3] 然后我们就进去了")

    m = dt.SPLIT_MARK
    assert parts[0].zh == "我们到了之后，" + m + "“这里真漂亮”她说，" + m + "然后我们就进去了", parts[0].zh


def test_d1_other_rejections_unchanged(caplog):
    """D1 只改“正文不一致”这一处；段数太少仍然拒绝。"""
    zh = "神志不清，没睡觉，不知道多久了，但租了辆车"
    seg, parts = _split_with_reply(zh, "[0.1] 神志不清没睡觉不知道多久了但租了辆车", caplog)

    assert parts == [seg]
    assert "kind=too_few_parts" in caplog.text


# ══════════════════════════════════════════════════════════════════
# B1 / B2 / B3 / 合同增量 #4：72 号字，所有烧录走同一个写出器
# ══════════════════════════════════════════════════════════════════

_ROOT = Path(__file__).resolve().parent.parent
_ASS_STYLE_FIELDS = ("Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
                     "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
                     "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, "
                     "Encoding").split(", ")


def _style_of(ass_path):
    line = next(l for l in Path(ass_path).read_text(encoding="utf-8").splitlines()
                if l.startswith("Style: "))
    return dict(zip(_ASS_STYLE_FIELDS, line[len("Style: "):].split(",")))


def _two_entries():
    from backend.core.display_timeline import build_display_timeline
    return build_display_timeline([
        _seg(0, 0.0, 2.0, "hello there.", "你好。"),
        _seg(1, 2.0, 4.0, "good bye.", "再见。"),
    ])


def test_b1_default_size_is_72_in_all_four_places(tmp_path):
    """B1：字号默认值四处一致 = 72（前端下拉框、前端 State、配置默认、样式写出器默认）。"""
    from backend.config_manager import DEFAULT_CONFIG
    from backend.subtitle_style import build_ass_styled

    html = (_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    select = re.search(r'<select id="styleSize">(.*?)</select>', html).group(1)
    assert re.findall(r'<option value="(\d+)" selected>', select) == ["72"], \
        f"字号下拉框应当有 72 且只默认选中它：{select}"
    state_style = re.search(r"style:\{font:\"PingFang SC\",size:(\d+)", html)
    assert state_style and state_style.group(1) == "72", "前端 State 默认字号不是 72"

    assert DEFAULT_CONFIG["style"]["size"] == 72

    ass = tmp_path / "a.ass"
    build_ass_styled(_two_entries(), str(ass), style=None)
    assert _style_of(ass)["Fontsize"] == "72"
    build_ass_styled(_two_entries(), str(ass), style={})
    assert _style_of(ass)["Fontsize"] == "72"


def test_b1_styled_ass_margins_are_96_96_46(tmp_path):
    """B1：左右边距 96、底边距 46，只在样式写出器里定义一处。"""
    from backend.subtitle_style import build_ass_styled

    ass = tmp_path / "a.ass"
    build_ass_styled(_two_entries(), str(ass), style={"size": 42})
    style = _style_of(ass)
    assert (style["MarginL"], style["MarginR"], style["MarginV"]) == ("96", "96", "46")
    assert style["Fontsize"] == "42", "用户选的字号照样生效"


def test_b2_default_font_follows_platform_constant(tmp_path, monkeypatch):
    """合同增量 #4：B2 让无样式烧录改走样式写出器；默认字体必须沿用
    `subtitler._DEFAULT_FONT`（Windows 是微软雅黑），不能写死 PingFang SC。"""
    from backend.core import subtitler
    from backend.subtitle_style import build_ass_styled

    monkeypatch.setattr(subtitler, "_DEFAULT_FONT", "Microsoft YaHei")
    ass = tmp_path / "a.ass"
    build_ass_styled(_two_entries(), str(ass), style=None)
    assert _style_of(ass)["Fontname"] == "Microsoft YaHei"


def _run_step_subtitle(tmp_path, *, style, subtitles):
    from backend import worker
    segs = [
        _seg(0, 0.0, 2.0, "hello there.", "你好，朋友。"),
        _seg(1, 2.0, 4.0, "good bye.", "再见。"),
    ]
    srt, ass = tmp_path / "a.srt", tmp_path / "a.ass"
    worker._step_subtitle(segs, [], {}, str(srt), str(ass), True, subtitles, style, 1920, 1080)
    return srt, ass


def test_b2_step_subtitle_without_style_writes_72pt_ass(tmp_path):
    """B2：任务没带样式时，烧录也走 `build_ass_styled`，用 B1 的默认值（以前会换成 20 号字）。"""
    srt, ass = _run_step_subtitle(tmp_path, style=None, subtitles="no")
    style = _style_of(ass)
    assert style["Fontsize"] == "72", f"无样式烧录没有走样式写出器：{style}"
    assert (style["MarginL"], style["MarginR"], style["MarginV"]) == ("96", "96", "46")
    assert style["Alignment"] == "2"


def test_b3_original_subtitles_keep_top_alignment(tmp_path):
    """B3：原视频自带字幕时，中文仍然放在画面顶部（alignment=8）。"""
    srt, ass = _run_step_subtitle(tmp_path, style=None, subtitles="yes")
    assert _style_of(ass)["Alignment"] == "8"


# ══════════════════════════════════════════════════════════════════
# 显示层（display_timeline）：A1–A9 与合同增量 #1–#3
# ══════════════════════════════════════════════════════════════════


def _texts(segs, report=None):
    return [e.text for e in dt.build_display_timeline(segs, report=report)]


def _words(pairs):
    return [{"word": w, "start": a, "end": b} for w, a, b in pairs]


# ── A4（P468）：补救用尽仍偏短的屏照常交付并记账，写出检查不再整份拒绝 ──

def test_a4_short_positive_entry_is_written_and_reported(tmp_path):
    """① 复现 R2：0.5 秒“哦。”后面紧跟换人，延不长、合不了、借不到。以前整份字幕被拒。"""
    from backend.core.subtitler import build_srt
    from backend.subtitle_style import build_ass_styled

    segs = [_seg(0, 0.0, 0.5, "Oh.", "哦。"),
            _seg(1, 0.5, 2.0, "How are you?", "你好吗？", speaker_change=True)]
    rep = dt.TimelineReport()
    entries = dt.build_display_timeline(segs, report=rep)

    assert [e.text for e in entries] == ["哦", "-你好吗？"]
    assert entries[0].end - entries[0].start == pytest.approx(0.5)
    assert len(rep.below_min_display) == 1, "偏短屏必须照旧记账"
    build_srt(entries, str(tmp_path / "a.srt"))
    build_ass_styled(entries, str(tmp_path / "a.ass"), style=None)


# ── A7（P471）：标点只在最后渲染一次 ─────────────────────────────

def test_a7_37s_example():
    """锁定方案 A7 必测例（30 分钟 37 秒）。"""
    assert dt._decorate("嗯，有点复杂，但基本上，我妈很多年前嫁给我爸，", False) == \
        "嗯 有点复杂 但基本上\n我妈很多年前嫁给我爸"


def test_a7_samara_example():
    assert dt._decorate("萨玛拉，你的中间名是什么？", False) == "萨玛拉 你的中间名是什么？"


def test_a7_decimal_and_english_word_untouched():
    assert dt._decorate("我买了3.5磅的Trader Joe's咖啡。", False) == "我买了3.5磅的Trader Joe's咖啡"


def test_a7_halfwidth_comma_and_period_after_han_are_rendered():
    assert dt._decorate("好的,我们走吧.", False) == "好的 我们走吧"


def test_a7_halfwidth_question_and_exclamation_after_han_become_fullwidth():
    assert dt._decorate("真的吗?", False) == "真的吗？"
    assert dt._decorate("太好了!", False) == "太好了！"


def test_a7_trailing_enumeration_comma_removed():
    assert dt._decorate("苹果、香蕉、", False) == "苹果、香蕉"


def test_a7_ellipsis_dash_colon_and_quotes_are_kept():
    assert dt._decorate("我觉得……其实——算了。", False) == "我觉得……其实——算了"
    assert dt._decorate("他说：“走吧”", False) == "他说：“走吧”"


def test_a7_prefix_is_hyphen_without_space():
    assert dt._decorate("你好吗？", True) == "-你好吗？"


def test_a7_period_before_closing_quote_moves_space_outside():
    """合同增量 #2：真实数据新西兰 462.46 秒。字面规则会变成“行啊 ”然后”。"""
    assert dt._decorate("他说：“行啊。”然后他把狗抱起来", False) == "他说：“行啊” 然后他把狗抱起来"
    assert dt._decorate("他说：“行啊。”", False) == "他说：“行啊”"


# ── A8：计数口径 ─────────────────────────────────────────────────

def test_a8_quotes_and_middle_dot_count_as_one_unit():
    assert dt._reading_units("“”‘’·") == 5.0
    assert dt._reading_units("—…") == 2.0
    assert dt._reading_units("中a 1\n") == 2.5, "半角字母、空格、数字各 0.5，换行不算"


# ── A6：阅读时间按渲染后的文字算（含前缀） ───────────────────────

def test_a6_reading_need_uses_rendered_text():
    with_commas = [0.0, 1.0, "嗯，有点复杂，但基本上，", False, []]
    rendered = [0.0, 1.0, "嗯 有点复杂 但基本上", False, []]
    assert dt._reading_need_s(with_commas) == pytest.approx(dt._reading_need_s(rendered))
    assert dt._reading_need_s(rendered) == pytest.approx(10.0 / 9 + 0.01)
    assert dt._reading_need_s([0.0, 1.0, "你好吗？", True, []]) == pytest.approx(4.5 / 9 + 0.01), \
        "前缀“-”算 0.5 单位"


# ── A5 与合同增量 #3：换行 ────────────────────────────────────────

def test_a5_one_line_when_rendered_within_16_units():
    """带标点是 17 单位，渲染后 15.5 单位——按渲染后的文字判断，一行。"""
    assert dt._decorate("咱们去机场吧，好吗？太久没穿鞋了。", False) == "咱们去机场吧 好吗？太久没穿鞋了"


def test_a5_prefers_top_line_not_longer_than_bottom():
    """同一级断点里优先上短下长（以前是挑两行最均衡，会选成上长下短）。"""
    assert dt._decorate("我们今天去，一个很远的地方，然后吃了饭再回家", False) == \
        "我们今天去\n一个很远的地方 然后吃了饭再回家"


def test_a5_avoids_one_or_two_unit_top_line():
    """上一行不能只有一两个字——哪怕只剩上长下短的断点可选。"""
    assert dt._decorate("好的，我觉得这里真漂亮，我们下次还来吧", False) == \
        "好的 我觉得这里真漂亮\n我们下次还来吧"


def test_a5_sentence_end_tier_beats_comma_tier():
    """合同增量 #3：句末断点和逗号断点分两级，不许为了上短下长把下一句开头挂到上一行。"""
    assert dt._decorate("我觉得这个很好。但是，如果你想要的话我们也可以", False) == \
        "我觉得这个很好\n但是 如果你想要的话我们也可以"


def test_a5_never_hard_breaks_between_two_han_characters(monkeypatch):
    # G12 第三批 B：排版 §12 起允许在分词词缝上断两个汉字；分词工具不可用时退回原规则（§14.3）
    _without_jieba(monkeypatch)
    assert dt._decorate("一" * 20, False) == "一" * 20


# ── A1（P466）：拆、合、重拆共用句子关系判断 ─────────────────────

def test_a1_merge_never_joins_complete_sentence_with_half():
    """① 复现 R1（90 秒成品第 6 条的形状）：“我现在该吃它吗？ 有点酸，”。"""
    segs = [_seg(0, 0.0, 1.2, "Should I eat it now?", "我现在该吃它吗？"),
            _seg(1, 1.2, 1.6, "it's a bit sour,", "有点酸，"),
            _seg(2, 1.6, 3.0, "but it tastes good.", "但是还挺好吃的。")]
    assert _texts(segs) == ["我现在该吃它吗？", "有点酸 但是还挺好吃的"]


def test_a1_two_complete_sentences_may_share_screen():
    """第三批 B 规则 3：两句完整的话同屏是允许的，不许被 A1 误拆。"""
    segs = [_seg(0, 0.0, 0.4, "sweet dreams.", "好梦。"),
            _seg(1, 0.4, 1.4, "it's already eaten.", "已经吃下去了。"),
            _seg(2, 1.4, 3.0, "how are you?", "你好吗？", speaker_change=True)]
    assert _texts(segs) == ["好梦 已经吃下去了", "-你好吗？"]


def test_a1_resplit_prefers_sentence_end():
    """合并后重拆先试句末：以前只用逗号，会切出“…好吗？太久没穿我的登山靴了，”这种混接。"""
    text = "咱们去机场吧，好吗？太久没穿我的登山靴了，在纽约街头穿它怪怪的，真的很奇怪"
    words = [{"word": f"w{i}", "start": i * 0.2, "end": i * 0.2 + 0.2} for i in range(30)]
    out = dt._resplit_over_two_lines([[0.0, 6.0, text, False, words]])
    assert out[0][2] == "咱们去机场吧，好吗？"


# ── A2：句末切点的时间锚点在附近真实词里找 ───────────────────────

def test_a2_sentence_end_cut_tries_nearby_anchor():
    """比例映射正好落在两个时间重叠的词上时，往旁边挪一个词找合法锚点。"""
    words = _words([("w0", 0.0, 0.5), ("w1", 0.5, 1.0), ("w2", 1.0, 1.4),
                    ("w3", 1.3, 2.0), ("w4", 2.0, 3.0)])
    segs = [_seg(0, 0.0, 3.0, "we are here and then go", "我们到了吧。然后走吧", words=words)]
    assert _texts(segs) == ["我们到了吧", "然后走吧"]


# ── A3：借时（唯一一套） ─────────────────────────────────────────

def test_a3_borrow_never_delays_speaker_change():
    segs = [_seg(0, 0.0, 0.5, "Oh.", "哦。"),
            _seg(1, 0.5, 2.0, "How are you?", "你好吗？", speaker_change=True)]
    entries = dt.build_display_timeline(segs)
    assert entries[1].start == 0.5, "换人后的第一句不许被推迟"


def test_a3_reading_time_borrowed_from_next_without_overlap():
    segs = [_seg(0, 0.0, 1.0, "this sentence needs a long reading time",
                 "这是一句需要比较长阅读时间的话"),
            _seg(1, 1.0, 6.0, "okay i know thanks for the reminder see you next time",
                 "好的，我知道了，谢谢你的提醒，我们下次再聊")]
    rep = dt.TimelineReport()
    entries = dt.build_display_timeline(segs, report=rep)
    # 插入④ 施工 B D4：不推后下一屏——没有真空白可借，第一屏照原时间交付（读得偏快，由质量检查记）
    assert entries[0].end - entries[0].start == pytest.approx(1.0)
    assert entries[1].start == pytest.approx(1.0), "下一屏不推后"
    # G12 第三批 B（排版 §12 一行优先）：第二段在逗号处拆成了几个一行的屏，看最后一屏的结束
    assert entries[-1].end == pytest.approx(6.0)
    assert rep.below_min_display == []


def test_a3_below_min_counted_after_borrow():
    """插入④ 施工 B D4 + D16：不推后下一屏；老规矩两边都会拼出混接时，允许只拼一边（取字最少的），
    拼好就不算偏短——记账在补救之后。"""
    segs = [_seg(0, 0.0, 1.0, "should i eat it now?", "我现在该吃它吗？"),
            _seg(1, 1.0, 1.5, "it's a bit sour and", "有点酸，"),
            _seg(2, 1.5, 5.0, "we go now and the weather", "我们走吧。今天天气不错",
                 words=_words([("w", 1.5, 5.0)]))]
    rep = dt.TimelineReport()
    entries = dt.build_display_timeline(segs, report=rep)
    assert entries[0].text == "我现在该吃它吗？ 有点酸", "D16：老规矩拼不成时，允许一整句+半句"
    assert rep.below_min_display == []
    assert entries[0].end - entries[0].start >= dt.MIN_DISPLAY_S - 1e-9


# ── A9（P469）：中文缺句末标点时，按英文线索在程序内部补句号 ─────

def _mark(rows):
    segs = [_seg(i, float(i), float(i) + 1.0, text, zh, speaker_change=sc)
            for i, (text, zh, sc) in enumerate(rows)]
    return dt._internal_zh_texts(segs)


def test_a9_english_sentence_end_marks_period():
    assert _mark([("we are good friends.", "我们是好朋友", False)]) == ["我们是好朋友。"]
    assert _mark([('he said "go."', "他说走", False)]) == ["他说走。"], "句号后面可以跟引号"


def test_a9_soft_trailing_punct_is_replaced_by_period():
    assert _mark([("we are friends.", "我们是朋友，", False)]) == ["我们是朋友。"]


def test_a9_next_capitalised_sentence_marks_period():
    assert _mark([("because we were best", "我们是最好的", False),
                  ("But she left", "但她走了", False)])[0] == "我们是最好的。"


def test_a9_next_starting_with_i_does_not_mark():
    for nxt in ("I think so", "I'm tired", "I've done it"):
        assert _mark([("we went there", "我们去了", False), (nxt, "我觉得", False)])[0] == "我们去了", nxt


def test_a9_proper_name_pair_gets_no_period():
    """锁定方案 A9 必测例：“The main character is named | Kyra Berry and she”不补句号。"""
    assert _mark([("The main character is named", "主角叫", False),
                  ("Kyra Berry and she", "凯拉·贝瑞，她", False)])[0] == "主角叫"


def test_a9_speaker_change_marks_period():
    assert _mark([("we went there", "我们去了", False), ("really", "真的吗", True)])[0] == "我们去了。"


def test_a9_failed_empty_or_complete_translations_untouched():
    assert _mark([("hello.", "[翻译失败]", False), ("hi.", "", False),
                  ("ok.", "好的。", False), ("so?", "好吗？”", False)]) == \
        ["[翻译失败]", "", "好的。", "好吗？”"]


def test_a9_never_writes_back_to_segment():
    segs = [_seg(0, 0.0, 2.0, "we are good friends.", "我们是好朋友"),
            _seg(1, 2.0, 4.0, "But she left", "但她走了")]
    dt.build_display_timeline(segs)
    assert [s.zh for s in segs] == ["我们是好朋友", "但她走了"], "补的句号不许写回译文"


# 真实任务缓存（只读导出，2026-09-17）：30 分钟任务 80703166ff 第 494–499 段
_M30_1087 = [
    (1083.09, 1085.25, 'There Elin I', '有个叫Elin的，我', False,
     [('There', 1083.09, 1083.97), ('Elin', 1084.29, 1084.85), ('I', 1085.01, 1085.25)]),
    (1085.25, 1086.93, 'would have hung out with her', '本来会跟她一起玩的', False,
     [('would', 1085.25, 1085.49), ('have', 1085.49, 1085.81), ('hung', 1085.81, 1085.97),
      ('out', 1085.97, 1086.13), ('with', 1086.13, 1086.29), ('her', 1086.53, 1086.93)]),
    (1087.17, 1088.29, "because we were like we're best", '因为我们就像，我们是班里最好的', False,
     [('because', 1087.17, 1087.49), ('we', 1087.49, 1087.57), ('were', 1087.57, 1087.73),
      ('like', 1087.73, 1087.89), ("we're", 1087.89, 1087.97), ('best', 1087.97, 1088.29)]),
    (1088.29, 1089.09, 'friends in the class', '朋友', False,
     [('friends', 1088.29, 1088.53), ('in', 1088.53, 1088.61), ('the', 1088.61, 1088.69),
      ('class', 1088.69, 1089.09)]),
    (1089.09, 1090.05, 'But she went away', '但她走了', False,
     [('But', 1089.09, 1089.33), ('she', 1089.33, 1089.49), ('went', 1089.49, 1089.65),
      ('away', 1089.65, 1090.05)]),
    (1090.21, 1092.37, "She's going away to summer camp for four months", '她去夏令营了，要去四个月', False,
     [("She's", 1090.21, 1090.37), ('going', 1090.37, 1090.53), ('away', 1090.53, 1090.77),
      ('to', 1090.77, 1090.85), ('summer', 1090.85, 1091.17), ('camp', 1091.17, 1091.49),
      ('for', 1091.49, 1091.57), ('four', 1091.57, 1091.89), ('months', 1091.89, 1092.37)]),
]

# 新西兰任务 1a1b9f8145 第 141–146 段
_NZ_931 = [
    (929.22, 931.46, 'This is the second brewery', '这是第二家啤酒厂', False,
     [('This', 929.22, 929.46), ('is', 929.46, 929.7), ('the', 929.78, 930.18),
      ('second', 930.18, 930.74), ('brewery', 930.9, 931.46)]),
    (931.46, 934.02, "and it's just literally right next to Rhyme and Reason", '就在Rhyme and Reason隔壁', False,
     [('and', 931.46, 931.54), ("it's", 931.54, 931.78), ('just', 931.78, 932.18),
      ('literally', 932.66, 932.98), ('right', 932.98, 933.14), ('next', 933.14, 933.38),
      ('to', 933.38, 933.46), ('Rhyme', 933.46, 933.62), ('and', 933.62, 933.7), ('Reason', 933.7, 934.02)]),
    (934.26, 934.42, 'over there', '就在那边', False,
     [('over', 934.26, 934.34), ('there', 934.34, 934.42)]),
    (934.42, 935.78, 'This one also came highly recommended', '这家也是被强烈推荐的', False,
     [('This', 934.42, 934.58), ('one', 934.58, 934.74), ('also', 934.74, 934.9),
      ('came', 934.98, 935.06), ('highly', 935.06, 935.3), ('recommended', 935.3, 935.78)]),
    (935.78, 937.7, 'and this one is Standard Lager', '这家我点的是标准拉格', False,
     [('and', 935.78, 935.86), ('this', 935.86, 936.1), ('one', 936.1, 936.34), ('is', 936.34, 936.82),
      ('Standard', 936.9, 937.3), ('Lager', 937.3, 937.7)]),
    (937.78, 940.98, "Let's see how it is It's good", '尝尝看怎么样，挺好喝的', False,
     [("Let's", 937.78, 937.94), ('see', 937.94, 938.02), ('how', 938.02, 938.18), ('it', 938.18, 938.26),
      ('is', 938.26, 938.42), ("It's", 938.42, 938.74), ('good', 940.82, 940.98)]),
]


def _real(rows):
    return [_seg(i, s, e, t, z, words=_words(ws), speaker_change=sc)
            for i, (s, e, t, z, sc, ws) in enumerate(rows)]


def test_a9_1087s_friend_example():
    """锁定方案 A9 必测例：30 分钟 1087 秒 → “因为我们就像/我们是班里最好的 朋友 ‖ 但她走了”。"""
    texts = _texts(_real(_M30_1087))
    # V5 曾靠推后下一屏让"朋友"单独成屏；插入④ 施工 B D4（不推后下一屏）后它并回前一屏，一行显示
    # 2026-10-27 C19 例外也不救它：前一屏「…最好的」话没说完，「朋友」是半截句（第 6 条，用户看视频后定）
    target = "因为我们就像 我们是班里最好的 朋友"
    assert target in texts, texts
    assert texts[texts.index(target) + 1] == "但她走了", texts


def test_a9_931s_rhyme_and_reason_example():
    """锁定方案 A9 必测例：新西兰 931 秒 → “就在Rhyme and Reason隔壁/就在那边 ‖ 这家也是被强烈推荐的”。"""
    texts = _texts(_real(_NZ_931))
    # V5 并成一行（问题 1/2）。2026-10-12 切点梯子（旧字宽口径）曾把它再切成"隔壁 / 就在那边"两屏；
    # 2026-10-13 改成汉字口径（用户定）：这一屏只有 8 个汉字、读速不超，一行放得下，不再进梯子，保持一行。
    # （这条期望的改动单独记在施工记录里，不是悄悄改。）
    target = ["就在Rhyme and Reason隔壁 就在那边", "这家也是被强烈推荐的"]
    assert target[0] in texts, texts
    i = texts.index(target[0])
    assert texts[i:i + 2] == target, texts


# ── 合同增量 #1（U5）：拆不开的“完整句 + 半句”屏必须记账 ──────────

def test_u5_unsplittable_mixed_screen_is_recorded():
    """① 复现 R7：每个切点的词时间都重叠，找不到合法切点，整条交付——以前一个数都不记。"""
    toks = "My name is Agata and back then I felt".split()
    words = [{"word": t, "start": k * 0.5, "end": k * 0.5 + 0.9} for k, t in enumerate(toks)]
    segs = [_seg(0, 0.0, 5.0, " ".join(toks), "我叫阿加塔。我那时候觉得", words=words),
            _seg(1, 5.0, 7.0, "it was really hard to speak.", "开口说真的很难。")]
    rep = dt.TimelineReport()
    entries = dt.build_display_timeline(segs, report=rep)
    assert entries[0].text == "我叫阿加塔 我那时候觉得"
    assert rep.half_sentence_screens == [(1, 0.0)]


def test_u5_summary_carries_count_without_subtitle_text():
    from backend import worker
    rep = dt.TimelineReport()
    rep.half_sentence_screens.append((3, 12.5))
    summary = worker._subtitle_quality_summary(rep)
    assert summary["half_sentence_screens"] == {"count": 1, "at": [12.5]}, \
        "只含条数和时间点，不含字幕正文"
