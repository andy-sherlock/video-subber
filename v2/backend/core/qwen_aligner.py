"""Qwen3-ForcedAligner — word-level timestamp alignment.

Loads Qwen/Qwen3-ForcedAligner-0.6B via the official qwen-asr package.
Input: audio file + text → Output: per-word start/end timestamps.

Model time-sharing: call close() to release model after use.
"""
import logging

_log = logging.getLogger("qwen_aligner")

# Detect qwen-asr package availability at import time
try:
    import qwen_asr  # noqa: F401
    _has_qwen_asr_package = True
except ImportError:
    _has_qwen_asr_package = False


class QwenAligner:
    """Wrapper around Qwen3-ForcedAligner for word-level alignment.

    Uses the official qwen-asr Qwen3ForcedAligner API.

    Usage:
        aligner = QwenAligner()
        words = aligner.align("audio.wav", "你好世界", language="zh")
        aligner.close()  # Release model memory
    """

    def __init__(self, model_id: str = "Qwen/Qwen3-ForcedAligner-0.6B",
                 device: str | None = None):
        """Load the ForcedAligner model via the official API.

        Args:
            model_id: HuggingFace model ID.
            device: "cuda", "mps", "cpu", or None (auto-detect).
        """
        self._model_id = model_id
        self._model = None
        self._closed = False

        try:
            from qwen_asr import Qwen3ForcedAligner

            import torch

            if device is None:
                if torch.cuda.is_available():
                    device = "cuda"
                elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                    device = "mps"
                else:
                    device = "cpu"

            _log.info("加载 Qwen3-ForcedAligner: %s (device=%s)", model_id, device)
            self._model = Qwen3ForcedAligner.from_pretrained(model_id)
            self._model.model.to(device)
            self._device = device
            _log.info("Qwen3-ForcedAligner 加载完成")
        except ImportError as e:
            _log.warning("Qwen3-ForcedAligner 不可用: %s", e)
            self._model = None
        except Exception as e:
            _log.warning("Qwen3-ForcedAligner 加载失败: %s", e)
            self._model = None

    def align(self, audio_path: str, text: str, language: str = "zh") -> list[dict]:
        """Align text to audio, returning per-word timestamps.

        Uses the official Qwen3ForcedAligner.align() API — no hand-written
        forward pass, no silent fallback to fabricated timestamps.

        Args:
            audio_path: Path to WAV audio file.
            text: Text to align (Chinese or other supported language).
            language: Language code (zh/en/ja/ko/de/fr/es/it/pt/ru).

        Returns:
            List of {"word": str, "start": float, "end": float} dicts.

        Raises:
            RuntimeError: If aligner has been closed.
            ValueError: If model failed to load or text is empty.
        """
        if self._closed:
            raise RuntimeError("QwenAligner has been closed")
        if self._model is None:
            raise ValueError("Qwen3-ForcedAligner model not loaded")
        if not text or not text.strip():
            return []

        _log.info("对齐中: audio=%s text_len=%d lang=%s", audio_path, len(text), language)

        lang_name = _map_language(language)

        # Use the official API — no hand-written forward pass, no silent fallback
        align_results = self._model.align(
            audio=audio_path,
            text=text,
            language=lang_name,
        )

        # Convert ForcedAlignResult → our dict format
        result = []
        for align_result in align_results:
            for item in align_result.items:
                result.append({
                    "word": item.text,
                    "start": round(item.start_time, 3),
                    "end": round(item.end_time, 3),
                    "_quality": "official",
                })

        _log.info("对齐完成: %d 词, 首词 %.3fs-%.3fs",
                  len(result),
                  result[0]["start"] if result else 0.0,
                  result[-1]["end"] if result else 0.0)
        return result

    def close(self):
        """Release the model from memory. Idempotent."""
        if not self._closed and self._model is not None:
            import gc
            del self._model
            self._model = None
            gc.collect()
            _log.info("Qwen3-ForcedAligner 模型已释放")
        self._closed = True


_LANG_MAP = {
    "zh": "Chinese", "en": "English", "ja": "Japanese",
    "ko": "Korean", "de": "German", "fr": "French",
    "es": "Spanish", "it": "Italian", "pt": "Portuguese",
    "ru": "Russian",
}


def _map_language(lang: str) -> str:
    """Map short language code to Qwen3-FA language name."""
    return _LANG_MAP.get(lang, lang)


