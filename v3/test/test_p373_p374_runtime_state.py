"""P373/P374：运行身份、退出收敛与真实运行状态合同。"""

# P464：`run_qwen_asr` 返回 QwenAsrOutcome（块 + 完整性状态），不再是裸列表。
# stub 必须跟上真模块的契约，否则测试就在撒谎。
def _Outcome(**kw):
    """惰性构造真的 QwenAsrOutcome。

    **不能在顶层 `from backend.core.qwen_asr_runner import ...`**：
    多个测试用 `sys.modules` 把该模块换成假模块；本文件若在那期间被导入
    （例如被别的测试 `from test.xxx import _stub_environment` 拉进来），
    顶层 import 会拿到假模块并炸在 `(unknown location)`。
    `transcriber_v2` 顶层持有真类的引用，不受 sys.modules 替换影响。
    """
    from backend.transcriber_v2 import QwenAsrOutcome
    return QwenAsrOutcome(**kw)
from pathlib import Path
import io
import json
import asyncio
import threading

from test import locked_decision

import pytest
from unittest.mock import AsyncMock


class _CheckpointRecorder:
    def save(self, *args, **kwargs):
        pass


def test_worker_start_rejects_live_process_without_killing_it(tmp_path, monkeypatch):
    """同一 job 已在运行时，新启动必须拒绝，不能静默杀掉旧 worker。"""
    import backend.worker_manager as wm
    from backend.worker_manager import WorkerManager

    class LiveProcess:
        def __init__(self):
            self.killed = False

        def poll(self):
            return None

        def kill(self):
            self.killed = True

    live = LiveProcess()
    manager = WorkerManager()
    manager._processes["same-job"] = live
    manager._job_dirs["same-job"] = Path(tmp_path)
    monkeypatch.setattr(
        wm.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("已有活进程时不应启动新进程"),
    )

    with pytest.raises(RuntimeError, match="正在运行"):
        manager.start(
            "same-job", tmp_path, "/tmp/input.mp4", {"model": "large-v2"}
        )

    assert live.killed is False
    assert manager._processes["same-job"] is live


def test_late_message_from_old_run_cannot_overwrite_current_job(monkeypatch, tmp_path):
    """旧 worker 的迟到终态不能把新一轮 running 改成 done。"""
    from backend import main

    job_id = "late-message"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    (job_dir / "meta.json").write_text(
        '{"status":"running","run_id":"new-run"}', encoding="utf-8"
    )
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(
        main,
        "JOBS",
        {
            job_id: {
                "id": job_id,
                "status": "running",
                "progress": 12,
                "run_id": "new-run",
            }
        },
    )

    main._sync_job_from_worker_message(
        job_id,
        {
            "type": "done",
            "run_id": "old-run",
            "result_srt": "/tmp/old.srt",
        },
    )

    assert main.JOBS[job_id]["status"] == "running"
    assert main.JOBS[job_id]["progress"] == 12
    assert "result_srt" not in main.JOBS[job_id]


def test_worker_start_binds_supplied_run_identity(monkeypatch, tmp_path):
    """父进程生成的运行编号必须和具体 Popen 绑定，供消息和退出校验。"""
    import backend.worker_manager as wm
    from backend.worker_manager import WorkerManager

    class FakeProcess:
        stdout = []
        stderr = []

        def poll(self):
            return None

    fake_process = FakeProcess()
    monkeypatch.setattr(wm.subprocess, "Popen", lambda *args, **kwargs: fake_process)

    class NoopThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(wm.threading, "Thread", NoopThread)

    manager = WorkerManager()
    returned = manager.start(
        "bound-job",
        tmp_path,
        "/tmp/input.mp4",
        {"model": "large-v2"},
        run_id="run-123",
        run_kind="resume",
    )

    assert returned == "run-123"
    assert manager.current_run("bound-job") == {
        "run_id": "run-123",
        "run_kind": "resume",
        "process": fake_process,
    }
    assert json.loads((tmp_path / "config.json").read_text())["video_path"] == "/tmp/input.mp4"


