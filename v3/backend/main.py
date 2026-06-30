"""V3 Web App — FastAPI backend."""
import sys, json, uuid, asyncio, time, hashlib, threading, logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .env_check import check_all, preload_resemblyzer, health_summary_line, missing_checks
from .ws_manager import ws_manager
from .worker_manager import worker_manager

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
        "translationMode": cfg.get("translationMode", ""),
        "translationPriority": cfg.get("translationPriority", ""),
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
    if JOBS_DIR.exists():
        for job_dir in JOBS_DIR.iterdir():
            if not job_dir.is_dir():
                continue
            meta_path = job_dir / "meta.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                fh = meta.get("file_hash")
                if not fh:
                    continue
                jid = job_dir.name
                status = meta.get("status", "unknown")
                # Reconcile status after restart
                if status in ("running", "queued"):
                    if (job_dir / "output.srt").exists():
                        status = "done"
                    else:
                        status = "interrupted"
                elif (job_dir / "output.srt").exists() and status != "done":
                    status = "done"

                cfg = meta.get("config", {})
                # Always recompute with current _config_hash so that hash function
                # changes (e.g. new fields added) don't break dedup for old jobs.
                tk = _task_key(fh, cfg)
                GLOBAL_HASH_INDEX[tk] = {
                    "job_id": jid, "status": status,
                    "config": cfg, "file_hash": fh,
                }
                JOB_ID_TO_HASH[jid] = tk
            except Exception:
                pass

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


@app.on_event("shutdown")
async def shutdown():
    worker_manager.shutdown_all()


def _cleanup_old_cache():
    """Delete intermediate cache files not accessed for 30+ days. Never touches final outputs."""
    import time as _time
    _INTERMEDIATE = {"audio.wav", "transcript.json", "translated.json"}
    _CUTOFF = 30 * 24 * 3600  # 30 days in seconds
    now = _time.time()
    try:
        for job_dir in JOBS_DIR.iterdir():
            if not job_dir.is_dir():
                continue
            # Walk cache subdirectories
            cache_root = job_dir / "cache"
            if cache_root.is_dir():
                for hash_dir in cache_root.iterdir():
                    if not hash_dir.is_dir():
                        continue
                    for f in hash_dir.iterdir():
                        if not f.is_file():
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
        data["whisper"] = {"installed": False, "models": []}
    return data


# ── Model download ──

@app.get("/api/models/status")
def model_status():
    """Return download status for all 5 models."""
    from .model_manager import get_model_status
    return get_model_status()


@app.post("/api/models/download/{model_id}")
def model_download(model_id: str):
    """Start downloading a model."""
    from .model_manager import start_download
    return start_download(model_id)


@app.delete("/api/models/download/{model_id}")
def model_cancel(model_id: str):
    """Cancel an in-progress model download."""
    from .model_manager import cancel_download
    return cancel_download(model_id)


@app.get("/api/backends")
def get_backends():
    """Return enabled free backends + available LLM platforms."""
    from .translation.google import GoogleBackend
    from .translation.bing import BingBackend
    from .translation.youdao import YoudaoBackend
    _name_map = {
        "google": GoogleBackend(),
        "bing": BingBackend(),
        "youdao": YoudaoBackend(),
    }
    enabled_free = [
        {"id": key, "name": b.name, "free": True}
        for key, b in _name_map.items() if b.enabled
    ]
    return {
        "free": enabled_free,
        "llm": _get_merged_models(),
    }


_LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
_FREE_MODEL_CACHE: dict[str, float] = {}  # {model_id: fetched_at_timestamp}
from backend.model_cache import _MODEL_CONTEXT_CACHE  # P186: shared module, no circular dependency
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
    """Return user config from ~/.video-subber/config.json.
    translationPriority is always merged with real available backends before returning.
    """
    from .config_manager import load_config
    from .translation.google import GoogleBackend
    from .translation.bing import BingBackend
    from .translation.youdao import YoudaoBackend
    cfg = load_config()
    available = [k for k, v in {"google": GoogleBackend(), "bing": BingBackend(), "youdao": YoudaoBackend()}.items() if v.enabled]
    saved = cfg.get("translationPriority", [])
    merged = [b for b in saved if b in available]
    for b in available:
        if b not in merged:
            merged.append(b)
    cfg["translationPriority"] = merged
    return cfg



@app.put("/api/config")
async def save_config(payload: dict):
    """Save user config to ~/.video-subber/config.json"""
    from .config_manager import save_config
    from .translation.google import GoogleBackend
    from .translation.bing import BingBackend
    from .translation.youdao import YoudaoBackend
    available = [k for k, v in {"google": GoogleBackend(), "bing": BingBackend(), "youdao": YoudaoBackend()}.items() if v.enabled]
    saved = payload.get("translationPriority", [])
    merged = [b for b in saved if b in available]
    for b in available:
        if b not in merged:
            merged.append(b)
    payload["translationPriority"] = merged
    save_config(payload)
    return {"ok": True}


# ── v2.04: Memory index helpers ──

def _update_hash_index(job_id: str, status: str):
    """O(1) status sync to GLOBAL_HASH_INDEX via JOB_ID_TO_HASH reverse lookup."""
    tk = JOB_ID_TO_HASH.get(job_id)
    if tk and tk in GLOBAL_HASH_INDEX:
        GLOBAL_HASH_INDEX[tk]["status"] = status


def _persist_job_status(job_id: str, status: str):
    """Write job status to disk AND sync memory index."""
    meta_path = JOBS_DIR / job_id / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["status"] = status
            if status in ("queued", "running", "done"):
                meta.pop("error", None)
            meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            _update_hash_index(job_id, status)
        except Exception:
            pass


def _cleanup_hash_index(job_id: str):
    """Remove job from both memory indexes (called on cancel)."""
    tk = JOB_ID_TO_HASH.pop(job_id, None)
    if tk:
        GLOBAL_HASH_INDEX.pop(tk, None)


def _is_recoverable_active_job(job_id: str, entry: dict) -> bool:
    """Return True when an active-job entry can be resumed from disk."""
    status = entry.get("status")
    if status in ("queued", "running"):
        return True
    if status != "interrupted":
        return False

    job_dir = _validate_job_path(job_id)
    if not job_dir or not job_dir.is_dir():
        return False
    if any(job_dir.glob("input.*")):
        return True

    source_url = (entry.get("config") or {}).get("source_url", "")
    return bool(source_url and _verify_safe_url(source_url))


# ── POST /api/jobs — the core: upload/path → hash → dedup → create or restore ──


def _normalize_job_config(cfg):
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    cfg.pop("tier", None)
    cfg.pop("local_path", None)
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
    url_fname = source_url.split("/")[-1].split("?")[0]
    cfg["video_filename"] = url_fname or "download.mp4"

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

    filename = Path(local_path).name
    cfg["video_filename"] = filename
    cfg["source_dir"] = str(Path(local_path).parent)

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


async def _prepare_upload_job(request: Request):
    import shutil as _shutil

    form = await request.form()
    file = form.get("file")
    config_str = form.get("config", "{}")
    cfg = json.loads(config_str)
    cfg.pop("tier", None)
    cfg["video_filename"] = file.filename if file else ""

    jid, job_dir = _new_job_dir()
    video_path = job_dir / f"input{Path(file.filename).suffix}" if file else job_dir / "input.mp4"

    if not file:
        return JSONResponse({"error": "请选择视频文件或输入本地路径"}, status_code=400)

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


