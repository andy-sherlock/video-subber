"""Voice activity detection — used for speech-ratio health check only.

v1.1 改造（P015 修复）：
原本项目自实现的滑窗 VAD（参数 threshold=0.5, min_speech_duration_ms=500）
会过度过滤短回应。现已废弃"切音频"逻辑，统一交给 faster-whisper 内置 VAD。
本模块仅保留"计算语音占比"用于健康检查（低于 20% 警告用户可能是纯音乐）。
"""
import wave
import numpy as np


def compute_speech_ratio_via_vad(audio_path: str):
    """Run silero-vad once to estimate percent of audio that contains speech.

    Returns float (0-100) or None if VAD failed to load."""
    try:
        import torch
    except ImportError:
        return None

    try:
        model, utils = torch.hub.load(
            "snakers4/silero-vad",
            "silero_vad",
            trust_repo=True
        )
    except Exception:
        print("       ⚠️ silero-vad 加载失败，跳过占比检测")
        return None

    try:
        wf = wave.open(audio_path, "rb")
        sample_rate = wf.getframerate()
        if sample_rate != 16000:
            wf.close()
            return None
        audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        total_dur = len(audio) / 16000.0
        wf.close()
    except Exception:
        return None

    get_speech_timestamps = utils[0]

    try:
        ts = get_speech_timestamps(
            torch.from_numpy(audio),
            model,
            sampling_rate=16000,
            threshold=0.4,
            min_speech_duration_ms=250,
            min_silence_duration_ms=100,
        )
    except Exception:
        return None

    if not ts or total_dur <= 0:
        return 0.0

    speech_time = sum((t["end"] - t["start"]) / 16000.0 for t in ts)
    return speech_time / total_dur * 100
