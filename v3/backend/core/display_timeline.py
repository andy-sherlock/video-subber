"""最终显示时间表 —— 字幕排版与时间的唯一作者（G5-1）。

要解决什么
----------
在这个模块出现之前，一条字幕的显示时间有五个作者，谁都能顺手改：
``sentence_splitter`` 会按译文字数凭空分配时间、会把时间压到两位小数；
``subtitler`` 的写出器会把后一条的开始时间往后推、会把空译文截到 6 秒。
四个 bug（P401 挂太久 / P402 一闪而过 / P414 零时长 / P415 上限失效）
的共同来源就是这个——**没有任何一个地方在最后一刻问过「这条字幕人眼能看吗」**。

本模块把那个位置补上，并且只有它一个。

流程（G12 第三批 B 收尾定稿）
----------------------------
    翻译后 segments（zh 可能缺句末标点）
      │
      ├─ 刀①  AI 断句            既有，在 sentence_splitter（P470：只删了标点时正文取原文）
      │
      ▼
    build_display_timeline()
      ├─ 内部句号（P469）        中文缺句末标点、英文原文表明已说完 → 只在内存里补“。”
      ├─ 刀③  拆屏               句末一定拆；逗号处只在两行放不下或超 7 秒时拆
      │                          译文切点 → 附近真实原词边界 → 验证合法才切（R-11）
      │                          不许切出「完整句 + 另一句半截」（P466）
      ├─ 时间修复               收紧重叠 → 空档补到 0.84 → 合并短屏（不拼坏句子）
      │                          → 合并后重拆 → 7 秒封顶 → 补阅读时间 → 向下一屏借时
      ├─ 混接屏记账             仍是「完整句 + 半截」的屏进质量摘要（U5）
      └─ 刀②  最终排版          换行（16 单位）+ 标点渲染（P471）+ 说话人前缀，见 `_decorate`
      │
      ▼
    list[DisplayEntry]  ← frozen，造出来就改不动
      │
      ├─→ build_srt          只序列化，各自取整
      ├─→ build_ass          只序列化（生产不再调用，只剩测试在用）
      └─→ build_ass_styled   只序列化（所有烧录都走它）

铁律
----
* 原词、词序、词级时间永不改写（R-2）——本模块只读 ``seg.words``，从不回写。
* 显示时间在进入本表之前不得取整（R-15）；取整只发生在写出器序列化那一刻。
* 写出器不得修改任何时间（R-14）——``DisplayEntry`` 是 frozen 的，改了跑不起来。
* 拆屏切点必须落到真实原词边界（R-11）；证明不了就整条不拆、封顶、进质量摘要。
* **标点全程保留，只在最后渲染一次**（P471）。所有判断用带标点的内部文字；
  行长、阅读时间、所有统计一律按渲染后的文字算。补的句号不写回 ``seg.zh``。
"""
from __future__ import annotations

import copy
import functools
import logging
import re
import unicodedata
from dataclasses import dataclass, field, fields

from backend.core.sentence_groups import _is_suspicious_period

_log = logging.getLogger(__name__)

# ── 产品数值 ────────────────────────────────────────────────────
MIN_DISPLAY_S = 0.84   # 最短显示的**补救目标**；补救用尽仍不足的照常交付并记账（P468）
MAX_DISPLAY_S = 7.00   # 每屏最长 7 秒；只有一个窄例外：_long_screen_allowed 放行的屏（贴 long_ok 标签，写出器放行）
MAX_ZH_CHARS = 16      # **只剩一个用途**：sentence_splitter 判断「这句长到要不要调 AI 断句」。
                       # 它不再是换行门槛。G12 第三批 B（排版 §13.1）20 → 16：两部片实测
                       # 「两个汉字中间换行」的屏 71 → 26。
LINE_MAX_HAN = 16      # 切点梯子的一行上限：16 个汉字（用户 2026-10-12 定；排版阶段仍用下面的字宽）
LINE_MAX_UNITS = 16.0  # 每行上限，按渲染后的最终文字计数（`_reading_units`，含说话人前缀）
READING_CPS = 9.0      # 阅读速度上限（奈飞简中成人节目口径：每秒 9 单位）
READING_CPS_RESCUE = 10.0   # 切点梯子的第 4 级（最后一级）：前三级都切不开时，读速上限放宽到这个数再试一次

_SRT_QUANTUM = 0.001   # SRT 时间戳精度 1 毫秒
_ASS_QUANTUM = 0.01    # ASS 时间戳精度 10 毫秒

_LONG_SILENCE_S = 2.00   # 长静音门槛；合并不得跨越它（否则字幕会被拉过静音区）
_MERGE_JOINER = " "      # 合并两条字幕时的连接符
SPLIT_MARK = "\x1f"      # AI 断句的断点记号（P475），**只在这里定义**。观众看不到：Python 把它当空白
                         # （isspace / strip / split 都认），按空白处理的断行逻辑原样生效；`_render_line` 第一步删掉。
_SPEAKER_PREFIX = "-"    # 说话人切换前缀（奈飞：不带空格）。**贴在换行之后**，首行要为它让出预算
_UNTRANSLATED_PREFIX = "【未翻译】"
_FAILED_MARKER = "[翻译失败]"
_MAP_SEARCH_SPAN = 2     # 时间锚点最多挪几个词。挪得越远，切点与语音越不贴。

# 句末标点优先，其次逗号类。两份正则曾分散在 sentence_splitter 与 subtitler，
# 且 subtitler 那份含英文句号 `.`，会在 "3.5" 的小数点后断开（④ 人工读 #3）。
# 合并时采用不含英文句号的一份。
_SENT_END = re.compile(r"[。！？!?]")
# G12 第三批 B（排版 §15.3）：书名号《…》里面的逗号不当拆屏点（后面紧跟「》」且中间没有「《」的不算）。
_COMMA = re.compile(r"[，,；;、：:…—](?![^《]*》)")

_FULL_WIDTH_ZH_PUNCT = "—…“”‘’·"
"""「— … “ ” ‘ ’ ·」Unicode 标为宽度不定，中文字幕里按全角计 1（奈飞口径）。"""

_ZH_CLOSERS = "”’）》」』】〕］"
_TRAILING_CLOSERS = _ZH_CLOSERS + "\"')]"
_CLOSER_CLASS = re.escape(_TRAILING_CLOSERS)
_NO_LINE_START = "，,。.！!？?、；;：:…—%）)》」』】〕］]”’\"'"
_OPENERS = "“‘（(《「『【〔［["
_HAN = "㐀-鿿豈-﫿"

# P469：英文原文里的句子线索
_EN_SENTENCE_END = re.compile(r"[.?!][\"')\]]*\s*$")
_EN_WORD = re.compile(r"[A-Za-z][A-Za-z']*")
_SOFT_TAIL = "，,、；;：:"

# ── G12 第三批 B：换屏对位 + 一行优先（方案-排版换屏对位 §3、§11–§15；实验参照
#    pipelines/复查-第26与第2件/ faithful、r1_align、cap_narrow、run_oneline_pref、
#    hanbreak、断点考卷、d5_wrap、d3_threeline、a7_fix）───────────────────────────
# 改动 1：逗号处在比例位置前后各 5 个英文词里找路标（标点优先，再 ≥0.45 秒停顿；考卷实验定的数）
_ANCHOR_SPAN = 5
_ANCHOR_PAUSE_S = 0.45
_EN_ANCHOR_PUNCT = re.compile(r"[,.;:?!…—–-][\"'”’)\]]*$")
_EN_PERIOD_END = re.compile(r"[.?!][\"'”’)\]]*$")
_EN_TRAILING_PUNCT = re.compile(r"[.!?,;:…—]+[\"')\]]*$")
# §15.1：句号那一刀先按润色文的句号配；每一对的位置占比差不超过 0.25 才用
_POLISH_PAIR_TOL = 0.25
_POLISH_HAN_SKIP = re.compile(r"[\s\x1f，,。．.！!？?、；;：:…—\-“”‘’\"'（）()《》「」『』【】]")
# 改动 4（7 秒窄例外「乙」，记录 27–30）：词时间可信 且 每个逗号切下去都碎 → 不受 7 秒限制（30 秒≈不限）
_TRUST_WORD_MAX_S = 2.0      # 与 sound_map._GAP_MINIMUM 同一个数
_TRUST_GAP_MAX_S = 2.0
_NARROW_CAP_S = 30.0
_FRAGMENT_UNITS = 6
# §12 一行优先（交接-第28件 第 2 节；决定 3）：可拆可不拆的那一刀，每块 ≥1 秒、≤7 字/秒
_PREF_PIECE_MIN_S = 1.0
_PREF_PIECE_MAX_CPS = 7.0
_PREF_GUARD_ROUNDS = 8       # 新出现短屏 / 快屏就撤回邻屏那一刀，最多重排 8 轮
_FILLER_MAX_UNITS = 4        # ≤4 字（语气词、短回应）才按现行做法并进邻句（决定 4「语气词 A」）
# 规则 C / D：两个汉字之间换行——只在分词工具切出的词缝上；虚词不和它抓住的词分开；新短语开头优先
_NO_BREAK_AFTER = ("给", "把", "跟", "在", "被", "对", "向", "往", "从", "为", "让", "和", "与", "比",
                   "能", "会", "要", "想", "可以", "应该", "很", "最", "太", "非常",
                   "这", "那", "这个", "那个")
_NO_BREAK_BEFORE = frozenset("的了着过们吗呢吧啊呀得地")
_PHRASE_HEAD_TAGS = ("p", "d", "c", "v", "t", "vn", "ad")
# §13.2 三行屏：从已有的换行里挑一处拆成两屏（AI 记号 > 标点 > 汉字中间；插入④ C3′ 与切点梯子同一顺序）
_THREE_LINE_SKIP = _POLISH_HAN_SKIP   # 同一套「不算字」的符号
_THREE_LINE_RANK = {"AI标记": 0, "标点": 1, "汉字中间": 2, "其他": 3}
_TITLE_OPENERS = frozenset("《「『（(“‘【")


class DisplayTimelineError(ValueError):
    """时间表结构无效且无法修复时抛出。

    消息必须带条目序号与时间点，**不得带字幕正文**（⑥ 安全审查）。
    """


@dataclass(frozen=True)
class DisplayEntry:
    """一条最终显示条目。

    frozen=True 不是装饰——它是 R-14「写出器不得修改任何时间」的落地方式。
    下游想改 start/end 会当场抛 FrozenInstanceError，而不是几个月后由用户发现。
    """

    start: float
    end: float
    text: str
    long_ok: bool = False
    """G12 第三批 B（改动 4）：7 秒窄例外放行的屏。只有这种屏允许超过 7 秒，自检认它。"""


@dataclass(frozen=True)
class _Rules:
    """这一趟排版用哪一套规则。

    ``pref=False``：第一部分（改动 1–4）的基准排版——只给「一行优先」的守卫循环做对照，
    看新排版里哪些短屏、快屏是**新出现**的。``block``：守卫循环拉黑的屏开始时间，
    落在这些时间的那一段不做「一行优先」的拆分。
    """

    pref: bool = True
    block: frozenset = frozenset()


_RULES = _Rules()
"""当前这一趟的规则。只在 ``build_display_timeline`` 里切换，结束时恢复。"""


@dataclass
class TimelineReport:
    """质量摘要素材。只含条数、序号、时间点，**不含字幕正文**。"""

    below_min_display: list[tuple[int, float, float]] = field(default_factory=list)
    """(条目序号, 开始时间, 时长) —— 补救用尽仍不足 0.84 秒、照常交付的屏（P468）。"""

    shortest_s: float | None = None
    mapping_failed_splits: int = 0
    no_safe_boundary_splits: int = 0
    single_word_over_max: int = 0
    over_two_lines: list[tuple[int, float, int]] = field(default_factory=list)
    """(条目序号, 开始时间, 实际行数) —— 渲染后仍超过两行的条目。

    合同 A3 的兜底：拆屏尽力了，但 R-11 禁止硬切——真找不到合法原词边界时
    整条不拆，于是仍可能折出三行。**这些条目照常交付，但不得静默交付。**"""

    capped_no_legal_cut: list[tuple[int, float, float]] = field(default_factory=list)
    """(条目序号, 开始时间, 原始跨度) —— 切不动且被 7 秒封顶的条目。

    合同 A9：这些条目照常交付，但必须在质量摘要中可见——
    字幕在 7 秒时消失，剩下的话没有字幕。静默交付是不允许的。"""

    half_sentence_screens: list[tuple[int, float]] = field(default_factory=list)
    """(条目序号, 开始时间) —— 屏里有一句说完的话、又挂着另一句的半截，但找不到合法切点（U5）。

    拆、合、重拆都不会**造出**这种屏；它只会原样留下来（原词时间重叠等切不动的情况）。
    以前整条交付、一个数都不记。"""

    ai_cut_sources: dict = field(default_factory=dict)
    """插入④ 施工 B R8：AI 每一刀的时间从哪来 —— 原词 / 润色换回 / 程序猜，以及整句退回的句数。"""

    unsplit_wide_screens: list[tuple[float, int, float]] = field(default_factory=list)
    """(开始秒, 汉字数, 每秒汉字) —— 切点梯子试过、所有级都切不开，保持一行的屏（汉字口径，2026-10-13 起；
    之前是（宽, 读速）的字宽口径）。这些屏汉字>16 或每秒汉字>9，照常交付，但不得静默交付。"""


# ── 对外：唯一入口 ──────────────────────────────────────────────

def build_display_timeline(segments, *, report: TimelineReport | None = None):
    """把翻译后的 segments 变成唯一的最终显示时间表。

    这是整个流程里**唯一**允许决定「这条字幕从几秒显示到几秒」的地方。
    三个写出器只读它的返回值，一个字都不许改。

    G12 第三批 B（排版 §12 第 1 步第 5 条，守卫）：先按第一部分（改动 1–4）排一遍当基准；
    再按全部规则排。新排版里**新出现**的不到 0.84 秒或读得太快的屏，把它前后邻屏
    拉黑——那一段不做「一行优先」的拆分——重排，最多 8 轮。
    """
    global _RULES
    rep = report if report is not None else TimelineReport()
    saved = _RULES
    try:
        baseline, _ = _layout_pass(segments, _Rules(pref=False))
        now_short = {round(e.start, 2) for e in baseline
                     if e.end - e.start < MIN_DISPLAY_S - 1e-6}
        now_fast = {e.text for e in baseline if _entry_too_fast(e)}
        block: set = set()
        for _round in range(_PREF_GUARD_ROUNDS):
            entries, pass_rep, drafts, _ai_spans = _layout_pass_full(
                segments, _Rules(pref=True, block=frozenset(block)))
            bad = [i for i, e in enumerate(entries)
                   if (e.end - e.start < MIN_DISPLAY_S - 1e-6 and round(e.start, 2) not in now_short)
                   or (_entry_too_fast(e) and e.text not in now_fast)]
            if not bad:
                break
            for i in bad:
                for j in (i - 1, i + 1):
                    if 0 <= j < len(entries):
                        block.add(entries[j].start)
                        block.update(round(b.start, 3) for b in baseline
                                     if b.start - 0.5 <= entries[j].start <= b.end)
    finally:
        _RULES = saved
    for f in fields(TimelineReport):
        setattr(rep, f.name, getattr(pass_rep, f.name))
    # 碰到 AI 句的屏已经是一行（D17），`_rework_double_line_screens` 只改两行屏，碰不到它们
    entries = _no_double_line(_rework_double_line_screens(entries, drafts, rep))
    if rep.ai_cut_sources:
        _log.info("AI 刀定时间：%s", "，".join(f"{k} {v}" for k, v in rep.ai_cut_sources.items()))
    for i, e in enumerate(entries, 1):
        if e.long_ok:
            _log.info("排版 7 秒窄例外：第 %d 屏（%.2f 秒起）显示 %.2f 秒——词时间可信，"
                      "每个逗号切下去都会切出不足 6 字的一段", i, e.start, e.end - e.start)
    return entries


