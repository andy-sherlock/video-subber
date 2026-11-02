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
import copy
import importlib
import inspect
import json
import math
import os
import stat
import subprocess
import sys
import time
import wave
from pathlib import Path

from test import locked_decision

import pytest


def _sound_map_module():
    try:
        return importlib.import_module("backend.core.sound_map")
    except ModuleNotFoundError:
        pytest.fail("G5-2 声音地图模块尚未实现")


def test_build_sound_hints_only_returns_evidence_without_mutating_words():
    sound_map = _sound_map_module()
    words = [
        {"text": "first", "start": 0.0, "end": 0.5},
        {"text": "second", "start": 2.5, "end": 3.0},
    ]
    before = copy.deepcopy(words)
    intervals = [{"start_sample": 8000, "end_sample": 12784}]

    result = sound_map.build_sound_hints(words, intervals, sampling_rate=16000)

    assert words == before
    assert result.hints == (
        sound_map.SoundHint(word_index=0, gap="2.000", voiced_total="0.299"),
    )
    assert result.conflicts == ()


def test_build_sound_hints_rejects_reversed_raw_interval():
    sound_map = _sound_map_module()
    words = [
        {"text": "first", "start": 0.0, "end": 0.5},
        {"text": "second", "start": 2.5, "end": 3.0},
    ]

    with pytest.raises(ValueError, match="VAD 区间无效"):
        sound_map.build_sound_hints(
            words,
            [{"start_sample": 12000, "end_sample": 8000}],
            sampling_rate=16000,
        )


def test_sound_hint_thresholds_use_merged_total_and_round_once():
    sound_map = _sound_map_module()
    words = [
        {"text": "a", "start": 0.0, "end": 1.0},
        {"text": "b", "start": 2.999, "end": 3.2},
        {"text": "c", "start": 5.2, "end": 5.5},
        {"text": "d", "start": 7.5, "end": 8.0},
    ]
    # 第一处空白不足 2 秒；第二处合并后总重叠 0.2994 秒；
    # 第三处由两个小段相加为 0.2995 秒，最后统一四舍五入成 0.300。
    intervals = [
        {"start_sample": 51200, "end_sample": 53595},
        {"start_sample": 53600, "end_sample": 55995},
        {"start_sample": 88000, "end_sample": 90396},
        {"start_sample": 92000, "end_sample": 94396},
    ]

    result = sound_map.build_sound_hints(words, intervals)

    assert [(hint.word_index, hint.gap, hint.voiced_total) for hint in result.hints] == [
        (1, "2.000", "0.299")
    ]
    assert [(hint.word_index, hint.gap, hint.voiced_total) for hint in result.conflicts] == [
        (2, "2.000", "0.300")
    ]


def test_vad_parameters_have_one_canonical_json_representation():
    sound_map = _sound_map_module()

    parameters = sound_map.canonical_vad_parameters()

    assert parameters == {
        "max_speech_duration_s": "inf",
        "min_silence_duration_ms": 200,
        "min_speech_duration_ms": 0,
        "neg_threshold": "0.350000",
        "speech_pad_ms": 0,
        "threshold": "0.500000",
    }
    encoded = sound_map.canonical_json_bytes(parameters)
    assert json.loads(encoded) == parameters
    assert encoded == (
        b'{"max_speech_duration_s":"inf","min_silence_duration_ms":200,'
        b'"min_speech_duration_ms":0,"neg_threshold":"0.350000",'
        b'"speech_pad_ms":0,"threshold":"0.500000"}'
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"threshold": math.nan},
        {"neg_threshold": math.inf},
        {"max_speech_duration_s": -math.inf},
    ],
)
def test_vad_parameters_reject_illegal_non_finite_values(overrides):
    sound_map = _sound_map_module()

    with pytest.raises(ValueError, match="VAD 参数非法"):
        sound_map.canonical_vad_parameters(overrides)


def test_map_input_identity_tracks_media_content_not_its_path(tmp_path):
    sound_map = _sound_map_module()
    media = tmp_path / "input.mkv"
    asset = tmp_path / "silero_vad_v6.onnx"
    media.write_bytes(b"first media content")
    asset.write_bytes(b"fixed vad asset")
    track = {"index": 0, "codec": "aac", "sample_rate": 48000, "channels": 2}

    first = sound_map.build_map_input_identity(media, track, asset)
    media.write_bytes(b"replacement media content")
    second = sound_map.build_map_input_identity(media, track, asset)

    assert first != second
    assert first["track_identity"] == track
    assert "input.mkv" not in json.dumps(first)
    assert set(first) == {
        "source_content_sha256",
        "track_identity",
        "vad_asset_sha256",
        "vad_parameters",
    }


def test_map_input_identity_uses_literal_none_when_media_is_unavailable(tmp_path):
    sound_map = _sound_map_module()
    asset = tmp_path / "silero_vad_v6.onnx"
    asset.write_bytes(b"fixed vad asset")

    identity = sound_map.build_map_input_identity(None, None, asset)

    assert identity["source_content_sha256"] == "none"
    assert identity["track_identity"] == "none"


def test_map_input_identity_rejects_extra_private_fields(tmp_path):
    sound_map = _sound_map_module()
    media = tmp_path / "input.mkv"
    asset = tmp_path / "silero_vad_v6.onnx"
    media.write_bytes(b"media")
    asset.write_bytes(b"asset")
    identity = sound_map.build_map_input_identity(
        media,
        {"index": 0, "codec": "aac", "sample_rate": 48000, "channels": 2},
        asset,
    )
    poisoned = dict(identity, source_path=str(media))

    with pytest.raises(ValueError, match="地图输入身份无效"):
        sound_map.validate_map_input_identity(poisoned)


def test_map_record_accepts_raw_samples_and_rejects_private_path_fields(tmp_path):
    sound_map = _sound_map_module()
    media = tmp_path / "input.mkv"
    asset = tmp_path / "silero_vad_v6.onnx"
    media.write_bytes(b"media")
    asset.write_bytes(b"asset")
    identity = sound_map.build_map_input_identity(
        media,
        {"index": 0, "codec": "aac", "sample_rate": 48000, "channels": 2},
        asset,
    )
    record = sound_map.make_map_record(
        identity,
        [{"start_sample": 10, "end_sample": 20}],
        total_samples=16000,
    )

    assert sound_map.validate_map_record(record, expected_identity=identity) == record
    poisoned = dict(record, source_path=str(media))
    with pytest.raises(ValueError, match="地图存档字段无效"):
        sound_map.validate_map_record(poisoned, expected_identity=identity)


def test_safe_archive_rejects_nan_and_keeps_the_previous_file(tmp_path):
    from backend.core.safe_archive import read_json_archive, write_json_archive

    target = tmp_path / "sound_map.json"
    assert write_json_archive(target, {"version": 1}, chmod=0o600)

    assert write_json_archive(
        target,
        {"bad": math.nan},
        chmod=0o600,
        allow_nan=False,
    ) is False
    assert read_json_archive(target) == {"version": 1}


def test_map_record_round_trip_is_private_and_revalidated(tmp_path):
    sound_map = _sound_map_module()
    media = tmp_path / "input.mkv"
    asset = tmp_path / "silero_vad_v6.onnx"
    media.write_bytes(b"media")
    asset.write_bytes(b"asset")
    identity = sound_map.build_map_input_identity(
        media,
        {"index": 0, "codec": "aac", "sample_rate": 48000, "channels": 2},
        asset,
    )
    record = sound_map.make_map_record(identity, [], total_samples=16000)
    path = tmp_path / "sound_map.json"

    assert sound_map.save_map_record(path, record)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert sound_map.load_map_record(path, expected_identity=identity) == record


