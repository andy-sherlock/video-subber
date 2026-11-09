"""把 JOBS 中的任务事实即时生成固定、公开的小状态。"""

import re
from pathlib import PurePath

SCHEMA_VERSION = 1
PUBLIC_TASK_STATE_FIELDS = (
    "schema_version", "job_id", "run_id", "revision", "status", "phase",
    "percent", "progress", "display_message", "activity", "actions", "result",
    "video_filename",  # P516 / Z2：从任务记录里唯一的原视频地址算出的名字
)
PROGRESS_FIELDS = ("total", "processed", "succeeded", "failed", "remaining")
_PATH_MARKERS = ("/Users/", "/home/", "/private/", "\\Users\\")
_ABSOLUTE_PATH = re.compile(
    r"(?:^|[\s:：=（(])(?:/[^\s，。；;]+|[A-Za-z]:[\\/][^\s，。；;]+|\\\\[^\s，。；;]+)"
)
_SOUND_REASONS = {
    "input_unavailable", "source_changed_during_run", "audio_track_unavailable",
    "vad_asset_unavailable", "map_compute_failed", "map_save_failed", "unknown",
}


def is_valid_saved_translation(value) -> bool:
    """「这句算不算翻好了」的唯一规则（R2）：worker 写记录前、main.py 数失败句都用它。"""
    return bool(
        isinstance(value, str)
        and value.strip()
        and value.strip() != "[翻译失败]"
    )


def _int(value):
    if isinstance(value, bool):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if value >= 0 else None


def _known_secrets(job):
    config = job.get("config") if isinstance(job, dict) else None
    keys = config.get("apiKeys") if isinstance(config, dict) else None
    return {item["key"] for item in keys or [] if isinstance(item, dict)
            and isinstance(item.get("key"), str) and item["key"]}


def safe_public_text(value, job=None):
    if not isinstance(value, str) or not value or len(value) > 500:
        return None
    if any(marker in value for marker in _PATH_MARKERS) or _ABSOLUTE_PATH.search(value):
        return None
    if any(secret in value for secret in _known_secrets(job)):
        return None
    return value


def make_progress(total=None, succeeded=None, failed=None) -> dict:
    """只用三个任务事实生成五个公开数字；总数未知时全部为 null。"""
    if total is None:
        return dict.fromkeys(PROGRESS_FIELDS)
    total, succeeded, failed = int(total), int(succeeded or 0), int(failed or 0)
    processed = succeeded + failed
    if min(total, succeeded, failed) < 0 or processed > total:
        raise ValueError("任务进度数字互相矛盾")
    return {"total": total, "processed": processed, "succeeded": succeeded,
            "failed": failed, "remaining": total - processed}


def _progress(job):
    if job.get("retry_status") in {"running", "post_processing"}:
        total = _int(job.get("retry_initial_failed_count"))
        processed = _int(job.get("retry_processed_count"))
        succeeded = _int(job.get("retry_new_success_count"))
        values = (total, succeeded, None if processed is None or succeeded is None
                  else processed - succeeded)
    else:
        values = tuple(_int(job.get(key)) for key in
                       ("segment_count", "success_count", "failed_count"))
    if any(value is None for value in values):
        return dict.fromkeys(PROGRESS_FIELDS)
    try:
        return make_progress(*values)
    except ValueError:
        return dict.fromkeys(PROGRESS_FIELDS)


_AI_UNUSED_STEPS = {"breaks", "refine", "breaks_and_refine"}
_AI_UNUSED_REASONS = {"platform_unsupported", "format", "validation"}


def _ai_unused(value):
    if not isinstance(value, list):
        return None
    notes = [
        {"step": item["step"], "reason": item["reason"]}
        for item in value
        if isinstance(item, dict)
        and item.get("step") in _AI_UNUSED_STEPS
        and item.get("reason") in _AI_UNUSED_REASONS
    ]
    return notes or None


