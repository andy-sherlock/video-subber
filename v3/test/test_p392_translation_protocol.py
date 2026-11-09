"""P392：翻译协议靠结构、不靠数换行；两条路共用同一套保护（T23-T28、T33）。

一句话：`deepseek-chat` 习惯在 --- 上下空一行，`deepseek-v4-flash` 不空，
代码一个字母没改，换个模型就从 165/165 掉到 5/177。条数信息必须在结构里，不能在字符里。
"""

import importlib
import json
import re
import sys
from http.client import BadStatusLine, IncompleteRead, RemoteDisconnected
from types import SimpleNamespace

import pytest

from backend.translation import base as translation_base
from backend.translation import orchestrator as orch
from test import locked_decision
from backend.transcriber_v2 import (
    ArtifactLanguageMismatch as _real_artifact_mismatch,
    TRANSCRIPTION_WORD_SOURCES_BY_PRIMARY as _real_word_sources,
)


class _CannedLLM(translation_base.LLMBackend):
    """受控替身：把模型会返回的原始文本直接喂给解析逻辑。"""

    def __init__(self, raw, *, model="canned"):
        self._raw = raw
        self._model = model
        self.prompts = []

    @property
    def name(self):
        return "canned-llm"

    @property
    def _api_url(self):
        return "https://example.invalid/v1/chat/completions"

    def _build_headers(self):
        return {"Content-Type": "application/json"}

    def _call_api_parse(self, response_data):
        return self._raw

    def _call_api_raw(self, prompt, max_tokens=8192):
        self.prompts.append(prompt)
        raw = self._raw(prompt) if callable(self._raw) else self._raw
        return raw, {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}


_BATCH_MARKER = "仅按 [编号] 译文 格式输出"
# 模型漏写编号：第一行不匹配 [编号] 前缀 → 解析判定 numbering_format
_BAD_NUMBERING = "模型忘了编号直接给了译文"
# 逐条恢复用的"必被拒绝但非空"回复：含 --- 过不了干净闸，
# 又不会触发 P367 的连续空返回熔断，否则测不到三级耗尽。
_REJECTED_SINGLE = "--- 这不是合格译文"


def _is_batch_prompt(prompt):
    """真实批量 prompt 的标志（base.py::translate_batch 构建）。

    逐条恢复 prompt（orchestrator.py 内）虽然也带 [编号] 上下文行，
    但不含这句格式要求，用它区分两种调用。
    """
    return _BATCH_MARKER in prompt


def _batch_input_count(prompt):
    """读取真实批量 prompt 里的输入条数（[编号] 开头的行数）。"""
    assert _is_batch_prompt(prompt), "这不是批量 prompt"
    return len(re.findall(r"^\[\d+\]\s", prompt, re.M))


def _numbered(texts):
    """按当前生产协议构造模型回复：[编号] 译文。"""
    return "\n".join(f"[{index}] {text}" for index, text in enumerate(texts))


def _load_worker(monkeypatch):
    class _Segment:
        def __init__(self, id, start, end, text):
            self.id = id
            self.start = start
            self.end = end
            self.text = text
            self.words = []
            self.zh = ""
            self.speaker_change = False
            self.uncertain = False

    class _Receipt:
        pass

    class _Envelope:
        pass

    monkeypatch.setitem(
        sys.modules,
        "backend.transcriber_v2",
        SimpleNamespace(
            Segment=_Segment,
            TranscriptionEnvelope=_Envelope,
            TranscriptionReceipt=_Receipt,
            transcribe=lambda *args, **kwargs: [],
            _whisper_is_usable=lambda *args, **kwargs: False,
            # G11：worker 会 import 这两个异常类型；替身模块必须提供**真的**那两个，
            # 否则 except 捕获的是另一个类，测试会假绿。
            ArtifactLanguageMismatch=_real_artifact_mismatch,
            TRANSCRIPTION_WORD_SOURCES_BY_PRIMARY=_real_word_sources,
        ),
    )
    monkeypatch.delitem(sys.modules, "backend.worker", raising=False)
    return importlib.import_module("backend.worker")


# ── 协议：靠结构数条数 ────────────────────────────────────────

def test_batch_parse_counts_and_marks_missing():
    """T24b：中间少一条时无法判断缺口位置，整批必须作废、零回填。"""
    llm = _CannedLLM("[0] 译文一\n[2] 译文三")
    result = llm.translate_batch(["one", "two", "three"])
    assert result.translated == ["[翻译失败]"] * 3
    assert result.failure_kind == "numbering_format"


