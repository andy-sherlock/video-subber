"""G11/P444：transcribe() 里语言只准被决定一次。

覆盖 CI-06（决定先于 Qwen 线程 + 按值传参）、CI-07（不保留可重新绑定的语言变量）、
以及第三刀（全量 Whisper 收到空语言就重猜并覆写）。
"""
# P464：`run_qwen_asr` 返回 QwenAsrOutcome（块 + 完整性状态），不再是裸列表。
# stub 必须跟上真模块的契约，否则测试就在撒谎。
import sys
from backend.core.qwen_asr_runner import (
    count_chunks_missing_punctuation as _real_count_chunks_missing_punctuation,
)
import types
from pathlib import Path


def _Outcome(**kw):
    """惰性构造真的 QwenAsrOutcome。

    **不能在顶层 `from backend.core.qwen_asr_runner import ...`**：
    多个测试用 `sys.modules` 把该模块换成假模块，本文件若在那期间被导入
    （例如被别的测试 `from test.xxx import _stub_environment` 拉进来），
    顶层 import 会拿到假模块并炸在 `(unknown location)`。
    `transcriber_v2` 顶层持有真类的引用，不受 sys.modules 替换影响。
    """
    from backend.transcriber_v2 import QwenAsrOutcome
    return QwenAsrOutcome(**kw)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _assembled_segment(transcriber_v2, text="hello", start=0.0, end=1.0):
    """组装出来的段**必须带词级时间**，否则替身与真货漂移。

    生产里 `_smart_assemble` → `_words_to_segment:561` 无条件设 `seg.words`。
    G12 第二批段二起，正式交付词流从 `segment.words` 导出，
    没有词的段会让交付流静默变空——`_assert_delivery_covers_segments` 会拦住它。
    替身少给 `words`，测的就不是生产的形状了。
    """
    seg = transcriber_v2.Segment(0, start, end, text)
    seg.words = [{"text": text, "word": text, "start": start, "end": end}]
    return seg


def _stub_environment(monkeypatch, transcriber_v2, *, events, qwen_calls, ram_gb=32):
    """把 transcribe() 的外部依赖全部换成受控替身，只留语言链路是真的。"""
    fake_hw = types.ModuleType("backend.core.hardware_detector")
    fake_hw.detect_hardware = lambda: {"ram_gb": ram_gb}
    monkeypatch.setitem(sys.modules, "backend.core.hardware_detector", fake_hw)

    fake_qwen = types.ModuleType("backend.core.qwen_asr_runner")
    # 假模块必须带上真模块被用到的符号，否则 transcriber_v2 的
    # 局部 import 会炸。直接挂真实现，避免假货与真货漂移。
    fake_qwen.count_chunks_missing_punctuation = (
        _real_count_chunks_missing_punctuation)
    fake_qwen.is_qwen_asr_available = lambda: True
    # P463：主引擎路由新增「后端能不能给词级时间」的判据。
    # 这个 stub 被多个 G11 编排测试共用，它们的前提都是 Qwen 有主引擎资格。
    fake_qwen.qwen_provides_word_timings = lambda: True

    def fake_run_qwen_asr(audio_path, language=None, cache_dir=None, **_kwargs):
        events.append("qwen_start")
        qwen_calls["language"] = language
        return _Outcome(complete=True, chunks=[{
            "text": "hello",
            "t_start": 0.0,
            "t_end": 1.0,
            "segments": [{"text": "hello", "start": 0.0, "end": 1.0}],
        }])

    fake_qwen.run_qwen_asr = fake_run_qwen_asr
    monkeypatch.setitem(sys.modules, "backend.core.qwen_asr_runner", fake_qwen)

    fake_sv = types.ModuleType("backend.core.sensevoice_runner")
    fake_sv.is_sensevoice_available = lambda: False
    fake_sv.run_sensevoice = lambda *_a, **_k: []
    monkeypatch.setitem(sys.modules, "backend.core.sensevoice_runner", fake_sv)

    monkeypatch.setattr(transcriber_v2, "_vad_speech_gate", lambda *_a, **_k: None)
    monkeypatch.setattr(transcriber_v2, "release_whisper_model", lambda *_a, **_k: None)
    monkeypatch.setattr(
        transcriber_v2, "_qwen_chunks_to_segments",
        lambda _chunks, _lang: [_assembled_segment(transcriber_v2)])
    monkeypatch.setattr(
        transcriber_v2, "_smart_assemble",
        lambda _raw, **_k: [_assembled_segment(transcriber_v2)])
    monkeypatch.setattr(
        transcriber_v2, "merge_short_subtitle_fragments",
        lambda segments, _lang, voice_timeline=None: segments)
    monkeypatch.setattr(
        transcriber_v2, "filter_hallucinations",
        lambda segments, *_a, **_k: segments)
    monkeypatch.setattr(
        transcriber_v2, "_detect_speaker_changes", lambda _p, segments: segments)


