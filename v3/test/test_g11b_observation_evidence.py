"""G11-B（2026-08-30）：补两个证据缺口，删**三个**会误导的统计字段。

本轮**不改变任何转录行为**：
- Mac 精准模式串行 Qwen **本来就收到了正确语言**，只是没写进收据；
- SenseVoice 仍然只是辅助观察证据，**不得**修改或解锁 LanguageDecision。
"""
import sys
from backend.core.qwen_asr_runner import (
    count_chunks_missing_punctuation as _real_count_chunks_missing_punctuation,
)
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test.test_g11_transcribe_orchestration import _stub_environment  # noqa: E402


def _locked(language="en"):
    from backend.core.whisper_runner import LanguageDecision, LanguageSample
    return LanguageDecision(
        language=language, locked=True, split=False,
        expected_count=7, valid_count=7, backend="mlx",
        samples=tuple(LanguageSample(i * 100.0, language, {language: 0.9})
                      for i in range(7)))


# ══════════════════════════════════════════════════════════════
# 一、Mac 精准模式串行 Qwen：收到什么语言，收据就要写什么语言
# ══════════════════════════════════════════════════════════════

def test_mac_serial_qwen_records_the_language_it_received(monkeypatch):
    """Mac（MLX）精准模式**没有并行线程**，Qwen 走串行兜底路径。

    `should_parallel_asr()` 在 mlx 后端恒为 False——所以串行不是边角情况，
    是每一台 Mac 精准模式的唯一路径。语言本来就传对了，这里测的是**收据有没有记下来**。
    """
    import backend.transcriber_v2 as transcriber_v2

    events, qwen_calls = [], {}
    _stub_environment(monkeypatch, transcriber_v2,
                      events=events, qwen_calls=qwen_calls)
    # 关键：模拟 Mac —— 不并行，Qwen 走串行
    monkeypatch.setattr(transcriber_v2, "should_parallel_asr", lambda: False)
    monkeypatch.setattr(
        transcriber_v2, "sample_language_multipoint", lambda *_a, **_k: _locked("en"))

    class _EnInfo:
        language, language_probability, duration = "en", 0.95, 2100.0
    monkeypatch.setattr(transcriber_v2, "transcribe_whisper",
                        lambda *_a, **_k: ([], _EnInfo()))

    def fake_to_segments(_out, results, _info, n, _cb):
        seg = transcriber_v2.Segment(0, 0.0, 1.0, "hello")
        seg.words = [{"text": "hello", "word": "hello", "start": 0.0, "end": 1.0}]
        results.append(seg)
        return n + 1
    monkeypatch.setattr(transcriber_v2, "_whisper_output_to_segments", fake_to_segments)

    envelope = transcriber_v2.transcribe(
        "/tmp/g11b.wav", model_size="large-v2", skip_vad=True,
        filter_keywords=True, speaker_detect=False,
        transcription_tier="precise", cache_dir="/tmp/g11bcache")

    assert qwen_calls.get("language") == "en", "前提：串行路径本来就该传对语言"
    assert envelope.receipt.engine_received_language.get("qwen") == "en", (
        "串行 Qwen 收到了 en，收据必须记下来，"
        f"实际 {envelope.receipt.engine_received_language}")


# ══════════════════════════════════════════════════════════════
# 二、SenseVoice 分块运行证据：planned / ok / failed / failed_ranges
# ══════════════════════════════════════════════════════════════