def test_count_mismatch_triggers_retry(monkeypatch):
    """T25：三级批量恢复都失败后，5 条必须全部进入付费单条重试。"""
    single_prompts = []

    def _raw(prompt):
        if _is_batch_prompt(prompt):
            expected = _batch_input_count(prompt)
            payload = ["批量译文"] * max(expected - 1, 0)
            return _numbered(payload)
        single_prompts.append(prompt)     # 单条重试请求
        return "补上的译文"

    llm = _CannedLLM(_raw)
    monkeypatch.setattr(orch, "_build_llm_backends", lambda keys: [llm])

    translated, failed, _tokens, _splits, _failures = orch.translate_with_orchestrator(
        progress_callback=lambda *a: None,
        texts=["a", "b", "c", "d", "e"],
        api_keys=[{"platform": "deepseek", "key": "x"}],
        context_aware=False,
    )

    assert len(single_prompts) == 5, \
        f"整批作废后应重发全部 5 条，实际重发 {len(single_prompts)} 条"
    assert failed == [], "重发之后不该还有失败"
    assert translated == ["补上的译文"] * 5


def test_paid_batch_mismatch_retries_whole_batch_on_same_backend_before_free(
    monkeypatch,
):
    """第一次 39/40 后，同一付费 AI 整批纠正重发成功；不换厂商、不碰免费引擎。"""
    texts = [f"source {index}" for index in range(40)]
    paid = None

    def _paid_raw(_prompt):
        if len(paid.prompts) == 1:
            payload = [f"第一厂商旧译文-{index}" for index in range(39)]
        else:
            payload = [f"第一厂商新译文-{index}" for index in range(40)]
        return _numbered(payload)

    paid = _CannedLLM(_paid_raw)
    second_paid = _CannedLLM(_numbered([f"第二厂商译文-{index}" for index in range(40)]))
    monkeypatch.setattr(
        orch,
        "_build_llm_backends",
        lambda _keys: [paid, second_paid],
    )

    translated, failed, tokens, _splits, _failures = orch.translate_with_orchestrator(
        progress_callback=lambda *a: None,
        texts=texts,
        api_keys=[{"platform": "deepseek", "key": "x"}],
        context_aware=False,
    )

    assert translated == [f"第一厂商新译文-{index}" for index in range(40)]
    assert failed == []
    assert len(paid.prompts) == 2
    assert second_paid.prompts == [], "协议重试不能换到第二个付费厂商"
    correction = (
        "上一次返回条数与输入不符，很可能合并了相邻两条。"
        "这一次必须严格一条对一条，即使两条原文本是一句话。"
    )
    assert correction not in paid.prompts[0]
    assert correction in paid.prompts[1]


def test_valid_paid_batch_does_not_trigger_protocol_recovery(monkeypatch):
    """合法 40/40 只调用一次付费 AI，不误触发恢复链或其他后端。"""
    texts = [f"source {index}" for index in range(40)]
    paid = _CannedLLM(_numbered([f"付费译文-{index}" for index in range(40)]))
    second_paid = _CannedLLM(_numbered([f"第二厂商译文-{index}" for index in range(40)]))
    monkeypatch.setattr(
        orch,
        "_build_llm_backends",
        lambda _keys: [paid, second_paid],
    )

    translated, failed, _tokens, _splits, _failures = orch.translate_with_orchestrator(
        progress_callback=lambda *a: None,
        texts=texts,
        api_keys=[{"platform": "deepseek", "key": "x"}],
        context_aware=False,
    )

    assert translated == [f"付费译文-{index}" for index in range(40)]
    assert failed == []
    assert len(paid.prompts) == 1
    assert second_paid.prompts == []


def test_three_paid_recovery_levels_exhausted_keep_source_never_free(
    monkeypatch,
):
    """整批、两半、逐条全部失败后保留原文；付费模式不再调用免费引擎（D-1 A5，合同变更）。"""
    texts = [f"source {index}" for index in range(4)]
    events = []

    def _paid_raw(prompt):
        events.append(("paid", prompt))
        if _is_batch_prompt(prompt):
            return _BAD_NUMBERING
        return _REJECTED_SINGLE

    paid = _CannedLLM(_paid_raw)
    second_paid = _CannedLLM(_numbered([f"第二厂商译文-{index}" for index in range(4)]))

    monkeypatch.setattr(
        orch,
        "_build_llm_backends",
        lambda _keys: [paid, second_paid],
    )

    translated, failed, _tokens, _splits, _failures = orch.translate_with_orchestrator(
        progress_callback=lambda *a: None,
        texts=texts,
        api_keys=[{"platform": "deepseek", "key": "x"}],
        context_aware=False,
    )

    batch_prompts = [
        prompt for prompt in paid.prompts
        if _is_batch_prompt(prompt)
    ]
    single_prompts = [
        prompt for prompt in paid.prompts
        if not _is_batch_prompt(prompt)
    ]
    assert [_batch_input_count(prompt) for prompt in batch_prompts] == [4, 4, 2, 2]
    assert len(single_prompts) == 4
    assert second_paid.prompts == [], "恢复耗尽后也不能切换第二个付费厂商"
    assert translated == texts, "修不好的句子保留原文"
    assert failed == []