@pytest.mark.parametrize(
    "mutate",
    [
        lambda record: record.update(version=999),
        lambda record: record.update(producer_version=999),
        lambda record: record["intervals"].append(
            {"start_sample": -1, "end_sample": 2}
        ),
        lambda record: record["intervals"].append(
            {"start_sample": 2, "end_sample": 16001}
        ),
        lambda record: record["intervals"].append(
            {"start_sample": 2.5, "end_sample": 3}
        ),
        lambda record: record.update(reason_code="private failure"),
    ],
)
def test_map_record_rejects_each_corrupt_shape(tmp_path, mutate):
    sound_map = _sound_map_module()
    media = tmp_path / "input.wav"
    asset = tmp_path / "vad.onnx"
    media.write_bytes(b"media")
    asset.write_bytes(b"asset")
    identity = sound_map.build_map_input_identity(
        media,
        {"index": 0, "codec": "pcm", "sample_rate": 16000, "channels": 1},
        asset,
    )
    record = sound_map.make_map_record(identity, [], total_samples=16000)
    mutate(record)

    with pytest.raises(ValueError):
        sound_map.validate_map_record(record, expected_identity=identity)


def test_audio_track_identity_comes_from_the_actual_decoded_track(tmp_path):
    from backend.core import extractor

    audio = tmp_path / "input.wav"
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\0\0" * 1600)

    identity = extractor.probe_audio_track_identity(audio)

    assert identity == {
        "index": 0,
        "codec": "pcm_s16le",
        "sample_rate": 16000,
        "channels": 1,
    }


def test_compute_sound_map_uses_bundled_silero_and_keeps_zero_speech_ok(tmp_path):
    sound_map = _sound_map_module()
    audio = tmp_path / "silence.wav"
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\0\0" * 1600)

    record = sound_map.compute_sound_map(audio)

    assert record["map_status"] == "ok"
    assert record["intervals"] == []
    assert record["total_samples"] == 1600
    assert record["map_input_identity"]["vad_parameters"] == (
        sound_map.canonical_vad_parameters()
    )
    assert "silence.wav" not in json.dumps(record)


def test_boundary_prompt_adds_only_normalized_hints_and_preserves_word_content():
    sound_map = _sound_map_module()
    from backend.core.sentence_groups import build_llm_primary_boundary_prompt

    words = [
        {"text": "first", "start": 0.0, "end": 0.5},
        {"text": "【】：[ ] |\nnext\t\\", "start": 3.4, "end": 4.0},
        {"text": "last", "start": 4.1, "end": 4.5},
    ]
    before = copy.deepcopy(words)
    hints = (sound_map.SoundHint(0, "2.900", "0.000"),)

    system_prompt, user_prompt, _allowed = build_llm_primary_boundary_prompt(
        words,
        language="en",
        sound_hints=hints,
    )

    assert words == before
    assert "0 [gap=2.900]: first" in user_prompt
    assert "1: 【】：[ ] |\\nnext\\t\\\\" in user_prompt
    assert "gap 是参考，不是强制断点" in system_prompt
    assert "冒号后的一个空格到行尾全部是原词" in system_prompt


def test_boundary_prompt_never_sends_a_hint_outside_the_current_break_range():
    sound_map = _sound_map_module()
    from backend.core.sentence_groups import build_llm_primary_boundary_prompt

    words = [
        {"text": f"w{index}", "start": float(index), "end": index + 0.5}
        for index in range(5)
    ]
    hints = (
        sound_map.SoundHint(1, "2.000", "0.000"),
        sound_map.SoundHint(2, "2.100", "0.000"),
        sound_map.SoundHint(3, "2.200", "0.000"),
    )

    _system, prompt, allowed = build_llm_primary_boundary_prompt(
        words,
        language="en",
        start_index=1,
        end_index=3,
        sound_hints=hints,
    )

    assert list(allowed) == [1, 2]
    assert "1 [gap=2.000]: w1" in prompt
    assert "2 [gap=2.100]: w2" in prompt
    assert "3: w3" in prompt
    assert "gap=2.200" not in prompt


def test_hint_fingerprint_changes_with_hint_engine_or_map_status():
    sound_map = _sound_map_module()
    identity = {
        "source_content_sha256": "a" * 64,
        "track_identity": {
            "index": 0,
            "codec": "aac",
            "sample_rate": 48000,
            "channels": 2,
        },
        "vad_asset_sha256": "b" * 64,
        "vad_parameters": sound_map.canonical_vad_parameters(),
    }
    hints = (sound_map.SoundHint(4, "2.900", "0.000"),)

    baseline = sound_map.build_hint_fingerprint(
        identity, "ok", "qwen", hints
    )

    assert baseline == sound_map.build_hint_fingerprint(
        identity, "ok", "qwen", hints
    )
    assert baseline != sound_map.build_hint_fingerprint(
        identity,
        "ok",
        "qwen",
        (sound_map.SoundHint(5, "2.900", "0.000"),),
    )
    assert baseline != sound_map.build_hint_fingerprint(
        identity, "ok", "whisper", hints
    )
    assert baseline != sound_map.build_hint_fingerprint(
        identity, "unavailable", "qwen", hints
    )


def test_hint_fingerprints_distinguish_hint_zero_hint_and_no_audio(tmp_path):
    sound_map = _sound_map_module()
    media = tmp_path / "input.wav"
    asset = tmp_path / "vad.onnx"
    media.write_bytes(b"media")
    asset.write_bytes(b"asset")
    track = {"index": 0, "codec": "pcm", "sample_rate": 16000, "channels": 1}
    with_audio = sound_map.build_map_input_identity(media, track, asset)
    no_audio = sound_map.build_map_input_identity(None, None, asset)

    fingerprints = {
        sound_map.build_hint_fingerprint(
            with_audio,
            "ok",
            "qwen",
            (sound_map.SoundHint(1, "2.000", "0.000"),),
        ),
        sound_map.build_hint_fingerprint(with_audio, "ok", "qwen", ()),
        sound_map.build_hint_fingerprint(no_audio, "unavailable", "qwen", ()),
    }

    assert len(fingerprints) == 3
    assert no_audio["source_content_sha256"] == "none"
    assert no_audio["track_identity"] == "none"


def test_standard_whisper_route_receipt_ignores_existing_qwen_words():
    from backend import transcriber_v2

    whisper = transcriber_v2.Segment(0, 0.0, 1.0, "Whisper primary")
    whisper.words = [
        {"word": "Whisper", "start": 0.0, "end": 0.4},
        {"word": "primary", "start": 0.5, "end": 1.0},
    ]
    qwen_chunks = [{
        "text": "wrong qwen stream",
        "segments": [
            {"text": "wrong", "start": 0.0, "end": 0.4},
            {"text": "qwen", "start": 0.5, "end": 0.7},
            {"text": "stream", "start": 0.8, "end": 1.0},
        ],
    }]

    route = transcriber_v2._resolve_routing(
        None,
        {"data": _Outcome(complete=True, chunks=qwen_chunks)},
        "standard",
        True,
        "/unused/input.wav",
        "en",
        "/unused/cache",
        "whisper",
        True,
        [whisper],
    )

    assert route.which_primary == "whisper"
    assert route.word_source == "whisper_word_timestamps"
    assert [word["text"] for word in route.source_words] == [
        "Whisper", "primary"
    ]
    assert "wrong" not in route.source_text


def test_precise_out_of_route_language_uses_the_selected_whisper_word_stream(
    monkeypatch,
):
    from backend import transcriber_v2

    monkeypatch.setattr(
        transcriber_v2,
        "_collect_qwen_chunks",
        lambda *_args, **_kwargs: _Outcome(complete=True, chunks=qwen_chunks),
    )

    whisper = transcriber_v2.Segment(0, 0.0, 1.0, "bonjour monde")
    whisper.words = [
        {"word": "bonjour", "start": 0.0, "end": 0.4},
        {"word": "monde", "start": 0.5, "end": 1.0},
    ]
    qwen_chunks = [{
        "text": "flux incorrect",
        "segments": [
            {"text": "flux", "start": 0.0, "end": 0.4},
            {"text": "incorrect", "start": 0.5, "end": 1.0},
        ],
    }]

    route = transcriber_v2._resolve_routing(
        None,
        {},
        "precise",
        True,
        "/unused/input.wav",
        "fr",
        "/unused/cache",
        None,
        True,
        [whisper],
    )

    assert route.which_primary == "whisper"
    assert route.word_source == "whisper_word_timestamps"
    assert [word["text"] for word in route.source_words] == ["bonjour", "monde"]
    assert route.source_text == "bonjour monde"