def _entry_too_fast(entry) -> bool:
    dur = entry.end - entry.start
    return dur > 0 and _reading_units(entry.text.replace("\n", "")) / dur > READING_CPS + 1e-9


# ── V5：两行屏事后处理（问题 1、2 定稿规则第 2 版）─────────────────
# 排好版之后，每个"一屏两行"的屏按下面的规则重新决定：拆成两屏（各一行），
# 或并成一行，或原样保留两行。只改两行屏；单行屏一个字不动。

_SPLIT_PUNCT = "，,、；;：:。！？!?…"
_PAUSE_SPLIT_S = 0.30      # 说话人在原换行处停顿 ≥0.30 秒，就值得拆成两屏
_ONE_LINE_MAX_UNITS = 24   # 并成一行最宽 24 单位


def _units(text: str) -> float:
    return _reading_units(_render_line(text))


def _join_breaks(text: str) -> str:
    """把换行去掉并成一行：原来换行前有空格就留一个空格；换行两边都是英文字母/数字也补一个空格
    （否则 long↵english 会粘成 longenglish）；中文之间不加空格。"""
    def fix(m):
        if m.group(0).startswith(" "):
            return " "
        before = text[m.start() - 1:m.start()]
        after = text[m.end():m.end() + 1]
        return " " if before.isascii() and before.isalnum() and after.isascii() and after.isalnum() else ""
    return re.sub(r" ?\n", fix, text).strip()


def _one_line(text: str) -> str:
    return _join_breaks(_render_line(text))


def _segment_cuts(text: str):
    """把屏文字压平（去换行），返回 (压平文字, 可切位置列表)：标点后、汉字后的空格处。"""
    flat, cuts = "", set()
    for ch in text:
        if ch == "\n":
            if flat:
                cuts.add(len(flat))
            continue
        flat += ch
        if ch == SPLIT_MARK and len(flat) - 1 in cuts:
            # 插入④ C6′：AI 记号紧贴在标点后面——和原换行（`_wrap_break_positions`）一样把这一刀记在记号后面，
            # 否则「两行屏在原换行处拆」会把同一刀当成两个位置而放弃拆
            cuts.discard(len(flat) - 1)
            cuts.add(len(flat))
        elif ch in _SPLIT_PUNCT:
            cuts.add(len(flat))
        elif ch.isspace() and len(flat) > 1 and _is_han(flat[-2]):
            cuts.add(len(flat))
    # P533：数字内部要看到右边那个字才能判，所以压平后统一排除（原换行落在「10:|25」的也一并排除）
    return flat, sorted(c for c in cuts if 0 < c < len(flat) and flat[c:].strip() and flat[:c].strip()
                        and not _cut_forbidden(flat, c))


def _original_break(text: str, flat: str):
    """程序原来的换行落在压平文字的哪个位置；没有则 None。"""
    if "\n" in text:
        p = len(text[:text.index("\n")].replace("\n", ""))
        while p < len(flat) and flat[p] == SPLIT_MARK:   # 插入④ C6′：紧贴的记号算同一刀，和 `_segment_cuts` 同一个数
            p += 1
        return p
    _lines, breaks = _wrap_break_positions(flat)
    return breaks[0][0] if breaks else None


def _split_time(start, end, flat, words, p, *, pause=False,
                min_units=None, cps=READING_CPS, best=False, han=False, long_with_mark=False):
    """在压平文字第 p 位拆成两屏，返回 (第二屏开始时间, 延后秒数)；拆不了返回 None。

    pause=False：左右各 ≥5 字。pause=True：左右各 ≥3 字，且切点处说话人停了 ≥0.30 秒
    （宁可晚、不要早：停顿词不得比按比例估的位置早 2 个词以上）。
    切点梯子用的三个参数，默认值保持原行为：``min_units`` 覆盖每块最少字数；``cps`` 读速上限；
    ``best=True`` 不是「找到第一个合格的就返回」，而是枚举全部（切点锚词 × 延后）取「两块里较快那块
    读速最低」的（平手取延后更小、第二屏开始更早的），并额外要求每屏 ≤ ``MAX_DISPLAY_S``。
    ``han=True``（切点梯子用）：块的大小、上下限、读速一律按汉字口径（`_han_count`、`_han_speed`）；
    默认 False 是旧字宽口径，排版阶段的老调用者不变。
    ``long_with_mark=True``（切点梯子 C13 用）：还带 AI 记号的那一块可以先超过 16 个汉字和 7 秒，
    由调用方接着在剩下的记号上再切。"""
    left, right = flat[:p].strip(), flat[p:].strip()
    n = len(words)
    if n < 2:                                  # 未翻译/翻译失败的屏没有词表：没有可对位的原词，不拆
        return None
    size = _han_count if han else _units
    speed = _han_speed if han else _screen_reading_speed
    def deferred(part):                       # 这一块留给下一刀切，先不按一屏的上限判
        return long_with_mark and SPLIT_MARK in part and size(part) > LINE_MAX_HAN
    if not left or not right or any(size(x) > LINE_MAX_HAN and not deferred(x) for x in (left, right)):
        return None
    if min(size(left), size(right)) < (min_units if min_units is not None else (3 if pause else 5)):
        return None
    chosen = None
    for ignore_ws in (False, True):
        base = _ratio_index(flat, p, n, ignore_whitespace=ignore_ws)
        indices = (range(max(1, base - 1), min(n - 1, base + 2) + 1) if pause
                   else _anchor_indices(words, base))
        for idx in indices:
            cut = _legal_cut(start, end, flat, words, p, idx, False, None)
            if cut is None:
                continue
            if pause and cut[3] - cut[2] < _PAUSE_SPLIT_S - 1e-9:
                continue
            for delay in (0.0, 0.1, 0.2, 0.3):
                second = cut[3] + delay
                lt, rt = second - start, end - second
                if lt >= MIN_DISPLAY_S and rt >= MIN_DISPLAY_S \
                        and speed(start, second, left) <= cps \
                        and speed(second, end, right) <= cps:
                    if not best:
                        return second, delay
                    if (lt > MAX_DISPLAY_S and not deferred(left)) or (rt > MAX_DISPLAY_S and not deferred(right)):
                        continue
                    key = (max(speed(start, second, left), speed(second, end, right)), delay, second)
                    if chosen is None or key < chosen[0]:
                        chosen = (key, (second, delay))
    return chosen[1] if chosen else None


def _sentences_of(text: str):
    parts, cur = [], ""
    for ch in text:
        cur += ch
        if ch in "。！？!?":
            parts.append(cur)
            cur = ""
    if cur.strip():
        parts.append(cur)
    return parts


def _piece_ok(piece: str, opens_mid: bool, closes_mid: bool) -> bool:
    """P466（屏里不许「完整句 + 半句」），另加例外：≤4 字的短回应可附在邻句上。"""
    if _screen_ok(piece, opens_mid, closes_mid):
        return True
    sentences = _sentences_of(piece)
    for i, sent in enumerate(sentences):
        if _units(sent) <= _FILLER_MAX_UNITS:
            rest = "".join(sentences[:i] + sentences[i + 1:])
            if rest.strip() and _screen_ok(rest, opens_mid, closes_mid):
                return True
    return False


def _cut_pieces(flat, p, opens_mid, closes_mid):
    """在压平文字第 p 位切开：返回 (左, 右, 两块是否都不拼出「完整句 + 半句」)。"""
    left, right = flat[:p].strip(), flat[p:].strip()
    seam_mid = not _ends_sentence(left)
    return left, right, _piece_ok(left, opens_mid, seam_mid) and _piece_ok(right, seam_mid, closes_mid)


# ── 切点梯子（用户 2026-10-12 定）──────────────────────────────────
# 并成一行后汉字>16 或每秒汉字>9 的屏，试着切成两屏（各一行）。只在这一屏自己的 [开始, 结束] 内切，
# 邻居屏一个字不动。每块 ≤16，只切两屏（一分为二，不递归）。依次试，前一级成功就停：
#   第 1 级 自然切点（标点、汉字后空格），每块 ≥5 个汉字
#   第 2 级 自然切点，每块 ≥3 个汉字
#   第 3 级 自然切点 + 词缝（分词工具认的汉字之间的缝），每块 ≥5 个汉字
#   第 4 级 同第 3 级，读速上限换成 READING_CPS_RESCUE
# 每一刀的合法性（≥0.84 秒、≤7 秒、读速、词时间不重叠、不拼半句）由 `_split_time(best=True)` 与
# `_cut_pieces` 判；两块是一起判的，一块不行整刀不成立。

def _han_count(text: str) -> int:
    """汉字口径（用户 2026-10-12 定）：字数 = 中文汉字个数；标点、空格、英文字母、数字、换行都不算。"""
    return sum(1 for ch in text if _is_han(ch))


def _han_speed(start, end, text) -> float:
    """汉字口径的读速：汉字个数 ÷ 显示秒数。**切点梯子用这一份**：要不要进梯子、一刀合不合法、
    `best` 模式的比较键、挑最舒服的一刀。"""
    dur = end - start
    return _han_count(text) / dur if dur > 0 else 0.0


def _ladder_needed(text: str, start, end) -> bool:
    """一屏并成一行后，要不要交给切点梯子：汉字 >16，或每秒汉字 >9。"""
    return _han_count(text) > LINE_MAX_HAN or _han_speed(start, end, text) > READING_CPS + 1e-9


def _screen_reading_speed(start, end, text) -> float:
    """旧口径（字宽）的读速：屏上原样文字、空格照算。**不在切点梯子里用**，只给梯子之前的排版阶段
    （`_split_time` 默认模式）和旧测试的违规检查用。
    **别的各自有自己口径的**：`_entry_too_fast`、`_rework_double_line_screen` 里的 `ok_one` 与
    `read_units`、`_reads_too_fast`、合并与借时间阶段的判断。"""
    dur = end - start
    return _units(text) / dur if dur > 0 else 0.0


def _cut_ladder(start, end, text, words, opens_mid, closes_mid):
    """切点梯子：返回 [(开始, 结束, 文字), ...]（两屏或更多）；所有办法都切不开返回 None（保持一行）。

    插入④ C6″/C13（用户 2026-10-24 看片定）：这一屏里有 AI 记号（不紧贴标点的那种）时，**先试 AI 那一刀**——
    ① AI 记号上能切就切；② 不合法 → 取离 AI 记号最近的合法刀（标点、词缝都算）；
    ③ 两块都放不下 16 个汉字时，允许还带记号的那一块先超长，再对它用同样办法切（需要三屏以上）。
    都不行、或没有 AI 记号，才走下面原来的四级梯子（C7：没有 Key 的用户行为不变）。"""
    flat, cuts = _segment_cuts(text)
    natural = [p for p in cuts if _cut_pieces(flat, p, opens_mid, closes_mid)[2]]
    seams = [p for p in sorted(_jieba_bounds(flat))
             if 0 < p < len(flat) and p not in cuts and _is_han(flat[p - 1]) and _is_han(flat[p])
             and _cut_pieces(flat, p, opens_mid, closes_mid)[2]]
    levels = [(natural, 5, READING_CPS), (natural, 3, READING_CPS), (natural + seams, 5, READING_CPS),
              (natural + seams, 5, READING_CPS_RESCUE)]
    # ⑦.5 #4：紧跟英文词 / 数字 / 「——」的记号不在 `_segment_cuts` 的候选里（它只认汉字后的空白），这里补上
    after_mark = [p for p in range(1, len(flat)) if flat[p - 1] == SPLIT_MARK and p not in cuts
                  and flat[:p].strip() and flat[p:].strip() and not _cut_forbidden(flat, p)
                  and _cut_pieces(flat, p, opens_mid, closes_mid)[2]]
    marks = [p for p in natural + after_mark
             if flat[p - 1] == SPLIT_MARK and _visible_before(flat, p) not in _SPLIT_PUNCT]
    if marks:
        found = (_ladder_pick(start, end, flat, words, [(marks, f, c) for _, f, c in levels])
                 or _ladder_pick(start, end, flat, words, [(natural + seams, f, c) for _, f, c in levels],
                                 near=marks))
        if found:
            return _ladder_two(start, end, flat, found)
        found = _ladder_pick(start, end, flat, words, [(marks, f, c) for _, f, c in levels], long_with_mark=True)
        pieces = found and _ladder_more(start, end, flat, words, found, opens_mid, closes_mid)
        if pieces:
            return pieces
    found = _ladder_pick(start, end, flat, words, levels)
    return _ladder_two(start, end, flat, found) if found else None


def _visible_before(flat, p):
    """切点 p 前面第一个看得见的字（跳过 AI 记号和空白）。"""
    q = p - 1
    while q >= 0 and flat[q].isspace():
        q -= 1
    return flat[q] if q >= 0 else ""


def _ladder_pick(start, end, flat, words, levels, *, near=None, long_with_mark=False):
    """按级试：每级在候选里挑「两块里较快那块读速最低」的一刀（``near`` 给了就先比离 AI 记号的远近）；
    前一级挑到就停。返回 (切点, 第二屏开始, 延后秒数) 或 None。"""
    for candidates, floor, cps in levels:
        best = None
        for p in candidates:
            found = _split_time(start, end, flat, words, p, min_units=floor, cps=cps, best=True, han=True,
                                long_with_mark=long_with_mark)
            if found is None:
                continue
            second, delay = found
            left, right = flat[:p].strip(), flat[p:].strip()
            key = (min(abs(p - m) for m in near) if near else 0,
                   max(_han_speed(start, second, left), _han_speed(second, end, right)), p, delay)
            if best is None or key < best[0]:
                best = (key, p, second, delay)
        if best is not None:
            return best[1:]
    return None


def _ladder_two(start, end, flat, found):
    p, second, _delay = found
    return [(start, second, _render_line(flat[:p].strip())), (second, end, _render_line(flat[p:].strip()))]


def _ladder_more(start, end, flat, words, found, opens_mid, closes_mid):
    """C13：第一刀切完，还超过 16 个汉字（一定还带记号）的那一块再交给梯子；有一块切不开就整条作罢。"""
    p, second, delay = found
    left, right = flat[:p].strip(), flat[p:].strip()
    seam_mid = not _ends_sentence(left)
    right_start = second - delay
    halves = [(start, second, left, [w for w in words if float(_word_start(w)) < right_start - 1e-9],
               opens_mid, seam_mid),
              (second, end, right, [w for w in words if float(_word_start(w)) >= right_start - 1e-9],
               seam_mid, closes_mid)]
    out = []
    for a, b, part, ws, om, cm in halves:
        if _han_count(part) <= LINE_MAX_HAN:
            out.append((a, b, _render_line(part)))
            continue
        sub = _cut_ladder(a, b, part, ws, om, cm)
        if sub is None:
            return None
        out.extend(sub)
    return out


