"""P395：删掉 64 词固定窗口，改为整篇一次请求 / 语义推进。

本文件锁定的是「窗口这个概念本身不存在」，而不是「窗口大小改成了多少」。
判据来自 P395 合同第五版（见 pipelines/bug-pipeline-2026-07-31-P395-merge.md 第 ④⑤ 节）。
"""

import importlib
import inspect
import json
import re
import sys
from types import SimpleNamespace

from test import transcription_envelope_from_raw, locked_decision
from backend.transcriber_v2 import (
    ArtifactLanguageMismatch as _real_artifact_mismatch,
    TRANSCRIPTION_WORD_SOURCES_BY_PRIMARY as _real_word_sources,
)


# 断句路径上被禁止的名字。它们全部是「预先按算术切好、再告诉模型只能在这几个
# 位置断」的产物；整篇/语义推进模式下这个概念不存在。
FORBIDDEN_NAMES = {
    "window_core_words",
    "context_words",
    "proposed_breaks_by_window",
    "allowed_break_indices",
}

# 允许存在的范围参数。它们与窗口的区别是：**起点由上一次模型选的断点决定，
# 不是 64 的倍数**；且范围内任意位置都可断，不再有「只能在这几个索引断」的名单。
ALLOWED_RANGE_NAMES = {"start_index", "end_index"}


def _words(texts, *, step=0.35):
    return [
        {
            "word": text,
            "text": text,
            "start": round(index * step, 3),
            "end": round(index * step + 0.3, 3),
        }
        for index, text in enumerate(texts)
    ]


def _install_lightweight_segment(monkeypatch):
    class _Segment:
        def __init__(self, id, start, end, text):
            self.id = id
            self.start = start
            self.end = end
            self.text = text

    monkeypatch.setitem(
        sys.modules,
        "backend.transcriber_v2",
        SimpleNamespace(Segment=_Segment),
    )


def _own_callables(module):
    """只看模块自己定义的函数，不看它 import 进来的。"""
    for name, obj in vars(module).items():
        if not callable(obj) or not hasattr(obj, "__code__"):
            continue
        if getattr(obj, "__module__", None) != module.__name__:
            continue
        yield name, obj


def _text_of(words):
    return " ".join(word["text"] for word in words)


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
        def __init__(self, routed_primary, actual_primary, word_source,
                     source_text, source_words, source_fingerprint=None,
                     reference_text="", reference_usable=False,
                     language_decision=None, engine_received_language=None,
                     engines_in_text_chain=None,
                     artifact_language_check="ok",
                     transcription_request_fingerprint="",
                     observation_evidence=None):
            self.routed_primary = routed_primary
            self.actual_primary = actual_primary
            self.word_source = word_source
            self.source_text = source_text
            self.source_words = source_words
            self.source_fingerprint = source_fingerprint or "test-source"
            # G2/B9+B10：收据带上副引擎的完整转录与可用性判定。
            self.reference_text = reference_text
            self.reference_usable = reference_usable
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

    class _Envelope:
        def __init__(self, segments, receipt):
            self.segments = list(segments)
            self.receipt = receipt

        def __iter__(self):
            return iter(self.segments)

        def __len__(self):
            return len(self.segments)

        def __getitem__(self, index):
            return self.segments[index]

        def __setitem__(self, index, value):
            self.segments[index] = value

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


class _CountingLLM:
    """记录每次调用的提示词；响应可以是字符串，也可以是要抛出的异常。"""

    def __init__(self, responses, *, max_input_tokens=1_000_000):
        if isinstance(responses, (str, Exception)):
            responses = [responses]
        self.responses = list(responses)
        self.calls = []
        self.max_input_tokens = max_input_tokens

    def translate_raw_json(self, prompt, system_prompt=None):
        self.calls.append(prompt)
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        item = self.responses[index]
        if isinstance(item, Exception):
            raise item
        return item


def _prompt_first_index(prompt):
    """从提示词里取出第一个出现的 `索引: 词` 的索引号。"""
    for line in prompt.splitlines():
        match = re.fullmatch(r"(\d+): (.*)", line)
        if match:
            return int(match.group(1))
    raise AssertionError("提示词里没有任何 `索引: 词` 行")


