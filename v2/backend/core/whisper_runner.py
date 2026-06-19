"""Whisper backend adapter.

Mac Apple Silicon uses mlx-whisper. Other platforms keep faster-whisper so the
Windows CUDA path stays unchanged.
"""
from __future__ import annotations

import logging
import platform
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
    return segments, WhisperInfo(
        language=_dict_get(result, "language"),
        language_probability=float(_dict_get(result, "language_probability", 0.0) or 0.0),
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
