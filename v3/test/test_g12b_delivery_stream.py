"""G12 第二批段二：过滤后的词流成为唯一正式交付源。

**要修的缺陷**：五层过滤器可以把一段标成已删除，但最终字幕是从**过滤之前**的
原始词流重新拼的，于是被删的内容在用户成品里原样复活。

真实证据（任务 `1a1b9f8145`，47 分钟）：
  `debug.log:999`   L5 去重: start=13.89 text='Oh'      ← 过滤器删了
  `transcript.json` 31 秒前只剩一段（id=0）              ← 删除确实生效
  成品 SRT 第 2 条   00:13,886 → 00:20,886 哦，          ← 🔴 原样复活

**根因**：正式收据在过滤**之前**就算好了（`_resolve_routing`），过滤在 Phase R
才跑，只有 `dual_failed` 分支会重算——那个分支正是本段要照抄的正确模板。

**为什么守恒检查是硬崩不是静默**：`TranscriptionReceipt.__post_init__` 比较
`source_text` 与 `source_words` 的字母数字，对不上直接抛
`ValueError("主引擎收据原词不守恒")`。所以这两个字段**必须在同一次改动里
从同一份过滤后数据导出**，没有中间态可以试探。

Run: cd v3 && .venv/bin/python -m pytest test/test_g12b_delivery_stream.py -v
"""
# P464：`run_qwen_asr` 现在返回 QwenAsrOutcome（块 + 完整性状态），
# 不再是裸列表。stub 必须跟上真模块的契约，否则测试就在撒谎。
def _Outcome(**kw):
    """惰性构造真的 QwenAsrOutcome。

    **不能在顶层 `from backend.core.qwen_asr_runner import ...`**：
    多个测试用 `sys.modules` 把该模块换成假模块；本文件若在那期间被导入
    （例如被别的测试 `from test.xxx import _stub_environment` 拉进来），
    顶层 import 会拿到假模块并炸在 `(unknown location)`。
    `transcriber_v2` 顶层持有真类的引用，不受 sys.modules 替换影响。
    """
    from backend.transcriber_v2 import QwenAsrOutcome
    return QwenAsrOutcome(**kw)
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.qwen_asr_runner import (
    count_chunks_missing_punctuation as _real_count_chunks_missing_punctuation,
)


# 三个词 → 三段。过滤器会删掉中间那段，模拟 L4/L3 的结构性删除。
_WORDS = [
    {"text": "alpha", "start": 0.0, "end": 1.0},
    {"text": "DELETED", "start": 1.0, "end": 2.0},
    {"text": "omega", "start": 2.0, "end": 3.0},
]


def _make_segment(transcriber_v2, index):
    word = _WORDS[index]
    seg = transcriber_v2.Segment(index, word["start"], word["end"], word["text"])
    seg.words = [dict(word)]
    return seg


