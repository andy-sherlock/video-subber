"""A4 / A5 / A6 / A7 / A9 / A12 / A13：Codex 缺陷修复第二批。

A4  进度类消息不得携带结果四元组
A5  终态广播快照必须在任务锁内冻结
A6  resume / partial 启动失败不恢复旧 JOBS，也不创建无 run_id 的条目
A7  WorkerManager.start() 全有或全无：Popen 之后出错必须 kill + wait + 清映射
A9  done checkpoint 写入失败必须产生用户可见的 error
A12 无可信同 run SRT 时，burn / mux 的视频候选没有资格发布
A13 Windows 锁加锁位置必须与解锁位置一致
"""

import json
import sys
import threading
import types

import pytest

from backend import main, worker_manager as worker_manager_module


SRT_BODY = "1\n00:00:00,000 --> 00:00:01,000\n你好\n"
RESULT_FIELDS = ("result_srt", "result_video", "result_run_id", "output_ready")


def _quad(payload):
    return {key: payload.get(key) for key in RESULT_FIELDS}


def _ffprobe_ok(monkeypatch):
    monkeypatch.setattr(
        "backend.output_artifacts.subprocess.run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="mov,mp4", stderr=""),
    )


# ── A4：进度类消息剥离结果四元组 ─────────────────────────────────────────

@pytest.mark.parametrize(
    "msg_type",
    ["progress", "transcription_progress", "retry_progress", "engine_status"],
)
def test_progress_messages_never_carry_result_fields(msg_type):
    """Worker 若在非终态消息里塞了结果字段，界面绝不能拿到它。

    **保护权交接（2026-08-19 · G9/P422）**
    原来这条测试直接调 `WorkerManager._strip_non_terminal_result_fields()`——
    一个「转发 Worker 原始消息」时代的过滤器。P422 之后运行中广播的是父进程
    生成的公开快照，那个过滤器连同它的理由一起删掉了。

    **保护本身更强了，不是没了**：快照里的结果四元组来自
    `_owned_result_fields()`，归属、路径边界、类型和内容都核对过；Worker
    自己声称的值根本没有机会进入任务状态。这条测试改为验证**真实管道**——
    把一条塞满假结果字段的运行中消息喂进去，断言广播出去的东西里没有那些假值。
    """
    from backend import main as main_module

    job_id = "a4job"
    forged = "/j/伪造的成品.srt"
    message = {"type": msg_type, "stage": "translate", "percent": 50,
               "run_id": "run-1",
               "result_srt": forged, "result_video": "/j/x.mp4",
               "result_run_id": "run-1", "output_ready": True,
               "delivery": {"target": "/Users/secret/x.mp4"},
               "is_delivering": True, "can_show_output_folder": True}

    # 运行中消息落盘失败会 early return（R5：磁盘没记录就不让内存前进），
    # 所以这里要有一个真的任务目录。
    import tempfile, json as _json, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    (tmp / job_id).mkdir()
    (tmp / job_id / "meta.json").write_text(
        _json.dumps({"status": "running", "run_id": "run-1", "config": {}}),
        encoding="utf-8")
    saved_dir = main_module.JOBS_DIR
    main_module.JOBS_DIR = tmp
    saved_jobs = main_module.JOBS
    main_module.JOBS = {job_id: {
        "id": job_id, "status": "running", "run_id": "run-1",
        "run_kind": "normal", "config": {},
        **main_module._empty_run_state(),
    }}
    try:
        snapshot = main_module._sync_job_from_worker_message(job_id, message)
    finally:
        main_module.JOBS = saved_jobs
        main_module.JOBS_DIR = saved_dir

    assert isinstance(snapshot, dict), "运行中的消息也必须返回可广播的公开快照"
    assert "result_srt" not in snapshot
    assert snapshot["result"]["output_ready"] is not True
    assert snapshot["result"]["can_show_output_folder"] is not True
    for key in ("delivery", "is_delivering"):
        assert key not in snapshot, key
    # 真实的进度信息仍然到得了界面
    assert snapshot["status"] == "running"


# ── A5：终态广播快照在锁内冻结 ───────────────────────────────────────────

