"""G12 第三批 B：字幕排版三项修法。

真实成品 `short10min.srt` 共 171 条，其中 **4 条是三行**（第 10 / 41 / 46 / 96 条）。
它们分两种病，本文件的用例全部用**那四条的真实文字与真实词级时间**构造：

* 病一（第 10、46 条）——**合并把词表丢在半路**。
  `_split_to_fit` 的生成式 ``for s, e, t, _ in _cut(...)`` 把 `_cut` 已经算好的词表
  第 4 位扔掉，于是草稿里没有词表。随后 `_merge_still_short` 把两条短屏用空格粘成
  一条 36 字的文本，此时**既没有词表可用来找合法切点，也没有「合完再看一眼行数」这一步**。

* 病二（第 41、96 条）——**拼接进来的空格把比例算歪**。
  `_find_safe_cut` 用 ``round(zh_pos / len(zh) * len(words))`` 把译文位置映射到原词索引，
  而 `len(zh)` 把 `sentence_splitter._build_split_segments` 拼接时插入的空格也数了进去
  （`words` 里没有这些空格对应的词）。实测偏移一个词就足以让候选被 0.84 秒条件误杀：
  第 41 条右侧 0.560s 被拒 → 去空白后 0.880s 通过；第 96 条 0.480s → 0.880s。

* 第三项不是 bug 修复，是排版：一句能一行说完的话不该被标点换行规则掰成两行。

**为什么这些用例是自包含的**：词级时间直接抄自真实任务缓存
（`evidence/g12b-run/cache/26668145d5e3/transcript.json`，SHA256 已在
`final-handoff/evidence-manifest.json` 校验），不依赖仓库外的文件，换台机器也能跑。
全量 171 条的回放另在流水线 ⑩ 端到端做。
"""

import pytest

from backend.core import display_timeline as dt


# ── 真实数据夹具（抄自真实任务缓存，不是编的）──────────────────────

# 成品第 10 条：0.48 秒的「咱们去机场吧，好吗？」与后一条被合并，合后 36 字折三行
R10A_START, R10A_END = 52.0505, 52.530499999999996
R10A_ZH = "咱们去机场吧，好吗？"
R10A_W = [(52.0505, 52.0505), (52.0505, 52.0505), (52.0505, 52.210499999999996),
          (52.210499999999996, 52.210499999999996), (52.210499999999996, 52.210499999999996),
          (52.290499999999994, 52.3705), (52.4505, 52.530499999999996)]

R10B_START, R10B_END = 52.8689375, 57.9089375
R10B_ZH = "太久没穿我的登山靴了， 在纽约街头穿它感觉怪怪的。"
R10B_W = [(52.8689375, 53.1889375), (53.1889375, 53.5089375), (53.5089375, 53.6689375),
          (53.6689375, 53.8289375), (53.8289375, 54.1489375), (54.1489375, 54.5489375),
          (54.5489375, 54.6289375), (54.7889375, 54.9489375), (54.9489375, 55.2689375),
          (55.2689375, 55.2689375), (55.9089375, 56.068937500000004),
          (56.068937500000004, 56.3089375), (56.3889375, 56.6289375),
          (56.6289375, 56.6289375), (56.708937500000005, 56.8689375),
          (56.8689375, 57.0289375), (57.0289375, 57.1089375), (57.1089375, 57.1089375),
          (57.1089375, 57.5089375), (57.5089375, 57.5089375), (57.5889375, 57.748937500000004),
          (57.748937500000004, 57.9089375)]

# 成品第 41 条：空格把比例算歪，右侧 0.560s 被 0.84 秒条件拒绝
R41_START, R41_END = 190.5301875, 194.5301875
R41_ZH = '有人在视频里告诉我， “Momo”复数不加s，就是momo。'
R41_W = [(190.5301875, 190.77018750000002), (190.77018750000002, 191.0101875),
         (191.0101875, 191.0901875), (191.0901875, 191.0901875),
         (191.0901875, 191.1701875), (191.1701875, 191.65018750000002),
         (191.65018750000002, 192.21018750000002), (192.5301875, 192.6901875),
         (192.77018750000002, 193.1701875), (193.2501875, 193.5701875),
         (193.5701875, 193.5701875), (193.65018750000002, 193.8901875),
         (193.9701875, 194.05018750000002), (194.05018750000002, 194.21018750000002),
         (194.21018750000002, 194.5301875)]

