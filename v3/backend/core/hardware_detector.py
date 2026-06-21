"""Hardware capability detection and transcription tier recommendation."""

import logging
import platform
import subprocess
import sys

_log = logging.getLogger("hardware_detector")

TIERS: dict[str, dict] = {
    "fast": {
        "label": "快速",
        "label_en": "Fast",
        "models": ["whisper"],
        "ram_needed_gb": 3,
    },
    "standard": {
        "label": "标准",
        "label_en": "Standard",
        "models": ["whisper", "sensevoice"],
        "ram_needed_gb": 4,
    },
    "precise": {
        "label": "精确",
        "label_en": "Precise",
        "models": ["whisper", "sensevoice", "qwen_asr"],
        "ram_needed_gb": 7,
    },
}

_TIER_ORDER = ("fast", "standard", "precise")


def is_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


def _detect_platform() -> str:
    if sys.platform == "darwin":
        return "mac_apple_silicon" if is_apple_silicon() else "mac_intel"
    if sys.platform == "win32":
        return "windows"
    return "linux"


def _detect_gpu() -> tuple[bool, str | None]:
    if is_apple_silicon():
        return True, "mlx"
    try:
        import torch
        if torch.cuda.is_available():
            return True, "cuda"
    except ImportError:
        pass
    return False, None


def _detect_ram_gb() -> int | None:
    try:
        import psutil
        return round(psutil.virtual_memory().total / 1e9, 1)
    except ImportError:
        pass

    if sys.platform == "darwin":
        try:
            r = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                return round(int(r.stdout.strip()) / 1e9, 1)
        except (subprocess.TimeoutExpired, ValueError, OSError):
            pass
    elif sys.platform == "linux":
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return round(kb / 1e6, 1)
        except (FileNotFoundError, ValueError, IndexError):
            pass

    return None


def recommend_tier(ram_gb: int | float | None) -> str:
    if ram_gb is None or ram_gb < 8:
        return "fast"
    if ram_gb < 16:
        return "standard"
    return "precise"


def downgrade_tier(tier: str, reason: str) -> str:
    try:
        idx = _TIER_ORDER.index(tier)
    except ValueError:
        _log.warning("未知 tier: %r，回退到 fast", tier)
        return "fast"
    if idx == 0:
        _log.info("已在最低 tier fast，无法继续降级 (reason=%s)", reason)
        return "fast"
    lowered = _TIER_ORDER[idx - 1]
    _log.info("降级 %s -> %s (reason=%s)", tier, lowered, reason)
    return lowered


def detect_hardware() -> dict:
    platform_str = _detect_platform()
    has_gpu, gpu_type = _detect_gpu()
    ram_gb = _detect_ram_gb()
    tier = recommend_tier(ram_gb)

    _log.info(
        "硬件检测: platform=%s gpu=%s gpu_type=%s ram=%.1fGB tier=%s",
        platform_str, has_gpu, gpu_type, ram_gb if ram_gb is not None else 0, tier,
    )
    return {
        "platform": platform_str,
        "has_gpu": has_gpu,
        "gpu_type": gpu_type,
        "ram_gb": ram_gb,
        "recommended_tier": tier,
    }
