"""B2 + 交付接线：删除下载出口，改「在文件夹中显示」。

- 两个成品下载端点和前端下载按钮整体删除
- /api/download-video（URL 输入视频下载）必须保留，不得误删
- 新入口只接受 job_id，目录由后端按本轮配置推导，不接受客户端传路径
- B3 回退（2026-07-19）：搬运责任交回 Worker，送到之后才宣告完成。父进程
  收到终态时只登记本轮候选并产生公开快照，自己不碰用户保存目录
- 没有"已完成但还没送到"的中间态，前端也就没有对应的渲染路径
"""

import json
import re
import subprocess
import threading
import types
from pathlib import Path

import pytest

from backend import main, worker_manager as worker_manager_module


HTML = (Path(__file__).parent.parent / "frontend" / "index.html").read_text(
    encoding="utf-8"
)
SRT_BODY = "1\n00:00:00,000 --> 00:00:01,000\n你好\n"


def _function_source(name):
    match = re.search(
        rf"(?:async\s+)?function\s+{re.escape(name)}\([^)]*\)\{{.*?\n\}}",
        HTML, re.S,
    )
    assert match, f"找不到函数 {name}"
    return match.group(0)


def _run_node(source):
    result = subprocess.run(
        ["node", "-e", source], text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _ffprobe_ok(monkeypatch):
    monkeypatch.setattr(
        "backend.output_artifacts.subprocess.run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="mov,mp4", stderr=""),
    )


# ── B2：旧下载出口必须消失 ───────────────────────────────────────────────

def test_artifact_download_endpoints_are_gone():
    routes = {getattr(r, "path", "") for r in main.app.routes}
    assert "/api/jobs/{job_id}/download/srt" not in routes
    assert "/api/jobs/{job_id}/download/video" not in routes
    for name in ("download_srt", "download_video", "_serve_registered_artifact",
                 "_artifact_snapshot", "_artifact_snapshot_unchanged"):
        assert not hasattr(main, name), f"{name} 应随下载端点一并删除"


def test_url_video_download_endpoint_is_preserved():
    """/api/download-video 是 URL 输入视频下载，不是成品交付接口。"""
    routes = {getattr(r, "path", "") for r in main.app.routes}
    assert "/api/download-video" in routes
    assert hasattr(main, "download_video_endpoint")


def test_frontend_has_no_artifact_download_links():
    assert "/download/srt" not in HTML
    assert "/download/video" not in HTML
    assert "下载 SRT" not in HTML
    assert "下载字幕视频" not in HTML


# ── B2：新入口的安全边界 ─────────────────────────────────────────────────

def _delivered_job(tmp_path, monkeypatch, *, run_id="run-1", status="done"):
    """一个已经跑完的任务：成品在保存目录里，任务目录内的副本已被清理。"""
    jobs_root = tmp_path / "jobs"
    job_dir = jobs_root / "b2job"
    job_dir.mkdir(parents=True)
    save_dir = tmp_path / "saved"
    save_dir.mkdir()
    target = save_dir / "clip_zh.mp4"
    target.write_bytes(b"delivered video")
    meta = {
        "status": status, "file_hash": "f" * 64, "run_id": run_id,
        "config": {"outputMode": "burn", "savePath": str(save_dir)},
        # 终态即已送达，成品归属只认本轮的结果四元组。
        "result_srt": str(job_dir / "clip.srt"),
        "result_video": str(job_dir / "clip_zh.mp4"),
        "result_run_id": run_id,
        "output_ready": True,
    }
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", jobs_root)
    monkeypatch.setattr(main, "JOBS", {})
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {})
    monkeypatch.setattr(main, "JOB_ID_TO_HASH", {})
    return job_dir, save_dir, target


class _Request:
    def __init__(self, headers):
        self.headers = headers


def _reveal_headers(host="127.0.0.1"):
    return {"host": host, "x-video-subber-action": "reveal-output"}


def test_reveal_opens_the_delivered_directory_without_a_client_path(
    tmp_path, monkeypatch
):
    job_dir, save_dir, target = _delivered_job(tmp_path, monkeypatch)
    opened = []
    monkeypatch.setattr(main, "_reveal_in_file_manager", lambda p: opened.append(p))

    response = main.reveal_job_output("b2job", _Request(_reveal_headers()))

    assert response == {"ok": True}
    # 只打开目录，不指向推导出来的文件名：同名冲突时那个名字下面躺的是用户
    # 自己的文件，指着它就是 P386 的认错对象。
    assert opened == [target.parent], "目录必须由后端按本轮配置推导，不接受客户端传路径"