def _locked_en(events):
    """受控的多点采样结果：整片英语，已锁定。"""
    from backend.core.whisper_runner import LanguageDecision

    def _fake(*_args, **_kwargs):
        events.append("decision")
        return LanguageDecision(
            language="en", locked=True, split=False,
            expected_count=7, valid_count=7)

    return _fake


def test_qwen_thread_starts_after_the_language_is_decided(monkeypatch):
    """CI-06：精准模式下 Qwen 线程原本在语言产生之前就出发，拿到的是 auto。"""
    import backend.transcriber_v2 as transcriber_v2

    events, qwen_calls = [], {}
    _stub_environment(monkeypatch, transcriber_v2, events=events, qwen_calls=qwen_calls)
    monkeypatch.setattr(transcriber_v2, "should_parallel_asr", lambda: True)
    monkeypatch.setattr(
        transcriber_v2, "sample_language_multipoint", _locked_en(events))

    class _Info:
        language = "en"
        language_probability = 0.99
        duration = 60.0

    def fake_transcribe_whisper(audio_path, model_size=None, language=None,
                                vad_threshold=None):
        events.append(f"whisper:{language}")
        return [], _Info()

    monkeypatch.setattr(transcriber_v2, "transcribe_whisper", fake_transcribe_whisper)

    def fake_to_segments(_out, results, _info, n, _cb):
        seg = transcriber_v2.Segment(0, 0.0, 1.0, "hello")
        seg.words = [{"text": "hello", "word": "hello", "start": 0.0, "end": 1.0}]
        results.append(seg)
        return n + 1

    monkeypatch.setattr(
        transcriber_v2, "_whisper_output_to_segments", fake_to_segments)

    transcriber_v2.transcribe(
        "/tmp/g11.wav", model_size="large-v2", skip_vad=True,
        filter_keywords=True, speaker_detect=False,
        transcription_tier="precise", cache_dir="/tmp/g11cache")

    assert "decision" in events and "qwen_start" in events
    assert events.index("decision") < events.index("qwen_start"), (
        f"语言决定必须先于 Qwen 线程启动，实际顺序：{events}")
    assert qwen_calls["language"] == "en", (
        f"Qwen 必须按值收到已锁定的语言，实际收到 {qwen_calls['language']!r}")


def test_full_whisper_never_re_guesses_the_locked_language(monkeypatch):
    """第三刀：引擎收到空语言就自己重猜，再把已决定的语言覆写掉。"""
    import backend.transcriber_v2 as transcriber_v2

    events, qwen_calls = [], {}
    _stub_environment(monkeypatch, transcriber_v2, events=events, qwen_calls=qwen_calls)
    monkeypatch.setattr(
        transcriber_v2, "sample_language_multipoint", _locked_en(events))

    seen = {}

    class _JaInfo:
        """忠实复刻事故：收到 None 就重猜成 ja。"""
        language = "ja"
        language_probability = 0.36
        duration = 1860.0

    def fake_transcribe_whisper(audio_path, model_size=None, language=None,
                                vad_threshold=None):
        seen["language"] = language
        return [], _JaInfo()

    monkeypatch.setattr(transcriber_v2, "transcribe_whisper", fake_transcribe_whisper)

    def fake_to_segments(_out, results, _info, n, _cb):
        seg = transcriber_v2.Segment(0, 0.0, 1.0, "bonjour")
        seg.words = [{"text": "bonjour", "word": "bonjour", "start": 0.0, "end": 1.0}]
        results.append(seg)
        return n + 1

    monkeypatch.setattr(
        transcriber_v2, "_whisper_output_to_segments", fake_to_segments)
    monkeypatch.setattr(
        transcriber_v2, "_run_sensevoice_and_fa",
        lambda *_a, **_k: ([], None, _a[-1], _a[-1], None))
    captured = {}
    monkeypatch.setattr(
        transcriber_v2, "filter_hallucinations",
        lambda segments, lang=None, *_a, **_k: (
            captured.__setitem__("filter_lang", lang) or segments))

    # fr 不在 QWEN_PRIMARY_LANGS → 走全量 Whisper，正是第三刀的开火路径
    from backend.core.whisper_runner import LanguageDecision
    monkeypatch.setattr(
        transcriber_v2, "sample_language_multipoint",
        lambda *_a, **_k: (events.append("decision") or LanguageDecision(
            language="fr", locked=True, split=False,
            expected_count=7, valid_count=7)))

    transcriber_v2.transcribe(
        "/tmp/g11.wav", model_size="large-v2", skip_vad=True,
        filter_keywords=True, speaker_detect=False,
        transcription_tier="standard", cache_dir="/tmp/g11cache")

    assert seen["language"] == "fr", (
        f"全量 Whisper 必须收到已锁定的语言，实际收到 {seen['language']!r}")
    assert captured.get("filter_lang") == "fr", (
        f"引擎重猜的结果不得覆写已决定的语言，过滤器收到 {captured.get('filter_lang')!r}")


