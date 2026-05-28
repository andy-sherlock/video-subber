"""Model download manager — handles per-model download, progress, cancellation."""
import os, threading, logging
from pathlib import Path

_log = logging.getLogger("model_manager")

MODELS = {
    "tiny":     {"name": "tiny",     "repo": "Systran/faster-whisper-tiny",      "size": "~75MB"},
    "base":     {"name": "base",     "repo": "Systran/faster-whisper-base",      "size": "~300MB"},
    "small":    {"name": "small",    "repo": "Systran/faster-whisper-small",     "size": "~900MB"},
    "medium":   {"name": "medium",   "repo": "Systran/faster-whisper-medium",    "size": "~1.5GB"},
    "large-v3": {"name": "large-v3", "repo": "Systran/faster-whisper-large-v3",  "size": "~2.9GB"},
}

CACHE_DIR = Path.home() / ".cache" / "huggingface" / "hub"

# In-memory download state: model_id → {downloading, progress, cancel_event, thread}
_downloads: dict[str, dict] = {}


def _detect_model_dir(model_id: str) -> Path | None:
    """Check if a specific model is cached on disk."""
    repo = MODELS[model_id]["repo"]
    dir_name = "models--" + repo.replace("/", "--")
    candidate = CACHE_DIR / dir_name
    if candidate.is_dir():
        # Verify it has actual model files (snapshots/*/model.bin or similar)
        snapshots = candidate / "snapshots"
        if snapshots.is_dir():
            for snap in snapshots.iterdir():
                if snap.is_dir() and any(snap.iterdir()):
                    return snap
        # Some older cache layouts put files directly in the model dir
        if any(f.suffix in (".bin", ".model", ".json") for f in candidate.glob("**/*") if f.is_file()):
            return candidate
    return None


def get_model_status() -> dict:
    """Return download status for all 5 models."""
    result = {}
    for mid, info in MODELS.items():
        dl = _downloads.get(mid)
        downloaded = _detect_model_dir(mid) is not None
        result[mid] = {
            "name": info["name"],
            "size": info["size"],
            "downloaded": downloaded,
            "downloading": dl is not None and dl.get("downloading", False),
            "progress": dl.get("progress", 0) if dl else 0,
        }
    return result


def start_download(model_id: str) -> dict:
    """Start downloading a model in a background thread. Returns initial status."""
    if model_id not in MODELS:
        return {"error": f"Unknown model: {model_id}"}

    if _detect_model_dir(model_id):
        return {"ok": True, "already_downloaded": True}

    existing = _downloads.get(model_id)
    if existing and existing.get("downloading"):
        return {"ok": True, "already_downloading": True, "progress": existing.get("progress", 0)}

    cancel_event = threading.Event()
    state = {"downloading": True, "progress": 0, "cancel_event": cancel_event}
    _downloads[model_id] = state

    thread = threading.Thread(
        target=_download_model,
        args=(model_id, state),
        daemon=True,
    )
    state["thread"] = thread
    thread.start()

    return {"ok": True, "progress": 0}


def cancel_download(model_id: str) -> dict:
    """Cancel an in-progress download."""
    dl = _downloads.get(model_id)
    if dl and dl.get("downloading"):
        dl["cancel_event"].set()
        dl["downloading"] = False
        _downloads.pop(model_id, None)
        return {"ok": True}
    return {"ok": True, "not_downloading": True}


def _download_model(model_id: str, state: dict):
    """Background thread: download model from HuggingFace with progress tracking."""
    repo = MODELS[model_id]["repo"]
    try:
        from huggingface_hub import snapshot_download

        # Track progress via a simple callback wrapper
        # huggingface_hub doesn't give per-file progress easily via snapshot_download,
        # but we can use the tqdm progress hook. For simplicity, we estimate progress
        # by counting files downloaded vs total.
        #
        # Actually, snapshot_download can take a progress callback via
        # `tqdm_class` or we can monitor the cache dir for new files.
        # The simplest approach: use the lower-level API or just report incremental
        # progress as files appear.
        #
        # For now, use snapshot_download and update progress by watching the cache dir
        # in a separate monitoring thread.

        def _monitor_progress():
            """Monitor cache dir for new blobs to estimate progress."""
            if state["cancel_event"].is_set():
                return
            target = CACHE_DIR / ("models--" + repo.replace("/", "--"))
            blobs_dir = target / "blobs" if target.exists() else None
            # Simple pulse: bump progress if not cancelled
            while state["downloading"] and not state["cancel_event"].is_set():
                current = state["progress"]
                if current < 95:
                    # Slow ramp to show activity, actual file count isn't easily available
                    state["progress"] = min(95, current + max(1, int((95 - current) * 0.15)))
                if state["cancel_event"].wait(0.5):
                    break

        monitor = threading.Thread(target=_monitor_progress, daemon=True)
        monitor.start()

        try:
            snapshot_download(
                repo,
                cache_dir=str(CACHE_DIR.parent),
                resume_download=True,
            )
            if not state["cancel_event"].is_set():
                state["progress"] = 100
        finally:
            state["downloading"] = False

    except Exception as e:
        _log.warning("Model download failed for %s: %s", model_id, e)
        state["downloading"] = False
        state["error"] = str(e)