def _handle_existing_job(existing: dict, job_dir: Path, video_path, cfg: dict):
    import shutil as _shutil

    old_jid = existing["job_id"]
    status = existing["status"]
    old_job_dir = JOBS_DIR / old_jid

    # Stale index entry — disk was cleared while server was running
    if not old_job_dir.is_dir():
        _cleanup_hash_index(old_jid)
        return None
    if status in ("interrupted", "error"):
        _shutil.rmtree(job_dir, ignore_errors=True)
        job_dir = old_job_dir
        try:
            video_path = next(job_dir.glob("input.*"))
        except StopIteration:
            pass

        JOBS[old_jid] = {
            "id": old_jid, "status": "running", "progress": 0,
            "stage": "", "message": "服务重启，正在恢复…",
            "result_video": None, "result_srt": None, "error": None,
            "config": existing["config"],
            "last_progress": time.time(),
        }
        existing["status"] = "running"
        _persist_job_status(old_jid, "running")
        worker_manager.start(old_jid, job_dir, str(video_path), existing["config"])
        return {"found": True, "job_id": old_jid, "status": "running",
                "config": existing["config"]}
    if status == "done" and not cfg.get("force"):
        _shutil.rmtree(job_dir, ignore_errors=True)
        return {"found": True, "job_id": old_jid, "status": "done",
                "config": existing["config"]}
    if status in ("queued", "running"):
        _shutil.rmtree(job_dir, ignore_errors=True)
        return {"found": True, "job_id": old_jid, "status": status,
                "config": existing["config"]}

    _cleanup_hash_index(old_jid)
    return None


