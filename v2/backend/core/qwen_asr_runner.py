"""Qwen3-ASR cross-validation transcription via MLX (Apple Silicon) or PyTorch (Windows).

Runs Qwen3-ASR on full audio and maps its transcription to Whisper segments.
Two alignment paths:
  - Timestamp alignment: when the model returns word-level timestamps (PyTorch)
  - Text similarity alignment: when timestamps are unavailable (MLX / Apple Silicon)

For audio longer than 20 minutes, chunks are formed at Whisper segment boundaries
(batched into ~5-minute groups) and each chunk is transcribed independently.
"""
import gc
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from difflib import SequenceMatcher

_log = logging.getLogger("qwen_asr_runner")

_SENTENCE_SPLIT_RE = re.compile(r"[。！？.!?；;]+")

# ── Chunking constants ──
_CHUNK_MAX_DURATION_S = 300       # 5 minutes per chunk
_LONG_AUDIO_THRESHOLD_S = 1200    # 20 minutes — switch to chunked mode
_AUDIO_PAD_S = 5.0                # pronunciation buffer on each side

_USE_MLX = False
_USE_PYTORCH = False
try:
    from qwen3_asr_mlx import Qwen3ASR  # noqa: F401
    _USE_MLX = True
except ImportError:
    pass
try:
    from qwen_asr import Qwen3ASRModel  # noqa: F401
    _USE_PYTORCH = True
except ImportError:
    pass


def is_qwen_asr_available() -> bool:
    return _USE_MLX or _USE_PYTORCH


# ── P147: anti-collapse generation parameters ──
_QWEN_REPETITION_PENALTY = 1.4   # default 1.2 too weak to break repetition loops
_QWEN_REPETITION_WINDOW = 256    # default 100 too short for long repeats
_QWEN_TOKENS_PER_SEC = 10        # real speech ~3-5 words/s; default ~50 let a
                                 # collapse run to 15000 tok/chunk (13.5 min hang)
_QWEN_MIN_TOKENS = 256


def _qwen_gen_kwargs(duration_s, language=None, use_mlx=True) -> dict:
    """Anti-collapse kwargs for model.transcribe() (P147).

    Caps generated tokens to ~10/sec so a repetition collapse force-stops in
    minutes instead of running to the default ~50/sec ceiling, tightens the
    repetition penalty/window, and forwards the language Whisper already
    detected so Qwen doesn't re-guess (and mis-guess) per chunk.

    Param names differ by backend: the MLX `transcribe()` takes `max_tokens` +
    `repetition_penalty`/`repetition_context_size`; the PyTorch wrapper takes
    `max_new_tokens` (its repetition params are unverified here, so omitted).
    """
    kwargs = {}
    if language and language not in ("auto", ""):
        kwargs["language"] = language
    if duration_s and duration_s > 0:
        cap = max(_QWEN_MIN_TOKENS, int(duration_s * _QWEN_TOKENS_PER_SEC))
        kwargs["max_tokens" if use_mlx else "max_new_tokens"] = cap
    if use_mlx:
        kwargs["repetition_penalty"] = _QWEN_REPETITION_PENALTY
        kwargs["repetition_context_size"] = _QWEN_REPETITION_WINDOW
    return kwargs


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_SPLIT_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def _match_qwen_to_whisper(
    qwen_sentences: list[str],
    whisper_segments: list,
) -> list[tuple[str, int]]:
    pairs: list[tuple[str, int]] = []
    segment_texts = [
        s.text.lower().strip() if s.text else "" for s in whisper_segments
    ]

    for qs in qwen_sentences:
        qs_key = qs.lower().strip()
        if not qs_key:
            continue
        best_idx = -1
        best_score = 0.0
        for i, st in enumerate(segment_texts):
            if not st:
                continue
            score = SequenceMatcher(None, qs_key, st).ratio()
            if score > best_score:
                best_score = score
                best_idx = i
        if best_score > 0.2:
            pairs.append((qs, best_idx))

    return pairs


