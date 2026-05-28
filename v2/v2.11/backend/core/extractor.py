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


def get_video_duration(video_path: str) -> float:
    """Get video duration in seconds via ffprobe."""
    import json
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", video_path
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    data = json.loads(r.stdout)
    return float(data["format"]["duration"])
