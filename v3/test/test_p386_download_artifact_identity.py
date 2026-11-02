"""P386：成品出口绝不猜文件。

原始故障是「下载 SRT」在 result_srt 为空时回退 glob 取第一个命中，把断句混乱的
中间产物 watch.srt 交给了用户。

B2（2026-07-19）把两个成品下载端点整体删除——成品由 Worker 在宣告完成之前送到
用户目录，应用内只保留「在文件夹中显示」。P386 的合同因此变成两条：

1. 结构上不存在任何"按文件名/时间/唯一候选猜测成品"的代码路径
2. 唯一的成品出口只认本轮可信的成品归属，并保留 P189 的路径规范化防线
"""

import json
from pathlib import Path

import pytest

from backend import main


SRT_BODY = "1\n00:00:00,000 --> 00:00:01,000\n你好\n"


class _Request:
    def __init__(self, headers):
        self.headers = headers


def _reveal_headers(host="127.0.0.1"):
    return {"host": host, "x-video-subber-action": "reveal-output"}


def _build_job(tmp_path, monkeypatch, *, run_id="run-1", job_id="p386job"):
    jobs_root = tmp_path / "jobs"
    job_dir = jobs_root / job_id
    job_dir.mkdir(parents=True)
    save_dir = tmp_path / "saved"
    save_dir.mkdir(exist_ok=True)
    target = save_dir / "real-title_zh.mp4"
    target.write_bytes(b"this round video")
    # 上一轮遗留的中间产物：任何猜测型解析都会命中它。
    (job_dir / "watch.srt").write_text(SRT_BODY, encoding="utf-8")
    # B3 回退后不再有 delivery 记录：成品归属只认本轮的结果四元组，保存目录
    # 由本轮 config 推导。任务目录内的副本已被交付后的清理删掉，所以这里
    # 只登记路径、不建文件——这正是真实运行完之后的样子。
    meta = {
        "status": "done", "file_hash": "f" * 64, "run_id": run_id,
        "config": {"outputMode": "burn", "savePath": str(save_dir)},
        "result_srt": str(job_dir / "real-title.srt"),
        "result_video": str(job_dir / "real-title_zh.mp4"),
        "result_run_id": run_id,
        "output_ready": True,
    }
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", jobs_root)
    monkeypatch.setattr(main, "JOBS", {})
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {})
    monkeypatch.setattr(main, "JOB_ID_TO_HASH", {})
    return job_dir, save_dir, target


# ── 结构上不存在猜测路径 ─────────────────────────────────────────────────

def test_no_artifact_resolution_path_globs_the_job_directory():
    """glob 兜底是 P386 的根因，任何成品解析路径都不得再出现它。"""
    import inspect

    source = inspect.getsource(main)
    for needle in ('glob("*.srt")', "glob('*.srt')",
                   'glob("*_zh_sub.*")', 'glob("*.mp4")'):
        assert needle not in source, f"成品解析不得回退 glob: {needle}"


def test_download_endpoints_and_their_helpers_are_gone():
    routes = {getattr(route, "path", "") for route in main.app.routes}
    assert "/api/jobs/{job_id}/download/srt" not in routes
    assert "/api/jobs/{job_id}/download/video" not in routes
    for name in ("download_srt", "download_video", "_serve_registered_artifact"):
        assert not hasattr(main, name)


# ── 唯一成品出口的归属与边界 ─────────────────────────────────────────────

def test_reveal_opens_only_this_round_delivered_target(tmp_path, monkeypatch):
    job_dir, save_dir, target = _build_job(tmp_path, monkeypatch)
    opened = []
    monkeypatch.setattr(main, "_reveal_in_file_manager", lambda p: opened.append(p))

    assert main.reveal_job_output("p386job", _Request(_reveal_headers())) == {"ok": True}
    # 只打开成品所在的目录。推导出来的文件名下面可能躺着用户自己的同名文件，
    # 指向它就是 P386 的"认错对象"。
    assert opened == [target.parent]


def test_reveal_rejects_an_artifact_from_a_previous_run(tmp_path, monkeypatch):
    job_dir, _save_dir, _target = _build_job(tmp_path, monkeypatch, run_id="run-old")
    meta = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    meta["run_id"] = "run-new"
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(main, "_reveal_in_file_manager",
                        lambda p: pytest.fail("上一轮的交付记录不得被认领"))

    response = main.reveal_job_output("p386job", _Request(_reveal_headers()))

    assert response.status_code == 409


def test_reveal_rejects_a_job_without_an_authoritative_run_id(tmp_path, monkeypatch):
    job_dir, _save_dir, _target = _build_job(tmp_path, monkeypatch)
    meta = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    meta["run_id"] = "   "
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(main, "_reveal_in_file_manager",
                        lambda p: pytest.fail("没有权威身份不得打开"))

    response = main.reveal_job_output("p386job", _Request(_reveal_headers()))

    assert response.status_code == 409


def test_reveal_on_a_job_with_no_delivery_is_409(tmp_path, monkeypatch):
    """有中间产物、没有本轮交付记录——正是原始故障的场景。"""
    jobs_root = tmp_path / "jobs"
    job_dir = jobs_root / "p386job"
    job_dir.mkdir(parents=True)
    (job_dir / "watch.srt").write_text(SRT_BODY, encoding="utf-8")
    (job_dir / "meta.json").write_text(json.dumps({
        "status": "done", "run_id": "run-1", "config": {"outputMode": "srt"},
    }), encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", jobs_root)
    monkeypatch.setattr(main, "JOBS", {})
    monkeypatch.setattr(main, "_reveal_in_file_manager",
                        lambda p: pytest.fail("没有交付记录不得打开任何目录"))

    response = main.reveal_job_output("p386job", _Request(_reveal_headers()))

    assert response.status_code == 409


def test_missing_job_directory_returns_404(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path / "jobs")
    (tmp_path / "jobs").mkdir()
    monkeypatch.setattr(main, "JOBS", {})

    response = main.reveal_job_output("nosuchjob", _Request(_reveal_headers()))

    assert response.status_code == 404


@pytest.mark.parametrize("raw_id", ["foo/../jobid", "../escape"])
def test_p189_raw_job_id_normalization_still_returns_400(
    tmp_path, monkeypatch, raw_id
):
    """P189 防线：未规范化的任务编号必须直接拒绝，不能靠 resolve() 事后补救。"""
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    monkeypatch.setattr(main, "JOBS_DIR", jobs_root)
    monkeypatch.setattr(main, "JOBS", {})
    monkeypatch.setattr(main, "_reveal_in_file_manager",
                        lambda p: pytest.fail("不该被调用"))

    response = main.reveal_job_output(raw_id, _Request(_reveal_headers()))

    assert response.status_code == 400


def test_symlinked_job_directory_is_rejected(tmp_path, monkeypatch):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "meta.json").write_text("{}", encoding="utf-8")
    (jobs_root / "linked").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(main, "JOBS_DIR", jobs_root)
    monkeypatch.setattr(main, "JOBS", {})
    monkeypatch.setattr(main, "_reveal_in_file_manager",
                        lambda p: pytest.fail("软链接任务目录不得被跟随"))

    response = main.reveal_job_output("linked", _Request(_reveal_headers()))

    # 软链接会让原始路径不再规范化，P189 那道防线先拦下它（400）；
    # 关键是拒绝并且没有跟随链接打开任何目录。
    assert response.status_code in (400, 404)