def test_terminal_snapshot_is_frozen_inside_the_job_lock(tmp_path, monkeypatch):
    """快照必须在锁内取，不能等出了锁再读 JOBS——那时可能已切到新一轮。"""
    job_dir = tmp_path / "a5job"
    job_dir.mkdir()
    srt = job_dir / "clip.srt"
    srt.write_text(SRT_BODY, encoding="utf-8")
    (job_dir / "meta.json").write_text(json.dumps({
        "status": "running", "config": {"outputMode": "srt"},
        "run_id": "run-1",
    }), encoding="utf-8")
    jobs = {"a5job": {"id": "a5job", "status": "running", "progress": 10,
                      "config": {"outputMode": "srt"}, "run_id": "run-1",
                      "run_kind": "normal", "message": ""}}
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", jobs)
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {})
    monkeypatch.setattr(main, "JOB_ID_TO_HASH", {})
    _ffprobe_ok(monkeypatch)

    real_lock = main._job_run_lock("a5job")
    observed = {}

    class WatchedLock:
        def __enter__(self):
            real_lock.acquire()
            return self

        def __exit__(self, *exc):
            # 出锁前记录 JOBS 状态，再模拟"锁一放开就切到新一轮"。
            observed["status_in_lock"] = jobs["a5job"]["status"]
            jobs["a5job"]["run_id"] = "run-2"
            jobs["a5job"].update(main._empty_run_state())
            real_lock.release()
            return False

    monkeypatch.setattr(main, "_job_run_lock", lambda jid: WatchedLock())

    snapshot = main._sync_job_from_worker_message("a5job", {
        "type": "done", "run_id": "run-1",
        "result_srt": str(srt), "result_video": None,
        "segment_count": 3, "success_count": 3,
    })

    assert observed["status_in_lock"] == "done"
    assert snapshot is not None, "锁内已完成的终态必须返回快照"
    assert snapshot["run_id"] == "run-1"
    assert snapshot["result"]["output_ready"] is True, (
        "状态在锁外才取，新一轮已把成品资格清空 —— 广播出去的是空壳"
    )


# ── A6：启动失败不恢复旧 JOBS ────────────────────────────────────────────

