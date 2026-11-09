"""P367 worker：partial 重入、缓存身份、快照与反思纵深防御。"""

import errno
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from test import locked_decision, stamp_request_fingerprint

import pytest

from backend import worker


def test_output_identity_comes_from_the_video_address():
    # P516：名字不单独存，成品名 = 原视频地址最后一段去后缀（取代原「配置里的名字优先」三条）
    stem, suffix = worker._derive_output_identity("/tmp/London VLOG_video.mp4")

    assert stem == "London VLOG_video"
    assert suffix == ".mp4"


def test_output_identity_without_any_suffix_raises_structured_burn_error():
    from backend.core.subtitler import SubtitleOutputError

    try:
        worker._derive_output_identity("/tmp/video-without-suffix")
    except SubtitleOutputError as error:
        assert error.stage == "burn"
        assert error.failure_kind == "output_identity_unknown"
        assert "视频格式" in error.user_message
        assert "video-without-suffix" in error.technical_detail
    else:
        raise AssertionError("两边都没有扩展名时必须抛 SubtitleOutputError")


def test_worker_main_emits_output_identity_error_and_returns_normally(
    monkeypatch, tmp_path
):
    from backend.core.subtitler import SubtitleOutputError

    job_dir = tmp_path / "identity-job"
    job_dir.mkdir()
    video_path = job_dir / "video-without-suffix"
    video_path.write_bytes(b"video")
    config_path = job_dir / "config.json"
    config_path.write_text(json.dumps({
        "video_path": str(video_path),
        "outputMode": "mux",
    }), encoding="utf-8")
    cache = job_dir / "cache" / "hash"
    cache.mkdir(parents=True)
    emitted = []

    monkeypatch.setattr(sys, "argv", ["worker", str(config_path)])
    # P388：没有父进程授予的运行身份，Worker 不得动任务文件。
    monkeypatch.setenv("VIDEO_SUBBER_RUN_ID", "run-1")
    monkeypatch.setattr(worker, "_setup_file_log", lambda job_id: ("user.log", "debug.log"))
    monkeypatch.setattr(worker, "_validate_config", lambda config: None)
    monkeypatch.setattr(worker, "_download_source_if_url", lambda config, directory, path: path)
    monkeypatch.setattr(
        worker, "find_versioned_cache",
        lambda base, path, **_kwargs: (cache, "hash"),
    )
    monkeypatch.setattr(worker, "cleanup_old_versions", lambda base, keep: None)
    monkeypatch.setattr(worker, "emit", lambda message_type, **fields: emitted.append({"type": message_type, **fields}))

    result = worker.main()

    assert result is None
    assert len(emitted) == 1
    assert emitted[0]["type"] == "error"
    assert emitted[0]["stage"] == "burn"
    assert emitted[0]["failure_kind"] == "output_identity_unknown"
    assert emitted[0]["result_srt"] is None
    assert emitted[0]["result_video"] is None
    assert emitted[0]["output_ready"] is False
    assert emitted[0]["failed_segments"] == []
    assert not isinstance(result, SubtitleOutputError)


def test_emit_subtitle_output_error_includes_existing_srt(monkeypatch, tmp_path):
    from backend.core.subtitler import SubtitleOutputError

    srt = tmp_path / "watch.srt"
    srt.write_text("1\n", encoding="utf-8")
    emitted = []
    monkeypatch.setattr(
        worker,
        "emit",
        lambda message_type, **fields: emitted.append(
            {"type": message_type, **fields}
        ),
    )
    error = SubtitleOutputError(
        stage="burn",
        failure_kind="output_mux_failed",
        user_message="字幕文件已生成，但视频封装失败。",
        technical_detail="stderr=noise",
    )

    worker._emit_subtitle_output_error(
        error,
        srt_path=srt,
        sound_reference_summary={
            "status": "unavailable",
            "message": "声音参考未使用，字幕仍已生成。",
            "reason_code": "map_compute_failed",
        },
    )

    assert emitted == [{
        "type": "error",
        "stage": "burn",
        "failure_kind": "output_mux_failed",
        "message": "字幕文件已生成，但视频封装失败。",
        "technical_detail": "stderr=noise",
        "result_srt": str(srt),
        "result_video": None,
        "output_ready": False,
        "failed_segments": [],
        "sound_reference": {
            "status": "unavailable",
            "message": "声音参考未使用，字幕仍已生成。",
            "reason_code": "map_compute_failed",
        },
    }]


