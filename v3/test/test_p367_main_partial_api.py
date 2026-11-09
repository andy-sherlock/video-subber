"""P367：partial 专用动作、状态持久化与任务响应 Key 边界。"""

import json
import os
import time
import hashlib

from fastapi.testclient import TestClient

from backend import main


def test_task_payload_recursively_removes_api_keys_and_values():
    fake_secret = "fake-secret-only-for-test"
    payload = {
        "config": {"apiKeys": [{"key": fake_secret}]},
        "nested": [{"apiKeys": [{"key": fake_secret}], "copy": fake_secret}],
    }

    sanitized = main._sanitize_task_payload(payload)
    encoded = json.dumps(sanitized)

    assert "apiKeys" not in encoded
    assert fake_secret not in encoded


def test_config_endpoint_remains_the_only_plain_key_channel(monkeypatch):
    fake_secret = "fake-config-secret"
    monkeypatch.setattr(
        "backend.config_manager.load_config",
        lambda: {"apiKeys": [{"key": fake_secret}], "translationPriority": []},
    )

    response = main.get_config()

    assert response["apiKeys"][0]["key"] == fake_secret


def test_partial_retry_uses_this_run_keys(monkeypatch, tmp_path):
    """D-1 补修 A3：Key 用前端点重试时检测过的那份（D-6 P503 起不再刷新免费翻译顺序）。"""
    fake_current = "fake-current-key"
    fake_old = "fake-old-key"
    job_id = "abc123"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    (job_dir / "input.mp4").write_bytes(b"video")
    meta = {
        "status": "partial",
        "config": {
            "apiKeys": [{"key": fake_old}],
            "translationPriority": ["google"],
        },
        "source_path": str(job_dir / "input.mp4"),  # P516：任务只记原视频地址
        "failed_keys": [[1, 0.0, 1.0]],
        "segment_count": 2,
        "success_count": 1,
        "failed_count": 1,
        "output_ready": False,
        "retry_count": 1,
        "consecutive_no_progress": 0,
    }
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    starts = []
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {})
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda jid: False)
    monkeypatch.setattr(
        main.worker_manager,
        "start",
        lambda jid, directory, video, config, **kwargs: starts.append(config.copy()),
    )
    monkeypatch.setattr(
        "backend.config_manager.load_config",
        lambda: {
            "apiKeys": [{"key": fake_current}],
            "translationPriority": ["bing", "youdao"],
        },
    )
    main.JOBS[job_id] = {"id": job_id, "status": "partial"}

    response = main.partial_action(
        job_id, {"action": "retry", "apiKeys": [{"key": fake_current}]})

    assert response["ok"] is True
    assert response["job_id"] == job_id
    assert response["action"] == "retry"
    assert "apiKeys" not in response["config"]
    assert starts[0]["apiKeys"] == [{"key": fake_current}]
    assert starts[0]["_partial_retry"] is True
    assert starts[0]["_retry_count"] == 2


def test_partial_retry_atomically_persists_authoritative_config_action_and_indexes(
    monkeypatch, tmp_path
):
    """P369：启动 worker 前，磁盘配置、action 和两个索引必须成为同一状态。"""
    job_id = "atomicretry"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    video = job_dir / "input.mp4"
    video.write_bytes(b"video")
    old_config = {
        "video_filename": "input.mp4", "outputMode": "mux",
        "apiKeys": [{"key": "old"}],
    }
    file_hash = hashlib.sha256(video.read_bytes()).hexdigest()
    # 升级前的任务存的是旧算法的身份键（D-6 P503 起重试本身不再改变身份键）
    old_key = f"{file_hash}_oldstylekey0"
    original_meta = {
        "status": "partial", "file_hash": file_hash, "task_key": old_key,
        "source_path": str(video),  # P516
        "config": old_config, "failed_keys": [[1, 0.0, 1.0]],
        "segment_count": 2, "success_count": 1, "failed_count": 1,
        "output_ready": False, "retry_count": 1,
        "consecutive_no_progress": 0,
    }
    (job_dir / "meta.json").write_text(json.dumps(original_meta), encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda jid: False)
    monkeypatch.setattr(main.worker_manager, "start", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "backend.config_manager.load_config",
        lambda: {"apiKeys": [{"key": "new"}], "translationPriority": ["bing"]},
    )
    monkeypatch.setattr(main, "JOBS", {job_id: {"id": job_id, "status": "partial"}})
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {
        old_key: {"job_id": job_id, "status": "partial", "config": old_config,
                  "file_hash": file_hash}
    })
    monkeypatch.setattr(main, "JOB_ID_TO_HASH", {job_id: old_key})

    response = main.partial_action(job_id, {"action": "retry", "apiKeys": [{"key": "new"}]})

    saved = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    new_key = main._task_key(file_hash, saved["config"])
    assert saved["status"] == "running"
    assert saved["partial_resume_action"] == "retry"
    assert saved["retry_count"] == 2
    assert saved["config"]["apiKeys"] == [{"key": "new"}]
    assert saved["task_key"] == new_key
    assert old_key not in main.GLOBAL_HASH_INDEX
    assert main.GLOBAL_HASH_INDEX[new_key]["job_id"] == job_id
    assert main.JOB_ID_TO_HASH[job_id] == new_key
    assert main.JOBS[job_id]["status"] == "running"
    assert "apiKeys" not in response["config"]


