"""V2 Web App — FastAPI backend."""
import sys, json, uuid, asyncio, time, hashlib
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .env_check import check_all, preload_resemblyzer
from .ws_manager import ws_manager
from .worker_manager import worker_manager

app = FastAPI(title="Video Subber V2")
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

# ── v2.04: Memory-resident hash index (built at startup, O(1) lookup forever) ──
# v2.09: key changed from file_hash → task_key (file_hash + config_hash)
GLOBAL_HASH_INDEX = {}   # task_key → {job_id, status, config, file_hash}
JOB_ID_TO_HASH = {}      # job_id → task_key  (reverse lookup for O(1) status updates)


def _config_hash(cfg: dict) -> str:
    relevant = {
        "model": cfg.get("model", ""),
        "translationMode": cfg.get("translationMode", ""),
        "translationPriority": cfg.get("translationPriority", ""),
        "contextAware": cfg.get("contextAware", False),
        "subtitles": cfg.get("subtitles", ""),
        "burn": cfg.get("burn", False),
        "style": cfg.get("style", {}),
        "savePath": cfg.get("savePath", ""),
    }
    raw = json.dumps(relevant, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _task_key(file_hash: str, cfg: dict) -> str:
    return f"{file_hash}_{_config_hash(cfg)}"


# === Tier definitions ===
# "free": Google + Baidu only. No LLM. Speed/Quality modes both work.
# "vip": user provides own LLM API keys. Context-aware translation unlocks.
# "svip": platform provides LLM keys. Full features.
TIER_FEATURES = {
    "free":  {"llm": False, "context_aware": False, "max_llm_keys": 0},
    "vip":   {"llm": True,  "context_aware": True,  "max_llm_keys": 10},
    "svip":  {"llm": True,  "context_aware": True,  "max_llm_keys": 0, "platform_keys": True},
}

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
         {"id": "gemini-2.0-flash",         "name": "2.0 Flash（推荐）"},
         {"id": "gemini-2.0-flash-lite",    "name": "2.0 Flash Lite（最快）"},
         {"id": "gemini-2.5-pro",           "name": "2.5 Pro（最强）"},
         {"id": "gemini-2.5-flash",         "name": "2.5 Flash（新·快）"},
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


# ═══════════════════════════════════════════════════════════
# Developer mode — activation code system.
# ═══════════════════════════════════════════════════════════

import os as _os
import uuid as _uuid
import json as _json
import urllib.request as _urllib

# ── Remote config base URL ────────────────────────────────
# Files are served from the GitHub repo.  The app fetches them via GitHub's
# raw URL (raw.githubusercontent.com).  Private repos need a token in the
# VSUB_GITHUB_TOKEN env var.  If the repo is public, no token needed.
# Files expected:
#   activation_codes.json → {"codes": ["CODE1", ...]}
#   models.json           → {"models": [...]}
#   platform_keys.json    → {"keys": [...]}
_REMOTE_BASE = _os.environ.get(
    "VSUB_REMOTE_CONFIG_URL",
    "https://raw.githubusercontent.com/andy-sherlock/video-subber/main",
)
_GITHUB_TOKEN = _os.environ.get("VSUB_GITHUB_TOKEN", "")


def _fetch_remote_json(filename: str) -> dict | None:
    """Fetch a JSON file from the remote config URL. Returns parsed dict or None."""
    if not _REMOTE_BASE:
        return None
    try:
        url = f"{_REMOTE_BASE}/{filename}"
        headers = {"User-Agent": "VideoSubber"}
        if _GITHUB_TOKEN:
            headers["Authorization"] = f"token {_GITHUB_TOKEN}"
        req = _urllib.Request(url, headers=headers)
        resp = _urllib.urlopen(req, timeout=5)
        return _json.loads(resp.read().decode())
    except Exception:
        return None


# ── Activation codes ──────────────────────────────────────

# Built-in developer codes — always valid, no env var needed.
_BUILTIN_CODES: set[str] = {"ANDY", "MENG"}

# Additional codes from env var (comma-separated), merged at runtime.
_raw_dev = _os.environ.get("VSUB_DEV_CODES", "")
_LOCAL_CODES: set[str] = set(
    c.strip().upper() for c in _raw_dev.split(",") if c.strip()
) if _raw_dev else set()

_ACTIVATION_FILE = Path.home() / ".video-subber" / "activation.json"
_ACTIVATION_CACHE = Path.home() / ".video-subber" / "activation_cache.json"

_dev_mode_active = False


def _all_valid_codes() -> set[str]:
    """Merge built-in + local + cached remote codes."""
    codes = set(_BUILTIN_CODES) | set(_LOCAL_CODES)
    if _ACTIVATION_CACHE.exists():
        try:
            cached = _json.loads(_ACTIVATION_CACHE.read_text(encoding="utf-8"))
            age_h = (time.time() - cached.get("fetched_at", 0)) / 3600
            if age_h < 24:
                codes.update(cached.get("codes", []))
        except Exception:
            pass
    return codes


def _fetch_remote_codes():
    """Pull latest activation codes from remote. Non-blocking, fail-safe."""
    data = _fetch_remote_json("activation_codes.json")
    if not data:
        return
    remote_codes = data.get("codes", [])
    if remote_codes:
        _ACTIVATION_CACHE.write_text(
            _json.dumps({"codes": remote_codes, "fetched_at": time.time()}, ensure_ascii=False),
            encoding="utf-8",
        )
        # 远程码写入缓存文件即可。_all_valid_codes() 会带 24h TTL 读取。
        # 不写入 _LOCAL_CODES，确保吊销的码在 24h 后自动失效。


# ── Remote model list ─────────────────────────────────────

_MODELS_CACHE = Path.home() / ".video-subber" / "models_cache.json"


def _get_merged_models() -> list[dict]:
    """Merge hardcoded model list with cached remote updates.
    Remote entries with the same platform id REPLACE the local entry entirely.
    """
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
    """Pull latest model definitions from remote. Non-blocking, fail-safe."""
    data = _fetch_remote_json("models.json")
    if not data:
        return
    models = data.get("models", [])
    if models:
        _MODELS_CACHE.write_text(
            _json.dumps({"models": models, "fetched_at": time.time()}, ensure_ascii=False),
            encoding="utf-8",
        )


# ── Platform keys (SVIP tier) ──────────────────────────────

_PLATFORM_KEYS_CACHE: list[dict] = []

def _fetch_platform_keys():
    """从远程拉取平台 API Key。失败则使用 72 小时本地缓存兜底。
    优先级：环境变量 VSUB_PLATFORM_KEYS > 远程 GitHub > 本地缓存。
    """
    global _PLATFORM_KEYS_CACHE
    # 最高优先级：环境变量（本地开发/测试用）
    env_keys_raw = _os.environ.get("VSUB_PLATFORM_KEYS", "")
    if env_keys_raw:
        try:
            keys = _json.loads(env_keys_raw)
            if isinstance(keys, list) and keys:
                _PLATFORM_KEYS_CACHE = keys
                return
        except Exception:
            pass
    data = _fetch_remote_json("platform_keys.json")
    if data:
        keys = data.get("keys", [])
        if keys:
            _PLATFORM_KEYS_CACHE = keys
            # 写入本地缓存，供下次离线启动使用
            cache_path = Path.home() / ".video-subber" / "platform_keys_cache.json"
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                _json.dumps({"keys": keys, "fetched_at": time.time()}, ensure_ascii=False),
                encoding="utf-8",
            )
            return
    # 拉取失败 → 尝试从本地缓存恢复
    cache_path = Path.home() / ".video-subber" / "platform_keys_cache.json"
    if cache_path.exists():
        try:
            cached = _json.loads(cache_path.read_text(encoding="utf-8"))
            age_h = (time.time() - cached.get("fetched_at", 0)) / 3600
            if age_h < 72:
                _PLATFORM_KEYS_CACHE = cached.get("keys", [])
        except Exception:
            pass

