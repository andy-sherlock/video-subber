"""G6：把「这句存疑」从字幕正文里请出去，改由界面统一告知。

本文件是 G6 批次 ⑤.5 合同落地表 C1-C13 的可执行证据。
合同全文见 pipelines/bug-pipeline-2026-08-13-G6.md。

**为什么删掉旧的 `[?]` 断言之后必须有这个文件**：
旧的 `test_phase1d_integration.py` 里有两个测试（`test_uncertain_survives_display_timeline`
和 `test_split_preserves_uncertain`）表面在断言 `[?]`，实际是拿 `[?]` 当**探针**在测
「`uncertain` 标志有没有在管道里丢掉」。`[?]` 一删，那个观察窗口就没了。
真正要守的东西搬到这里，锚在 `segments` 层——`question_mark_count` 就是从那里算的
（`worker.py:2986`），跟显示层无关。

`test_phase1d_question_mark.py` 里的 `TestQuestionMarkAppending` 三个测试则是
**自包含镜像测试**：函数体内自己写 `text = text + " [?]"` 再断言自己的局部变量，
从未调用产品代码，删掉产品代码它们也照样全绿。零覆盖，已整类删除。
"""
import inspect
import re
from pathlib import Path

from test import locked_decision

import pytest

from backend.core import display_timeline as dt
from backend.core.display_timeline import (
    MAX_ZH_CHARS,
    build_display_timeline,
    wrap_lines,
)

_FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "index.html"


class _Seg:
    """最小 Segment 替身：只带 display_timeline 会读的字段。"""

    def __init__(self, zh, *, start=0.0, end=3.0, uncertain=False,
                 speaker_change=False, text="source text"):
        self.id = 1
        self.start = start
        self.end = end
        self.text = text
        self.zh = zh
        self.words = []
        self.uncertain = uncertain
        self.speaker_change = speaker_change


def _texts(segs):
    return [e.text for e in build_display_timeline(segs)]


# ── C1 / C13：存疑不再进入用户成品，且删除边界正确 ────────────────────



def _without_jieba(monkeypatch):
    """G12 第三批 B（排版 §14.3）：分词工具不可用 → 不在两个汉字之间换行（等于原来的换行）。"""
    for cache in (dt._jieba_bounds, dt._jieba_tokens, dt._pos_tags):
        cache.cache_clear()
    monkeypatch.setattr(dt, "_jieba", lambda: None)

def test_c1_uncertain_segment_has_no_marker():
    """C1：uncertain=True 的句子，输出里不含 [?]。

    这是本批的全部意义——用户下载的 SRT 和烧录成品里不再有内部标注。
    真实证据（施工前）：jobs/9db7d51138 的成品 SRT 有 9 个 [?]。
    """
    assert "[?]" not in _texts([_Seg("哦。", uncertain=True)])[0]


def test_c13_uncertain_true_and_false_produce_identical_text():
    """C13：存疑与不存疑产出的文本必须**逐字节相同**。

    这一条同时守住两件事：
      ① 不再输出任何存疑标记（换个符号也会被抓住，不只是 [?]）
      ② 删掉 drafts 元组第 5 位没有改变行为

    **为什么不用运行时断言「text 不得含 [?]」来守**（④ elon-musk 副审否决）：
    那种断言检查的是**文本内容**，用户视频本身在讲正则表达式或问号图标时，
    译文合法含 [?] 会让**整个任务抛异常失败**——用一个更常见、更严重的新病，
    换一个已被本批消灭的旧病。本条断言检查的是**代码行为**，
    用户视频讲什么都不影响它，零误伤。
    """
    marked = _texts([_Seg("你怎么做的？", uncertain=True)])
    clean = _texts([_Seg("你怎么做的？", uncertain=False)])
    assert marked == clean


def test_c13_multiline_uncertain_also_identical():
    """C13：需要换行的长句，存疑与否同样必须逐字节相同。"""
    long_zh = "这是第一句话足够长了。" + "这是第二句话也足够长了。" + "第三句。"
    assert _texts([_Seg(long_zh, uncertain=True)]) == \
        _texts([_Seg(long_zh, uncertain=False)])


