"""Audio extraction via ffmpeg. Migrated from V1.3 in V2.08."""
import subprocess
from pathlib import Path


def extract_audio(video_path: str, output_path: str) -> str:
    """Extract 16kHz mono WAV from video."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-i", video_path,
        "-vn", "-ar", "16000", "-ac", "1",
        "-c:a", "pcm_s16le",
        str(out), "-y"
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"音频提取失败: {r.stderr[-300:]}")
    return str(out)


def get_video_resolution(video_path: str) -> tuple[int, int]:
    """Get video width and height via ffprobe. Returns (1920, 1080) on failure."""
    import json
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        video_path
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        data = json.loads(r.stdout)
        stream = data["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except Exception:
        return 1920, 1080


def get_video_duration(video_path: str) -> float:
    """Get video duration in seconds via ffprobe."""
    import json
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", video_path
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        data = json.loads(r.stdout)
        return float(data["format"]["duration"])
    except (ValueError, KeyError, json.JSONDecodeError):
        return 0.0
