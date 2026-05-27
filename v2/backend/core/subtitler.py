"""SRT/ASS subtitle generation and ffmpeg subtitle burning.
Migrated from V1.3 in V2.08 with cross-platform fixes:
- burn_subtitles: cross-platform ffmpeg path + Windows ASS path escaping
- build_ass: platform-conditional default font
"""
import re, subprocess, os, sys, shutil
from pathlib import Path

SENTENCE_END = re.compile(r'[。！？.!?]')
COMMA = re.compile(r'[，,;；、]')

_DEFAULT_FONT = "Microsoft YaHei" if sys.platform == "win32" else "PingFang SC"


def _to_srt_time(seconds: float) -> str:
    h = int(seconds // 3600); m = int((seconds % 3600) // 60)
    s = int(seconds % 60); ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _to_ass_time(seconds: float) -> str:
    h = int(seconds // 3600); m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _split_zh_into_sentences(text: str, max_chars: int = 20) -> list[str]:
    """Split text into readable subtitle chunks. Two-pass: sentence-end first, then comma."""
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


def _annotate_speaker_changes(entries_with_flag: list) -> list:
    result = []
    for s, e, t, changed, unc in entries_with_flag:
        if changed:
            t = f"- {t}"
        if unc:
            t = f"{t} [ ? ]"
        result.append((s, e, t))
    return result


def _collect_entries(segments: list) -> list:
    out = []
    for seg in segments:
        changed = getattr(seg, "speaker_change", False)
        unc = getattr(seg, "uncertain", False)
        for start, end, text in _split_segment(seg):
            out.append((start, end, text, changed, unc))
    return out


def build_srt(segments: list, output_path: str) -> None:
    """Generate SRT with per-sentence splitting and speaker change prefixes."""
    entries = _annotate_speaker_changes(_collect_entries(segments))
    lines = []
    for i, (start, end, text) in enumerate(entries, 1):
        lines.append(str(i))
        lines.append(f"{_to_srt_time(start)} --> {_to_srt_time(end)}")
        lines.append(text)
        lines.append("")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def build_ass(segments: list, output_path: str, alignment: int = 8,
              font_name: str = None) -> None:
    """Generate ASS with per-sentence splitting and speaker prefixes."""
    font = font_name or _DEFAULT_FONT
    entries = _annotate_speaker_changes(_collect_entries(segments))
    lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1920", "PlayResY: 1080", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font},20,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        f"0,0,0,0,100,100,0,0,1,2,2,{alignment},10,10,50,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for start, end, text in entries:
        if not text:
            continue
        lines.append(f"Dialogue: 0,{_to_ass_time(start)},{_to_ass_time(end)},Default,,0,0,0,,{text}")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _find_ffmpeg() -> str:
    """Locate ffmpeg binary. Prefers full builds (with libass) on macOS."""
    if sys.platform == "win32":
        for path in [
            r"C:\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
        ]:
            if os.path.exists(path):
                return path
    else:
        for path in [
            "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
            "/usr/local/opt/ffmpeg-full/bin/ffmpeg",
        ]:
            if os.path.exists(path):
                return path
    return shutil.which("ffmpeg") or "ffmpeg"


def burn_subtitles(video_path: str, ass_path: str, output_path: str) -> None:
    """Burn ASS subtitles into video via ffmpeg (requires libass)."""
    ffmpeg = _find_ffmpeg()

    # Windows: ffmpeg -vf ass= filter cannot handle backslashes or bare drive colons.
    # Convert C:\path\to\file.ass → C\:/path/to/file.ass
    filter_path = ass_path
    if sys.platform == "win32":
        filter_path = ass_path.replace("\\", "/").replace(":", "\\:")

    cmd = [ffmpeg, "-i", video_path, "-vf", f"ass={filter_path}",
           "-c:a", "copy", output_path, "-y"]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0 or not Path(output_path).exists():
        raise RuntimeError(f"字幕烧录失败: {r.stderr[-500:]}")
