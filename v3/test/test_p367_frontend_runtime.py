"""P367 前端：真实执行保存顺序和 partial FIFO，不做源码字符串假绿。"""

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
    return json.loads(result.stdout)


def _startup_recovery_sources():
    return "\n".join([
        _function_source("fetchWithTimeout"),
        _function_source("readActiveJobIds"),
        _function_source("writeActiveJobIds"),
        _function_source("addActiveJobIds"),
        _function_source("taskStateView"),
        _function_source("restoreActiveJobsAtStartup"),
    ])


# P512（2026-10-03）：「重试前等设置保存」已按方案删除——Key 由后台在收到
# 请求时读设置文件。新合同「重试不再等保存」见 test_d1c_batch2_frontend.py。


def test_partial_prompts_are_fifo_ticketed_on_display_and_skip_stale_items():
    declarations = re.search(
        r"const _partialPromptQueue=\[\];.*?let _partialPromptCurrent=null;",
        HTML,
        re.S,
    ).group(0)
    js = "\n".join(
        [
            """
function element(){const s=new Set();return{textContent:'',innerHTML:'',children:[],classList:{add:c=>s.add(c),remove:c=>s.delete(c),contains:c=>s.has(c)},appendChild(x){this.children.push(x)}}}
const els={infoModalOverlay:element(),infoModalTitle:element(),infoModalBody:element(),infoModalActions:element()};
const $=id=>els[id];const document={createElement:()=>element()};
const storage=new Map();const localStorage={getItem:k=>storage.has(k)?storage.get(k):null,setItem:(k,v)=>storage.set(k,String(v))};
const activeJobs={A:{partialState:{retry_count:0,output_ready:false,failed_count:1}},B:{partialState:{retry_count:0,output_ready:false,failed_count:1}},C:{partialState:{retry_count:0,output_ready:false,failed_count:1}},D:{partialState:{retry_count:0,output_ready:false,failed_count:1}},E:{partialState:{retry_count:0,output_ready:false,failed_count:1}}};
const partialCopy=msg=>({title:msg.title,body:msg.title});
const requestPartialAction=()=>Promise.resolve(true);const isPartialIgnored=()=>false;
""",
            declarations,
            _function_source("enqueuePartialPrompt"),
            _function_source("showNextPartialPrompt"),
            _function_source("closeInfoModal"),
            """
enqueuePartialPrompt('A',{retry_count:0,output_ready:false,failed_count:1,title:'A'});
enqueuePartialPrompt('B',{retry_count:0,output_ready:false,failed_count:1,title:'B'});
const whileA={current:_partialPromptCurrent.jid,bTicket:storage.get('partialPrompt:B')||null,title:els.infoModalTitle.textContent};
closeInfoModal();
const afterA={current:_partialPromptCurrent.jid,bTicket:storage.get('partialPrompt:B'),title:els.infoModalTitle.textContent};
closeInfoModal();

enqueuePartialPrompt('C',{retry_count:0,output_ready:false,failed_count:1,title:'C'});
enqueuePartialPrompt('D',{retry_count:0,output_ready:false,failed_count:1,title:'D'});
activeJobs.D.partialActionPending=true;closeInfoModal();
const stale={current:_partialPromptCurrent,dTicket:storage.get('partialPrompt:D')||null};

els.infoModalOverlay.classList.add('active');
enqueuePartialPrompt('E',{retry_count:0,output_ready:false,failed_count:1,title:'E'});
const userModalWait={current:_partialPromptCurrent,eTicket:storage.get('partialPrompt:E')||null};
closeInfoModal();
const afterUserModal={current:_partialPromptCurrent.jid,eTicket:storage.get('partialPrompt:E'),title:els.infoModalTitle.textContent};
console.log(JSON.stringify({whileA,afterA,stale,userModalWait,afterUserModal}));
""",
        ]
    )
    data = _run_node(js)

    assert data["whileA"] == {"current": "A", "bTicket": None, "title": "A"}
    assert data["afterA"] == {"current": "B", "bTicket": "0", "title": "B"}
    assert data["stale"] == {"current": None, "dTicket": None}
    assert data["userModalWait"] == {"current": None, "eTicket": None}
    assert data["afterUserModal"] == {"current": "E", "eTicket": "0", "title": "E"}


def test_startup_recovery_merges_local_and_backend_partial_jobs():
    js = "\n".join([
        "const storage=new Map([['activeJobs',JSON.stringify(['local'])]]);",
        "const localStorage={getItem:k=>storage.has(k)?storage.get(k):null,setItem:(k,v)=>storage.set(k,String(v))};",
        "const isPartialIgnored=()=>false;let reconnects=0;const reconnectJobs=()=>{reconnects+=1};",
        "const fetch=async()=>({ok:true,json:async()=>({jobs:[{state:{job_id:'backend',status:'partial',progress:{}},details:{retry_count:0}}]})});",
        _startup_recovery_sources(),
        "restoreActiveJobsAtStartup().then(()=>console.log(JSON.stringify({ids:JSON.parse(storage.get('activeJobs')),reconnects})));",
    ])

    data = _run_node(js)

    assert data == {"ids": ["local", "backend"], "reconnects": 1}


