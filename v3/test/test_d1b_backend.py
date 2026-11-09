"""D-1 补修（2026-10-02）后端合同测试。

合同来源：pipelines/方案-D1补修-2026-10-02.md + bug-pipeline-2026-10-02-D1补修.md
（② 主审 / ② 主审追加 / ③ / ④ / ⑤.5）。

A3  继续 / 重试用「本次」apiKeys，并写回任务记录
A5  只翻译取「或」
D2  「保留未翻译字幕并生成文件」不处理 apiKeys
B1  干活程序没留原因就停 → 中断（与启动扫描、按编号查状态同一规则）
C1  force 字段不再起作用
C2  重新提交命中中断 / 出错任务：只返回原卡片，不启动
"""

import json

import pytest

from backend import main


OLD_KEYS = [{"platform": "deepseek", "key": "fake-old-key"}]
NEW_KEYS = [{"platform": "deepseek", "key": "fake-new-key"}]


def _write_job(tmp_path, job_id, *, status, config=None, **extra):
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    (job_dir / "clip.mp4").write_bytes(b"video")
    meta = {
        "status": status,
        "config": config if config is not None else {
            "model": "large-v2", "apiKeys": OLD_KEYS,
        },
        "source_path": str(job_dir / "clip.mp4"),  # P516：任务只记原视频地址
        "file_hash": "f" * 64,
        **extra,
    }
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return job_dir


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    starts = []
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {})
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {})
    monkeypatch.setattr(main, "JOB_ID_TO_HASH", {})
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda jid: False)
    monkeypatch.setattr(
        main.worker_manager, "start",
        lambda jid, directory, video, config, **kw: starts.append(
            json.loads(json.dumps(config))),
    )
    monkeypatch.setattr("backend.model_manager.is_model_loadable", lambda m: True)
    monkeypatch.setattr(main, "_resume_cache_problem", lambda *a, **k: None)
    return starts


def _meta(tmp_path, job_id):
    return json.loads((tmp_path / job_id / "meta.json").read_text(encoding="utf-8"))


# ── A3：继续用本次 Key，并写回 ─────────────────────────────────────────

@pytest.fixture
def saved_new_keys(monkeypatch):
    # P512：Key 只从本机设置文件读
    monkeypatch.setattr("backend.config_manager.load_config",
                        lambda: {"apiKeys": NEW_KEYS, "translationPriority": ["bing"]})


def test_resume_runs_with_the_settings_file_keys_and_writes_them_back(isolated, tmp_path, saved_new_keys):
    _write_job(tmp_path, "rk1", status="interrupted")

    response = main.resume_job("rk1", {})

    assert response["status"] == "running"
    assert isolated[0]["apiKeys"] == NEW_KEYS, "后台必须用设置文件里的当前 Key 运行"
    assert _meta(tmp_path, "rk1")["config"]["apiKeys"] == NEW_KEYS, (
        "任务记录里的设置 = 上一次真正运行用的设置（D3 写回）"
    )
    assert "fake-new-key" not in json.dumps(response), "返回给前端的内容不得带 Key"


def test_resume_keeps_other_old_settings(isolated, tmp_path):
    _write_job(tmp_path, "rk2", status="interrupted", config={
        "model": "large-v3", "video_filename": "input.mp4",
        "apiKeys": OLD_KEYS, "translationMode": "quality",
    })

    main.resume_job("rk2", {"apiKeys": NEW_KEYS})

    assert isolated[0]["model"] == "large-v3"
    assert isolated[0]["translationMode"] == "quality"


def test_resume_without_body_uses_the_settings_file_keys(isolated, tmp_path, saved_new_keys):
    # P512：取代「不带 Key 就沿用旧 Key」——刚开软件网页还没读回设置时点继续，也用当前 Key
    _write_job(tmp_path, "rk3", status="interrupted")

    main.resume_job("rk3")

    assert isolated[0]["apiKeys"] == NEW_KEYS

# P512：「网页带来的 Key 格式不对返回 400」已随网页不再带 Key 删除；后台不读请求里的 Key
# （见 test_d1c_batch2_backend.py::test_resume_ignores_keys_sent_by_the_page）。


