"""G8（P379 + P378 运行中缺口）：免费兜底逐句实时进度。

病 A：等待期闭嘴——进度事件绑在「结果出来之后」，单句最坏 3 引擎 × 30 秒全程静默。
病 B：人工重试收不到 progress_callback——worker 的 if/else 分叉里 if 分支少传一个键。

合同见 pipelines/bug-pipeline-2026-08-18-G8.md ⑤.5（A1-A16 / B1-B5）。

五数字口径（⑤.5 裁决）：描述「本次运行的全部句子」，不是「免费兜底那批」。
  总数 N   = len(texts)
  已处理 P = API 成功数 + 免费已结算数
  成功 S   = API 成功数 + 免费成功数
  失败 F   = 免费最终失败数
  剩余 R   = N - P
  序号 k   = P + 1
恒等式恒成立：P == S + F 且 N == P + R
"""
import json
import re
import subprocess
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.translation.base import FAILED_TRANSLATION, TranslationResult
from backend.translation import orchestrator
from backend.translation.orchestrator import _fill_gaps_with_free


# ── 测试替身 ──────────────────────────────────────────────────────────

class _Free:
    """假免费引擎：translations 里有 source→译文即成功，缺失→空串即失败。"""

    def __init__(self, name, translations, raises=None):
        self.name = name
        self.translations = translations
        self.raises = raises
        self.calls = []

    def translate_batch(self, texts, **kwargs):
        self.calls.append(list(texts))
        if self.raises:
            raise self.raises
        return TranslationResult(
            translated=[self.translations.get(t, "") for t in texts],
            backend_name=self.name,
        )


def _texts(n):
    return [f"line number {i} here" for i in range(n)]


def _zh_all(texts):
    return {t: f"第{i}行中文" for i, t in enumerate(texts)}


def _collect(n_total, n_failed, backends, **kw):
    """跑一遍 _fill_gaps_with_free，返回 (消息列表, translated, 结算记录)。

    前 n_total-n_failed 句视为 API 已成功，后 n_failed 句交给免费兜底。
    """
    texts = _texts(n_total)
    api_ok = n_total - n_failed
    translated = [f"API译文{i}" for i in range(api_ok)] + [FAILED_TRANSLATION] * n_failed
    failed = list(range(api_ok, n_total))
    msgs, settled = [], []
    _fill_gaps_with_free(
        texts, translated, failed, backends,
        lambda pct, msg: msgs.append((pct, msg)),
        lambda idx, text, ok, isf: settled.append((idx, ok)),
        **kw,
    )
    return msgs, translated, settled


# ── 数字解析 ──────────────────────────────────────────────────────────

_NUM = re.compile(
    r"已完成 (?P<p>\d+) 句，成功 (?P<s>\d+) 句，失败 (?P<f>\d+) 句，还剩 (?P<r>\d+) 句"
)
_NUM_SETTLED = re.compile(
    r"已处理 (?P<p>\d+)/(?P<n>\d+) 句，成功 (?P<s>\d+) 句，失败 (?P<f>\d+) 句，还剩 (?P<r>\d+) 句"
)
_WAITING = re.compile(r"正在等待免费翻译返回第 (?P<k>\d+)/(?P<n>\d+) 句")


def _numbers(msg):
    """从任意一条进度文案里抽出 (总数, 已处理, 成功, 失败, 剩余)。"""
    w = _WAITING.search(msg)
    if w:
        m = _NUM.search(msg)
        assert m, f"等待消息缺五数字: {msg!r}"
        return (int(w.group("n")), int(m.group("p")), int(m.group("s")),
                int(m.group("f")), int(m.group("r")))
    m = _NUM_SETTLED.search(msg)
    assert m, f"结算消息缺五数字: {msg!r}"
    return (int(m.group("n")), int(m.group("p")), int(m.group("s")),
            int(m.group("f")), int(m.group("r")))


# ══ A1：请求发出之前必须先说话（病 A 本体）════════════════════════════