def _rework_double_line_screen(entry, start, end, text, words, opens_mid, closes_mid):
    """决定一个两行屏的去向：返回 [(开始, 结束, 文字), ...]（1 或 2 屏）。永不返回两行。"""
    flat, cuts = _segment_cuts(text)
    lines = entry.text.split("\n")
    u1 = _units(lines[0])
    u2 = _units(lines[1]) if len(lines) > 1 else 99
    one = _one_line(text)
    width, dur = _units(one), entry.end - entry.start
    pieces_n = len(cuts) + 1
    ok_one = dur > 0 and width / dur <= READING_CPS + 1e-9 and width <= _ONE_LINE_MAX_UNITS
    p0 = _original_break(text, flat)

    def cut_ok(p):
        return _cut_pieces(flat, p, opens_mid, closes_mid)

    def split_at_original_break():
        # 只在程序原来的换行位置拆，不自己另选位置
        if p0 not in cuts:
            return None
        left, right, ok = cut_ok(p0)
        if not ok:
            return None
        r = _split_time(start, end, flat, words, p0)
        return None if r is None else [(entry.start, r[0], _render_line(left)),
                                       (r[0], entry.end, _render_line(right))]

    if min(u1, u2) <= 5:
        if ok_one:                                    # 一边≤5字：并成一行；但原换行处停顿够长就拆
            if p0 in cuts:
                left, right, ok = cut_ok(p0)
                r = _split_time(start, end, flat, words, p0, pause=True) if ok else None
                if r:
                    return [(entry.start, r[0], _render_line(left)), (r[0], entry.end, _render_line(right))]
            return [(entry.start, entry.end, one)]
    else:
        # 宽>20，或宽≤20 但小段≥3：先试在原换行处拆成两屏
        split = split_at_original_break() if (width > 20 or pieces_n >= 3) else None
        if split:
            return split
        if width <= 20 and ok_one:                    # 宽≤20：拆不了就并成一行
            return [(entry.start, entry.end, one)]
    # 兜底（两行没处理掉）：无标点时在原换行处拆；否则 24 字内且读得过来就并成一行
    if p0 is not None:
        left, right = flat[:p0].strip(), flat[p0:].strip()
        seam_mid = not _ends_sentence(left)
        if left and right and _piece_ok(left, opens_mid, seam_mid) \
                and _piece_ok(right, seam_mid, closes_mid):
            r = _split_time(start, end, flat, words, p0)
            if r:
                return [(entry.start, r[0], _render_line(left)), (r[0], entry.end, _render_line(right))]
    read_units = _units(entry.text.replace("\n", ""))   # 并行时加的空格不用读，不算进读速
    if width <= _ONE_LINE_MAX_UNITS and dur > 0 and read_units / dur <= READING_CPS + 1e-9:
        return [(entry.start, entry.end, one)]
    # 永不双行（用户 2026-10-12 定）：拆不开、读不过来也不留两行，并成一行（例外，由质量检查另记）
    return [(entry.start, entry.end, one)]


def _no_double_line(entries):
    """永不双行的最后一道保险：不管前面哪一步漏了，输出的屏里不许有换行符。
    （换人前缀等当前不开的功能留下的两行屏也在这里并成一行。）"""
    return [e if "\n" not in e.text else DisplayEntry(
        start=e.start, end=e.end, long_ok=e.long_ok,
        text=_join_breaks(e.text))
        for e in entries]


def _rework_double_line_screens(entries, drafts, report):
    """把每个两行屏按上面的规则改成两屏/一行/原样；单行屏原样。
    只剩一屏、但宽>16 或读速>9 的，再交给切点梯子；梯子也切不开的记进 ``report``。"""
    out = []
    for i, (entry, draft) in enumerate(zip(entries, drafts)):
        start, end, text, changed, words = draft
        # 换人的屏带说话人前缀（0.5 单位），新规则没在这种屏上验证过：原样保留
        if "\n" not in entry.text or changed:
            out.append(entry)
            continue
        opens_mid, closes_mid = _row_opens_mid(drafts, i), _row_closes_mid(draft)
        result = _rework_double_line_screen(
            entry, float(start), float(end), text, words, opens_mid, closes_mid)
        if len(result) == 1 and _ladder_needed(result[0][2], entry.start, entry.end):
            ladder = _cut_ladder(float(start), float(end), text, words, opens_mid, closes_mid)
            if ladder is not None:
                result = ladder
            else:
                row = (float(start), _han_count(result[0][2]), _han_speed(entry.start, entry.end, entry.text))
                _log.info("切点梯子：第 %.2f 秒起的屏所有切法都不合规则，保持一行（%d 个汉字，每秒 %.2f 个）", *row)
                report.unsplit_wide_screens.append(row)
        long_ok = entry.long_ok if len(result) == 1 else False
        out.extend(DisplayEntry(start=a, end=b, text=t, long_ok=long_ok) for a, b, t in result)
    return out


def _layout_pass(segments, rules):
    """按给定规则整片排一遍，返回 ``(条目, 质量摘要)``。"""
    entries, rep, _drafts, _ai_spans = _layout_pass_full(segments, rules)
    return entries, rep


def _layout_pass_full(segments, rules):
    """同 `_layout_pass`，另返回草稿（与条目一一对应）供两行屏事后处理用，
    以及 AI 切过的句子的时间段（插入④ 施工 B D17：碰到它们的屏不改拆、不重拆）。"""
    global _RULES
    _RULES = rules
    rep = TimelineReport()
    segs = _layout_segments(segments, polish_marks=rules.pref)

    # 草稿的第 5 位是词表。G12-B 修法 1：它必须一路活到合并之后——
    # 合并会把两条短屏粘成一条长文本，那时候没有词表就再也找不到合法切点了。
    drafts: list[tuple[float, float, str, bool, list]] = []
    ai_spans: list[tuple[float, float]] = []
    prev_closes_mid = False
    for seg, zh in zip(segs, _internal_zh_texts(segs)):
        made = None
        # 插入④ 施工 B：有 AI 词证据的句子按 AI 的刀切、按词定时间（D1）；定不下来整句退回（D10），
        # 退回就是这一句不用 AI 的切法——记号一起去掉（R3）。没有词证据的（预拆分）照现在的做法（R2）。
        if getattr(seg, "ai_cut_words", None) is not None and SPLIT_MARK in zh:
            made = _ai_sentence_drafts(seg, zh, rep)
            if made is None:
                zh = zh.replace(SPLIT_MARK, "")
            else:
                ai_spans.append((float(seg.start), float(seg.end)))
        if made is None:
            made = _segment_to_drafts(seg, zh, rep, opens_mid=prev_closes_mid)
        drafts.extend(_end_when_speech_ends(made))
        prev_closes_mid = not _ends_sentence(zh)

    drafts = _repair_times(drafts, rep, ai_spans)

    entries = []
    for index, (start, end, text, changed, _words) in enumerate(drafts):
        if not _screen_ok(text, _row_opens_mid(drafts, index), _row_closes_mid(drafts[index])):
            rep.half_sentence_screens.append((index + 1, round(start, 3)))
        # D17 + D18：碰到 AI 句的屏一行显示，不先换行（先换行会吞掉换行处的句号）
        rendered = (_one_line_screen(text, changed) if _touches(start, end, ai_spans)
                    else _decorate(text, changed))
        # 行数只有在这里才是最终的（`_decorate` 之后）。合同 A3：
        # 超两行照常交付，但必须在质量摘要里可见。
        line_count = len(rendered.split("\n"))
        if line_count > 2:
            rep.over_two_lines.append((index, start, line_count))
        # ⑦.5 ④：只有满足窄例外的超长屏才放行，自检才能抓住以后封顶漏掉的屏
        entries.append(DisplayEntry(start=start, end=end, text=rendered,
                                    long_ok=(end - start > MAX_DISPLAY_S + 1e-6
                                             and _long_screen_allowed(_words, text))))
    return entries, rep, drafts, ai_spans


def _touches(start, end, spans) -> bool:
    """这一屏和某个 AI 句的时间有重叠（D17：不能只看屏的开头——合并会拼出「前半程序句 + 后半 AI 句」）。"""
    return any(start < b - 0.02 and end > a + 0.02 for a, b in spans)


def _one_line_screen(text, changed) -> str:
    """D18：一行显示。原来有标点的地方渲染成一个空格，中文之间不加空格。"""
    body = _render_line(text)
    return _SPEAKER_PREFIX + body if changed else body


def _ai_cut_word_index(words, refined_text, pair):
    """A2 + D6：这一刀的刀后词是第几个原始识别词。返回 ``(序号, 来源)``；找不到序号是 None。

    先在原始识别词里找挨着的两个词，只能一处；找不到再到润色英文里找，按逐词对齐换回原词。
    破折号、连字符两边拆开再比（`_en_tokens`）；刀后词必须是一个原词的开头。"""
    if pair is None:
        return None, "没写词"
    a, b = pair
    raw, owner = [], []
    for k, w in enumerate(words):
        for t in _en_tokens(_w_text(w)):
            raw.append(t)
            owner.append(k)

    def starts_word(i):
        return 0 < i < len(raw) and owner[i] != owner[i - 1]

    hits = [i for i in range(1, len(raw)) if raw[i - 1] == a and raw[i] == b and starts_word(i)]
    if len(hits) == 1:
        return owner[hits[0]], "原词"
    if len(hits) > 1:
        return None, "重复"
    from backend.core.refined_alignment import align_opcodes
    ref = _en_tokens(refined_text)
    ops = align_opcodes(raw, ref)
    if ops is None:
        return None, "找不到"
    to_raw = {}
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            to_raw.update(zip(range(j1, j2), range(i1, i2)))
    hits = [r for r in range(1, len(ref)) if ref[r - 1] == a and ref[r] == b]
    if len(hits) > 1:
        return None, "重复"
    if not hits or hits[0] not in to_raw or not starts_word(to_raw[hits[0]]):
        return None, "找不到"
    return owner[to_raw[hits[0]]], "润色换回"


def _ai_sentence_drafts(seg, zh, rep: TimelineReport):
    """D1 + D10：AI 切过的句子，每一刀落在刀后词开口的那一刻。

    找到词的刀留下；没找到的那一刀用程序现有的猜法（`_ratio_index` + `_anchor_indices`），
    只在左右两把已找到的刀之间的英文词里猜。猜不出、或刀的顺序不对 → 返回 None，整句退回。"""
    pieces = zh.split(SPLIT_MARK)
    words = list(getattr(seg, "words", None) or [])
    refined = getattr(seg, "text", "") or ""
    count = rep.ai_cut_sources
    found = []
    for k in range(1, len(pieces)):
        pair = seg.ai_cut_words.get(_cut_key(SPLIT_MARK.join(pieces[:k])))
        j, src = _ai_cut_word_index(words, refined, pair)
        if j is not None and not 0 < j < len(words):
            j, src = None, "找不到"
        count[src] = count.get(src, 0) + 1       # 没找到的也记（没写词 / 找不到 / 重复），C18
        found.append(j)
    cuts = list(found)
    for k, j in enumerate(found):
        if j is not None:
            continue
        left = max([found[i] for i in range(k) if found[i] is not None], default=0)
        right = min([found[i] for i in range(k + 1, len(found)) if found[i] is not None], default=len(words))
        li = max([i + 1 for i in range(k) if found[i] is not None], default=0)
        ri = min([i + 1 for i in range(k + 1, len(found)) if found[i] is not None], default=len(pieces))
        zh_sub = "".join(pieces[li:ri])
        zh_pos = len("".join(pieces[li:k + 1]))
        sub = words[left:right]
        guess = None
        if len(sub) >= 2 and 0 < zh_pos < len(zh_sub):
            base = max(1, min(len(sub) - 1, _ratio_index(zh_sub, zh_pos, len(sub))))
            guess = next((c for c in _anchor_indices(sub, base) if 0 < c < len(sub)), None)
        cuts[k] = None if guess is None else left + guess
        if guess is not None:
            count["程序猜"] = count.get("程序猜", 0) + 1
    if any(j is None for j in cuts) or cuts != sorted(set(cuts)):
        count["整句退回"] = count.get("整句退回", 0) + 1
        return None
    bounds = [0] + cuts + [len(words)]
    starts = [float(seg.start)] + [float(_word_start(words[j])) for j in cuts]
    changed = bool(getattr(seg, "speaker_change", False))
    return [(starts[n], float(seg.end) if n == len(pieces) - 1 else starts[n + 1],
             pieces[n].strip(), changed and n == 0, words[bounds[n]:bounds[n + 1]])
            for n in range(len(pieces))]


def _end_when_speech_ends(drafts):
    """D9：每一屏（AI 切的、程序切的、短句、退回的）在它最后一个英文词说完时结束；
    时间标错的词不算「还在说」（D14）。不够读，后面 `_fill_reading_gaps` 只借真空白。"""
    out = []
    for start, end, text, changed, words in drafts:
        k = len(words) - 1
        while k > 0 and _word_time_bad(words, k):
            k -= 1
        if words:
            last = float(_word_end(words[k]))
            if start < last < end:
                end = last
        out.append((start, end, text, changed, words))
    return out


def _layout_segments(segments, *, polish_marks):
    """排版只在**词的副本**上做记号（决定 35），原词、原 segment 一个字段都不改（R-2）。

    * 改动 3：润色否掉的句号，排版不认（只删不加）。
    * §15.1：记下「润色文在这里是不是句末」（键 ``_pe``，只在最终排版）。
    * §11：词中间的破折号（``s'more—chocolate``）拆成两个词，时间按字母比例分。
    """
    out = []
    for seg in segments:
        layout_seg = copy.copy(seg)
        words = [dict(w) if isinstance(w, dict) else w for w in (getattr(seg, "words", None) or [])]
        refined = getattr(seg, "text", "") or ""
        if words and all(isinstance(w, dict) for w in words):
            _drop_refiner_removed_periods(words, refined)
            if polish_marks:
                _mark_polish_sentence_ends(words, refined)
            words = [piece for w in words for piece in _split_inner_dash(w)]
        layout_seg.words = words
        out.append(layout_seg)
    return out


def _drop_refiner_removed_periods(words, refined_text) -> None:
    """改动 3：原词带句号、一一对应的润色词没有句号 → 排版当它没有句号。润色新加的标点一概不搬。"""
    from backend.core.refined_alignment import align_opcodes

    raw = [_w_text(w) for w in words]
    refined = refined_text.split()
    if raw == refined:
        return
    opcodes = align_opcodes(raw, refined)
    if opcodes is None:
        return
    for tag, i1, i2, j1, j2 in opcodes:
        if tag != "equal" and (i2 - i1) != (j2 - j1):
            continue
        for i, j in zip(range(i1, i2), range(j1, j2)):
            if _EN_PERIOD_END.search(raw[i]) and not re.search(r"[.?!]", refined[j]):
                key = "word" if "word" in words[i] else "text"
                words[i][key] = _EN_TRAILING_PUNCT.sub("", raw[i])


def _polish_is_end(tokens, j) -> bool:
    """润色文第 j 个词是不是句末：词尾 . ? !（不算省略号），且后面没有词或下一个词大写开头。"""
    token = tokens[j]
    if (not _EN_PERIOD_END.search(token)
            or re.search(r"(\.\.\.|…)[\"'”’)\]]*$", token)):
        return False
    if j == len(tokens) - 1:
        return True
    following = re.sub(r"^[\"'“‘(\[]+", "", tokens[j + 1])
    return following[:1].isupper()


