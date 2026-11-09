"""D-3 · 界面讲真话（P498、P499 旧横幅、P530、P549，合同增量 A6）。

**用户看到的**：10-27 新西兰片（任务 4979552328，562 段）做完后，顶部黄条
「部分字幕可能不准确（6%）」，任务卡「35 句存疑 · 1 条字幕显示偏短 ·
2 条长字幕已按 7 秒截断 · 3 条长字幕未能拆分」。这些是程序内部的怀疑和
我们自己的排版毛病，不该写给用户。

反过来，「翻译有失败、还没出成品」那张卡上，用户要决定导出还是重试，
却看不到「第 X 分钟识别失败」——同一句话，已出成品的出口带着，未出成品的出口漏了。

改法：删 A1 存疑句数、A2 红黄横幅、A3 三行排版提示、A6 设置面板那条说明；
A4 让未出成品的部分完成出口带上同一个 `_delivered_shortfall`。

Run: cd v3 && .venv/bin/python -m pytest test/test_d3_ui_truth.py -v
"""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import job_state
from backend import main as backend_main

_HTML = (Path(__file__).resolve().parent.parent / "frontend" / "index.html").read_text(
    encoding="utf-8")

# 10-27 新西兰片 meta.json 里界面拿去显示的那几样（原样抄出）。
_NZ = {
    "question_mark_count": 35,
    "segment_count": 562, "success_count": 562,
    "subtitle_quality": {
        "below_min_display": {"count": 1, "at": [516.923], "shortest_s": 0.64},
        "capped_no_legal_cut": {"count": 2, "at": [682.62, 2000.778]},
        "mapping_failed_splits": 3},
    "translation_summary": {
        "requested_tier": "api_segment", "api_success_count": 562,
        "api_total_count": 562, "kept_original_count": 0},
    "sound_reference": {"status": "used", "message": "", "reason_code": None},
    "transcription_shortfall": None,
}

_GONE = ["句存疑", "条字幕显示偏短", "已按 7 秒截断", "条长字幕未能拆分",
         "音频质量较差", "部分字幕可能不准确", "字幕质量情况"]


def _js_function(name: str) -> str:
    m = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\([^)]*\)\{{.*?\n\}}",
                  _HTML, re.S)
    assert m, f"找不到 JS 函数 {name}"
    return m.group(0)


