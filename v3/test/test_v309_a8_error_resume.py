"""A8：error 卡按 partial_resume_action 给出中文继续按钮，并复用 /resume。

前端断言一律真实执行从 index.html 抽出来的函数，不做源码字符串匹配——
上一轮就吃过"只搜到调用文本、没真跑过渲染路径"的假绿亏。
"""

import json
import re
import subprocess
from pathlib import Path

from backend import main


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


_DOM_STUB = """
function element(){const s=new Set();return{
  textContent:'',innerHTML:'',disabled:false,className:'',style:{},children:[],
  classList:{add:c=>s.add(c),remove:c=>s.delete(c),contains:c=>s.has(c),toggle(){}},
  appendChild(x){this.children.push(x);return x},
  querySelectorAll:()=>[],removeAttribute(){},
}}
const els={};
const $=id=>(els[id]=els[id]||element());
const document={createElement:()=>element()};
let fetched=[];
const fetch=(url,opts)=>{fetched.push({url,method:(opts||{}).method});
  return Promise.resolve({ok:true,json:async()=>({status:'running',config:{}})})};
const showToast=()=>{};
const activeJobs={};
const setScene=()=>{};const showVideoBox=()=>{};const saveActiveJobs=()=>{};
const connectCard=()=>{};
const failMilestones=()=>{};
const renderOutputFolder=()=>{};
const renderTranslationSummary=()=>{};
const openJobLog=()=>{};
function allButtons(el){const out=[];for(const c of el.children){out.push(c);
  out.push(...allButtons(c))}return out}
"""


def _render_error_card_js(msg, tail):
    return "\n".join([
        _DOM_STUB,
        _function_source("errorStageText"),
        _function_source("formatJobErrorText"),
        _function_source("hasUserAdvice"),
        _function_source("renderErrorActions"),
        _function_source("resumeInterruptedJob"),
        _function_source("renderCardError"),
        f"renderCardError('J', {json.dumps(msg, ensure_ascii=False)});",
        tail,
    ])


def test_error_card_shows_a_chinese_continue_button_for_a_pending_retry():
    js = _render_error_card_js(
        {"message": "重试失败部分没能启动，请稍后继续。", "stage": "translate",
         "resume_action": "retry"},
        """
const buttons=allButtons(els['err-J']).map(b=>b.textContent);
console.log(JSON.stringify({buttons}));
""",
    )

    data = _run_node(js)

    assert any("继续" in text for text in data["buttons"]), (
        f"error 卡必须给出中文继续按钮，实际按钮：{data['buttons']}"
    )


def test_error_card_continue_button_posts_to_resume():
    js = _render_error_card_js(
        {"message": "生成字幕文件没能启动，请稍后继续。", "stage": "translate",
         "resume_action": "accept"},
        """
async function run(){
  const button=allButtons(els['err-J']).find(b=>b.textContent.includes('继续'));
  if(!button){console.log(JSON.stringify({fetched:[],found:false}));return}
  await button.onclick();
  console.log(JSON.stringify({fetched,found:true}));
}
run();
""",
    )

    data = _run_node(js)

    assert data["found"] is True
    assert data["fetched"] == [
        {"url": "/api/jobs/J/resume", "method": "POST"}
    ], "继续按钮必须复用 /resume，不得另开接口"


def test_error_card_without_a_pending_action_has_no_continue_button():
    js = _render_error_card_js(
        {"message": "处理过程中断。", "stage": "translate"},
        """
const buttons=allButtons(els['err-J']).map(b=>b.textContent);
console.log(JSON.stringify({buttons}));
""",
    )

    data = _run_node(js)

    assert not any("继续" in text for text in data["buttons"]), (
        "没有待续动作时不得给继续按钮"
    )


# ── 后端要把待续动作投影给前端 ───────────────────────────────────────────

def test_public_snapshot_exposes_the_pending_resume_action(tmp_path, monkeypatch):
    """partial_resume_action 本身被脱敏摘掉了，需要一个受控的公开投影。"""
    job_dir = tmp_path / "a8job"
    job_dir.mkdir()
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)

    payload = main.job_state.build_public_task_state({
        "id": "a8job", "status": "error", "run_id": "run-1",
        "partial_resume_action": "retry",
    })

    assert payload["actions"]["resume_action"] == "retry"
    assert "partial_resume_action" not in json.dumps(payload)


def test_public_snapshot_reports_no_resume_action_when_there_is_none(
    tmp_path, monkeypatch
):
    job_dir = tmp_path / "a8job2"
    job_dir.mkdir()
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)

    payload = main.job_state.build_public_task_state({
        "id": "a8job2", "status": "error", "run_id": "run-1",
    })

    assert payload["actions"]["resume_action"] is None