def test_resume_rejects_non_boolean_translate_only(isolated, tmp_path):
    _write_job(tmp_path, "rk5", status="interrupted")

    response = main.resume_job("rk5", {"apiKeys": NEW_KEYS, "aiTranslateOnly": "yes"})

    assert response.status_code == 400
    assert isolated == []


# ── A5：只翻译取「或」 ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "old, sent, expected",
    [(False, True, True), (True, None, True), (False, None, False), (True, False, True)],
)
def test_resume_translate_only_is_old_or_this_run(isolated, tmp_path, old, sent, expected):
    config = {"model": "large-v2", "video_filename": "input.mp4", "apiKeys": OLD_KEYS}
    if old:
        config["aiTranslateOnly"] = True
    _write_job(tmp_path, "to1", status="interrupted", config=config)
    body = {"apiKeys": NEW_KEYS}
    if sent is not None:
        body["aiTranslateOnly"] = sent

    main.resume_job("to1", body)

    assert bool(isolated[0].get("aiTranslateOnly")) is expected
    assert bool(_meta(tmp_path, "to1")["config"].get("aiTranslateOnly")) is expected


# ── A3：重试失败部分用本次 Key；D2：导出不碰 Key ─────────────────────

def _partial_job(tmp_path, job_id):
    return _write_job(
        tmp_path, job_id, status="partial",
        config={"apiKeys": OLD_KEYS, "translationPriority": ["google"]},
        failed_keys=[[1, 0.0, 1.0]], segment_count=2, success_count=1,
        failed_count=1, output_ready=False, retry_count=0,
        consecutive_no_progress=0,
    )


def test_partial_retry_uses_the_settings_file_keys(isolated, tmp_path, monkeypatch):
    _partial_job(tmp_path, "pr1")
    monkeypatch.setattr(
        "backend.config_manager.load_config",
        lambda: {"apiKeys": [{"platform": "gpt", "key": "fake-saved-key"}],
                 "translationPriority": ["bing"]},
    )

    response = main.partial_action("pr1", {"action": "retry", "apiKeys": NEW_KEYS})

    assert response["ok"] is True
    saved = [{"platform": "gpt", "key": "fake-saved-key"}]
    assert isolated[0]["apiKeys"] == saved, "P512：重试用设置文件里的当前 Key，不认网页带来的"
    assert _meta(tmp_path, "pr1")["config"]["apiKeys"] == saved


# P512：「重试时网页带来的 Key 格式不对返回 400」已随网页不再带 Key 删除。


def test_partial_accept_ignores_api_keys(isolated, tmp_path):
    _partial_job(tmp_path, "pa1")

    main.partial_action("pa1", {"action": "accept", "apiKeys": NEW_KEYS})

    assert isolated[0]["_accept_partial"] is True
    assert isolated[0]["apiKeys"] == OLD_KEYS, "导出不处理 Key（worker 内现算为空）"
    assert _meta(tmp_path, "pa1")["config"]["apiKeys"] == OLD_KEYS


# ── C2：重新提交命中中断 / 出错任务，只认出、不启动 ─────────────────

@pytest.mark.parametrize("status", ["interrupted", "error"])
def test_resubmit_matching_unfinished_job_only_returns_its_card(isolated, tmp_path, status):
    _write_job(tmp_path, "old1", status=status, error="上一轮的原因")
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    existing = {"job_id": "old1", "status": status,
                "config": {"model": "large-v2"}, "file_hash": "f" * 64}

    response = main._handle_existing_job(
        existing, prepared, str(prepared / "input.mp4"), {"apiKeys": NEW_KEYS})

    assert isolated == [], "重新提交不得自动开跑"
    assert response["found"] is True
    assert response["job_id"] == "old1"
    assert response["state"]["status"] == status
    assert not prepared.exists(), "刚准备的临时任务目录要清掉"
    assert _meta(tmp_path, "old1")["status"] == status, "任务记录不得被改写"


