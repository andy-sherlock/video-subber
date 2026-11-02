"""Sentence splitting and word-level alignment for subtitle segments.

T2: Moved from worker.py (_split_zh_simple, _word_align_segments).
T4: LLM semantic splitting with 4-signal decision logic.
"""
import re
import logging

from backend.core.display_timeline import (
    MAX_ZH_CHARS as _MAX_ZH_CHARS, SPLIT_MARK as _SPLIT_MARK, _han_count,
    _CUT_PUNCT, _cut_key, _en_tokens, _w_text, _word_end, _word_start, _word_time_bad)
from backend.core.refined_alignment import align_opcodes

_log = logging.getLogger(__name__)

_ZH_SENT_RE = re.compile(r'[。！？!?]+')

# G5-1：`word_align_segments()` 已删除。它做的是「找译文句末标点 → 映射到原词
# 序列里带 .!? 的词 → 用该词真实 end 切屏」，与 display_timeline 的 7 秒拆屏
# 是同一个算法、只是触发条件不同（句号 vs 超 7 秒）。两份实现意味着将来必有
# 一份忘了改（项目已在 P368/P353/P367/P352/P405 吃过五次这个亏），因此合并为
# `display_timeline._find_safe_cut()` 一份、两个触发条件。
# 它顺带带走了 `round(bw.end, 2)` —— 那处提前取整违反 R-15。


def split_zh_simple(text: str) -> list[str]:
    """Split Chinese text on sentence-ending punctuation."""
    parts, last = [], 0
    for m in _ZH_SENT_RE.finditer(text):
        p = text[last:m.end()].strip()
        if p:
            parts.append(p)
        last = m.end()
    remainder = text[last:].strip()
    if remainder:
        if parts:
            parts[-1] += remainder
        else:
            parts.append(remainder)
    return [p for p in parts if p] or [text]


# ── T4: LLM Semantic Sentence Splitting (4-signal decision) ──


def llm_split_segments(segments: list, llm_backend=None, pre_splits: dict | None = None,
                       *, on_wait, on_resume=None) -> list:
    """Split long Chinese subtitles using 4-signal decision logic.

    Signals:
    1. Pause position — gaps between segments from word timestamps
    2. Pause duration — <0.3s don't cut, 0.3-1s check semantics, >1s must cut
    3. Semantic completeness — LLM judges natural breakpoints (LLM mode only)
    4. Chinese length — max 20 chars per subtitle line

    Args:
        segments: list of Segment objects with .zh attribute.
        llm_backend: Optional LLM backend for semantic splitting — one backend, or the
                     user's ordered list (D-1 A7: the paid request rotates keys).
        on_wait: D-1 C5 — called (kind, backend_name, seconds) before waiting in place.
                 Required (P514): a forgotten reporter must fail loudly, not wait silently.
        on_resume: ⑦.5 R3 — called once when a request succeeds after waiting.
        pre_splits: Optional dict {original_idx: [split_part_1, ...]} from
                    LLM translation step. If provided, apply these splits directly
                    instead of making a separate LLM splitting call.

    Falls back to rule-based splitting when no LLM is available.
    """
    # Phase 1C: apply pre-computed splits from LLM translation step
    if pre_splits:
        return _apply_precomputed_splits(segments, pre_splits)

    long_segs = _find_long_segment_indexes(segments)

    if not long_segs:
        return segments

    replacements = _split_long_segments(segments, long_segs, llm_backend,
                                        on_wait=on_wait, on_resume=on_resume)
    return _replace_segments_with_splits(segments, long_segs, replacements)


_PAUSE_SEND_S = 1.0     # 插入④ 施工 B D11：不到 16 字、但有这么长的停顿也送 AI
_PAUSE_MARK_S = 0.5     # D13：送 AI 的英文里标出这么长以上的停顿


def _is_long_zh_segment(seg) -> bool:
    zh = getattr(seg, 'zh', '').strip()
    if not zh or zh == '[翻译失败]':
        return False
    # 插入④ C4（P529）：只数汉字，和显示层同一个 `_han_count`（以前数全部字符，多送 15% 的句子）
    return _han_count(zh) > _MAX_ZH_CHARS or _longest_pause(seg) >= _PAUSE_SEND_S - 1e-9