def test_worker_exit_waits_for_terminal_message_then_calls_exit_callback(tmp_path):
    """stdout 的 partial 必须先落地，之后才允许进程退出收敛。"""
    from backend.worker_manager import WorkerManager

    class FinishedProcess:
        returncode = 0
        stderr = []

        def __init__(self):
            self.stdout = iter([json.dumps({"type": "partial", "failed_count": 2})])

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    process = FinishedProcess()
    manager = WorkerManager()
    manager._job_dirs["job"] = tmp_path
    manager._processes["job"] = process
    manager._current_runs["job"] = {
        "run_id": "run-1",
        "run_kind": "partial_retry",
        "process": process,
    }
    events = []
    # B3 起终态走 terminal callback（交付事务在读取线程里跑，不占事件循环）。
    manager.set_terminal_callback(
        lambda job_id, message, publish: events.append(
            ("message", message["type"], message.get("run_id"))
        )
    )
    manager.set_progress_callback(
        lambda job_id, message: events.append(
            ("message", message["type"], message.get("run_id"))
        )
    )
    manager.set_exit_callback(
        lambda job_id, run_id, proc, exit_code, last_type: events.append(
            ("exit", exit_code, run_id, last_type)
        )
    )

    loop = asyncio.new_event_loop()
    manager.set_main_loop(loop)
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        manager._read_stdout("job", process, "run-1")
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()

    assert events == [
        ("message", "partial", "run-1"),
        ("exit", 0, "run-1", "partial"),
    ]


def test_qwen_chunked_reports_real_completed_chunks_and_audio_end(monkeypatch):
    """标准模式的 Qwen 每完成一个真实音频块，都要回报块数和结束时间。"""
    from backend.core import qwen_asr_runner as runner

    monkeypatch.setattr(runner, "_chunk_by_time", lambda duration: [(0.0, 300.0), (300.0, 620.0)])
    monkeypatch.setattr(runner, "_load_qwen_chunk_ckpt", lambda *args: ([], 0))
    monkeypatch.setattr(runner, "_load_qwen_model", lambda: object())
    monkeypatch.setattr(
        runner,
        "_transcribe_chunk",
        lambda model, audio_path, start, end, language, index, total: {
            "text": f"chunk-{index}",
            "segments": [],
            "t_start": start,
            "t_end": end,
        },
    )
    monkeypatch.setattr(runner, "_save_qwen_chunk_ckpt", lambda *args: None)
    events = []

    result = runner._run_qwen_asr_chunked(
        "/tmp/audio.wav",
        620.0,
        progress_callback=events.append,
    )

    assert len(result.chunks) == 2  # P464：返回 QwenAsrOutcome，块在 .chunks 上
    assert events == [
        {"completed_segments": 1, "processed_duration": 300.0},
        {"completed_segments": 2, "processed_duration": 620.0},
    ]


def test_standard_qwen_router_forwards_transcription_progress(monkeypatch):
    """标准模式切到 Qwen 后，必须沿用 worker 提供的真实进度回调。"""
    from types import SimpleNamespace
    from backend import transcriber_v2 as transcriber
    from backend.core import qwen_asr_runner as runner

    from backend.core.whisper_runner import LanguageDecision
    decision = LanguageDecision(
        language="en", locked=True, split=False, expected_count=3, valid_count=3)
    monkeypatch.setattr(transcriber, "choose_standard_primary_engine", lambda *args: "qwen")
    monkeypatch.setattr(transcriber, "release_whisper_model", lambda: None)
    monkeypatch.setattr(runner, "is_qwen_asr_available", lambda: True)
    # P463：主引擎资格还要看后端能不能给词级时间。显式声明这条测试跑在 MLX 后端上，
    # 否则同一份代码在没有 MLX 的机器上会红——全量测试的颜色不能取决于谁在跑。
    monkeypatch.setattr(runner, "_USE_MLX", True)
    received = []

    def fake_run(*args, progress_callback=None, **kwargs):
        received.append(progress_callback)
        progress_callback({"completed_segments": 1, "processed_duration": 30.0})
        return _Outcome(complete=True, chunks=[])

    monkeypatch.setattr(runner, "run_qwen_asr", fake_run)
    callback_events = []

    transcriber._run_standard_language_router(
        "/tmp/audio.wav",
        decision,
        True,
        True,
        "/tmp/cache",
        callback_events.append,
        {},
    )

    assert received == [callback_events.append]
    assert callback_events[-1] == {"completed_segments": 1, "processed_duration": 30.0}


