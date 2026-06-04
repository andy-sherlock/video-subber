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
    if len(zh) <= 20:
        return [(seg.start, seg.end, zh)]
    parts = _split_zh_into_sentences(zh)
    return [(seg.start, seg.end, "\n".join(parts))]


def _annotate_speaker_changes(entries_with_flag: list) -> list:
    result = []
    for s, e, t, changed, unc in entries_with_flag:
        if changed:
            t = f"- {t}"
        if unc:
            t = f"{t} [?]"
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
              font_name: str = None, video_width: int = 1920, video_height: int = 1080) -> None:
    """Generate ASS with per-sentence splitting and speaker prefixes.

    PlayRes fixed at 1920x1080 so font size stays consistent across resolutions.
    video_width/video_height used only for aspect ratio detection (portrait vs landscape).
    """
    font = font_name or _DEFAULT_FONT
    play_res_x, play_res_y = 1920, 1080
    entries = _annotate_speaker_changes(_collect_entries(segments))
    lines = [
        "[Script Info]", "ScriptType: v4.00+",
        f"PlayResX: {play_res_x}", f"PlayResY: {play_res_y}", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font},20,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        f"0,0,0,0,100,100,0,0,1,2,2,{alignment},6,6,10,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for start, end, text in entries:
        if not text:
            continue
        ass_text = text.replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{_to_ass_time(start)},{_to_ass_time(end)},Default,,0,0,0,,{ass_text}")
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


def mux_subtitles(video_path: str, srt_path: str, output_path: str) -> None:
    """Embed SRT as a soft subtitle track into video container.

    Video and audio streams are copied without re-encoding.
    MP4/M4V/MOV uses mov_text codec, other containers use srt codec.
    """
    ffmpeg = _find_ffmpeg()
    if not (shutil.which(ffmpeg) or os.path.isfile(ffmpeg)):
        raise RuntimeError("FFmpeg 未安装，无法封装字幕。请安装 ffmpeg 后重试。")

    ext = os.path.splitext(output_path)[1].lower()
    codec = "mov_text" if ext in (".mp4", ".m4v", ".mov") else "srt"

    cmd = [
        ffmpeg, "-y",
        "-i", video_path,
        "-i", srt_path,
        "-c", "copy",
        "-c:s", codec,
        "-map", "0:v", "-map", "0:a?", "-map", "1:s",
        output_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0 or not Path(output_path).exists():
        raise RuntimeError(f"字幕封装失败: {r.stderr[-800:]}")


def burn_subtitles(video_path: str, ass_path: str, output_path: str) -> None:
    """Burn ASS subtitles into video via ffmpeg (requires libass)."""
    import tempfile, shutil as _shutil
    ffmpeg = _find_ffmpeg()

    # ffmpeg -vf ass= filter treats ( ) , : as special syntax chars.
    # Copy ASS to a temp path with a safe ASCII name to avoid filter parse errors
    # on filenames like "(Av436571688,P1).ass" or paths with Chinese/spaces.
    tmp_ass = None
    try:
        fd, tmp_ass = tempfile.mkstemp(suffix=".ass")
        os.close(fd)
        _shutil.copy2(ass_path, tmp_ass)

        filter_path = tmp_ass
        if sys.platform == "win32":
            filter_path = tmp_ass.replace("\\", "/").replace(":", "\\:")

        cmd = [ffmpeg, "-i", video_path, "-vf", f"ass={filter_path}",
               "-c:a", "copy", output_path, "-y"]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0 or not Path(output_path).exists():
            raise RuntimeError(f"字幕烧录失败: {r.stderr[-800:]}")
    finally:
        if tmp_ass and Path(tmp_ass).exists():
            Path(tmp_ass).unlink(missing_ok=True)