# 成品第 96 条：同一种病，右侧 0.480s 被拒
R96_START, R96_END = 367.2368125, 370.9968125
R96_ZH = "即便如此， 我们还是在一片雾气中 泡着热水澡结束了夜晚，谁知道呢？"
R96_W = [(367.2368125, 367.5568125), (367.5568125, 367.87681249999997),
         (367.87681249999997, 368.0368125), (368.0368125, 368.19681249999996),
         (368.19681249999996, 368.27681249999995), (368.27681249999995, 368.51681249999996),
         (368.51681249999996, 368.59681249999994), (368.59681249999994, 368.6768125),
         (368.6768125, 368.9168125), (368.9168125, 369.15681249999994),
         (369.15681249999994, 369.2368125), (369.31681249999997, 369.63681249999996),
         (369.63681249999996, 370.0368125), (370.1168125, 370.3568125),
         (370.51681249999996, 370.59681249999994), (370.59681249999994, 370.9968125)]


def _words(pairs):
    return [{"text": f"w{i}", "start": s, "end": e} for i, (s, e) in enumerate(pairs)]


class _Seg:
    """最小的 segment 替身：只带 build_display_timeline 真正会读的字段。"""

    def __init__(self, start, end, zh, words=(), *, speaker_change=False, text=""):
        self.start = start
        self.end = end
        self.zh = zh
        self.words = list(words)
        self.speaker_change = speaker_change
        self.text = text


def _lines(entry_or_text):
    text = getattr(entry_or_text, "text", entry_or_text)
    return text.split("\n")


def _report():
    return dt.TimelineReport()


def _body(text):
    """正文比对口径（锁定方案第 8 节第 1 条）：两边都去掉空白、逗号、句号、顿号，其余每个字都要一致。

    G12 第三批 B 收尾：成品按奈飞规范去掉逗号句号（P471），逐字节比对改用这个口径。"""
    return "".join(ch for ch in text if not ch.isspace() and ch not in "，。、,.")


def _lines_within_limit(text):
    """C1：每行严格不超过 16 单位（按渲染后的文字、含前缀）。宽松放行函数已删除。"""
    return all(dt._line_units(line) <= dt.LINE_MAX_UNITS + 1e-9 for line in text.split("\n"))


# ══════════════════════════════════════════════════════════════════
# 修法 1：词表透传 + 合并后重拆
# ══════════════════════════════════════════════════════════════════



def _without_jieba(monkeypatch):
    """G12 第三批 B（排版 §14.3）：分词工具不可用 → 不在两个汉字之间换行（等于原来的换行）。"""
    for cache in (dt._jieba_bounds, dt._jieba_tokens, dt._pos_tags):
        cache.cache_clear()
    monkeypatch.setattr(dt, "_jieba", lambda: None)

def test_split_to_fit_carries_words_out_instead_of_dropping_them():
    """A1：`_split_to_fit` 必须把 `_cut` 算好的词表带出来，一个词都不少。

    现状 `:206-209` 的 ``(s, e, t) for s, e, t, _ in _cut(...)`` 是一段
    **专门用来扔掉词表的投影**。扔掉之后，合并产物就再也找不到合法切点了。
    """
    rep = _report()
    parts = dt._split_to_fit(R10B_START, R10B_END, R10B_ZH, _words(R10B_W), rep)

    for p in parts:
        assert len(p) == 4, f"这一屏没带词表出来：{p!r}"
    assert sum(len(p[3]) for p in parts) == len(R10B_W), \
        "拆屏前后词数不等——词表在路上被吃掉或被复制了"
    # 词表必须是原对象的切片，不是重新造的
    flat = [w for p in parts for w in p[3]]
    assert [(w["start"], w["end"]) for w in flat] == R10B_W, \
        "原词时间被改写了（R-2：原词、词序、词级时间永不改写）"