def _write_raw(tmp_path, words):
    raw_text = _text_of(words)
    raw_path = tmp_path / "qwen_raw.json"
    raw_path.write_text(
        json.dumps({"full_text": raw_text, "word_timestamps": words}),
        encoding="utf-8",
    )
    return raw_path


def _tokens_for_chunk(chunk_words):
    """反推出让单次发送量正好等于 chunk_words 的模型上下文大小。

    P408: 生产侧换算改为 (max_input_tokens * 0.30 - overhead) // per_unit，
    断句的 overhead=1000, per_unit=8。
    反推：ctx = ceil((chunk_words * 8 + 1000) / 0.30) + 余量。
    """
    return int((chunk_words * 8 + 1000) / 0.30) + 8


# ── 合同 1b：断句路径上不存在任何窗口 / 分段 / 批次参数 ────────────────────

def test_no_window_constants_remain():
    """P395/1b：worker 不再持有固定窗口大小和上下文词数这两个常量。

    只把 64 改成别的数字不算修好——**这两个名字必须消失**，
    否则下一个人照样能写出「太长了切一刀」。
    """
    from backend import worker

    offenders = [
        name for name in ("_P390_BOUNDARY_WINDOW_WORDS", "_P390_BOUNDARY_CONTEXT_WORDS")
        if hasattr(worker, name)
    ]
    assert not offenders, f"断句路径仍持有固定窗口常量：{offenders}"


def test_no_window_parameters_in_any_boundary_function():
    """P395/1b：两个模块自己定义的函数，签名里不允许出现窗口类参数。"""
    from backend import worker
    from backend.core import sentence_groups

    offenders = []
    for module in (sentence_groups, worker):
        for name, func in _own_callables(module):
            try:
                signature = inspect.signature(func)
            except (TypeError, ValueError):
                continue
            for parameter in signature.parameters:
                if parameter in FORBIDDEN_NAMES:
                    offenders.append(f"{module.__name__}.{name}({parameter})")

    assert not offenders, f"断句路径仍存在窗口参数：{offenders}"


def test_window_planning_function_is_gone():
    """P395/1b：按词数机械切窗口的规划函数整个删除。"""
    from backend.core import sentence_groups

    assert not hasattr(sentence_groups, "plan_llm_primary_windows"), (
        "plan_llm_primary_windows 仍在——它就是把词流按 64 的倍数切开的那把刀"
    )


def test_source_boundary_window_implementation_is_gone():
    """P395：32 词窗口那套已下线实现整个删除（P405 同名缓存一并解决）。"""
    from backend import worker
    from backend.core import sentence_groups

    leftovers = [
        f"sentence_groups.{name}"
        for name in ("SourceBoundaryWindow", "SourceBoundaryPlan",
                     "plan_source_boundary_candidates",
                     "rebuild_sentence_groups_from_break_indices")
        if hasattr(sentence_groups, name)
    ]
    leftovers += [
        f"worker.{name}"
        for name in ("_judge_source_boundaries", "_SOURCE_BOUNDARY_CACHE_NAME")
        if hasattr(worker, name)
    ]
    assert not leftovers, f"32 词窗口的死代码仍在：{leftovers}"


# ── 合同 1c：提示词不得逐个列举可选断点索引 ────────────────────────────────

def test_prompt_builder_no_longer_takes_a_window():
    """P395/1c：提示词构造不再接收「这一份能断哪些位置」的包裹。"""
    from backend.core import sentence_groups

    signature = inspect.signature(sentence_groups.build_llm_primary_boundary_prompt)
    assert "window" not in signature.parameters, (
        "提示词构造仍在接收窗口对象；整篇模式下可断位置就是全部，不需要传"
    )


