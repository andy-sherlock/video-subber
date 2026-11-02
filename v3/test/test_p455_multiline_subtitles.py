"""P455：一条字幕塞进三到五行，因为逗号切分被时长门槛挡在门外。

真实成品 895 条里 49 条达到三行以上（3行33 / 4行14 / 5行2）。
这 49 条**时长全部 ≤6.98 秒**，而逗号那一趟写着「只有超过 7 秒才切」，
于是一次都没拔出来过。229.47–235.71 那条五行字幕（6.24 秒、45 字）里有四个逗号，
一个都没用上。

同一份切点，`wrap_lines` 用了（折成 5 行），`_cut` 没用（整条不拆）——
同一件事有两份实现。门槛也早就存在并标定过：`MAX_ZH_CHARS = 20`，注释写着「单一来源」。
"""

from backend.core import display_timeline as dt


def _report():
    return dt.TimelineReport()



def _without_jieba(monkeypatch):
    """G12 第三批 B（排版 §14.3）：分词工具不可用 → 不在两个汉字之间换行（等于原来的换行）。"""
    for cache in (dt._jieba_bounds, dt._jieba_tokens, dt._pos_tags):
        cache.cache_clear()
    monkeypatch.setattr(dt, "_jieba", lambda: None)

def test_short_but_long_text_gets_split_into_multiple_screens():
    """6.24 秒、45 字、四个逗号——现状整条不拆，应当拆成多屏。"""
    zh = "每一天的风景都越来越美，越来越不真实，所以坚持下去，像看一部小电影一样看下去，希望你们喜欢"
    words = [{"text": f"w{i}", "start": 229.47 + i * 0.13, "end": 229.47 + i * 0.13 + 0.12}
             for i in range(48)]

    parts = dt._split_to_fit(229.47, 235.71, zh, words, _report())

    assert len(parts) > 1, "≤7 秒的长字幕仍然整条不拆"
    # G12-B：`_split_to_fit` 现在把词表一起带出来（第 4 位），解包多一个占位。
    # 断言本身一个字没动——仍然是「这一屏不得超过两行」。
    for _s, _e, text, _words in parts:
        assert len(dt.wrap_lines(text).split("\n")) <= 2, f"这一屏仍然超过两行: {text!r}"


def test_text_preserved_in_order_after_splitting():
    """拆屏不得丢字、不得乱序。"""
    zh = "每一天的风景都越来越美，越来越不真实，所以坚持下去，像看一部小电影一样看下去，希望你们喜欢"
    words = [{"text": f"w{i}", "start": 229.47 + i * 0.13, "end": 229.47 + i * 0.13 + 0.12}
             for i in range(48)]

    parts = dt._split_to_fit(229.47, 235.71, zh, words, _report())

    assert "".join(p[2] for p in parts) == zh


def test_short_text_under_threshold_is_left_alone():
    """没超过换行门槛的短字幕不受影响，行为逐字节不变。"""
    zh = "今天天气不错。"
    words = [{"text": "w0", "start": 10.0, "end": 10.4},
             {"text": "w1", "start": 10.5, "end": 10.9}]

    parts = dt._split_to_fit(10.0, 11.0, zh, words, _report())

    assert len(parts) == 1
    assert parts[0][2] == zh


def test_unsplittable_long_text_is_counted_not_silently_dropped():
    """由**行数**触发、但切不动时，计数器必须记账——否则报警器永远读 0。

    构造：有逗号所以 wrap_lines 折得出三行以上，但词级时间只有一个词，
    `_find_safe_cut` 找不到合法的原词边界，整条拆不动。
    这一条守的是「只改门槛不改计数器守卫」这个漏法。
    """
    zh = "每一天的风景都越来越美，越来越不真实，所以坚持下去，像看一部小电影一样看下去，希望你们喜欢"
    assert len(dt.wrap_lines(zh).split("\n")) > 2, "用例前提：这段文字折得出三行以上"
    words = [{"text": "w0", "start": 10.0, "end": 15.0}]   # 只有一个词，切不动
    rep = _report()

    dt._split_to_fit(10.0, 15.0, zh, words, rep)

    total = (rep.single_word_over_max + rep.mapping_failed_splits
             + rep.no_safe_boundary_splits)
    assert total > 0, "由行数触发、切不动，却一个计数器都没加"


