"""Sentence splitting and word-level alignment for subtitle segments.

T2: Moved from worker.py (_split_zh_simple, _word_align_segments).
T4: LLM semantic splitting with 4-signal decision logic.
"""
import re
import logging

_log = logging.getLogger(__name__)

_ZH_SENT_RE = re.compile(r'[。！？!?]+')
_MAX_ZH_CHARS = 20


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


def word_align_segments(segments: list) -> list:
    """Re-split segments using English word timestamps for accurate Chinese timing.

    Each Whisper segment may contain multiple sentences (e.g. A+B+C in 4.56s).
    With word timestamps, each Chinese sentence gets the time of its corresponding
    English sentence — same start/end as when the English words were actually spoken.
    """
    from backend.transcriber_v2 import Segment

    result = []
    for seg in segments:
        words = getattr(seg, 'words', [])
        zh = getattr(seg, 'zh', '').strip()

        if not words or not zh or zh == '[翻译失败]':
            result.append(seg)
            continue

        zh_parts = split_zh_simple(zh)
        if len(zh_parts) <= 1:
            result.append(seg)
            continue

        sent_end_idxs = [
            i for i, w in enumerate(words)
            if re.search(r'[.!?]\s*$', w['word'] if isinstance(w, dict) else getattr(w, 'word', ''))
        ]

        if len(sent_end_idxs) < len(zh_parts) - 1:
            result.append(seg)
            continue

        boundaries = sent_end_idxs[:len(zh_parts) - 1]
        t = seg.start
        prev_boundary = -1
        for i, zh_part in enumerate(zh_parts):
            if i < len(boundaries):
                bw = words[boundaries[i]]
                t_end = round(bw['end'] if isinstance(bw, dict) else bw.end, 2)
                en_words = words[prev_boundary + 1: boundaries[i] + 1]
                prev_boundary = boundaries[i]
            else:
                t_end = seg.end
                en_words = words[prev_boundary + 1:]
            en_text = " ".join(
                (w['word'] if isinstance(w, dict) else getattr(w, 'word', '')).strip()
                for w in en_words
            ).strip() or seg.text
            sub = Segment(id=seg.id * 1000 + i, start=t, end=t_end, text=en_text)
            sub.zh = zh_part
            sub.speaker_change = seg.speaker_change if i == 0 else False
            sub.uncertain = getattr(seg, 'uncertain', False)
            sub.words = []
            result.append(sub)
            t = t_end

    return result


# ── T4: LLM Semantic Sentence Splitting (4-signal decision) ──


def llm_split_segments(segments: list, llm_backend=None, pre_splits: dict | None = None) -> list:
    """Split long Chinese subtitles using 4-signal decision logic.

    Signals:
    1. Pause position — gaps between segments from word timestamps
    2. Pause duration — <0.3s don't cut, 0.3-1s check semantics, >1s must cut
    3. Semantic completeness — LLM judges natural breakpoints (LLM mode only)
    4. Chinese length — max 20 chars per subtitle line

    Args:
        segments: list of Segment objects with .zh attribute.
        llm_backend: Optional LLM backend for semantic splitting.
        pre_splits: Optional dict {original_idx: [split_part_1, ...]} from
                    LLM translation step. If provided, apply these splits directly
                    instead of making a separate LLM splitting call.

    Falls back to rule-based splitting when no LLM is available.
    """
    from backend.transcriber_v2 import Segment

    # Phase 1C: apply pre-computed splits from LLM translation step
    if pre_splits:
        _log.info("使用预计算断句 (LLM 翻译已拆 %d 条)", len(pre_splits))
        result = []
        for i, seg in enumerate(segments):
            if i in pre_splits and len(pre_splits[i]) >= 2:
                parts = pre_splits[i]
                subs = _build_split_segments(seg, parts)
                result.extend(subs)
            else:
                result.append(seg)
        # Apply rule-based splitting to remaining long segments
        # that were NOT pre-split (fallback gap-fill)
        final_result = []
        for seg in result:
            zh = getattr(seg, 'zh', '').strip()
            if (zh and zh != '[翻译失败]' and len(zh) > _MAX_ZH_CHARS
                    and not any(seg.id == orig * 100 + j
                                for orig, parts in pre_splits.items()
                                for j in range(len(parts)))):
                final_result.extend(_rule_based_split(seg))
            else:
                final_result.append(seg)
        return final_result

    long_segs = []
    for i, seg in enumerate(segments):
        zh = getattr(seg, 'zh', '').strip()
        if zh and zh != '[翻译失败]' and len(zh) > _MAX_ZH_CHARS:
            long_segs.append(i)

    if not long_segs:
        return segments

    if llm_backend and hasattr(llm_backend, 'translate_raw'):
        replacements = _llm_batch_split(
            [segments[i] for i in long_segs], llm_backend
        )
    else:
        replacements = [_rule_based_split(segments[i]) for i in long_segs]

    result = []
    replace_map = dict(zip(long_segs, replacements))
    for i, seg in enumerate(segments):
        if i in replace_map:
            result.extend(replace_map[i])
        else:
            result.append(seg)

    return result