@pytest.mark.parametrize(
    "make_error",
    [
        lambda: RemoteDisconnected("Remote end closed connection without response"),
        lambda: IncompleteRead(b"half a response"),
        lambda: BadStatusLine("truncated status line"),
    ],
    ids=["RemoteDisconnected", "IncompleteRead", "BadStatusLine"],
)
def test_protocol_recovery_disconnect_waits_on_same_vendor_then_continues(
    monkeypatch, make_error,
):
    """整批纠正阶段真实断连：原地等同一家恢复后接着修，不换厂商、不转免费。

    D-1 合同变更：8 月的做法是断连立刻转免费补缺；本批改为付费请求函数原地等
    （合同增量 5：修复阶段只等同一家，不换 Key）。这是 Agnes 那次 25 条批次的
    真实故障形状——远端主动关连接。

    三种参数都是真实的 HTTP 断流：`RemoteDisconnected` 因为同时继承
    `ConnectionResetError` 本来就能被认出；`IncompleteRead` 和 `BadStatusLine`
    只挂在 `HTTPException` 下，曾被归成 `unknown` 而躲过熔断，
    结果连接已经坏了还照打 43 次恢复请求。
    """
    texts = [f"source {index}" for index in range(4)]

    class _DisconnectingPaid(translation_base.TranslationBackend):
        def __init__(self,
                     language_decision=None, engine_received_language=None,
                     engines_in_text_chain=None,
                     artifact_language_check="ok",
                     transcription_request_fingerprint="",
                     observation_evidence=None):
            self.batch_calls = []
            self.single_calls = []
            # G11：语言决定与各引擎实收语言，随收据一起穿过存档往返。
            if language_decision is None:
                from test import locked_decision
                language_decision = locked_decision()
            self.language_decision = language_decision
            self.engine_received_language = dict(engine_received_language or {})
            # G11-B：本次哪些引擎的文字进了正式链路——缓存复用检查的依据。
            self.engines_in_text_chain = tuple(
                engines_in_text_chain
                if engines_in_text_chain is not None
                else self.engine_received_language.keys())
            self.artifact_language_check = artifact_language_check
            self.transcription_request_fingerprint = transcription_request_fingerprint
            self.observation_evidence = dict(observation_evidence or {})

        @property
        def name(self):
            return "disconnecting-paid"

        @property
        def is_llm(self):
            return True

        def translate_batch(self, batch, **kwargs):
            self.batch_calls.append(list(batch))
            if len(self.batch_calls) == 1:
                return translation_base.TranslationResult(
                    translated=["[翻译失败]"] * len(batch),
                    backend_name=self.name,
                    failure_kind="numbering_format",
                )
            if len(self.batch_calls) == 2:
                raise make_error()
            return translation_base.TranslationResult(
                translated=[f"恢复译文{index}" for index in range(len(batch))],
                backend_name=self.name,
            )

        def translate_raw(self, prompt, max_tokens=8192):
            self.single_calls.append(prompt)
            return translation_base.TranslationResult(
                translated=[_REJECTED_SINGLE],
                backend_name=self.name,
            )

    from backend.translation import paid_request
    waits = []
    monkeypatch.setattr(paid_request, "_wait", waits.append)
    paid = _DisconnectingPaid()
    second_paid = _CannedLLM(_numbered([f"第二厂商译文-{index}" for index in range(4)]))
    monkeypatch.setattr(
        orch,
        "_build_llm_backends",
        lambda _keys: [paid, second_paid],
    )

    translated, failed, _tokens, _splits, _failures = orch.translate_with_orchestrator(
        progress_callback=lambda *a: None,
        texts=texts,
        api_keys=[{"platform": "deepseek", "key": "x"}],
        context_aware=False,
    )

    assert paid.batch_calls == [texts, texts, texts], "断连后原地等，再对同一家重发整批纠正"
    assert waits == [5]
    assert paid.single_calls == [], "整批纠正成功就不再逐条"
    assert second_paid.prompts == [], "恢复链断连也不能把同一批交给另一个付费厂商"
    assert translated == [f"恢复译文{index}" for index in range(4)]
    assert failed == []