def test_partial_retry_task_key_collision_returns_409_without_mutation(monkeypatch, tmp_path):
    """P369：新配置已被别的任务占用时，不能静默覆盖任务索引。

    D-6 P503 起重试不再刷新免费引擎顺序，任务身份键只会因「旧任务存的是旧算法的键」而变
    （升级前的任务）；这里用一个旧键模拟它。"""
    job_id = "collision"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    video = job_dir / "input.mp4"
    video.write_bytes(b"video")
    file_hash = hashlib.sha256(video.read_bytes()).hexdigest()
    old_config = {"video_filename": "input.mp4"}
    new_config = old_config
    old_key = f"{file_hash}_oldstylekey0"
    new_key = main._task_key(file_hash, new_config)
    meta = {
        "status": "partial", "file_hash": file_hash, "task_key": old_key,
        "source_path": str(video),  # P516
        "config": old_config, "failed_keys": [[1, 0.0, 1.0]],
        "segment_count": 2, "success_count": 1, "failed_count": 1,
        "output_ready": False, "retry_count": 0,
    }
    meta_path = job_dir / "meta.json"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    starts = []
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda jid: False)
    monkeypatch.setattr(
        main.worker_manager, "start",
        lambda *args, **kwargs: starts.append((args, kwargs)),
    )
    monkeypatch.setattr(
        "backend.config_manager.load_config",
        lambda: {"apiKeys": [], "translationPriority": ["bing"]},
    )
    monkeypatch.setattr(main, "JOBS", {job_id: {"id": job_id, "status": "partial"}})
    original_indexes = {
        old_key: {"job_id": job_id, "status": "partial", "config": old_config,
                  "file_hash": file_hash},
        new_key: {"job_id": "other-job", "status": "running", "config": new_config,
                  "file_hash": file_hash},
    }
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", json.loads(json.dumps(original_indexes)))
    monkeypatch.setattr(main, "JOB_ID_TO_HASH", {job_id: old_key, "other-job": new_key})

    response = main.partial_action(job_id, {"action": "retry"})

    assert response.status_code == 409
    assert starts == []
    assert json.loads(meta_path.read_text(encoding="utf-8")) == meta
    assert main.JOB_ID_TO_HASH[job_id] == old_key
    assert main.GLOBAL_HASH_INDEX[new_key]["job_id"] == "other-job"


def test_partial_retry_worker_start_failure_converges_to_resumable_error(monkeypatch, tmp_path):
    """P369/P388：worker 启动失败时不得留在 running。

    P388 改变了收敛方向：不回滚成 partial（start() 可能已拉起进程，回退 run_id
    会让孤儿 Worker 的消息被当成当前轮次），而是收敛为 error + 空结果四元组，
    保留原动作和统计，让用户通过 /resume 继续同一动作。"""
    job_id = "rollback"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    video = job_dir / "input.mp4"
    video.write_bytes(b"video")
    file_hash = hashlib.sha256(video.read_bytes()).hexdigest()
    config = {"video_filename": "input.mp4", "translationPriority": ["google"]}
    old_key = main._task_key(file_hash, config)
    meta = {
        "status": "partial", "file_hash": file_hash, "task_key": old_key,
        "source_path": str(video),  # P516
        "config": config, "failed_keys": [[1, 0.0, 1.0]],
        "segment_count": 2, "success_count": 1, "failed_count": 1,
        "output_ready": False, "retry_count": 0,
    }
    meta_path = job_dir / "meta.json"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda jid: False)
    monkeypatch.setattr(main.worker_manager, "start", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(
        "backend.config_manager.load_config",
        lambda: {"apiKeys": [], "translationPriority": ["bing"]},
    )
    monkeypatch.setattr(main, "JOBS", {job_id: {"id": job_id, "status": "partial"}})
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {
        old_key: {"job_id": job_id, "status": "partial", "config": config,
                  "file_hash": file_hash}
    })
    monkeypatch.setattr(main, "JOB_ID_TO_HASH", {job_id: old_key})

    response = main.partial_action(job_id, {"action": "retry"})

    assert response.status_code == 500
    saved = json.loads(meta_path.read_text(encoding="utf-8"))
    assert saved["status"] == "error", "不得留在 running"
    assert saved["partial_resume_action"] == "retry", "原动作必须保留"
    assert saved["failed_keys"] == [[1, 0.0, 1.0]], "统计必须保留"
    assert saved["error"], "必须留下可见的失败原因"
    assert not any(saved.get(key) for key in main._RESULT_FIELDS), (
        "启动失败不得留下任何成品资格"
    )
    # 磁盘、索引、内存共用同一个新身份，/resume 才能读到正确动作。
    new_key = main.JOB_ID_TO_HASH[job_id]
    assert saved["task_key"] == new_key
    assert main.GLOBAL_HASH_INDEX[new_key]["status"] == "error"
    assert main.JOBS[job_id]["status"] == "error"


def test_error_resume_restores_persisted_partial_retry_identity(monkeypatch, tmp_path):
    """P369：Retry 后只在封装阶段失败，再恢复仍要跳过 Refiner 和全片翻译。"""
    job_id = "retryerror"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    video = job_dir / "input.mp4"
    video.write_bytes(b"video")
    config = {"video_filename": "input.mp4", "model": "large-v2"}
    meta = {
        "status": "error", "config": config,
        "partial_resume_action": "retry", "retry_count": 2,
        "failed_keys": [[1, 0.0, 1.0]], "consecutive_no_progress": 1,
    }
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    new_dir = tmp_path / "throwaway"
    new_dir.mkdir()
    starts = []
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {})
    monkeypatch.setattr(
        main.worker_manager, "start",
        lambda *args, **kwargs: starts.append((args, kwargs)),
    )
    monkeypatch.setattr("backend.model_manager.is_model_loadable", lambda model: True)
    existing = {"job_id": job_id, "status": "error", "config": config}

    response = main._start_existing_job(existing, "")

    assert response["status"] == "running"
    run_config = starts[0][0][3]
    assert run_config["_partial_retry"] is True
    assert run_config["_retry_count"] == 2
    assert run_config["_previous_failed_keys"] == [[1, 0.0, 1.0]]
    assert run_config["_consecutive_no_progress"] == 1
    assert starts[0][1]["run_kind"] == "partial_retry"