def test_prompt_does_not_enumerate_allowed_indices(monkeypatch):
    """P395/1c：提示词里不允许出现 `0, 1, 2, 3 …` 这种索引清单。

    64 词窗口下这行是 64 个数字；整篇 1807 词时会膨胀到 9724 个字符，
    占词内容的 45%，直接吃掉合同第 7 条刚定的 30% 额度的近三分之一。
    """
    _install_lightweight_segment(monkeypatch)
    from backend.core import sentence_groups

    words = _words([f"w{index}" for index in range(300)])
    full_text = " ".join(word["text"] for word in words)

    _system_prompt, user_prompt, _allowed_range = sentence_groups.build_llm_primary_boundary_prompt(
        words,
        language="en",
        full_text=full_text,
    )

    assert not re.search(r"\b0,\s*1,\s*2,\s*3\b", user_prompt), (
        "提示词仍在逐个列举可选断点索引"
    )


def test_prompt_length_stays_close_to_word_content(monkeypatch):
    """P395/1c：提示词的体积必须由词内容决定，不能被索引清单撑大。"""
    _install_lightweight_segment(monkeypatch)
    from backend.core import sentence_groups

    words = _words([f"w{index}" for index in range(300)])
    full_text = " ".join(word["text"] for word in words)

    _system_prompt, user_prompt, _allowed_range = sentence_groups.build_llm_primary_boundary_prompt(
        words,
        language="en",
        full_text=full_text,
    )

    word_lines = "\n".join(
        f"{index}: {word['text']}" for index, word in enumerate(words)
    )
    assert len(user_prompt) < len(word_lines) * 1.5, (
        f"提示词 {len(user_prompt)} 字符，词内容只有 {len(word_lines)} 字符——"
        "多出来的部分就是被索引清单吃掉的额度"
    )


# ── 合同 1a：装得下就整篇；装不下按语义推进 ────────────────────────────────

def test_whole_transcript_is_sent_in_one_request_when_it_fits(monkeypatch, tmp_path):
    """P395/1a：一次装得下时只发一次，且提示词从第 0 个词开始。"""
    worker = _load_worker(monkeypatch)
    words = _words([f"w{index}" for index in range(120)])
    raw_path = _write_raw(tmp_path, words)

    llm = _CountingLLM('{"break_after_indices": [40, 80, 118]}')
    groups, reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path, language="en"), boundary_cache_path=raw_path.with_name("sentence_boundaries.json"), llm_boundary_backend=llm,
    )

    assert len(llm.calls) == 1, f"装得下就该只发一次，实际发了 {len(llm.calls)} 次"
    assert _prompt_first_index(llm.calls[0]) == 0
    assert reason is None, "正常路径不应该有回退原因"
    assert groups, "必须产出句子组"


def test_advance_starts_from_the_break_the_model_chose(monkeypatch, tmp_path):
    """P395/1a：装不下时按语义推进——下一次的起点是上一次模型选的最后一个断点+1。

    这是与 64 词窗口的根本区别：切口位置由模型决定，不是算术决定。
    """
    worker = _load_worker(monkeypatch)
    words = _words([f"w{index}" for index in range(300)])
    raw_path = _write_raw(tmp_path, words)

    llm = _CountingLLM(
        [
            # 第一次发 0~199，末断点选在 195
            '{"break_after_indices": [50, 100, 195]}',
            # 第二次必须从 196 开始发，断点落在该次范围内
            '{"break_after_indices": [220, 260, 294]}',
        ],
        max_input_tokens=_tokens_for_chunk(200),
    )
    groups, reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path, language="en"), boundary_cache_path=raw_path.with_name("sentence_boundaries.json"), llm_boundary_backend=llm,
    )

    assert len(llm.calls) == 2, (
        f"300 个词、单次只能发 200 个，应当推进两次，实际 {len(llm.calls)} 次"
    )
    assert _prompt_first_index(llm.calls[0]) == 0
    assert _prompt_first_index(llm.calls[1]) == 196, (
        "第二次推进的起点必须是上一次末断点 195 的下一个词，"
        f"实际是 {_prompt_first_index(llm.calls[1])}"
    )
    assert reason is None
    assert groups


# ── 合同 1d：推进前检查剩余词数，≤ 一段上限就不再发 ────────────────────────

