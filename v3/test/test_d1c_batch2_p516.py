"""D-1 补修第二批 P516（2026-10-03）：任务只记原视频地址、名字从地址现算。

合同见 pipelines/方案-D1补修-第二批-v2-2026-10-03.md §4.8、§8-2，合同增量 Z1–Z5。
全部在临时目录用假任务跑（JOBS_DIR 指过去；启动干活程序换成记录参数的替身）。
"""

import json
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import job_state, main
from backend.output_artifacts import derive_output_identity


@pytest.fixture
def jobs(monkeypatch, tmp_path):
    """真实的断点检查（不替换 _resume_cache_problem），只替换启动干活程序。"""
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    starts = []
    monkeypatch.setattr(main, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(main, "JOBS", {})
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {})
    monkeypatch.setattr(main, "JOB_ID_TO_HASH", {})
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda jid: False)
    monkeypatch.setattr(main.worker_manager, "cancel", lambda jid: None)
    monkeypatch.setattr(
        main.worker_manager, "start",
        lambda jid, directory, video, config, **kw: starts.append(
            {"job_id": jid, "video": video, "config": json.loads(json.dumps(config))}),
    )
    monkeypatch.setattr("backend.model_manager.is_model_loadable", lambda m: True)
    return starts


def _client():
    return TestClient(main.app)


_CONFIG = {"model": "large-v2", "translationPriority": ["google", "bing", "youdao"]}  # 与设置文件默认值一致


def _submit_path(path):
    res = _client().post("/api/jobs", json={"local_path": str(path), "config": json.dumps(_CONFIG)})
    assert res.status_code == 200, res.text
    return res.json()


def _meta(job_id):
    return json.loads((main.JOBS_DIR / job_id / "meta.json").read_text(encoding="utf-8"))


def _write_checkpoint(job_id, video_path, stage="translate"):
    """干活程序写下的断点（含另存的旧地址 input_file）+ 识别结果 + 已付费的翻译缓存。"""
    meta = _meta(job_id)
    input_hash = meta["file_hash"][:16]
    cache = main.JOBS_DIR / job_id / "cache" / input_hash[:12]
    cache.mkdir(parents=True)
    (cache / "checkpoint.json").write_text(json.dumps({
        "input_file": str(video_path), "input_hash": input_hash,
        "stage": stage, "meta": meta["config"],
    }), encoding="utf-8")
    (cache / "transcript.json").write_text(json.dumps({
        "version": 2, "receipt": {}, "segments": [{"id": 0, "start": 0.0, "end": 1.0, "text": "hi"}],
    }), encoding="utf-8")
    translated = cache / "translated.json"
    translated.write_text(json.dumps([{"id": 0, "zh": "已付费的译文"}]), encoding="utf-8")
    return translated


def _set_status(job_id, status, **fields):
    main._persist_job_status(job_id, status, fields)
    main.JOBS.setdefault(job_id, {"id": job_id}).update({"status": status, **fields})
    key = main.JOB_ID_TO_HASH.get(job_id)
    if key:
        main.GLOBAL_HASH_INDEX[key]["status"] = status


@pytest.fixture
def video(tmp_path):
    folder = tmp_path / "视频"
    folder.mkdir()
    path = folder / "旅行.mp4"
    path.write_bytes(b"the-same-video-content")
    return path


# ── 新建：只记地址、不复制；名字从地址取 ─────────────────────────────────

def test_new_local_job_records_only_the_address(jobs, video):
    job_id = _submit_path(video)["id"]
    meta = _meta(job_id)
    assert meta["source_path"] == str(video)
    assert "video_filename" not in meta and "video_filename" not in meta["config"]
    assert "source_dir" not in meta["config"]
    assert jobs[0]["video"] == str(video)
    assert not [p for p in (main.JOBS_DIR / job_id).iterdir() if p.is_file() and p.suffix == ".mp4"]
    assert main._public_job_details(main.JOBS[job_id])["video_filename"] == "旅行.mp4"


def test_public_state_carries_the_name_from_the_address(jobs, video):
    # Z2：卡片名只认后台给的名字
    job_id = _submit_path(video)["id"]
    assert job_state.build_public_task_state(main.JOBS[job_id])["video_filename"] == "旅行.mp4"


