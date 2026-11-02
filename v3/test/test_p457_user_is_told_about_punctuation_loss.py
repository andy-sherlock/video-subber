"""P457：挂标点失败必须告知用户，不能只写一行日志。

真实任务里这个警告打了 20 次，三个月没有任何人发现——**只写日志等于没说**。
产品裁决（2026-09-02）：真正文字对不上时，日志 + 界面告知用户。

照 `_p395_transcribe_message` 的既有先例：「回退必须**带原因**。
只说『已回退』用户不知道能做什么」。
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
from backend import worker


def test_message_is_unchanged_when_nothing_failed():
    assert worker._p395_transcribe_message(895, None, 0) == "语音识别完成: 共 895 句"


def test_message_tells_the_user_when_punctuation_was_lost():
    message = worker._p395_transcribe_message(895, None, 3)

    assert "895" in message
    assert "3" in message
    assert "标点" in message


def test_punctuation_notice_coexists_with_the_boundary_fallback_reason():
    """两种降级可以同时发生，用户必须两条都看得到。"""
    message = worker._p395_transcribe_message(895, "DeepSeek 不可用", 2)

    assert "DeepSeek 不可用" in message
    assert "标点" in message


def test_receipt_carries_the_missing_punctuation_count():
    """收据里必须带这个数——验收观察点就是收据，不是诊断文件。"""
    from backend.transcriber_v2 import TranscriptionReceipt

    receipt = TranscriptionReceipt(
        routed_primary="qwen", actual_primary="qwen", word_source="qwen_word_timestamps",
        source_text="hi", source_words=[{"text": "hi", "start": 0.0, "end": 0.4}],
        observation_evidence={"punctuation": {"missing_chunks": 2}},
    )

    assert receipt.observation_evidence["punctuation"]["missing_chunks"] == 2


def test_worker_call_site_passes_the_count_to_the_message():
    """源码断言：真正的用户可见调用点必须把这个数传进去。

    只把参数加进函数签名、调用点不传，等于没做——这条守的就是那个漏法。
    """
    import ast
    from pathlib import Path

    # ⑦.5：原来按字符偏移切 300 字，两个方向都会误判——
    # 把计数提成局部变量是正确重构却会误报；文件里多一处同名调用就切错地方。
    # 改用 AST：找真正的调用节点，断言它传了三个实参。
    tree = ast.parse(Path(worker.__file__).read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name)
             and n.func.id == "_p395_transcribe_message"]

    assert calls, "worker 里找不到用户可见文案的调用点"
    assert all(len(c.args) == 3 for c in calls), (
        "调用点没有把缺标点段数传进去（实参不足 3 个）")


def test_short_audio_path_also_records_punctuation_failure(monkeypatch):
    """短音频（<20 分钟，绝大多数视频）也必须记账。

    ⑦.5 测试专家发现：P457 只在长音频分块路径上落地。短音频路径直接调
    `_attach_full_text_punctuation`，返回的块没有 `punctuation_attached` 字段，
    而 `count_chunks_missing_punctuation` 把「没这个字段」当成「挂上了」——
    **这个新功能在最常见的场景下永远不会开火。**
    """
    from backend.core import qwen_asr_runner as Q

    monkeypatch.setattr(Q, "_load_qwen_model", lambda: object())
    # 文字真对不上（不是词内标点）→ 挂标点必然失败
    monkeypatch.setattr(
        Q, "_qwen_transcribe",
        lambda *a, **k: ("completely different words here.",
                         [{"text": "out", "start": 0.0, "end": 0.4},
                          {"text": "of", "start": 0.5, "end": 0.9}]))
    monkeypatch.setattr(Q, "_write_qwen_raw_diagnostic", lambda *a, **k: None)
    monkeypatch.setattr(Q, "_resolve_qwen_duration", lambda *a, **k: 100.0)
    monkeypatch.setattr(Q, "_USE_MLX", True)

    out = Q.run_qwen_asr("/nonexistent.wav", segments=None, language="en")

    assert out.chunks, "短音频路径应当仍然交出块"  # P464：返回 QwenAsrOutcome，块在 .chunks 上
    assert out.chunks[0].get("punctuation_attached") is False
    assert Q.count_chunks_missing_punctuation(out.chunks) == 1


# ── 补：真实集成，不再是同义反复 ──────────────────────────────

def test_real_transcribe_writes_missing_punctuation_into_the_receipt(monkeypatch):
    """走**真实** `transcribe()`，让 Qwen 交回一个「没挂上标点」的块，
    断言收据里 `observation_evidence.punctuation.missing_chunks` 真的是 1。

    ⑦.5 测试专家发现原来那条测试是同义反复：手工 new 一个 TranscriptionReceipt
    自己塞进去自己读出来，从头到尾没调生产代码。它做了变异验证——
    把真实计数强制改成 999，全量测试无一失败。
    """
    import sys, types
    import backend.transcriber_v2 as tv2
    from test.test_g11_transcribe_orchestration import _stub_environment, _locked_en

    events, qwen_calls = [], {}
    _stub_environment(monkeypatch, tv2, events=events, qwen_calls=qwen_calls)

    # 让 Qwen 交回一个明确标着「没挂上标点」的块
    fake_qwen = sys.modules["backend.core.qwen_asr_runner"]
    fake_qwen.run_qwen_asr = lambda audio_path, language=None, cache_dir=None, **k: _Outcome(complete=True, chunks=[{
        "text": "hello", "t_start": 0.0, "t_end": 1.0,
        "segments": [{"text": "hello", "start": 0.0, "end": 1.0}],
        "punctuation_attached": False,
    }])

    monkeypatch.setattr(tv2, "should_parallel_asr", lambda: False)
    monkeypatch.setattr(tv2, "sample_language_multipoint", _locked_en(events))

    class _EnInfo:
        language, language_probability, duration = "en", 0.95, 2100.0
    monkeypatch.setattr(tv2, "transcribe_whisper", lambda *_a, **_k: ([], _EnInfo()))

    def fake_to_segments(_out, results, _info, n, _cb):
        seg = tv2.Segment(0, 0.0, 1.0, "hello")
        seg.words = [{"text": "hello", "word": "hello", "start": 0.0, "end": 1.0}]
        results.append(seg)
        return n + 1
    monkeypatch.setattr(tv2, "_whisper_output_to_segments", fake_to_segments)

    envelope = tv2.transcribe(
        "/tmp/p457.wav", model_size="large-v2", skip_vad=True,
        filter_keywords=True, speaker_detect=False,
        transcription_tier="precise", cache_dir="/tmp/p457cache")

    evidence = envelope.receipt.observation_evidence
    assert "punctuation" in evidence, f"收据里没有 punctuation 证据: {evidence}"
    assert evidence["punctuation"]["missing_chunks"] == 1, (
        f"生产代码没把缺标点块数写进收据: {evidence['punctuation']}")


def test_worker_progress_message_tells_the_user_about_missing_punctuation():
    """走 worker 真正用的那条读取表达式，断言用户可见文案里出现提示。

    ⑦.5 发现：真实调用点被跑到 13 次，第三参数**每次都是 0**——
    「收据 → worker → 用户可见文案」整条链一次都没被真实驱动过。
    """
    class _Receipt:
        observation_evidence = {"punctuation": {"missing_chunks": 3}}

    class _Transcription:
        receipt = _Receipt()

    # 与 worker.py:3825 完全同一个读取表达式
    count = ((_Transcription.receipt.observation_evidence or {})
             .get("punctuation", {}).get("missing_chunks", 0))
    message = worker._p395_transcribe_message(895, None, count)

    assert count == 3
    assert "3" in message and "标点" in message
