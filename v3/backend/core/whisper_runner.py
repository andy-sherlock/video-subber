"""Whisper backend adapter.

Mac Apple Silicon uses mlx-whisper. Other platforms keep faster-whisper so the
Windows CUDA path stays unchanged.
"""
from __future__ import annotations

import logging
import os
import platform
import subprocess
import tempfile
from dataclasses import dataclass

_log = logging.getLogger("whisper_runner")

MLX_WHISPER_MODELS = {
    "tiny": "mlx-community/whisper-tiny",
    "base": "mlx-community/whisper-base",
    "small": "mlx-community/whisper-small",
    "medium": "mlx-community/whisper-medium",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
}


@dataclass
class WhisperInfo:
    language: str | None = None
    language_probability: float = 0.0
    duration: float = 0.0
    backend: str = "unknown"


@dataclass
class WhisperSegment:
    start: float
    end: float
    text: str
    words: list


def choose_whisper_backend() -> str:
    """Return the Whisper implementation for the current platform."""
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "mlx"
    return "faster"


def should_parallel_asr() -> bool:
    """Unified memory (Apple Silicon) cannot truly parallelise two large models."""
    return choose_whisper_backend() != "mlx"


def _normalize_words(words) -> list:
    normalized = []
    for word in words or []:
        if isinstance(word, dict):
            normalized.append({
                "word": word.get("word") or word.get("text") or "",
                "start": word.get("start", 0.0),
                "end": word.get("end", 0.0),
                "probability": word.get("probability", 0.0),
            })
        else:
            normalized.append({
                "word": getattr(word, "word", ""),
                "start": getattr(word, "start", 0.0),
                "end": getattr(word, "end", 0.0),
                "probability": getattr(word, "probability", 0.0),
            })
    return normalized


def _normalize_segment(seg) -> WhisperSegment:
    if isinstance(seg, dict):
        return WhisperSegment(
            start=float(seg.get("start", 0.0)),
            end=float(seg.get("end", 0.0)),
            text=(seg.get("text") or "").strip(),
            words=_normalize_words(seg.get("words") or []),
        )
    return WhisperSegment(
        start=float(getattr(seg, "start", 0.0)),
        end=float(getattr(seg, "end", 0.0)),
        text=(getattr(seg, "text", "") or "").strip(),
        words=_normalize_words(getattr(seg, "words", []) or []),
    )


def _dict_get(result, key, default=None):
    if isinstance(result, dict):
        return result.get(key, default)
    return getattr(result, key, default)


def transcribe_with_mlx_whisper(
    audio_path: str,
    model_size: str,
    language: str | None,
    vad_threshold: float = 0.0,
) -> tuple[list[WhisperSegment], WhisperInfo]:
    """Run mlx-whisper and return the same shape the app expects."""
    try:
        import mlx_whisper
    except ImportError as exc:
        raise RuntimeError(
            "Mac 端需要安装 mlx-whisper 才能转录。请先安装依赖并下载模型。"
        ) from exc

    if vad_threshold > 0:
        _log.warning("mlx-whisper 不支持当前 faster-whisper VAD 参数，已忽略 vad_threshold=%.2f", vad_threshold)

    repo = MLX_WHISPER_MODELS.get(model_size, model_size)
    result = mlx_whisper.transcribe(
        audio_path,
        path_or_hf_repo=repo,
        language=language,
        word_timestamps=True,
    )
    raw_segments = _dict_get(result, "segments", []) or []
    segments = [_normalize_segment(seg) for seg in raw_segments]
    detected_lang = _dict_get(result, "language")
    if language:
        lang_prob = 1.0
    else:
        try:
            import mlx.core as mx
            from mlx_whisper.audio import (
                load_audio, log_mel_spectrogram, pad_or_trim,
                N_FRAMES, N_SAMPLES,
            )
            from mlx_whisper.load_models import load_model
            dtype = mx.float16
            model = load_model(repo, dtype=dtype)
            audio_data = load_audio(audio_path)
            mel = log_mel_spectrogram(audio_data, n_mels=model.dims.n_mels, padding=N_SAMPLES)
            mel_seg = pad_or_trim(mel, N_FRAMES, axis=-2).astype(dtype)
            _, probs = model.detect_language(mel_seg)
            if not probs or detected_lang not in probs:
                raise RuntimeError(
                    f"mlx-whisper 完整转录后无法获取语言概率 (language={detected_lang})"
                )
            lang_prob = float(probs[detected_lang])
        except ImportError as exc:
            raise RuntimeError(
                "mlx-whisper 完整转录后无法获取语言概率：底层模块导入失败"
            ) from exc
    return segments, WhisperInfo(
        language=detected_lang,
        language_probability=lang_prob,
        duration=float(_dict_get(result, "duration", 0.0) or 0.0),
        backend="mlx",
    )


