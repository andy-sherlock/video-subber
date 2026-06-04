"""Five-layer hallucination filter (three hard, two soft).

Processing order: L1 → L4 → L5 → L2 → L3
Hard filters discard; soft filters only mark uncertain.
"""
import logging
import re
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


def _layer1_sensevoice(seg) -> bool:
    tag = getattr(seg, "sensevoice_tag", None)
    if tag is None:
        return False
    if tag != "Speech":
        seg.filtered = True
        seg.filter_reason = "layer1_sensevoice"
        _log.info("L1 丢弃: start=%.2f tag=%s text=%r", seg.start, tag, seg.text[:50])
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


def _layer2_cross_validate(seg) -> None:
    qwen_text = getattr(seg, "qwen_text", None)
    if qwen_text is None:
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


def filter_hallucinations(segments: list, source_lang: str = "auto") -> list:
    """Five-layer hallucination filter.

    Sets segment attributes:
      filtered=True/False, filter_reason=str|None, uncertain=True/False
    Returns only segments with filtered=False, re-numbered.
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
            _layer1_sensevoice(seg)

    for seg in segments:
        if not seg.filtered:
            _layer4_regex(seg)

    surviving = [s for s in segments if not s.filtered]
    surviving = _layer5_dedup(surviving)

    for seg in surviving:
        _layer2_cross_validate(seg)

    for seg in surviving:
        if not seg.filtered:
            _layer3_speech_rate(seg, source_lang)

    result = [s for s in surviving if not s.filtered]
    for i, s in enumerate(result):
        s.id = i

    return result
