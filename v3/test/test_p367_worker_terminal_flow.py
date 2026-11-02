"""P367：worker 翻译后必须走 done 或 partial 的真实终态分叉。"""

import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from test import locked_decision

import pytest

from backend import worker


# 交付前的核验用的是父进程那一套：字幕要打开读到时间轴、视频要 ffprobe 认得。
# 替身写占位字符串会被拒，测试必须造真东西。
VALID_SRT = "1\n00:00:00,000 --> 00:00:01,000\n你好\n"


def _write_probe_video(path: Path) -> None:
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-f", "lavfi",
         "-i", "color=c=black:s=32x32:d=0.1", "-y", str(path)],
        capture_output=True,
    )
    if result.returncode != 0 or not path.is_file():
        pytest.skip("本机没有可用的 ffmpeg，跳过需要真实成品的终态测试")


class FakeCheckpoint:
    instances = []
    events = []

    def __init__(self, path, video_path, **_kwargs):
        self.saved = []
        self.__class__.instances.append(self)

    def load(self):
        return "translate"

    def save(self, stage, meta=None, *, run_id=None, result_srt=None,
             result_video=None, output_mode=None):
        # A9：身份与成品路径已并入 save()，桩要跟着走同一个签名。
        # 外审第四轮①：`output_mode` 记的是**实际**交付方式（音频输入会被
        # 收敛成 srt），恢复端认它而不是 config——桩同样要跟着改签名。
        self.saved.append((stage, dict(meta or {})))
        self.__class__.events.append("checkpoint:" + stage)


