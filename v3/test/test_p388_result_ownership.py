"""P388：成品归属。一次运行只能认领本轮验证过的字幕/视频。

结果四元组 = result_srt / result_video / result_run_id / output_ready。
只有 当前 run_id == 结果来源 run == result_run_id 时，结果才有资格
恢复、下载或展示。旧文件存在、滞后文件名、保存副本、非 done checkpoint
都不能把任务升为 done。
"""

import asyncio
import hashlib
import json

import pytest

from backend import main, worker_manager as worker_manager_module


RESULT_FIELDS = ("result_srt", "result_video", "result_run_id", "output_ready")

EMPTY_QUAD = {"result_srt": None, "result_video": None,
              "result_run_id": None, "output_ready": False}

SRT_BODY = "1\n00:00:00,000 --> 00:00:01,000\n你好\n"


def _quad(payload: dict) -> dict:
    """只取结果四元组。断言用它，避免把整份 meta（含密钥）打进报错。"""
    return {key: payload.get(key) for key in RESULT_FIELDS}


def _empty_quadruple(payload: dict) -> bool:
    """结果四元组整组为空（下架状态）。"""
    quad = _quad(payload)
    return not any(bool(value) for value in quad.values())


def _ffprobe_ok(monkeypatch):
    monkeypatch.setattr(
        "backend.output_artifacts.subprocess.run",
        lambda *args, **kwargs: type(
            "Result", (), {"returncode": 0, "stdout": "mov,mp4", "stderr": ""}
        )(),
    )


def _build_job(
    tmp_path,
    job_id="p388job",
    *,
    status="partial",
    run_id="run-old",
    output_mode="srt",
    video_filename="watch.mp4",
    with_old_results=True,
    extra_meta=None,
):
    """构造一个"上一轮留下了成品"的任务目录。"""
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "input.mp4").write_bytes(b"p388-source-video")
    old_srt = job_dir / "watch.srt"
    old_srt.write_text(SRT_BODY, encoding="utf-8")
    old_video = job_dir / "watch_zh_sub.mp4"
    old_video.write_bytes(b"old-round-video")

    config = {
        "outputMode": output_mode,
        "video_filename": video_filename,
        "savePath": str(tmp_path / "saved"),
    }
    meta = {
        "status": status,
        "file_hash": hashlib.sha256(b"p388-source-video").hexdigest(),
        "video_filename": video_filename,
        "source_path": str(job_dir / "input.mp4"),  # P516：任务只记原视频地址
        "config": config,
        "run_id": run_id,
        "run_kind": "normal",
        "failed_keys": [[1, 0.0, 1.0]],
        "segment_count": 2,
        "success_count": 1,
        "failed_count": 1,
        "retry_count": 1,
        "consecutive_no_progress": 0,
    }
    if with_old_results:
        meta.update({
            "result_srt": str(old_srt),
            "result_video": str(old_video),
            "output_ready": True,
        })
    if extra_meta:
        meta.update(extra_meta)
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return job_dir, meta, old_srt, old_video


def _isolate(monkeypatch, tmp_path, *, jobs=None, alive=False, start=None):
    # 隔离用户真实配置：retry 分支会读全局 apiKeys，测试绝不碰真实密钥。
    monkeypatch.setattr(
        "backend.config_manager.load_config",
        lambda: {
            "apiKeys": [{"key": "fake-test-key-not-real"}],
            "translationPriority": ["google"],
        },
    )
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", jobs if jobs is not None else {})
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {})
    monkeypatch.setattr(main, "JOB_ID_TO_HASH", {})
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda jid: alive)
    monkeypatch.setattr(
        "backend.model_manager.is_model_loadable", lambda _model: True
    )
    started = []
    monkeypatch.setattr(
        main.worker_manager,
        "start",
        start or (lambda jid, d, v, cfg, **kw: started.append((jid, cfg, kw))),
    )
    return started


# ── 组 1：新一轮开始，旧结果四元组立即失去资格 ──

def test_partial_retry_revokes_previous_result_quadruple_on_disk(
    monkeypatch, tmp_path
):
    job_dir, _meta, _srt, _video = _build_job(tmp_path)
    _isolate(monkeypatch, tmp_path)

    response = main.partial_action("p388job", {"action": "retry"})
    assert getattr(response, "status_code", 200) == 200

    written = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert _quad(written) == EMPTY_QUAD, "新一轮重试必须整组撤销旧结果"
    assert written["status"] == "running"
    assert written["partial_resume_action"] == "retry"