def _pauses(words) -> list[tuple[int, float]]:
    """(第几个词之前, 停了几秒)：相邻两词之间的空白。时间标错的词前面的空白不算（D14）。"""
    return [(k, float(_word_start(words[k])) - float(_word_end(words[k - 1])))
            for k in range(1, len(words)) if not _word_time_bad(words, k)]


def _longest_pause(seg) -> float:
    return max((gap for _k, gap in _pauses(getattr(seg, 'words', None) or [])), default=0.0)


def _english_with_pauses(seg) -> str:
    """D13：润色英文里，在原始识别词停了 ≥0.5 秒的位置前面插「⏸0.8秒」。对不上润色英文的停顿不标。"""
    text = getattr(seg, 'text', '') or ''
    words = getattr(seg, 'words', None) or []
    raw, owner = [], []
    for k, w in enumerate(words):
        for t in _en_tokens(_w_text(w)):
            raw.append(t)
            owner.append(k)
    refined = text.split()
    ref, ref_owner = [], []
    for j, w in enumerate(refined):
        for t in _en_tokens(w):
            ref.append(t)
            ref_owner.append(j)
    ops = align_opcodes(raw, ref)
    if ops is None:
        return text
    to_ref = {}
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            to_ref.update(zip(range(i1, i2), range(j1, j2)))
    first_raw = {}
    for i, k in enumerate(owner):
        first_raw.setdefault(k, i)
    marks = {}
    for k, gap in _pauses(words):
        i = first_raw.get(k)
        if gap >= _PAUSE_MARK_S - 1e-9 and i in to_ref and ref_owner[to_ref[i]] > 0:
            marks[ref_owner[to_ref[i]]] = gap
    return " ".join((f"⏸{marks[j]:.1f}秒 " if j in marks else "") + w for j, w in enumerate(refined))


def _apply_precomputed_splits(segments: list, pre_splits: dict) -> list:
    _log.info("使用预计算断句 (LLM 翻译已拆 %d 条)", len(pre_splits))
    result = []
    for seg in segments:
        if seg.id in pre_splits and len(pre_splits[seg.id]) >= 2:
            subs = _build_split_segments(seg, pre_splits[seg.id])
            if len(subs) >= 2:
                result.extend(subs)
                continue
            seg = subs[0]
        if _is_long_zh_segment(seg):
            result.extend(_rule_based_split(seg))
        else:
            result.append(seg)
    return result


def _find_long_segment_indexes(segments: list) -> list[int]:
    return [i for i, seg in enumerate(segments) if _is_long_zh_segment(seg)]


# G12 第三批 B（排版 §10、§13.1）：整片一次发出时，回答写到第 111 / 125 条就被 4096 上限截断，定为每批 80 条。
# 插入④ 施工 B D3：每刀多报两个英文词，实验 0g 有 6 批回答超过 4000 token → 上限 8192；批量仍写死 80，不跟上限变。
_SPLIT_MAX_TOKENS = 8192
_SPLIT_BATCH = 80


def _split_long_segments(segments: list, long_segs: list[int], llm_backend=None,
                         *, on_wait, on_resume=None) -> list[list]:
    backends = llm_backend if isinstance(llm_backend, (list, tuple)) else [llm_backend]
    backends = [b for b in backends if b is not None and hasattr(b, 'translate_raw')]
    if not backends:
        return [_rule_based_split(segments[i]) for i in long_segs]
    # 决定 36：每批各自成败——一批出错只这一批不拆，一批截断只截断点之后不拆。
    stats: dict = {}
    results = []
    for k in range(0, len(long_segs), _SPLIT_BATCH):
        batch = [segments[i] for i in long_segs[k:k + _SPLIT_BATCH]]
        results.extend(_llm_batch_split(batch, backends, stats,
                                        on_wait=on_wait, on_resume=on_resume))
    _reask(results, backends, on_wait=on_wait, on_resume=on_resume)
    reasons = "，".join(f"{_REASON_NAMES.get(r, r)} {n}"
                        for r, n in stats.get("rejected", {}).items())
    _log.info("AI 拆句：发出 %d 条，采用 %d 条，未采用 %d 条（%s）；共 %d 批，出错 %d 批，截断 %d 批",
              len(long_segs), stats.get("adopted", 0),
              len(long_segs) - stats.get("adopted", 0), reasons or "无",
              stats.get("batches", 0), stats.get("error_batches", 0),
              stats.get("truncated_batches", 0))
    return results


