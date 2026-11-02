"""D-1 补修第二批（2026-10-03）前端合同测试。

做法同 test_d1b_frontend.py：从 index.html 抽出真实函数用 node 执行。
合同见 pipelines/方案-D1补修-第二批-v2-2026-10-03.md。
"""

import json

from test.test_d1b_frontend import _function_source, _run_node


# ── P520：卡片重新在跑后，停止 / × 一律先确认 ──────────────────────────

def _connect_then_close(setup):
    js = "\n".join([
        """
        function element(){const s=new Set();return{textContent:'',classList:{add:c=>s.add(c),remove:c=>s.delete(c),contains:c=>s.has(c)},
          querySelector:()=>({textContent:'clip.mp4'})}}
        const els={};const $=id=>(els[id]=els[id]||element());
        const activeJobs={};let modal=null;let path=[];
        const location={protocol:'http:',host:'x'};class WebSocket{};
        const computePollDelay=()=>null;const setTimeout=()=>0;const clearTimeout=()=>{};
        const setInfoModal=(title,body,actions)=>{modal={title,body,actions}};
        const dismissRestoredCard=jid=>path.push('restored');
        const dismissTerminalCard=jid=>path.push('terminal');
        const requestCancelCard=jid=>path.push('cancel');
        const discardInterruptedCard=jid=>path.push('discard');
        """,
        _function_source("markRestoredCard"),
        _function_source("connectCard"),
        _function_source("cancelCard"),
        setup,
        "cancelCard('J1');console.log(JSON.stringify({title:modal&&modal.title,path}));",
    ])
    return _run_node(js)


def test_restored_card_that_resumed_asks_before_stopping():
    # 刷新 → 中断卡「一键继续」→ 在跑 → 点停止
    out = _connect_then_close("""
        activeJobs.J1={filename:'clip.mp4'};
        markRestoredCard('J1','interrupted');
        connectCard('J1');
    """)
    assert out == {"title": "取消这个任务？", "path": []}


def test_live_partial_card_that_retried_asks_before_stopping():
    # 没刷新过的部分完成卡也挂着 restoredStatus='partial'（handlePartialState），点重试后在跑
    out = _connect_then_close("""
        activeJobs.J1={filename:'clip.mp4',restoredStatus:'partial'};
        connectCard('J1');
    """)
    assert out == {"title": "取消这个任务？", "path": []}


def test_finished_restored_card_still_closes_without_asking():
    # A20-2：已结束的恢复卡（没有重新在跑）关闭行为不变
    for status in ("done", "partial"):
        out = _connect_then_close(f"""
            activeJobs.J1={{filename:'clip.mp4'}};
            markRestoredCard('J1','{status}');
        """)
        assert out == {"title": None, "path": ["restored"]}, status


# ── P512：网页的请求不再带 Key；后台自己读设置文件 ─────────────────────

from test.test_d1b_frontend import _STUB  # noqa: E402

_NO_KEY_STUB = _STUB  # 第一批的替身已改为 startGate(button)，不再收 Key


def _bodies(data):
    return [f["body"] for f in data["fetched"] if f["body"] is not None]


def test_start_does_not_send_api_keys():
    js = "\n".join([_NO_KEY_STUB, _function_source("handleFoundJob"), _function_source("startJob"), """
State.localPath='/videos/a.mp4';
startJob().then(()=>console.log(JSON.stringify({fetched})));
"""])
    data = _run_node(js)
    cfg = json.loads(_bodies(data)[0]["config"])
    assert "apiKeys" not in cfg


def test_continue_and_restart_do_not_send_api_keys():
    js = "\n".join([_NO_KEY_STUB, _function_source("resumeInterruptedJob"), """
resumeInterruptedJob('J',element()).then(()=>console.log(JSON.stringify({fetched})));
"""])
    data = _run_node(js)
    assert data["fetched"][0]["url"] == "/api/jobs/J/resume"
    assert "apiKeys" not in (data["fetched"][0]["body"] or {})


def test_retry_does_not_wait_for_settings_save_and_does_not_send_api_keys():
    js = "\n".join([_NO_KEY_STUB,
                    "let _partialPromptCurrent=null;const clearPartialIgnore=()=>{};const resetPartialPromptTicket=()=>{};"
                    "const renderRetryProgress=()=>{};const writePartialIgnore=()=>{};const showNextPartialPrompt=()=>{};",
                    _function_source("requestPartialAction"), """
_configSaveInFlight=true;_configSavePromise=new Promise(()=>{});   // 设置保存永远不落地
activeJobs.J={ws:null,partialState:{failed_count:1,retry_count:0}};
requestPartialAction('J','retry',element()).then(()=>console.log(JSON.stringify({fetched})));
"""])
    data = _run_node(js)
    assert data["fetched"][0]["url"] == "/api/jobs/J/partial-action", "不得再等设置保存"
    assert data["fetched"][0]["body"] == {"action": "retry"}