def test_process_exit_adopts_latest_partial_snapshot_from_disk(monkeypatch, tmp_path):
    """进程已经退出时，磁盘 partial 必须立即覆盖内存里的旧 running。"""
    from backend import main

    job_id = "exit-partial"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    meta = {
        "status": "partial",
        "run_id": "retry-run",
        "segment_count": 240,
        "success_count": 24,
        "failed_count": 216,
        "failed_keys": [[1, 0.0, 1.0]],
        "retry_count": 2,
        "output_ready": False,
        "primary_failure_kind": "rate_limit",
        "failure_kind_counts": {"rate_limit": 216},
        "sound_reference": {
            "status": "unavailable",
            "message": "/private/input.mkv",
            "reason_code": "map_compute_failed",
            "source_words": ["secret"],
            "map_input_identity": {"source_content_sha256": "a" * 64},
        },
    }
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(
        main,
        "JOBS",
        {
            job_id: {
                "id": job_id,
                "status": "running",
                "progress": 40,
                "run_id": "retry-run",
                "success_count": 12,
                "failed_count": 228,
            }
        },
    )

    snapshot = main._sync_job_from_worker_exit(
        job_id, "retry-run", object(), 0, "partial"
    )

    assert snapshot["status"] == "partial"
    assert main.JOBS[job_id]["status"] == "partial"
    assert main.JOBS[job_id]["progress"] == 100
    assert main.JOBS[job_id]["process_alive"] is False
    assert main.JOBS[job_id]["success_count"] == 24
    assert main.JOBS[job_id]["failed_count"] == 216
    assert main.JOBS[job_id]["primary_failure_kind"] == "rate_limit"
    assert snapshot["result"]["quality"]["sound_reference"] == {
        "status": "unavailable",
        "message": "声音参考未使用，字幕仍已生成。",
        "reason_code": "map_compute_failed",
    }


