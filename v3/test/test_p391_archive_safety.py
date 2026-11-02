"""P391 / A9：存档统一防坏（T29-T32）。

一句话：软件一跑十几分钟，中途关掉是常态。写到一半被打断不能弄丢老存档；
读到半截存档要干净地重来，而不是每次都崩在同一个地方、把这个视频永久废掉。
"""

import importlib
import json
import sys
from types import SimpleNamespace

import pytest

from test import (transcription_envelope_from_raw, locked_decision,
                  stamp_request_fingerprint)

from backend.core import safe_archive
from backend.transcriber_v2 import (
    ArtifactLanguageMismatch as _real_artifact_mismatch,
    TRANSCRIPTION_WORD_SOURCES_BY_PRIMARY as _real_word_sources,
)


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
                from test import locked_decision, stamp_request_fingerprint
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


def _transcription_envelope(worker, text):
    segment = worker.Segment(0, 0.0, 1.0, text)
    segment.words = [{"text": text, "start": 0.0, "end": 1.0}]
    receipt = worker.TranscriptionReceipt(
        routed_primary="whisper",
        actual_primary="whisper",
        word_source="whisper_word_timestamps",
        source_text=text,
        source_words=segment.words,
                  language_decision=locked_decision(),
        artifact_language_check="ok",
              )
    return worker.TranscriptionEnvelope([segment], receipt)


def _words(texts, *, step=0.4):
    return [
        {
            "word": text,
            "text": text,
            "start": round(index * step, 3),
            "end": round(index * step + 0.3, 3),
        }
        for index, text in enumerate(texts)
    ]


# ── T29：写一半被打断，老存档必须完好 ────────────────────────

def test_interrupted_write_keeps_old_snapshot_intact(monkeypatch, tmp_path):
    """T29：草稿创建后的受控 OSError 不伤老存档，并清理本次草稿。

    这不是拔电源测试。真实进程被强杀时 ``finally`` 不一定执行，草稿可能残留。
    """
    target = tmp_path / "transcript.json"
    target.write_text('{"old": "完好的老存档"}', encoding="utf-8")
    temp_files_seen_during_failure = []

    def _fail_after_temp_exists(_fd):
        temp_files_seen_during_failure.extend(
            path.name for path in tmp_path.glob(".transcript-*.tmp")
        )
        raise OSError("模拟草稿已经创建后的落盘失败")

    monkeypatch.setattr(safe_archive.os, "fsync", _fail_after_temp_exists)

    ok = safe_archive.write_json_archive(target, {"new": "本次新存档"})

    assert ok is False, "写不成功必须如实返回失败"
    assert temp_files_seen_during_failure, "异常必须发生在草稿已经创建之后"
    assert json.loads(target.read_text(encoding="utf-8")) == {"old": "完好的老存档"}, \
        "老存档必须完好无损"
    leftovers = [
        path.name for path in tmp_path.iterdir()
        if path.name != "transcript.json"
    ]
    assert leftovers == [], f"草稿文件必须被清干净，实际残留: {leftovers}"


def test_atomic_write_replaces_content_and_is_readable(tmp_path):
    """T29b：正常写入之后读得回来（防止「安全」到把功能写坏）。"""
    target = tmp_path / "transcript.json"
    assert safe_archive.write_json_archive(target, {"hello": "世界"}) is True
    assert safe_archive.read_json_archive(target) == {"hello": "世界"}


def test_write_refuses_symlink_target(tmp_path):
    """T29c：目标是软链接时拒绝写入，不碰链接指向的文件（守住 P390 已有防线）。"""
    outside = tmp_path / "outside.json"
    outside.write_text("outside sentinel", encoding="utf-8")
    target = tmp_path / "archive.json"
    target.symlink_to(outside)

    assert safe_archive.write_json_archive(target, {"x": 1}) is False
    assert outside.read_text(encoding="utf-8") == "outside sentinel"
    assert target.is_symlink()


# ── T30：读到半截存档，干净地重来 ────────────────────────────

def test_truncated_transcript_cache_does_not_crash(monkeypatch, tmp_path):
    """T30：半截 transcript.json → 删掉重来、重新识别，不崩溃、不永久卡死。

    这条是「用户中途关一次软件，这个视频就永久废了」的根因守门员。
    """
    worker = _load_worker(monkeypatch)
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text('[{"id": 0, "start": 0.0, "end": 1.0, "te',
                               encoding="utf-8")

    called = []

    def _fake_transcribe(*args, **kwargs):
        called.append(True)
        return _transcription_envelope(worker, "hello")

    monkeypatch.setattr(worker, "transcribe_v2", _fake_transcribe)

    segments = worker._step_transcribe(
        "translate",                      # 即使进度已经走过转录，也必须重来
        str(tmp_path / "audio.wav"),
        transcript_path,
        "large-v2", False, "standard", tmp_path, {},
        SimpleNamespace(save=lambda *a, **k: None),
    )

    assert called, "半截存档必须触发重新识别，而不是拿它去解析"
    assert len(segments) == 1
    archive = json.loads(transcript_path.read_text(encoding="utf-8"))
    assert archive["version"] == 2
    assert archive["segments"][0]["text"] == "hello"