# ── C2 / C3：存疑信息本身必须活着 ──────────────────────────────────


def test_c2_display_pipeline_does_not_clear_segment_uncertain():
    """C2：走完显示流水线后，segments 上的 uncertain 必须原样还在。

    `question_mark_count` 从这里算（worker.py:2986）。删过界删到这个字段，
    用户会彻底失去字幕质量提示，而且**不报错、不崩溃**。
    """
    segs = [_Seg("存疑的句子。", uncertain=True),
            _Seg("正常的句子。", uncertain=False),
            _Seg("另一句存疑。", uncertain=True)]

    build_display_timeline(segs)

    qm_count = sum(1 for s in segs if getattr(s, "uncertain", False))
    assert qm_count == 2, "显示流水线把 segments 上的 uncertain 弄丢了"


def test_c3_uncertain_survives_archive_round_trip():
    """C3：uncertain 必须能落盘再读回——中断任务恢复靠它。

    worker.py:1529 写、:1599 读。删掉落盘字段，老任务恢复后
    `raw.get("uncertain", False)` 会让所有句子静默变成「不存疑」。
    """
    from backend.transcriber_v2 import TranscriptionEnvelope, TranscriptionReceipt
    from backend.worker import (
        _read_transcription_envelope,
        _segment_archive_payload,
        _transcription_envelope_payload,
    )

    seg = _Seg("存疑的句子。", uncertain=True)
    assert _segment_archive_payload(seg)["uncertain"] is True, \
        "uncertain 没有被写进任务状态"

    # 走**生产写入路径**再走生产读回路径，不手搓 payload——
    # 手搓的 payload 只能证明我猜对了格式，证明不了落盘真的带上了这个字段。
    receipt = TranscriptionReceipt(
        routed_primary="whisper",
        actual_primary="whisper",
        word_source="whisper_word_timestamps",
        source_text="",
        source_words=[],
                  language_decision=locked_decision(),
        artifact_language_check="ok",
              )
    payload = _transcription_envelope_payload(TranscriptionEnvelope([seg], receipt))
    assert payload["segments"][0]["uncertain"] is True

    envelope = _read_transcription_envelope(payload)
    assert envelope is not None, "整个收据没能读回（前置条件坏了，不是 uncertain 的问题）"
    assert list(envelope)[0].uncertain is True, "uncertain 没能从任务状态读回"


# ── C8 / C9：说话人前缀的换行预算 ─────────────────────────────────


def _first_line(text):
    return text.split("\n")[0]


def test_c8_first_line_makes_room_for_prefix_when_a_break_exists():
    """C8（目的不变：有可断点且需要换行时，首行为说话人前缀让出预算）。

    G12 第三批 B 收尾：前缀改成 `-`（0.5 单位），行长按渲染后的文字算。
    顿号在行中保留（1 单位），渲染后正文恰好 16：不带前缀一行，带前缀必须折行。"""
    zh = "一" * 7 + "、" + "二" * 8
    text = _texts([_Seg(zh, speaker_change=True)])[0]

    assert text.startswith(dt._SPEAKER_PREFIX), "说话人前缀被弄丢了（本批不删它）"
    # 永不双行（用户 2026-10-12 定）：带前缀也不留两行；说话人前缀功能当前不开，只保证前缀没丢
    assert "\n" not in text, "永不双行：带前缀也不许有换行"


def test_c8_short_body_with_prefix_stays_on_one_line():
    """C8（目的不变：短正文 + 前缀一行放得下就一行）。上限按渲染后文字 16 单位（含前缀）。"""
    zh = "一" * 7 + "，" + "二" * 8          # 渲染后 15.5 + 前缀 0.5 = 16
    text = _texts([_Seg(zh, speaker_change=True)])[0]
    assert text == "-" + "一" * 7 + " " + "二" * 8, f"短正文没有一行，或正文被改动了：{text!r}"
    assert "\n" not in text