def _stub_environment(monkeypatch, transcriber_v2, *, drop_middle):
    """只留「过滤 → 收据」这一段是真的，其余全部换成受控替身。"""
    fake_hw = types.ModuleType("backend.core.hardware_detector")
    fake_hw.detect_hardware = lambda: {"ram_gb": 32}
    monkeypatch.setitem(sys.modules, "backend.core.hardware_detector", fake_hw)

    fake_qwen = types.ModuleType("backend.core.qwen_asr_runner")
    fake_qwen.count_chunks_missing_punctuation = _real_count_chunks_missing_punctuation
    fake_qwen.is_qwen_asr_available = lambda: True
    fake_qwen.run_qwen_asr = lambda *_a, **_k: _Outcome(complete=True, chunks=[{
        "text": " ".join(w["text"] for w in _WORDS),
        "t_start": 0.0,
        "t_end": 3.0,
        "segments": [dict(w) for w in _WORDS],
    }])
    monkeypatch.setitem(sys.modules, "backend.core.qwen_asr_runner", fake_qwen)

    fake_sv = types.ModuleType("backend.core.sensevoice_runner")
    fake_sv.is_sensevoice_available = lambda: False
    fake_sv.run_sensevoice = lambda *_a, **_k: []
    monkeypatch.setitem(sys.modules, "backend.core.sensevoice_runner", fake_sv)

    monkeypatch.setattr(transcriber_v2, "_vad_speech_gate", lambda *_a, **_k: None)
    monkeypatch.setattr(transcriber_v2, "release_whisper_model", lambda *_a, **_k: None)
    monkeypatch.setattr(transcriber_v2, "should_parallel_asr", lambda: True)
    monkeypatch.setattr(
        transcriber_v2, "_smart_assemble",
        lambda *_a, **_k: [_make_segment(transcriber_v2, i) for i in range(3)])
    monkeypatch.setattr(
        transcriber_v2, "merge_short_subtitle_fragments",
        lambda segments, _lang, voice_timeline=None: segments)
    monkeypatch.setattr(
        transcriber_v2, "_detect_speaker_changes", lambda _p, segments: segments)

    def fake_filter(segments, *_a, **_k):
        """模拟过滤器删掉中间那段（结构性删除，白名单内）。"""
        if not drop_middle:
            return segments
        kept = [s for s in segments if s.text != "DELETED"]
        for seg in segments:
            if seg.text == "DELETED":
                seg.filtered = True
                seg.filter_reason = "layer3_impossible_duration"
        for index, seg in enumerate(kept):
            seg.id = index
        return kept

    monkeypatch.setattr(transcriber_v2, "filter_hallucinations", fake_filter)

    from backend.core.whisper_runner import LanguageDecision
    monkeypatch.setattr(
        transcriber_v2, "sample_language_multipoint",
        lambda *_a, **_k: LanguageDecision(
            language="en", locked=True, split=False,
            expected_count=7, valid_count=7))

    class _Info:
        language = "en"
        language_probability = 0.99
        duration = 60.0

    monkeypatch.setattr(
        transcriber_v2, "transcribe_whisper",
        lambda *_a, **_k: ([], _Info()))
    monkeypatch.setattr(
        transcriber_v2, "_whisper_output_to_segments",
        lambda _out, _results, _info, n, _cb: n)


def _run(transcriber_v2, tmp_path):
    return transcriber_v2.transcribe(
        str(tmp_path / "g12b.wav"), model_size="large-v2", skip_vad=True,
        filter_keywords=True, speaker_detect=False,
        transcription_tier="precise", cache_dir=str(tmp_path / "cache"))


# ══════════════════════════════════════════════════════════════════
# R3（回归）· 被过滤器删掉的文字不得出现在正式交付词流里
# ══════════════════════════════════════════════════════════════════

def test_filtered_text_is_absent_from_the_delivery_word_stream(monkeypatch, tmp_path):
    """这是「第二条哦复活」的最小复现：删了，但收据里还在。"""
    import backend.transcriber_v2 as transcriber_v2

    _stub_environment(monkeypatch, transcriber_v2, drop_middle=True)
    receipt = _run(transcriber_v2, tmp_path).receipt

    self_texts = [word["text"] for word in receipt.source_words]
    assert "DELETED" not in self_texts, (
        f"过滤器删掉的词仍在正式词流里：{self_texts}——这正是成品里那条「哦」复活的机制")
    assert self_texts == ["alpha", "omega"]


def test_filtered_text_is_absent_from_the_delivery_full_text(monkeypatch, tmp_path):
    """`source_text` 与 `source_words` 必须同源；只改一个会让守恒检查开火。"""
    import backend.transcriber_v2 as transcriber_v2

    _stub_environment(monkeypatch, transcriber_v2, drop_middle=True)
    receipt = _run(transcriber_v2, tmp_path).receipt

    assert "DELETED" not in receipt.source_text, (
        f"过滤器删掉的文字仍在正式正文里：{receipt.source_text!r}")


# ══════════════════════════════════════════════════════════════════
# R9 · 守恒检查在删段之后仍然成立
# ══════════════════════════════════════════════════════════════════