_REASK_OVER = 18     # 插入④ 施工 B D5：AI 切出的段超过 18 字，再问一次（17–18 字照收）
_REASK_CUT = re.compile(r'^\s*\[(\d+)\.(\d+)\]\s*(.*?)\s*(?:‖\s*(.*?))?\s*$')
_REASK_FILL = re.compile(r'^\s*\[补(\d+)\]\s*(.+?)\s*$')


def _reask_prompt(cut_items, fill_items) -> str:
    parts = ["你是字幕断句专家。下面有两部分，请都回答。"]
    if cut_items:
        parts.append(
            f"第一部分：以下每条中文字幕已经切过一次，但还是超过{_REASK_OVER}个汉字（不算标点），需要再切开。"
            "每条下面附了这句话的英文原文；英文可能比这条中文长，这条中文只对应其中一部分。\n规则：\n"
            "- 只在意思完整处切分（句号、逗号、语气停顿、两个短语之间）\n"
            f"- 按意思断开，每段不超过{_MAX_ZH_CHARS}个汉字（不算标点），需要几刀就给几刀；一定要给切点\n"
            "- 不要把「把、在、从、给、对、和、或、跟、的、了」这类字留在一段的末尾\n"
            "- 不要拆开一个词、人名或地名\n- 保持口语自然，不改变原文\n" + _WORD_RULES + "\n"
            + "\n".join(f"[{n}] {piece}\n    英文：{en}" for n, (_s, _k, piece, en) in enumerate(cut_items)))
    if fill_items:
        parts.append(
            "第二部分：以下每条已经在「‖」处切好了，不要再切。只要报出这一刀的 刀前词 ｜ 刀后词。\n"
            "刀前词、刀后词的意思同上：刀前词是「‖」前面那段中文最后几个字对应英文的最后一个词；"
            "刀后词是英文里紧挨着它的下一个词，小词也不能跳过。照抄，不改、不翻译；⏸ 不是词。\n"
            "输出格式：[补编号] 刀前词 ｜ 刀后词，每条一行。例：[补0] nice ｜ Then\n\n"
            + "\n".join(f"[补{n}] {a} ‖ {b}\n    英文：{en}"
                        for n, (_s, _key, a, b, en) in enumerate(fill_items)))
    return "\n\n".join(parts)


