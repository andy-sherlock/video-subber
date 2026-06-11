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


def _load_qwen_model():
    """Load Qwen3-ASR model (MLX or PyTorch depending on platform)."""
    if _USE_MLX:
        from qwen3_asr_mlx import Qwen3ASR
        return Qwen3ASR.from_pretrained("mlx-community/Qwen3-ASR-1.7B-bf16")
    else:
        from qwen_asr import Qwen3ASRModel
        return Qwen3ASRModel.from_pretrained("Qwen/Qwen3-ASR-1.7B")


# ── P207 qwen-asr v0.0.6: transcribe() takes (audio, context, language, return_time_stamps).
# max_new_tokens=512 is built into the model constructor — no external cap needed.
# P147→P156 anti-collapse params (repetition_penalty, max_tokens, etc.) removed;
# v0.0.6 handles these internally.

# P222: v0.0.6 requires full language names ("English"), not short codes ("en").
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



# P251: align_by_similarity / align_by_timestamps / _match_qwen_to_whisper /
# _split_sentences removed — Qwen-ASR no longer maps onto Whisper segments.
# L2 (_assign_qwen_to_windows in hallucination_filter) assigns Qwen text to
# 30s windows by stem similarity instead.


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


def _transcribe_chunk(model, audio_path, t_start, t_end, language,
                      chunk_idx=0, total_chunks=1):
    """P251: transcribe one audio chunk → text.

    No longer aligns to Whisper segments. The caller collects the returned
    text as {text, t_start, t_end} and L2 assigns it to 30s windows. Identical
    on MLX and PyTorch — both backends expose full text; word-level timestamps
    (PyTorch only) are not needed at 30s window granularity.
    """
    fd, chunk_path = tempfile.mkstemp(
        suffix=".wav", prefix=f"qwen_chunk_{chunk_idx}_")
    os.close(fd)
    try:
        _extract_audio_chunk(audio_path, t_start, t_end, chunk_path)
        result = model.transcribe(
            chunk_path,
            language=_resolve_language(language),
            )
        if _USE_MLX:
            full_text = result.text if result.text else ""
        else:
            full_text = result[0].text if result else ""

        if not full_text or not full_text.strip():
            _log.info("Qwen3-ASR chunk %d/%d 无文字, 跳过",
                      chunk_idx + 1, total_chunks)
            return ""

        _log.info("Qwen3-ASR chunk %d/%d 完成 (%.1fs–%.1fs)",
                  chunk_idx + 1, total_chunks, t_start, t_end)
        return full_text
    except Exception:
        _log.warning("Qwen3-ASR chunk %d/%d 失败",
                     chunk_idx + 1, total_chunks, exc_info=True)
        return ""
    finally:
        try:
            os.unlink(chunk_path)
        except OSError:
            pass


def _qwen_consumer(q, audio_path, language, qwen_chunks):
    """Consumer thread: transcribe queued chunks, append {text, t_start, t_end}
    to the shared qwen_chunks list (P251 — no longer writes seg.qwen_text)."""
    try:
        model = _load_qwen_model()
    except Exception:
        _log.warning("千ASR 消费者线程模型加载失败")
        while True:
            chunk = q.get()
            if chunk is None:
                break
        return

    try:
        idx = 0
        while True:
            chunk = q.get()
            if chunk is None:
                break
            _chunk_segments, t_start, t_end = chunk
            text = _transcribe_chunk(model, audio_path, t_start, t_end,
                                     language, idx)
            if text and text.strip():
                qwen_chunks.append({"text": text, "t_start": t_start,
                                    "t_end": t_end})
            idx += 1
    except Exception:
        _log.warning("千ASR 消费者线程异常", exc_info=True)
    finally:
        del model
        gc.collect()


def _run_qwen_asr_chunked(audio_path: str, segments: list, language=None) -> list:
    """Chunked cross-validation for long audio → list of {text, t_start, t_end}.

    Batches Whisper segments into ~5-minute chunks at natural boundaries and
    transcribes each chunk independently. L2 assigns the text to 30s windows.
    """
    chunks = _chunk_segments(segments)
    _log.info("Qwen3-ASR 分段交叉验证: %d chunks (%d segments)",
              len(chunks), len(segments))

    t0 = time.time()
    try:
        model = _load_qwen_model()
    except Exception:
        _log.warning("Qwen3-ASR 模型加载失败，跳过交叉验证")
        return []
    _log.info("Qwen3-ASR 模型加载耗时 %.1fs", time.time() - t0)

    qwen_chunks = []
    for idx, (seg_start, seg_end, t_start, t_end) in enumerate(chunks):
        text = _transcribe_chunk(model, audio_path, t_start, t_end,
                                 language, idx, len(chunks))
        if text and text.strip():
            qwen_chunks.append({"text": text, "t_start": t_start,
                                "t_end": t_end})

    del model
    gc.collect()

    _log.info("Qwen3-ASR 分段交叉验证完成: %d chunks 有文字", len(qwen_chunks))
    return qwen_chunks


# ── Public entry point ──

def run_qwen_asr(audio_path: str, segments: list, language=None) -> list:
    """P251: return Qwen-ASR output as a list of {text, t_start, t_end} dicts.

    No longer writes seg.qwen_text. L2 assigns this text to 30s windows by
    stem similarity. Segments are used only to size the timeline / choose the
    chunked path. Returns [] when Qwen is unavailable or produced nothing.
    """
    if not is_qwen_asr_available():
        _log.warning("Qwen3-ASR 未安装，跳过交叉验证")
        return []

    # ── Chunked path: total audio > 20 min ──
    if not segments:
        return []
    total_duration = segments[-1].end
    if total_duration > _LONG_AUDIO_THRESHOLD_S:
        _log.info("音频 %.0fs > 20min，启用 Qwen 分段对齐", total_duration)
        return _run_qwen_asr_chunked(audio_path, segments, language=language)

    # ── Direct path: short audio, one chunk spanning the whole timeline ──
    t0 = time.time()
    try:
        model = _load_qwen_model()
    except Exception:
        _log.warning("Qwen3-ASR 模型加载失败，跳过交叉验证")
        return []
    _log.info("Qwen3-ASR 模型加载耗时 %.1fs", time.time() - t0)

    t1 = time.time()
    try:
        result = model.transcribe(
            audio_path,
            language=_resolve_language(language),
            )
        if _USE_MLX:
            detected_lang = getattr(result, 'language', None)
            full_text = result.text if result.text else ""
        else:
            detected_lang = result[0].language if result and hasattr(result[0], 'language') else None
            full_text = result[0].text if result else ""
    except Exception:
        _log.warning("Qwen3-ASR 转录失败，跳过交叉验证")
        del model
        gc.collect()
        return []
    _log.info("Qwen3-ASR 转录耗时 %.1fs, language=%s", time.time() - t1, detected_lang or "unknown")

    del model
    gc.collect()

    if not full_text or not full_text.strip():
        return []

    _log.info("Qwen3-ASR 交叉验证完成: %d 字", len(full_text))
    return [{"text": full_text, "t_start": 0.0, "t_end": total_duration}]