def test_partial_retry_revokes_previous_result_quadruple_in_memory(
    monkeypatch, tmp_path
):
    _build_job(tmp_path)
    jobs = {
        "p388job": {
            "id": "p388job",
            "status": "partial",
            "run_id": "run-old",
            "result_srt": str(tmp_path / "p388job" / "watch.srt"),
            "result_video": str(tmp_path / "p388job" / "watch_zh_sub.mp4"),
            "output_ready": True,
        }
    }
    _isolate(monkeypatch, tmp_path, jobs=jobs)

    main.partial_action("p388job", {"action": "retry"})

    assert _quad(main.JOBS["p388job"]) == EMPTY_QUAD, (
        "内存快照也必须整组撤销旧结果"
    )


def test_partial_accept_revokes_previous_result_quadruple(monkeypatch, tmp_path):
    job_dir, _meta, _srt, _video = _build_job(tmp_path)
    _isolate(monkeypatch, tmp_path)

    main.partial_action("p388job", {"action": "accept"})

    written = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert _quad(written) == EMPTY_QUAD, "accept 新一轮同样必须撤销旧结果资格"


def test_resume_revokes_previous_result_quadruple(monkeypatch, tmp_path):
    job_dir, meta, _srt, _video = _build_job(tmp_path, status="interrupted")
    _isolate(monkeypatch, tmp_path)
    existing = {
        "job_id": "p388job",
        "status": "interrupted",
        "config": meta["config"],
        "file_hash": meta["file_hash"],
    }

    main._start_existing_job(existing, str(job_dir / "input.mp4"))

    written = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert _quad(written) == EMPTY_QUAD, "resume 进入新一轮时必须撤销旧结果资格"
    assert _quad(main.JOBS["p388job"]) == EMPTY_QUAD


def test_new_round_writes_new_run_id_before_worker_starts(monkeypatch, tmp_path):
    job_dir, _meta, _srt, _video = _build_job(tmp_path)
    seen_at_start = {}

    def _capture_start(jid, directory, video, cfg, **kwargs):
        seen_at_start["meta"] = json.loads(
            (job_dir / "meta.json").read_text(encoding="utf-8")
        )
        seen_at_start["run_id"] = kwargs.get("run_id")

    _isolate(monkeypatch, tmp_path, start=_capture_start)

    main.partial_action("p388job", {"action": "retry"})

    assert seen_at_start, "Worker 必须被启动"
    assert _quad(seen_at_start["meta"]) == EMPTY_QUAD, (
        "Worker 启动前磁盘上必须已经是空结果四元组"
    )
    assert seen_at_start["meta"]["run_id"] == seen_at_start["run_id"] != "run-old"


# ── 组 2：启动失败不恢复旧结果资格 ──

def test_partial_retry_start_failure_keeps_results_empty_and_surfaces_error(
    monkeypatch, tmp_path
):
    job_dir, _meta, _srt, _video = _build_job(tmp_path)

    def _boom(*args, **kwargs):
        raise RuntimeError("worker 启动失败")

    _isolate(monkeypatch, tmp_path, start=_boom)

    response = main.partial_action("p388job", {"action": "retry"})
    assert getattr(response, "status_code", 200) == 500

    written = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert _quad(written) == EMPTY_QUAD, "启动失败绝不能恢复旧结果资格"
    assert written["status"] == "error", "启动失败必须收敛为 error"
    assert written.get("partial_resume_action") == "retry", (
        "必须保留原动作，用户才能通过 /resume 继续正确动作"
    )
    assert written.get("failed_count") == 1, "统计必须保留"
    assert written.get("error"), "必须留下可见的失败原因"