def _fake_chunk_env(monkeypatch, chunk_plan, behaviour):
    """把 SenseVoice 分块路径的外部依赖换成受控替身。

    behaviour[idx] = "ok" | "empty" | "boom"
    """
    import backend.core.sensevoice_runner as sv

    fake_qwen = types.ModuleType("backend.core.qwen_asr_runner")
    # 假模块必须带上真模块被用到的符号，否则 transcriber_v2 的
    # 局部 import 会炸。直接挂真实现，避免假货与真货漂移。
    fake_qwen.count_chunks_missing_punctuation = (
        _real_count_chunks_missing_punctuation)
    fake_qwen._chunk_segments = lambda _segs: chunk_plan
    fake_qwen._extract_audio_chunk = lambda *_a, **_k: None
    monkeypatch.setitem(sys.modules, "backend.core.qwen_asr_runner", fake_qwen)

    calls = {"i": -1}

    class _Model:
        def generate(self, **_kw):
            calls["i"] += 1
            what = behaviour[calls["i"]]
            if what == "boom":
                raise RuntimeError("chunk exploded")
            if what == "empty":
                return []
            return [{"text": "<|en|><|NEUTRAL|><|Speech|>hello",
                     "timestamp": [[0, 1000]]}]

    monkeypatch.setattr(sv, "_load_sensevoice_model", lambda _d: _Model())
    monkeypatch.setattr(sv, "extract_tags", lambda _t: {
        "clean_text": "hello", "language": "en",
        "emotion": "NEUTRAL", "event": "Speech"})
    return sv


def test_chunk_stats_record_planned_ok_and_failed_with_ranges(monkeypatch):
    """计划 7 块、成功 6 块、失败 1 块 —— 计数与失败时间范围都要准。"""
    plan = [(0, 0, i * 300.0, (i + 1) * 300.0) for i in range(7)]
    behaviour = ["ok"] * 7
    behaviour[3] = "boom"                       # 第 4 块炸
    sv = _fake_chunk_env(monkeypatch, plan, behaviour)

    stats = {}
    sv._run_sensevoice_chunked("/tmp/a.wav", "cpu", [object()], run_stats=stats)

    assert stats["planned"] == 7, f"计划块数错：{stats}"
    assert stats["ok"] == 6, f"成功块数错：{stats}"
    assert stats["failed"] == 1, f"失败块数错：{stats}"
    assert stats["failed_ranges"] == [[900.0, 1200.0]], (
        f"失败块的时间范围要能指出是哪几分钟：{stats}")


def test_a_chunk_that_succeeds_with_zero_events_is_not_a_failure(monkeypatch):
    """成功处理但一个事件都没返回 —— 算成功，**不得**误记为失败。

    这正是审查人指出的概念混淆：模型可能完整处理了一块音频却只返回很少（甚至零个）
    事件。只看事件数量分不清「处理失败」和「这一段本来就没声音事件」。
    """
    plan = [(0, 0, 0.0, 300.0), (0, 0, 300.0, 600.0)]
    sv = _fake_chunk_env(monkeypatch, plan, ["ok", "empty"])

    stats = {}
    events = sv._run_sensevoice_chunked("/tmp/a.wav", "cpu", [object()], run_stats=stats)

    assert stats["planned"] == 2
    assert stats["ok"] == 2, f"零事件的那一块也处理成功了：{stats}"
    assert stats["failed"] == 0, f"不得把「零事件」记成失败：{stats}"
    assert stats["failed_ranges"] == []
    assert len(events) == 1, "只有第一块产生了事件"


# ══════════════════════════════════════════════════════════════
# 三、删掉三个会误导的统计字段：covered_seconds / coverage_ratio / audio_duration
# ══════════════════════════════════════════════════════════════

def test_misleading_coverage_fields_are_gone():
    """`covered_seconds` / `coverage_ratio` 算的是「事件占了多少时间」，
    却容易被读成「SenseVoice 实际处理了多少音频」。含义不可靠且无人消费，删除。
    """
    from backend.transcriber_v2 import build_sound_observation

    out = build_sound_observation(
        [{"start_s": 0.0, "end_s": 5.0, "tags": {"language": "en"}, "event": "Speech"}],
        run_stats=None)

    for gone in ("covered_seconds", "coverage_ratio", "audio_duration"):
        assert gone not in out, f"三个误导性统计字段必须全部删除：{gone}"
    # 原始事件是真证据，必须保留
    assert out["events"][0]["start_s"] == 0.0
    assert out["events"][0]["end_s"] == 5.0
    assert out["events"][0]["language"] == "en"
    assert out["events"][0]["event"] == "Speech"


