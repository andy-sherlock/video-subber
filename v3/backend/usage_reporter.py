"""Usage reporter — collects in-memory, POSTs to server. Token info never shown to user.

V3 server URL: placeholder — fill in when V3 cloud server goes live.
"""
import json, time, urllib.request, logging
from pathlib import Path

_log = logging.getLogger("usage_reporter")

# ── V3 SERVER PLACEHOLDER ──────────────────────────────────
# TODO(V3): Replace with actual server URL when cloud goes live.
# Example: "https://api.video-subber.example.com/api/usage"
SERVER_URL = None  # Set to a string URL when V3 server is ready
# ────────────────────────────────────────────────────────────

_pending_usage: list[dict] = []
_FALLBACK_PATH = Path.home() / ".video-subber" / "pending_usage.json"


def record_usage(job_id: str, platform: str, model: str,
                 input_tokens: int, output_tokens: int, duration_ms: int):
    """Record one LLM call's token usage. Called from orchestrator."""
    if SERVER_URL is None:
        _log.debug("Token usage not reported (V3 server not configured): "
                   f"{platform}/{model} in={input_tokens} out={output_tokens}")
        return

    _pending_usage.append({
        "job_id": job_id,
        "platform": platform,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "duration_ms": duration_ms,
        "timestamp": int(time.time()),
    })


def flush_usage():
    """POST all pending usage to server. Called after job completes."""
    if SERVER_URL is None:
        _log.debug("Skipping flush_usage (V3 server not configured)")
        return

    if not _pending_usage:
        return

    payload = json.dumps(_pending_usage, ensure_ascii=False).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                SERVER_URL, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
            count = len(_pending_usage)
            _pending_usage.clear()
            _log.info("Token usage reported: %d records", count)
            return
        except Exception as e:
            _log.warning("Token usage report attempt %d failed: %s", attempt + 1, e)
            time.sleep(2 ** attempt)

    # All retries failed — write to fallback file
    _FALLBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = (
        json.loads(_FALLBACK_PATH.read_text(encoding="utf-8"))
        if _FALLBACK_PATH.exists() else []
    )
    existing.extend(_pending_usage)
    _FALLBACK_PATH.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    _pending_usage.clear()
    _log.warning("Token usage saved to fallback file for later retry")


def replay_fallback():
    """On startup, try to flush any previously-failed usage records."""
    if SERVER_URL is None:
        return

    if _FALLBACK_PATH.exists():
        try:
            data = json.loads(_FALLBACK_PATH.read_text(encoding="utf-8"))
            if data:
                _pending_usage.extend(data)
                _FALLBACK_PATH.unlink()
                flush_usage()
        except Exception:
            pass
