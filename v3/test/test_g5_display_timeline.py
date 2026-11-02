"""G5-1 显示线合同测试。

对应管道 `pipelines/bug-pipeline-2026-08-09-G5.md` ⑤.5 施工映射表 A1—A22。
本文件只测 G5-1（施工 5/6/7/8/9）；地图线（施工 1/2/3/4）属 G5-2，不在此。

数据流（⑤ 走查定稿的三把刀顺序）：
    翻译后 segments
      → 刀① 字数拆条（既有，sentence_splitter，本批不动其触发条件）
      → 刀③ 7 秒拆屏（build_display_timeline 内，映射译文切点到真实原词边界）
      → 时间修复（0.84 补救 / 7 秒封顶 / 结构修复）
      → 刀② 每屏仍超 20 字才换行
      → 三个写出器只读同一张表，各自取整序列化
"""
import re
import pytest


class _Seg:
    """最小 Segment 替身：只带显示层真正读的字段。"""

    def __init__(self, id, start, end, text="", zh="", words=None,
                 speaker_change=False, uncertain=False):
        self.id = id
        self.start = start
        self.end = end
        self.text = text
        self.zh = zh
        self.words = words or []
        self.speaker_change = speaker_change
        self.uncertain = uncertain


def _w(word, start, end):
    return {"word": word, "start": start, "end": end}


# ── A15 / T35：时间表条目不可变 ──────────────────────────────────

def test_a15_display_entry_is_immutable():
    """把 R-14「写出器不得修改任何时间」从嘱咐变成语言层面的事实。"""
    from backend.core.display_timeline import DisplayEntry

    entry = DisplayEntry(start=1.0, end=2.0, text="你好")
    with pytest.raises(Exception) as exc:
        entry.start = 5.0
    assert "FrozenInstance" in type(exc.value).__name__ or isinstance(exc.value, AttributeError)


# ── A2 / T36：进入时间表前不得提前取整 ────────────────────────────

def test_a2_no_early_rounding_before_timeline():
    """原词 end 的小数位超过两位时，不得在进入时间表前被压成两位。"""
    from backend.core.display_timeline import build_display_timeline

    # 总长与每段都控制在 7 秒内，避免把封顶规则混进这条断言
    seg = _Seg(
        id=1, start=0.0, end=11.3456, zh="你好。世界。",
        text="Hello. World.",
        words=[_w("Hello.", 0.0, 5.0), _w("World.", 5.1, 11.3456)],
    )
    entries = build_display_timeline([seg])
    ends = [e.end for e in entries]
    assert 11.3456 in ends, f"11.3456 被提前取整了: {ends}"


# ── A17 / T37：空译文占位服从统一规则，没有硬编码 6 秒 ──────────────

def test_a17_empty_translation_obeys_unified_rules():
    """删掉 subtitler.py:90 的 min(seg.end, seg.start + 6.0)。"""
    from backend.core.display_timeline import build_display_timeline, MAX_DISPLAY_S

    seg = _Seg(id=1, start=0.0, end=30.0, zh="", text="untranslated")
    entries = build_display_timeline([seg])
    assert entries, "空译文不应产出零条目"
    for e in entries:
        dur = e.end - e.start
        assert dur <= MAX_DISPLAY_S + 1e-9, f"空译文占位 {dur}s 超过统一上限"
    # 不得恰好等于旧的 6.0 秒硬编码
    assert not any(abs((e.end - e.start) - 6.0) < 1e-9 for e in entries), \
        "仍在使用硬编码 6 秒截断"


def test_a17_failed_translation_has_single_user_message():
    """④ 发现 4：两个失败分支说同一件事，合并为一种用户可见文案。"""
    from backend.core.display_timeline import build_display_timeline

    empty = build_display_timeline([_Seg(id=1, start=0.0, end=3.0, zh="", text="abc")])
    marked = build_display_timeline(
        [_Seg(id=2, start=0.0, end=3.0, zh="[翻译失败]", text="abc")]
    )
    kinds = {re.sub(r"abc", "", e.text).strip() for e in empty + marked}
    assert len(kinds) == 1, f"翻译失败仍有两种文案: {kinds}"


# ── A4 / T10：最短显示 0.84 秒 ────────────────────────────────