def test_a1_waiting_message_emitted_before_the_request():
    """A1：每句在调 translate_batch 之前，必须已经发出「正在等待…第 k/N 句」。"""
    texts = _texts(3)
    order = []

    class _Recording(_Free):
        def translate_batch(self, t, **kw):
            order.append(("request", t[0]))
            return super().translate_batch(t, **kw)

    backend = _Recording("google", _zh_all(texts))
    translated = [FAILED_TRANSLATION] * 3
    _fill_gaps_with_free(
        texts, translated, [0, 1, 2], [backend],
        lambda pct, msg: order.append(("msg", msg)),
    )
    # 第一个事件必须是等待消息，不是请求
    assert order[0][0] == "msg", f"第一个事件应是消息，实际 {order[0]}"
    assert _WAITING.search(order[0][1]), f"首条应为等待消息，实际 {order[0][1]!r}"

    # 每个 request 之前，紧邻的必须有一条 waiting
    for i, (kind, payload) in enumerate(order):
        if kind == "request":
            prior = [m for k, m in order[:i] if k == "msg" and _WAITING.search(m)]
            assert prior, f"第 {i} 个请求之前没有任何等待消息"


def test_a1_waiting_index_matches_processed_plus_one():
    """A1：等待消息里的序号 k 必须等于已处理数 + 1。"""
    msgs, _, _ = _collect(10, 4, [_Free("google", _zh_all(_texts(10)))])
    assert any(_WAITING.search(m) for _p, m in msgs), "一条等待消息都没有，测试会空跑"
    for _pct, msg in msgs:
        w = _WAITING.search(msg)
        if w:
            n, p, s, f, r = _numbers(msg)
            assert int(w.group("k")) == p + 1, f"序号应为已处理+1: {msg!r}"
            assert int(w.group("n")) == n


# ══ A2：每句结算后立即更新，不受节流 ═══════════════════════════════════

def test_a2_every_item_emits_a_settled_message_without_throttle():
    """A2：20 句瞬间处理完，也必须有 20 条结算消息（旧代码 1 秒节流会砍掉）。"""
    texts = _texts(20)
    msgs, _, _ = _collect(20, 20, [_Free("google", _zh_all(texts))])
    settled_msgs = [m for _p, m in msgs if _NUM_SETTLED.search(m)]
    assert len(settled_msgs) == 20, f"应有 20 条结算消息，实际 {len(settled_msgs)}"


def test_a2_no_time_throttle_left_in_the_loop():
    """A2：等待 + 结算 = 每句两条，20 句应为 40 条（证明节流已删干净）。"""
    texts = _texts(20)
    msgs, _, _ = _collect(20, 20, [_Free("google", _zh_all(texts))])
    assert len(msgs) == 40, f"20 句应发 40 条（等待+结算），实际 {len(msgs)}"


# ══ A3：两条恒等式，每一次输出都成立 ═══════════════════════════════════

def test_a3_two_identities_hold_on_every_single_emit():
    """A3：混合成功失败，每条消息都满足 已处理==成功+失败 且 总数==已处理+剩余。"""
    texts = _texts(9)
    zh = {t: f"第{i}行中文" for i, t in enumerate(texts) if i % 2 == 0}  # 一半失败
    msgs, _, _ = _collect(9, 6, [_Free("google", zh)])
    assert msgs
    for _pct, msg in msgs:
        n, p, s, f, r = _numbers(msg)
        assert p == s + f, f"已处理 != 成功+失败: {msg!r}"
        assert n == p + r, f"总数 != 已处理+剩余: {msg!r}"


def test_a3_api_successes_counted_into_the_totals():
    """A3：口径是「本次运行全部句子」——10 句里 6 句 API 成功时，总数必须是 10。"""
    texts = _texts(10)
    msgs, _, _ = _collect(10, 4, [_Free("google", _zh_all(texts))])
    n, p, s, f, r = _numbers(msgs[0][1])
    assert n == 10, f"总数应为本次运行全部 10 句，实际 {n}"
    assert p == 6 and s == 6 and f == 0 and r == 4, f"首条数字错: {msgs[0][1]!r}"


# ══ A4：只增不减，同一句不重复计算 ════════════════════════════════════

def test_a4_numbers_never_regress():
    """A4：已处理/成功/失败单调不减，剩余单调不增。"""
    texts = _texts(12)
    zh = {t: f"第{i}行中文" for i, t in enumerate(texts) if i % 3 != 0}
    msgs, _, _ = _collect(12, 12, [_Free("google", zh)])
    prev = None
    for _pct, msg in msgs:
        n, p, s, f, r = _numbers(msg)
        if prev:
            assert p >= prev[1] and s >= prev[2] and f >= prev[3], f"数字倒退: {msg!r}"
            assert r <= prev[4], f"剩余变大: {msg!r}"
        prev = (n, p, s, f, r)