def test_process_exit_never_leaves_running_without_terminal_evidence(monkeypatch, tmp_path):
    """进程退出而磁盘仍是 running 时，证据不足收敛为 interrupted（D-1 补修 B1，可一键继续）。"""
    from backend import main

    job_id = "exit-without-terminal"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    (job_dir / "meta.json").write_text(
        json.dumps({"status": "running", "run_id": "run-x", "config": {}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(
        main,
        "JOBS",
        {job_id: {"id": job_id, "status": "running", "run_id": "run-x"}},
    )
    monkeypatch.setattr(main, "_recover_completed_checkpoint", lambda *args: None)

    snapshot = main._sync_job_from_worker_exit(
        job_id, "run-x", object(), 0, None
    )

    saved = json.loads((job_dir / "meta.json").read_text())
    assert snapshot["status"] == "interrupted"
    assert main.JOBS[job_id]["status"] == "interrupted"
    assert saved["status"] == "interrupted"
    assert "没有找到可确认的完成结果" not in json.dumps(saved, ensure_ascii=False)


def test_exit_convergence_broadcasts_the_same_terminal_snapshot(monkeypatch):
    """退出收敛产生的最新快照必须主动发给 WebSocket，不能只等轮询。"""
    from backend.worker_manager import WorkerManager
    from backend.ws_manager import ws_manager

    manager = WorkerManager()
    manager.set_exit_callback(
        lambda *args: {
            "status": "partial",
            "success_count": 24,
            "failed_count": 216,
        }
    )
    # B3 起退出收敛在读取线程里跑（它也可能触发交付事务），
    # 只有广播那一步交回事件循环。
    sent = []
    manager._send = lambda job_id, message, loop: sent.append((job_id, message))

    class FinishedProcess:
        returncode = 0
        stderr = []

        def __init__(self):
            self.stdout = iter([])

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    process = FinishedProcess()
    manager._processes["job"] = process
    manager._current_runs["job"] = {
        "run_id": "run-1", "run_kind": "normal", "process": process,
    }
    manager._read_stdout("job", process, "run-1")

    assert sent == [("job", {
        "type": "state",
        "state": {
            "status": "partial", "success_count": 24, "failed_count": 216,
        },
    })]


def test_worker_reader_never_writes_business_error_before_exit_convergence(tmp_path):
    """异常退出只能通知主服务收敛，reader 不能抢先把 meta 钉成 error。"""
    from backend.worker_manager import WorkerManager

    class _Process:
        stdout = io.StringIO("")
        stderr = io.StringIO("")
        returncode = 9

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    meta_path = job_dir / "meta.json"
    meta_path.write_text(
        json.dumps({"status": "running", "run_id": "run-1"}),
        encoding="utf-8",
    )
    process = _Process()
    exits = []
    manager = WorkerManager()
    manager._processes["job"] = process
    manager._job_dirs["job"] = job_dir
    manager._current_runs["job"] = {
        "run_id": "run-1",
        "run_kind": "normal",
        "process": process,
    }
    manager.set_exit_callback(
        lambda job_id, run_id, proc, exit_code, last_type: exits.append(
            (job_id, run_id, proc, exit_code, last_type)
        )
    )

    manager._read_stdout("job", process, "run-1")

    assert json.loads(meta_path.read_text(encoding="utf-8"))["status"] == "running"
    assert exits == [("job", "run-1", process, 9, None)]


def test_terminal_disk_write_failure_never_reports_done(monkeypatch, tmp_path):
    """meta 写盘失败时，内存和页面不能假报任务已经完成。"""
    from backend import main

    job_id = "disk-failure"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    meta_path = job_dir / "meta.json"
    meta_path.write_text('{"status":"running","run_id":"run-1"}', encoding="utf-8")
    srt = job_dir / "result.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(
        main,
        "JOBS",
        {job_id: {"id": job_id, "status": "running", "progress": 85,
                  "run_id": "run-1"}},
    )
    monkeypatch.setattr(
        main,
        "_write_json_atomic",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    main._sync_job_from_worker_message(
        job_id,
        {"type": "done", "run_id": "run-1", "result_srt": str(srt),
         "success_count": 2},
    )

    assert main.JOBS[job_id]["status"] == "error"
    assert main.JOBS[job_id]["progress"] == 100
    assert "保存任务状态失败" in main.JOBS[job_id]["error"]
    assert json.loads(meta_path.read_text())["status"] == "running"


def test_exit_recovery_write_failure_never_reports_recovered_terminal(
    monkeypatch, tmp_path
):
    """退出兜底推断出 partial 后仍须先写盘；写失败不能向页面假报 partial。"""
    from backend import main

    job_id = "exit-write-failure"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    (job_dir / "meta.json").write_text(
        json.dumps({"status": "running", "run_id": "run-y", "config": {}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(
        main,
        "JOBS",
        {job_id: {"id": job_id, "status": "running", "run_id": "run-y"}},
    )
    monkeypatch.setattr(
        main,
        "_recover_completed_checkpoint",
        lambda *args: {"status": "partial", "success_count": 1, "failed_count": 1},
    )
    monkeypatch.setattr(
        main,
        "_write_json_atomic",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    snapshot = main._sync_job_from_worker_exit(
        job_id, "run-y", object(), 0, None
    )

    assert snapshot["status"] == "error"
    assert "保存任务状态失败" in snapshot["display_message"]


def test_new_job_persists_run_identity_before_worker_start(monkeypatch, tmp_path):
    """新任务启动前，meta/JOBS/WorkerManager 必须绑定同一个 run_id。"""
    from backend import main

    job_id = "new-job"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    video = job_dir / "input.mp4"
    video.write_bytes(b"video")
    starts = []
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {})
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {})
    monkeypatch.setattr(main, "JOB_ID_TO_HASH", {})
    monkeypatch.setattr(main.uuid, "uuid4", lambda: type("U", (), {"hex": "fixed-run"})())
    monkeypatch.setattr(
        main.worker_manager,
        "start",
        lambda *args, **kwargs: starts.append((args, kwargs)) or kwargs["run_id"],
    )

    main._start_new_job(
        job_id,
        job_dir,
        video,
        {"video_filename": "input.mp4"},
        "file-hash",
        "task-key",
    )

    saved = json.loads((job_dir / "meta.json").read_text())
    assert saved["run_id"] == "fixed-run"
    assert saved["run_kind"] == "normal"
    assert main.JOBS[job_id]["run_id"] == "fixed-run"
    assert main.JOBS[job_id]["run_kind"] == "normal"
    assert starts[0][1] == {"run_id": "fixed-run", "run_kind": "normal"}


def test_interrupted_resume_persists_resume_run_identity(monkeypatch, tmp_path):
    """间接恢复旧入口也必须绑定 resume 编号，不能成为绕过运行守卫的旧路。"""
    from backend import main

    job_id = "resume-job"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    video = job_dir / "input.mp4"
    video.write_bytes(b"video")
    (job_dir / "meta.json").write_text(
        json.dumps({
            "status": "interrupted",
            "config": {"model": "large-v2", "video_filename": "input.mp4"},
        }),
        encoding="utf-8",
    )
    throwaway = tmp_path / "throwaway"
    throwaway.mkdir()
    starts = []
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {})
    monkeypatch.setattr(main.uuid, "uuid4", lambda: type("U", (), {"hex": "resume-run"})())
    monkeypatch.setattr("backend.model_manager.is_model_loadable", lambda model: True)
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda jid: False)
    monkeypatch.setattr(
        main.worker_manager,
        "start",
        lambda *args, **kwargs: starts.append((args, kwargs)) or kwargs["run_id"],
    )

    response = main._start_existing_job(
        {
            "job_id": job_id,
            "status": "interrupted",
            "config": {"model": "large-v2", "video_filename": "input.mp4"},
        },
        video,
    )

    saved = json.loads((job_dir / "meta.json").read_text())
    assert response["status"] == "running"
    assert saved["run_id"] == "resume-run"
    assert saved["run_kind"] == "resume"
    assert main.JOBS[job_id]["run_id"] == "resume-run"
    assert starts[0][1] == {"run_id": "resume-run", "run_kind": "resume"}


def test_partial_retry_persists_retry_run_identity(monkeypatch, tmp_path):
    """失败字幕重试必须有独立运行身份，供迟到消息和前端状态判断。"""
    from backend import main

    job_id = "partial-retry"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    video = job_dir / "input.mp4"
    video.write_bytes(b"video")
    meta = {
        "status": "partial",
        "config": {"translationPriority": ["bing"]},
        "source_path": str(video),  # P516：任务只记原视频地址
        "failed_keys": [[1, 0.0, 1.0]],
        "segment_count": 2,
        "success_count": 1,
        "failed_count": 1,
        "output_ready": False,
        "retry_count": 0,
    }
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    starts = []
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {job_id: {"id": job_id, "status": "partial"}})
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {})
    monkeypatch.setattr(main, "JOB_ID_TO_HASH", {})
    monkeypatch.setattr(main.uuid, "uuid4", lambda: type("U", (), {"hex": "retry-run"})())
    monkeypatch.setattr(main.time, "time", lambda: 1234.5)
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda jid: False)
    monkeypatch.setattr(
        main.worker_manager,
        "start",
        lambda *args, **kwargs: starts.append((args, kwargs)) or kwargs["run_id"],
    )
    monkeypatch.setattr(
        "backend.config_manager.load_config",
        lambda: {"apiKeys": [], "translationPriority": ["bing"]},
    )

    response = main.partial_action(job_id, {"action": "retry"})

    saved = json.loads((job_dir / "meta.json").read_text())
    assert response["ok"] is True
    assert saved["run_id"] == "retry-run"
    assert saved["run_kind"] == "partial_retry"
    assert saved["retry_started_at"] == 1234.5
    assert saved["retry_initial_failed_count"] == 1
    assert saved["retry_processed_count"] == 0
    assert saved["retry_new_success_count"] == 0
    assert saved["retry_status"] == "running"
    assert main.JOBS[job_id]["run_id"] == "retry-run"
    assert starts[0][1] == {"run_id": "retry-run", "run_kind": "partial_retry"}