def test_done_message_clears_partial_action_and_persists_output_paths(monkeypatch, tmp_path):
    """P369/P365：真正完成后才清 action，并把结果路径写进磁盘 meta。

    P388：结果只在核对完运行身份和文件内容后才登记，并写明属于哪一轮。"""
    job_id = "doneclear"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    (job_dir / "watch.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8"
    )
    (job_dir / "watch.mp4").write_bytes(b"video")
    monkeypatch.setattr(
        "backend.output_artifacts.subprocess.run",
        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "mp4"})(),
    )
    meta_path = job_dir / "meta.json"
    meta_path.write_text(json.dumps({
        "status": "running", "config": {}, "partial_resume_action": "retry",
        "run_id": "run-1",
    }), encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {
        job_id: {"id": job_id, "status": "running", "progress": 90, "config": {},
                 "run_id": "run-1"}
    })

    main._sync_job_from_worker_message(job_id, {
        "type": "done", "run_id": "run-1",
        "result_srt": str(job_dir / "watch.srt"),
        "result_video": str(job_dir / "watch.mp4"), "failed_segments": [],
        "segment_count": 2, "success_count": 2,
    })

    saved = json.loads(meta_path.read_text(encoding="utf-8"))
    assert "partial_resume_action" not in saved
    assert saved["result_srt"].endswith("watch.srt")
    assert saved["result_video"].endswith("watch.mp4")
    assert saved["result_run_id"] == "run-1", "成品必须写明属于哪一轮"


def test_partial_terminal_clears_action_but_error_keeps_it(monkeypatch, tmp_path):
    """P369：回到 partial 可重新选择；error/interrupted 仍需保留续跑身份。"""
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    for job_id, message, should_keep in (
        ("partialclear", {"type": "partial", "failed_count": 1,
                           "failed_keys": [[1, 0.0, 1.0]], "retry_count": 2,
                           "output_ready": False}, False),
        ("errorkeep", {"type": "error", "message": "mux failed",
                        "stage": "burn"}, True),
    ):
        job_dir = tmp_path / job_id
        job_dir.mkdir()
        meta_path = job_dir / "meta.json"
        meta_path.write_text(json.dumps({
            "status": "running", "config": {}, "partial_resume_action": "retry",
            "run_id": "run-1",
        }), encoding="utf-8")
        main.JOBS[job_id] = {"id": job_id, "status": "running", "progress": 90,
                             "run_id": "run-1"}
        main._sync_job_from_worker_message(job_id, {**message, "run_id": "run-1"})
        saved = json.loads(meta_path.read_text(encoding="utf-8"))
        assert ("partial_resume_action" in saved) is should_keep


def test_partial_action_rejects_missing_original_and_snapshot(monkeypatch, tmp_path):
    job_id = "missing1"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    (job_dir / "meta.json").write_text(json.dumps({
        "status": "partial",
        "config": {"source_dir": str(tmp_path / "gone"), "video_filename": "gone.mp4"},
    }), encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda jid: False)

    response = main.partial_action(job_id, {"action": "retry"})

    assert response.status_code == 409
    assert "找不到原视频" in response.body.decode()  # ⑦.5 R4：提示改成大白话


# P516：「按文件名猜链接任务下载的视频」（_partial_video_path）已删除——下载完成时地址写进
# 任务记录（合同增量 Z1），三条猜测测试随之删除；新合同见 test_d1c_batch2_p516.py。


def test_partial_retry_passes_url_video_path_to_worker(monkeypatch, tmp_path):
    job_id = "urlretry"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    video = job_dir / "标题_video.mp4"
    video.write_bytes(b"video")
    (job_dir / "meta.json").write_text(json.dumps({
        "status": "partial",
        "config": {"source_url": "https://example.test/v"},
        "source_path": str(video),  # P516 / Z1：下载完成时记下的地址
        "failed_keys": [[1, 0.0, 1.0]],
        "segment_count": 2,
        "success_count": 1,
        "failed_count": 1,
        "output_ready": False,
        "retry_count": 0,
    }), encoding="utf-8")
    starts = []
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {})
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda jid: False)
    monkeypatch.setattr(main.worker_manager, "start", lambda jid, directory, path, config, **kwargs: starts.append((path, config.copy())))
    monkeypatch.setattr("backend.config_manager.load_config", lambda: {"apiKeys": [], "translationPriority": []})

    response = main.partial_action(job_id, {"action": "retry"})

    assert response["ok"] is True
    assert response["job_id"] == job_id
    assert response["action"] == "retry"
    assert response["config"]["source_url"] == "https://example.test/v"
    assert starts[0][0] == str(video)