def test_second_half_recovers_at_its_own_offset_and_clean_half_is_skipped(monkeypatch):
    """后半批按非零偏移回填；已经没有失败项的前半批不许再付费重发。

    旧版本只让前半批成功，`offset=start` 恒为 0，非零偏移根本没被走到。
    这里改成用奇数批 5 条（midpoint=3，拆成 3+2）证明「奇数批不得错位」；
    整批纠正只救回前 3 条，让失败集中在后半批，一次覆盖两件事：
      · 后半批的译文必须落回 3/4/5，不能挤到 0/1/2（`offset=start`）
      · 前半批此刻已无失败项，再调它既浪费钱，万一断连还会把
        `fused=True` 传出去，害得真正需要恢复的后半批直接失去机会
    """
    texts = [f"source {index}" for index in range(5)]
    paid = None

    def _paid_raw(prompt):
        if not _is_batch_prompt(prompt):
            return "逐条付费译文"
        count = _batch_input_count(prompt)
        if len(paid.prompts) == 1:
            return _BAD_NUMBERING                      # 初次：协议失败，进阶梯
        if count == 5:
            # 整批纠正：条数编号全对，前 3 条合格中文，后 2 条纯英文。
            # 批级语言闸看整批中文占比（39%）放行，后 2 条由单条强检查拦下。
            return _numbered([
                "这是第零条合格的中文译文",
                "这是第一条合格的中文译文",
                "这是第二条合格的中文译文",
                "totally english output three",
                "totally english output four",
            ])
        return _numbered([
            "这是第三条恢复成功的译文",
            "这是第四条恢复成功的译文",
        ])

    paid = _CannedLLM(_paid_raw)
    monkeypatch.setattr(orch, "_build_llm_backends", lambda _keys: [paid])

    translated, failed, _tokens, _splits, _failures = orch.translate_with_orchestrator(
        progress_callback=lambda *a: None,
        texts=texts,
        api_keys=[{"platform": "deepseek", "key": "x"}],
        context_aware=False,
    )

    batch_counts = [
        _batch_input_count(prompt) for prompt in paid.prompts
        if _is_batch_prompt(prompt)
    ]
    single_prompts = [
        prompt for prompt in paid.prompts if not _is_batch_prompt(prompt)
    ]

    assert batch_counts == [5, 5, 2], \
        "初次 + 整批纠正 + 后半批(2 条)；前半批已无失败项，不该再被调用"
    assert single_prompts == [], "两级批量恢复已补齐全部条目，不该走到逐条"
    assert translated[:3] == [
        "这是第零条合格的中文译文",
        "这是第一条合格的中文译文",
        "这是第二条合格的中文译文",
    ]
    assert translated[3:] == [
        "这是第三条恢复成功的译文",
        "这是第四条恢复成功的译文",
    ], "奇数批 5 条拆成 3+2，后半批必须落回 3/4，非零 offset 不能错位"
    assert failed == []


def test_half_batch_failure_kind_does_not_overwrite_the_other_half():
    """半批失败的原因只能写给这半批，不能盖掉另一半已经查明的原因。

    直接测恢复函数本身：这个场景要求前后两个半批都被调用且都失败，
    与上面那条「无失败半批必须跳过」的场景互斥，没法并进同一个测试。
    错误的原因表不会让字幕贴错位置，但会让用户看到的失败提示指向错的病因。
    """
    batch = [f"source {index}" for index in range(4)]
    results = ["[翻译失败]"] * 4
    failed_local = {0, 1, 2, 3}
    failure_local = {index: "numbering_format" for index in range(4)}

    class _Backend:
        name = "paid"
        is_llm = True

        def __init__(self):
            self.calls = []

        def translate_batch(self, texts, source_lang="auto", target_lang="zh", context=""):
            self.calls.append(list(texts))
            kinds = ["numbering_format", "empty_translation", "quality_rejected"]
            return translation_base.TranslationResult(
                translated=["[翻译失败]"] * len(texts),
                backend_name=self.name,
                failure_kind=kinds[len(self.calls) - 1],
            )

    backend = _Backend()
    orch._recover_paid_batch_protocol_failure(
        batch, backend, results, failed_local, failure_local,
        paid=orch._paid_requester([backend], backend, lambda *a: None, 0),
    )

    assert [len(call) for call in backend.calls] == [4, 2, 2]
    assert failure_local[0] == "empty_translation", \
        "位置 0/1 的原因由前半批那次调用决定，不能被后半批盖掉"
    assert failure_local[1] == "empty_translation"
    assert failure_local[2] == "quality_rejected", "位置 2/3 才是后半批的原因"
    assert failure_local[3] == "quality_rejected"


def test_paid_protocol_recovery_has_a_hard_43_retry_call_limit(monkeypatch):
    """即使有 41 个失败位置，初次请求之后也最多再调用付费 AI 43 次。"""
    texts = [f"source {index}" for index in range(41)]

    def _always_fail(prompt):
        if _is_batch_prompt(prompt):
            return _BAD_NUMBERING
        return _REJECTED_SINGLE

    paid = _CannedLLM(_always_fail)
    monkeypatch.setattr(orch, "_build_llm_backends", lambda _keys: [paid])

    translated, failed, tokens, _splits, _failures = orch.translate_with_orchestrator(
        progress_callback=lambda *a: None,
        texts=texts,
        api_keys=[{"platform": "deepseek", "key": "x"}],
        context_aware=False,
    )

    assert len(paid.prompts) - 1 == 43, \
        "恢复链只能有 1 次整批、2 次半批和最多 40 次单条调用"
    # D-1 A5（合同变更）：修不好的句子保留原文，不再记为失败。
    assert translated == texts
    assert failed == []