def _gate_js(tail, gate_reply):
    return "\n".join([
        """
        function element(){const s=new Set();return{textContent:'开始',innerHTML:'',disabled:false,style:{},offsetWidth:80,
          parentNode:null,classList:{add:c=>s.add(c),remove:c=>s.delete(c),contains:c=>s.has(c)}}}
        const els={};const $=id=>(els[id]=els[id]||element());const sbtn=element();
        let fetched=[];let panels=[];let drawer=[];let closedModal=0;
        const fetch=(url,opts)=>{fetched.push({url,body:opts&&opts.body?JSON.parse(opts.body):null});
          return Promise.resolve({ok:true,json:async()=>(%s)})};
        const gatePanelFor=b=>'panel';const closeGatePanel=()=>{};const gateMark=()=>{};
        const _gateMinShown=()=>Promise.resolve();const refreshStartBtn=()=>{};const gateReasonText=k=>k;
        const askTranslateOnly=async()=>({aiTranslateOnly:true});
        const openGatePanel=(msg,actions,panel)=>panels.push(actions);
        const openSettingsDrawer=s=>drawer.push(s);
        const closeInfoModal=()=>{closedModal++};
        const setTimeout=(f)=>f();
        """ % gate_reply,
        _function_source("testGate"),
        _function_source("startGate"),
        tail,
    ])


def test_gate_asks_the_backend_without_sending_keys():
    data = _run_node(_gate_js(
        "startGate(sbtn).then(r=>console.log(JSON.stringify({r,fetched})));",
        "{ok:true,translate_only:false,kind:null}"))
    assert data["fetched"] == [{"url": "/api/llm-gate", "body": {}}]
    assert data["r"] == {}


def test_gate_lets_the_task_start_when_no_key_is_saved():
    # 删除免费翻译前，不填 Key 仍可开始（方案 §5 残留，行为不变）
    data = _run_node(_gate_js(
        "startGate(sbtn).then(r=>console.log(JSON.stringify({r})));",
        "{ok:false,translate_only:false,kind:'no_key'}"))
    assert data["r"] == {}


def test_go_to_settings_closes_the_dialog_first():
    # A12-4：部分完成弹窗里检测失败点「去设置」，抽屉不能被弹窗挡住
    data = _run_node(_gate_js("""
$('infoModalOverlay').classList.add('active');
startGate(element()).then(r=>{panels[0][0].onclick();console.log(JSON.stringify({r,drawer,closedModal}))});
""", "{ok:false,translate_only:false,kind:'auth'}"))
    assert data["r"] is None
    assert data["closedModal"] == 1
    assert data["drawer"] == ["api"]


# ── P494：「收地址」接口——地址放在请求最外层 ───────────────────────────

def test_local_path_is_sent_at_the_top_level_of_the_request():
    js = "\n".join([_STUB, _function_source("handleFoundJob"), _function_source("startJob"), """
State.localPath='/Users/x/视频/旅行.mp4';
startJob().then(()=>console.log(JSON.stringify({fetched})));
"""])
    data = _run_node(js)
    body = _bodies(data)[0]
    assert body["local_path"] == "/Users/x/视频/旅行.mp4"
    assert "local_path" not in json.loads(body["config"]), "地址只放外层一处"


# ── P516 / Z2：卡片名只认后台从地址算出的名字 ───────────────────────────

def test_card_name_follows_the_name_from_the_backend():
    # 链接任务：卡片先用占位名建，下载完成后状态里带上真名
    js = "\n".join([
        """
        function element(){return{textContent:''}}
        const name=element();const card={querySelector:s=>s==='.job-card-name'?name:null};
        const $=id=>id==='card-J'?card:null;
        const activeJobs={J:{filename:'watch',lastState:null}};
        const shouldApplyState=()=>true;let progress=[];
        const setJobProgress=(...a)=>progress.push(a);
        name.textContent='watch';
        """,
        _function_source("applyTaskState"),
        """
        applyTaskState('J',{status:'running',percent:3,video_filename:'Real Title_video.mp4'});
        const first=name.textContent;
        applyTaskState('J',{status:'running',percent:4,video_filename:null});
        console.log(JSON.stringify({first,after:name.textContent,job:activeJobs.J.filename}));
        """,
    ])
    assert _run_node(js) == {"first": "Real Title_video.mp4", "after": "Real Title_video.mp4",
                              "job": "Real Title_video.mp4"}


def test_found_card_uses_the_name_from_the_backend():
    stub = _STUB.replace("const addJobCard=()=>{};", "let added=[];const addJobCard=(jid,name)=>added.push(name);")
    js = "\n".join([stub, """
const taskStateView=(s,d)=>({status:s.status,resume_action:null});
const isOriginalModelReady=()=>true;
""", _function_source("handleFoundJob"), """
handleFoundJob({found:true,state:{job_id:'J',status:'interrupted',video_filename:'旅行.mp4'},details:{video_filename:'旅行.mp4',config:{}}},'拖进来的名字.mp4');
console.log(JSON.stringify({added}));
"""])
    assert _run_node(js) == {"added": ["旅行.mp4"]}


def test_no_snapshot_failure_copy_is_left():
    # Z5：快照删了，「原视频已移动且未能保存副本」这句再也不会出现
    assert "snapshot_failed" not in _function_source("partialCopy")


# ── ⑨ 浏览器实测暴露：整页脚本必须能被解析（单个函数抠出来跑的测试看不到这一层）──

def test_the_whole_page_script_parses():
    import re
    import subprocess
    import tempfile
    from test.test_d1b_frontend import HTML
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", HTML, re.S)
    assert scripts, "页面里没找到内联脚本"
    for i, code in enumerate(scripts):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(code)
        result = subprocess.run(["node", "--check", f.name], capture_output=True, text=True)
        assert result.returncode == 0, f"第 {i + 1} 段内联脚本语法错误：{result.stderr[-400:]}"
