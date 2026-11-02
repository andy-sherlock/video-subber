"""A1：公开结果出口收敛为单一投影函数。

结果四元组（result_srt / result_video / result_run_id / output_ready）只能经
`_public_result_projection()` 生成。前端只拿一个受控布尔：can_show_output_folder。

B3 回退（2026-07-19）之后不再有交付状态机：成品由 Worker 在宣告完成之前送到
用户目录，所以"完成"就等于"已送达"，判据是本轮终态加成品归属，没有中间态。

`delivery` 字段在新模型下不再产生，但 B3 时代的旧任务 meta.json 里可能残留。
它含用户目标绝对路径和内容摘要，必须继续被剥掉，永不出现在 REST / WebSocket /
JOBS 公开对象里。
"""

import json

import pytest

from backend import main


SRT_BODY = "1\n00:00:00,000 --> 00:00:01,000\n你好\n"
RESULT_FIELDS = ("result_srt", "result_video", "result_run_id", "output_ready")
PROJECTION_FIELDS = ("can_show_output_folder",)


def _flags(payload: dict) -> dict:
    """只取公开布尔投影，避免断言失败时展开整份 meta。"""
    if isinstance(payload.get("state"), dict):
        payload = payload["state"]["result"]
    return {key: payload.get(key) for key in PROJECTION_FIELDS}


def _quad(payload: dict) -> dict:
    return {key: payload.get(key) for key in RESULT_FIELDS}


def _build_job(tmp_path, job_id="a1job", *, run_id="run-now", delivery=None,
               status="running", extra_meta=None):
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True)
    srt = job_dir / "clip.srt"
    srt.write_text(SRT_BODY, encoding="utf-8")
    meta = {
        "status": status,
        "file_hash": "a" * 64,
        "video_filename": "clip.mp4",
        "config": {"outputMode": "srt", "video_filename": "clip.mp4",
                   "savePath": str(tmp_path / "saved")},
        "run_id": run_id,
        "run_kind": "normal",
        "result_srt": str(srt),
        "result_video": None,
        "result_run_id": run_id,
        "output_ready": True,
    }
    if delivery is not None:
        meta["delivery"] = delivery
    if extra_meta:
        meta.update(extra_meta)
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return job_dir, meta, srt


def _legacy_delivery(run_id, source, target):
    """B3 时代残留在旧任务 meta.json 里的交付记录。

    新模型不产生也不读它，但它带着用户目标绝对路径和摘要，必须继续被剥掉。"""
    return {
        "run_id": run_id, "output_mode": "srt", "state": "delivered",
        "files": [{"run_id": run_id, "output_mode": "srt", "kind": "srt",
                   "source": str(source), "target": str(target),
                   "size": 12, "sha256": "b" * 64, "state": "delivered"}],
    }


def _isolate(monkeypatch, tmp_path, jobs=None):
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", jobs if jobs is not None else {})
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {})
    monkeypatch.setattr(main, "JOB_ID_TO_HASH", {})
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda jid: False)
    monkeypatch.setattr(
        "backend.model_manager.is_model_loadable", lambda _model: True
    )


# ── 投影函数本身 ──────────────────────────────────────────────────────────

def test_projection_while_running_offers_no_output_folder(tmp_path):
    """还没到终态就还没送达，布尔必须是 False，且必须真的存在（不是 None）。"""
    job_dir, meta, _srt = _build_job(tmp_path, status="running")

    projection = main._public_result_projection(meta, job_dir)

    assert _flags(projection) == {"can_show_output_folder": False}


def test_projection_ignores_a_legacy_delivery_record(tmp_path):
    """B3 残留的 delivery 记录不再参与判定，一个字节都不许影响投影。"""
    job_dir, meta, srt = _build_job(tmp_path, status="running")
    meta["delivery"] = _legacy_delivery("run-now", srt,
                                        tmp_path / "saved" / "clip.srt")

    projection = main._public_result_projection(meta, job_dir)

    assert _flags(projection) == {"can_show_output_folder": False}
    assert "delivery" not in projection


def test_projection_allows_output_folder_on_terminal_status(tmp_path):
    """终态即已送达：done / partial 且成品归属成立时才给入口。"""
    job_dir, meta, srt = _build_job(tmp_path, status="done")

    projection = main._public_result_projection(meta, job_dir)

    assert _flags(projection) == {"can_show_output_folder": True}
    assert projection["result_srt"] == str(srt)

    meta["status"] = "partial"
    assert main._public_result_projection(meta, job_dir)[
        "can_show_output_folder"] is True


def test_projection_refuses_output_folder_without_output_ready(tmp_path):
    """终态但成品没就绪（例如只有半份结果）→ 不给入口。"""
    job_dir, meta, _srt = _build_job(tmp_path, status="done")
    meta["output_ready"] = False

    projection = main._public_result_projection(meta, job_dir)

    assert _flags(projection) == {"can_show_output_folder": False}


@pytest.mark.parametrize("status", ["done", "partial"])
def test_projection_ignores_results_from_another_run(tmp_path, status):
    """上一轮的成品没有任何公开资格，终态也不行。"""
    job_dir, meta, _srt = _build_job(tmp_path, run_id="run-new", status=status)
    meta["result_run_id"] = "run-old"

    projection = main._public_result_projection(meta, job_dir)

    assert _flags(projection) == {"can_show_output_folder": False}
    assert _quad(projection) == {"result_srt": None, "result_video": None,
                                 "result_run_id": None, "output_ready": False}