def test_a4_each_item_settles_exactly_once():
    """A4：每个失败项恰好结算一次（B1 现有合同，不得破坏）。"""
    texts = _texts(15)
    _msgs, _translated, settled = _collect(15, 15, [_Free("google", _zh_all(texts))])
    assert sorted(i for i, _ in settled) == list(range(15))


# ══ A5：结束时剩余归零 ════════════════════════════════════════════════

def test_a5_final_message_has_zero_remaining_all_success():
    texts = _texts(5)
    msgs, _, _ = _collect(5, 5, [_Free("google", _zh_all(texts))])
    n, p, s, f, r = _numbers(msgs[-1][1])
    assert (n, p, s, f, r) == (5, 5, 5, 0, 0)


def test_a5_final_message_has_zero_remaining_all_failed():
    texts = _texts(5)
    msgs, _, _ = _collect(5, 5, [_Free("google", {})])
    n, p, s, f, r = _numbers(msgs[-1][1])
    assert (n, p, s, f, r) == (5, 5, 0, 5, 0)


def test_a5_single_item():
    texts = _texts(1)
    msgs, _, _ = _collect(1, 1, [_Free("google", _zh_all(texts))])
    assert _numbers(msgs[-1][1]) == (1, 1, 1, 0, 0)


# ══ A6：一家失败继续下一家时，不得提前计失败 ══════════════════════════

def test_a6_first_backend_fails_second_succeeds_counts_success_once():
    """A6：第一家返回空、第二家成功——该句只能计一次，且计成功。"""
    texts = _texts(3)
    bad = _Free("google", {})           # 全部返回空 = 失败
    good = _Free("bing", _zh_all(texts))
    msgs, translated, settled = _collect(3, 3, [bad, good])
    assert translated == [f"第{i}行中文" for i in range(3)]
    assert settled == [(0, True), (1, True), (2, True)]
    n, p, s, f, r = _numbers(msgs[-1][1])
    assert (n, p, s, f, r) == (3, 3, 3, 0, 0), "第一家失败不得让该句进失败数"


def test_a6_all_backends_fail_counts_failure_once():
    """A6：所有允许的免费路线都失败，才计入失败数，且只计一次。"""
    texts = _texts(3)
    msgs, _, settled = _collect(3, 3, [_Free("google", {}), _Free("bing", {})])
    assert settled == [(0, False), (1, False), (2, False)]
    assert _numbers(msgs[-1][1]) == (3, 3, 0, 3, 0)


def test_a6_timeout_and_empty_content_are_failures_not_crashes():
    """A6：请求超时（抛异常）和返回空内容，都算失败，不得让循环崩掉。"""
    texts = _texts(4)
    boom = _Free("google", {}, raises=TimeoutError("timed out"))
    msgs, _, settled = _collect(4, 4, [boom])
    assert len(settled) == 4 and all(ok is False for _i, ok in settled)
    assert _numbers(msgs[-1][1]) == (4, 4, 0, 4, 0)


# ══ A7：没有免费引擎时不许说「正在等待」（副审必须项）═══════════════════

def test_a7_no_backend_emits_no_waiting_message():
    """A7：ordered_backends 为空 = 没有东西可等，说「正在等待」是假话。"""
    texts = _texts(5)
    msgs, _, settled = _collect(5, 5, [])
    waiting = [m for _p, m in msgs if _WAITING.search(m)]
    assert waiting == [], f"无引擎时不得发等待消息，实际 {waiting}"
    # 对照：同样 5 句、但有引擎时必须有 5 条等待消息。否则上面那条断言是空跑。
    with_backend, _, _ = _collect(5, 5, [_Free("google", _zh_all(_texts(5)))])
    assert len([m for _p, m in with_backend if _WAITING.search(m)]) == 5
    # 但每句仍必须恰好结算一次（B1 现有合同）
    assert sorted(i for i, _ in settled) == list(range(5))


def test_a7_no_backend_still_reports_final_numbers():
    """A7：无引擎时仍要有结算消息，且最终剩余为 0。"""
    msgs, _, _ = _collect(5, 5, [])
    assert msgs, "无引擎时仍必须报结算进度"
    assert _numbers(msgs[-1][1]) == (5, 5, 0, 5, 0)