def transcribe_with_faster_whisper(
    audio_path: str,
    model_size: str,
    language: str | None,
    vad_threshold: float = 0.0,
) -> tuple[list[WhisperSegment], WhisperInfo]:
    """Run faster-whisper. This is the unchanged Windows/Linux path."""
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="auto", compute_type="auto")
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
        audio_path,
        beam_size=5,
        language=language,
        condition_on_previous_text=True,
        log_prob_threshold=-1.0,
        word_timestamps=True,
        **vad_kwargs,
    )
    segments = [_normalize_segment(seg) for seg in segments_iter]
    return segments, WhisperInfo(
        language=getattr(info, "language", None),
        language_probability=float(getattr(info, "language_probability", 0.0) or 0.0),
        duration=float(getattr(info, "duration", 0.0) or 0.0),
        backend="faster",
    )


def _create_language_probe_clip(audio_path: str, probe_seconds: int = 30) -> str:
    """Extract a short mono WAV clip for language detection."""
    fd, probe_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            audio_path,
            "-t",
            str(probe_seconds),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            probe_path,
        ],
        check=True,
    )
    return probe_path


def detect_language_with_mlx_whisper(
    audio_path: str,
    model_size: str,
    language: str | None,
    probe_seconds: int = 30,
) -> WhisperInfo:
    """Detect language with mlx-whisper using only a short audio clip.

    Calls model.detect_language() directly instead of transcribe(),
    because mlx_whisper.transcribe() does not return language_probability.
    """
    try:
        import mlx.core as mx
        from mlx_whisper.audio import (
            load_audio, log_mel_spectrogram, pad_or_trim,
            N_FRAMES, N_SAMPLES,
        )
        from mlx_whisper.load_models import load_model
    except ImportError as exc:
        raise RuntimeError(
            "Mac 端需要安装 mlx-whisper 才能检测语言。请先安装依赖并下载模型。"
        ) from exc

    repo = MLX_WHISPER_MODELS.get(model_size, model_size)
    probe_path = _create_language_probe_clip(audio_path, probe_seconds)
    try:
        dtype = mx.float16
        model = load_model(repo, dtype=dtype)
        audio = load_audio(probe_path)
        mel = log_mel_spectrogram(audio, n_mels=model.dims.n_mels, padding=N_SAMPLES)
        mel_segment = pad_or_trim(mel, N_FRAMES, axis=-2).astype(dtype)

        if language:
            return WhisperInfo(
                language=language,
                language_probability=1.0,
                backend="mlx",
            )

        _, probs = model.detect_language(mel_segment)
        if not probs:
            raise RuntimeError(
                "mlx-whisper model.detect_language() 返回空概率表，无法判断语言"
            )
        best_lang = max(probs, key=probs.get)
        best_prob = float(probs[best_lang])
        _log.info("MLX 语言探测: %s (概率 %.4f)", best_lang, best_prob)
        return WhisperInfo(
            language=best_lang,
            language_probability=best_prob,
            backend="mlx",
        )
    finally:
        try:
            os.unlink(probe_path)
        except FileNotFoundError:
            pass


def detect_language_with_faster_whisper(
    audio_path: str,
    model_size: str,
    language: str | None,
) -> WhisperInfo:
    """Detect language with faster-whisper without consuming all segments."""
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="auto", compute_type="auto")
    try:
        segments_iter, info = model.transcribe(
            audio_path,
            beam_size=5,
            language=language,
            condition_on_previous_text=True,
            log_prob_threshold=-1.0,
            word_timestamps=False,
        )
        del segments_iter
        return WhisperInfo(
            language=getattr(info, "language", None),
            language_probability=float(getattr(info, "language_probability", 0.0) or 0.0),
            duration=float(getattr(info, "duration", 0.0) or 0.0),
            backend="faster",
        )
    finally:
        del model


def detect_language_with_whisper(
    audio_path: str,
    model_size: str,
    language: str | None,
    probe_seconds: int = 30,
) -> WhisperInfo:
    backend = choose_whisper_backend()
    if backend == "mlx":
        return detect_language_with_mlx_whisper(
            audio_path, model_size, language, probe_seconds)
    return detect_language_with_faster_whisper(audio_path, model_size, language)


def release_model():
    """Release mlx-whisper's cached model from memory.

    mlx-whisper keeps the model in a class variable (ModelHolder.model) after
    transcribe(). gc.collect() alone cannot free it. This function clears that
    reference so the ~3 GB model memory is actually reclaimed.
    """
    backend = choose_whisper_backend()
    if backend == "mlx":
        try:
            from mlx_whisper.transcribe import ModelHolder
            ModelHolder.model = None
            ModelHolder.model_path = None
        except (ImportError, AttributeError):
            pass
    import gc
    gc.collect()
    _log.info("Whisper 模型内存已释放")


def transcribe_whisper(
    audio_path: str,
    model_size: str,
    language: str | None,
    vad_threshold: float = 0.0,
) -> tuple[list[WhisperSegment], WhisperInfo]:
    backend = choose_whisper_backend()
    if backend == "mlx":
        return transcribe_with_mlx_whisper(audio_path, model_size, language, vad_threshold)
    return transcribe_with_faster_whisper(audio_path, model_size, language, vad_threshold)