def test_conservation_invariant_still_holds_after_deletion(monkeypatch, tmp_path):
    """守恒是硬崩：`source_text` 与 `source_words` 对不上直接抛 ValueError。

    这条测试的价值不在「没抛异常」——它在断言**两者真的同源**：
    正文的字母数字必须与逐词拼起来完全一致。
    """
    import backend.transcriber_v2 as transcriber_v2
    import unicodedata

    _stub_environment(monkeypatch, transcriber_v2, drop_middle=True)
    receipt = _run(transcriber_v2, tmp_path).receipt

    def _alnum(text):
        return "".join(
            ch.casefold()
            for ch in unicodedata.normalize("NFKC", text)
            if unicodedata.category(ch)[0] in {"L", "N"}
        )

    assert _alnum(receipt.source_text) == _alnum(
        "".join(w["text"] for w in receipt.source_words))


def test_nothing_is_dropped_when_the_filter_deletes_nothing(monkeypatch, tmp_path):
    """反向守卫：过滤器什么都没删时，交付流必须一个词不少。

    没有这一条，一个「无条件清空再重建」的实现也能让上面几条变绿。
    """
    import backend.transcriber_v2 as transcriber_v2

    _stub_environment(monkeypatch, transcriber_v2, drop_middle=False)
    receipt = _run(transcriber_v2, tmp_path).receipt

    assert [w["text"] for w in receipt.source_words] == [
        "alpha", "DELETED", "omega"]


# ══════════════════════════════════════════════════════════════════
# B1 · 交付流导出函数必须语义诚实
# ══════════════════════════════════════════════════════════════════

def test_delivery_source_text_helper_exists():
    """不许复用 `_primary_source_text` 的位置参数把戏。

    现有调用 `_primary_source_text("dual_failed", [], results, lang)` 把过滤后的
    通用段列表塞进名为 `whisper_segments` 的形参，还谎报主引擎是 dual_failed。
    能跑通，但它在撒谎——下一个人读代码会以为主引擎真的是 dual_failed。
    """
    import backend.transcriber_v2 as transcriber_v2

    assert hasattr(transcriber_v2, "_delivery_source_text"), (
        "缺少语义诚实的交付流导出函数")


def test_delivery_source_text_joins_by_language():
    import backend.transcriber_v2 as transcriber_v2

    segments = [_make_segment(transcriber_v2, i) for i in (0, 2)]

    assert transcriber_v2._delivery_source_text(segments, "en") == "alpha omega"
    assert transcriber_v2._delivery_source_text(segments, "zh") == "alphaomega"


# ══════════════════════════════════════════════════════════════════
# 交付流不得静默变空（施工中发现并补上的守卫）
# ══════════════════════════════════════════════════════════════════

def test_all_segments_without_words_fail_loudly():
    """所有段都没有 `words` → 交付流静默变空，必须响亮失败。

    **这个洞是段二自己挖出来的**：`_delivery_source_text` 与
    `_flatten_primary_segment_words` 都只读 `segment.words`，两者会一起变空，
    于是收据的守恒检查平凡通过（空 == 空），整份转录静默清零。

    生产里确实可达：`_whisper_output_to_segments:1248` 是条件赋值
    （`if _words is not None`），Whisper 不给词级时间戳时段上就没有 `words`。
    """
    import pytest
    import backend.transcriber_v2 as transcriber_v2

    naked = transcriber_v2.Segment(0, 0.0, 1.0, "hello world")

    with pytest.raises(transcriber_v2.DeliveryStreamContamination):
        transcriber_v2._assert_delivery_not_silently_empty([naked], [])


def test_text_not_backed_by_words_does_not_fire_the_gate():
    """闸门 2 真跑撤回：段文字多于词流**不是**故障，不许拦停。

    两条真实机制会造出这种段：
      1. `_assemble_qwen_words_on_whisper_boundaries:1134-1136`——某个 Whisper 段
         没有任何 Qwen 词映射过来时，拿 Whisper 的文字造一个不带词的段；
      2. `merge_short_subtitle_fragments:721-726`——把那种段并进相邻的带词段，
         **文字接上了，词一个没加**。

    那些 Whisper 兜底文字从来就不进交付流（成品由 Qwen 词流断句而来）。
    第一版闸门查了「逐字相符」，把真实的 30 分钟任务整条拦停
    （实测 23652 vs 23645，差 7 个字母数字），已撤回。
    """
    import backend.transcriber_v2 as transcriber_v2

    merged = transcriber_v2.Segment(0, 0.0, 2.0, "alpha whisper only text")
    merged.words = [{"text": "alpha", "start": 0.0, "end": 1.0}]

    transcriber_v2._assert_delivery_not_silently_empty([merged], merged.words)