# ══ N=0 边界：没有句子需要免费处理 ════════════════════════════════════

def test_zero_items_emits_nothing():
    """没有句子需要免费补全时，一条消息都不该发。"""
    msgs, _, settled = _collect(5, 0, [_Free("google", {})])
    assert msgs == [] and settled == []


# ══ A13：进度回调抛异常，循环必须跑完（⑤ Q6 发现 1）═══════════════════

def test_a13_progress_callback_raising_does_not_abort_the_loop():
    """A13：进度回调自己抛异常时不得打断整批，否则字幕缺一半。

    （生产里的汇报函数写不出去时会直接结束进程——D-1 补修 D1；这里测的是
    循环对回调异常的容忍，与那条无关。）
    """
    texts = _texts(6)
    translated = [FAILED_TRANSLATION] * 6
    settled = []

    def boom(_pct, _msg):
        raise BrokenPipeError("callback failed")

    _fill_gaps_with_free(
        texts, translated, list(range(6)), [_Free("google", _zh_all(texts))],
        boom,
        lambda idx, text, ok, isf: settled.append(idx),
    )
    assert sorted(settled) == list(range(6)), "回调抛异常时仍须处理完所有句子"
    assert translated == [f"第{i}行中文" for i in range(6)]


# ══ A16：文案不得带出字幕正文 ═════════════════════════════════════════

def test_a16_messages_never_contain_subtitle_text():
    """A16：进度文案只许有数字和固定标签，不得带原文或译文。"""
    texts = ["SECRET SOURCE SENTENCE"] * 1
    translated = [FAILED_TRANSLATION]
    msgs = []
    _fill_gaps_with_free(
        texts, translated, [0],
        [_Free("google", {"SECRET SOURCE SENTENCE": "机密译文内容"})],
        lambda pct, msg: msgs.append(msg),
    )
    blob = " ".join(msgs)
    assert "SECRET" not in blob and "机密译文内容" not in blob, f"文案泄露内容: {blob!r}"


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
    free = _Free("google", {t: f"译文{i}" for i, t in enumerate(texts)})

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
    monkeypatch.setattr(orchestrator, "FREE_BACKENDS", {"google": free})
    msgs = []
    translated, failed, *_ = orchestrator.translate_with_orchestrator(
        texts, priority=["google"], api_keys=[{"key": "k"}], context_aware=False,
        progress_callback=lambda pct, msg: msgs.append(msg),
    )
    assert translated == ["付费0", "付费1", "付费2"] and failed == []
    assert waits == [5]
    assert "用户API 服务器出错，恢复后自动继续" in msgs
    assert not any(_WAITING.search(m) for m in msgs), "不得再出现免费兜底进度"


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
            "subtitle", segments, translated_path, "speed", ["google"], [],
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
            "subtitle", segments, translated_path, "speed", ["google"], [],
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
        "message": "正在再次处理上次失败的 40 句字幕｜正在等待免费翻译返回第 13/40 句。"
                   "已完成 12 句，成功 11 句，失败 1 句，还剩 28 句。",
    })
    assert "正在等待免费翻译返回第 13/40 句" in out["step"], out


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
        "message": "正在等待免费翻译返回第 13/40 句。已完成 12 句，成功 11 句，失败 1 句，还剩 28 句。",
    })
    assert "本轮待处理" not in out["err"], f"有 message 时不该再拼数字行: {out}"


# ══ A11：运行中数字与结束提示同源 —— 把「巧合相等」变成「被测试钉住」═══