def test_partial_action_cold_start_builds_complete_job_from_meta(monkeypatch, tmp_path):
    job_id = "coldmeta"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    (job_dir / "watch.mp4").write_bytes(b"video")
    meta = {
        "status": "partial",
        "config": {},
        "source_path": str(job_dir / "watch.mp4"),  # P516：任务只记原视频地址
        "segment_count": 465,
        "success_count": 396,
        "failed_count": 69,
        "failed_keys": [[index, float(index), float(index + 1)] for index in range(69)],
        "retry_count": 2,
        "consecutive_no_progress": 1,
        "recovered_count": 0,
        "primary_failure_kind": "numbering_format",
        "failure_kind_counts": {"numbering_format": 69},
        "output_ready": False,
    }
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    starts = []
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {})
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {})
    monkeypatch.setattr(main, "JOB_ID_TO_HASH", {})
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda jid: False)
    monkeypatch.setattr(main.worker_manager, "start", lambda *args, **kwargs: starts.append(args))
    monkeypatch.setattr("backend.config_manager.load_config", lambda: {"apiKeys": [], "translationPriority": []})

    response = main.partial_action(job_id, {"action": "retry"})

    assert response["ok"] is True
    assert response["job_id"] == job_id
    assert response["action"] == "retry"
    assert main.JOBS[job_id]["source_path"] == str(job_dir / "watch.mp4")  # P516：名字从地址现算
    assert len(starts) == 1
    job = main.JOBS[job_id]
    assert job["segment_count"] == 465
    assert job["success_count"] == 396
    assert job["failed_count"] == 69
    assert len(job["failed_keys"]) == 69
    assert job["snapshot_source"] == "meta"
    assert job["snapshot_incomplete"] is False
    assert job["status"] == "running"
    assert job["progress"] == 0


def test_partial_snapshot_recomputes_missing_counts_from_matching_checkpoint(tmp_path):
    job_dir = tmp_path / "cachejob"
    video = job_dir / "input.mp4"
    video.parent.mkdir()
    video.write_bytes(b"current-video")
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    cache = job_dir / "cache" / digest[:12]
    cache.mkdir(parents=True)
    rows = [
        {"id": index, "start": float(index), "end": float(index + 1),
         "zh": "[翻译失败]" if index >= 396 else "成功"}
        for index in range(465)
    ]
    (cache / "translated.json").write_text(
        json.dumps(rows, ensure_ascii=False), encoding="utf-8"
    )
    config = {"video_filename": "input.mp4", "outputMode": "srt"}
    (cache / "checkpoint.json").write_text(json.dumps({
        "input_file": str(video), "input_hash": digest[:16],
        "stage": "translate", "meta": config,
    }), encoding="utf-8")

    snapshot = main._partial_snapshot_from_disk(
        job_dir, {
            "status": "partial", "retry_count": 3,
            "file_hash": digest, "config": config,
        }
    )

    assert snapshot["snapshot_source"] == "cache_recomputed"
    assert snapshot["snapshot_incomplete"] is False
    assert snapshot["segment_count"] == 465
    assert snapshot["success_count"] == 396
    assert snapshot["failed_count"] == 69
    assert len(snapshot["failed_keys"]) == 69
    assert snapshot["retry_count"] == 3


def test_partial_snapshot_does_not_use_arbitrary_cache_without_identity(tmp_path):
    """P365：缺身份的旧 translated.json 不能替当前任务补状态。"""
    job_dir = tmp_path / "ambiguous"
    cache = job_dir / "cache" / "old"
    cache.mkdir(parents=True)
    (cache / "translated.json").write_text(json.dumps([
        {"id": 0, "start": 0.0, "end": 1.0, "zh": "[翻译失败]"}
    ]), encoding="utf-8")

    snapshot = main._partial_snapshot_from_disk(
        job_dir, {"status": "partial", "config": {}, "retry_count": 1}
    )

    assert snapshot["snapshot_source"] == "meta_incomplete"
    assert snapshot["snapshot_incomplete"] is True


def test_partial_action_refreshes_stale_memory_snapshot(monkeypatch, tmp_path):
    job_id = "stalejob"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    video = job_dir / "input.mp4"
    video.write_bytes(b"video")
    file_hash = hashlib.sha256(video.read_bytes()).hexdigest()
    old_task_key = main._task_key(file_hash, {})
    (job_dir / "meta.json").write_text(json.dumps({
        "status": "partial",
        "config": {},
        "source_path": str(video),  # P516
        "file_hash": file_hash,
        "task_key": old_task_key,
        "segment_count": 10,
        "success_count": 7,
        "failed_count": 3,
        "failed_keys": [[7, 7.0, 8.0], [8, 8.0, 9.0], [9, 9.0, 10.0]],
        "retry_count": 1,
        "output_ready": False,
    }), encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {
        job_id: {"id": job_id, "status": "partial", "segment_count": 0, "failed_count": 0}
    })
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {
        old_task_key: {"job_id": job_id, "status": "partial", "config": {}}
    })
    monkeypatch.setattr(main, "JOB_ID_TO_HASH", {job_id: old_task_key})
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda jid: False)
    monkeypatch.setattr(main.worker_manager, "start", lambda *args, **kwargs: None)
    monkeypatch.setattr("backend.config_manager.load_config", lambda: {"apiKeys": [], "translationPriority": []})

    main.partial_action(job_id, {"action": "retry"})

    assert main.JOBS[job_id]["segment_count"] == 10
    assert main.JOBS[job_id]["failed_count"] == 3


def test_partial_action_rejects_incomplete_snapshot_without_starting_worker(
    monkeypatch, tmp_path
):
    job_id = "brokenpartial"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    (job_dir / "input.mp4").write_bytes(b"video")
    (job_dir / "meta.json").write_text(json.dumps({
        "status": "partial", "config": {}, "retry_count": 1,
        "source_path": str(job_dir / "input.mp4"),  # P516：原视频在，查的是记录不完整
    }), encoding="utf-8")
    starts = []
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {})
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda jid: False)
    monkeypatch.setattr(main.worker_manager, "start", lambda *args, **kwargs: starts.append(args))

    response = main.partial_action(job_id, {"action": "retry"})

    assert response.status_code == 409
    assert "任务记录不完整" in response.body.decode()
    assert starts == []