def _start_new_job(jid: str, job_dir: Path, video_path, cfg: dict, file_hash: str, tk: str):
    meta = {"file_hash": file_hash, "task_key": tk, "config": cfg,
            "video_filename": cfg.get("video_filename", ""), "status": "queued"}
    (job_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    GLOBAL_HASH_INDEX[tk] = {"job_id": jid, "status": "queued", "config": cfg, "file_hash": file_hash}
    JOB_ID_TO_HASH[jid] = tk

    JOBS[jid] = {
        "id": jid, "status": "queued", "progress": 0,
        "stage": "", "message": "", "result_video": None,
        "result_srt": None, "error": None,
        "config": cfg,
        "video_filename": cfg.get("video_filename", ""),
        "last_progress": time.time(),
    }
    worker_manager.start(jid, job_dir, str(video_path), cfg)
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
            return response

    return _start_new_job(jid, job_dir, video_path, cfg, file_hash, tk)


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
        active.append({
            "job_id": jid,
            "status": entry["status"],
            "config": entry.get("config", {}),
            "progress": j["progress"] if j else 0,
            "message": j["message"] if j else "",
            "stage": j["stage"] if j else "",
        })
    return {"jobs": active}


# ── Job status / download / cancel ──

@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    j = JOBS.get(job_id)
    safe_dir = _validate_job_path(job_id)
    if not safe_dir:
        return JSONResponse({"error": "无效的任务编号"}, status_code=400)
    if j:
        meta_path = safe_dir / "meta.json"
        if not meta_path.exists() and not worker_manager.is_alive(job_id):
            JOBS.pop(job_id, None)
            _cleanup_hash_index(job_id)
            return JSONResponse({"error": "未找到该任务"}, status_code=404)
        j["process_alive"] = worker_manager.is_alive(job_id)
        return j
    if not j:
        meta_path = safe_dir / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                # v2.05: priority to GLOBAL_HASH_INDEX (corrected at startup, e.g. "interrupted")
                tk = JOB_ID_TO_HASH.get(job_id)
                status = GLOBAL_HASH_INDEX[tk]["status"] if (tk and tk in GLOBAL_HASH_INDEX) else meta.get("status", "unknown")
                return {
                    "id": job_id, "status": status,
                    "progress": 0, "stage": "", "message": "",
                    "result_video": None, "result_srt": None, "error": None,
                    "config": meta.get("config", {}),
                    "video_filename": meta.get("video_filename", ""),
                    "last_progress": 0,
                    "process_alive": worker_manager.is_alive(job_id),
                }
            except Exception:
                pass
        return JSONResponse({"error": "未找到该任务"}, status_code=404)


@app.get("/api/jobs/{job_id}/download/video")
def download_video(job_id: str):
    j = JOBS.get(job_id)
    if j and j.get("result_video"):
        path = j["result_video"]
        if Path(path).exists():
            return FileResponse(path, filename=Path(path).name)
    job_dir = JOBS_DIR / job_id
    # P189: prevent path traversal via .resolve() guard
    if job_dir.resolve() != job_dir or not str(job_dir.resolve()).startswith(str(JOBS_DIR.resolve())):
        return JSONResponse({"error": "无效的任务编号"}, status_code=400)
    if job_dir.exists():
        for pattern in ["*_zh.mp4", "*_zh_sub.*"]:
            for f in job_dir.glob(pattern):
                return FileResponse(str(f), filename=f.name)
    return JSONResponse({"error": "未找到下载文件"}, status_code=404)


@app.get("/api/jobs/{job_id}/download/srt")
def download_srt(job_id: str):
    j = JOBS.get(job_id)
    if j and j.get("result_srt"):
        path = j["result_srt"]
        if Path(path).exists():
            return FileResponse(path, filename=Path(path).name)
    job_dir = JOBS_DIR / job_id
    # P189: prevent path traversal via .resolve() guard
    if job_dir.resolve() != job_dir or not str(job_dir.resolve()).startswith(str(JOBS_DIR.resolve())):
        return JSONResponse({"error": "无效的任务编号"}, status_code=400)
    if job_dir.exists():
        for f in job_dir.glob("*.srt"):
            return FileResponse(str(f), filename=f.name)
    return JSONResponse({"error": "not found"}, status_code=404)


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
        for input_file in job_dir.glob("input.*"):
            input_file.unlink(missing_ok=True)
        meta_path = job_dir / "meta.json"
        meta_path.unlink(missing_ok=True)

    return {"ok": True}


# ── LLM fallback decision ──

@app.post("/api/jobs/{job_id}/llm-fallback")
def llm_fallback_decision(job_id: str, payload: dict):
    """Worker polls this file after emitting 'llm_failed' message."""
    safe_dir = _validate_job_path(job_id)
    if not safe_dir:
        return JSONResponse({"error": "无效的任务编号"}, status_code=400)
    decision = payload.get("decision", "keep_failed")
    decision_file = safe_dir / "llm_fallback.json"
    decision_file.write_text(json.dumps({"decision": decision}, ensure_ascii=False))
    return {"ok": True}


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
    path = download_video(url, dl_dir)
    if not path:
        return JSONResponse({"error": "下载失败，请检查链接是否有效"}, status_code=500)
    return {"ok": True, "path": path, "filename": Path(path).name}

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
    if j:
        j["last_progress"] = time.time()
        if message.get("type") == "progress":
            j["progress"] = message.get("percent", j["progress"])
            j["stage"] = message.get("stage", "")
            j["message"] = message.get("message", "")
            if j["status"] == "queued":
                j["status"] = "running"
            _persist_job_status(job_id, j["status"])
        elif message.get("type") == "token_usage":
            j["token_usage"] = message.get("usage")
        elif message.get("type") == "done":
            j["status"] = "done"
            j["progress"] = 100
            j["result_video"] = message.get("result_video")
            j["result_srt"] = message.get("result_srt")
            j["failed_segments"] = message.get("failed_segments")
            j["segment_count"] = message.get("segment_count")
            j["success_count"] = message.get("success_count")
            _persist_job_status(job_id, "done")
        elif message.get("type") == "error":
            j["status"] = "error"
            j["error"] = message.get("message")
            _persist_job_status(job_id, "error")


worker_manager.set_progress_callback(_sync_job_from_worker_message)


# ── Frontend static files ──

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
