"""V3 Web App — FastAPI backend."""
from . import hf_mirror  # noqa: F401  # 设置 HF 镜像，必须在任何 huggingface 导入前
import sys, os, re, json, uuid, asyncio, time, hashlib, threading, logging, copy, subprocess
import shutil, tempfile
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .env_check import check_all, preload_resemblyzer, health_summary_line, missing_checks
from .ws_manager import ws_manager
from . import job_state
from .worker_manager import worker_manager
from .core.sound_map import (
    MAP_REASON_CODES as _SOUND_REFERENCE_REASON_CODES,
    SOUND_REFERENCE_UNAVAILABLE_MESSAGE as _SOUND_REFERENCE_UNAVAILABLE_MESSAGE,
)

app = FastAPI(title="Video Subber V3")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8766",
        "http://localhost:8766",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

JOBS_DIR = Path.home() / ".video-subber" / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)

LOG_DIR = Path.home() / ".video-subber" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
JOBS = {}
_log = logging.getLogger(__name__)
_PARTIAL_ACTION_LOCKS: dict = {}
_JOB_INDEX_LOCK = threading.RLock()
_TRANSLATION_SUMMARY_FIELD = "translation_summary"
_SOUND_REFERENCE_FIELD = "sound_reference"

_RESTORE_EXCLUDED_FROM_META = frozenset({
    "id", "status", "config",
    *("result_srt", "result_video", "result_run_id", "output_ready"),
    "message", "activity", "display_message", "detail", "process_alive",
    "current_state", "engine_status", "transcription_phase",
    "file_hash", "task_key", "last_progress",
})


def _restored_from_meta(meta: dict) -> dict:
    """meta.json → 内存的唯一恢复规则。四条恢复路径全部走它。

    以前这里是四份各自维护的点名名单（`get_job` 两条分支、`get_active_jobs`、
    `_sync_job_from_worker_exit`），其中 `_sync_job_from_worker_exit` 那一份
    从来就漏登记字幕质量字段——终态消息丢失时，质量摘要从内存里直接消失。
    """
    if not isinstance(meta, dict):
        return {}
    return {key: value for key, value in meta.items()
            if key not in _RESTORE_EXCLUDED_FROM_META}



def _job_run_lock(job_id: str):
    # RLock：交付事务要在同一个线程里嵌套持有（登记候选 → 搬运 → 终态），
    # 而重试/恢复/删除来自别的线程，仍会被正常挡在门外。
    with _JOB_INDEX_LOCK:
        return _PARTIAL_ACTION_LOCKS.setdefault(job_id, threading.RLock())


# ── P388: 成品归属。一次运行只能认领本轮验证过的成品 ──

_RESULT_FIELDS = ("result_srt", "result_video", "result_run_id", "output_ready")
_TERMINAL_STATUSES = ("done", "partial", "error")


def _empty_result_fields() -> dict:
    """结果四元组的下架状态。新一轮和失败终态都整组写入它。"""
    return {"result_srt": None, "result_video": None,
            "result_run_id": None, "output_ready": False}


def _empty_run_state() -> dict:
    """开始新一轮：结果四元组与交付记录必须一起清空，不留半份旧资格。"""
    return {
        **_empty_result_fields(),
        "delivery": None,
        _SOUND_REFERENCE_FIELD: None,
    }


def _authoritative_run_id(source: dict | None) -> str | None:
    """非空字符串才是可授权的运行身份；缺失/空/非法都表示当前没有授权运行。"""
    if not isinstance(source, dict):
        return None
    value = source.get("run_id")
    return value if isinstance(value, str) and value.strip() else None


def _result_path_in_job(value, job_dir: Path | None) -> str | None:
    """结果路径必须解析后仍落在本任务目录内，否则不予承认。"""
    if not isinstance(value, str) or not value or job_dir is None:
        return None
    try:
        candidate = Path(value).resolve()
        candidate.relative_to(Path(job_dir).resolve())
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    return value


def _owned_result_fields(source: dict, job_dir: Path | None) -> dict:
    """只保留属于当前轮次、且路径在任务目录内的结果；任一不成立就整组下架。"""
    run_id = _authoritative_run_id(source)
    result_run_id = source.get("result_run_id") if isinstance(source, dict) else None
    if not run_id or result_run_id != run_id:
        return _empty_result_fields()
    srt = _result_path_in_job(source.get("result_srt"), job_dir)
    video = _result_path_in_job(source.get("result_video"), job_dir)
    if not srt and not video:
        return _empty_result_fields()
    return {
        "result_srt": srt,
        "result_video": video,
        # 没有字幕就谈不上"成品就绪"，但视频本身仍可独立判定归属。
        "output_ready": bool(source.get("output_ready")) and bool(srt),
        "result_run_id": run_id,
    }


def _verified_result_fields(
    job_id: str, run_id: str | None, candidate: dict, *, output_ready: bool
) -> dict:
    """核对 Worker 候选结果：归属、目录边界、类型、内容都通过才登记。"""
    from .output_artifacts import is_valid_media_file, is_valid_srt_file

    if not run_id:
        return _empty_result_fields()
    job_dir = _validate_job_path(job_id)
    srt = _result_path_in_job(candidate.get("result_srt"), job_dir)
    if srt and not is_valid_srt_file(Path(srt), job_dir):
        srt = None
    if not srt:
        return _empty_result_fields()
    video = _result_path_in_job(candidate.get("result_video"), job_dir)
    if video and not is_valid_media_file(Path(video), job_dir):
        video = None
    return {
        "result_srt": srt,
        "result_video": video,
        "result_run_id": run_id,
        "output_ready": bool(output_ready),
    }


# delivery 是 B3 时代父进程的私有交付记录。新模型下不再产生它，但旧任务的
# meta.json 里可能残留，仍然剥掉：永远不进 REST / WebSocket / 前端。
_PRIVATE_TASK_FIELDS = frozenset({
    "delivery",
    "transcription_receipt",
    "source_text",
    "source_words",
    "map_record",
    "map_input_identity",
    "hint_fingerprint",
    "hints",
    "diagnostics",
    "file_hash",
    "task_key",
    "last_progress",
    "failed_keys",
    "failure_kind_counts",
})
_PUBLIC_PROJECTION_FIELDS = ("can_show_output_folder",)


def _normalize_sound_reference(value) -> dict:
    if not isinstance(value, dict) or value.get("status") != "used":
        reason_code = value.get("reason_code") if isinstance(value, dict) else None
        if reason_code not in _SOUND_REFERENCE_REASON_CODES:
            reason_code = "unknown"
        return {
            "status": "unavailable",
            "message": _SOUND_REFERENCE_UNAVAILABLE_MESSAGE,
            "reason_code": reason_code,
        }
    return {
        "status": "used",
        "message": "",
        "reason_code": None,
    }


def _public_result_projection(source, job_dir: Path | None) -> dict:
    """核对成品归属，供后端文件夹入口使用。

    成品由 Worker 在宣告完成之前送到用户目录，所以终态即已送达：不存在
    "已完成但还没送到"的中间态，判据就是本轮自己的终态与成品归属。"""
    fields = _owned_result_fields(source, job_dir)
    status = source.get("status") if isinstance(source, dict) else None
    return {**fields,
            "can_show_output_folder": status in ("done", "partial")
            and bool(fields["output_ready"])}


_DETAIL_CONFIG_FIELDS = frozenset({
    "model", "contextAware", "style",
    "outputMode", "subtitles", "speakerDetect", "transcriptionTier", "burn",
})
_DETAIL_STYLE_FIELDS = frozenset({
    "font", "size", "color", "outlineColor", "outlineWidth", "bgColor",
})
_DETAIL_FAILURE_KINDS = frozenset({
    "auth", "rate_limit", "connection", "timeout", "service_error",
    "insufficient_balance", "request_rejected",
    "numbering_format", "empty_translation", "quality_rejected", "mixed", "unknown",
})


def _public_config(value) -> dict:
    if not isinstance(value, dict):
        return {}
    public = {}
    for key in _DETAIL_CONFIG_FIELDS:
        item = value.get(key)
        if key == "style" and isinstance(item, dict):
            public[key] = {
                name: setting for name, setting in item.items()
                if name in _DETAIL_STYLE_FIELDS
                and isinstance(setting, (int, float, bool))
                or name in _DETAIL_STYLE_FIELDS
                and isinstance(setting, str)
                and job_state.safe_public_text(setting, {"config": value}) is not None
            }
        elif isinstance(item, str):
            cleaned = job_state.safe_public_text(item, {"config": value})
            if cleaned is not None:
                public[key] = cleaned
        elif isinstance(item, (int, float, bool)):
            public[key] = item
    return public


def _public_failed_segments(value) -> list:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        public = {}
        if isinstance(item.get("index"), int):
            public["index"] = item["index"]
        reason = item.get("failure_kind") or item.get("reason")
        if (isinstance(reason, str) and len(reason) <= 64
                and all(char.isalnum() or char in "_-" for char in reason)):
            public["reason"] = reason
        if public:
            result.append(public)
    return result


def _public_job_details(source: dict) -> dict:
    """查询接口专用的安全静态/终态资料；不复制任务对象。"""
    filename = _video_name(source)  # P516：名字从地址现算
    int_fields = (
        "retry_count", "consecutive_no_progress", "recovered_count",
        "retry_initial_failed_count", "retry_processed_count",
        "retry_new_success_count",
    )
    details = {
        "video_filename": filename,
        "config": _public_config(source.get("config")),
        **{key: source.get(key) if isinstance(source.get(key), int) else None
           for key in int_fields},
        "primary_failure_kind": source.get("primary_failure_kind")
        if source.get("primary_failure_kind") in _DETAIL_FAILURE_KINDS else "unknown",
        "retry_status": source.get("retry_status")
        if source.get("retry_status") in {"running", "post_processing", "completed"}
        else None,
        "action_error": job_state.safe_public_text(source.get("action_error"), source),
        "failed_segments": _public_failed_segments(source.get("failed_segments"))
        if source.get("status") == "partial" else [],
    }
    return details


def _public_job_response(job_id: str, source: dict,
                         job_dir: Path | None = None) -> dict:
    """查询响应。state 与 WebSocket 同源，details 只在查询时返回。"""
    return {
        "state": job_state.build_public_task_state(source),
        "details": _public_job_details(source),
    }


def _output_mode_of(config) -> str:
    """burn 标志是旧配置的写法，统一折算成 outputMode。"""
    if not isinstance(config, dict):
        config = {}
    mode = config.get("outputMode")
    if mode is None:
        mode = "burn" if config.get("burn", True) else "srt"
    return mode


def _delivery_candidates(
    job_id: str, run_id: str | None, config: dict, candidate: dict
) -> dict | None:
    """A12：交付资格闸门。够格才返回候选，不够格返回 None。

    burn / mux 的视频候选必须配一份可信、同 run、且在任务目录内的 SRT——
    没有字幕就谈不上"带字幕的成品"，视频不得单独进入交付事务。"""
    mode = _output_mode_of(config)
    if mode not in ("srt", "burn", "mux"):
        return None
    verified = _verified_result_fields(job_id, run_id, candidate, output_ready=True)
    if not verified["result_srt"]:
        return None
    if mode in ("burn", "mux") and not verified["result_video"]:
        return None
    return {**verified, "output_mode": mode}


def _collect_api_secrets(value, secrets: set[str]) -> None:
    if isinstance(value, dict):
        raw_keys = value.get("apiKeys")
        if isinstance(raw_keys, list):
            for item in raw_keys:
                if isinstance(item, dict):
                    secret = item.get("key")
                    if isinstance(secret, str) and secret:
                        secrets.add(secret)
        for item in value.values():
            _collect_api_secrets(item, secrets)
    elif isinstance(value, list):
        for item in value:
            _collect_api_secrets(item, secrets)


def _sanitize_task_payload(value, secrets: set[str] | None = None):
    """Remove apiKeys and their values from every task-facing payload."""
    if secrets is None:
        secrets = set()
        _collect_api_secrets(value, secrets)
    if isinstance(value, dict):
        return {
            key: _sanitize_task_payload(item, secrets)
            for key, item in value.items()
            if key not in {"apiKeys", "partial_resume_action", *_PRIVATE_TASK_FIELDS}
        }
    if isinstance(value, list):
        return [_sanitize_task_payload(item, secrets) for item in value]
    if isinstance(value, str) and value in secrets:
        return ""
    return value


def _validate_job_path(job_id: str):
    """Return resolved job directory Path if safe, else None (P240/P244)."""
    job_dir = JOBS_DIR / job_id
    resolved = job_dir.resolve()
    try:
        resolved.relative_to(JOBS_DIR.resolve())
    except ValueError:
        return None
    return resolved


def _verify_safe_url(url_str: str) -> bool:
    """Return True if URL is http(s) and not targeting private networks (P243)."""
    from urllib.parse import urlparse
    import ipaddress
    import socket
    parsed = urlparse(url_str)
    if parsed.scheme not in ("http", "https"):
        return False
    hostname = parsed.hostname or ""
    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_private or addr.is_loopback or addr.is_reserved:
            return False
    except ValueError:
        try:
            resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            for _, _, _, _, sockaddr in resolved:
                addr = ipaddress.ip_address(sockaddr[0])
                if addr.is_private or addr.is_loopback or addr.is_reserved:
                    return False
        except (socket.gaierror, OSError):
            pass
    return True

# ── v2.04: Memory-resident hash index (built at startup, O(1) lookup forever) ──
# v2.09: key changed from file_hash → task_key (file_hash + config_hash)
GLOBAL_HASH_INDEX = {}   # task_key → {job_id, status, config, file_hash}
JOB_ID_TO_HASH = {}      # job_id → task_key  (reverse lookup for O(1) status updates)


