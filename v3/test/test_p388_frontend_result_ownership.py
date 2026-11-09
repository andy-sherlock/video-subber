"""P387：没有可信成品就不显示成品入口。

done / partial / error 三种终态，经由重连、历史命中、轮询和实时消息四个入口
进来时，共用同一条显示规则。B2 起应用内不再提供下载——成品已交付到用户目录，
入口改为「在文件夹中显示」，显隐只看后端的 can_show_output_folder（它只在本轮
delivery.delivered 存在时为真）。没有可信成品时隐藏入口并在应用内给中文说明。

前端测试用 Node 真实执行提取出来的函数，不做源码字符串假绿。
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
    return json.loads(result.stdout)


_ELEMENT = (
    "function element(){const s=new Set();return{href:'stale',textContent:'',"
    "style:{display:''},classList:{add:c=>s.add(c),remove:c=>s.delete(c),"
    "contains:c=>s.has(c),toggle(c,on){if(on)s.add(c);else s.delete(c)}},"
    "removeAttribute(name){if(name==='href')delete this.href},"
    "innerHTML:'',children:[],"
    "appendChild(x){this.children.push(x);return x},querySelectorAll:()=>[]}}"
)


def _done_harness(msg_js):
    """真实跑一次 renderCardDone，返回下载入口与说明文字的快照。"""
    return "\n".join([
        _ELEMENT,
        "const els={};",
        "const ids=['fill-j','step-j','pct-j','card-j','result-j','dlV-j','dlS-j',"
        "'err-j','ms-j','quality-j'];",
        "ids.forEach(id=>els[id]=element());",
        "const $=id=>els[id];",
        "const document={getElementById:id=>els[id]||null,"
        "createElement:()=>element()};",
        "function hideMilestones(){}",
        "const revealJobOutput=()=>{};",
        _function_source("renderCardDone"),
        f"renderCardDone('j',{msg_js});",
        "console.log(JSON.stringify({"
        "labels:els['result-j'].children.map(b=>b.textContent),"
        "show:els['result-j'].classList.contains('show'),"
        "step:els['step-j'].textContent}));",
    ])


def test_done_card_hides_output_entry_when_nothing_was_delivered():
    """P387 的原始故障：done 卡片无条件挂成品入口，点了会跳 JSON 错误页。"""
    snapshot = _run_node(_done_harness(
        "{output_ready:false,can_show_output_folder:false}"
    ))

    assert snapshot["labels"] == [], "没有本轮交付记录时不得画成品入口"
    assert snapshot["show"] is False


def test_done_card_explains_in_chinese_when_no_trusted_subtitle():
    no_result = _run_node(_done_harness(
        "{output_ready:false,can_show_output_folder:false}"
    ))
    with_result = _run_node(_done_harness(
        "{output_ready:true,can_show_output_folder:true}"
    ))

    assert no_result["step"], "必须给出应用内说明"
    assert re.search(r"[一-鿿]", no_result["step"]), "说明必须是中文"
    assert "可以下载" not in no_result["step"], (
        f"没有可信成品时不得声称可以下载，实际: {no_result['step']}"
    )
    assert no_result["step"] != with_result["step"], (
        "有成品和没成品必须给出不同的说明，不能一律说已完成可下载"
    )


def test_done_card_entry_follows_delivery_not_result_paths():
    """有结果路径不等于已交付：入口只认 can_show_output_folder。"""
    not_delivered = _run_node(_done_harness(
        "{output_ready:true,can_show_output_folder:false}"
    ))
    assert not_delivered["labels"] == [], "还没交付就不得给入口"

    delivered = _run_node(_done_harness(
        "{output_ready:true,can_show_output_folder:true}"
    ))
    assert delivered["labels"] == ["在文件夹中显示"]
    assert delivered["show"] is True


# ── 四个入口共用同一条规则 ──

def _entry_call(function_name, snippet):
    """抓出某个入口调用 renderCardDone 时实际传了什么。"""
    source = _function_source(function_name)
    match = re.search(r"renderCardDone\([^;]*?\)", source, re.S)
    assert match, f"{function_name} 里找不到 renderCardDone 调用"
    return match.group(0)


def test_reconnect_entry_passes_the_backend_video_not_a_config_guess():
    """重连入口不得用 config 推测视频是否存在。"""
    call = _entry_call("reconnectJobs", None)

    assert "jobOutputNeedsVideo" not in call, (
        f"重连必须使用后端返回的可信 result_video，实际: {call}"
    )
    # **保护权交接（2026-08-19 · G9/P422）**：入口不再逐个点名字段，改为把后端返回的整份公开快照原样交出去。
    # 「用后端的可信值、不用 config 猜」这条规则**因此更牢**——它现在覆盖
    # 所有字段，而不只是当初点名的那两个。
    assert call == "renderCardDone(jid,view)", (
        f"重连没有整份透传后端快照，实际: {call}"
    )


def test_history_hit_entry_passes_real_result_fields():
    """历史命中入口当前根本没传 result_srt，按钮因此永远显示。"""
    call = _entry_call("handleFoundJob", None)

    assert "jobOutputNeedsVideo" not in call, (
        f"历史命中必须使用后端返回的可信字段，实际: {call}"
    )
    # **保护权交接（2026-08-19 · G9/P422）**：同上，整份透传。
    assert call == "renderCardDone(d.job_id,d)", (
        f"历史命中没有整份透传后端快照，实际: {call}"
    )


def test_partial_and_error_cards_share_the_same_output_entry_rule():
    """P2-c：partial / error 复用同一条显隐合同，不得被绕过。"""
    js = "\n".join([
        _ELEMENT,
        "const els={'result-j':element()};const $=id=>els[id];",
        "const document={createElement:()=>element()};const revealJobOutput=()=>{};",
        _function_source("renderOutputFolder"),
        "renderOutputFolder('j',{result_video:null,result_srt:null});",
        "console.log(JSON.stringify({"
        "labels:els['result-j'].children.map(b=>b.textContent),"
        "show:els['result-j'].classList.contains('show')}));",
    ])

    snapshot = _run_node(js)

    assert snapshot["labels"] == []
    assert snapshot["show"] is False
