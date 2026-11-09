"""G8（P379 + P378 运行中缺口）：翻译阶段的实时进度。

D-6 P503 删免费翻译后，免费补全逐句进度（A1-A7、A11、A13、A16、缺陷 2）随功能删除；
这里留下与免费无关的部分：逐段付费路线原地等待（A12）、progress_callback 无条件传（A15）、
前端有 message 时只显示它（A14）、人工重试每条消息带本轮总量（缺陷 1）。

合同见 pipelines/bug-pipeline-2026-08-18-G8.md ⑤.5。
"""
import json
import re
import subprocess
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.translation.base import FAILED_TRANSLATION, TranslationResult
from backend.translation import orchestrator


# ── 测试替身 ──────────────────────────────────────────────────────────

# ══ A12：逐段模式必须跨批累计，序号不许倒退（⑤ Q6 发现 2）═════════════

class _AlwaysFailApi:
    name = "用户API"

    def translate_batch(self, texts, **kw):
        return TranslationResult([FAILED_TRANSLATION] * len(texts), self.name,
                                 failure_kind="service_error")

    def translate_raw(self, prompt, max_tokens=512):
        return TranslationResult([FAILED_TRANSLATION], self.name,
                                 failure_kind="service_error")


def test_a12_segmented_paid_mode_waits_instead_of_free_fill(monkeypatch):
    """D-1 A5（合同变更）：逐段付费模式不再有「免费兜底」循环。

    8 月这里两条测试（A12：逐段模式免费兜底逐句回报、序号跨批累计）测的是
    付费失败后转免费补全的进度；该路径已整段删除。现在付费服务出错 →
    进度文字写原因、原地等，恢复后接着用付费翻译，免费引擎 0 次。
    """
    from backend.translation import paid_request

    texts = [f"segmented sentence number {i} with enough length to split" for i in range(3)]

    class _FailOnceApi(_AlwaysFailApi):
        calls = 0

        def translate_batch(self, texts, **kw):
            type(self).calls += 1
            if type(self).calls == 1:
                return super().translate_batch(texts, **kw)
            return TranslationResult([f"付费{i}" for i in range(len(texts))], self.name)

    waits = []
    monkeypatch.setattr(paid_request, "_wait", waits.append)
    monkeypatch.setattr(orchestrator, "_build_llm_backends", lambda keys: [_FailOnceApi()])
    msgs = []
    translated, failed, *_ = orchestrator.translate_with_orchestrator(
        texts, api_keys=[{"key": "k"}], context_aware=False,
        progress_callback=lambda pct, msg: msgs.append(msg),
    )
    assert translated == ["付费0", "付费1", "付费2"] and failed == []
    assert waits == [5]
    assert "用户API 服务器出错，恢复后自动继续" in msgs


# ══ A15：progress_callback 无条件传（病 B 的结构性修法）═══════════════

def test_a15_worker_passes_progress_callback_unconditionally(tmp_path):
    """A15（病 B 本体）：人工重试时，worker 必须也把 progress_callback 传下去。

    真跑 worker._step_translate 的 _partial_retry 分支，抓送进
    translate_with_orchestrator 的 kwargs——不看源码字符串。
    """
    from unittest.mock import patch
    from backend import worker

    translated_path = tmp_path / "translated.json"
    translated_path.write_text(json.dumps([
        {"id": 0, "start": 0.0, "end": 1.0, "text": "one", "zh": "[翻译失败]"},
        {"id": 1, "start": 1.0, "end": 2.0, "text": "two", "zh": "已有好译文"},
    ], ensure_ascii=False), encoding="utf-8")

    segments = [worker.Segment(0, 0.0, 1.0, "one"), worker.Segment(1, 1.0, 2.0, "two")]

    captured = {}

    def _spy(**kwargs):
        captured.update(kwargs)
        n = len(kwargs["texts"])
        return ["补回的译文"] * n, [], None, {}, {}

    class _Ck:
        def save(self, *a, **k):
            pass

    with patch("backend.translation.orchestrator.translate_with_orchestrator", _spy):
        worker._step_translate(
            "subtitle", segments, translated_path, [],
            False, {"_partial_retry": True, "_retry_initial_failed_count": 1},
            tmp_path, len(segments), _Ck(),
        )

    assert captured, "translate_with_orchestrator 没被调用，测试无效"
    assert "progress_callback" in captured, (
        "人工重试路径没有拿到 progress_callback —— 这就是病 B："
        "worker.py 的 if/else 分叉里，if 分支少传了这个键，"
        "导致免费兜底期间页面一次都不更新。"
    )
    assert callable(captured["progress_callback"])


def test_a15_normal_run_still_gets_progress_callback(tmp_path):
    """A15 对照：普通新翻译路径本来就有 progress_callback，不得被改坏。"""
    from unittest.mock import patch
    from backend import worker

    translated_path = tmp_path / "translated.json"
    segments = [worker.Segment(0, 0.0, 1.0, "one"), worker.Segment(1, 1.0, 2.0, "two")]
    captured = {}

    def _spy(**kwargs):
        captured.update(kwargs)
        return ["译文"] * len(kwargs["texts"]), [], None, {}, {}

    class _Ck:
        def save(self, *a, **k):
            pass

    with patch("backend.translation.orchestrator.translate_with_orchestrator", _spy):
        worker._step_translate(
            "subtitle", segments, translated_path, [],
            False, {}, tmp_path, len(segments), _Ck(),
        )

    assert "progress_callback" in captured and callable(captured["progress_callback"])