def _run_worker(
    monkeypatch, tmp_path, *, accept=False, retry=False, all_success=False,
    reject_accept=False, terminal_failure=None,
):
    job_dir = tmp_path / "job"
    FakeCheckpoint.events = []
    job_dir.mkdir()
    save_dir = tmp_path / "user-save"
    save_dir.mkdir(exist_ok=True)
    # P388：Worker 必须由父进程授予运行身份，否则在写任何任务文件前就拒绝。
    monkeypatch.setenv("VIDEO_SUBBER_RUN_ID", "run-1")
    # 铁律 7：Worker 送货前后都要比对 meta.json 的权威 run_id。父进程在启动
    # Worker 之前就把它写进 meta.json（main.py 的 _write_json_atomic 早于
    # worker_manager.start），测试必须还原这个前置条件。
    (job_dir / "meta.json").write_text(
        json.dumps({"status": "running", "run_id": "run-1"}), encoding="utf-8"
    )
    video = job_dir / "input.mp4"
    video.write_bytes(b"video")
    config = {
        "video_path": str(video),
        "video_filename": "input.mp4",
        "model": "large-v2",
        "translationMode": "speed",
        "translationPriority": ["google"],
        "apiKeys": [],
        "contextAware": False,
        "outputMode": "mux",
        "subtitles": "no",
        # 新流程下 Worker 真的会送货，savePath 必须留在 tmp_path 内，
        # 否则测试会往用户的 ~/Downloads/Video-Subber 里写垃圾。
        "savePath": str(save_dir),
    }
    if accept:
        config["_accept_partial"] = True
    if retry:
        config["_partial_retry"] = True
        config["_retry_started_at"] = 100.0
        config["_retry_initial_failed_count"] = 1
    if accept or retry:
        config["_retry_count"] = 2
        config["_previous_failed_keys"] = [[1, 1.0, 2.0]]
        config["_consecutive_no_progress"] = 1
    config_path = job_dir / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    if reject_accept:
        (job_dir / "meta.json").write_text(json.dumps({
            "status": "partial",
            "run_id": "run-1",
            "segment_count": 2,
            "success_count": 1,
            "failed_count": 1,
            "output_ready": False,
            "failed_keys": [[1, 1.0, 2.0]],
            "retry_count": 1,
            "consecutive_no_progress": 0,
            "failure_kind_counts": {"timeout": 1},
            "primary_failure_kind": "timeout",
        }), encoding="utf-8")
    cache = job_dir / "cache" / "hash"
    cache.mkdir(parents=True)
    segments = [
        SimpleNamespace(
            id=0, start=0.0, end=1.0, text="one", zh="", uncertain=False,
            speaker_change=False,
            words=[{"text": "one", "start": 0.0, "end": 1.0}],
        ),
        SimpleNamespace(
            id=1, start=1.0, end=2.0, text="two", zh="", uncertain=False,
            speaker_change=False,
            words=[{"text": "two", "start": 1.0, "end": 2.0}],
        ),
    ]
    receipt = worker.TranscriptionReceipt(
        routed_primary="whisper",
        actual_primary="whisper",
        word_source="whisper_word_timestamps",
        source_text="one two",
        source_words=[word for segment in segments for word in segment.words],
                  language_decision=locked_decision(),
        artifact_language_check="ok",
              )
    transcription = worker.TranscriptionEnvelope(segments, receipt)
    old_srt = job_dir / "input.srt"
    old_mux = job_dir / "input_zh_sub.mp4"
    if retry:
        old_srt.write_text("old", encoding="utf-8")
        old_mux.write_bytes(b"old")
    calls = {"subtitle": 0, "mux": 0, "old_removed": None}
    output = io.StringIO()

    monkeypatch.setattr(worker, "_real_stdout", output)
    monkeypatch.setattr(worker.sys, "argv", ["worker", str(config_path)])
    monkeypatch.setattr(worker, "_setup_file_log", lambda job_id: ("user", "debug"))
    monkeypatch.setattr(worker, "Checkpoint", FakeCheckpoint)
    monkeypatch.setattr(
        worker, "find_versioned_cache",
        lambda base, path, **_kwargs: (cache, "hash"),
    )
    monkeypatch.setattr(worker, "cleanup_old_versions", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "_step_extract_audio", lambda *args: (1920, 1080, "has_audio"))
    monkeypatch.setattr(worker, "_validate_output_audio", lambda *args: "has_audio")
    monkeypatch.setattr(
        worker, "_step_transcribe", lambda *args: transcription
    )
    monkeypatch.setattr(
        worker,
        "_prepare_sentence_groups",
        lambda envelope, **_kwargs: (envelope.segments, None),
    )
    from backend.core import sound_map

    def fake_acquire_sound_map(
        _media_path, _cache_dir, *, source_content_sha256=None
    ):
        identity = {
            "source_content_sha256": source_content_sha256,
            "track_identity": {
                "index": 0,
                "codec": "aac",
                "sample_rate": 48000,
                "channels": 2,
            },
            "vad_asset_sha256": "b" * 64,
            "vad_parameters": sound_map.canonical_vad_parameters(),
        }
        return sound_map.make_map_record(
            identity,
            [],
            total_samples=0,
            map_status="unavailable",
            reason_code="map_compute_failed",
        ), "unavailable"

    monkeypatch.setattr(
        worker, "_acquire_task_sound_map", fake_acquire_sound_map
    )
    monkeypatch.setattr(worker, "_run_transcript_refiner", lambda *args, **kwargs: None)

    def fake_translate(*args, **kwargs):
        if reject_accept:
            error_type = getattr(worker, "PartialActionRejected", RuntimeError)
            raise error_type("翻译缓存与当前字幕不匹配，不能直接导出")
        if terminal_failure == "ordinary":
            raise RuntimeError("受控普通异常")
        segments[0].zh = "成功甲"
        segments[1].zh = "成功乙" if all_success else "[翻译失败]"
        failed = [] if all_success else [(1, "two", "[翻译失败]")]
        failures = {} if all_success else {1: "timeout"}
        return failed, {}, 2 if all_success else 1, failures

    def fake_subtitle(*args):
        calls["subtitle"] += 1
        if terminal_failure == "subtitle":
            from backend.core.subtitler import SubtitleOutputError

            raise SubtitleOutputError(
                stage="subtitle",
                failure_kind="output_write_failed",
                user_message="字幕写入失败。",
                technical_detail="受控字幕异常",
            )
        if retry or accept:
            calls["old_removed"] = not old_srt.exists() and not old_mux.exists()
        # 必须写真正带时间轴的字幕：交付前的核验用的是父进程那一套
        # （is_valid_srt_file 会打开读内容），占位字符串过不了。
        Path(args[3]).write_text(VALID_SRT, encoding="utf-8")

    def fake_mux(video_path, ass_path, srt_path, video_out, video_out_mux, burn, mux, cfg):
        calls["mux"] += 1
        # 同理，成品视频要 ffprobe 认得（is_valid_media_file）。
        _write_probe_video(Path(video_out_mux))

    monkeypatch.setattr(worker, "_step_translate", fake_translate)
    monkeypatch.setattr(worker, "_step_subtitle", fake_subtitle)
    monkeypatch.setattr(worker, "_step_burn_or_mux", fake_mux)
    if terminal_failure == "delivery":
        monkeypatch.setattr(
            worker,
            "_deliver_outputs",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                worker.DeliveryError("受控交付异常")
            ),
        )
    worker.main()
    messages = [json.loads(line) for line in output.getvalue().splitlines() if line.strip()]
    return messages, calls, FakeCheckpoint.instances[-1]


