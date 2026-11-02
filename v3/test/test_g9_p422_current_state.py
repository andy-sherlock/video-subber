"""G9/P422 减法回炉：公开状态是 JOBS 事实的即时投影，不是第二份账。"""

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from backend import job_state, main
from backend.worker_manager import WorkerManager


HTML_PATH = Path(__file__).parent.parent / "frontend" / "index.html"
SRT_BODY = "1\n00:00:00,000 --> 00:00:01,000\n你好\n"


def _isolate(monkeypatch, tmp_path, jobs=None):
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", jobs if jobs is not None else {})
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {})
    monkeypatch.setattr(main, "JOB_ID_TO_HASH", {})
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda _jid: True)


def _job(tmp_path, job_id="g9job", **overrides):
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    srt = job_dir / "output.srt"
    srt.write_text(SRT_BODY, encoding="utf-8")
    job = {
        "id": job_id,
        "status": "running",
        "run_id": "run-1",
        "revision": 4,
        "stage": "translate",
        "progress": 42,
        "message": "正在翻译字幕",
        "activity": "正在等待第 7/9 句",
        "config": {"video_filename": "clip.mp4", "apiKeys": [{"key": "secret"}]},
        "video_filename": "clip.mp4",
        "result_srt": str(srt),
        "result_video": str(job_dir / "output.mp4"),
        "result_run_id": "run-1",
        "output_ready": False,
    }
    job.update(overrides)
    return job_dir, job


def _write_meta(job_dir, job):
    meta = dict(job)
    meta.pop("id", None)
    (job_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )


def _extract_js_function(source, name):
    marker = f"function {name}("
    start = source.index(marker)
    brace = source.index("{", start)
    depth = 0
    quote = None
    escaped = False
    for pos in range(brace, len(source)):
        char = source[pos]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "'\"`":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : pos + 1]
    raise AssertionError(f"找不到完整函数：{name}")