def test_startup_recovery_filters_ignored_backend_partial_after_merge():
    js = "\n".join([
        "const storage=new Map([['activeJobs',JSON.stringify(['local'])]]);",
        "const localStorage={getItem:k=>storage.has(k)?storage.get(k):null,setItem:(k,v)=>storage.set(k,String(v))};",
        "const isPartialIgnored=(jid,msg)=>jid==='ignored'&&msg.retry_count===2;let reconnects=0;const reconnectJobs=()=>{reconnects+=1};",
        "const fetch=async()=>({ok:true,json:async()=>({jobs:[{state:{job_id:'ignored',status:'partial',progress:{}},details:{retry_count:2}}]})});",
        _startup_recovery_sources(),
        "restoreActiveJobsAtStartup().then(()=>console.log(JSON.stringify({ids:JSON.parse(storage.get('activeJobs')),reconnects})));",
    ])

    data = _run_node(js)

    assert data == {"ids": ["local"], "reconnects": 1}


def test_save_active_jobs_adds_without_dropping_ids_not_yet_restored():
    js = "\n".join([
        "const storage=new Map([['activeJobs',JSON.stringify(['local','waiting'])]]);",
        "const localStorage={getItem:k=>storage.has(k)?storage.get(k):null,setItem:(k,v)=>storage.set(k,String(v))};",
        "const activeJobs={local:{},fresh:{}};",
        _function_source("readActiveJobIds"),
        _function_source("writeActiveJobIds"),
        _function_source("addActiveJobIds"),
        _function_source("saveActiveJobs"),
        "saveActiveJobs();console.log(JSON.stringify(JSON.parse(storage.get('activeJobs'))));",
    ])

    assert _run_node(js) == ["local", "waiting", "fresh"]


def test_startup_recovery_falls_back_to_local_jobs_when_backend_fails():
    js = "\n".join([
        "const storage=new Map([['activeJobs',JSON.stringify(['local'])]]);",
        "const localStorage={getItem:k=>storage.has(k)?storage.get(k):null,setItem:(k,v)=>storage.set(k,String(v))};",
        "const isPartialIgnored=()=>false;let reconnects=0;const reconnectJobs=()=>{reconnects+=1};",
        "const fetch=async()=>{throw new Error('offline')};",
        _startup_recovery_sources(),
        "restoreActiveJobsAtStartup().then(()=>console.log(JSON.stringify({ids:JSON.parse(storage.get('activeJobs')),reconnects})));",
    ])

    data = _run_node(js)

    assert data == {"ids": ["local"], "reconnects": 1}


def test_partial_prompt_with_direct_export_renders_english_source_note_in_all_tiers():
    declarations = re.search(
        r"const _partialPromptQueue=\[\];.*?let _partialPromptCurrent=null;",
        HTML,
        re.S,
    ).group(0)
    js = "\n".join([
        """
function element(){const s=new Set();return{textContent:'',innerHTML:'',children:[],className:'',classList:{add:c=>s.add(c),remove:c=>s.delete(c),contains:c=>s.has(c)},appendChild(x){this.children.push(x)}}}
const els={infoModalOverlay:element(),infoModalTitle:element(),infoModalBody:element(),infoModalActions:element()};
const $=id=>els[id];const document={createElement:()=>element()};
const storage=new Map();const localStorage={getItem:k=>storage.has(k)?storage.get(k):null,setItem:(k,v)=>storage.set(k,String(v))};
const activeJobs={first:{partialState:{retry_count:0,output_ready:false,failed_count:2}},tired:{partialState:{retry_count:2,output_ready:false,failed_count:2}}};
const partialCopy=msg=>({title:msg.title,body:msg.title});const requestPartialAction=()=>Promise.resolve(true);const isPartialIgnored=()=>false;
""",
        declarations,
        _function_source("enqueuePartialPrompt"),
        _function_source("showNextPartialPrompt"),
        _function_source("closeInfoModal"),
        """
enqueuePartialPrompt('first',{retry_count:0,output_ready:false,failed_count:2,consecutive_no_progress:0,title:'first'});
const first={notes:els.infoModalBody.children.map(x=>x.textContent),actions:els.infoModalActions.children.map(x=>x.textContent)};
closeInfoModal();els.infoModalBody.children=[];els.infoModalActions.children=[];
enqueuePartialPrompt('tired',{retry_count:2,output_ready:false,failed_count:2,consecutive_no_progress:2,title:'tired'});
const tired={notes:els.infoModalBody.children.map(x=>x.textContent),actions:els.infoModalActions.children.map(x=>x.textContent)};
console.log(JSON.stringify({first,tired}));
""",
    ])

    data = _run_node(js)
    note = "未翻译的句子会在字幕里显示英文原文"

    assert note in data["first"]["notes"]
    assert note in data["tired"]["notes"]
    assert "保留未翻译字幕并生成文件" in data["first"]["actions"]
    assert data["tired"]["actions"] == ["保留未翻译字幕并生成文件"]