def test_retry_progress_is_persisted_and_refreshes_latest_counts(monkeypatch, tmp_path):
    """本轮真实计数必须同时更新内存和磁盘，轮询不能继续读上一轮数字。"""
    from backend import main

    job_id = "retry-progress"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    meta = {
        "status": "running",
        "run_id": "current-run",
        "run_kind": "partial_retry",
        "retry_started_at": 100.0,
        "retry_initial_failed_count": 5,
        "retry_processed_count": 0,
        "retry_new_success_count": 0,
        "retry_status": "running",
        "config": {},
    }
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {
        job_id: {"id": job_id, "progress": 40, **meta},
    })

    main._sync_job_from_worker_message(job_id, {
        "type": "retry_progress",
        "run_id": "current-run",
        "stage": "translate",
        "message": "正在重试",
        "retry_started_at": 100.0,
        "retry_initial_failed_count": 5,
        "retry_processed_count": 3,
        "retry_new_success_count": 2,
        "retry_status": "running",
    })

    saved = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert main.JOBS[job_id]["retry_processed_count"] == 3
    assert main.JOBS[job_id]["retry_new_success_count"] == 2
    assert main.JOBS[job_id]["message"] == "正在重试"
    assert saved["retry_processed_count"] == 3
    assert saved["retry_new_success_count"] == 2
    assert saved["retry_status"] == "running"