def is_qwen_aligner_available() -> bool:
    """Check if Qwen3-ForcedAligner can be loaded (packages + model cached)."""
    if not _has_qwen_asr_package:
        return False
    try:
        import torch  # noqa: F401
        from transformers import AutoConfig
        AutoConfig.from_pretrained(
            "Qwen/Qwen3-ForcedAligner-0.6B", local_files_only=True
        )
        return True
    except (ImportError, OSError):
        return False


def extract_segment_time(aligned_words: list[dict]) -> tuple[float, float]:
    """Extract segment start/end time from a list of aligned words.

    Takes the first word's start and the last word's end.

    Args:
        aligned_words: List of {"word", "start", "end"} dicts from align().

    Returns:
        (start_time, end_time) tuple. Returns (0.0, 0.0) if list is empty.
    """
    if not aligned_words:
        return 0.0, 0.0
    return aligned_words[0]["start"], aligned_words[-1]["end"]


def align_segments(aligner: "QwenAligner", audio_path: str,
                   segments: list, language: str = "zh") -> list[dict]:
    """Align text to audio in sentence-level chunks.

    Each Whisper segment (~5 seconds) is aligned individually to avoid
    the O(n²) attention-matrix memory explosion in the official API.
    Results from all segments are concatenated.

    Args:
        aligner: A loaded QwenAligner instance.
        audio_path: Path to the full WAV audio file.
        segments: List of Segment objects with .start, .end, .text attributes.
        language: Language code (zh/en/ja/...).

    Returns:
        Flat list of {"word", "start", "end", "_quality"} dicts.
    """
    import tempfile
    import os

    import librosa
    import soundfile as sf

    audio, sr = librosa.load(audio_path, sr=16000, mono=True)

    all_results = []
    for seg in segments:
        txt = (getattr(seg, "text", "") or "").strip()
        if not txt:
            continue

        t_start = max(0.0, getattr(seg, "start", 0.0))
        t_end = max(t_start + 0.1, getattr(seg, "end", t_start + 0.5))

        start_sample = int(t_start * sr)
        end_sample = min(len(audio), int(t_end * sr))
        chunk = audio[start_sample:end_sample]

        if len(chunk) < int(sr * 0.1):  # skip sub-100ms chunks
            continue

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            chunk_path = f.name
        try:
            sf.write(chunk_path, chunk, sr)
            result = aligner.align(chunk_path, txt, language=language)
            # P224: FA returns timestamps relative to chunk start (0–5s).
            # Add the segment's absolute start offset to each word.
            offset = t_start
            for w in result:
                w["start"] = round(w["start"] + offset, 3)
                w["end"] = round(w["end"] + offset, 3)
            all_results.extend(result)
        finally:
            os.unlink(chunk_path)

    _log.info("分段对齐完成: %d 段 → %d 词", len(segments), len(all_results))
    return all_results


def apply_aligner_timestamps(segments: list, aligned_words: list[dict]) -> list:
    """Apply Qwen3-FA word-level timestamps to transcribed segments.

    Matches aligned words against segments by time range, then updates
    seg.start and seg.end with precise word-level timestamps.

    Args:
        segments: List of Segment objects.
        aligned_words: Flat list of aligned word dicts from QwenAligner.align().

    Returns:
        The same segments list with start/end updated (mutated in place).

    Fallback: If aligned_words is empty or None, segments are returned unchanged.
    """
    if not aligned_words:
        return segments

    total_words = len(aligned_words)
    if total_words == 0:
        return segments

    # Match words to segments by time range — no word counting, no drift.
    unmatched = 0
    for seg in segments:
        seg_original_start = getattr(seg, 'start', 0.0) or 0.0
        seg_original_end = getattr(seg, 'end', seg_original_start + 0.5) or 0.0

        # Collect all aligned words whose timestamps fall within this segment
        seg_words = [
            w for w in aligned_words
            if seg_original_start <= w.get("start", 0) < seg_original_end
        ]

        if seg_words:
            seg.start = seg_words[0]["start"]
            seg.end = seg_words[-1]["end"]
        else:
            unmatched += 1

    if unmatched:
        _log.warning("P213: %d/%d 段未匹配到对齐词，保留原始时间戳", unmatched, len(segments))

    return segments