@pytest.mark.parametrize(
    (
        "tier", "standard_primary", "language", "qwen_ok", "whisper_ok",
        "expected_routed", "expected_actual", "expected_word_source",
    ),
    [
        ("standard", "qwen", "en", True, False,
         "qwen", "qwen", "qwen_word_timestamps"),
        ("standard", "whisper", "en", False, True,
         "whisper", "whisper", "whisper_word_timestamps"),
        ("precise", None, "ja", True, True,
         "qwen", "qwen", "qwen_word_timestamps"),
        ("precise", None, "fr", True, True,
         "whisper", "whisper", "whisper_word_timestamps"),
        ("precise", None, "en", False, True,
         "qwen", "whisper", "whisper_word_timestamps"),
        ("precise", None, "en", False, False,
         "qwen", "dual_failed", "emergency_combined"),
    ],
)
def test_unique_router_emits_all_six_receipt_paths(
    monkeypatch,
    tier,
    standard_primary,
    language,
    qwen_ok,
    whisper_ok,
    expected_routed,
    expected_actual,
    expected_word_source,
):
    from backend import transcriber_v2

    qwen_chunks = [{
        "text": "qwen",
        "segments": [{"text": "qwen", "start": 0.0, "end": 1.0}],
    }]
    whisper = transcriber_v2.Segment(0, 0.0, 1.0, "whisper")
    whisper.words = [{"text": "whisper", "start": 0.0, "end": 1.0}]
    monkeypatch.setattr(
        transcriber_v2, "_collect_qwen_chunks",
        lambda *_args, **_kwargs: _Outcome(complete=True, chunks=qwen_chunks),
    )
    monkeypatch.setattr(
        transcriber_v2, "_qwen_is_reliable", lambda _chunks: qwen_ok
    )
    monkeypatch.setattr(
        transcriber_v2,
        "_whisper_is_usable",
        lambda *_args, **_kwargs: whisper_ok,
    )

    route = transcriber_v2._resolve_routing(
        None, {}, tier, True, "/unused.wav", language, "/unused-cache",
        standard_primary, True, [whisper],
    )

    assert route.routed_primary == expected_routed
    assert route.actual_primary == expected_actual
    assert route.word_source == expected_word_source
    receipt = transcriber_v2.TranscriptionReceipt(
        routed_primary=route.routed_primary,
        actual_primary=route.actual_primary,
        word_source=route.word_source,
        source_text=route.source_text,
        source_words=route.source_words,
                  language_decision=locked_decision(),
        artifact_language_check="ok",
              )
    assert receipt.actual_primary == expected_actual
    expected_words = {
        "qwen_word_timestamps": ["qwen"],
        "whisper_word_timestamps": ["whisper"],
        "emergency_combined": ["whisper"],
    }[expected_word_source]
    assert [word["text"] for word in route.source_words] == expected_words


def test_pytorch_qwen_text_becomes_formal_only_after_external_alignment(
    monkeypatch,
):
    from backend import transcriber_v2

    qwen_chunks = [{
        "text": "hello world",
        "segments": [],
        "t_start": 0.0,
        "t_end": 1.2,
    }]
    monkeypatch.setattr(
        transcriber_v2,
        "_collect_qwen_chunks",
        lambda *_args, **_kwargs: _Outcome(complete=True, chunks=qwen_chunks),
    )
    monkeypatch.setattr(
        transcriber_v2, "_qwen_is_reliable", lambda _chunks: True
    )

    route = transcriber_v2._resolve_routing(
        None, {}, "standard", True, "/unused.wav", "en", "/unused-cache",
        "qwen", False, [],
    )

    assert route.actual_primary == "qwen"
    assert route.source_words == []
    assert route.word_source == "external_forced_alignment"
    assert [segment.text for segment in route.qwen_l1_segments] == [
        "hello world"
    ]
    assert [
        (segment.start, segment.end) for segment in route.qwen_l1_segments
    ] == [(0.0, 1.2)]

    aligned_words = [
        {"word": "hello", "start": 0.05, "end": 0.45},
        {"word": "world", "start": 0.55, "end": 1.15},
    ]
    transcriber_v2._finalize_routing_after_alignment(
        route, aligned_words, "en"
    )
    receipt = transcriber_v2.TranscriptionReceipt(
        routed_primary=route.routed_primary,
        actual_primary=route.actual_primary,
        word_source=route.word_source,
        source_text=route.source_text,
        source_words=route.source_words,
                  language_decision=locked_decision(),
        artifact_language_check="ok",
              )
    # G11 4-3：_assemble_and_merge 现在同时返回人声缺口结果——
    # 那份结果原来只打日志、无人消费，现在要进收据。
    assembled, _voice_gaps = transcriber_v2._assemble_and_merge(
        route.actual_primary,
        route.qwen_raw,
        route.whisper_segments,
        route.qwen_l1_segments,
        [],
        "en",
        None,
        formal_qwen_words=route.source_words,
    )

    assert receipt.word_source == "external_forced_alignment"
    assert receipt.source_words == [
        {"text": "hello", "start": 0.05, "end": 0.45},
        {"text": "world", "start": 0.55, "end": 1.15},
    ]
    assert [segment.text for segment in assembled] == ["hello world"]
    assert assembled[0].words == [
        {
            "word": "hello", "text": "hello",
            "start": 0.05, "end": 0.45,
        },
        {
            "word": "world", "text": "world",
            "start": 0.55, "end": 1.15,
        },
    ]


def test_pytorch_qwen_alignment_failure_falls_to_whisper_formal_words(
    monkeypatch,
):
    from backend import transcriber_v2

    qwen_chunks = [{
        "text": "wrong qwen text",
        "segments": [],
        "t_start": 0.0,
        "t_end": 1.0,
    }]
    whisper = transcriber_v2.Segment(0, 0.0, 1.0, "usable whisper")
    whisper.words = [
        {"word": "usable", "start": 0.0, "end": 0.4},
        {"word": "whisper", "start": 0.5, "end": 1.0},
    ]
    monkeypatch.setattr(
        transcriber_v2,
        "_collect_qwen_chunks",
        lambda *_args, **_kwargs: _Outcome(complete=True, chunks=qwen_chunks),
    )
    monkeypatch.setattr(
        transcriber_v2, "_qwen_is_reliable", lambda _chunks: True
    )
    monkeypatch.setattr(
        transcriber_v2,
        "_whisper_is_usable",
        lambda *_args, **_kwargs: True,
    )

    route = transcriber_v2._resolve_routing(
        None, {}, "precise", True, "/unused.wav", "en", "/unused-cache",
        None, True, [whisper],
    )
    transcriber_v2._finalize_routing_after_alignment(route, [], "en")
    receipt = transcriber_v2.TranscriptionReceipt(
        routed_primary=route.routed_primary,
        actual_primary=route.actual_primary,
        word_source=route.word_source,
        source_text=route.source_text,
        source_words=route.source_words,
                  language_decision=locked_decision(),
        artifact_language_check="ok",
              )

    assert route.routed_primary == "qwen"
    assert receipt.actual_primary == "whisper"
    assert receipt.word_source == "whisper_word_timestamps"
    assert [word["text"] for word in receipt.source_words] == [
        "usable", "whisper"
    ]


def test_whisper_fallback_assembly_discards_qwen_fa_working_copy():
    from backend import transcriber_v2

    whisper = transcriber_v2.Segment(
        0, 0.0, 1.0, "usable whisper"
    )
    whisper.words = [
        {"word": "usable", "start": 0.0, "end": 0.4},
        {"word": "whisper", "start": 0.5, "end": 1.0},
    ]
    qwen_fa_working_copy = transcriber_v2.Segment(
        0, 0.0, 1.0, "wrong qwen text"
    )

    # G11 4-3：_assemble_and_merge 现在同时返回人声缺口结果——
    # 那份结果原来只打日志、无人消费，现在要进收据。
    assembled, _voice_gaps = transcriber_v2._assemble_and_merge(
        "whisper",
        [{"text": "wrong qwen text", "segments": []}],
        [whisper],
        [qwen_fa_working_copy],
        [qwen_fa_working_copy],
        "en",
        None,
    )

    assert [segment.text for segment in assembled] == ["usable whisper"]
    assert assembled[0].words == whisper.words