def test_retry_enters_post_processing_and_persists_real_stage(monkeypatch, tmp_path):
    from backend import main

    job_id = "retry-post"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    meta = {"status": "running", "run_id": "run", "retry_status": "running", "config": {}}
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {job_id: {"id": job_id, "progress": 80, **meta}})

    main._sync_job_from_worker_message(job_id, {
        "type": "progress", "run_id": "run", "stage": "subtitle",
        "percent": 85, "message": "正在生成字幕文件…",
        "retry_status": "post_processing",
    })

    saved = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert main.JOBS[job_id]["retry_status"] == "post_processing"
    assert saved["retry_status"] == "post_processing"
    assert saved["stage"] == "subtitle"
    # **保护权交接（2026-08-19 · G9/P422）**：`message` 是只在当下成立的临时文案，不再落盘（P436 裁决）。
    # 重启之后「正在生成字幕文件…」已经不成立，把它写回页面就是拿过时的话
    # 冒充当前状态。阶段与重试状态这两个**持久事实**照旧落盘，上面两行仍在保护。
    # 当下这一刻页面看到的文案由 JOBS 事实即时生成：
    assert "message" not in saved
    assert main.job_state.build_public_task_state(main.JOBS[job_id])["display_message"] == "正在生成字幕文件…"


def test_retry_done_persists_current_round_result(monkeypatch, tmp_path):
    """全部补回后，done 也必须保留本轮结果，不能退回上一轮数字。"""
    from backend import main

    job_id = "retry-done"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    meta = {
        "status": "running",
        "run_id": "current-run",
        "run_kind": "partial_retry",
        "config": {},
    }
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {
        job_id: {"id": job_id, "progress": 40, **meta},
    })

    state = main._sync_job_from_worker_message(job_id, {
        "type": "done",
        "run_id": "current-run",
        "result_srt": "result.srt",
        "segment_count": 10,
        "success_count": 10,
        "retry_started_at": 100.0,
        "retry_initial_failed_count": 3,
        "retry_processed_count": 3,
        "retry_new_success_count": 3,
        "retry_status": "completed",
    })

    saved = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert main.JOBS[job_id]["retry_new_success_count"] == 3
    assert main.JOBS[job_id]["retry_status"] == "completed"
    assert saved["retry_processed_count"] == 3
    assert saved["retry_new_success_count"] == 3
    assert saved["retry_status"] == "completed"


def test_interrupted_job_has_direct_resume_using_same_job_and_local_video(
    monkeypatch, tmp_path
):
    """一键继续不得要求重新提交，也不得生成新任务编号。"""
    from backend import main

    job_id = "direct-resume"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    video = job_dir / "input.mp4"
    video.write_bytes(b"video")
    config = {"model": "large-v2"}
    (job_dir / "meta.json").write_text(json.dumps({
        "status": "interrupted",
        "config": config,
        "source_path": str(video),  # P516：任务只记原视频地址
        "file_hash": "fake-hash",
    }), encoding="utf-8")
    starts = []
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {})
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {})
    monkeypatch.setattr(main, "JOB_ID_TO_HASH", {})
    monkeypatch.setattr(main.uuid, "uuid4", lambda: type("U", (), {"hex": "resume-run"})())
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda jid: False)
    monkeypatch.setattr("backend.model_manager.is_model_loadable", lambda model: True)
    monkeypatch.setattr(
        main.worker_manager,
        "start",
        lambda jid, directory, source, cfg, **kwargs: starts.append(
            (jid, directory, source, cfg.copy(), kwargs)
        ) or kwargs["run_id"],
    )

    response = main.resume_job(job_id)

    assert response["ok"] is True
    assert response["job_id"] == job_id
    assert response["status"] == "running"
    assert starts == [(
        job_id,
        job_dir,
        str(video),
        {**config, "apiKeys": [], "_resume": True},  # P512：Key 来自设置文件（测试里为空）
        {"run_id": "resume-run", "run_kind": "resume"},
    )]
    assert not [path for path in tmp_path.iterdir() if path.name != job_id]