def test_reveal_rejects_a_non_localhost_host(tmp_path, monkeypatch):
    _delivered_job(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "_reveal_in_file_manager",
                        lambda p: pytest.fail("不该被调用"))

    response = main.reveal_job_output(
        "b2job", _Request(_reveal_headers(host="evil.example.com")))

    assert response.status_code == 403


def test_reveal_rejects_a_missing_action_header(tmp_path, monkeypatch):
    _delivered_job(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "_reveal_in_file_manager",
                        lambda p: pytest.fail("不该被调用"))

    response = main.reveal_job_output("b2job", _Request({"host": "127.0.0.1"}))

    assert response.status_code == 403


def test_reveal_refuses_while_the_job_is_still_running(tmp_path, monkeypatch):
    """还没到终态就还没送达，不得打开任何目录。"""
    _delivered_job(tmp_path, monkeypatch, status="running")
    monkeypatch.setattr(main, "_reveal_in_file_manager",
                        lambda p: pytest.fail("尚未送达不得打开"))

    response = main.reveal_job_output("b2job", _Request(_reveal_headers()))

    assert response.status_code == 409


def test_reveal_refuses_an_artifact_from_another_run(tmp_path, monkeypatch):
    job_dir, save_dir, target = _delivered_job(tmp_path, monkeypatch)
    meta = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    meta["run_id"] = "run-2"          # 成品是 run-1 的，当前轮已经换人
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(main, "_reveal_in_file_manager",
                        lambda p: pytest.fail("run 不匹配不得打开"))

    response = main.reveal_job_output("b2job", _Request(_reveal_headers()))

    assert response.status_code == 409


def test_reveal_never_uses_a_shell():
    import inspect

    source = inspect.getsource(main._reveal_in_file_manager)
    assert "shell=True" not in source
    assert "os.system" not in source


# ── 交付事务接线 ─────────────────────────────────────────────────────────

def test_worker_owns_delivery_and_no_longer_archives():
    from backend import worker

    assert hasattr(worker, "_deliver_outputs"), "搬运责任已交回 Worker"
    assert hasattr(worker, "_still_current_run"), "铁律 7 的轮次检查必须在 Worker 侧"
    # C1：归档用户已有同名文件的逻辑已废除，改为挑空闲名。
    assert not hasattr(worker, "_archive_path")
    assert not hasattr(main, "_archive_existing_target")


def _wired_job(tmp_path, monkeypatch, output_mode="srt"):
    jobs_root = tmp_path / "jobs"
    job_dir = jobs_root / "w1"
    job_dir.mkdir(parents=True)
    save_dir = tmp_path / "saved"
    save_dir.mkdir()
    srt = job_dir / "clip.srt"
    srt.write_text(SRT_BODY, encoding="utf-8")
    config = {"outputMode": output_mode, "video_filename": "clip.mp4",
              "savePath": str(save_dir)}
    (job_dir / "meta.json").write_text(json.dumps({
        "status": "running", "file_hash": "f" * 64, "config": config,
        "run_id": "run-1", "run_kind": "normal",
    }), encoding="utf-8")
    jobs = {"w1": {"id": "w1", "status": "running", "progress": 5,
                   "config": config, "run_id": "run-1", "run_kind": "normal",
                   "message": "", "stage": ""}}
    monkeypatch.setattr(main, "JOBS_DIR", jobs_root)
    monkeypatch.setattr(main, "JOBS", jobs)
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {})
    monkeypatch.setattr(main, "JOB_ID_TO_HASH", {})
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda jid: False)
    _ffprobe_ok(monkeypatch)
    return job_dir, save_dir, srt


def test_terminal_registers_the_round_without_delivering(tmp_path, monkeypatch):
    """B3 回退：收到终态时货已经在用户目录里，父进程只登记并产生公开快照。"""
    job_dir, save_dir, srt = _wired_job(tmp_path, monkeypatch)
    published = []

    final = main._handle_worker_terminal(
        "w1",
        {"type": "done", "run_id": "run-1", "result_srt": str(srt),
         "result_video": None, "segment_count": 3, "success_count": 3},
        publish=published.append,
    )

    assert published == [], "父进程不再广播任何中间态快照"
    assert final["status"] == "done"
    assert final["result"]["can_show_output_folder"] is True
    assert "is_delivering" not in final
    assert "delivery" not in final