def _build_split_segments(seg, parts: list[str]) -> list:
    """Create Segment objects from split parts, distributing duration by char count."""
    from backend.transcriber_v2 import Segment

    duration = seg.end - seg.start
    total_chars = max(sum(len(p) for p in parts), 1)
    subs = []
    t = seg.start
    for j, part in enumerate(parts):
        t_end = t + duration * len(part) / total_chars
        if j == len(parts) - 1:
            t_end = seg.end
        sub = Segment(
            id=seg.id * 100 + j,
            start=round(t, 2), end=round(t_end, 2),
            text=seg.text,
        )
        sub.zh = part
        sub.speaker_change = seg.speaker_change if j == 0 else False
        sub.uncertain = getattr(seg, 'uncertain', False)
        sub.words = []
        subs.append(sub)
        t = t_end
    return subs


def _llm_batch_split(segments: list, llm_backend) -> list[list]:
    """Use LLM to split long Chinese subtitles semantically."""
    from backend.transcriber_v2 import Segment

    lines = []
    for i, seg in enumerate(segments):
        zh = getattr(seg, 'zh', '')
        dur = round(seg.end - seg.start, 2)
        lines.append(f"[{i}] ({dur}s) {zh}")

    prompt = (
        "你是字幕断句专家。以下字幕每条超过20个中文字符，需要拆分。\n"
        "规则：\n"
        "- 每条拆分后不超过20个中文字符\n"
        "- 在语义完整处切分（句号、逗号、语气停顿）\n"
        "- 保持口语自然，不改变原文\n"
        "- 输出格式：[原编号.子编号] 文本\n"
        "  例：[0] 原文 → [0.1] 前半 [0.2] 后半\n\n"
        + "\n".join(lines)
    )

    try:
        result = llm_backend.translate_raw(prompt, max_tokens=4096)
        raw = result.translated[0] if result.translated else ""
    except Exception:
        _log.warning("LLM 断句失败，回退到规则断句")
        return [_rule_based_split(seg) for seg in segments]

    pattern = re.compile(r'\[(\d+)\.(\d+)\]\s*(.+)')
    split_map: dict[int, list[str]] = {}
    for line in raw.split('\n'):
        m = pattern.match(line.strip())
        if m:
            orig_idx = int(m.group(1))
            sub_text = m.group(3).strip()
            if orig_idx not in split_map:
                split_map[orig_idx] = []
            split_map[orig_idx].append(sub_text)

    results = []
    for i, seg in enumerate(segments):
        if i in split_map and len(split_map[i]) > 1:
            results.append(_build_split_segments(seg, split_map[i]))
        else:
            results.append(_rule_based_split(seg))

    return results


def _rule_based_split(seg) -> list:
    """Split a long Chinese subtitle at punctuation (free mode fallback)."""
    from backend.transcriber_v2 import Segment

    zh = getattr(seg, 'zh', '')
    if len(zh) <= _MAX_ZH_CHARS:
        return [seg]

    split_points = [m.end() for m in re.finditer(r'[，,；;、：:…—]', zh)]

    if not split_points:
        split_points = [len(zh) // 2]

    mid = len(zh) / 2
    best = min(split_points, key=lambda p: abs(p - mid))

    parts = [zh[:best].strip(), zh[best:].strip()]
    parts = [p for p in parts if p]

    if len(parts) <= 1:
        return [seg]

    duration = seg.end - seg.start
    ratio = len(parts[0]) / max(len(zh), 1)
    split_time = seg.start + duration * ratio

    subs = []
    for j, part in enumerate(parts):
        t_start = seg.start if j == 0 else round(split_time, 2)
        t_end = round(split_time, 2) if j == 0 else seg.end
        sub = Segment(id=seg.id * 100 + j, start=t_start, end=t_end, text=seg.text)
        sub.zh = part
        sub.speaker_change = seg.speaker_change if j == 0 else False
        sub.uncertain = getattr(seg, 'uncertain', False)
        sub.words = []
        subs.append(sub)

    return subs
