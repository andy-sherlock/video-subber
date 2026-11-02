"""Shared output naming and validation for worker and crash recovery."""
from __future__ import annotations

import re
import subprocess
import os
from pathlib import Path

_SRT_TIMELINE = re.compile(
    r"\d{2}:\d{2}:\d{2},\d{3}\s+-->\s+\d{2}:\d{2}:\d{2},\d{3}"
)
_FFPROBE_TIMEOUT = 30
_COMPARE_CHUNK_SIZE = 1024 * 1024


def derive_output_identity(video_path: str) -> tuple[str, str]:
    """P516：成品名 = 原视频地址最后一段去后缀；名字不另存、不改名。"""
    actual_path = Path(video_path)
    video_stem = actual_path.stem or "output"
    video_suffix = actual_path.suffix.lower()
    if not video_suffix:
        raise ValueError("cannot determine video suffix")
    return video_stem, video_suffix


def output_paths(job_dir: Path, config: dict, video_path: str) -> dict[str, Path]:
    stem, suffix = derive_output_identity(video_path)
    root = Path(job_dir)
    return {
        "srt": root / f"{stem}.srt",
        "burn": root / f"{stem}_zh.mp4",
        "mux": root / f"{stem}_zh_sub{suffix}",
    }


def is_inside(path, folder) -> bool:
    """两边都解析成真实位置（跟随链接、消除 ..、Windows 盘符大小写）后，path 是否在 folder 之内。
    commonpath 比的是整段目录名，job1 与 job10 不会混。"""
    try:
        real = os.path.normcase(os.path.realpath(path))
        root = os.path.normcase(os.path.realpath(folder))
        return real != root and os.path.commonpath([real, root]) == root
    except (OSError, ValueError):
        return False


def saved_output_paths(
    config: dict, artifacts: dict[str, Path], output_mode: str, *, source_path, job_dir
) -> dict[str, Path]:
    """Return the final user-facing destinations for this output mode.

    P516：保存位置从任务唯一的原视频地址现算——用户自己的视频（在任务文件夹之外）
    成品存它旁边；开发期上传、链接下载的视频在任务文件夹里，存设置的保存位置。"""
    source = str(source_path or "")
    if source and not is_inside(source, job_dir) and Path(source).parent.is_dir():
        save_dir = Path(source).parent
    else:
        save_dir = Path(
            config.get("savePath", "~/Downloads/Video-Subber")
        ).expanduser()
    if output_mode == "burn":
        keys = ("burn",)
    elif output_mode == "mux":
        keys = ("mux", "srt")
    elif output_mode == "srt":
        keys = ("srt",)
    else:
        return {}
    return {key: save_dir / artifacts[key].name for key in keys}


def files_match(source: Path, destination: Path) -> bool:
    """Return True only when both files exist and their bytes are identical."""
    try:
        source_path = Path(source)
        destination_path = Path(destination)
        if not source_path.is_file() or not destination_path.is_file():
            return False
        if source_path.stat().st_size != destination_path.stat().st_size:
            return False
        with source_path.open("rb") as source_file, destination_path.open("rb") as destination_file:
            while True:
                source_chunk = source_file.read(_COMPARE_CHUNK_SIZE)
                destination_chunk = destination_file.read(_COMPARE_CHUNK_SIZE)
                if source_chunk != destination_chunk:
                    return False
                if not source_chunk:
                    return True
    except OSError:
        return False


def _contained_file(path: Path, job_dir: Path) -> Path | None:
    try:
        candidate = Path(path).resolve()
        candidate.relative_to(Path(job_dir).resolve())
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    if not candidate.is_file() or candidate.stat().st_size <= 0:
        return None
    return candidate


def is_valid_srt_file(path: Path, job_dir: Path) -> bool:
    candidate = _contained_file(path, job_dir)
    if candidate is None:
        return False
    try:
        text = candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return bool(_SRT_TIMELINE.search(text))


def is_valid_media_file(path: Path, job_dir: Path) -> bool:
    candidate = _contained_file(path, job_dir)
    if candidate is None:
        return False
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=format_name",
        "-of", "default=noprint_wrappers=1:nokey=1", str(candidate),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=_FFPROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and bool((result.stdout or "").strip())