def test_no_extra_request_when_remaining_words_fit_one_group(monkeypatch, tmp_path):
    """P395/1d（⑤ 走查 Q6 发现）：剩下的词已经装得下一组，就别再发请求。

    场景：200 个词，模型末断点在 195，只剩 4 个词。
    为这 4 个词再发一次完整请求 = 白烧一次调用。
    """
    worker = _load_worker(monkeypatch)
    words = _words([f"w{index}" for index in range(200)])
    raw_path = _write_raw(tmp_path, words)

    llm = _CountingLLM('{"break_after_indices": [50, 120, 195]}')
    groups, reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path, language="en"), boundary_cache_path=raw_path.with_name("sentence_boundaries.json"), llm_boundary_backend=llm,
    )

    assert len(llm.calls) == 1, (
        f"剩 4 个词不该再发请求，实际发了 {len(llm.calls)} 次"
    )
    assert reason is None
    assert groups[-1].end_word_index == len(words) - 1, "最后一组必须收尾到末词"


# ── 合同 2：覆盖检查——模型提前收工必须被抓住 ──────────────────────────────

def test_model_stopping_early_is_caught_and_falls_back(monkeypatch, tmp_path):
    """P395/2：格式完全合法但只覆盖前面一小段的答案，必须判为提前收工。

    这份 JSON 严格递增、全是整数、全在范围内——**格式检查一定放行**。
    抓住它的唯一办法是数「最后一个断点之后还剩多少词」。
    """
    worker = _load_worker(monkeypatch)
    words = _words([f"w{index}" for index in range(200)])
    raw_path = _write_raw(tmp_path, words)

    llm = _CountingLLM('{"break_after_indices": [10, 20, 30]}')
    groups, reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path, language="en"), boundary_cache_path=raw_path.with_name("sentence_boundaries.json"), llm_boundary_backend=llm,
    )

    assert reason, "末断点 30、剩 169 个词没断——必须判为提前收工并回退"
    assert groups, "回退之后仍然要产出可交付的句子组"
    total_words = sum(len(group.words) for group in groups)
    assert total_words == len(words), "回退不能丢词"


# ── 合同 3：失败三分类 ─────────────────────────────────────────────────────

def test_connection_failure_waits_and_resumes_without_rule_fallback(monkeypatch, tmp_path):
    """D-1 A2（合同变更）：接口暂时不可用 → 原地等、恢复后接着断，**不退规则**。

    8 月 P395/3a 的规矩是「接口不可用立即整篇回退、不重试」；本批改为付费请求
    函数原地等（会自己好的错）。不会自己好的错由任务出错处理（test_d1_worker_steps）。
    """
    from backend.translation import paid_request

    waits = []
    monkeypatch.setattr(paid_request, "_wait", waits.append)
    worker = _load_worker(monkeypatch)
    words = _words([f"W{index}" for index in range(120)])
    raw_path = _write_raw(tmp_path, words)

    llm = _CountingLLM([ConnectionError("远端断开"), '{"break_after_indices": [40, 80, 118]}'])
    groups, reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path, language="en"), boundary_cache_path=raw_path.with_name("sentence_boundaries.json"), llm_boundary_backend=llm,
    )

    assert len(llm.calls) == 2 and waits == [5]
    assert reason is None
    assert sum(len(group.words) for group in groups) == len(words)


def test_malformed_answer_is_retried_exactly_once(monkeypatch, tmp_path):
    """P395/3b：回答本身坏了（JSON 不合法）→ 原样重试一次，第二次好就用它。"""
    worker = _load_worker(monkeypatch)
    # G12 第三批 B：首字母大写，避免 A+ 规则 1 撤掉无标点小写词前的刀（本条只验重试）
    words = _words([f"W{index}" for index in range(120)])
    raw_path = _write_raw(tmp_path, words)

    llm = _CountingLLM([
        "这不是 JSON",
        '{"break_after_indices": [40, 80, 118]}',
    ])
    groups, reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path, language="en"), boundary_cache_path=raw_path.with_name("sentence_boundaries.json"), llm_boundary_backend=llm,
    )

    assert len(llm.calls) == 2, "坏回答应当原样重试一次"
    assert reason is None, "第二次成功就不该回退"
    assert [group.start_word_index for group in groups] == [0, 41, 81, 119]


