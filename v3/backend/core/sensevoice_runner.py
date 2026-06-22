"""SenseVoice audio event classifier.

Runs SenseVoice (funasr) on full audio with its own sentence-level segmentation.
Returns raw sound-event segments — each with text, tags, and timestamps.
Downstream code uses these to trim non-speech intervals from Whisper segments.

For long audio (> 20 min) the model is run on ~5-minute chunks whose boundaries
align with Whisper segments so each chunk starts/ends on a natural sentence break.
"""
import gc
import logging
import os
import re
import tempfile
import time

_log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<\|(\w+)\|>")

# Chunking constants (mirrors qwen_asr_runner)
_LONG_AUDIO_THRESHOLD_S = 1200  # 20 minutes


def is_sensevoice_available() -> bool:
    try:
        import funasr  # noqa: F401
        return True
    except ImportError:
        return False


def extract_tags(text: str) -> dict:
    tags = _TAG_RE.findall(text)
    language = tags[0] if len(tags) > 0 else "unknown"
    emotion = tags[1] if len(tags) > 1 else "NEUTRAL"
    event = tags[2] if len(tags) > 2 else "Speech"
    clean_text = _TAG_RE.sub("", text).strip()
    return {"language": language, "emotion": emotion, "event": event, "clean_text": clean_text}


# ═══════════════════════════════════════════════════════════════════
# Chunked SenseVoice (internal)
# ═══════════════════════════════════════════════════════════════════

def _run_sensevoice_chunked(audio_path: str, device: str,
                            segments: list) -> list[dict] | None:
    """Chunked SenseVoice for long audio.

    Uses the same chunking strategy as Qwen3-ASR: batches Whisper segments
    into ~5-minute groups, extracts each chunk's audio, and runs SenseVoice
    independently.  Timestamps are offset by each chunk's start time.
    """
    from backend.core.qwen_asr_runner import _chunk_segments, _extract_audio_chunk

    chunks = _chunk_segments(segments)
    _log.info("SenseVoice 分段处理: %d chunks (%d segments)",
              len(chunks), len(segments))

    try:
        from funasr import AutoModel
    except ImportError:
        _log.warning("SenseVoice 未安装 (funasr import failed)")
        return None

    _log.info("加载 SenseVoice 模型 (device=%s) ...", device)
    t0 = time.time()
    try:
        model = AutoModel(
            model="iic/SenseVoiceSmall",
            trust_remote_code=True,
            device=device,
            disable_pbar=True,     # P238: 关闭 tqdm 进度条，避免 stderr BrokenPipe
        )
    except Exception:
        _log.warning("SenseVoice 模型加载失败", exc_info=True)
        return None
    _log.info("SenseVoice 模型加载完成 (%.1fs)", time.time() - t0)

    all_events = []

    for idx, (_seg_start, _seg_end, t_start, t_end) in enumerate(chunks):
        fd, chunk_path = tempfile.mkstemp(
            suffix=".wav", prefix=f"sv_chunk_{idx}_")
        os.close(fd)
        try:
            _extract_audio_chunk(audio_path, t_start, t_end, chunk_path)

            res = model.generate(
                input=chunk_path,
                cache={},
                language="auto",
                use_itn=True,
                batch_size_s=60,
                sentence_timestamp=True,
            )

            if res:
                for item in res:
                    raw_text = item.get("text", "")
                    parsed = extract_tags(raw_text)
                    timestamp = item.get("timestamp") or []
                    start_s = (timestamp[0][0] / 1000.0 + t_start
                               if timestamp else t_start)
                    end_s = (timestamp[-1][1] / 1000.0 + t_start
                             if timestamp else t_end)

                    all_events.append({
                        "text": parsed["clean_text"],
                        "tags": parsed,
                        "start_s": start_s,
                        "end_s": end_s,
                        "event": parsed["event"],
                    })

            _log.info("SenseVoice chunk %d/%d 完成 (%.1fs–%.1fs)",
                      idx + 1, len(chunks), t_start, t_end)
        except Exception:
            _log.warning("SenseVoice chunk %d/%d 失败",
                         idx + 1, len(chunks), exc_info=True)
        finally:
            try:
                os.unlink(chunk_path)
            except OSError:
                pass

    del model
    gc.collect()
    _log.info("SenseVoice 模型已释放")

    tag_counts = {}
    for ev in all_events:
        tag_counts[ev["event"]] = tag_counts.get(ev["event"], 0) + 1
    _log.info("SenseVoice 分段处理完成, 标签分布: %s", tag_counts)

    return all_events


# ═══════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════