# ── A16-4 / A16-6：挪走 → 提示缺失；拖回 → 更新地址接着做；再中断再继续仍能接上 ─

def test_moved_video_is_recognised_and_continues_with_paid_translation_kept(jobs, video, tmp_path):
    job_id = _submit_path(video)["id"]
    translated = _write_checkpoint(job_id, video)
    _set_status(job_id, "interrupted")

    moved = tmp_path / "别处" / "旅行.mp4"
    moved.parent.mkdir()
    shutil.move(str(video), str(moved))

    blocked = main.resume_job(job_id, {})
    assert blocked.status_code == 409
    assert "原视频" in json.loads(blocked.body)["error"] and "重新提交" in json.loads(blocked.body)["error"]

    found = _submit_path(moved)
    assert found["found"] is True and found["job_id"] == job_id, "认视频不认位置"
    assert _meta(job_id)["source_path"] == str(moved), "记录里的地址更新为新地址"

    started = main.resume_job(job_id, {})
    assert started["status"] == "running"
    assert jobs[-1]["video"] == str(moved)
    assert translated.exists(), "已付费的翻译缓存保留"

    _set_status(job_id, "interrupted")
    again = main.resume_job(job_id, {})
    assert again["status"] == "running", "断点里的旧地址不再参与比较（A16-6）"


def test_moved_and_renamed_video_uses_the_new_name(jobs, video, tmp_path):
    job_id = _submit_path(video)["id"]
    _write_checkpoint(job_id, video)
    _set_status(job_id, "interrupted")
    renamed = tmp_path / "旅行-剪辑前.mp4"
    shutil.move(str(video), str(renamed))

    _submit_path(renamed)
    main.resume_job(job_id, {})
    assert derive_output_identity(jobs[-1]["video"])[0] == "旅行-剪辑前"


def test_partial_job_moved_and_dragged_back_can_retry(jobs, video, tmp_path):
    # §8-2：部分完成后挪了视频，拖回来要更新地址，否则重试永远失败
    job_id = _submit_path(video)["id"]
    _write_checkpoint(job_id, video)
    _set_status(job_id, "partial", failed_keys=[[0, 0.0, 1.0]], segment_count=1, success_count=0,
                failed_count=1, output_ready=False, retry_count=0, consecutive_no_progress=0)
    moved = tmp_path / "旅行.mp4"
    shutil.move(str(video), str(moved))

    assert main.partial_action(job_id, {"action": "retry"}).status_code == 409
    _submit_path(moved)
    assert _meta(job_id)["source_path"] == str(moved)
    assert main.partial_action(job_id, {"action": "retry"})["ok"] is True
    assert jobs[-1]["video"] == str(moved)


@pytest.mark.parametrize("status", ["interrupted", "error", "done", "partial"])
def test_found_response_carries_the_updated_name(jobs, video, tmp_path, status):
    # ⑨ 实测暴露：部分完成那条分支用了地址更新前的旧数据，响应里名字为空
    job_id = _submit_path(video)["id"]
    _write_checkpoint(job_id, video)
    _set_status(job_id, status)
    renamed = tmp_path / "旅行-剪辑前.mp4"
    shutil.move(str(video), str(renamed))

    found = _submit_path(renamed)
    assert found["found"] is True and found["job_id"] == job_id
    assert found["state"]["video_filename"] == "旅行-剪辑前.mp4"
    assert found["details"]["video_filename"] == "旅行-剪辑前.mp4"


@pytest.mark.parametrize("status", ["queued", "running"])
def test_running_job_keeps_its_address(jobs, video, tmp_path, status):
    job_id = _submit_path(video)["id"]
    _set_status(job_id, status)
    copy = tmp_path / "副本.mp4"
    shutil.copy(str(video), str(copy))
    _submit_path(copy)
    assert _meta(job_id)["source_path"] == str(video), "正在跑的任务不改地址"


