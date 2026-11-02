"""声音地图：只把 VAD 人声区间换成断句参考证据。

这个模块不接收 Segment，也不会返回修改后的词、时间或字幕。
"""

import argparse
import bisect
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import threading
import time


_GAP_MINIMUM = Decimal("2.000")
_VOICE_CANCEL_THRESHOLD = Decimal("0.300")
_MILLISECOND = Decimal("0.001")
MAP_RECORD_VERSION = 1
MAP_PRODUCER_VERSION = 1
MAP_SAMPLING_RATE = 16000
HINT_FORMAT_VERSION = 1
HINT_RULE_VERSION = 1
SOUND_REFERENCE_UNAVAILABLE_MESSAGE = "声音参考未使用，字幕仍已生成。"
VAD_OPTIONS = {
    "speech_pad_ms": 0,
    "min_silence_duration_ms": 200,
    "threshold": 0.5,
    "neg_threshold": 0.35,
    "min_speech_duration_ms": 0,
    "max_speech_duration_s": math.inf,
}
_MAP_RECORD_FIELDS = {
    "version",
    "producer_version",
    "map_input_identity",
    "map_status",
    "sampling_rate",
    "total_samples",
    "intervals",
    "reason_code",
}
_MAP_RECORD_MAX_BYTES = 16 * 1024 * 1024
MAP_REASON_CODES = frozenset({
    "input_unavailable",
    "source_changed_during_run",
    "audio_track_unavailable",
    "vad_asset_unavailable",
    "map_compute_failed",
    "map_save_failed",
    "unknown",
})
_MAP_INPUT_IDENTITY_FIELDS = {
    "source_content_sha256",
    "track_identity",
    "vad_asset_sha256",
    "vad_parameters",
}
_TRACK_IDENTITY_FIELDS = {"index", "codec", "sample_rate", "channels"}
_DIAGNOSTIC_FIELDS = {
    "decode_seconds", "vad_seconds", "total_seconds", "peak_memory_bytes"
}


def canonical_json_bytes(payload) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_vad_parameters(overrides=None):
    raw = dict(VAD_OPTIONS)
    if overrides:
        if not isinstance(overrides, dict) or not set(overrides) <= set(raw):
            raise ValueError("VAD 参数非法")
        raw.update(overrides)

    for field, value in raw.items():
        if field == "max_speech_duration_s" and value == math.inf:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("VAD 参数非法")
        if not math.isfinite(value):
            raise ValueError("VAD 参数非法")

    return {
        "speech_pad_ms": int(raw["speech_pad_ms"]),
        "min_silence_duration_ms": int(raw["min_silence_duration_ms"]),
        "threshold": format(raw["threshold"], ".6f"),
        "neg_threshold": format(raw["neg_threshold"], ".6f"),
        "min_speech_duration_ms": int(raw["min_speech_duration_ms"]),
        "max_speech_duration_s": (
            "inf"
            if raw["max_speech_duration_s"] == math.inf
            else format(raw["max_speech_duration_s"], ".3f")
        ),
    }