def test_non_empty_delivery_passes():
    """反向守卫：词流非空时不许误报。"""
    import backend.transcriber_v2 as transcriber_v2

    seg = transcriber_v2.Segment(0, 0.0, 1.0, "hello world")
    seg.words = [
        {"text": "hello", "start": 0.0, "end": 0.5},
        {"text": "world", "start": 0.5, "end": 1.0},
    ]
    transcriber_v2._assert_delivery_not_silently_empty([seg], seg.words)


def test_resurrected_word_is_caught_by_the_gate():
    """裁决为删除的词若混进交付流，闸门必须开火。

    比对的是**词身份**（文字 + 起始时间），不是文字本身——
    同一个词在别处合法出现是常态，只有同词同时间才叫复活。
    """
    import pytest
    import backend.transcriber_v2 as transcriber_v2

    dropped = transcriber_v2.Segment(1, 13.886, 21.806, "Oh")
    dropped.words = [{"text": "Oh", "start": 13.886, "end": 21.806}]
    dropped.filtered = True
    dropped.filter_reason = "layer5_dedup"

    with pytest.raises(transcriber_v2.DeliveryStreamContamination):
        transcriber_v2._assert_dropped_text_absent(
            [dropped], [{"text": "Oh", "start": 13.886, "end": 21.806}])


def test_same_word_at_a_different_time_is_not_a_resurrection():
    import backend.transcriber_v2 as transcriber_v2

    dropped = transcriber_v2.Segment(1, 13.886, 21.806, "Oh")
    dropped.words = [{"text": "Oh", "start": 13.886, "end": 21.806}]
    dropped.filtered = True

    transcriber_v2._assert_dropped_text_absent(
        [dropped], [{"text": "Oh", "start": 900.0, "end": 900.5}])


# ══════════════════════════════════════════════════════════════════
# B4 · 转录合同版本不许再提高
# ══════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════
# B5/B6 · 断点缓存必须因交付词流改变而失效
# ══════════════════════════════════════════════════════════════════
#
# **为什么这条不能只靠推理**：`worker.py:823` 的断点缓存命中分支
# **只在用户跑到一半关掉 APP、第二天续跑时才走**，开发路径永远碰不到。
# 如果指纹没能拦住旧断点，用户拿到的字幕是**用过滤前的旧词流拼的**
# ——那两条「哦」原样回来，被误删的真话还是没了。
# 五个提交修好的东西，在一条谁也没测过的路径上静默复活。
#
# `_p390_read_boundary_cache` 的「指纹不符就拒绝」已由
# `test_p390_worker_integration.py::test_worker_rejects_damaged_source_mismatched_and_group_mismatched_cache`
# 覆盖（它直接篡改 source_fingerprint）。**本节补的是链条中间那一环**：
# 过滤器删掉内容之后，收据的指纹到底变不变。


def _receipt_with_words(words):
    from backend.transcriber_v2 import TranscriptionReceipt
    from backend.core.whisper_runner import LanguageDecision

    return TranscriptionReceipt(
        routed_primary="qwen",
        actual_primary="qwen",
        word_source="qwen_word_timestamps",
        source_text=" ".join(w["text"] for w in words),
        source_words=[dict(w) for w in words],
        language_decision=LanguageDecision(
            language="en", locked=True, split=False,
            expected_count=7, valid_count=7),
        engine_received_language={"qwen": "en"},
        artifact_language_check="ok",
    )


def test_receipt_fingerprint_changes_when_the_filter_removes_a_word():
    """链条中间那一环：交付词流少一个词，指纹就必须变。

    指纹不变 → 旧断点缓存被判为「身份相符」→ 静默复用 → 整批工作失效。
    """
    full = [
        {"text": "alpha", "start": 0.0, "end": 1.0},
        {"text": "DELETED", "start": 1.0, "end": 2.0},
        {"text": "omega", "start": 2.0, "end": 3.0},
    ]
    filtered = [full[0], full[2]]

    before = _receipt_with_words(full).source_fingerprint
    after = _receipt_with_words(filtered).source_fingerprint

    assert before and after
    assert before != after, (
        "过滤器删掉一个词，收据指纹却没变——旧断点缓存会被静默复用")


