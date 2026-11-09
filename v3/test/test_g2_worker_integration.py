"""G2 · A7 + A8 + A2 的主流程接线：润色成品必须真的挂在 run() 上。

前面几个文件测的是「函数自己写对了」。这个文件测的是「函数真的被接上了」——
两者是两回事，而后者错了同样不报错、不崩溃。

覆盖：
- A7：换模型 → 转录阶段缓存全清 → 润色成品也得跟着清（否则新模型的转录
  配着旧模型时代的润色原文）。
- A8：`partial_retry` / `accept_partial` 且成品缺失 → 不许跑润色。
- A2：正常运行时，润色成品的路径确实传进了润色函数。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test.test_p390_worker_integration import (  # noqa: E402
    _empty_transcription_envelope,
    _load_worker_without_audio_models,
    _recording_delivery,
)


def _build_job(tmp_path, config_extra=None, checkpoint_meta=None):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    video_path = job_dir / "input.mp4"
    video_path.write_bytes(b"video")
    config = {
        "video_path": str(video_path),
        "video_filename": "input.mp4",
        "outputMode": "srt",
        "model": "large-v3",
        "language": "en",
        "transcriptionTier": "standard",
    }
    config.update(config_extra or {})
    config_path = job_dir / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    cache = job_dir / "cache" / "hash"
    cache.mkdir(parents=True)
    (cache / "checkpoint.json").write_text(json.dumps({
        "meta": checkpoint_meta or {
            "model": "large-v3",
            "transcriptionTier": "standard",
        },
    }), encoding="utf-8")
    return job_dir, config_path, cache


class _Checkpoint:
    def __init__(self, *args, **kwargs):
        pass

    def load(self):
        return "burn"

    def save(self, *args, **kwargs):
        return None


def _wire_common(monkeypatch, worker, config_path, cache):
    monkeypatch.setattr(sys, "argv", ["worker", str(config_path)])
    monkeypatch.setenv("VIDEO_SUBBER_RUN_ID", "g2-integration")
    monkeypatch.setattr(worker, "Checkpoint", _Checkpoint)
    monkeypatch.setattr(
        worker, "_setup_file_log", lambda job_id: ("user.log", "debug.log"))
    monkeypatch.setattr(
        worker, "_download_source_if_url",
        lambda config, directory, path: path)
    monkeypatch.setattr(
        worker, "find_versioned_cache",
        lambda base, path, **_kwargs: (cache, "hash"))
    monkeypatch.setattr(worker, "cleanup_old_versions", lambda base, keep: None)
    monkeypatch.setattr(
        worker, "_step_extract_audio", lambda *args: (1920, 1080, "has_audio"))
    monkeypatch.setattr(
        worker, "_step_transcribe",
        lambda *args: _empty_transcription_envelope(worker))
    monkeypatch.setattr(
        worker, "_prepare_sentence_groups",
        lambda segments, **kwargs: (segments, None))
    # G12 第三批 B：主流程新增「润色后并组」一步；本文件的分组替身不返回真实组，一并替掉。
    monkeypatch.setattr(
        worker, "_merge_groups_after_refiner", lambda segments, **kwargs: segments)
    monkeypatch.setattr(
        worker, "_step_translate", lambda *args, **kwargs: ([], {}, 0, {}, None))
    monkeypatch.setattr(worker, "_step_subtitle", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        worker, "_step_burn_or_mux", lambda *args, **kwargs: None)
    return _recording_delivery(monkeypatch, worker)


def test_refined_file_cleared_on_model_change(monkeypatch, tmp_path):
    """A7：换模型 → 润色成品必须跟着转录阶段的缓存一起清掉。

    不清 = 新模型转出来的字幕，配着旧模型时代润色出来的原文。
    """
    worker = _load_worker_without_audio_models(monkeypatch)
    job_dir, config_path, cache = _build_job(
        tmp_path,
        checkpoint_meta={"model": "large-v2", "transcriptionTier": "standard"},
    )
    refined_path = cache / "refined_transcript.json"
    for name in ("transcript.json", "qwen_raw.json",
                 "sentence_boundaries.json", "refined_transcript.json"):
        (cache / name).write_text("old cache", encoding="utf-8")

    observed = []
    delivered = _wire_common(monkeypatch, worker, config_path, cache)
    # 探针必须在 _wire_common 之后挂，否则会被里面的默认替身覆盖掉。
    monkeypatch.setattr(
        worker, "_prepare_sentence_groups",
        lambda segments, **kwargs: (observed.append(refined_path.exists())
                                    or (segments, None)))
    monkeypatch.setattr(
        worker, "_run_transcript_refiner", lambda *args, **kwargs: None)

    assert worker.main() is None
    assert observed == [False]
    assert delivered == [1]


def test_refiner_receives_refined_path(monkeypatch, tmp_path):
    """A2 接线：正常运行时润色成品的路径确实传进了润色函数。

    传不进去 → 每次都重跑润色、每次都覆盖用户成品，而且完全没有报错。
    """
    worker = _load_worker_without_audio_models(monkeypatch)
    job_dir, config_path, cache = _build_job(tmp_path)
    seen = {}

    def _spy(api_keys, segments, **kwargs):
        seen.update(kwargs)

    delivered = _wire_common(monkeypatch, worker, config_path, cache)
    monkeypatch.setattr(worker, "_run_transcript_refiner", _spy)

    assert worker.main() is None
    assert seen["refined_path"] == cache / "refined_transcript.json"
    assert seen["allow_refine"] is True
    assert delivered == [1]


def test_snapshot_read_uses_current_text_fingerprint(monkeypatch, tmp_path):
    """A5 接线：翻译快照必须用**当前这套原文**的指纹去读。

    Worker 在翻译途中被杀 → 重来时若只按编号恢复，一份字幕里会混进
    两套原文的译文。这条测的是指纹真的传进去了，不只是函数会算。
    """
    worker = _load_worker_without_audio_models(monkeypatch)

    class _Seg:
        def __init__(self, id, text):
            self.id = id
            self.text = text
            self.start = float(id)
            self.end = float(id) + 1.0
            self.zh = "已翻译"

    segments = [_Seg(1, "refined source one")]
    seen = {}

    def _spy_read(path, refined_fingerprint=""):
        seen["read"] = refined_fingerprint
        return {}

    monkeypatch.setattr(worker, "_read_translation_snapshot", _spy_read)

    try:
        worker._step_translate(
            "translate", segments, tmp_path / "translated.json",
            [], False, {}, tmp_path, len(segments),
            _Checkpoint(),
        )
    except Exception:
        pass

    assert seen["read"] == worker._segments_text_fingerprint(segments)


def test_partial_retry_disallows_refine(monkeypatch, tmp_path):
    """A8：重试模式下 `allow_refine` 必须是 False。

    这里跑润色 = 改原文 = 已经翻译成功的句子的译文当场作废。
    """
    worker = _load_worker_without_audio_models(monkeypatch)
    job_dir, config_path, cache = _build_job(
        tmp_path, config_extra={"_partial_retry": True})
    seen = {}

    def _spy(api_keys, segments, **kwargs):
        seen.update(kwargs)

    delivered = _wire_common(monkeypatch, worker, config_path, cache)
    monkeypatch.setattr(worker, "_run_transcript_refiner", _spy)

    assert worker.main() is None
    assert seen["allow_refine"] is False
    assert delivered == [1]
