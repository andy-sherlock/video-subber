"""#9 yt-dlp video download — paste URL, download, enter pipeline."""
import logging
import os

_log = logging.getLogger(__name__)


def is_ytdlp_available() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


def _make_progress_hook(callback=None):
    if callback is None:
        return lambda d: None

    def _hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                pct = int(d["downloaded_bytes"] / total * 100)
                callback(f"下载中… {pct}%")
            else:
                callback("下载中…")
    return _hook


def _download_stream(url, output_dir, fmt, suffix, progress_callback):
    """Download a single stream (video-only or audio-only)."""
    try:
        import yt_dlp
    except ImportError:
        return None

    outtmpl = os.path.join(output_dir, f"%(title)s_{suffix}.%(ext)s")
    ydl_opts = {
        "format": fmt,
        "outtmpl": outtmpl,
        "progress_hooks": [_make_progress_hook(progress_callback)],
        "quiet": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)
    except Exception:
        _log.warning("yt-dlp 下载失败 (%s): %s", suffix, url, exc_info=True)
        return None


def download_video(url: str, output_dir: str, progress_callback=None) -> tuple[str, str | None] | None:
    """Download video and audio as separate files.

    Returns (video_path, audio_path) on success.
    audio_path may be None if separate audio is unavailable.
    Returns None on total failure.
    """
    video_path = _download_stream(
        url, output_dir, "bestvideo[ext=mp4]/bestvideo/best", "video", progress_callback)
    if not video_path:
        return None

    audio_path = _download_stream(
        url, output_dir, "bestaudio[ext=m4a]/bestaudio", "audio", progress_callback)

    return video_path, audio_path