def test_receipt_carries_the_decision_and_what_each_engine_received(monkeypatch):
    """验收「一、正向结构证据」：收据是主证据，日志只是第二份现场证据。"""
    import backend.transcriber_v2 as transcriber_v2

    events, qwen_calls = [], {}
    _stub_environment(monkeypatch, transcriber_v2, events=events, qwen_calls=qwen_calls)
    monkeypatch.setattr(transcriber_v2, "should_parallel_asr", lambda: True)
    monkeypatch.setattr(
        transcriber_v2, "sample_language_multipoint", _locked_en(events))

    class _Info:
        language = "en"
        language_probability = 0.99
        duration = 60.0

    def fake_transcribe_whisper(audio_path, model_size=None, language=None,
                                vad_threshold=None):
        return [], _Info()

    def fake_to_segments(_out, results, _info, n, _cb):
        seg = transcriber_v2.Segment(0, 0.0, 1.0, "hello")
        seg.words = [{"text": "hello", "word": "hello", "start": 0.0, "end": 1.0}]
        results.append(seg)
        return n + 1

    monkeypatch.setattr(transcriber_v2, "transcribe_whisper", fake_transcribe_whisper)
    monkeypatch.setattr(
        transcriber_v2, "_whisper_output_to_segments", fake_to_segments)

    envelope = transcriber_v2.transcribe(
        "/tmp/g11.wav", model_size="large-v2", skip_vad=True,
        filter_keywords=True, speaker_detect=False,
        transcription_tier="precise", cache_dir="/tmp/g11cache")

    receipt = envelope.receipt
    assert receipt.language_decision is not None, "收据必须留下语言决定"
    assert receipt.language_decision.language == "en"
    assert receipt.language_decision.locked is True
    assert receipt.engine_received_language.get("qwen") == "en", (
        f"Qwen 实收语言必须入账，实际 {receipt.engine_received_language}")
    assert receipt.engine_received_language.get("whisper") == "en", (
        f"Whisper 实收语言必须入账，实际 {receipt.engine_received_language}")


def test_a_split_vote_still_delivers_using_the_majority_language(monkeypatch):
    """2026-08-29 方向变更：分票不再中止交付，多数说了算。

    七点里六点英语、一点日语 → 整条链路照常跑完，**每一个引擎都收到 en**。
    这条测的是「不失败」，所以断言必须落在下游真的收到了语言上；
    只断言「没抛异常」的话，一个提前 return 的实现也能过。
    """
    import backend.transcriber_v2 as transcriber_v2
    from backend.core.whisper_runner import LanguageDecision, LanguageSample

    events, qwen_calls = [], {}
    _stub_environment(monkeypatch, transcriber_v2,
                      events=events, qwen_calls=qwen_calls)
    monkeypatch.setattr(transcriber_v2, "should_parallel_asr", lambda: True)

    samples = tuple(
        [LanguageSample(i * 300.0, "en", {"en": 0.9}) for i in range(6)]
        + [LanguageSample(1800.0, "ja", {"ja": 0.8})])
    monkeypatch.setattr(
        transcriber_v2, "sample_language_multipoint",
        lambda *_a, **_k: (events.append("decision") or LanguageDecision(
            language="en", locked=True, split=True,
            expected_count=7, valid_count=7, samples=samples)))

    class _EnInfo:
        language = "en"
        language_probability = 0.95
        duration = 2100.0

    monkeypatch.setattr(
        transcriber_v2, "transcribe_whisper",
        lambda *_a, **_k: ([], _EnInfo()))

    def fake_to_segments(_out, results, _info, n, _cb):
        seg = transcriber_v2.Segment(0, 0.0, 1.0, "hello")
        seg.words = [{"text": "hello", "word": "hello", "start": 0.0, "end": 1.0}]
        results.append(seg)
        return n + 1

    monkeypatch.setattr(
        transcriber_v2, "_whisper_output_to_segments", fake_to_segments)

    envelope = transcriber_v2.transcribe(
        "/tmp/g11split.wav", model_size="large-v2", skip_vad=True,
        filter_keywords=True, speaker_detect=False,
        transcription_tier="precise", cache_dir="/tmp/g11cache")

    assert qwen_calls.get("language") == "en", (
        f"分票时 Qwen 必须收到多数语言，实际 {qwen_calls.get('language')!r}")
    assert envelope.receipt.language_decision.language == "en"
    assert envelope.receipt.language_decision.split is True, (
        "分歧仍要如实记进收据——只是不再阻止交付")