def _mark_polish_sentence_ends(words, refined_text) -> None:
    """§15.1：在词副本上记「润色文在这里是不是句末」（键 ``_pe``）；并补改动 3 的
    「块长不等、润色文这一段没有句号」：结巴删句号，改口改破折号。整组对不齐就不记。"""
    from backend.core.refined_alignment import _norm, align_opcodes

    raw = [_w_text(w) for w in words]
    refined = refined_text.split()
    if not raw or not refined:
        return
    opcodes = align_opcodes(raw, refined)
    if opcodes is None:
        return
    marks = [False] * len(raw)
    last = None
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for i, j in zip(range(i1, i2), range(j1, j2)):
                if _polish_is_end(refined, j):
                    marks[i] = True
                    last = (i, j)
        elif tag == "replace":
            if _polish_is_end(refined, j2 - 1):
                marks[i2 - 1] = True
                last = (i2 - 1, j2 - 1)
            if not any(re.search(r"[.?!]", refined[j]) for j in range(j1, j2)):
                for i in range(i1, i2):
                    if (_EN_PERIOD_END.search(raw[i])
                            and not re.search(r"(\.\.\.|…)", raw[i])):
                        key = "word" if "word" in words[i] else "text"
                        base = re.sub(r"[.?!]+([\"'”’)\]]*)$", r"\1", raw[i])
                        restart = any(
                            len(parts := [_norm(x) for x in refined[j].split("—")]) >= 2
                            and parts[0] == _norm(base) and parts[1] and parts[1] != parts[0]
                            for j in range(j1, j2) if "—" in refined[j])
                        words[i][key] = base + "—" if restart else base
        elif tag == "insert":
            if i1 > 0 and _polish_is_end(refined, j2 - 1):
                marks[i1 - 1] = True
                last = (i1 - 1, j2 - 1)
    if last and last[1] == len(refined) - 1 and last[0] != len(raw) - 1:
        marks[last[0]] = False
        marks[-1] = True
    for w, mark in zip(words, marks):
        w["_pe"] = mark


_INNER_DASH = re.compile(r"[A-Za-z0-9'][—–](?=[A-Za-z0-9'])")


def _split_inner_dash(word):
    """§11：词中间的破折号也算英文路标——在破折号处把这个词拆成两个，时间按字母比例分。"""
    text = _w_text(word)
    match = _INNER_DASH.search(text)
    if not match:
        return [word]
    cut = match.start() + 2
    left_text, right_text = text[:cut], text[cut:]

    def letters(part):
        return len(re.sub(r"[^A-Za-z]", "", part))

    start, end = float(_word_start(word)), float(_word_end(word))
    middle = start + (end - start) * letters(left_text) / (letters(text) or 1)
    left, right = dict(word), dict(word)
    for piece, piece_text in ((left, left_text), (right, right_text)):
        for key in ("word", "text"):
            if key in piece:
                piece[key] = piece_text
    left["start"], left["end"] = start, middle
    right["start"], right["end"] = middle, end
    if "_pe" in word:
        left["_pe"] = False
    return [left] + _split_inner_dash(right)


def assert_timeline_valid(entries, *, srt: bool = False, ass: bool = False) -> None:
    """三个写出器共用的结构检查。**只检查，绝不修改任何时间。**

    真正的修复只发生在 build_display_timeline 里。走到写出器还有结构问题，
    说明上游漏了，必须报错，不得掩盖（R-14 / §11.1）。

    取整检查按本次实际输出格式条件化：只出 SRT 就只查 1ms；烧录另生成
    内部 ASS 时再查 10ms。两者取整后时间不保证逐条相同，这是正确行为。
    """
    _check_types_and_bounds(entries)
    _check_at(entries, quantum=None, label="原始")
    if srt:
        _check_at(entries, quantum=_SRT_QUANTUM, label="SRT 取整后")
    if ass:
        _check_at(entries, quantum=_ASS_QUANTUM, label="ASS 取整后")


# ── 内部：内部文字（P469）────────────────────────────────────────

def _internal_zh_texts(segments) -> list[str]:
    """每段用于**判断**的内部文字：中文缺句末标点、英文原文表明这句已说完时，补一个句号。

    句末是逗号、顿号、分号、冒号的，换成句号。满足任意一条就算说完：
      1. 这一段英文以 . ? ! 结尾（后面可以跟引号或括号）；
      2. 下一段英文以大写字母开头，第一个词不是 I / I'm / I've 这一类，
         并且前两个词不都是大写开头（排除人名地名：Kyra Berry、New Zealand）；
      3. 下一段换了说话人。

    **只在内存里用**：不写回 ``seg.zh``、不写进翻译缓存；渲染时按标点规则去掉或变空格，
    观众看不到，也不计入行长与速度。翻译失败或译文为空的段不处理。
    """
    out = []
    for k, seg in enumerate(segments):
        zh = (getattr(seg, "zh", "") or "").strip()
        if zh and zh != _FAILED_MARKER and not _ends_sentence(zh):
            nxt = segments[k + 1] if k + 1 < len(segments) else None
            if _english_says_sentence_ended(seg, nxt):
                zh = (zh[:-1] if zh[-1] in _SOFT_TAIL else zh) + "。"
        out.append(zh)
    return out


def _english_says_sentence_ended(seg, nxt) -> bool:
    if _EN_SENTENCE_END.search((getattr(seg, "text", "") or "").strip()):
        return True
    if nxt is None:
        return False
    if getattr(nxt, "speaker_change", False):
        return True
    words = _EN_WORD.findall(getattr(nxt, "text", "") or "")
    if not words or not words[0][0].isupper() or re.match(r"I('|$)", words[0]):
        return False
    return not (len(words) >= 2 and words[1][0].isupper())


# ── 内部：一条 segment → 若干草稿条目 ────────────────────────────

def _segment_to_drafts(seg, zh, rep: TimelineReport, *, opens_mid=False):
    start = float(getattr(seg, "start", 0.0))
    end = float(getattr(seg, "end", 0.0))
    changed = bool(getattr(seg, "speaker_change", False))

    # G6：这里原来还读一个 `uncertain` 一路运到 `_decorate` 变成 `[?]`。
    # `[?]` 不再进入用户成品，这条运输线上就没有消费者了，整条撤掉。
    #
    # **注意边界**：撤掉的只是 draft 元组里这个中间字段。
    # `seg.uncertain` 本身必须留着——`question_mark_count` 从它算，
    # 而且它要落盘供中断任务恢复时读回。删到那个字段，用户会失去字幕质量提示，
    # 且老任务恢复后全部静默变成「不存疑」——不报错，不崩溃。

    # 翻译失败与空译文是同一件事，只有一种用户可见文案（④ 人工读 #4）。
    # 旧代码有两个分支、两套文案，其中一个还把另一个的哨兵值打到屏幕上。
    if not zh or zh == _FAILED_MARKER:
        original = (getattr(seg, "text", "") or "").strip()
        # 词表位置留**空列表**，不是缺省——这条路本来就没有可靠的词级时间，
        # 造一个出来就是伪造。空词表进 `_find_safe_cut` 会因 len<2 直接返回 None，
        # 于是整条不拆，这正是想要的。
        out = [(start, end, f"{_UNTRANSLATED_PREFIX}{original}", changed, [])]
    else:
        words = list(getattr(seg, "words", []) or [])
        # 判断行数与实际渲染必须用**同一个**首行预算，来源只有 `_first_line_budget`。
        parts = _split_to_fit(start, end, zh, words, rep,
                              first_max=_first_line_budget(changed),
                              opens_mid=opens_mid, closes_mid=not _ends_sentence(zh))
        out = []
        for i, (p_start, p_end, p_text, p_words) in enumerate(parts):
            out.append((p_start, p_end, p_text, changed if i == 0 else False, p_words))
    if not _RULES.pref:
        return out
    # G12 第三批 B（排版 §13.2、§14.1）：排出三行的屏，从已有的换行里挑一处拆成两屏。
    return [piece for row in out for piece in _split_three_line_screen(row)]


def _split_to_fit(start, end, zh, words, rep: TimelineReport, *, first_max=None,
                  opens_mid=False, closes_mid=False):
    """把一条字幕切成若干屏。**一份映射实现，两个触发条件。**

    触发条件一 —— 句末标点：一句话一条字幕本来就更好读。这继承自原来的
    ``word_align_segments()``，是 P099/P390 的现有保护，不看时长也要切。
    触发条件二 —— 两行放不下，或超过 7 秒：在逗号类标点处切。

    两个条件共用同一份「译文切点 → 真实原词边界」的映射与验证：
    左词 end <= 右词 start、两边都有正时长；逗号那一刀还要求两边都能达到 0.84 秒。
    证明不了就整条不拆——**绝不按字符中点或字数比例硬切**（R-11）。

    返回四元组 ``(start, end, text, words)``。G12-B 修法 1：词表必须跟着每一屏走，
    否则 `_merge_still_short` 合出来的长文本就再也找不到合法切点。
    """
    out = []
    pieces = _cut(start, end, zh, words, _SENT_END, rep, always=True,
                  opens_mid=opens_mid, closes_mid=closes_mid)
    last = len(pieces) - 1
    for index, (p_start, p_end, p_zh, p_words) in enumerate(pieces):
        # 说话人前缀只贴在第一屏（`_segment_to_drafts` 是 `changed if i == 0 else False`），
        # 所以只有第一屏的首行预算被它挤掉。
        budget = first_max if index == 0 else None
        out.extend(_cut(p_start, p_end, p_zh, p_words, _COMMA, rep,
                        always=False, first_max=budget,
                        opens_mid=opens_mid if index == 0 else False,
                        closes_mid=closes_mid if index == last else False))
    return out


def _first_line_budget(changed: bool) -> float | None:
    """首行能放几个单位。**全项目唯一来源。**

    说话人前缀贴在换行之后，所以它要从首行预算里让出自己的宽度（`-` 是 0.5 单位）。
    ``None`` 表示用默认预算（``wrap_lines`` 的 ``first_max`` 缺省行为）。

    ⑦.5：判断行数和实际渲染必须问**同一个**预算。原来 ``_needs_split`` 按 20 问
    「折几行」、``_decorate`` 按 18 折，于是说话人切换的字幕被判成两行、实际三行，
    从 P455 的修复底下溜过去——**把两份实现合成一份代码、两套参数，等于没合并。**
    """
    return LINE_MAX_UNITS - _line_units(_SPEAKER_PREFIX) if changed else None


def _needs_split(start, end, zh, first_max=None, *, cap=MAX_DISPLAY_S) -> bool:
    """这一屏要不要动逗号那一刀：**时长超上限，或者两行放不下**。

    P455：原来只看时长。真实成品 49 条三行以上的字幕**时长全部 ≤6.98 秒**，
    于是逗号那一趟一次都没拔出来过。行数问换行函数，**不要另猜一个字数**。
    ``first_max`` 必须与渲染用的一致。``cap``：这一段的时长上限（改动 4 放行时是 30 秒）。

    G12 第三批 B（排版 §12 第 1 步）：一行放不下也进入拆屏尝试（一行优先）；
    「原来就要拆」按原来的换行判断（防止因为能在汉字中间换行，就不再拆屏）。
    """
    if not _RULES.pref:
        if end - start > cap:
            return True
        return len(_base_wrap(zh, first_max=first_max).split("\n")) > 2
    return (_originally_needs_split(start, end, zh, first_max, cap=cap)
            or not _fits_one_line(zh, first_max))


def _originally_needs_split(start, end, zh, first_max=None, *, cap=MAX_DISPLAY_S) -> bool:
    """原来就必须拆：超时长，或原来的换行排出三行；只是有一行超长的，规则 D 换行后两行放得下就不拆。"""
    if end - start > cap:
        return True
    old = _base_wrap(zh, first_max=first_max)
    if len(old.split("\n")) > 2:
        return True
    if not _over(old):
        return False
    return len(_wrap_d(zh, first_max=first_max).split("\n")) > 2


def _fits_one_line(text, first_max=None) -> bool:
    return _line_units(_render_line(text)) <= (first_max or LINE_MAX_UNITS) + 1e-9


def _cut(start, end, zh, words, pattern, rep: TimelineReport, *, always: bool,
         first_max=None, opens_mid=False, closes_mid=False):
    """按 ``pattern`` 递归切分。``always=False`` 时由 ``_needs_split`` 决定切不切。

    G12 第三批 B（改动 4）：每一层按**这一段**的词和译文决定时长上限——
    词时间可信且每个逗号切下去都碎的，不按 7 秒切。
    """
    cap = _NARROW_CAP_S if _long_screen_allowed(words, zh) else MAX_DISPLAY_S
    if not always and not _needs_split(start, end, zh, first_max, cap=cap):
        return [(start, end, zh, words)]

    # 0.84 秒可行性只约束逗号那一刀：它是按需切的，不该制造新短字幕。
    # 句末拆条是 P099/P390 的既有保护（一句话一条），它优先于 0.84——
    # 切出来的短屏由 _repair_times 的补救阶梯接管。
    found = _find_safe_cut(start, end, zh, words, pattern,
                           require_min_display=not always,
                           keep_whole=(opens_mid, closes_mid), cap=cap)
    if found is None:
        # 守卫必须与上面的门槛**用同一个表达式**：只改门槛不改这里，
        # 由行数触发的那一批切不动时一个计数器都不加，报警器会永远读 0。
        # G12 第三批 B（⑦.5 ①）：一行优先让 `_needs_split` 对普通两行屏也为真；
        # 计「未能拆分」只认原来的门槛（放不下两行 / 超时长），否则普通两行屏全被记成失败。
        if not always and _originally_needs_split(start, end, zh, first_max, cap=cap):
            # 这一屏仍然切不动，记账后交给封顶。
            if len(words) <= 1:
                rep.single_word_over_max += 1
            elif pattern.search(zh) or _SENT_END.search(zh):
                rep.mapping_failed_splits += 1   # 有标点但映射不到合法原词边界
            else:
                rep.no_safe_boundary_splits += 1  # 无标点，本批无中文安全边界提供者
        return [(start, end, zh, words)]

    zh_pos, idx, left_end, right_start = found
    left = zh[:zh_pos].strip()
    right = zh[zh_pos:].strip()
    # 左半仍然是第一屏（前缀贴在它上面），右半不再是。
    seam_mid = not _ends_sentence(left)
    return (_cut(start, left_end, left, words[:idx], pattern, rep, always=always,
                 first_max=first_max, opens_mid=opens_mid, closes_mid=seam_mid)
            + _cut(right_start, end, right, words[idx:], pattern, rep, always=always,
                   opens_mid=seam_mid, closes_mid=closes_mid))


