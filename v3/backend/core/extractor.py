"""Audio extraction via ffmpeg. Migrated from V1.3 in V2.08."""
import json
import subprocess
from pathlib import Path

# P346: 外部进程超时护栏。ffprobe 只读元数据，应秒级；ffmpeg 抽音频是 I/O 型，
# 30 分钟是纯安全网——只兜真卡死，不会误杀正常长视频。stdin 一并隔离，
# 避免 ffmpeg/ffprobe 在管道环境里等输入而挂住。
_FFPROBE_TIMEOUT = 30
_FFMPEG_AUDIO_TIMEOUT = 1800


def _remove_quietly(path: Path):
    try:
        path.unlink()
    except (FileNotFoundError, OSError):
        pass


def extract_audio(video_path: str, output_path: str) -> str:
    """Extract 16kHz mono WAV from video.

    P346: ffmpeg writes to a temp file first; only a clean run (returncode 0,
    non-empty output) is atomically renamed to the final path. A timeout /
    non-zero exit / empty output never leaves a half-written final file behind
    (the worker treats an existing audio.wav as a valid cache) and never deletes
    a previously good audio.wav."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    # P346: 清掉上次进程被强杀可能留下的旧 tmp，避免它被误当本次输出 replace 成最终文件。
    _remove_quietly(tmp)
    cmd = [
        "ffmpeg", "-nostdin", "-i", video_path,
        "-vn", "-ar", "16000", "-ac", "1",
        "-c:a", "pcm_s16le",
        # P346: 写 .tmp 后缀，ffmpeg 无法从扩展名推断格式，必须显式 -f wav。
        "-f", "wav",
        str(tmp), "-y"
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, timeout=_FFMPEG_AUDIO_TIMEOUT)
    except subprocess.TimeoutExpired:
        _remove_quietly(tmp)
        raise RuntimeError(
            f"音频提取超时（超过 {_FFMPEG_AUDIO_TIMEOUT} 秒）。视频可能损坏或异常，请重新提交。")
    if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        stderr_tail = (r.stderr or "")[-300:]
        _remove_quietly(tmp)
        raise RuntimeError(f"音频提取失败: {stderr_tail}")
    tmp.replace(out)
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
                           encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, timeout=_FFPROBE_TIMEOUT)
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
                           encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, timeout=_FFPROBE_TIMEOUT)
        data = json.loads(r.stdout)
        return float(data["format"]["duration"])
    except (ValueError, KeyError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return 0.0


def probe_audio_track_identity(media_path) -> dict:
    """返回 ffmpeg 默认解码的第一条音轨身份。"""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=index,codec_name,sample_rate,channels",
        "-of", "json",
        str(media_path),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=_FFPROBE_TIMEOUT,
        )
        payload = json.loads(result.stdout or "{}")
        stream = payload["streams"][0]
        identity = {
            "index": int(stream["index"]),
            "codec": str(stream["codec_name"]).lower(),
            "sample_rate": int(stream["sample_rate"]),
            "channels": int(stream["channels"]),
        }
    except (
        OSError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
    ) as exc:
        raise RuntimeError("无法确定实际音轨身份") from exc
    if result.returncode != 0 or not identity["codec"]:
        raise RuntimeError("无法确定实际音轨身份")
    return identity


def probe_media_streams(media_path: str) -> dict:
    """一次探测同时回答两问：**有没有声音、有没有画面**。

    P462／产品合同（`video-subtitle.md`「输入输出合同」）：

        有没有声音？ → 决定这活能不能干（没声音 = 什么都做不了）
        有没有画面？ → 决定成品长什么样（没画面 = 烧不了录）

        两件独立的事。**一次 ffprobe 同时给出两个答案，成本不变。**

    **不要按「是不是视频文件」分流**——那样会漏掉**有画面但没有声音的视频**
    （YouTube 画面/声音分开下载的 `*_video.mp4`）。真实事故 9110c09770 就是它：
    死在「音频提取失败」，用户只看到「我们没能识别出具体原因」。

    **扩展名不能当分流依据**：`.mp4` 可能没有视频流，`.mkv` 的 MIME 常常是空。
    扩展名只配在前端做提前提示。

    返回 `{"has_audio": bool|None, "has_video": bool|None}`。
    **探测不出来时是 `None`（不知道），不是 `False`（没有）**——
    把「未知」压成「没有」会让一个正常视频被判成不能处理，
    与本项目在采样时长上栽过的跟头是同一个形状。
    """
    # 同时取 `attached_pic`：**专辑封面不是画面**。
    # 很多 mp3 内嵌一张封面图，ffprobe 照样把它报成一路 `video` 流；
    # 只看 codec_type 会把「一段声音 + 一张图」判成视频，然后去烧录一张静态图。
    # ffmpeg 自己标了这件事——封面流 `attached_pic=1`，真视频流是 `0`。
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_type:stream_disposition=attached_pic",
        "-of", "csv=p=0",
        media_path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=_FFPROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"has_audio": None, "has_video": None}
    if result.returncode != 0:
        return {"has_audio": None, "has_video": None}
    has_audio = has_video = False
    for line in (result.stdout or "").splitlines():
        parts = [piece.strip() for piece in line.split(",")]
        if not parts or not parts[0]:
            continue
        if parts[0] == "audio":
            has_audio = True
        elif parts[0] == "video":
            # 取不到 disposition（老 ffprobe / 格式意外）时**按真画面算**：
            # 那是「不知道」，擅自当成封面会把正常视频的烧录悄悄取消掉。
            if (parts[1] if len(parts) > 1 else "0") != "1":
                has_video = True
    return {"has_audio": has_audio, "has_video": has_video}


def resolve_output_mode(requested: str, has_video) -> str:
    """按**实际有没有画面**收敛输出方式。

    产品裁决：「只要不是视频文件，最终生成的全部都是字幕文件。」
    用户即使选了烧录，只要输入没有画面也只给字幕文件
    ——**不报错、不打断、不弹窗问**。

    `has_video is None`（探测不出来）时**保持用户的选择**：
    擅自降级会把正常视频的烧录悄悄取消掉。
    """
    if has_video is False and requested in ("burn", "mux"):
        return "srt"
    return requested


def has_audio_stream(video_path: str) -> str:
    """Return has_audio, no_audio, or unknown when ffprobe cannot decide."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=index",
        "-of", "csv=p=0",
        video_path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=_FFPROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return "has_audio" if (result.stdout or "").strip() else "no_audio"