def test_terminal_never_touches_the_save_path(tmp_path, monkeypatch):
    """父进程一个字节都不写用户保存目录——复制失败也不影响它产出终态。"""
    job_dir, save_dir, srt = _wired_job(tmp_path, monkeypatch)
    monkeypatch.setattr(
        main.shutil, "copyfile",
        lambda *a, **k: pytest.fail("父进程不得复制任何成品"),
    )

    final = main._handle_worker_terminal(
        "w1",
        {"type": "done", "run_id": "run-1", "result_srt": str(srt),
         "result_video": None},
        publish=lambda snapshot: None,
    )

    assert final["status"] == "done"
    assert list(save_dir.iterdir()) == [], "父进程动了用户保存目录"
    assert srt.exists(), "父进程不得删任务目录里的成品"


def test_partial_action_is_refused_while_the_terminal_is_being_handled(
    tmp_path, monkeypatch
):
    """终态处理期间的重试/恢复/删除必须与同一任务锁串行。"""
    job_dir, save_dir, srt = _wired_job(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "backend.config_manager.load_config",
        lambda: {"apiKeys": [{"key": "fake-test-key"}],
                 "translationPriority": ["google"]},
    )
    responses = []
    entered = threading.Event()
    release = threading.Event()

    real_sync = main._sync_job_from_worker_message

    def _slow_sync(job_id, message):
        entered.set()
        release.wait(timeout=5)
        return real_sync(job_id, message)

    monkeypatch.setattr(main, "_sync_job_from_worker_message", _slow_sync)

    def _handle():
        main._handle_worker_terminal(
            "w1",
            {"type": "done", "run_id": "run-1", "result_srt": str(srt),
             "result_video": None},
            publish=lambda snapshot: None,
        )

    worker_thread = threading.Thread(target=_handle)
    worker_thread.start()
    assert entered.wait(timeout=5), "终态处理没有开始"
    responses.append(main.partial_action("w1", {"action": "retry"}))
    release.set()
    worker_thread.join(timeout=10)

    assert responses[0].status_code == 409, "终态处理期间不得让新一轮插进来"


def test_worker_manager_terminal_also_uses_the_fixed_state_frame():
    """终态与运行态使用同一种 `{type: state, state}` 小帧。"""
    manager = worker_manager_module.WorkerManager()
    sent = []
    manager.set_terminal_callback(
        lambda job_id, msg, publish: (
            publish({"type": "progress", "status": "running",
                     "can_show_output_folder": False}),
            {"status": "done", "can_show_output_folder": True},
        )[1]
    )
    manager._send = lambda job_id, message, loop: sent.append(message)

    manager._handle_terminal("j", {"type": "done", "run_id": "run-1"}, None)

    assert sent == [{
        "type": "state",
        "state": {"status": "done", "can_show_output_folder": True},
    }]


def test_rejected_terminal_is_never_broadcast():
    manager = worker_manager_module.WorkerManager()
    sent = []
    manager.set_terminal_callback(lambda job_id, msg, publish: None)
    manager._send = lambda job_id, message, loop: sent.append(message)

    manager._handle_terminal("j", {"type": "done", "run_id": "stale"}, None)

    assert sent == []


# ── 前端：保存提示走普通进度、在文件夹中显示 ─────────────────────────────

_DOM_STUB = """
function element(){const s=new Set();return{
  textContent:'',innerHTML:'',disabled:false,className:'',style:{},children:[],
  href:undefined,
  classList:{add:c=>s.add(c),remove:c=>s.delete(c),contains:c=>s.has(c),toggle(){}},
  appendChild(x){this.children.push(x);return x},
  querySelectorAll:()=>[],removeAttribute(){this.href=undefined},
}}
const els={};
const $=id=>(els[id]=els[id]||element());
const document={createElement:()=>element(),getElementById:id=>$(id)};
let fetched=[];
const fetch=(url,opts)=>{fetched.push({url,method:(opts||{}).method,
  action:((opts||{}).headers||{})['X-Video-Subber-Action']});
  return Promise.resolve({ok:true,json:async()=>({ok:true})})};
const showToast=()=>{};
const activeJobs={J:{config:{}}};
const State={settings:{},doneJobConfig:null};
function allButtons(el){const out=[];for(const c of el.children){out.push(c);
  out.push(...allButtons(c))}return out}
"""


def test_frontend_has_no_delivering_intermediate_state():
    """B3 回退：没有"已完成但还没送到"的中间态，前端也就没有对应的渲染路径。"""
    assert "is_delivering" not in HTML
    assert "renderDeliveringCard" not in HTML