def test_worker_main_catches_structured_output_error_without_crash_snapshot(
    monkeypatch, tmp_path
):
    from backend.core.subtitler import SubtitleOutputError

    job_dir = tmp_path / "mux-job"
    job_dir.mkdir()
    video_path = job_dir / "watch.mp4"  # P516：名字从地址取
    video_path.write_bytes(b"video")
    config_path = job_dir / "config.json"
    config_path.write_text(json.dumps({
        "video_path": str(job_dir / "watch.mp4"),
        "outputMode": "mux",
    }), encoding="utf-8")
    cache = job_dir / "cache" / "hash"
    cache.mkdir(parents=True)
    emitted = []
    segment = SimpleNamespace(
        id=0, start=0.0, end=1.0, text="one", zh="一", uncertain=False
    )

    class MainCheckpoint:
        def __init__(self, *args, **kwargs):
            pass

        def load(self):
            return "start"

        def save(self, *args, **kwargs):
            pass

    monkeypatch.setattr(sys, "argv", ["worker", str(config_path)])
    # P388：没有父进程授予的运行身份，Worker 不得动任务文件。
    monkeypatch.setenv("VIDEO_SUBBER_RUN_ID", "run-1")
    monkeypatch.setattr(worker, "_setup_file_log", lambda job_id: ("user.log", "debug.log"))
    monkeypatch.setattr(worker, "_validate_config", lambda config: None)
    monkeypatch.setattr(worker, "_download_source_if_url", lambda config, directory, path: path)
    monkeypatch.setattr(
        worker, "find_versioned_cache",
        lambda base, path, **_kwargs: (cache, "hash"),
    )
    monkeypatch.setattr(worker, "cleanup_old_versions", lambda base, keep: None)
    monkeypatch.setattr(worker, "Checkpoint", MainCheckpoint)
    monkeypatch.setattr(worker, "_invalidate_stale_caches", lambda *args: "start")
    monkeypatch.setattr(worker, "_step_extract_audio", lambda *args: (1920, 1080, "has_audio"))
    receipt = worker.TranscriptionReceipt(
        routed_primary="whisper",
        actual_primary="whisper",
        word_source="whisper_word_timestamps",
        source_text="one",
        source_words=[{"text": "one", "start": 0.0, "end": 1.0}],
                  language_decision=locked_decision(),
        artifact_language_check="ok",
              )
    monkeypatch.setattr(
        worker,
        "_step_transcribe",
        lambda *args: worker.TranscriptionEnvelope([segment], receipt),
    )
    monkeypatch.setattr(worker, "_run_transcript_refiner", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "_step_translate", lambda *args, **kwargs: ([], [], 1, {}, None))

    def make_srt(segments, api_keys, sub_splits, srt_path, *args):
        Path(srt_path).write_text("1\n", encoding="utf-8")

    monkeypatch.setattr(worker, "_step_subtitle", make_srt)
    monkeypatch.setattr(
        worker,
        "_step_burn_or_mux",
        lambda *args, **kwargs: (_ for _ in ()).throw(SubtitleOutputError(
            stage="burn",
            failure_kind="output_mux_failed",
            user_message="字幕文件已生成，但视频封装失败。",
            technical_detail="stderr=noise",
        )),
    )
    monkeypatch.setattr(
        worker,
        "_log_crash_snapshot",
        lambda: (_ for _ in ()).throw(AssertionError("结构化错误不能写 crash snapshot")),
    )
    monkeypatch.setattr(
        worker,
        "emit",
        lambda message_type, **fields: emitted.append(
            {"type": message_type, **fields}
        ),
    )

    result = worker.main()

    assert result is None
    assert worker.current_step == "burn"
    assert emitted[-1]["type"] == "error"
    assert emitted[-1]["failure_kind"] == "output_mux_failed"
    assert emitted[-1]["result_srt"].endswith("watch.srt")