def test_partial_snapshot_and_done_recovery_both_reject_unidentified_cache(tmp_path):
    job_dir = tmp_path / "separate"
    cache = job_dir / "cache" / "hash"
    cache.mkdir(parents=True)
    rows = [
        {"id": 0, "start": 0.0, "end": 1.0, "zh": "成功"},
        {"id": 1, "start": 1.0, "end": 2.0, "zh": "[翻译失败]"},
    ]
    (cache / "translated.json").write_text(json.dumps(rows), encoding="utf-8")

    snapshot = main._partial_snapshot_from_disk(job_dir, {"status": "partial"})
    legacy = main._legacy_failed_translation_state(job_dir)

    assert snapshot["snapshot_source"] == "meta_incomplete"
    assert snapshot["snapshot_incomplete"] is True
    assert "status" not in snapshot
    assert legacy is None


def test_done_job_is_not_demoted_by_old_failed_translation_cache(tmp_path):
    """P365：done 任务目录里残留旧失败缓存，也不能被降成 partial。"""
    job_dir = tmp_path / "done-old-cache"
    cache = job_dir / "cache" / "old"
    cache.mkdir(parents=True)
    (cache / "translated.json").write_text(json.dumps([
        {"id": 0, "start": 0.0, "end": 1.0, "zh": "[翻译失败]"}
    ]), encoding="utf-8")

    assert main._legacy_failed_translation_state(job_dir) is None


def test_old_llm_fallback_endpoint_returns_404():
    client = TestClient(main.app)

    response = client.post("/api/jobs/no-such/llm-fallback", json={"decision": "use_free"})

    assert response.status_code == 404


def test_get_job_and_active_jobs_strip_keys(monkeypatch, tmp_path):
    fake_secret = "fake-task-secret"
    job_id = "safejob"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    meta = {
        "status": "partial",
        "config": {"apiKeys": [{"key": fake_secret}]},
    }
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {})
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {})
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda jid: False)
    main.JOBS[job_id] = {
        "id": job_id,
        "status": "partial",
        "config": {"apiKeys": [{"key": fake_secret}]},
        "copy": fake_secret,
    }
    main.GLOBAL_HASH_INDEX["fake-task"] = {
        "job_id": job_id,
        "status": "partial",
        "output_ready": False,
        "config": {"apiKeys": [{"key": fake_secret}]},
    }

    job_payload = main.get_job(job_id)
    active_payload = main.get_active_jobs()
    encoded = json.dumps([job_payload, active_payload])

    assert "apiKeys" not in encoded
    assert fake_secret not in encoded


def test_worker_message_projects_to_a_safe_websocket_state(monkeypatch, tmp_path):
    fake_secret = "fake-ws-secret"
    job_id = "wsjob"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    (job_dir / "meta.json").write_text(json.dumps({
        "status": "running", "config": {}, "run_id": "run-1"
    }), encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    main.JOBS[job_id] = {
        "id": job_id, "status": "running", "progress": 0, "run_id": "run-1"
    }
    message = {
        "type": "progress",
        "run_id": "run-1",
        "percent": 1,
        "config": {"apiKeys": [{"key": fake_secret}]},
        "copy": fake_secret,
    }

    state = main._sync_job_from_worker_message(job_id, message)
    encoded = json.dumps(state)

    assert "apiKeys" not in encoded
    assert fake_secret not in encoded
    assert set(state) == set(main.job_state.PUBLIC_TASK_STATE_FIELDS)


def test_structured_error_persists_and_survives_cold_get(monkeypatch, tmp_path):
    job_id = "outputerror"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    srt = job_dir / "watch.srt"
    # P388：结果要经过内容验证才登记，字幕必须是真的字幕。
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")
    (job_dir / "meta.json").write_text(json.dumps({
        "status": "running",
        "config": {"outputMode": "mux"},
        "video_filename": "watch",
        "run_id": "run-1",
    }), encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {
        job_id: {"id": job_id, "status": "running", "progress": 92,
                 "run_id": "run-1"}
    })
    message = {
        "type": "error",
        "run_id": "run-1",
        "stage": "burn",
        "failure_kind": "output_mux_failed",
        "message": "字幕文件已生成，但视频封装失败。",
        "technical_detail": "stderr=SAR DAR",
        "result_srt": str(srt),
        "result_video": None,
        "output_ready": False,
        "failed_segments": [],
    }

    main._sync_job_from_worker_message(job_id, message)

    assert main.JOBS[job_id]["status"] == "error"
    assert main.JOBS[job_id]["progress"] == 100
    assert main.JOBS[job_id]["failure_kind"] == "output_mux_failed"
    saved = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert saved["status"] == "error"
    assert saved["stage"] == "burn"
    assert "technical_detail" not in saved
    assert saved["result_srt"] == str(srt)

    main.JOBS.clear()
    restored = main.get_job(job_id)

    assert restored["state"]["status"] == "error"
    assert restored["state"]["phase"] == "burn"
    assert restored["state"]["display_message"] == "字幕文件已生成，但视频封装失败。"
    public_json = json.dumps(restored, ensure_ascii=False)
    assert "stderr=SAR DAR" not in public_json
    assert str(srt) not in public_json


def test_only_error_with_srt_is_recoverable_active_job(monkeypatch, tmp_path):
    """P388：发现资格只认属于当前轮次的 SRT，旧字段不算数。"""
    job_id = "recovererror"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)

    assert main._is_recoverable_active_job(job_id, {
        "status": "error", "run_id": "run-1", "result_run_id": "run-1",
        "result_srt": str(job_dir / "watch.srt"),
    }) is True
    assert main._is_recoverable_active_job(
        job_id, {"status": "error", "run_id": "run-1", "result_srt": None}
    ) is False
    assert main._is_recoverable_active_job(job_id, {
        "status": "error", "run_id": "run-2", "result_run_id": "run-1",
        "result_srt": str(job_dir / "watch.srt"),
    }) is False, "上一轮的 SRT 不能让任务保持可恢复"