def test_old_format_archives_are_rejected_and_retranscribed(monkeypatch):
    """旧格式存档**本来就该失效并重新转录**——这里走真实读取入口证明它。

    上一版这条测试直接调 `_reuse_rejection_reason`，然后声称「旧收据能正常读取」。
    那是假的，错了两层：
      ① 它绕过了真实入口的第一道闸 `set(payload["receipt"]) != _TRANSCRIPTION_RECEIPT_FIELDS`
         ——那是**精确集合比对**，字段少一个就直接返回 None，根本走不到内部检查函数；
      ② 结论也是反的：旧格式存档不是「能正常读取」，是**确定性失效**。

    本项目不为兼容旧缓存修改生产代码。字段集合变了就重新转录，这是合同要求的
    「让新代码自己拒绝旧缓存」，不需要有人手工清缓存。
    """
    from test.test_p390_worker_integration import _load_worker_without_audio_models
    worker = _load_worker_without_audio_models(monkeypatch)
    from test import locked_decision

    # 一份 G11-B 之前的存档：带着当时的三个覆盖统计，且没有 engines_in_text_chain
    old_receipt = {
        "routed_primary": "qwen", "actual_primary": "qwen",
        "word_source": "qwen_word_timestamps",
        "source_text": "hello", "source_words": [],
        "source_fingerprint": "x", "segments_fingerprint": "y",
        "reference_text": "", "reference_usable": False,
        "language_decision": locked_decision().as_payload(),
        "engine_received_language": {"qwen": "en"},
        "artifact_language_check": "ok",
        "transcription_request_fingerprint": "fp",
        "observation_evidence": {"sound": {
            "event_count": 1,
            "events": [{"start_s": 0.0, "end_s": 5.0,
                        "language": "en", "event": "Speech"}],
            "covered_seconds": 5.0, "coverage_ratio": 0.05, "audio_duration": 100.0}},
    }
    payload = {"version": worker._TRANSCRIPTION_ENVELOPE_VERSION,
               "receipt": old_receipt, "segments": []}

    assert worker._read_transcription_envelope(payload, "fp") is None, (
        "旧格式存档必须失效并重新转录——不得为了兼容它修改生产代码")
    # 失效原因是字段集合不符，不是崩溃：确认新字段确实不在旧存档里
    assert "engines_in_text_chain" not in old_receipt
    assert "engines_in_text_chain" in worker._TRANSCRIPTION_RECEIPT_FIELDS




# ══════════════════════════════════════════════════════════════
# 四、缓存复用：缺 Qwen 实收语言的旧存档不得冒充验证通过
# ══════════════════════════════════════════════════════════════

def test_archive_that_used_qwen_without_recording_its_language_is_rejected(monkeypatch):
    """Qwen 出了词流，但收据没记它实际收到什么语言 → 拒绝复用、重新转录。

    这份存档连 `engines_in_text_chain` 都没有（历史格式）。名单缺失时
    **不得默认解释为「没有引擎参与」**，退回可判定的最小事实：主引擎必须有记录。

    不为这种存档开 `unrecorded` 之类的特例：`engine_received_language` 的合法内容
    只能是语言值。放一个非语言值进去，等于让「没有证据的旧缓存」绕过合同。
    """
    from test.test_p390_worker_integration import _load_worker_without_audio_models
    worker = _load_worker_without_audio_models(monkeypatch)
    from test import locked_decision

    payload = {
        "language_decision": locked_decision().as_payload(),
        "engine_received_language": {"whisper": "en"},   # 只有 whisper，没有 qwen
        "actual_primary": "qwen",                        # 但实际主引擎是 qwen
        "word_source": "qwen_word_timestamps",
        "artifact_language_check": "ok",
    }
    assert worker._reuse_rejection_reason(payload, None) == "missing_engine_language_record", (
        "Qwen 出了词流却没记实收语言，必须拒绝复用")