def test_resumed_run_rejects_the_boundary_cache_written_before_filtering(
        monkeypatch, tmp_path):
    """端到端续跑：先用过滤**前**的词流写缓存，再用过滤**后**的词流跑，必须被拒绝。

    这是真实的用户场景——跑到一半关掉 APP，第二天接着跑。
    """
    import importlib
    import backend.worker as worker_module

    worker = importlib.reload(worker_module)

    class _LLM:
        def __init__(self, response):
            self.response = response
            self.calls = []

        def translate_raw_json(self, prompt, system_prompt=None):
            self.calls.append(prompt)
            return self.response

    full = [
        {"word": t, "text": t, "start": round(i * 0.4, 3),
         "end": round(i * 0.4 + 0.3, 3)}
        for i, t in enumerate(
            ["We", "found", "a", "hidden", "gem.", "It", "was", "great."])
    ]
    # 模拟过滤器摘掉 "hidden"（结构性删除后交付流少一个词）
    filtered = [w for w in full if w["text"] != "hidden"]
    cache_path = tmp_path / "sentence_boundaries.json"

    first = _LLM('{"break_after_indices": [4]}')
    worker._prepare_sentence_groups(
        worker.TranscriptionEnvelope([], _receipt_with_words(full)),
        boundary_cache_path=str(cache_path),
        llm_boundary_backend=first,
    )
    assert len(first.calls) == 1, "第一趟应当真的问过 LLM"
    assert cache_path.exists(), "第一趟应当写下断点缓存"

    second = _LLM('{"break_after_indices": [3]}')
    worker._prepare_sentence_groups(
        worker.TranscriptionEnvelope([], _receipt_with_words(filtered)),
        boundary_cache_path=str(cache_path),
        llm_boundary_backend=second,
    )

    assert len(second.calls) == 1, (
        "交付词流已改变，旧断点缓存却被复用——用户续跑会拿到过滤前的字幕")


def test_resumed_run_still_reuses_the_cache_when_nothing_changed(
        monkeypatch, tmp_path):
    """反向守卫：交付词流没变时缓存仍要复用，否则每次续跑都白花一次 LLM。

    没有这一条，一个「永远拒绝缓存」的实现也能让上面那条变绿。
    """
    import importlib
    import backend.worker as worker_module

    worker = importlib.reload(worker_module)

    class _LLM:
        def __init__(self, response):
            self.response = response
            self.calls = []

        def translate_raw_json(self, prompt, system_prompt=None):
            self.calls.append(prompt)
            return self.response

    words = [
        {"word": t, "text": t, "start": round(i * 0.4, 3),
         "end": round(i * 0.4 + 0.3, 3)}
        for i, t in enumerate(
            ["We", "found", "a", "hidden", "gem.", "It", "was", "great."])
    ]
    cache_path = tmp_path / "sentence_boundaries.json"

    first = _LLM('{"break_after_indices": [4]}')
    worker._prepare_sentence_groups(
        worker.TranscriptionEnvelope([], _receipt_with_words(words)),
        boundary_cache_path=str(cache_path),
        llm_boundary_backend=first,
    )
    assert len(first.calls) == 1

    second = _LLM('{"break_after_indices": [4]}')
    worker._prepare_sentence_groups(
        worker.TranscriptionEnvelope([], _receipt_with_words(words)),
        boundary_cache_path=str(cache_path),
        llm_boundary_backend=second,
    )

    assert second.calls == [], "词流没变却重新问了 LLM，断点缓存白写了"


def test_transcription_contract_version_bumped_once_per_batch():
    """**每批只准提高一次**——多提一次会白白作废全体旧缓存。

    第一批 1→2（挂标点改变正式词流）。
    第三批 A 2→3（P463 改主引擎路由 + P465 改接缝词流，两条触发条件都命中）。

    这个守护测试的作用是**逼人有意识地改它**：
    提高版本会把执行推进那条绕过挂标点的全缓存命中分支，
    所以每次变更都必须在这里留下一行理由。
    """
    import backend.worker as worker

    assert worker._TRANSCRIPTION_CONTRACT_VERSION == 3
