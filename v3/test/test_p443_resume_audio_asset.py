"""P443：关掉 APP 再打开、点一键继续之后，独立音频不能丢。

真实任务 80703166ff：YouTube 高清视频的画面和声音是分开下载的，任务目录里同时存在
`*_video.mp4`（只有画面）和 `*_audio.m4a`（只有声音）。重启恢复时程序只复用了视频，
从来没有去找音频；后面的声音地图和最终封装因此拿到一个没有声音的文件，最终成品只有
视频流和字幕流。

合同：URL 任务无论首次运行还是恢复运行，都必须走同一道「音频资产齐备」检查。
拿不到有效音频、视频本身也没有音轨时，必须报错停止，不能静默交付无声成品。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import worker  # noqa: E402

URL = "https://www.youtube.com/watch?v=SkQzl3kO0cA"
VIDEO_NAME = "30 Minute Conversation_video.mp4"
AUDIO_NAME = "30 Minute Conversation_audio.m4a"


@pytest.fixture
def job_dir(tmp_path):
    path = tmp_path / "80703166ff"
    path.mkdir()
    return path


@pytest.fixture
def no_download(monkeypatch):
    """封死**所有**下载入口。

    测试永远不许真的连网络下载。第三轮返工新增了 `download_audio_only()` 这个入口，
    只挡住 `download_video()` 已经不够——漏掉一个，测试就会静默地去下真视频。
    """
    def _boom(*args, **kwargs):
        raise AssertionError("恢复时不应该重新下载已经存在的视频或音频")

    monkeypatch.setattr("backend.core.video_downloader.download_video", _boom)
    monkeypatch.setattr("backend.core.video_downloader.download_audio_only", _boom)
    monkeypatch.setattr(
        "backend.core.video_downloader.is_ytdlp_available", lambda: True
    )


@pytest.fixture
def refill_fails(monkeypatch):
    """允许触发补下载，但让它拿不到音频——用于验证"补也补不回来"的失败路径。"""
    calls = []

    def _audio(url, output_dir, progress_callback=None, **kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(
        "backend.core.video_downloader.download_audio_only", _audio
    )
    return calls


@pytest.fixture
def progress(monkeypatch):
    emitted = []
    monkeypatch.setattr(worker, "stderr_progress", lambda *args: emitted.append(args))
    return emitted


@pytest.fixture
def errors(monkeypatch):
    """捕获 Worker 的终态错误事件。

    复审返工后失败走的是 `emit("error", ...)` 而不是 `stderr_progress("error", ...)`，
    因为后者固定发 `type="progress"`，父进程根本不当终态处理，退出后具体原因会被
    「后台处理已结束，但没有找到可确认的完成结果」覆盖掉。
    """
    captured = []
    monkeypatch.setattr(
        worker, "emit",
        lambda event_type, **kwargs: captured.append((event_type, kwargs)),
    )
    return captured


def _audio_states(monkeypatch, mapping, default="no_audio"):
    """按文件名决定 ffprobe 的结果，模拟真实的 video-only / audio-only 文件。"""
    def _fake(path):
        return mapping.get(Path(path).name, default)

    monkeypatch.setattr("backend.core.extractor.has_audio_stream", _fake)


def _resume_config(**extra):
    config = {"source_url": URL, "_resume": True, "video_filename": "watch"}
    config.update(extra)
    return config


def _write(path: Path, data: bytes = b"media"):
    path.write_bytes(data)
    return path


# ── 核心：P443 本体 ────────────────────────────────────────────────


def test_resume_restores_the_separate_audio_asset(
    job_dir, no_download, progress, monkeypatch
):
    """一键继续时必须找回独立音频，而不是把 video-only 文件当成音频来源。"""
    video = _write(job_dir / VIDEO_NAME)
    audio = _write(job_dir / AUDIO_NAME)
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio", AUDIO_NAME: "has_audio"})

    config = _resume_config()
    assert worker._download_source_if_url(config, job_dir, str(video)) == str(video)
    assert config.get("dl_audio_path") == str(audio), (
        "恢复后没有拿回独立音频，后面的声音地图和封装会退回到只有画面的文件"
    )


def test_resume_without_any_audio_source_refuses_to_continue(
    job_dir, no_download, refill_fails, progress, errors, monkeypatch
):
    """P443 的错误放行点：video-only 文件不等于「原视频本来就没声音」。"""
    video = _write(job_dir / VIDEO_NAME)
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"})

    with pytest.raises(SystemExit):
        worker._download_source_if_url(_resume_config(), job_dir, str(video))

    assert [kind for kind, _ in errors] == ["error"], (
        "失败没有走终态错误通道，具体原因会在退出后被笼统文案覆盖"
    )
    assert errors[-1][1]["failure_kind"] == "url_audio_missing"


def test_resume_failure_message_names_a_next_step(
    job_dir, no_download, refill_fails, progress, errors, monkeypatch
):
    """错误消息要说下一步做什么。

    注意：这一条只证明消息里有动作。「用户照做真的能恢复」由
    `test_p443_multi_candidate_recovery.py` 走真实 API 证明——光断言文案含某几个字，
    正是上一轮把「失败消息可操作」写成过度表述的原因。
    """
    video = _write(job_dir / VIDEO_NAME)
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"})

    with pytest.raises(SystemExit):
        worker._download_source_if_url(_resume_config(), job_dir, str(video))

    message = errors[-1][1]["message"]
    assert "重新提交" in message, f"错误消息没有告诉用户下一步做什么：{message}"


# ── 候选查找的边界 ────────────────────────────────────────────────


def test_resume_accepts_audio_containers_other_than_m4a(
    job_dir, no_download, progress, monkeypatch
):
    """不许硬编码只认 .m4a——YouTube 也会给 webm/opus。"""
    video = _write(job_dir / VIDEO_NAME)
    audio = _write(job_dir / "30 Minute Conversation_audio.webm")
    _audio_states(
        monkeypatch,
        {VIDEO_NAME: "no_audio", "30 Minute Conversation_audio.webm": "has_audio"},
    )

    config = _resume_config()
    worker._download_source_if_url(config, job_dir, str(video))
    assert config.get("dl_audio_path") == str(audio)


def test_resume_claims_the_audio_that_pairs_with_this_video(
    job_dir, no_download, progress, errors, monkeypatch
):
    """作者改过标题留下的旧音频，不该让整个任务失败。

    第三轮返工前这里是「目录里有两个音频 → 一律失败」。但视频文件自己带着配对前缀
    （`<前缀>_video.mp4` 配 `<前缀>_audio.m4a`），按前缀认领是**事实**不是猜测。
    真歧义（同一前缀下两个有效音频）的用例在 `test_p443_audio_refill.py` 里。
    """
    video = _write(job_dir / VIDEO_NAME)
    mine = _write(job_dir / AUDIO_NAME)
    stranger = _write(job_dir / "Renamed Title_audio.m4a")
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"}, default="has_audio")

    config = _resume_config()
    worker._download_source_if_url(config, job_dir, str(video))

    assert config["dl_audio_path"] == str(mine)
    assert stranger.exists(), "配不上对的音频文件不得被删除"
    assert not any(kind == "error" for kind, _ in errors)


def test_resume_ignores_half_downloaded_part_files(
    job_dir, no_download, progress, monkeypatch
):
    """yt-dlp 中断会留下 *.part 半成品，它不是可用资产。"""
    video = _write(job_dir / VIDEO_NAME)
    audio = _write(job_dir / AUDIO_NAME)
    _write(job_dir / "30 Minute Conversation_audio.m4a.part")
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"}, default="has_audio")

    config = _resume_config()
    worker._download_source_if_url(config, job_dir, str(video))
    assert config.get("dl_audio_path") == str(audio), (
        "半成品 .part 文件被当成了候选，导致要么误用要么误判多候选"
    )


def test_resume_does_not_mistake_the_transcription_cache_for_the_audio_asset(
    job_dir, no_download, refill_fails, progress, monkeypatch
):
    """cache/audio.wav 是转录衍生物，不能冒充最终封装需要的原始音频。"""
    video = _write(job_dir / VIDEO_NAME)
    cache = job_dir / "cache" / "a1e2585a2e59"
    cache.mkdir(parents=True)
    _write(cache / "audio.wav")
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"}, default="has_audio")

    config = _resume_config()
    with pytest.raises(SystemExit):
        worker._download_source_if_url(config, job_dir, str(video))

    assert config.get("dl_audio_path") is None


def test_resume_rejects_a_candidate_that_escapes_the_job_directory(
    job_dir, tmp_path, no_download, refill_fails, progress, monkeypatch
):
    """软链接指向任务目录外面的文件时，不得采用。"""
    video = _write(job_dir / VIDEO_NAME)
    outside = _write(tmp_path / "outside_audio.m4a")
    link = job_dir / AUDIO_NAME
    link.symlink_to(outside)
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"}, default="has_audio")

    config = _resume_config()
    with pytest.raises(SystemExit):
        worker._download_source_if_url(config, job_dir, str(video))

    assert config.get("dl_audio_path") is None


def test_resume_rejects_a_candidate_without_a_real_audio_stream(
    job_dir, no_download, refill_fails, progress, monkeypatch
):
    """名字对不代表内容对，必须真的探一次。"""
    video = _write(job_dir / VIDEO_NAME)
    _write(job_dir / AUDIO_NAME, b"corrupted")
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio", AUDIO_NAME: "unknown"})

    config = _resume_config()
    with pytest.raises(SystemExit):
        worker._download_source_if_url(config, job_dir, str(video))

    assert config.get("dl_audio_path") is None


# ── 不能被打坏的既有路径 ──────────────────────────────────────────


def test_resume_url_video_with_embedded_audio_still_continues(
    job_dir, no_download, progress, monkeypatch
):
    """有些 URL 下下来就是一个自带声音的文件，没有独立音频是正常的。"""
    combined = _write(job_dir / "combined_video.mp4")
    _audio_states(monkeypatch, {"combined_video.mp4": "has_audio"})

    config = _resume_config()
    assert worker._download_source_if_url(
        config, job_dir, str(combined)
    ) == str(combined)
    assert "dl_audio_path" not in config


def test_local_file_resume_is_untouched_by_the_url_audio_contract(
    job_dir, no_download, progress, errors, monkeypatch
):
    """本地视频任务没有 source_url，整条 URL 音频合同都不该介入。"""
    silent = _write(job_dir / "input.mp4")
    _audio_states(monkeypatch, {}, default="no_audio")

    config = {"_resume": True, "video_filename": "input.mp4"}
    assert worker._download_source_if_url(
        config, job_dir, str(silent)
    ) == str(silent)
    assert "dl_audio_path" not in config
    assert not any(call[0] == "error" for call in progress)
    assert not any(kind == "error" for kind, _ in errors)


# P516：「继续时把占位名同步成真实文件名」已删除——名字从原视频地址现算，不再改名。


def test_partial_accept_path_also_gets_the_audio_asset(
    job_dir, no_download, progress, monkeypatch
):
    """接受部分结果一样要走到最终封装，一样需要音频。"""
    video = _write(job_dir / VIDEO_NAME)
    audio = _write(job_dir / AUDIO_NAME)
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"}, default="has_audio")

    config = {"source_url": URL, "_accept_partial": True, "video_filename": "watch"}
    worker._download_source_if_url(config, job_dir, str(video))
    assert config.get("dl_audio_path") == str(audio)


# ── 不建第二套状态系统 ────────────────────────────────────────────


def test_recovery_adds_no_new_persisted_state_field(
    job_dir, no_download, progress, monkeypatch
):
    """事实就是任务目录里那个音频文件本身，不新增任何持久化字段。"""
    video = _write(job_dir / VIDEO_NAME)
    _write(job_dir / AUDIO_NAME)
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"}, default="has_audio")

    config = _resume_config()
    before = set(config)
    worker._download_source_if_url(config, job_dir, str(video))

    assert set(config) - before == {"dl_audio_path"}, (
        "恢复过程写入了 dl_audio_path 以外的新字段，等于建了第二套状态"
    )
    assert not (job_dir / "meta.json").exists()
    assert not list(job_dir.glob("*asset*.json"))


def test_resume_and_first_download_log_distinguishable_events(
    job_dir, no_download, progress, monkeypatch, caplog
):
    """恢复接上音频这件事必须在日志里看得见，否则下次排查又是一片空白。"""
    import logging

    video = _write(job_dir / VIDEO_NAME)
    _write(job_dir / AUDIO_NAME)
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"}, default="has_audio")

    with caplog.at_level(logging.INFO, logger="worker"):
        worker._download_source_if_url(_resume_config(), job_dir, str(video))

    text = caplog.text
    assert "P443 分轨音频已从任务目录认领" in text, (
        "恢复路径没有留下音频接上的证据"
    )
    assert "P198 分轨下载完成" not in text, (
        "恢复时没有下载任何东西，不能沿用下载事件的日志标记"
    )


# ── A7/A10：三个下游消费者必须拿到同一个权威音频，成品无音轨必须被拒 ──


class _FakeCheckpoint:
    def __init__(self):
        self.saved = []

    def save(self, stage, meta=None, **kwargs):
        self.saved.append((stage, meta))


def test_extract_audio_consumes_the_restored_asset_not_the_video(
    job_dir, no_download, progress, monkeypatch, tmp_path
):
    """A7 第一站：抽音频必须用恢复回来的独立音频，不是只有画面的视频。"""
    video = _write(job_dir / VIDEO_NAME)
    audio = _write(job_dir / AUDIO_NAME)
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"}, default="has_audio")
    monkeypatch.setattr(
        "backend.core.extractor.get_video_resolution", lambda path: (1920, 1080)
    )
    monkeypatch.setattr(worker, "emit", lambda *args, **kwargs: None)
    seen = []
    monkeypatch.setattr(worker, "extract_audio", lambda src, dst: seen.append(src))

    config = _resume_config()
    worker._download_source_if_url(config, job_dir, str(video))
    _, _, state = worker._step_extract_audio(
        "start", str(video), str(tmp_path / "audio.wav"), config, _FakeCheckpoint()
    )

    assert seen == [str(audio)], f"抽音频拿到的不是权威音频来源：{seen}"
    assert state == "has_audio", (
        "源音轨状态被判成无声，最终校验就会放行一个无声成品"
    )


def test_mux_and_burn_both_receive_the_restored_asset(
    job_dir, no_download, progress, monkeypatch, tmp_path
):
    """A7 最后一站：封装和烧录两条路都要拿到同一个音频。"""
    video = _write(job_dir / VIDEO_NAME)
    audio = _write(job_dir / AUDIO_NAME)
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"}, default="has_audio")
    mux_calls, burn_calls = [], []
    monkeypatch.setattr(
        worker, "mux_subtitles",
        lambda *a, audio_path=None, **k: mux_calls.append(audio_path),
    )
    monkeypatch.setattr(
        worker, "burn_subtitles",
        lambda *a, audio_path=None, **k: burn_calls.append(audio_path),
    )
    monkeypatch.setattr(worker, "is_valid_media_file", lambda *a, **k: False)

    config = _resume_config()
    worker._download_source_if_url(config, job_dir, str(video))
    worker._step_burn_or_mux(
        str(video), str(tmp_path / "s.ass"), str(tmp_path / "s.srt"),
        str(tmp_path / "out_zh.mp4"), str(tmp_path / "out_zh_sub.mp4"),
        True, True, config,
    )

    assert mux_calls == [str(audio)], f"封装没有拿到权威音频：{mux_calls}"
    assert burn_calls == [str(audio)], f"烧录没有拿到权威音频：{burn_calls}"


def test_silent_product_is_rejected_when_the_task_had_audio(monkeypatch, tmp_path):
    """A10：首次确认有音频的任务，成品没有音频流必须失败，不能标记完成。"""
    from backend.core.subtitler import SubtitleOutputError

    product = _write(tmp_path / "out_zh_sub.mp4")
    monkeypatch.setattr(
        "backend.core.extractor.has_audio_stream", lambda path: "no_audio"
    )

    with pytest.raises(SubtitleOutputError) as excinfo:
        worker._validate_output_audio("has_audio", "mux", str(product))

    assert excinfo.value.failure_kind == "output_audio_missing"


# ── 复审返工：首次下载的音频也必须过同一套有效性检查 ──────────────
#
# 上一轮 `_require_url_audio_asset()` 名义上被首次下载和恢复共用，但三道检查
# （在任务目录内 / 存在且非空 / ffprobe 确认有音频流）只作用在恢复发现的候选上。
# 首次下载只要 downloader 返回一个非空字符串就直接写 dl_audio_path 并记
# 「P198 分轨下载完成」，一个字节都没验过。


def _first_download(monkeypatch, job_dir, video_name, audio_path):
    """把 download_video 的返回值换掉，其余走真实代码。"""
    video = job_dir / video_name
    if not video.exists():
        _write(video)
    monkeypatch.setattr(
        "backend.core.video_downloader.is_ytdlp_available", lambda: True
    )
    monkeypatch.setattr(
        "backend.core.video_downloader.download_video",
        lambda *a, **k: (str(video), audio_path),
    )
    return video


def test_first_download_rejects_an_audio_path_that_does_not_exist(
    job_dir, progress, monkeypatch, caplog
):
    import logging

    video = _first_download(
        monkeypatch, job_dir, VIDEO_NAME, str(job_dir / "ghost_audio.m4a")
    )
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"}, default="has_audio")

    config = {"source_url": URL, "video_filename": "watch"}
    with caplog.at_level(logging.INFO, logger="worker"):
        with pytest.raises(SystemExit):
            worker._download_source_if_url(config, job_dir, None)

    assert "dl_audio_path" not in config
    assert "P198 分轨下载完成" not in caplog.text, (
        "音频文件根本不存在，却记了「分轨下载完成」"
    )


def test_first_download_rejects_an_empty_audio_file(
    job_dir, progress, monkeypatch, caplog
):
    import logging

    empty = job_dir / AUDIO_NAME
    empty.write_bytes(b"")
    _first_download(monkeypatch, job_dir, VIDEO_NAME, str(empty))
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"}, default="has_audio")

    config = {"source_url": URL, "video_filename": "watch"}
    with caplog.at_level(logging.INFO, logger="worker"):
        with pytest.raises(SystemExit):
            worker._download_source_if_url(config, job_dir, None)

    assert "dl_audio_path" not in config
    assert "P198 分轨下载完成" not in caplog.text


def test_first_download_rejects_an_audio_path_outside_the_job_directory(
    job_dir, tmp_path, progress, monkeypatch, caplog
):
    import logging

    outside = _write(tmp_path / "elsewhere_audio.m4a")
    _first_download(monkeypatch, job_dir, VIDEO_NAME, str(outside))
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"}, default="has_audio")

    config = {"source_url": URL, "video_filename": "watch"}
    with caplog.at_level(logging.INFO, logger="worker"):
        with pytest.raises(SystemExit):
            worker._download_source_if_url(config, job_dir, None)

    assert "dl_audio_path" not in config
    assert "P198 分轨下载完成" not in caplog.text


def test_first_download_rejects_audio_without_a_real_stream(
    job_dir, progress, monkeypatch, caplog
):
    """容器在、文件非空，但 ffprobe 说里面没有音频流。"""
    import logging

    broken = _write(job_dir / AUDIO_NAME, b"not really audio")
    _first_download(monkeypatch, job_dir, VIDEO_NAME, str(broken))
    _audio_states(
        monkeypatch, {VIDEO_NAME: "no_audio", AUDIO_NAME: "no_audio"}
    )

    config = {"source_url": URL, "video_filename": "watch"}
    with caplog.at_level(logging.INFO, logger="worker"):
        with pytest.raises(SystemExit):
            worker._download_source_if_url(config, job_dir, None)

    assert "dl_audio_path" not in config
    assert "P198 分轨下载完成" not in caplog.text


def test_first_download_rejects_audio_ffprobe_cannot_read(
    job_dir, progress, monkeypatch, caplog
):
    """损坏到 ffprobe 都读不出来（unknown）也不能放行。"""
    import logging

    broken = _write(job_dir / AUDIO_NAME, b"\x00\x01corrupt")
    _first_download(monkeypatch, job_dir, VIDEO_NAME, str(broken))
    _audio_states(
        monkeypatch, {VIDEO_NAME: "no_audio", AUDIO_NAME: "unknown"}
    )

    config = {"source_url": URL, "video_filename": "watch"}
    with caplog.at_level(logging.INFO, logger="worker"):
        with pytest.raises(SystemExit):
            worker._download_source_if_url(config, job_dir, None)

    assert "dl_audio_path" not in config
    assert "P198 分轨下载完成" not in caplog.text


def test_first_download_with_invalid_audio_falls_back_to_embedded_track(
    job_dir, progress, errors, monkeypatch
):
    """音频文件坏了，但视频本身带音轨——这时应该继续，不是硬失败。"""
    broken = _write(job_dir / AUDIO_NAME, b"corrupt")
    _first_download(monkeypatch, job_dir, VIDEO_NAME, str(broken))
    _audio_states(
        monkeypatch, {VIDEO_NAME: "has_audio", AUDIO_NAME: "unknown"}
    )

    config = {"source_url": URL, "video_filename": "watch"}
    result = worker._download_source_if_url(config, job_dir, None)

    assert result == str(job_dir / VIDEO_NAME)
    assert "dl_audio_path" not in config
    assert not any(call[0] == "error" for call in progress)
    assert not any(kind == "error" for kind, _ in errors)


def test_first_download_with_valid_audio_still_records_the_download_event(
    job_dir, progress, monkeypatch, caplog
):
    """正常路径不能被这次收紧打坏。"""
    import logging

    good = _write(job_dir / AUDIO_NAME)
    _first_download(monkeypatch, job_dir, VIDEO_NAME, str(good))
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"}, default="has_audio")

    config = {"source_url": URL, "video_filename": "watch"}
    with caplog.at_level(logging.INFO, logger="worker"):
        worker._download_source_if_url(config, job_dir, None)

    assert config["dl_audio_path"] == str(good)
    assert "P198 分轨下载完成" in caplog.text
    assert "P443 分轨音频已从任务目录认领" not in caplog.text