def test_projection_booleans_are_real_booleans_not_records(tmp_path):
    """can_show_output_folder 只能是布尔值，不得把任何记录透出去。"""
    job_dir, meta, srt = _build_job(tmp_path, status="done")
    meta["delivery"] = _legacy_delivery("run-now", srt,
                                        tmp_path / "saved" / "clip.srt")

    projection = main._public_result_projection(meta, job_dir)

    for key in PROJECTION_FIELDS:
        assert isinstance(projection[key], bool), key


# ── REST 出口 ─────────────────────────────────────────────────────────────

def test_rest_get_job_never_leaks_legacy_delivery_record(tmp_path, monkeypatch):
    # 目标目录故意与 config.savePath 不同：savePath 是用户设置，本来就公开；
    # 真正不许外泄的是残留交付记录里的目标绝对路径和摘要。
    secret_target = tmp_path / "elsewhere" / "clip.srt"
    job_dir, meta, srt = _build_job(tmp_path, status="done")
    meta["delivery"] = _legacy_delivery("run-now", srt, secret_target)
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    _isolate(monkeypatch, tmp_path)

    payload = main.get_job("a1job")

    assert "delivery" not in payload
    assert str(secret_target) not in json.dumps(payload, ensure_ascii=False)
    assert "b" * 64 not in json.dumps(payload, ensure_ascii=False)
    assert _flags(payload) == {"can_show_output_folder": True}


def test_rest_get_job_while_running_offers_no_output_folder(tmp_path, monkeypatch):
    """还在跑的任务不得给「在文件夹中显示」入口。"""
    job_dir, meta, srt = _build_job(tmp_path, status="running")
    jobs = {"a1job": {"id": "a1job", "status": "running", "progress": 99,
                      "stage": "copy", "message": "", "config": meta["config"],
                      "run_id": "run-now", "run_kind": "normal",
                      "result_srt": str(srt), "result_video": None,
                      "result_run_id": "run-now", "output_ready": True}}
    _isolate(monkeypatch, tmp_path, jobs=jobs)
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda jid: True)

    payload = main.get_job("a1job")

    assert payload["state"]["status"] == "running"
    assert _flags(payload) == {"can_show_output_folder": False}


def test_rest_active_jobs_projects_booleans_and_hides_delivery(tmp_path, monkeypatch):
    secret_target = tmp_path / "elsewhere" / "clip.srt"
    job_dir, meta, srt = _build_job(tmp_path, status="running")
    meta["delivery"] = _legacy_delivery("run-now", srt, secret_target)
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    _isolate(monkeypatch, tmp_path)
    main.GLOBAL_HASH_INDEX["tk"] = {
        "job_id": "a1job", "status": "running", "config": meta["config"],
        "file_hash": meta["file_hash"], "run_id": "run-now",
        "delivery": meta["delivery"], "result_srt": str(srt),
        "result_run_id": "run-now", "output_ready": True,
    }

    payload = main.get_active_jobs()

    entry = payload["jobs"][0]
    assert "delivery" not in entry
    assert str(secret_target) not in json.dumps(payload, ensure_ascii=False)
    assert _flags(entry) == {"can_show_output_folder": False}


def test_cold_meta_get_job_projects_the_same_flags(tmp_path, monkeypatch):
    """内存里没有条目、只靠磁盘 meta 时也必须走同一个投影。"""
    job_dir, meta, srt = _build_job(tmp_path, status="done")
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    _isolate(monkeypatch, tmp_path)

    payload = main.get_job("a1job")

    assert _flags(payload) == {"can_show_output_folder": True}
    assert "delivery" not in payload


def test_sanitize_task_payload_strips_delivery_at_every_depth(tmp_path):
    """兜底防线：脱敏函数本身就必须把 delivery 摘掉。"""
    payload = {"a": {"delivery": {"target": "/Users/secret/out.mp4"}},
               "b": [{"delivery": {"target": "/Users/secret/out.srt"}}]}

    cleaned = main._sanitize_task_payload(payload)

    assert "/Users/secret" not in json.dumps(cleaned, ensure_ascii=False)


# ── 新 run 清空 ───────────────────────────────────────────────────────────

def test_new_run_clears_delivery_and_quadruple_together(tmp_path, monkeypatch):
    """开始新一轮时结果四元组必须整组清掉，残留的 delivery 一并清成 None。"""
    job_dir, meta, srt = _build_job(tmp_path, status="interrupted")
    meta["delivery"] = _legacy_delivery("run-now", srt,
                                        tmp_path / "saved" / "clip.srt")
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    _isolate(monkeypatch, tmp_path)
    started = {}
    monkeypatch.setattr(
        main.worker_manager, "start",
        lambda jid, jdir, vpath, cfg, **kw: started.update(kw) or "run-fresh",
    )

    main._start_existing_job(
        {"job_id": "a1job", "status": "interrupted", "config": meta["config"],
         "file_hash": meta["file_hash"]},
        str(job_dir / "input.mp4"),
    )

    written = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert written.get("delivery") is None
    assert _quad(written) == {"result_srt": None, "result_video": None,
                              "result_run_id": None, "output_ready": False}
