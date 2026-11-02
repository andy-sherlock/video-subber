"""D-1 补修（2026-10-02）前端合同测试。

一律从 index.html 抽出真实函数用 node 执行（同 test_v309_a8_error_resume.py 的做法）。

A1  点「开始」那一刻固定设置：检测的和提交的是同一份
A2  继续 / 重试前先门口检测；检测不过不发请求
D2  「保留未翻译字幕并生成文件」不检测、不带 Key
C2  重新提交命中中断任务：只显示中断卡，不接回运行
C1/C5 删「重新处理」
"""

import json
import re
import subprocess
from pathlib import Path


HTML = (Path(__file__).parent.parent / "frontend" / "index.html").read_text(
    encoding="utf-8"
)


def _function_source(name):
    match = re.search(
        rf"(?:async\s+)?function\s+{re.escape(name)}\([^)]*\)\{{.*?\n\}}",
        HTML,
        re.S,
    )
    assert match, f"找不到函数 {name}"
    return match.group(0)


def _run_node(source):
    result = subprocess.run(
        ["node", "-e", source], text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


_STUB = """
function element(){const s=new Set();return{
  textContent:'',innerHTML:'',disabled:false,className:'',style:{},children:[],offsetWidth:80,
  classList:{add:c=>s.add(c),remove:c=>s.delete(c),contains:c=>s.has(c),toggle(){}},
  appendChild(x){this.children.push(x);return x},scrollIntoView(){},querySelector:()=>element(),querySelectorAll:()=>[],
}}
const els={};
const $=id=>(els[id]=els[id]||element());
const document={createElement:()=>element(),getElementById:id=>els[id]||null};
let fetched=[];
const fetch=(url,opts)=>{fetched.push({url,method:(opts||{}).method,body:(opts||{}).body?JSON.parse(opts.body):null});
  return Promise.resolve({ok:true,json:async()=>({status:'running',config:{},found:false,id:'NEW',ok:true})})};
const showToast=()=>{};
const activeJobs={};
const setScene=()=>{};const showVideoBox=()=>{};const saveActiveJobs=()=>{};
let connected=[];const connectCard=jid=>connected.push(jid);
let gateCalls=[];let gateResult={};let gateHook=null;
async function startGate(button){gateCalls.push({isStartButton:button===sbtn});
  if(gateHook)await gateHook();return gateResult}
const sbtn=element();
const State={settings:{apiKeys:[{platform:'deepseek',key:'fake-a'}],model:'large-v2'},
  videoFile:null,localPath:null,sourceUrl:null,doneJobConfig:null,configReady:true};
let _configSaveInFlight=false;let _configSavePromise=Promise.resolve(true);
let settingsLocked=false;
const lockUI=()=>{};const unlockUI=()=>{};const refreshStartBtn=()=>{};
const envRuntimeBlocked=()=>false;const envNeedsPrepare=()=>false;
const lpi={value:''};const uri={value:''};const df={style:{}};
const addJobCard=()=>{};const removeActiveJobId=()=>{};
let interrupted=[];const renderInterruptedCard=jid=>interrupted.push(jid);
"""


# ── A1：检测的和提交的是同一份设置 ────────────────────────────────────

def test_start_submits_the_settings_captured_when_start_was_clicked():
    js = "\n".join([
        _STUB,
        _function_source("handleFoundJob"),
        _function_source("startJob"),
        """
State.localPath='/videos/a.mp4';
gateHook=async()=>{State.settings.model='large-v3'};
startJob().then(()=>{
  const post=fetched.find(f=>f.url==='/api/jobs');
  const cfg=JSON.parse(post.body.config);
  console.log(JSON.stringify({gate:gateCalls,hasKeys:'apiKeys' in cfg,model:cfg.model}));
});
""",
    ])

    data = _run_node(js)

    assert data["gate"][0]["isStartButton"] is True
    assert data["hasKeys"] is False, "P512：Key 由后台从设置文件读，网页不递"
    assert data["model"] == "large-v2", "检测中途改了设置，提交的必须仍是点「开始」时那份"


# ── A2 / D2：继续 / 重试前检测；导出不检测 ─────────────────────────────

def _resume_js(tail):
    return "\n".join([_STUB, _function_source("resumeInterruptedJob"), tail])


def test_continue_checks_the_connection_before_resuming():
    data = _run_node(_resume_js("""
const button=element();
gateResult={};
resumeInterruptedJob('J',button).then(()=>console.log(JSON.stringify({gate:gateCalls,fetched})));
"""))

    assert len(data["gate"]) == 1
    assert data["gate"][0]["isStartButton"] is False, "转圈的应是被点的那个按钮"
    assert data["fetched"][0]["url"] == "/api/jobs/J/resume"
    assert "apiKeys" not in data["fetched"][0]["body"], "P512：Key 由后台从设置文件读"


def test_continue_stops_when_the_connection_check_fails():
    data = _run_node(_resume_js("""
const button=element();
gateResult=null;
resumeInterruptedJob('J',button).then(ok=>console.log(JSON.stringify({ok,fetched,disabled:button.disabled})));
"""))

    assert data["ok"] is False
    assert data["fetched"] == [], "检测不过不得发继续请求"
    assert data["disabled"] is False


def test_continue_carries_the_translate_only_choice():
    data = _run_node(_resume_js("""
gateResult={aiTranslateOnly:true};
resumeInterruptedJob('J',element()).then(()=>console.log(JSON.stringify({fetched})));
"""))

    assert data["fetched"][0]["body"]["aiTranslateOnly"] is True


def test_continue_export_only_does_not_check_or_send_keys():
    data = _run_node(_resume_js("""
resumeInterruptedJob('J',element(),{exportOnly:true}).then(()=>console.log(JSON.stringify({gate:gateCalls,fetched})));
"""))

    assert data["gate"] == [], "「继续生成字幕文件」不调用 AI，不检测"
    assert data["fetched"][0]["url"] == "/api/jobs/J/resume"
    assert not (data["fetched"][0]["body"] or {}).get("apiKeys")


def _partial_js(tail):
    stub = _STUB + """
const clearPartialIgnore=()=>{};const resetPartialPromptTicket=()=>{};
const renderRetryProgress=()=>{};const writePartialIgnore=()=>{};
let _partialPromptCurrent=null;const showNextPartialPrompt=()=>{};
"""
    return "\n".join([stub, _function_source("requestPartialAction"), tail])


def test_retry_checks_the_connection_and_sends_no_keys():
    data = _run_node(_partial_js("""
activeJobs.J={partialState:{retry_count:0,failed_count:3}};
requestPartialAction('J','retry',element()).then(()=>console.log(JSON.stringify({gate:gateCalls,fetched})));
"""))

    assert len(data["gate"]) == 1
    assert data["gate"][0]["isStartButton"] is False
    body = data["fetched"][0]["body"]
    assert body == {"action": "retry"}, "P512：Key 由后台从设置文件读"


def test_retry_stops_when_the_connection_check_fails():
    data = _run_node(_partial_js("""
gateResult=null;
requestPartialAction('J','retry',element()).then(ok=>console.log(JSON.stringify({ok,fetched})));
"""))

    assert data["ok"] is False
    assert data["fetched"] == []


def test_export_does_not_check_or_send_keys():
    data = _run_node(_partial_js("""
activeJobs.J={partialState:{retry_count:0,failed_count:3}};
requestPartialAction('J','accept',element()).then(()=>console.log(JSON.stringify({gate:gateCalls,fetched})));
"""))

    assert data["gate"] == []
    assert data["fetched"][0]["body"] == {"action": "accept"}


# ── 门口检测函数：问后台（P512：不带 Key），转圈的是被点的按钮 ─────────

def test_gate_asks_the_backend_on_the_given_button():
    js = "\n".join([
        _STUB.replace("async function startGate", "async function _unusedGate"),
        "const closeGatePanel=()=>{};const gateMark=()=>{};const _gateMinShown=async()=>{};",
        "let tested=0;const testGate=async()=>{tested++;return {ok:true}};",
        _function_source("gatePanelFor"),
        _function_source("startGate"),
        """
const card=element();card.textContent='一键继续';
startGate(card).then(r=>console.log(JSON.stringify({
  r,tested,cardText:card.textContent,startText:sbtn.textContent})));
""",
    ])

    data = _run_node(js)

    assert data["r"] == {}
    assert data["tested"] == 1
    assert data["cardText"] == "一键继续", "检测结束要把按钮文字还原"
    assert data["startText"] == "", "检测卡片按钮时不得动「开始」按钮"


# ── C2：重新提交命中中断任务，只显示中断卡 ─────────────────────────────

def test_resubmitted_interrupted_job_shows_its_card_without_reconnecting():
    js = "\n".join([
        _STUB,
        "const isOriginalModelReady=()=>true;",
        _function_source("taskStateView"),
        _function_source("handleFoundJob"),
        """
handleFoundJob({found:true,state:{job_id:'OLD',status:'interrupted'},details:{config:{}}},'a.mp4');
console.log(JSON.stringify({interrupted,connected}));
""",
    ])

    data = _run_node(js)

    assert data["interrupted"] == ["OLD"]
    assert data["connected"] == [], "中断任务不得被接回运行"


# ── C1 / C5：删「重新处理」 ───────────────────────────────────────────

def test_rerun_switch_is_gone():
    assert "forceRerun" not in HTML
    assert "cfg.force" not in HTML


def test_missing_subtitle_message_no_longer_points_to_rerun():
    assert "请重新处理该视频" not in HTML
    assert "处理已结束，但没有找到这次生成的字幕文件。" in HTML


# ── C2 补（用户 2026-10-02 定）：一般出错卡给「重新开始」 ─────────────

def _error_card_js(msg, tail):
    stub = _STUB + """
const failMilestones=()=>{};const renderOutputFolder=()=>{};const renderTranslationSummary=()=>{};
const openJobLog=()=>{};
function allButtons(el){const out=[];for(const c of el.children){out.push(c);out.push(...allButtons(c))}return out}
"""
    return "\n".join([
        stub,
        _function_source("errorStageText"),
        _function_source("formatJobErrorText"),
        _function_source("hasUserAdvice"),
        _function_source("renderErrorActions"),
        _function_source("resumeInterruptedJob"),
        _function_source("renderCardError"),
        f"renderCardError('J', {json.dumps(msg, ensure_ascii=False)});",
        tail,
    ])


def test_plain_error_card_offers_restart_that_checks_then_resumes():
    data = _run_node(_error_card_js(
        {"message": "链接下载失败，请检查网络后重试。", "stage": "download"},
        """
const b=allButtons(els['err-J']).find(x=>x.textContent==='重新开始');
if(!b){console.log(JSON.stringify({found:false}))}
else b.onclick().then(()=>console.log(JSON.stringify({found:true,gate:gateCalls,fetched})));
""",
    ))

    assert data["found"] is True, "一般出错卡必须给用户一个能点的「重新开始」"
    assert len(data["gate"]) == 1, "重新开始会调用 AI，先门口检测"
    assert data["fetched"][0]["url"] == "/api/jobs/J/resume"
    assert "apiKeys" not in data["fetched"][0]["body"], "P512：Key 由后台从设置文件读"


def test_error_card_with_a_pending_action_does_not_add_restart():
    data = _run_node(_error_card_js(
        {"message": "重试失败部分没能启动，请稍后继续。", "stage": "translate", "resume_action": "retry"},
        """
console.log(JSON.stringify({buttons:allButtons(els['err-J']).map(b=>b.textContent)}));
""",
    ))

    assert "重新开始" not in data["buttons"], "已有「继续重试剩余字幕」时不再多给一个按钮"


# ── ⑦.5 独立审查发现的修复（2026-10-02） ─────────────────────────────

def test_live_interrupted_state_releases_the_ui():
    """#1：后台程序没留原因就停 → 中断。必须移出「进行中」并解锁界面，否则开始按钮一直锁着。"""
    js = "\n".join([
        _STUB,
        "let allDone=[];const checkAllDone=r=>allDone.push(r);const setJobProgress=()=>{};",
        "const shouldApplyState=()=>true;",
        _function_source("applyTaskState"),
        """
activeJobs.J={ws:null,pollTimer:null,filename:'a.mp4',config:{}};
applyTaskState('J',{status:'interrupted',actions:{resume_action:null},result:{}});
console.log(JSON.stringify({still:'J' in activeJobs,allDone,interrupted}));
""",
    ])

    data = _run_node(js)

    assert data["interrupted"] == ["J"]
    assert data["still"] is False
    assert data["allDone"] == ["interrupted"]


def test_resubmitted_interrupted_card_is_not_left_in_the_running_list():
    js = "\n".join([
        _STUB.replace("const addJobCard=()=>{};", "const addJobCard=jid=>{activeJobs[jid]={filename:'a.mp4'}};"),
        "const isOriginalModelReady=()=>true;",
        _function_source("taskStateView"),
        _function_source("handleFoundJob"),
        """
handleFoundJob({found:true,state:{job_id:'OLD',status:'interrupted'},details:{config:{}}},'a.mp4');
console.log(JSON.stringify({still:'OLD' in activeJobs}));
""",
    ])

    assert _run_node(js)["still"] is False


def test_interrupted_export_round_continues_without_check():
    """#4：导出那一轮被中断，继续时仍是导出：不检测、不带 Key（D2）。"""
    js = "\n".join([
        _STUB.replace("let interrupted=[];const renderInterruptedCard=jid=>interrupted.push(jid);", ""),
        _function_source("resumeInterruptedJob"),
        _function_source("renderInterruptedCard"),
        """
function allButtons(el){const out=[];for(const c of el.children){out.push(c);out.push(...allButtons(c))}return out}
renderInterruptedCard('J','accept');
const b=allButtons(els['err-J']).find(x=>x.textContent==='一键继续');
b.onclick().then(()=>console.log(JSON.stringify({gate:gateCalls,fetched})));
""",
    ])

    data = _run_node(js)

    assert data["gate"] == []
    assert not (data["fetched"][0]["body"] or {}).get("apiKeys")


def test_card_gate_failure_is_shown_inside_the_card():
    """#2：处理中 / 已完成画面里「开始」下面的提示面板是隐藏的；卡片按钮检测失败的提示要显示在卡片里。"""
    js = "\n".join([
        _STUB.replace("async function startGate", "async function _unusedGate"),
        "const gateMark=()=>{};const _gateMinShown=async()=>{};const gateReasonText=()=>'API Key 无效';",
        "const openSettingsDrawer=()=>{};const testGate=async()=>({ok:false,kind:'auth'});",
        _function_source("closeGatePanel"),
        _function_source("openGatePanel"),
        _function_source("gatePanelFor"),
        _function_source("startGate"),
        """
const holder=element();const card=element();card.textContent='一键继续';card.parentNode=holder;
startGate(card).then(r=>{
  const panels=holder.children.filter(c=>c.className==='gate-panel');
  console.log(JSON.stringify({r,inCard:panels.length,text:panels.length?panels[0].children[0].textContent:'',
    mainPanel:(els.gatePanel||{}).children?els.gatePanel.children.length:0}));
});
""",
    ])

    data = _run_node(js)

    assert data["r"] is None
    assert data["inCard"] == 1, "提示必须出现在被点按钮所在的卡片里"
    assert data["text"] == "API Key 无效"
    assert data["mainPanel"] == 0


def test_continue_before_settings_loaded_keeps_the_task_keys():
    """#5：设置还没从后端读回来（apiKeys 还是默认空表）时点继续，不得把旧任务的 Key 清空。"""
    data = _run_node(_resume_js("""
State.configReady=false;State.settings.apiKeys=[];
resumeInterruptedJob('J',element()).then(()=>console.log(JSON.stringify({fetched})));
"""))

    assert "apiKeys" not in data["fetched"][0]["body"], "设置未就绪时不带 Key，后台沿用任务自己的 Key"


# ── 终审增量 P517：中断卡不显示「停止任务」；× 先确认再删 ─────────────

def test_interrupted_card_hides_the_stop_button():
    assert ".job-card-interrupted .job-card-stop{display:none}" in HTML


def test_interrupted_card_is_marked_and_connecting_to_a_live_job_unmarks_it():
    js = "\n".join([
        _STUB.replace("let connected=[];const connectCard=jid=>connected.push(jid);", ""),
        "const location={protocol:'http:',host:'x'};class WebSocket{};",
        "const computePollDelay=()=>null;const setTimeout=()=>0;const clearTimeout=()=>{};",
        _function_source("renderInterruptedCard").replace("function renderInterruptedCard", "function realRender"),
        _function_source("connectCard"),
        """
        realRender('J1');
        const marked=$('card-J1').classList.contains('job-card-interrupted');
        activeJobs.J1={filename:'clip.mp4'};
        connectCard('J1');
        console.log(JSON.stringify({marked,after:$('card-J1').classList.contains('job-card-interrupted')}));
        """,
    ])
    out = _run_node(js)
    assert out == {"marked": True, "after": False}, "卡片重新在跑（继续 / 重新开始 / 重拖重跑），「停止任务」要回来"


def test_every_path_back_to_running_goes_through_connect_card():
    for name in ("resumeInterruptedJob", "handleFoundJob"):
        assert "connectCard(" in _function_source(name), name


def _close_js(setup, tail):
    return "\n".join([
        """
        function element(){const s=new Set();return{textContent:'',classList:{add:c=>s.add(c),remove:c=>s.delete(c),contains:c=>s.has(c)},
          querySelector:()=>({textContent:'clip.mp4'})}}
        const els={};const $=id=>(els[id]=els[id]||element());
        const activeJobs={};let deleted=[];let removed=[];let modal=null;let path=[];
        const fetchWithTimeout=(url,opts)=>{deleted.push([url,opts.method]);return Promise.resolve({ok:true,json:async()=>({ok:true})})};
        const setInfoModal=(title,body,actions)=>{modal={title,body,actions}};
        const removeCard=(jid,opts)=>removed.push(jid);
        const showToast=()=>{};const refreshStartBtn=()=>{};
        const dismissRestoredCard=jid=>{path.push('restored');deleted.push(['/api/jobs/'+jid,'DELETE'])};
        const dismissTerminalCard=jid=>path.push('terminal');
        const requestCancelCard=jid=>path.push('cancel');
        """,
        _function_source("cancelCard"),
        _function_source("discardInterruptedCard"),
        setup,
        tail,
    ])


def _close_flow(setup):
    tail = """
    (async()=>{
      cancelCard('J1');
      const before={deleted:deleted.length,title:modal&&modal.title};
      const danger=modal&&modal.actions.find(a=>a.danger);
      if(danger)await danger.onClick();
      await new Promise(r=>setTimeout(r,0));
      console.log(JSON.stringify({before,deleted,removed,path,body:modal&&modal.body}));
    })();
    """
    return _run_node(_close_js(setup, tail))


def test_closing_a_live_interrupted_card_asks_then_deletes():
    out = _close_flow("$('card-J1').classList.add('job-card-interrupted');")
    assert out["before"] == {"deleted": 0, "title": "关掉这张卡？"}
    assert out["deleted"] == [["/api/jobs/J1", "DELETE"]]
    assert out["removed"] == ["J1"]
    assert out["path"] == []
    assert "不能再继续" in out["body"]


def test_closing_a_restored_interrupted_card_also_asks_first():
    out = _close_flow(
        "$('card-J1').classList.add('job-card-interrupted');"
        "activeJobs.J1={filename:'clip.mp4',restoredStatus:'interrupted'};"
    )
    assert out["before"] == {"deleted": 0, "title": "关掉这张卡？"}, "刷新后的中断卡不能一点就删"
    assert out["deleted"] == [["/api/jobs/J1", "DELETE"]]
    assert out["path"] == []


def test_closing_a_running_card_is_unchanged():
    out = _close_flow("activeJobs.J1={filename:'clip.mp4'};")
    assert out["before"]["title"] == "取消这个任务？"
    assert out["path"] == ["cancel"]