def test_untranslated_segment_carries_empty_word_list_without_crashing():
    """A2：未翻译 / 翻译失败路径要带**空**词表，不造词时间，也不能解包炸掉。"""
    for zh in ("", dt._FAILED_MARKER):
        segs = [_Seg(10.0, 13.0, zh, (), text="a source line that was not translated")]
        rep = _report()
        entries = dt.build_display_timeline(segs, report=rep)

        assert len(entries) == 1
        assert entries[0].text.startswith(dt._UNTRANSLATED_PREFIX), \
            f"未翻译标记丢了：{entries[0].text!r}"
        assert entries[0].start == 10.0 and entries[0].end == 13.0, \
            "未翻译条目的时间被动过了"


def test_merge_concatenates_word_lists_in_order_without_rewriting_times():
    """A3：合并时词表按原序拼接，原词时间一个都不改。

    顺序颠倒不会报错——它会让 `left_end > right_start` 恒成立，
    于是重拆**永远静默失败**。这种病没有异常，只有难看的字幕。
    """
    fixed = [
        [0.0, 0.5, "前半。", False, _words([(0.0, 0.2), (0.2, 0.5)])],
        [0.5, 2.0, "后半。", False, _words([(0.5, 1.2), (1.2, 2.0)])],
    ]
    out = dt._merge_still_short(fixed)

    assert len(out) == 1, "两条都不足 0.84 秒，应当合并成一条"
    assert [(w["start"], w["end"]) for w in out[0][4]] == \
        [(0.0, 0.2), (0.2, 0.5), (0.5, 1.2), (1.2, 2.0)], \
        "合并后的词表不是两侧按原序拼接"


def test_real_entry_10_merged_screen_is_resplit_instead_of_shipping_three_lines():
    """A4：真实成品第 10 条。0.48 秒的邻居触发合并，合出 36 字折三行——必须重拆。

    合并产物：'咱们去机场吧，好吗？ 太久没穿我的登山靴了， 在纽约街头穿它感觉怪怪的。'
    """
    segs = [
        _Seg(R10A_START, R10A_END, R10A_ZH, _words(R10A_W)),
        _Seg(R10B_START, R10B_END, R10B_ZH, _words(R10B_W)),
    ]
    rep = _report()
    entries = dt.build_display_timeline(segs, report=rep)

    for e in entries:
        assert len(_lines(e)) <= 2, f"仍然是 {len(_lines(e))} 行：{e.text!r}"
    assert rep.over_two_lines == [], f"超行记账不该再触发：{rep.over_two_lines}"


def test_resplit_never_creates_a_screen_below_min_display():
    """A4/A5：重拆必须显式 `require_min_display=True`，不能把短屏又拆回来。

    ——重拆的存在理由就是「短屏被合并」，它自己再造短屏就成了循环。
    """
    segs = [
        _Seg(R10A_START, R10A_END, R10A_ZH, _words(R10A_W)),
        _Seg(R10B_START, R10B_END, R10B_ZH, _words(R10B_W)),
    ]
    entries = dt.build_display_timeline(segs, report=_report())

    for i, e in enumerate(entries, 1):
        assert e.end - e.start >= dt.MIN_DISPLAY_S - 1e-9, \
            f"第 {i} 条 {e.end - e.start:.3f}s 低于 {dt.MIN_DISPLAY_S}s"
    # 结构检查是交付前的最后一道闸；重拆若造出重叠或零时长，这里会抛
    dt.assert_timeline_valid(entries)


