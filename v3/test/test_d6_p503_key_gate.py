"""D-6 P503 第 2 步：没 Key 的人门口只给「去拿 Key」；后台「选了翻译、真要翻、且没 Key」明确报错。

合同 K3、K4、K5（pipelines/bug-pipeline-2026-11-04-D6-P503.md ⑤.5）。
"""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.translation import orchestrator
from test.test_d1b_frontend import HTML, _function_source, _run_node
from test.test_d1c_batch2_frontend import _gate_js


# ── K3：后台拦截 ────────────────────────────────────────────────────

def test_translation_without_any_key_fails_loudly():
    from backend import worker

    with pytest.raises(RuntimeError) as excinfo:
        orchestrator.translate_with_orchestrator(texts=["hello"], api_keys=[])
    message = str(excinfo.value)
    assert "API Key" in message
    assert worker._terminal_error_message(excinfo.value) == message, \
        "任务卡上要原样显示这句人话，不能被改写成「处理过程中断」"


def test_export_of_partial_result_is_not_blocked_by_missing_key(monkeypatch, tmp_path):
    """「保留未翻译字幕并生成文件」本轮故意不带 Key（D-1 补修 D2）——绝不能被无 Key 拦截误伤。"""
    from backend import worker

    def forbidden(**kwargs):
        raise AssertionError("导出不应调用翻译")

    monkeypatch.setattr(orchestrator, "translate_with_orchestrator", forbidden)
    segments = [
        SimpleNamespace(id=i, start=i, end=i + 0.5, text=f"line {i}", zh="", words=[])
        for i in range(4)
    ]
    path = tmp_path / "translated.json"
    path.write_text(json.dumps([
        {"id": s.id, "start": s.start, "end": s.end, "text": s.text,
         "zh": "译文" if s.id < 2 else "[翻译失败]"}
        for s in segments
    ], ensure_ascii=False), encoding="utf-8")
    config = {"_accept_partial": True}

    worker._step_translate(
        "translate", segments, path,
        worker._api_keys_for_run(config), False,
        config, tmp_path, len(segments), SimpleNamespace(save=lambda *a, **k: None),
    )


# ── K4：门口没 Key 不再放行，只给「去拿 Key」 ─────────────────────────

def _no_key_gate(tail):
    js = _gate_js(tail, "{ok:false,translate_only:false,kind:'no_key'}")
    return js.replace("const closeInfoModal=", "let guided=0;const openKeyGuide=()=>{guided++};const closeInfoModal=")


def test_gate_without_key_does_not_start_the_task():
    data = _run_node(_no_key_gate(
        "startGate(sbtn).then(r=>console.log(JSON.stringify({r,labels:panels[0].map(a=>a.text)})));"))
    assert data["r"] is None, "没 Key 不得开始任务"
    assert data["labels"] == ["去拿 Key"], "门口只给一条路"


def test_get_key_button_opens_the_guide():
    data = _run_node(_no_key_gate(
        "startGate(sbtn).then(r=>{panels[0][0].onclick();console.log(JSON.stringify({drawer,guided}))});"))
    assert data == {"drawer": ["api"], "guided": 1}


# ── K5：「去拿 Key」引导内容 ─────────────────────────────────────────

def test_key_guide_has_three_steps_deepseek_url_and_cost_line():
    start = HTML.index('id="keyGuide"')
    guide = HTML[start:HTML.index("</div><!-- /keyGuide -->", start)]
    assert "platform.deepseek.com/api_keys" in guide
    assert "软件免费，翻译用你自己的 Key，一部 30 分钟片约一毛钱" in guide
    for step in ("1", "2", "3"):
        assert f'<li data-step="{step}"' in guide


def test_open_key_guide_shows_it_and_preselects_deepseek_when_no_key():
    js = "\n".join([
        """
        function element(){const s=new Set();return{value:'',hidden:true,style:{},
          classList:{add:c=>s.add(c),remove:c=>s.delete(c),contains:c=>s.has(c)},
          dispatchEvent(){this.fired=(this.fired||0)+1},scrollIntoView(){}}}
        const els={};const $=id=>(els[id]=els[id]||element());
        class Event{constructor(t){this.type=t}}
        $('apiPlatform').value='';$('apiKeyInput').value='';
        """,
        _function_source("openKeyGuide"),
        "openKeyGuide();console.log(JSON.stringify({hidden:$('keyGuide').hidden,"
        "platform:$('apiPlatform').value,fired:$('apiPlatform').fired||0}));",
    ])
    data = _run_node(js)
    assert data["hidden"] is False
    assert data["platform"] == "deepseek"
    assert data["fired"] == 1, "换平台后要触发 change，让模型列表与自动检测跟上"