class FakeCheckpoint:
    def __init__(self):
        self.saved = []

    def save(self, stage, meta=None):
        self.saved.append((stage, meta))


def test_resume_retry_or_accept_reuses_existing_url_video_without_download(monkeypatch, tmp_path):
    video = tmp_path / "标题_video.mp4"
    video.write_bytes(b"video")
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    calls = []

    monkeypatch.setattr(
        "backend.core.video_downloader.download_video",
        lambda *args, **kwargs: calls.append(args) or None,
    )
    # P443：本条只验证「不重新下载」。复用的这个视频自带音轨，走的是 URL 内嵌音轨
    # 分支，因此不需要独立音频文件；假视频文件探不出音轨，这里直接给出答案。
    monkeypatch.setattr("backend.core.extractor.has_audio_stream", lambda path: "has_audio")
    for flag in ("_resume", "_partial_retry", "_accept_partial"):
        config = {"source_url": "https://example.test/v", flag: True}
        assert worker._download_source_if_url(config, job_dir, str(video)) == str(video)

    assert calls == []


def test_resume_after_transcribe_reuses_audio_and_transcript(monkeypatch, tmp_path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    transcript = tmp_path / "transcript.json"
    segment = worker.Segment(0, 0.0, 1.0, "hello")
    receipt = worker.TranscriptionReceipt(
        routed_primary="whisper",
        actual_primary="whisper",
        word_source="whisper_word_timestamps",
        source_text="hello",
        source_words=[{"text": "hello", "start": 0.0, "end": 1.0}],
                  language_decision=locked_decision(),
        artifact_language_check="ok",
              )
    # G11：存档要能被复用，必须盖上与本次请求匹配的指纹。
    transcript.write_text(
        json.dumps(worker._transcription_envelope_payload(
            stamp_request_fingerprint(
                worker, worker.TranscriptionEnvelope([segment], receipt),
                cache=tmp_path, model="large-v2",
                transcription_tier="standard", speaker_detect=False)
        )),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "backend.core.extractor.get_video_resolution", lambda path: (1280, 720)
    )
    monkeypatch.setattr(
        "backend.core.extractor.has_audio_stream", lambda path: "has_audio"
    )
    monkeypatch.setattr(
        worker,
        "extract_audio",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("恢复不得重新抽音频")
        ),
    )
    monkeypatch.setattr(
        worker,
        "transcribe_v2",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("恢复不得重新识别")
        ),
    )

    assert worker._step_extract_audio(
        "transcribe", str(video), str(audio), {"_resume": True}, FakeCheckpoint()
    ) == (1280, 720, "has_audio")
    segments = worker._step_transcribe(
        "transcribe",
        str(audio),
        transcript,
        "large-v2",
        False,
        "standard",
        tmp_path,
        {"_resume": True},
        FakeCheckpoint(),
    )

    assert [(segment.id, segment.text) for segment in segments] == [(0, "hello")]


def test_new_url_task_still_downloads_and_missing_partial_file_falls_back(monkeypatch, tmp_path):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    downloaded = job_dir / "标题_video.mp4"
    calls = []

    monkeypatch.setattr("backend.core.video_downloader.is_ytdlp_available", lambda: True)
    monkeypatch.setattr(
        "backend.core.video_downloader.download_video",
        lambda url, directory, progress_callback=None: calls.append((url, directory)) or (str(downloaded), None),
    )
    monkeypatch.setattr("backend.core.extractor.has_audio_stream", lambda path: True)
    normal = {"source_url": "https://example.test/v"}
    assert worker._download_source_if_url(normal, job_dir, None) == str(downloaded)
    missing_partial = {"source_url": "https://example.test/v", "_partial_retry": True}
    assert worker._download_source_if_url(missing_partial, job_dir, None) == str(downloaded)
    assert len(calls) == 2