def _reask(results: list[list], backends, *, on_wait, on_resume=None) -> None:
    """插入④ 施工 B D5 + D7：整片只问一次——超 18 字的段再切，第一遍漏写词的刀补词。

    只改被问到的那几刀：再切的段只在段内插新刀，补词只填那一刀的词对；回问出错、漏答、
    改了字的照第一遍，交给排版按 D10 处理。**就地改** ``results`` 里的 segment。
    """
    cut_items, fill_items = [], []
    for subs in results:
        seg = subs[0]
        if len(subs) != 1 or seg.ai_cut_words is None:
            continue
        pieces = seg.zh.split(_SPLIT_MARK)
        en = _english_with_pauses(seg)
        for k, piece in enumerate(pieces):
            if _han_count(piece) > _REASK_OVER:
                cut_items.append((seg, k, piece, en))
            if k < len(pieces) - 1:
                key = _cut_key(_SPLIT_MARK.join(pieces[:k + 1]))
                if seg.ai_cut_words.get(key) is None:
                    fill_items.append((seg, key, piece.strip(), pieces[k + 1].strip(), en))
    if not cut_items and not fill_items:
        return

    from backend.translation.paid_request import call_paid
    prompt = _reask_prompt(cut_items, fill_items)
    _used, result = call_paid(
        backends, lambda b: b.translate_raw(prompt, max_tokens=_SPLIT_MAX_TOKENS),
        on_wait=on_wait, on_resume=on_resume)
    raw = "" if getattr(result, "failure_kind", None) else (result.translated[0] if result.translated else "")
    _log.debug("AI 拆句回问原话：\n%s", raw)

    got, fill = {}, {}
    for line in raw.split('\n'):
        m = _REASK_FILL.match(line)
        if m:
            fill[int(m.group(1))] = _word_pair(m.group(2))
            continue
        m = _REASK_CUT.match(line)
        if m and m.group(3):
            got.setdefault(int(m.group(1)), {})[int(m.group(2))] = (m.group(3), m.group(4))

    filled = 0
    for n, (seg, key, _a, _b, _en) in enumerate(fill_items):
        if fill.get(n) is not None:
            seg.ai_cut_words[key] = fill[n]
            filled += 1

    adopted = 0
    plan: dict = {}
    for n, (seg, k, piece, _en) in enumerate(cut_items):
        sub = got.get(n, {})
        ids = sorted(sub)
        if len(ids) < 2 or ids != list(range(1, len(ids) + 1)):
            continue
        parts = [sub[i][0] for i in ids]
        cuts = _cut_positions(piece, parts)
        if cuts is None or len(cuts) != len(parts) - 1:
            continue                      # 改了字
        adopted += 1
        plan.setdefault(id(seg), (seg, {}))[1][k] = (cuts, [_word_pair(sub[i][1]) for i in ids[:-1]])
    for seg, by_k in plan.values():
        pieces = seg.zh.split(_SPLIT_MARK)
        out = []
        for k, piece in enumerate(pieces):
            if k not in by_k:
                out.append(piece)
                continue
            cuts, pairs = by_k[k]
            base = _cut_key(_SPLIT_MARK.join(pieces[:k]))
            prev = 0
            for c, pair in zip(cuts, pairs):
                out.append(piece[prev:c])
                seg.ai_cut_words[base + _cut_key(piece[:c])] = pair
                prev = c
            out.append(piece[prev:])
        seg.zh = _SPLIT_MARK.join(out)
    _log.info("AI 拆句回问：超 %d 字再切 %d 段，采用 %d 段；漏写词 %d 刀，补到 %d 刀",
              _REASK_OVER, len(cut_items), adopted, len(fill_items), filled)


_REASON_NAMES = {
    "no_answer": "没回答",
    "call_failed": "调用出错",
    "too_few_parts": "只给一段",
    "duplicate_subnumber": "编号重复",
    "non_contiguous_subnumber": "编号不连续",
    "quality_rejected": "格式看不懂",
    "target_language": "不是中文",
    "text_changed": "改了字",
}


def _replace_segments_with_splits(segments: list, long_segs: list[int], replacements: list[list]) -> list:
    result = []
    replace_map = dict(zip(long_segs, replacements))
    for i, seg in enumerate(segments):
        if i in replace_map:
            result.extend(replace_map[i])
        else:
            result.append(seg)
    return result


def _build_split_segments(seg, parts: list[str], pairs: list | None = None) -> list:
    """把 AI 给的断开位置记进原译文，整条不拆，**不再按字数比例分配时间**（G5-1 / R-11）。

    旧版按 `len(part) / total_chars` 给每一片算 start/end。那个时间是凭空造的
    ——它跟视频里谁在哪一秒说话没有任何关系，正是 P401/P402/P414/P415 的来源之一。

    没有译文分片到原词的可靠对应，就不能产生分屏。因此这里整条退回不拆、
    保留 seg 的真实时间与**原词表**，把拆分交给 `display_timeline` 的 7 秒拆屏
    ——那一层用真实原词边界切，切不动就整条封顶。

    断开位置记成断点记号 `SPLIT_MARK`，插进 seg 自己的译文，原文每个字符都保留（P475）。
    以前用空格把分片拼回一条：这一屏没在那里换行时，空格就留在观众看到的字幕里；
    英文单词之间的原空格也只能靠原文保住。分片对不上译文时不插记号，译文原样。

    插入④ 施工 B：``pairs`` 是 AI 拆句每一刀的「刀前词｜刀后词」，按刀的位置挂到 ``ai_cut_words``，
    排版拿它定这一刀的时间。预拆分（翻译步骤交来的分片）没有词，不传，``ai_cut_words`` 仍是 None。
    """
    from backend.transcriber_v2 import Segment

    zh = getattr(seg, 'zh', '')
    cut_words = _cut_words_by_key(zh, parts, pairs) if pairs is not None else None
    for cut in reversed(_cut_positions(zh, parts) or []):
        zh = zh[:cut] + _SPLIT_MARK + zh[cut:]
    sub = Segment(id=seg.id, start=seg.start, end=seg.end, text=seg.text)
    sub.zh = zh
    sub.speaker_change = seg.speaker_change
    sub.uncertain = getattr(seg, 'uncertain', False)
    sub.words = list(getattr(seg, 'words', []) or [])
    sub.ai_cut_words = cut_words
    return [sub]


