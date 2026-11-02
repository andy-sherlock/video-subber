"""SRT/ASS subtitle generation and ffmpeg subtitle burning.
Migrated from V1.3 in V2.08 with cross-platform fixes:
- burn_subtitles: cross-platform ffmpeg path + Windows ASS path escaping
- build_ass: platform-conditional default font
"""
import subprocess, os, sys, shutil
from pathlib import Path

from backend.core.display_timeline import assert_timeline_valid

_DEFAULT_FONT = "Microsoft YaHei" if sys.platform == "win32" else "PingFang SC"
_FFMPEG_MUX_TIMEOUT = 1800
_FFMPEG_BURN_TIMEOUT = 7200


class SubtitleOutputError(RuntimeError):
    def __init__(
        self,
        *,
        stage: str,
        failure_kind: str,
        user_message: str,
        technical_detail: str = "",
    ):
        super().__init__(user_message)
        self.stage = stage
        self.failure_kind = failure_kind
        self.user_message = user_message
        self.technical_detail = technical_detail


def _to_srt_time(seconds: float) -> str:
    total_ms = max(0, int(round(float(seconds) * 1000)))
    h, remainder = divmod(total_ms, 3600 * 1000)
    m, remainder = divmod(remainder, 60 * 1000)
    s, ms = divmod(remainder, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _to_ass_time(seconds: float) -> str:
    h = int(seconds // 3600); m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def build_srt(entries: list, output_path: str) -> None:
    """把最终显示时间表序列化成 SRT。**只序列化，不改任何时间。**

    G5-1：本函数不再自己收集条目。排版（拆屏、换行、说话人前缀）和时间修复
    全部由 `display_timeline.build_display_timeline()` 一次性完成，三个写出器
    读同一张表。以前 `start = max(start, previous_end)` 那一行会静默把后一条
    往后推——那是在改数据，不是断言（R-14）。真正的修复只在时间表阶段发生；
    走到这里还有结构问题说明上游漏了，必须报错。
    """
    assert_timeline_valid(entries, srt=True)
    lines = []
    for i, e in enumerate(entries, 1):
        lines.append(str(i))
        lines.append(f"{_to_srt_time(e.start)} --> {_to_srt_time(e.end)}")
        lines.append(e.text)
        lines.append("")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def build_ass(entries: list, output_path: str, alignment: int = 8,
              font_name: str = None, video_width: int = 1920, video_height: int = 1080) -> None:
    """把最终显示时间表序列化成内部 ASS（不作为成品交付）。

    G12 第三批 B 收尾起**生产不再调用它**：所有烧录都走 `subtitle_style.build_ass_styled`。
    只剩测试在用，留待后续清理（锁定方案第 9 节残留）。

    与 build_srt 读同一张未取整的表，各自按自身精度取整（SRT 1ms / ASS 10ms）。
    两者取整后时间不保证逐条相同，这是正确行为。
    """
    assert_timeline_valid(entries, srt=True, ass=True)
    font = font_name or _DEFAULT_FONT
    play_res_x, play_res_y = 1920, 1080
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
    for e in entries:
        if not e.text:
            continue
        ass_text = e.text.replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{_to_ass_time(e.start)},{_to_ass_time(e.end)},Default,,0,0,0,,{ass_text}")
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


def mux_subtitles(video_path: str, srt_path: str, output_path: str,
                   audio_path: str | None = None) -> None:
    """Embed SRT as a soft subtitle track into video container.

    Video and audio streams are copied without re-encoding.
    MP4/M4V/MOV uses mov_text codec, other containers use srt codec.
    When audio_path is provided, audio comes from that separate file.
    """
    ffmpeg = _find_ffmpeg()
    if not (shutil.which(ffmpeg) or os.path.isfile(ffmpeg)):
        raise SubtitleOutputError(
            stage="burn",
            failure_kind="ffmpeg_not_installed",
            user_message=(
                "没有检测到 FFmpeg，无法生成带字幕的视频。"
                "请先完成运行环境准备，然后重试。"
            ),
            technical_detail=f"ffmpeg={ffmpeg!r}",
        )

    ext = os.path.splitext(output_path)[1].lower()
    codec = "mov_text" if ext in (".mp4", ".m4v", ".mov") else "srt"

    if audio_path:
        cmd = [
            ffmpeg, "-nostdin", "-y",
            "-i", video_path,
            "-i", audio_path,
            "-i", srt_path,
            "-c", "copy",
            "-c:s", codec,
            "-map", "0:v", "-map", "1:a", "-map", "2:s",
            output_path,
        ]
    else:
        cmd = [
            ffmpeg, "-nostdin", "-y",
            "-i", video_path,
            "-i", srt_path,
            "-c", "copy",
            "-c:s", codec,
            "-map", "0:v", "-map", "0:a?", "-map", "1:s",
            output_path,
        ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, timeout=_FFMPEG_MUX_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        Path(output_path).unlink(missing_ok=True)
        raise SubtitleOutputError(
            stage="burn",
            failure_kind="output_mux_timeout",
            user_message=(
                "字幕文件已生成，但视频封装超时。"
                "你可以先下载 SRT 字幕文件，然后重新生成视频。"
            ),
            technical_detail=(
                f"timeout={_FFMPEG_MUX_TIMEOUT}s "
                f"output_path={output_path!r}"
            ),
        ) from exc
    if r.returncode != 0 or not Path(output_path).is_file():
        Path(output_path).unlink(missing_ok=True)
        raise SubtitleOutputError(
            stage="burn",
            failure_kind="output_mux_failed",
            user_message=(
                "字幕文件已生成，但视频封装失败。"
                "你可以先下载 SRT 字幕文件，然后重新生成视频。"
            ),
            technical_detail=(
                f"returncode={r.returncode}; "
                f"output_exists={Path(output_path).is_file()}; "
                f"stderr={r.stderr[-800:]}"
            ),
        )


def burn_subtitles(video_path: str, ass_path: str, output_path: str,
                   audio_path: str | None = None) -> None:
    """Burn ASS subtitles into video via ffmpeg (requires libass).

    When audio_path is provided, audio comes from that separate file
    instead of the video file (split-download mode).
    """
    import tempfile, shutil as _shutil
    ffmpeg = _find_ffmpeg()
    if not (shutil.which(ffmpeg) or os.path.isfile(ffmpeg)):
        raise SubtitleOutputError(
            stage="burn",
            failure_kind="ffmpeg_not_installed",
            user_message=(
                "没有检测到 FFmpeg，无法生成带字幕的视频。"
                "请先完成运行环境准备，然后重试。"
            ),
            technical_detail=f"ffmpeg={ffmpeg!r}",
        )

    tmp_ass = None
    try:
        fd, tmp_ass = tempfile.mkstemp(suffix=".ass")
        os.close(fd)
        _shutil.copy2(ass_path, tmp_ass)

        filter_path = tmp_ass
        if sys.platform == "win32":
            filter_path = tmp_ass.replace("\\", "/").replace(":", "\\:")

        if audio_path:
            cmd = [ffmpeg, "-nostdin", "-i", video_path, "-i", audio_path,
                   "-vf", f"ass={filter_path}",
                   "-map", "0:v", "-map", "1:a", "-c:a", "copy",
                   output_path, "-y"]
        else:
            cmd = [ffmpeg, "-nostdin", "-i", video_path, "-vf", f"ass={filter_path}",
                   "-c:a", "copy", output_path, "-y"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               stdin=subprocess.DEVNULL, timeout=_FFMPEG_BURN_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            Path(output_path).unlink(missing_ok=True)
            raise SubtitleOutputError(
                stage="burn",
                failure_kind="output_burn_timeout",
                user_message=(
                    "字幕文件已生成，但视频烧录超时。"
                    "你可以先下载 SRT 字幕文件，然后重新生成视频。"
                ),
                technical_detail=(
                    f"timeout={_FFMPEG_BURN_TIMEOUT}s "
                    f"output_path={output_path!r}"
                ),
            ) from exc
        if r.returncode != 0 or not Path(output_path).is_file():
            Path(output_path).unlink(missing_ok=True)
            raise SubtitleOutputError(
                stage="burn",
                failure_kind="output_burn_failed",
                user_message=(
                    "字幕文件已生成，但视频烧录失败。"
                    "你可以先下载 SRT 字幕文件，然后重新生成视频。"
                ),
                technical_detail=(
                    f"returncode={r.returncode}; "
                    f"output_exists={Path(output_path).is_file()}; "
                    f"stderr={r.stderr[-800:]}"
                ),
            )
    finally:
        if tmp_ass and Path(tmp_ass).exists():
            Path(tmp_ass).unlink(missing_ok=True)