def test_resplit_judges_line_count_with_decorate_not_bare_wrap_lines(monkeypatch):
    """E1（目的不变）：带说话人前缀时首行预算更小，判断行数必须问 `_decorate`。
    裸 `wrap_lines` 判两行、带前缀实际三行的条目必须被重拆。

    G12 第三批 B 收尾：前缀改成 `-`（0.5 单位），原输入带前缀后已不再三行；
    换成渲染后首行恰好 16 单位的输入（带前缀预算 15.5 放不下）。
    G12 第三批 B：排版 §12 起可在分词词缝上换行，前提会变；本条在分词工具不可用时验（§14.3）。"""
    _without_jieba(monkeypatch)
    text = "一二三四五六七八、九十一二三四五，六七八九十一二三四。"   # 渲染后首行 8+1+7 = 16，次行 9
    assert len(dt.wrap_lines(text).split("\n")) == 2, "用例前提：裸 wrap_lines 判两行"
    assert len(dt._decorate(text, True).split("\n")) == 3, "用例前提：带前缀后 _decorate 判三行"

    rows = [[0.0, 6.0, text, True, _words([(i * 0.2, i * 0.2 + 0.2) for i in range(30)])]]
    out = dt._resplit_over_two_lines(rows)

    assert len(out) == 2, "带说话人前缀、实际三行的条目没有被重拆"


def test_resplit_is_single_pass_and_never_recurses():
    """E2：重拆只切一刀。递归会为了凑两行把一句话剁成更多屏，与产品规则 1 相悖。"""
    text = "第一段内容啊啊啊，第二段内容啊啊啊，第三段内容啊啊啊，第四段内容啊啊啊，第五段内容啊啊。"
    assert len(dt.wrap_lines(text).split("\n")) > 2, "用例前提：这段折得出三行以上"

    rows = [[0.0, 6.9, text, False, _words([(i * 0.15, i * 0.15 + 0.15) for i in range(45)])]]
    out = dt._resplit_over_two_lines(rows)

    assert len(out) == 2, f"一次重拆应当恰好把一条变两条，实际 {len(out)} 条"


def test_unsplittable_row_keeps_fallback_and_over_two_lines_accounting():
    """A6：切不动就整条保留，并且**照常记账**——静默交付是不允许的。

    构造：有逗号所以折得出三行，但只有一个词，`_find_safe_cut` 必然放弃。
    """
    text = "每一天的风景都越来越美，越来越不真实，所以坚持下去，像看一部小电影一样看下去。"
    segs = [_Seg(10.0, 16.0, text, _words([(10.0, 16.0)]))]
    rep = _report()
    entries = dt.build_display_timeline(segs, report=rep)

    assert len(entries) == 1, "切不动却被拆开了——说明有人在硬切"
    assert _body(entries[0].text) == _body(text), "切不动的条目正文被改动了"
    assert rep.over_two_lines, "切不动而仍超两行，一个计数器都没加（报警器读 0）"


# ══════════════════════════════════════════════════════════════════
# 修法 2：空白让比例算歪，合法切点被误杀
# ══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("start,end,zh,wpairs,label", [
    (R41_START, R41_END, R41_ZH, R41_W, "成品第 41 条"),
    (R96_START, R96_END, R96_ZH, R96_W, "成品第 96 条"),
])
def test_real_entries_with_joined_spaces_get_split_instead_of_three_lines(
        start, end, zh, wpairs, label):
    """B1-B5（目的不变：真实第 41/96 条不得再是三行，不丢字、不造短屏）。

    G12 第三批 B 收尾（锁定方案 C3，行为变化）：16 单位两行排版下这两条都放得进同一屏两行，
    原「必须拆成两屏」改为「不超过两行、每行严格 ≤16 单位（C1）、不造短屏」。"""
    segs = [_Seg(start, end, zh, _words(wpairs))]
    rep = _report()
    entries = dt.build_display_timeline(segs, report=rep)

    for e in entries:
        assert len(_lines(e)) <= 2, f"{label} 仍是 {len(_lines(e))} 行：{e.text!r}"
        assert _lines_within_limit(e.text), f"{label} 有一行超过 16 单位：{e.text!r}"
        assert e.end - e.start >= dt.MIN_DISPLAY_S - 1e-9, \
            f"{label} 出现了 {e.end - e.start:.3f}s 的短屏"
    assert _body("".join(e.text for e in entries)) == _body(zh), f"{label} 丢字或乱序"