# 插入④ 施工 B D2 + D12 + D13：每一刀报「刀前词 ｜ 刀后词」，程序据此在带时间的英文词里定这一刀的时间。
# 原样取自实验 0i（用户 2026-10-26 验收）；回问第一部分也用这一份。例子是自编句子，不用测试数据。
_WORD_RULES = (
    "- 英文里的「⏸0.8秒」表示说话人在这里停了多久，它不是词。停顿超过 1 秒的地方，尽量就在停顿处切；\n"
    f"  即使整条不超过{_MAX_ZH_CHARS}个汉字，有超过 1 秒的停顿也要在停顿处切开\n"
    "- 先只看中文按上面的规矩切好；切好以后，每一刀标出它在英文原句里的位置\n"
    "- 输出格式：[原编号.子编号] 文本，除了最后一段，每段后面写 ‖ 刀前词 ｜ 刀后词\n"
    "  刀前词 = 这一段中文最后那几个字，在「英文：」里对应的那部分英文的最后一个词（thing、it、that 这种词也算，不能跳过）\n"
    "  刀后词 = 「英文：」里紧挨在刀前词后面的那一个词，不管它是 but、and、so、my 还是别的小词，都不能跳过\n"
    "  两个词照抄英文原词，不改、不翻译。最后一段后面不写 ‖。每段单独一行，不要抄中文原文，除了 ‖ 和 ｜ 不要加任何符号。\n"
    "  正确的例子（原文：我觉得有时候最好的旅行决定就是多做你已经热爱的事 / 英文：I think sometimes the best travel decision is just doing more of what you already love.）：\n"
    "  [0.1] 我觉得有时候最好的旅行决定 ‖ decision ｜ is\n"
    "  [0.2] 就是多做你已经热爱的事\n"
    "  一段里有句号时，看这一段最后那几个字，不要对到前一个句号（原文：我们先去了海边。天气特别好。然后去吃了午饭。/ 英文：We went to the beach first. The weather was really nice. Then we had lunch.）：\n"
    "  [0.1] 我们先去了海边。天气特别好 ‖ nice ｜ Then\n"
    "  [0.2] 然后去吃了午饭\n"
    "  错误的例子（抄了英文开头的词，不是刀所在的位置）：[0.1] 我觉得有时候最好的旅行决定 ‖ I ｜ think\n"
)


def _word_pair(text) -> tuple[str, str] | None:
    """「刀前词 ｜ 刀后词」→ (刀前词, 刀后词)，都按 `_en_tokens` 切好；写不全返回 None。"""
    if not text or "｜" not in text:
        return None
    before, after = (_en_tokens(x) for x in text.split("｜", 1))
    return (before[-1], after[0]) if before and after else None


def _cut_words_by_key(zh: str, parts: list[str], pairs: list) -> dict | None:
    """把 AI 每段后面报的词对挂到它那一刀的位置上（R1）。``_cut_positions`` 只会扔掉末尾的刀。"""
    cuts = _cut_positions(zh, parts) or []
    return {_cut_key(zh[:c]): pairs[n] for n, c in enumerate(cuts)} or None