def test_rejected_partial_action_restores_card_and_shows_human_message():
    js = "\n".join([
        """
const activeJobs={j:{partialActionPending:true}};let rendered=0,saved=0,toasts=[];let settingsLocked=true;
const renderPartialCard=()=>{rendered+=1};const saveActiveJobs=()=>{saved+=1};const unlockUI=()=>{};const refreshStartBtn=()=>{};
const showToast=msg=>toasts.push(msg);const writePartialIgnore=()=>{};const removeActiveJobId=()=>{};const enqueuePartialPrompt=()=>{};const isPartialIgnored=()=>false;const clearPartialIgnore=()=>{};const resetPartialPromptTicket=()=>{};
const localStorage={getItem:()=> '1'};
""",
        _function_source("handlePartialState"),
        "handlePartialState('j',{retry_count:1,output_ready:false,action_error:'翻译缓存与当前字幕不匹配，任务仍保留'});console.log(JSON.stringify({pending:activeJobs.j.partialActionPending,rendered,saved,toasts}));",
    ])

    data = _run_node(js)

    assert data == {
        "pending": False,
        "rendered": 1,
        "saved": 1,
        "toasts": ["翻译缓存与当前字幕不匹配，任务仍保留"],
    }


def test_job_http_response_distinguishes_missing_from_temporary_failures():
    """P360：只有 404 是任务消失；500 和断网不能清掉恢复记录。"""
    js = "\n".join([
        _function_source("readJobResponse"),
        """
async function run(){
  let jsonCalls=0;
  const missing=await readJobResponse({ok:false,status:404,json:async()=>{jsonCalls+=1;return {}}});
  const temporary=await readJobResponse({ok:false,status:500,json:async()=>{jsonCalls+=1;return {}}});
  const ok=await readJobResponse({ok:true,status:200,json:async()=>{jsonCalls+=1;return {id:'j'}}});
  console.log(JSON.stringify({missing,temporary,ok,jsonCalls}));
}
run();
""",
    ])

    data = _run_node(js)

    assert data == {
        "missing": {"kind": "missing"},
        "temporary": {"kind": "temporary"},
        "ok": {"kind": "ok", "job": {"id": "j"}},
        "jsonCalls": 1,
    }


def test_unknown_model_is_normalized_before_slider_render():
    """P363：旧模型 id 进入模型区时，说明、选中项和保存值必须统一。"""
    js = "\n".join([
        """
function element(){return{innerHTML:'',children:[],textContent:'',className:'',classList:{toggle(){},add(){}},appendChild(x){this.children.push(x)},addEventListener(){}}}
const els={modelDots:element(),modelHintText:element(),modelCurrent:element()};
const $=id=>els[id];
const document={createElement:()=>element()};
const State={settings:{model:'medium'}};
const DEFAULT_MODEL='large-v2';
const MODELS=['large-v3-turbo','large-v2','large-v3'];
const MODEL_LABELS={'large-v3-turbo':'更快','large-v2':'日常推荐','large-v3':'高准确度'};
const MODEL_INFO={'large-v3-turbo':'快','large-v2':'默认说明','large-v3':'准'};
const MODEL_SCENE={'large-v3-turbo':'快','large-v2':'稳','large-v3':'准'};
const modelDownloads={'large-v3-turbo':{downloaded:true},'large-v2':{downloaded:true},'large-v3':{downloaded:true}};
let pendingDeleteModel=null;
const formatModelStatusText=()=>'';const onModelDotClick=()=>{};const syncMainModelSummary=()=>{};const alignModelLine=()=>{};
""",
        _function_source("buildModelSlider"),
        "buildModelSlider();console.log(JSON.stringify({model:State.settings.model,current:els.modelCurrent.innerHTML,active:els.modelDots.children.filter(x=>x.className.includes('active')).length}));",
    ])

    data = _run_node(js)

    assert data["model"] == "large-v2"
    assert "默认说明" in data["current"]
    assert data["active"] == 1


def test_reconnect_and_card_poll_use_the_same_http_classifier():
    """P360：页面刷新恢复和卡片轮询不能再各自发明一套错误规则。"""
    reconnect = _function_source("reconnectJobs")
    connect = _function_source("connectCard")

    assert "readJobResponse" in reconnect
    assert "readJobResponse" in connect
    assert "result.kind==='missing'" in reconnect
    assert "result.kind==='missing'" in connect
    assert "activeJobs[jid]" in connect


def test_partial_action_response_updates_card_authoritative_config():
    """P369：Retry 后后端的配置变了，完成卡不能继续拿旧配置做比较。"""
    js = "\n".join([
        """
const activeJobs={j:{partialState:{retry_count:1},partialActionPending:false,ws:{}}};
const State={settings:{apiKeys:[]},configReady:true};async function startGate(){return {}}let _configSaveInFlight=false;let _configSavePromise=Promise.resolve(true);
const localStorage={};const clearPartialIgnore=()=>{};const resetPartialPromptTicket=()=>{};
const writePartialIgnore=()=>{};const removeActiveJobId=()=>{};const connectCard=()=>{};
const showNextPartialPrompt=()=>{};const showToast=()=>{};const $=()=>({classList:{remove(){}}});
let _partialPromptCurrent=null;
""",
        "const fetch=async()=>({ok:true,json:async()=>({config:{model:'large-v3',contextAware:true}})});",
        _function_source("requestPartialAction"),
        "requestPartialAction('j','retry',null).then(()=>console.log(JSON.stringify(activeJobs.j.config)));",
    ])

    assert _run_node(js) == {
        "model": "large-v3",
        "contextAware": True,
    }


def test_job_poll_refreshes_card_config_from_backend():
    """P369：后续轮询也必须继续同步后端的权威配置。"""
    apply_details = _function_source("applyTaskDetails")
    assert "job.config={...details.config}" in apply_details