def _load_sensevoice_model(device: str):
    """加载 SenseVoice 模型；import 失败或加载失败返回 None。"""
    try:
        from funasr import AutoModel
    except ImportError:
        _log.warning("SenseVoice 未安装 (funasr import failed)")
        return None

    _log.info("加载 SenseVoice 模型 (device=%s) ...", device)
    t0 = time.time()
    try:
        model = AutoModel(
            model="iic/SenseVoiceSmall",
            trust_remote_code=True,
            device=device,
            disable_pbar=True,     # P238: 关闭 tqdm 进度条，避免 stderr BrokenPipe
        )
    except Exception:
        _log.warning("SenseVoice 模型加载失败", exc_info=True)
        return None

    load_time = time.time() - t0
    _log.info("SenseVoice 模型加载完成 (%.1fs)", load_time)
    return model


def _sensevoice_generate(model, audio_path: str):
    """SenseVoice 推理；失败返回 None，成功返回原始 res。"""
    t1 = time.time()
    try:
        res = model.generate(
            input=audio_path,
            cache={},
            language="auto",
            use_itn=True,
            batch_size_s=60,
            sentence_timestamp=True,
        )
    except Exception:
        _log.warning("SenseVoice generate() 失败", exc_info=True)
        return None

    proc_time = time.time() - t1
    n_items = len(res) if res else 0
    _log.info("SenseVoice 处理完成 (%.1fs), %d 个片段", proc_time, n_items)
    return res


def _parse_sensevoice_results(res) -> list[dict]:
    """把 SenseVoice 原始 res 解析为 event dict 列表，并打印标签分布。"""
    events = []
    for item in res:
        raw_text = item.get("text", "")
        parsed = extract_tags(raw_text)
        timestamp = item.get("timestamp") or []
        start_s = timestamp[0][0] / 1000.0 if timestamp else 0.0
        end_s = timestamp[-1][1] / 1000.0 if timestamp else 0.0

        events.append({
            "text": parsed["clean_text"],
            "tags": parsed,
            "start_s": start_s,
            "end_s": end_s,
            "event": parsed["event"],
        })

    tag_counts = {}
    for ev in events:
        tag_counts[ev["event"]] = tag_counts.get(ev["event"], 0) + 1
    _log.info("SenseVoice 标签分布: %s", tag_counts)

    return events


def run_sensevoice(audio_path: str, device: str = "cpu",
                   segments: list = None) -> list[dict] | None:
    """Run SenseVoice on audio and return raw sound-event segments.

    Each returned dict: {text, tags: {language, emotion, event, clean_text},
                         start_s, end_s, event}

    Args:
        audio_path: path to audio file.
        device: "cpu" or "cuda".
        segments: optional Whisper segments for chunking; if provided and
                  total duration > 20 min, chunked mode is used.

    Returns None on failure so callers can distinguish "no speech events"
    from "model failed".
    """
    # ── Chunked path ──
    if segments:
        total_duration = segments[-1].end
        if total_duration > _LONG_AUDIO_THRESHOLD_S:
            _log.info("SenseVoice 音频 %.0fs > 20min，启用分段处理",
                      total_duration)
            return _run_sensevoice_chunked(audio_path, device, segments)

    # ── Direct path ──
    model = _load_sensevoice_model(device)
    if model is None:
        return None

    res = _sensevoice_generate(model, audio_path)

    del model
    gc.collect()
    # 注：generate 失败（res is None）保持原行为——不打印"已释放"日志，直接返回 None。
    if res is None:
        return None
    _log.info("SenseVoice 模型已释放")

    if not res:
        return []

    return _parse_sensevoice_results(res)


def trim_segments_by_sensevoice(segments: list, sv_events: list[dict]) -> list:
    """Use SenseVoice sound-event boundaries to trim Whisper segments.

    Non-speech intervals (BGM, Applause, Laughter, etc.) are removed.
    Whisper segments that fall inside a non-speech zone are discarded.
    Whisper segments that overlap a non-speech boundary are trimmed.

    Returns trimmed list (new list — does not mutate originals).
    """
    if not sv_events:
        return segments

    non_speech_ranges = []
    for ev in sv_events:
        if ev["event"] != "Speech":
            non_speech_ranges.append((ev["start_s"], ev["end_s"]))

    if not non_speech_ranges:
        return segments

    _log.info("SenseVoice 发现 %d 个非人声区间，开始修剪字幕",
              len(non_speech_ranges))

    trimmed = []
    dropped = 0
    for seg in segments:
        seg_start = seg.start
        seg_end = seg.end

        for ns_start, ns_end in non_speech_ranges:
            if ns_start <= seg_start < ns_end:
                seg_start = ns_end
            if ns_start < seg_end <= ns_end:
                seg_end = ns_start

        if seg_end - seg_start < 0.1:
            dropped += 1
            continue

        seg.start = round(seg_start, 2)
        seg.end = round(seg_end, 2)
        trimmed.append(seg)

    if dropped:
        _log.info("SenseVoice 修剪: 丢弃 %d 段, 保留 %d 段", dropped, len(trimmed))
    return trimmed
