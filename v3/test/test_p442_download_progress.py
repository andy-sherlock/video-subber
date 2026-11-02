"""P442：下载百分比不能倒退。

yt-dlp 的 total_bytes_estimate 是会变的预计总大小。已下载字节增加、估算总量增加得
更多时，downloaded/total 算出来的百分比会下降，界面上就出现 9% → 8%。

修复只在下载进度转换层：每一路各自记住见过的最大值，下载完成前上限 99%。
分段下载优先用分段完成比例，普通单文件继续用真实字节比例。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.core.video_downloader import _make_progress_hook  # noqa: E402


def _collect(kind="video"):
    calls = []
    hook = _make_progress_hook(lambda msg: calls.append(msg), kind=kind)
    return hook, calls


def _percents(calls):
    return [call["download_percent"] for call in calls]


def test_current_formula_reproduces_the_9_to_8_regression():
    """真实任务复现：下载量 90→96，估算总量 1000→1200，旧公式输出 9%→8%。"""
    hook, calls = _collect()
    hook({"status": "downloading", "downloaded_bytes": 90, "total_bytes_estimate": 1000})
    hook({"status": "downloading", "downloaded_bytes": 96, "total_bytes_estimate": 1200})

    percents = _percents(calls)
    assert percents == sorted(percents), (
        f"下载量一直在涨，显示的百分比却倒退了：{percents}"
    )


def test_video_progress_is_monotonic_across_many_estimate_changes():
    hook, calls = _collect(kind="video")
    samples = [
        (90, 1000), (96, 1200), (150, 1300), (200, 2000),
        (400, 2100), (500, 2000), (900, 1900),
    ]
    for downloaded, total in samples:
        hook({
            "status": "downloading",
            "downloaded_bytes": downloaded,
            "total_bytes_estimate": total,
        })

    percents = _percents(calls)
    assert percents == sorted(percents), f"视频下载百分比倒退：{percents}"


def test_audio_progress_is_monotonic_too():
    hook, calls = _collect(kind="audio")
    hook({"status": "downloading", "downloaded_bytes": 90, "total_bytes_estimate": 1000})
    hook({"status": "downloading", "downloaded_bytes": 96, "total_bytes_estimate": 1200})

    percents = _percents(calls)
    assert percents == sorted(percents), f"音频下载百分比倒退：{percents}"


def test_video_and_audio_keep_independent_counters():
    """Q6②：切成两路之后，每一路必须单独记账，不能互相盖掉。"""
    video_hook, video_calls = _collect(kind="video")
    audio_hook, audio_calls = _collect(kind="audio")

    video_hook({"status": "downloading", "downloaded_bytes": 900, "total_bytes": 1000})
    audio_hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 1000})

    assert _percents(video_calls) == [90]
    assert _percents(audio_calls) == [5], (
        "音频那一路被视频的进度污染了，说明两路共用了同一个计数器"
    )


def test_fragmented_download_uses_fragment_ratio():
    """分段下载没有可靠的总字节数，用已完成分段数算才准。"""
    hook, calls = _collect()
    hook({
        "status": "downloading",
        "downloaded_bytes": 1,
        "total_bytes_estimate": 10 ** 9,
        "fragment_index": 50,
        "fragment_count": 100,
    })

    assert _percents(calls) == [50]


def test_plain_single_file_download_still_uses_real_bytes():
    hook, calls = _collect()
    hook({"status": "downloading", "downloaded_bytes": 250, "total_bytes": 1000})

    assert _percents(calls) == [25]


def test_percent_is_capped_at_99_until_download_finishes():
    """下载真正结束前不许显示 100%，避免用户以为卡住了。"""
    hook, calls = _collect()
    hook({"status": "downloading", "downloaded_bytes": 1000, "total_bytes": 1000})

    assert _percents(calls) == [99]


def test_percent_never_disappears_once_it_has_been_shown():
    """复审返工：数字不倒退还不够，数字也不能消失。

    旧实现在总大小临时未知时返回 None，Worker 会拼出不带数字的「正在下载视频」，
    用户看到的是 50% → 百分比消失 → 70%。那仍然是 P442 说的闪动。
    """
    hook, calls = _collect()
    hook({"status": "downloading", "downloaded_bytes": 500, "total_bytes": 1000})
    hook({"status": "downloading", "downloaded_bytes": 600})
    hook({"status": "downloading", "downloaded_bytes": 700, "total_bytes": 1000})

    assert _percents(calls) == [50, 50, 70], (
        f"已经显示过百分比之后又消失了：{_percents(calls)}"
    )


def test_percent_stays_absent_until_the_first_valid_reading():
    """一次都没算出来过时，不显示数字是对的——不能凭空编一个 0%。"""
    hook, calls = _collect()
    hook({"status": "downloading", "downloaded_bytes": 600})
    hook({"status": "downloading", "downloaded_bytes": 700, "total_bytes": 1000})

    assert _percents(calls) == [None, 70]


def test_unknown_total_does_not_reset_the_counter_for_the_other_stream():
    """一路进入未知总量，不能把另一路的记账带偏。"""
    video_hook, video_calls = _collect(kind="video")
    audio_hook, audio_calls = _collect(kind="audio")

    video_hook({"status": "downloading", "downloaded_bytes": 800, "total_bytes": 1000})
    audio_hook({"status": "downloading", "downloaded_bytes": 100})
    audio_hook({"status": "downloading", "downloaded_bytes": 300, "total_bytes": 1000})
    video_hook({"status": "downloading", "downloaded_bytes": 850})

    assert _percents(video_calls) == [80, 80]
    assert _percents(audio_calls) == [None, 30]


def test_non_downloading_statuses_are_still_ignored():
    hook, calls = _collect()
    hook({"status": "finished"})
    hook({"status": "processing"})

    assert calls == []


# ── 复审返工：整条链路 yt-dlp hook → Worker detail → 公开任务状态 ──


def test_worker_detail_keeps_the_percent_visible_through_the_whole_chain(monkeypatch):
    """百分比不能在界面文字里消失。

    这条不 mock 进度 hook，也不 mock 文案拼装：真的把 yt-dlp 形状的事件喂进
    `_make_progress_hook`，让 `worker._download_source_if_url` 里的 `_dl_cb` 拼文案，
    再断言送出去的 detail 一直带着数字。
    """
    import json as _json
    import io
    from backend import worker

    events = [
        {"status": "downloading", "downloaded_bytes": 500, "total_bytes": 1000},
        {"status": "downloading", "downloaded_bytes": 600},
        {"status": "downloading", "downloaded_bytes": 700, "total_bytes": 1000},
    ]

    def _fake_download(url, output_dir, progress_callback=None):
        hook = _make_progress_hook(progress_callback, kind="video")
        for event in events:
            hook(event)
        video = Path(output_dir) / "t_video.mp4"
        video.write_bytes(b"v")
        audio = Path(output_dir) / "t_audio.m4a"
        audio.write_bytes(b"a")
        return str(video), str(audio)

    captured = io.StringIO()
    monkeypatch.setattr(worker, "_real_stdout", captured)
    monkeypatch.setattr(
        "backend.core.video_downloader.is_ytdlp_available", lambda: True
    )
    monkeypatch.setattr(
        "backend.core.video_downloader.download_video", _fake_download
    )
    monkeypatch.setattr(
        "backend.core.extractor.has_audio_stream", lambda path: "has_audio"
    )

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        job_dir = Path(tmp).resolve()
        worker._download_source_if_url(
            {"source_url": "https://example.test/v"}, job_dir, None
        )

    # `_dl_cb` 调的是 `stderr_progress("download", 1, detail)`，第三个位置参数是
    # `message`，所以下载文案是任务卡的**主文案**，不是副文案。
    texts = [
        _json.loads(line)["message"]
        for line in captured.getvalue().splitlines()
        if line.strip() and _json.loads(line).get("stage") == "download"
    ]
    download_texts = [t for t in texts if t.startswith("正在下载视频 ")]
    assert download_texts == [
        "正在下载视频 50%", "正在下载视频 50%", "正在下载视频 70%"
    ], f"界面文字里的百分比消失了：{download_texts}"


def test_public_task_state_keeps_the_percent_in_the_card_text(monkeypatch, tmp_path):
    """再往前走一步：这段文字真的进到用户看到的公开状态里。"""
    import json as _json
    from backend import main as _main

    job_id = "p442job"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    (job_dir / "meta.json").write_text(
        _json.dumps({"status": "running", "run_id": "r1", "config": {}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(_main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(_main, "JOBS", {job_id: {
        "id": job_id, "status": "running", "run_id": "r1", "config": {},
        **_main._empty_run_state(),
    }})

    hook, calls = _collect(kind="video")
    hook({"status": "downloading", "downloaded_bytes": 500, "total_bytes": 1000})
    hook({"status": "downloading", "downloaded_bytes": 600})

    seen = []
    for call in calls:
        percent = call["download_percent"]
        text = (
            f"正在下载视频 {percent}%" if percent is not None else "正在下载视频"
        )
        # 和 worker._dl_cb 一样，把文案放在 message 位上。
        _main._sync_job_from_worker_message(job_id, {
            "type": "progress", "run_id": "r1", "stage": "download",
            "percent": 1, "message": text, "detail": "",
        })
        seen.append(
            _main.job_state.build_public_task_state(
                _main.JOBS[job_id]
            )["display_message"]
        )

    assert seen == ["正在下载视频 50%", "正在下载视频 50%"], (
        f"公开任务状态里的百分比消失了：{seen}"
    )