def test_archive_that_recorded_qwen_language_is_accepted(monkeypatch):
    """记全了的存档照常复用——新规则不得误伤合格存档。"""
    from test.test_p390_worker_integration import _load_worker_without_audio_models
    worker = _load_worker_without_audio_models(monkeypatch)
    from test import locked_decision

    payload = {
        "language_decision": locked_decision().as_payload(),
        "engine_received_language": {"qwen": "en"},
        "actual_primary": "qwen",
        "word_source": "qwen_word_timestamps",
        "artifact_language_check": "ok",
    }
    assert worker._reuse_rejection_reason(payload, None) is None


# ══════════════════════════════════════════════════════════════
# 五、红线：SenseVoice 只是观察证据，永远不能当语言裁判
# ══════════════════════════════════════════════════════════════

def test_sensevoice_language_disagreeing_never_changes_the_decision(monkeypatch):
    """SenseVoice 说日语、LanguageDecision 说英语 —— 只准记录，不准改主语言、不准改路由。"""
    import backend.transcriber_v2 as transcriber_v2

    events, qwen_calls = [], {}
    _stub_environment(monkeypatch, transcriber_v2,
                      events=events, qwen_calls=qwen_calls)
    monkeypatch.setattr(transcriber_v2, "should_parallel_asr", lambda: False)
    monkeypatch.setattr(
        transcriber_v2, "sample_language_multipoint", lambda *_a, **_k: _locked("en"))

    # SenseVoice 报的是日语——与决定冲突
    fake_sv = types.ModuleType("backend.core.sensevoice_runner")
    fake_sv.is_sensevoice_available = lambda: True
    fake_sv.run_sensevoice = lambda *_a, **_k: [
        {"start_s": 0.0, "end_s": 5.0, "text": "こんにちは",
         "tags": {"language": "ja", "event": "Speech"}, "event": "Speech"}]
    monkeypatch.setitem(sys.modules, "backend.core.sensevoice_runner", fake_sv)

    class _EnInfo:
        language, language_probability, duration = "en", 0.95, 2100.0
    monkeypatch.setattr(transcriber_v2, "transcribe_whisper",
                        lambda *_a, **_k: ([], _EnInfo()))

    def fake_to_segments(_out, results, _info, n, _cb):
        seg = transcriber_v2.Segment(0, 0.0, 1.0, "hello")
        seg.words = [{"text": "hello", "word": "hello", "start": 0.0, "end": 1.0}]
        results.append(seg)
        return n + 1
    monkeypatch.setattr(transcriber_v2, "_whisper_output_to_segments", fake_to_segments)

    envelope = transcriber_v2.transcribe(
        "/tmp/g11b2.wav", model_size="large-v2", skip_vad=True,
        filter_keywords=True, speaker_detect=False,
        transcription_tier="precise", cache_dir="/tmp/g11bcache2")

    assert envelope.receipt.language_decision.language == "en", "主语言不得被 SenseVoice 改写"
    assert qwen_calls.get("language") == "en", "路由不得被 SenseVoice 改写"
    sound = envelope.receipt.observation_evidence.get("sound") or {}
    langs = [e["language"] for e in sound.get("events", [])]
    assert "ja" in langs, f"冲突的观察结果仍要如实留证：{sound}"


# ══════════════════════════════════════════════════════════════
# 六、SenseVoice 运行数字必须对得上账（2026-08-30 第二轮外审）
# ══════════════════════════════════════════════════════════════