def test_direct_resume_rejects_damaged_checkpoint_before_start(monkeypatch, tmp_path):
    """已有断点损坏时才要求重新提交，不能悄悄从头重跑识别。"""
    from backend import main

    job_id = "damaged-resume"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    (job_dir / "input.mp4").write_bytes(b"video")
    (job_dir / "meta.json").write_text(json.dumps({
        "status": "interrupted",
        "config": {"video_filename": "input.mp4", "model": "large-v2"},
    }), encoding="utf-8")
    cache = job_dir / "cache" / "broken"
    cache.mkdir(parents=True)
    (cache / "checkpoint.json").write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(
        main.worker_manager,
        "start",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("损坏断点不得启动 worker")
        ),
    )

    response = main.resume_job(job_id)
    payload = json.loads(response.body)

    assert response.status_code == 409
    assert "缓存" in payload["error"]
    assert "重新提交" in payload["error"]


def test_direct_resume_start_failure_converges_to_error_with_new_identity(
    monkeypatch, tmp_path
):
    """一键继续启动失败不能把任务留在假的运行中。

    A6（2026-07-19 修订）：不再回滚成 interrupted。start() 可能已经把进程拉
    起来了，退回上一轮身份会让孤儿 Worker 的消息被当成当前轮次接受。改为
    收敛到 error + 新身份 + 空结果，用户仍可经 /resume 继续。"""
    from backend import main

    job_id = "resume-start-failed"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    (job_dir / "input.mp4").write_bytes(b"video")
    original = {
        "status": "interrupted",
        "config": {"model": "large-v2"},
        "source_path": str(job_dir / "input.mp4"),  # P516：任务只记原视频地址
    }
    (job_dir / "meta.json").write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {})
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda jid: False)
    monkeypatch.setattr("backend.model_manager.is_model_loadable", lambda model: True)
    monkeypatch.setattr(
        main.worker_manager,
        "start",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("spawn failed")),
    )

    response = main.resume_job(job_id)
    saved = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))

    assert response.status_code == 500
    assert saved["status"] == "error"
    assert main.JOBS[job_id]["status"] == "error"
    # 不得留下没有运行身份的条目，也不得保留上一轮的成品资格。
    assert main.JOBS[job_id].get("run_id")
    assert main.JOBS[job_id]["result_srt"] is None
    assert main.JOBS[job_id]["output_ready"] is False


def test_transcription_progress_reports_real_audio_times_without_fixed_percent(
    monkeypatch, tmp_path
):
    from backend import worker

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    transcript = tmp_path / "transcript.json"
    output = io.StringIO()
    monkeypatch.setattr(worker, "_real_stdout", output)
    monkeypatch.setattr("backend.core.extractor.get_video_duration", lambda path: 120.0)
    monkeypatch.setattr(worker.time, "time", lambda: 1000.0)
    monotonic_values = iter([50.0, 50.0, 62.0, 70.0])
    monkeypatch.setattr(worker.time, "monotonic", lambda: next(monotonic_values))

    def fake_transcribe(*args, **kwargs):
        kwargs["progress_callback"]({
            "completed_segments": 10,
            "processed_duration": 30.0,
        })
        segment = worker.Segment(0, 0.0, 30.0, "hello")
        receipt = worker.TranscriptionReceipt(
            routed_primary="whisper",
            actual_primary="whisper",
            word_source="whisper_word_timestamps",
            source_text="hello",
            source_words=[{"text": "hello", "start": 0.0, "end": 30.0}],
                      language_decision=locked_decision(),
        artifact_language_check="ok",
                  )
        return worker.TranscriptionEnvelope([segment], receipt)

    monkeypatch.setattr(worker, "transcribe_v2", fake_transcribe)

    worker._step_transcribe(
        "start", str(audio), transcript, "large-v2", False, "standard",
        tmp_path, {}, _CheckpointRecorder(),
    )

    events = [
        json.loads(line) for line in output.getvalue().splitlines()
        if json.loads(line).get("type") == "transcription_progress"
    ]
    running = next(event for event in events if event["completed_segments"] == 10)
    assert "percent" not in running
    assert running["transcription_started_at"] == 1000.0
    assert running["audio_total_duration"] == 120.0
    assert running["audio_processed_duration"] == 30.0
    assert running["audio_remaining_duration"] == 90.0
    assert running["elapsed_seconds"] == 12
    assert running["duration_reliable"] is True


