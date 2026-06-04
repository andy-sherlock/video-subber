"""SenseVoice audio event classifier.

Runs SenseVoice (funasr) on full audio with its own sentence-level segmentation.
Returns raw sound-event segments — each with text, tags, and timestamps.
Downstream code uses these to trim non-speech intervals from Whisper segments.
"""
import gc
import logging
import re
import time

_log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<\|(\w+)\|>")


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


def run_sensevoice(audio_path: str, device: str = "cpu") -> list[dict] | None:
    """Run SenseVoice on audio and return raw sound-event segments.

    Each returned dict: {text, tags: {language, emotion, event, clean_text},
                         start_s, end_s, event}

    Returns None on failure so callers can distinguish "no speech events"
    from "model failed".
    """
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
        )
    except Exception:
        _log.warning("SenseVoice 模型加载失败", exc_info=True)
        return None

    load_time = time.time() - t0
    _log.info("SenseVoice 模型加载完成 (%.1fs)", load_time)

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
        del model
        gc.collect()
        return None

    proc_time = time.time() - t1
    n_items = len(res) if res else 0
    _log.info("SenseVoice 处理完成 (%.1fs), %d 个片段", proc_time, n_items)

    del model
    gc.collect()
    _log.info("SenseVoice 模型已释放")

    if not res:
        return []

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
