"""Transcription via faster-whisper."""
import os
from faster_whisper import WhisperModel
from .vad_filter import get_speech_segments, compute_speech_ratio


class Segment:
    def __init__(self, id, start, end, text):
        self.id = id
        self.start = start
        self.end = end
        self.text = text
        self.zh = ""


def transcribe(audio_path: str, model_size: str = "medium",
               source_lang: str = "auto", skip_vad: bool = False) -> list:
    """Transcribe audio, return list of Segment."""
    total_dur = _get_duration(audio_path)

    vad_segments = []
    if not skip_vad:
        print("       VAD 语音检测中…", flush=True)
        vad_segments = get_speech_segments(audio_path)
        if vad_segments:
            ratio = compute_speech_ratio(vad_segments, total_dur)
            print(f"       语音占比: {ratio:.1f}%", flush=True)
            if ratio < 20:
                r = input(f"       ⚠️ 语音极低({ratio:.0f}%)，可能为纯音乐。继续？[y/N] ")
                if r.lower() != "y":
                    raise RuntimeError("用户取消")
            elif ratio < 40:
                print(f"       ⚠️ 非语音内容较多，部分对话可能被过滤。使用 --no-vad 跳过过滤", flush=True)

    compute = "int8" if os.uname().machine == "arm64" else "int8"
    print(f"       加载 Whisper {model_size} 模型…", flush=True)
    model = WhisperModel(model_size, device="cpu", compute_type=compute)

    lang = None if source_lang == "auto" else source_lang
    segments_iter, info = model.transcribe(audio_path, beam_size=5, language=lang)
    print(f"       检测语言: {info.language} (概率 {info.language_probability:.2f})", flush=True)

    results = []
    for seg in segments_iter:
        # Filter: skip high no_speech_prob segments
        if hasattr(seg, "no_speech_prob") and seg.no_speech_prob > 0.5:
            continue
        text = seg.text.strip()
        if len(text) < 2:
            continue
        results.append(Segment(
            id=len(results),
            start=round(seg.start, 2),
            end=round(seg.end, 2),
            text=text
        ))

    if not results:
        raise RuntimeError("未检测到任何语音内容。视频可能为纯音乐或无声")

    return results


def _get_duration(path: str) -> float:
    import wave
    with wave.open(path, "rb") as wf:
        return wf.getnframes() / wf.getframerate()
