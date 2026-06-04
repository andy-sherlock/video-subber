"""Five-layer hallucination filter (three hard, two soft).

Processing order: L1 → L4 → L5 → L2 → L3
Hard filters discard; soft filters only mark uncertain.
"""
import logging
import re
from collections import Counter
from difflib import SequenceMatcher

_log = logging.getLogger("hallucination_filter")

HALLUCINATION_KEYWORDS = (
    "音乐", "music", "♪", "♫", "[music]", "[applause]", "[laughter]",
    "掌声", "笑声", "鼓掌", "谢谢观看", "请订阅", "字幕组", "字幕由",
    "музыка", "МУЗЫКА", "аплодисменты", "смех",
    "音楽", "拍手", "笑い声",
)

_SPEECH_RATE_THRESHOLDS = {
    "zh": 15,
    "ja": 15,
    "en": 25,
    "ru": 25,
    "auto": 20,
}

_RE_BRACKET = re.compile(r"^\s*[\[\(（【].+[\]\)）】]\s*$")
_RE_REPEATED_PATTERN = re.compile(r"^(.{1,4})\1{2,}$")
_RE_REPEATED_WORDS = re.compile(r"^((\S+)\s+)\2{2,}\2*$")


def _layer1_dual_signal(seg, aligner_result=None) -> bool:
    """L1: SenseVoice + Qwen3-FA dual-signal cross-verification (Phase 1D).

    Four-way decision matrix:
      SenseVoice non-human + no alignment → discard (both confirm)
      SenseVoice non-human + has alignment → keep (SV likely wrong)
      SenseVoice human   + no alignment → keep + mark [?] (possible hallucination)
      SenseVoice human   + has alignment → normal pass (both confirm)

    Falls back to single-signal (SenseVoice-only) when aligner_result is None.
    """
    tag = getattr(seg, "sensevoice_tag", None)

    # No SenseVoice data → skip L1
    if tag is None:
        return False

    is_nonhuman = (tag != "Speech")
    has_alignment = _segment_has_alignment(seg, aligner_result)

    if aligner_result is not None and len(aligner_result) > 0:
        # ── Dual-signal path (only when we have actual alignment data) ──
        if is_nonhuman and not has_alignment:
            # Both signals agree: not human speech, no aligned words → discard
            seg.filtered = True
            seg.filter_reason = "layer1_dual_confirm_nonhuman"
            _log.info("L1 双确认丢弃: start=%.2f tag=%s text=%r",
                      seg.start, tag, seg.text[:50])
            return True
        elif is_nonhuman and has_alignment:
            # SenseVoice says non-human but aligner found words → SV likely wrong
            seg.sensevoice_tag = None  # Clear the wrong tag
            _log.info("L1 SV误判清除: start=%.2f tag=%s (对齐器找到了词)",
                      seg.start, tag)
            return False
        elif not is_nonhuman and not has_alignment:
            # SV says human but aligner found nothing → possible hallucination
            seg.uncertain = True
            _log.info("L1 疑似幻觉 [?]: start=%.2f tag=%s text=%r",
                      seg.start, tag, seg.text[:50])
            return False
        else:
            # Both signals agree: human speech confirmed by alignment → pass
            return False
    else:
        # ── Single-signal fallback (original behavior) ──
        if is_nonhuman:
            seg.filtered = True
            seg.filter_reason = "layer1_sensevoice"
            _log.info("L1 丢弃: start=%.2f tag=%s text=%r", seg.start, tag, seg.text[:50])
            return True

    return False


def _segment_has_alignment(seg, aligner_result) -> bool:
    """Check if any aligned word falls within the segment's time range."""
    if aligner_result is None:
        return False
    if not aligner_result:
        return False
    for w in aligner_result:
        ws = w.get("start", 0)
        we = w.get("end", 0)
        if ws < seg.end and we > seg.start:
            return True
    return False


def _layer4_regex(seg) -> bool:
    t = seg.text.strip()
    if not t:
        seg.filtered = True
        seg.filter_reason = "layer4_regex"
        return True

    tl = t.lower()
    for kw in HALLUCINATION_KEYWORDS:
        if kw.lower() in tl:
            seg.filtered = True
            seg.filter_reason = "layer4_regex"
            _log.info("L4 关键词: start=%.2f kw=%r text=%r", seg.start, kw, t[:50])
            return True

    if all(not c.isalnum() and c not in "你我他她它的了是有不在" for c in tl):
        seg.filtered = True
        seg.filter_reason = "layer4_regex"
        _log.info("L4 纯符号: start=%.2f text=%r", seg.start, t[:50])
        return True

    if _RE_BRACKET.match(t):
        seg.filtered = True
        seg.filter_reason = "layer4_regex"
        _log.info("L4 括号描述: start=%.2f text=%r", seg.start, t[:50])
        return True

    t_nospace = t.replace(" ", "")
    if len(t_nospace) >= 3 and _RE_REPEATED_PATTERN.match(t_nospace):
        seg.filtered = True
        seg.filter_reason = "layer4_regex"
        _log.info("L4 重复模式: start=%.2f text=%r", seg.start, t[:50])
        return True

    if _RE_REPEATED_WORDS.match(t):
        seg.filtered = True
        seg.filter_reason = "layer4_regex"
        _log.info("L4 重复词: start=%.2f text=%r", seg.start, t[:50])
        return True

    half = len(t) // 2
    if half >= 5:
        first_half = t[:half].strip()
        second_half = t[half:].strip()
        if first_half and first_half == second_half:
            seg.filtered = True
            seg.filter_reason = "layer4_regex"
            _log.info("L4 循环句式: start=%.2f text=%r", seg.start, t[:50])
            return True

    return False