def align_by_timestamps(timestamp_items, segments: list) -> int:
    matched = 0
    for item in timestamp_items:
        start_s = getattr(item, 'start_time', 0)
        end_s = getattr(item, 'end_time', 0)
        text = getattr(item, 'text', '')
        if not text:
            continue
        for seg in segments:
            overlap = min(end_s, seg.end) - max(start_s, seg.start)
            if overlap > 0:
                existing = seg.qwen_text or ""
                seg.qwen_text = (existing + " " + text).strip()
                matched += 1
                break
    return matched


def align_by_similarity(full_text: str, segments: list) -> int:
    qwen_sentences = _split_sentences(full_text)
    if not qwen_sentences:
        return 0

    n_qwen = len(qwen_sentences)
    n_whisper = len(segments)
    count_diff = abs(n_qwen - n_whisper)
    threshold = max(n_whisper, 1) * 0.2

    if count_diff <= threshold:
        _log.info("Qwen->Whisper 使用 1:1 映射 (qwen=%d whisper=%d)", n_qwen, n_whisper)
        n_match = min(n_qwen, n_whisper)
        for i in range(n_match):
            segments[i].qwen_text = qwen_sentences[i]
        return n_match
    else:
        _log.info("Qwen->Whisper 使用相似度匹配 (qwen=%d whisper=%d)", n_qwen, n_whisper)
        pairs = _match_qwen_to_whisper(qwen_sentences, segments)
        for qs, idx in pairs:
            segments[idx].qwen_text = qs
        return len(pairs)


# ═══════════════════════════════════════════════════════════════════
# Long-audio chunking helpers
# ═══════════════════════════════════════════════════════════════════