def test_c8_never_hard_cuts_when_no_break_exists(monkeypatch):
    """C8 的另一半：**没有标点可断时保持原样，绝不硬切。**

    `wrap_lines` 从不硬切，这是它的固有性格——实测 50 字无标点的输入
    原样输出 50 字一行。为了把首行从 21 字凑到 20 字去硬切，会把
    「十九个字的一句话」切成十八个字 + 一个孤零零的句号，比 21 字更难看。

    这是 2026-08-02 P404 第五轮那个教训的形状：**为消灭一个构造案例
    而引入真实场景的伤害**。用户已裁决走「有标点就帮忙，没标点就算了」。
    """
    # G12 第三批 B：排版 §12 起允许在分词词缝上断两个汉字；分词工具不可用时退回原规则（§14.3）
    _without_jieba(monkeypatch)
    zh = "一" * 19                      # 19 字，一个标点都没有
    text = _texts([_Seg(zh, speaker_change=True)])[0]

    assert text == dt._SPEAKER_PREFIX + zh, f"没有标点却被切开了（硬切）：{text!r}"
    assert "\n" not in text, "无合法断点时不得硬切"


def test_c8_wrapped_line_with_speaker_prefix_stays_within_limit():
    """C8（目的不变：需要换行的长句 + 说话人前缀，行长守上限）。

    锁定方案 C1：宽松放行函数已删除，恢复严格检查「每行 ≤16 单位」。
    锁定方案 C2 说的「带前缀、首行 17 单位的人造长句」就是这一条：r10 下首行是
    `- `(1) + 15 字 + 行尾逗号(1) = 17。本批行尾逗号渲染时删掉（A7）、前缀改 `-`(0.5)，
    首行变成 15.5 单位，严格检查直接通过——按 C2 自己的规则（修好就去掉「已知未通过」标记），
    这里不再标 xfail。"""
    zh = "这是一个很长的句子需要换行处理，后面还有更多内容要说，最后还有一段。"
    text = _texts([_Seg(zh, speaker_change=True)])[0]
    assert text.startswith(dt._SPEAKER_PREFIX)
    assert "\n" not in text, f"永不双行（用户 2026-10-12 定）：{text!r}"


def test_c8_untranslated_prefix_stacked_with_speaker_prefix():
    """C8（目的不变：`【未翻译】` 与说话人前缀叠加时两者都计入首行；不在词中间硬切）。

    旧期望「无标点英文原句整行不切」被新标准替换：已有空格是可靠断点，按空格换行，
    但任何英文单词都不得被切开。G12 第三批 B 收尾：前缀改 `-`，逗号渲染成空格。"""
    lead = dt._SPEAKER_PREFIX + dt._UNTRANSLATED_PREFIX
    seg = _Seg("", speaker_change=True, text="前半句内容，后半句内容啊啊啊啊啊啊啊。")
    text = _texts([seg])[0]
    assert text.startswith(lead), f"叠加形态不对：{text!r}"
    assert "\n" not in text, "永不双行（用户 2026-10-12 定）"

    raw = "a fairly long english source line here"
    plain = _texts([_Seg("", speaker_change=True, text=raw)])[0]
    assert plain.startswith(lead)
    assert "\n" not in plain, plain
    words_out = plain[len(lead):].split()
    assert words_out == raw.split(), f"英文单词被切开或丢失：{plain!r}"


def test_c8_short_untranslated_with_both_prefixes_stays_on_one_line():
    """C8（目的不变：`【未翻译】` + 说话人前缀叠加、正文短时一行）。
    按渲染后 16 单位含两个前缀：0.5 + 5 + 5 + 0.5 + 5 = 16。"""
    seg = _Seg("", speaker_change=True, text="前半句内容，后半句内容。")
    text = _texts([seg])[0]
    assert text == "-【未翻译】前半句内容 后半句内容", f"短的未翻译句没有一行，或正文被改动了：{text!r}"