def test_a4_short_entry_extended_to_min_display():
    """P402 真实现场：英文 02:07 的「哦。」只有 0.08 秒。"""
    from backend.core.display_timeline import build_display_timeline, MIN_DISPLAY_S

    segs = [
        _Seg(id=1, start=127.121, end=127.201, zh="哦。", text="Oh.",
             words=[_w("Oh.", 127.121, 127.201)]),
        _Seg(id=2, start=135.0, end=138.0, zh="然后呢。", text="Then what.",
             words=[_w("Then", 135.0, 136.0), _w("what.", 136.0, 138.0)]),
    ]
    entries = build_display_timeline(segs)
    first = entries[0]
    assert first.end - first.start >= MIN_DISPLAY_S - 1e-9, \
        f"0.08 秒字幕未被补救: {first.end - first.start}"


def test_a4_extension_never_moves_start_earlier():
    """R-7：字幕提前出现完全禁止，补救只能向后延长。"""
    from backend.core.display_timeline import build_display_timeline

    seg = _Seg(id=1, start=10.0, end=10.08, zh="哦。", text="Oh.",
               words=[_w("Oh.", 10.0, 10.08)])
    entries = build_display_timeline([seg])
    assert entries[0].start >= 10.0 - 1e-9, "字幕被提前了"


# ── A5 / T9：每屏最长 7.00 秒，零例外 ──────────────────────────

def test_a5_no_entry_exceeds_max_display():
    """P401 真实现场：英文 10:32 挂 15.31 秒，中间 11.14 秒无人说话。"""
    from backend.core.display_timeline import build_display_timeline, MAX_DISPLAY_S

    words = [
        _w("Um", 632.89, 633.29), _w("could", 633.37, 633.53),
        _w("I", 633.53, 633.53), _w("have", 633.53, 633.69),
        _w("a", 633.69, 633.85), _w("double", 635.85, 636.09),
        _w("scoop", 636.17, 636.41), _w("Thank", 647.55, 647.87),
        _w("you.", 647.87, 648.19),
    ]
    seg = _Seg(id=1, start=632.89, end=648.19,
               zh="嗯，能要双球吗？谢谢！", text="Um could I have a double scoop Thank you.",
               words=words)
    entries = build_display_timeline([seg])
    for e in entries:
        assert e.end - e.start <= MAX_DISPLAY_S + 1e-9, \
            f"交付了 {e.end - e.start}s 的字幕，超过 7 秒零例外上限"


def test_a5_single_word_longer_than_max_is_capped_not_split():
    """单词自身超 7 秒：不劈开词、原词时间不改、显示仍封顶。"""
    from backend.core.display_timeline import build_display_timeline, MAX_DISPLAY_S

    seg = _Seg(id=1, start=55.261, end=64.221, zh="啊", text="Aaaah",
               words=[_w("Aaaah", 55.261, 64.221)])
    entries = build_display_timeline([seg])
    assert len(entries) == 1, "单个原词被劈开了"
    assert entries[0].end - entries[0].start <= MAX_DISPLAY_S + 1e-9


# ── A6 / A7：拆屏只在能证明安全时进行 ─────────────────────────

def test_a6_no_split_inside_a_word():
    """T13：无标点长译文不得把「商店」切成「商 / 店」。"""
    from backend.core.display_timeline import build_display_timeline

    words = [_w("I", 0.0, 2.0), _w("went", 2.0, 5.0), _w("shopping", 5.0, 12.0)]
    seg = _Seg(id=1, start=0.0, end=12.0,
               zh="我去了商店买东西然后又去了另外一家商店看看",
               text="I went shopping", words=words)
    entries = build_display_timeline([seg])
    joined = "".join(e.text.replace("\n", "") for e in entries)
    assert "商店" in joined, "「商店」被从中间切开了"


def test_a7_unmappable_split_falls_back_to_no_split():
    """T18/R-11：无法对应真实原词边界的分屏不得进入时间表。"""
    from backend.core.display_timeline import build_display_timeline, MAX_DISPLAY_S

    # 无标点、无可用原词边界（只有一个词）→ 只能整条不拆 + 封顶
    seg = _Seg(id=1, start=0.0, end=15.0,
               zh="这是一段没有任何标点符号的很长的中文译文根本没法安全拆开",
               text="Mumble", words=[_w("Mumble", 0.0, 15.0)])
    entries = build_display_timeline([seg])
    assert len(entries) == 1, "在没有安全边界时仍然拆屏了"
    assert entries[0].end - entries[0].start <= MAX_DISPLAY_S + 1e-9


def test_a7_no_char_ratio_time_allocation():
    """按译文字数比例分配时间的路径必须消失。"""
    from backend.core import display_timeline as dt

    src = __import__("inspect").getsource(dt)
    assert "len(zh) // 2" not in src, "字符中点硬切仍在"


# ── A1 / T12：原词一个不少、顺序不变 ──────────────────────────