def test_malformed_answer_twice_falls_back(monkeypatch, tmp_path):
    """P395/3b：重试后仍然坏 → 整篇回退，不再第三次。"""
    worker = _load_worker(monkeypatch)
    words = _words([f"w{index}" for index in range(120)])
    raw_path = _write_raw(tmp_path, words)

    llm = _CountingLLM("这不是 JSON")
    groups, reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path, language="en"), boundary_cache_path=raw_path.with_name("sentence_boundaries.json"), llm_boundary_backend=llm,
    )

    assert len(llm.calls) == 2, "最多重试一次，不该有第三次"
    assert reason == "AI 答案未通过校验"
    assert sum(len(group.words) for group in groups) == len(words)


def test_semantically_bad_break_is_dropped_without_asking_again(monkeypatch, tmp_path):
    """P395/3c：语义不合格的断点【直接剔除】，不反馈重试。

    剔除一定对；重问可能又错，而且整篇模式下重问要把全部原词重发一遍。
    """
    worker = _load_worker(monkeypatch)
    words = _words(["It", "was", "freezing", "but", "it", "was", "worth", "it."])
    raw_path = _write_raw(tmp_path, words)

    # 索引 3 是 "but" —— 在它后面断开就是「话没说完」
    llm = _CountingLLM('{"break_after_indices": [3]}')
    groups, reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path, language="en"), boundary_cache_path=raw_path.with_name("sentence_boundaries.json"), llm_boundary_backend=llm,
    )

    assert len(llm.calls) == 1, "语义不合格不该重问模型"
    assert reason is None, "剔除坏断点不算回退"
    starts = [group.start_word_index for group in groups]
    assert 4 not in starts, "but 后面那一刀必须被剔除，不能有组从索引 4 开始"


def test_advance_waits_on_network_failure_and_continues(monkeypatch, tmp_path):
    """D-1 A2（合同变更）：推进途中断网 → 原地等、恢复后接着推进，不整篇回退。

    8 月 P395/3d 是「推进途中失败 → 整篇回退、立即停止」；格式两次坏仍整篇回退
    （test_malformed_answer_twice_falls_back），断网改为原地等。
    """
    from backend.translation import paid_request

    waits = []
    monkeypatch.setattr(paid_request, "_wait", waits.append)
    worker = _load_worker(monkeypatch)
    words = _words([f"w{index}" for index in range(300)])
    raw_path = _write_raw(tmp_path, words)

    llm = _CountingLLM(
        [
            '{"break_after_indices": [50, 100, 195]}',   # 第一段成功
            ConnectionError("第二段断开"),                 # 第二段接口挂了 → 原地等
            '{"break_after_indices": [220, 260, 294]}',   # 恢复后接着用
        ],
        max_input_tokens=_tokens_for_chunk(200),
    )
    groups, reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path, language="en"), boundary_cache_path=raw_path.with_name("sentence_boundaries.json"), llm_boundary_backend=llm,
    )

    assert len(llm.calls) == 3 and waits == [5]
    assert reason is None
    assert sum(len(group.words) for group in groups) == len(words), "不能丢词"


# ── 合同 4：回退必须用户可见且带原因 ───────────────────────────────────────

def test_progress_message_carries_the_fallback_reason(monkeypatch):
    """P395/4：用户在进度条上看到的那句话，必须带上回退原因。

    只说「已回退」不够——用户得知道能做什么（比如把 DeepSeek 挪到 API 列表第一位）。
    """
    worker = _load_worker(monkeypatch)

    normal = worker._p395_transcribe_message(141, None)
    assert normal == "语音识别完成: 共 141 句"

    degraded = worker._p395_transcribe_message(141, "AI 服务连接失败")
    assert "141" in degraded
    assert "回退" in degraded and "AI 服务连接失败" in degraded


def test_progress_event_is_real_json_on_the_wire(monkeypatch):
    """P395/4：断言的是真实进度事件本身，不是日志字符串。"""
    import io

    worker = _load_worker(monkeypatch)
    captured = io.StringIO()
    monkeypatch.setattr(worker, "_real_stdout", captured)

    worker.stderr_progress(
        "transcribe", 35,
        worker._p395_transcribe_message(141, "当前 AI 服务不支持断句"),
    )

    event = json.loads(captured.getvalue().strip())
    assert event["type"] == "progress"
    assert event["stage"] == "transcribe"
    assert "当前 AI 服务不支持断句" in event["message"]


