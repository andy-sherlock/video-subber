"""Five-layer hallucination filter (three hard, two soft).

Processing order: L1 → L2 → L4 → L5 → L3
Hard filters discard; soft filters only mark uncertain.
"""
import logging
import re
import unicodedata
from bisect import bisect_right
from collections import Counter

_log = logging.getLogger("hallucination_filter")


class LightweightStemmer:
    """Zero-dependency multi-language stemmer (P160).

    Desktop-app safe — no NLTK, no model downloads, no network required.
    Each language gets a different key because word morphology differs:

      zh — no stemming; extract individual characters
      en — strip common suffixes (-ing/-ed/-es/-s/-tion/-ment/-ness/-ly
            -able/-ible/-ful/-ous/-ive/-ize/-ise/-ity/-al)
      ru — manual Snowball-esque suffix list (case/tense/aspect)
      ja/ko — filter grammatical particles, keep content words
    """
    @staticmethod
    def clean_and_stem(text, lang):
        if not text or not text.strip():
            return set()
        text = text.lower()

        # Chinese: extract individual hanzi characters
        if lang == "zh":
            return set(re.findall(r'[一-龥]', text))

        # English: lightweight suffix stripping
        if lang == "en":
            words = re.findall(r"[a-z0-9']+", text)
            stems = []
            for w in words:
                if len(w) >= 4:
                    if w.endswith('tion') or w.endswith('ment') or w.endswith('ness') or w.endswith('able') or w.endswith('ible'):
                        w = w[:-4]
                    elif w.endswith('ful') or w.endswith('ous') or w.endswith('ive') or w.endswith('ize') or w.endswith('ise') or w.endswith('ity') or w.endswith('ing'):
                        w = w[:-3]
                        if w.endswith('i') and len(w) > 2:
                            pass
                        elif len(w) >= 3 and w[-1] == w[-2]:
                            w = w[:-1]
                    elif w.endswith('ed') or w.endswith('er') or w.endswith('ly') or w.endswith('al'):
                        w = w[:-2]
                    elif w.endswith('es'):
                        w = w[:-2]
                    elif w.endswith('s') and not w.endswith('ss'):
                        w = w[:-1]
                stems.append(w)
            return set(stems)

        # Russian: manual suffix stripping (Snowball-inspired, P160)
        if lang == "ru":
            words = re.findall(r'[а-яё0-9]+', text)
            stems = []
            ru_suffixes = (
                'его', 'ого',                # adj genitive - P160: missing in v1
                'ами', 'ями', 'ов', 'ев', 'ей', 'ег',
                'ах', 'ях', 'ом', 'ем', 'ой', 'ей',
                'ую', 'юю', 'ая', 'яя', 'ое', 'ее',
                'ые', 'ие', 'ым', 'им', 'их', 'ых',
                'ешь', 'ет', 'ем', 'ете', 'ут', 'ют',
                'ишь', 'ит', 'им', 'ите', 'ат', 'ят',
                'ся', 'сь',                   # reflexive - P160: missing in v1
                'л', 'ла', 'ло', 'ли',
                'ть', 'ти', 'а', 'я', 'о', 'е', 'ы', 'и', 'ь',
            )
            for w in words:
                if len(w) > 3:
                    stemmed = False
                    for suff in ru_suffixes:
                        if w.endswith(suff):
                            w = w[:-len(suff)]
                            stemmed = True
                            break
                    if not stemmed:
                        for suff in ('а', 'я', 'о', 'е', 'ы', 'и', 'ь'):
                            if w.endswith(suff) and len(w) - len(suff) >= 3:
                                w = w[:-len(suff)]
                                break
                    # re-strip trailing soft sign after longer suffix removal
                    if w.endswith('ь') and len(w) > 3:
                        w = w[:-1]
                stems.append(w)
            return set(stems)

        # Japanese/Korean: filter grammatical particles
        if lang in ("ja", "ko"):
            words = re.findall(r'[぀-ゟ゠-ヿ一-龯가-힣]+', text)
            if lang == "ja":
                ja_particles = {'は', 'が', 'を', 'に', 'の', 'で', 'へ', 'と', 'から',
                                'まで', 'です', 'ます', 'た'}
                return {w for w in words if w not in ja_particles}
            return set(words)

        # Default: split on whitespace
        return set(text.split())