def test_resume_start_failure_keeps_new_identity_and_never_restores_old_jobs(
    tmp_path, monkeypatch
):
    job_dir = tmp_path / "a6job"
    job_dir.mkdir()
    (job_dir / "input.mp4").write_bytes(b"video")
    srt = job_dir / "old.srt"
    srt.write_text(SRT_BODY, encoding="utf-8")
    (job_dir / "meta.json").write_text(json.dumps({
        "status": "interrupted", "file_hash": "f" * 64,
        "config": {"outputMode": "srt", "video_filename": "old.mp4"},
        "run_id": "run-old", "result_srt": str(srt),
        "result_run_id": "run-old", "output_ready": True,
    }), encoding="utf-8")
    stale_job = {"id": "a6job", "status": "interrupted",
                 "result_srt": str(srt), "result_run_id": "run-old",
                 "output_ready": True}
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {"a6job": stale_job})
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {})
    monkeypatch.setattr(main, "JOB_ID_TO_HASH", {})
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda jid: False)
    monkeypatch.setattr(
        "backend.model_manager.is_model_loadable", lambda _model: True
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("popen failed")

    monkeypatch.setattr(main.worker_manager, "start", _boom)

    result = main._start_existing_job(
        {"job_id": "a6job", "status": "interrupted",
         "config": {"outputMode": "srt", "video_filename": "old.mp4"},
         "file_hash": "f" * 64},
        str(job_dir / "input.mp4"),
    )

    entry = main.JOBS["a6job"]
    assert entry.get("run_id"), "启动失败也不得留下没有运行身份的条目"
    assert entry["run_id"] != "run-old", "不得回退到上一轮身份"
    assert _quad(entry) == {"result_srt": None, "result_video": None,
                            "result_run_id": None, "output_ready": False}
    assert entry["status"] == "error"
    assert result["start_failed"] is True


# ── A7：start() 全有或全无 ───────────────────────────────────────────────

def test_start_kills_and_clears_when_post_popen_setup_fails(tmp_path, monkeypatch):
    manager = worker_manager_module.WorkerManager()
    killed = {"kill": 0, "wait": 0}

    class FakeProc:
        def __init__(self):
            self.stdout, self.stderr = [], []
            self.returncode = None

        def poll(self):
            return None

        def kill(self):
            killed["kill"] += 1

        def wait(self, timeout=None):
            killed["wait"] += 1
            return 0

    monkeypatch.setattr(worker_manager_module.subprocess, "Popen",
                        lambda *a, **k: FakeProc())
    monkeypatch.setattr(worker_manager_module, "_worker_env", lambda *a, **k: {})

    def _boom(*args, **kwargs):
        raise RuntimeError("thread start failed")

    monkeypatch.setattr(worker_manager_module.threading, "Thread", _boom)

    with pytest.raises(RuntimeError):
        manager.start("a7job", tmp_path, "video.mp4", {}, run_id="run-1")

    assert killed["kill"] == 1, "Popen 之后出错必须 kill 掉子进程"
    assert killed["wait"] == 1, "必须 wait，否则下一次 start 会和垂死进程赛跑"
    assert manager._processes == {}
    assert manager._job_dirs == {}
    assert manager._current_runs == {}


# ── A9：checkpoint 写入失败必须可见 ─────────────────────────────────────

def test_done_checkpoint_write_failure_surfaces_a_visible_error(tmp_path, monkeypatch):
    """写不进 done checkpoint 就不能悄悄当成功——必须发 error，不得发 done。"""
    import importlib.util
    from pathlib import Path as _Path

    spec = importlib.util.spec_from_file_location(
        "_p367_terminal_flow",
        _Path(__file__).parent / "test_p367_worker_terminal_flow.py",
    )
    flow = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(flow)
    FakeCheckpoint, _run_worker = flow.FakeCheckpoint, flow._run_worker

    # 正对照：同样的输入在 checkpoint 正常时必须走到 done，
    # 否则下面的 "done not in kinds" 会是假绿。
    control_root = tmp_path / "control"
    control_root.mkdir()
    healthy, _calls, _ck = _run_worker(monkeypatch, control_root, all_success=True)
    assert "done" in [m["type"] for m in healthy]

    def _boom(self, stage, meta=None, **kwargs):
        if stage == "done":
            raise OSError("disk full")
        self.saved.append((stage, dict(meta or {})))

    monkeypatch.setattr(FakeCheckpoint, "save", _boom)

    messages, _calls, _ck = _run_worker(monkeypatch, tmp_path, all_success=True)
    kinds = [m["type"] for m in messages]

    assert "done" not in kinds, "checkpoint 没写成功就不得宣布完成"
    assert "error" in kinds, "必须给用户一个可见的失败"


# ── A12：无可信 SRT 不发布视频 ──────────────────────────────────────────

@pytest.mark.parametrize("output_mode", ["burn", "mux"])
def test_video_is_not_published_without_a_trustworthy_same_run_srt(
    tmp_path, monkeypatch, output_mode
):
    job_dir = tmp_path / "a12job"
    job_dir.mkdir()
    video = job_dir / "clip_zh.mp4"
    video.write_bytes(b"rendered video")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    _ffprobe_ok(monkeypatch)

    fields = main._verified_result_fields(
        "a12job", "run-1",
        {"result_srt": None, "result_video": str(video)},
        output_ready=True,
    )

    assert _quad(fields) == {"result_srt": None, "result_video": None,
                             "result_run_id": None, "output_ready": False}


def test_video_outside_the_job_dir_is_never_published(tmp_path, monkeypatch):
    job_dir = tmp_path / "a12job2"
    job_dir.mkdir()
    srt = job_dir / "clip.srt"
    srt.write_text(SRT_BODY, encoding="utf-8")
    outside = tmp_path / "outside_zh.mp4"
    outside.write_bytes(b"not ours")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    _ffprobe_ok(monkeypatch)

    fields = main._verified_result_fields(
        "a12job2", "run-1",
        {"result_srt": str(srt), "result_video": str(outside)},
        output_ready=True,
    )

    assert fields["result_srt"] == str(srt)
    assert fields["result_video"] is None, "任务目录外的视频没有交付资格"


def test_delivery_eligibility_requires_video_and_srt_for_burn_and_mux(
    tmp_path, monkeypatch
):
    """A12 的交付闸门：burn / mux 少了任一候选都不得进入交付事务。"""
    job_dir = tmp_path / "a12job3"
    job_dir.mkdir()
    srt = job_dir / "clip.srt"
    srt.write_text(SRT_BODY, encoding="utf-8")
    video = job_dir / "clip_zh.mp4"
    video.write_bytes(b"rendered")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    _ffprobe_ok(monkeypatch)

    ok = main._delivery_candidates(
        "a12job3", "run-1", {"outputMode": "burn"},
        {"result_srt": str(srt), "result_video": str(video)},
    )
    assert ok is not None and ok["result_video"] == str(video)

    missing_video = main._delivery_candidates(
        "a12job3", "run-1", {"outputMode": "burn"},
        {"result_srt": str(srt), "result_video": None},
    )
    assert missing_video is None

    missing_srt = main._delivery_candidates(
        "a12job3", "run-1", {"outputMode": "mux"},
        {"result_srt": None, "result_video": str(video)},
    )
    assert missing_srt is None


# ── A13：Windows 锁位置一致 ─────────────────────────────────────────────

def test_windows_lock_seeks_to_zero_before_locking(tmp_path, monkeypatch):
    """加锁位置必须和解锁位置一致，否则锁的是文件末尾、解的是开头。"""
    from backend import worker

    import os

    positions = {}

    def _record(fileno, mode, nbytes):
        # 锁是按"当前文件指针"生效的，所以直接量文件描述符的位置。
        positions.setdefault("at_lock", os.lseek(fileno, 0, os.SEEK_CUR))

    fake_msvcrt = types.SimpleNamespace(LK_NBLCK=1, LK_UNLCK=0, locking=_record)
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(worker.sys, "platform", "win32")
    (tmp_path / ".worker.lock").write_bytes(b"stale lock content")

    handle = worker._acquire_job_lock(tmp_path)
    assert handle is not None
    worker._release_job_lock(handle)

    assert positions["at_lock"] == 0, (
        "追加模式打开后文件指针在末尾；加锁前必须 seek(0)"
    )
