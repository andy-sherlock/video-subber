"""SRT/ASS generation and ffmpeg subtitle burning."""
import re
import subprocess
import os
from pathlib import Path

FONT_PATH = "/System/Library/Fonts/PingFang.ttc"
SENTENCE_END = re.compile(r'[。！？.!?]')
COMMA = re.compile(r'[，,;；、]')


def _to_srt_time(seconds: float) -> str:
    h = int(seconds // 3600); m = int((seconds % 3600) // 60)
    s = int(seconds % 60); ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _to_ass_time(seconds: float) -> str:
    h = int(seconds // 3600); m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _split_zh_into_sentences(text: str, max_chars: int = 20) -> list[str]:
    """Split text into readable subtitle chunks. Two-pass: sentence-end first, then comma for long chunks."""
    # Pass 1: split on sentence-ending punctuation
    parts = []
    last = 0
    for m in SENTENCE_END.finditer(text):
        end = m.end()
        sent = text[last:end].strip()
        if sent:
            parts.append(sent)
        last = end
    remainder = text[last:].strip()
    if remainder:
        if parts:
            parts[-1] += remainder
        else:
            parts.append(remainder)

    # Pass 2: split long chunks on commas
    result = []
    for p in parts:
        if len(p) <= max_chars:
            result.append(p)
        else:
            sub = []
            sub_last = 0
            for m in COMMA.finditer(p):
                end = m.end()
                chunk = p[sub_last:end].strip()
                if chunk:
                    sub.append(chunk)
                sub_last = end
            sub_rem = p[sub_last:].strip()
            if sub_rem:
                sub.append(sub_rem)
            result.extend([s for s in sub if s])
    return [p for p in result if p]


def _split_segment(seg, duration: float = None) -> list:
    """Split one segment into sentence-level sub-segments.
    Returns list of (start, end, text) tuples.
    """
    zh = getattr(seg, "zh", seg.text).strip()
    if not zh:
        return [(seg.start, min(seg.end, seg.start + 6.0), "[翻译失败]")]
    d = duration or (seg.end - seg.start)
    parts = _split_zh_into_sentences(zh)
    if len(parts) <= 1:
        return [(seg.start, min(seg.end, seg.start + 6.0), zh)]
    total_chars = sum(len(p) for p in parts)
    if total_chars == 0:
        return [(seg.start, seg.end, zh)]
    results = []
    t = seg.start
    for p in parts:
        part_dur = d * len(p) / total_chars
        results.append((round(t, 2), round(t + part_dur, 2), p))
        t += part_dur
    return results


def build_srt(segments: list, output_path: str) -> None:
    """Generate SRT with per-sentence splitting."""
    entries = []
    for seg in segments:
        entries.extend(_split_segment(seg))
    lines = []
    for i, (start, end, text) in enumerate(entries, 1):
        lines.append(str(i))
        lines.append(f"{_to_srt_time(start)} --> {_to_srt_time(end)}")
        lines.append(text)
        lines.append("")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def build_ass(segments: list, output_path: str, alignment: int = 8) -> None:
    """Generate ASS with per-sentence splitting."""
    entries = []
    for seg in segments:
        entries.extend(_split_segment(seg))
    font_name = "PingFang SC"
    lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1920", "PlayResY: 1080", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font_name},20,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        f"0,0,0,0,100,100,0,0,1,2,2,{alignment},10,10,50,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for start, end, text in entries:
        if not text or text == "[翻译失败]":
            continue
        lines.append(f"Dialogue: 0,{_to_ass_time(start)},{_to_ass_time(end)},Default,,0,0,0,,{text}")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def burn_subtitles(video_path: str, ass_path: str, output_path: str) -> None:
    """Burn ASS subtitles into video via ffmpeg-full (requires libass)."""
    ffmpeg = "ffmpeg"
    full = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
    if os.path.exists(full):
        ffmpeg = full
    cmd = [ffmpeg, "-i", video_path, "-vf", f"ass={ass_path}",
           "-c:a", "copy", output_path, "-y"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not Path(output_path).exists():
        raise RuntimeError(f"字幕烧录失败: {r.stderr[-500:]}")