HALLUCINATION_KEYWORDS = (
    "音乐", "music", "♪", "♫", "[music]", "[applause]", "[laughter]",
    "掌声", "笑声", "鼓掌", "谢谢观看", "请订阅", "字幕组", "字幕由",
    "музыка", "МУЗЫКА", "аплодисменты", "смех",
    "音楽", "拍手", "笑い声",
)

# P250: thresholds in speech-units/sec. CJK (zh/ja) counts characters
# (1 char = 1 syllable); other languages count whitespace-split words.
# An English word averages 5-6 chars, so the old char-based en=25 fired on
# normal speech (~4.5 words/sec); 8 words/sec is the repetition-hallucination
# floor.
_SPEECH_RATE_THRESHOLDS = {
    "zh": 15,    # chars/sec
    "ja": 15,    # chars/sec
    "en": 8,     # words/sec
    "ru": 8,     # words/sec
    "auto": 7,   # words/sec (split fallback)
}


def count_speech_units(text: str, lang: str) -> int:
    """Speech units for speech-rate estimation (P250).

    CJK languages are syllable-timed and unspaced: one character ≈ one
    syllable, so count characters (matches the original len() behaviour for
    zh/ja exactly). Everything else is counted by whitespace-split words.
    """
    t = text.strip()
    if not t:
        return 0
    if lang in ("zh", "ja"):
        return len(t)
    return len(t.split())

_RE_BRACKET = re.compile(r"^\s*[\[\(（【].+[\]\)）】]\s*$")
_RE_REPEATED_PATTERN = re.compile(r"^(.{1,4})\1{2,}$")
_RE_REPEATED_WORDS = re.compile(r"^(\S+)(\s+\1)+$")


def _has_cjk_text(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text or "")
    for ch in normalized:
        if "\u3040" <= ch <= "\u30ff":  # Japanese kana
            return True
        if "\u3400" <= ch <= "\u9fff":  # CJK ideographs
            return True
        if "\uac00" <= ch <= "\ud7af":  # Korean hangul
            return True
    return False


def _mark_cjk_repetition_uncertain(seg, text: str) -> bool:
    if not _has_cjk_text(text):
        return False
    seg.uncertain = True
    seg.filter_reason = "layer4_cjk_repetition_uncertain"
    _log.info("L4 CJK重复保留为疑似 [?]: start=%.2f text=%r",
              seg.start, seg.text[:50])
    return True