def get_platform_keys() -> list[dict]:
    """供 orchestrator 调用，返回当前有效的平台 Key 列表。"""
    return list(_PLATFORM_KEYS_CACHE)


def _machine_fingerprint() -> str:
    """A hash that identifies this computer."""
    parts = [
        str(_uuid.getnode()),
        _os.name,
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _load_activation() -> str | None:
    """Check if this machine has a valid activation. Returns the code or None.

    Once activated (file exists + fingerprint matches), trust it directly.
    No need to re-validate the code against any list on every startup.
    """
    if not _ACTIVATION_FILE.exists():
        return None
    try:
        data = _json.loads(_ACTIVATION_FILE.read_text(encoding="utf-8"))
        code = data.get("code", "")
        fp = data.get("fingerprint", "")
        if fp == _machine_fingerprint() and code:
            return code
    except Exception:
        pass
    return None


@app.on_event("startup")
async def startup():
    global _dev_mode_active

    # ── 0a. 后台拉取远程配置（激活码 + 模型列表 + 平台 Key），不阻塞启动 ──
    import threading
    threading.Thread(target=_fetch_remote_codes, daemon=True).start()
    threading.Thread(target=_fetch_remote_models, daemon=True).start()
    threading.Thread(target=_fetch_platform_keys, daemon=True).start()

    if _load_activation():
        _dev_mode_active = True

    # ── 0b. P2: 清理 V2.04 旧版单日志残留（升级到 V2.05 后执行一次）──
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
                tk = meta.get("task_key") or _task_key(fh, cfg)
                GLOBAL_HASH_INDEX[tk] = {
                    "job_id": jid, "status": status,
                    "config": cfg, "file_hash": fh,
                }
                JOB_ID_TO_HASH[jid] = tk
            except Exception:
                pass

    # ── 2. Preload resemblyzer in background ──
    threading.Thread(target=preload_resemblyzer, daemon=True).start()

    # ── 3. Store main event loop for worker_manager reader threads ──
    worker_manager.set_main_loop(asyncio.get_running_loop())

    # ── 4. P3: 写入 .version 标记文件（V3 升级时读取，触发迁移逻辑）──
    version_file = Path.home() / ".video-subber" / ".version"
    try:
        current = json.loads(version_file.read_text(encoding="utf-8")) if version_file.exists() else {}
    except (json.JSONDecodeError, IOError):
        current = {}

    if current.get("version") != "2.12":
        import datetime
        version_data = {
            "version": "2.12",
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "last_compatible_version": "2.00",
        }
        version_file.write_text(json.dumps(version_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 5. Cache auto-cleanup: delete intermediate files older than 30 days ──
    # Final outputs (output.srt, burned *.mp4) are never auto-deleted.
    threading.Thread(target=_cleanup_old_cache, daemon=True).start()


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


# ── Frontend static files ──
@app.post("/api/dev/activate")
async def dev_activate(payload: dict):
    """Activate developer mode using a code. Binds to this machine."""
    global _dev_mode_active
    code = payload.get("code", "").strip().upper()
    if code not in _all_valid_codes():
        return JSONResponse({"error": "激活码无效"}, status_code=403)

    fp = _machine_fingerprint()
    _ACTIVATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    _ACTIVATION_FILE.write_text(
        _json.dumps({"code": code, "fingerprint": fp}, ensure_ascii=False),
        encoding="utf-8",
    )
    _dev_mode_active = True
    return {"ok": True}


@app.post("/api/dev/deactivate")
def dev_deactivate():
    """Deactivate developer mode on this machine."""
    global _dev_mode_active
    _dev_mode_active = False
    _ACTIVATION_FILE.unlink(missing_ok=True)
    return {"ok": True}


@app.get("/api/dev-status")
def dev_status():
    return {
        "dev_mode": _dev_mode_active,
        "has_platform_keys": bool(_PLATFORM_KEYS_CACHE),
    }


def _effective_tier(requested: str) -> str:
    """Resolve the real tier for this request.

    Dev mode: trust the requested tier (for testing).
    Normal mode: every user is 'free' + can bring their own LLM keys.
    """
    if _dev_mode_active:
        tier = requested if requested in TIER_FEATURES else "free"
        # In dev mode with platform keys set, allow svip
        return tier

    # Production: everyone is free. LLM access is gated by whether the
    # user has configured their own API keys — not by tier.
    return "free"


@app.get("/api/health")
def health():
    return {"ok": True, "version": _detect_version()}


def _detect_version() -> str:
    """Auto-detect version from git tag → dir scan → pyproject.toml → fallback."""
    import subprocess, re, glob as _glob

    # 1) Git tag: git describe --tags --abbrev=0
    try:
        r = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            capture_output=True, text=True, timeout=3,
            cwd=str(Path(__file__).parent.parent),
        )
        tag = r.stdout.strip()
        if tag and re.match(r"v?\d+\.\d+", tag):
            return tag.lstrip("v")
    except Exception:
        pass

    # 2) Scan version dirs: find highest v2.XX/ in project root
    try:
        root = Path(__file__).parent.parent
        best = (0, 0)
        for d in root.glob("v[0-9]*.[0-9]*"):
            m = re.match(r"v(\d+)\.(\d+)", d.name)
            if m:
                v = (int(m.group(1)), int(m.group(2)))
                if v > best:
                    best = v
        if best != (0, 0):
            return f"{best[0]}.{best[1]:02d}"
    except Exception:
        pass

    # 3) pyproject.toml
    try:
        ppt = Path(__file__).parent.parent / "pyproject.toml"
        if ppt.exists():
            import tomllib
            data = tomllib.loads(ppt.read_text())
            v = data.get("project", {}).get("version")
            if v:
                return v
    except Exception:
        pass

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
def get_backends(tier: str = "free"):
    """Return ENABLED backends + current tier info.

    Dev mode: ?tier=svip works (for testing).
    Production: tier is always 'free'. LLM access is gated by user's own API keys.
    """
    from .translation.google import GoogleBackend
    from .translation.baidu import BaiduBackend
    _name_map = {"google": GoogleBackend(), "baidu": BaiduBackend()}
    enabled_free = [
        {"id": key, "name": b.name, "free": True}
        for key, b in _name_map.items() if b.enabled
    ]
    current = _effective_tier(tier)
    return {
        "free": enabled_free,
        "llm": _get_merged_models(),
        "tiers": TIER_FEATURES,
        "current_tier": current,
        "dev_mode": _dev_mode_active,
    }


@app.get("/api/config")
def get_config():
    """Return user config from ~/.video-subber/config.json. API keys are masked."""
    from .config_manager import load_config
    from .key_manager import mask_key
    cfg = load_config()
    for k in cfg.get("apiKeys", []):
        if "key" in k:
            k["key"] = mask_key(k["key"])
    return cfg


@app.put("/api/config")
async def save_config(payload: dict):
    """Save user config to ~/.video-subber/config.json"""
    from .config_manager import save_config
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
            meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            _update_hash_index(job_id, status)
        except Exception:
            pass


def _cleanup_hash_index(job_id: str):
    """Remove job from both memory indexes (called on cancel)."""
    tk = JOB_ID_TO_HASH.pop(job_id, None)
    if tk:
        GLOBAL_HASH_INDEX.pop(tk, None)


# ── POST /api/jobs — the core: upload/path → hash → dedup → create or restore ──


@app.post("/api/jobs")
async def create_job(request: Request):
    import shutil as _shutil, os as _os_module

    content_type = request.headers.get("content-type", "")

    # ── 模式 1：本地路径（JSON body）──
    if content_type and "json" in content_type:
        body = await request.json()
        cfg = json.loads(body.get("config", "{}"))
        cfg["tier"] = _effective_tier(cfg.get("tier", "free"))
        local_path = cfg.pop("local_path", "")

        if not local_path or not _os_module.path.isfile(local_path):
            return JSONResponse(
                {"error": f"找不到文件：{local_path or '(空路径)'}"},
                status_code=400,
            )

        filename = Path(local_path).name
        cfg["video_filename"] = filename
        cfg["source_dir"] = str(Path(local_path).parent)

        jid = uuid.uuid4().hex[:10]
        job_dir = JOBS_DIR / jid
        job_dir.mkdir(parents=True)

        # 流式读取计算 SHA256（不复制文件，直接引用原始路径）
        hasher = hashlib.sha256()
        try:
            with open(local_path, "rb") as src:
                while True:
                    chunk = src.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    hasher.update(chunk)
            file_hash = hasher.hexdigest()
        except Exception:
            _shutil.rmtree(job_dir, ignore_errors=True)
            return JSONResponse({"error": "读取本地文件失败，请检查文件是否可访问"}, status_code=500)

        # 本地路径模式：直接使用原始文件路径，不复制到 jobs 目录
        video_path = local_path

    # ── 模式 2：文件上传（multipart/form-data）──
    else:
        form = await request.form()
        file = form.get("file")
        config_str = form.get("config", "{}")
        cfg = json.loads(config_str)
        cfg["tier"] = _effective_tier(cfg.get("tier", "free"))
        cfg["video_filename"] = file.filename if file else ""

        jid = uuid.uuid4().hex[:10]
        job_dir = JOBS_DIR / jid
        job_dir.mkdir(parents=True)
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

    # ── 以下为共享的去重 + 任务创建逻辑 ──
    # v2.09: config-aware dedup — same video + same settings = reuse, different settings = new task

    tk = _task_key(file_hash, cfg)
    existing = GLOBAL_HASH_INDEX.get(tk)

    if existing:
        old_jid = existing["job_id"]
        status = existing["status"]
        old_job_dir = JOBS_DIR / old_jid

        # Stale index entry — disk was cleared while server was running
        if not old_job_dir.is_dir():
            _cleanup_hash_index(old_jid)
        elif status in ("interrupted", "error"):
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
        elif status == "done" and not cfg.get("force"):
            _shutil.rmtree(job_dir, ignore_errors=True)
            return {"found": True, "job_id": old_jid, "status": "done",
                    "config": existing["config"]}
        elif status in ("queued", "running"):
            _shutil.rmtree(job_dir, ignore_errors=True)
            return {"found": True, "job_id": old_jid, "status": status,
                    "config": existing["config"]}
        else:
            _cleanup_hash_index(old_jid)

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


# ── Factory reset: wipe all state back to zero ──

@app.post("/api/reset")
def reset_all():
    """Clear all jobs, cache, and memory indexes. Dev mode only."""
    if not _dev_mode_active:
        return JSONResponse({"error": "forbidden — developer mode required"}, status_code=403)
    import shutil
    # Kill all running workers
    for jid in list(JOBS.keys()):
        worker_manager.cancel(jid)
    JOBS.clear()
    GLOBAL_HASH_INDEX.clear()
    JOB_ID_TO_HASH.clear()
    # Delete all job directories from disk
    if JOBS_DIR.exists():
        shutil.rmtree(JOBS_DIR, ignore_errors=True)
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "message": "All jobs, cache, and indexes cleared"}


# ── v2.05: Active jobs recovery (fallback when localStorage is empty) ──

@app.get("/api/active-jobs")
def get_active_jobs():
    """Return all incomplete jobs from memory index.
    Frontend calls this when localStorage is empty but disk may have
    unfinished work — e.g. user cleared browser data or switched browsers."""
    active = []
    for fh, entry in GLOBAL_HASH_INDEX.items():
        if entry["status"] in ("queued", "running", "interrupted"):
            jid = entry["job_id"]
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
    if not j:
        # Fallback: check disk for meta.json (backend restarted, JOBS dict empty)
        meta_path = JOBS_DIR / job_id / "meta.json"
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
    j["process_alive"] = worker_manager.is_alive(job_id)
    return j


@app.get("/api/jobs/{job_id}/download/video")
def download_video(job_id: str):
    j = JOBS.get(job_id)
    if j and j.get("result_video"):
        path = j["result_video"]
        if Path(path).exists():
            return FileResponse(path, filename=Path(path).name)
    job_dir = JOBS_DIR / job_id
    if job_dir.exists():
        for f in job_dir.glob("*_zh.mp4"):
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
    if job_dir.exists():
        for f in job_dir.glob("*.srt"):
            return FileResponse(str(f), filename=f.name)
    return JSONResponse({"error": "not found"}, status_code=404)


@app.delete("/api/jobs/{job_id}")
def cancel_job(job_id: str):
    import time, shutil

    worker_manager.cancel(job_id)

    time.sleep(0.1)
    log_dir = LOG_DIR / job_id
    if log_dir.exists():
        shutil.rmtree(log_dir, ignore_errors=True)

    j = JOBS.pop(job_id, None)
    if j:
        j["status"] = "cancelled"
    _cleanup_hash_index(job_id)

    # 仅删除 input 视频，保留 cache/ 下的中间产物（audio.wav, transcript.json 等）。
    # 用户重新上传同一视频时可复用缓存，不需要从零跑流水线。
    job_dir = JOBS_DIR / job_id
    if job_dir.exists():
        for input_file in job_dir.glob("input.*"):
            input_file.unlink(missing_ok=True)

    return {"ok": True}


# ── LLM fallback decision ──

@app.post("/api/jobs/{job_id}/llm-fallback")
def llm_fallback_decision(job_id: str, payload: dict):
    """Worker polls this file after emitting 'llm_failed' message."""
    decision = payload.get("decision", "keep_failed")
    decision_file = JOBS_DIR / job_id / "llm_fallback.json"
    decision_file.write_text(json.dumps({"decision": decision}, ensure_ascii=False))
    return {"ok": True}


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

_original_send = ws_manager.send


async def _tracking_send(job_id: str, message: dict):
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
    await _original_send(job_id, message)


ws_manager.send = _tracking_send  # type: ignore[method-assign]


# ── Frontend static files ──

FRONTEND = Path(__file__).parent.parent / "frontend"
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")


def run():
    print("Video Subber V2 -> http://localhost:8766")
    uvicorn.run(app, host="127.0.0.1", port=8766)


if __name__ == "__main__":
    run()