def test_url_video_without_separate_or_embedded_audio_stops_with_precise_error(monkeypatch, tmp_path):
    """P364：video-only 不能再被送去 ffmpeg 后显示成普通抽音频失败。"""
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    downloaded = job_dir / "标题_video.mp4"
    downloaded.write_bytes(b"video-only")
    emitted = []

    monkeypatch.setattr("backend.core.video_downloader.is_ytdlp_available", lambda: True)
    monkeypatch.setattr(
        "backend.core.video_downloader.download_video",
        lambda *args, **kwargs: (str(downloaded), None),
    )
    monkeypatch.setattr("backend.core.extractor.has_audio_stream", lambda path: False)
    monkeypatch.setattr(worker, "stderr_progress", lambda *args: None)
    # P443 复审返工：这条错误必须走终态通道 emit("error", ...)。
    # 原来的 stderr_progress("error", ...) 固定发 type="progress"，父进程不当终态处理，
    # 退出后具体原因会被「后台处理已结束，但没有找到可确认的完成结果」覆盖掉。
    monkeypatch.setattr(
        worker, "emit",
        lambda event_type, **kwargs: emitted.append((event_type, kwargs)),
    )

    with pytest.raises(SystemExit):
        worker._download_source_if_url(
            {"source_url": "https://example.test/video"}, job_dir, None
        )

    assert emitted[-1][0] == "error"
    assert "音频下载失败" in emitted[-1][1]["message"]
    assert "尽量复用" in emitted[-1][1]["message"]
    assert emitted[-1][1]["failure_kind"] == "url_audio_missing"


def test_url_video_with_embedded_audio_continues_when_separate_audio_is_missing(monkeypatch, tmp_path):
    """P364：没有独立音频文件不等于视频无声，自带音轨时继续处理。"""
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    downloaded = job_dir / "combined.mp4"
    downloaded.write_bytes(b"combined")

    monkeypatch.setattr("backend.core.video_downloader.is_ytdlp_available", lambda: True)
    monkeypatch.setattr(
        "backend.core.video_downloader.download_video",
        lambda *args, **kwargs: (str(downloaded), None),
    )
    monkeypatch.setattr("backend.core.extractor.has_audio_stream", lambda path: True)

    config = {"source_url": "https://example.test/video"}
    assert worker._download_source_if_url(config, job_dir, None) == str(downloaded)
    assert "dl_audio_path" not in config


def test_partial_action_rejection_payload_reads_retry_count_from_disk(tmp_path):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "meta.json").write_text(json.dumps({
        "status": "partial", "retry_count": 7, "failed_count": 1,
    }), encoding="utf-8")

    payload = worker._partial_action_rejection_payload(job_dir, "拒绝")

    assert payload["retry_count"] == 7
    assert payload["action_error"] == "拒绝"


def _cache_args(tmp_path):
    translated = tmp_path / "translated.json"
    translated.write_text("[]", encoding="utf-8")
    return (
        "burn",
        {"apiKeys": [{"key": "old"}]},
        {"apiKeys": [{"key": "new"}]},
        worker.SETTING_STAGE,  # 用生产里的那张表（D-1 补修：apiKeys 已不在表里）
        worker.STAGE_ORDER,
        {"translate": [str(translated)]},
        FakeCheckpoint(),
        translated,
    )


def test_partial_retry_key_change_preserves_translation_cache(tmp_path):
    stage, old, new, setting, order, cache, ck, translated = _cache_args(tmp_path)
    new["_partial_retry"] = True

    result = worker._invalidate_stale_caches(
        stage, old, new, setting, order, cache, ck
    )

    assert result == "burn"
    assert translated.exists()
    assert ck.saved == []