def _layer1_dual_signal(seg, aligner_result=None, source_lang="auto") -> bool:
    """L1: SenseVoice + Qwen3-FA dual-signal cross-verification (Phase 1D).

    Four-way decision matrix (requires both signals):
      SenseVoice non-human + no alignment → discard (both confirm)
      SenseVoice non-human + has alignment → keep (SV likely wrong)
      SenseVoice human   + no alignment → keep + mark [?] (possible hallucination)
      SenseVoice human   + has alignment → normal pass (both confirm)

    P183: When aligner_result is None or empty, falls back to single-signal
    — trusts SenseVoice alone for non-Speech tags (no alignment data to cross-check).
    """
    tag = getattr(seg, "sensevoice_tag", None)

    # No SenseVoice data → skip L1
    if tag is None:
        return False

    is_nonhuman = (tag != "Speech")
    has_alignment = _segment_has_alignment(seg, aligner_result)

    if not (aligner_result is not None and len(aligner_result) > 0):
        # P183: single-signal fallback — when FA is unavailable, trust SV alone
        if is_nonhuman:
            seg.filtered = True
            seg.filter_reason = "layer1_sv_only_nonhuman"
            _log.info("L1 单信号丢弃: start=%.2f tag=%s text=%r",
                      seg.start, tag, seg.text[:50])
            return True
        return False

    # ── Dual-signal path (only when we have actual alignment data) ──
    if is_nonhuman and not has_alignment:
        if _has_cjk_text(seg.text):
            seg.uncertain = True
            seg.filter_reason = "layer1_sv_nonhuman_cjk_uncertain"
            _log.info("L1 SV非人声但含CJK文本，保留为疑似 [?]: start=%.2f tag=%s text=%r",
                      seg.start, tag, seg.text[:50])
            return False
        seg.filtered = True
        seg.filter_reason = "layer1_dual_confirm_nonhuman"
        _log.info("L1 双确认丢弃: start=%.2f tag=%s text=%r",
                  seg.start, tag, seg.text[:50])
        return True
    elif is_nonhuman and has_alignment:
        seg.sensevoice_tag = None
        seg.bgm_override = True  # P229: signal L2 to use lower threshold
        _log.info("L1 SV误判清除: start=%.2f tag=%s (对齐器找到了词)",
                  seg.start, tag)
        return False
    elif not is_nonhuman and not has_alignment:
        # P262: 短词豁免——对齐器在 ≤3 个语音单位时物理层面无法可靠对齐。
        # 但只豁免"Speech"标签的短词；Music/Noise 标签的短词仍然标记 [?]
        # （因为背景音中出现的短词更可能是幻觉）。
        if tag == "Speech" and count_speech_units(seg.text, source_lang) <= 3:
            _log.info("L1 短词豁免: start=%.2f tag=%s text=%r",
                      seg.start, tag, seg.text[:50])
            return False
        seg.uncertain = True
        _log.info("L1 疑似幻觉 [?]: start=%.2f tag=%s text=%r",
                  seg.start, tag, seg.text[:50])
        return False
    else:
        return False


def _qwen_segments_as_alignment(qwen_chunks) -> list:
    """Flatten Qwen word-level segments into FA-compatible alignment list.

    MLX path: qwen_chunks carry segments=[{text, start, end}] from internal FA.
    This flattens them into [{text, start, end}] — same shape as external FA's
    aligner_result, so L1's _segment_has_alignment() works unchanged.
    """
    alignment = []
    for chunk in qwen_chunks or []:
        if not isinstance(chunk, dict):
            continue
        for seg in chunk.get("segments") or []:
            start = seg.get("start")
            end = seg.get("end")
            if start is not None and end is not None:
                alignment.append({"text": seg.get("text", ""),
                                  "start": start, "end": end})
    return alignment


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
    if getattr(seg, "l2_status", None) != "GREEN":
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

    t_nospace = unicodedata.normalize("NFKC", t.replace(" ", ""))
    if getattr(seg, "l2_status", None) != "GREEN":
        if len(t_nospace) >= 3 and _RE_REPEATED_PATTERN.match(t_nospace):
            if _mark_cjk_repetition_uncertain(seg, t_nospace):
                return False
            seg.filtered = True
            seg.filter_reason = "layer4_regex"
            _log.info("L4 重复模式: start=%.2f text=%r", seg.start, t[:50])
            return True

    if getattr(seg, "l2_status", None) != "GREEN":
        t_normalized = unicodedata.normalize("NFKC", t)
        if _RE_REPEATED_WORDS.match(t_normalized):
            if _mark_cjk_repetition_uncertain(seg, t_normalized):
                return False
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

# P251: local sentence split for L2 Qwen handling. Kept here (not imported from
# qwen_asr_runner) because qwen_asr_runner already imports from this module —
# importing back would create a circular import.
_L2_SENTENCE_SPLIT = re.compile(r"[。！？.!?；;]+")