@pytest.mark.parametrize("short_first", [True, False])
def test_short_fragment_merge_preserves_every_source_word(short_first):
    from backend.transcriber_v2 import Segment, merge_short_subtitle_fragments

    short = Segment(0, 0.0, 0.2, "Oh")
    short.words = [{"text": "Oh", "start": 0.0, "end": 0.2}]
    long = Segment(1, 0.3, 2.0, "this is longer")
    long.words = [
        {"text": "this", "start": 0.3, "end": 0.6},
        {"text": "is", "start": 0.7, "end": 0.9},
        {"text": "longer", "start": 1.0, "end": 2.0},
    ]
    segments = [short, long] if short_first else [long, short]
    if not short_first:
        long.id = 0
        long.start, long.end = 0.0, 1.7
        for word, start, end in zip(
            long.words,
            (0.0, 0.4, 0.7),
            (0.3, 0.6, 1.7),
        ):
            word["start"], word["end"] = start, end
        short.id = 1
        short.start, short.end = 1.8, 2.0
        short.words[0]["start"], short.words[0]["end"] = 1.8, 2.0
    expected_words = copy.deepcopy(
        short.words + long.words if short_first else long.words + short.words
    )

    result = merge_short_subtitle_fragments(segments, "en")

    assert len(result) == 1
    assert result[0].words == expected_words


def test_transcription_envelope_binds_segments_to_primary_receipt():
    from backend import transcriber_v2

    segment = transcriber_v2.Segment(0, 0.0, 1.0, "hello world")
    words = [
        {"text": "hello", "start": 0.0, "end": 0.4},
        {"text": "world", "start": 0.5, "end": 1.0},
    ]
    receipt = transcriber_v2.TranscriptionReceipt(
        routed_primary="whisper",
        actual_primary="whisper",
        word_source="whisper_word_timestamps",
        source_text="hello world",
        source_words=words,
                  language_decision=locked_decision(),
        artifact_language_check="ok",
              )
    envelope = transcriber_v2.TranscriptionEnvelope([segment], receipt)

    assert not isinstance(envelope, list)
    assert len(envelope) == 1
    assert envelope[0] is segment
    assert envelope.receipt.source_words == words
    assert envelope.receipt.source_fingerprint


def test_dual_failed_transcription_envelope_round_trips():
    from backend import transcriber_v2, worker

    segment = transcriber_v2.Segment(0, 0.0, 1.0, "[此时段字幕暂不可用]")
    receipt = transcriber_v2.TranscriptionReceipt(
        routed_primary="qwen",
        actual_primary="dual_failed",
        word_source="emergency_combined",
        source_text="",
        source_words=[],
                  language_decision=locked_decision(),
        artifact_language_check="ok",
              )
    envelope = transcriber_v2.TranscriptionEnvelope([segment], receipt)

    restored = worker._read_transcription_envelope(
        worker._transcription_envelope_payload(envelope)
    )

    assert restored is not None
    assert restored.receipt.routed_primary == "qwen"
    assert restored.receipt.actual_primary == "dual_failed"
    assert restored.receipt.word_source == "emergency_combined"


def test_dual_failed_without_formal_words_never_deletes_emergency_subtitles():
    from backend import transcriber_v2, worker

    emergency = transcriber_v2.Segment(
        0, 1.25, 4.75, "[此时段字幕暂不可用]"
    )
    emergency.uncertain = True
    receipt = transcriber_v2.TranscriptionReceipt(
        routed_primary="qwen",
        actual_primary="dual_failed",
        word_source="emergency_combined",
        source_text="",
        source_words=[],
                  language_decision=locked_decision(),
        artifact_language_check="ok",
              )
    envelope = transcriber_v2.TranscriptionEnvelope([emergency], receipt)
    before = [
        (segment.text, segment.start, segment.end)
        for segment in envelope.segments
    ]

    groups, fallback_reason = worker._prepare_sentence_groups(
        envelope,
    )

    assert [(group.text, group.start, group.end) for group in groups] == before
    assert fallback_reason == "双引擎均无可用正式词流"
    assert envelope.segments is groups


def test_transcription_receipt_rejects_source_text_that_drops_or_changes_words():
    from backend import transcriber_v2

    with pytest.raises(ValueError, match="主引擎收据原词不守恒"):
        transcriber_v2.TranscriptionReceipt(
            routed_primary="qwen",
            actual_primary="qwen",
            word_source="qwen_word_timestamps",
            source_text="Good.",
            source_words=[{"text": "Bad", "start": 0.0, "end": 1.0}],
            language_decision=locked_decision(),
        artifact_language_check="ok",
        )


def test_step_transcribe_persists_and_restores_one_v2_envelope(
    tmp_path, monkeypatch
):
    from backend import transcriber_v2, worker

    segment = transcriber_v2.Segment(0, 0.0, 1.0, "hello world")
    segment.words = [
        {"text": "hello", "start": 0.0, "end": 0.4},
        {"text": "world", "start": 0.5, "end": 1.0},
    ]
    receipt = transcriber_v2.TranscriptionReceipt(
        routed_primary="whisper",
        actual_primary="whisper",
        word_source="whisper_word_timestamps",
        source_text="hello world",
        source_words=segment.words,
                  language_decision=locked_decision(),
        artifact_language_check="ok",
              )
    expected = transcriber_v2.TranscriptionEnvelope([segment], receipt)
    calls = []

    def fake_transcribe(*_args, **_kwargs):
        calls.append("transcribed")
        return expected

    monkeypatch.setattr(worker, "transcribe_v2", fake_transcribe)

    class Checkpoint:
        def save(self, *_args, **_kwargs):
            pass

    transcript = tmp_path / "transcript.json"
    first = worker._step_transcribe(
        "start", str(tmp_path / "audio.wav"), transcript, "large-v2",
        False, "standard", tmp_path, {}, Checkpoint(),
    )
    archive = json.loads(transcript.read_text(encoding="utf-8"))
    second = worker._step_transcribe(
        "transcribe", str(tmp_path / "audio.wav"), transcript, "large-v2",
        False, "standard", tmp_path, {}, Checkpoint(),
    )

    assert archive["version"] == 2
    assert set(archive) == {"version", "receipt", "segments"}
    assert calls == ["transcribed"]
    assert first.receipt.source_fingerprint == second.receipt.source_fingerprint
    assert [word["text"] for word in second.receipt.source_words] == [
        "hello", "world"
    ]


@pytest.mark.parametrize(
    "damage",
    [
        "legacy_list",
        "truncated_json",
        "missing_receipt",
        "source_fingerprint_mismatch",
        "segments_fingerprint_mismatch",
    ],
)
def test_step_transcribe_rebuilds_every_invalid_or_legacy_envelope(
    tmp_path, monkeypatch, damage
):
    from backend import transcriber_v2, worker

    segment = transcriber_v2.Segment(0, 0.0, 1.0, "hello")
    segment.words = [{"text": "hello", "start": 0.0, "end": 1.0}]
    receipt = transcriber_v2.TranscriptionReceipt(
        routed_primary="whisper",
        actual_primary="whisper",
        word_source="whisper_word_timestamps",
        source_text="hello",
        source_words=segment.words,
                  language_decision=locked_decision(),
        artifact_language_check="ok",
              )
    expected = transcriber_v2.TranscriptionEnvelope([segment], receipt)
    valid_payload = worker._transcription_envelope_payload(expected)
    transcript = tmp_path / "transcript.json"
    if damage == "legacy_list":
        transcript.write_text(
            json.dumps(valid_payload["segments"]), encoding="utf-8"
        )
    elif damage == "truncated_json":
        transcript.write_text('{"version":2,"receipt":', encoding="utf-8")
    else:
        damaged = copy.deepcopy(valid_payload)
        if damage == "missing_receipt":
            damaged.pop("receipt")
        elif damage == "source_fingerprint_mismatch":
            damaged["receipt"]["source_fingerprint"] = "0" * 64
        elif damage == "segments_fingerprint_mismatch":
            damaged["segments"][0]["text"] = "changed"
        transcript.write_text(json.dumps(damaged), encoding="utf-8")

    calls = []
    monkeypatch.setattr(
        worker,
        "transcribe_v2",
        lambda *_args, **_kwargs: calls.append("transcribed") or expected,
    )
    monkeypatch.setattr(
        "backend.core.extractor.get_video_duration", lambda *_args: 1.0
    )

    class Checkpoint:
        def save(self, *_args, **_kwargs):
            pass

    rebuilt = worker._step_transcribe(
        "transcribe",
        str(tmp_path / "audio.wav"),
        transcript,
        "large-v2",
        False,
        "standard",
        tmp_path,
        {},
        Checkpoint(),
    )

    assert calls == ["transcribed"]
    assert rebuilt.receipt.source_fingerprint == receipt.source_fingerprint
    assert worker._read_transcription_envelope(
        json.loads(transcript.read_text(encoding="utf-8"))
    ) is not None