def test_translation_cache_identity_includes_text_not_just_count_id_start_end():
    """G2/A4：缓存身份必须**包含原文**。

    这个测试原名 `..._uses_count_id_start_end_but_not_text`，断言
    `old source` → `new source` 仍然「匹配」——**它把 P400 的症状写成了
    通过条件**：润色改了原文，编号和时间一个没变，旧缓存照样被认下，
    于是新原文套上旧译文，没有任何报错。

    改名是必须的：留着旧名字，下一个人会以为「不看文本」是设计意图。
    """
    segments = [
        SimpleNamespace(id=1, start=0.0, end=1.25, text="new source"),
        SimpleNamespace(id=2, start=1.25, end=2.5, text="other"),
    ]
    cache = [
        {"id": 1, "start": 0.0, "end": 1.25, "text": "old source", "zh": "甲"},
        {"id": 2, "start": 1.25, "end": 2.5, "text": "old", "zh": "乙"},
    ]

    # 原文变了 → 不认
    assert not worker._translation_cache_matches_segments(cache, segments)

    # 原文一致 → 认
    cache[0]["text"] = "new source"
    cache[1]["text"] = "other"
    assert worker._translation_cache_matches_segments(cache, segments)

    # 编号与时间这两条既有身份仍然生效，没有被文本比对顶替掉
    cache[1]["end"] = 2.6
    assert not worker._translation_cache_matches_segments(cache, segments)
    cache[1]["end"] = 2.5
    cache[1]["id"] = 99
    assert not worker._translation_cache_matches_segments(cache, segments)


# P516：「重试用快照」已按方案删除（不复制原视频），三条快照测试随之删除。


def test_partial_retry_preserves_success_and_only_translates_failed_cached_text(
    monkeypatch, tmp_path
):
    # G2/A4：原来这里的 segments 写的是 "new zero/one/two"，缓存行写的是
    # "cached ..."，故意让两边原文不一致。A4 之后这种不一致是**必须被拒绝**的
    # 情况（见下一个测试），所以夹具改成两边一致——重试要验的是「只重译失败的
    # 那一条」，不是「原文对不上还硬接着译」。
    segments = [
        SimpleNamespace(id=0, start=0.0, end=1.0, text="cached zero", zh=""),
        SimpleNamespace(id=1, start=1.0, end=2.0, text="cached failed", zh=""),
        SimpleNamespace(id=2, start=2.0, end=3.0, text="cached two", zh=""),
    ]
    translated_path = tmp_path / "translated.json"
    translated_path.write_text(json.dumps([
        {"id": 0, "start": 0.0, "end": 1.0, "text": "cached zero", "zh": "成功甲"},
        {"id": 1, "start": 1.0, "end": 2.0, "text": "cached failed", "zh": "[翻译失败]"},
        {"id": 2, "start": 2.0, "end": 3.0, "text": "cached two", "zh": "成功乙"},
    ], ensure_ascii=False), encoding="utf-8")
    calls = []

    def translate_with_orchestrator(**kwargs):
        calls.append(kwargs["texts"])
        return ["补回"], [], None, {}, {}

    monkeypatch.setattr(
        "backend.translation.orchestrator.translate_with_orchestrator",
        translate_with_orchestrator,
    )

    checkpoint = FakeCheckpoint()
    transient_keys = {
        "_partial_retry", "_accept_partial", "_retry_count",
        "_previous_failed_keys", "_consecutive_no_progress",
    }
    config = {
        "_partial_retry": True,
        "_accept_partial": False,
        "_retry_count": 2,
        "_previous_failed_keys": [(1, 1.0, 2.0)],
        "_consecutive_no_progress": 1,
    }
    failed, _, ok_count, failure_by_index, translation_summary = worker._step_translate(
        "subtitle", segments, translated_path,
        [{"key": "fake"}], False, config, tmp_path,
        3, checkpoint, include_failure_details=True,
    )

    assert calls == [["cached failed"]]
    assert [segment.zh for segment in segments] == ["成功甲", "补回", "成功乙"]
    assert failed == []
    assert ok_count == 3
    assert failure_by_index == {}
    assert translation_summary is not None
    translate_meta = next(meta for stage, meta in checkpoint.saved if stage == "translate")
    assert transient_keys.isdisjoint(translate_meta)