def test_paid_protocol_recovery_retries_40_items_despite_ten_item_gate(
    monkeypatch,
):
    """协议整批失败时，40 条都要逐条重发，不能被旧的 ≤10 门槛挡住。"""
    texts = [f"source {index}" for index in range(40)]

    def _fail_batches_but_pass_singles(prompt):
        if _is_batch_prompt(prompt):
            return _BAD_NUMBERING
        return "逐条付费译文"

    paid = _CannedLLM(_fail_batches_but_pass_singles)
    monkeypatch.setattr(orch, "_build_llm_backends", lambda _keys: [paid])

    translated, failed, tokens, _splits, _failures = orch.translate_with_orchestrator(
        progress_callback=lambda *a: None,
        texts=texts,
        api_keys=[{"platform": "deepseek", "key": "x"}],
        context_aware=False,
    )

    single_prompts = [
        prompt for prompt in paid.prompts
        if not _is_batch_prompt(prompt)
    ]
    assert len(single_prompts) == 40
    assert translated == ["逐条付费译文"] * 40
    assert failed == []
    assert tokens == {"input_tokens": 44, "output_tokens": 44}, \
        "初次、整批、两半和 40 次逐条付费调用都必须计入用量"


@pytest.mark.parametrize(
    ("case_name", "raw"),
    [
        ("39/40", _numbered(["译文"] * 39)),
        ("41/40", _numbered(["译文"] * 41)),
        ("40/40 含空字符串", _numbered(["译文"] * 39 + [""])),
        # 编号协议下一切都是文本，旧的「含非字符串」故障不复存在；
        # 等价故障是编号重复——行数看着对，编号集合却缺一个。
        ("40/40 编号重复", _numbered(["译文"] * 39) + "\n[0] 重复编号"),
    ],
)
def test_invalid_batch_is_discarded_without_any_positional_fill(case_name, raw):
    """数量或结构不符时整批作废，不能保留看起来正确的前半批。"""
    llm = _CannedLLM(raw)

    result = llm.translate_batch([f"line {index}" for index in range(40)])

    assert result.translated == ["[翻译失败]"] * 40, case_name


def test_invalid_batch_keeps_empty_source_empty_and_discards_non_empty_positions():
    """空源文本不送模型也不标失败；同批其余非空源文本必须全部作废。"""
    llm = _CannedLLM(_numbered(["只有一条译文"]))

    result = llm.translate_batch(["first", "", "third"])

    assert result.translated == ["[翻译失败]", "", "[翻译失败]"]


def test_batch_prompt_forbids_merging_or_splitting_entries():
    """提示词必须明确禁止合并或拆分，即使相邻两条本来是一句话。"""
    llm = _CannedLLM(_numbered(["一壶", "茶"]))

    result = llm.translate_batch(["a pot", "of tea."])

    assert result.translated == ["一壶", "茶"]
    assert len(llm.prompts) == 1
    assert (
        "必须返回和输入完全相同的条数，不许合并、不许拆分，"
        "哪怕两条本来是一句话。"
    ) in llm.prompts[0]


def test_partial_failure_is_recovered_without_leaving_failures(monkeypatch):
    """T26：177 条里 172 条失败 → 付费恢复补全或保留原文（D-6 P503 起已无免费引擎），不能留一堆 [翻译失败]。"""
    texts = [f"line {index}" for index in range(177)]

    def _raw(prompt):
        return _numbered(["只有一条"])

    llm = _CannedLLM(_raw)
    monkeypatch.setattr(orch, "_build_llm_backends", lambda keys: [llm])

    translated, failed, _tokens, _splits, _failures = orch.translate_with_orchestrator(
        progress_callback=lambda *a: None,
        texts=texts,
        api_keys=[{"platform": "deepseek", "key": "x"}],
        context_aware=False,
    )

    assert len(translated) == 177
    assert "[翻译失败]" not in translated, "缺口必须由付费恢复补上或保留原文"
    assert failed == []