def test_a1_all_translation_text_preserved_in_order():
    from backend.core.display_timeline import build_display_timeline

    words = [_w("One.", 0.0, 3.0), _w("Two.", 3.0, 6.0), _w("Three.", 6.0, 9.0)]
    seg = _Seg(id=1, start=0.0, end=9.0, zh="一。二。三。", text="One. Two. Three.",
               words=words)
    entries = build_display_timeline([seg])
    joined = "".join(e.text.replace("\n", "") for e in entries)
    for ch in "一二三":
        assert ch in joined, f"译文丢了「{ch}」"
    assert joined.index("一") < joined.index("二") < joined.index("三"), "译文乱序"


# ── A11 / A12 / T38：结构检查与可读错误 ───────────────────────

def test_a11_structure_check_rejects_overlap():
    from backend.core.display_timeline import (
        DisplayEntry, assert_timeline_valid, DisplayTimelineError,
    )

    entries = [
        DisplayEntry(start=0.0, end=5.0, text="甲"),
        DisplayEntry(start=4.0, end=9.0, text="乙"),  # 与前一条重叠
    ]
    with pytest.raises(DisplayTimelineError):
        assert_timeline_valid(entries)


def test_a11_structure_check_rejects_zero_duration():
    from backend.core.display_timeline import (
        DisplayEntry, assert_timeline_valid, DisplayTimelineError,
    )

    with pytest.raises(DisplayTimelineError):
        assert_timeline_valid([DisplayEntry(start=3.0, end=3.0, text="甲")])


def test_a12_error_message_carries_position_not_subtitle_text():
    """⑤ 走查偏差 4：错误必须带条目序号与时间点；⑥ 安全：不得带字幕正文。"""
    from backend.core.display_timeline import (
        DisplayEntry, assert_timeline_valid, DisplayTimelineError,
    )

    secret = "这是一句不该出现在错误消息里的字幕正文"
    entries = [
        DisplayEntry(start=0.0, end=5.0, text="甲"),
        DisplayEntry(start=4.0, end=9.0, text=secret),
    ]
    with pytest.raises(DisplayTimelineError) as exc:
        assert_timeline_valid(entries)
    msg = str(exc.value)
    assert "2" in msg, f"错误消息缺少条目序号: {msg}"
    assert "4" in msg, f"错误消息缺少时间点: {msg}"
    assert secret not in msg, "错误消息泄露了字幕正文"


def test_a11_srt_rounding_zero_is_rejected():
    """T28：相邻间隔 0.0004 秒，按 SRT 的 1ms 取整后归零。"""
    from backend.core.display_timeline import (
        DisplayEntry, assert_timeline_valid, DisplayTimelineError,
    )

    entries = [DisplayEntry(start=1.0, end=1.0004, text="甲")]
    with pytest.raises(DisplayTimelineError):
        assert_timeline_valid(entries, srt=True)


def test_a11_rounding_check_catches_sub_quantum_entries():
    """取整后归零的条目由取整检查拦下（原名 `test_a11_rounding_checks_are_subsumed_by_the_bounds_check`）。

    **产品规则变更，用户 2026-09-16 确认（P468 / 锁定方案 A4、C4）**：0.84 秒是补救目标，
    不再是写出门槛——补救用尽仍偏短、但时长为正的屏照常交付。于是原来「亚毫秒条目先被
    0.84 下限拦住、取整检查够不着」的事实不再成立：现在由取整检查（SRT 1ms / ASS 10ms）
    把「取整后时长为 0」拦下，这正是它被保留的理由。

    另一半事实不变：四舍五入是单调的，原始 `前条end <= 后条start` 必然推出
    `round(前条end) <= round(后条start)`——**取整不可能制造出原本不存在的重叠。**
    """
    from backend.core.display_timeline import (
        DisplayEntry, assert_timeline_valid, DisplayTimelineError,
    )

    # 亚毫秒时长：取整后为 0，无法交付
    with pytest.raises(DisplayTimelineError, match="显示时长为 0"):
        assert_timeline_valid([DisplayEntry(start=1.0, end=1.0004, text="甲")], srt=True)

    # 合法条目：不管查不查 ASS 精度，结论都一样
    ok = [
        DisplayEntry(start=0.0, end=1.0, text="甲"),
        DisplayEntry(start=1.004, end=2.0, text="乙"),
    ]
    assert_timeline_valid(ok, srt=True)
    assert_timeline_valid(ok, srt=True, ass=True)


# ── A16：两份 20 字常量合并为单一来源 ────────────────────────

