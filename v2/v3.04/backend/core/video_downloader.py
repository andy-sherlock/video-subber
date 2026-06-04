"""#9 yt-dlp video download — paste URL, download, enter pipeline."""
import logging

_log = logging.getLogger(__name__)


def is_ytdlp_available() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


def _build_output_template(output_dir: str) -> str:
    import os
    return os.path.join(output_dir, "%(title)s.%(ext)s")


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


def download_video(url: str, output_dir: str, progress_callback=None) -> str | None:
    try:
        import yt_dlp
    except ImportError:
        _log.warning("yt-dlp 未安装")
        return None

    ydl_opts = {
        "format": "best",
        "outtmpl": _build_output_template(output_dir),
        "progress_hooks": [_make_progress_hook(progress_callback)],
        "quiet": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)
    except Exception:
        _log.warning("yt-dlp 下载失败: %s", url, exc_info=True)
        return None