def test_second_round_does_not_inflate_the_unsplittable_counter():
    """E7：第二轮成功时，用户不能看到假的「N 条长字幕未能拆分」。

    这条钉死的是**位置**：重试必须写在 `_find_safe_cut` 内部。
    `_cut:257-264` 的计数器只在 `found is None` 时才加；重试若放在 `_cut` 外层，
    第一轮失败已经把计数器加过了，第二轮再成功也收不回来。
    """
    rep = _report()
    dt._split_to_fit(R41_START, R41_END, R41_ZH, _words(R41_W), rep)

    total = (rep.mapping_failed_splits + rep.no_safe_boundary_splits
             + rep.single_word_over_max)
    assert total == 0, \
        f"第二轮已经把它拆开了，却还记了 {total} 条「未能拆分」——用户会看到假的失败计数"


def test_first_round_is_untouched_when_it_already_succeeds():
    """B1/B2：原候选能过时，行为逐字节不变，且**第二轮一次都不进**。

    用受控替身数第二轮的调用次数——不是查源码字符串，是查它有没有真的执行。
    """
    assert hasattr(dt, "_ratio_index"), "比例映射没有抽成单一实现（E4）"

    calls = []
    original = dt._ratio_index

    def spy(zh, zh_pos, n_words, *, ignore_whitespace=False):
        calls.append(ignore_whitespace)
        return original(zh, zh_pos, n_words, ignore_whitespace=ignore_whitespace)

    dt._ratio_index = spy
    try:
        # 无空格、原比例就能切开的普通长句
        zh = "第一段内容说完了，第二段内容也说完了，最后一段收尾。"
        words = _words([(i * 0.3, i * 0.3 + 0.3) for i in range(24)])
        found = dt._find_safe_cut(0.0, 7.2, zh, words, dt._COMMA)
    finally:
        dt._ratio_index = original

    assert found is not None, "用例前提：这一条第一轮就该切得开"
    assert calls, "两轮都没调 `_ratio_index`——比例映射不是单一实现"
    assert True not in calls, \
        "第一轮已经成功，却仍然跑了去空白的第二轮（B2：只有全部失败才重试一次）"


def test_split_preserves_body_text_and_english_word_spaces():
    """B4：只改索引计算，正文与英文词内空格一个都不删。"""
    zh = "他说 hot tub in misty weather，然后我们就回去了，真的很舒服。"
    words = _words([(i * 0.25, i * 0.25 + 0.25) for i in range(24)])
    segs = [_Seg(0.0, 6.0, zh, words)]
    entries = dt.build_display_timeline(segs, report=_report())

    rebuilt = " ".join(e.text.replace("\n", "") for e in entries)
    assert "hot tub in misty weather" in rebuilt, \
        f"英文词之间的空格被删了：{rebuilt!r}"


# ══════════════════════════════════════════════════════════════════
# 修法 3（G12 第三批 B 收尾改写）：16 单位以内一行，行长按渲染后的文字算
# ══════════════════════════════════════════════════════════════════
#
# 原来的「短两行合回一行」（正文非空白 ≤26 字）已删除，被锁定方案 A5 取代：
# 渲染后 16 单位以内不折，超过才排两行（用户 2026-09-16 按奈飞确认）。
# 17–26 单位的内容从一行变成两行是**有意的变更**。成品按奈飞规范去掉逗号句号（A7），
# 所以下面的期望值都是渲染后的文字。


def test_short_two_line_subtitle_is_rendered_as_one_line():
    """C1（目的不变：放得下一行就一行、正文不变）。标准：渲染后 ≤16 单位。"""
    at_16 = "咱们去，机场吧，好吗？太久没穿鞋了。"      # 渲染后 3+0.5+3+0.5+3+6 = 16.0
    over = "咱们去，机场吧，好吗？太久没穿靴子了。"     # 渲染后 17.0
    assert dt._decorate(at_16, False) == "咱们去 机场吧 好吗？太久没穿鞋了", "16 单位放得下一行"
    assert dt._line_units(dt._decorate(at_16, False)) == 16.0
    assert dt._decorate(over, False) == "咱们去 机场吧 好吗？\n太久没穿靴子了", \
        "17 单位必须两行，且优先在句末标点后断"