def test_intact_transcript_cache_is_still_reused(monkeypatch, tmp_path):
    """T30b：完好的转录存档照常复用（防止「安全」到每次都重跑 ASR）。"""
    worker = _load_worker(monkeypatch)
    transcript_path = tmp_path / "transcript.json"
    # G11：存档要能被复用，必须盖上与本次请求匹配的指纹——
    # 指纹存在信封自己身上，checkpoint 在不在都拦得住。
    transcript_path.write_text(
        json.dumps(worker._transcription_envelope_payload(
            stamp_request_fingerprint(
                worker, _transcription_envelope(worker, "cached"),
                cache=tmp_path, model="large-v2",
                transcription_tier="standard", speaker_detect=False)
        )),
        encoding="utf-8",
    )

    def _must_not_run(*args, **kwargs):
        raise AssertionError("完好的存档不该触发重新识别")

    monkeypatch.setattr(worker, "transcribe_v2", _must_not_run)

    segments = worker._step_transcribe(
        "translate", str(tmp_path / "audio.wav"), transcript_path,
        "large-v2", False, "standard", tmp_path, {},
        SimpleNamespace(save=lambda *a, **k: None),
    )
    assert [segment.text for segment in segments] == ["cached"]


# ── T31：大存档不许被大小上限误拒 ────────────────────────────

def test_large_cache_not_rejected_by_size_limit(tmp_path):
    """T31：300 KB 的转录存档、171 KB 的 Qwen 原始存档必须正常复用。

    照搬断句存档的 64KB 上限会让它们每次都被拒 → 每跑一次都重做十几分钟语音识别，
    比原来的 bug 严重得多。
    """
    transcript = tmp_path / "transcript.json"
    payload = [{"id": index, "text": "x" * 200} for index in range(1500)]
    assert safe_archive.write_json_archive(transcript, payload) is True
    assert transcript.stat().st_size > 300 * 1024

    loaded = safe_archive.read_json_archive(
        transcript, max_bytes=safe_archive.LARGE_ARCHIVE_MAX_BYTES
    )
    assert loaded is not None, "300 KB 的转录存档不能被大小上限误拒"
    assert len(loaded) == 1500


def test_small_cache_keeps_its_own_tight_limit(monkeypatch, tmp_path):
    """T31b：断句存档自己的 64 KB 上限仍然生效（上限是参数，不是一个数）。"""
    worker = _load_worker(monkeypatch)
    cache_path = tmp_path / "sentence_boundaries.json"
    cache_path.write_text(json.dumps({
        "version": worker._P390_BOUNDARY_CACHE_VERSION,
        "source_fingerprint": "x" * 64,
        "language": "en",
        "break_after_indices": list(range(0, 40000, 2)),
        "group_fingerprint": "y" * 64,
    }), encoding="utf-8")
    assert cache_path.stat().st_size > 64 * 1024

    assert worker._p390_read_boundary_cache(
        cache_path,
        source_fingerprint="x" * 64,
        hint_fingerprint="no-map",
        language="en",
        word_count=100000,
    ) is None, "断句存档超过 64 KB 仍然必须拒绝复用"


# ── T32：Qwen 诊断存档损坏不得拖累正式收据 ───────────────────

def test_broken_qwen_diagnostic_does_not_delete_valid_receipt(
    monkeypatch, tmp_path
):
    worker = _load_worker(monkeypatch)
    raw_path = tmp_path / "qwen_raw.json"
    transcript_path = tmp_path / "transcript.json"
    raw_path.write_text('{"full_text": "We found a hidden', encoding="utf-8")
    envelope = _transcription_envelope(worker, "hello")
    transcript_path.write_text(
        json.dumps(worker._transcription_envelope_payload(envelope)),
        encoding="utf-8",
    )

    groups, _reason = worker._prepare_sentence_groups(
        envelope,
        boundary_cache_path=tmp_path / "sentence_boundaries.json",
    )

    assert [group.text for group in groups] == ["hello"]
    assert raw_path.exists(), "诊断文件不再属于正式词流，组句不得擅自处理它"
    assert transcript_path.exists(), "无关诊断损坏不得删除有效转录信封"