def test_partial_prompt_is_shown_when_output_is_ready_but_sentences_failed():
    declarations = re.search(
        r"const _partialPromptQueue=\[\];.*?let _partialPromptCurrent=null;",
        HTML,
        re.S,
    ).group(0)
    js = "\n".join([
        "function element(){const s=new Set();return{textContent:'',innerHTML:'',children:[],className:'',classList:{add:c=>s.add(c),remove:c=>s.delete(c),contains:c=>s.has(c)},appendChild(x){this.children.push(x)}}}",
        "const els={infoModalOverlay:element(),infoModalTitle:element(),infoModalBody:element(),infoModalActions:element()};const $=id=>els[id];const document={createElement:()=>element()};",
        "const storage=new Map();const localStorage={getItem:k=>storage.has(k)?storage.get(k):null,setItem:(k,v)=>storage.set(k,String(v)),removeItem:k=>storage.delete(k)};",
        "const activeJobs={j:{partialState:{retry_count:0,output_ready:true,failed_count:2}}};const partialCopy=msg=>({title:'失败',body:'仍有失败句'});const requestPartialAction=()=>Promise.resolve(true);",
        "const isPartialIgnored=()=>false;",
        declarations,
        _function_source("enqueuePartialPrompt"),
        _function_source("showNextPartialPrompt"),
        "enqueuePartialPrompt('j',{retry_count:0,output_ready:true,failed_count:2});console.log(JSON.stringify({current:_partialPromptCurrent&&_partialPromptCurrent.jid,actions:els.infoModalActions.children.map(x=>x.textContent)}));",
    ])

    assert _run_node(js) == {"current": "j", "actions": ["再次重试剩余字幕"]}


def test_partial_ignore_and_prompt_ticket_are_cleared_after_rejected_accept():
    js = "\n".join([
        "const _partialPromptKeys=new Set();const activeJobs={j:{partialActionPending:true}};let rendered=0,saved=0,removed=0,toasts=[];let settingsLocked=true;",
        "const renderPartialCard=()=>{rendered+=1};const saveActiveJobs=()=>{saved+=1};const removeActiveJobId=()=>{removed+=1};const unlockUI=()=>{};const refreshStartBtn=()=>{};const showToast=m=>toasts.push(m);const enqueuePartialPrompt=()=>{};",
        "const storage=new Map([['partialJobIgnores',JSON.stringify({j:{dismissed_retry_count:0}})],['partialPrompt:j','0']]);const localStorage={getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,String(v)),removeItem:k=>storage.delete(k)};",
        _function_source("partialIgnores"),
        _function_source("clearPartialIgnore"),
        _function_source("resetPartialPromptTicket"),
        _function_source("isPartialIgnored"),
        _function_source("handlePartialState"),
        "handlePartialState('j',{retry_count:0,output_ready:false,failed_count:2,action_error:'缓存不匹配'});console.log(JSON.stringify({rendered,saved,removed,toasts,ignore:storage.get('partialJobIgnores'),ticket:storage.has('partialPrompt:j')?storage.get('partialPrompt:j'):null}));",
    ])

    data = _run_node(js)
    assert data["rendered"] == 1
    assert data["saved"] == 1
    assert data["removed"] == 0
    assert data["toasts"] == ["缓存不匹配"]
    assert data["ignore"] == "{}"
    assert data["ticket"] is None


def test_partial_card_buttons_use_project_action_class():
    js = "\n".join([
        "function element(){const s=new Set();return{textContent:'',innerHTML:'',children:[],className:'',style:{},classList:{add:c=>s.add(c),remove:c=>s.delete(c),contains:c=>s.has(c)},appendChild(x){this.children.push(x)},removeAttribute(){}}}",
        "const els={'step-j':element(),'err-j':element(),'card-j':element(),'fill-j':element(),'pct-j':element()};const $=id=>els[id];const document={createElement:()=>element()};const partialCopy=()=>({title:'x',body:'y'});const requestPartialAction=()=>Promise.resolve(true);const renderOutputFolder=()=>{};",
        _function_source("renderPartialCard"),
        "renderPartialCard('j',{failed_count:2,retry_count:0,output_ready:false});console.log(JSON.stringify(els['err-j'].children.slice(1).map(x=>x.className)));",
    ])

    assert _run_node(js) == ["job-error-action", "job-error-action"]


