"""D-1 补修第二批 · 自审第 ④ 条（用户定，2026-10-04）。

链接任务连第一步「下载」都没走完就关了软件 → 下次启动把这个半成品任务文件夹整个删掉，
用户重新贴链接即可；网页本地记着的那个任务编号，重开时问不到就悄悄划掉，不弹卡。
已完成 / 部分完成的任务、本地视频任务一律不碰。
"""

import json

from backend import main
from test.test_d1b_frontend import _function_source, _run_node


def _job(root, job_id, *, status, source_url="https://www.youtube.com/watch?v=x", source_path=None):
    job_dir = root / job_id
    job_dir.mkdir()
    (job_dir / "Some Title_video.mp4.part").write_bytes(b"half")  # 下载工具留下的半截文件
    config = {"model": "large-v2"}
    if source_url:
        config["source_url"] = source_url
    meta = {"status": status, "config": config, "file_hash": "f" * 64}
    if source_path:
        meta["source_path"] = source_path
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return job_dir


def _scan(monkeypatch, root):
    monkeypatch.setattr(main, "JOBS_DIR", root)
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {})
    monkeypatch.setattr(main, "JOB_ID_TO_HASH", {})
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda jid: False)
    main._scan_jobs_into_index()


def test_unfinished_link_download_is_deleted_on_startup(tmp_path, monkeypatch):
    root = tmp_path / "jobs"
    root.mkdir()
    for status in ("interrupted", "error", "running", "queued"):
        _job(root, f"half_{status}", status=status)

    _scan(monkeypatch, root)

    assert [p.name for p in root.iterdir()] == [], "第一步没走完的链接任务，整个文件夹删掉"
    assert main.JOB_ID_TO_HASH == {}, "也不进索引——重新贴同一个链接就是新任务"


def test_everything_else_is_left_alone(tmp_path, monkeypatch):
    root = tmp_path / "jobs"
    root.mkdir()
    downloaded = root / "dl" / "Some Title_video.mp4"
    kept = {
        "done_link": _job(root, "done_link", status="done"),          # 开发机 3 个老任务就是这种
        "partial_link": _job(root, "partial_link", status="partial"),
        "dl": _job(root, "dl", status="interrupted", source_path=str(downloaded)),
        "local": _job(root, "local", status="interrupted", source_url=None),
    }
    downloaded.write_bytes(b"video")
    outside = tmp_path / "用户的视频.mp4"
    outside.write_bytes(b"mine")

    _scan(monkeypatch, root)

    for name, job_dir in kept.items():
        assert (job_dir / "meta.json").is_file(), f"{name} 不该被删"
    assert set(main.JOB_ID_TO_HASH) == set(kept)
    assert outside.read_bytes() == b"mine"


def _reconnect(status_code):
    js = "\n".join([
        f"""
        const store={{activeJobs:JSON.stringify(['J1'])}};
        const localStorage={{getItem:k=>store[k]??null,setItem:(k,v)=>{{store[k]=v}}}};
        let missingCards=[],removed=[],timers=0;
        const showMissingJobCard=jid=>missingCards.push(jid);
        const removeActiveJobId=jid=>removed.push(jid);
        const setTimeout=()=>{{timers++}};
        const fetch=()=>Promise.resolve({{status:{status_code},ok:{str(status_code < 400).lower()},json:async()=>({{}})}});
        """,
        _function_source("readJobResponse"),
        _function_source("reconnectJobs"),
        """
        reconnectJobs();
        setImmediate(()=>setImmediate(()=>console.log(JSON.stringify({missingCards,removed}))));
        """,
    ])
    return _run_node(js)


def test_reopening_quietly_drops_a_task_the_backend_no_longer_has():
    # 后台启动时删了半成品 → 网页按纸条上的编号去问 → 404 → 悄悄划掉，不弹「找不到该任务」卡
    assert _reconnect(404) == {"missingCards": [], "removed": ["J1"]}