def test_unresolved_failures_stop_before_subtitle_and_emit_partial(monkeypatch, tmp_path):
    messages, calls, _ = _run_worker(monkeypatch, tmp_path)

    partial = next(message for message in messages if message["type"] == "partial")
    assert partial["output_ready"] is False
    assert partial["failed_count"] == 1
    assert partial["primary_failure_kind"] == "timeout"
    assert partial["sound_reference"] == {
        "status": "unavailable",
        "message": "声音参考未使用，字幕仍已生成。",
        "reason_code": "map_compute_failed",
    }
    assert calls == {"subtitle": 0, "mux": 0, "old_removed": None}
    assert all(message["type"] != "done" for message in messages)
    assert all(message["type"] != "llm_failed" for message in messages)


def test_accept_partial_runs_subtitle_and_mux_but_stays_partial(monkeypatch, tmp_path):
    messages, calls, checkpoint = _run_worker(monkeypatch, tmp_path, accept=True)

    partial = next(message for message in messages if message["type"] == "partial")
    assert partial["output_ready"] is True
    assert partial["sound_reference"]["status"] == "unavailable"
    assert calls == {"subtitle": 1, "mux": 1, "old_removed": True}
    assert all(message["type"] != "done" for message in messages)
    transient_keys = {
        "_partial_retry", "_accept_partial", "_retry_count",
        "_previous_failed_keys", "_consecutive_no_progress",
    }
    assert all(transient_keys.isdisjoint(meta) for _, meta in checkpoint.saved)


def test_accept_cache_mismatch_emits_partial_and_preserves_previous_state(
    monkeypatch, tmp_path
):
    messages, calls, _ = _run_worker(
        monkeypatch, tmp_path, accept=True, reject_accept=True
    )

    partial = next(message for message in messages if message["type"] == "partial")
    assert partial["failed_count"] == 1
    assert partial["failed_keys"] == [[1, 1.0, 2.0]]
    assert partial["retry_count"] == 1
    assert partial["consecutive_no_progress"] == 0
    assert partial["output_ready"] is False
    assert "缓存与当前字幕不匹配" in partial["action_error"]
    assert all(message["type"] != "error" for message in messages)
    assert calls == {"subtitle": 0, "mux": 0, "old_removed": None}


def test_retry_that_fills_every_line_is_the_only_path_to_done(monkeypatch, tmp_path):
    messages, calls, _ = _run_worker(monkeypatch, tmp_path, all_success=True)

    done = next(message for message in messages if message["type"] == "done")
    assert done["sound_reference"] == {
        "status": "unavailable",
        "message": "声音参考未使用，字幕仍已生成。",
        "reason_code": "map_compute_failed",
    }
    assert all(message["type"] != "partial" for message in messages)
    assert calls == {"subtitle": 1, "mux": 1, "old_removed": None}


