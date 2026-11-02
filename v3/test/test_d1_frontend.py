"""D-1 前端：在 node 里真跑 index.html 的函数（C1 / C2 / C6 / C9 / C12 / A5 / A8）。"""

import json
import re
import subprocess
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "frontend" / "index.html").read_text(encoding="utf-8")


def _fn(name):
    m = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\([^)]*\)\{{.*?\n\}}", HTML, re.S)
    assert m, f"找不到 JS 函数 {name}"
    return m.group(0)


def _run(source):
    r = subprocess.run(["node", "-e", source], text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


# ── C12 / A5：任务卡说明 ─────────────────────────────

def _ai_unused(notes):
    return _run(f"{_fn('aiUnusedText')}\nconsole.log(JSON.stringify(aiUnusedText({json.dumps(notes)})))")


def test_ai_unused_texts():
    assert _ai_unused(None) == ""
    assert _ai_unused([]) == ""
    assert _ai_unused([{"step": "breaks_and_refine", "reason": "platform_unsupported"}]) == "本次未使用 AI 断句和润色"
    assert _ai_unused([{"step": "refine", "reason": "format"}]) == "本次未使用 AI 润色（AI 回复格式有误）"
    assert _ai_unused([{"step": "breaks", "reason": "validation"}]) == "本次未使用 AI 断句（识别结果校验未通过）"
    assert _ai_unused([
        {"step": "breaks", "reason": "format"}, {"step": "refine", "reason": "format"},
    ]) == "本次未使用 AI 断句（AI 回复格式有误）、AI 润色（AI 回复格式有误）"


def _summary_lines(summary):
    source = f"""
const target={{textContent:'',classList:{{add(){{}}}}}};
globalThis.$=(id)=>id==='quality-j'?target:null;
{_fn('aiUnusedText')}
{_fn('renderTranslationSummary')}
renderTranslationSummary('j',{{translation_summary:{json.dumps(summary)}}});
console.log(JSON.stringify(target.textContent.split(' · ')));
"""
    return _run(source)


def test_card_shows_kept_original_and_ai_unused_lines():
    lines = _summary_lines({
        "requested_tier": "api_segment", "api_success_count": 8, "api_total_count": 10,
        "kept_original_count": 2, "auto_fallback": False,
        "ai_unused": [{"step": "refine", "reason": "format"}],
    })
    assert "有 2 句未能翻译，保留了原文" in lines
    assert "本次未使用 AI 润色（AI 回复格式有误）" in lines
    assert not any("免费" in line for line in lines)


def test_card_success_has_no_extra_lines():
    lines = _summary_lines({
        "requested_tier": "api_segment", "api_success_count": 10, "api_total_count": 10,
        "kept_original_count": 0, "auto_fallback": False,
    })
    assert not any("保留了原文" in line or "未使用 AI" in line for line in lines)


# ── C1：门口测试结果 → 原因文字 ─────────────────────────────

def _reason(kind):
    return _run(f"{_fn('gateReasonText')}\nconsole.log(JSON.stringify(gateReasonText({json.dumps(kind)})))")


def test_gate_reason_texts():
    assert _reason("connection") == "连不上，可能没开代理"
    assert _reason("auth") == "Key 不对或已过期"
    assert _reason("insufficient_balance") == "余额不足，请先充值"
    assert _reason("no_key") == "请先填写 API Key"
    assert _reason("anything-else") == "检测失败，请稍后重新检测"


# ── C6 / A8：拉模型失败的提示 ─────────────────────────────

def _model_hint(platform, error):
    return _run(f"{_fn('modelFetchHint')}\nconsole.log(JSON.stringify(modelFetchHint({json.dumps(platform)},{json.dumps(error)})))")


def test_model_fetch_hints():
    assert _model_hint("deepseek", "fetch_failed: HTTP Error 401: Unauthorized") == "Key 不对或已过期"
    assert _model_hint("gpt", "fetch_failed: HTTP Error 403: Forbidden") == "Key 不对或已过期"
    assert _model_hint("deepseek", "fetch_failed: <urlopen error timed out>") == "没查到可用模型：请检查 Key，或手动填写模型名"
    assert _model_hint("custom", "") == "没查到，请手动输入模型名"


def test_stale_model_reply_does_not_leave_loading_hint():
    """A8：添加 Key 后输入框被清空，在途回复被当过期丢弃时，提示不能卡在「正在识别」。"""
    m = re.search(r"async function fetchAndFillModels\(\)\{.*?\n\}", HTML, re.S)
    body = m.group(0)
    assert "_modelReqSeq" in body
    assert "if(_isStale()){_clearLoadingHint(reqId);return}" in body


# ── C9 / C2：静态文字与动画写法 ─────────────────────────────

def test_balance_reminder_text_is_present():
    assert "使用前请确认账户余额充足" in HTML


def test_spinner_is_css_animated_not_scripted():
    assert "@keyframes d1spin" in HTML
    assert "prefers-reduced-motion" in HTML
    gate_js = _fn("gateMark")
    assert "requestAnimationFrame" not in gate_js and "setInterval" not in gate_js


def test_start_gate_runs_before_lock_on_all_three_submit_paths():
    # D-1 补修：点击处理抽成具名函数 startJob；检测的是点下那一刻固定的 runSettings
    m = re.search(r"async function startJob\(\)\{.*?\n\}\nsbtn\.addEventListener\('click',startJob\);", HTML, re.S)
    handler = m.group(0)
    gate_at = handler.index("await startGate(sbtn)")  # P512：不再传 Key
    for marker in ("// ── 本地路径模式", "// ── URL 直传模式", "// ── 文件上传模式"):
        assert gate_at < handler.index(marker)
    assert handler.count("...gateCfg") == 3
