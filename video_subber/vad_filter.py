"""Voice Activity Detection via silero-vad."""
import torch
import numpy as np
import wave


def get_speech_segments(audio_path: str) -> list[tuple[float, float]]:
    """Return [(start, end), ...] segments where speech is detected."""
    try:
        model, utils = torch.hub.load(
            "snakers4/silero-vad",
            "silero_vad",
            trust_repo=True
        )
    except Exception:
        print("  ⚠️ silero-vad 加载失败，跳过 VAD 过滤")
        return []

    wf = wave.open(audio_path, "rb")
    sample_rate = wf.getframerate()
    if sample_rate != 16000:
        print("  ⚠️ VAD 需要 16kHz 音频，跳过")
        return []

    audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    wf.close()

    get_speech_timestamps = utils[0]
    vad_model = model

    # Feed entire audio to VAD — it processes internally with proper context windows.
    try:
        ts = get_speech_timestamps(
            torch.from_numpy(audio),
            vad_model,
            sampling_rate=16000,
            threshold=0.5,
            min_speech_duration_ms=500,
            min_silence_duration_ms=100,
        )
        speech_ts = [(t["start"] / 16000, t["end"] / 16000) for t in ts]
    except Exception:
        print("  ⚠️ VAD 处理失败，跳过过滤")
        return []

    merged = []
    for seg in speech_ts:
        if merged and seg[0] - merged[-1][1] < 0.3:
            merged[-1] = (merged[-1][0], seg[1])
        else:
            merged.append(seg)

    return merged


def compute_speech_ratio(segments: list, total_duration: float) -> float:
    """Return percentage of total audio that contains speech."""
    if total_duration <= 0:
        return 0.0
    speech_time = sum(e - s for s, e in segments)
    return speech_time / total_duration * 100
