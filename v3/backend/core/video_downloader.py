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


def _make_progress_hook(callback=None, kind=None):
    """P442: 把 yt-dlp 的临时估算变成稳定的产品进度。

    total_bytes_estimate 是会变的预计总大小。已下载字节增加、估算总量增加得更多时，
    downloaded/total 会算出一个更小的百分比，界面上就出现 9% → 8%。

    分段下载优先用已完成分段比例（没有可靠总字节数时它更准），普通单文件继续用真实
    字节比例；两者都统一取历史最大值，并在真正下载完成前钳到 99%。视频和音频各自
    创建一个 hook，因此每一路都有自己的计数器，互不覆盖。

    数字不倒退还不够，数字也不能消失：总大小临时算不出来时，如果这一路已经显示过
    有效百分比，继续显示上一次的稳定值；一次都没算出来过才不显示数字（不编 0%）。
    否则用户看到的是 50% → 百分比消失 → 70%，那仍然是同一种闪动。
    """
    if callback is None:
        return lambda d: None

    state = {"percent": 0, "seen": False}

    def _stable_percent(d):
        fragment_count = d.get("fragment_count")
        if fragment_count:
            ratio = (d.get("fragment_index") or 0) / fragment_count
        else:
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if not total:
                return state["percent"] if state["seen"] else None
            ratio = (d.get("downloaded_bytes") or 0) / total
        state["percent"] = max(state["percent"], min(int(ratio * 100), 99))
        state["seen"] = True
        return state["percent"]

    def _hook(d):
        if d.get("status") != "downloading":
            return
        pct = _stable_percent(d)
        if kind is None:
            callback(f"下载中… {pct}%" if pct is not None else "下载中…")
        else:
            callback({"download_kind": kind, "download_percent": pct})
    return _hook


# P198 起 YouTube 输入就是「视频 + 音频」两项资产，两个格式串是同一份事实，
# 整段下载和只补音频都必须用它们，不许各写一份。
_VIDEO_FORMAT = "bestvideo[ext=mp4]/bestvideo/best"
_AUDIO_FORMAT = "bestaudio[ext=m4a]/bestaudio"


def _download_stream(url, output_dir, fmt, suffix, progress_callback, proxy=None,
                     *, force_fresh=False):
    """Download a single stream (video-only or audio-only).

    force_fresh：这一次下载不要断点续传。
    yt-dlp 的 `continuedl` 一旦为真，只要目标路径上有文件就直接报告「已下载完成」，
    连网络都不碰（`yt_dlp/downloader/common.py:434-455`）。正常情况下这正是我们要的
    ——已下完的视频不重下。但当目标路径上躺着一个**无效**的残留时，同一条规则会让
    补下载原样拿回那个坏文件。只有那种时候才把它关掉，其余情况保持可续传。
    """
    try:
        import yt_dlp
    except ImportError:
        return None

    outtmpl = os.path.join(output_dir, f"%(title)s_{suffix}.%(ext)s")
    ydl_opts = {
        "format": fmt,
        "outtmpl": outtmpl,
        "progress_hooks": [_make_progress_hook(progress_callback, kind=suffix)],
        "quiet": True,
        "retries": 10,
        "fragment_retries": 10,
        "retry_sleep_functions": {"http": lambda n: 10, "fragment": lambda n: 10},
        "socket_timeout": 30,
        "continuedl": not force_fresh,
        "js_runtimes": {"node": {}},
    }
    if proxy:
        ydl_opts["proxy"] = proxy

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)
    except Exception:
        if suffix == "audio":
            _log.info("独立音频未取得，正在检查视频自带音轨: %s", url)
        else:
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
        url, output_dir, _VIDEO_FORMAT, "video", progress_callback, proxy=proxy)
    if not video_path:
        return None

    audio_path = _download_stream(
        url, output_dir, _AUDIO_FORMAT, "audio", progress_callback, proxy=proxy)

    return video_path, audio_path


def download_audio_only(url: str, output_dir: str, progress_callback=None,
                        *, force_fresh: bool = False) -> str | None:
    """P364：视频已经在本地了，只把缺失的独立音频补下载回来。

    和 download_video() 走同一个 _download_stream()，因此代理检测、Node 运行时、
    重试次数、进度处理和文件命名全部一致，没有第二套下载逻辑。
    视频文件一个字节都不碰。
    """
    return _download_stream(
        url, output_dir, _AUDIO_FORMAT, "audio", progress_callback,
        proxy=_detect_system_proxy(), force_fresh=force_fresh)