@pytest.mark.parametrize(
    "terminal_failure",
    ["delivery", "subtitle", "ordinary"],
)
def test_terminal_errors_after_sound_map_keep_public_summary(
    monkeypatch, tmp_path, terminal_failure
):
    messages, _, _ = _run_worker(
        monkeypatch,
        tmp_path,
        all_success=True,
        terminal_failure=terminal_failure,
    )

    error = next(message for message in messages if message["type"] == "error")
    assert error["sound_reference"] == {
        "status": "unavailable",
        "message": "声音参考未使用，字幕仍已生成。",
        "reason_code": "map_compute_failed",
    }


def test_worker_delivers_then_announces_and_cleans_up(monkeypatch, tmp_path):
    """B3 回退：搬运责任交回 Worker，送到之后才宣告完成。

    取代原 test_worker_keeps_candidates_in_the_job_dir_and_never_delivers —— 那
    条断言的是 B3 的「Worker 绝不碰用户保存目录」，正是本次回退掉的行为。"""
    messages, _, _ = _run_worker(monkeypatch, tmp_path, all_success=True)
    save_dir = tmp_path / "user-save"
    job_dir = tmp_path / "job"

    assert any(message["type"] == "done" for message in messages)
    # mux 模式送视频 + 字幕
    assert (save_dir / "input_zh_sub.mp4").is_file(), "成品视频必须送到保存目录"
    assert (save_dir / "input.srt").is_file(), "字幕必须送到保存目录"
    assert not list(save_dir.glob(".video-subber-*.part")), "临时文件必须清理"
    # 第 12 步只清 cache 里的 audio.wav：任务目录内的成品副本要留给父进程核对，
    # 磁盘占用由独立的清缓存功能负责。
    assert (job_dir / "input_zh_sub.mp4").is_file(), "成品副本要留给父进程核对"
    assert (job_dir / "input.srt").is_file(), "成品副本要留给父进程核对"


def test_successful_partial_retry_invalidates_old_outputs_before_regeneration(monkeypatch, tmp_path):
    messages, calls, checkpoint = _run_worker(
        monkeypatch, tmp_path, retry=True, all_success=True
    )

    done = next(message for message in messages if message["type"] == "done")
    assert done["retry_started_at"] == 100.0
    assert done["retry_initial_failed_count"] == 1
    assert done["retry_processed_count"] == 1
    assert done["retry_new_success_count"] == 1
    assert done["retry_status"] == "completed"
    assert calls == {"subtitle": 1, "mux": 1, "old_removed": True}
    transient_keys = {
        "_partial_retry", "_accept_partial", "_retry_count",
        "_previous_failed_keys", "_consecutive_no_progress",
    }
    assert all(transient_keys.isdisjoint(meta) for _, meta in checkpoint.saved)


def test_partial_retry_terminal_reports_current_round_failure_counts(monkeypatch, tmp_path):
    messages, calls, _ = _run_worker(
        monkeypatch, tmp_path, retry=True, all_success=False
    )

    partial = next(message for message in messages if message["type"] == "partial")
    assert partial["failed_count"] == 1
    assert partial["retry_initial_failed_count"] == 1
    assert partial["retry_processed_count"] == 1
    assert partial["retry_new_success_count"] == 0
    assert partial["retry_status"] == "completed"
    assert calls == {"subtitle": 0, "mux": 0, "old_removed": None}


def test_worker_manager_keeps_partial_terminal_state_after_nonzero_exit(tmp_path):
    """worker 已交出 partial 后异常退出，管理器不能再把它覆盖成 error。"""
    from backend.worker_manager import WorkerManager

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    meta_path = job_dir / "meta.json"
    meta_path.write_text(json.dumps({"status": "partial"}), encoding="utf-8")
    proc = MagicMock()
    proc.stdout = iter([json.dumps({"type": "partial", "failed_count": 1})])
    proc.returncode = 17
    proc.wait.return_value = None

    manager = WorkerManager()
    manager._job_dirs["job"] = job_dir
    manager._read_stdout("job", proc)

    assert json.loads(meta_path.read_text(encoding="utf-8")) == {"status": "partial"}