def test_active_jobs_restores_structured_error_with_srt(monkeypatch, tmp_path):
    job_id = "activeerror"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    srt = job_dir / "watch.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {})
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {
        "task": {
            "job_id": job_id,
            "status": "error",
            "config": {"outputMode": "mux"},
            "stage": "burn",
            "failure_kind": "output_mux_failed",
            "technical_detail": "stderr=noise",
            "run_id": "run-1",
            "result_run_id": "run-1",
            "result_srt": str(srt),
            "result_video": None,
            "output_ready": False,
            "error": "字幕文件已生成，但视频封装失败。",
        }
    })

    payload = main.get_active_jobs()

    assert len(payload["jobs"]) == 1
    restored = payload["jobs"][0]
    assert restored["state"]["status"] == "error"
    assert restored["state"]["phase"] == "burn"
    public_json = json.dumps(restored, ensure_ascii=False)
    assert "stderr=noise" not in public_json
    assert str(srt) not in public_json


def test_final_output_validation_respects_mode_format_and_job_boundary(monkeypatch, tmp_path):
    job_dir = tmp_path / "outputs"
    job_dir.mkdir()
    srt = job_dir / "watch.srt"
    video = job_dir / "watch_zh_sub.mp4"
    outside = tmp_path / "outside.srt"
    valid_srt = "1\n00:00:00,000 --> 00:00:01,000\nHello\n"
    srt.write_text(valid_srt, encoding="utf-8")
    video.write_bytes(b"video")
    outside.write_text(valid_srt, encoding="utf-8")
    monkeypatch.setattr(
        "backend.output_artifacts.subprocess.run",
        lambda *args, **kwargs: type("Result", (), {
            "returncode": 0, "stdout": "mov,mp4", "stderr": ""
        })(),
    )

    # P388：结果先要属于当前轮次，才轮得到检查模式、格式和目录边界。
    owned = {"run_id": "run-1", "result_run_id": "run-1"}

    assert main._has_valid_final_outputs(job_dir, {
        **owned, "config": {"outputMode": "srt"}, "result_srt": str(srt)
    }) is True
    assert main._has_valid_final_outputs(job_dir, {
        **owned, "config": {"burn": False}, "result_srt": str(srt)
    }) is True
    assert main._has_valid_final_outputs(job_dir, {
        **owned, "config": {}, "result_srt": str(srt), "result_video": str(video)
    }) is True
    assert main._has_valid_final_outputs(job_dir, {
        **owned, "config": {"outputMode": "mux"}, "result_srt": str(srt)
    }) is False
    assert main._has_valid_final_outputs(job_dir, {
        **owned, "config": {"outputMode": "invalid"},
        "result_srt": str(srt), "result_video": str(video),
    }) is False
    assert main._has_valid_final_outputs(job_dir, {
        **owned, "config": {"outputMode": "srt"}, "result_srt": str(outside)
    }) is False
    # 归属缺失或对不上时，文件再有效也不算本轮成品。
    assert main._has_valid_final_outputs(job_dir, {
        "config": {"outputMode": "srt"}, "result_srt": str(srt)
    }) is False
    assert main._has_valid_final_outputs(job_dir, {
        "run_id": "run-2", "result_run_id": "run-1",
        "config": {"outputMode": "srt"}, "result_srt": str(srt),
    }) is False


def test_reconcile_only_recomputes_active_statuses(monkeypatch, tmp_path):
    job_dir = tmp_path / "reconcile"
    job_dir.mkdir()
    srt = job_dir / "watch.srt"
    video = job_dir / "watch_zh_sub.mp4"
    srt.write_text("1\n", encoding="utf-8")
    video.write_bytes(b"video")

    for status in ("done", "partial", "error", "interrupted", "cancelled"):
        meta = {
            "status": status,
            "config": {"outputMode": "mux"},
            "result_srt": str(srt),
            "result_video": str(video),
        }
        assert main._reconcile_job_status_on_disk(job_dir, meta) == status

    running_only_srt = {
        "status": "running",
        "config": {"outputMode": "mux"},
        "result_srt": str(srt),
        "result_video": None,
    }
    assert main._reconcile_job_status_on_disk(job_dir, running_only_srt) == "interrupted"