def test_default_path_has_same_protections_as_context_path(monkeypatch):
    """T28：默认路径（逐段翻译）与上下文感知路径拥有同一套保护。"""
    texts = [f"line {index}" for index in range(6)]

    outcomes = {}
    for context_aware in (False, True):
        llm = _CannedLLM(_BAD_NUMBERING)
        monkeypatch.setattr(orch, "_build_llm_backends", lambda keys, _l=llm: [_l])
        translated, failed, _t, _s, _f = orch.translate_with_orchestrator(
            progress_callback=lambda *a: None,
            texts=texts,
            api_keys=[{"platform": "deepseek", "key": "x"}],
            context_aware=context_aware,
        )
        outcomes[context_aware] = (translated, failed)

    assert outcomes[False][1] == [] and outcomes[True][1] == [], \
        "两条路都必须有同一套保护，不能只有一条路有降落伞"
    assert "[翻译失败]" not in outcomes[False][0]
    assert "[翻译失败]" not in outcomes[True][0]


def test_recovery_stage_keeps_the_same_strong_checks_as_the_main_path(monkeypatch):
    """恢复阶段回填必须过与主路径完全相同的强检查——P352 那个洞的唯一守卫。

    候选分支原版阶梯内部用的是弱检查（只判非空、不等于 [翻译失败]），照搬会让
    英文原文、点评文字从恢复旁路漏进字幕。这里构造的形状只有单条检查能拦：
    整批合起来中文占比够高，`_batch_target_language_failure_kind` 的批级语言闸
    放行，但其中一条是纯英文——必须被 `_accept_recovered_batch` 挡在字幕之外。
    """
    texts = ["first source", "second source"]
    paid = None

    def _paid_raw(prompt):
        if not _is_batch_prompt(prompt):
            return _REJECTED_SINGLE
        if len(paid.prompts) == 1:
            return _BAD_NUMBERING            # 初次：协议失败 → 进入恢复阶梯
        # 整批纠正：编号条数全对，第一条正经中文，第二条是纯英文
        return _numbered(["这是一句完全合格的中文译文内容", "totally english output"])

    paid = _CannedLLM(_paid_raw)
    monkeypatch.setattr(orch, "_build_llm_backends", lambda _keys: [paid])

    translated, failed, _tokens, _splits, _failures = orch.translate_with_orchestrator(
        progress_callback=lambda *a: None,
        texts=texts,
        api_keys=[{"platform": "deepseek", "key": "x"}],
        context_aware=False,
    )

    assert translated[0] == "这是一句完全合格的中文译文内容", "合格的那条应当被接受"
    assert "totally english output" not in translated, \
        "恢复阶段的英文原文必须被强检查拦住，绝不能漏进字幕"
    # D-1 A5（合同变更）：被拒的那条保留原文，不再交给免费引擎。
    assert translated[1] == "second source"
    assert failed == []

    # ── C6 的前缀识别层：只收审校流程词，且容忍模型加的装饰 ──────────
    #
    # 「**建议：**」「1. 建议：」这类逃逸是真实的——P392 整个立项就是因为
    # 模型格式不听话。这是补强既有防线，纯收益、零误伤。
    assert not translation_base.is_clean_translation_text("建议：改成你好")
    assert not translation_base.is_clean_translation_text("**建议：** 改成你好")
    assert not translation_base.is_clean_translation_text("1. 建议：改成你好")
    assert not translation_base.is_clean_translation_text("- 原因：这里更口语")
    #
    # 反向——这几句必须放行。P404 第四轮曾把「原文」「点评」加进这张表，
    # 导致视频里本来就在说这些话时字幕被整条删掉，已撤回。
    # **删用户字幕比多一句脏字幕严重**，这张表只收正常字幕不会用来起头的词。
    assert translation_base.is_clean_translation_text("原文：Hello"), \
        "视频里真的在说「原文：」时不能删掉这条字幕"
    assert translation_base.is_clean_translation_text("点评：这很好。"), \
        "视频里真的在说「点评：」时不能删掉这条字幕"
    assert translation_base.is_clean_translation_text("建议你早点睡"), \
        "没有冒号的日常用语不能被误伤"


class _ScriptedPaid(translation_base.TranslationBackend):
    """直接抛异常的付费后端替身。

    内置 LLMBackend 会把 API 异常包成 TranslationResult，走不到编排层的
    `except`；要测那条旁路只能用这种自己抛的替身。合同明确覆盖"直接抛异常"，
    真实 `RemoteDisconnected` 测试也是从这个入口进的。
    """

    def __init__(self, batch_script, raw_script=()):
        self.batch_script = list(batch_script)
        self.raw_script = list(raw_script)
        self.batch_calls = []
        self.raw_calls = []

    @property
    def name(self):
        return "scripted-paid"

    @property
    def is_llm(self):
        return True

    def _next(self, script, texts):
        item = script.pop(0) if script else None
        if isinstance(item, Exception):
            raise item
        return item

    def translate_batch(self, texts, source_lang="auto", target_lang="zh", context=""):
        self.batch_calls.append(list(texts))
        item = self._next(self.batch_script, texts)
        return translation_base.TranslationResult(
            translated=["[翻译失败]"] * len(texts),
            backend_name=self.name,
            failure_kind=item,
        )

    def translate_raw(self, prompt, max_tokens=512):
        self.raw_calls.append(prompt)
        item = self._next(self.raw_script, None)
        # 与 base.py::translate_raw 保持一致：非字符串或空白都判 empty_translation，
        # 而 translated 里原样放着那个 None——编排层必须扛得住。
        kind = None if isinstance(item, str) and item.strip() else "empty_translation"
        return translation_base.TranslationResult(
            translated=[item], backend_name=self.name, failure_kind=kind,
        )