def test_partial_card_and_modal_use_terminal_counts_and_clear_actions():
    declarations = re.search(
        r"const _partialPromptQueue=\[\];.*?let _partialPromptCurrent=null;",
        HTML,
        re.S,
    ).group(0)
    js = "\n".join([
        "function element(){const s=new Set();return{textContent:'',innerHTML:'',children:[],className:'',style:{},classList:{add:c=>s.add(c),remove:c=>s.delete(c),contains:c=>s.has(c)},appendChild(x){this.children.push(x)},removeAttribute(){}}}",
        "const els={'step-j':element(),'err-j':element(),'card-j':element(),'fill-j':element(),'pct-j':element(),'stop-j':element(),'result-j':element(),'dlV-j':element(),'dlS-j':element(),infoModalOverlay:element(),infoModalTitle:element(),infoModalBody:element(),infoModalActions:element()};const $=id=>els[id];const document={createElement:()=>element()};",
        "const storage=new Map();const localStorage={getItem:k=>storage.has(k)?storage.get(k):null,setItem:(k,v)=>storage.set(k,String(v)),removeItem:k=>storage.delete(k)};const activeJobs={j:{partialState:{retry_count:0,failed_count:2,output_ready:false}}};const partialCopy=()=>({title:'还差2句',body:'正文'});const requestPartialAction=()=>Promise.resolve(true);const isPartialIgnored=()=>false;const openPartialOptions=()=>{};",
        declarations,
        _function_source("renderOutputFolder"),
        _function_source("renderPartialCard"),
        _function_source("enqueuePartialPrompt"),
        _function_source("showNextPartialPrompt"),
        "renderPartialCard('j',activeJobs.j.partialState);enqueuePartialPrompt('j',activeJobs.j.partialState);console.log(JSON.stringify({card:els['err-j'].children.map(x=>x.textContent),modal:els.infoModalActions.children.map(x=>x.textContent),pct:els['pct-j'].textContent,width:els['fill-j'].style.width,stop:els['stop-j'].style.display}));",
    ])

    assert _run_node(js) == {
        "card": ["正文", "再次重试剩余字幕", "保留未翻译字幕并生成文件"],
        "modal": ["再次重试剩余字幕", "保留未翻译字幕并生成文件"],
        "pct": "—",
        "width": "0%",
        "stop": "none",
    }


def test_open_partial_options_fetches_missing_state_and_queues_without_overwriting_modal():
    js = "\n".join([
        "function element(){const s=new Set();return{classList:{add:c=>s.add(c),remove:c=>s.delete(c),contains:c=>s.has(c)}}}",
        "const overlay=element();overlay.classList.add('active');const $=id=>overlay;const activeJobs={};let cleared=[],reset=[],queued=[],toasts=[];let _partialPromptCurrent=null;const clearPartialIgnore=jid=>cleared.push(jid);const resetPartialPromptTicket=(jid,retry)=>reset.push([jid,retry]);const enqueuePartialPrompt=(jid,msg)=>queued.push([jid,msg]);const showToast=m=>toasts.push(m);",
        "const fetch=async()=>({ok:true,json:async()=>({state:{status:'partial',progress:{total:5,succeeded:3,failed:2},result:{output_ready:false,can_show_output_folder:false,quality:{}},actions:{}},details:{video_filename:'watch',config:{},retry_count:3,failed_count:2}})});",
        _function_source("taskStateView"),
        _function_source("openPartialOptions"),
        "openPartialOptions('j').then(()=>console.log(JSON.stringify({state:activeJobs.j.partialState,cleared,reset,queued,toasts,modalStillActive:overlay.classList.contains('active')})));",
    ])

    data = _run_node(js)
    assert data["state"]["failed_count"] == 2
    assert data["cleared"] == ["j"]
    assert data["reset"] == [["j", 3]]
    assert data["queued"][0][0] == "j"
    assert data["modalStillActive"] is True


def test_open_partial_options_stops_when_backend_is_no_longer_partial():
    js = "\n".join([
        "const overlay={classList:{contains:()=>false}};const $=id=>overlay;const activeJobs={};let queued=0,toasts=[];let _partialPromptCurrent=null;const clearPartialIgnore=()=>{};const resetPartialPromptTicket=()=>{};const enqueuePartialPrompt=()=>{queued+=1};const showToast=m=>toasts.push(m);",
        "const fetch=async()=>({ok:true,json:async()=>({state:{status:'done',progress:{},result:{quality:{}},actions:{}},details:{}})});",
        _function_source("taskStateView"),
        _function_source("openPartialOptions"),
        "openPartialOptions('j').then(()=>console.log(JSON.stringify({queued,toasts})));",
    ])

    assert _run_node(js) == {
        "queued": 0,
        "toasts": ["这个任务已经不需要处理"],
    }


def test_task_config_cannot_overwrite_frontend_keys():
    js = "\n".join([
        _function_source("mergeableJobSettings"),
        "const current={apiKeys:[{key:'current-fake'}],model:'large-v2'};",
        "const task={apiKeys:[{key:'old-fake'}],model:'large-v3',source_dir:'/tmp'};",
        "const merged={...current,...mergeableJobSettings(task)};",
        "console.log(JSON.stringify(merged));",
    ])

    data = _run_node(js)

    assert data["apiKeys"] == [{"key": "current-fake"}]
    assert data["model"] == "large-v3"
    assert "source_dir" not in data


def test_closing_partial_card_never_deletes_backend_job():
    js = "\n".join([
        "const activeJobs={j:{restoredStatus:'partial',partialState:{retry_count:1,output_ready:false}}};let requests=[];const fetch=(...args)=>requests.push(args);let ignored=[];const writePartialIgnore=(...args)=>ignored.push(args);const removeCard=()=>{};",
        # 外审第四轮④：关卡片后按剩余失败卡重算顶部提示，桩要跟上。
        "const $=id=>({classList:{remove(){}}});"
        "const document={querySelectorAll:()=>[]};",
        _function_source("refreshShortfallBanner"),
        _function_source("dismissRestoredCard"),
        "dismissRestoredCard('j');console.log(JSON.stringify({requests,ignored}));",
    ])

    data = _run_node(js)

    assert data["requests"] == []
    assert data["ignored"][0][0] == "j"
    assert data["ignored"][0][2] == "dismissed_pending"