# ── 合同 5：缓存 ───────────────────────────────────────────────────────────

def test_old_window_format_cache_is_rejected(monkeypatch, tmp_path):
    """P395/5a：带窗口字段的旧缓存必须被确定性拒绝，不能被新代码误用。

    不拒绝的话，旧的 64 词断点会被原样复用，新逻辑完全不执行——
    而且**表面上一切正常**，这是最容易「改了没效果」的地方。
    """
    worker = _load_worker(monkeypatch)
    words = _words(["It", "was", "freezing", "but", "it", "was", "worth", "it."])
    raw_path = _write_raw(tmp_path, words)

    cache_path = tmp_path / "sentence_boundaries.json"
    cache_path.write_text(json.dumps({
        "version": 3,
        "source_fingerprint": worker._p390_source_fingerprint(_text_of(words), words),
        "language": "en",
        "window_core_words": 64,
        "context_words": 8,
        "break_after_indices": [3],
        "group_fingerprint": "irrelevant",
    }), encoding="utf-8")

    llm = _CountingLLM('{"break_after_indices": [2]}')
    groups, reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path, language="en"), boundary_cache_path=raw_path.with_name("sentence_boundaries.json"), llm_boundary_backend=llm,
    )

    assert llm.calls, "旧格式缓存必须被拒绝并重新判断"
    assert reason is None


def test_fallback_result_is_never_written_to_cache(monkeypatch, tmp_path):
    """P395/5b：回退结果不写缓存。

    写进去的话，重跑同一个视频时缓存校验会全过，日志变成
    「已复用安全断点缓存」——**回退痕迹当场消失**，用户再也看不到提示。
    """
    worker = _load_worker(monkeypatch)
    words = _words([f"w{index}" for index in range(60)])
    raw_path = _write_raw(tmp_path, words)

    # D-1（合同变更）：回退只剩「回复格式两次不可用」这一种来源。
    llm = _CountingLLM("这不是 JSON")
    _groups, reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path, language="en"), boundary_cache_path=raw_path.with_name("sentence_boundaries.json"), llm_boundary_backend=llm,
    )

    assert reason, "这一趟应当是回退"
    assert not (tmp_path / "sentence_boundaries.json").exists(), (
        "回退结果被写进了缓存——重跑时回退提示会消失"
    )


def test_run_after_a_fallback_judges_again(monkeypatch, tmp_path):
    """P395/5b：回退之后再跑一次，必须重新判断，不能命中缓存。"""
    worker = _load_worker(monkeypatch)
    words = _words([f"w{index}" for index in range(60)])
    raw_path = _write_raw(tmp_path, words)

    failed = _CountingLLM("这不是 JSON")   # D-1：回退只剩回复格式坏这一种来源
    worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path, language="en"), boundary_cache_path=raw_path.with_name("sentence_boundaries.json"), llm_boundary_backend=failed,
    )

    healthy = _CountingLLM('{"break_after_indices": [20, 40, 58]}')
    _groups, reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path, language="en"), boundary_cache_path=raw_path.with_name("sentence_boundaries.json"), llm_boundary_backend=healthy,
    )

    assert healthy.calls, "第二次必须重新判断，不能复用上一次的回退结果"
    assert reason is None


# ── 合同 6：提示词软约束 + 保险丝分栏统计 ──────────────────────────────────