def test_boundary_cache_v8_rejects_a_changed_hint_fingerprint(tmp_path):
    from backend import worker

    cache_path = tmp_path / "sentence_boundaries.json"
    worker._p390_write_boundary_cache(
        cache_path,
        source_fingerprint="source",
        hint_fingerprint="hint-a",
        language="en",
        break_after_indices=[0],
        group_fingerprint="groups",
    )

    assert worker._P390_BOUNDARY_CACHE_VERSION == 9  # G12 第三批 B 升版本
    assert worker._p390_read_boundary_cache(
        cache_path,
        source_fingerprint="source",
        hint_fingerprint="hint-a",
        language="en",
        word_count=2,
    ) == ([0], "groups")
    assert worker._p390_read_boundary_cache(
        cache_path,
        source_fingerprint="source",
        hint_fingerprint="hint-b",
        language="en",
        word_count=2,
    ) is None


def test_prepare_sentence_groups_uses_receipt_not_existing_qwen_file(tmp_path):
    from backend import transcriber_v2, worker

    segment = transcriber_v2.Segment(0, 0.0, 2.0, "Whisper is primary")
    segment.words = [
        {"text": "Whisper", "start": 0.0, "end": 0.5},
        {"text": "is", "start": 0.6, "end": 0.8},
        {"text": "primary", "start": 0.9, "end": 2.0},
    ]
    receipt = transcriber_v2.TranscriptionReceipt(
        routed_primary="whisper",
        actual_primary="whisper",
        word_source="whisper_word_timestamps",
        source_text="Whisper is primary",
        source_words=segment.words,
                  language_decision=locked_decision(),
        artifact_language_check="ok",
              )
    qwen_raw = tmp_path / "qwen_raw.json"
    qwen_raw.write_text(json.dumps({
        "full_text": "wrong qwen stream",
        "word_timestamps": [
            {"text": "wrong", "start": 0.0, "end": 0.5},
            {"text": "qwen", "start": 0.6, "end": 0.9},
            {"text": "stream", "start": 1.0, "end": 2.0},
        ],
    }), encoding="utf-8")

    groups, _reason = worker._prepare_sentence_groups(
        transcriber_v2.TranscriptionEnvelope([segment], receipt),
        boundary_cache_path=tmp_path / "sentence_boundaries.json",
        hint_fingerprint="no-map",
    )

    assert " ".join(group.text for group in groups) == "Whisper is primary"
    assert "wrong" not in " ".join(group.text for group in groups)


def test_prepare_sentence_groups_has_no_qwen_file_source_selection_port():
    from backend import worker

    assert "raw_path" not in inspect.signature(
        worker._prepare_sentence_groups
    ).parameters


def test_smart_assemble_does_not_redecide_the_locked_primary(monkeypatch):
    from backend import transcriber_v2

    called = []
    monkeypatch.setattr(transcriber_v2, "_qwen_is_reliable", lambda _chunks: False)
    monkeypatch.setattr(
        transcriber_v2, "_whisper_is_usable", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        transcriber_v2,
        "_assemble_qwen_primary",
        lambda *_args, **_kwargs: called.append("qwen") or [],
    )
    monkeypatch.setattr(
        transcriber_v2,
        "_assemble_qwen_words_on_whisper_boundaries",
        lambda *_args, **_kwargs: called.append("whisper") or [],
    )

    transcriber_v2._smart_assemble(
        [{
            "text": "qwen",
            "segments": [{"text": "qwen", "start": 0.0, "end": 1.0}],
        }],
        [transcriber_v2.Segment(0, 0.0, 1.0, "whisper")],
        "ja",
    )

    assert called == ["qwen"]


def test_sound_reference_context_has_hints_or_visible_unavailable_summary():
    from backend import transcriber_v2, worker
    from backend.core import sound_map

    words = [
        {"text": "first", "start": 0.0, "end": 0.5},
        {"text": "second", "start": 3.4, "end": 4.0},
    ]
    receipt = transcriber_v2.TranscriptionReceipt(
        routed_primary="qwen",
        actual_primary="qwen",
        word_source="qwen_word_timestamps",
        source_text="first second",
        source_words=words,
                  language_decision=locked_decision(),
        artifact_language_check="ok",
              )
    identity = {
        "source_content_sha256": "a" * 64,
        "track_identity": {
            "index": 0,
            "codec": "aac",
            "sample_rate": 48000,
            "channels": 2,
        },
        "vad_asset_sha256": "b" * 64,
        "vad_parameters": sound_map.canonical_vad_parameters(),
    }
    ok_record = sound_map.make_map_record(identity, [], total_samples=64000)
    unavailable_identity = dict(
        identity,
        source_content_sha256="none",
        track_identity="none",
    )
    unavailable_record = sound_map.make_map_record(
        unavailable_identity,
        [],
        total_samples=0,
        map_status="unavailable",
        reason_code="input_unavailable",
    )

    used = worker._sound_reference_from_record(receipt, ok_record)
    unavailable = worker._sound_reference_from_record(
        receipt, unavailable_record
    )

    assert [hint.gap for hint in used.hints] == ["2.900"]
    assert used.public_summary == {
        "status": "used",
        "message": "",
        "reason_code": None,
    }
    assert unavailable.hints == ()
    assert unavailable.public_summary == {
        "status": "unavailable",
        "message": "声音参考未使用，字幕仍已生成。",
        "reason_code": "input_unavailable",
    }
    assert unavailable.hint_fingerprint != used.hint_fingerprint


def test_task_sound_map_recomputes_in_child_then_reuses_from_temp_cache(tmp_path):
    from backend import worker

    audio = tmp_path / "input.wav"
    cache = tmp_path / "cache"
    cache.mkdir()
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\0\0" * 1600)

    first, first_branch = worker._acquire_task_sound_map(audio, cache)
    second, second_branch = worker._acquire_task_sound_map(audio, cache)

    assert first_branch == "recomputed"
    assert second_branch == "reused"
    assert second == first
    assert [path.name for path in cache.iterdir()] == ["sound_map.json"]
    assert stat.S_IMODE((cache / "sound_map.json").stat().st_mode) == 0o600


def test_task_sound_map_rejects_same_path_replaced_after_transcription(
    tmp_path, monkeypatch
):
    from backend import worker

    media = tmp_path / "input.wav"
    cache = tmp_path / "cache"
    cache.mkdir()
    media.write_bytes(b"original-media")
    transcription_hash = worker.get_file_hash(media)
    media.write_bytes(b"replacement-media")
    monkeypatch.setattr(
        "backend.core.extractor.probe_audio_track_identity",
        lambda _path: {
            "index": 0,
            "codec": "pcm_s16le",
            "sample_rate": 16000,
            "channels": 1,
        },
    )
    monkeypatch.setattr(
        worker.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("内容已变时不得启动地图解码")
        ),
    )

    record, branch = worker._acquire_task_sound_map(
        media,
        cache,
        source_content_sha256=transcription_hash,
    )

    assert branch == "unavailable"
    assert record["reason_code"] == "source_changed_during_run"
    assert record["map_input_identity"]["source_content_sha256"] == "none"
    assert [path.name for path in cache.iterdir()] == ["sound_map.json"]