def test_restored_partial_action_reconnects_only_when_no_receipt_channel_exists():
    js = "\n".join([
        """
const activeJobs={missing:{ws:null},live:{ws:{readyState:1}}};
let connects=[];const connectCard=jid=>{connects.push(jid);activeJobs[jid].ws={readyState:0}};
const State={settings:{apiKeys:[]},configReady:true};async function startGate(){return {}}let _configSaveInFlight=false;let _configSavePromise=Promise.resolve(true);let _partialPromptCurrent=null;
const clearPartialIgnore=()=>{};const resetPartialPromptTicket=()=>{};const renderRetryProgress=()=>{};
const $=id=>({classList:{remove(){}}});const showNextPartialPrompt=()=>{};const showToast=()=>{};
const fetch=async()=>({ok:true,json:async()=>({})});
""",
        _function_source("requestPartialAction"),
        """
async function run(){
  await requestPartialAction('missing','retry',null);
  await requestPartialAction('live','accept',null);
  console.log(JSON.stringify({connects,missingPending:activeJobs.missing.partialActionPending,livePending:activeJobs.live.partialActionPending}));
}
run();
""",
    ])

    data = _run_node(js)

    assert data == {
        "connects": ["missing"],
        "missingPending": True,
        "livePending": True,
    }


def test_output_mode_normalization_keeps_legacy_default_consistent():
    js = "\n".join([
        _function_source("normalizeJobOutputMode"),
        _function_source("jobOutputNeedsVideo"),
        "console.log(JSON.stringify({explicit:normalizeJobOutputMode({outputMode:'mux'}),legacySrt:normalizeJobOutputMode({burn:false}),legacyBurn:normalizeJobOutputMode({}),needsMux:jobOutputNeedsVideo({outputMode:'mux'}),needsSrt:jobOutputNeedsVideo({burn:false})}));",
    ])

    assert _run_node(js) == {
        "explicit": "mux",
        "legacySrt": "srt",
        "legacyBurn": "burn",
        "needsMux": True,
        "needsSrt": False,
    }


def test_error_stage_text_distinguishes_output_and_translation_failures():
    js = "\n".join([
        _function_source("errorStageText"),
        "console.log(JSON.stringify([errorStageText('burn','output_mux_failed'),errorStageText('translate','unknown')]));",
    ])

    assert _run_node(js) == ["生成带字幕的视频失败", "翻译字幕失败"]


def test_interrupted_card_has_direct_one_click_resume_button():
    js = "\n".join([
        "function element(){return{style:{},textContent:'',children:[],classList:{add(){},remove(){}},appendChild(child){this.children.push(child)}}}",
        "const els={'step-j':element(),'fill-j':element(),'pct-j':element(),'err-j':element()};const $=id=>els[id];",
        "const document={createElement:()=>element()};",
        _function_source("renderInterruptedCard"),
        "renderInterruptedCard('j');console.log(JSON.stringify({step:els['step-j'].textContent,pct:els['pct-j'].textContent,width:els['fill-j'].style.width,buttons:els['err-j'].children.map(x=>x.textContent)}));",
    ])

    assert _run_node(js) == {
        "step": "任务已中断",
        "pct": "—",
        "width": "0%",
        "buttons": ["可以直接使用原视频和已有缓存继续处理。", "一键继续"],
    }


def test_one_click_resume_calls_same_job_endpoint_and_reconnects():
    js = "\n".join([
        "const State={settings:{apiKeys:[]},configReady:true};async function startGate(){return {}}let _configSaveInFlight=false;let _configSavePromise=Promise.resolve(true);",
        "const activeJobs={j:{config:{old:true},ws:null}};const events=[];const fetch=async(url,options)=>{events.push([url,options.method]);return{ok:true,json:async()=>({status:'running',config:{fresh:true}})}};",
        "const showToast=m=>events.push(['toast',m]);const connectCard=jid=>events.push(['connect',jid]);const saveActiveJobs=()=>events.push(['save']);const setScene=s=>events.push(['scene',s]);const showVideoBox=()=>{};const $=id=>({textContent:'',style:{},classList:{add(){},remove(){}}});",
        _function_source("resumeInterruptedJob"),
        "(async()=>{const button={disabled:false};const ok=await resumeInterruptedJob('j',button);console.log(JSON.stringify({ok,disabled:button.disabled,config:activeJobs.j.config,events}))})()",
    ])

    assert _run_node(js) == {
        "ok": True,
        "disabled": True,
        "config": {"fresh": True},
        "events": [
            ["/api/jobs/j/resume", "POST"],
            ["save"],
            ["scene", "processing"],
            ["connect", "j"],
        ],
    }


def test_transcription_card_ignores_durations_during_speech_recognition():
    from backend import job_state
    state = job_state.build_public_task_state({
        "status": "running", "stage": "transcribe", "progress": 17,
        "transcription_phase": "recognizing_speech", "completed_segments": 20,
        "audio_total_duration": 180, "audio_processed_duration": 60,
    })
    assert state["display_message"] == "正在识别语音，请稍等"
    assert state["percent"] == 17
    # P453：识别阶段不再显示段数（内部计数三种含义，贴成进度会假倒退）
    assert "已识别" not in (state["activity"] or "")


def test_transcription_card_shows_sound_analysis_without_segments_or_elapsed_time():
    from backend import job_state
    state = job_state.build_public_task_state({
        "status": "running", "stage": "transcribe", "progress": 9,
        "transcription_phase": "analyzing_sound", "completed_segments": 7,
    })
    assert state["display_message"] == "正在分析声音类型，请稍等"
    # P453：同上，标签保留、数字去掉
    assert "已识别" not in (state["activity"] or "")


