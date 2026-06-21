"""Transcription pipeline with two modes.

Standard mode:
  Whisper language probe → choose Whisper or Qwen primary
  → SenseVoice sound classification + P275 voice timeline
  → L1 dual-signal filter (SenseVoice × FA) → assembly → L4/L5/L3

Precise mode:
  Whisper ∥ Qwen3-ASR (parallel, dual engine)
  → SenseVoice sound classification + P275 voice timeline
  → L1 dual-signal filter → assembly → L2 cross-validation → L4/L5/L3

FA source: Mac (MLX) = Qwen internal; Windows (PyTorch) = external Qwen3-FA.
Speaker detection (resemblyzer) runs after filtering in both modes.
"""
import bisect
import logging
import threading
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
import numpy as np
from backend.core.whisper_runner import (
    detect_language_with_whisper,
    transcribe_whisper,
    release_model as release_whisper_model,
    should_parallel_asr,
)
from backend.core.vad_filter import compute_speech_ratio_via_vad
from backend.core.hallucination_filter import filter_hallucinations, _has_timestamp_segments, _qwen_is_reliable

_log = logging.getLogger("transcriber")

_SVFA_MIN_RAM_GB = 8


def should_parallel_svfa(tier: str, ram_gb: float) -> bool:
    """SV+FA parallelism: any tier with enough RAM (decoupled from Qwen)."""
    return ram_gb >= _SVFA_MIN_RAM_GB


def should_qwen_pipeline(tier: str, ram_gb: float) -> bool:
    """Qwen ASR pipeline: standard and precise tiers with enough RAM."""
    return tier in ("standard", "precise") and ram_gb >= 16


def should_load_whisper(tier: str) -> bool:
    """Whisper runs as a full transcription engine in precise mode."""
    return tier == "precise"


QWEN_PRIMARY_LANGS = {"en", "ja", "ko", "zh"}
STANDARD_ROUTE_MIN_PROB = 0.70


def choose_standard_primary_engine(detected_lang: str, probability: float) -> str:
    """Pick the standard-mode primary engine from Whisper language detection."""
    if not detected_lang:
        return "whisper"
    if probability < STANDARD_ROUTE_MIN_PROB:
        return "whisper"
    return "qwen" if detected_lang in QWEN_PRIMARY_LANGS else "whisper"


def log_sensevoice_unavailable() -> None:
    _log.warning(
        "SenseVoice 不可用：当前 Python 环境无法 import funasr，"
        "跳过声音分类和人声地图"
    )


def choose_transcription_primary(
    transcription_tier: str,
    standard_primary: str | None,
    detected_lang: str,
    qwen_chunks: list | None,
) -> tuple[str, bool]:
    """Return (primary_engine, keep_qwen_chunks_for_l2)."""
    if transcription_tier == "standard":
        return ("qwen", False) if standard_primary == "qwen" else ("whisper", False)
    if detected_lang in QWEN_PRIMARY_LANGS and qwen_chunks:
        return "qwen", True
    return "whisper", True


class Segment:
    """A single transcribed + translated subtitle segment."""
    def __init__(self, id, start, end, text):
        self.id = id
        self.start = start
        self.end = end
        self.text = text
        self.speaker_change = False
        self.uncertain = False
        self.zh = ""
        self.words = []
        self.sensevoice_tag = None
        self.filtered = False
        self.filter_reason = None


def _apply_sv_tags(segments, sv_events):
    for seg in segments:
        seg.sensevoice_tag = None
        best_overlap = 0.0
        best_event = None
        for ev in sv_events:
            ov_start = max(seg.start, ev["start_s"])
            ov_end = min(seg.end, ev["end_s"])
            ov = max(0.0, ov_end - ov_start)
            if ov > best_overlap:
                best_overlap = ov
                best_event = ev["event"]
        seg_dur = seg.end - seg.start
        if best_overlap > 0 and seg_dur > 0 and best_overlap / seg_dur >= 0.5:
            seg.sensevoice_tag = best_event


def _build_voice_timeline(sv_events, audio_duration):
    """Convert SenseVoice events into voice/non-voice region lists.

    Merges overlapping or adjacent (gap <= 1s) Speech events.
    """
    if audio_duration <= 0:
        return {"voice_regions": [], "non_voice_regions": [], "voice_ratio": 0.0}

    speech = sorted(
        [(ev["start_s"], ev["end_s"]) for ev in (sv_events or []) if ev.get("event") == "Speech"],
        key=lambda x: x[0],
    )

    merged = []
    for start, end in speech:
        if merged and start <= merged[-1][1] + 1.0:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    non_voice = []
    prev = 0.0
    for start, end in merged:
        if start > prev:
            non_voice.append((prev, start))
        prev = end
    if prev < audio_duration:
        non_voice.append((prev, audio_duration))

    voice_total = sum(e - s for s, e in merged)
    return {
        "voice_regions": merged,
        "non_voice_regions": non_voice,
        "voice_ratio": voice_total / audio_duration,
    }


def _segment_center(seg) -> float:
    return (getattr(seg, "start", 0.0) + getattr(seg, "end", 0.0)) / 2.0


def _region_containing_time(regions, point: float):
    for start, end in regions or []:
        if start <= point <= end:
            return (start, end)
    return None


def _segment_voice_region(seg, voice_timeline):
    if not voice_timeline:
        return None
    return _region_containing_time(
        voice_timeline.get("voice_regions", []), _segment_center(seg))


def _segment_in_non_voice(seg, voice_timeline) -> bool:
    if not voice_timeline:
        return False
    return _region_containing_time(
        voice_timeline.get("non_voice_regions", []), _segment_center(seg)) is not None


def _same_voice_region(left, right, voice_timeline) -> bool:
    left_region = _segment_voice_region(left, voice_timeline)
    if not left_region:
        return False
    return left_region == _segment_voice_region(right, voice_timeline)


def summarize_voice_timeline_quality(segments, voice_timeline, lang="auto"):
    """Summarize subtitle coverage over the SenseVoice voice timeline."""
    voice_regions = list((voice_timeline or {}).get("voice_regions", []))
    non_voice_short = 0
    for seg in segments or []:
        if _is_short_fragment(seg, lang) and _segment_in_non_voice(seg, voice_timeline):
            non_voice_short += 1

    uncovered = []
    for start, end in voice_regions:
        covered = False
        for seg in segments or []:
            overlap = max(
                0.0,
                min(getattr(seg, "end", 0.0), end) - max(getattr(seg, "start", 0.0), start),
            )
            if overlap > 0:
                covered = True
                break
        if not covered:
            uncovered.append((start, end))

    longest = None
    if uncovered:
        longest = max(uncovered, key=lambda region: region[1] - region[0])

    return {
        "voice_region_count": len(voice_regions),
        "uncovered_voice_region_count": len(uncovered),
        "non_voice_short_fragment_count": non_voice_short,
        "longest_uncovered_voice_region": longest,
    }