def _config_hash(cfg: dict) -> str:
    # Normalize outputMode: derive from burn flag for backward compat
    _output_mode = cfg.get("outputMode")
    if _output_mode is None:
        _output_mode = "burn" if cfg.get("burn", True) else "srt"
    relevant = {
        "model": cfg.get("model", ""),
        "contextAware": cfg.get("contextAware", False),
        "subtitles": cfg.get("subtitles", ""),
        "burn": cfg.get("burn", False),
        "outputMode": _output_mode,
        "style": cfg.get("style", {}),
        "savePath": cfg.get("savePath", ""),
        "speakerDetect": cfg.get("speakerDetect", False),
        "transcriptionTier": cfg.get("transcriptionTier", "standard"),
    }
    raw = json.dumps(relevant, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _task_key(file_hash: str, cfg: dict) -> str:
    return f"{file_hash}_{_config_hash(cfg)}"



# Available LLM backends — add new ones here and they appear in the UI automatically.
# "models": list of {id, name} — first one is the default.
LLM_BACKEND_LIST = [
    {"id": "claude",   "name": "Claude",
     "models": [
         {"id": "claude-sonnet-4-6",        "name": "Sonnet 4.6（推荐）"},
         {"id": "claude-opus-4-7",          "name": "Opus 4.7（最强·贵）"},
         {"id": "claude-haiku-4-5",         "name": "Haiku 4.5（最快·便宜）"},
     ]},
    {"id": "gpt",      "name": "GPT",
     "models": [
         {"id": "gpt-4o",                   "name": "GPT-4o（推荐）"},
         {"id": "gpt-4o-mini",              "name": "GPT-4o Mini（快·便宜）"},
         {"id": "gpt-4.1",                  "name": "GPT-4.1（最新）"},
     ]},
    {"id": "gemini",   "name": "Gemini",
     "models": [
         {"id": "gemini-2.0-flash",         "name": "2.0 Flash（推荐·免费）"},
         {"id": "gemini-2.0-flash-lite",    "name": "2.0 Flash Lite（最快·免费）"},
         {"id": "gemini-2.5-pro",           "name": "2.5 Pro（最强）"},
         {"id": "gemini-2.5-flash",         "name": "2.5 Flash（新·快·免费）"},
     ]},
    {"id": "deepseek", "name": "DeepSeek",
     "models": [
         {"id": "deepseek-chat",            "name": "DeepSeek-V3（推荐）"},
         {"id": "deepseek-reasoner",        "name": "DeepSeek-R1（推理）"},
     ]},
    {"id": "doubao",   "name": "豆包",
     "models": [
         {"id": "doubao-pro-32k",           "name": "豆包 Pro 32K（推荐）"},
         {"id": "doubao-lite-32k",          "name": "豆包 Lite 32K（快·便宜）"},
     ]},
    {"id": "glm",      "name": "GLM",
     "models": [
         {"id": "glm-4-flash",              "name": "GLM-4 Flash（推荐·免费）"},
         {"id": "glm-4-flashx",             "name": "GLM-4 Flash X（增强·免费）"},
         {"id": "glm-4",                    "name": "GLM-4（标准）"},
         {"id": "glm-4-plus",               "name": "GLM-4 Plus（最强）"},
     ]},
    {"id": "custom",  "name": "自定义平台 (OpenAI 兼容)",
     "models": [
         {"id": "custom-default",           "name": "由用户在前端填入模型名"},
     ]},
]


import os as _os
import json as _json
import urllib.request as _urllib

_REMOTE_BASE = _os.environ.get(
    "VSUB_REMOTE_CONFIG_URL",
    "https://raw.githubusercontent.com/andy-sherlock/video-subber-config/main",
)


def _fetch_remote_json(filename: str) -> dict | None:
    if not _REMOTE_BASE:
        return None
    try:
        url = f"{_REMOTE_BASE}/{filename}"
        req = _urllib.Request(url, headers={"User-Agent": "VideoSubber"})
        resp = _urllib.urlopen(req, timeout=5)
        return _json.loads(resp.read().decode())
    except Exception:
        return None


_MODELS_CACHE = Path.home() / ".video-subber" / "models_cache.json"


def _get_merged_models() -> list[dict]:
    merged = {b["id"]: dict(b) for b in LLM_BACKEND_LIST}
    if _MODELS_CACHE.exists():
        try:
            cached = _json.loads(_MODELS_CACHE.read_text(encoding="utf-8"))
            age_h = (time.time() - cached.get("fetched_at", 0)) / 3600
            if age_h < 24:
                for remote_b in cached.get("models", []):
                    merged[remote_b["id"]] = remote_b
        except Exception:
            pass
    return list(merged.values())


def _fetch_remote_models():
    data = _fetch_remote_json("models.json")
    if not data:
        return
    models = data.get("models", [])
    if models:
        _MODELS_CACHE.write_text(
            _json.dumps({"models": models, "fetched_at": time.time()}, ensure_ascii=False),
            encoding="utf-8",
        )


@app.on_event("startup")
async def startup():
    # ── 启动健康检查：依赖/模型 ✅/❌ 一行摘要（P174：暴露缺失的 yt-dlp 等）──
    try:
        _health = check_all()
        _log.warning("启动健康检查: %s", health_summary_line(_health))
        _missing = missing_checks(_health)
        if _missing:
            _log.warning("启动健康检查: 以下未就绪 → %s", ", ".join(_missing))
    except Exception as _e:
        _log.warning("启动健康检查执行失败: %s", _e)

    import threading
    threading.Thread(target=_fetch_remote_models, daemon=True).start()

    # ── 清理旧版单日志残留 ──
    for old_log in LOG_DIR.glob("job-*.log"):
        try:
            old_log.unlink()
        except OSError:
            pass

    # ── 1. Scan all historical jobs once → build GLOBAL_HASH_INDEX + JOB_ID_TO_HASH ──
    _scan_jobs_into_index()

    # ── 2. Preload resemblyzer in background ──
    threading.Thread(target=preload_resemblyzer, daemon=True).start()

    # ── 2b. Fetch community model pricing in background ──
    threading.Thread(target=_fetch_community_pricing, daemon=True).start()

    # ── 3. Store main event loop for worker_manager reader threads ──
    worker_manager.set_main_loop(asyncio.get_running_loop())
    _log.info("Server Python: %s", sys.executable)

    # ── 4. 同步 .version 缓存文件（供外部工具读取）──
    version_file = Path.home() / ".video-subber" / ".version"
    try:
        current = json.loads(version_file.read_text(encoding="utf-8")) if version_file.exists() else {}
    except (json.JSONDecodeError, IOError):
        current = {}

    _detected = _detect_version()
    if current.get("version") != _detected:
        import datetime
        version_data = {
            "version": _detected,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "last_compatible_version": current.get("version", "unknown"),
        }
        version_file.write_text(json.dumps(version_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 5. Cache auto-cleanup: delete intermediate files older than 30 days ──
    # Final outputs (output.srt, burned *.mp4) are never auto-deleted.
    threading.Thread(target=_cleanup_old_cache, daemon=True).start()


def _is_unfinished_download(meta: dict) -> bool:
    """链接任务、记录里还没有原视频地址（下载完成才写，见 source_ready）、也没有成品。"""
    config = meta.get("config") if isinstance(meta.get("config"), dict) else {}
    return (bool(config.get("source_url")) and not meta.get("source_path")
            and meta.get("status") not in ("done", "partial"))


def _scan_jobs_into_index() -> None:
    """扫描历史任务目录，重建内存索引。

    A11：软链接的任务目录一律跳过——顺着它读写会落到任务根目录之外。"""
    if JOBS_DIR.exists():
        for job_dir in JOBS_DIR.iterdir():
            if job_dir.is_symlink() or not job_dir.is_dir():
                continue
            meta_path = job_dir / "meta.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                fh = meta.get("file_hash")
                if not fh:
                    continue
                if _is_unfinished_download(meta):
                    # 自审④（用户定）：链接任务第一步「下载」都没走完软件就关了——留下的只是
                    # 半截文件，没有可继续的东西。整个删掉、不进索引，用户重新贴链接即可。
                    shutil.rmtree(job_dir, ignore_errors=True)
                    continue
                jid = job_dir.name
                # P346: reconcile status after restart AND persist back to disk
                status = _reconcile_job_status_on_disk(job_dir, meta)
                if any(key in meta for key in _META_TRANSIENT_FIELDS):
                    _write_json_atomic(meta_path, meta)

                if status == "partial":
                    snapshot = _partial_snapshot_from_disk(job_dir, meta)
                    meta.update(snapshot)
                    if (
                        snapshot.get("snapshot_source") == "cache_recomputed"
                        and not snapshot.get("snapshot_incomplete")
                    ):
                        _write_json_atomic(meta_path, meta)

                cfg = meta.get("config", {})
                # Always recompute with current _config_hash so that hash function
                # changes (e.g. new fields added) don't break dedup for old jobs.
                tk = _task_key(fh, cfg)
                index_entry = {
                    "job_id": jid, "status": status,
                    "config": cfg, "file_hash": fh,
                    **{
                        key: meta.get(key)
                        for key in (
                            "output_ready", "segment_count", "success_count",
                            "failed_count", "failed_keys", "retry_count",
                            "consecutive_no_progress", "recovered_count",
                            "primary_failure_kind", "failure_kind_counts",
                            *_RESULT_FIELDS, "delivery",
                        )
                        if key in meta
                    },
                    **_restored_from_meta(meta),
                }
                if _SOUND_REFERENCE_FIELD in index_entry:
                    if isinstance(index_entry[_SOUND_REFERENCE_FIELD], dict):
                        index_entry[_SOUND_REFERENCE_FIELD] = (
                            _normalize_sound_reference(
                                index_entry[_SOUND_REFERENCE_FIELD]
                            )
                        )
                    else:
                        index_entry.pop(_SOUND_REFERENCE_FIELD, None)
                GLOBAL_HASH_INDEX[tk] = index_entry
                JOB_ID_TO_HASH[jid] = tk
            except Exception:
                pass


@app.on_event("shutdown")
async def shutdown():
    worker_manager.shutdown_all()


def _cleanup_old_cache():
    """Delete intermediate cache files not accessed for 30+ days. Never touches final outputs."""
    import time as _time
    _INTERMEDIATE = {"audio.wav", "transcript.json", "translated.json"}
    _CUTOFF = 30 * 24 * 3600  # 30 days in seconds
    now = _time.time()
    from .core.checkpoint import is_safe_cache_path
    try:
        for job_dir in JOBS_DIR.iterdir():
            # A11: 任务目录、cache 根、版本目录、待删文件——任一是软链接就整条跳过。
            # 这里每次启动后台静默运行，已实测复现过任务目录外的越界删除。
            if not is_safe_cache_path(job_dir) or not job_dir.is_dir():
                continue
            meta_path = job_dir / "meta.json"
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
            if meta.get("status") == "partial":
                continue
            # Walk cache subdirectories
            cache_root = job_dir / "cache"
            if is_safe_cache_path(cache_root) and cache_root.is_dir():
                for hash_dir in cache_root.iterdir():
                    if not is_safe_cache_path(hash_dir) or not hash_dir.is_dir():
                        continue
                    for f in hash_dir.iterdir():
                        if not is_safe_cache_path(f) or not f.is_file():
                            continue
                        if f.name in _INTERMEDIATE or f.suffix == ".ass":
                            try:
                                if now - f.stat().st_mtime > _CUTOFF:
                                    f.unlink()
                            except OSError:
                                pass
    except Exception:
        pass


@app.get("/api/health")
def health():
    return {"ok": True, "version": _detect_version()}


@app.get("/api/hardware")
def hardware():
    from .core.hardware_detector import detect_hardware, TIERS
    hw = detect_hardware()
    hw["tiers"] = TIERS
    return hw


def _detect_version() -> str:
    """Read version from pyproject.toml (single source of truth)."""
    try:
        import tomllib
        ppt = Path(__file__).parent.parent / "pyproject.toml"
        data = tomllib.loads(ppt.read_text())
        return data.get("project", {}).get("version", "dev")
    except Exception:
        return "dev"


@app.get("/api/env")
def get_env(simulate: str = ""):
    """Return environment status. ?simulate=noffmpeg,nowhisper to fake missing deps for testing."""
    data = check_all()
    sims = simulate.split(",") if simulate else []
    if "noffmpeg" in sims:
        data["ffmpeg"] = {"installed": False, "has_libass": False, "path": None}
    elif "nolibass" in sims:
        _sim_path = r"C:\ffmpeg\bin\ffmpeg.exe" if __import__("sys").platform == "win32" else "/usr/local/bin/ffmpeg"
        data["ffmpeg"] = {"installed": True, "has_libass": False, "path": _sim_path}
    if "nowhisper" in sims:
        # 模拟"包在、模型没下载"这一现实缺失态：installed_pkg 为真、model_ready 为假，
        # 前端 split() 据此显示"语音识别模型未下载"并给出一键修复入口。
        data["whisper"] = {"installed": False, "installed_pkg": True,
                           "model_ready": False, "models": []}
    return data


# ── Model download ──

def _has_running_jobs() -> bool:
    """P316 防线：是否有排队/运行中的任务（与 /api/active-jobs 同源）。"""
    return any(entry.get("status") in ("queued", "running")
               for entry in GLOBAL_HASH_INDEX.values())


@app.get("/api/models/status")
def model_status():
    """Whisper 各型号下载状态（旧消费方）+ 四模型准备/修复通道（C3.5）。"""
    from .model_manager import get_model_status
    from .prepare_manager import get_prepare_status
    result = get_model_status()
    result["prepare"] = get_prepare_status()
    return result


@app.post("/api/prepare")
def api_prepare(payload: dict = None):
    """一键准备：检测四模型，缺哪个下哪个。幂等，重复调用只补缺失项。"""
    from .prepare_manager import start_prepare
    whisper_model_id = (payload or {}).get("model")
    return start_prepare(whisper_model_id=whisper_model_id)


@app.post("/api/prepare/stop")
def api_prepare_stop():
    """停止当前准备下载，保留已下载部分。"""
    from .prepare_manager import stop_prepare
    return stop_prepare()


@app.post("/api/repair")
def api_repair(payload: dict = None):
    """一键修复：文件级优先 + 整模型兜底。运行中任务时拒绝（P316）。"""
    if _has_running_jobs():
        return {"error": "当前有任务正在进行，请等任务完成后再修复"}
    from .prepare_manager import repair_model
    payload = payload or {}
    return repair_model(target=payload.get("target"),
                        whisper_model_id=payload.get("model"))


@app.post("/api/models/download/{model_id}")
def model_download(model_id: str):
    """开始下载指定模型。"""
    from .model_manager import start_download
    return start_download(model_id)


@app.delete("/api/models/download/{model_id}")
def model_cancel(model_id: str):
    """停止正在下载的模型。"""
    from .model_manager import cancel_download
    return cancel_download(model_id)


@app.delete("/api/models/{model_id}")
def model_delete(model_id: str):
    """删除本应用自管目录里已下载的模型。

    运行中任务时拒绝（P316）；一键准备/修复进行中也拒绝（P2：避免与整模型
    修复并发 rmtree/下载/写 manifest 同一目录）。
    """
    if _has_running_jobs():
        return {"error": "当前有任务正在进行，请等任务完成后再删除模型"}
    from .prepare_manager import delete_model_if_idle
    return delete_model_if_idle(model_id)


@app.get("/api/backends")
def get_backends():
    """Return available LLM platforms."""
    return {"llm": _get_merged_models()}


_LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
_FREE_MODEL_CACHE: dict[str, float] = {}  # {model_id: fetched_at_timestamp}
from backend.model_cache import _MODEL_CONTEXT_CACHE, save_context_cache  # P186/P407: shared module
_PRICING_LOCK = threading.Lock()
_FETCH_IN_PROGRESS = False

# Models confirmed free through official platform channels that LiteLLM may not
# capture (e.g. free tier models not reflected in the community pricing JSON).
_HARDCODED_FREE = frozenset({
    "glm-4-flash", "glm-4-flashx",
    "gemini-2.0-flash", "gemini-2.0-flash-lite",
    "gemini-2.5-flash", "gemini-2.5-flash-lite",
})


def _is_free_model(model_id: str) -> bool:
    """Check community pricing + hardcoded fallback for free status."""
    mid = model_id.lower()
    if mid in _HARDCODED_FREE:
        return True
    candidates = [
        mid,
        f"gemini/{mid}",
        f"zai/{mid}", f"zai.{mid}",
        f"openrouter/google/{mid}",
        f"openrouter/z-ai/{mid}",
    ]
    with _PRICING_LOCK:
        for c in candidates:
            if c in _FREE_MODEL_CACHE:
                return True
        for k in _FREE_MODEL_CACHE:
            if k.endswith(f"/{mid}") or k.endswith(f".{mid}"):
                return True
    return False


def _fetch_community_pricing():
    """Background: pull LiteLLM pricing JSON, cache free model IDs for 24h."""
    global _FETCH_IN_PROGRESS
    with _PRICING_LOCK:
        if _FETCH_IN_PROGRESS:
            return
        if _FREE_MODEL_CACHE:
            newest = max(_FREE_MODEL_CACHE.values())
            if time.time() - newest < 86400:
                return  # cache still fresh
        _FETCH_IN_PROGRESS = True

    try:
        req = _urllib.Request(_LITELLM_URL, headers={"User-Agent": "VideoSubber"})
        with _urllib.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read().decode())
        now = time.time()
        free = {}
        contexts = {}
        for mid, info in data.items():
            if not isinstance(info, dict):
                continue
            ic = info.get("input_cost_per_token", -1)
            oc = info.get("output_cost_per_token", -1)
            if ic == 0 and oc == 0:
                free[mid] = now
            mit = info.get("max_input_tokens")
            if isinstance(mit, (int, float)) and mit > 0:
                contexts[mid] = int(mit)
        with _PRICING_LOCK:
            _FREE_MODEL_CACHE.clear()
            _FREE_MODEL_CACHE.update(free)
            _MODEL_CONTEXT_CACHE.clear()
            _MODEL_CONTEXT_CACHE.update(contexts)
            if contexts:
                try:
                    save_context_cache(contexts)
                except Exception:
                    pass
    except Exception:
        pass  # silent fallback — no free labels shown
    finally:
        with _PRICING_LOCK:
            _FETCH_IN_PROGRESS = False


def _curate_model_ids(ids: list) -> list:
    bad = ("embedding", "whisper", "tts", "audio", "realtime", "dall-e", "dalle",
           "moderation", "rerank", "-search-", "image", "video", "vision-", "babbage",
           "davinci", "text-ada", "stable-", "sora", "veo", "imagen", "aqa", "guard",
           "lyria", "nano-banana", "robotics", "computer-use", "deep-research",
           "antigravity", "native-audio", "-live", "gemma")
    seen = set()
    out = []
    for mid in ids:
        if not isinstance(mid, str) or not mid:
            continue
        if any(b in mid.lower() for b in bad):
            continue
        if mid in seen:
            continue
        seen.add(mid)
        label = mid + "（免费）" if _is_free_model(mid) else mid
        out.append({"id": mid, "name": label})
    return out


# ⑦.5 R4：门口测试每个 Key 最多等 15 秒。底层请求自带 120 秒超时，代理把请求吞掉时
# 按钮会转圈好几分钟；这里到点就按「连不上」报给用户，没回来的请求留在后台自己结束。
_GATE_PROBE_SECONDS = 15
_GATE_POOL = None


def _gate_probe(fn, deadline):
    import concurrent.futures
    import time as _time

    global _GATE_POOL
    remaining = deadline - _time.monotonic()
    if remaining <= 0:
        raise TimeoutError("门口测试超过时限")
    if _GATE_POOL is None:
        _GATE_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="llm-gate")
    try:
        return _GATE_POOL.submit(fn).result(timeout=remaining)
    except concurrent.futures.TimeoutError as error:
        raise TimeoutError("门口测试超过时限") from error


_GATE_JSON_SYSTEM = "你是连接测试。只输出一个 JSON 对象，不要输出其他文字。"
_GATE_JSON_PROMPT = 'Return exactly this JSON object: {"ok": true}'
_GATE_TRANSLATE_PROMPT = "把 hello 翻译成中文，只输出译文。"


def _current_api_keys() -> list:
    """P512：Key 只住在本机设置文件一个地方；开始 / 继续 / 重试 / 重新开始 / 门口检测
    都在收到请求的那一刻来这里读，网页不再替后台记 Key、递 Key。"""
    from .config_manager import load_config
    keys = load_config().get("apiKeys")
    return keys if isinstance(keys, list) else []


@app.post("/api/llm-gate")
def llm_gate(payload: dict | None = None):
    """P512 门口检测（点开始 / 继续 / 重试 / 重新开始前）：只测设置文件里的 Key。
    请求里带了 Key 也不看——和紧接着的运行读的是同一个设置文件（A12-3）。"""
    return _probe_api_keys(_current_api_keys())


@app.post("/api/llm-test")
def test_llm_connection(payload: dict):
    """设置抽屉里填 Key / 换平台 / 换模型时的实时检测：测用户手里正在填的这个 Key
    （P512 §8-1：它问的是「这个 Key 好不好」，不是运行用哪个 Key）。"""
    entries = payload.get("apiKeys") if isinstance(payload, dict) else None
    return _probe_api_keys(entries if isinstance(entries, list) else [])


def _probe_api_keys(entries: list):
    """D-1 C1 检测一组 Key。

    与任务用同一个构造函数 `_build_llm_backends`、同一份 Key 列表、同一顺序。
    先测断句润色用的 JSON 请求路（`translate_raw_json`），某个 Key 通了即放行；
    JSON 路结构性不通（请求被拒 / 认不出的错）而翻译路通 → 「只能翻译」（第 7 条，
    Claude、Gemini 的情况）。网络类失败先对同一 Key 再测一次，偶发一次不误报。
    返回里不带 Key。
    """
    from backend.translation import orchestrator as _orch
    from backend.translation.base import classify_translation_failure
    from backend.translation.paid_request import RECOVERABLE_KINDS, platform_label

    backends = _orch._build_llm_backends(entries)
    if not backends:
        return {"ok": False, "translate_only": False, "kind": "no_key", "platform": ""}

    import time as _time

    def json_kind(backend, deadline):
        try:
            _gate_probe(lambda: backend.translate_raw_json(
                _GATE_JSON_PROMPT, system_prompt=_GATE_JSON_SYSTEM), deadline)
        except Exception as error:
            kind = classify_translation_failure(error)
            _log.warning("门口测试 JSON 路不通: backend=%s kind=%s", backend.name, kind)
            return kind
        return None

    first_kind = None
    translate_only_backend = None
    for backend in backends:
        deadline = _time.monotonic() + _GATE_PROBE_SECONDS
        kind = json_kind(backend, deadline)
        if kind in RECOVERABLE_KINDS and kind != "timeout":
            kind = json_kind(backend, deadline)
        if kind is None:
            return {"ok": True, "translate_only": False, "kind": None,
                    "platform": platform_label(backend.name)}
        first_kind = first_kind or kind
        if kind in RECOVERABLE_KINDS or kind == "auth":
            continue
        try:
            result = _gate_probe(lambda: backend.translate_raw(
                _GATE_TRANSLATE_PROMPT, max_tokens=32), deadline)
        except Exception:
            result = None
        if (result is not None and not getattr(result, "failure_kind", None)
                and (result.translated or [""])[0].strip()):
            translate_only_backend = translate_only_backend or backend
    if translate_only_backend is not None:
        return {"ok": False, "translate_only": True, "kind": first_kind,
                "platform": platform_label(translate_only_backend.name)}
    return {"ok": False, "translate_only": False, "kind": first_kind,
            "platform": platform_label(backends[0].name)}


@app.post("/api/llm-models")
def list_llm_models(payload: dict):
    """用用户 Key 调对应平台的列模型接口，返回该 Key 真实可用的模型。"""
    import urllib.request as _u
    platform = (payload.get("platform") or "").lower()
    key = (payload.get("key") or "").strip()
    endpoint = (payload.get("endpoint") or "").strip()
    if not key:
        return {"models": [], "error": "no_key"}

    if platform == "claude":
        url = "https://api.anthropic.com/v1/models?limit=1000"
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        def _parse(d): return [m.get("id", "") for m in d.get("data", [])]
    elif platform == "gemini":
        url = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000"
        headers = {"x-goog-api-key": key}
        def _parse(d): return [m.get("name", "").split("/")[-1] for m in d.get("models", [])
                               if "generateContent" in (m.get("supportedGenerationMethods") or [])]
    else:
        _chat = {
            "gpt": "https://api.openai.com/v1/chat/completions",
            "deepseek": "https://api.deepseek.com/v1/chat/completions",
            "doubao": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
            "glm": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            "custom": endpoint,
        }.get(platform, endpoint)
        if not _chat:
            return {"models": [], "error": "no_endpoint"}
        if not _verify_safe_url(_chat):
            return {"models": [], "error": "unsafe_endpoint"}
        _clean = _chat.rstrip("/")
        if _clean.endswith("/chat/completions"):
            url = _clean[:-len("/chat/completions")] + "/models"
        else:
            url = _clean + "/models"
        headers = {"Authorization": f"Bearer {key}"}
        def _parse(d): return [m.get("id", "") for m in d.get("data", [])]

    try:
        req = _urllib.Request(url, headers={**headers, "User-Agent": "VideoSubber"})
        with _urllib.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read().decode())
        raw_ids = _parse(data)
    except Exception as e:
        return {"models": [], "error": f"fetch_failed: {e}"}

    return {"models": _curate_model_ids(raw_ids)}