def test_retry_running_card_shows_real_round_counts_without_actions_or_percent():
    js = "\n".join([
        "function element(){return{style:{},textContent:'',innerHTML:'',children:[],classList:{add(){},remove(){}},appendChild(child){this.children.push(child)}}}",
        "const activeJobs={j:{partialState:{success_count:8,failed_count:5}}};const els={'step-j':element(),'pct-j':element(),'fill-j':element(),'err-j':element()};const $=id=>els[id];const document={createElement:()=>element()};",
        _function_source("renderRetryProgress"),
        "renderRetryProgress('j',{retry_initial_failed_count:5,retry_processed_count:3,retry_new_success_count:2,retry_status:'running'});console.log(JSON.stringify({step:els['step-j'].textContent,pct:els['pct-j'].textContent,width:els['fill-j'].style.width,texts:els['err-j'].children.map(x=>x.textContent)}))",
    ])

    assert _run_node(js) == {
        "step": "正在重试",
        "pct": "—",
        "width": "0%",
        "texts": ["本轮待处理 5 · 已处理 3 · 本轮新成功 2 · 仍失败 1 · 剩余 2"],
    }


def test_retry_running_card_reports_invalid_counts_instead_of_clamping_them():
    js = "\n".join([
        "function element(){return{style:{},textContent:'',innerHTML:'',children:[],classList:{add(){},remove(){}},appendChild(child){this.children.push(child)}}}",
        "const activeJobs={j:{}};const els={'step-j':element(),'pct-j':element(),'fill-j':element(),'err-j':element()};const $=id=>els[id];const document={createElement:()=>element()};const console={error(){},log:globalThis.console.log};",
        _function_source("renderRetryProgress"),
        "renderRetryProgress('j',{retry_initial_failed_count:5,retry_processed_count:7,retry_new_success_count:6,retry_status:'running'});console.log(JSON.stringify(els['err-j'].children.map(x=>x.textContent)))",
    ])

    assert _run_node(js) == ["任务统计状态异常，请查看日志"]


def test_retry_finished_card_shows_previous_current_latest_and_direct_actions():
    js = "\n".join([
        "function element(){return{style:{},textContent:'',innerHTML:'',children:[],classList:{add(){},remove(){}},appendChild(child){this.children.push(child)}}}",
        "const activeJobs={j:{partialState:{}}};const els={'step-j':element(),'pct-j':element(),'fill-j':element(),'err-j':element(),'card-j':element(),'result-j':element()};const $=id=>els[id];const document={createElement:()=>element()};const renderOutputFolder=()=>{};const requestPartialAction=()=>{};",
        _function_source("partialReason"),
        _function_source("partialCopy"),
        _function_source("renderPartialCard"),
        "renderPartialCard('j',{segment_count:10,success_count:8,failed_count:2,retry_initial_failed_count:5,retry_new_success_count:3,retry_status:'completed',primary_failure_kind:'timeout',output_ready:false});console.log(JSON.stringify({step:els['step-j'].textContent,texts:els['err-j'].children.map(x=>x.textContent)}))",
    ])

    assert _run_node(js) == {
        "step": "最新结果：成功 8 句，仍失败 2 句",
        "texts": [
            "上一轮：成功 5 句，失败 5 句。本轮新成功 3 句。最新：成功 8 句，仍失败 2 句。原因：请检查 Key、余额和网络后重试。",
            "再次重试剩余字幕",
            "保留未翻译字幕并生成文件",
        ],
    }


def test_failure_reason_copy_is_actionable():
    js = "\n".join([
        _function_source("partialReason"),
        "console.log(JSON.stringify([partialReason('connection'),partialReason('rate_limit'),partialReason('service_error'),partialReason('unknown')]))",
    ])

    assert _run_node(js) == [
        # D-6 Z3：这几条新任务走不到（软件已自动轮换用户的 Key），旧任务落到兜底句
        "请检查 Key、余额和网络后重试",
        "请检查 Key、余额和网络后重试",
        "请检查 Key、余额和网络后重试",
        "请检查 Key、余额和网络后重试",
    ]


def test_engine_status_shows_api_failure():
    from backend import job_state
    state = job_state.build_public_task_state({
        "status": "running", "stage": "translate",
        "engine_status": {"phase": "api_failed", "failure_kind": "auth"},
    })
    assert state["display_message"] == "用户配置的 API 失败"
    assert "failure_kind" not in json.dumps(state, ensure_ascii=False)


def test_job_log_uses_post_and_never_opens_the_long_log_modal():
    js = "\n".join([
        "let calls=[];let toasts=[];const showToast=m=>toasts.push(m);const fetch=async(url,options)=>{calls.push({url,options});return{ok:true,json:async()=>({ok:true})}};",
        _function_source("openJobLog"),
        "const button={disabled:false};openJobLog('j',button).then(()=>console.log(JSON.stringify({calls,toasts,disabled:button.disabled})))",
    ])
    result = _run_node(js)
    assert result["calls"] == [{
        "url": "/api/jobs/j/log/open",
        "options": {
            "method": "POST",
            "headers": {"X-Video-Subber-Action": "open-job-log"},
        },
    }]
    assert result["toasts"] == ["日志文件已打开"]
    assert result["disabled"] is False
    assert "infoModal" not in _function_source("openJobLog")