def test_a11_worker_counters_match_the_last_running_message():
    """A11 / ③副审「数字单一作者」的实测闸门。

    运行中文案的数字由 orchestrator 算（`_fill_gaps_with_free` 内部累加）；
    结束提示的 `fallback_success` / `fallback_processed` 由 worker 的
    `item_result` 累加。**两个计数器在数同一件事**——这正是 C001 的形状。

    它们今天相等是构造上必然（每个免费项恰好触发一次 is_fallback=True 回调），
    但「必然」没人守着就会变成「曾经必然」。这条测试就是那个守门的。
    """
    texts = _texts(10)
    zh = {t: f"第{i}行中文" for i, t in enumerate(texts) if i % 3 != 0}
    translated = [f"API译文{i}" for i in range(4)] + [FAILED_TRANSLATION] * 6
    failed = list(range(4, 10))

    # 复刻 worker.item_result 的记账方式（worker.py:_run_translation 内）
    worker_side = {"processed": 0, "success": 0}

    def item_result(_idx, _text, ok, is_fallback):
        if is_fallback:
            worker_side["processed"] += 1
            if ok:
                worker_side["success"] += 1

    msgs = []
    _fill_gaps_with_free(
        texts, translated, failed, [_Free("google", zh)],
        lambda pct, msg: msgs.append(msg), item_result,
    )

    n, p, s, f, r = _numbers(msgs[-1])
    api_settled = 4
    assert worker_side["processed"] == p - api_settled, (
        "worker 记的免费已处理数与运行中文案对不上 —— 两个计数器数的不是同一件事了"
    )
    assert worker_side["success"] == s - api_settled, (
        "worker 记的免费成功数与运行中文案对不上"
    )
    assert p == n and r == 0


# ══ ⑦.5 读代码抓到的两个缺陷（测试原来照不出）═════════════════════════

def _run_prog(config, messages):
    """真跑 worker 的 prog，捕获它实际发出去的东西。"""
    from unittest.mock import patch
    from backend import worker
    from backend.translation.orchestrator import FREE_FILL_PREFIX

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
            "subtitle", segments, translated_path, "speed", ["google"], [],
            False, config, Path(_TMP.name), 1, _Ck(),
        )
    for line in real_stdout:
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("type") == "retry_progress":
            sent.append(("retry", obj.get("message", "")))
    return sent, FREE_FILL_PREFIX


import tempfile as _tempfile
_TMP = _tempfile.TemporaryDirectory()


def test_defect1_retry_label_present_on_every_message_not_only_free_fill():
    """⑦.5 缺陷 1：人工重试时，**每一条**运行中消息都要带「本轮 N 句」。

    原实现只给免费补全那几条加标签。API 翻译阶段的消息没有它，
    而前端在有 message 时会把本地那行数字藏掉 —— 用户整段看不到本轮总量。
    """
    from backend.translation.orchestrator import FREE_FILL_PREFIX

    sent, _ = _run_prog(
        {"_partial_retry": True, "_retry_initial_failed_count": 40},
        ["正在用 DeepSeek 分析内容…", FREE_FILL_PREFIX + "正在等待免费翻译返回第 1/1 句。已完成 0 句，成功 0 句，失败 0 句，还剩 1 句。"],
    )
    retry_msgs = [m for kind, m in sent if kind == "retry"]
    assert retry_msgs, "人工重试没发出任何 retry_progress"
    for m in retry_msgs:
        assert "正在再次处理上次失败的 40 句字幕" in m, f"这条缺本轮总量标签: {m!r}"


def test_defect2_auto_fallback_notice_rides_on_the_first_running_message():
    """⑦.5 缺陷 2：「用户 API 翻译失败」不能单独发一条 —— 会被下一条秒覆盖。

    只出现一次的消息，在每秒好几条的流里等于没出现过。
    它必须挂在第一条运行中消息上，那条至少活到第一句返回。
    """
    from backend.translation.orchestrator import FREE_FILL_PREFIX

    free1 = FREE_FILL_PREFIX + "正在等待免费翻译返回第 1/2 句。已完成 0 句，成功 0 句，失败 0 句，还剩 2 句。"
    free2 = FREE_FILL_PREFIX + "已处理 1/2 句，成功 1 句，失败 0 句，还剩 1 句。"
    sent, _ = _run_prog({}, [free1, free2])
    msgs = [m for kind, m in sent if kind == "progress"]

    # 不许有「只说这一句、不带进度」的独立公告
    assert not any(m.strip() == "用户 API 翻译失败，正在自动使用免费翻译。" for m in msgs), \
        f"公告仍是单独一条，会被秒覆盖: {msgs}"
    # 第一条免费补全消息必须自带这个因果
    free_msgs = [m for m in msgs if "免费翻译返回第" in m or "已处理 " in m]
    assert free_msgs, f"没有免费补全消息: {msgs}"
    assert "用户 API 翻译失败" in free_msgs[0], f"第一条没带因果: {free_msgs[0]!r}"
    assert "正在等待免费翻译返回第 1/2 句" in free_msgs[0]
    # 后续用短标签
    assert free_msgs[1].startswith("自动免费兜底｜"), f"后续标签错: {free_msgs[1]!r}"