@app.get("/api/config")
def get_config():
    """Return user config from ~/.video-subber/config.json（旧文件里的废弃键不往外送）."""
    from .config_manager import load_config
    cfg = load_config()
    for _key in _DROPPED_CONFIG_KEYS:
        cfg.pop(_key, None)
    return cfg



@app.put("/api/config")
async def save_config(payload: dict):
    """Save user config to ~/.video-subber/config.json"""
    from .config_manager import save_config
    # 旧前端、旧配置文件里可能还带着已废弃的键，不许再写回磁盘。
    for _key in _DROPPED_CONFIG_KEYS:
        payload.pop(_key, None)
    save_config(payload)
    return {"ok": True}


# ── v2.04: Memory index helpers ──

def _update_hash_index(job_id: str, status: str):
    """O(1) status sync to GLOBAL_HASH_INDEX via JOB_ID_TO_HASH reverse lookup."""
    tk = JOB_ID_TO_HASH.get(job_id)
    if tk and tk in GLOBAL_HASH_INDEX:
        GLOBAL_HASH_INDEX[tk]["status"] = status


_META_TRANSIENT_FIELDS = frozenset({
    "current_state", "activity", "display_message", "message", "detail",
    "action_error",
    "engine_status", "transcription_phase", "completed_segments",
    "audio_total_duration", "audio_processed_duration", "duration_reliable",
    "audio_remaining_duration", "elapsed_seconds", "transcription_started_at",
    "transcription_status", "progress",
})


