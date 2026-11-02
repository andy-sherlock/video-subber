"""P443 第三轮返工：恢复 URL 任务时，程序自己把缺的音频补回来。

这不是新功能，是**回归修复**。P364（2026-07-14）记的旧合同就是：

> 同 URL 同设置重新提交 → 复用 error 任务目录 → 已下完的视频不重下（yt-dlp continuedl）
> → 音频重试下载成功才能续跑

P367 里还有真实任务 `754d144731` 的实测证据（2026-07-11）：
「下载恢复（2.7G 视频未重下、21M 音频补下）、转录、封装本身全部正常」。

后来 P373 加「一键继续」，把 `_resume` 并进了「只要视频文件在就跳过整个下载步骤」的早返回，
这个能力从此消失。屏幕上那句「重新提交时会尽量复用已下载部分」也从真话变成了谎话。

同时，「任务目录里有多个音频文件」不再一律失败：视频文件自己带着配对前缀
（`<前缀>_video.mp4` 对 `<前缀>_audio.m4a`），按前缀认领是事实，不是猜测。
只有同一前缀下出现多个有效音频，才是真歧义。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import worker  # noqa: E402

URL = "https://www.youtube.com/watch?v=SkQzl3kO0cA"
PREFIX = "Long Talk"
VIDEO_NAME = f"{PREFIX}_video.mp4"
AUDIO_NAME = f"{PREFIX}_audio.m4a"


@pytest.fixture
def job_dir(tmp_path):
    path = tmp_path / "80703166ff"
    path.mkdir()
    return path


@pytest.fixture
def progress(monkeypatch):
    emitted = []
    monkeypatch.setattr(worker, "stderr_progress", lambda *args: emitted.append(args))
    return emitted


@pytest.fixture
def errors(monkeypatch):
    captured = []
    monkeypatch.setattr(
        worker, "emit",
        lambda event_type, **kwargs: captured.append((event_type, kwargs)),
    )
    return captured


@pytest.fixture
def downloader(monkeypatch):
    """记录下载器被怎么调用的。默认两个入口都视为"不该被调"。"""
    calls = {"full": [], "audio": []}

    def _full(*args, **kwargs):
        calls["full"].append((args, kwargs))
        raise AssertionError("恢复时不应该调用整段下载（会重下视频）")

    def _audio(*args, **kwargs):
        calls["audio"].append((args, kwargs))
        raise AssertionError("本条用例不应该触发补下载")

    monkeypatch.setattr(
        "backend.core.video_downloader.is_ytdlp_available", lambda: True
    )
    monkeypatch.setattr("backend.core.video_downloader.download_video", _full)
    monkeypatch.setattr("backend.core.video_downloader.download_audio_only", _audio)
    return calls


def _allow_refill(monkeypatch, calls, result):
    """允许补下载，并记录它收到的参数。result 可以是路径字符串或 None。"""
    def _audio(url, output_dir, progress_callback=None, **kwargs):
        calls["audio"].append({"url": url, "output_dir": str(output_dir), **kwargs})
        if callable(result):
            return result(Path(output_dir))
        return result

    monkeypatch.setattr(
        "backend.core.video_downloader.download_audio_only", _audio
    )


def _audio_states(monkeypatch, mapping, default="no_audio"):
    monkeypatch.setattr(
        "backend.core.extractor.has_audio_stream",
        lambda path: mapping.get(Path(path).name, default),
    )


def _resume_config(**extra):
    config = {"source_url": URL, "_resume": True, "video_filename": "watch"}
    config.update(extra)
    return config


def _write(path: Path, data: bytes = b"media"):
    path.write_bytes(data)
    return path


# ── 按视频前缀认领已有音频 ────────────────────────────────────────


def test_audio_matching_the_video_prefix_is_claimed_without_downloading(
    job_dir, progress, errors, downloader, monkeypatch
):
    video = _write(job_dir / VIDEO_NAME)
    audio = _write(job_dir / AUDIO_NAME)
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"}, default="has_audio")

    config = _resume_config()
    assert worker._download_source_if_url(config, job_dir, str(video)) == str(video)

    assert config["dl_audio_path"] == str(audio)
    assert downloader["audio"] == [] and downloader["full"] == []


def test_audio_with_an_unrelated_prefix_is_ignored_not_deleted(
    job_dir, progress, errors, downloader, monkeypatch
):
    """作者改过标题留下的旧音频，不该让整个任务失败——它跟当前视频配不上对。"""
    video = _write(job_dir / VIDEO_NAME)
    mine = _write(job_dir / AUDIO_NAME)
    stranger = _write(job_dir / "Renamed Title_audio.m4a")
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"}, default="has_audio")

    config = _resume_config()
    worker._download_source_if_url(config, job_dir, str(video))

    assert config["dl_audio_path"] == str(mine), "没有按视频前缀认领配对的音频"
    assert stranger.exists(), "不配对的音频文件不得被删除"
    assert not any(kind == "error" for kind, _ in errors)


def test_two_valid_audios_under_the_same_prefix_still_fail_safely(
    job_dir, progress, errors, downloader, monkeypatch
):
    """同一前缀下出现两个有效音频，才是真歧义。猜错=给用户配错声音。"""
    video = _write(job_dir / VIDEO_NAME)
    _write(job_dir / AUDIO_NAME)
    _write(job_dir / f"{PREFIX}_audio.webm")
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"}, default="has_audio")

    config = _resume_config()
    with pytest.raises(SystemExit):
        worker._download_source_if_url(config, job_dir, str(video))

    assert "dl_audio_path" not in config
    assert errors[-1][1]["failure_kind"] == "url_audio_ambiguous"
    assert downloader["audio"] == []


def test_snapshot_video_without_a_prefix_scans_every_candidate(
    job_dir, progress, errors, downloader, monkeypatch
):
    """P367 的本地快照 `input.mp4` 推不出前缀，退回扫描全部候选。"""
    video = _write(job_dir / "input.mp4")
    audio = _write(job_dir / AUDIO_NAME)
    _audio_states(monkeypatch, {"input.mp4": "no_audio"}, default="has_audio")

    config = _resume_config()
    worker._download_source_if_url(config, job_dir, str(video))
    assert config["dl_audio_path"] == str(audio)


# ── 缺音频时只补下载音频 ──────────────────────────────────────────


def test_missing_audio_is_refilled_without_touching_the_video(
    job_dir, progress, errors, downloader, monkeypatch
):
    """P364 旧合同回归：视频不重下，只把音频补回来。"""
    video = _write(job_dir / VIDEO_NAME, b"a" * 4096)
    before = (video.read_bytes(), video.stat().st_mtime_ns)
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"}, default="has_audio")
    _allow_refill(
        monkeypatch, downloader,
        lambda d: str(_write(d / AUDIO_NAME)),
    )

    config = _resume_config()
    assert worker._download_source_if_url(config, job_dir, str(video)) == str(video)

    assert len(downloader["audio"]) == 1, "缺音频时没有触发补下载"
    assert downloader["full"] == [], "补音频不得走整段下载，那会重下视频"
    assert config["dl_audio_path"] == str(job_dir / AUDIO_NAME)
    assert (video.read_bytes(), video.stat().st_mtime_ns) == before, "视频文件被动过了"
    assert not any(kind == "error" for kind, _ in errors)


def test_refill_downloads_into_the_task_directory(
    job_dir, progress, errors, downloader, monkeypatch
):
    video = _write(job_dir / VIDEO_NAME)
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"}, default="has_audio")
    _allow_refill(
        monkeypatch, downloader, lambda d: str(_write(d / AUDIO_NAME))
    )

    worker._download_source_if_url(_resume_config(), job_dir, str(video))
    assert Path(downloader["audio"][0]["output_dir"]) == job_dir
    assert downloader["audio"][0]["url"] == URL


def test_video_with_embedded_audio_never_triggers_a_refill(
    job_dir, progress, errors, downloader, monkeypatch
):
    video = _write(job_dir / VIDEO_NAME)
    _audio_states(monkeypatch, {VIDEO_NAME: "has_audio"})

    config = _resume_config()
    assert worker._download_source_if_url(config, job_dir, str(video)) == str(video)
    assert "dl_audio_path" not in config
    assert downloader["audio"] == [] and downloader["full"] == []


# ── 损坏残留挡住下载 ──────────────────────────────────────────────


def test_corrupt_leftover_forces_a_fresh_audio_download(
    job_dir, progress, errors, downloader, monkeypatch
):
    """yt-dlp 的 continuedl 会把目标路径上的残留当成「已下载完成」直接跳过。

    实测（yt_dlp/downloader/common.py:434-455）：`continuedl=True` + 目标文件存在
    → 立刻返回成功、连网络都不碰。所以残留无效时，这一次音频下载必须关掉 continuedl，
    否则补下载拿回来的还是那个坏文件。
    """
    video = _write(job_dir / VIDEO_NAME)
    corrupt = _write(job_dir / AUDIO_NAME, b"corrupted")
    _audio_states(
        monkeypatch, {VIDEO_NAME: "no_audio", AUDIO_NAME: "unknown"},
    )

    def _refilled(d):
        # 补下载成功后这个路径变成有效音频
        _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"}, default="has_audio")
        return str(corrupt)

    _allow_refill(monkeypatch, downloader, _refilled)

    config = _resume_config()
    worker._download_source_if_url(config, job_dir, str(video))

    assert downloader["audio"][0].get("force_fresh") is True, (
        "残留无效却仍按断点续传下载，拿回来的会是同一个坏文件"
    )
    assert config["dl_audio_path"] == str(corrupt)


def test_clean_directory_keeps_resumable_download(
    job_dir, progress, errors, downloader, monkeypatch
):
    """没有残留时不关 continuedl——真正的半截 .part 还应该能续传。"""
    video = _write(job_dir / VIDEO_NAME)
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"}, default="has_audio")
    _allow_refill(monkeypatch, downloader, lambda d: str(_write(d / AUDIO_NAME)))

    worker._download_source_if_url(_resume_config(), job_dir, str(video))
    assert downloader["audio"][0].get("force_fresh") is False


# ── 补下载的结果必须重新校验 ──────────────────────────────────────


def test_refilled_audio_is_revalidated_before_it_is_trusted(
    job_dir, progress, errors, downloader, monkeypatch
):
    """下载器返回一个路径 ≠ 那个文件可用。"""
    video = _write(job_dir / VIDEO_NAME)
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio", AUDIO_NAME: "no_audio"})
    _allow_refill(
        monkeypatch, downloader, lambda d: str(_write(d / AUDIO_NAME, b"junk"))
    )

    config = _resume_config()
    with pytest.raises(SystemExit):
        worker._download_source_if_url(config, job_dir, str(video))

    assert "dl_audio_path" not in config
    assert errors[-1][1]["failure_kind"] == "url_audio_missing"


def test_refill_failure_produces_the_specific_terminal_error(
    job_dir, progress, errors, downloader, monkeypatch
):
    video = _write(job_dir / VIDEO_NAME)
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"})
    _allow_refill(monkeypatch, downloader, None)

    config = _resume_config()
    with pytest.raises(SystemExit):
        worker._download_source_if_url(config, job_dir, str(video))

    assert [kind for kind, _ in errors][-1] == "error"
    assert errors[-1][1]["failure_kind"] == "url_audio_missing"
    assert "音频" in errors[-1][1]["message"]


def test_refilled_audio_is_rediscoverable_after_another_restart(
    job_dir, progress, errors, downloader, monkeypatch
):
    """补回来的音频落在任务目录里，下次重启还能按前缀认领，不用再下一次。"""
    video = _write(job_dir / VIDEO_NAME)
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"}, default="has_audio")
    _allow_refill(monkeypatch, downloader, lambda d: str(_write(d / AUDIO_NAME)))

    first = _resume_config()
    worker._download_source_if_url(first, job_dir, str(video))
    assert len(downloader["audio"]) == 1

    # 第二次重启：内存全空，只剩磁盘上的文件
    monkeypatch.setattr(
        "backend.core.video_downloader.download_audio_only",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("已经有有效音频了，不该再下一次")
        ),
    )
    second = _resume_config()
    worker._download_source_if_url(second, job_dir, str(video))
    assert second["dl_audio_path"] == first["dl_audio_path"]


def test_first_download_path_does_not_refill(
    job_dir, progress, errors, downloader, monkeypatch
):
    """首次下载刚刚失败过一次，立刻再下一次没有意义——补下载只属于恢复路径。"""
    video = _write(job_dir / VIDEO_NAME)
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"})
    monkeypatch.setattr(
        "backend.core.video_downloader.download_video",
        lambda *a, **k: (str(video), None),
    )

    config = {"source_url": URL, "video_filename": "watch"}
    with pytest.raises(SystemExit):
        worker._download_source_if_url(config, job_dir, None)

    assert downloader["audio"] == []
    assert errors[-1][1]["failure_kind"] == "url_audio_missing"


# ── force_fresh 必须真的翻到下载器的行为上 ──────────────────────────


def test_force_fresh_actually_disables_yt_dlp_resume(monkeypatch, tmp_path):
    """光传一个 force_fresh 参数不算数，它必须真的改到 yt-dlp 的 continuedl。

    这一层是整条修复的承重点：实测 `yt_dlp/downloader/common.py:434-455`——
    `continuedl=True` 且目标路径上有文件时，下载器立刻报告「已下载完成」，连网络都
    不碰。视频靠这条规则免于重下；而无效的音频残留也会靠同一条规则被原样留下。
    """
    from backend.core import video_downloader as vd

    seen = []

    class _FakeYDL:
        def __init__(self, opts):
            seen.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            return {"title": PREFIX, "ext": "m4a"}

        def prepare_filename(self, info):
            return str(tmp_path / AUDIO_NAME)

    import types
    fake = types.ModuleType("yt_dlp")
    fake.YoutubeDL = _FakeYDL
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)
    monkeypatch.setattr(vd, "_detect_system_proxy", lambda: None)

    vd.download_audio_only(URL, str(tmp_path))
    vd.download_audio_only(URL, str(tmp_path), force_fresh=True)

    assert seen[0]["continuedl"] is True, "默认必须保持可续传，否则视频会被重下"
    assert seen[1]["continuedl"] is False, (
        "force_fresh 没有关掉 continuedl，补下载会把无效残留当成已完成"
    )
    # 两次都必须是音频格式，且不碰视频
    assert all(o["format"] == vd._AUDIO_FORMAT for o in seen)
    assert all(o["outtmpl"].endswith("_audio.%(ext)s") for o in seen)