def _layer5_dedup(segments: list) -> list:
    if len(segments) < 2:
        return segments
    kept = [segments[0]]
    for seg in segments[1:]:
        if seg.text.strip() == kept[-1].text.strip():
            seg.filtered = True
            seg.filter_reason = "layer5_dedup"
            _log.info("L5 去重: start=%.2f text=%r", seg.start, seg.text[:50])
        else:
            kept.append(seg)
    return kept


# P148: fraction of identical qwen_text above which Qwen-ASR is deemed collapsed
# (repetition collapse) and therefore an untrustworthy cross-validation referee.
_QWEN_COLLAPSE_RATIO = 0.3


def _qwen_is_reliable(segments: list) -> bool:
    """Detect Qwen-ASR repetition collapse (P148).

    When Qwen-ASR's cross-validation output is dominated by a single identical
    string (e.g. job 97c57255ec: 58% of segments became
    "1056234897 нет ты меня прости"), it is an unreliable referee. L2 must not
    use it to flag the correct Whisper text. Returns False when one qwen_text
    value covers more than _QWEN_COLLAPSE_RATIO of the non-empty qwen outputs.
    """
    texts = [(getattr(s, "qwen_text", None) or "").strip() for s in segments]
    texts = [t for t in texts if t]
    if len(texts) < 5:
        return True  # too little data to judge — don't disable L2
    most_common, count = Counter(texts).most_common(1)[0]
    if count / len(texts) > _QWEN_COLLAPSE_RATIO:
        _log.warning("L2 跳过：千问疑似坍缩 (同一串占 %d/%d=%.0f%%): %r",
                     count, len(texts), 100 * count / len(texts),
                     most_common[:40])
        return False
    return True


def _layer2_cross_validate(seg, qwen_reliable: bool = True) -> None:
    qwen_text = getattr(seg, "qwen_text", None)
    if not qwen_text:  # None or empty string
        return
    if not qwen_reliable:  # P148: collapsed Qwen is not a trustworthy referee
        return
    ratio = SequenceMatcher(None, seg.text, qwen_text).ratio()
    if ratio < 0.3:
        seg.uncertain = True
        _log.info("L2 交叉验证: start=%.2f sim=%.2f whisper=%r qwen=%r",
                  seg.start, ratio, seg.text[:30], qwen_text[:30])


def _layer3_speech_rate(seg, source_lang: str = "auto") -> None:
    dur = seg.end - seg.start
    text_len = len(seg.text.strip())

    if dur < 0.1 and text_len > 5:
        seg.filtered = True
        seg.filter_reason = "layer3_impossible_duration"
        _log.info("L3 不可能时长: start=%.2f dur=%.3f len=%d", seg.start, dur, text_len)
        return

    if dur <= 0:
        seg.filtered = True
        seg.filter_reason = "layer3_impossible_duration"
        return

    cps = text_len / dur
    threshold = _SPEECH_RATE_THRESHOLDS.get(source_lang, _SPEECH_RATE_THRESHOLDS["auto"])
    if cps > threshold:
        seg.uncertain = True
        _log.info("L3 语速异常: start=%.2f cps=%.1f threshold=%d lang=%s",
                  seg.start, cps, threshold, source_lang)


def filter_hallucinations(segments: list, source_lang: str = "auto",
                          aligner_result=None) -> list:
    """Five-layer hallucination filter (Phase 1D: dual-signal L1).

    Args:
        segments: List of Segment objects.
        source_lang: Detected source language for L3 speech-rate thresholds.
        aligner_result: Optional Qwen3-FA alignment data (list of aligned word
            dicts). When provided, L1 uses dual-signal (SenseVoice + Qwen3-FA).
            When None, L1 falls back to single-signal (SenseVoice only).

    Returns:
        Only segments with filtered=False, re-numbered.
    """
    if not segments:
        return []

    for seg in segments:
        if not hasattr(seg, "filtered"):
            seg.filtered = False
        if not hasattr(seg, "filter_reason"):
            seg.filter_reason = None
        if not hasattr(seg, "uncertain"):
            seg.uncertain = False

    for seg in segments:
        if not seg.filtered:
            _layer1_dual_signal(seg, aligner_result)

    for seg in segments:
        if not seg.filtered:
            _layer4_regex(seg)

    surviving = [s for s in segments if not s.filtered]
    surviving = _layer5_dedup(surviving)

    qwen_reliable = _qwen_is_reliable(surviving)
    for seg in surviving:
        _layer2_cross_validate(seg, qwen_reliable)

    for seg in surviving:
        if not seg.filtered:
            _layer3_speech_rate(seg, source_lang)

    result = [s for s in surviving if not s.filtered]
    for i, s in enumerate(result):
        s.id = i

    return result
