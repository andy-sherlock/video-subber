"""Qwen3-ASR cross-validation transcription via MLX (Apple Silicon) or PyTorch (Windows).

MLX backend (mlx-qwen3-asr): returns word-level timestamps via internal FA.
PyTorch backend (qwen-asr): returns text only, L2 assigns to windows by stem similarity.

For audio longer than 20 minutes, chunks are formed at Whisper segment boundaries
(batched into ~5-minute groups) and each chunk is transcribed independently.
"""
import gc
import json
import logging
import os
import subprocess
import tempfile
import time

_log = logging.getLogger("qwen_asr_runner")

# ── Chunking constants ──
_CHUNK_MAX_DURATION_S = 300       # 5 minutes per chunk
_LONG_AUDIO_THRESHOLD_S = 1200    # 20 minutes — switch to chunked mode
_AUDIO_PAD_S = 5.0                # pronunciation buffer on each side

_USE_MLX = False
_USE_PYTORCH = False
try:
    from mlx_qwen3_asr import load_model as _mlx_load_model, transcribe as _mlx_transcribe  # noqa: F401
    _USE_MLX = True
except ImportError:
    pass
try:
    from qwen_asr import Qwen3ASRModel  # noqa: F401
    _USE_PYTORCH = True
except ImportError:
    pass


def _audio_extract_bounds(start_s: float, end_s: float) -> tuple[float, float]:
    """Return the padded ffmpeg extraction range for one ASR chunk."""
    extract_start = max(0.0, start_s - _AUDIO_PAD_S)
    extract_end = end_s + _AUDIO_PAD_S
    return extract_start, extract_end


def is_qwen_asr_available() -> bool:
    return _USE_MLX or _USE_PYTORCH


def _load_qwen_model():
    """Load Qwen3-ASR model (MLX or PyTorch depending on platform)."""
    if _USE_MLX:
        model, _config = _mlx_load_model("Qwen/Qwen3-ASR-1.7B")
        return model
    else:
        from qwen_asr import Qwen3ASRModel
        return Qwen3ASRModel.from_pretrained("Qwen/Qwen3-ASR-1.7B")


# Both backends (MLX mlx-qwen3-asr, PyTorch qwen-asr) require full language
# names ("English"), not short codes ("en").
_LANG_NAME = {
    "zh": "Chinese", "en": "English", "ja": "Japanese",
    "ko": "Korean", "de": "German", "fr": "French",
    "es": "Spanish", "it": "Italian", "pt": "Portuguese",
    "ru": "Russian",
}


def _resolve_language(lang: str | None) -> str | None:
    """Map short language code to full name for v0.0.6 transcribe()."""
    if not lang or lang == "auto":
        return None
    return _LANG_NAME.get(lang, lang)





# ═══════════════════════════════════════════════════════════════════
# Long-audio chunking helpers
# ═══════════════════════════════════════════════════════════════════

