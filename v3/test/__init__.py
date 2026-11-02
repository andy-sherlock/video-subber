import json


def locked_decision(language="en", count=3):
    """G11：测试用的已锁定语言决定。

    断句要求收据带一份已锁定的决定——缺了就报错，不许悄悄退回通用规则。
    公共构造器默认带上它，免得每个测试各造一份（那正是本项目 6 份 _Receipt
    替身各自复制的老毛病）。
    """
    from backend.core.whisper_runner import LanguageDecision, LanguageSample
    return LanguageDecision(
        language=language, locked=True, split=False,
        expected_count=count, valid_count=count,
        samples=tuple(
            LanguageSample(float(i * 300), language, {language: 0.9})
            for i in range(count)),
    )


def transcription_receipt_from_raw(worker, raw_path, language="en"):
    """把旧测试夹具升级成正式转录收据，不让生产代码再读诊断文件选源。"""
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    words = payload.get("word_timestamps") or []
    return worker.TranscriptionReceipt(
        routed_primary="qwen",
        actual_primary="qwen",
        word_source="qwen_word_timestamps",
        source_text=payload.get("full_text") or "",
        source_words=words,
        language_decision=locked_decision(language),
        engine_received_language={"qwen": language},
        artifact_language_check="ok",
    )


def transcription_envelope_from_raw(worker, raw_path, segments=None, language="en"):
    return worker.TranscriptionEnvelope(
        list(segments or []),
        transcription_receipt_from_raw(worker, raw_path, language),
    )


def transcription_envelope_from_segments(worker, segments, *, engine="whisper", language="en"):
    segments = list(segments)
    words = [
        dict(word)
        for segment in segments
        for word in getattr(segment, "words", [])
    ]
    text = " ".join(str(word.get("text") or word.get("word") or "") for word in words)
    receipt = worker.TranscriptionReceipt(
        routed_primary=engine,
        actual_primary=engine,
        word_source=f"{engine}_word_timestamps",
        source_text=text,
        source_words=words,
        language_decision=locked_decision(language),
        engine_received_language={engine: language},
        artifact_language_check="ok",
    )
    return worker.TranscriptionEnvelope(segments, receipt)


def stamp_request_fingerprint(worker, envelope, *, cache, model="large-v2",
                              transcription_tier="standard", speaker_detect=False):
    """给存档盖上「本次请求」的指纹，让它对指定请求可复用。

    G11：`_step_transcribe` 会用同样的输入算一次指纹并与存档比对——
    对不上就重新转录。测试若想验证「复用」，必须盖上匹配的指纹。
    """
    from pathlib import Path as _Path
    from backend.core.whisper_runner import choose_whisper_backend

    envelope.receipt.transcription_request_fingerprint = (
        worker.transcription_request_fingerprint(
            audio_identity=_Path(cache).name,
            model=model,
            transcription_tier=transcription_tier,
            speaker_detect=speaker_detect,
            skip_vad=True,
            vad_threshold=0.0,
            backend=choose_whisper_backend(),
        ))
    return envelope
