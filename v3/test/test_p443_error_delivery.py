"""P443 复审返工：音频缺失的具体原因必须活着到达用户，且提示的动作必须真的能自救。

上一轮的缺口有两个，都不是"函数返回了什么"能发现的：

1. `_fail_missing_url_audio()` 走的是 `stderr_progress("error", ...)`，而它固定输出
   `type="progress"`。`main.py` 明确 `if message_type not in {"done","partial","error"}:
   return`，所以这条根本不是终态。随后 `sys.exit(1)`，`_sync_job_from_worker_exit()`
   看到 meta 还不是终态，就把它覆写成「后台处理已结束，但没有找到可确认的完成结果」。
   **用户看到的是一句什么都没说的话。**

2. 提示让用户「重新提交这个视频」。但 URL 任务的身份是 `sha256(source_url)`，同链接
   同设置会命中去重、回到**同一个任务目录**，多个音频候选还在，同样的错误再来一次。

这个文件里的测试一律不停在 Worker 进程边界。
"""

import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import worker  # noqa: E402

URL = "https://www.youtube.com/watch?v=SkQzl3kO0cA"
VIDEO_NAME = "Long Talk_video.mp4"
AUDIO_NAME = "Long Talk_audio.m4a"


# `build_public_task_state()` 在 error 终态把 `job["error"]` 渲染成 `display_message`
# （backend/job_state.py:139）。用户任务卡上看到的就是这一句，所以断言打在它身上。


class _ProcStub:
    """WorkerManager 传给退出回调的进程对象，只用到 returncode。"""

    def __init__(self, returncode=1):
        self.returncode = returncode


def _run_worker_download_step(config, job_dir, video_path):
    """真跑 Worker 的下载/恢复步骤，把它写到 stdout 的原始行收回来。

    不 mock `stderr_progress`，也不 mock `emit`——要的就是 Worker 真正送出去的字节。
    """
    captured = io.StringIO()
    original = worker._real_stdout
    worker._real_stdout = captured
    try:
        worker._download_source_if_url(config, job_dir, video_path)
        exit_code = 0
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else 1
    finally:
        worker._real_stdout = original
    lines = [
        json.loads(line)
        for line in captured.getvalue().splitlines()
        if line.strip()
    ]
    return lines, exit_code


def _feed_through_worker_manager(main_mod, job_id, run_id, lines, exit_code):
    """按 WorkerManager 的真实读取顺序把这些行喂进父进程，再做退出收敛。

    参照 `worker_manager._read_stdout()`：逐行解析 → 终态类型走终态回调 →
    进程结束后调用退出回调。
    """
    last_msg_type = None
    for msg in lines:
        last_msg_type = msg.get("type")
        msg["run_id"] = run_id
        if last_msg_type in ("done", "partial", "error"):
            main_mod._handle_worker_terminal(job_id, msg)
        else:
            main_mod._sync_job_from_worker_message(job_id, msg)
    return main_mod._sync_job_from_worker_exit(
        job_id, run_id, _ProcStub(exit_code), exit_code, last_msg_type
    )