def test_old_record_without_an_address_counts_as_missing_video(jobs, video):
    # A16-5：开发机历史任务不迁移
    job_id = _submit_path(video)["id"]
    meta = _meta(job_id)
    meta.pop("source_path")
    meta["config"]["video_filename"] = "旅行.mp4"
    (main.JOBS_DIR / job_id / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    problem = main._resume_problem(main.JOBS_DIR / job_id)[0]
    assert problem and "原视频" in problem


# ── N1：删任务绝不碰用户的原视频 ─────────────────────────────────────────

def _job_pointing_at(jobs_fixture, job_id, source):
    job_dir = main.JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "meta.json").write_text(json.dumps({
        "status": "interrupted", "config": {}, "source_path": str(source), "file_hash": "f" * 64,
    }), encoding="utf-8")
    return job_dir


def test_delete_never_touches_an_external_video(jobs, video):
    _job_pointing_at(jobs, "n1a", video)
    main.cancel_job("n1a")
    assert video.read_bytes() == b"the-same-video-content"


def test_delete_never_follows_a_link_inside_the_job_folder(jobs, video):
    job_dir = _job_pointing_at(jobs, "n1b", "placeholder")
    link = job_dir / "旅行.mp4"
    os.symlink(video, link)
    _job_pointing_at(jobs, "n1b", link)
    main.cancel_job("n1b")
    assert video.read_bytes() == b"the-same-video-content"


def test_delete_never_follows_dot_dot(jobs, video):
    job_dir = _job_pointing_at(jobs, "n1c", "placeholder")
    sneaky = job_dir / ".." / ".." / video.parent.name / video.name
    assert sneaky.resolve() == video.resolve()
    _job_pointing_at(jobs, "n1c", sneaky)
    main.cancel_job("n1c")
    assert video.read_bytes() == b"the-same-video-content"


def test_delete_never_touches_a_sibling_folder_with_the_same_prefix(jobs):
    sibling = main.JOBS_DIR / "job10"
    sibling.mkdir()
    victim = sibling / "旅行.mp4"
    victim.write_bytes(b"v")
    _job_pointing_at(jobs, "job1", victim)
    main.cancel_job("job1")
    assert victim.read_bytes() == b"v"


def test_delete_removes_a_video_that_lives_inside_the_job_folder(jobs):
    job_dir = _job_pointing_at(jobs, "n1e", "placeholder")
    inside = job_dir / "下载的视频.mp4"
    inside.write_bytes(b"v")
    _job_pointing_at(jobs, "n1e", inside)
    main.cancel_job("n1e")
    assert not inside.exists()
    assert not (job_dir / "meta.json").exists()


# ── N3 / Z4：开发期网页上传按原名存；危险名字换安全名字 ──────────────────

def _upload(name, content=b"uploaded-video"):
    res = _client().post("/api/jobs", files={"file": (name, content, "video/mp4")},
                         data={"config": json.dumps({"model": "large-v2"})})
    assert res.status_code == 200, res.text
    return res.json()


def test_upload_is_stored_under_its_original_name(jobs):
    job_id = _upload("旅行.mp4")["id"]
    source = Path(_meta(job_id)["source_path"])
    assert source == main.JOBS_DIR / job_id / "旅行.mp4"
    assert source.read_bytes() == b"uploaded-video"


@pytest.mark.parametrize("name", ["../../evil.mp4", "a/b.mp4", "a\\b.mp4", "..", "x" * 300 + ".mp4", "bad\x01name.mp4"])
def test_dangerous_upload_names_stay_inside_the_job_folder(jobs, name):
    job_id = _upload(name)["id"]
    source = Path(_meta(job_id)["source_path"])
    job_dir = (main.JOBS_DIR / job_id).resolve()
    assert source.resolve().parent == job_dir
    assert source.name not in {"", ".", ".."} and len(source.name.encode()) <= 200
    assert source.read_bytes() == b"uploaded-video"


def test_reuploading_the_same_video_keeps_the_existing_copy(jobs):
    job_id = _upload("旅行.mp4")["id"]
    _set_status(job_id, "interrupted")
    again = _upload("旅行.mp4")
    assert again["job_id"] == job_id
    assert _meta(job_id)["source_path"] == str(main.JOBS_DIR / job_id / "旅行.mp4")
    assert len([p for p in main.JOBS_DIR.iterdir() if p.is_dir()]) == 1, "临时文件夹已清走"