def _detect_speaker_changes(audio_path: str, segments: list) -> list:
    """Compare adjacent segment voiceprints via resemblyzer.
    Sets segment.speaker_change=True where voice changes.
    Threshold is auto-detected from score distribution — no magic number.
    """
    try:
        from resemblyzer import VoiceEncoder, preprocess_wav
        import librosa
    except ImportError:
        _log.warning("未安装 resemblyzer/librosa，跳过说话人检测")
        return segments

    try:
        encoder = VoiceEncoder()
    except Exception:
        return segments

    def _get_emb(start: float, end: float):
        try:
            dur = end - start
            if dur < 1.0:
                return None
            y, _sr = librosa.load(audio_path, sr=16000, offset=start, duration=dur)
            if len(y) < 16000 * 1.0:
                return None
            wav = preprocess_wav(y, source_sr=16000)
            return encoder.embed_utterance(wav)
        except Exception:
            return None

    # ── Pass 1: collect all similarity scores ──
    prev_emb = None
    sims = []  # (segment_index, similarity_score)
    for seg in segments:
        emb = _get_emb(seg.start, seg.end)
        if emb is not None and prev_emb is not None:
            sim = float(np.dot(emb, prev_emb))
            sims.append((seg, sim))
        if emb is not None:
            prev_emb = emb

    if len(sims) < 2:
        _log.info("说话人检测: 有效相似度不足，跳过")
        return segments

    # ── Guard B: skip detection for short videos (< 5 segments) ──
    if len(segments) < 5:
        _log.info("说话人检测: 段数不足5（%d段），跳过", len(segments))
        return segments

    scores = np.array([s for _, s in sims], dtype=np.float64)
    score_range = float(scores.max() - scores.min())

    # ── Solo-speaker check: tight cluster → no split needed ──
    if score_range < 0.01 or float(scores.std()) < score_range * 0.15:
        _log.info("说话人检测: 单人（std=%.3f, range=%.3f），无横杠", float(scores.std()), score_range)
        return segments

    # ── Multi-speaker: find natural split via minimum within-group variance ──
    sorted_scores = np.sort(scores)
    best_thr = sorted_scores[len(sorted_scores) // 2]  # fallback
    best_var = float("inf")
    for i in range(1, len(sorted_scores)):
        left = sorted_scores[:i]
        right = sorted_scores[i:]
        var_sum = float(left.var() * len(left) + right.var() * len(right))
        if var_sum < best_var:
            best_var = var_sum
            best_thr = float((left[-1] + right[0]) / 2.0)

    # ── Pass 2: apply found threshold ──
    changes = 0
    for seg, sim in sims:
        if sim < best_thr:
            seg.speaker_change = True
            changes += 1

    # ── Guard C: two independent checks — either can cancel ──
    sorted_all = np.sort(scores)
    score_gaps = np.diff(sorted_all)
    gap_ratio = 0.0
    group_gap = 0.0

    # Check 1: gap detection — is there a real crack in the distribution?
    if len(score_gaps) >= 3:
        median_gap = float(np.median(score_gaps))
        max_gap = float(score_gaps.max())
        if median_gap > 1e-6:
            gap_ratio = max_gap / median_gap
            if gap_ratio < 3.0:
                _log.info("说话人检测: 间隙检测未通过（%.1f倍 < 3倍），判定语气波动，全部取消", gap_ratio)
                for seg, _ in sims:
                    seg.speaker_change = False
                return segments

    # Check 2: safety clamp — absolute group distance
    above = [s for s in scores if s >= best_thr]
    below = [s for s in scores if s < best_thr]
    if above and below:
        group_gap = float(np.mean(above) - np.mean(below))
        if group_gap < 0.15:
            _log.info("说话人检测: 安全钳未通过（组间距%.3f < 0.15），判定语气波动，全部取消", group_gap)
            for seg, _ in sims:
                seg.speaker_change = False
            return segments

    _log.info("说话人检测完成: %d 处变化 (阈值=%.3f, 间隙比=%.1f, 组间差=%.3f)",
              changes, best_thr, gap_ratio, group_gap)
    return segments


_SENTENCE_ENDERS = ("。", "！", "？", "!", "?", "…")
_JA_STRONG_ENDERS = ("よ", "ね", "な", "わ", "ぞ", "ぜ", "か", "だ", "ます", "たい")
_JA_WEAK_ENDERS = ("の", "さ", "で")
_JA_STRONG_ENDER_GAP = 0.15
_JA_WEAK_ENDER_GAP = 0.3


def _flatten_qwen_words(qwen_chunks):
    words = []
    for chunk in (qwen_chunks or []):
        if not isinstance(chunk, dict):
            continue
        for w in chunk.get("segments") or []:
            text = w.get("text", "").strip()
            if text:
                words.append({
                    "text": text,
                    "start": w.get("start", 0.0),
                    "end":   w.get("end", 0.0),
                })
    words.sort(key=lambda w: w["start"])
    return words


def _words_to_segment(words, seg_id, joiner):
    text = joiner.join(w["text"] for w in words)
    seg = Segment(id=seg_id, text=text,
                  start=words[0]["start"], end=words[-1]["end"])
    seg.words = [
        {
            "word": w.get("word") or w.get("text", ""),
            "text": w.get("text", ""),
            "start": w.get("start", 0.0),
            "end": w.get("end", 0.0),
        }
        for w in words
    ]
    seg.whisper_text = ""
    return seg


def _is_short_fragment(seg, lang):
    text = (getattr(seg, "text", "") or "").strip()
    if not text:
        return False
    duration = max(0.0, getattr(seg, "end", 0.0) - getattr(seg, "start", 0.0))
    if lang in ("ja", "zh", "ko", "auto"):
        return len(text) <= 1 or (duration < 0.35 and len(text) <= 3)
    return duration < 0.25 and len(text.split()) <= 1


def _merge_whisper_text(target, source, joiner, prepend=False):
    target_text = (getattr(target, "whisper_text", "") or "").strip()
    source_text = (getattr(source, "whisper_text", "") or "").strip()
    if not source_text:
        return
    if target_text:
        if prepend:
            target.whisper_text = f"{source_text}{joiner}{target_text}".strip()
        else:
            target.whisper_text = f"{target_text}{joiner}{source_text}".strip()
    else:
        target.whisper_text = source_text


def merge_short_subtitle_fragments(segments, lang, max_gap=1.0,
                                   voice_timeline=None, voice_region_gap=2.0):
    """Merge tiny standalone subtitle fragments into nearby segments."""
    if not segments:
        return segments
    merged = []
    joiner = "" if lang in ("ja", "zh", "ko") else " "
    i = 0
    while i < len(segments):
        seg = segments[i]
        if _is_short_fragment(seg, lang):
            prev_gap = seg.start - merged[-1].end if merged else None
            if merged and (
                prev_gap <= max_gap or (
                    prev_gap <= voice_region_gap
                    and _same_voice_region(merged[-1], seg, voice_timeline)
                )
            ):
                prev = merged[-1]
                prev.text = f"{prev.text}{joiner}{seg.text}".strip()
                _merge_whisper_text(prev, seg, joiner)
                prev.end = max(prev.end, seg.end)
                prev.uncertain = getattr(prev, "uncertain", False) or getattr(seg, "uncertain", False)
                i += 1
                continue
            next_gap = segments[i + 1].start - seg.end if i + 1 < len(segments) else None
            if i + 1 < len(segments) and (
                next_gap <= max_gap or (
                    next_gap <= voice_region_gap
                    and _same_voice_region(seg, segments[i + 1], voice_timeline)
                )
            ):
                nxt = segments[i + 1]
                nxt.text = f"{seg.text}{joiner}{nxt.text}".strip()
                _merge_whisper_text(nxt, seg, joiner, prepend=True)
                nxt.start = min(seg.start, nxt.start)
                nxt.uncertain = getattr(nxt, "uncertain", False) or getattr(seg, "uncertain", False)
                i += 1
                continue
            if _segment_in_non_voice(seg, voice_timeline):
                seg.filter_reason = "voice_timeline_non_voice_short_fragment"
        merged.append(seg)
        i += 1
    for idx, seg in enumerate(merged):
        seg.id = idx
    return merged


def _should_break_ja_sentence(prev_text, gap):
    text = (prev_text or "").strip()
    if not text:
        return False
    if gap >= _JA_STRONG_ENDER_GAP and text.endswith(_JA_STRONG_ENDERS):
        return True
    if gap >= _JA_WEAK_ENDER_GAP and text.endswith(_JA_WEAK_ENDERS):
        return True
    return False


def _sv_tag_at(time_point, whisper_segments, ws_starts):
    if not ws_starts:
        return None
    idx = bisect.bisect_right(ws_starts, time_point) - 1
    if idx >= 0:
        ws = whisper_segments[idx]
        if ws.start <= time_point <= ws.end:
            return getattr(ws, "sensevoice_tag", None)
    return None


def _is_repetition_pattern(text):
    text = text.strip()
    if not text:
        return False
    words = text.split()
    if len(words) >= 4:
        top_count = Counter(words).most_common(1)[0][1]
        if top_count / len(words) > 0.6:
            return True
    sample = text[:50]
    for length in range(2, min(10, len(sample) // 3 + 1)):
        for i in range(len(sample) - 3 * length + 1):
            if (sample[i:i + length]
                    == sample[i + length:i + 2 * length]
                    == sample[i + 2 * length:i + 3 * length]):
                return True
    return False


def _whisper_is_usable(whisper_segments, total_end=None):
    if not whisper_segments:
        return False

    if total_end is None:
        total_end = max((getattr(s, "end", 0.0) for s in whisper_segments), default=0.0)

    # Tiny/failed Whisper on long media often returns only a few broad segments.
    if total_end >= 300 and len(whisper_segments) < 20:
        return False

    if total_end >= 300:
        covered = sum(max(0.0, getattr(s, "end", 0.0) - getattr(s, "start", 0.0))
                      for s in whisper_segments)
        if total_end > 0 and covered / total_end < 0.30:
            return False

    non_empty = [s for s in whisper_segments if getattr(s, "text", "").strip()]
    if not non_empty:
        return False

    if len(whisper_segments) < 5:
        return True

    digit_segs = []
    for i, s in enumerate(whisper_segments):
        cleaned = unicodedata.normalize("NFKC", s.text.strip())
        if cleaned.isdigit():
            digit_segs.append((i, int(cleaned)))

    if len(digit_segs) / len(whisper_segments) >= 0.2:
        return False

    texts = [unicodedata.normalize("NFKC", s.text.strip()) for s in non_empty]
    top_count = Counter(texts).most_common(1)[0][1]
    if top_count / len(texts) >= 0.30:
        return False

    max_run = 0
    run = 0
    for j in range(1, len(digit_segs)):
        ip, vp = digit_segs[j - 1]
        ic, vc = digit_segs[j]
        if ic == ip + 1 and 0 < vc - vp <= 3:
            run += 1
            max_run = max(max_run, run)
        else:
            run = 0

    return max_run < 5


def _pause_segment(words, start_id, lang, pause_threshold=0.5):
    if not words:
        return []
    words = sorted(words, key=lambda w: w["start"])
    joiner = "" if lang in ("ja", "zh", "ko") else " "

    segments = []
    buf = [words[0]]

    for i in range(1, len(words)):
        prev, curr = words[i - 1], words[i]
        gap = curr["start"] - prev["end"]

        should_break = gap >= pause_threshold
        if prev["text"].rstrip().endswith(_SENTENCE_ENDERS):
            should_break = True

        text_so_far = joiner.join(w["text"] for w in buf)
        span = curr["start"] - buf[0]["start"]
        if span >= 8.0 or len(text_so_far) >= 40:
            should_break = True

        if should_break:
            segments.append(_words_to_segment(buf, start_id + len(segments), joiner))
            buf = [curr]
        else:
            buf.append(curr)

    if buf:
        segments.append(_words_to_segment(buf, start_id + len(segments), joiner))
    return segments


def _qwen_chunks_to_segments(qwen_chunks, lang):
    words = _flatten_qwen_words(qwen_chunks)
    return _pause_segment(words, 0, lang)


def _propagate_l1_marks(l1_sources, assembled):
    if not l1_sources or not assembled:
        return assembled

    l1_sources = sorted(l1_sources, key=lambda s: s.start)
    l1_starts = [s.start for s in l1_sources]
    for asm in assembled:
        asm_duration = asm.end - asm.start

        if asm_duration <= 0:
            asm.filtered = False
            asm.filter_reason = None
            asm.uncertain = False
            continue

        lo = max(0, bisect.bisect_right(l1_starts, asm.start) - 1)
        hi = min(len(l1_sources), bisect.bisect_right(l1_starts, asm.end) + 1)

        filtered_overlap = 0.0
        best_reason = None
        best_reason_ov = 0.0
        inherited_uncertain = False
        inherited_bgm = False

        for k in range(lo, hi):
            src = l1_sources[k]
            ov = min(asm.end, src.end) - max(asm.start, src.start)
            if ov <= 0:
                continue

            if getattr(src, "filtered", False):
                filtered_overlap += ov
                if ov > best_reason_ov:
                    best_reason_ov = ov
                    best_reason = getattr(src, "filter_reason", "L1")

            if getattr(src, "uncertain", False):
                inherited_uncertain = True
            if getattr(src, "bgm_override", False):
                inherited_bgm = True

        if filtered_overlap / asm_duration > 0.5:
            asm.filtered = True
            asm.filter_reason = best_reason
        else:
            asm.filtered = False
            asm.filter_reason = None

        asm.uncertain = inherited_uncertain
        if inherited_bgm:
            asm.bgm_override = True

    return assembled


def _find_uncovered_gaps(segments, total_start, total_end, min_gap=3.0):
    if not segments:
        if total_end - total_start >= min_gap:
            return [(total_start, total_end)]
        return []

    intervals = sorted([(s.start, s.end) for s in segments])
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    gaps = []
    cursor = total_start
    for start, end in merged:
        if start - cursor >= min_gap:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if total_end - cursor >= min_gap:
        gaps.append((cursor, total_end))
    return gaps


def _assemble_qwen_primary(qwen_words, whisper_segments, lang):
    if not qwen_words:
        return []

    joiner = "" if lang in ("ja", "zh", "ko") else " "
    base_pause = 0.5
    max_span = 5.0 if lang == "ja" else 8.0
    max_chars = 25 if lang == "ja" else 40
    ws_starts = [ws.start for ws in whisper_segments] if whisper_segments else []

    segments = []
    buf = [qwen_words[0]]

    for i in range(1, len(qwen_words)):
        prev, curr = qwen_words[i - 1], qwen_words[i]
        gap = curr["start"] - prev["end"]

        threshold = base_pause
        if ws_starts and gap > 0:
            mid = (prev["end"] + curr["start"]) / 2
            tag = _sv_tag_at(mid, whisper_segments, ws_starts)
            if tag in ("<|Breath|>", "<|Crying|>", "<|Laughter|>"):
                threshold = base_pause * 1.5

        should_break = gap >= threshold
        if prev["text"].rstrip().endswith(_SENTENCE_ENDERS):
            should_break = True
        if lang == "ja" and _should_break_ja_sentence(prev.get("text", ""), gap):
            should_break = True

        text_so_far = joiner.join(w["text"] for w in buf)
        span = curr["start"] - buf[0]["start"]
        if span >= max_span or len(text_so_far) >= max_chars:
            should_break = True

        if should_break:
            segments.append(_words_to_segment(buf, len(segments), joiner))
            buf = [curr]
        else:
            buf.append(curr)

    if buf:
        segments.append(_words_to_segment(buf, len(segments), joiner))

    for seg in segments:
        if whisper_segments:
            overlapping = [
                ws.text for ws in whisper_segments
                if ws.start < seg.end and ws.end > seg.start
            ]
            seg.whisper_text = " ".join(overlapping) if overlapping else ""
        else:
            seg.whisper_text = ""

    return segments


def _assemble_whisper_primary(qwen_words, whisper_segments, lang):
    if not whisper_segments:
        return _pause_segment(qwen_words, 0, lang)

    joiner = "" if lang in ("ja", "zh", "ko") else " "
    seg_starts = [ws.start for ws in whisper_segments]

    word_to_seg = {}
    for w_idx, w in enumerate(qwen_words):
        right = bisect.bisect_right(seg_starts, w["end"])
        left = max(0, bisect.bisect_left(seg_starts, w["start"]) - 1)

        best_idx, best_ov = None, 0.0
        for s_idx in range(left, min(right + 1, len(whisper_segments))):
            ws = whisper_segments[s_idx]
            ov = min(w["end"], ws.end) - max(w["start"], ws.start)
            if ov > best_ov:
                best_ov = ov
                best_idx = s_idx

        if best_idx is not None and best_ov > 0:
            word_to_seg[w_idx] = best_idx

    seg_to_words = defaultdict(list)
    for w_idx, s_idx in word_to_seg.items():
        seg_to_words[s_idx].append(w_idx)

    matched = set()
    segments = []
    for idx, ws in enumerate(whisper_segments):
        w_indices = seg_to_words.get(idx, [])
        if w_indices:
            words = sorted(
                [qwen_words[i] for i in w_indices],
                key=lambda w: w["start"])
            matched.update(w_indices)
            text = joiner.join(w["text"] for w in words)
            seg = Segment(id=idx, text=text,
                          start=words[0]["start"], end=words[-1]["end"])
        else:
            seg = Segment(id=idx, text=ws.text,
                          start=ws.start, end=ws.end)
        seg.whisper_text = ws.text
        seg.sensevoice_tag = getattr(ws, "sensevoice_tag", None)
        segments.append(seg)

    unmatched = [qwen_words[i]
                 for i in range(len(qwen_words)) if i not in matched]
    if unmatched:
        extra = _pause_segment(unmatched, len(segments), lang)
        segments.extend(extra)
        segments.sort(key=lambda s: s.start)

    return segments


def _assemble_emergency_fallback(qwen_words, whisper_segments, lang, total_end):
    segments = []

    for ws in whisper_segments:
        if ws.text.strip().isdigit() or _is_repetition_pattern(ws.text):
            continue
        seg = Segment(id=len(segments), text=ws.text,
                      start=ws.start, end=ws.end)
        seg.whisper_text = ws.text
        seg.uncertain = True
        segments.append(seg)

    covered = [(s.start, s.end) for s in segments]
    orphans = [
        w for w in qwen_words
        if not _is_repetition_pattern(w["text"])
        and not any(s <= w["start"] and w["end"] <= e for s, e in covered)
    ]
    if orphans:
        extra = _pause_segment(orphans, len(segments), lang)
        for s in extra:
            s.uncertain = True
        segments.extend(extra)

    segments.sort(key=lambda s: s.start)
    gaps = _find_uncovered_gaps(segments, 0.0, total_end)
    for gap_start, gap_end in gaps:
        seg = Segment(id=len(segments),
                      text="[此时段字幕暂不可用]",
                      start=gap_start, end=gap_end)
        seg.uncertain = True
        seg.whisper_text = ""
        segments.append(seg)

    segments.sort(key=lambda s: s.start)
    return segments


def _smart_assemble(qwen_chunks, whisper_segments, lang, primary_engine):
    qwen_words = _flatten_qwen_words(qwen_chunks)
    total_end = 0.0
    if whisper_segments:
        total_end = whisper_segments[-1].end
    elif qwen_words:
        total_end = qwen_words[-1]["end"]

    whisper_ok = _whisper_is_usable(whisper_segments, total_end=total_end)
    qwen_ok = _qwen_is_reliable(qwen_chunks)

    if primary_engine == "qwen" and not qwen_ok:
        primary_engine = "whisper" if whisper_ok else "dual_failed"
    elif primary_engine == "whisper" and not whisper_ok:
        primary_engine = "qwen" if qwen_ok else "dual_failed"

    if primary_engine == "qwen":
        if lang == "en" and whisper_ok:
            return _assemble_whisper_primary(qwen_words, whisper_segments, lang)
        return _assemble_qwen_primary(qwen_words, whisper_segments, lang)
    elif primary_engine == "whisper":
        return _assemble_whisper_primary(qwen_words, whisper_segments, lang)
    else:
        return _assemble_emergency_fallback(
            qwen_words, whisper_segments, lang, total_end)


def _vad_speech_gate(audio_path: str, skip_vad: bool) -> None:
    """VAD 语音占比 gate：语音极低时询问用户，较低时告警。"""
    if not skip_vad:
        _log.info("VAD 语音占比检测中…")
        ratio = compute_speech_ratio_via_vad(audio_path)
        if ratio is not None:
            _log.info("语音占比: %.1f%%", ratio)
            if ratio < 20:
                r = input(f"       ⚠️ 语音极低({ratio:.0f}%)，可能为纯音乐。继续？[y/N] ")
                if r.lower() != "y":
                    raise RuntimeError("用户取消")
            elif ratio < 40:
                _log.warning("非语音内容较多。如对话被过滤可用 --vad-threshold 0.4 启用内置 VAD")


def _whisper_words_to_dicts(seg):
    """把 Whisper 段的词级时间戳转为 dict 列表；无词时返回 None。"""
    if not getattr(seg, "words", None):
        return None
    words = []
    for w in seg.words:
        if isinstance(w, dict):
            words.append(w)
        else:
            words.append({
                "word": getattr(w, "word", ""),
                "start": getattr(w, "start", 0.0),
                "end": getattr(w, "end", 0.0),
                "probability": getattr(w, "probability", 0.0),
            })
    return words


def _whisper_output_to_segments(whisper_output, results, info, n_total,
                                progress_callback):
    """把 Whisper 段迭代为 Segment 追加进 results（原地），返回累计段数。"""
    for seg in whisper_output:
        text = seg.text.strip()
        n_total += 1

        s = Segment(
            id=len(results),
            start=round(seg.start, 2),
            end=round(seg.end, 2),
            text=text,
        )
        _words = _whisper_words_to_dicts(seg)
        if _words is not None:
            s.words = _words
        results.append(s)

        if progress_callback and n_total % 10 == 0:
            try:
                progress_callback(n_total)
            except Exception:
                pass
        _audio_duration = getattr(info, "duration", 0) or 0
        if n_total % 50 == 0 and _audio_duration > 0 and seg.end > 0:
            pct = min(99, round(seg.end / _audio_duration * 100))
            _log.info("Whisper 进度: %d%% (音频 %ds/%ds, 已识别 %d 段)",
                      pct, int(seg.end), int(_audio_duration), n_total)
    return n_total


def _collect_qwen_chunks(qwen_thread, qwen_holder, transcription_tier,
                         filter_keywords, audio_path, detected_lang, cache_dir):
    """Phase F：等待 Qwen 线程 → 取结果；精确模式无线程时串行兜底跑一次。"""
    if qwen_thread:
        qwen_thread.join()
        _log.info("Qwen3-ASR 独立线程已结束")
    qwen_chunks = qwen_holder.get("data", [])

    # ── Serial Qwen fallback (non-pipeline mode, precise only) ──
    if transcription_tier == "precise" and filter_keywords and not qwen_thread:
        _log.info("Qwen3-ASR 交叉验证开始 (tier=%s, 串行)", transcription_tier)
        from backend.core.qwen_asr_runner import run_qwen_asr, is_qwen_asr_available
        if is_qwen_asr_available():
            qwen_chunks = run_qwen_asr(audio_path, language=detected_lang,
                                       cache_dir=cache_dir)
        else:
            _log.warning("Qwen3-ASR 不可用，跳过交叉验证")
    return qwen_chunks


@dataclass
class _Routing:
    """Phase F+G+H 路由结果：等 Qwen → 选主引擎 → FA 需求 → L1 段。"""
    qwen_chunks: list
    whisper_segments: list
    which_primary: str
    qwen_raw: list
    has_qwen_timestamps: bool
    need_external_fa: bool
    qwen_l1_segments: list


def _resolve_routing(qwen_thread, qwen_holder, transcription_tier,
                     filter_keywords, audio_path, detected_lang, cache_dir,
                     standard_primary, use_whisper, results) -> "_Routing":
    """等待 Qwen → 按模式/语言选主引擎 → 判定外部 FA 需求 → 准备 L1 段。"""
    qwen_chunks = _collect_qwen_chunks(
        qwen_thread, qwen_holder, transcription_tier, filter_keywords,
        audio_path, detected_lang, cache_dir)

    # ── P254/P276 智能路由：按模式和源语言选择主引擎 ──
    _whisper_segments = None
    _which_primary, _keep_qwen_for_l2 = choose_transcription_primary(
        transcription_tier, standard_primary, detected_lang, qwen_chunks)
    _qwen_raw = []
    if _which_primary == "qwen":
        _log.info("%s 模式: %s → Qwen3-ASR 主引擎", transcription_tier, detected_lang)
        if use_whisper:
            _whisper_segments = results
        _qwen_raw = qwen_chunks
        qwen_chunks = list(_qwen_raw)
    else:
        if transcription_tier == "precise" and detected_lang in QWEN_PRIMARY_LANGS and not qwen_chunks:
            _log.warning(
                "P254: 语言=%s 在 QWEN_PRIMARY_LANGS 中，但 Qwen3-ASR 无产出，"
                "回退到 Whisper 主引擎。请检查 Qwen 模型是否正常加载。",
                detected_lang)
        _log.info("%s 模式: Whisper 主引擎", transcription_tier)
        qwen_chunks = list(qwen_chunks or [])

    # ── Determine if external FA is needed ──
    # MLX path: Qwen segments carry word-level timestamps from internal FA → skip external FA
    # PyTorch path: no Qwen timestamps → need external FA for L1
    has_qwen_timestamps = _has_timestamp_segments(qwen_chunks)
    need_external_fa = not has_qwen_timestamps

    _qwen_l1_segments = []
    if _which_primary == "qwen":
        _qwen_l1_segments = _qwen_chunks_to_segments(qwen_chunks, detected_lang)
        if not _qwen_l1_segments:
            _qwen_l1_segments = list(results)

    return _Routing(qwen_chunks, _whisper_segments, _which_primary,
                    _qwen_raw, has_qwen_timestamps, need_external_fa,
                    _qwen_l1_segments)


def _start_sv_thread(audio_path, _sv_target, _sv_holder):
    """启动 SenseVoice 后台线程，结果写入 _sv_holder["data"]。"""
    from backend.core.sensevoice_runner import run_sensevoice

    def _run_sv():
        try:
            _sv_holder["data"] = run_sensevoice(audio_path, device="cpu", segments=_sv_target)
        except Exception as _sv_exc:
            _log.error("SenseVoice 线程异常: %s", _sv_exc, exc_info=True)
    t = threading.Thread(target=_run_sv, daemon=True)
    t.start()
    return t


def _start_fa_thread(audio_path, _fa_target, detected_lang, _fa_holder):
    """启动 Qwen3-FA 并行对齐线程，结果写入 _fa_holder["data"]。"""
    from backend.core.qwen_aligner import QwenAligner, align_segments

    def _run_fa():
        aligner = None
        try:
            aligner = QwenAligner()
            if aligner._model is not None:
                _fa_holder["data"] = align_segments(
                    aligner, audio_path, _fa_target, language=detected_lang)
        except Exception as e:
            _fa_holder["_quality"] = "fallback"
            _log.warning("Qwen3-FA 并行对齐失败: %s", e)
        finally:
            if aligner is not None and aligner._model is not None:
                aligner.close()
    t = threading.Thread(target=_run_fa, daemon=True)
    t.start()
    return t


def _dispatch_sv_fa(audio_path, detected_lang, filter_keywords, use_parallel,
                    transcription_tier, has_qwen_timestamps, need_external_fa,
                    _sv_target, _fa_target):
    """Phase I：派发 SenseVoice + 外部 FA（并行）；或串行降级直接拿 sv_events。
    返回 (sv_thread, _sv_holder, fa_thread, _fa_holder, sv_events)。"""
    sv_thread = None
    fa_thread = None
    _sv_holder = {}
    _fa_holder = {}
    sv_events = None
    if use_parallel and filter_keywords:
        from backend.core.sensevoice_runner import is_sensevoice_available
        if is_sensevoice_available():
            if has_qwen_timestamps:
                _log.info("SenseVoice 声音分类已启动（Qwen 内部 FA 已提供时间戳，跳过外部 FA）")
            else:
                _log.info("SenseVoice 声音分类与 Qwen3-FA 同时启动")
            sv_thread = _start_sv_thread(audio_path, _sv_target, _sv_holder)
        else:
            log_sensevoice_unavailable()

        if need_external_fa:
            from backend.core.qwen_aligner import is_qwen_aligner_available
            if is_qwen_aligner_available():
                fa_thread = _start_fa_thread(
                    audio_path, _fa_target, detected_lang, _fa_holder)
    elif filter_keywords and transcription_tier in ("standard", "precise"):
        _log.info("SenseVoice 声音分类开始 (tier=%s, 串行降级)", transcription_tier)
        from backend.core.sensevoice_runner import (
            run_sensevoice, is_sensevoice_available,
        )
        if is_sensevoice_available():
            sv_events = run_sensevoice(audio_path, device="cpu", segments=_sv_target)
        else:
            log_sensevoice_unavailable()
    return sv_thread, _sv_holder, fa_thread, _fa_holder, sv_events


def _await_sv_fa_threads(sv_thread, _sv_holder, fa_thread, sv_events):
    """Phase J：等待并行 SV/FA 线程结束，回收 sv_events。"""
    if sv_thread:
        sv_thread.join()
        sv_events = _sv_holder.get("data")
        if sv_events is None:
            _log.warning("SenseVoice 线程结束但未返回事件，跳过人声地图")
    if fa_thread:
        fa_thread.join()
    return sv_events


def _serial_fa_align_and_apply(audio_path, _fa_target, detected_lang,
                               use_whisper, results):
    """Phase K 串行分支：外部 FA 分段对齐并应用时间戳。
    返回 (aligner_result, results)。"""
    from backend.core.qwen_aligner import (
        QwenAligner, is_qwen_aligner_available, apply_aligner_timestamps,
        align_segments,
    )
    aligner_result = None
    if is_qwen_aligner_available():
        aligner = None
        try:
            _log.info("Qwen3-ForcedAligner 词级对齐开始（分段模式）")
            aligner = QwenAligner()
            if aligner._model is not None:
                aligner_result = align_segments(
                    aligner, audio_path, _fa_target, language=detected_lang,
                )
                if aligner_result:
                    _fa_target = apply_aligner_timestamps(_fa_target, aligner_result)
                    if use_whisper:
                        results = _fa_target
                    _log.info("Qwen3-FA 对齐完成: %d 词", len(aligner_result))
            else:
                _log.warning("Qwen3-FA 模型未加载，跳过对齐")
        except Exception as e:
            _log.warning("Qwen3-FA 对齐失败，保留原始时间戳: %s", e)
            aligner_result = []
        finally:
            if aligner is not None and aligner._model is not None:
                aligner.close()
    return aligner_result, results


def _apply_fa_timestamps(fa_thread, _fa_holder, need_external_fa, filter_keywords,
                         transcription_tier, audio_path, _fa_target, detected_lang,
                         use_whisper, results):
    """Phase K：应用外部 FA 时间戳（并行结果优先，否则串行对齐）。
    返回 (aligner_result, results)。"""
    aligner_result = None
    if fa_thread:
        fa_data = _fa_holder.get("data")
        if fa_data:
            from backend.core.qwen_aligner import apply_aligner_timestamps
            aligner_result = fa_data
            _fa_target = apply_aligner_timestamps(_fa_target, aligner_result)
            if use_whisper:
                results = _fa_target
            _log.info("Qwen3-FA 对齐完成（并行）: %d 词", len(aligner_result))
            _fa_holder["_quality"] = "official"
    elif need_external_fa and filter_keywords and transcription_tier in ("standard", "precise"):
        aligner_result, results = _serial_fa_align_and_apply(
            audio_path, _fa_target, detected_lang, use_whisper, results)
    return aligner_result, results


def _annotate_fa_quality(results, has_qwen_timestamps, fa_thread, _fa_holder,
                         aligner_result):
    """Phase L (P214)：标注每段 FA 质量，fallback 时告警。"""
    if has_qwen_timestamps:
        _fa_quality = "qwen_internal"
    elif fa_thread:
        _fa_quality = _fa_holder.get("_quality", "fallback")
    elif aligner_result is not None:
        _fa_quality = "official" if aligner_result else "fallback"
    else:
        _fa_quality = "not_attempted"
    for seg in results:
        seg._fa_quality = _fa_quality
    if _fa_quality == "fallback":
        _log.warning("⚠ 词级对齐未成功，字幕使用 Whisper 粗时间戳")


def _run_sensevoice_and_fa(audio_path, detected_lang, filter_keywords,
                           use_parallel, use_whisper, transcription_tier,
                           has_qwen_timestamps, need_external_fa,
                           _which_primary, _qwen_l1_segments, results):
    """Phase I+J+K+L 协调：派发 → 等待 → 应用 FA → 标注质量。
    返回 (sv_events, aligner_result, results, _sv_target)。"""
    # P268: SenseVoice follows the Qwen timeline when Qwen is primary.
    _sv_target = _qwen_l1_segments if _which_primary == "qwen" and _qwen_l1_segments else results
    # FA alignment target: standard mode has no Whisper segments, align Qwen segments instead
    _fa_target = _sv_target if _which_primary == "qwen" else results

    sv_thread, _sv_holder, fa_thread, _fa_holder, sv_events = _dispatch_sv_fa(
        audio_path, detected_lang, filter_keywords, use_parallel,
        transcription_tier, has_qwen_timestamps, need_external_fa,
        _sv_target, _fa_target)
    sv_events = _await_sv_fa_threads(sv_thread, _sv_holder, fa_thread, sv_events)
    aligner_result, results = _apply_fa_timestamps(
        fa_thread, _fa_holder, need_external_fa, filter_keywords,
        transcription_tier, audio_path, _fa_target, detected_lang,
        use_whisper, results)
    _annotate_fa_quality(
        results, has_qwen_timestamps, fa_thread, _fa_holder, aligner_result)
    return sv_events, aligner_result, results, _sv_target


def _infer_standard_language(use_whisper, detected_lang, sv_events):
    """Phase M：标准模式无 Whisper 时，从 SenseVoice 事件推断语言。"""
    if not use_whisper and detected_lang == "auto" and sv_events:
        lang_counts = Counter(
            ev.get("tags", {}).get("language", "unknown")
            for ev in sv_events if ev.get("event") == "Speech"
        )
        if lang_counts:
            top_lang = lang_counts.most_common(1)[0][0]
            _SV_LANG_MAP = {"zh": "zh", "en": "en", "ja": "ja", "ko": "ko",
                            "yue": "zh"}
            if top_lang in _SV_LANG_MAP:
                detected_lang = _SV_LANG_MAP[top_lang]
                _log.info("标准模式: SenseVoice 检测语言=%s", detected_lang)
    return detected_lang


def _make_voice_timeline(sv_events, results, _qwen_l1_segments):
    """Phase N（P275）：从 SenseVoice 事件构建人声地图，返回 timeline 或 None。"""
    _voice_timeline = None
    if sv_events:
        _audio_dur = 0.0
        if results:
            _audio_dur = max(s.end for s in results)
        elif _qwen_l1_segments:
            _audio_dur = max(s.end for s in _qwen_l1_segments)
        if _audio_dur > 0:
            _voice_timeline = _build_voice_timeline(sv_events, _audio_dur)
            _log.info("P275 人声地图: %.1f%% 人声, %d 个语音区间, %d 个非语音区间",
                      _voice_timeline["voice_ratio"] * 100,
                      len(_voice_timeline["voice_regions"]),
                      len(_voice_timeline["non_voice_regions"]))
    return _voice_timeline


def _run_l1_dual_signal(filter_keywords, _which_primary, _qwen_l1_segments,
                        qwen_chunks, aligner_result, detected_lang):
    """Phase P（P268）：L1 双信号交叉验证（SenseVoice × FA，平台无关）。"""
    if filter_keywords and _which_primary == "qwen" and _qwen_l1_segments:
        from backend.core.hallucination_filter import (
            _layer1_dual_signal, _qwen_segments_as_alignment,
        )
        # Mac (MLX): Qwen 内置 FA → 从 qwen_chunks 提取对齐数据
        # Windows (PyTorch): 外部 FA → 用 aligner_result
        if _has_timestamp_segments(qwen_chunks):
            _l1_align = _qwen_segments_as_alignment(qwen_chunks)
        elif aligner_result:
            _l1_align = aligner_result
        else:
            _l1_align = None
        for _qseg in _qwen_l1_segments:
            if not getattr(_qseg, "filtered", False):
                _layer1_dual_signal(_qseg, _l1_align, detected_lang)


def _assemble_and_merge(_which_primary, _qwen_raw, _whisper_segments,
                        _qwen_l1_segments, results, detected_lang,
                        _voice_timeline):
    """Phase Q（P254）：Qwen 主引擎组装骨架 → 短字幕合并 → 人声地图质量日志。"""
    if _which_primary == "qwen":
        _assembled = _smart_assemble(_qwen_raw, whisper_segments=_whisper_segments, lang=detected_lang, primary_engine=_which_primary)
        _propagate_l1_marks(_qwen_l1_segments, _assembled)
        results = _assembled
    results = merge_short_subtitle_fragments(
        results, detected_lang, voice_timeline=_voice_timeline)
    if _voice_timeline:
        _vt_quality = summarize_voice_timeline_quality(
            results, _voice_timeline, detected_lang)
        _log.info(
            "P275 人声地图质量: 人声区=%d, 无字幕人声区=%d, 非人声短词=%d, 最长疑似漏字幕=%s",
            _vt_quality["voice_region_count"],
            _vt_quality["uncovered_voice_region_count"],
            _vt_quality["non_voice_short_fragment_count"],
            _vt_quality["longest_uncovered_voice_region"],
        )
    return results


def _apply_five_layer_filter(results, detected_lang, aligner_result, qwen_chunks,
                             use_whisper, _whisper_segments, _which_primary,
                             transcription_tier, filter_keywords):
    """Phase R：五层幻觉过滤（L2-L5；L1 已跑则跳过；标准模式无第二信号跳 L2）。"""
    # Standard mode: no Whisper → L2 cross-validation has no second signal, skip it
    _filter_qwen = qwen_chunks if use_whisper else None
    _alt_segs = _whisper_segments if _which_primary == "qwen" else None
    if filter_keywords:
        results = filter_hallucinations(results, detected_lang,
                                        aligner_result=aligner_result,
                                        qwen_chunks=_filter_qwen,
                                        skip_l1=(_which_primary == "qwen"),
                                        skip_l2=(transcription_tier == "standard"),
                                        alt_segments=_alt_segs)
    return results


def _finalize_segments(results, speaker_detect, audio_path):
    """Phase S+T：空检查 + 说话人检测。"""
    if not results:
        _log.error("未检测到任何语音内容")
        raise RuntimeError("未检测到任何语音内容。视频可能为纯音乐或无声")
    # ── Step 7: Speaker detection ──
    if speaker_detect:
        _log.info("说话人检测开始（自适阈值）")
        results = _detect_speaker_changes(audio_path, results)
    return results


def _should_parallel_qwen(use_qwen, filter_keywords, use_whisper):
    """Phase C 条件：是否启动 Qwen 并行线程（与 Whisper 同跑）。"""
    if not (use_qwen and filter_keywords and use_whisper and should_parallel_asr()):
        return False
    from backend.core.qwen_asr_runner import is_qwen_asr_available
    return is_qwen_asr_available()


def _run_standard_language_router(audio_path, model_size, lang, source_lang,
                                  use_qwen, filter_keywords, cache_dir,
                                  progress_callback, _qwen_holder):
    """Phase D：标准模式 Whisper 短探测语言 → 选主引擎 → 语言适合则切 Qwen。
    返回 (info, detected_lang, _standard_primary)；切 Qwen 时结果写入 _qwen_holder。"""
    _log.info("标准模式: Whisper 短音频语言探测开始 (%s)", model_size)
    if progress_callback:
        progress_callback(-1)
    info = detect_language_with_whisper(
        audio_path,
        model_size=model_size,
        language=lang,
    )
    _log.info("检测语言: %s (概率 %.2f)", info.language, info.language_probability)
    detected_lang = info.language if info.language else source_lang
    lang_probability = info.language_probability or 0.0

    _standard_primary = choose_standard_primary_engine(
        detected_lang, lang_probability)
    _log.info(
        "标准模式: Whisper 检测语言=%s 概率=%.2f，主引擎=%s",
        detected_lang, lang_probability, _standard_primary)

    if _standard_primary == "qwen":
        from backend.core.qwen_asr_runner import run_qwen_asr, is_qwen_asr_available
        if use_qwen and filter_keywords and is_qwen_asr_available():
            release_whisper_model()
            _log.info("标准模式: 语言适合 Qwen，停止 Whisper，切换 Qwen")
            _log.info("标准模式: Qwen 使用锁定语言=%s", detected_lang)
            _qwen_holder["data"] = run_qwen_asr(
                audio_path, language=detected_lang,
                cache_dir=cache_dir)
        else:
            _log.warning("标准模式: Qwen3-ASR 不可用或内存不足，继续 Whisper 主引擎")
            _standard_primary = "whisper"
    return info, detected_lang, _standard_primary


def _run_full_whisper(audio_path, model_size, lang, vad_threshold, detected_lang,
                      results, n_total, progress_callback):
    """Phase E：加载 Whisper 全量转录 → 段迭代追加进 results → 释放模型。
    返回 (info, detected_lang, n_total)。"""
    _log.info("加载 Whisper %s 模型…", model_size)
    if progress_callback:
        progress_callback(-1)
    whisper_output, info = transcribe_whisper(
        audio_path,
        model_size=model_size,
        language=lang,
        vad_threshold=vad_threshold,
    )
    _log.info("检测语言: %s (概率 %.2f)", info.language, info.language_probability)
    detected_lang = info.language if info.language else detected_lang

    # ── Whisper segment iteration ──
    n_total = _whisper_output_to_segments(
        whisper_output, results, info, n_total, progress_callback)

    release_whisper_model()
    _log.info("Whisper 模型已释放")
    return info, detected_lang, n_total


def transcribe(audio_path: str, model_size: str = "medium",
               source_lang: str = "auto", skip_vad: bool = False,
               vad_threshold: float = 0.0,
               filter_keywords: bool = True,
               speaker_detect: bool = True,
               transcription_tier: str = "standard",
               progress_callback=None,
               cache_dir: str = None) -> list:
    """Transcribe audio, return list of Segment."""
    _log.info("transcribe() 开始: audio=%s model=%s lang=%s",
              audio_path, model_size, source_lang)

    _vad_speech_gate(audio_path, skip_vad)

    from backend.core.hardware_detector import detect_hardware

    hw = detect_hardware()
    ram_gb = hw.get("ram_gb", 0) or 0
    use_qwen = should_qwen_pipeline(transcription_tier, ram_gb)
    use_parallel = should_parallel_svfa(transcription_tier, ram_gb)
    use_whisper = should_load_whisper(transcription_tier)
    use_standard_router = transcription_tier == "standard"

    lang = None if source_lang == "auto" else source_lang
    detected_lang = source_lang
    results = []
    n_total = 0
    _standard_primary = None
    info = None

    # ═══════════════════════════════════════════════════════════════
    # Qwen-ASR: start early. In precise mode, runs in a parallel
    # thread alongside Whisper. In standard mode, runs synchronously
    # as the sole ASR engine.
    # ═══════════════════════════════════════════════════════════════
    qwen_thread = None
    _qwen_holder = {}
    # 注：闭包刻意捕获 transcribe 的 detected_lang（late-binding），不抽进 helper。
    # detected_lang 在 Whisper 后可能被重新赋值，此处保持原竞态行为（见 problems.md）。
    if _should_parallel_qwen(use_qwen, filter_keywords, use_whisper):
        from backend.core.qwen_asr_runner import run_qwen_asr

        def _run_qwen():
            _qwen_holder["data"] = run_qwen_asr(
                audio_path, language=detected_lang,
                cache_dir=cache_dir)
        qwen_thread = threading.Thread(target=_run_qwen, daemon=True)
        qwen_thread.start()
        _log.info("Qwen3-ASR 独立线程已启动（与 Whisper 并行）")

    # ═══════════════════════════════════════════════════════════════
    # Whisper: short language probe in standard mode, full engine in precise mode.
    # ═══════════════════════════════════════════════════════════════
    if use_standard_router:
        info, detected_lang, _standard_primary = _run_standard_language_router(
            audio_path, model_size, lang, source_lang, use_qwen,
            filter_keywords, cache_dir, progress_callback, _qwen_holder)

    if use_whisper or _standard_primary == "whisper":
        info, detected_lang, n_total = _run_full_whisper(
            audio_path, model_size, lang, vad_threshold, detected_lang,
            results, n_total, progress_callback)
    else:
        _log.info("标准模式: 跳过 Whisper 加载")

    if use_whisper or _standard_primary == "whisper":
        _log.info("Whisper 转录: %d 段, 语言=%s (概率 %.2f)", n_total, info.language, info.language_probability)

    # ── Phase F+G+H: 等 Qwen → 路由选主引擎 → FA 需求 → L1 段 ──
    _route = _resolve_routing(
        qwen_thread, _qwen_holder, transcription_tier, filter_keywords,
        audio_path, detected_lang, cache_dir, _standard_primary,
        use_whisper, results)
    qwen_chunks = _route.qwen_chunks
    _whisper_segments = _route.whisper_segments
    _which_primary = _route.which_primary
    _qwen_raw = _route.qwen_raw
    has_qwen_timestamps = _route.has_qwen_timestamps
    need_external_fa = _route.need_external_fa
    _qwen_l1_segments = _route.qwen_l1_segments

    # ── Phase I+J+K+L: SenseVoice + FA 派发/等待/应用/质量标注 ──
    sv_events, aligner_result, results, _sv_target = _run_sensevoice_and_fa(
        audio_path, detected_lang, filter_keywords, use_parallel, use_whisper,
        transcription_tier, has_qwen_timestamps, need_external_fa,
        _which_primary, _qwen_l1_segments, results)

    # ── Phase M: 标准模式从 SenseVoice 推断语言 ──
    detected_lang = _infer_standard_language(use_whisper, detected_lang, sv_events)

    # ── Phase N (P275): 人声地图 ──
    _voice_timeline = _make_voice_timeline(sv_events, results, _qwen_l1_segments)

    # ── Phase O: 应用 SenseVoice 标签到 L1 源段 ──
    if sv_events:
        _apply_sv_tags(_sv_target, sv_events)

    # ── Phase P (P268): L1 双信号交叉验证 ──
    _run_l1_dual_signal(filter_keywords, _which_primary, _qwen_l1_segments,
                        qwen_chunks, aligner_result, detected_lang)

    # ── Phase Q (P254): 组装 + 短字幕合并 ──
    results = _assemble_and_merge(
        _which_primary, _qwen_raw, _whisper_segments, _qwen_l1_segments,
        results, detected_lang, _voice_timeline)

    # ── Phase R: 五层幻觉过滤 ──
    results = _apply_five_layer_filter(
        results, detected_lang, aligner_result, qwen_chunks, use_whisper,
        _whisper_segments, _which_primary, transcription_tier, filter_keywords)

    # ── Phase S+T: 空检查 + 说话人检测 ──
    results = _finalize_segments(results, speaker_detect, audio_path)

    _log.info("transcribe() 结束: 返回 %d 段", len(results))
    return results
