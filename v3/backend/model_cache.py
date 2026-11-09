"""Shared model metadata cache — avoids circular dependency between main.py and translation layer.

P186: _MODEL_CONTEXT_CACHE was previously defined in main.py and imported via
lazy import from translation/base.py. Moving it here breaks the reverse dependency.

P407: Added file persistence so worker subprocesses can read the cache.
P408: Added compute_fatigue_chunk — shared chunking formula (P500: translation no longer uses it; sentence chunking does).
"""
import json
import os
import tempfile
from pathlib import Path

_MODEL_CONTEXT_CACHE: dict[str, int] = {}  # {model_id: max_input_tokens}

FATIGUE_RATIO = 0.30

_CONTEXT_CACHE_PATH = Path.home() / ".video-subber" / "context_cache.json"

_LOCAL_CONTEXT_FALLBACK: dict[str, int] = {
    "deepseek-chat": 65536,
    "deepseek-v4-flash": 1000000,
    "deepseek-reasoner": 65536,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gemini-2.0-flash": 1048576,
}


def compute_fatigue_chunk(
    ctx: int,
    *,
    overhead: int,
    per_unit: int,
    floor: int,
    ceiling: int,
) -> int:
    """(ctx × FATIGUE_RATIO − overhead) ÷ per_unit, clamped to [floor, ceiling].

    P253: LLMs degrade past ~30% context fill ("Lost in the Middle").
    Used by both translation (orchestrator.py) and sentence chunking (worker.py).
    """
    fatigue = int(ctx * FATIGUE_RATIO)
    available = fatigue - overhead
    raw = available // per_unit
    return max(floor, min(raw, ceiling))


def save_context_cache(
    data: dict[str, int],
    *,
    path: Path | None = None,
) -> None:
    target = path or _CONTEXT_CACHE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(target.parent),
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, str(target))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_context_cache(*, path: Path | None = None) -> dict[str, int]:
    target = path or _CONTEXT_CACHE_PATH
    try:
        raw = target.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if isinstance(v, (int, float)) and v > 0}
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {}


def get_model_context(model_id: str) -> int:
    if _MODEL_CONTEXT_CACHE:
        if model_id in _MODEL_CONTEXT_CACHE:
            val = _MODEL_CONTEXT_CACHE[model_id]
            if val > 0:
                return val
        for key, val in _MODEL_CONTEXT_CACHE.items():
            if val > 0 and key.endswith("/" + model_id):
                return val

    file_cache = load_context_cache()
    if file_cache:
        if model_id in file_cache:
            return file_cache[model_id]
        for key, val in file_cache.items():
            if val > 0 and key.endswith("/" + model_id):
                return val

    if model_id in _LOCAL_CONTEXT_FALLBACK:
        return _LOCAL_CONTEXT_FALLBACK[model_id]

    return 32768