def test_reupload_after_the_copy_is_gone_moves_the_new_upload_in(jobs):
    job_id = _upload("旅行.mp4")["id"]
    _set_status(job_id, "interrupted")
    Path(_meta(job_id)["source_path"]).unlink()
    _upload("旅行.mp4")
    source = Path(_meta(job_id)["source_path"])
    assert source.parent == main.JOBS_DIR / job_id and source.read_bytes() == b"uploaded-video"


# ── N2：后台按 input 名找视频的写法为零 ─────────────────────────────────

def test_no_backend_code_looks_for_input_files():
    # 合同本身就是「搜代码可验」
    root = Path(main.__file__).parent
    for py in [root / "main.py", root / "worker.py"]:
        source = py.read_text(encoding="utf-8")
        assert 'glob("input.*")' not in source and 'f"input{' not in source and '"input.mp4"' not in source, py


# ── A16-3 / Z1：链接任务下载完写地址；继续不重下、不改名 ─────────────────

def test_link_download_reports_its_address_and_the_record_keeps_it(jobs, tmp_path):
    res = _client().post("/api/jobs", json={"url": "https://example.com/watch?v=1", "config": json.dumps({"model": "large-v2"})})
    job_id = res.json()["id"]
    downloaded = main.JOBS_DIR / job_id / "Real Title_video.mp4"
    downloaded.write_bytes(b"v")
    run_id = main.JOBS[job_id]["run_id"]

    state = main._sync_job_from_worker_message(job_id, {"type": "source_ready", "run_id": run_id, "path": str(downloaded)})

    assert _meta(job_id)["source_path"] == str(downloaded)
    assert main.JOBS[job_id]["status"] in {"queued", "running"}, "这条消息不是终态"
    assert state["video_filename"] == "Real Title_video.mp4"
    _set_status(job_id, "interrupted")
    main.resume_job(job_id, {})
    assert jobs[-1]["video"] == str(downloaded), "继续时用下载好的文件，不重新下载"


def test_worker_reports_the_downloaded_file(monkeypatch, tmp_path):
    from backend import worker

    downloaded = tmp_path / "Real Title_video.mp4"
    downloaded.write_bytes(b"v")
    sent = []
    monkeypatch.setattr(worker, "emit", lambda t, **kw: sent.append((t, kw)))
    monkeypatch.setattr(worker, "stderr_progress", lambda *a, **k: None)
    monkeypatch.setattr("backend.core.video_downloader.is_ytdlp_available", lambda: True)
    monkeypatch.setattr("backend.core.video_downloader.download_video",
                        lambda url, d, progress_callback=None: (str(downloaded), None))
    monkeypatch.setattr(worker, "_require_url_audio_asset", lambda *a, **k: None)
    config = {"source_url": "https://example.com/watch?v=1"}

    assert worker._download_source_if_url(config, tmp_path, "") == str(downloaded)
    assert ("source_ready", {"path": str(downloaded)}) in sent
    assert "video_filename" not in config


@pytest.mark.parametrize("flag", ["_resume", "_partial_retry", "_accept_partial"])
def test_continuing_never_renames(monkeypatch, tmp_path, flag):
    from backend import worker

    clip = tmp_path / "旅行.mp4"
    clip.write_bytes(b"v")
    monkeypatch.setattr(worker, "_require_url_audio_asset", lambda *a, **k: None)
    config = {flag: True}
    assert worker._download_source_if_url(config, tmp_path, str(clip)) == str(clip)
    assert "video_filename" not in config


# ── A16-2 / N4：成品名 = 地址最后一段去后缀 ─────────────────────────────

@pytest.mark.parametrize("path,stem,suffix", [
    ("/Users/x/视频/旅行.mp4", "旅行", ".mp4"),
    ("/jobs/abc/Real Title_video.webm", "Real Title_video", ".webm"),
    ("/jobs/abc/旅行.MOV", "旅行", ".mov"),
])
def test_output_name_comes_from_the_address(path, stem, suffix):
    assert derive_output_identity(path) == (stem, suffix)


def test_continue_export_and_retry_all_hand_over_the_original_address(jobs, video):
    job_id = _submit_path(video)["id"]
    _write_checkpoint(job_id, video)
    _set_status(job_id, "partial", failed_keys=[[0, 0.0, 1.0]], segment_count=1, success_count=0,
                failed_count=1, output_ready=False, retry_count=0, consecutive_no_progress=0)
    main.partial_action(job_id, {"action": "retry"})
    _set_status(job_id, "partial")
    main.partial_action(job_id, {"action": "accept"})
    _set_status(job_id, "error", partial_resume_action="accept")
    main.resume_job(job_id, {})
    assert [s["video"] for s in jobs] == [str(video)] * 4
    assert {derive_output_identity(s["video"])[0] for s in jobs} == {"旅行"}