def _fake_whole_env(monkeypatch, *, model_ok=True, generate=None):
    """整段（不分块）路径的受控环境。"""
    import backend.core.sensevoice_runner as sv
    monkeypatch.setattr(sv, "_load_sensevoice_model",
                        lambda _d: (object() if model_ok else None))
    monkeypatch.setattr(sv, "_sensevoice_generate", lambda *_a, **_k: generate)
    monkeypatch.setattr(sv, "_parse_sensevoice_results",
                        lambda _r: [{"start_s": 0.0, "end_s": 1.0,
                                     "tags": {"language": "en"}, "event": "Speech"}])
    return sv


def test_whole_path_success_counts_as_one_ok():
    """整段处理成功 → planned=1 / ok=1 / failed=0。

    这笔账必须对得上：计划了一块，那一块要么成功要么失败，
    不能既不算成功也不算失败地凭空消失。
    """
    import pytest
    with pytest.MonkeyPatch.context() as mp:
        sv = _fake_whole_env(mp, generate=[{"text": "x"}])
        stats = {}
        sv.run_sensevoice("/tmp/a.wav", run_stats=stats)
    assert (stats["planned"], stats["ok"], stats["failed"]) == (1, 1, 0), stats


def test_whole_path_generate_failure_counts_as_one_failed():
    """整段处理失败（generate 返回 None）→ planned=1 / ok=0 / failed=1。"""
    import pytest
    with pytest.MonkeyPatch.context() as mp:
        sv = _fake_whole_env(mp, generate=None)
        stats = {}
        assert sv.run_sensevoice("/tmp/a.wav", run_stats=stats) is None
    assert (stats["planned"], stats["ok"], stats["failed"]) == (1, 0, 1), stats


def test_whole_path_model_load_failure_counts_as_one_failed():
    """整段路径模型加载失败 → 同样要记成失败，不得留下 ok=0/failed=0。"""
    import pytest
    with pytest.MonkeyPatch.context() as mp:
        sv = _fake_whole_env(mp, model_ok=False)
        stats = {}
        assert sv.run_sensevoice("/tmp/a.wav", run_stats=stats) is None
    assert (stats["planned"], stats["ok"], stats["failed"]) == (1, 0, 1), stats


def test_chunked_model_load_failure_marks_every_planned_chunk_failed(monkeypatch):
    """分块前模型就加载失败 → 计划的每一块都算失败，不能「计划了 7 块但零成功零失败」。"""
    plan = [(0, 0, i * 300.0, (i + 1) * 300.0) for i in range(7)]
    sv = _fake_chunk_env(monkeypatch, plan, ["ok"] * 7)
    monkeypatch.setattr(sv, "_load_sensevoice_model", lambda _d: None)

    stats = {}
    assert sv._run_sensevoice_chunked("/tmp/a.wav", "cpu", [object()],
                                      run_stats=stats) is None
    assert stats["planned"] == 7
    assert stats["ok"] == 0
    assert stats["failed"] == 7, f"计划的块不能凭空消失：{stats}"
    assert len(stats["failed_ranges"]) == 7


# ══════════════════════════════════════════════════════════════
# 七、缓存拒绝要按「本次实际有哪些引擎参与了正式文字链路」判断
# ══════════════════════════════════════════════════════════════

def test_qwen_in_the_text_chain_without_a_language_record_is_rejected(monkeypatch):
    """主稿来自 Whisper，但 Qwen 的文字也进了正式链路，收据却没记它的实收语言 → 拒绝复用。

    精准模式下 Qwen **无条件运行**（`transcription_tier == "precise" and filter_keywords`，
    没有语言条件）。它不是主引擎，但它的文字会进五层过滤、影响最终字幕。
    只看 `actual_primary` 会漏掉这一整类。
    """
    from test.test_p390_worker_integration import _load_worker_without_audio_models
    worker = _load_worker_without_audio_models(monkeypatch)
    from test import locked_decision

    payload = {
        "language_decision": locked_decision().as_payload(),
        "engine_received_language": {"whisper": "en"},
        "engines_in_text_chain": ["whisper", "qwen"],   # Qwen 参与了
        "actual_primary": "whisper",
        "word_source": "whisper_word_timestamps",
        "artifact_language_check": "ok",
    }
    assert worker._reuse_rejection_reason(payload, None) == "missing_engine_language_record", (
        "参与正式文字链路的引擎缺实收语言，必须拒绝复用")