def test_reconcile_recovers_done_checkpoint_before_meta_received_output_paths(
    tmp_path, monkeypatch
):
    """P365：worker 已写 done checkpoint、但完成消息未到时，应回填精确成品。

    B3 起恢复还要把交付补上，所以要隔离 JOBS_DIR。"""
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    job_dir = tmp_path / "checkpointdone"
    video = job_dir / "input.mp4"
    video.parent.mkdir()
    video.write_bytes(b"same-run-video")
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    cache = job_dir / "cache" / digest[:12]
    cache.mkdir(parents=True)
    srt = job_dir / "watch.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n",
        encoding="utf-8",
    )
    save_dir = tmp_path / "saved"
    save_dir.mkdir()
    (save_dir / srt.name).write_bytes(srt.read_bytes())
    config = {
        "outputMode": "srt",
        "video_filename": "watch.mp4",
        "savePath": str(save_dir),
    }
    # P388：done checkpoint 必须写明本轮身份和明确的成品路径，
    # 恢复不再从滞后的 meta 文件名推导。
    (cache / "checkpoint.json").write_text(json.dumps({
        "input_file": str(video),
        "input_hash": digest[:16],
        "stage": "done",
        "meta": config,
        "run_id": "run-1",
        "result_srt": str(srt),
        "result_video": None,
    }), encoding="utf-8")
    meta = {
        "status": "running",
        "file_hash": digest,
        "video_filename": "watch.mp4",
        "config": config,
        "run_id": "run-1",
    }
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    assert main._reconcile_job_status_on_disk(job_dir, meta) == "done"
    saved = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert saved["result_srt"] == str(srt)
    assert saved["result_video"] is None
    assert saved["result_run_id"] == "run-1"


def test_reconcile_recovers_valid_outputs_before_checkpoint_reaches_done(
    monkeypatch, tmp_path
):
    """P372：成品已有效时，checkpoint 仍停在 translate 也应恢复完成。

    B3 起恢复还要把交付补上，所以要隔离 JOBS_DIR。"""
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    job_dir = tmp_path / "outputsbeforedone"
    video = job_dir / "input.mp4"
    video.parent.mkdir()
    video.write_bytes(b"same-run-video")
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    cache = job_dir / "cache" / digest[:12]
    cache.mkdir(parents=True)
    srt = job_dir / "watch.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n",
        encoding="utf-8",
    )
    output_video = job_dir / "watch_zh_sub.mp4"
    output_video.write_bytes(b"valid-video")
    monkeypatch.setattr(
        "backend.output_artifacts.subprocess.run",
        lambda *args, **kwargs: type(
            "Result", (), {"returncode": 0, "stdout": "mov,mp4", "stderr": ""}
        )(),
    )
    save_dir = tmp_path / "saved"
    save_dir.mkdir()
    (save_dir / srt.name).write_bytes(srt.read_bytes())
    (save_dir / output_video.name).write_bytes(output_video.read_bytes())
    config = {
        "outputMode": "mux",
        "video_filename": "watch.mp4",
        "savePath": str(save_dir),
    }
    (cache / "checkpoint.json").write_text(json.dumps({
        "input_file": str(video),
        "input_hash": digest[:16],
        "stage": "translate",
        "meta": config,
    }), encoding="utf-8")
    # P388：本轮已经登记过成品，恢复走 meta 的明确指针；
    # 停在 translate 的 checkpoint 本身不构成完成证据。
    meta = {
        "status": "running",
        "file_hash": digest,
        "video_filename": "watch.mp4",
        "config": config,
        "run_id": "run-1",
        "result_run_id": "run-1",
        "result_srt": str(srt),
        "result_video": str(output_video),
        "output_ready": True,
    }
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    assert main._reconcile_job_status_on_disk(job_dir, meta) == "done"
    saved = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert saved["result_srt"] == str(srt)
    assert saved["result_video"] == str(output_video)

    # 同样的磁盘状态，只是结果属于上一轮 → 绝不能恢复成 done。
    stale = {**meta, "status": "running", "result_run_id": "run-0"}
    (job_dir / "meta.json").write_text(json.dumps(stale), encoding="utf-8")
    assert main._reconcile_job_status_on_disk(job_dir, stale) == "interrupted"


def test_reconcile_checks_default_save_path_when_config_omits_save_path(
    monkeypatch, tmp_path
):
    """P372：缺少显式保存路径时，也必须检查实际使用的默认目录。"""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    job_dir = tmp_path / "defaultsave"
    video = job_dir / "input.mp4"
    video.parent.mkdir()
    video.write_bytes(b"same-run-video")
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    cache = job_dir / "cache" / digest[:12]
    cache.mkdir(parents=True)
    srt = job_dir / "watch.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n",
        encoding="utf-8",
    )
    config = {"outputMode": "srt", "video_filename": "watch.mp4"}
    (cache / "checkpoint.json").write_text(json.dumps({
        "input_file": str(video),
        "input_hash": digest[:16],
        "stage": "translate",
        "meta": config,
    }), encoding="utf-8")
    meta = {"status": "running", "file_hash": digest, "config": config}

    assert main._reconcile_job_status_on_disk(job_dir, meta) == "interrupted"


def test_reconcile_rejects_done_checkpoint_with_invalid_srt(tmp_path):
    """P365：只存在文件名不算完成，坏 SRT 不能把 running 提升为 done。"""
    job_dir = tmp_path / "badcheckpoint"
    video = job_dir / "input.mp4"
    video.parent.mkdir()
    video.write_bytes(b"same-run-video")
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    cache = job_dir / "cache" / digest[:12]
    cache.mkdir(parents=True)
    (job_dir / "watch.srt").write_text("1\n", encoding="utf-8")
    config = {"outputMode": "srt", "video_filename": "watch.mp4"}
    (cache / "checkpoint.json").write_text(json.dumps({
        "input_file": str(video), "input_hash": digest[:16],
        "stage": "done", "meta": config,
    }), encoding="utf-8")
    meta = {"status": "running", "file_hash": digest, "config": config}

    assert main._reconcile_job_status_on_disk(job_dir, meta) == "interrupted"


