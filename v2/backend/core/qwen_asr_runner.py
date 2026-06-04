"""Qwen3-ASR cross-validation transcription via MLX (Apple Silicon) or PyTorch (Windows).

Runs Qwen3-ASR on full audio and maps its transcription to Whisper segments.
Two alignment paths:
  - Timestamp alignment: when the model returns word-level timestamps (PyTorch)
  - Text similarity alignment: when timestamps are unavailable (MLX / Apple Silicon)
"""
import gc
import logging
import re
import time
from difflib import SequenceMatcher

_log = logging.getLogger("qwen_asr_runner")

_SENTENCE_SPLIT_RE = re.compile(r"[。！？.!?；;]+")

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
        _log.info("Qwen→Whisper 使用 1:1 映射 (qwen=%d whisper=%d)", n_qwen, n_whisper)
        n_match = min(n_qwen, n_whisper)
        for i in range(n_match):
            segments[i].qwen_text = qwen_sentences[i]
        return n_match
    else:
        _log.info("Qwen→Whisper 使用相似度匹配 (qwen=%d whisper=%d)", n_qwen, n_whisper)
        pairs = _match_qwen_to_whisper(qwen_sentences, segments)
        for qs, idx in pairs:
            segments[idx].qwen_text = qs
        return len(pairs)


def transcribe_raw(audio_path: str) -> dict | None:
    """只做转录，不依赖 Whisper segments。失败返回 None。"""
    if not is_qwen_asr_available():
        return None
    t0 = time.time()
    try:
        if _USE_MLX:
            from qwen3_asr_mlx import Qwen3ASR
            model = Qwen3ASR.from_pretrained("mlx-community/Qwen3-ASR-1.7B-bf16")
            result = model.transcribe(audio_path)
            lang = getattr(result, 'language', None)
            text = result.text if result.text else ""
            ts = None
        else:
            from qwen_asr import Qwen3ASRModel
            model = Qwen3ASRModel.from_pretrained("Qwen/Qwen3-ASR-1.7B")
            result = model.transcribe(audio_path, return_time_stamps=True)
            lang = getattr(result, 'language', None)
            text = result.text if hasattr(result, 'text') else ""
            ts = getattr(result, 'time_stamps', None)
    except Exception:
        _log.warning("Qwen3-ASR 转录失败", exc_info=True)
        return None
    finally:
        del model
        gc.collect()
    _log.info("Qwen3-ASR 转录耗时 %.1fs, language=%s", time.time() - t0, lang or "unknown")
    return {"text": text, "language": lang, "timestamps": ts}


def run_qwen_asr(audio_path: str, segments: list) -> list:
    for seg in segments:
        seg.qwen_text = None

    if not is_qwen_asr_available():
        _log.warning("Qwen3-ASR 未安装，跳过交叉验证")
        return segments

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
        result = model.transcribe(audio_path)
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
        _log.info("Qwen→Whisper 使用时间戳对齐（PyTorch 路径）")
        ts_all = []
        for ts in result.time_stamps:
            if hasattr(ts, 'items'):
                ts_all.extend(ts.items)
            elif isinstance(ts, list):
                ts_all.extend(ts)
        matched = align_by_timestamps(ts_all, segments)
    else:
        _log.info("Qwen→Whisper 使用文字相似度对齐（MLX 路径）")
        matched = align_by_similarity(full_text, segments)

    total = sum(1 for s in segments if s.qwen_text is not None)
    _log.info("Qwen3-ASR 交叉验证完成: %d/%d segments 已映射", total, len(segments))
    return segments