def test_error_after_start_failure_can_be_resumed_with_original_action(
    monkeypatch, tmp_path
):
    job_dir, _meta, _srt, _video = _build_job(tmp_path)

    def _boom(*args, **kwargs):
        raise RuntimeError("worker 启动失败")

    _isolate(monkeypatch, tmp_path, start=_boom)
    main.partial_action("p388job", {"action": "retry"})

    resumed = []
    monkeypatch.setattr(
        main.worker_manager,
        "start",
        lambda jid, d, v, cfg, **kw: resumed.append(cfg),
    )
    response = main.resume_job("p388job")

    assert getattr(response, "status_code", 200) == 200, (
        f"启动失败后必须能通过现有 /resume 继续，实际: {response}"
    )
    assert resumed and resumed[0].get("_partial_retry") is True, (
        "恢复必须还原为原来的重试动作"
    )


def test_start_new_job_atomic_meta_failure_leaves_no_partial_state(
    monkeypatch, tmp_path
):
    job_dir = tmp_path / "fresh388"
    job_dir.mkdir()
    (job_dir / "input.mp4").write_bytes(b"fresh")
    started = _isolate(monkeypatch, tmp_path)

    def _fail_atomic(path, payload):
        raise OSError("磁盘写入失败")

    monkeypatch.setattr(main, "_write_json_atomic", _fail_atomic)

    with pytest.raises(OSError):
        main._start_new_job(
            "fresh388", job_dir, job_dir / "input.mp4",
            {"video_filename": "input.mp4"}, "hash388", "task388",
        )

    assert not (job_dir / "meta.json").exists(), "不得留下半份 meta"
    assert "fresh388" not in main.JOBS
    assert "task388" not in main.GLOBAL_HASH_INDEX
    assert not started, "meta 写失败时不得启动 Worker"


# ── 组 3：权威 run_id 缺失时安全失败（P1-A） ──

@pytest.mark.parametrize("bad_run_id", [None, "", "   ", 12345])
def test_worker_terminal_rejected_when_authoritative_run_id_is_unusable(
    monkeypatch, tmp_path, bad_run_id
):
    """身份非法时，即使 Worker 报的 run_id "一致"也必须拒绝。"""
    job_dir, _meta, srt, _video = _build_job(tmp_path, status="running")
    job = {"id": "p388job", "status": "running", "progress": 0}
    if bad_run_id is not None:
        job["run_id"] = bad_run_id
    _isolate(monkeypatch, tmp_path, jobs={"p388job": job})

    main._sync_job_from_worker_message("p388job", {
        "type": "done",
        "run_id": bad_run_id,
        "result_srt": str(srt),
        "result_video": None,
        "segment_count": 2,
        "success_count": 2,
    })

    written = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert written["status"] != "done", (
        f"权威 run_id 为 {bad_run_id!r} 时必须拒绝 Worker 终态，不得落盘 done"
    )
    assert main.JOBS["p388job"]["status"] != "done", "内存也不得宣布 done"
    assert _empty_quadruple(main.JOBS["p388job"]), "不得写入任何结果字段"


def test_stale_run_terminal_never_overwrites_current_round(monkeypatch, tmp_path):
    job_dir, _meta, srt, _video = _build_job(tmp_path, status="running")
    _isolate(monkeypatch, tmp_path, jobs={
        "p388job": {"id": "p388job", "status": "running", "run_id": "run-new"}
    })

    main._sync_job_from_worker_message("p388job", {
        "type": "done",
        "run_id": "run-old",
        "result_srt": str(srt),
    })

    written = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert written["status"] == "running"
    assert main.JOBS["p388job"]["status"] == "running"
    assert _empty_quadruple(main.JOBS["p388job"])


# ── 组 4：终态整组替换结果四元组 ──

def test_done_registers_result_run_id_of_the_current_round(monkeypatch, tmp_path):
    _ffprobe_ok(monkeypatch)
    job_dir, _meta, srt, _video = _build_job(
        tmp_path, status="running", with_old_results=False
    )
    _isolate(monkeypatch, tmp_path, jobs={
        "p388job": {"id": "p388job", "status": "running", "run_id": "run-new"}
    })

    main._sync_job_from_worker_message("p388job", {
        "type": "done",
        "run_id": "run-new",
        "result_srt": str(srt),
        "result_video": None,
        "segment_count": 2,
        "success_count": 2,
    })

    written = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert written["status"] == "done"
    assert written["result_run_id"] == "run-new", (
        "登记成品时必须写明它属于哪一轮"
    )
    assert written["result_srt"] == str(srt)
    assert main.JOBS["p388job"]["result_run_id"] == "run-new"