def _quality(job):
    subtitle = job.get("subtitle_quality")
    subtitle = subtitle if isinstance(subtitle, dict) else {}
    translation = job.get("translation_summary")
    translation = translation if isinstance(translation, dict) else {}
    sound = job.get("sound_reference")
    sound = sound if isinstance(sound, dict) else {}

    def count_box(key):
        value = subtitle.get(key)
        count = _int(value.get("count")) if isinstance(value, dict) else None
        return {"count": count} if count is not None else None

    subtitle_public = {
        "below_min_display": count_box("below_min_display"),
        "capped_no_legal_cut": count_box("capped_no_legal_cut"),
        **{key: _int(subtitle.get(key)) for key in (
            "mapping_failed_splits", "no_safe_boundary_splits", "single_word_over_max",
        )},
    }
    translation_public = {
        "requested_tier": safe_public_text(translation.get("requested_tier"), job),
        **{key: _int(translation.get(key)) for key in (
            "api_success_count", "api_total_count",
            # D-1 A5：格式修复后仍失败、保留原文的句子数。
            "kept_original_count",
        )},
        # D-1 C12：哪一步没用上 AI、为什么。只放白名单里的值，文字由前端现算。
        "ai_unused": _ai_unused(translation.get("ai_unused")),
    }
    sound_status = sound.get("status")
    sound_public = None
    if sound_status in {"used", "unavailable"}:
        reason = sound.get("reason_code")
        sound_public = {
            "status": sound_status,
            "message": "" if sound_status == "used" else "声音参考未使用，字幕仍已生成。",
            "reason_code": None if sound_status == "used"
            else reason if reason in _SOUND_REASONS else "unknown",
        }
    return {
        "question_mark_count": _int(job.get("question_mark_count")),
        # E2：转录欠账说明。None = 没有欠账，界面什么都不显示。
        # 走公开状态而不是进度消息，**刷新后能重建同一句话**。
        "transcription_shortfall": (
            job.get("transcription_shortfall")
            if isinstance(job.get("transcription_shortfall"), dict) else None),
        "subtitle_quality": subtitle_public if any(
            value is not None for value in subtitle_public.values()) else None,
        "translation_summary": translation_public if any(
            value is not None for value in translation_public.values()) else None,
        "sound_reference": sound_public,
    }


def _display(job, status, phase):
    terminal = {
        "interrupted": "任务已中断，可以继续", "done": "处理完成",
        "partial": "部分字幕尚未完成", "cancelled": "任务已取消",
        "queued": "等待开始处理",
    }
    if status in terminal:
        return terminal[status], None
    if status == "error":
        return safe_public_text(job.get("error"), job) or "处理失败", None
    message = safe_public_text(job.get("message"), job)
    activity = safe_public_text(job.get("activity"), job)
    if job.get("retry_status") in {"running", "post_processing"}:
        return message or "正在重试剩余字幕", activity
    completed = _int(job.get("completed_segments"))
    if phase == "transcribe" and (job.get("transcription_phase") or completed is not None):
        label = ("正在分析声音类型，请稍等"
                 if job.get("transcription_phase") == "analyzing_sound"
                 else message or "正在识别语音，请稍等")
        # P453：`completed_segments` 有 4 个写入点、3 种含义（Whisper 字幕段数 /
        # Qwen 音频块序号 / 常量 1），贴成「已识别 N 段」会让界面假倒退
        # （实测约 300→2→10）。P375 已裁决识别阶段不显示这类数字。
        # `completed` 仍参与上面的 if 条件——连它一起拿掉，标签会掉成默认文案。
        return label, activity
    engine = job.get("engine_status")
    if phase == "translate" and isinstance(engine, dict):
        labels = {"trying_api": "正在使用用户配置的 API",
                  "api_failed": "用户配置的 API 失败"}
        return message or labels.get(engine.get("phase"), "正在翻译字幕"), activity
    return message or "正在处理任务", activity


def build_public_task_state(job: dict) -> dict:
    """从一份任务事实生成公开状态；不改输入、不读磁盘、不持有状态。"""
    job = job if isinstance(job, dict) else {}
    status = safe_public_text(job.get("status"), job) or "unknown"
    if status in {"running", "queued"} and job.get("process_alive") is False:
        status = "interrupted"
    phase = safe_public_text(job.get("stage"), job)
    if job.get("retry_status") in {"running", "post_processing"}:
        phase = phase or "translate"
    if isinstance(job.get("engine_status"), dict):
        phase = "translate"
    if job.get("transcription_phase"):
        phase = "transcribe"
    message, activity = _display(job, status, phase)
    run_id = safe_public_text(job.get("run_id"), job)
    owns_result = bool(run_id and job.get("result_run_id") == run_id and
                       (job.get("result_srt") or job.get("result_video")))
    output_ready = bool(status in {"done", "partial"} and
                        job.get("output_ready") and owns_result)
    action = job.get("resume_action") or job.get("partial_resume_action")
    action = action if action in {"retry", "accept"} else None
    try:
        percent = max(0, min(100, round(float(job.get("progress", 0)))))
    except (TypeError, ValueError, OverflowError):
        percent = 0
    quality = _quality(job)
    if status == "error":
        quality["sound_reference"] = None
    source = job.get("source_path")
    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": safe_public_text(job.get("id") or job.get("job_id"), job), "run_id": run_id,
        # Z2：卡片名只有后台这一个作者——从任务记录里唯一的地址算出来（链接任务下载完成前为空）
        "video_filename": PurePath(source).name if isinstance(source, str) and source else None,
        "revision": _int(job.get("revision")) or 0, "status": status, "phase": phase,
        "percent": 100 if status in {"done", "error"} else percent,
        "progress": _progress(job), "display_message": message, "activity": activity,
        "actions": {"can_resume": status == "interrupted",
                    "can_retry": status == "partial" and (_int(job.get("failed_count")) or 0) > 0,
                    "resume_action": action},
        "result": {"output_ready": output_ready,
                   "can_show_output_folder": output_ready, "quality": quality},
    }