def _write_json_atomic(path: Path, payload: dict) -> None:
    if path.name == "meta.json":
        payload = {
            key: value for key, value in payload.items()
            if key not in _META_TRANSIENT_FIELDS
        }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _persist_job_status(
    job_id: str,
    status: str,
    fields: dict | None = None,
    *,
    clear_partial_action: bool = False,
):
    """Write job status to disk AND sync memory index."""
    meta_path = JOBS_DIR / job_id / "meta.json"
    if not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["status"] = status
        if fields:
            meta.update(fields)
        if status in ("queued", "running", "done"):
            meta.pop("error", None)
        if clear_partial_action:
            meta.pop("partial_resume_action", None)
        _write_json_atomic(meta_path, meta)
        with _JOB_INDEX_LOCK:
            _update_hash_index(job_id, status)
        return True
    except Exception as exc:
        _log.error("[worker:%s] 保存任务状态失败: %s", job_id, exc)
        return False


def _has_valid_final_outputs(job_dir: Path, meta: dict) -> bool:
    """P388: 结果必须先属于当前轮次，才轮得到检查文件是否有效。"""
    from .output_artifacts import is_valid_media_file, is_valid_srt_file

    owned = _owned_result_fields(meta, job_dir)
    if not owned["result_srt"]:
        return False

    config = meta.get("config")
    if not isinstance(config, dict):
        config = {}
    output_mode = config.get("outputMode")
    if output_mode is None:
        output_mode = "burn" if config.get("burn", True) else "srt"

    srt_ok = is_valid_srt_file(Path(owned["result_srt"]), job_dir)
    if output_mode == "srt":
        return srt_ok
    if output_mode in ("burn", "mux"):
        return srt_ok and is_valid_media_file(
            Path(owned["result_video"] or ""), job_dir
        )
    return False


def _matching_checkpoint(job_dir: Path, meta: dict, *, stage: str | None = None):
    config = meta.get("config")
    if not isinstance(config, dict):
        return None
    from .core.checkpoint import is_safe_cache_path
    matches = []
    for checkpoint_path in job_dir.glob("cache/*/checkpoint.json"):
        # A11: 恢复扫描与 resume 检查都走这里，读之前先拒绝软链接。
        if not is_safe_cache_path(
            job_dir, job_dir / "cache", checkpoint_path.parent, checkpoint_path
        ):
            continue
        try:
            data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            input_hash = str(data["input_hash"])
            checkpoint_config = data.get("meta")
            if not isinstance(checkpoint_config, dict) or len(input_hash) != 16:
                continue
            if stage is not None and data.get("stage") != stage:
                continue
            if checkpoint_path.parent.name != input_hash[:12]:
                continue
            if _config_hash(checkpoint_config) != _config_hash(config):
                continue

            # P516 / A16-6：只比指纹与设置。原视频在哪只看任务记录里那一个地址，
            # 断点文件里另存的 input_file 不再读（换了位置也能接着用已付费的缓存）。
            if config.get("source_url"):
                # 链接任务记录的是网址指纹，视频指纹要从下载好的那个文件算
                source = _source_video(meta)
                if source is None or _hash_local_file(str(source))[:16] != input_hash:
                    continue
            elif not str(meta.get("file_hash", "")).startswith(input_hash):
                continue
            matches.append((checkpoint_path, data))
        except (KeyError, OSError, RuntimeError, TypeError, ValueError):
            continue
    return matches[0] if len(matches) == 1 else None


def _snapshot_from_translated_rows(rows: list, meta: dict) -> dict:
    failed_rows = [
        row for row in rows
        if isinstance(row, dict) and not job_state.is_valid_saved_translation(row.get("zh"))
    ]
    return {
        "segment_count": len(rows),
        "success_count": len(rows) - len(failed_rows),
        "failed_count": len(failed_rows),
        "failed_keys": [
            [row.get("id"), row.get("start"), row.get("end")]
            for row in failed_rows
        ],
        "retry_count": int(meta.get("retry_count", 0)),
        "consecutive_no_progress": int(meta.get("consecutive_no_progress", 0)),
        "recovered_count": int(meta.get("recovered_count", 0)),
        "primary_failure_kind": meta.get("primary_failure_kind", "unknown"),
        "failure_kind_counts": meta.get(
            "failure_kind_counts",
            {"unknown": len(failed_rows)} if failed_rows else {},
        ),
    }


def _recover_completed_checkpoint(job_dir: Path, meta: dict) -> dict | None:
    """P388: 只认同一轮写下的 stage=done checkpoint 和它记录的明确结果路径。

    非 done 阶段、别的轮次、无归属、或从滞后文件名推导出来的候选一律拒绝——
    "文件还在" 不等于 "它是本轮的成品"。"""
    run_id = _authoritative_run_id(meta)
    if not run_id:
        return None
    match = _matching_checkpoint(job_dir, meta, stage="done")
    if match is None:
        return None
    checkpoint_path, data = match
    if data.get("run_id") != run_id:
        return None
    from .output_artifacts import is_valid_media_file, is_valid_srt_file

    config = meta.get("config", {})
    # 优先认凭据里记的**实际**交付方式；老凭据没这个字段时才回落到 config。
    # 拖 mp3 选烧录的任务实际只出 srt，按 config 判会去找不存在的视频成品，
    # 于是一个已经做完的任务被判成没做完，用户重开软件要白跑一遍。
    output_mode = data.get("output_mode") or _output_mode_of(config)
    if output_mode not in ("srt", "burn", "mux"):
        return None

    result_srt = _result_path_in_job(data.get("result_srt"), job_dir)
    if not result_srt or not is_valid_srt_file(Path(result_srt), job_dir):
        return None
    result_video = None
    if output_mode in ("burn", "mux"):
        result_video = _result_path_in_job(data.get("result_video"), job_dir)
        if not result_video or not is_valid_media_file(Path(result_video), job_dir):
            return None

    # A10: 不再读用户保存目录的副本做比较。那是无归属的存在性猜测，
    # 会让任务目录里明明有本轮完成凭据的任务被误判成未完成。

    recovered = {
        "status": "done",
        "result_srt": result_srt,
        "result_video": result_video,
        "result_run_id": run_id,
        "output_ready": True,
    }
    checkpoint_meta = data.get("meta")
    if (
        isinstance(checkpoint_meta, dict)
        and _SOUND_REFERENCE_FIELD in checkpoint_meta
    ):
        recovered[_SOUND_REFERENCE_FIELD] = _normalize_sound_reference(
            checkpoint_meta[_SOUND_REFERENCE_FIELD]
        )
    translated_path = checkpoint_path.parent / "translated.json"
    try:
        rows = json.loads(translated_path.read_text(encoding="utf-8"))
    except Exception:
        rows = None
    if isinstance(rows, list):
        snapshot = _snapshot_from_translated_rows(rows, meta)
        if snapshot["failed_count"]:
            recovered.update(snapshot)
            recovered["status"] = "partial"
    return recovered


def _reconcile_job_status_on_disk(job_dir: Path, meta: dict) -> str:
    """P346: Reconcile a job's status after restart AND persist it back to meta.json.

    Only running/queued jobs whose worker disappeared are recomputed.
    Writing back keeps disk meta.json consistent with the in-memory index, so a
    stalled job never reads back as 'running' on the next restart."""
    status = meta.get("status", "unknown")
    if status not in ("running", "queued"):
        return status
    # 铁律 6：只读面只判定状态，绝不搬运、删除或补交付。成品由 Worker 在宣告
    # 完成之前送达，所以这里没有"完成了但还没送到"需要补的情况。
    if _has_valid_final_outputs(job_dir, meta):
        recovered = {"status": "done", **_owned_result_fields(meta, job_dir)}
    else:
        recovered = _recover_completed_checkpoint(job_dir, meta)
    new_status = recovered["status"] if recovered else "interrupted"
    if new_status != status:
        meta["revision"] = int(meta.get("revision", 0) or 0) + 1
    # P388: 恢复不成立时，上一轮遗留的结果指针必须整组下架，不能留给下游当成品。
    meta.update(recovered or {"status": new_status, **_empty_result_fields()})
    try:
        _write_json_atomic(job_dir / "meta.json", meta)
    except OSError as exc:
        # 写盘失败时内存不得比磁盘更早宣布完成。
        _log.error("[worker:%s] 恢复状态写盘失败: %s", job_dir.name, exc)
        meta.update({"status": "interrupted", **_empty_result_fields()})
        return "interrupted"
    return new_status


def _cleanup_hash_index(job_id: str):
    """Remove job from both memory indexes (called on cancel)."""
    tk = JOB_ID_TO_HASH.pop(job_id, None)
    if tk:
        GLOBAL_HASH_INDEX.pop(tk, None)


def _source_video(meta: dict) -> Path | None:
    """P516：任务的原视频——任务记录里唯一的那个地址。文件不在就是「原视频缺失」。"""
    source = meta.get("source_path") if isinstance(meta, dict) else None
    if isinstance(source, str) and source and Path(source).is_file():
        return Path(source)
    return None


def _video_name(source: dict) -> str:
    """P516：名字不单独存，需要时从地址现算（最后一段）。"""
    path = source.get("source_path") if isinstance(source, dict) else None
    return Path(path).name if isinstance(path, str) and path else ""


def _set_source_path(job_id: str, path) -> None:
    """P516：同一视频换了位置——记录里的地址更新为这次的地址（只这一份，内存镜像同步）。"""
    meta_path = JOBS_DIR / job_id / "meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["source_path"] = str(path)
        _write_json_atomic(meta_path, meta)
    except (OSError, ValueError) as exc:
        _log.warning("[job:%s] 更新原视频地址失败: %s", job_id, exc)
        return
    if isinstance(JOBS.get(job_id), dict):
        JOBS[job_id]["source_path"] = str(path)


def _is_recoverable_active_job(job_id: str, entry: dict) -> bool:
    """Return True when an active-job entry can be resumed from disk."""
    status = entry.get("status")
    if status in ("queued", "running"):
        return True
    # P388: 发现资格只看属于当前轮次的结果，旧字段不得影响判断。
    owned = _owned_result_fields(entry, _validate_job_path(job_id))
    if status == "partial":
        return not owned["output_ready"]
    if status == "error":
        # E2：带转录欠账说明的失败任务必须能被刷新后重新发现。
        # 它确实没什么可「恢复」的（用户要先把运行环境弄好再重来），
        # 但那句「已处理到 20:00，模型加载失败」是他唯一的行动线索——
        # **等了半小时看到的话，不能刷一下就没了。**
        # 没有说明的普通失败仍然排除：留在列表里只是噪音。
        if isinstance(entry.get("transcription_shortfall"), dict):
            return True
        return bool(owned["result_srt"])
    if status != "interrupted":
        return False

    job_dir = _validate_job_path(job_id)
    if not job_dir or not job_dir.is_dir():
        return False
    try:
        meta = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    if _source_video(meta) is not None:
        return True

    source_url = (entry.get("config") or {}).get("source_url", "")
    return bool(source_url and _verify_safe_url(source_url))


# ── POST /api/jobs — the core: upload/path → hash → dedup → create or restore ──


# 已废弃的设置键，读、写、建任务时一律丢掉。**只清这几个已废弃字段，不扩张成完整白名单**
# （⑥ 安全审查已确认：本地回环单用户下，完整白名单消除不了任何真实攻击路径，
#   只增加维护面）。
# - G11 两个语言键：软件自己识别语言，留着它们就是留着第二个语言来源。
# - D-6 P503 两个旧翻译键（极速/质量优先、引擎顺序）：那条翻译路线已删。
_DROPPED_CONFIG_KEYS = ("sourceLanguage", "language", "translationMode", "translationPriority")


def _normalize_job_config(cfg):
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    cfg.pop("tier", None)
    cfg.pop("local_path", None)
    for _key in _DROPPED_CONFIG_KEYS:
        cfg.pop(_key, None)
    return cfg


def _new_job_dir():
    jid = uuid.uuid4().hex[:10]
    job_dir = JOBS_DIR / jid
    job_dir.mkdir(parents=True)
    return jid, job_dir


def _prepare_url_job(cfg: dict, source_url: str):
    if not _verify_safe_url(source_url):
        return JSONResponse(
            {"error": "仅支持安全的 http/https 链接"},
            status_code=400,
        )

    # P174: URL mode — worker subprocess handles download
    cfg["source_url"] = source_url
    # P516：地址（下载好的文件）在下载完成时由干活程序汇报、写进记录（合同增量 Z1）

    jid, job_dir = _new_job_dir()
    return {
        "cfg": cfg,
        "jid": jid,
        "job_dir": job_dir,
        "file_hash": hashlib.sha256(source_url.encode()).hexdigest(),
        "video_path": "",
    }