def _split_for_l2(text: str) -> list:
    return [p.strip() for p in _L2_SENTENCE_SPLIT.split(text or "") if p.strip()]


def _qwen_is_reliable(qwen_chunks: list) -> bool:
    """Detect Qwen-ASR repetition collapse (P148, P251).

    Qwen-ASR now hands L2 a list of per-chunk dicts {text, t_start, t_end}
    instead of per-segment qwen_text. Split all chunk text into sentences;
    when one identical sentence dominates (> _QWEN_COLLAPSE_RATIO of the
    non-empty sentences, e.g. job 97c57255ec rehearsed
    "1056234897 нет ты меня прости"), Qwen is an unreliable referee — skip L2.
    """
    sentences = []
    for chunk in qwen_chunks or []:
        if isinstance(chunk, dict):
            sentences.extend(_split_for_l2(chunk.get("text")))
    sentences = [s for s in sentences if s]
    if len(sentences) < 5:
        return True  # too little data to judge — don't disable L2
    most_common, count = Counter(sentences).most_common(1)[0]
    if count / len(sentences) > _QWEN_COLLAPSE_RATIO:
        _log.warning("L2 跳过：千问疑似坍缩 (同一句占 %d/%d=%.0f%%): %r",
                     count, len(sentences), 100 * count / len(sentences),
                     most_common[:40])
        return False
    return True


def _extract_qwen_sentences(qwen_chunks):
    """P256: merge word-level Qwen timestamps into sentence-level.

    Each Qwen chunk has segments=[{text, start, end}] at word level.
    This groups consecutive words into sentences (split by _L2_SENTENCE_SPLIT)
    and returns [{text, start, end}] at sentence level.
    """
    if not qwen_chunks:
        return []
    all_words = []
    for chunk in qwen_chunks:
        if not isinstance(chunk, dict):
            continue
        chunk_words = []
        for seg in chunk.get("segments") or []:
            text = (seg.get("text") or "").strip()
            start = seg.get("start")
            end = seg.get("end")
            if text and start is not None and end is not None:
                chunk_words.append({"text": text, "start": start, "end": end})
        if chunk_words:
            all_words.append(chunk_words)
    if not all_words:
        return []
    sentences = []
    for chunk_words in all_words:
        buf_texts = []
        buf_start = None
        for w in chunk_words:
            if buf_start is None:
                buf_start = w["start"]
            buf_texts.append(w["text"])
            if _L2_SENTENCE_SPLIT.search(w["text"]):
                text = " ".join(buf_texts).strip()
                clean = [p.strip() for p in _L2_SENTENCE_SPLIT.split(text)
                         if p.strip()]
                if clean:
                    sentences.append({"text": clean[0], "start": buf_start,
                                      "end": w["end"]})
                buf_texts = []
                buf_start = None
        if buf_texts:
            text = " ".join(buf_texts).strip()
            if text:
                sentences.append({"text": text, "start": buf_start,
                                  "end": chunk_words[-1]["end"]})
    return sentences


_SMART_WINDOW_DEFAULT = 30
_SMART_WINDOW_MIN = 20
_SMART_WINDOW_MAX = 40