def test_partial_retry_rejects_cache_whose_source_text_changed(tmp_path):
    """G2/A4：重试时发现缓存配的是另一套原文 → 明确拒绝，不许硬接着译。

    上一个测试原本的夹具就是这种情况（两边原文不一致）。它当时能通过，
    正是 P400 的病：编号和时间一致就认下，于是把新原文的字幕配上旧原文的译文。
    宁可让用户看到「不能安全重试」，也不能给出一份混着两套原文的成品。
    """
    segments = [
        SimpleNamespace(id=0, start=0.0, end=1.0, text="refined zero", zh=""),
        SimpleNamespace(id=1, start=1.0, end=2.0, text="refined one", zh=""),
    ]
    translated_path = tmp_path / "translated.json"
    translated_path.write_text(json.dumps([
        {"id": 0, "start": 0.0, "end": 1.0, "text": "unrefined zero", "zh": "甲"},
        {"id": 1, "start": 1.0, "end": 2.0, "text": "unrefined one", "zh": "[翻译失败]"},
    ], ensure_ascii=False), encoding="utf-8")

    with pytest.raises(worker.PartialActionRejected):
        worker._step_translate(
            "subtitle", segments, translated_path,
            [{"key": "fake"}], False, {"_partial_retry": True}, tmp_path,
            2, FakeCheckpoint(), include_failure_details=True,
        )


def test_partial_retry_emits_real_completed_counts_without_fixed_percent(
    monkeypatch, tmp_path
):
    segments = [
        SimpleNamespace(id=0, start=0.0, end=1.0, text="zero", zh=""),
        SimpleNamespace(id=1, start=1.0, end=2.0, text="one", zh=""),
        SimpleNamespace(id=2, start=2.0, end=3.0, text="two", zh=""),
    ]
    translated_path = tmp_path / "translated.json"
    translated_path.write_text(json.dumps([
        {"id": 0, "start": 0.0, "end": 1.0, "text": "zero", "zh": "成功甲"},
        {"id": 1, "start": 1.0, "end": 2.0, "text": "one", "zh": "[翻译失败]"},
        {"id": 2, "start": 2.0, "end": 3.0, "text": "two", "zh": "[翻译失败]"},
    ], ensure_ascii=False), encoding="utf-8")

    def translate_with_orchestrator(**kwargs):
        kwargs["batch_progress_callback"](1, 1)
        return ["补回", "[翻译失败]"], [1], None, {}, {1: "timeout"}

    monkeypatch.setattr(
        "backend.translation.orchestrator.translate_with_orchestrator",
        translate_with_orchestrator,
    )
    output = io.StringIO()
    monkeypatch.setattr(worker, "_real_stdout", output)

    worker._step_translate(
        "subtitle", segments, translated_path, [], False,
        {
            "_partial_retry": True,
            "_retry_count": 1,
            "_retry_started_at": 1234.5,
            "_retry_initial_failed_count": 2,
        },
        tmp_path, 3, FakeCheckpoint(), include_failure_details=True,
    )

    events = [json.loads(line) for line in output.getvalue().splitlines()]
    retry_event = next(event for event in events if event["type"] == "retry_progress")
    assert "percent" not in retry_event
    assert retry_event["retry_started_at"] == 1234.5
    assert retry_event["retry_initial_failed_count"] == 2
    assert retry_event["retry_processed_count"] == 1
    assert retry_event["retry_new_success_count"] == 1
    assert retry_event["retry_status"] == "running"


def test_accept_partial_cannot_use_mismatched_cache(tmp_path):
    segments = [SimpleNamespace(id=0, start=0.0, end=1.0, text="one", zh="")]
    translated_path = tmp_path / "translated.json"
    translated_path.write_text(json.dumps([
        {"id": 99, "start": 0.0, "end": 1.0, "text": "other", "zh": "旧译文"},
    ], ensure_ascii=False), encoding="utf-8")

    try:
        worker._step_translate(
            "subtitle", segments, translated_path, [],
            False, {"_accept_partial": True}, tmp_path, 1, FakeCheckpoint(),
        )
    except RuntimeError as error:
        assert "缓存" in str(error)
    else:
        raise AssertionError("缓存身份不匹配时 accept 不得继续")