def test_task_sound_map_hash_failure_is_nonfatal_and_never_spawns_child(
    tmp_path, monkeypatch
):
    from backend import worker

    media = tmp_path / "input.wav"
    cache = tmp_path / "cache"
    cache.mkdir()
    media.write_bytes(b"media")
    monkeypatch.setattr(
        worker,
        "get_file_hash",
        lambda _path: (_ for _ in ()).throw(OSError("unreadable")),
    )
    monkeypatch.setattr(
        "backend.core.extractor.probe_audio_track_identity",
        lambda _path: {
            "index": 0,
            "codec": "pcm_s16le",
            "sample_rate": 16000,
            "channels": 1,
        },
    )
    monkeypatch.setattr(
        worker.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("哈希失败时不得启动地图解码")
        ),
    )

    record, branch = worker._acquire_task_sound_map(media, cache)

    assert branch == "unavailable"
    assert record["reason_code"] == "audio_track_unavailable"
    assert [path.name for path in cache.iterdir()] == ["sound_map.json"]


def test_compute_sound_map_reports_private_timing_and_peak_memory(
    monkeypatch, tmp_path
):
    sound_map = _sound_map_module()
    media = tmp_path / "input.wav"
    media.write_bytes(b"audio")
    identity = {
        "source_content_sha256": "a" * 64,
        "track_identity": {
            "index": 0,
            "codec": "pcm_s16le",
            "sample_rate": 16000,
            "channels": 1,
        },
        "vad_asset_sha256": "b" * 64,
        "vad_parameters": sound_map.canonical_vad_parameters(),
    }
    monkeypatch.setattr(
        "faster_whisper.audio.decode_audio",
        lambda *_args, **_kwargs: [0.0] * 16000,
    )
    monkeypatch.setattr(
        "faster_whisper.vad.get_speech_timestamps",
        lambda *_args, **_kwargs: [{"start": 0, "end": 8000}],
    )
    diagnostics = {}

    record = sound_map.compute_sound_map(
        media,
        map_input_identity=identity,
        diagnostics=diagnostics,
    )

    assert record["intervals"] == [{"start_sample": 0, "end_sample": 8000}]
    assert set(diagnostics) == {
        "decode_seconds", "vad_seconds", "total_seconds", "peak_memory_bytes"
    }
    assert diagnostics["decode_seconds"] >= 0
    assert diagnostics["vad_seconds"] >= 0
    assert diagnostics["total_seconds"] >= (
        diagnostics["decode_seconds"] + diagnostics["vad_seconds"]
    )
    assert diagnostics["peak_memory_bytes"] is None or (
        type(diagnostics["peak_memory_bytes"]) is int
        and diagnostics["peak_memory_bytes"] > 0
    )


def test_sound_map_child_exits_when_its_parent_pipe_closes(tmp_path):
    from backend.core.safe_archive import write_json_archive

    request = tmp_path / "request.json"
    result = tmp_path / "result.json"
    write_json_archive(
        request,
        {
            "version": 1,
            "media_path": str(tmp_path / "unused.wav"),
            "map_input_identity": {
                "source_content_sha256": "0" * 64,
                "track_identity": {
                    "index": 0,
                    "codec": "pcm_s16le",
                    "sample_rate": 16000,
                    "channels": 1,
                },
                "vad_asset_sha256": "1" * 64,
                "vad_parameters": _sound_map_module().canonical_vad_parameters(),
            },
        },
        chmod=0o600,
        allow_nan=False,
    )
    script = (
        "import time\n"
        "from backend.core import sound_map\n"
        "sound_map.compute_sound_map = lambda _path, **_kwargs: time.sleep(60)\n"
        f"raise SystemExit(sound_map._run_cli({str(request)!r}, {str(result)!r}))\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=str(Path(__file__).parents[1]),
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.2)
    assert process.stdin is not None
    process.stdin.close()

    assert process.wait(timeout=5) == 72
    assert not result.exists()


def test_task_sound_map_timeout_kills_and_reaps_the_fixed_child(
    tmp_path, monkeypatch
):
    from backend import worker

    audio = tmp_path / "input.wav"
    cache = tmp_path / "cache"
    cache.mkdir()
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\0\0" * 1600)

    calls = []

    class FakeStdin:
        closed = False

        def close(self):
            self.closed = True

    class FakeProcess:
        def __init__(self, command, **kwargs):
            self.command = command
            self.kwargs = kwargs
            self.stdin = FakeStdin()
            self.killed = False
            self.wait_count = 0
            calls.append(self)

        def wait(self, timeout=None):
            self.wait_count += 1
            if self.wait_count == 1:
                raise subprocess.TimeoutExpired(self.command, timeout)
            return -9

        def kill(self):
            self.killed = True

        def poll(self):
            return None if not self.killed else -9

    monkeypatch.setattr(worker.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(worker, "_SOUND_MAP_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(
        "backend.core.extractor.probe_audio_track_identity",
        lambda _path: {
            "index": 0,
            "codec": "pcm_s16le",
            "sample_rate": 16000,
            "channels": 1,
        },
    )

    record, branch = worker._acquire_task_sound_map(audio, cache)

    child = calls[0]
    assert child.command[:3] == [
        sys.executable,
        "-m",
        "backend.core.sound_map",
    ]
    assert child.kwargs["shell"] is False
    assert child.killed is True
    assert child.wait_count == 2
    assert child.stdin.closed is True
    assert branch == "unavailable"
    assert record["reason_code"] == "map_compute_failed"
    assert [path.name for path in cache.iterdir()] == ["sound_map.json"]


@pytest.mark.parametrize(
    "failure_mode",
    ["nonzero", "missing_result", "invalid_result", "spawn_error"],
)
def test_task_sound_map_child_failures_are_visible_but_nonfatal(
    tmp_path, monkeypatch, failure_mode
):
    from backend import worker

    audio = tmp_path / "input.wav"
    cache = tmp_path / "cache"
    cache.mkdir()
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\0\0" * 1600)
    monkeypatch.setattr(
        "backend.core.extractor.probe_audio_track_identity",
        lambda _path: {
            "index": 0,
            "codec": "pcm_s16le",
            "sample_rate": 16000,
            "channels": 1,
        },
    )

    class FakeStdin:
        def close(self):
            return None

    class FakeProcess:
        def __init__(self, command):
            self.command = command
            self.stdin = FakeStdin()

        def wait(self, timeout=None):
            if failure_mode == "invalid_result":
                result_path = Path(
                    self.command[self.command.index("--result") + 1]
                )
                result_path.write_text("{not-json", encoding="utf-8")
            return 4 if failure_mode == "nonzero" else 0

        def poll(self):
            return 4 if failure_mode == "nonzero" else 0

        def kill(self):
            return None

    if failure_mode == "spawn_error":
        monkeypatch.setattr(
            worker.subprocess,
            "Popen",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("spawn")),
        )
    else:
        monkeypatch.setattr(
            worker.subprocess,
            "Popen",
            lambda command, **_kwargs: FakeProcess(command),
        )

    record, branch = worker._acquire_task_sound_map(audio, cache)

    assert branch == "unavailable"
    assert record["map_status"] == "unavailable"
    assert record["reason_code"] == "map_compute_failed"
    assert [path.name for path in cache.iterdir()] == ["sound_map.json"]


def test_task_sound_map_save_failure_is_visible_but_nonfatal(
    tmp_path, monkeypatch
):
    from backend import worker

    audio = tmp_path / "input.wav"
    cache = tmp_path / "cache"
    cache.mkdir()
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\0\0" * 1600)
    monkeypatch.setattr(
        "backend.core.sound_map.save_map_record",
        lambda *_args, **_kwargs: False,
    )

    record, branch = worker._acquire_task_sound_map(audio, cache)

    assert branch == "unavailable"
    assert record["map_status"] == "unavailable"
    assert record["reason_code"] == "map_save_failed"
    assert list(cache.iterdir()) == []


def test_missing_bundled_vad_asset_is_visible_but_never_fails_subtitles(
    tmp_path, monkeypatch
):
    from backend import worker

    media = tmp_path / "input.wav"
    cache = tmp_path / "cache"
    cache.mkdir()
    media.write_bytes(b"media")
    monkeypatch.setattr(
        "backend.core.sound_map.bundled_vad_asset_path",
        lambda: (_ for _ in ()).throw(RuntimeError("asset missing")),
    )
    monkeypatch.setattr(
        "backend.core.extractor.probe_audio_track_identity",
        lambda _path: {
            "index": 0,
            "codec": "pcm_s16le",
            "sample_rate": 16000,
            "channels": 1,
        },
    )

    record, branch = worker._acquire_task_sound_map(media, cache)

    assert branch == "unavailable"
    assert record["map_status"] == "unavailable"
    assert record["reason_code"] == "vad_asset_unavailable"
    assert record["map_input_identity"]["vad_asset_sha256"] == "none"
    assert [path.name for path in cache.iterdir()] == ["sound_map.json"]