def test_done_does_not_inherit_previous_round_video(monkeypatch, tmp_path):
    _ffprobe_ok(monkeypatch)
    job_dir, _meta, srt, old_video = _build_job(tmp_path, status="running")
    _isolate(monkeypatch, tmp_path, jobs={
        "p388job": {
            "id": "p388job", "status": "running", "run_id": "run-new",
            "result_video": str(old_video), "output_ready": True,
        }
    })

    main._sync_job_from_worker_message("p388job", {
        "type": "done",
        "run_id": "run-new",
        "result_srt": str(srt),
        "segment_count": 2,
        "success_count": 2,
    })

    written = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert not written.get("result_video"), (
        "本轮没有产出视频时，绝不能继承上一轮的视频"
    )
    assert not main.JOBS["p388job"].get("result_video")


def test_error_terminal_clears_stale_output_ready(monkeypatch, tmp_path):
    job_dir, _meta, _srt, _video = _build_job(tmp_path, status="running")
    _isolate(monkeypatch, tmp_path, jobs={
        "p388job": {
            "id": "p388job", "status": "running", "run_id": "run-new",
            "output_ready": True,
        }
    })

    main._sync_job_from_worker_message("p388job", {
        "type": "error",
        "run_id": "run-new",
        "message": "处理失败",
        "stage": "translate",
    })

    written = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert written["status"] == "error"
    assert not written.get("output_ready"), (
        "error 终态不得残留上一轮的 output_ready=True"
    )
    assert not written.get("result_run_id")
    assert not main.JOBS["p388job"].get("output_ready")


def test_partial_terminal_registers_result_run_id(monkeypatch, tmp_path):
    job_dir, _meta, srt, _video = _build_job(
        tmp_path, status="running", with_old_results=False
    )
    _isolate(monkeypatch, tmp_path, jobs={
        "p388job": {"id": "p388job", "status": "running", "run_id": "run-new"}
    })

    main._sync_job_from_worker_message("p388job", {
        "type": "partial",
        "run_id": "run-new",
        "result_srt": str(srt),
        "output_ready": True,
        "failed_count": 1,
        "segment_count": 2,
        "success_count": 1,
    })

    written = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert written["status"] == "partial"
    assert written["result_run_id"] == "run-new"


# ── 组 5：恢复只认当前轮次的明确证据 ──

def test_running_job_with_stale_result_pointer_is_not_promoted_to_done(
    monkeypatch, tmp_path
):
    """P388 路径 1：partial retry 遗留的旧指针不得让恢复直接升 done。"""
    _ffprobe_ok(monkeypatch)
    job_dir, meta, _srt, _video = _build_job(
        tmp_path,
        status="running",
        extra_meta={"run_id": "run-new", "result_run_id": "run-old"},
    )
    _isolate(monkeypatch, tmp_path)

    status = main._reconcile_job_status_on_disk(job_dir, meta)

    assert status != "done", (
        "结果属于上一轮（result_run_id=run-old ≠ run_id=run-new），不得升 done"
    )
    assert _quad(meta) == EMPTY_QUAD, "不属于本轮的结果必须整组下架"


def test_running_job_without_result_run_id_is_not_promoted_to_done(
    monkeypatch, tmp_path
):
    """历史无归属指针（老任务）同样不能被重新认领。"""
    _ffprobe_ok(monkeypatch)
    job_dir, meta, _srt, _video = _build_job(tmp_path, status="running")
    _isolate(monkeypatch, tmp_path)

    status = main._reconcile_job_status_on_disk(job_dir, meta)

    assert status != "done", "缺少归属的结果指针不得升 done"