def _hash_local_file(local_path: str) -> str:
    hasher = hashlib.sha256()
    with open(local_path, "rb") as src:
        while True:
            chunk = src.read(8 * 1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _prepare_local_path_job(cfg: dict, local_path: str):
    import os as _os_module
    import shutil as _shutil

    if not local_path or not _os_module.path.isfile(local_path):
        return JSONResponse(
            {"error": f"找不到文件：{local_path or '(空路径)'}"},
            status_code=400,
        )

    local_path = os.path.abspath(local_path)  # P516：只记这一个地址，不复制、不另存名字

    jid, job_dir = _new_job_dir()
    try:
        file_hash = _hash_local_file(local_path)
    except Exception:
        _shutil.rmtree(job_dir, ignore_errors=True)
        return JSONResponse({"error": "读取本地文件失败，请检查文件是否可访问"}, status_code=500)

    return {
        "cfg": cfg,
        "jid": jid,
        "job_dir": job_dir,
        "file_hash": file_hash,
        "video_path": local_path,
    }


async def _prepare_json_job(request: Request):
    body = await request.json()
    cfg = _normalize_job_config(body.get("config", {}))
    local_path = body.get("local_path", "")
    source_url = body.get("url", "").strip()

    if source_url:
        return _prepare_url_job(cfg, source_url)
    return _prepare_local_path_job(cfg, local_path)


_SAFE_SUFFIX = re.compile(r"\.[A-Za-z0-9]{1,10}")


def _safe_upload_name(name) -> str:
    """N3：开发期网页上传按原名存进任务文件夹——只取最后一段名字（/ 与 \\ 都算分隔）；
    空名、. / ..、含控制字符、超过 200 字节的一律换成 video<后缀>，后缀不像后缀就用 .mp4。"""
    base = re.split(r"[\\/]", str(name or ""))[-1]
    suffix = os.path.splitext(base)[1]
    suffix = suffix if _SAFE_SUFFIX.fullmatch(suffix) else ".mp4"
    bad = (base in {"", ".", ".."} or len(base.encode("utf-8")) > 200
           or any(ord(ch) < 32 or ord(ch) == 127 for ch in base))
    return f"video{suffix.lower()}" if bad else base


async def _prepare_upload_job(request: Request):
    import shutil as _shutil

    form = await request.form()
    file = form.get("file")
    config_str = form.get("config", "{}")
    cfg = json.loads(config_str)
    cfg.pop("tier", None)
    # G11：上传入口同样要清掉已废弃的设置键——三个入口一个都不能漏。
    for _key in _DROPPED_CONFIG_KEYS:
        cfg.pop(_key, None)

    if not file:
        return JSONResponse({"error": "请选择视频文件或输入本地路径"}, status_code=400)

    jid, job_dir = _new_job_dir()
    # P516：按原名存，只交出这个文件的地址（V3.10-B1 接上桌面拖拽后整个入口删除）
    video_path = job_dir / _safe_upload_name(file.filename)

    # 流式写入 + SHA256（8MB 分块，恒定内存）
    hasher = hashlib.sha256()
    try:
        with open(video_path, "wb") as f:
            while chunk := await file.read(8 * 1024 * 1024):
                hasher.update(chunk)
                f.write(chunk)
        file_hash = hasher.hexdigest()
    except Exception:
        _shutil.rmtree(job_dir, ignore_errors=True)
        return JSONResponse({"error": "上传失败，请重试"}, status_code=500)

    return {
        "cfg": cfg,
        "jid": jid,
        "job_dir": job_dir,
        "file_hash": file_hash,
        "video_path": video_path,
    }


def _legacy_failed_translation_state(job_dir: Path) -> dict | None:
    """旧兼容入口保留为空；done 状态不再由任意缓存反向降级。"""
    return None


_RETRY_COUNTER_FIELDS = (
    "retry_started_at",
    "retry_initial_failed_count",
    "retry_processed_count",
    "retry_new_success_count",
    "retry_status",
)

_PARTIAL_SNAPSHOT_FIELDS = (
    "segment_count",
    "success_count",
    "failed_count",
    "failed_keys",
    "retry_count",
    "consecutive_no_progress",
    "recovered_count",
    "primary_failure_kind",
    "failure_kind_counts",
    "output_ready",
    _TRANSLATION_SUMMARY_FIELD,
    *_RETRY_COUNTER_FIELDS,
)


def _partial_snapshot_from_disk(job_dir: Path, meta: dict) -> dict:
    """从 meta 或最新翻译缓存恢复 partial 任务的完整状态。"""
    snapshot = {
        key: meta.get(key)
        for key in _PARTIAL_SNAPSHOT_FIELDS
        if meta.get(key) is not None
    }
    required = {
        "segment_count",
        "success_count",
        "failed_count",
        "failed_keys",
        "retry_count",
        "output_ready",
    }
    if required.issubset(snapshot):
        snapshot["snapshot_source"] = "meta"
        snapshot["snapshot_incomplete"] = False
        return snapshot

    match = _matching_checkpoint(job_dir, meta)
    if match is not None:
        path = match[0].parent / "translated.json"
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            rows = None
        if isinstance(rows, list):
            derived = _snapshot_from_translated_rows(rows, meta)
            derived["output_ready"] = bool(meta.get("output_ready", False))
            for key, value in derived.items():
                snapshot.setdefault(key, value)
            snapshot["snapshot_source"] = "cache_recomputed"
        else:
            snapshot["snapshot_source"] = "meta_incomplete"
    else:
        snapshot["snapshot_source"] = "meta_incomplete"

    snapshot["snapshot_incomplete"] = not required.issubset(snapshot)
    return snapshot


def _resume_cache_problem(job_dir: Path, meta: dict) -> str | None:
    checkpoints = list(job_dir.glob("cache/*/checkpoint.json"))
    if not checkpoints:
        return None
    match = _matching_checkpoint(job_dir, meta)
    if match is None:
        return "任务断点缓存已损坏，请重新提交原视频"
    checkpoint_path, data = match
    stage = data.get("stage", "start")
    stage_order = {
        "start": 0,
        "extract_audio": 1,
        "transcribe": 2,
        "translate": 3,
        "subtitle": 4,
        "burn": 5,
        "done": 6,
    }
    if stage not in stage_order:
        return "任务断点缓存已损坏，请重新提交原视频"
    cache_dir = checkpoint_path.parent
    if stage == "extract_audio":
        audio_path = cache_dir / "audio.wav"
        if not audio_path.is_file() or audio_path.stat().st_size == 0:
            return "音频缓存缺失或损坏，请重新提交原视频"
    if stage_order[stage] >= stage_order["transcribe"]:
        try:
            archive = json.loads(
                (cache_dir / "transcript.json").read_text(encoding="utf-8")
            )
            rows = archive.get("segments") if isinstance(archive, dict) else None
            if (
                not isinstance(archive, dict)
                or archive.get("version") != 2
                or not isinstance(archive.get("receipt"), dict)
                or not isinstance(rows, list)
                or any(
                not isinstance(row, dict)
                or not {"id", "start", "end", "text"}.issubset(row)
                for row in rows
                )
            ):
                raise ValueError("invalid transcript")
        except Exception:
            return "语音识别缓存缺失或损坏，请重新提交原视频"
    if stage_order[stage] >= stage_order["translate"]:
        try:
            rows = json.loads((cache_dir / "translated.json").read_text(encoding="utf-8"))
            if not isinstance(rows, list):
                raise ValueError("invalid translation")
        except Exception:
            return "字幕翻译缓存缺失或损坏，请重新提交原视频"
    return None


def _discard_prepared_job(job_dir: Path | None, old_job_dir: Path) -> None:
    import shutil as _shutil

    if job_dir is not None and job_dir.resolve() != old_job_dir.resolve():
        _shutil.rmtree(job_dir, ignore_errors=True)


def _resume_problem(job_dir: Path):
    """「继续」能不能走通：返回 (原因或 None, meta, config, 原视频路径)。

    /resume 与重新提交共用这一套检查：继续会被拒绝的情况，正好就是重拖会重跑的情况
    （D-1 补修，用户 2026-10-02 定）。"""
    try:
        meta = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        return "任务记录或缓存已损坏，请重新提交原视频", {}, {}, None
    config = meta.get("config")
    if not isinstance(config, dict):
        return "任务设置已损坏，请重新提交原视频", meta, {}, None
    video_path = _source_video(meta)  # P516：只看任务记录里那一个地址
    if video_path is None:
        return "原视频或必要缓存缺失，请重新提交原视频", meta, config, None
    cache_problem = _resume_cache_problem(job_dir, meta)
    if cache_problem:
        return cache_problem, meta, config, video_path
    return None, meta, config, video_path


def _adopt_prepared_input(job_dir: Path | None, old_job_dir: Path, old_meta: dict, video_path):
    """重拖认出同一任务时，这次该记下的地址（P516；链接任务没有，返回 None）。

    手填 / 桌面拖拽：就是这次的地址。开发期网页上传（Z4）：新文件在临时任务文件夹里，
    认出后那个文件夹会被删——旧记录的视频还在就沿用旧的；不在了才把新上传挪进旧任务
    文件夹（按原名）。"""
    import shutil as _shutil

    if not video_path or not Path(video_path).is_file():
        return None  # 不是真实存在的文件就不换地址
    source = Path(video_path)
    try:
        uploaded = job_dir is not None and source.resolve().parent == job_dir.resolve()
    except OSError:
        uploaded = False
    if not uploaded:
        return source
    kept = _source_video(old_meta)
    if kept is not None:
        return kept
    target = old_job_dir / source.name
    _shutil.move(str(source), str(target))
    return target


def _existing_job_source(existing: dict, old_jid: str, old_job_dir: Path) -> dict:
    """同身份旧任务的公开状态来源：索引 → 内存 → 磁盘，后者覆盖前者。"""
    source = dict(existing)
    try:
        disk_source = json.loads(
            (old_job_dir / "meta.json").read_text(encoding="utf-8")
        )
    except Exception:
        disk_source = {}
    if isinstance(JOBS.get(old_jid), dict):
        source.update(JOBS[old_jid])
    if isinstance(disk_source, dict):
        source.update(disk_source)
    return source


def _handle_existing_job(existing: dict, job_dir: Path | None, video_path, cfg: dict):
    """新提交命中同身份旧任务：只认出、返回原卡片，不启动（D-1 补修 C2）。

    中断 / 出错 → 原卡片，用户点「一键继续 / 继续」走 /resume 才开跑；
    已完成 → 原完成卡；运行中 → 接回；部分完成 → 原卡片。
    """

    old_jid = existing["job_id"]
    status = existing["status"]
    old_job_dir = JOBS_DIR / old_jid

    # Stale index entry — disk was cleared while server was running
    if not old_job_dir.is_dir():
        _cleanup_hash_index(old_jid)
        return None
    if status not in ("queued", "running"):
        # P516 / §8-2：认视频不认位置。先把记录里的地址更新为这次的地址，再做检查——
        # 检查要读地址；用旧地址查，挪过的视频就会被判成「继续不了」。
        try:
            old_meta = json.loads((old_job_dir / "meta.json").read_text(encoding="utf-8"))
        except Exception:
            old_meta = {}
        address = _adopt_prepared_input(job_dir, old_job_dir, old_meta, video_path)
        if address is not None:
            if str(address) != old_meta.get("source_path"):
                _set_source_path(old_jid, address)
            video_path = address
    if status in ("interrupted", "error"):
        problem = _resume_problem(old_job_dir)[0]
        if problem:
            # 「继续」走不通（缓存坏了 / 链接任务没下载完）：用户照提示重新提交原视频，就用这次的视频重跑，
            # Key 由后台从设置文件读（P512）。
            _log.info("[job:%s] 无法继续（%s），按重新提交重跑", old_jid, problem)
            _discard_prepared_job(job_dir, old_job_dir)
            this_run = _this_run_settings(cfg)
            if isinstance(this_run, JSONResponse):
                this_run = {}
            return _start_existing_job(existing, video_path, this_run)
        _discard_prepared_job(job_dir, old_job_dir)
        source = _existing_job_source(existing, old_jid, old_job_dir)
        source.setdefault("status", status)
        return {
            "found": True,
            "job_id": old_jid,
            **_public_job_response(old_jid, source, old_job_dir),
        }
    if status == "done":
        _discard_prepared_job(job_dir, old_job_dir)
        source = _existing_job_source(existing, old_jid, old_job_dir)
        source.update({"status": "done", "config": existing["config"]})
        return {
            "found": True,
            "job_id": old_jid,
            **_public_job_response(old_jid, source, old_job_dir),
        }
    if status == "partial":
        _discard_prepared_job(job_dir, old_job_dir)
        source = _existing_job_source(existing, old_jid, old_job_dir)
        source.update({"status": "partial", "config": existing.get("config", {})})
        return {"found": True, "job_id": old_jid, **_public_job_response(old_jid, source, old_job_dir)}
    if status in ("queued", "running"):
        _discard_prepared_job(job_dir, old_job_dir)
        return {"found": True, "job_id": old_jid, "status": status,
                "config": existing["config"]}

    _cleanup_hash_index(old_jid)
    return None


def _this_run_settings(payload):
    """继续 / 重试时前端带来的本次选择：只认 aiTranslateOnly，格式不对返回 400。
    Key 不在这里——后台自己读设置文件（P512）。"""
    if not isinstance(payload, dict):
        return {}
    this_run = {}
    if "aiTranslateOnly" in payload:
        if not isinstance(payload["aiTranslateOnly"], bool):
            return JSONResponse({"error": "设置格式不正确"}, status_code=400)
        this_run["aiTranslateOnly"] = payload["aiTranslateOnly"]
    return this_run


def _apply_this_run(config: dict, this_run: dict) -> dict:
    """旧任务设置 + 设置文件里的当前 Key（P512）；只翻译取「或」——旧任务是或本次检测后选了，
    都算（D-1 补修 D4）。"""
    merged = copy.deepcopy(config)
    merged["apiKeys"] = _current_api_keys()
    if this_run.get("aiTranslateOnly"):
        merged["aiTranslateOnly"] = True
    return merged


def _round_fields(*, config: dict, run_id: str, run_kind: str, revision: int,
                  resume_action=None, extra: dict | None = None) -> dict:
    """P510：「这一轮运行的信息」只拼这一份，同时写进硬盘记录和内存状态。

    原先两边各自手写一份，内存那份漏了「这次是导出」，出错卡的按钮由内存决定
    （job_state.build_public_task_state 读 partial_resume_action），于是导出失败后
    变成普通「重新开始」，被失效的 Key 挡住。"""
    fields = {
        "status": "running", "config": config,
        "run_id": run_id, "run_kind": run_kind, "revision": revision,
        **_empty_run_state(), **(extra or {}),
    }
    if resume_action in {"retry", "accept"}:
        fields["partial_resume_action"] = resume_action
    return fields


def _start_existing_job(existing: dict, video_path, this_run: dict | None = None):
    """/resume：真正启动中断 / 出错的旧任务（D-1 补修 C2 后只有这一条路会启动旧任务）。"""
    old_jid = existing["job_id"]
    old_job_dir = JOBS_DIR / old_jid
    if not old_job_dir.is_dir():
        _cleanup_hash_index(old_jid)
        return None
    job_dir = old_job_dir
    try:
        from . import model_manager
        original_model = existing["config"].get("model") or "large-v2"
        # B3: 用和 worker 加载同一把尺子（自管 ready 或旧缓存回退），
        # is_model_ready 只认自管目录，会把旧缓存模型误判成缺失。
        if not model_manager.is_model_loadable(original_model):
            return {
                "found": True,
                "job_id": old_jid,
                "status": "model_missing",
                "original_model": original_model,
                "config": existing["config"],
            }
    except Exception:
        pass

    lock = _job_run_lock(old_jid)
    if not lock.acquire(blocking=False):
        return {"found": True, "job_id": old_jid, "status": "running",
                "config": existing.get("config", {})}
    try:
        if worker_manager.is_alive(old_jid):
            return {"found": True, "job_id": old_jid, "status": "running",
                    "config": existing.get("config", {})}
        try:
            resume_meta = json.loads((old_job_dir / "meta.json").read_text(encoding="utf-8"))
        except Exception:
            resume_meta = {}
        persisted_config = resume_meta.get("config")
        if not isinstance(persisted_config, dict):
            persisted_config = existing["config"]
        # D-1 补修 A3/A5：旧任务设置 + 本次 Key / 只翻译，写回记录（记录 = 上一次真正运行的设置）
        persisted_config = _apply_this_run(persisted_config, this_run or {})
        run_config = copy.deepcopy(persisted_config)
        resume_action = resume_meta.get("partial_resume_action")
        if resume_action == "retry":
            run_config["_partial_retry"] = True
        elif resume_action == "accept":
            run_config["_accept_partial"] = True
        if resume_action in {"retry", "accept"}:
            run_config["_retry_count"] = int(resume_meta.get("retry_count", 0))
            run_config["_previous_failed_keys"] = resume_meta.get("failed_keys", [])
            run_config["_consecutive_no_progress"] = int(
                resume_meta.get("consecutive_no_progress", 0)
            )

        run_id = uuid.uuid4().hex
        run_kind = {
            "retry": "partial_retry",
            "accept": "accept_partial",
        }.get(resume_action, "resume")
        if run_kind == "resume":
            run_config["_resume"] = True
        previous_revision = max(
            int(resume_meta.get("revision", 0) or 0),
            int((JOBS.get(old_jid) or {}).get("revision", 0) or 0),
        )
        next_revision = previous_revision + 1
        round_fields = _round_fields(
            config=persisted_config, run_id=run_id, run_kind=run_kind,
            revision=next_revision, resume_action=resume_action,
            extra={"source_path": resume_meta.get("source_path")},  # 名字从它现算
        )
        next_job = {
            "id": old_jid, "progress": 0, "stage": "", "message": "正在继续处理…",
            "error": None, "last_progress": time.time(),
            **round_fields,
        }
        if not _persist_job_status(old_jid, "running", round_fields):
            return {"found": True, "job_id": old_jid, "status": "error",
                    "config": persisted_config}
        JOBS[old_jid] = next_job
        existing["config"] = persisted_config
        existing["status"] = "running"
        try:
            worker_manager.start(
                old_jid, job_dir, str(video_path), run_config,
                run_id=run_id, run_kind=run_kind,
            )
        except Exception:
            # A6: 启动失败不回滚到上一轮。start() 可能已经拉起进程，
            # 恢复旧 JOBS 会让孤儿 Worker 的消息被当成当前轮次接受；
            # 也绝不留下没有 run_id 的条目。收敛为 error + 空结果 +
            # 保留新身份，用户经 /resume 继续同一动作。
            failure_message = "任务恢复启动失败，请稍后继续。"
            resume_meta.update({
                "status": "error", "error": failure_message,
                "run_id": run_id, "run_kind": run_kind,
                "config": persisted_config,
                **_empty_run_state(),
            })
            try:
                _write_json_atomic(old_job_dir / "meta.json", resume_meta)
            except OSError:
                pass
            existing["status"] = "error"
            JOBS[old_jid] = {
                "id": old_jid,
                "status": "error",
                "progress": 100,
                "error": failure_message,
                "config": persisted_config,
                "source_path": resume_meta.get("source_path"),
                "run_id": run_id,
                "run_kind": run_kind,
                "revision": next_revision,
                **_empty_run_state(),
            }
            with _JOB_INDEX_LOCK:
                _update_hash_index(old_jid, "error")
            return {
                "found": True,
                "job_id": old_jid,
                "status": "error",
                "config": persisted_config,
                "start_failed": True,
            }
        return {"found": True, "job_id": old_jid, "status": "running",
                "config": persisted_config}
    finally:
        lock.release()


def _start_new_job(jid: str, job_dir: Path, video_path, cfg: dict, file_hash: str, tk: str):
    run_id = uuid.uuid4().hex
    run_kind = "normal"
    # P516：每个任务只记一样东西——原视频地址（链接任务下载完成时才有，见 Z1）
    source_path = str(video_path) if video_path else None
    meta = {"file_hash": file_hash, "task_key": tk, "config": cfg,
            "source_path": source_path, "status": "queued",
            "run_id": run_id, "run_kind": run_kind, "revision": 0,
            **_empty_run_state()}
    # 原子写：写盘失败就不建索引、不启 Worker，也不留下半份 meta。
    _write_json_atomic(job_dir / "meta.json", meta)

    GLOBAL_HASH_INDEX[tk] = {"job_id": jid, "status": "queued", "config": cfg, "file_hash": file_hash}
    JOB_ID_TO_HASH[jid] = tk

    JOBS[jid] = {
        "id": jid, "status": "queued", "progress": 0,
        "stage": "", "message": "", "error": None,
        "config": cfg,
        "source_path": source_path,
        "last_progress": time.time(),
        "run_id": run_id, "run_kind": run_kind, "revision": 0,
        **_empty_run_state(),
    }
    worker_manager.start(
        jid, job_dir, str(video_path), cfg,
        run_id=run_id, run_kind=run_kind,
    )
    return {"id": jid, "found": False}


@app.post("/api/jobs")
async def create_job(request: Request):
    content_type = request.headers.get("content-type", "")
    prepared = (
        await _prepare_json_job(request)
        if content_type and "json" in content_type
        else await _prepare_upload_job(request)
    )
    if isinstance(prepared, JSONResponse):
        return prepared

    cfg = prepared["cfg"]
    cfg["apiKeys"] = _current_api_keys()  # P512：不认网页带来的 Key
    jid = prepared["jid"]
    job_dir = prepared["job_dir"]
    file_hash = prepared["file_hash"]
    video_path = prepared["video_path"]

    # ── 以下为共享的去重 + 任务创建逻辑 ──
    # v2.09: config-aware dedup — same video + same settings = reuse, different settings = new task

    tk = _task_key(file_hash, cfg)
    existing = GLOBAL_HASH_INDEX.get(tk)

    if existing:
        response = _handle_existing_job(existing, job_dir, video_path, cfg)
        if response is not None:
            return _sanitize_task_payload(response)

    return _start_new_job(jid, job_dir, video_path, cfg, file_hash, tk)


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str, payload: dict | None = None):
    this_run = _this_run_settings(payload)
    if isinstance(this_run, JSONResponse):
        return this_run
    job_dir = _validate_job_path(job_id)
    if not job_dir or not job_dir.is_dir():
        return JSONResponse({"error": "未找到该任务"}, status_code=404)
    problem, meta, config, video_path = _resume_problem(job_dir)
    if meta and meta.get("status") not in {"interrupted", "error"}:
        return JSONResponse({"error": "任务当前不能继续"}, status_code=409)
    if problem:
        return JSONResponse({"error": problem}, status_code=409)

    file_hash = str(meta.get("file_hash") or "")
    task_key = str(meta.get("task_key") or "")
    if not task_key and file_hash:
        task_key = _task_key(file_hash, config)
    existing = {
        "job_id": job_id,
        "status": meta["status"],
        "config": config,
        "file_hash": file_hash,
    }
    with _JOB_INDEX_LOCK:
        if task_key:
            GLOBAL_HASH_INDEX[task_key] = existing
            JOB_ID_TO_HASH[job_id] = task_key

    result = _start_existing_job(existing, str(video_path), this_run)
    if result is None:
        return JSONResponse({"error": "任务无法继续"}, status_code=409)
    if result.get("start_failed"):
        return JSONResponse({
            "error": "任务恢复启动失败，请稍后重试"
        }, status_code=500)
    return _sanitize_task_payload({"ok": result.get("status") == "running", **result})


