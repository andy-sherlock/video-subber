"""Qwen3-ForcedAligner — word-level timestamp alignment.

Loads Qwen/Qwen3-ForcedAligner-0.6B via HuggingFace transformers.
Input: audio file + Chinese text → Output: per-word start/end timestamps.

Model time-sharing: call close() to release model after use.
"""
import logging

_log = logging.getLogger("qwen_aligner")


class QwenAligner:
    """Wrapper around Qwen3-ForcedAligner for word-level alignment.

    Usage:
        aligner = QwenAligner()
        words = aligner.align("audio.wav", "你好世界", language="zh")
        aligner.close()  # Release model memory
    """

    def __init__(self, model_id: str = "Qwen/Qwen3-ForcedAligner-0.6B",
                 device: str | None = None):
        """Load the ForcedAligner model from HuggingFace.

        Args:
            model_id: HuggingFace model ID.
            device: "cuda", "mps", "cpu", or None (auto-detect).
        """
        self._model_id = model_id
        self._model = None
        self._closed = False

        try:
            import torch
            from transformers import AutoModel, AutoProcessor

            if device is None:
                if torch.cuda.is_available():
                    device = "cuda"
                elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                    device = "mps"
                else:
                    device = "cpu"

            _log.info("加载 Qwen3-ForcedAligner: %s (device=%s)", model_id, device)
            self._model = AutoModel.from_pretrained(
                model_id, torch_dtype=torch.float16 if device == "cuda" else torch.float32,
                device_map=device if device == "cuda" else None,
            )
            if device != "cuda":
                self._model = self._model.to(device)
            self._processor = AutoProcessor.from_pretrained(model_id)
            self._device = device
            _log.info("Qwen3-ForcedAligner 加载完成")
        except ImportError as e:
            _log.warning("Qwen3-ForcedAligner 不可用: %s", e)
            self._model = None
            self._processor = None

    def align(self, audio_path: str, text: str, language: str = "zh") -> list[dict]:
        """Align text to audio, returning per-word timestamps.

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

        try:
            import torch
            import librosa

            # Load audio
            audio, sr = librosa.load(audio_path, sr=16000, mono=True)
            lang_name = _map_language(language)

            # Tokenize text with <timestamp> markers around each word
            words = text.split()
            marked_text = ""
            for w in words:
                marked_text += f"<timestamp> {w} <timestamp> "

            # Prepare inputs
            inputs = self._processor(
                text=marked_text,
                audio=audio,
                sampling_rate=16000,
                return_tensors="pt",
                language=lang_name,
            )
            if self._device:
                inputs = {k: v.to(self._device) for k, v in inputs.items()}

            with torch.no_grad():
                outputs = self._model(**inputs)

            # Extract timestamps from logits at <timestamp> token positions
            # Qwen3-FA outputs 5000-class logits (80ms per class)
            logits = outputs.logits  # [batch, seq_len, 5000]
            ts_indices = torch.argmax(logits, dim=-1)  # [batch, seq_len]
            ts_seconds = ts_indices.float() * 0.08  # Convert class → seconds

            # Find positions of <timestamp> tokens and extract their predictions
            ts_mask = inputs.get("timestamp_mask", None)
            if ts_mask is None:
                # Fallback: every even position after the first is a timestamp
                ts_positions = range(1, ts_seconds.shape[1], 2)
            else:
                ts_positions = ts_mask.nonzero(as_tuple=True)[1]

            timestamps = ts_seconds[0, ts_positions].cpu().tolist()

            # Pair start/end timestamps with words
            result = []
            for i, w in enumerate(words):
                start_idx = i * 2
                end_idx = i * 2 + 1
                if start_idx < len(timestamps) and end_idx < len(timestamps):
                    result.append({
                        "word": w,
                        "start": round(timestamps[start_idx], 3),
                        "end": round(timestamps[end_idx], 3),
                    })
                else:
                    # Fallback: evenly space
                    t = i * 0.5
                    result.append({"word": w, "start": t, "end": t + 0.4})

            _log.info("对齐完成: %d 词, 首词 %.3fs-%.3fs",
                      len(result),
                      result[0]["start"] if result else 0.0,
                      result[-1]["end"] if result else 0.0)
            return result

        except Exception as e:
            _log.warning("Qwen3-FA 对齐失败，降级到均匀分配: %s", e)
            # Fallback: evenly distribute timestamps
            words = text.split()
            if not words:
                return []
            duration = 1.0
            try:
                import librosa
                duration = librosa.get_duration(path=audio_path)
            except Exception:
                pass
            per_word = duration / len(words)
            result = []
            for i, w in enumerate(words):
                result.append({
                    "word": w,
                    "start": round(i * per_word, 3),
                    "end": round((i + 1) * per_word, 3),
                })
            return result

    def close(self):
        """Release the model from memory. Idempotent."""
        if not self._closed and self._model is not None:
            import gc
            del self._model
            self._model = None
            if hasattr(self, '_processor') and self._processor is not None:
                del self._processor
                self._processor = None
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
    """Check if Qwen3-ForcedAligner can be loaded."""
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
        return True
    except ImportError:
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


def apply_aligner_timestamps(segments: list, aligned_words: list[dict]) -> list:
    """Apply Qwen3-FA word-level timestamps to transcribed segments.

    Matches aligned words against each segment's transcription text (seg.text),
    then updates seg.start and seg.end with the precise timestamps.

    Args:
        segments: List of Segment objects with .zh attribute set.
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

    # Build a flat timeline: cumulative character positions → time
    word_idx = 0

    for seg in segments:
        txt = getattr(seg, 'text', '') or ''
        txt_len = len(txt.replace(' ', ''))

        if txt_len == 0 or word_idx >= total_words:
            continue

        # Count how many aligned words overlap with this segment's text
        seg_word_count = max(1, len(txt.split()))
        end_word_idx = min(word_idx + seg_word_count, total_words)

        seg_words = aligned_words[word_idx:end_word_idx]
        if seg_words:
            seg.start = seg_words[0]["start"]
            seg.end = seg_words[-1]["end"]

        word_idx = end_word_idx

    return segments