@pytest.fixture
def parent(monkeypatch, tmp_path):
    """一套隔离的父进程状态：临时 JOBS_DIR、临时 JOBS、临时 user.log 目录。"""
    from backend import main as _main

    job_id = "p443err"
    job_dir = tmp_path / "jobs" / job_id
    job_dir.mkdir(parents=True)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    run_id = "run443"
    (job_dir / "meta.json").write_text(json.dumps({
        "status": "running", "run_id": run_id, "run_kind": "resume",
        "config": {"source_url": URL},
    }), encoding="utf-8")

    monkeypatch.setattr(_main, "JOBS_DIR", tmp_path / "jobs")
    # P440 教训：error 终态会调 `_ensure_error_user_log()`，它按 `main.LOG_DIR` 建目录
    # 写 user.log。名字写错就会**静默**写进用户的真实 `~/.video-subber/logs/`。
    # 这里不加 hasattr 保护——名字错了就应该当场炸，而不是悄悄污染真实目录。
    assert hasattr(_main, "LOG_DIR"), "main.LOG_DIR 改名了，测试隔离已失效"
    monkeypatch.setattr(_main, "LOG_DIR", logs_dir)
    monkeypatch.setattr(_main, "JOBS", {job_id: {
        "id": job_id, "status": "running", "run_id": run_id,
        "run_kind": "resume", "config": {"source_url": URL},
        **_main._empty_run_state(),
    }})
    monkeypatch.setattr(_main, "GLOBAL_HASH_INDEX", {})
    monkeypatch.setattr(_main, "JOB_ID_TO_HASH", {})
    real_logs = Path.home() / ".video-subber" / "logs"
    before = sorted(p.name for p in real_logs.glob("*")) if real_logs.is_dir() else []
    yield {
        "main": _main, "job_id": job_id, "job_dir": job_dir,
        "run_id": run_id, "logs_dir": logs_dir,
    }
    after = sorted(p.name for p in real_logs.glob("*")) if real_logs.is_dir() else []
    assert after == before, f"测试写进了用户真实日志目录：新增 {set(after) - set(before)}"


def _audio_states(monkeypatch, mapping, default="no_audio"):
    monkeypatch.setattr(
        "backend.core.extractor.has_audio_stream",
        lambda path: mapping.get(Path(path).name, default),
    )


def _no_download(monkeypatch, *, refill=None):
    """封死所有下载入口；refill 给出补下载的返回值（默认 None＝补不回来）。"""
    monkeypatch.setattr(
        "backend.core.video_downloader.is_ytdlp_available", lambda: True
    )
    monkeypatch.setattr(
        "backend.core.video_downloader.download_video",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("恢复时不应该重新下载整段视频")
        ),
    )
    monkeypatch.setattr(
        "backend.core.video_downloader.download_audio_only",
        lambda *a, **k: refill,
    )


def _user_log_text(worker_module):
    """Worker 的人话日志——用户点「查看日志」看到的就是它。"""
    handlers = getattr(worker_module._user_log, "handlers", [])
    for handler in handlers:
        path = getattr(handler, "baseFilename", None)
        if path and Path(path).exists():
            return Path(path).read_text(encoding="utf-8")
    return ""


# ── 问题 1：具体原因必须活着到达公开状态 ──────────────────────────


def test_missing_audio_reaches_the_public_state_as_a_specific_error(
    parent, monkeypatch
):
    """独立音频缺失 + 视频没有音轨 → 用户必须看到「音频」相关的具体原因。"""
    _no_download(monkeypatch)
    job_dir = parent["job_dir"]
    video = job_dir / VIDEO_NAME
    video.write_bytes(b"video-only")
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"})

    lines, exit_code = _run_worker_download_step(
        {"source_url": URL, "_resume": True, "video_filename": "watch"},
        job_dir, str(video),
    )

    assert exit_code == 1
    assert any(line.get("type") == "error" for line in lines), (
        "Worker 没有发出真正的终态错误，只发了 progress："
        f"{[l.get('type') for l in lines]}"
    )

    snapshot = _feed_through_worker_manager(
        parent["main"], parent["job_id"], parent["run_id"], lines, exit_code
    )

    assert snapshot["status"] == "error"
    assert "没有找到可确认的完成结果" not in (snapshot.get("display_message") or ""), (
        "具体的音频错误被退出收敛的笼统文案盖掉了"
    )
    assert "音频" in (snapshot.get("display_message") or ""), (
        f"公开状态没有保留音频原因：{snapshot.get('display_message')!r}"
    )


def test_meta_and_jobs_carry_the_same_specific_reason(parent, monkeypatch):
    """任务卡、JOBS 和 meta.json 必须是同一个原因，不能各说各的。"""
    _no_download(monkeypatch)
    job_dir = parent["job_dir"]
    video = job_dir / VIDEO_NAME
    video.write_bytes(b"video-only")
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"})

    lines, exit_code = _run_worker_download_step(
        {"source_url": URL, "_resume": True, "video_filename": "watch"},
        job_dir, str(video),
    )
    snapshot = _feed_through_worker_manager(
        parent["main"], parent["job_id"], parent["run_id"], lines, exit_code
    )

    meta = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    job = parent["main"].JOBS[parent["job_id"]]

    assert meta["status"] == "error"
    assert meta.get("error") == job.get("error") == snapshot.get("display_message")
    assert "音频" in meta["error"]