def _ratio_index(zh, zh_pos, n_words, *, ignore_whitespace=False):
    """译文里第 ``zh_pos`` 个字符，按比例落在第几个原词上。**全项目唯一实现。**

    ``ignore_whitespace=True`` 时前缀与全文都改用非空白字符计数——
    两边必须用同一口径，只对一边去空白会让比例更歪。

    为什么需要去空白这一档：`sentence_splitter._build_split_segments` 在 LLM 给的
    断开处插入断点记号 `SPLIT_MARK`（以前是拼接空格），合并短屏还会插连接空格，
    ``words`` 里都没有对应的词。于是 ``len(zh)`` 比真正对应词表的字符数大，比例整体偏移。
    记号也是空白字符，这一档会一并去掉。实测偏一个词就足以让合法切点被
    0.84 秒条件误杀（真实成品第 41 条右侧 0.560s / 第 96 条 0.480s，
    去空白后都是 0.880s）。

    这里**只算索引**，切正文仍然用原文本位置 ``zh[:zh_pos]``——
    正文与英文词内空格一个都不能删。

    调用方保证 ``n_words >= 2``（`_find_safe_cut` 开头 `len(words) < 2` 就返回 None），
    所以这里不再重复检查——夹紧到 ``[1, n_words-1]`` 只在那个前提下有意义。
    """
    total = _ratio_weight(zh, ignore_whitespace)
    prefix = _ratio_weight(zh[:zh_pos], ignore_whitespace)
    if total <= 0:
        return 1
    idx = round(prefix / total * n_words)
    return max(1, min(n_words - 1, idx))


_LATIN_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’.\-]*")


def _ratio_weight(text, ignore_whitespace) -> int:
    """比例换算里"这段译文有多长"：每个连续的英文词/数字（Redbrid、Joe's、2024）算 1 个字符，
    不按字母个数算。译文里的英文名在英文原文里也只是 1 个词；按字母算会把切点推到更靠后的词，
    字幕晚出（T121/T191 晚 2.2 秒、T046 晚 2.9 秒）。没有英文词的译文，结果与逐字符计数相同。"""
    squeezed = _LATIN_WORD.sub("L", text)
    return len("".join(squeezed.split())) if ignore_whitespace else len(squeezed)


def _find_safe_cut(start, end, zh, words, pattern, *, require_min_display=True,
                   keep_whole=None, cap=MAX_DISPLAY_S):
    """把译文切点映射到真实原词边界（G12 第三批 B：换屏对位）。

    * 句号那一刀：先按润色文的句号配（§15.1，只在最终排版）；再 P528 方案 B——个数相等且
      首个合法配对位置差 ≤0.35 才直接用（快速步骤），否则走允许跳过的顺序对齐
      （`_align_sentence_cuts`）；都不行退回按比例。
    * 逗号那一刀：比例位置前后各 5 个英文词里找路标——标点优先、再 ≥0.45 秒停顿，
      都过全部合法性检查；找不到退回附近几个词（改动 1）。不设闸（记录 27）。
    * 合法性检查一条都不放宽；最终排版里「可拆可不拆」的那一刀另加每块条件（§12）。
    """
    check = _pref_legal_cut if _RULES.pref else _legal_cut
    if len(words) < 2 or not zh:
        return None
    if _RULES.pref and pattern is _SENT_END:
        found = _cut_at_polish_periods(start, end, zh, words, require_min_display,
                                       keep_whole, cap)
        if found is not None:
            return found
    candidates = sorted(
        dict.fromkeys(p for p in (_cut_position(zh, m.end()) for m in pattern.finditer(zh))
                      if 0 < p < len(zh)),
        key=lambda p: abs(p - len(zh) / 2),
    )
    if pattern is _SENT_END:
        # P528 方案 B 快速步骤：个数相等时，离中点最近的首个合法配对位置差 ≤0.35 才信；
        # 超了不再找别的数量配对，直接转顺序对齐
        ordered = sorted(candidates)
        ends = [j + 1 for j, w in enumerate(words[:-1]) if _EN_PERIOD_END.search(_w_text(w))]
        if len(ends) != len(ordered):
            ends = [j for j in ends if not _is_suspicious_period(_w_text(words[j - 1]), "en")]
        if ordered and len(ends) == len(ordered):
            han_total, n = _polish_han_len(zh) or 1, len(words)
            for zh_pos, idx in sorted(zip(ordered, ends), key=lambda t: abs(t[0] - len(zh) / 2)):
                found = check(start, end, zh, words, zh_pos, idx, require_min_display,
                              keep_whole, cap)
                if found is not None:
                    if abs(_polish_han_len(zh[:zh_pos]) / han_total - idx / n) <= _ALIGN_FAST_GATE:
                        return found
                    break
        for zh_pos, idx in sorted(_align_sentence_cuts(zh, words), key=lambda t: abs(t[0] - len(zh) / 2)):
            found = check(start, end, zh, words, zh_pos, idx, require_min_display,
                          keep_whole, cap)
            if found is not None:
                return found
    if pattern is _COMMA:
        n = len(words)
        for ignore_whitespace in (False, True):
            for zh_pos in candidates:
                base = _ratio_index(zh, zh_pos, n, ignore_whitespace=ignore_whitespace)
                for idx in _anchor_indices(words, base):
                    found = check(start, end, zh, words, zh_pos, idx, require_min_display,
                                  keep_whole, cap)
                    if found is not None:
                        return found
            if len("".join(zh.split())) == len(zh):
                break
        return None
    return _find_safe_cut_by_ratio(start, end, zh, words, pattern,
                                   require_min_display=require_min_display,
                                   keep_whole=keep_whole)


def _anchor_indices(words, base):
    """改动 1：比例位置前后各 5 个词里的候选（英文标点优先、再停顿），然后是附近几个词。"""
    n = len(words)
    window = range(max(1, base - _ANCHOR_SPAN), min(n - 1, base + _ANCHOR_SPAN) + 1)
    punct = sorted((j for j in window if _EN_ANCHOR_PUNCT.search(_w_text(words[j - 1]))),
                   key=lambda j: (abs(j - base), j))
    pause = sorted((j for j in window
                    if float(_word_start(words[j])) - float(_word_end(words[j - 1])) >= _ANCHOR_PAUSE_S),
                   key=lambda j: (abs(j - base), j))
    return list(dict.fromkeys(punct + pause)) + list(_nearby_indices(base, n))


def _cut_at_polish_periods(start, end, zh, words, require_min_display, keep_whole, cap):
    """§15.1：句号那一刀先按润色文的句号配——个数相等、每一对位置占比差 ≤0.25 才用。"""
    if not all("_pe" in w for w in words):
        return None
    zh_cuts = sorted(dict.fromkeys(
        p for p in (_cut_position(zh, m.end()) for m in _SENT_END.finditer(zh))
        if 0 < p < len(zh)))
    en_cuts = [j + 1 for j, w in enumerate(words[:-1]) if w["_pe"]]
    if not zh_cuts or len(en_cuts) != len(zh_cuts):
        return None
    han_total, n = _polish_han_len(zh) or 1, len(words)
    if not all(abs(_polish_han_len(zh[:zp]) / han_total - idx / n) <= _POLISH_PAIR_TOL
               for zp, idx in zip(zh_cuts, en_cuts)):
        return None
    for zh_pos, idx in sorted(zip(zh_cuts, en_cuts), key=lambda t: abs(t[0] - len(zh) / 2)):
        found = _pref_legal_cut(start, end, zh, words, zh_pos, idx, require_min_display,
                                keep_whole, cap)
        if found is not None:
            return found
    return None


def _polish_han_len(text: str) -> int:
    return len(_POLISH_HAN_SKIP.sub("", text))


# ── P528 方案 B：允许跳过的顺序对齐（pipelines/P528-正式方案-会话二-2026-10-23.md §5.2；
#    参数与 P528-证据/重放对比.py 候选 B 一个不改）。边界 = (位置, 是否句末强标点, 位置占比)。
_ALIGN_FAST_GATE = 0.35     # 快速步骤：数量配对的位置差门
_ALIGN_TOL = 0.45           # 位置差超过它不配
_ALIGN_STRONG_BONUS = 1.5   # 句末配句末加分
_ALIGN_LEN_PENALTY = 1.0    # 相邻两段相对长度差的惩罚系数


def _align_en_bounds(words):
    """英文词之间的边界：标点（句末为强、可疑句号降弱、带省略号的不算），无标点但停顿 ≥0.45 秒为弱。"""
    n, out = len(words), []
    for j in range(1, n):
        t = _w_text(words[j - 1])
        if not _EN_ANCHOR_PUNCT.search(t):
            if float(_word_start(words[j])) - float(_word_end(words[j - 1])) >= _ANCHOR_PAUSE_S:
                out.append((j, False, j / n))
        elif not re.search(r"(\.\.\.|…)", t):
            strong = bool(_EN_PERIOD_END.search(t)) and not _is_suspicious_period(t, "en")
            out.append((j, strong, j / n))
    return out


def _align_zh_bounds(zh):
    """中文边界：句末（强）与逗号类（弱，书名号内不算、P533 数字内部不算）；同一位置强优先。"""
    total, bounds = _polish_han_len(zh) or 1, {}
    for m in re.finditer(r"[。！？!?]|" + _COMMA.pattern, zh):
        p = _cut_position(zh, m.end())
        if 0 < p < len(zh) and not _cut_forbidden(zh, p):
            strong = bool(_SENT_END.match(m.group())) or bounds.get(p, (0, False))[1]
            bounds[p] = (p, strong, _polish_han_len(zh[:p]) / total)
    return [bounds[p] for p in sorted(bounds)]


def _align_sentence_cuts(zh, words):
    """顺序对齐中英边界（不交叉、两边都可跳过），返回 [(中文句末切点, 英文词索引), ...]。

    只读输入；评分表是函数内的临时数据。没有正分的对齐返回空，由调用方退回比例。"""
    en, cn = _align_en_bounds(words), _align_zh_bounds(zh)

    def score(e, z):
        d = abs(e[2] - z[2])
        if d > _ALIGN_TOL:
            return None
        s = 1.5 * (1 - d / _ALIGN_TOL)
        if e[1] and z[1]:
            return s + _ALIGN_STRONG_BONUS
        if not e[1] and not z[1]:
            return s + 0.2
        return s - 0.3 if z[1] else s - 0.1

    pairs = sorted((i, j, s) for i in range(len(en)) for j in range(len(cn))
                   if (s := score(en[i], cn[j])) is not None)
    best, back = {}, {}
    for i, j, s in pairs:
        b, bp = s, None
        for i2, j2, _ in pairs:
            if i2 < i and j2 < j and (i2, j2) in best:
                v = best[(i2, j2)] + s - _ALIGN_LEN_PENALTY * abs(
                    (en[i][2] - en[i2][2]) - (cn[j][2] - cn[j2][2]))
                if v > b:
                    b, bp = v, (i2, j2)
        best[(i, j)], back[(i, j)] = b, bp
    if not best:
        return []
    k = max(best, key=best.get)
    if best[k] <= 0:
        return []
    chosen = {}
    while k:
        chosen[k[1]] = k[0]
        k = back[k]
    return [(cn[j][0], en[i][0]) for j, i in sorted(chosen.items()) if cn[j][1]]


def _number_punct_cut(text: str, p: int) -> bool:
    """P533：切点 p 紧跟在数字内部的半角 `:` 或 `,` 后面（10:25、3:2、1,000）——不是句子边界，不许切。

    **全项目唯一一份数字规则。** 所有切点都要过 `_legal_cut`（逗号分支、`_split_time`、
    `_english_cut_at`）；候选与判断另在 `_segment_cuts`、`_every_comma_cut_fragments` 用它。
    全角「：」、带空格的写法、普通列举标点不在保护范围。
    看左右邻字时跳过 `SPLIT_MARK`：AI 断句的记号观众看不到，夹在「10:25」里也仍是一个数字
    （⑦.5 发现，用户 2026-10-23 定一起修）。"""
    def skip(i, step):
        while 0 <= i < len(text) and text[i] == SPLIT_MARK:
            i += step
        return i
    punct = skip(p - 1, -1)
    if not (0 < punct and p < len(text) and text[punct] in ",:"):
        return False
    left, right = skip(punct - 1, -1), skip(p, 1)
    return left >= 0 and right < len(text) and text[left].isdecimal() and text[right].isdecimal()


def _cut_forbidden(text: str, p: int) -> bool:
    """「这里不许下刀」——**全项目唯一一份**，所有产生切点的地方都问它（插入④ 3.1）：
    数字内部的半角 `:` `,`（P533，`_number_punct_cut`）；「——」两个「—」中间（P487：「——」算一个标点，
    只能切在它后面）。"""
    return _number_punct_cut(text, p) or (0 < p < len(text) and text[p - 1] == "—" and text[p] == "—")


# `cap` 本函数不用，但它和 `_pref_legal_cut` 由同一个 `check(..., cap)` 调用，签名必须一致
def _legal_cut(start, end, zh, words, zh_pos, idx, require_min_display, keep_whole, cap=None):
    """一刀的合法性检查：`_cut_forbidden` 不许的位置不切（P533 数字内部、P487「——」中间）、不重叠、
    在本屏时间内、逗号那一刀两边各 ≥0.84 秒、``keep_whole`` 不把好屏切坏。
    除 `_cut_forbidden` 那一条外，与按比例那一版的内层检查逐条相同；比例那一版只服务句末标点，碰不到这些位置。"""
    if _cut_forbidden(zh, zh_pos):
        return None
    left_end = float(_word_end(words[idx - 1]))
    right_start = float(_word_start(words[idx]))
    if left_end > right_start or not (start < left_end and right_start < end):
        return None
    if require_min_display and (left_end - start < MIN_DISPLAY_S
                                or end - right_start < MIN_DISPLAY_S):
        return None
    if keep_whole is not None:
        opens_mid, closes_mid = keep_whole
        if _screen_ok(zh, opens_mid, closes_mid):
            left_text = zh[:zh_pos].strip()
            seam_mid = not _ends_sentence(left_text)
            if not (_screen_ok(left_text, opens_mid, seam_mid)
                    and _screen_ok(zh[zh_pos:].strip(), seam_mid, closes_mid)):
                return None
    return zh_pos, idx, left_end, right_start


def _pref_legal_cut(start, end, zh, words, zh_pos, idx, require_min_display, keep_whole,
                    cap=MAX_DISPLAY_S):
    """最终排版的合法性检查：原来就要拆的、句末那一刀照旧；只为「一行放得下」而拆的那一刀，
    不从「……」中间拆（「——」中间由 `_legal_cut` 统一管）、不在守卫拉黑的屏上拆，且每块 ≥6 字或完整一句、≥1 秒、≤7 字/秒。"""
    found = _legal_cut(start, end, zh, words, zh_pos, idx, require_min_display, keep_whole)
    if found is None or _originally_needs_split(start, end, zh, cap=cap):
        return found
    if 0 < zh_pos < len(zh) and zh[zh_pos - 1] == "…" and zh[zh_pos] == "…":
        return None
    if _ends_sentence(zh[:zh_pos].strip()):
        return found
    if any(start - 1e-3 <= b < end for b in _RULES.block):
        return None
    _, _, left_end, right_start = found
    if (_pref_piece_ok(zh[:zh_pos].strip(), start, left_end)
            and _pref_piece_ok(zh[zh_pos:].strip(), right_start, end)):
        return found
    return None


def _pref_piece_ok(text, a, b) -> bool:
    units = _reading_units(_render_line(text))
    return ((units >= _FRAGMENT_UNITS or _ends_sentence(text))
            and b - a >= _PREF_PIECE_MIN_S - 1e-9
            and (b - a <= 0 or units / (b - a) <= _PREF_PIECE_MAX_CPS + 1e-9))


def _w_text(w) -> str:
    """词的文字：优先 ``word``（排版在词副本上改过的是它）。"""
    if isinstance(w, dict):
        return w.get("word") or w.get("text") or ""
    return getattr(w, "word", None) or getattr(w, "text", "") or ""