# ── ⑦.5 /review 发现：继续后 / 刷新恢复后，内存里不能丢地址（否则卡片名为空）──

def test_resumed_job_keeps_its_address_in_memory(jobs, video):
    job_id = _submit_path(video)["id"]
    _write_checkpoint(job_id, video)
    _set_status(job_id, "interrupted")
    main.JOBS.pop(job_id, None)                      # 软件重开过：内存里没有这个任务
    main.resume_job(job_id, {})
    assert main.JOBS[job_id].get("source_path") == str(video)
    assert main._public_job_details(main.JOBS[job_id])["video_filename"] == "旅行.mp4"


def test_restoring_from_the_record_keeps_the_address(jobs, video):
    job_id = _submit_path(video)["id"]
    restored = main._restored_from_meta(_meta(job_id))
    assert restored.get("source_path") == str(video), "四条恢复路径共用这一个函数"


# ── ⑦.5 R1（用户定）：成品照旧存在原视频旁边（从记录里唯一的地址现算）────

from backend.output_artifacts import saved_output_paths  # noqa: E402


def test_outputs_go_next_to_the_users_own_video(tmp_path, video):
    job_dir = tmp_path / "jobs" / "j1"
    job_dir.mkdir(parents=True)
    srt = job_dir / "旅行.srt"
    dest = saved_output_paths({"savePath": str(tmp_path / "saved")}, {"srt": srt}, "srt",
                              source_path=str(video), job_dir=job_dir)
    assert dest == {"srt": video.parent / "旅行.srt"}


def test_outputs_of_videos_kept_in_the_job_folder_go_to_the_save_path(tmp_path):
    job_dir = tmp_path / "jobs" / "j2"
    job_dir.mkdir(parents=True)
    inside = job_dir / "Real Title_video.mp4"
    inside.write_bytes(b"v")
    old_dir = tmp_path / "旧位置"
    old_dir.mkdir()
    dest = saved_output_paths({"savePath": str(tmp_path / "saved"), "source_dir": str(old_dir)},
                              {"srt": job_dir / "x.srt"}, "srt", source_path=str(inside), job_dir=job_dir)
    assert dest == {"srt": tmp_path / "saved" / "x.srt"}, "老记录残留的 source_dir 不再读"


def test_worker_delivers_next_to_the_users_video(tmp_path, video):
    from backend import worker
    job_dir = tmp_path / "jobs" / "j3"
    job_dir.mkdir(parents=True)
    srt = job_dir / "旅行.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")
    plan = worker._delivery_plan(job_dir, {"video_path": str(video), "savePath": str(tmp_path / "saved")},
                                 "srt", str(srt), None)
    assert [Path(target).parent for _source, target in plan] == [video.parent]


def test_show_in_folder_points_next_to_the_users_video(jobs, video):
    job_id = _submit_path(video)["id"]
    job_dir = main.JOBS_DIR / job_id
    target = main._saved_output_target(
        {"source_path": str(video), "config": {"outputMode": "srt"}},
        {"result_srt": str(job_dir / "旅行.srt"), "result_video": None}, job_dir)
    assert target == video.parent, "只返回文件夹（不推导文件名）"


# ── ⑦.5 R4（用户定）：找不到原视频的提示用大白话 ─────────────────────────

def test_missing_video_message_tells_the_user_what_to_do(jobs, video, tmp_path):
    job_id = _submit_path(video)["id"]
    _set_status(job_id, "partial", failed_keys=[[0, 0.0, 1.0]], segment_count=1, success_count=0,
                failed_count=1, output_ready=False, retry_count=0, consecutive_no_progress=0)
    shutil.move(str(video), str(tmp_path / "别处.mp4"))
    body = json.loads(main.partial_action(job_id, {"action": "retry"}).body)
    assert body["error"] == "找不到原视频，请把它放回原来的位置，或重新拖进来"