def test_join_back_preserves_inner_spaces_byte_for_byte():
    """C2（目的不变：渲染只动逗号句号，英文词和词间空格一个不丢；折行只在断点处）。"""
    zh = "Hello world， and then some more"
    out = dt._decorate(zh, False)
    assert out == "Hello world and then some more" and "\n" not in out, \
        f"逗号应变成空格且与原空格合一，其余逐字节保留：{out!r}"

    longer = zh + " ok"                           # 渲染后 16.5 单位
    out2 = dt._decorate(longer, False)
    assert out2 == "Hello world\nand then some more ok", out2
    assert " ".join(out2.split()) == " ".join(longer.replace("，", " ").split()), \
        "英文词被切开或空格被吞"


def test_join_back_threshold_counts_non_whitespace_body_only():
    """C1 计数口径（**旧规则被新标准替换**）：原「只数非空白、26 进 27 不进」
    改为「按渲染后文字：空格与半角计 0.5、全角计 1，16 进 16.5 不进」。"""
    assert dt._line_units("中") == 1.0 and dt._line_units("a") == 0.5
    assert dt._line_units(" ") == 0.5 and dt._line_units("- ") == 1.0
    assert dt._line_units("——") == 2.0 and dt._line_units("……") == 2.0, "中文破折号、省略号是全角"
    at_16 = "一二三四五六七八九十 一二 三四五"          # 10+0.5+2+0.5+3 = 16.0
    at_16_5 = at_16 + "a"                          # 16.5
    assert dt._decorate(at_16, False) == at_16, "16.0 应当一行，且空格保留"
    out = dt._decorate(at_16_5, False)
    assert "\n" in out, "16.5 超过上限，必须两行"
    assert "".join(out.split()) == "".join(at_16_5.split())
    commas = "一二三四五六七八九十，一二三四五"          # 带标点 16 单位，渲染后 15.5
    assert dt._decorate(commas, False) == "一二三四五六七八九十 一二三四五", "行长按渲染后的文字算"


def test_join_back_keeps_speaker_prefix_separate_from_the_26_budget():
    """C3（**旧规则被新标准替换**）：原「前缀不参与阈值」改为「前缀计入最终行长」。
    前缀改成 `-`（0.5 单位）：渲染后正文 16 + 前缀 = 16.5 必须两行；正文 15.5 + 前缀 = 16 一行。"""
    at_16 = "我那时候觉得开口说真的难。是。真的"          # 渲染后 12+0.5+1+0.5+2 = 16.0
    assert dt._decorate(at_16, False) == "我那时候觉得开口说真的难 是 真的", "不带前缀时恰好一行"
    out = dt._decorate(at_16, True)
    assert out.startswith(dt._SPEAKER_PREFIX)
    assert len(out.split("\n")) == 2, f"前缀没算进行长：{out!r}"
    assert max(dt._line_units(l) for l in out.split("\n")) <= 16.0
    at_15_5 = "我那时候觉得开口说真的难。是真的"          # 渲染后 15.5
    assert dt._decorate(at_15_5, True) == "-我那时候觉得开口说真的难 是真的", "前缀 0.5 + 正文 15.5 = 16，一行"


def test_three_line_and_long_two_line_subtitles_are_untouched():
    """C1 的反面（合回功能已删除，目的由「放不下两行的一个字不丢、行数如实变多」接管）。"""
    three = "第一段内容啊啊啊，第二段内容啊啊啊，第三段内容啊啊啊，第四段内容啊。"   # 渲染后 32.5 单位
    out = dt._decorate(three, False)
    assert len(out.split("\n")) >= 3, "放不下两行的文字被硬塞进两行"
    assert _body(out) == _body(three), "折行丢字或乱序"
    assert all(dt._line_units(l) <= 16 for l in out.split("\n"))


def test_space_inside_an_english_name_is_the_weakest_break():
    """真实反例（新西兰 272.900s，r9 实际输出）：`路上放着The Strokes和Tame / Impala的歌，`
    ——乐队名 Tame Impala 被拆到两行。英文词之间的空格排在中外文交界之后。

    G12 第三批 B 收尾：同级断点优先上短下长（A5），行尾逗号渲染时删掉（A7）；
    期望从 `路上放着The Strokes / 和Tame Impala的歌，` 变成下面这样——两个英文名仍然都没被拆开。"""
    zh = "路上放着The Strokes和Tame Impala的歌，"
    out = dt._decorate(zh, False)
    assert out == "路上放着\nThe Strokes和Tame Impala的歌", f"英文专名被拆开：{out!r}"