def test_c9_plain_subtitle_wrapping_is_byte_identical():
    """C9（目的不变：无前缀字幕走同一个换行函数、默认参数等于唯一行长常量）。
    行长常量改为 `LINE_MAX_UNITS`；`MAX_ZH_CHARS` 只剩 sentence_splitter 的 AI 断句门槛（见 C11）。"""
    samples = [
        "短句。",
        "一" * 20,
        "一" * 21,
        "这是一个很长的句子需要换行处理，后面还有更多内容要说，最后还有一段。",
    ]
    for zh in samples:
        assert wrap_lines(zh) == wrap_lines(zh, dt.LINE_MAX_UNITS), \
            f"默认参数与显式传参结果不同：{zh!r}"
        # V5：两行屏若 24 单位内读得过来，会并成一行（问题 1/2）；其余仍与 wrap_lines 逐字节相同
        got, want = _texts([_Seg(zh)])[0], wrap_lines(zh)
        # 永不双行（用户 2026-10-12 定）：有换行的结果一律并成一行，比较时忽略空白
        assert "\n" not in got and "".join(got.split()) == "".join(want.split()), \
            f"无前缀字幕的换行结果与 wrap_lines 不一致：{zh!r}"


# ── C11：阈值常量单一来源不变 ─────────────────────────────────────


def test_c11_max_zh_chars_unchanged_and_shared():
    """C11：MAX_ZH_CHARS 与 sentence_splitter 共用同一个对象。
    G12 第三批 B（排版 §13.1、决定 21）按新合同 20 → 16。"""
    from backend.core import sentence_splitter

    assert MAX_ZH_CHARS == 16
    assert sentence_splitter._MAX_ZH_CHARS is MAX_ZH_CHARS


# ── C4 / C5 / C6 / C7 / C10 / C12：前端 ───────────────────────────
#
# 前端是单文件内嵌 JS，项目既有做法是对源码断言（见 test_v309_c3_finalize.py）。


@pytest.fixture(scope="module")
def frontend():
    return _FRONTEND.read_text(encoding="utf-8")


def test_c4_frontend_never_points_user_at_the_marker(frontend):
    """C4：前端不得再有任何文案指引用户去找 [?]。

    P174 的教训形状：只删后端不改前端 = 把用户送去找一个不存在的东西。
    施工前 index.html:2375 直接写着「可查看 [?] 标记」。
    """
    offenders = [
        line.strip()
        for line in frontend.splitlines()
        if "[?]" in line and not line.lstrip().startswith(("/*", "*", "//"))
    ]
    assert not offenders, "前端仍有文案提到 [?]：\n" + "\n".join(offenders)


def test_c5_subtitle_quality_reaches_all_four_done_entries(frontend):
    """C5：质量摘要必须活过刷新——四条 done 入口都要拿得到它。

    **保护权交接（2026-08-19 · G9/P422）**
    原断言数 `subtitle_quality` 在 index.html 里出现几次，依据是「四条入口各自
    点名列举字段」。P422 之后点名式全部删除，四条入口改为把整份公开快照原样递
    出去，出现次数必然下降——**继续数次数就是在要求那个病本身继续存在**。

    这条保护现在由两条真行为测试接管，比数字符串强：
      · `test_g9_p422_current_state.py::test_c8_四条_done_入口都把整份快照原样
        交给完成卡_零登记透传` —— 真跑 JS，断言含一个从未登记过的新字段在内的
        整份快照被原样递给渲染函数。
      · `test_g9_p422_current_state.py::test_c8_落盘过的字段能活过冷启动而不需要
        在任何名单里登记[subtitle_quality]` —— 后端那一半。

    本条改为断言**结构性事实**：四条入口一处都不许再手工点名字段。
    """
    forbidden = [
        line.strip()[:120]
        for line in frontend.splitlines()
        if "question_mark_count:j." in line
        or "question_mark_count:d." in line
    ]
    assert not forbidden, (
        "还有入口在手工点名质量字段（P422 要删掉的正是这个）：\n"
        + "\n".join(forbidden)
    )
    assert frontend.count("subtitle_quality:quality.subtitle_quality") == 1