def _llm_batch_split(segments: list, llm_backend, stats: dict | None = None,
                     *, on_wait, on_resume=None) -> list[list]:
    """一批长句问 AI 在哪断开；只采用断开位置，正文一律取原文。``stats`` 累计统计日志的数字。"""
    stats = {} if stats is None else stats
    stats["batches"] = stats.get("batches", 0) + 1
    rejected = stats.setdefault("rejected", {})
    from backend.transcriber_v2 import Segment

    lines = []
    for i, seg in enumerate(segments):
        zh = getattr(seg, 'zh', '')
        dur = round(seg.end - seg.start, 2)
        lines.append(f"[{i}] ({dur}s) {zh}\n    英文：{_english_with_pauses(seg)}")

    prompt = (
        f"你是字幕断句专家。以下字幕大多超过{_MAX_ZH_CHARS}个汉字（不算标点），需要拆分；"
        "少数较短但中间有长停顿，也请在停顿处拆。每条下面附了英文原文。\n"
        "规则：\n"
        "- 只在意思完整处切分（句号、逗号、语气停顿、两个短语之间）\n"
        f"- 按意思断开，每段不超过{_MAX_ZH_CHARS}个汉字（不算标点），需要几刀就给几刀；"
        f"一定要给切点，除非整条不超过{_MAX_ZH_CHARS}个汉字\n"
        "- 不要把「把、在、从、给、对、和、或、跟、的、了」这类字留在一段的末尾\n"
        "- 不要拆开一个词、人名或地名\n"
        "- 保持口语自然，不改变原文\n"
        + _WORD_RULES + "\n"
        + "\n".join(lines)
    )

    from backend.translation.paid_request import call_paid

    # D-1 A4：请求出错不再整批按规则切——付费请求函数换 Key、原地等；
    # 不会自己好的错抛出，任务停在出错。回来的只剩成功或 AI 回复格式类失败（空回答）。
    backends = llm_backend if isinstance(llm_backend, (list, tuple)) else [llm_backend]
    _used, result = call_paid(
        backends,
        lambda b: b.translate_raw(prompt, max_tokens=_SPLIT_MAX_TOKENS),
        on_wait=on_wait,
        on_resume=on_resume,
    )
    raw = result.translated[0] if result.translated else ""
    # 插入④ C9′（P534）：原话进任务的 debug.log（worker 把 root 全量 DEBUG 写进它），供事后复核 AI 怎么切的
    _log.debug("AI 拆句原话（%d 条）：\n%s", len(segments), raw)
    if getattr(result, "failure_kind", None):
        _log.warning("AI 拆句：这一批 %d 条回答不可用（%s），这一批不拆",
                     len(segments), result.failure_kind)
        stats["error_batches"] = stats.get("error_batches", 0) + 1
        rejected["call_failed"] = rejected.get("call_failed", 0) + len(segments)
        return [_rule_based_split(seg) for seg in segments]
    if ((getattr(result, "tokens_used", None) or {}).get("output_tokens") or 0) >= _SPLIT_MAX_TOKENS:
        stats["truncated_batches"] = stats.get("truncated_batches", 0) + 1

    from backend.translation.base import is_clean_translation_text
    from backend.translation.orchestrator import _is_valid_translation

    pattern = re.compile(r'\[(\d+)\.(\d+)\]\s*(.+)')
    expected_ids = set(range(len(segments)))
    split_map: dict[int, dict[int, str]] = {}
    words_map: dict[int, dict[int, str]] = {}
    invalid_ids: set[int] = set()
    for line in raw.split('\n'):
        m = pattern.match(line.strip())
        if m:
            orig_idx = int(m.group(1))
            sub_idx = int(m.group(2))
            sub_text, _bar, cut_words = m.group(3).partition('‖')
            sub_text = sub_text.strip()
            words_map.setdefault(orig_idx, {})[sub_idx] = cut_words
            if orig_idx not in expected_ids:
                _log.debug("LLM 断句拒绝: idx=%d kind=outside_batch", orig_idx)
                continue
            parts = split_map.setdefault(orig_idx, {})
            if sub_idx in parts:
                invalid_ids.add(orig_idx)
                _log.debug("LLM 断句拒绝: idx=%d kind=duplicate_subnumber", orig_idx)
                continue
            parts[sub_idx] = sub_text

    results = []
    for i, seg in enumerate(segments):
        part_map = split_map.get(i, {})
        sub_ids = sorted(part_map)
        reason = None
        if i in invalid_ids:
            reason = "duplicate_subnumber"
        elif not sub_ids:
            reason = "no_answer"      # 提示词要求一定给切点，AI 仍可能漏答；被截断也落在这里
        elif len(sub_ids) == 1:
            reason = "too_few_parts"
        elif sub_ids != list(range(1, len(sub_ids) + 1)):
            reason = "non_contiguous_subnumber"

        parts = [part_map[sub_id] for sub_id in sub_ids]
        if reason is None:
            for part in parts:
                if not is_clean_translation_text(part):
                    reason = "quality_rejected"
                    break
                if not _is_valid_translation(part):
                    reason = "target_language"
                    break

        # P470：模型常在断开处删掉逗号。只动了标点或空白时照样采用它给的断开位置，
        # 正文一律取原文——模型怎么改标点都碰不到正文。改了字的仍拒绝。
        if reason is None and _cut_positions(getattr(seg, 'zh', ''), parts) is None:
            reason = "text_changed"

        if reason is not None:
            _log.debug("LLM 断句拒绝: idx=%d kind=%s", i, reason)
            rejected[reason] = rejected.get(reason, 0) + 1
            results.append(_rule_based_split(seg))
            continue
        stats["adopted"] = stats.get("adopted", 0) + 1
        pairs = [_word_pair(words_map.get(i, {}).get(sub_id)) for sub_id in sub_ids[:-1]]
        results.append(_build_split_segments(seg, parts, pairs))

    return results