def test_no_punctuation_single_line_is_not_a_p455_case(monkeypatch):
    """无标点的长文本 wrap_lines 只给一行（它从不硬切），不属于 P455 的 ≥3 行缺陷。

    明确记录这个边界，免得下一轮审查把它当漏网重新讨论一遍。
    G12 第三批 B：排版 §12 起可在分词词缝上换行；本条改验分词工具不可用时的退回（§14.3）。
    """
    _without_jieba(monkeypatch)
    zh = "这是一段没有任何标点可以断开的很长的中文字幕内容一直写下去写到超过二十个字为止"

    assert len(dt.wrap_lines(zh).split("\n")) == 1


# ── ⑦.5 读代码发现：说话人切换的字幕从这次修复底下溜过去 ──────

class _Seg:
    """最小 Segment 替身，与 test_g5_display_timeline.py 一致。"""

    def __init__(self, id, start, end, text="", zh="", words=None,
                 speaker_change=False, uncertain=False):
        self.id, self.start, self.end = id, start, end
        self.text, self.zh = text, zh
        self.words = words or []
        self.speaker_change = speaker_change
        self.uncertain = uncertain


def test_speaker_change_subtitle_also_stays_within_two_lines():
    """说话人切换时前缀「- 」占掉两个字，首行预算从 20 缩到 18。

    `_needs_split` 原来按 20 问「折几行」，渲染却按 18 折——**同一件事两套配置**。
    于是这条被判成「2 行不用拆」，用户实际看到 3 行。
    这一条走 `build_display_timeline` 端到端，断言用户**真正看到**的行数。
    """
    zh = "今天的天气真的很不错，我们出去走走走。第二句话。"
    words = [{"word": f"w{i}", "start": 10.0 + i * 0.2, "end": 10.0 + i * 0.2 + 0.18}
             for i in range(12)]
    seg = _Seg(0, 10.0, 12.4, text="x", zh=zh, words=words, speaker_change=True)

    entries = dt.build_display_timeline([seg])

    for entry in entries:
        text = entry.text if hasattr(entry, "text") else entry[2]
        assert len(text.split("\n")) <= 2, f"说话人切换的这一屏有 {len(text.splitlines())} 行: {text!r}"


def test_line_budget_has_one_source():
    """首行预算只能有一个来源（目的不变）。计数口径改为最终可见单位：前缀 `-` = 0.5。"""
    assert dt._first_line_budget(True) == dt.LINE_MAX_UNITS - dt._line_units(dt._SPEAKER_PREFIX)
    assert dt._first_line_budget(False) is None


def test_over_two_lines_entries_are_reported_not_silently_delivered():
    """合同 A3 的兜底（目的不变）：真放不下两行又切不动时，必须进质量摘要。
    原输入 26 单位在 16 单位两行排版下已放得下，换成确实放不下两行的输入。"""
    zh = "好吧。在那些选项里，我选了“Momo怀旧杰克逊高地视频”。然后我们就出发去了机场，一路上都很顺利。"
    words = [{"word": "w0", "start": 177.47, "end": 183.39}]   # 只有一个词，切不动
    seg = _Seg(0, 177.47, 183.39, text="x", zh=zh, words=words)
    rep = _report()

    entries = dt.build_display_timeline([seg], report=rep)

    rendered = [e.text for e in entries]
    # 永不双行（用户 2026-10-12 定）：切不动也并成一行交付，但仍要记账（下面）
    assert all("\n" not in t for t in rendered), "永不双行"
    assert rep.over_two_lines, "超过两行的条目静默交付了，质量摘要里一条记录都没有"
    assert rep.over_two_lines[0][1] == 177.47, f"记账要能指出是哪一条: {rep.over_two_lines}"


def test_two_line_entries_are_not_reported():
    """两行以内是正常的，不得误报。"""
    seg = _Seg(0, 10.0, 12.0, text="x", zh="今天天气不错。",
               words=[{"word": "w0", "start": 10.0, "end": 12.0}])
    rep = _report()

    dt.build_display_timeline([seg], report=rep)

    assert not rep.over_two_lines