def test_line_never_starts_with_closing_punctuation_or_splits_a_dash_pair():
    """真实反例（30 分钟 472.970s，r5 实际输出）：`我们不知道—\n—\n哦，\n你们不知道性别啊。`
    ——「——」被拆开，单独一个「—」占一行。标点与收尾引号必须留在上一行行尾。"""
    zh = "我们不知道——哦，你们不知道性别啊。"
    out = dt._decorate(zh, False)
    assert _body(out) == _body(zh)
    for line in out.split("\n")[1:]:
        assert line[0] not in "，。！？、；：…—”’）》」』", f"行首是标点：{out!r}"
    assert "—\n—" not in out, f"「——」被拆开：{out!r}"
    closing = "他说：“你马上就会遇到全世界最狂热的粉丝了。”然后就走了。"
    out2 = dt._decorate(closing, False)
    assert _body(out2) == _body(closing)
    for line in out2.split("\n")[1:]:
        assert line[0] not in "，。！？、；：…—”’）》」』", f"行首是标点或收尾引号：{out2!r}"


def test_join_back_threshold_constant_actually_drives_the_behavior(monkeypatch):
    """E5（目的不变：行长上限必须是被真正读到的具名常量）。原 `_JOIN_BACK_MAX_CHARS` 已删除，
    唯一的行长常量是 `LINE_MAX_UNITS`。"""
    zh = "咱们去机场吧，好吗？"                   # 渲染后 9.5 单位
    assert dt._decorate(zh, False) == "咱们去机场吧 好吗？", "用例前提：默认上限下一行"
    monkeypatch.setattr(dt, "LINE_MAX_UNITS", 8.0)
    assert "\n" in dt._decorate(zh, False), "把上限调到 8 之后行为没变——常量没有被读到"
    assert dt._first_line_budget(True) == 7.5, "前缀 `-` 占 0.5 单位"


def test_join_back_does_not_change_any_timing():
    """C3（锁定方案，G12 第三批 B 收尾）：「排版不改时间」只对“只换行、不需要补阅读时间”的
    单句断言——真实第 41、96 条，起止时间必须等于原句。

    四条夹具一起跑从 6 屏变成 4 屏，**是行为变化**：16 单位两行放得下第 41、96 条，不再拆屏；
    第 10 条的 0.48 秒短屏改为向后一屏借时补到 0.84 秒，不再合并。

    G12 第三批 B（排版 §12 一行优先）：两行放得下的句子，每块 ≥6 字、≥1 秒、每秒 ≤7 字时
    会在逗号处拆成一行的屏——「只换行不拆」的旧规矩被方案改掉。本条原意（排版不改原句的
    起止时间、不丢字）照旧：第一屏从原句开头出现，最后一屏在原句结尾消失。"""
    for start, end, zh, wpairs in ((R41_START, R41_END, R41_ZH, R41_W),
                                    (R96_START, R96_END, R96_ZH, R96_W)):
        entries = dt.build_display_timeline([_Seg(start, end, zh, _words(wpairs))], report=_report())
        assert (entries[0].start, entries[-1].end) == (start, end), "单句的起止时间被改了"
        assert _body("".join(e.text for e in entries)) == _body(zh), "排版丢字或乱序"

    segs = [
        _Seg(R10A_START, R10A_END, R10A_ZH, _words(R10A_W)),
        _Seg(R10B_START, R10B_END, R10B_ZH, _words(R10B_W)),
        _Seg(R41_START, R41_END, R41_ZH, _words(R41_W)),
        _Seg(R96_START, R96_END, R96_ZH, _words(R96_W)),
    ]
    entries = dt.build_display_timeline(segs, report=_report())
    assert len(entries) >= 4          # 一行优先：第 41、96 条可能各拆成两个一行的屏
    assert _body("".join(e.text for e in entries)) == _body("".join(s.zh for s in segs)), "排版丢字或乱序"
    for e in entries:
        assert len(_lines(e)) <= 2 and _lines_within_limit(e.text), e.text