def _get_audio_duration(audio_path: str) -> float:
    """Get audio duration in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        audio_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        _log.warning("ffprobe 无法获取音频时长: %s", audio_path)
        return 0.0


def _chunk_by_time(total_duration, max_duration=_CHUNK_MAX_DURATION_S):
    """Split audio into fixed-interval time chunks (no Whisper segments needed)."""
    chunks = []
    t = 0.0
    while t < total_duration:
        end = min(t + max_duration, total_duration)
        chunks.append((t, end))
        t = end
    return chunks


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
    extract_start, extract_end = _audio_extract_bounds(start_s, end_s)
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


def _clip_chunk_segments(raw_segments: list, extract_start: float,
                         t_start: float, t_end: float) -> list:
    """Map chunk-relative segment times to full audio and drop padding-only text."""
    segments = []
    for s in raw_segments:
        abs_start = float(s["start"]) + extract_start
        abs_end = float(s["end"]) + extract_start
        if abs_end <= t_start or abs_start >= t_end:
            continue
        start = max(abs_start, t_start)
        end = min(abs_end, t_end)
        if end <= start:
            continue
        segments.append({"text": s["text"], "start": start, "end": end})
    return segments


def _join_segment_texts(segments: list, language: str) -> str:
    lang = (language or "").lower()
    joiner = "" if lang in ("ja", "zh", "ko", "japanese", "chinese", "korean") else " "
    return joiner.join((s.get("text") or "").strip()
                       for s in segments if (s.get("text") or "").strip())


def _transcribe_chunk(model, audio_path, t_start, t_end, language,
                      chunk_idx=0, total_chunks=1):
    """Transcribe one audio chunk.

    MLX: returns {text, segments, t_start, t_end} with word-level timestamps.
    PyTorch: returns {text, t_start, t_end} (no timestamps).
    Segments' start/end are offset by the padded extract_start to map to the
    full audio timeline, then clipped back to the requested chunk bounds.
    """
    fd, chunk_path = tempfile.mkstemp(
        suffix=".wav", prefix=f"qwen_chunk_{chunk_idx}_")
    os.close(fd)
    try:
        extract_start, _extract_end = _audio_extract_bounds(t_start, t_end)
        _extract_audio_chunk(audio_path, t_start, t_end, chunk_path)
        if _USE_MLX:
            result = _mlx_transcribe(
                chunk_path,
                model=model,
                language=_resolve_language(language),
                return_timestamps=True,
            )
            full_text = result.text or ""
            raw_segments = result.segments or []
            segments = _clip_chunk_segments(
                raw_segments, extract_start, t_start, t_end)
            full_text = _join_segment_texts(segments, language)
        else:
            result = model.transcribe(
                chunk_path,
                language=_resolve_language(language),
            )
            full_text = result[0].text if result else ""
            segments = []

        if not full_text or not full_text.strip():
            _log.info("Qwen3-ASR chunk %d/%d 无文字, 跳过",
                      chunk_idx + 1, total_chunks)
            return {"text": "", "segments": [], "t_start": t_start, "t_end": t_end}

        _log.info("Qwen3-ASR chunk %d/%d 完成 (%.1fs–%.1fs, %d segments)",
                  chunk_idx + 1, total_chunks, t_start, t_end, len(segments))
        # 进度百分比
        if total_chunks > 0:
            pct = min(99, round((chunk_idx + 1) / total_chunks * 100))
            _log.info("Qwen3-ASR 进度: %d%% (%d/%d chunks)", pct, chunk_idx + 1, total_chunks)
        return {"text": full_text, "segments": segments,
                "t_start": t_start, "t_end": t_end}
    except Exception:
        _log.warning("Qwen3-ASR chunk %d/%d 失败",
                     chunk_idx + 1, total_chunks, exc_info=True)
        return {"text": "", "segments": [], "t_start": t_start, "t_end": t_end}
    finally:
        try:
            os.unlink(chunk_path)
        except OSError:
            pass


def _qwen_chunk_ckpt_path(cache_dir: str | None) -> str | None:
    if not cache_dir:
        return None
    return os.path.join(cache_dir, "qwen_chunks.json")


def _load_qwen_chunk_ckpt(ckpt_path: str | None, time_chunks: list) -> tuple[list, int]:
    """Load saved Qwen chunk results. Returns (saved_chunks, resume_index).

    Validates that saved chunks match the current time_chunks plan — if the
    chunking changed (different audio, different segment boundaries), the
    checkpoint is discarded.
    """
    if not ckpt_path or not os.path.exists(ckpt_path):
        return [], 0
    try:
        with open(ckpt_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        saved_plan = data.get("time_chunks", [])
        saved_chunks = data.get("results", [])
        done_idx = data.get("done_idx", 0)
        if len(saved_plan) != len(time_chunks):
            _log.info("Qwen 分段计划变更 (%d→%d chunks)，丢弃旧 checkpoint",
                      len(saved_plan), len(time_chunks))
            return [], 0
        for i, (saved, current) in enumerate(zip(saved_plan, time_chunks)):
            if abs(saved[0] - current[0]) > 0.1 or abs(saved[1] - current[1]) > 0.1:
                _log.info("Qwen chunk %d 时间不匹配，丢弃旧 checkpoint", i)
                return [], 0
        _log.info("Qwen 断点续跑: 恢复 %d/%d chunks", done_idx, len(time_chunks))
        return saved_chunks, done_idx
    except Exception:
        _log.warning("Qwen chunk checkpoint 损坏，从头开始", exc_info=True)
        return [], 0


def _save_qwen_chunk_ckpt(ckpt_path: str | None, time_chunks: list,
                           results: list, done_idx: int) -> None:
    if not ckpt_path:
        return
    data = {
        "time_chunks": list(time_chunks),
        "results": results,
        "done_idx": done_idx,
    }
    try:
        with open(ckpt_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        _log.warning("Qwen chunk checkpoint 保存失败", exc_info=True)


def _run_qwen_asr_chunked(audio_path: str, total_duration: float,
                           segments: list = None, language=None,
                           cache_dir: str = None) -> list:
    """Chunked cross-validation for long audio.

    Returns list of {text, segments, t_start, t_end}. MLX results include
    word-level timestamps in segments; PyTorch results have segments=[].

    When segments is provided, chunks at Whisper segment boundaries.
    When segments is None, chunks at fixed 5-minute intervals.

    cache_dir: if provided, saves per-chunk progress to qwen_chunks.json
    so a crashed run can resume from the last completed chunk.
    """
    if segments:
        raw_chunks = _chunk_segments(segments)
        time_chunks = [(t_start, t_end) for (_s, _e, t_start, t_end) in raw_chunks]
        _log.info("Qwen3-ASR 分段交叉验证: %d chunks (%d segments)",
                  len(time_chunks), len(segments))
    else:
        time_chunks = _chunk_by_time(total_duration)
        _log.info("Qwen3-ASR 独立分段: %d chunks (%.0fs 音频)",
                  len(time_chunks), total_duration)

    ckpt_path = _qwen_chunk_ckpt_path(cache_dir)
    qwen_chunks, resume_idx = _load_qwen_chunk_ckpt(ckpt_path, time_chunks)

    if resume_idx >= len(time_chunks):
        _log.info("Qwen3-ASR 全部 chunks 已有缓存，跳过转录")
        return [c for c in qwen_chunks if c.get("text", "").strip()]

    t0 = time.time()
    try:
        model = _load_qwen_model()
    except Exception:
        _log.warning("Qwen3-ASR 模型加载失败，跳过交叉验证", exc_info=True)
        return [c for c in qwen_chunks if c.get("text", "").strip()]
    _log.info("Qwen3-ASR 模型加载耗时 %.1fs", time.time() - t0)

    for idx in range(resume_idx, len(time_chunks)):
        t_start, t_end = time_chunks[idx]
        result = _transcribe_chunk(model, audio_path, t_start, t_end,
                                   language, idx, len(time_chunks))
        qwen_chunks.append(result)
        _save_qwen_chunk_ckpt(ckpt_path, time_chunks, qwen_chunks, idx + 1)

    del model
    gc.collect()

    _log.info("Qwen3-ASR 分段交叉验证完成: %d chunks 有文字",
              sum(1 for c in qwen_chunks if c.get("text", "").strip()))
    return [c for c in qwen_chunks if c.get("text", "").strip()]


# ── Public entry point ──

def run_qwen_asr(audio_path: str, segments: list = None, language=None,
                  cache_dir: str = None) -> list:
    """Return Qwen-ASR output as a list of {text, segments, t_start, t_end}.

    MLX backend: segments contain word-level timestamps [{text, start, end}].
    PyTorch backend: segments is [].
    Returns [] when Qwen is unavailable or produced nothing.

    When segments is None, Qwen runs independently — gets audio duration via
    ffprobe and chunks by fixed time intervals if needed.

    cache_dir: path to the job's versioned cache directory. When provided,
    long-audio chunked mode saves per-chunk progress so a crashed run can
    resume from the last completed chunk.
    """
    if not is_qwen_asr_available():
        _log.warning("Qwen3-ASR 未安装，跳过交叉验证")
        return []

    if segments is not None:
        if not segments:
            return []
        total_duration = segments[-1].end
    else:
        try:
            total_duration = _get_audio_duration(audio_path)
        except Exception:
            _log.warning("无法获取音频时长，跳过 Qwen 交叉验证")
            return []

    if total_duration > _LONG_AUDIO_THRESHOLD_S:
        _log.info("音频 %.0fs > 20min，启用 Qwen 分段对齐", total_duration)
        return _run_qwen_asr_chunked(audio_path, total_duration,
                                     segments=segments, language=language,
                                     cache_dir=cache_dir)

    t0 = time.time()
    try:
        model = _load_qwen_model()
    except Exception:
        _log.warning("Qwen3-ASR 模型加载失败，跳过交叉验证", exc_info=True)
        return []
    _log.info("Qwen3-ASR 模型加载耗时 %.1fs", time.time() - t0)

    t1 = time.time()
    try:
        if _USE_MLX:
            result = _mlx_transcribe(
                audio_path,
                model=model,
                language=_resolve_language(language),
                return_timestamps=True,
            )
            detected_lang = result.language
            full_text = result.text or ""
            raw_segments = result.segments or []
            ts_segments = [
                {"text": s["text"], "start": s["start"], "end": s["end"]}
                for s in raw_segments
            ]
        else:
            result = model.transcribe(
                audio_path,
                language=_resolve_language(language),
            )
            detected_lang = result[0].language if result and hasattr(result[0], 'language') else None
            full_text = result[0].text if result else ""
            ts_segments = []
    except Exception:
        _log.warning("Qwen3-ASR 转录失败，跳过交叉验证")
        del model
        gc.collect()
        return []
    _log.info("Qwen3-ASR 转录耗时 %.1fs, language=%s, %d segments",
              time.time() - t1, detected_lang or "unknown", len(ts_segments))

    del model
    gc.collect()

    if not full_text or not full_text.strip():
        return []

    _log.info("Qwen3-ASR 交叉验证完成: %d 字, %d segments",
              len(full_text), len(ts_segments))
    return [{"text": full_text, "segments": ts_segments,
             "t_start": 0.0, "t_end": total_duration}]