def test_receipt_boundary_cache_replays_and_rejects_moved_hint(tmp_path):
    from backend import transcriber_v2, worker
    from backend.core import sound_map

    words = [
        {"text": text, "start": index * 0.4, "end": index * 0.4 + 0.3}
        for index, text in enumerate(
            ["We", "found", "a", "hidden", "gem.", "It", "was", "great."]
        )
    ]
    segment = transcriber_v2.Segment(0, 0.0, 3.1, "We found a hidden gem. It was great.")
    segment.words = words
    receipt = transcriber_v2.TranscriptionReceipt(
        routed_primary="qwen",
        actual_primary="qwen",
        word_source="qwen_word_timestamps",
        source_text=segment.text,
        source_words=words,
                  language_decision=locked_decision(),
        artifact_language_check="ok",
              )
    cache_path = tmp_path / "sentence_boundaries.json"

    class Backend:
        def __init__(self):
            self.calls = 0

        def translate_raw_json(self, *_args, **_kwargs):
            self.calls += 1
            return '{"break_after_indices": [4]}'

    first_backend = Backend()
    first_hints = (sound_map.SoundHint(3, "2.000", "0.000"),)
    first_fp = sound_map.build_hint_fingerprint(
        {"test": "identity"}, "ok", "qwen", first_hints
    )
    first, _ = worker._prepare_sentence_groups(
        transcriber_v2.TranscriptionEnvelope([segment], receipt),
        boundary_cache_path=cache_path,
        sound_hints=first_hints,
        hint_fingerprint=first_fp,
        llm_boundary_backend=first_backend,
    )

    replay_backend = Backend()
    replay, _ = worker._prepare_sentence_groups(
        transcriber_v2.TranscriptionEnvelope([segment], receipt),
        boundary_cache_path=cache_path,
        sound_hints=first_hints,
        hint_fingerprint=first_fp,
        llm_boundary_backend=replay_backend,
    )
    moved_hints = (sound_map.SoundHint(2, "2.000", "0.000"),)
    moved_fp = sound_map.build_hint_fingerprint(
        {"test": "identity"}, "ok", "qwen", moved_hints
    )
    moved_backend = Backend()
    worker._prepare_sentence_groups(
        transcriber_v2.TranscriptionEnvelope([segment], receipt),
        boundary_cache_path=cache_path,
        sound_hints=moved_hints,
        hint_fingerprint=moved_fp,
        llm_boundary_backend=moved_backend,
    )

    assert first_backend.calls == 1
    assert replay_backend.calls == 0
    assert [(group.start, group.end, group.text) for group in replay] == [
        (group.start, group.end, group.text) for group in first
    ]
    assert moved_backend.calls == 1


@pytest.mark.parametrize(
    ("scenario", "map_kind", "response", "expected_first_group_words"),
    [
        # G12 第三批 B：`hidden ‖ gem.` 是半句中间的长停顿，A+ 规则 1 撤掉这一刀
        # （A+ §9「落在半句中间的长停顿不切」明确接受）；本条仍验词流不被人声证据改动。
        ("ai_accepts_hint", "ok", '{"break_after_indices": [3]}', 8),
        ("ai_ignores_hint", "ok", '{"break_after_indices": [4]}', 5),
        ("voice_conflict", "conflict", '{"break_after_indices": [4]}', 5),
        ("map_unavailable", "unavailable", '{"break_after_indices": [4]}', 5),
        # D-1（合同变更）：规则断句只剩「AI 回复格式两次不可用」这一种来源。
        ("rule_fallback", "ok", "这不是 JSON", None),
    ],
)
def test_sound_evidence_never_changes_the_translation_word_stream(
    tmp_path, scenario, map_kind, response, expected_first_group_words
):
    from backend import transcriber_v2, worker
    from backend.core import sound_map

    texts = ["We", "found", "a", "hidden", "gem.", "Then", "kept", "walking."]
    starts = [0.0, 0.4, 0.8, 1.2, 4.0, 4.4, 4.8, 5.2]
    words = [
        {"text": text, "start": start, "end": start + 0.3}
        for text, start in zip(texts, starts)
    ]
    source_words = copy.deepcopy(words)
    segment = transcriber_v2.Segment(
        0, words[0]["start"], words[-1]["end"], " ".join(texts)
    )
    segment.words = copy.deepcopy(words)
    receipt = transcriber_v2.TranscriptionReceipt(
        routed_primary="qwen",
        actual_primary="qwen",
        word_source="qwen_word_timestamps",
        source_text=segment.text,
        source_words=copy.deepcopy(words),
                  language_decision=locked_decision(),
        artifact_language_check="ok",
              )
    identity = {
        "source_content_sha256": "a" * 64,
        "track_identity": {
            "index": 0,
            "codec": "aac",
            "sample_rate": 48000,
            "channels": 2,
        },
        "vad_asset_sha256": "b" * 64,
        "vad_parameters": sound_map.canonical_vad_parameters(),
    }
    intervals = (
        [{"start_sample": 25600, "end_sample": 30400}]
        if map_kind == "conflict"
        else []
    )
    record = sound_map.make_map_record(
        identity,
        intervals,
        total_samples=96000,
        map_status="unavailable" if map_kind == "unavailable" else "ok",
        reason_code="map_compute_failed" if map_kind == "unavailable" else None,
    )
    sound_reference = worker._sound_reference_from_record(receipt, record)

    class Backend:
        def translate_raw_json(self, *_args, **_kwargs):
            if isinstance(response, Exception):
                raise response
            return response

    groups, fallback_reason = worker._prepare_sentence_groups(
        transcriber_v2.TranscriptionEnvelope([segment], receipt),
        boundary_cache_path=tmp_path / f"{scenario}.json",
        sound_hints=sound_reference.hints,
        hint_fingerprint=sound_reference.hint_fingerprint,
        llm_boundary_backend=Backend(),
    )

    translation_words = [
        word for group in groups for word in getattr(group, "words", [])
    ]
    assert receipt.source_words == source_words
    assert [
        (word["text"], word["start"], word["end"])
        for word in translation_words
    ] == [
        (word["text"], word["start"], word["end"])
        for word in source_words
    ]
    if expected_first_group_words is None:
        assert fallback_reason == "AI 答案未通过校验"   # D-1：回退只剩回复格式坏这一种来源
    else:
        assert fallback_reason is None
        assert len(groups[0].words) == expected_first_group_words
    if map_kind == "conflict":
        assert sound_reference.hints == ()
        assert len(sound_reference.conflicts) == 1
    elif map_kind == "unavailable":
        assert sound_reference.hints == ()
    else:
        assert [hint.word_index for hint in sound_reference.hints] == [3]


@pytest.mark.parametrize("terminal_type", ["done", "partial"])
def test_terminal_messages_persist_visible_unavailable_sound_summary(
    tmp_path, monkeypatch, terminal_type
):
    from backend import main

    jobs_dir = tmp_path / "jobs"
    job_id = "job123"
    job_dir = jobs_dir / job_id
    job_dir.mkdir(parents=True)
    srt = job_dir / "output.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nhello\n",
        encoding="utf-8",
    )
    meta = {
        "status": "running",
        "run_id": "run-1",
        "config": {"outputMode": "srt"},
    }
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(main, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {})
    monkeypatch.setattr(main, "JOB_ID_TO_HASH", {})
    job = {"status": "running", "progress": 50, "run_id": "run-1"}
    message = {
        "type": terminal_type,
        "result_srt": str(srt),
        "result_video": None,
        "output_ready": terminal_type == "partial",
        "sound_reference": {
            "status": "unavailable",
            "message": "/private/leak",
            "reason_code": "map_compute_failed",
        },
    }

    main._sync_current_job_message(job_id, job, message)

    expected = {
        "status": "unavailable",
        "message": "声音参考未使用，字幕仍已生成。",
        "reason_code": "map_compute_failed",
    }
    saved = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    assert job["status"] == terminal_type
    assert job["sound_reference"] == expected
    assert saved["sound_reference"] == expected
    public = main.job_state.build_public_task_state(job)
    assert public["result"]["quality"]["sound_reference"] == expected