def test_wrap_lines_and_needs_split_behaviour_unchanged(monkeypatch):
    """C4（目的不变：`wrap_lines` / `_needs_split` 是「怎么换行、要不要拆屏」的单一来源）。
    期望行数按 16 单位两行规则更新；无可靠断点的纯汉字长串仍不硬切。

    G12 第三批 B：分词工具不可用时（§14.3）换行等于原来；「两行放得下」的屏现在也进入
    一行优先的拆屏**尝试**（§12 第 1 步），真拆不拆由每块条件决定。"""
    _without_jieba(monkeypatch)
    expected = {
        "短句。": 1,
        "一" * 20: 1,
        "一" * 21: 1,
        "咱们去机场吧，好吗？太久没穿我的登山靴了。": 2,
        "这是一个很长的句子需要换行处理，后面还有更多内容要说，最后还有一段。": 3,
    }
    for zh, n in expected.items():
        assert len(dt.wrap_lines(zh).split("\n")) == n, f"`wrap_lines` 行数不对：{zh!r}"
        assert dt.wrap_lines(zh) == dt.wrap_lines(zh, dt.LINE_MAX_UNITS), "默认参数与显式传参结果不同"

    assert dt._needs_split(0.0, 8.0, "短句。") is True, "超 7 秒该拆"
    assert dt._needs_split(0.0, 3.0, "短句。") is False, "两行以内、不超时的不该拆"
    assert dt._needs_split(0.0, 3.0, "咱们去机场吧，好吗？太久没穿我的登山靴了。") is True, \
        "一行放不下的进入一行优先的拆屏尝试（排版 §12）"
    assert dt._originally_needs_split(0.0, 3.0, "咱们去机场吧，好吗？太久没穿我的登山靴了。") is False, \
        "原来就两行放得下的，不算「原来就必须拆」"
    assert dt._needs_split(0.0, 3.0, "这是一个很长的句子需要换行处理，后面还有更多内容要说，最后还有一段。") is True, \
        "放不下两行的该拆"


# ══════════════════════════════════════════════════════════════════
# 组合：三项一起跑，四条真实三行必须全部归零
# ══════════════════════════════════════════════════════════════════


def test_all_four_real_three_line_entries_are_fixed_together():
    """D1/D2：四条真实三行同时进流水线，全部归零，且不产生新的时间违规。"""
    segs = [
        _Seg(R10A_START, R10A_END, R10A_ZH, _words(R10A_W)),
        _Seg(R10B_START, R10B_END, R10B_ZH, _words(R10B_W)),
        _Seg(R41_START, R41_END, R41_ZH, _words(R41_W)),
        _Seg(R96_START, R96_END, R96_ZH, _words(R96_W)),
    ]
    rep = _report()
    entries = dt.build_display_timeline(segs, report=rep)

    three_line = [(i, e.text) for i, e in enumerate(entries, 1) if len(_lines(e)) > 2]
    assert three_line == [], f"仍有三行字幕：{three_line}"
    assert rep.over_two_lines == [], f"超行记账仍在触发：{rep.over_two_lines}"
    assert rep.below_min_display == [], f"出现了低于 0.84 秒的条目：{rep.below_min_display}"

    dt.assert_timeline_valid(entries)
    for e in entries:
        dur = e.end - e.start
        assert dt.MIN_DISPLAY_S - 1e-9 <= dur <= dt.MAX_DISPLAY_S + 1e-9, \
            f"{dur:.3f}s 越界：{e.text!r}"


def test_speaker_change_rows_are_never_merged_across_people():
    """D4/C6：已标记换人处不跨人合并（既有保护），本轮三项不得削弱它。"""
    fixed = [
        [0.0, 0.4, "这一句很短。", False, _words([(0.0, 0.2), (0.2, 0.4)])],
        [0.4, 2.0, "换人说话了。", True, _words([(0.4, 1.2), (1.2, 2.0)])],
    ]
    out = dt._merge_still_short([list(r) for r in fixed])

    assert len(out) == 2, "跨说话人被合并了——两个人的话被粘成一条"