def test_translate_checkpoint_with_old_subtitle_never_recovers_as_done(
    monkeypatch, tmp_path
):
    """P388 路径 2：非 done checkpoint + 滞后文件名，不得回填 watch.srt。"""
    _ffprobe_ok(monkeypatch)
    job_dir, meta, old_srt, _video = _build_job(
        tmp_path, status="running", with_old_results=False
    )
    (tmp_path / "saved").mkdir(exist_ok=True)
    (tmp_path / "saved" / old_srt.name).write_text(SRT_BODY, encoding="utf-8")
    (job_dir / "real-title.srt").write_text(SRT_BODY, encoding="utf-8")

    digest = hashlib.sha256((job_dir / "input.mp4").read_bytes()).hexdigest()
    cache = job_dir / "cache" / digest[:12]
    cache.mkdir(parents=True)
    (cache / "checkpoint.json").write_text(json.dumps({
        "input_file": str(job_dir / "input.mp4"),
        "input_hash": digest[:16],
        "stage": "translate",
        "meta": meta["config"],
    }), encoding="utf-8")
    _isolate(monkeypatch, tmp_path)

    status = main._reconcile_job_status_on_disk(job_dir, meta)

    assert status != "done", (
        "translate 阶段的 checkpoint 不是完成证据，不得升 done"
    )
    assert meta.get("result_srt") != str(old_srt), (
        "绝不能把上一轮的 watch.srt 回填为当前成品"
    )


def test_done_checkpoint_of_current_run_recovers_with_explicit_paths(
    monkeypatch, tmp_path
):
    """唯一允许的恢复：同 run 的 stage=done checkpoint + 明确结果路径。"""
    _ffprobe_ok(monkeypatch)
    job_dir, meta, _old_srt, _video = _build_job(
        tmp_path, status="running", with_old_results=False,
        extra_meta={"run_id": "run-cur"},
    )
    real_srt = job_dir / "real-title.srt"
    real_srt.write_text(SRT_BODY, encoding="utf-8")
    save_dir = tmp_path / "saved"
    save_dir.mkdir(exist_ok=True)
    (save_dir / real_srt.name).write_text(SRT_BODY, encoding="utf-8")

    digest = hashlib.sha256((job_dir / "input.mp4").read_bytes()).hexdigest()
    cache = job_dir / "cache" / digest[:12]
    cache.mkdir(parents=True)
    (cache / "checkpoint.json").write_text(json.dumps({
        "input_file": str(job_dir / "input.mp4"),
        "input_hash": digest[:16],
        "stage": "done",
        "meta": meta["config"],
        "run_id": "run-cur",
        "result_srt": str(real_srt),
        "result_video": None,
    }), encoding="utf-8")
    _isolate(monkeypatch, tmp_path)

    status = main._reconcile_job_status_on_disk(job_dir, meta)

    assert status == "done", "同一轮的 done checkpoint 带明确路径才允许恢复"
    assert meta["result_srt"] == str(real_srt)
    assert meta["result_run_id"] == "run-cur"


def test_done_checkpoint_from_another_run_is_rejected(monkeypatch, tmp_path):
    """所有其他条件都满足，只有归属对不上——仍必须拒绝。"""
    _ffprobe_ok(monkeypatch)
    job_dir, meta, _old_srt, _video = _build_job(
        tmp_path, status="running", with_old_results=False,
        video_filename="real-title.mp4",
        extra_meta={"run_id": "run-cur"},
    )
    real_srt = job_dir / "real-title.srt"
    real_srt.write_text(SRT_BODY, encoding="utf-8")
    save_dir = tmp_path / "saved"
    save_dir.mkdir(exist_ok=True)
    (save_dir / real_srt.name).write_text(SRT_BODY, encoding="utf-8")

    digest = hashlib.sha256((job_dir / "input.mp4").read_bytes()).hexdigest()
    cache = job_dir / "cache" / digest[:12]
    cache.mkdir(parents=True)
    (cache / "checkpoint.json").write_text(json.dumps({
        "input_file": str(job_dir / "input.mp4"),
        "input_hash": digest[:16],
        "stage": "done",
        "meta": meta["config"],
        "run_id": "run-previous",
        "result_srt": str(real_srt),
    }), encoding="utf-8")
    _isolate(monkeypatch, tmp_path)

    assert main._reconcile_job_status_on_disk(job_dir, meta) != "done", (
        "别的轮次写的 done checkpoint 不能认领为本轮成品"
    )


