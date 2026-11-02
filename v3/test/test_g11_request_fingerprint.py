"""G11 施工项 3：转录请求指纹 + 六条存档复用拒绝条件。

验收四（快速集成测试）：用假转录器 + 临时目录，数转录器被调了几次。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test import locked_decision


def _load_worker(monkeypatch):
    from test.test_p390_worker_integration import _load_worker_without_audio_models
    return _load_worker_without_audio_models(monkeypatch)


_REQUEST_A = dict(audio_identity="sha-a", model="large-v2",
                  transcription_tier="standard", speaker_detect=True,
                  skip_vad=True, vad_threshold=0.0, backend="mlx")
_REQUEST_B = dict(_REQUEST_A, model="large-v3")     # 只换了模型


def test_fingerprint_changes_when_any_pre_transcription_input_changes(monkeypatch):
    worker = _load_worker(monkeypatch)
    fp = worker.transcription_request_fingerprint

    a = fp(**_REQUEST_A)
    assert a == fp(**_REQUEST_A), "同样的输入必须得到同样的指纹"
    assert a != fp(**_REQUEST_B), "换了模型就是另一个请求"
    assert a != fp(**dict(_REQUEST_A, transcription_tier="precise"))
    assert a != fp(**dict(_REQUEST_A, backend="faster")), (
        "MLX 与 faster-whisper 输出不保证逐字一致，后端换了就是另一个请求")
    assert a != fp(**dict(_REQUEST_A, audio_identity="sha-b"))


def test_contract_version_is_not_the_archive_format_version(monkeypatch):
    """合同：「存档格式版本」与「转录缓存合同版本」是两个概念，不得混用。

    现有 _TRANSCRIPTION_ENVELOPE_VERSION 只管存档形状，不得兼职算法身份。
    """
    worker = _load_worker(monkeypatch)
    assert hasattr(worker, "_TRANSCRIPTION_CONTRACT_VERSION")
    assert hasattr(worker, "_TRANSCRIPTION_ENVELOPE_VERSION")

    # 2026-09-02（G12 第一批）：这里原来断言「两个数字不能相等」。
    # 那是个坏代理——两个各自独立递增的计数器早晚会撞上同一个值，
    # 撞上并不说明它们被混用了。合同版本 1→2 时正好撞上信封版本 2，
    # 测试就误报了一次。**要守的是「谁在用哪一个」，不是「数字长得像不像」。**
    source = Path(worker.__file__).read_text(encoding="utf-8")
    envelope_uses = source.count("_TRANSCRIPTION_ENVELOPE_VERSION")
    contract_uses = source.count("_TRANSCRIPTION_CONTRACT_VERSION")
    assert envelope_uses >= 2, "信封版本应当既被写入也被校验"
    assert contract_uses >= 2, "合同版本应当被定义并喂进请求指纹"

    # 真正的混用风险：请求指纹拿信封版本当算法身份。
    # 指纹必须随合同版本变，而**不随**信封版本变。
    baseline = worker.transcription_request_fingerprint(**_REQUEST_A)
    monkeypatch.setattr(worker, "_TRANSCRIPTION_ENVELOPE_VERSION",
                        worker._TRANSCRIPTION_ENVELOPE_VERSION + 1)
    assert worker.transcription_request_fingerprint(**_REQUEST_A) == baseline, \
        "请求指纹跟着信封格式版本变了——信封版本兼职了算法身份"

    a = worker.transcription_request_fingerprint(**_REQUEST_A)
    b = worker.transcription_request_fingerprint(
        **_REQUEST_A, contract_version=worker._TRANSCRIPTION_CONTRACT_VERSION + 1)
    assert a != b, "合同版本变了，旧存档必须失效"


def _envelope(worker, *, fingerprint, decision=None, engine_lang=None,
              actual="qwen", word_source="qwen_word_timestamps",
              artifact_check="ok"):
    segment = worker.Segment(0, 0.0, 1.0, "hello")
    segment.words = [{"text": "hello", "word": "hello", "start": 0.0, "end": 1.0}]
    return worker.TranscriptionEnvelope(
        [segment],
        worker.TranscriptionReceipt(
            routed_primary="qwen", actual_primary=actual,
            word_source=word_source, source_text="hello",
            source_words=list(segment.words),
            language_decision=decision if decision is not None else locked_decision(),
            engine_received_language=(engine_lang if engine_lang is not None
                                      else {"qwen": "en"}),
            artifact_language_check=artifact_check,
            transcription_request_fingerprint=fingerprint,
        ),
    )


def test_matching_request_reuses_the_archive(monkeypatch):
    worker = _load_worker(monkeypatch)
    fp_a = worker.transcription_request_fingerprint(**_REQUEST_A)
    payload = worker._transcription_envelope_payload(_envelope(worker, fingerprint=fp_a))

    assert worker._read_transcription_envelope(payload, request_fingerprint=fp_a) is not None


def test_different_request_rejects_the_archive(monkeypatch):
    """3-5：请求指纹必须**独立**守住 transcript.json，不依赖 checkpoint。"""
    worker = _load_worker(monkeypatch)
    fp_a = worker.transcription_request_fingerprint(**_REQUEST_A)
    fp_b = worker.transcription_request_fingerprint(**_REQUEST_B)
    payload = worker._transcription_envelope_payload(_envelope(worker, fingerprint=fp_a))

    assert worker._read_transcription_envelope(payload, request_fingerprint=fp_b) is None, (
        "换了模型还复用旧稿——checkpoint 缺失时的既有漏洞就是这么来的")


def test_six_reuse_rejection_conditions(monkeypatch):
    """3-6：即使请求指纹匹配，这六种存档也不得复用。"""
    from backend.core.whisper_runner import LanguageDecision
    worker = _load_worker(monkeypatch)
    fp = worker.transcription_request_fingerprint(**_REQUEST_A)
    read = lambda env: worker._read_transcription_envelope(
        worker._transcription_envelope_payload(env), request_fingerprint=fp)

    # ① 决定未锁定
    unlocked = LanguageDecision(language="en", locked=False, split=True,
                                expected_count=3, valid_count=3)
    assert read(_envelope(worker, fingerprint=fp, decision=unlocked)) is None, "①"

    # ② 决定语言与引擎实收语言不一致
    assert read(_envelope(worker, fingerprint=fp,
                          engine_lang={"qwen": "ja"})) is None, "②"

    # ③④ 引擎与词流来源必须吻合（复用既有 TRANSCRIPTION_WORD_SOURCES_BY_PRIMARY）。
    # 注意：收据自己的 __post_init__ 已经会拒绝非法组合，所以这里直接改 payload——
    # 测的是「读存档时也要拦」，不是「构造收据时会拦」。
    def read_payload(**overrides):
        payload = worker._transcription_envelope_payload(
            _envelope(worker, fingerprint=fp))
        payload["receipt"].update(overrides)
        return worker._read_transcription_envelope(payload, request_fingerprint=fp)

    assert read_payload(actual_primary="dual_failed") is None, (
        "③ dual_failed 只能配 emergency_combined，不得冒充高置信完成")
    assert read_payload(actual_primary="whisper") is None, (
        "④ whisper 配 qwen 词流来源，矛盾")

    # ⑤ 产物一致性检查不是「已跑且通过」
    assert read(_envelope(worker, fingerprint=fp, artifact_check="")) is None, "⑤"

    # ⑥ 缺少采样明细
    bare = LanguageDecision(language="en", locked=True, split=False,
                            expected_count=3, valid_count=3, samples=())
    assert read(_envelope(worker, fingerprint=fp, decision=bare)) is None, "⑥"

    # 对照：六条都不犯的存档必须能复用，否则上面全是假绿
    assert read(_envelope(worker, fingerprint=fp)) is not None, "合法存档被误拒"


def _fake_envelope(worker):
    seg = worker.Segment(0, 0.0, 1.0, "hello")
    seg.words = [{"text": "hello", "word": "hello", "start": 0.0, "end": 1.0}]
    return worker.TranscriptionEnvelope(
        [seg],
        worker.TranscriptionReceipt(
            routed_primary="qwen", actual_primary="qwen",
            word_source="qwen_word_timestamps", source_text="hello",
            source_words=list(seg.words),
            language_decision=locked_decision(),
            engine_received_language={"qwen": "en"},
            artifact_language_check="ok",
        ),
    )


def test_integration_same_request_reuses_different_request_retranscribes(
        monkeypatch, tmp_path):
    """验收四：临时任务目录 + 假转录器，数转录器调用次数。

    **即使 checkpoint.json 缺失**，也必须由信封自身的请求指纹拒绝旧稿。
    """
    worker = _load_worker(monkeypatch)
    calls = {"n": 0}

    def fake_transcribe(*_a, **_k):
        calls["n"] += 1
        return _fake_envelope(worker)

    monkeypatch.setattr(worker, "transcribe_v2", fake_transcribe)
    monkeypatch.setattr(worker, "stderr_progress", lambda *_a, **_k: None)
    monkeypatch.setattr(worker, "emit", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "backend.core.extractor.get_video_duration", lambda _p: 600.0)

    cache = tmp_path / "8feaed0f7ee7"
    cache.mkdir()
    transcript = cache / "transcript.json"
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")

    class _Ck(dict):
        def save(self, *_a, **_k): pass

    def run(model):
        return worker._step_transcribe(
            "transcribe", str(audio), str(transcript), model,
            True, "standard", cache, {}, _Ck())

    run("large-v2")
    assert calls["n"] == 1, "第一次必须真的转录"
    assert transcript.exists(), "第一次必须写下正式信封"

    run("large-v2")
    assert calls["n"] == 1, "同一个请求必须复用存档，转录器不得再被调用"

    # checkpoint.json 从头到尾都不存在——指纹必须自己拦住
    assert not (cache / "checkpoint.json").exists()
    run("large-v3")
    assert calls["n"] == 2, (
        "换了模型还复用旧稿——checkpoint 缺失时的既有漏洞就是这么来的")
