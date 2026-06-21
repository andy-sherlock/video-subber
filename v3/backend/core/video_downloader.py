"""#9 yt-dlp video download — paste URL, download, enter pipeline.

P249: Auto-detect system proxy so yt-dlp works through Clash/V2Ray/soft-router
without requiring the user to configure anything.
"""
import logging
import os
import platform
import subprocess

_log = logging.getLogger(__name__)


def is_ytdlp_available() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


def _detect_system_proxy() -> str | None:
    """Auto-detect proxy from env vars or OS settings (P249).

    Priority: HTTP_PROXY env → macOS system proxy → Windows system proxy.
    Returns proxy URL string (e.g. 'http://127.0.0.1:7890') or None.
    """
    # ① Environment variables (many proxy tools set these)
    for var in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        val = os.environ.get(var, "").strip()
        if val:
            from urllib.parse import urlparse as _urlparse
            _p = _urlparse(val)
            _safe = f"{_p.scheme}://{_p.hostname}:{_p.port}" if _p.hostname else val
            _log.info("P249: 使用环境变量代理 %s=%s", var, _safe)
            return val

    # ② macOS — read System Preferences proxy (FlClash, ClashX, Surge, V2Ray use this)
    if platform.system() == "Darwin":
        try:
            raw = subprocess.run(
                ["scutil", "--proxy"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            host = port = None
            for line in raw.splitlines():
                line = line.strip()
                if line == "HTTPEnable : 1":
                    pass  # proxy is enabled
                elif line.startswith("HTTPProxy : "):
                    host = line.split(":", 1)[1].strip()
                elif line.startswith("HTTPPort : "):
                    port = line.split(":", 1)[1].strip()
            if host and port:
                proxy_url = f"http://{host}:{port}"
                _log.info("P249: 检测到 macOS 系统代理 %s", proxy_url)
                return proxy_url
        except Exception:
            pass

    # ③ Windows — check IE proxy settings via registry (future: implement)
    # ④ Nothing found
    return None


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


def _download_stream(url, output_dir, fmt, suffix, progress_callback, proxy=None):
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
        "retries": 10,
        "fragment_retries": 10,
        "retry_sleep_functions": {"http": lambda n: 10, "fragment": lambda n: 10},
        "socket_timeout": 30,
        "continuedl": True,
    }
    if proxy:
        ydl_opts["proxy"] = proxy

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
    proxy = _detect_system_proxy()

    video_path = _download_stream(
        url, output_dir, "bestvideo[ext=mp4]/bestvideo/best", "video",
        progress_callback, proxy=proxy)
    if not video_path:
        return None

    audio_path = _download_stream(
        url, output_dir, "bestaudio[ext=m4a]/bestaudio", "audio",
        progress_callback, proxy=proxy)

    return video_path, audio_path