def _node(source: str) -> dict:
    r = subprocess.run(["node", "-e", source], text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


_FAKE_DOM = """
function el(){const e={textContent:'',innerHTML:'',style:{},_cls:new Set(),children:[],
  classList:{add(...x){x.forEach(c=>e._cls.add(c))},remove(...x){x.forEach(c=>e._cls.delete(c))}},
  appendChild(c){e.children.push(c)}};return e}
const nodes={};
globalThis.$=(id)=>(nodes[id]=nodes[id]||el());
globalThis.document={createElement:()=>el()};
"""


def _summary_text(msg: dict) -> str:
    return _node(f"""{_FAKE_DOM}
{_js_function('aiUnusedText')}
{_js_function('renderTranslationSummary')}
renderTranslationSummary('J', {json.dumps(msg, ensure_ascii=False)});
console.log(JSON.stringify({{text:$('quality-J').textContent}}));
""")["text"]


# ── A1 / A2 / A3 / A6：这些话在界面里写不出来 ──

def test_page_source_has_none_of_the_removed_words():
    for word in _GONE:
        assert word not in _HTML, f"界面里仍有「{word}」"
    for name in ("showQmWarning", 'id="qmWarn"', "subtitleMarkerNote",
                 "question_mark_count", "subtitle_quality"):
        assert name not in _HTML, f"前端仍引用 {name}"


def test_shortfall_bar_and_its_styles_stay():
    """`.qm-warn` 样式是识别欠账提示条 #shortfallWarn 在用的，删横幅不能连它一起删。"""
    assert 'id="shortfallWarn"' in _HTML
    for rule in (".qm-warn{", ".qm-warn.active{", ".qm-warn.yellow{", ".qm-warn.red{"):
        assert rule in _HTML, f"样式 {rule} 被误删"
    assert "检测到可能的说话人切换时字幕加" in _HTML, "说话人检测开关下的说明要保留"


def test_new_zealand_card_shows_none_of_it():
    """真实任务 4979552328 的字段喂进去：卡上只剩翻译档位和 API 成功数。"""
    text = _summary_text(_NZ)
    for word in _GONE:
        assert word not in text, f"任务卡仍显示「{word}」：{text!r}"
    assert text == "API 逐段翻译 · 本轮 API 成功 562/562"


def test_nothing_to_say_shows_no_empty_box():
    """收尾审查新发现：没有一行可写时（无识别欠账、声音参考正常、翻译走缓存没有翻译记录），
    质量框不能还挂着 `show`——用户会看到一个空的橙色框。以前「N 句存疑」常把它填上，删掉后露出来。
    先放一句旧内容再渲染空消息，证明「显示过」的框也会收起。"""
    out = _node(f"""{_FAKE_DOM}
{_js_function('aiUnusedText')}
{_js_function('renderTranslationSummary')}
renderTranslationSummary('J', {{sound_reference:{{status:'unavailable',message:'声音参考未使用，字幕仍已生成。'}}}});
const before=[...$('quality-J')._cls];
renderTranslationSummary('J', {{sound_reference:{{status:'used',message:''}}}});
console.log(JSON.stringify({{before, text:$('quality-J').textContent, cls:[...$('quality-J')._cls]}}));
""")
    assert "show" in out["before"], "前提：有内容时框要显示"
    assert out["text"] == ""
    assert "show" not in out["cls"], "没内容时不能显示一个空框"


# ── A5：原有的行一行不少 ──

def test_every_other_line_survives():
    msg = dict(_NZ, success_count=560,
               transcription_shortfall={"severity": "info",
                                        "message": "第 1 分钟识别失败，那里的字幕已留空。"},
               translation_summary={
                   "requested_tier": "api_segment", "api_success_count": 550,
                   "api_total_count": 562, "kept_original_count": 2,
                   "ai_unused": [{"step": "breaks", "reason": "format"}]},
               sound_reference={"status": "unavailable",
                                "message": "声音参考未使用，字幕仍已生成。"})
    text = _summary_text(msg)
    parts = text.split(" · ")
    assert parts[0] == "第 1 分钟识别失败，那里的字幕已留空。", "识别说明必须排第一"
    for line in ("API 逐段翻译", "本轮 API 成功 550/562",
                 "有 2 句未能翻译，保留了原文", "声音参考未使用，字幕仍已生成。"):
        assert line in parts, f"少了「{line}」：{text!r}"
    assert any(p.startswith("本次未使用") for p in parts), f"少了「本次未使用…」：{text!r}"


# ── A4：翻译有失败、还没出成品的部分完成卡，也带识别说明 ──

def test_unexported_partial_carries_the_shortfall_to_the_card(monkeypatch, tmp_path):
    """走 worker 真实的 `main()`：翻译有失败 → 未出成品的 partial（worker.py 未导出分支）。
    再经服务端终态入库规则和公开状态，最后由前端 taskStateView → renderPartialCard 渲染。"""
    from test.test_d2_p148_stuck_words import _main_with_stuck_note
    messages, note = _main_with_stuck_note(monkeypatch, tmp_path)
    partial = next(m for m in messages if m["type"] == "partial")
    assert partial["output_ready"] is False, "测试必须走到未出成品的那条分支"
    assert partial.get("transcription_shortfall", {}).get("message") == note

    # 刷新后：服务端把终态消息的字段写进任务记录，公开状态从记录重建。
    fields = backend_main._terminal_fields_from_message(partial)
    state = job_state.build_public_task_state(dict(fields, status="partial"))
    assert state["result"]["quality"]["transcription_shortfall"]["message"] == note

    first = _node(f"""{_FAKE_DOM}
globalThis.partialCopy=()=>({{title:'还差几句没翻完',body:''}});
globalThis.renderOutputFolder=()=>{{}};
{_js_function('taskStateView')}
{_js_function('aiUnusedText')}
{_js_function('renderTranslationSummary')}
{_js_function('renderPartialCard')}
renderPartialCard('J', taskStateView({json.dumps(state, ensure_ascii=False)}));
console.log(JSON.stringify({{text:$('quality-J').textContent}}));
""")["text"].split(" · ")[0]
    assert first == note, f"部分完成卡第一行应是识别说明，实际：{first!r}"