def test_a16_single_source_of_max_zh_chars():
    """20 字阈值只能有一份。

    原来两份：`sentence_splitter._MAX_ZH_CHARS`（超 20 字拆成多条）与
    `subtitler` 的 `max_chars=20`（超 20 字换行）。改一处漏一处，正是
    项目在 P368/P353/P367/P352/P405 吃过五次的那个形状。
    G5-1 后换行搬进 display_timeline，写出器不再需要这个阈值。"""
    import inspect
    from backend.core import display_timeline, sentence_splitter, subtitler

    assert sentence_splitter._MAX_ZH_CHARS is display_timeline.MAX_ZH_CHARS
    assert "20" not in inspect.getsource(subtitler.build_srt), \
        "写出器不该再持有自己的字数阈值"


# ── A21：word_align_segments 并入统一映射，不留两份实现 ────────

def test_a21_no_second_mapping_implementation():
    from backend.core import sentence_splitter

    assert not hasattr(sentence_splitter, "word_align_segments"), \
        "word_align_segments 仍在，与统一映射构成两份实现"


# ── A10 / T39：时间表只生成一次 ─────────────────────────────

def test_a10_timeline_built_once_for_srt_and_ass(tmp_path, monkeypatch):
    """三个写出器消费同一张表，不得各自重算。"""
    from backend.core import display_timeline as dt

    calls = []
    real = dt.build_display_timeline

    def counting(segments, **kw):
        calls.append(1)
        return real(segments, **kw)

    monkeypatch.setattr(dt, "build_display_timeline", counting)

    import backend.worker as worker
    monkeypatch.setattr(worker, "build_display_timeline", counting, raising=False)

    segs = [_Seg(id=1, start=0.0, end=3.0, zh="你好。", text="Hi.",
                 words=[_w("Hi.", 0.0, 3.0)])]
    worker._step_subtitle(
        segs, None, None,
        str(tmp_path / "o.srt"), str(tmp_path / "o.ass"),
        True, "yes", None, 1920, 1080,
    )
    assert len(calls) == 1, f"时间表被生成了 {len(calls)} 次，应为 1 次"


def test_a10_writers_do_not_recompute_entries():
    """写出器内部不得再调用 _collect_entries（或其继任者）。"""
    import inspect
    from backend.core import subtitler
    from backend import subtitle_style

    for mod in (subtitler, subtitle_style):
        src = inspect.getsource(mod)
        assert "_collect_entries(" not in src, \
            f"{mod.__name__} 仍在自行收集条目"


# ── A4 第 2 步：向后延长被挡住时必须合并 ─────────────────────────
# 这两条是端到端实测抓出来的缺口：首版只实现了「延长」和「报告」，
# 漏了中间的「合并」，导致英文 26 条 / 日文 18 条真实字幕仍低于 0.84 秒
# （最短仍是 P402 原样的 0.080 秒）。单元测试当时全绿。

def test_a4_merge_when_extension_is_blocked():
    """背靠背的短字幕延不动，必须合并——否则 0.84 秒线形同虚设。"""
    from backend.core.display_timeline import build_display_timeline, MIN_DISPLAY_S

    segs = [
        _Seg(id=1, start=10.00, end=10.08, zh="哦。", text="Oh.",
             words=[_w("Oh.", 10.00, 10.08)]),
        _Seg(id=2, start=10.08, end=10.30, zh="好。", text="OK.",
             words=[_w("OK.", 10.08, 10.30)]),
    ]
    entries = build_display_timeline(segs)
    for e in entries:
        assert e.end - e.start >= MIN_DISPLAY_S - 1e-9, \
            f"{e.end - e.start:.3f}s 仍低于 0.84 秒，合并没生效"
    joined = "".join(e.text for e in entries)
    # G12 第三批 B 收尾：成品按奈飞规范去掉句号（P471），比对正文时去掉空白与句号。
    assert "".join(ch for ch in joined if not ch.isspace() and ch != "。") == "哦好", "合并丢了译文"


def test_a4_merge_never_crosses_long_silence():
    """合并不得跨越长静音——那正是 P401 要消灭的形状。"""
    from backend.core.display_timeline import build_display_timeline

    segs = [
        _Seg(id=1, start=10.0, end=10.08, zh="哦。", text="Oh.",
             words=[_w("Oh.", 10.0, 10.08)]),
        _Seg(id=2, start=30.0, end=30.08, zh="好。", text="OK.",
             words=[_w("OK.", 30.0, 30.08)]),
    ]
    entries = build_display_timeline(segs)
    assert len(entries) == 2, "隔着 20 秒静音的两条字幕被合并了"