def _run_node(source):
    result = subprocess.run(
        ["node", "-e", source], text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_a1_公开状态只有唯一合同字段且嵌套字段固定(tmp_path):
    _, job = _job(tmp_path)
    state = job_state.build_public_task_state(job)
    assert set(state) == {
        "schema_version", "job_id", "run_id", "revision", "status", "phase",
        "percent", "progress", "display_message", "activity", "actions", "result",
        "video_filename",  # P516 / Z2：卡片名只认后台从地址算出的名字
    }
    assert set(state["progress"]) == {
        "total", "processed", "succeeded", "failed", "remaining"
    }
    assert set(state["actions"]) == {
        "can_resume", "can_retry", "resume_action"
    }
    assert set(state["result"]) == {
        "output_ready", "can_show_output_folder", "quality"
    }
    assert set(state["result"]["quality"]) == {
        "question_mark_count", "subtitle_quality", "translation_summary",
        "fa_quality", "alignment_fallback", "sound_reference",
        # G12 第三批 A / E2：转录欠账说明（None = 没有欠账）。
        # 走公开状态而不是进度消息——进度文案任务跑完就被覆盖，
        # 用户刷新页面再也看不到（第一批的缺标点警告就是这么丢的）。
        "transcription_shortfall",
    }


def test_a2_不知道总数时五个数字全为_null(tmp_path):
    _, job = _job(tmp_path)
    state = job_state.build_public_task_state(job)
    assert state["progress"] == {
        "total": None, "processed": None, "succeeded": None,
        "failed": None, "remaining": None,
    }
    assert state["percent"] == 42


def test_a2_已知终态数字由一个函数计算(tmp_path):
    _, job = _job(
        tmp_path, status="partial", segment_count=10, success_count=7,
        failed_count=3, output_ready=True,
    )
    state = job_state.build_public_task_state(job)
    assert state["progress"] == {
        "total": 10, "processed": 10, "succeeded": 7,
        "failed": 3, "remaining": 0,
    }


def test_a2_重试数字从任务事实生成而不是前端重算(tmp_path):
    _, job = _job(
        tmp_path, retry_status="running", retry_initial_failed_count=9,
        retry_processed_count=4, retry_new_success_count=3,
    )
    state = job_state.build_public_task_state(job)
    assert state["progress"] == {
        "total": 9, "processed": 4, "succeeded": 3,
        "failed": 1, "remaining": 5,
    }


def test_a3_同一revision的_worker回调与查询state逐值相同(monkeypatch, tmp_path):
    job_dir, job = _job(tmp_path)
    _write_meta(job_dir, job)
    jobs = {job["id"]: job}
    _isolate(monkeypatch, tmp_path, jobs)
    ws_state = main._sync_job_from_worker_message(job["id"], {
        "type": "progress", "run_id": "run-1", "stage": "translate",
        "percent": 48, "message": "正在翻译第 4/9 句",
    })
    rest = main.get_job(job["id"])
    assert rest["state"] == ws_state
    assert rest["state"]["revision"] == jobs[job["id"]]["revision"]


def test_a4_运行帧只有type和state且五百条失败时仍小于2KB(monkeypatch, tmp_path):
    _, job = _job(
        tmp_path, failed_segments=[{"index": i, "source": "内部原文" * 10}
                                   for i in range(500)],
        segment_count=2000, failed_count=500, file_hash="f" * 64,
    )
    manager = WorkerManager()
    manager.set_progress_callback(
        lambda _jid, _msg: job_state.build_public_task_state(job)
    )
    sent = []
    async def capture(_jid, payload):
        sent.append(payload)
    from backend import ws_manager as ws_module
    monkeypatch.setattr(ws_module.ws_manager, "send", capture)
    asyncio.run(manager._forward(job["id"], {
        "type": "progress", "run_id": "run-1", "percent": 50,
    }))
    assert set(sent[0]) == {"type", "state"}
    assert sent[0]["type"] == "state"
    assert len(json.dumps(sent[0], ensure_ascii=False).encode("utf-8")) < 2048
    assert "failed_segments" not in json.dumps(sent[0], ensure_ascii=False)


@pytest.mark.parametrize("forbidden", [
    "result_srt", "result_video", "file_hash", "task_key", "last_progress",
    "failed_keys", "failure_kind_counts", "source_text", "source_words",
    "diagnostics", "apiKeys", "technical_detail", "internal_error",
    "completed_segments", "transcription_phase", "audio_total_duration",
    "audio_processed_duration",
])
def test_a5_内部字段无论放进任务哪里都不能进入公开state(tmp_path, forbidden):
    _, job = _job(tmp_path)
    job[forbidden] = {"secret": "/Users/andy/private/file.srt"}
    blob = json.dumps(job_state.build_public_task_state(job), ensure_ascii=False)
    assert forbidden not in blob
    assert "/Users/andy/" not in blob


def test_a5_质量摘要只保留页面实际使用的固定字段(tmp_path):
    _, job = _job(
        tmp_path,
        question_mark_count=3,
        subtitle_quality={
            "below_min_display": {"count": 2, "secret": "drop"},
            "capped_no_legal_cut": {"count": 1},
            "mapping_failed_splits": 4,
            "no_safe_boundary_splits": 5,
            "single_word_over_max": 6,
            "diagnostics": {"path": "/tmp/private"},
        },
        translation_summary={
            "requested_tier": "api_context", "api_success_count": 8,
            "api_total_count": 9, "auto_fallback": True, "fallback_success": 1,
            "raw_responses": ["private"],
        },
        sound_reference={
            "status": "unavailable", "message": "声音参考不可用",
            "reason_code": "missing", "hints": ["private"],
        },
    )
    quality = job_state.build_public_task_state(job)["result"]["quality"]
    blob = json.dumps(quality, ensure_ascii=False)
    assert quality["question_mark_count"] == 3
    assert quality["subtitle_quality"]["below_min_display"] == {"count": 2}
    assert quality["translation_summary"]["fallback_success"] == 1
    assert set(quality["sound_reference"]) == {"status", "message", "reason_code"}
    assert "secret" not in blob and "raw_responses" not in blob and "hints" not in blob


def test_a5_公开文案和查询详情都拒绝绝对路径(tmp_path):
    job_dir, job = _job(
        tmp_path, status="error", error="失败于 /Users/andy/private/key.txt",
        source_path="/Users/andy/private/clip.mp4",  # P516：名字从地址现算
        action_error="请检查 /private/tmp/trace.log",
    )
    response = main._public_job_response(job["id"], job, job_dir)
    assert response["state"]["display_message"] == "处理失败"
    assert response["details"]["video_filename"] == "clip.mp4"
    assert response["details"]["action_error"] is None
    assert "/Users/" not in json.dumps(response, ensure_ascii=False)


def test_a5_公开文案拒绝其他绝对路径和配置中的已知密钥(tmp_path):
    job_dir, job = _job(
        tmp_path, status="error",
        error="失败于 /Volumes/media/input.mov，令牌是 secret",
        action_error="临时记录在 /tmp/log/trace.log",
    )
    response = main._public_job_response(job["id"], job, job_dir)
    assert response["state"]["display_message"] == "处理失败"
    assert response["details"]["action_error"] is None
    blob = json.dumps(response, ensure_ascii=False)
    assert "/Volumes/" not in blob
    assert "/tmp/" not in blob
    assert "secret" not in blob


@pytest.mark.parametrize(("error_text", "expected"), [
    (r"失败于 \\server\share\secret.srt", "处理失败"),
    (r"失败于 \\?\C:\Users\andy\secret.srt", "处理失败"),
    ("失败于 /Users/andy/secret.srt", "处理失败"),
    (r"失败于 C:\Users\andy\secret.srt", "处理失败"),
    ("网络连接失败，请稍后重试", "网络连接失败，请稍后重试"),
])
def test_a5_公开响应拦截跨平台绝对路径但保留普通错误文字(
        tmp_path, error_text, expected):
    job_dir, job = _job(
        tmp_path, status="error", error=error_text,
        config={"video_filename": "clip.mp4", "apiKeys": []},
    )
    response = main._public_job_response(job["id"], job, job_dir)
    assert response["state"]["display_message"] == expected


def test_a6_运行文案与current_state都不写入meta(monkeypatch, tmp_path):
    job_dir, job = _job(tmp_path)
    _write_meta(job_dir, job)
    jobs = {job["id"]: job}
    _isolate(monkeypatch, tmp_path, jobs)
    main._sync_job_from_worker_message(job["id"], {
        "type": "progress", "run_id": "run-1", "stage": "translate",
        "percent": 55, "message": "正在等待第 7/9 句", "detail": "调用内部引擎",
    })
    saved = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert saved["revision"] == jobs[job["id"]]["revision"]
    assert saved["stage"] == "translate"
    assert "current_state" not in saved
    assert "message" not in saved
    assert "activity" not in saved
    assert "display_message" not in saved
    assert "detail" not in saved


def test_a6_冷启动忽略旧current_state且旧等待文案不复活(monkeypatch, tmp_path):
    job_id = "cold-job"
    job_dir, job = _job(
        tmp_path, job_id=job_id, status="running", file_hash="a" * 64,
        task_key="a" * 64 + ":cfg",
        current_state={"display_message": "正在等待第 77/99 句", "activity": "旧引擎"},
        message="正在等待第 77/99 句",
    )
    _write_meta(job_dir, job)
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(main.worker_manager, "is_alive", lambda _jid: False)
    main._scan_jobs_into_index()
    response = main.get_job(job_id)
    assert response["state"]["status"] == "interrupted"
    assert response["state"]["revision"] == 5
    saved = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert saved["revision"] == 5
    assert response["state"]["display_message"] == "任务已中断，可以继续"
    assert response["state"]["activity"] is None
    assert "current_state" not in main.JOBS.get(job_id, {})
    assert "等待第 77" not in json.dumps(response, ensure_ascii=False)


def test_a8_旧run消息在后端拒绝且revision不变(monkeypatch, tmp_path):
    job_dir, job = _job(tmp_path)
    _write_meta(job_dir, job)
    jobs = {job["id"]: job}
    _isolate(monkeypatch, tmp_path, jobs)
    before = dict(job)
    rejected = main._sync_job_from_worker_message(job["id"], {
        "type": "progress", "run_id": "old-run", "stage": "extract",
        "percent": 2, "message": "旧消息",
    })
    assert rejected is None
    assert jobs[job["id"]]["revision"] == before["revision"]
    assert jobs[job["id"]]["stage"] == before["stage"]


def test_a8_每条当前run消息只推进顶层revision(monkeypatch, tmp_path):
    job_dir, job = _job(tmp_path)
    _write_meta(job_dir, job)
    jobs = {job["id"]: job}
    _isolate(monkeypatch, tmp_path, jobs)
    first = main._sync_job_from_worker_message(job["id"], {
        "type": "progress", "run_id": "run-1", "stage": "extract", "percent": 5,
    })
    second = main._sync_job_from_worker_message(job["id"], {
        "type": "progress", "run_id": "run-1", "stage": "transcribe", "percent": 10,
    })
    assert (first["revision"], second["revision"]) == (5, 6)
    assert "current_state" not in jobs[job["id"]]


def test_a11_路径只影响两个安全布尔而不进入state(tmp_path):
    _, job = _job(
        tmp_path, status="done", output_ready=True, result_run_id="run-1"
    )
    state = job_state.build_public_task_state(job)
    assert state["result"]["output_ready"] is True
    assert state["result"]["can_show_output_folder"] is True
    blob = json.dumps(state, ensure_ascii=False)
    assert "output.srt" not in blob and "output.mp4" not in blob


def test_a11_查询details是独立白名单且不含路径或密钥(tmp_path):
    job_dir, job = _job(
        tmp_path, status="partial", output_ready=True, segment_count=9,
        success_count=7, failed_count=2,
        failed_segments=[{"index": 1, "reason": "timeout"}],
        internal_error={"trace": "private"},
        source_path="/private/jobs/clip.mp4",  # P516：名字从地址现算
        config={
            "apiKeys": [{"key": "secret"}],
            "style": {"font": "PingFang SC", "secret": "nested-secret"},
        },
    )
    response = main._public_job_response(job["id"], job, job_dir)
    assert set(response) == {"state", "details"}
    assert response["details"]["video_filename"] == "clip.mp4"
    assert response["details"]["failed_segments"] == [{"index": 1, "reason": "timeout"}]
    blob = json.dumps(response, ensure_ascii=False)
    assert "result_srt" not in blob and "result_video" not in blob
    assert "secret" not in blob and "internal_error" not in blob
    assert response["details"]["config"]["style"] == {"font": "PingFang SC"}
    assert "/Users/andy/" not in blob


def test_a9_前端唯一入口只按revision接收固定state():
    html = HTML_PATH.read_text(encoding="utf-8")
    should_apply = _extract_js_function(html, "shouldApplyState")
    apply_state = _extract_js_function(html, "applyTaskState")
    result = _run_node(f"""
const activeJobs={{job1:{{lastState:null}}}};
const calls=[];
function setJobProgress(...args){{calls.push(['progress',...args])}}
function renderCardDone(...args){{calls.push(['done',...args])}}
function handlePartialState(...args){{calls.push(['partial',...args])}}
function renderCardError(...args){{calls.push(['error',...args])}}
function renderInterruptedCard(...args){{calls.push(['interrupted',...args])}}
function removeCard(...args){{calls.push(['cancelled',...args])}}
function showAlignmentFallbackNotice(){{}}
function renderTranslationSummary(){{}}
function showQmWarning(){{}}
{should_apply}
{apply_state}
const newer={{revision:8,status:'running',phase:'translate',percent:44,
  progress:{{total:9,processed:4,succeeded:3,failed:1,remaining:5}},
  display_message:'固定状态文案',activity:'安全活动',run_id:'random-a',
  actions:{{can_resume:false,can_retry:false,resume_action:null}},
  result:{{output_ready:false,can_show_output_folder:false,quality:{{}}}}}};
const accepted=applyTaskState('job1',newer);
const stale=applyTaskState('job1',{{...newer,revision:7,display_message:'旧文案',run_id:'zzz'}});
console.log(JSON.stringify({{accepted,stale,calls,last:activeJobs.job1.lastState}}));
""")
    assert result["accepted"] is True
    assert result["stale"] is False
    assert result["calls"][0][0] == "progress"
    assert "固定状态文案" in result["calls"][0]
    assert result["last"]["revision"] == 8


def test_a10_轮询策略覆盖首次断线静默窗口与终态():
    html = HTML_PATH.read_text(encoding="utf-8")
    compute = _extract_js_function(html, "computePollDelay")
    stop = _extract_js_function(html, "shouldStopPolling")
    result = _run_node(f"""
{compute}
{stop}
console.log(JSON.stringify({{
  first:computePollDelay({{first:true,wsHealthy:false,wsClosed:true,silentMs:0}}),
  healthy:computePollDelay({{first:false,wsHealthy:true,wsClosed:false,silentMs:9000}}),
  closed:computePollDelay({{first:false,wsHealthy:false,wsClosed:true,silentMs:0}}),
  silent1:computePollDelay({{first:false,wsHealthy:true,wsClosed:false,silentMs:10000}}),
  silent2:computePollDelay({{first:false,wsHealthy:true,wsClosed:false,silentMs:20000}}),
  stops:['done','partial','error','interrupted','cancelled'].every(shouldStopPolling)
}}));
""")
    assert result == {
        "first": 0, "healthy": None, "closed": 3000,
        "silent1": 0, "silent2": 0, "stops": True,
    }


def test_a9_done清掉上一轮重试进度文案():
    html = HTML_PATH.read_text(encoding="utf-8")
    render_done = _extract_js_function(html, "renderCardDone")
    result = _run_node(f"""
const classes=new Set(['show','job-retry-status']);
const err={{innerHTML:'本轮待处理 1 · 仍失败 1',classList:{{
  add:(...xs)=>xs.forEach(x=>classes.add(x)),remove:(...xs)=>xs.forEach(x=>classes.delete(x))
}}}};
const els={{'fill-j':{{style:{{}},classList:{{remove:()=>{{}}}}}},'step-j':{{}},
  'pct-j':{{}},'card-j':{{classList:{{add:()=>{{}},remove:()=>{{}}}}}},'result-j':null,'err-j':err}};
const document={{getElementById:id=>els[id]||null}};
function hideMilestones(){{}}
function showAlignmentFallbackNotice(){{}}
{render_done}
renderCardDone('j',{{output_ready:true,can_show_output_folder:false,failed_segments:[]}});
console.log(JSON.stringify({{text:err.innerHTML,classes:[...classes]}}));
""")
    assert result == {"text": "", "classes": []}


def test_a10_onopen标健康_onmessage更新时间_onclose立即查询():
    html = HTML_PATH.read_text(encoding="utf-8")
    compute = _extract_js_function(html, "computePollDelay")
    connect = _extract_js_function(html, "connectCard")
    result = _run_node(f"""
let now=1000;Date.now=()=>now;
const activeJobs={{job1:{{ws:null,pollTimer:null,filename:'clip',config:{{}}}}}};
const timers=[];let fetches=0;
function setTimeout(fn,delay){{timers.push({{fn,delay}});return timers.length}}
function clearTimeout(){{}}
function readJobResponse(r){{return r}}
function handleCardMsg(){{}}
function showMissingJobCard(){{}}
function applyTaskState(){{}}
function shouldStopPolling(){{return false}}
function $(id){{return {{style:{{}},classList:{{add(){{}},remove(){{}}}}}}}}
const location={{protocol:'http:',host:'127.0.0.1:8766'}};
class WebSocket{{constructor(url){{this.url=url;globalThis.socket=this}}}}
function fetch(){{fetches++;return new Promise(()=>{{}})}}
{compute}
{connect}
connectCard('job1');
socket.onopen();const healthyAfterOpen=activeJobs.job1.wsHealthy;
now=2500;socket.onmessage({{data:'{{"type":"state","state":{{}}}}'}});
const lastAfterMessage=activeJobs.job1.lastMsgAt;
socket.onclose();
console.log(JSON.stringify({{healthyAfterOpen,lastAfterMessage,fetches,closedHealthy:activeJobs.job1.wsHealthy}}));
""")
    assert result == {
        "healthyAfterOpen": True,
        "lastAfterMessage": 2500,
        "fetches": 1,
        "closedHealthy": False,
    }


def test_a10_partial从websocket到达后关闭连接并停止轮询():
    html = HTML_PATH.read_text(encoding="utf-8")
    should_apply = _extract_js_function(html, "shouldApplyState")
    apply_state = _extract_js_function(html, "applyTaskState")
    result = _run_node(f"""
let closed=0,handled=0,cleared=0;
const activeJobs={{job1:{{lastState:null,ws:{{close(){{closed++}}}},pollTimer:7,
  pendingDetails:{{retry_count:0}}}}}};
function clearTimeout(){{cleared++}}
function applyTaskDetails(){{handled++}}
function fetchTaskDetails(){{}}
{should_apply}
{apply_state}
const state={{revision:1,status:'partial',phase:'translate',percent:100,
  progress:{{total:9,processed:9,succeeded:7,failed:2,remaining:0}},
  display_message:'部分字幕尚未完成',activity:null,run_id:'r1',
  actions:{{can_resume:false,can_retry:true,resume_action:'retry'}},
  result:{{output_ready:false,can_show_output_folder:false,quality:{{}}}}}};
applyTaskState('job1',state);
console.log(JSON.stringify({{closed,handled,cleared,terminal:activeJobs.job1.terminal}}));
""")
    assert result == {"closed": 1, "handled": 1, "cleared": 1, "terminal": True}