def _word_times_trusted(words) -> bool:
    """改动 4 条件①：屏里没有一个词超过 2 秒，也没有两个词之间空 ≥2 秒。"""
    if not words:
        return False
    for w in words:
        if float(_word_end(w)) - float(_word_start(w)) > _TRUST_WORD_MAX_S:
            return False
    for a, b in zip(words, words[1:]):
        if float(_word_start(b)) - float(_word_end(a)) >= _TRUST_GAP_MAX_S:
            return False
    return True


def _every_comma_cut_fragments(zh: str) -> bool:
    """改动 4 条件②：这一屏里每个逗号切下去，都会切出不足 6 字的一段。"""
    cuts = [p for p in (_cut_position(zh, m.end()) for m in _COMMA.finditer(zh))
            if 0 < p < len(zh) and not _cut_forbidden(zh, p)]   # P533/P487：与「能不能切」同一口径
    if not cuts:
        return True

    def units(part):
        return _reading_units(part.replace(SPLIT_MARK, "").strip().rstrip("，,；;、：:…—"))
    return all(min(units(zh[:p]), units(zh[p:])) < _FRAGMENT_UNITS for p in cuts)


def _long_screen_allowed(words, zh) -> bool:
    """改动 4（7 秒窄例外「乙」）：两个条件同时满足才允许超过 7 秒。**两处共用这一个判断。**"""
    return _word_times_trusted(words) and _every_comma_cut_fragments(zh)


def _split_three_line_screen(row):
    """§13.2 + §14.1：排出三行时，从已有换行里挑一处拆成两屏（AI 记号 > 标点 > 汉字中间）；
    书名号里的候选跳过；拆完任一屏不到 0.84 秒或读得太快就换一处；都不行保持原样。"""
    start, end, text, changed, words = row
    lines, breaks = _wrap_break_positions(text)
    if len(lines) < 3:
        return [row]
    for zh_pos, kind in sorted(breaks, key=lambda t: _THREE_LINE_RANK[t[1]]):
        if _inside_title(text, zh_pos):
            continue
        found = _english_cut_at(start, end, text, words, zh_pos)
        if found is None:
            continue
        _, idx, left_end, right_start = found
        left, right = text[:zh_pos].strip(), text[zh_pos:].strip()
        if _reads_too_fast(left, start, left_end) or _reads_too_fast(right, right_start, end):
            continue
        return [(start, left_end, left, changed, words[:idx]),
                (right_start, end, right, False, words[idx:])]
    return [row]


def _wrap_break_positions(text):
    """这一屏换行后，每处换行落在原文哪个位置、属于哪一类；开符号跟后文。"""
    lines = wrap_lines(text).split("\n")
    out, count = [], 0
    for line in lines[:-1]:
        count += len(_THREE_LINE_SKIP.sub("", line))
        seen, pos = 0, None
        for i, ch in enumerate(text):
            if _THREE_LINE_SKIP.match(ch):
                continue
            seen += 1
            if seen == count:
                pos = i + 1
                break
        if pos is None:
            continue
        q = pos
        while q < len(text) and _THREE_LINE_SKIP.match(text[q]):
            q += 1
        gap = text[pos:q]
        if re.search(r"[，,。．.！!？?、；;：:…—]", gap):
            kind = "标点"
        elif SPLIT_MARK in gap:
            kind = "AI标记"
        elif _is_han(text[pos - 1]) and q < len(text) and _is_han(text[q]):
            kind = "汉字中间"
        else:
            kind = "其他"
        while q > pos and text[q - 1] in _TITLE_OPENERS:
            q -= 1
        out.append((q, kind))
    return lines, out


def _english_cut_at(start, end, zh, words, zh_pos):
    """给定中文切点，按逗号那一刀的对位办法找英文词边界。"""
    n = len(words)
    if n < 2:
        return None
    for ignore_whitespace in (False, True):
        base = _ratio_index(zh, zh_pos, n, ignore_whitespace=ignore_whitespace)
        for idx in _anchor_indices(words, base):
            found = _pref_legal_cut(start, end, zh, words, zh_pos, idx, True, None)
            if found is not None:
                return found
    return None


def _reads_too_fast(text, a, b) -> bool:
    body = text.replace(SPLIT_MARK, "").strip()
    return b > a and _reading_units(body) / (b - a) > READING_CPS + 1e-9


def _find_safe_cut_by_ratio(start, end, zh, words, pattern, *, require_min_display=True,
                            keep_whole=None):
    """把译文切点按相对位置映射到真实原词边界，逐个验证合法性。

    保证：不把完整原词从中间切开、不伪造词级时间、每个画面时间结构合法。
    不保证：目标语言标点与源语言音频逐秒对应——用户已明确不要求这一点，
    只要求完整译文位于原句总时间范围内。

    返回 ``(译文切点, 原词切点索引, 左段结束, 右段开始)``；找不到返回 None。
    切点索引用于把 ``words`` 一起切开，否则递归时相对位置映射会算到错误区段。

    **两轮，不是两套。** 第一轮按原口径（含空格）算索引；只有当**全部**候选都失败之后，
    才用非空白口径重试一次（G12-B 修法 2）。合法性检查两轮共用，一条都不放宽。
    重试必须写在这里而不是 `_cut` 里：`_cut` 的计数器只在 ``found is None`` 时才加，
    重试若放在外层，第二轮成功了第一轮已经记下的「未能拆分」也收不回来。

    ``keep_whole=(opens_mid, closes_mid)``：P466。**只禁止「把好屏切坏」，不阻止
    「让坏屏变好」**——绝对否决会连能减轻问题的切法一起挡掉（新西兰 328.738s 实测
    退化成一屏 6 行）。
    """
    if len(words) < 2 or not zh:
        return None

    mid = len(zh) / 2
    candidates = sorted(
        dict.fromkeys(
            p for p in (_cut_position(zh, m.end()) for m in pattern.finditer(zh))
            if 0 < p < len(zh)
        ),
        key=lambda p: abs(p - mid),
    )
    for ignore_whitespace in (False, True):
        for zh_pos in candidates:
            base = _ratio_index(zh, zh_pos, len(words),
                                ignore_whitespace=ignore_whitespace)
            # 比例映射落到的那一对词时间重叠时，原来整条切点作废。真实数据里
            # 零时长词很常见（90 秒样本的 `we're` 就是 start==end），于是
            # 本该切开的句末切不动，一个完整句和一个半句被迫留在同一屏。
            # 正文切点不动，只把时间锚点在邻近词里挪一挪；
            # **每个锚点都把全部条件查完再决定用不用它**。
            for idx in _nearby_indices(base, len(words)):
                left_end = float(_word_end(words[idx - 1]))
                right_start = float(_word_start(words[idx]))
                if left_end > right_start:          # 原词时间重叠，这一刀不安全
                    continue
                if not (start < left_end and right_start < end):
                    continue
                if require_min_display and (
                    left_end - start < MIN_DISPLAY_S or end - right_start < MIN_DISPLAY_S
                ):
                    continue
                if keep_whole is not None:
                    opens_mid, closes_mid = keep_whole
                    if _screen_ok(zh, opens_mid, closes_mid):
                        left_text = zh[:zh_pos].strip()
                        seam_mid = not _ends_sentence(left_text)
                        if not (_screen_ok(left_text, opens_mid, seam_mid)
                                and _screen_ok(zh[zh_pos:].strip(), seam_mid, closes_mid)):
                            continue
                return zh_pos, idx, left_end, right_start
        if len("".join(zh.split())) == len(zh):
            break   # 正文里一个空白都没有，第二轮与第一轮逐位相同，不必空跑
    return None


def _nearby_indices(base, n_words):
    """从比例映射点向两侧交替搜索合法的时间锚点，最多 ``_MAP_SEARCH_SPAN`` 个词。"""
    yield base
    for step in range(1, _MAP_SEARCH_SPAN + 1):
        for idx in (base - step, base + step):
            if 1 <= idx <= n_words - 1:
                yield idx


def _cut_position(zh: str, pos: int) -> int:
    """把切点推到收尾引号/括号之后。

    `_SENT_END` 匹配的是 `。` 本身，`m.end()` 落在句号与右引号之间——
    于是 `”` 被单独切成一屏，再粘到下一句上（10 分钟样本 451–459 秒实见）。
    """
    while pos < len(zh) and zh[pos] in _TRAILING_CLOSERS:
        pos += 1
    return pos


def _ends_sentence(text: str) -> bool:
    """这段文字是不是停在一个完整句子的末尾（收尾引号/括号先剥掉）。"""
    stripped = (text or "").rstrip().rstrip(_TRAILING_CLOSERS).rstrip()
    return bool(stripped) and bool(_SENT_END.fullmatch(stripped[-1]))


def _has_internal_sentence_end(text: str) -> bool:
    """这一屏里面有没有一道「一句话说完了」的缝（屏尾那一处不算）。"""
    body = (text or "").rstrip()
    while body and body[-1] in _TRAILING_CLOSERS:
        body = body[:-1].rstrip()
    if body and _SENT_END.fullmatch(body[-1]):
        body = body[:-1]
    return bool(_SENT_END.search(body))


def _screen_ok(text: str, opens_mid: bool, closes_mid: bool) -> bool:
    """P466 不变量：**一屏要么全是完整句子，要么是同一句话的一段。**

    屏内有句界说明它装了不止一句；此时任何一端还挂着半句，画面上就是
    「完整句 + 半句」（「我现在就吃了吗？ 有点酸，」）。屏内没有句界 = 整屏在同一句里。
    两句完整短句同屏（「好梦。 已经吃下去了。」）合法，长句分屏也合法。
    """
    if _has_internal_sentence_end(text):
        return not (opens_mid or closes_mid)
    return True


def _row_opens_mid(rows, i) -> bool:
    """第 i 屏是不是从一句话的中间开始的 —— **看上一屏的文字，不存字段**。"""
    return i > 0 and not _ends_sentence(rows[i - 1][2])


def _row_closes_mid(row) -> bool:
    """这一屏是不是停在一句话的中间 —— 看它自己的文字。"""
    return not _ends_sentence(row[2])


def _word_start(w):
    return w["start"] if isinstance(w, dict) else getattr(w, "start", 0.0)


def _word_end(w):
    return w["end"] if isinstance(w, dict) else getattr(w, "end", 0.0)


# 插入④ 施工 B D14（P542 显示层）：识别给出的「零长度、又离前一个词 1 秒以上」的词时间标错了
# （新西兰 23:11「Cove.」标在 10 秒后）。算停顿（D11、D13）、算「这一屏话说完」（D9）时绕开它；
# 只隔离，不改识别结果。**全项目只此一份**，拆句也从这里取。
_BAD_WORD_MAX_S = 0.02
_BAD_WORD_GAP_S = 1.0


def _word_time_bad(words, k) -> bool:
    if k <= 0:
        return False
    w = words[k]
    return (float(_word_end(w)) - float(_word_start(w)) < _BAD_WORD_MAX_S
            and float(_word_start(w)) - float(_word_end(words[k - 1])) > _BAD_WORD_GAP_S)


# 插入④ 施工 B R1：AI 每一刀的「刀前词｜刀后词」按刀所在的位置记——位置 = 刀前面有几个「字」
# （去掉空白、断点记号和标点后的字数，与拆句 `_cut_positions` 同一个数法）。按第几刀记会在
# `_cut_positions` 扔掉末尾一刀后全错位。
_CUT_PUNCT = re.compile(r'[，,。．.！!？?、；;：:…—“”‘’"\'（）()《》「」『』【】]')


def _cut_key(text: str) -> int:
    return len(_CUT_PUNCT.sub("", "".join(text.split())))


def _en_tokens(text: str) -> list[str]:
    """英文词切小块再比：破折号、连字符两边拆开，去标点、转小写（D6）。``⏸`` 停顿标记不是词。"""
    return [t for t in (re.sub(r"[^a-z0-9']", "", x.lower().replace("’", "'"))
                        for x in re.split(r"[\s—–-]+", text or "")) if t]


# ── 内部：时间修复（唯一执行一次的地方）──────────────────────────

def _repair_times(drafts, rep: TimelineReport, ai_spans=()):
    """收紧重叠 → 空档补到 0.84 → 合并短屏 → 合并后重拆 → 7 秒封顶
    → 补阅读时间 → 仍偏短的再合并一轮 → 记偏短。

    补救只允许向后延长、只吃真空白（R-7：字幕提前出现完全禁止）；**不推后下一屏**
    （插入④ 施工 B D4，管所有屏：0f 模拟 195 屏被推后出现）。唯一例外是 C19
    （`_nudge_next_for_min`：为凑 0.84 秒最多推后 0.15 秒、不连锁、只救完整一句）。两屏任何时候不重叠。
    """
    fixed = []
    prev_end = None
    for start, end, text, changed, words in drafts:
        if prev_end is not None and start < prev_end:
            start = prev_end
        if end < start:
            end = start
        fixed.append([start, end, text, changed, words])
        prev_end = max(prev_end or end, end)

    for i, row in enumerate(fixed):
        start, end = row[0], row[1]
        limit = fixed[i + 1][0] if i + 1 < len(fixed) else None

        if end - start < MIN_DISPLAY_S:
            want = start + MIN_DISPLAY_S
            end = want if limit is None else min(want, limit)

        row[0], row[1] = start, end

    # 合并必须发生在封顶**之前**：级联合并会把跨度加长，
    # 若先封顶再合并，合出来的条目可以突破 7 秒，A5（每屏最长 7 秒）就守不住了。
    # G12 第三批 B（排版 §12 第 3 步）：合不合并按原来的换行判断；最终排版里，
    # 会并出「两行各是一句完整的话」、而这一屏又不是 ≤4 字语气词的，不并（先交给借时间）。
    pushed: dict = {}        # C19：被推过的屏（跨两轮合并都记着，不连锁）
    _nudge_next_for_min(fixed, pushed)
    if _RULES.pref:
        fixed = _merge_still_short(fixed, choose=_best_merge_window_pref, wrap=_base_wrap)
    else:
        fixed = _merge_still_short(fixed, wrap=_base_wrap)

    # 重拆必须夹在合并与封顶**之间**（G12-B 修法 1）：
    #   * 在合并之后——它的输入就是合并产物的文本与词表；
    #   * 在封顶之前——重拆会缩短每段时长，若先封顶，`capped_no_legal_cut`
    #     会记在一个随后被拆开的条目上，记的账对不上交出去的东西。
    fixed = _resplit_over_two_lines(fixed, ai_spans)

    capped = set()   # ⑦.5 ⑤：30 秒封顶记过账的屏，最后的 7 秒封顶不再重复记
    for i, row in enumerate(fixed):
        if row[1] - row[0] > _NARROW_CAP_S:
            capped.add(id(row))
            # A9：切不动才会走到这里（能切的在 _split_to_fit 已切开）。
            # 封顶后照常交付，但必须记账让用户看见。7 秒封顶在最后（改动 4）。
            rep.capped_no_legal_cut.append(
                (i + 1, round(row[0], 3), round(row[1] - row[0], 3))
            )
            row[1] = row[0] + _NARROW_CAP_S

    # 阅读时间放在拆、合、重拆、封顶全部完成之后，按最终文字算一次——
    # 早算会算错：后面还要合并与重拆，最终一屏的文字已经不是当时那份了。
    _fill_reading_gaps(fixed)
    # 插入④ 施工 B D4：不推后下一屏。补完空白仍不到 0.84 秒的再合并一轮——这一轮**不带**最终排版的
    # 「两行各一整句不并」那道关（`_best_merge_window_pref`），否则 D16 放进来的「一整句+半句」候选
    # 在第一轮被否掉后就没处去了（⑤ 走查 R4；实验 0i 也是这样）。
    if any(r[1] - r[0] < MIN_DISPLAY_S - 1e-9 for r in fixed):
        _nudge_next_for_min(fixed, pushed)
        fixed[:] = _merge_still_short(fixed, wrap=_base_wrap)

    # 记偏短必须在补救之后：补好的屏不算偏短（P468）。
    shortest = None
    for i, row in enumerate(fixed, 1):
        dur = row[1] - row[0]
        if shortest is None or dur < shortest:
            shortest = dur
        if dur < MIN_DISPLAY_S - 1e-9:
            rep.below_min_display.append((i, round(row[0], 3), round(dur, 3)))
    rep.shortest_s = None if shortest is None else round(shortest, 3)

    # G12 第三批 B（改动 4）：7 秒封顶放在最后；只有「词时间可信 且 每个逗号切下去都碎」的屏不封。
    for i, row in enumerate(fixed, 1):
        if row[1] - row[0] > MAX_DISPLAY_S and not _long_screen_allowed(row[4], row[2]):
            if id(row) not in capped:
                rep.capped_no_legal_cut.append((i, round(row[0], 3), round(row[1] - row[0], 3)))
            row[1] = row[0] + MAX_DISPLAY_S

    return [(r[0], r[1], r[2], r[3], r[4]) for r in fixed]


