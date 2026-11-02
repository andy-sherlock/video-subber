"""G11 施工项 4：交付前比对「决定说什么」与「最终正式交付源稿是什么」。

验收二：正常素材零误开火。
验收三：故障注入必开火——缺这一项，一个根本没接上线的报警器也能取得零开火。
"""
# P464：`run_qwen_asr` 现在返回 QwenAsrOutcome（块 + 完整性状态），
# 不再是裸列表。stub 必须跟上真模块的契约，否则测试就在撒谎。
import sys
from backend.core.qwen_asr_runner import (
    count_chunks_missing_punctuation as _real_count_chunks_missing_punctuation,
)
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


def _decision(language="en"):
    from backend.core.whisper_runner import LanguageDecision, LanguageSample
    return LanguageDecision(
        language=language, locked=True, split=False,
        expected_count=3, valid_count=3, backend="mlx",
        samples=(LanguageSample(0.0, language, {language: 0.9}),))


# 事故 fc441fb713 的真实产物开头：英语音频被产成 66.3% 假名的日语
_ACCIDENT_TEXT = "多くのスウェーデン人は英語をとても流利に話します。でも、私たちは6年生から学びました。"


def test_normal_english_artifact_does_not_fire():
    """验收二：正常素材零误开火。"""
    from backend.transcriber_v2 import check_artifact_language_consistency

    text = ("Most Swedish people speak English very fluently. "
            "But we started learning from sixth grade. Yes, like first grade.")
    assert check_artifact_language_consistency(_decision("en"), text) is None


def test_japanese_artifact_under_a_japanese_decision_does_not_fire():
    """决定就是日语时，假名当然合法——闸门不该开火。"""
    from backend.transcriber_v2 import check_artifact_language_consistency

    assert check_artifact_language_consistency(_decision("ja"), _ACCIDENT_TEXT) is None


def test_fault_injection_the_accident_artifact_must_fire():
    """验收三：人工构造确定矛盾——决定说 en，正式源稿却是假名占优。

    这正是 fc441fb713 的形状。**预期必须开火。**
    """
    from backend.transcriber_v2 import check_artifact_language_consistency

    reason = check_artifact_language_consistency(_decision("en"), _ACCIDENT_TEXT)
    assert reason, "报警器没开火——那它可能根本没接上线"
    assert "假名" in reason, f"要说清是什么矛盾：{reason}"


def test_the_gate_is_bound_to_a_variable_not_a_hardcoded_receipt_field():
    """合同：检查对象写成「当前唯一正式交付源稿」，不得写死 receipt.source_text。

    G12-C 会把权威词流迁移到过滤与局部重跑之后的新词流，
    届时必须能把同一个检查重新绑定到新源稿上。
    """
    import inspect
    from backend.transcriber_v2 import check_artifact_language_consistency

    # 行为测试，不是源码字符串测试：同一个决定喂两份不同的源稿，
    # 结果必须跟着源稿走——这就证明源稿是从参数来的，不是从某个固定位置读的。
    params = list(inspect.signature(check_artifact_language_consistency).parameters)
    assert len(params) == 2, f"源稿必须由调用方传入，实际签名 {params}"

    decision = _decision("en")
    assert check_artifact_language_consistency(decision, _ACCIDENT_TEXT)
    assert check_artifact_language_consistency(
        decision, "This is plain English text long enough to be measured properly.") is None


def test_short_text_does_not_fire():
    """极短文本的字符占比没有统计意义，不得据此拦截。"""
    from backend.transcriber_v2 import check_artifact_language_consistency

    assert check_artifact_language_consistency(_decision("en"), "ok") is None