def test_done_checkpoint_without_run_ownership_is_rejected(monkeypatch, tmp_path):
    """历史 checkpoint 没有归属字段时安全失败，不得靠文件存在升 done。"""
    _ffprobe_ok(monkeypatch)
    job_dir, meta, _old_srt, _video = _build_job(
        tmp_path, status="running", with_old_results=False,
        video_filename="real-title.mp4",
        extra_meta={"run_id": "run-cur"},
    )
    real_srt = job_dir / "real-title.srt"
    real_srt.write_text(SRT_BODY, encoding="utf-8")
    save_dir = tmp_path / "saved"
    save_dir.mkdir(exist_ok=True)
    (save_dir / real_srt.name).write_text(SRT_BODY, encoding="utf-8")

    digest = hashlib.sha256((job_dir / "input.mp4").read_bytes()).hexdigest()
    cache = job_dir / "cache" / digest[:12]
    cache.mkdir(parents=True)
    (cache / "checkpoint.json").write_text(json.dumps({
        "input_file": str(job_dir / "input.mp4"),
        "input_hash": digest[:16],
        "stage": "done",
        "meta": meta["config"],
    }), encoding="utf-8")
    _isolate(monkeypatch, tmp_path)

    assert main._reconcile_job_status_on_disk(job_dir, meta) != "done", (
        "无归属的 done checkpoint 不构成本轮完成证据"
    )


def test_reconcile_atomic_writeback_failure_does_not_announce_done(
    monkeypatch, tmp_path
):
    _ffprobe_ok(monkeypatch)
    job_dir, meta, _old_srt, _video = _build_job(
        tmp_path, status="running", with_old_results=False,
        video_filename="real-title.mp4",
        extra_meta={"run_id": "run-cur"},
    )
    real_srt = job_dir / "real-title.srt"
    real_srt.write_text(SRT_BODY, encoding="utf-8")
    save_dir = tmp_path / "saved"
    save_dir.mkdir(exist_ok=True)
    (save_dir / real_srt.name).write_text(SRT_BODY, encoding="utf-8")
    digest = hashlib.sha256((job_dir / "input.mp4").read_bytes()).hexdigest()
    cache = job_dir / "cache" / digest[:12]
    cache.mkdir(parents=True)
    (cache / "checkpoint.json").write_text(json.dumps({
        "input_file": str(job_dir / "input.mp4"),
        "input_hash": digest[:16],
        "stage": "done",
        "meta": meta["config"],
        "run_id": "run-cur",
        "result_srt": str(real_srt),
    }), encoding="utf-8")
    _isolate(monkeypatch, tmp_path)

    def _fail_atomic(path, payload):
        raise OSError("磁盘写入失败")

    monkeypatch.setattr(main, "_write_json_atomic", _fail_atomic)

    status = main._reconcile_job_status_on_disk(job_dir, meta)

    assert status != "done", (
        "回写失败时内存不得比磁盘更早宣布 done"
    )


def test_saved_copy_alone_never_satisfies_recovery(monkeypatch, tmp_path):
    """成品只存在于保存目录时，不得认领它（回归保护）。"""
    _ffprobe_ok(monkeypatch)
    job_dir, meta, old_srt, _video = _build_job(
        tmp_path, status="running", with_old_results=False,
        video_filename="real-title.mp4",
        extra_meta={"run_id": "run-cur"},
    )
    old_srt.unlink()
    save_dir = tmp_path / "saved"
    save_dir.mkdir(exist_ok=True)
    (save_dir / "real-title.srt").write_text(SRT_BODY, encoding="utf-8")

    digest = hashlib.sha256((job_dir / "input.mp4").read_bytes()).hexdigest()
    cache = job_dir / "cache" / digest[:12]
    cache.mkdir(parents=True)
    (cache / "checkpoint.json").write_text(json.dumps({
        "input_file": str(job_dir / "input.mp4"),
        "input_hash": digest[:16],
        "stage": "done",
        "meta": meta["config"],
        "run_id": "run-cur",
        "result_srt": str(job_dir / "real-title.srt"),
    }), encoding="utf-8")
    _isolate(monkeypatch, tmp_path)

    assert main._reconcile_job_status_on_disk(job_dir, meta) != "done", (
        "任务目录内没有成品时，不得拿保存目录的副本顶替"
    )
    assert not meta.get("result_srt")