@pytest.mark.parametrize("cache_text", ["{broken", "[]"])
def test_partial_retry_rejects_unusable_translation_cache(
    monkeypatch, tmp_path, cache_text
):
    """重试缓存损坏或身份不匹配时必须停下，不能退回全量翻译。"""
    segments = [SimpleNamespace(id=0, start=0.0, end=1.0, text="one", zh="")]
    translated_path = tmp_path / "translated.json"
    translated_path.write_text(cache_text, encoding="utf-8")
    monkeypatch.setattr(
        "backend.translation.orchestrator.translate_with_orchestrator",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("partial retry 不得调用全量翻译")
        ),
    )

    with pytest.raises(worker.PartialActionRejected, match="缓存"):
        worker._step_translate(
            "subtitle", segments, translated_path, [],
            False, {"_partial_retry": True}, tmp_path, 1, FakeCheckpoint(),
        )


def test_failed_keys_progress_three_branches():
    previous = [(1, 0.0, 1.0), (2, 1.0, 2.0)]

    progressed = worker._evaluate_failed_key_progress(
        previous, [(2, 1.0, 2.0)], 1
    )
    unchanged = worker._evaluate_failed_key_progress(previous, previous, 1)

    assert progressed == (0, 1)
    assert unchanged == (2, 0)


def test_failed_keys_new_success_regression_is_contract_violation():
    try:
        worker._evaluate_failed_key_progress(
            [(1, 0.0, 1.0)], [(1, 0.0, 1.0), (3, 2.0, 3.0)], 0
        )
    except RuntimeError as error:
        assert "成功句" in str(error)
    else:
        raise AssertionError("出现新失败句必须拒绝")


def test_partial_payload_contains_persistent_failure_contract():
    segments = [
        SimpleNamespace(id=1, start=0.0, end=1.0, text="one", zh="成功"),
        SimpleNamespace(id=2, start=1.0, end=2.0, text="two", zh="[翻译失败]"),
    ]

    payload = worker._build_partial_payload(
        segments,
        {1: "timeout"},
        retry_count=2,
        consecutive_no_progress=1,
        output_ready=False,
    )

    assert payload["status"] == "partial"
    assert payload["segment_count"] == 2
    assert payload["success_count"] == 1
    assert payload["failed_count"] == 1
    assert payload["failed_keys"] == [(2, 1.0, 2.0)]
    assert payload["failure_kind_counts"] == {"timeout": 1}
    assert payload["primary_failure_kind"] == "timeout"
    assert payload["retry_count"] == 2
    assert payload["consecutive_no_progress"] == 1
    assert payload["output_ready"] is False
    assert "snapshot_error" not in payload, "P516：快照已删除"


def test_structured_backend_failure_below_half_enters_partial_instead_of_corrupt_cache_error(
    monkeypatch, tmp_path
):
    segments = [
        SimpleNamespace(id=0, start=0.0, end=1.0, text="one", zh=""),
        SimpleNamespace(id=1, start=1.0, end=2.0, text="two", zh=""),
    ]
    path = tmp_path / "translated.json"
    monkeypatch.setattr(
        "backend.translation.orchestrator.translate_with_orchestrator",
        lambda **kwargs: (
            ["[翻译失败]", "[翻译失败]"],
            [0, 1],
            None,
            {},
            {0: "timeout", 1: "timeout"},
        ),
    )

    failed, _, ok_count, failures, translation_summary = worker._step_translate(
        "translate", segments, path, [{"key": "fake"}],
        False, {}, tmp_path, 2, FakeCheckpoint(), include_failure_details=True,
    )

    assert len(failed) == 2
    assert ok_count == 0
    assert failures == {0: "timeout", 1: "timeout"}
    assert translation_summary is not None
    assert path.exists()