_PUNCT_RE = _CUT_PUNCT      # 与排版找刀位置同一份（`display_timeline._cut_key`）
# 收尾类标点留在前一段；开头类（“‘（《「『【）不在这里，自然跟着后一段。
_CLOSING_PUNCT = set('，,。．.！!？?、；;：:…—”’"\'）)》」』】〕］]')


def _cut_positions(original: str, parts: list[str]) -> list[int] | None:
    """按模型每段的字数，在**原文**上找断开位置（P470 / P475）。

    字数只数“去掉空白和标点”之后的字；两边这样数出来不一致，说明模型改了字，
    返回 None 让调用方照旧拒绝。收尾标点留在前一段，开头标点跟着后一段。
    切不出至少两段也返回 None。采用判断（`_llm_batch_split`）和插记号
    （`_build_split_segments`）共用这一个函数，不写第二套对齐。
    """
    def _core(text):
        return _PUNCT_RE.sub("", "".join(text.split()))

    if len(parts) < 2 or _core("".join(parts)) != _core(original):
        return None
    targets, total = [], 0
    for part in parts[:-1]:
        total += len(_core(part))
        targets.append(total)

    cuts, counted = [], 0
    for i, ch in enumerate(original):
        if ch.isspace() or _PUNCT_RE.match(ch):
            continue
        counted += 1
        if len(cuts) < len(targets) and counted == targets[len(cuts)]:
            j = i + 1
            while j < len(original) and original[j] in _CLOSING_PUNCT:
                j += 1
            cuts.append(j)

    cuts = [cut for cut in cuts if original[cut:].strip()]   # 断开处后面必须还有字
    return cuts or None


def _rule_based_split(seg) -> list:
    """整条退回不拆（G5-1 / R-11）。

    旧版做两件今天被禁止的事：
      1. 没有逗号时用 `split_points = [len(zh) // 2]` 从字符中点硬切
         ——「商店」会被切成「商 / 店」；
      2. 按 `len(parts[0]) / len(zh)` 的字数比例算切点时间——凭空造的时间。

    长译文的拆分现在只由 `display_timeline` 负责：它把译文切点映射到真实原词
    边界，验证左右两边时间都合法才切；证明不了就整条不拆、封顶 7 秒、进质量摘要。
    """
    return [seg]