def test_prompt_gives_word_limit_but_never_seconds(monkeypatch):
    """P395/6a：把模型数得出来的限制告诉它；数不出来的不要给。

    模型收到的只有编号和词，**没有词级时间**——让它遵守「不超过 20 秒」
    只会让它瞎猜。20 秒仍由程序硬限制负责。

    P409（2026-08-04）撤掉原本并列的「不超过 240 个字符」断言。**这不是放弃
    P395 的目标，是换了个更有效的手段达成同一个目标。**

    P395/6a 想要的是「少让程序按时间戳盲切」。1807 词真实转录实测：
      · 原版（提示词含 240）        盲切 4 次
      · P409（提示词无 240）        盲切 1 次
    4 次盲切**全部由 20 秒触发**，唯一一组超 240 字符的同时也超了 20 秒——
    字符上限在这份样本里从未单独生效过。模型拿回标点后自己就断得更碎
    （136 刀 vs 125 刀），根本没机会撞上限。

    P395 合同第 6 条自带的撤回条款是「补刀减少但语义坏断点增加则撤回该提示」；
    此处两者**同时下降**（坏断点 11 → 3），撤回条款未触发，目标反而更达成。

    词数上限保留：英文路径确实用不满（实测 43/80），但 CJK 路径一词一字，
    80 词可能先于 120 字符上限触发——不能拿英文样本判它死刑。

    240 仍是程序端硬限制（``_DEFAULT_MAX_CHARS``），由
    ``enforce_llm_break_hard_limits`` 执行，本次一行未动。撤掉的只是
    「告诉模型这个数字」。
    """
    _install_lightweight_segment(monkeypatch)
    from backend.core import sentence_groups

    words = _words([f"w{index}" for index in range(30)])
    system_prompt, _user_prompt, _allowed_range = sentence_groups.build_llm_primary_boundary_prompt(
        words,
        language="en",
        full_text=" ".join(word["text"] for word in words),
    )

    assert "80" in system_prompt, "应当告诉模型一组最多 80 个词"
    assert "20 秒" not in system_prompt, "模型看不到时间，不能给它测不了的约束"
    assert sentence_groups._DEFAULT_MAX_CHARS == 240, (
        "240 字符仍是程序端硬限制，只是不再写进提示词"
    )


def test_hard_limits_report_which_breaks_they_added(monkeypatch):
    """P395/6b：硬限制必须报出「哪几刀是它自己补的」。

    补刀按设计不看语义（只找时间戳不重叠的位置），落在词组中间是常态。
    把它和模型断点混在一起统计，会得出「模型断得很差」的错误结论。
    """
    _install_lightweight_segment(monkeypatch)
    from backend.core import sentence_groups

    words = _words([f"w{index}" for index in range(200)])
    full_text = " ".join(word["text"] for word in words)

    final_breaks, added_by_limits = sentence_groups.enforce_llm_break_hard_limits(
        full_text,
        words,
        break_after_indices=[],
        language="en",
    )

    assert isinstance(added_by_limits, set)
    assert added_by_limits, "200 个词没有任何断点，硬限制必须补刀"
    assert added_by_limits <= set(final_breaks), "补的刀必须都在最终断点里"


# ── ⑦.5 diff 审查发现：推进量下限 ────────────────────────────────────────

def test_a_barely_advancing_answer_cannot_drag_the_run_into_many_calls(
    monkeypatch, tmp_path,
):
    """推进太少 = 模型没好好做，必须当成坏答案，不能让它把一篇拖成上千次调用。

    这条不是合同里写的，是 ⑦.5 读代码时发现的：中间块只检查「有没有推进」
    （`next_start > start_index`），不检查「推进了多少」。模型每次只返回
    第一个位置的话，1807 个词会变成 1807 次 API 调用——**测试想不到，
    只有读代码才看得见**。
    """
    worker = _load_worker(monkeypatch)
    words = _words([f"w{index}" for index in range(600)])
    raw_path = _write_raw(tmp_path, words)

    class _CrawlingLLM:
        """每次只往前挪一个词。"""

        max_input_tokens = _tokens_for_chunk(200)

        def __init__(self):
            self.calls = []

        def translate_raw_json(self, prompt, system_prompt=None):
            self.calls.append(prompt)
            first = _prompt_first_index(prompt)
            return json.dumps({"break_after_indices": [first]})

    llm = _CrawlingLLM()
    groups, reason = worker._prepare_sentence_groups(
        transcription_envelope_from_raw(worker, raw_path, language="en"), boundary_cache_path=raw_path.with_name("sentence_boundaries.json"), llm_boundary_backend=llm,
    )

    assert len(llm.calls) <= 4, (
        f"爬行式回答必须被当成坏答案尽早停手，实际调用了 {len(llm.calls)} 次"
    )
    assert reason, "这种答案不能被当成成功"
    assert sum(len(group.words) for group in groups) == len(words), "回退不能丢词"