def test_reconcile_never_touches_files_outside_the_job_directory(
    monkeypatch, tmp_path
):
    """越界指针必须安全失败，且不读不删任务目录外的文件。"""
    _ffprobe_ok(monkeypatch)
    outside = tmp_path / "outside.srt"
    outside.write_text(SRT_BODY, encoding="utf-8")
    job_dir, meta, _srt, _video = _build_job(
        tmp_path, status="running", with_old_results=False,
        extra_meta={
            "run_id": "run-cur",
            "result_run_id": "run-cur",
            "result_srt": str(outside),
            "output_ready": True,
        },
    )
    _isolate(monkeypatch, tmp_path)

    status = main._reconcile_job_status_on_disk(job_dir, meta)

    assert status != "done", "任务目录之外的结果指针必须被拒绝"
    assert outside.exists(), "绝不能删除任务目录外的文件"


# ── 组 6：REST 出口统一过滤 ──

def test_active_jobs_never_exposes_results_from_a_previous_run(
    monkeypatch, tmp_path
):
    job_dir, _meta, srt, _video = _build_job(tmp_path, status="partial")
    _isolate(monkeypatch, tmp_path)
    main.GLOBAL_HASH_INDEX["tk"] = {
        "job_id": "p388job",
        "status": "partial",
        "config": {},
        "run_id": "run-new",
        "result_run_id": "run-old",
        "result_srt": str(srt),
        "output_ready": True,
    }
    main.JOB_ID_TO_HASH["p388job"] = "tk"

    payload = main.get_active_jobs()
    entry = payload["jobs"][0]

    assert entry["state"]["result"]["output_ready"] is False
    assert "result_srt" not in json.dumps(entry, ensure_ascii=False)


def test_active_jobs_recoverability_does_not_depend_on_stale_results(
    monkeypatch, tmp_path
):
    _build_job(tmp_path, status="error")
    _isolate(monkeypatch, tmp_path)
    entry = {
        "job_id": "p388job",
        "status": "error",
        "config": {},
        "run_id": "run-new",
        "result_run_id": "run-old",
        "result_srt": str(tmp_path / "p388job" / "watch.srt"),
    }

    assert main._is_recoverable_active_job("p388job", entry) is False, (
        "发现资格不得由上一轮的 result_srt 决定"
    )


def test_cold_meta_get_job_filters_unowned_results(monkeypatch, tmp_path):
    job_dir, _meta, srt, _video = _build_job(
        tmp_path,
        status="done",
        extra_meta={"run_id": "run-new", "result_run_id": "run-old"},
    )
    _isolate(monkeypatch, tmp_path)

    payload = main.get_job("p388job")

    assert payload["state"]["result"]["output_ready"] is False
    assert str(srt) not in json.dumps(payload, ensure_ascii=False), (
        "冷 meta 读取也不得公开不属于当前轮次的结果路径"
    )


# ── 组 7：只广播父进程验证过的可信快照 ──

def _patch_ws(monkeypatch):
    """拦截 WebSocket 广播，返回收到的消息列表。"""
    import backend.ws_manager as ws_module

    sent = []

    async def _send(job_id, message):
        sent.append(message)

    monkeypatch.setattr(ws_module.ws_manager, "send", _send, raising=False)
    return sent


def test_websocket_never_broadcasts_a_rejected_worker_terminal(monkeypatch):
    """父进程拒绝的终态不得原文直发给前端。"""
    # B3 起终态改由 _handle_terminal 在读取线程里交给父进程处理
    # （交付事务会阻塞，不能占事件循环），合同本身没变。
    sent = []
    manager = worker_manager_module.WorkerManager()
    manager.set_terminal_callback(lambda job_id, msg, publish: None)
    manager._send = lambda job_id, message, loop: sent.append(message)

    manager._handle_terminal("p388job", {
        "type": "done",
        "run_id": "run-old",
        "result_srt": "/tmp/watch.srt",
    }, None)

    assert not sent, (
        "父进程没有返回可信快照时，不得把原始 Worker 终态广播给前端"
    )


