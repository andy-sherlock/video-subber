"""Transcription via faster-whisper + resemblyzer speaker change detection.

Simplified pipeline:
  Whisper (context enabled) → keyword hallucination filter →
  hallucination chain dedup → speaker detection.
"""
import logging
import numpy as np
from faster_whisper import WhisperModel
from backend.core.vad_filter import compute_speech_ratio_via_vad

_log = logging.getLogger("transcriber")


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


HALLUCINATION_KEYWORDS = (
    "音乐", "music", "♪", "♫", "[music]", "[applause]", "[laughter]",
    "掌声", "笑声", "鼓掌", "谢谢观看", "请订阅", "字幕组", "字幕由",
    "музыка", "МУЗЫКА", "аплодисменты", "смех",
    "音楽", "拍手", "笑い声",
)


def _is_hallucination(text: str) -> bool:
    t = text.strip().lower()
    if not t:
        return True
    for kw in HALLUCINATION_KEYWORDS:
        if kw.lower() in t:
            return True
    if all(not c.isalnum() and c not in "你我他她它的了是有不在" for c in t):
        return True
    if 4 < len(t) <= 30 and len(set(t)) / len(t) < 0.3:
        return True
    return False


def _dedup_hallucination_chains(segments: list) -> list:
    """Remove consecutive segments with identical text (hallucination chains).

    With condition_on_previous_text=True, Whisper may repeat the same text
    across multiple segments. Keep only the first occurrence in each chain.
    """
    if len(segments) < 2:
        return segments
    result = [segments[0]]
    for seg in segments[1:]:
        if seg.text.strip() == result[-1].text.strip():
            _log.info("幻觉链: 丢弃重复段 start=%.2f text=%r", seg.start, seg.text[:50])
            continue
        result.append(seg)
    for i, s in enumerate(result):
        s.id = i
    return result


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
            if dur < 0.3:
                return None
            y, _sr = librosa.load(audio_path, sr=16000, offset=start, duration=dur)
            if len(y) < 16000 * 0.3:
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


def transcribe(audio_path: str, model_size: str = "medium",
               source_lang: str = "auto", skip_vad: bool = False,
               vad_threshold: float = 0.0,
               filter_keywords: bool = True,
               speaker_detect: bool = True,
               progress_callback=None) -> list:
    """Transcribe audio, return list of Segment."""
    _log.info("transcribe() 开始: audio=%s model=%s lang=%s",
              audio_path, model_size, source_lang)

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

    _log.info("加载 Whisper %s 模型…", model_size)
    if progress_callback:
        progress_callback(-1)
    model = WhisperModel(model_size, device="auto", compute_type="auto")

    lang = None if source_lang == "auto" else source_lang

    vad_kwargs = {}
    if vad_threshold > 0:
        vad_kwargs = {
            "vad_filter": True,
            "vad_parameters": {
                "threshold": vad_threshold,
                "min_speech_duration_ms": 250,
                "min_silence_duration_ms": 100,
            },
        }

    segments_iter, info = model.transcribe(
        audio_path, beam_size=5, language=lang,
        condition_on_previous_text=True,
        log_prob_threshold=-1.0,
        word_timestamps=True,
        **vad_kwargs
    )
    _log.info("检测语言: %s (概率 %.2f)", info.language, info.language_probability)

    results = []
    n_total = 0
    dropped = 0

    for seg in segments_iter:
        text = seg.text.strip()
        n_total += 1

        if filter_keywords and _is_hallucination(text):
            dropped += 1
            _log.debug("DROP hallucination: start=%.2f text=%r", seg.start, text[:80])
            continue

        if len(text) < 2:
            _log.debug("DROP too short: start=%.2f text=%r", seg.start, text)
            continue

        s = Segment(
            id=len(results),
            start=round(seg.start, 2),
            end=round(seg.end, 2),
            text=text,
        )
        if hasattr(seg, "words") and seg.words:
            s.words = []
            for w in seg.words:
                if isinstance(w, dict):
                    s.words.append(w)
                else:
                    s.words.append({
                        "word": getattr(w, "word", ""),
                        "start": getattr(w, "start", 0.0),
                        "end": getattr(w, "end", 0.0),
                        "probability": getattr(w, "probability", 0.0),
                    })
        results.append(s)

        if progress_callback and n_total % 10 == 0:
            progress_callback(n_total)

    _log.info("过滤: 总=%d 保留=%d 幻觉关键词=%d", n_total, len(results), dropped)

    before = len(results)
    results = _dedup_hallucination_chains(results)
    if len(results) < before:
        _log.info("幻觉链去重: %d → %d 段", before, len(results))

    if not results:
        _log.error("未检测到任何语音内容")
        raise RuntimeError("未检测到任何语音内容。视频可能为纯音乐或无声")

    if speaker_detect:
        _log.info("说话人检测开始（自适阈值）")
        results = _detect_speaker_changes(audio_path, results)

    _log.info("transcribe() 结束: 返回 %d 段", len(results))
    return results