def _reading_units(text: str) -> float:
    """按奈飞口径数字符：全角计 1，拉丁字母/数字/半角标点/空格计 0.5，换行不计。

    「— … “ ” ‘ ’ ·」Unicode 标为宽度不定，中文字幕里按全角计。
    调用方负责先渲染——统计一律按观众看到的文字。
    """
    total = 0.0
    for ch in text:
        if ch == "\n":
            continue
        wide = unicodedata.east_asian_width(ch) in ("W", "F") or ch in _FULL_WIDTH_ZH_PUNCT
        total += 1.0 if wide else 0.5
    return total


def _line_units(text: str) -> float:
    """行长计数，与阅读速度**同一套口径**。"""
    return _reading_units(text)


def _reading_time_s(text: str) -> float:
    """这段文字按每秒 9 单位读完需要多久（未加余量）。"""
    return _reading_units(text) / READING_CPS


def _reading_need_s(row) -> float:
    """这一屏的阅读时间目标：按**最终显示文字**（渲染后、含前缀）计数，
    再加一个 ASS 取整精度（10ms）——写出器分别取整起止时间，最多吃掉 10ms，
    不留这点余量，内存里的每秒 9.00 单位写进文件就成了 9.02。不超过 7 秒。"""
    return min(_reading_time_s(_decorate(row[2], row[3])) + _ASS_QUANTUM, _NARROW_CAP_S)


def _fill_reading_gaps(fixed) -> None:
    """只向后吃已有空档补阅读时间：不改下一屏起点，不超过 7 秒。"""
    for i, row in enumerate(fixed):
        limit = fixed[i + 1][0] if i + 1 < len(fixed) else None
        need = _reading_need_s(row)
        if row[1] - row[0] < need:
            want = row[0] + need
            row[1] = want if limit is None else min(want, limit)


_NUDGE_MAX_S = 0.15      # C19：为凑 0.84 秒，下一屏最多推后这么多（E1 2026-10-28 由 0.1 改：0.72 秒短句要推 0.12）


def _nudge_next_for_min(fixed, pushed: dict) -> None:
    """C19（D4 唯一例外，用户 2026-10-27 看并排视频后定）：差一点点就够 0.84 秒的短屏，
    把紧挨的下一屏往后挪，让它单独成屏，不被并进别人的话（「谢谢」并进「天哪」）。

    识别的词时间按 0.08 秒一格，说 10 格的短句正好 0.80 秒、永远差 0.04 秒。
    六条全满足才推：这一短屏本身是完整一句——自己以句末收尾、前一屏也以句末收尾
    （半截句如 A9「…最好的｜朋友。」照旧并回去；句号可能是 `_internal_zh_texts` 按英文补的）；
    只为凑 0.84（不为阅读时间）；推 ≤0.15 秒；只推下一屏，被推过的屏
    不再推它后面的（``pushed`` 按 id 记、同时存住这一屏本身，免得它被合并释放后 id 被新屏复用；
    两轮合并共用）；下一屏不换人；推完下一屏仍够读。
    推不了的照旧交给 `_merge_still_short` 并屏。就地改 ``fixed``。"""
    for i in range(len(fixed) - 1):
        row, nxt = fixed[i], fixed[i + 1]
        if (id(row) in pushed or row[1] - row[0] >= MIN_DISPLAY_S - 1e-9 or nxt[3]
                or not _ends_sentence(row[2]) or (i and not _ends_sentence(fixed[i - 1][2]))):
            continue
        need = MIN_DISPLAY_S - (nxt[0] - row[0])
        if not 0 < need <= _NUDGE_MAX_S + 1e-9:
            continue
        if nxt[1] - (nxt[0] + need) < max(MIN_DISPLAY_S, _reading_need_s(nxt)) - 1e-9:
            continue
        nxt[0] += need
        row[1] = nxt[0]
        pushed[id(nxt)] = nxt


def _merge_still_short(fixed, *, choose=None, wrap=None):
    """补到 0.84 之后仍然偏短的，与相邻条目合并。

    向后延长会被下一条的开始时间挡住——两条字幕背靠背时，前一条延不动。
    真实数据证明这不是边角：英文 26 条、日文 18 条卡在这里，最短仍是 0.080 秒
    （P402 的「哦。」原样）。

    合并规则：不得跨说话人变化；不得跨长静音；**不得拼出「完整句 + 半句」**（P466，
    见 `_best_merge_window`）；不得超过 7 秒。不推迟下一屏来代替合并（插入④ 施工 B D4）。
    仍不足的保留完整文字与有效时间，交给后面的记账。
    """
    choose = (lambda f, i: _best_merge_window(f, i, wrap=wrap)) if choose is None else choose
    changed = True
    while changed:
        changed = False
        for i, row in enumerate(fixed):
            if row[1] - row[0] >= MIN_DISPLAY_S - 1e-9:
                continue
            best = choose(fixed, i)
            if best is None:
                continue
            lo, hi = best
            merged_text = _MERGE_JOINER.join(r[2] for r in fixed[lo:hi + 1])
            merged = [
                fixed[lo][0], max(r[1] for r in fixed[lo:hi + 1]),
                merged_text,
                fixed[lo][3],
                # 词表按原序拼接。顺序颠倒不会报错——它会让 `left_end > right_start`
                # 恒成立，于是重拆**永远静默失败**：没有异常，只有难看的字幕。
                [w for r in fixed[lo:hi + 1] for w in r[4]],
            ]
            fixed[lo:hi + 1] = [merged]
            changed = True
            break
    return fixed


def _window_is_joinable(fixed, lo, hi) -> bool:
    """窗口内每一道缝都允许合并：不跨说话人变化、不跨长静音。"""
    for k in range(lo, hi):
        if fixed[k + 1][3]:
            return False
        if fixed[k + 1][0] - fixed[k][1] >= _LONG_SILENCE_S:
            return False
    return True


_MERGE_PREFER_HAN = 20    # 插入④ 施工 B D16：老规矩拼出来不超过 20 字就用它
_MERGE_MAX_HAN = 25       # D16：硬上限，都超过就不拼


def _best_merge_window(fixed, i, *, wrap=None):
    """D16（用户 2026-10-26 定，门槛 20）：先按老规矩（P466 不拼「一整句+半句」）挑；拼出来 ≤20 字就用它。
    超过 20 字时，允许只拼左边一张或右边一张（可以是「一整句+半句」），和老规矩那个一起比，
    取字数最少的（同字数时老规矩优先，再取跨度小的）。都超过 25 字就不拼。"""
    def han_of(lo, hi):
        return _han_count(_MERGE_JOINER.join(r[2] for r in fixed[lo:hi + 1]))

    best = _best_p466_window(fixed, i, wrap=wrap)
    if best is not None and han_of(*best) <= _MERGE_PREFER_HAN:
        return best
    candidates = [] if best is None or han_of(*best) > _MERGE_MAX_HAN else [(han_of(*best), 0, best)]
    for lo, hi in ((i - 1, i), (i, i + 1)):
        if lo < 0 or hi >= len(fixed) or not _window_is_joinable(fixed, lo, hi):
            continue
        span = fixed[hi][1] - fixed[lo][0]
        if han_of(lo, hi) <= _MERGE_MAX_HAN and span <= MAX_DISPLAY_S + 1e-9:
            candidates.append((han_of(lo, hi), span, (lo, hi)))
    return min(candidates)[2] if candidates else None


def _best_p466_window(fixed, i, *, wrap=None):
    """围住第 ``i`` 条短屏、跨度最小的**合法**窗口。合法 = 缝都可合 +
    合出来的屏满足 P466 不变量 + 不超过 7 秒。

    为什么不能只看左右各一条：真实数据里出现过「一个完整短句被两个半句夹住」——
    欧洲女孩 1663.000s 的「我那时候觉得开口说真的很难。」，左右单独合都会造出
    「完整句 + 半句」，只有把整句连同它的另外半边一起收进同一屏才合法。

    **7 秒上限同时是搜索的剪枝边界**：向外每多收一条跨度只增不减，超过 7 秒立刻停。
    """
    best, best_key = None, None
    lo = i
    while lo >= 0:
        hi = max(i, lo + 1)
        while hi < len(fixed):
            if not _window_is_joinable(fixed, lo, hi):
                break
            span = max(r[1] for r in fixed[lo:hi + 1]) - fixed[lo][0]
            if span > MAX_DISPLAY_S + 1e-9:
                break                      # 再往右只会更长
            text = _MERGE_JOINER.join(r[2] for r in fixed[lo:hi + 1])
            if _screen_ok(text, _row_opens_mid(fixed, lo), _row_closes_mid(fixed[hi])):
                # 排版与跨度一起排序：先避免新造三行以上，再取跨度最小。
                lines = len(_decorate(text, fixed[lo][3], wrap).split("\n"))
                key = (lines > 2, span)
                if best_key is None or key < best_key:
                    best, best_key = (lo, hi), key
            hi += 1
        if lo < i:
            if not _window_is_joinable(fixed, lo, i):
                break
            if max(r[1] for r in fixed[lo:i + 1]) - fixed[lo][0] > MAX_DISPLAY_S + 1e-9:
                break                      # 再往左只会更长
        lo -= 1
    return best


def _best_merge_window_pref(fixed, i):
    """最终排版：并出「两行各是一句完整的话」、而这一屏又不是 ≤4 字语气词的，不并。"""
    best = _best_merge_window(fixed, i, wrap=_base_wrap)
    if best is None:
        return None
    lo, hi = best
    text = _MERGE_JOINER.join(r[2] for r in fixed[lo:hi + 1])
    if (_has_internal_sentence_end(text) and len(_base_wrap(text).split("\n")) >= 2
            and _reading_units(_render_line(fixed[i][2])) > _FILLER_MAX_UNITS):
        return None
    return best


def _resplit_over_two_lines(fixed, ai_spans=()):
    """合并之后仍然两行放不下的条目，再切一刀（G12-B 修法 1）。

    `_merge_still_short` 把两条短屏粘成一条，粘完之后可能放不下两行。
    **合并制造的问题，由合并之后的一步收拾，而不是让它出厂。**

    * **判断行数问 `_decorate`**：带说话人前缀时首行预算更小，用裸 `wrap_lines`
      会把带前缀的字幕判成两行、实际渲染三行。
    * **先试句末，再试逗号**（P466）：先合后拆同样可能切出「完整句 + 半句」
      （标准模式 52.090s「咱们去机场吧，好吗？ 太久没穿我的登山靴了，」）。
    * **`require_min_display=True`**：重拆自己再造出短屏就成了循环。
    * **只切一刀，不递归**：切完仍超两行的，走 `over_two_lines` 记账——照常交付，但不静默。
    """
    out = []
    for i, row in enumerate(fixed):
        start, end, text, changed, words = row
        # 插入④ 施工 B D1 + D17：碰到 AI 句的屏不重拆（程序没有词证据，只能按比例猜）；程序句照旧（U3）
        if len(_decorate(text, changed).split("\n")) <= 2 or _touches(start, end, ai_spans):
            out.append(row)
            continue
        keep_whole = (_row_opens_mid(fixed, i), _row_closes_mid(row))
        found = None
        for pattern in (_SENT_END, _COMMA):
            found = _find_safe_cut(start, end, text, words, pattern,
                                   require_min_display=True, keep_whole=keep_whole,
                                   cap=_NARROW_CAP_S)
            if found is not None:
                break
        if found is None:
            out.append(row)     # 切不动就整条保留，交给封顶与超行记账
            continue
        zh_pos, idx, left_end, right_start = found
        # 左半仍是第一屏（前缀贴在它上面），右半不再是——与 `_cut` 同一约定。
        out.append([start, left_end, text[:zh_pos].strip(), changed, words[:idx]])
        out.append([right_start, end, text[zh_pos:].strip(), False, words[idx:]])
    return out


# ── 内部：刀② 换行 + 标点渲染 + 说话人前缀 ────────────────────────
#
# G6：这里原来写的是「换行 + 装饰」。「装饰」是个筐，什么都能往里装，
# 装了没人看得出来——`[?]` 存疑标记就是这么混进用户成品的。
# 改成明写的清单之后，下一个想往这里加东西的人，会发现自己是在往一个有明确清单的地方加东西。
#
# **这个函数只允许做三件事：换行、标点渲染、加说话人前缀。**
# G12 第三批 B 收尾：删掉了「短两行合回一行」（16 单位以内本来就不折），加了标点渲染。
# 要加第四件，同样先改这里。


def _decorate(text, changed, wrap=None):
    wrap = wrap_lines if wrap is None else wrap
    body = wrap(text, first_max=_first_line_budget(True)) if changed else wrap(text)
    return _SPEAKER_PREFIX + body if changed else body


def wrap_lines(text: str, max_chars: float | None = None, *,
               first_max: float | None = None) -> str:
    """刀②：一屏之内怎么换行，返回**渲染后**的各行。仍是一条，时间不变。

    G12 第三批 B（排版 §12 第 2 步、§14.2、§15.2）：先用原来的断点（AI 标记也要过
    「不许换行」名单）；放不下两行才允许在两个汉字之间换（规则 D）；含书名号的字幕，
    这两轮都不在书名里换，仍放不下才放开书名。打了补丁反而排不进两行 → 退回没打补丁的换行。
    基准排版（``_RULES.pref`` 为假）只用原来的断点。
    """
    if not _RULES.pref:
        return _base_wrap(text, max_chars, first_max=first_max)
    rounds = [functools.partial(_tier_marked, allow_title=False),
              functools.partial(_tier_d5, allow_title=False)]
    if "《" in text:
        rounds.append(functools.partial(_tier_d5, allow_title=True))
    for tier in rounds:
        out = _wrap_with(text, max_chars, first_max, tier)
        if not _over(out):
            break
    ref = _wrap_d(text, max_chars, first_max=first_max)
    if _over(out) and not _over(ref):
        return ref
    return out