def test_resubmit_error_card_keeps_its_reason(isolated, tmp_path):
    _write_job(tmp_path, "old2", status="error", error="上一轮的原因")
    existing = {"job_id": "old2", "status": "error",
                "config": {"model": "large-v2"}, "file_hash": "f" * 64}

    response = main._handle_existing_job(existing, None, "", {})

    assert "上一轮的原因" in json.dumps(response, ensure_ascii=False)


def test_resume_endpoint_still_starts_the_old_job(isolated, tmp_path):
    _write_job(tmp_path, "old3", status="error")

    response = main.resume_job("old3", {"apiKeys": NEW_KEYS})

    assert response["status"] == "running"
    assert len(isolated) == 1


def test_resume_message_matches_the_card_text(isolated, tmp_path):
    _write_job(tmp_path, "old4", status="interrupted")

    main.resume_job("old4", {"apiKeys": NEW_KEYS})

    assert main.JOBS["old4"]["message"] == "正在继续处理…", (
        "前端点继续时写的就是这一句；后端不得另写「服务重启」"
    )


# ── C1：force 不再起作用 ───────────────────────────────────────────

def test_force_flag_no_longer_bypasses_a_finished_job(isolated, tmp_path):
    _write_job(tmp_path, "done1", status="done")
    existing = {"job_id": "done1", "status": "done",
                "config": {"model": "large-v2"}, "file_hash": "f" * 64}

    response = main._handle_existing_job(existing, None, "", {"force": True})

    assert response is not None
    assert response["state"]["status"] == "done"
    assert isolated == []


# ── B1：没留原因就停 → 中断，三处同一规则 ──────────────────────────

def test_worker_exit_without_reason_converges_to_interrupted(monkeypatch, tmp_path):
    job_id = "exit-no-reason"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    (job_dir / "meta.json").write_text(
        json.dumps({"status": "running", "run_id": "run-x", "config": {}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {job_id: {
        "id": job_id, "status": "running", "run_id": "run-x", "progress": 40}})
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {"tk": {"job_id": job_id, "status": "running"}})
    monkeypatch.setattr(main, "JOB_ID_TO_HASH", {job_id: "tk"})
    monkeypatch.setattr(main, "_recover_completed_checkpoint", lambda *a: None)

    snapshot = main._sync_job_from_worker_exit(job_id, "run-x", object(), -15, None)

    saved = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert snapshot["status"] == "interrupted"
    assert saved["status"] == "interrupted"
    assert "没有找到可确认的完成结果" not in json.dumps(saved, ensure_ascii=False)
    assert main.JOBS[job_id]["status"] == "interrupted"
    assert main.JOBS[job_id].get("progress") != 100
    assert main.GLOBAL_HASH_INDEX["tk"]["status"] == "interrupted"


def test_worker_exit_uses_the_same_rule_as_startup_scan(monkeypatch, tmp_path):
    """三处收敛只能有一条规则：退出回调必须调用 _reconcile_job_status_on_disk。"""
    job_id = "exit-shared"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    (job_dir / "meta.json").write_text(
        json.dumps({"status": "running", "run_id": "run-y", "config": {}}),
        encoding="utf-8",
    )
    calls = []
    real = main._reconcile_job_status_on_disk
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {job_id: {"id": job_id, "status": "running", "run_id": "run-y"}})
    monkeypatch.setattr(main, "_reconcile_job_status_on_disk",
                        lambda d, m: calls.append(d) or real(d, m))
    monkeypatch.setattr(main, "_recover_completed_checkpoint", lambda *a: None)

    main._sync_job_from_worker_exit(job_id, "run-y", object(), 0, None)

    assert calls, "退出回调没有走共用规则"