def test_transcription_progress_falls_back_to_real_segments_and_elapsed_time(
    monkeypatch, tmp_path
):
    from backend import worker

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    transcript = tmp_path / "transcript.json"
    output = io.StringIO()
    monkeypatch.setattr(worker, "_real_stdout", output)
    monkeypatch.setattr("backend.core.extractor.get_video_duration", lambda path: 0.0)
    monkeypatch.setattr(worker.time, "time", lambda: 2000.0)
    monotonic_values = iter([10.0, 10.0, 17.0, 20.0])
    monkeypatch.setattr(worker.time, "monotonic", lambda: next(monotonic_values))

    def fake_transcribe(*args, **kwargs):
        kwargs["progress_callback"](7)
        segment = worker.Segment(0, 0.0, 1.0, "hello")
        receipt = worker.TranscriptionReceipt(
            routed_primary="whisper",
            actual_primary="whisper",
            word_source="whisper_word_timestamps",
            source_text="hello",
            source_words=[{"text": "hello", "start": 0.0, "end": 1.0}],
                      language_decision=locked_decision(),
        artifact_language_check="ok",
                  )
        return worker.TranscriptionEnvelope([segment], receipt)

    monkeypatch.setattr(worker, "transcribe_v2", fake_transcribe)
    worker._step_transcribe(
        "start", str(audio), transcript, "large-v2", False, "standard",
        tmp_path, {}, _CheckpointRecorder(),
    )

    events = [
        json.loads(line) for line in output.getvalue().splitlines()
        if json.loads(line).get("type") == "transcription_progress"
    ]
    running = next(event for event in events if event["completed_segments"] == 7)
    assert "percent" not in running
    assert running["duration_reliable"] is False
    assert running["audio_total_duration"] is None
    assert running["audio_processed_duration"] is None
    assert running["audio_remaining_duration"] is None
    assert running["elapsed_seconds"] == 7


def test_transcription_progress_is_available_to_websocket_and_polling(
    monkeypatch, tmp_path
):
    from backend import main

    job_id = "transcription-progress"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    meta = {"status": "running", "run_id": "run", "config": {}}
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {
        job_id: {"id": job_id, "status": "running", "progress": 5, **meta},
    })

    main._sync_job_from_worker_message(job_id, {
        "type": "transcription_progress",
        "run_id": "run",
        "stage": "transcribe",
        "message": "正在识别语音",
        "transcription_started_at": 1000.0,
        "completed_segments": 20,
        "elapsed_seconds": 45,
        "duration_reliable": True,
        "audio_total_duration": 180.0,
        "audio_processed_duration": 60.0,
        "audio_remaining_duration": 120.0,
        "transcription_status": "running",
    })

    saved = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert main.JOBS[job_id]["completed_segments"] == 20
    assert main.JOBS[job_id]["audio_processed_duration"] == 60.0
    assert main.JOBS[job_id]["message"] == "正在识别语音"
    state = main.job_state.build_public_task_state(main.JOBS[job_id])
    assert "completed_segments" not in saved
    assert "audio_remaining_duration" not in saved
    assert "transcription_status" not in saved
    assert saved["stage"] == "transcribe"
    assert state["phase"] == "transcribe"
    # P453：识别阶段不再显示段数
    assert "已识别" not in (state["activity"] or "")