# ── v2.05: Active jobs recovery (fallback when localStorage is empty) ──

@app.get("/api/active-jobs")
def get_active_jobs():
    """Return all incomplete jobs from memory index.
    Frontend calls this when localStorage is empty but disk may have
    unfinished work — e.g. user cleared browser data or switched browsers."""
    active = []
    for fh, entry in GLOBAL_HASH_INDEX.items():
        jid = entry["job_id"]
        if not _is_recoverable_active_job(jid, entry):
            continue
        j = JOBS.get(jid)
        source = j if j else entry
        public_source = {
            "job_id": jid,
            "status": entry["status"],
            "config": entry.get("config", {}),
            "progress": j.get("progress", 0) if j else 0,
            "message": j.get("message", "") if j else entry.get("error", ""),
            "stage": j.get("stage", "") if j else entry.get("stage", ""),
            **{
                key: source.get(key)
                for key in _RESULT_FIELDS
                if key in source
            },
            **{
                key: value
                for key, value in _restored_from_meta(j if j else entry).items()
                if key not in _RESULT_FIELDS
            },
        }
        active.append(_public_job_response(jid, public_source, _validate_job_path(jid)))
    return _sanitize_task_payload({"jobs": active})


# ── Job status / download / cancel ──

@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    j = JOBS.get(job_id)
    safe_dir = _validate_job_path(job_id)
    if not safe_dir:
        return JSONResponse({"error": "无效的任务编号"}, status_code=400)
    if j:
        meta_path = safe_dir / "meta.json"
        alive = worker_manager.is_alive(job_id)
        if not meta_path.exists() and not alive:
            JOBS.pop(job_id, None)
            _cleanup_hash_index(job_id)
            return JSONResponse({"error": "未找到该任务"}, status_code=404)
        # P346: authoritative convergence — if the worker is gone but the tracked
        # status is still active, converge to interrupted (recoverable) and persist
        # to meta.json + hash index, so the frontend doesn't rely only on local
        # dead-poll detection.
        if not alive and j.get("status") in ("running", "queued"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {"status": j.get("status", "running"), "config": j.get("config", {})}
            reconciled = _reconcile_job_status_on_disk(safe_dir, meta)
            j.update(_restored_from_meta(meta))
            j["status"] = reconciled
            j["process_alive"] = False
            for key in (*_RESULT_FIELDS, "delivery"):
                if key in meta:
                    j[key] = meta[key]
            if reconciled in ("done", "partial"):
                j["progress"] = 100
            _update_hash_index(job_id, reconciled)
            return _public_job_response(job_id, j, safe_dir)
        j["process_alive"] = alive
        return _public_job_response(job_id, j, safe_dir)
    if not j:
        meta_path = safe_dir / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                # v2.05: priority to GLOBAL_HASH_INDEX (corrected at startup, e.g. "interrupted")
                tk = JOB_ID_TO_HASH.get(job_id)
                status = GLOBAL_HASH_INDEX[tk]["status"] if (tk and tk in GLOBAL_HASH_INDEX) else meta.get("status", "unknown")
                return _public_job_response(job_id, {
                    "id": job_id, "status": status,
                    # 投影的原料必须来自 meta：运行身份和上一轮结果，缺一项就会
                    # 被 _public_result_projection 判成"没有归属"。
                    "run_id": meta.get("run_id"),
                    **{key: meta.get(key) for key in _RESULT_FIELDS},
                    "progress": 0, "stage": "", "message": "", "error": None,
                    "config": meta.get("config", {}),
                    "source_path": meta.get("source_path"),
                    "last_progress": 0,
                    "process_alive": worker_manager.is_alive(job_id),
                    **_restored_from_meta(meta),
                    "status": status,
                }, safe_dir)
            except Exception:
                pass
        return JSONResponse({"error": "未找到该任务"}, status_code=404)


def _open_with_default_app(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))
        return
    command = ["open", "-a", "TextEdit"] if sys.platform == "darwin" else ["xdg-open"]
    subprocess.run(
        [*command, str(path)],
        check=True,
        timeout=10,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@app.post("/api/jobs/{job_id}/log/open")
def open_job_log(job_id: str, request: Request):
    host = request.headers.get("host", "").lower().split(":", 1)[0]
    if (
        request.headers.get("x-video-subber-action") != "open-job-log"
        or host not in {"127.0.0.1", "localhost"}
    ):
        return JSONResponse({"error": "不允许执行此操作"}, status_code=403)

    job_entry = JOBS_DIR / job_id
    job_dir = _validate_job_path(job_id)
    if not job_dir or job_entry.is_symlink() or not job_dir.is_dir():
        return JSONResponse({"error": "未找到该任务"}, status_code=404)

    log_root = LOG_DIR.resolve()
    log_dir = LOG_DIR / job_id
    if log_dir.is_symlink():
        return JSONResponse({"error": "当前任务还没有可打开的日志"}, status_code=404)
    resolved_log_dir = log_dir.resolve()
    try:
        resolved_log_dir.relative_to(log_root)
    except ValueError:
        return JSONResponse({"error": "当前任务还没有可打开的日志"}, status_code=404)
    if not resolved_log_dir.is_dir():
        return JSONResponse({"error": "当前任务还没有可打开的日志"}, status_code=404)

    log_path = log_dir / "user.log"
    if log_path.is_symlink() or not log_path.is_file():
        return JSONResponse({"error": "当前任务还没有可打开的日志"}, status_code=404)
    resolved_log_path = log_path.resolve()
    try:
        resolved_log_path.relative_to(log_root)
    except ValueError:
        return JSONResponse({"error": "当前任务还没有可打开的日志"}, status_code=404)
    if resolved_log_path.parent != resolved_log_dir:
        return JSONResponse({"error": "当前任务还没有可打开的日志"}, status_code=404)

    try:
        _open_with_default_app(resolved_log_path)
    except (OSError, subprocess.SubprocessError):
        return JSONResponse(
            {"error": "无法打开任务日志，请稍后重试"}, status_code=500
        )
    return {"ok": True}


def _saved_output_target(source: dict, fields: dict, job_dir: Path) -> Path | None:
    """本轮成品所在的用户保存目录。目录由本轮配置推导，不存任何路径记录。

    只返回目录，绝不返回推导出来的文件名。Worker 遇到同名但内容不同的文件时
    会避让到 "xxx (2).srt"，而推导出的原始名下面躺着的正是用户自己的文件——
    "推导路径存在"不等于"它就是本轮成品"。指着它就是 P386 的认错对象。"""
    from .output_artifacts import saved_output_paths

    config = source.get("config") if isinstance(source, dict) else None
    config = config if isinstance(config, dict) else {}
    srt = fields.get("result_srt")
    if not srt:
        return None
    video = fields.get("result_video")
    mode = _output_mode_of(config)
    artifacts = {"srt": Path(srt),
                 "burn": Path(video or srt),
                 "mux": Path(video or srt)}
    destinations = saved_output_paths(
        config, artifacts, mode, source_path=source.get("source_path"), job_dir=job_dir)
    # burn / mux 优先定位视频；srt 模式只有字幕。
    preferred = destinations.get(mode) or destinations.get("srt")
    if preferred is None:
        return None
    return preferred.parent if preferred.parent.is_dir() else None


def _reveal_in_file_manager(path: Path) -> None:
    """在系统文件管理器里定位这个文件。

    单独实现，不复用 _open_with_default_app 的 TextEdit 逻辑：那是"用文本
    编辑器打开内容"，这里要的是"在文件夹里显示"。参数按数组传，不走 shell。"""
    path = Path(path)
    if sys.platform == "win32":
        command = ["explorer", f"/select,{path}"]
    elif sys.platform == "darwin":
        command = ["open", "-R", str(path)]
    else:
        command = ["xdg-open", str(path.parent)]
    subprocess.run(
        command, check=False, timeout=10,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


@app.post("/api/jobs/{job_id}/output/reveal")
def reveal_job_output(job_id: str, request: Request):
    """B2：在文件夹中显示。只接受 job_id，目录由后端从可信交付记录决定。"""
    host = request.headers.get("host", "").lower().split(":", 1)[0]
    if (
        request.headers.get("x-video-subber-action") != "reveal-output"
        or host not in {"127.0.0.1", "localhost"}
    ):
        return JSONResponse({"error": "不允许执行此操作"}, status_code=403)

    job_entry = JOBS_DIR / job_id
    # P189: 原始任务编号必须已经是规范化的，不能靠 resolve() 事后补救。
    if job_entry.resolve() != job_entry:
        return JSONResponse({"error": "无效的任务编号"}, status_code=400)
    job_dir = _validate_job_path(job_id)
    if not job_dir or job_entry.is_symlink() or not job_dir.is_dir():
        return JSONResponse({"error": "未找到该任务"}, status_code=404)

    source = JOBS.get(job_id)
    if not isinstance(source, dict) or source.get("status") not in ("done", "partial"):
        try:
            source = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return JSONResponse({"error": "未找到该任务"}, status_code=404)
    fields = _public_result_projection(source, job_dir)
    if not fields["can_show_output_folder"]:
        return JSONResponse(
            {"error": "本次处理还没有已保存的成品文件"}, status_code=409
        )
    target = _saved_output_target(source, fields, job_dir)
    if target is None:
        return JSONResponse(
            {"error": "本次处理还没有已保存的成品文件"}, status_code=409
        )
    try:
        _reveal_in_file_manager(target)
    except (OSError, subprocess.SubprocessError):
        return JSONResponse(
            {"error": "无法打开成品所在文件夹，请稍后重试"}, status_code=500
        )
    return {"ok": True}


def _handle_worker_terminal(job_id: str, message: dict, publish=None):
    """Worker 终态的唯一入口。在 Worker 读取线程里跑，绝不占用事件循环。

    收到这条消息时成品已经在用户目录里了——Worker 送达之后才宣告完成。父进程
    只在任务锁内登记本轮候选并产生公开终态快照，不搬运、不删源。

    `publish` 由 WorkerManager 按位置传入，新流程不再需要它：搬运期间的进度
    由 Worker 自己发普通 progress 消息。签名保留以匹配调用方。"""
    with _job_run_lock(job_id):
        snapshot = _sync_job_from_worker_message(job_id, message)
        if snapshot is None:
            return None
        j = JOBS.get(job_id)
        if not isinstance(j, dict):
            return snapshot
        terminal = j.get("status")
        if terminal not in ("done", "partial") or not j.get("output_ready"):
            return snapshot
        job_dir = _validate_job_path(job_id)
        if job_dir is None:
            return snapshot
        with _JOB_INDEX_LOCK:
            _update_hash_index(job_id, terminal)
        return job_state.build_public_task_state(j)


@app.delete("/api/jobs/{job_id}")
def cancel_job(job_id: str):
    import time, shutil

    worker_manager.cancel(job_id)

    time.sleep(0.1)

    j = JOBS.pop(job_id, None)
    if j:
        j["status"] = "cancelled"
    _cleanup_hash_index(job_id)

    # 删除 input 视频 + meta.json。保留 cache/ 下的中间产物（audio.wav, transcript.json 等）
    # ——同一视频+相同设置重新上传时可复用缓存。meta.json 必须删，否则 startup 扫描复用索引。
    job_dir = JOBS_DIR / job_id
    # P189: prevent path traversal via .resolve() guard
    if job_dir.resolve() != job_dir or not str(job_dir.resolve()).startswith(str(JOBS_DIR.resolve())):
        return JSONResponse({"error": "无效的任务编号"}, status_code=400)
    if job_dir.exists():
        meta_path = job_dir / "meta.json"
        try:
            source = json.loads(meta_path.read_text(encoding="utf-8")).get("source_path")
        except Exception:
            source = None
        # N1：删任务绝不碰用户的原视频——只删真实位置落在本任务文件夹之内的那个
        from .output_artifacts import is_inside
        if isinstance(source, str) and source and is_inside(source, job_dir):
            Path(source).unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)

    return {"ok": True}


@app.post("/api/jobs/{job_id}/partial-action")
def partial_action(job_id: str, payload: dict):
    safe_dir = _validate_job_path(job_id)
    if not safe_dir or not safe_dir.is_dir():
        return JSONResponse({"error": "未找到该任务"}, status_code=404)
    lock = _job_run_lock(job_id)
    if not lock.acquire(blocking=False):
        return JSONResponse({"error": "任务正在处理，请稍后"}, status_code=409)
    try:
        meta_path = safe_dir / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return JSONResponse({"error": "任务记录损坏"}, status_code=409)
        if meta.get("status") != "partial" or worker_manager.is_alive(job_id):
            return JSONResponse({"error": "任务当前不能执行该操作"}, status_code=409)
        action = payload.get("action")
        if action not in {"retry", "accept"}:
            return JSONResponse({"error": "无效操作"}, status_code=400)
        video_path = _source_video(meta)  # P516：只看任务记录里那一个地址
        if video_path is None:
            return JSONResponse({
                "error": "找不到原视频，请把它放回原来的位置，或重新拖进来"
            }, status_code=409)
        snapshot = _partial_snapshot_from_disk(safe_dir, meta)
        if snapshot.get("snapshot_incomplete"):
            return JSONResponse({
                "error": "任务记录不完整，无法安全重试。请重新提交原视频。"
            }, status_code=409)
        authoritative_config = copy.deepcopy(meta.get("config", {}))
        if action == "retry":
            # P512：Key 由 _apply_this_run 从设置文件读（与门口检测同一份）；
            # 「保留未翻译字幕并生成文件」不调用 AI，不处理 Key（D2，worker 内现算为空）。
            this_run = _this_run_settings(payload)
            if isinstance(this_run, JSONResponse):
                return this_run
            authoritative_config = _apply_this_run(authoritative_config, this_run)
        retry_count = int(meta.get("retry_count", 0)) + (1 if action == "retry" else 0)
        file_hash = str(meta.get("file_hash") or "")
        if not file_hash:
            old_key = JOB_ID_TO_HASH.get(job_id)
            old_entry = GLOBAL_HASH_INDEX.get(old_key, {}) if old_key else {}
            file_hash = str(old_entry.get("file_hash") or "")
        if not file_hash and authoritative_config.get("source_url"):
            file_hash = hashlib.sha256(
                authoritative_config["source_url"].encode()
            ).hexdigest()
        if not file_hash:
            try:
                file_hash = _hash_local_file(str(video_path))
            except Exception:
                return JSONResponse({"error": "无法确认原视频身份，请重新提交"}, status_code=409)

        old_task_key = (
            JOB_ID_TO_HASH.get(job_id)
            or meta.get("task_key")
            or _task_key(file_hash, meta.get("config", {}))
        )
        new_task_key = _task_key(file_hash, authoritative_config)
        run_id = uuid.uuid4().hex
        run_kind = "partial_retry" if action == "retry" else "accept_partial"
        run_config = copy.deepcopy(authoritative_config)
        if action == "retry":
            run_config["_partial_retry"] = True
        else:
            run_config["_accept_partial"] = True
        run_config["_retry_count"] = retry_count
        run_config["_previous_failed_keys"] = meta.get("failed_keys", [])
        run_config["_consecutive_no_progress"] = int(
            meta.get("consecutive_no_progress", 0)
        )
        retry_state = {}
        if action == "retry":
            retry_state = {
                "retry_started_at": time.time(),
                "retry_initial_failed_count": int(meta.get("failed_count", 0)),
                "retry_processed_count": 0,
                "retry_new_success_count": 0,
                "retry_status": "running",
            }
            run_config["_retry_started_at"] = retry_state["retry_started_at"]
            run_config["_retry_initial_failed_count"] = retry_state[
                "retry_initial_failed_count"
            ]

        with _JOB_INDEX_LOCK:
            collision = GLOBAL_HASH_INDEX.get(new_task_key)
            if collision and collision.get("job_id") != job_id:
                return JSONResponse({"error": "相同视频和设置已有另一个任务"}, status_code=409)

            old_index_entry = copy.deepcopy(GLOBAL_HASH_INDEX.get(old_task_key))
            previous_revision = max(
                int(meta.get("revision", 0) or 0),
                int((JOBS.get(job_id) or {}).get("revision", 0) or 0),
            )
            next_revision = previous_revision + 1
            # P388: 新一轮先撤销上一轮的成品资格（_empty_run_state），Worker 启动前就已生效。
            round_fields = _round_fields(
                config=authoritative_config, run_id=run_id, run_kind=run_kind,
                revision=next_revision, resume_action=action,
                extra={"file_hash": file_hash, "task_key": new_task_key,
                       "retry_count": retry_count, **retry_state},
            )
            new_meta = copy.deepcopy(meta)
            new_meta.update(round_fields)
            new_meta.pop("error", None)
            try:
                _write_json_atomic(meta_path, new_meta)
                if old_task_key != new_task_key:
                    GLOBAL_HASH_INDEX.pop(old_task_key, None)
                entry = copy.deepcopy(old_index_entry or {})
                entry.update({
                    "job_id": job_id,
                    "status": "running",
                    "config": authoritative_config,
                    "file_hash": file_hash,
                })
                GLOBAL_HASH_INDEX[new_task_key] = entry
                JOB_ID_TO_HASH[job_id] = new_task_key

                JOBS.setdefault(job_id, {
                    "id": job_id,
                    "source_path": new_meta.get("source_path"),
                })
                JOBS[job_id].pop("current_state", None)
                JOBS[job_id].update(snapshot)
                JOBS[job_id].update({
                    "progress": 0,
                    "stage": "translate",
                    "message": "正在重试失败部分…" if action == "retry" else "正在生成字幕…",
                    **round_fields,
                })
                worker_manager.start(
                    job_id, safe_dir, str(video_path), run_config,
                    run_id=run_id, run_kind=run_kind,
                )
            except Exception:
                # P388/P1-B: 新一轮启动失败 → error + 空结果四元组。
                # 不回滚到 partial：start() 可能已经拉起进程，回退 run_id 会让
                # 孤儿 Worker 的消息被当成当前轮次接受。改为保持新身份并收敛为
                # error，保留原动作和统计，用户通过 /resume 继续同一动作。
                failure_message = (
                    "重试失败部分没能启动，请稍后继续。"
                    if action == "retry"
                    else "生成字幕文件没能启动，请稍后继续。"
                )
                failed_meta = copy.deepcopy(new_meta)
                failed_meta.update({
                    "status": "error",
                    "error": failure_message,
                    "partial_resume_action": action,
                    **_empty_run_state(),
                })
                try:
                    _write_json_atomic(meta_path, failed_meta)
                except OSError:
                    pass
                # 磁盘、索引、内存保持同一个新身份，/resume 才能读到正确动作。
                if old_task_key != new_task_key:
                    GLOBAL_HASH_INDEX.pop(old_task_key, None)
                entry = copy.deepcopy(old_index_entry or {})
                entry.update({
                    "job_id": job_id,
                    "status": "error",
                    "error": failure_message,
                    "config": authoritative_config,
                    "file_hash": file_hash,
                    **_empty_run_state(),
                })
                GLOBAL_HASH_INDEX[new_task_key] = entry
                JOB_ID_TO_HASH[job_id] = new_task_key
                JOBS.setdefault(job_id, {"id": job_id})
                JOBS[job_id].update({
                    "status": "error",
                    "progress": 100,
                    "error": failure_message,
                    "partial_resume_action": action,
                    **_empty_run_state(),
                })
                return JSONResponse({"error": failure_message}, status_code=500)

        return _sanitize_task_payload({
            "ok": True, "job_id": job_id, "action": action,
            "config": authoritative_config,
        })
    finally:
        lock.release()


# ── Video download ──

@app.post("/api/download-video")
def download_video_endpoint(payload: dict):
    """Download a video from URL, return local file path."""
    url = payload.get("url", "").strip()
    if not url:
        return JSONResponse({"error": "请提供视频链接"}, status_code=400)
    if not _verify_safe_url(url):
        return JSONResponse({"error": "仅支持安全的 http/https 链接"}, status_code=400)
    from .core.video_downloader import download_video, is_ytdlp_available
    if not is_ytdlp_available():
        return JSONResponse({"error": "yt-dlp 未安装，无法下载视频"}, status_code=500)
    dl_dir = str(Path.home() / "Downloads" / "Video-Subber")
    Path(dl_dir).mkdir(parents=True, exist_ok=True)
    result = download_video(url, dl_dir)
    if not result:
        return JSONResponse({"error": "下载失败，请检查链接是否有效"}, status_code=500)
    video_path, _audio_path = result
    return {"ok": True, "path": video_path, "filename": Path(video_path).name}

# ── WebSocket ──

@app.websocket("/ws/{job_id}")
async def websocket_progress(websocket: WebSocket, job_id: str):
    await ws_manager.register(job_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.unregister(job_id, websocket)
    except Exception:
        ws_manager.unregister(job_id, websocket)


# ── Tracking: sync JOBS dict from worker messages ──

def _sync_job_from_worker_message(job_id: str, message: dict):
    j = JOBS.get(job_id)
    if not j:
        return None
    # P1-A: 权威身份缺失、为空或非法 → 当前没有可授权运行，一律拒绝。
    current_run_id = _authoritative_run_id(j)
    if not current_run_id or message.get("run_id") != current_run_id:
        _log.info(
            "[worker:%s] 丢弃无权运行消息: current=%r incoming=%r type=%s",
            job_id,
            j.get("run_id"),
            message.get("run_id"),
            message.get("type"),
        )
        return None
    lock = _job_run_lock(job_id)
    with lock:
        # 拿到锁后重新核对，防止等锁期间已经切换到新一轮。
        if _authoritative_run_id(JOBS.get(job_id)) != current_run_id:
            return None
        _sync_current_job_message(job_id, j, message)
        return job_state.build_public_task_state(j)


def _mark_terminal_persist_error(j: dict) -> None:
    j["status"] = "error"
    j["progress"] = 100
    j["error"] = "保存任务状态失败，请检查磁盘空间或文件权限后重试。"
    j["revision"] = int(j.get("revision", 0) or 0) + 1
    j["message"] = None
    j["activity"] = None


_RUNNING_MESSAGE_TYPES = frozenset({
    "progress", "transcription_progress", "retry_progress", "engine_status",
})

_RUNNING_MESSAGE_EXCLUDED = frozenset({
    "type", "percent", "detail",
    *("result_srt", "result_video", "result_run_id", "output_ready"),
    "delivery", "is_delivering", "can_show_output_folder",
})

_RUNNING_DURABLE_FIELDS = frozenset({
    "run_id", "stage", "retry_started_at", "retry_initial_failed_count",
    "retry_processed_count", "retry_new_success_count", "retry_status",
})


def _running_fields_from_message(message: dict) -> dict:
    """运行中消息 → 任务字段。反向排除，新增字段零登记。

    JOBS 保留 Worker 的原始任务事实；公开文案由 job_state 即时生成。
    """
    fields = {key: value for key, value in message.items()
              if key not in _RUNNING_MESSAGE_EXCLUDED}
    if message.get("percent") is not None:
        fields["progress"] = message.get("percent")
    fields["activity"] = message.get("detail")
    if message.get("type") == "engine_status":
        for _raw in ("engine_category", "phase", "failure_kind"):
            fields.pop(_raw, None)
        fields["engine_status"] = {
            "engine_category": message.get("engine_category"),
            "phase": message.get("phase"),
            "failure_kind": message.get("failure_kind"),
        }
    if message.get("type") == "transcription_progress":
        fields.setdefault("stage", "transcribe")
    elif message.get("type") == "progress":
        fields["transcription_phase"] = None
        fields["engine_status"] = None
    return fields

_TERMINAL_MESSAGE_EXCLUDED = frozenset({
    "type", "status", "action_error", "percent", "detail", "message",
    "technical_detail",
    *("result_srt", "result_video", "result_run_id", "output_ready"),
})


def _terminal_fields_from_message(message: dict) -> dict:
    """终态消息 → 任务字段。反向排除，新增字段零登记。"""
    fields = {key: value for key, value in message.items()
              if key not in _TERMINAL_MESSAGE_EXCLUDED}
    if _SOUND_REFERENCE_FIELD in fields:
        fields[_SOUND_REFERENCE_FIELD] = _normalize_sound_reference(
            fields[_SOUND_REFERENCE_FIELD]
        )
    return fields


def _sync_current_job_message(job_id: str, j: dict, message: dict) -> None:
    j["last_progress"] = time.time()
    message_type = message.get("type")
    revision = int(j.get("revision", 0) or 0) + 1

    if message_type in _RUNNING_MESSAGE_TYPES:
        fields = _running_fields_from_message(message)
        next_status = "running" if j.get("status") in ("queued", "running") else j.get("status")
        durable = {
            key: value for key, value in fields.items()
            if key in _RUNNING_DURABLE_FIELDS
        }
        durable["revision"] = revision
        if not _persist_job_status(job_id, next_status, durable):
            _mark_terminal_persist_error(j)
            return
        j.update(fields)
        j["status"] = next_status
        j["revision"] = revision
        return

    if message_type == "audio_track_status":
        source_audio_state = message.get("source_audio_state", "unknown")
        if source_audio_state not in {"has_audio", "no_audio", "unknown"}:
            source_audio_state = "unknown"
        if not _persist_job_status(job_id, "running", {
            "source_audio_state": source_audio_state, "revision": revision,
        }):
            return
        j.update({"source_audio_state": source_audio_state,
                  "status": "running", "revision": revision})
        return

    if message_type == "token_usage":
        j["token_usage"] = message.get("usage")
        return

    if message_type == "source_ready":
        # Z1：链接任务下载完成——下载好的文件就是这个任务的原视频地址，只写这一次。
        # 任务记录只由主程序写，干活程序经汇报交过来。
        path = message.get("path")
        if isinstance(path, str) and path and Path(path).is_file():
            _set_source_path(job_id, path)
            j["revision"] = revision
        return

    if message_type not in {"done", "partial", "error"}:
        return

    fields = _terminal_fields_from_message(message)
    fields.update(_verified_result_fields(
        job_id, _authoritative_run_id(j), message,
        output_ready=message_type == "done" or bool(message.get("output_ready")),
    ))
    fields["revision"] = revision
    if message_type == "error":
        error_message = message.get("message")
        error_message = error_message if isinstance(error_message, str) else "处理失败"
        fields["error"] = error_message
    if message_type == "partial" and isinstance(message.get("action_error"), str):
        fields["action_error"] = message["action_error"]

    if not _persist_job_status(
        job_id, message_type, fields,
        clear_partial_action=message_type in {"done", "partial"},
    ):
        _mark_terminal_persist_error(j)
        return

    j.update(fields)
    j["status"] = message_type
    j["progress"] = 100
    j["revision"] = revision
    j["message"] = None
    j["activity"] = None
    if message_type == "partial" and not message.get("action_error"):
        j.pop("action_error", None)
    if message_type in {"partial", "error"}:
        with _JOB_INDEX_LOCK:
            tk = JOB_ID_TO_HASH.get(job_id)
            if tk and tk in GLOBAL_HASH_INDEX:
                GLOBAL_HASH_INDEX[tk].update(fields)


def _ensure_error_user_log(job_id: str) -> None:
    """P384 终态兜底：error 终态确保 user.log 存在且至少有一句人话。

    只在 error 终态调用（done/partial 不调用，保持"成功即静默"）。覆盖
    run_id 缺失/锁失败等 worker 在建日志前就 sys.exit、user.log 从未创建的情况——
    否则用户点"打开日志文件"会撞 404「当前任务还没有可打开的日志」。
    已有内容（含 worker 写入的求助清单文案）一律不覆盖。
    """
    log_dir = LOG_DIR / job_id
    user_log = log_dir / "user.log"
    try:
        if user_log.is_file() and user_log.stat().st_size > 0:
            return
        log_dir.mkdir(parents=True, exist_ok=True)
        hint = "（技术细节在同目录的 debug.log 里）" if (log_dir / "debug.log").is_file() else ""
        user_log.write_text(
            f"处理失败了，我们没能识别出具体原因。请先重试一次；"
            f"如果还是失败，把这个任务的日志发给我们{hint}。\n",
            encoding="utf-8",
        )
    except OSError:
        pass  # 兜底本身不能再抛错拖垮终态收敛


def _sync_job_from_worker_exit(
    job_id: str,
    run_id: str | None,
    process,
    exit_code: int,
    last_message_type: str | None,
):
    """Converge the current run from disk after all stdout messages are handled."""
    lock = _job_run_lock(job_id)
    with lock:
        j = JOBS.get(job_id)
        if not j:
            return None
        current_run_id = j.get("run_id")
        if current_run_id and run_id != current_run_id:
            return job_state.build_public_task_state(j)

        job_dir = _validate_job_path(job_id)
        if not job_dir:
            return job_state.build_public_task_state(j)
        meta_path = job_dir / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {"status": "running", "config": j.get("config", {})}

        meta_run_id = meta.get("run_id")
        if meta_run_id and run_id != meta_run_id:
            return job_state.build_public_task_state(j)

        terminal = meta.get("status")
        if terminal not in {"done", "partial", "error"}:
            # D-1 补修 B1：干活程序没留原因就停（关软件、被系统结束、自身崩溃），
            # 与启动扫描、按编号查状态走同一条规则：有成果判完成，否则记中断（可一键继续）。
            # 不判断退出码。退出码只进日志。
            _log.info("[worker:%s] 未留终态即退出（退出码 %s），按共用规则收敛", job_id, exit_code)
            if meta.get("status") not in ("running", "queued"):
                meta["status"] = "running"  # 记录缺状态字段 / 状态异常：干活程序刚退出，按运行中收敛
            terminal = _reconcile_job_status_on_disk(job_dir, meta)
            try:
                disk_status = json.loads(meta_path.read_text(encoding="utf-8")).get("status")
            except Exception:
                disk_status = None
            if disk_status != terminal:
                # 写盘失败：内存不得比磁盘先宣布结果，照旧报出原因（磁盘满 / 无权限）。
                terminal = "error"
                meta["status"] = "error"
                meta["error"] = (
                    "保存任务状态失败，请检查磁盘空间或文件权限后重试。"
                )

        j.update(_restored_from_meta(meta))
        for key in (*_RESULT_FIELDS, "delivery"):
            if key in meta:
                j[key] = meta[key]
        j["status"] = terminal
        j["process_alive"] = False
        if terminal == "error":
            _ensure_error_user_log(job_id)  # P384：错误终态确保 user.log 有一句人话
        if terminal in {"done", "partial", "error"}:
            j["progress"] = 100
        with _JOB_INDEX_LOCK:
            _update_hash_index(job_id, terminal)
        return job_state.build_public_task_state(j)


worker_manager.set_progress_callback(_sync_job_from_worker_message)
worker_manager.set_exit_callback(_sync_job_from_worker_exit)
worker_manager.set_terminal_callback(_handle_worker_terminal)


# ── Frontend static files ──

@app.api_route(
    "/api/{unmatched_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
def api_not_found(unmatched_path: str):
    return JSONResponse({"error": "not found"}, status_code=404)

FRONTEND = Path(__file__).parent.parent / "frontend"
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")


def _default_run_port(default: int = 8766) -> int:
    raw = _os.environ.get("VSUB_PORT")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _parse_run_args(argv=None):
    import argparse
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=_default_run_port())
    parser.add_argument("--no-reload", action="store_true")
    args, _unknown = parser.parse_known_args(argv)
    return args


def run(argv=None):
    args = _parse_run_args(argv)
    reload_flag = not args.no_reload
    print(f"Video Subber V3 -> http://localhost:{args.port}  (reload={'on' if reload_flag else 'off'})")
    uvicorn.run(
        "backend.main:app" if reload_flag else app,
        host=args.host, port=args.port,
        reload=reload_flag,
        reload_dirs=[str(Path(__file__).parent.parent / "frontend"), str(Path(__file__).parent)] if reload_flag else None,
    )


if __name__ == "__main__":
    run()