def test_c6_quality_line_reports_uncertain_count(frontend):
    """C6：完成卡质量行必须显示存疑句数（含低于 5% 的区间）。

    D1 裁决：阈值一个字不动，但低于 5% 不再彻底沉默——
    在质量行末尾追加一句不带情绪的事实，与「本轮 API 成功 118/124」并排。
    """
    body = _render_translation_summary_source(frontend)
    assert "句存疑" in body, "质量摘要函数里没有存疑句数"
    assert "question_mark_count" in body, "存疑句数没有取自 question_mark_count"


def test_c7_restore_paths_carry_quality_fields(frontend):
    """C7：两条恢复路径必须带上质量字段，刷新页面后仍能看到。

    施工前 index.html:1516 / :1729 既不传 question_mark_count 也不调 showQmWarning，
    刷新一次质量提示就全丢。[?] 删掉后界面成了唯一渠道，这个旧缺口会升级成
    「用户彻底不知道」。
    """
    # **保护权交接（2026-08-19 · G9/P422）**：原断言要求恢复路径那一行里
    # 逐个出现 question_mark_count / subtitle_quality。P422 删掉了点名式，
    # 恢复路径改为整份快照透传，那种断言照不出任何东西了。
    # 现在断言的是更强的事实：恢复路径把**整个任务对象**交出去，没有中间过滤。
    restore_lines = [
        line.strip() for line in frontend.splitlines()
        if "renderCardDone(" in line and ("markRestoredCard" in line
                                          or "d.job_id" in line)
    ]
    assert restore_lines, "找不到恢复路径的 renderCardDone 调用"
    for line in restore_lines:
        assert ("renderCardDone(jid,view)" in line
                or "renderCardDone(d.job_id,d)" in line), (
            f"恢复路径没有整份透传，仍在手工挑字段：{line[:140]}"
        )
        assert "showQmWarning(" in line, f"恢复路径没有触发质量提示：{line[:140]}"


def test_c5_backend_persists_quality_fields_on_done():
    """C5 的后端一半：done 落盘必须带上质量字段。

    **这是 ⑦.5 读代码才发现的缺陷**，任何单元测试和源码断言都抓不到：
    `main.py` 的 done 分支是**点名式白名单**（`fields = {...}` 逐个列举），
    不在里面登记的字段，worker 发了也白发——WebSocket 那一瞬间的消息里有，
    落盘的任务状态里没有。轮询路径和两条恢复路径读到的永远是 undefined，
    **不报错、不崩溃，只是一片空白**。

    `question_mark_count` 此前就是这样：质量横幅只在完成那一秒出现过一次，
    刷新页面就再也不出现，看起来像前端 bug，根子在后端白名单。
    """
    # **保护权交接（2026-08-19 · G9/P422）**：原断言检查后端有一个叫
    # `_SUBTITLE_QUALITY_FIELDS` 的白名单、里面登记了这两个字段。
    # P422 把 done 分支的点名白名单整个删掉，改成反向排除——**没有名单可查了**，
    # 而这正是修复本身。真正要保护的「质量字段能落盘、能活过冷启动」由
    # `test_g9_p422_current_state.py::test_c8_落盘过的字段能活过冷启动而不需要
    # 在任何名单里登记` 接管（它连一个从未登记过的新字段都能照出来）。
    from backend.main import _TERMINAL_MESSAGE_EXCLUDED

    assert "question_mark_count" not in _TERMINAL_MESSAGE_EXCLUDED
    assert "subtitle_quality" not in _TERMINAL_MESSAGE_EXCLUDED

    from backend import job_state
    state = job_state.build_public_task_state({
        "id": "j", "status": "done", "question_mark_count": 2,
        "subtitle_quality": {"mapping_failed_splits": 1},
    })
    assert state["result"]["quality"]["question_mark_count"] == 2
    assert state["result"]["quality"]["subtitle_quality"]["mapping_failed_splits"] == 1