def test_copy_progress_renders_through_the_normal_progress_path():
    """复制期间的「正在保存成品…」由 Worker 的普通 progress 消息驱动。"""
    js = "\n".join([
        _DOM_STUB,
        """
let progressed=[];
function setJobProgress(jid,percent,stage,message,detail,runId){
  progressed.push({jid,percent,stage,message,detail});
}
""",
        "activeJobs.J={config:{}};",
        "function handlePartialState(){};function renderInterruptedCard(){};"
        "function removeCard(){};function renderTranscriptionProgress(){};"
        "function renderRetryProgress(){};function renderEngineStatus(){};",
        _function_source("shouldApplyState"),
        _function_source("applyTaskState"),
        _function_source("handleCardMsg"),
        """
// **保护权交接（2026-08-19 · G9/P422）**：Worker 的复制进度仍是普通 progress 消息，
// 但父进程现在把它折算成公开快照再广播（type:'state'），
// 好让 WebSocket 与查询接口是同一个函数的同一个返回值。渲染路径没变。
handleCardMsg('J',{type:'state',state:{run_id:'r1',revision:1,status:'running',
  percent:99,phase:'copy',display_message:'正在保存成品到目标文件夹…',activity:'50%',
  progress:{total:null,processed:null,succeeded:null,failed:null,remaining:null},
  actions:{can_resume:false,can_retry:false,resume_action:null},
  result:{output_ready:false,can_show_output_folder:false,quality:{}}}});
console.log(JSON.stringify({progressed}));
""",
    ])

    data = _run_node(js)

    assert data["progressed"] == [{
        "jid": "J", "percent": 99, "stage": "copy",
        "message": "正在保存成品到目标文件夹…", "detail": "50%",
    }], "复制进度必须走普通 setJobProgress 路径"


def test_done_card_shows_output_folder_button_only_when_allowed():
    def _render(msg):
        return _run_node("\n".join([
            _DOM_STUB,
            _function_source("hideMilestones"),
            _function_source("renderTranslationSummary"),
            _function_source("revealJobOutput"),
            _function_source("renderCardDone"),
            f"renderCardDone('J', {json.dumps(msg)});",
            """
const labels=allButtons(els['result-J']).map(b=>b.textContent);
console.log(JSON.stringify({labels}));
""",
        ]))

    allowed = _render({"result_srt": "/j/a.srt", "can_show_output_folder": True})
    refused = _render({"result_srt": "/j/a.srt", "can_show_output_folder": False})

    assert any("文件夹" in text for text in allowed["labels"]), allowed["labels"]
    assert not any("文件夹" in text for text in refused["labels"]), refused["labels"]


def test_done_card_renders_unavailable_sound_reference_summary():
    message = "声音参考未使用，字幕仍已生成。"
    data = _run_node("\n".join([
        _DOM_STUB,
        _function_source("hideMilestones"),
        _function_source("renderTranslationSummary"),
        _function_source("revealJobOutput"),
        _function_source("renderCardDone"),
        f"""
renderCardDone('J', {{
  result_srt:'/j/a.srt',can_show_output_folder:false,
  sound_reference:{{status:'unavailable',message:{json.dumps(message)}}}
}});
console.log(JSON.stringify({{quality:els['quality-J'].textContent}}));
""",
    ]))

    assert message in data["quality"]


def test_all_done_reentry_paths_forward_sound_reference():
    # **保护权交接（2026-08-19 · G9/P422）**：两条恢复入口不再逐个点名字段，改为把整份公开快照原样交给完成卡。
    # `sound_reference` 因此不再需要在任何地方登记——这正是修复本身。
    # 真正的保护（它确实到得了完成卡）由 test_g9_p422_current_state.py 的
    # 「四条 done 入口都把整份快照原样交给完成卡_零登记透传」接管。
    reconnect = _function_source("reconnectJobs")
    found = _function_source("handleFoundJob")
    assert "renderCardDone(jid,view)" in reconnect, "启动恢复没有使用唯一安全视图"
    assert "renderCardDone(d.job_id,d)" in found, "晚到补建没有整份透传"


def test_output_folder_button_posts_job_id_only():
    js = "\n".join([
        _DOM_STUB,
        _function_source("revealJobOutput"),
        """
async function run(){
  await revealJobOutput('J',{disabled:false});
  console.log(JSON.stringify({fetched}));
}
run();
""",
    ])

    data = _run_node(js)

    assert data["fetched"] == [{
        "url": "/api/jobs/J/output/reveal",
        "method": "POST",
        "action": "reveal-output",
    }], "前端只能传 job_id，且必须带自定义动作头"