# ══ A14：前端有 message 时只显示它（单一作者）═════════════════════════

HTML = (Path(__file__).parent.parent / "frontend" / "index.html").read_text(encoding="utf-8")


def _function_source(name):
    match = re.search(
        rf"(?:async\s+)?function\s+{re.escape(name)}\([^)]*\)\{{.*?\n\}}", HTML, re.S
    )
    assert match, f"找不到函数 {name}"
    return match.group(0)


def _run_retry_render(msg):
    """在 node 里真跑 renderRetryProgress，返回步骤文字和错误行文字。"""
    source = f"""
const store = {{}};
function mk(id){{ return {{id, textContent:'', innerHTML:'', style:{{}},
  classList:{{add(){{}}, remove(){{}}, toggle(){{}}}},
  appendChild(c){{ this.textContent = c.textContent; }} }}; }}
const els = {{}};
for (const id of ['step-J','fill-J','pct-J','err-J','stop-J']) els[id] = mk(id);
globalThis.$ = (id) => els[id] || null;
globalThis.document = {{ createElement: () => mk('tmp') }};
globalThis.activeJobs = {{}};
globalThis.console = console;
{_function_source('renderRetryProgress')}
renderRetryProgress('J', {json.dumps(msg)});
console.log(JSON.stringify({{step: els['step-J'].textContent, err: els['err-J'].textContent}}));
"""
    result = subprocess.run(["node", "-e", source], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_a14_frontend_shows_message_when_present():
    """A14：带 message 时，步骤文字必须显示它，而不是写死的「正在重试」。"""
    out = _run_retry_render({
        "retry_initial_failed_count": 40, "retry_processed_count": 12,
        "retry_new_success_count": 11, "retry_status": "running",
        "message": "正在再次处理上次失败的 40 句字幕｜正在翻译第 13/40 句。"
                   "已完成 12 句，成功 11 句，失败 1 句，还剩 28 句。",
    })
    assert "正在翻译第 13/40 句" in out["step"], out


def test_a14_frontend_falls_back_when_no_message():
    """A14：点「重试」那一瞬间传的对象没有 message，必须仍显示「正在重试」。"""
    out = _run_retry_render({
        "retry_initial_failed_count": 40, "retry_processed_count": 0,
        "retry_new_success_count": 0, "retry_status": "running",
    })
    assert out["step"] == "正在重试", out


def test_a14_frontend_does_not_print_a_second_number_set():
    """A14：单一作者——有 message 时不得再拼一行自己的数字（两组数字会打架）。"""
    out = _run_retry_render({
        "retry_initial_failed_count": 40, "retry_processed_count": 12,
        "retry_new_success_count": 11, "retry_status": "running",
        "message": "正在翻译第 13/40 句。已完成 12 句，成功 11 句，失败 1 句，还剩 28 句。",
    })
    assert "本轮待处理" not in out["err"], f"有 message 时不该再拼数字行: {out}"


# ══ ⑦.5 读代码抓到的两个缺陷（测试原来照不出）═════════════════════════

def _run_prog(config, messages):
    """真跑 worker 的 prog，捕获它实际发出去的东西。"""
    from unittest.mock import patch
    from backend import worker
    sent = []

    translated_path = Path(_TMP.name) / f"t{len(sent)}_{id(messages)}.json"
    segments = [worker.Segment(0, 0.0, 1.0, "one")]

    def _spy(**kwargs):
        cb = kwargs["progress_callback"]
        for m in messages:
            cb(96, m)
        return ["译文"], [], None, {}, {}

    class _Ck:
        def save(self, *a, **k):
            pass

    def _grab_stderr(_stage, _pct, message, *a, **k):
        sent.append(("progress", message))

    real_stdout = []

    class _FakeStdout:
        def write(self, line):
            real_stdout.append(line)

        def flush(self):
            pass

    with patch("backend.translation.orchestrator.translate_with_orchestrator", _spy), \
         patch.object(worker, "stderr_progress", _grab_stderr), \
         patch.object(worker, "_real_stdout", _FakeStdout()):
        worker._step_translate(
            "subtitle", segments, translated_path, [],
            False, config, Path(_TMP.name), 1, _Ck(),
        )
    for line in real_stdout:
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("type") == "retry_progress":
            sent.append(("retry", obj.get("message", "")))
    return sent


import tempfile as _tempfile
_TMP = _tempfile.TemporaryDirectory()


def test_defect1_retry_label_present_on_every_message():
    """⑦.5 缺陷 1：人工重试时，**每一条**运行中消息都要带「本轮 N 句」。

    原实现只给免费补全那几条加标签。API 翻译阶段的消息没有它，
    而前端在有 message 时会把本地那行数字藏掉 —— 用户整段看不到本轮总量。
    """
    sent = _run_prog(
        {"_partial_retry": True, "_retry_initial_failed_count": 40},
        ["正在用 DeepSeek 分析内容…", "正在翻译第 1/1 批"],
    )
    retry_msgs = [m for kind, m in sent if kind == "retry"]
    assert retry_msgs, "人工重试没发出任何 retry_progress"
    for m in retry_msgs:
        assert "正在再次处理上次失败的 40 句字幕" in m, f"这条缺本轮总量标签: {m!r}"