def _get_audio_duration(audio_path: str) -> float:
    """Get audio duration in seconds via ffprobe.  Returns 0.0 on failure."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", audio_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info = json.loads(result.stdout)
        return float(info["format"]["duration"])
    except Exception:
        _log.warning("ffprobe 获取时长失败", exc_info=True)
        return 0.0


def _chunk_segments(segments, max_duration=_CHUNK_MAX_DURATION_S):
    """Group segments into batches of ~5 minutes each.

    Returns list of (start_idx, end_idx, start_time, end_time) tuples.
    end_idx is exclusive.  Batches break at natural segment boundaries
    (never mid-segment), so each chunk starts and ends on a sentence break.

    Args:
        segments: list of objects with .start / .end attributes (seconds).
        max_duration: target max seconds per chunk (default 300 = 5 min).
    """
    if not segments:
        return []

    chunks = []
    batch_start = 0
    batch_start_time = segments[0].start

    for i in range(1, len(segments)):
        batch_duration = segments[i].end - batch_start_time
        if batch_duration >= max_duration:
            chunks.append((
                batch_start, i,
                batch_start_time,
                segments[i - 1].end,
            ))
            batch_start = i
            batch_start_time = segments[i].start

    # ── Final batch ──
    if batch_start < len(segments):
        chunks.append((
            batch_start, len(segments),
            batch_start_time,
            segments[-1].end,
        ))

    return chunks


def _extract_audio_chunk(audio_path: str, start_s: float, end_s: float,
                         output_path: str) -> None:
    """Extract an audio slice via ffmpeg with 5 s padding on each side.

    The padding is a pronunciation buffer — it ensures words at the edges
    are fully captured, without providing semantic context (which is not
    needed for transcription).
    """
    extract_start = max(0.0, start_s - _AUDIO_PAD_S)
    extract_end = end_s + _AUDIO_PAD_S
    duration = extract_end - extract_start

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(extract_start),
        "-t", str(duration),
        "-i", audio_path,
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)


# ═══════════════════════════════════════════════════════════════════
# Chunked transcription (internal helpers)
# ═══════════════════════════════════════════════════════════════════

def _transcribe_raw_chunked(audio_path: str, segments, language=None) -> dict | None:
    """Chunked Qwen3-ASR for long audio — used by transcribe_raw().

    Extracts each chunk, transcribes independently, merges text and
    offsets timestamps.
    """
    chunks = _chunk_segments(segments)
    _log.info("Qwen3-ASR 分段转录: %d chunks (%d segments)",
              len(chunks), len(segments))

    all_texts = []
    all_languages = []
    all_timestamps = []

    t0 = time.time()

    if _USE_MLX:
        from qwen3_asr_mlx import Qwen3ASR
        model = Qwen3ASR.from_pretrained("mlx-community/Qwen3-ASR-1.7B-bf16")
    else:
        from qwen_asr import Qwen3ASRModel
        model = Qwen3ASRModel.from_pretrained("Qwen/Qwen3-ASR-1.7B")

    try:
        for idx, (_seg_start, _seg_end, t_start, t_end) in enumerate(chunks):
            fd, chunk_path = tempfile.mkstemp(
                suffix=".wav", prefix=f"qwen_raw_chunk_{idx}_")
            os.close(fd)
            try:
                _extract_audio_chunk(audio_path, t_start, t_end, chunk_path)
                result = model.transcribe(
                    chunk_path,
                    **_qwen_gen_kwargs(t_end - t_start, language, _USE_MLX))

                if _USE_MLX:
                    text = result.text if result.text else ""
                    lang = getattr(result, 'language', None)
                    ts = None
                else:
                    text = result.text if hasattr(result, 'text') else ""
                    lang = getattr(result, 'language', None)
                    raw_ts = getattr(result, 'time_stamps', None)
                    ts = raw_ts

                if text:
                    all_texts.append(text)
                if lang:
                    all_languages.append(lang)
                if ts is not None:
                    # Offset timestamps by this chunk's start time
                    for t in ts:
                        if hasattr(t, 'items'):
                            for item in t.items:
                                item.start_time = (
                                    getattr(item, 'start_time', 0) + t_start)
                                item.end_time = (
                                    getattr(item, 'end_time', 0) + t_start)
                        elif isinstance(t, list):
                            for item in t:
                                item.start_time = (
                                    getattr(item, 'start_time', 0) + t_start)
                                item.end_time = (
                                    getattr(item, 'end_time', 0) + t_start)
                    all_timestamps.extend(ts)

                _log.info("Qwen3-ASR chunk %d/%d 完成 (%.1fs–%.1fs)",
                          idx + 1, len(chunks), t_start, t_end)
            finally:
                try:
                    os.unlink(chunk_path)
                except OSError:
                    pass
    finally:
        del model
        gc.collect()

    merged_text = " ".join(t for t in all_texts if t)
    merged_lang = all_languages[0] if all_languages else None
    merged_ts = all_timestamps if all_timestamps else None

    _log.info("Qwen3-ASR 分段转录完成 %.1fs, lang=%s, chunks=%d",
              time.time() - t0, merged_lang or "unknown", len(chunks))
    return {"text": merged_text, "language": merged_lang, "timestamps": merged_ts}


def _run_qwen_asr_chunked(audio_path: str, segments: list, language=None) -> list:
    """Chunked cross-validation for long audio.

    Batches Whisper segments into ~5-minute chunks at natural boundaries,
    transcribes each chunk independently, and aligns results to the
    corresponding segment subset.
    """
    chunks = _chunk_segments(segments)
    _log.info("Qwen3-ASR 分段交叉验证: %d chunks (%d segments)",
              len(chunks), len(segments))

    t0 = time.time()
    try:
        if _USE_MLX:
            from qwen3_asr_mlx import Qwen3ASR
            model = Qwen3ASR.from_pretrained("mlx-community/Qwen3-ASR-1.7B-bf16")
        else:
            from qwen_asr import Qwen3ASRModel
            model = Qwen3ASRModel.from_pretrained("Qwen/Qwen3-ASR-1.7B")
    except Exception:
        _log.warning("Qwen3-ASR 模型加载失败，跳过交叉验证")
        return segments
    _log.info("Qwen3-ASR 模型加载耗时 %.1fs", time.time() - t0)

    for idx, (seg_start, seg_end, t_start, t_end) in enumerate(chunks):
        chunk_segments = segments[seg_start:seg_end]

        fd, chunk_path = tempfile.mkstemp(
            suffix=".wav", prefix=f"qwen_chunk_{idx}_")
        os.close(fd)
        try:
            _extract_audio_chunk(audio_path, t_start, t_end, chunk_path)

            result = model.transcribe(
                chunk_path,
                **_qwen_gen_kwargs(t_end - t_start, language, _USE_MLX))
            if _USE_MLX:
                full_text = result.text if result.text else ""
            else:
                full_text = result.text if hasattr(result, 'text') else ""

            if not full_text or not full_text.strip():
                _log.info("Qwen3-ASR chunk %d/%d 无文字, 跳过",
                          idx + 1, len(chunks))
                continue

            # ── Alignment for this chunk ──
            has_timestamps = False
            if not _USE_MLX:
                try:
                    has_timestamps = (
                        hasattr(result, 'time_stamps')
                        and bool(result.time_stamps))
                except Exception:
                    pass

            if has_timestamps:
                ts_all = []
                for ts_obj in result.time_stamps:
                    if hasattr(ts_obj, 'items'):
                        items = ts_obj.items
                    elif isinstance(ts_obj, list):
                        items = ts_obj
                    else:
                        items = []
                    for it in items:
                        it.start_time = (
                            getattr(it, 'start_time', 0) + t_start)
                        it.end_time = (
                            getattr(it, 'end_time', 0) + t_start)
                    ts_all.extend(items)
                align_by_timestamps(ts_all, chunk_segments)
            else:
                align_by_similarity(full_text, chunk_segments)

            _log.info("Qwen3-ASR chunk %d/%d 完成 (segs %d–%d, %.1fs–%.1fs)",
                      idx + 1, len(chunks), seg_start, seg_end,
                      t_start, t_end)
        except Exception:
            _log.warning("Qwen3-ASR chunk %d/%d 失败",
                         idx + 1, len(chunks), exc_info=True)
        finally:
            try:
                os.unlink(chunk_path)
            except OSError:
                pass

    del model
    gc.collect()

    total = sum(1 for s in segments if s.qwen_text is not None)
    _log.info("Qwen3-ASR 分段交叉验证完成: %d/%d segments 已映射",
              total, len(segments))
    return segments


# ═══════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════

def transcribe_raw(audio_path: str, segments_hint=None, language=None) -> dict | None:
    """Transcribe audio without Whisper-segment dependency.

    Args:
        audio_path: path to audio file.
        segments_hint: optional list of Whisper segments; if provided and
                       audio duration > 20 min, chunk-based transcription is
                       used to stay within the model's single-call limit.

    Returns dict with keys text / language / timestamps, or None on failure.
    """
    if not is_qwen_asr_available():
        return None

    # ── Chunked path: audio too long for single call ──
    if segments_hint:
        need_chunk = False
        try:
            dur = segments_hint[-1].end
            if dur > _LONG_AUDIO_THRESHOLD_S:
                need_chunk = True
        except Exception:
            try:
                dur = _get_audio_duration(audio_path)
                if dur > _LONG_AUDIO_THRESHOLD_S:
                    need_chunk = True
            except Exception:
                pass
        if need_chunk:
            _log.info("Qwen3-ASR 音频 %.0fs > 20min，启用分段转录", dur)
            return _transcribe_raw_chunked(audio_path, segments_hint,
                                           language=language)

    # ── Direct path (original behaviour) ──
    t0 = time.time()
    _dur = _get_audio_duration(audio_path)
    model = None
    try:
        if _USE_MLX:
            from qwen3_asr_mlx import Qwen3ASR
            model = Qwen3ASR.from_pretrained("mlx-community/Qwen3-ASR-1.7B-bf16")
            result = model.transcribe(
                audio_path, **_qwen_gen_kwargs(_dur, language, True))
            lang = getattr(result, 'language', None)
            text = result.text if result.text else ""
            ts = None
        else:
            from qwen_asr import Qwen3ASRModel
            model = Qwen3ASRModel.from_pretrained("Qwen/Qwen3-ASR-1.7B")
            result = model.transcribe(
                audio_path, return_time_stamps=True,
                **_qwen_gen_kwargs(_dur, language, False))
            lang = getattr(result, 'language', None)
            text = result.text if hasattr(result, 'text') else ""
            ts = getattr(result, 'time_stamps', None)
    except Exception:
        _log.warning("Qwen3-ASR 转录失败", exc_info=True)
        return None
    finally:
        if model is not None:
            del model
        gc.collect()
    _log.info("Qwen3-ASR 转录耗时 %.1fs, language=%s", time.time() - t0, lang or "unknown")
    return {"text": text, "language": lang, "timestamps": ts}


def run_qwen_asr(audio_path: str, segments: list, language=None) -> list:
    for seg in segments:
        seg.qwen_text = None

    if not is_qwen_asr_available():
        _log.warning("Qwen3-ASR 未安装，跳过交叉验证")
        return segments

    # ── Chunked path: total audio > 20 min ──
    total_duration = segments[-1].end
    if total_duration > _LONG_AUDIO_THRESHOLD_S:
        _log.info("音频 %.0fs > 20min，启用 Qwen 分段对齐", total_duration)
        return _run_qwen_asr_chunked(audio_path, segments, language=language)

    # ── Direct path (original behaviour) ──
    t0 = time.time()
    try:
        if _USE_MLX:
            from qwen3_asr_mlx import Qwen3ASR
            model = Qwen3ASR.from_pretrained("mlx-community/Qwen3-ASR-1.7B-bf16")
        else:
            from qwen_asr import Qwen3ASRModel
            model = Qwen3ASRModel.from_pretrained("Qwen/Qwen3-ASR-1.7B")
    except Exception:
        _log.warning("Qwen3-ASR 模型加载失败，跳过交叉验证")
        return segments
    _log.info("Qwen3-ASR 模型加载耗时 %.1fs", time.time() - t0)

    t1 = time.time()
    try:
        result = model.transcribe(
            audio_path,
            **_qwen_gen_kwargs(total_duration, language, _USE_MLX))
        if _USE_MLX:
            detected_lang = getattr(result, 'language', None)
            full_text = result.text if result.text else ""
        else:
            detected_lang = getattr(result, 'language', None)
            full_text = result.text if hasattr(result, 'text') else ""
    except Exception:
        _log.warning("Qwen3-ASR 转录失败，跳过交叉验证")
        del model
        gc.collect()
        return segments
    _log.info("Qwen3-ASR 转录耗时 %.1fs, language=%s", time.time() - t1, detected_lang or "unknown")

    del model
    gc.collect()

    if not full_text or not full_text.strip():
        return segments

    # ── Choose alignment path based on whether model returned timestamps ──
    has_timestamps = False
    if not _USE_MLX:
        try:
            has_timestamps = hasattr(result, 'time_stamps') and bool(result.time_stamps)
        except Exception:
            pass

    if has_timestamps:
        _log.info("Qwen->Whisper 使用时间戳对齐（PyTorch 路径）")
        ts_all = []
        for ts in result.time_stamps:
            if hasattr(ts, 'items'):
                ts_all.extend(ts.items)
            elif isinstance(ts, list):
                ts_all.extend(ts)
        matched = align_by_timestamps(ts_all, segments)
    else:
        _log.info("Qwen->Whisper 使用文字相似度对齐（MLX 路径）")
        matched = align_by_similarity(full_text, segments)

    total = sum(1 for s in segments if s.qwen_text is not None)
    _log.info("Qwen3-ASR 交叉验证完成: %d/%d segments 已映射", total, len(segments))
    return segments