def test_every_late_worker_terminal_can_attach_the_same_sound_summary():
    from backend import worker

    summary = {
        "status": "unavailable",
        "message": "声音参考未使用，字幕仍已生成。",
        "reason_code": "map_compute_failed",
    }

    assert worker._terminal_sound_fields(summary) == {
        "sound_reference": summary
    }


def test_public_task_payload_strips_private_sound_map_data_and_normalizes_summary():
    from backend import main

    payload = main._sanitize_task_payload({
        "sound_reference": {
            "status": "unavailable",
            "message": "leaked path /private/input.mkv",
            "reason_code": "unexpected-private-reason",
            "source_words": ["secret"],
        },
        "transcription_receipt": {"source_text": "secret"},
        "map_record": {"intervals": [[1, 2]]},
        "hint_fingerprint": "a" * 64,
        "hints": [{"word_index": 1}],
        "diagnostics": {"path": "/private/input.mkv"},
    })
    payload["sound_reference"] = main._normalize_sound_reference(
        payload["sound_reference"]
    )

    assert payload == {
        "sound_reference": {
            "status": "unavailable",
            "message": "声音参考未使用，字幕仍已生成。",
            "reason_code": "unknown",
        }
    }


def test_new_run_clears_an_old_sound_summary_without_publishing_a_fake_one(
    monkeypatch
):
    from backend import main

    job = {
        "status": "running",
        "sound_reference": {
            "status": "unavailable",
            "message": "声音参考未使用，字幕仍已生成。",
            "reason_code": "map_compute_failed",
        },
    }
    job.update(main._empty_run_state())
    monkeypatch.setattr(main, "_validate_job_path", lambda *_args: None)

    public = main.job_state.build_public_task_state(job)

    assert job["sound_reference"] is None
    assert public["result"]["quality"]["sound_reference"] is None


def test_error_snapshot_never_claims_that_subtitles_were_generated(monkeypatch):
    from backend import main

    monkeypatch.setattr(main, "_validate_job_path", lambda *_args: None)
    public = main.job_state.build_public_task_state({
        "id": "job",
        "status": "error",
        "sound_reference": {
            "status": "unavailable",
            "message": "声音参考未使用，字幕仍已生成。",
            "reason_code": "map_compute_failed",
        },
    })

    assert public["result"]["quality"]["sound_reference"] is None


def test_resume_cache_accepts_the_v2_transcription_envelope(tmp_path, monkeypatch):
    from backend import main, transcriber_v2, worker

    job_dir = tmp_path / "job"
    cache = job_dir / "cache" / "abc123"
    cache.mkdir(parents=True)
    (cache / "checkpoint.json").write_text("{}", encoding="utf-8")
    segment = transcriber_v2.Segment(0, 0.0, 1.0, "hello")
    segment.words = [{"text": "hello", "start": 0.0, "end": 1.0}]
    receipt = transcriber_v2.TranscriptionReceipt(
        routed_primary="whisper",
        actual_primary="whisper",
        word_source="whisper_word_timestamps",
        source_text="hello",
        source_words=segment.words,
                  language_decision=locked_decision(),
        artifact_language_check="ok",
              )
    envelope = transcriber_v2.TranscriptionEnvelope([segment], receipt)
    (cache / "transcript.json").write_text(
        json.dumps(worker._transcription_envelope_payload(envelope)),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        main,
        "_matching_checkpoint",
        lambda *_args, **_kwargs: (
            cache / "checkpoint.json",
            {"stage": "transcribe"},
        ),
    )

    assert main._resume_cache_problem(job_dir, {}) is None


def test_restart_index_keeps_only_the_public_sound_reference_summary(
    tmp_path, monkeypatch
):
    from backend import main

    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "job123"
    job_dir.mkdir(parents=True)
    (job_dir / "meta.json").write_text(
        json.dumps({
            "status": "done",
            "file_hash": "a" * 64,
            "config": {"model": "large-v2"},
            "sound_reference": {
                "status": "unavailable",
                "message": "/private/leak",
                "reason_code": "map_compute_failed",
                "map_record": {"private": True},
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {})
    monkeypatch.setattr(main, "JOB_ID_TO_HASH", {})

    main._scan_jobs_into_index()

    entry = next(iter(main.GLOBAL_HASH_INDEX.values()))
    assert entry["sound_reference"] == {
        "status": "unavailable",
        "message": "声音参考未使用，字幕仍已生成。",
        "reason_code": "map_compute_failed",
    }


def test_done_checkpoint_recovery_keeps_the_public_sound_reference_summary(
    tmp_path, monkeypatch
):
    from backend import main

    job_dir = tmp_path / "job123"
    cache = job_dir / "cache" / "abc123"
    cache.mkdir(parents=True)
    srt = job_dir / "output.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nhello\n",
        encoding="utf-8",
    )
    checkpoint = cache / "checkpoint.json"
    checkpoint.write_text("{}", encoding="utf-8")
    data = {
        "stage": "done",
        "run_id": "run-1",
        "result_srt": str(srt),
        "result_video": None,
        "meta": {
            "sound_reference": {
                "status": "unavailable",
                "message": "/private/leak",
                "reason_code": "map_compute_failed",
            }
        },
    }
    monkeypatch.setattr(
        main,
        "_matching_checkpoint",
        lambda *_args, **_kwargs: (checkpoint, data),  # P516：断点匹配不再返回旧地址
    )

    recovered = main._recover_completed_checkpoint(
        job_dir,
        {
            "run_id": "run-1",
            "config": {"outputMode": "srt"},
        },
    )

    assert recovered["sound_reference"] == {
        "status": "unavailable",
        "message": "声音参考未使用，字幕仍已生成。",
        "reason_code": "map_compute_failed",
    }


def test_active_jobs_keeps_the_public_sound_reference_summary(monkeypatch):
    from backend import main

    job_id = "active-sound"
    summary = {
        "status": "unavailable",
        "message": "/private/input.mkv",
        "reason_code": "unexpected-private-reason",
        "source_content_sha256": "a" * 64,
        "intervals": [{"start_sample": 1, "end_sample": 2}],
    }
    monkeypatch.setattr(
        main,
        "GLOBAL_HASH_INDEX",
        {"hash": {"job_id": job_id, "status": "running", "config": {}}},
    )
    monkeypatch.setattr(
        main,
        "JOBS",
        {job_id: {"id": job_id, "status": "running", "sound_reference": summary}},
    )
    monkeypatch.setattr(main, "_is_recoverable_active_job", lambda *_args: True)
    monkeypatch.setattr(main, "_validate_job_path", lambda *_args: None)

    payload = main.get_active_jobs()

    assert payload["jobs"][0]["state"]["result"]["quality"]["sound_reference"] == {
        "status": "unavailable",
        "message": "声音参考未使用，字幕仍已生成。",
        "reason_code": "unknown",
    }


def test_done_dedup_response_uses_the_public_sound_reference_projection(
    tmp_path, monkeypatch
):
    from backend import main

    job_id = "done-sound"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    private_summary = {
        "status": "unavailable",
        "message": "/private/input.mkv",
        "reason_code": "map_compute_failed",
        "source_words": ["secret"],
    }
    (job_dir / "meta.json").write_text(
        json.dumps({
            "status": "done",
            "config": {"model": "large-v2"},
            "sound_reference": private_summary,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS", {})

    response = main._handle_existing_job(
        {
            "job_id": job_id,
            "status": "done",
            "config": {"model": "large-v2"},
            "sound_reference": private_summary,
        },
        None,
        job_dir / "input.mp4",
        {},
    )

    assert response["state"]["result"]["quality"]["sound_reference"] == {
        "status": "unavailable",
        "message": "声音参考未使用，字幕仍已生成。",
        "reason_code": "map_compute_failed",
    }
    assert set(response["state"]["result"]["quality"]["sound_reference"]) == {
        "status", "message", "reason_code"
    }