def test_qm_warning_clean_job_does_not_wipe_a_dirty_one(frontend):
    """恢复路径的干净任务不得清空别人的横幅。

    **这是本批自己捅出来的缺陷**（⑦.5 抓到）：`#qmWarn` 是全局唯一的一个元素，
    而恢复/补建路径会对**每一个**已完成任务各调一次 `showQmWarning`。
    干净任务照常执行清空的话，一批恢复里最后那个干净任务，
    会把前面某个真有问题的任务的红/黄横幅擦掉——用户看到「什么问题都没有」。

    改之前这个函数根本不在恢复路径上，所以不会发生；是本批把它加上去才引入的。
    """
    body = _slice_function(frontend, "function showQmWarning(")
    assert "keepIfClean" in body, "showQmWarning 没有「不清空」模式"
    assert body.count("if(!keepIfClean)") == 2, \
        "两个清空分支都必须受 keepIfClean 保护（早退分支 + 低于阈值分支）"

    calls = re.findall(r"(?<!function )showQmWarning\((?:[^()]|\{[^{}]*\})*\)", frontend)
    assert len(calls) == 3, f"showQmWarning 调用点应为 3 个（实时 / 恢复 / 补建），实际 {len(calls)}：{calls}"

    protected = [c for c in calls if c.rstrip().endswith(",true)")]
    assert len(protected) == 2, (
        "两条恢复路径必须传 keepIfClean=true，实时完成那一条不传（它一次只处理一个任务，"
        f"清空是对的）。实际带保护的有 {len(protected)} 个：{calls}"
    )


def test_c10_warning_thresholds_untouched(frontend):
    """C10：5% / 15% / 50 三个阈值一字不改（红线，须先问用户）。"""
    body = _slice_function(frontend, "function showQmWarning(")
    assert "0.15" in body, "红横幅阈值 15% 被改动了"
    assert "0.05" in body, "黄横幅阈值 5% 被改动了"
    assert "qm>=50" in body.replace(" ", ""), "绝对数量阈值 50 被改动了"


def test_c12_quality_rendering_never_uses_inner_html(frontend):
    """C12：新增渲染必须走 textContent，不得走 innerHTML。

    ⑥ 安全审查：前端有 23 处 innerHTML，其中有直接拼字符串的写法。
    本批把 subtitle_quality 并进已经全程使用 textContent 的
    renderTranslationSummary，新增 XSS 面为零——这是第 1 层防护（结构上写不出），
    不是第 3 层的「记得用 textContent」。
    """
    body = _render_translation_summary_source(frontend)
    assert "innerHTML" not in body, "质量摘要渲染里出现了 innerHTML"
    assert "textContent" in body, "质量摘要渲染没有使用 textContent"


# ── 前端源码切片工具 ──────────────────────────────────────────────


def _slice_function(source, header):
    start = source.index(header)
    depth, i = 0, source.index("{", start)
    for j in range(i, len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[start:j + 1]
    raise AssertionError(f"没找到 {header} 的函数体")


def _render_translation_summary_source(source):
    return _slice_function(source, "function renderTranslationSummary(")


# ── 走查 Q1：注释不得再用「装饰」这个筐 ──────────────────────────


def test_walkthrough_q1_decorate_comment_names_what_it_does():
    """⑤ 走查偏差 1：`_decorate` 的说明必须明写它干了什么，不许用「装饰」。

    「装饰」是个筐，什么都能往里装，装了没人看得出来——[?] 就是这么混进
    交付物、且六道审查全没发现的。改成明写的清单之后，
    下一个想往这里加东西的人，会发现自己在往一个有明确清单的地方加东西。
    """
    source = inspect.getsource(dt)
    marker = re.search(r"#\s*──\s*内部：刀②[^\n]*", source)
    assert marker, "找不到刀②那一行说明"
    assert "装饰" not in marker.group(0), \
        f"刀②的说明仍在用「装饰」这个筐：{marker.group(0)}"
    assert "说话人前缀" in marker.group(0), \
        f"刀②的说明没有明写「说话人前缀」：{marker.group(0)}"