def test_a_job_that_never_ran_qwen_is_not_rejected(monkeypatch):
    """根本没运行 Qwen 的任务，不得因为「没有 Qwen 字段」被误拒。

    判据是**本次实际有哪些引擎参与**，不是「所有存档都必须有 Qwen」。
    """
    from test.test_p390_worker_integration import _load_worker_without_audio_models
    worker = _load_worker_without_audio_models(monkeypatch)
    from test import locked_decision

    payload = {
        "language_decision": locked_decision().as_payload(),
        "engine_received_language": {"whisper": "en"},
        "engines_in_text_chain": ["whisper"],           # 只有 Whisper 跑过
        "actual_primary": "whisper",
        "word_source": "whisper_word_timestamps",
        "artifact_language_check": "ok",
    }
    assert worker._reuse_rejection_reason(payload, None) is None, (
        "没跑 Qwen 的任务不该因缺 Qwen 字段被拒")


def test_receipt_records_which_engines_entered_the_text_chain(monkeypatch):
    """收据必须记下本次哪些引擎的文字进了正式链路——这是缓存检查的唯一依据。"""
    import backend.transcriber_v2 as transcriber_v2

    events, qwen_calls = [], {}
    _stub_environment(monkeypatch, transcriber_v2,
                      events=events, qwen_calls=qwen_calls)
    monkeypatch.setattr(transcriber_v2, "should_parallel_asr", lambda: False)
    monkeypatch.setattr(
        transcriber_v2, "sample_language_multipoint", lambda *_a, **_k: _locked("en"))

    class _EnInfo:
        language, language_probability, duration = "en", 0.95, 2100.0
    monkeypatch.setattr(transcriber_v2, "transcribe_whisper",
                        lambda *_a, **_k: ([], _EnInfo()))

    def fake_to_segments(_out, results, _info, n, _cb):
        seg = transcriber_v2.Segment(0, 0.0, 1.0, "hello")
        seg.words = [{"text": "hello", "word": "hello", "start": 0.0, "end": 1.0}]
        results.append(seg)
        return n + 1
    monkeypatch.setattr(transcriber_v2, "_whisper_output_to_segments", fake_to_segments)

    envelope = transcriber_v2.transcribe(
        "/tmp/g11b3.wav", model_size="large-v2", skip_vad=True,
        filter_keywords=True, speaker_detect=False,
        transcription_tier="precise", cache_dir="/tmp/g11bcache3")

    chain = set(envelope.receipt.engines_in_text_chain)
    assert "qwen" in chain, f"精准模式 Qwen 跑了，必须记进参与名单：{chain}"


# ══════════════════════════════════════════════════════════════
# 八、三个误导字段全部删除（第二轮外审正式批准 audio_duration）
# ══════════════════════════════════════════════════════════════

def test_all_three_misleading_fields_are_gone():
    """`covered_seconds` / `coverage_ratio` / `audio_duration` 三个都不得再出现。

    `audio_duration` 记的不是音频真实长度，而是最后一条字幕的结束时间；
    删掉两个比率之后它也没有剩余用途——三个字段一并删除。
    """
    from backend.transcriber_v2 import build_sound_observation

    out = build_sound_observation(
        [{"start_s": 0.0, "end_s": 5.0, "tags": {"language": "en"}, "event": "Speech"}])
    for gone in ("covered_seconds", "coverage_ratio", "audio_duration"):
        assert gone not in out, f"误导性字段必须删除：{gone}"