def test_multiple_candidates_also_reach_the_public_state(parent, monkeypatch):
    """同一前缀下两个有效音频＝真歧义，必须是具体错误，不是笼统错误。"""
    _no_download(monkeypatch)
    job_dir = parent["job_dir"]
    video = job_dir / VIDEO_NAME
    video.write_bytes(b"video-only")
    (job_dir / AUDIO_NAME).write_bytes(b"a1")
    (job_dir / "Long Talk_audio.webm").write_bytes(b"a2")
    _audio_states(monkeypatch, {VIDEO_NAME: "no_audio"}, default="has_audio")

    lines, exit_code = _run_worker_download_step(
        {"source_url": URL, "_resume": True, "video_filename": "watch"},
        job_dir, str(video),
    )
    snapshot = _feed_through_worker_manager(
        parent["main"], parent["job_id"], parent["run_id"], lines, exit_code
    )

    assert snapshot["status"] == "error"
    assert "没有找到可确认的完成结果" not in (snapshot.get("display_message") or "")
    assert "多个音频文件" in (snapshot.get("display_message") or "")


def test_corrupted_audio_also_reaches_the_public_state(parent, monkeypatch):
    """候选存在但探不到音频流，也要给具体原因。"""
    _no_download(monkeypatch)
    job_dir = parent["job_dir"]
    video = job_dir / VIDEO_NAME
    video.write_bytes(b"video-only")
    (job_dir / AUDIO_NAME).write_bytes(b"corrupted")
    _audio_states(
        monkeypatch, {VIDEO_NAME: "no_audio", AUDIO_NAME: "unknown"}
    )

    lines, exit_code = _run_worker_download_step(
        {"source_url": URL, "_resume": True, "video_filename": "watch"},
        job_dir, str(video),
    )
    snapshot = _feed_through_worker_manager(
        parent["main"], parent["job_id"], parent["run_id"], lines, exit_code
    )

    assert snapshot["status"] == "error"
    assert "没有找到可确认的完成结果" not in (snapshot.get("display_message") or "")
    assert "音频" in (snapshot.get("display_message") or "")


def test_download_failure_also_uses_the_final_error_channel(parent, monkeypatch):
    """同一个函数里的老失败出口也不能继续丢原因——同缺陷同修法。"""
    monkeypatch.setattr(
        "backend.core.video_downloader.is_ytdlp_available", lambda: True
    )
    monkeypatch.setattr(
        "backend.core.video_downloader.download_video", lambda *a, **k: None
    )
    job_dir = parent["job_dir"]

    lines, exit_code = _run_worker_download_step(
        {"source_url": URL, "video_filename": "watch"}, job_dir, None
    )
    snapshot = _feed_through_worker_manager(
        parent["main"], parent["job_id"], parent["run_id"], lines, exit_code
    )

    assert snapshot["status"] == "error"
    assert "没有找到可确认的完成结果" not in (snapshot.get("display_message") or "")
    assert "下载" in (snapshot.get("display_message") or "")


def test_ytdlp_missing_also_uses_the_final_error_channel(parent, monkeypatch):
    monkeypatch.setattr(
        "backend.core.video_downloader.is_ytdlp_available", lambda: False
    )
    job_dir = parent["job_dir"]

    lines, exit_code = _run_worker_download_step(
        {"source_url": URL, "video_filename": "watch"}, job_dir, None
    )
    snapshot = _feed_through_worker_manager(
        parent["main"], parent["job_id"], parent["run_id"], lines, exit_code
    )

    assert snapshot["status"] == "error"
    assert "没有找到可确认的完成结果" not in (snapshot.get("display_message") or "")
    assert "yt-dlp" in (snapshot.get("display_message") or "")