def _build_smart_boundaries(qwen_chunks, total_duration):
    """P256: build window boundaries aligned to Qwen sentence edges.

    Starts from fixed 30s cuts, then slides each cut point to avoid splitting
    a Qwen sentence. The sentence's occupancy ratio decides which side absorbs
    it. Windows are clamped to [20s, 40s].

    Returns a sorted list of floats: [0, b1, b2, ..., total_duration].
    """
    ws = _SMART_WINDOW_DEFAULT
    if total_duration <= ws:
        return [0.0, total_duration]

    fixed = [i * ws for i in range(1, int(total_duration // ws) + 1)
             if i * ws < total_duration]
    if not fixed:
        return [0.0, total_duration]

    sentences = _extract_qwen_sentences(qwen_chunks)

    boundaries = list(fixed)
    for i, b in enumerate(boundaries):
        straddlers = [s for s in sentences
                      if s["start"] < b < s["end"]]
        if not straddlers:
            continue
        s = straddlers[0]
        left_ratio = (b - s["start"]) / (s["end"] - s["start"])
        new_b = s["end"] if left_ratio >= 0.5 else s["start"]

        prev = boundaries[i - 1] if i > 0 else 0.0
        nxt = boundaries[i + 1] if i + 1 < len(boundaries) else total_duration
        win_left = new_b - prev
        win_right = nxt - new_b
        if win_left < _SMART_WINDOW_MIN or win_right < _SMART_WINDOW_MIN:
            continue
        if win_left > _SMART_WINDOW_MAX or win_right > _SMART_WINDOW_MAX:
            continue
        boundaries[i] = new_b

    return [0.0] + boundaries + [total_duration]


def _has_timestamp_segments(qwen_chunks):
    """Check if any chunk carries word-level timestamp segments (MLX path)."""
    for chunk in qwen_chunks or []:
        if isinstance(chunk, dict) and chunk.get("segments"):
            return True
    return False


def _assign_qwen_to_windows(qwen_chunks, whisper_windows, window_size, language,
                            boundaries=None):
    """Place Qwen-ASR text into 30s windows.

    Two paths:
      - Timestamp path (MLX): each segment has start/end → precise window index.
      - Similarity path (PyTorch fallback): stem overlap heuristic.
    """
    num_windows = len(whisper_windows)
    qwen_windows = ["" for _ in range(num_windows)]
    if not qwen_chunks or num_windows == 0:
        return qwen_windows

    if _has_timestamp_segments(qwen_chunks):
        return _assign_by_timestamps(qwen_chunks, num_windows, window_size,
                                     boundaries=boundaries)

    return _assign_by_similarity(qwen_chunks, whisper_windows, window_size, language)


def _assign_by_timestamps(qwen_chunks, num_windows, window_size, boundaries=None):
    """Place Qwen segments into windows using their precise start times."""
    qwen_windows = ["" for _ in range(num_windows)]
    last = num_windows - 1
    for chunk in qwen_chunks:
        if not isinstance(chunk, dict):
            continue
        for seg in chunk.get("segments") or []:
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            if boundaries:
                win = min(last, max(0, bisect_right(boundaries, seg["start"]) - 1))
            else:
                win = min(last, max(0, int(seg["start"] // window_size)))
            qwen_windows[win] += " " + text
    return qwen_windows


def _assign_by_similarity(qwen_chunks, whisper_windows, window_size, language):
    """Fallback: place Qwen text into windows by stem similarity (PyTorch)."""
    num_windows = len(whisper_windows)
    qwen_windows = ["" for _ in range(num_windows)]

    w_stems = [LightweightStemmer.clean_and_stem(w or "", language)
               for w in whisper_windows]

    for chunk in qwen_chunks:
        if not isinstance(chunk, dict):
            continue
        text = (chunk.get("text") or "").strip()
        if not text:
            continue
        last = num_windows - 1
        lo = min(last, max(0, int((chunk.get("t_start") or 0) // window_size)))
        hi = min(last, max(lo, int((chunk.get("t_end") or 0) // window_size)))
        cursor = lo
        for sentence in _split_for_l2(text):
            q_stems = LightweightStemmer.clean_and_stem(sentence, language)
            if not q_stems:
                continue
            best_win = cursor
            best_score = -1.0
            for w in range(cursor, hi + 1):
                ws = w_stems[w]
                score = len(q_stems & ws) / len(q_stems) if ws else 0.0
                if score > best_score:
                    best_score = score
                    best_win = w
            qwen_windows[best_win] += " " + sentence
            cursor = best_win
    return qwen_windows


def _layer2_cross_validate(segments: list, language: str = "auto",
                           custom_thresholds: dict = None, qwen_chunks=None):
    """P160/P256: window stem-overlap cross-validation (B+A architecture).

    P256: windows align to Qwen sentence boundaries (smart boundaries)
    instead of fixed 30s cuts. Falls back to fixed 30s when no Qwen
    timestamps are available.

    Returns (annotated_segments, window_stats).
    """
    if not segments:
        return segments, {}

    # ── Per-language thresholds ──
    thresholds = {
        "zh": 0.55, "en": 0.35, "ru": 0.28,
        "ja": 0.45, "ko": 0.45,
    }
    if custom_thresholds:
        thresholds.update(custom_thresholds)
    current_threshold = thresholds.get(language, 0.30)

    # ── P256: smart window boundaries ──
    WINDOW_SIZE = 30
    total_duration_s = max(getattr(s, "end", 0) for s in segments)
    if total_duration_s <= 0:
        return segments, {}

    boundaries = _build_smart_boundaries(qwen_chunks, total_duration_s)
    num_windows = len(boundaries) - 1

    whisper_windows = ["" for _ in range(num_windows)]

    def _find_best_window(start, end):
        idx = max(0, bisect_right(boundaries, start) - 1)
        best = min(idx, num_windows - 1)
        max_overlap = 0
        lo = max(0, bisect_right(boundaries, start) - 1)
        hi = min(num_windows - 1, bisect_right(boundaries, end) - 1)
        for w in range(lo, hi + 1):
            ws = boundaries[w]
            we = boundaries[w + 1]
            overlap = min(end, we) - max(start, ws)
            if overlap > max_overlap:
                max_overlap = overlap
                best = w
        return best

    for seg in segments:
        if seg.filtered:
            continue
        win = _find_best_window(seg.start, seg.end)
        if win < num_windows:
            whisper_windows[win] += " " + seg.text

    qwen_windows = _assign_qwen_to_windows(
        qwen_chunks, whisper_windows, WINDOW_SIZE, language,
        boundaries=boundaries)

    # ── Stem + score each window ──
    MIN_STEMS = 5
    _BGM_THRESHOLD = 0.20
    window_stats = {}
    for idx in range(num_windows):
        w_text = whisper_windows[idx].strip()
        q_text = qwen_windows[idx].strip()

        if not w_text and not q_text:
            window_stats[idx] = {"status": "EMPTY", "score": 1.0,
                                 "w_count": 0, "q_count": 0, "shared": 0}
            continue

        if not q_text:
            window_stats[idx] = {"status": "NO_QWEN", "score": 1.0,
                                 "w_count": 0, "q_count": 0, "shared": 0}
            continue

        w_stems = LightweightStemmer.clean_and_stem(w_text, language)
        q_stems = LightweightStemmer.clean_and_stem(q_text, language)

        shared = w_stems & q_stems
        score = len(shared) / max(len(w_stems), len(q_stems)) if (w_stems or q_stems) else 0.0

        win_has_bgm = any(
            getattr(seg, "bgm_override", False)
            for seg in segments
            if not seg.filtered and _find_best_window(seg.start, seg.end) == idx
        )
        effective_threshold = _BGM_THRESHOLD if win_has_bgm else current_threshold

        ws_entry = {"score": score, "w_count": len(w_stems),
                    "q_count": len(q_stems), "shared": len(shared),
                    "bgm": win_has_bgm}

        if len(w_stems) < MIN_STEMS:
            ws_entry["status"] = "LOW_DATA"
        elif score >= effective_threshold:
            ws_entry["status"] = "GREEN"
        else:
            ws_entry["status"] = "YELLOW"

        window_stats[idx] = ws_entry

    # ── P149 calibration diagnostics ──
    for idx in sorted(window_stats):
        ws = window_stats[idx]
        t0 = boundaries[idx]
        t1 = boundaries[idx + 1]
        _log.debug("L2 窗口[%d] %.1f-%.1fs | w=%d q=%d shared=%d score=%.3f → %s",
                   idx, t0, t1, ws["w_count"], ws["q_count"],
                   ws["shared"], ws["score"], ws["status"])

    # ── Annotate surviving segments with l2_status ──
    for seg in segments:
        if seg.filtered:
            continue
        win = _find_best_window(seg.start, seg.end)
        ws = window_stats.get(win)
        if ws:
            seg.l2_status = ws["status"]
            if ws["status"] in ("YELLOW", "LOW_DATA"):
                seg.uncertain = True
        else:
            seg.l2_status = "NO_DATA"

    _log.info("L2 窗口比对完成: %d 窗口 (P256 智能边界), 阈值=%.2f, 最低词干=%d, %s",
              num_windows, current_threshold, MIN_STEMS,
              {s: sum(1 for v in window_stats.values() if v["status"] == s)
               for s in ("GREEN", "YELLOW", "LOW_DATA", "EMPTY", "NO_QWEN")})

    return segments, window_stats


def _layer3_speech_rate(seg, source_lang: str = "auto") -> None:
    dur = seg.end - seg.start
    units = count_speech_units(seg.text, source_lang)

    if dur < 0.1 and units > 2:
        seg.filtered = True
        seg.filter_reason = "layer3_impossible_duration"
        _log.info("L3 不可能时长: start=%.2f dur=%.3f units=%d", seg.start, dur, units)
        return

    if dur <= 0:
        seg.filtered = True
        seg.filter_reason = "layer3_impossible_duration"
        return

    rate = units / dur
    threshold = _SPEECH_RATE_THRESHOLDS.get(source_lang, _SPEECH_RATE_THRESHOLDS["auto"])
    if rate > threshold:
        seg.uncertain = True
        _log.info("L3 语速异常: start=%.2f rate=%.1f threshold=%d lang=%s",
                  seg.start, rate, threshold, source_lang)


def filter_hallucinations(segments: list, source_lang: str = "auto",
                          aligner_result=None, qwen_chunks=None, skip_l1: bool = False) -> list:
    """Five-layer hallucination filter. Order: L1→L2→L4→L5→L3.

    Args:
        segments: List of Segment objects.
        source_lang: Detected source language for L3 speech-rate thresholds.
        aligner_result: Optional Qwen3-FA alignment data (list of aligned word
            dicts). When provided, L1 uses dual-signal (SenseVoice + Qwen3-FA).
            When None, L1 skips entirely.
        qwen_chunks: Optional Qwen3-ASR output as a list of {text, t_start,
            t_end} dicts (P251). L2 assigns this text to 30s windows by
            similarity instead of reading a per-segment qwen_text. When None
            or empty, L2 runs with no Qwen cross-validation signal.

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

    # When Qwen chunks carry word-level timestamps (MLX path), derive
    # alignment data from them — no external FA needed for L1.
    if not skip_l1:
        effective_alignment = aligner_result
        if not effective_alignment and _has_timestamp_segments(qwen_chunks):
            effective_alignment = _qwen_segments_as_alignment(qwen_chunks)
            _log.info("L1 使用 Qwen 内部 FA 时间戳: %d 词", len(effective_alignment))

        for seg in segments:
            if not seg.filtered:
                _layer1_dual_signal(seg, effective_alignment, source_lang)

    surviving = [s for s in segments if not s.filtered]
    qwen_reliable = _qwen_is_reliable(qwen_chunks)
    if qwen_reliable:
        surviving, _ignore_stats = _layer2_cross_validate(
            surviving, source_lang, qwen_chunks=qwen_chunks)

    for seg in surviving:
        if not seg.filtered:
            _layer4_regex(seg)

    surviving = [s for s in surviving if not s.filtered]
    surviving = _layer5_dedup(surviving)

    for seg in surviving:
        if not seg.filtered:
            _layer3_speech_rate(seg, source_lang)

    result = [s for s in surviving if not s.filtered]
    for i, s in enumerate(result):
        s.id = i

    return result