def test_half_batch_unknown_exception_stops_the_task():
    """半批调用抛认不出的异常 → 任务出错（D-1 增 4，合同变更）。

    8 月的规矩是「非熔断异常只记这半批」；D-1 定为认不出的错 = 程序错误 =
    不会自己好，付费模式下不许悄悄跳过，抛 PaidAIUnavailable 让任务停在出错。
    """
    batch = [f"source {index}" for index in range(4)]
    results = ["[翻译失败]"] * 4
    failed_local = {0, 1, 2, 3}
    failure_local = {index: "numbering_format" for index in range(4)}

    backend = _ScriptedPaid([
        "numbering_format",              # 整批纠正：协议仍然失败
        ValueError("后端内部解析错误"),    # 前半批：非熔断异常
        "quality_rejected",              # 后半批：普通失败
    ])

    from backend.translation.paid_request import PaidAIUnavailable

    with pytest.raises(PaidAIUnavailable) as info:
        orch._recover_paid_batch_protocol_failure(
            batch, backend, results, failed_local, failure_local,
        paid=orch._paid_requester([backend], backend, lambda *a: None, 0),
        )
    assert info.value.kind == "unknown"
    assert [len(call) for call in backend.batch_calls] == [4, 2]


def test_single_retry_unknown_exception_stops_the_task():
    """逐条恢复里某一条抛认不出的异常 → 任务出错（D-1 增 4，合同变更）。"""
    texts = [f"source {index}" for index in range(4)]
    backend = _ScriptedPaid(
        # 初次 + 整批纠正 + 前后两半，四次批量全部协议失败 → 落到逐条
        ["numbering_format"] * 4,
        [ValueError("这一条炸了"), "译文一", "译文二", "译文三"],
    )

    from backend.translation.paid_request import PaidAIUnavailable

    with pytest.raises(PaidAIUnavailable):
        orch._translate_llm_per_batch(texts, [backend], progress_callback=lambda *a: None)
    assert len(backend.raw_calls) == 1


def test_none_reply_survives_and_success_resets_the_empty_streak():
    """接口交回 None 不许把任务打崩；中间成功的一条要打断连续空返回的计数。

    ① `base.py::translate_raw` 明确允许非字符串内容——它会交回
       `translated=[None]` 且 `failure_kind=empty_translation`。编排层直接对它
       `.strip()`，整个翻译任务会以 `AttributeError` 崩掉。

    ② D-1 合同变更：8 月这里测的是「异常打断空返回计数」；本批认不出的异常
       改为让任务出错（增 4），异常不再出现在逐条链中，改用成功的一条来打断。
    """
    texts = [f"source {index}" for index in range(4)]
    backend = _ScriptedPaid(
        ["numbering_format"] * 4,                      # 阶梯四次批量全败 → 落到逐条
        [None, "第二条译文", None, "第四条译文"],
    )
    translated, failed, _tokens, _splits, _failures = orch._translate_llm_per_batch(
        texts, [backend], progress_callback=lambda *a: None,
    )

    assert len(backend.raw_calls) == 4, \
        "None → 成功 → None 不构成连续两次空返回，第 4 条必须还有机会"
    assert translated[1] == "第二条译文"
    assert translated[3] == "第四条译文"
    assert [translated[0], translated[2]] == [texts[0], texts[2]], \
        "付费救不回来的保留原文（D-1 A5），不能因为崩溃而丢掉整个任务"
    assert failed == []

    # 源头也堵上：LLMBackend 不该交出违反 list[str] 契约的结果。
    # 上面那几条断言守的是消费端兜底，这几条守的是源头不再产生非字符串。
    #
    # None 是 falsy，`len(content) if content else 0` 那行不会执行；
    # 但 123 / True / object() 是**真值**非字符串，会先在 len() 上抛 TypeError，
    # 被外层 except 归成 unknown —— 那就绕过了「连续两次空返回就停手」，
    # 最坏会照发满 40 次付费逐条请求。
    for bad in (None, 123, True, object()):
        probe = _CannedLLM(lambda _prompt, _value=bad: _value)
        probe_result = probe.translate_raw("任意 prompt")
        assert probe_result.translated == [""], \
            f"{type(bad).__name__} 必须在源头规范成空串"
        assert probe_result.failure_kind == "empty_translation", \
            f"{type(bad).__name__} 应判 empty_translation，不能掉成 unknown"