def test_job_log_open_failure_restores_button_and_only_shows_short_message():
    js = "\n".join([
        "let toasts=[];const showToast=m=>toasts.push(m);const fetch=async()=>{throw new Error('network detail')};",
        _function_source("openJobLog"),
        "const button={disabled:false};openJobLog('j',button).then(()=>console.log(JSON.stringify({toasts,disabled:button.disabled})))",
    ])

    assert _run_node(js) == {
        "toasts": ["无法打开任务日志，请稍后重试"],
        "disabled": False,
    }


def test_error_card_log_button_describes_the_real_action():
    assert "logButton.textContent='打开日志文件'" in HTML
    assert "logButton.onclick=()=>openJobLog(jid,logButton)" in HTML


def test_render_output_folder_follows_can_show_output_folder():
    """B2：应用内不再有下载入口，只按后端布尔决定「在文件夹中显示」。"""
    js = "\n".join([
        "function element(){const s=new Set();return{innerHTML:'',children:[],textContent:'',className:'',classList:{add:c=>s.add(c),remove:c=>s.delete(c),contains:c=>s.has(c)},appendChild(x){this.children.push(x);return x}}}",
        "const els={'result-j':element()};const $=id=>els[id];const document={createElement:()=>element()};const revealJobOutput=()=>{};",
        _function_source("renderOutputFolder"),
        "const snap=()=>({show:els['result-j'].classList.contains('show'),labels:els['result-j'].children.map(b=>b.textContent)});",
        "const out=[];renderOutputFolder('j',{can_show_output_folder:true});out.push(snap());renderOutputFolder('j',{can_show_output_folder:false});out.push(snap());renderOutputFolder('j',{result_srt:'s',result_video:'v'});out.push(snap());renderOutputFolder('j',{can_show_output_folder:true});out.push(snap());console.log(JSON.stringify(out));",
    ])

    assert _run_node(js) == [
        {"show": True, "labels": ["在文件夹中显示"]},
        {"show": False, "labels": []},
        # 只有结果路径、没有本轮交付记录时，不得出现入口。
        {"show": False, "labels": []},
        {"show": True, "labels": ["在文件夹中显示"]},
    ]


def test_structured_error_renders_red_terminal_without_reformatting_message():
    js = "\n".join([
        "function element(){const s=new Set();return{textContent:'',style:{},classList:{add:c=>s.add(c),remove:c=>s.delete(c),contains:c=>s.has(c),toggle(c,on){if(on)s.add(c);else s.delete(c)}}}}",
        "const milestones=[element(),element(),element(),element(),element()];const box=element();box.querySelectorAll=()=>milestones;",
        "const els={'fill-j':element(),'pct-j':element(),'step-j':element(),'card-j':element(),'err-j':element(),'ms-j':box};const $=id=>els[id];",
        "let formatted=0;const formatJobErrorText=m=>{formatted+=1;return m+'。请查看日志或重新提交任务。'};const renderErrorActions=()=>{};const renderOutputFolder=()=>{};",
        _function_source("errorStageText"),
        _function_source("failMilestones"),
        _function_source("renderCardError"),
        "renderCardError('j',{stage:'burn',failure_kind:'output_mux_failed',message:'字幕文件已生成，但视频封装失败。',result_srt:'x'});console.log(JSON.stringify({width:els['fill-j'].style.width,color:els['fill-j'].style.background,working:els['fill-j'].classList.contains('working'),pct:els['pct-j'].textContent,step:els['step-j'].textContent,terminal:els['card-j'].classList.contains('job-card-error-terminal'),message:els['err-j'].textContent,formatted,states:milestones.map(x=>({done:x.classList.contains('done'),failed:x.classList.contains('failed'),active:x.classList.contains('active')}))}));",
    ])

    data = _run_node(js)
    assert data["width"] == "100%"
    assert data["color"] == "var(--red)"
    assert data["working"] is False
    assert data["pct"] == "100%"
    assert data["step"] == "生成带字幕的视频失败"
    assert data["terminal"] is True
    assert data["message"] == "字幕文件已生成，但视频封装失败。"
    assert data["formatted"] == 0
    assert data["states"][-1] == {"done": False, "failed": True, "active": False}
    assert all(item["done"] for item in data["states"][:-1])


def test_legacy_error_still_uses_existing_formatter():
    js = "\n".join([
        "function element(){const s=new Set();return{textContent:'',style:{},classList:{add:c=>s.add(c),remove:c=>s.delete(c),contains:c=>s.has(c)}}}",
        "const els={'fill-j':element(),'pct-j':element(),'step-j':element(),'card-j':element(),'err-j':element()};const $=id=>els[id];",
        "let formatted=0;const formatJobErrorText=m=>{formatted+=1;return m+'。请查看日志或重新提交任务。'};const renderErrorActions=()=>{};const renderOutputFolder=()=>{};const failMilestones=()=>{};",
        _function_source("errorStageText"),
        _function_source("renderCardError"),
        "renderCardError('j',{stage:'translate',message:'旧错误'});console.log(JSON.stringify({message:els['err-j'].textContent,formatted}));",
    ])

    assert _run_node(js) == {
        "message": "旧错误。请查看日志或重新提交任务。",
        "formatted": 1,
    }