def test_transcribe_records_the_check_result_on_the_receipt(monkeypatch):
    """验收一：产物一致性检查结果必须留在收据上。"""
    import backend.transcriber_v2 as tv2
    from test.test_g11_transcribe_orchestration import _stub_environment, _locked_en

    events, qwen_calls = [], {}
    _stub_environment(monkeypatch, tv2, events=events, qwen_calls=qwen_calls)
    monkeypatch.setattr(tv2, "sample_language_multipoint", _locked_en(events))

    class _Info:
        language = "en"; language_probability = 0.99; duration = 60.0

    def fake_to_segments(_out, results, _info, n, _cb):
        seg = tv2.Segment(0, 0.0, 1.0, "hello")
        seg.words = [{"text": "hello", "word": "hello", "start": 0.0, "end": 1.0}]
        results.append(seg)
        return n + 1

    monkeypatch.setattr(tv2, "transcribe_whisper",
                        lambda *a, **k: ([], _Info()))
    monkeypatch.setattr(tv2, "_whisper_output_to_segments", fake_to_segments)

    envelope = tv2.transcribe(
        "/tmp/g11.wav", model_size="large-v2", skip_vad=True,
        filter_keywords=True, speaker_detect=False,
        transcription_tier="standard", cache_dir="/tmp/g11cache")

    assert envelope.receipt.artifact_language_check == "ok"


def test_fault_injection_blocks_delivery(monkeypatch):
    """验收三：构造确定矛盾 → 必须拦住交付，不得当成成品端出去。

    ⚠️ 故障注入必须打在**真正交付的那条路**上。
    决定是 en，而 en 路由到 Qwen，所以正式交付源稿来自 Qwen——
    把假名塞进 Whisper 段是打不中的（第一版就是这么错的，闸门没开火）。
    """
    import sys as _sys
    import types
    import pytest
    import backend.transcriber_v2 as tv2
    from test.test_g11_transcribe_orchestration import _stub_environment, _locked_en

    events, qwen_calls = [], {}
    _stub_environment(monkeypatch, tv2, events=events, qwen_calls=qwen_calls)
    monkeypatch.setattr(tv2, "sample_language_multipoint", _locked_en(events))

    # 决定说 en、引擎实收 en，但 Qwen 端产出假名占优的文本——事故 fc441fb713 的形状
    fake_qwen = types.ModuleType("backend.core.qwen_asr_runner")
    # 假模块必须带上真模块被用到的符号，否则 transcriber_v2 的
    # 局部 import 会炸。直接挂真实现，避免假货与真货漂移。
    fake_qwen.count_chunks_missing_punctuation = (
        _real_count_chunks_missing_punctuation)
    fake_qwen.is_qwen_asr_available = lambda: True
    # P463：主引擎路由新增「后端能不能给词级时间」的判据。
    # 本测试测的是 Qwen 当主引擎时的行为，所以这里必须为 True——
    # 假模块缺这个函数会让路由在 import 处就炸。
    fake_qwen.qwen_provides_word_timings = lambda: True
    fake_qwen.run_qwen_asr = lambda audio_path, language=None, **k: _Outcome(complete=True, chunks=[{
        "text": _ACCIDENT_TEXT,
        "t_start": 0.0,
        "t_end": 3.0,
        "segments": [{"text": _ACCIDENT_TEXT, "start": 0.0, "end": 3.0}],
    }])
    monkeypatch.setitem(_sys.modules, "backend.core.qwen_asr_runner", fake_qwen)
    # 组装出来的段必须带词级时间——生产里 `_words_to_segment` 无条件设 `seg.words`。
    # G12 第二批段二起交付词流从 `segment.words` 导出，替身少给词就测不到生产的形状。
    from test.test_g11_transcribe_orchestration import _assembled_segment
    _accident_segment = lambda: _assembled_segment(
        tv2, text=_ACCIDENT_TEXT, start=0.0, end=3.0)
    monkeypatch.setattr(
        tv2, "_qwen_chunks_to_segments", lambda _c, _l: [_accident_segment()])
    monkeypatch.setattr(
        tv2, "_smart_assemble", lambda _raw, **_k: [_accident_segment()])

    seen = {}
    _orig = tv2.check_artifact_language_consistency
    monkeypatch.setattr(
        tv2, "check_artifact_language_consistency",
        lambda d, text: seen.update(text=text) or _orig(d, text))

    with pytest.raises(tv2.ArtifactLanguageMismatch):
        tv2.transcribe(
            "/tmp/g11.wav", model_size="large-v2", skip_vad=True,
            filter_keywords=True, speaker_detect=False,
            transcription_tier="standard", cache_dir="/tmp/g11cache")

    assert "スウェーデン" in seen.get("text", ""), (
        f"闸门查的不是真正交付的那份源稿，实际拿到：{seen.get('text', '')[:40]!r}")