def _sha256_file(path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_map_input_identity(
    source_path,
    track_identity,
    vad_asset_path,
    *,
    source_content_sha256=None,
    vad_asset_sha256=None,
):
    identity = {
        "source_content_sha256": (
            (source_content_sha256 or _sha256_file(source_path))
            if source_path is not None
            else "none"
        ),
        "track_identity": (
            dict(track_identity) if track_identity is not None else "none"
        ),
        "vad_asset_sha256": (
            vad_asset_sha256
            or (
                _sha256_file(vad_asset_path)
                if vad_asset_path is not None
                else "none"
            )
        ),
        "vad_parameters": canonical_vad_parameters(),
    }
    return validate_map_input_identity(identity)


def validate_map_input_identity(identity):
    if not isinstance(identity, dict) or set(identity) != _MAP_INPUT_IDENTITY_FIELDS:
        raise ValueError("地图输入身份无效")
    source_hash = identity["source_content_sha256"]
    asset_hash = identity["vad_asset_sha256"]
    if not isinstance(source_hash, str) or (
        source_hash != "none"
        and (len(source_hash) != 64 or any(char not in "0123456789abcdef" for char in source_hash))
    ):
        raise ValueError("地图输入身份无效")
    if (
        not isinstance(asset_hash, str)
        or (
            asset_hash != "none"
            and (
                len(asset_hash) != 64
                or any(char not in "0123456789abcdef" for char in asset_hash)
            )
        )
    ):
        raise ValueError("地图输入身份无效")
    track = identity["track_identity"]
    if track != "none":
        if not isinstance(track, dict) or set(track) != _TRACK_IDENTITY_FIELDS:
            raise ValueError("地图输入身份无效")
        if (
            type(track["index"]) is not int
            or track["index"] < 0
            or not isinstance(track["codec"], str)
            or not track["codec"]
            or type(track["sample_rate"]) is not int
            or track["sample_rate"] <= 0
            or type(track["channels"]) is not int
            or track["channels"] <= 0
        ):
            raise ValueError("地图输入身份无效")
    if (source_hash == "none") != (track == "none"):
        raise ValueError("地图输入身份无效")
    if identity["vad_parameters"] != canonical_vad_parameters():
        raise ValueError("地图输入身份无效")
    return identity


def make_map_record(
    map_input_identity,
    intervals,
    *,
    total_samples: int,
    map_status: str = "ok",
    reason_code=None,
):
    record = {
        "version": MAP_RECORD_VERSION,
        "producer_version": MAP_PRODUCER_VERSION,
        "map_input_identity": validate_map_input_identity(map_input_identity),
        "map_status": map_status,
        "sampling_rate": MAP_SAMPLING_RATE,
        "total_samples": total_samples,
        "intervals": [dict(interval) for interval in intervals],
        "reason_code": reason_code,
    }
    return validate_map_record(record, expected_identity=map_input_identity)


def validate_map_record(record, *, expected_identity=None):
    if not isinstance(record, dict) or set(record) != _MAP_RECORD_FIELDS:
        raise ValueError("地图存档字段无效")
    if (
        record["version"] != MAP_RECORD_VERSION
        or record["producer_version"] != MAP_PRODUCER_VERSION
        or record["sampling_rate"] != MAP_SAMPLING_RATE
        or record["map_status"] not in {"ok", "unavailable"}
    ):
        raise ValueError("地图存档字段无效")
    if expected_identity is not None and record["map_input_identity"] != expected_identity:
        raise ValueError("地图存档身份不符")
    validate_map_input_identity(record["map_input_identity"])
    total_samples = record["total_samples"]
    if type(total_samples) is not int or total_samples < 0:
        raise ValueError("地图存档字段无效")
    intervals = record["intervals"]
    if not isinstance(intervals, list):
        raise ValueError("地图存档字段无效")
    interval_limit = math.ceil(total_samples / (MAP_SAMPLING_RATE * 0.2)) + 1
    if len(intervals) > interval_limit:
        raise ValueError("地图存档区间过多")
    for interval in intervals:
        if not isinstance(interval, dict) or set(interval) != {
            "start_sample", "end_sample"
        }:
            raise ValueError("地图存档区间无效")
        start = interval["start_sample"]
        end = interval["end_sample"]
        if (
            type(start) is not int
            or type(end) is not int
            or start < 0
            or end < start
            or end > total_samples
        ):
            raise ValueError("地图存档区间无效")
    if record["map_status"] == "unavailable" and intervals:
        raise ValueError("不可用地图不得包含区间")
    if (
        record["map_status"] == "ok"
        and record["map_input_identity"]["vad_asset_sha256"] == "none"
    ):
        raise ValueError("可用地图必须绑定 VAD 资产")
    if record["map_status"] == "ok" and record["reason_code"] is not None:
        raise ValueError("可用地图不得包含失败原因")
    if (
        record["map_status"] == "unavailable"
        and record["reason_code"] not in MAP_REASON_CODES
    ):
        raise ValueError("地图失败原因无效")
    return record


def save_map_record(path, record) -> bool:
    from backend.core.safe_archive import write_json_archive

    try:
        validate_map_record(record)
    except ValueError:
        return False
    return write_json_archive(
        path,
        record,
        chmod=0o600,
        allow_nan=False,
    )


def load_map_record(path, *, expected_identity):
    from backend.core.safe_archive import read_json_archive

    record = read_json_archive(path, max_bytes=_MAP_RECORD_MAX_BYTES)
    if record is None:
        return None
    try:
        return validate_map_record(record, expected_identity=expected_identity)
    except ValueError:
        return None


def validate_sound_map_diagnostics(value):
    if not isinstance(value, dict) or set(value) != _DIAGNOSTIC_FIELDS:
        raise ValueError("声音地图诊断无效")
    for field in ("decode_seconds", "vad_seconds", "total_seconds"):
        number = value[field]
        if (
            isinstance(number, bool)
            or not isinstance(number, (int, float))
            or not math.isfinite(number)
            or number < 0
        ):
            raise ValueError("声音地图诊断无效")
    peak = value["peak_memory_bytes"]
    if peak is not None and (type(peak) is not int or peak <= 0):
        raise ValueError("声音地图诊断无效")
    if value["total_seconds"] + 1e-9 < (
        value["decode_seconds"] + value["vad_seconds"]
    ):
        raise ValueError("声音地图诊断无效")
    return value


def _peak_memory_bytes():
    try:
        import resource

        peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if peak <= 0:
            return None
        return peak if sys.platform == "darwin" else peak * 1024
    except (ImportError, OSError, ValueError):
        return None


def bundled_vad_asset_path() -> Path:
    from faster_whisper.vad import get_assets_path

    asset = Path(get_assets_path()) / "silero_vad_v6.onnx"
    if not asset.is_file():
        raise RuntimeError("包内 Silero VAD 资源不可用")
    return asset


def compute_sound_map(media_path, *, map_input_identity=None, diagnostics=None):
    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    from backend.core.extractor import probe_audio_track_identity

    if diagnostics is not None and not isinstance(diagnostics, dict):
        raise ValueError("声音地图诊断容器无效")
    started = time.monotonic()
    media_path = Path(media_path)
    if map_input_identity is None:
        asset_path = bundled_vad_asset_path()
        track_identity = probe_audio_track_identity(media_path)
        identity = build_map_input_identity(media_path, track_identity, asset_path)
    else:
        identity = validate_map_input_identity(map_input_identity)
    decode_started = time.monotonic()
    audio = decode_audio(str(media_path), sampling_rate=MAP_SAMPLING_RATE)
    decode_seconds = time.monotonic() - decode_started
    options = VadOptions(**VAD_OPTIONS)
    vad_started = time.monotonic()
    raw_intervals = get_speech_timestamps(
        audio,
        options,
        sampling_rate=MAP_SAMPLING_RATE,
    )
    vad_seconds = time.monotonic() - vad_started
    intervals = [
        {
            "start_sample": int(interval["start"]),
            "end_sample": int(interval["end"]),
        }
        for interval in raw_intervals
    ]
    record = make_map_record(
        identity,
        intervals,
        total_samples=len(audio),
    )
    if diagnostics is not None:
        diagnostics.update({
            "decode_seconds": decode_seconds,
            "vad_seconds": vad_seconds,
            "total_seconds": time.monotonic() - started,
            "peak_memory_bytes": _peak_memory_bytes(),
        })
        validate_sound_map_diagnostics(diagnostics)
    return record


@dataclass(frozen=True)
class SoundHint:
    word_index: int
    gap: str
    voiced_total: str


@dataclass(frozen=True)
class SoundHintResult:
    hints: tuple[SoundHint, ...]
    conflicts: tuple[SoundHint, ...]


def _decimal(value) -> Decimal:
    return Decimal(str(value))


def _three_decimals(value: Decimal) -> str:
    return format(value.quantize(_MILLISECOND, rounding=ROUND_HALF_UP), ".3f")


def _merge_voice_intervals(intervals, sampling_rate: int):
    normalized = []
    for interval in intervals:
        start_sample = int(interval["start_sample"])
        end_sample = int(interval["end_sample"])
        if start_sample < 0 or end_sample < start_sample:
            raise ValueError("VAD 区间无效")
        normalized.append(
            (
                Decimal(start_sample) / sampling_rate,
                Decimal(end_sample) / sampling_rate,
            )
        )
    normalized.sort()
    merged = []
    for start, end in normalized:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def build_sound_hints(
    words, intervals, *, sampling_rate: int = MAP_SAMPLING_RATE
):
    """返回词间疑似停顿与时间冲突，不修改输入词流。"""
    voice_intervals = _merge_voice_intervals(intervals, sampling_rate)
    voice_starts = [start for start, _end in voice_intervals]
    voice_ends = [end for _start, end in voice_intervals]
    voice_prefix = [Decimal(0)]
    for start, end in voice_intervals:
        voice_prefix.append(voice_prefix[-1] + end - start)
    hints = []
    conflicts = []
    for word_index, (left, right) in enumerate(zip(words, words[1:])):
        gap_start = _decimal(left["end"])
        gap_end = _decimal(right["start"])
        gap = gap_end - gap_start
        if gap < _GAP_MINIMUM:
            continue
        first = bisect.bisect_right(voice_ends, gap_start)
        after_last = bisect.bisect_left(voice_starts, gap_end)
        if first >= after_last:
            voiced_total = Decimal(0)
        else:
            voiced_total = voice_prefix[after_last] - voice_prefix[first]
            voiced_total -= max(Decimal(0), gap_start - voice_starts[first])
            voiced_total -= max(
                Decimal(0), voice_ends[after_last - 1] - gap_end
            )
        voiced_text = _three_decimals(voiced_total)
        evidence = SoundHint(
            word_index=word_index,
            gap=_three_decimals(gap),
            voiced_total=voiced_text,
        )
        if Decimal(voiced_text) >= _VOICE_CANCEL_THRESHOLD:
            conflicts.append(evidence)
        else:
            hints.append(evidence)
    return SoundHintResult(hints=tuple(hints), conflicts=tuple(conflicts))


def build_hint_fingerprint(
    map_input_identity,
    map_status: str,
    primary_engine: str,
    hints,
) -> str:
    if map_status not in {"ok", "unavailable"}:
        raise ValueError("地图状态无效")
    normalized_hints = []
    for hint in hints or ():
        if (
            type(getattr(hint, "word_index", None)) is not int
            or not isinstance(getattr(hint, "gap", None), str)
        ):
            raise ValueError("声音提示无效")
        normalized_hints.append(
            {
                "word_index": hint.word_index,
                "gap": hint.gap,
            }
        )
    payload = {
        "map_input_identity": map_input_identity,
        "map_status": map_status,
        "primary_engine": str(primary_engine),
        "hints": normalized_hints,
        "voice_cancel_threshold": format(_VOICE_CANCEL_THRESHOLD, ".3f"),
        "hint_format_version": HINT_FORMAT_VERSION,
        "hint_rule_version": HINT_RULE_VERSION,
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _watch_parent_pipe():
    try:
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        stream.read(1)
    except Exception:
        pass
    os._exit(72)


def _run_cli(request_path, result_path) -> int:
    from backend.core.safe_archive import read_json_archive, write_json_archive

    request_path = Path(request_path)
    result_path = Path(result_path)
    try:
        if request_path.parent.resolve() != result_path.parent.resolve():
            return 2
        if request_path.is_symlink() or result_path.is_symlink():
            return 2
        request = read_json_archive(request_path, max_bytes=64 * 1024)
        if (
            not isinstance(request, dict)
            or set(request) != {"version", "media_path", "map_input_identity"}
            or request["version"] != 1
            or not isinstance(request["media_path"], str)
        ):
            return 2
        identity = validate_map_input_identity(request["map_input_identity"])
        watchdog = threading.Thread(target=_watch_parent_pipe, daemon=True)
        watchdog.start()
        diagnostics = {}
        record = compute_sound_map(
            request["media_path"],
            map_input_identity=identity,
            diagnostics=diagnostics,
        )
        result = {
            "version": 1,
            "map_record": record,
            "diagnostics": validate_sound_map_diagnostics(diagnostics),
        }
        return 0 if write_json_archive(
            result_path, result, chmod=0o600, allow_nan=False
        ) else 3
    except Exception:
        return 4


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args(argv)
    return _run_cli(args.request, args.result)


if __name__ == "__main__":
    os._exit(main())