def test_worker_exit_keeps_a_terminal_reason_written_by_the_worker(monkeypatch, tmp_path):
    job_id = "exit-has-reason"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    (job_dir / "meta.json").write_text(
        json.dumps({"status": "error", "run_id": "run-z", "config": {},
                    "error": "视频没有声音轨道"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {job_id: {"id": job_id, "status": "running", "run_id": "run-z"}})

    snapshot = main._sync_job_from_worker_exit(job_id, "run-z", object(), 1, None)

    assert snapshot["status"] == "error", "干活程序自己留了原因的，保持出错"


def test_two_tasks_with_the_same_identity_do_not_crash_lookup(isolated, tmp_path):
    """删 force 之后不再产生；开发机历史残留的「同身份两个任务」读到其中一个即可，不崩。"""
    _write_job(tmp_path, "dupA", status="done")
    _write_job(tmp_path, "dupB", status="done")
    main.GLOBAL_HASH_INDEX["same"] = {"job_id": "dupB", "status": "done",
                                      "config": {}, "file_hash": "f" * 64}
    main.JOB_ID_TO_HASH["dupA"] = "same"
    main.JOB_ID_TO_HASH["dupB"] = "same"

    response = main._handle_existing_job(main.GLOBAL_HASH_INDEX["same"], None, "", {})

    assert response["job_id"] == "dupB"


# ── ⑦.5 独立审查发现（用户 2026-10-02 定）：继续不了的旧任务，重拖就重跑 ──

def test_resubmit_after_the_old_copy_is_gone_moves_the_upload_in(isolated, tmp_path, saved_new_keys):
    # P516 / Z4（取代原「旧视频没了就重跑」）：新上传挪进旧任务文件夹、记录地址更新；
    # 缓存是好的，所以只显示原卡片、不自动开跑（C2）——缓存坏了才重跑，见下一条
    job_dir = _write_job(tmp_path, "gone1", status="interrupted")
    (job_dir / "clip.mp4").unlink()
    prepared = tmp_path / "prepared2"
    prepared.mkdir()
    (prepared / "clip.mp4").write_bytes(b"fresh-video")
    existing = {"job_id": "gone1", "status": "interrupted",
                "config": {"model": "large-v2"}, "file_hash": "f" * 64}

    response = main._handle_existing_job(existing, prepared, prepared / "clip.mp4", {})

    assert isolated == [], "重新提交不得自动开跑"
    assert response["found"] is True and response["job_id"] == "gone1"
    assert (job_dir / "clip.mp4").read_bytes() == b"fresh-video", "新传的视频要放进旧任务目录"
    assert _meta(tmp_path, "gone1")["source_path"] == str(job_dir / "clip.mp4")
    assert not prepared.exists()


def test_resubmit_when_old_cache_is_broken_restarts(isolated, tmp_path, monkeypatch):
    _write_job(tmp_path, "broken1", status="error")
    monkeypatch.setattr(main, "_resume_cache_problem", lambda *a, **k: "任务断点缓存已损坏，请重新提交原视频")
    existing = {"job_id": "broken1", "status": "error",
                "config": {"model": "large-v2"}, "file_hash": "f" * 64}

    response = main._handle_existing_job(existing, None, "", {"apiKeys": NEW_KEYS})

    assert len(isolated) == 1
    assert response["status"] == "running"


def test_resume_and_resubmit_use_the_same_check(isolated, tmp_path, monkeypatch):
    """「继续」拒绝的条件，必须正好就是「重拖会重跑」的条件（一套检查，两处共用）。"""
    _write_job(tmp_path, "same1", status="interrupted")
    calls = []
    real = main._resume_problem
    monkeypatch.setattr(main, "_resume_problem", lambda d: calls.append(d.name) or real(d))

    main.resume_job("same1", {"apiKeys": NEW_KEYS})
    main._handle_existing_job({"job_id": "same1", "status": "interrupted",
                               "config": {"model": "large-v2"}, "file_hash": "f" * 64},
                              None, "", {})

    assert calls == ["same1", "same1"]


def test_worker_exit_with_status_missing_is_not_reported_as_disk_failure(monkeypatch, tmp_path):
    job_id = "exit-no-status"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    (job_dir / "meta.json").write_text(json.dumps({"run_id": "run-q", "config": {}}), encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {job_id: {"id": job_id, "status": "running", "run_id": "run-q"}})
    monkeypatch.setattr(main, "_recover_completed_checkpoint", lambda *a: None)

    snapshot = main._sync_job_from_worker_exit(job_id, "run-q", object(), -15, None)

    assert snapshot["status"] == "interrupted"
    assert "保存任务状态失败" not in json.dumps(snapshot, ensure_ascii=False)
