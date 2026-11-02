"""G11/P444：收据必须记住语言决定，且能穿过存档往返。

合同：施工项 3-1（收据新增字段并同步存档链三处）+ 验收「一、正向结构证据」。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _load_worker(monkeypatch):
    from test.test_p390_worker_integration import _load_worker_without_audio_models
    return _load_worker_without_audio_models(monkeypatch)


def _decision():
    from backend.core.whisper_runner import LanguageDecision, LanguageSample
    return LanguageDecision(
        language="en", locked=True, split=False,
        expected_count=3, valid_count=3,
        samples=(
            LanguageSample(0.0, "en", {"en": 0.40, "ja": 0.31}),
            LanguageSample(600.0, "en", {"en": 0.67}),
            LanguageSample(1200.0, "en", {"en": 0.94}),
        ),
    )


def test_receipt_records_the_language_decision_and_what_engines_received(monkeypatch):
    """出事之后必须能从收据回溯：决定是什么、引擎实际收到什么。"""
    worker = _load_worker(monkeypatch)

    receipt = worker.TranscriptionReceipt(
        routed_primary="qwen",
        actual_primary="qwen",
        word_source="qwen_word_timestamps",
        source_text="hello",
        source_words=[{"text": "hello", "word": "hello", "start": 0.0, "end": 1.0}],
        language_decision=_decision(),
        engine_received_language={"qwen": "en"},
    )

    assert receipt.language_decision.language == "en"
    assert receipt.language_decision.locked is True
    assert len(receipt.language_decision.samples) == 3, "完整采样证据必须留下"
    assert receipt.engine_received_language == {"qwen": "en"}


def test_language_decision_survives_the_archive_round_trip(monkeypatch):
    """①' 新发现 2：存档链三处改法不同，机械照抄会漏。

    只改 dataclass 不同步存档链 → 首次运行读得到，缓存恢复后丢失，
    结构分叉只是被推迟到第二次运行。
    """
    worker = _load_worker(monkeypatch)

    segment = worker.Segment(0, 0.0, 1.0, "hello")
    segment.words = [{"text": "hello", "word": "hello", "start": 0.0, "end": 1.0}]
    envelope = worker.TranscriptionEnvelope(
        [segment],
        worker.TranscriptionReceipt(
            routed_primary="qwen",
            actual_primary="qwen",
            word_source="qwen_word_timestamps",
            source_text="hello",
            source_words=list(segment.words),
            language_decision=_decision(),
            engine_received_language={"qwen": "en"},
        ),
    )

    payload = worker._transcription_envelope_payload(envelope)
    restored = worker._read_transcription_envelope(payload)

    assert restored is not None, "自己刚写出来的信封必须能读回来"
    assert restored.receipt.language_decision.language == "en"
    assert restored.receipt.language_decision.locked is True
    assert len(restored.receipt.language_decision.samples) == 3
    assert restored.receipt.engine_received_language == {"qwen": "en"}


def test_archive_without_language_fields_is_rejected(monkeypatch):
    """G11 前的旧存档必须被确定性拒绝并重新转录——不需要有人手工清缓存。

    做法：用真的序列化器造一份**样样合法**的存档，再把两个语言字段抠掉。
    这样唯一的拒绝理由就只剩「缺语言字段」，不会被词守恒或段落指纹之类
    的其它检查抢先挡下来（那种通过是假绿）。
    """
    worker = _load_worker(monkeypatch)

    segment = worker.Segment(0, 0.0, 1.0, "hello")
    segment.words = [{"text": "hello", "word": "hello", "start": 0.0, "end": 1.0}]
    envelope = worker.TranscriptionEnvelope(
        [segment],
        worker.TranscriptionReceipt(
            routed_primary="qwen",
            actual_primary="qwen",
            word_source="qwen_word_timestamps",
            source_text="hello",
            source_words=list(segment.words),
            language_decision=_decision(),
            engine_received_language={"qwen": "en"},
        ),
    )
    payload = worker._transcription_envelope_payload(envelope)

    # 先证明这份存档本身是合法的——否则下面的拒绝说明不了任何问题
    assert worker._read_transcription_envelope(payload) is not None

    legacy = {
        "version": payload["version"],
        "receipt": {k: v for k, v in payload["receipt"].items()
                    if k not in {"language_decision", "engine_received_language"}},
        "segments": payload["segments"],
    }

    assert worker._read_transcription_envelope(legacy) is None, (
        "缺语言字段的旧存档必须被拒绝")