def test_websocket_broadcasts_the_trusted_snapshot_not_the_worker_claim(
    monkeypatch
):
    sent = []
    manager = worker_manager_module.WorkerManager()
    trusted = main.job_state.build_public_task_state({
        "id": "p388job", "status": "done", "run_id": "run-new",
        "result_run_id": "run-new", "result_srt": "/jobs/p388job/real-title.srt",
        "output_ready": True,
    })
    manager.set_terminal_callback(lambda job_id, msg, publish: trusted)
    manager._send = lambda job_id, message, loop: sent.append(message)

    manager._handle_terminal("p388job", {
        "type": "done",
        "run_id": "run-new",
        "result_srt": "/jobs/p388job/watch.srt",
    }, None)

    assert len(sent) == 1
    assert sent == [{"type": "state", "state": trusted}]
    encoded = json.dumps(sent, ensure_ascii=False)
    assert "/jobs/p388job/real-title.srt" not in encoded
    assert "/jobs/p388job/watch.srt" not in encoded


# ── 组 8：Worker 侧的运行身份与整轮独占锁 ──

def test_url_reuse_names_outputs_after_the_real_video(tmp_path, monkeypatch):
    """P388 路径 2 根因：复用本地视频时输出名不能是网址占位名（watch）。

    否则输出路径被推导成 watch.srt，恢复时就会把上一轮的 watch.srt 当成本轮成品。
    P516 起名字不再单独存、不再改名：成品名直接从下载好的文件地址算。"""
    from backend import worker

    job_dir = tmp_path / "urljob"
    job_dir.mkdir()
    real_video = job_dir / "London VLOG_video.mp4"
    real_video.write_bytes(b"downloaded")
    # P443：本条只验证文件名同步。这个假视频当作自带音轨处理，走 URL 内嵌音轨分支，
    # 避免因为音频合同提前退出而验不到原本要验的东西。
    monkeypatch.setattr("backend.core.extractor.has_audio_stream", lambda path: "has_audio")
    config = {
        "source_url": "https://www.youtube.com/watch?v=abc",
        "_partial_retry": True,
    }

    returned = worker._download_source_if_url(config, job_dir, str(real_video))

    assert returned == str(real_video)
    assert worker._derive_output_identity(returned)[0] == "London VLOG_video", (
        "成品名必须是真实文件名，不是网址占位名"
    )


def test_done_checkpoint_is_stamped_with_run_and_explicit_results(tmp_path):
    """done checkpoint 必须记录本轮身份和明确成品路径，供崩溃恢复取证。

    A9：身份与完成状态已并入 Checkpoint.save() 一次原子写入，
    不再事后直读—改—替换（那条绕过封装的路径已收回）。"""
    from backend.core.checkpoint import Checkpoint

    source = tmp_path / "input.mp4"
    source.write_bytes(b"video")
    ck_path = tmp_path / "checkpoint.json"

    Checkpoint(str(ck_path), str(source)).save(
        "done", meta={"outputMode": "srt"},
        run_id="run-1", result_srt="/jobs/j/real.srt", result_video=None,
    )

    data = json.loads(ck_path.read_text(encoding="utf-8"))
    assert data["run_id"] == "run-1"
    assert data["result_srt"] == "/jobs/j/real.srt"
    assert data["result_video"] is None
    assert data["stage"] == "done", "不得破坏原有 checkpoint 内容"
    assert data["meta"] == {"outputMode": "srt"}, "配置快照必须原样保留"


def test_worker_job_lock_blocks_a_second_worker_on_the_same_job(tmp_path):
    """同一任务目录不得同时有两个 Worker 在写。"""
    from backend import worker

    job_dir = tmp_path / "lockjob"
    job_dir.mkdir()

    first = worker._acquire_job_lock(job_dir)
    assert first is not None, "第一个 Worker 必须拿得到锁"
    try:
        assert worker._acquire_job_lock(job_dir) is None, (
            "第二个 Worker 必须被拒绝"
        )
    finally:
        worker._release_job_lock(first)

    second = worker._acquire_job_lock(job_dir)
    assert second is not None, "释放后必须可以重新获取"
    worker._release_job_lock(second)


def test_existing_lock_file_alone_does_not_mean_occupied(tmp_path):
    """锁文件残留不等于任务被占用，否则重启后永远起不来。"""
    from backend import worker

    job_dir = tmp_path / "stalelock"
    job_dir.mkdir()
    (job_dir / ".worker.lock").write_text("", encoding="utf-8")

    handle = worker._acquire_job_lock(job_dir)

    assert handle is not None, "只有活着的持锁进程才算占用"
    worker._release_job_lock(handle)