def _base_wrap(text, max_chars=None, *, first_max=None):
    """原来的换行：只在标点、空格、中外文交界处断（书名号里的断点排最末）。"""
    return _wrap_with(text, max_chars, first_max, _base_tier)


def _wrap_d(text, max_chars=None, *, first_max=None):
    """原来的断点放不下两行时，才允许规则 D 的汉字间断点（实验 hanbreak.make_wrap("D")）。"""
    out = _base_wrap(text, max_chars, first_max=first_max)
    if not _over(out):
        return out
    return _wrap_with(text, max_chars, first_max, _tier_d)


def _over(text: str) -> bool:
    """超过两行，或有一行超过 16 单位。"""
    lines = text.split("\n")
    return len(lines) > 2 or any(_line_units(x) > LINE_MAX_UNITS + 1e-9 for x in lines)


def _wrap_with(text: str, max_chars, first_max, tier) -> str:
    """按给定的断点等级函数 ``tier`` 换行。

    规则：
      1. 渲染后整行（含前缀预算）放得下 → 不折。
      2. 放得进两行 → 在合法断点里挑，排序见 `_best_break`。
      3. 放不进两行 → 逐行尽量填满（行数 > 2 交给 `_needs_split` 去拆屏）。

    断点在**带标点的内部文字**上选（标点是最可靠的断点），行长按**渲染后**每行算。
    合法断点：不切开拉丁单词/数字组合；标点与收尾引号留在上一行行尾；
    上一行不以开引号结尾；不拆开「——」「……」；两个汉字之间能不能断由 ``tier`` 决定。
    找不到合法断点就不折（不硬切）。
    """
    max_chars = LINE_MAX_UNITS if max_chars is None else max_chars   # 调用时读取，常量改了立即生效
    first_max = max_chars if first_max is None else first_max
    if _line_units(_render_line(text)) <= first_max + 1e-9:
        return _render_line(text)
    k = _best_break(text, first_max, max_chars, tier=tier)
    if k is not None:
        return _render_line(text[:k]) + "\n" + _render_line(text[k:])
    # 放不进两行：逐行尽量填满。第一行在上限内找不到合法断点时，退到最近的合法断点
    # （这一行会超长，但行数如实变多，`_needs_split` 才看得见要拆屏）。
    lines, rest, budget = [], text, first_max
    while _line_units(_render_line(rest)) > budget + 1e-9:
        k = _best_break(rest, budget, None, tier=tier)
        if k is None:
            k = _best_break(rest, float("inf"), None, earliest=True, tier=tier)
        if k is None:
            break
        lines.append(rest[:k])
        rest = rest[k:]
        budget = max_chars
    lines.append(rest)
    return "\n".join(x for x in (_render_line(line) for line in lines) if x)


def _render_line(line: str) -> str:
    """P471 标点渲染（奈飞简中规范）。**全项目唯一实现**，只作用于一行。

    * 中文逗号、句号：行内换成一个半角空格，行首行尾删除，连续空格合成一个；
      紧贴右引号/右括号时删掉它，空格放到收尾符后面（合同增量 #2：新西兰 462.46s）。
    * 半角 `,` `.`：只在紧跟汉字或中文收尾标点时按上一条处理（3.5、Joe's 不动）。
    * 紧跟汉字的半角 `?` `!` 换成全角；问号、叹号、冒号、引号、省略号、破折号保留。
    * 顿号只保留在列举中间，行末的删掉。
    * AI 断句的断点记号 `SPLIT_MARK` 最先删掉，不留任何痕迹（P475）。
    """
    s = line.replace(SPLIT_MARK, "")
    s = re.sub(rf"[，。]([{_CLOSER_CLASS}]+)", r"\1 ", s)
    s = re.sub(rf"(?<=[{_HAN}{_ZH_CLOSERS}])[,.](?!\d)", " ", s)
    s = s.replace("，", " ").replace("。", " ")
    s = re.sub(rf"(?<=[{_HAN}])\?", "？", s)
    s = re.sub(rf"(?<=[{_HAN}])!", "！", s)
    s = re.sub(r" {2,}", " ", s).strip()
    return re.sub(r"、+$", "", s).strip()


def _is_word_char(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch in "'.-")


def _is_han(ch: str) -> bool:
    return "㐀" <= ch <= "鿿" or "豈" <= ch <= "﫿"


def _break_tier(text: str, k: int) -> int | None:
    """在 text[k] 之前断行的等级（越小越好）；None = 不合法。

    1 句末标点后 > 2 逗号类标点后 > 3 中文旁已有的空格 > 4 汉字与拉丁字母/数字交界
    > 5 两个英文单词之间的空格。句末与逗号**分两级**（合同增量 #3）：合成一级的话，
    「同级优先上短下长」会把下一句的开头挂到上一行。
    """
    left, right = text[:k].rstrip(), text[k:].lstrip()
    if not left or not right:
        return None
    a, b = text[k - 1], text[k]
    if right[0] in _NO_LINE_START or left[-1] in _OPENERS:
        return None
    if _is_word_char(a) and _is_word_char(b):
        return None                       # 拉丁单词、数字组合中间
    if a == b and a in "—…":
        return None                       # 「——」「……」中间
    core = left.rstrip(_TRAILING_CLOSERS)
    if core and _SENT_END.fullmatch(core[-1]):
        return 1
    if core and (_COMMA.fullmatch(core[-1]) or core.endswith("——") or core.endswith("……")):
        return 2
    if a.isspace() or b.isspace():
        # 两个英文单词之间的空格（「Tame Impala」）是最弱的断点，排在中外文交界之后；
        # 中文片段之间、标点旁的空格（合并连接符）排第三。AI 断句的断点记号 `SPLIT_MARK`
        # 也是空白字符，走同一条规则，等级与空格完全相同（P475）。
        before, after = left[-1], right[0]
        return 5 if _is_word_char(before) and _is_word_char(after) else 3
    if (_is_han(a) and _is_word_char(b)) or (_is_word_char(a) and _is_han(b)):
        return 4
    return None


def _inside_title(text: str, k: int) -> bool:
    """位置 k 落在书名号《…》里面。"""
    head = text[:k]
    return head.count("《") > head.count("》")


def _base_tier(text: str, k: int, allow_title: bool = True) -> int | None:
    """原来的断点等级；书名号里面的断点排最末（8），``allow_title=False`` 时不许（排版 §14.2）。"""
    tier = _break_tier(text, k)
    if tier is not None and 0 < k < len(text) and _inside_title(text, k):
        return 8 if allow_title else None
    return tier


_JIEBA = None
_JIEBA_FAILED = False


def _jieba():
    """分词工具 jieba，首次用时加载；加载失败只记一次日志，以后都不再用（排版 §14.3）。"""
    global _JIEBA
    if _JIEBA is None and not _JIEBA_FAILED:
        try:
            import jieba
            jieba.setLogLevel(logging.ERROR)
            _JIEBA = jieba
        except Exception as exc:  # noqa: BLE001  任何加载失败都退回现行换行
            _jieba_failed(exc)
    return _JIEBA


def _jieba_failed(exc) -> None:
    """分词工具不可用：不在两个汉字中间换行 / 拆屏，等于现行正式软件换行；任务不失败。"""
    global _JIEBA, _JIEBA_FAILED
    _JIEBA = None
    if not _JIEBA_FAILED:
        _log.warning("分词工具不可用，换行退回现行规则（不在两个汉字中间换行）：%r", exc)
    _JIEBA_FAILED = True
    _jieba_bounds.cache_clear()
    _jieba_tokens.cache_clear()
    _pos_tags.cache_clear()


@functools.lru_cache(maxsize=None)
def _jieba_bounds(text: str) -> frozenset:
    """分词工具切出的词缝位置。词典在第一次分词时才加载，出错也退回（确认轮外审）。"""
    jb = _jieba()
    if jb is None:
        return frozenset()
    bounds, pos = set(), 0
    try:
        for word in jb.cut(text):
            pos += len(word)
            bounds.add(pos)
    except Exception as exc:  # noqa: BLE001
        _jieba_failed(exc)
        return frozenset()
    return frozenset(bounds)


@functools.lru_cache(maxsize=None)
def _jieba_tokens(text: str) -> tuple:
    if _jieba() is None:
        return ()
    try:
        return tuple(_jieba().cut(text))
    except Exception as exc:  # noqa: BLE001
        _jieba_failed(exc)
        return ()


@functools.lru_cache(maxsize=None)
def _pos_tags(text: str) -> dict:
    """每个词开头位置的词性；标注出错时退回（不给优先级，不会断到禁止的位置）。"""
    if _jieba() is None:
        return {}
    starts, pos = {}, 0
    try:
        import jieba.posseg as posseg
        for word, flag in posseg.cut(text):
            starts[pos] = flag
            pos += len(word)
    except Exception as exc:  # noqa: BLE001
        _jieba_failed(exc)
        return {}
    return starts


def _phrase_head_tier(text: str, k: int) -> int:
    tag = _pos_tags(text).get(k, "")
    return 6 if tag[:2] in _PHRASE_HEAD_TAGS or tag[:1] in ("p", "d", "c", "v", "t") else 7


def _tier_c(text: str, k: int) -> int | None:
    """规则 C：原来的断点之外，允许在分词工具的词缝上断两个汉字；虚词不和它抓住的词分开。"""
    tier = _base_tier(text, k)
    if tier is not None:
        return tier
    if not 0 < k < len(text):
        return None
    if not (_is_han(text[k - 1]) and _is_han(text[k])):
        return None
    if k not in _jieba_bounds(text):
        return None
    if text[k] in _NO_BREAK_BEFORE or any(text[:k].endswith(w) for w in _NO_BREAK_AFTER):
        return None
    return 6


def _tier_d(text: str, k: int) -> int | None:
    """规则 D：规则 C + 优先断在新短语开头前面（介词、副词、连词、动词、时间词）。"""
    tier = _tier_c(text, k)
    return _phrase_head_tier(text, k) if tier == 6 else tier


def _left_ends_with_no_break_word(left: str) -> bool:
    """断点左边最后一个词（按分词工具的整词比：「机会」不算「会」）在「不许换行」名单里。"""
    tokens = _jieba_tokens(left)
    return bool(tokens) and tokens[-1] in _NO_BREAK_AFTER


def _tier_marked(text: str, k: int, allow_title: bool = True) -> int | None:
    """原来的断点；翻译后 AI 标的断点落在两个汉字之间时也过「不许换行」名单（排版 §15.2）。

    认「是不是 AI 标记」看这个位置有没有断点记号，不看等级（§14.2）。
    """
    tier = _base_tier(text, k, allow_title)
    if tier is not None and SPLIT_MARK in text[max(0, k - 1):k + 1]:
        left = text[:k].replace(SPLIT_MARK, "").replace(" ", "")
        right = text[k:].replace(SPLIT_MARK, "").replace(" ", "")
        if left and right and _is_han(left[-1]) and _is_han(right[0]):
            if _left_ends_with_no_break_word(left) or right[0] in _NO_BREAK_BEFORE:
                return None
    return tier


def _tier_d5(text: str, k: int, allow_title: bool = True) -> int | None:
    """最终排版用的断点等级：``_tier_marked`` + 规则 D；书名里的汉字间断点前两轮不许、放开时排最末。"""
    tier = _tier_marked(text, k, allow_title)
    if tier is not None:
        return tier
    if not 0 < k < len(text):
        return None
    if _inside_title(text, k) and not allow_title:
        return None
    if not (_is_han(text[k - 1]) and _is_han(text[k])):
        return None
    if k not in _jieba_bounds(text):
        return None
    if (text[k] in _NO_BREAK_BEFORE or any(text[:k].endswith(w) for w in _NO_BREAK_AFTER)
            or _left_ends_with_no_break_word(text[:k])):
        return None
    if _inside_title(text, k):
        return 8
    return _phrase_head_tier(text, k)


def _best_break(text: str, first_max: float, second_max: float | None, *, earliest=False,
                tier=None):
    """挑断点。行长一律按渲染后的每一行算。

    两行模式（second_max 给定）：两行都放得下的断点里，按
    (上一行只有一两个单位, 等级, 上一行比下一行长, 两行差) 取最小——
    先避开上一行只剩一两个字，再看断点等级，同级里优先上短下长，最后取两行最接近。
    多行模式（second_max=None）：只要求第一行放得下，取 (等级, -第一行长度) 最小；
    earliest=True 时取最靠前的合法断点（用于第一行怎么都放不下的情况）。
    """
    tier_of = _base_tier if tier is None else tier
    best, best_key = None, None
    for k in range(1, len(text)):
        tier = tier_of(text, k)
        if tier is None:
            continue
        u1 = _line_units(_render_line(text[:k]))
        if u1 > first_max + 1e-9:
            continue
        if second_max is not None:
            u2 = _line_units(_render_line(text[k:]))
            if u2 > second_max + 1e-9:
                continue
            key = (u1 <= 2 + 1e-9, tier, u1 > u2 + 1e-9, abs(u1 - u2))
        elif earliest:
            key = (u1,)
        else:
            key = (tier, -u1)
        if best_key is None or key < best_key:
            best, best_key = k, key
    return best


# ── 内部：结构检查 ──────────────────────────────────────────────

def _check_types_and_bounds(entries):
    """拒绝错误类型，并把「每屏最长 7 秒（带 long_ok 标签的例外屏除外）」在运行时也守住。

    为什么需要这一层：旧的 ``Segment`` 恰好也有 ``.start`` / ``.end`` / ``.text``，
    结构上完全合法——把 Segment 列表直接喂给 ``build_srt()``，它会一声不吭地
    写出一份超过 7 秒的字幕。外审五分钟内就误传了一次。

    **0.84 秒不在这里查**（P468，用户 2026-09-16 确认）：它是补救目标，不是交付门槛。
    补救用尽仍偏短的屏照常交付、记进 ``below_min_display``、界面提示“N 条字幕显示偏短”。
    以前在这里整份报错，一屏 0.5 秒就让用户什么都拿不到。时长为 0 或取整后为 0
    仍由 ``_check_at`` 拦下。
    """
    for i, e in enumerate(entries, 1):
        if not isinstance(e, DisplayEntry):
            raise DisplayTimelineError(
                f"字幕第 {i} 条不是最终显示条目（收到 {type(e).__name__}）——"
                f"写出器只接受 build_display_timeline() 的产物"
            )
        dur = e.end - e.start
        if dur > MAX_DISPLAY_S + 1e-6 and not getattr(e, "long_ok", False):
            raise DisplayTimelineError(
                f"字幕第 {i} 条（{e.start:.3f}s）显示 {dur:.3f}s，"
                f"超过最长 {MAX_DISPLAY_S}s——封顶没跑过"
            )


def _check_at(entries, *, quantum, label):
    prev_end = None
    for i, e in enumerate(entries, 1):
        start = e.start if quantum is None else round(e.start / quantum) * quantum
        end = e.end if quantum is None else round(e.end / quantum) * quantum
        if end <= start:
            raise DisplayTimelineError(
                f"字幕第 {i} 条（{start:.3f}s）{label}显示时长为 0，无法交付"
            )
        if prev_end is not None and start < prev_end:
            raise DisplayTimelineError(
                f"字幕第 {i} 条（{start:.3f}s）{label}与上一条重叠（上一条结束于 {prev_end:.3f}s）"
            )
        prev_end = end