def test_a4_merged_entry_still_capped_at_max():
    """合并必须发生在封顶之前，否则级联合并能突破 7 秒零例外。"""
    from backend.core.display_timeline import build_display_timeline, MAX_DISPLAY_S

    segs = [
        _Seg(id=i, start=t, end=t + 0.08, zh=f"{i}。", text=f"w{i}.",
             words=[_w(f"w{i}.", t, t + 0.08)])
        for i, t in enumerate([0.0, 1.9, 3.8, 5.7, 7.6, 9.5], start=1)
    ]
    entries = build_display_timeline(segs)
    for e in entries:
        assert e.end - e.start <= MAX_DISPLAY_S + 1e-9, \
            f"合并后 {e.end - e.start:.3f}s 突破了 7 秒上限"


# ── A13：质量摘要必须随终态消息返回，且不含字幕正文 ──────────────
# 这条缺口是 ⑨ Web QA 的检查点抓出来的：_step_subtitle 返回了摘要，
# 但调用点丢弃了它，终态消息里根本没有。所有单元测试当时全绿。

def test_a13_quality_summary_reaches_terminal_message():
    from backend.core.display_timeline import TimelineReport
    from backend.worker import _subtitle_quality_summary

    rep = TimelineReport()
    rep.capped_no_legal_cut.append((3, 632.89, 15.31))
    rep.below_min_display.append((7, 127.121, 0.08))
    rep.shortest_s = 0.08
    rep.single_word_over_max = 1

    summary = _subtitle_quality_summary(rep)
    assert summary, "有异常时摘要不得为空"
    assert summary["capped_no_legal_cut"]["count"] == 1
    assert 632.89 in summary["capped_no_legal_cut"]["at"]
    assert summary["below_min_display"]["shortest_s"] == 0.08
    assert summary["single_word_over_max"] == 1


def test_a13_summary_is_none_when_nothing_to_report():
    """全部正常时不给用户看一堆全零字段。"""
    from backend.core.display_timeline import TimelineReport
    from backend.worker import _subtitle_quality_summary

    assert _subtitle_quality_summary(TimelineReport()) is None
    assert _subtitle_quality_summary(None) is None


def test_a13_summary_never_leaks_subtitle_text():
    """⑥ 安全审查：摘要只含条数、序号、时间点，不含字幕正文。"""
    import json
    from backend.core.display_timeline import TimelineReport
    from backend.worker import _subtitle_quality_summary

    rep = TimelineReport()
    rep.capped_no_legal_cut.append((1, 10.0, 20.0))
    rep.below_min_display.append((2, 20.0, 0.1))
    blob = json.dumps(_subtitle_quality_summary(rep), ensure_ascii=False)
    assert not any("一" <= ch <= "鿿" for ch in blob), \
        f"摘要里出现了中文，可能泄露字幕正文: {blob}"


# ── 运行时守住 7.00 这条产品硬线，并拒绝错误类型 ─────────────────
# 外审发现：assert_timeline_valid 原来只查结构，不查数字。
# 旧的 Segment 恰好也有 .start/.end/.text，结构合法 → 写出器会一声不吭地
# 写出一份超过 7 秒的字幕。外审五分钟内就误传了一次。
# G12 第三批 B 收尾（P468）：0.84 秒不再是写出门槛，见下面第二条。

def test_writer_rejects_wrong_entry_type():
    from backend.core.display_timeline import assert_timeline_valid, DisplayTimelineError
    from backend.transcriber_v2 import Segment

    with pytest.raises(DisplayTimelineError, match="不是最终显示条目"):
        assert_timeline_valid([Segment(1, 3.12, 5.0, "x")], srt=True)


def test_writer_accepts_short_but_positive_entry():
    """原名 `test_writer_rejects_below_min_display`。

    **产品规则变更，用户 2026-09-16 确认（P468 / 锁定方案 A4、C4）**：0.84 秒是补救目标，
    补救用尽后仍不足、时长为正、文字不空的屏照常交付（并记进 below_min_display、界面提示）。
    以前在这里整份报错，一屏 0.5 秒就让用户什么都拿不到。"""
    from backend.core.display_timeline import DisplayEntry, assert_timeline_valid

    assert_timeline_valid([DisplayEntry(start=3.12, end=3.92, text="x")], srt=True, ass=True)


def test_writer_rejects_above_max_display():
    from backend.core.display_timeline import (
        DisplayEntry, assert_timeline_valid, DisplayTimelineError,
    )
    with pytest.raises(DisplayTimelineError, match="超过最长"):
        assert_timeline_valid([DisplayEntry(start=11.28, end=18.64, text="x")], srt=True)