def test_live_get_job_uses_same_done_checkpoint_recovery_as_startup(monkeypatch, tmp_path):
    """P365：服务没重启、只发现 worker 已退出时，也要走同一份恢复判断。"""
    job_id = "livecheckpoint"
    job_dir = tmp_path / job_id
    video = job_dir / "input.mp4"
    video.parent.mkdir()
    video.write_bytes(b"same-run-video")
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    cache = job_dir / "cache" / digest[:12]
    cache.mkdir(parents=True)
    srt = job_dir / "watch.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8"
    )
    save_dir = tmp_path / "saved"
    save_dir.mkdir()
    (save_dir / srt.name).write_bytes(srt.read_bytes())
    config = {
        "outputMode": "srt",
        "video_filename": "watch.mp4",
        "savePath": str(save_dir),
    }
    (cache / "checkpoint.json").write_text(json.dumps({
        "input_file": str(video), "input_hash": digest[:16],
        "stage": "done", "meta": config,
        "run_id": "run-1", "result_srt": str(srt), "result_video": None,
    }), encoding="utf-8")
    (job_dir / "meta.json").write_text(json.dumps({
        "status": "running", "file_hash": digest,
        "video_filename": "watch.mp4", "config": config,
        "run_id": "run-1",
    }), encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda jid: False)
    monkeypatch.setattr(main, "JOBS", {
        job_id: {"id": job_id, "status": "running", "progress": 98,
                 "config": config, "run_id": "run-1"}
    })

    response = main.get_job(job_id)

    assert response["state"]["status"] == "done"
    assert response["state"]["result"]["output_ready"] is True
    assert str(srt) not in json.dumps(response, ensure_ascii=False)


def test_rejected_accept_partial_message_keeps_disk_and_memory_partial(
    monkeypatch, tmp_path
):
    job_id = "rejectaccept"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    original = {
        "status": "partial",
        "segment_count": 3,
        "success_count": 2,
        "failed_count": 1,
        "output_ready": False,
        "failed_keys": [[2, 2.0, 3.0]],
        "retry_count": 4,
        "consecutive_no_progress": 1,
    }
    (job_dir / "meta.json").write_text(
        json.dumps({**original, "run_id": "run-1"}), encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {
        job_id: {"id": job_id, "status": "partial", "progress": 100,
                 "run_id": "run-1"}
    })
    message = {
        "type": "partial",
        "run_id": "run-1",
        **{key: value for key, value in original.items() if key != "status"},
        "action_error": "翻译缓存与当前字幕不匹配，任务仍保留",
    }

    main._sync_job_from_worker_message(job_id, message)

    saved = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert main.JOBS[job_id]["status"] == "partial"
    assert saved["status"] == "partial"
    assert saved["failed_keys"] == original["failed_keys"]
    assert saved["retry_count"] == original["retry_count"]
    assert saved["consecutive_no_progress"] == original["consecutive_no_progress"]
    assert "action_error" not in saved
    assert main.JOBS[job_id]["action_error"] == message["action_error"]
    assert message["type"] == "partial"


def test_partial_action_returns_409_while_worker_is_running(monkeypatch, tmp_path):
    job_id = "busyjob"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    (job_dir / "input.mp4").write_bytes(b"video")
    (job_dir / "meta.json").write_text(json.dumps({
        "status": "partial", "config": {}
    }), encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda jid: True)

    response = main.partial_action(job_id, {"action": "retry"})

    assert response.status_code == 409


def test_done_job_with_unidentified_failed_cache_stays_done(monkeypatch, tmp_path):
    old_id = "legacyjob"
    old_dir = tmp_path / old_id
    cache_dir = old_dir / "cache" / "hash"
    cache_dir.mkdir(parents=True)
    rows = [
        {"id": 0, "start": 0.0, "end": 1.0, "text": "one", "zh": "成功"},
        {"id": 1, "start": 1.0, "end": 2.0, "text": "two", "zh": "[翻译失败]"},
    ]
    (cache_dir / "translated.json").write_text(
        json.dumps(rows, ensure_ascii=False), encoding="utf-8"
    )
    (old_dir / "meta.json").write_text(json.dumps({
        "status": "done", "config": {}, "file_hash": "fake"
    }), encoding="utf-8")
    new_dir = tmp_path / "newjob"
    new_dir.mkdir()
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {})
    existing = {"job_id": old_id, "status": "done", "config": {}}

    response = main._handle_existing_job(existing, new_dir, "", {})

    assert response["state"]["status"] == "done"
    assert not new_dir.exists()


def test_delete_job_removes_the_video_kept_in_the_job_folder(monkeypatch, tmp_path):
    # P516：快照已删除；任务文件夹里的视频（开发期上传 / 链接下载）随任务删除，外部原视频不碰（N1）
    job_id = "deletejob"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    snapshot = job_dir / "clip.mov"
    snapshot.write_bytes(b"video")
    (job_dir / "meta.json").write_text(json.dumps({"source_path": str(snapshot)}), encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main.worker_manager, "cancel", lambda jid: None)

    response = main.cancel_job(job_id)

    assert response == {"ok": True}
    assert not snapshot.exists()


def test_thirty_day_cleanup_keeps_partial_translation_cache(monkeypatch, tmp_path):
    job_dir = tmp_path / "partialold"
    cache = job_dir / "cache" / "hash"
    cache.mkdir(parents=True)
    (job_dir / "meta.json").write_text(json.dumps({
        "status": "partial"
    }), encoding="utf-8")
    translated = cache / "translated.json"
    translated.write_text("[]", encoding="utf-8")
    old = time.time() - 40 * 24 * 3600
    os.utime(translated, (old, old))
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)

    main._cleanup_old_cache()

    assert translated.exists()