# ── 报错说人话 ────────────────────────────────────────────────

def _partial_cache_case(monkeypatch, tmp_path, saved_zh):
    """造一份身份对得上的「部分完成」缓存，返回 (worker, segments, 缓存路径, 翻译调用记录)。

    `saved_zh(index)` 决定第 index 句存的译文。
    """
    worker = _load_worker(monkeypatch)
    segments = [
        SimpleNamespace(id=index, start=index, end=index + 0.5,
                        text=f"line {index}", zh="", words=[])
        for index in range(10)
    ]
    translated_path = tmp_path / "translated.json"
    translated_path.write_text(json.dumps([
        {"id": seg.id, "start": seg.start, "end": seg.end,
         "text": seg.text, "zh": saved_zh(index)}
        for index, seg in enumerate(segments)
    ], ensure_ascii=False), encoding="utf-8")

    translate_calls = []

    def _fake_translate(**kwargs):
        translate_calls.append(kwargs)
        return ([], [], None, {}, {})

    monkeypatch.setattr(
        sys.modules["backend.translation.orchestrator"],
        "translate_with_orchestrator",
        _fake_translate,
    )
    return worker, segments, translated_path, translate_calls


def _one_ok_nine_failed(index):
    return "译文" if index == 0 else "[翻译失败]"


def test_p521_accept_partial_exports_even_when_most_lines_failed(monkeypatch, tmp_path):
    """P521：「部分完成」弹窗点「保留未翻译字幕并生成文件」，成功不到一半也要能导出。

    原来这里有一道「成功率<50% 就当坏存档，删缓存并报错」的质量闸。可「[翻译失败]」
    是我们自己合法写进缓存的正常状态，弹窗正是为「失败太多」而存在——导出必撞闸，
    用户按了「生成文件」却拿到报错，缓存还被删了。真坏的存档由读缓存前的三道结构
    检查（JSON 坏 / 批量污染 / 身份对不上）管，质量闸是第二份、判错的那份规则，已删。
    """
    worker, segments, translated_path, translate_calls = _partial_cache_case(
        monkeypatch, tmp_path, _one_ok_nine_failed)

    failed, _splits, ok_count = worker._step_translate(
        "translate", segments, translated_path,
        [], False, {"_accept_partial": True}, tmp_path, len(segments),
        SimpleNamespace(save=lambda *a, **k: None),
    )

    assert translate_calls == [], "导出只读缓存，不能偷偷重翻"
    assert translated_path.exists(), "用户同意导出的缓存不能被删"
    assert ok_count == 1
    assert len(failed) == 9
    assert [s.zh for s in segments[1:]] == ["[翻译失败]"] * 9, \
        "失败句保持「[翻译失败]」，交给显示层标成「【未翻译】原文」"


def test_p521_plain_rerun_on_mostly_failed_cache_goes_to_partial_dialog(monkeypatch, tmp_path):
    """普通重跑（例如翻译后中断再跑）读到同一份缓存：不报错、不删缓存，
    返回 9 条失败交给上层再弹一次「部分完成」让用户选。"""
    worker, segments, translated_path, translate_calls = _partial_cache_case(
        monkeypatch, tmp_path, _one_ok_nine_failed)

    failed, _splits, ok_count = worker._step_translate(
        "translate", segments, translated_path,
        [], False, {}, tmp_path, len(segments),
        SimpleNamespace(save=lambda *a, **k: None),
    )

    assert translate_calls == []
    assert translated_path.exists()
    assert ok_count == 1
    assert [item[0] for item in failed] == list(range(1, 10))


def test_p521_empty_saved_translation_counts_as_failed(monkeypatch, tmp_path):
    """②-P521 主审补的那一行：缓存里译文是空串（旧版本或手改过的缓存）时，
    读缓存要把它当成「[翻译失败]」——否则删闸后它不进 failed、不弹窗，
    静默出一份整片「【未翻译】」的字幕。「什么算失败」只认
    `_is_valid_saved_translation` 一份规则。"""
    worker, segments, translated_path, _calls = _partial_cache_case(
        monkeypatch, tmp_path, lambda index: "译文" if index == 0 else "")

    failed, _splits, ok_count = worker._step_translate(
        "translate", segments, translated_path,
        [], False, {}, tmp_path, len(segments),
        SimpleNamespace(save=lambda *a, **k: None),
    )

    assert ok_count == 1
    assert [s.zh for s in segments[1:]] == ["[翻译失败]"] * 9
    assert len(failed) == 9


# ── 止血那一行的边界 ──────────────────────────────────────────
