"""Environment checks: ffmpeg, whisper, resemblyzer."""
import importlib.util
import subprocess, shutil, os, glob, sys
from pathlib import Path


def check_ffmpeg() -> dict:
    """Check if ffmpeg is installed and has libass support.
    Returns: {installed, has_libass, path}
    - Green: installed=True, has_libass=True
    - Yellow: installed=True, has_libass=False
    - Red: installed=False
    """
    possible = []
    if sys.platform == "win32":
        for prefix in [
            r"C:\ffmpeg\bin",
            r"C:\Program Files\ffmpeg\bin",
            r"C:\Program Files (x86)\ffmpeg\bin",
        ]:
            candidate = os.path.join(prefix, "ffmpeg.exe")
            if os.path.exists(candidate):
                possible.append(candidate)
    else:
        for prefix in [
            "/opt/homebrew/opt/ffmpeg-full/bin",    # macOS Apple Silicon
            "/usr/local/opt/ffmpeg-full/bin",        # macOS Intel
            "/opt/homebrew/bin",                     # macOS Apple Silicon (brew)
            "/usr/local/bin",                        # macOS Intel / Linux
        ]:
            candidate = os.path.join(prefix, "ffmpeg")
            if os.path.exists(candidate):
                possible.append(candidate)
    possible.append("ffmpeg")  # fallback to PATH

    for ff in possible:
        if os.path.exists(ff) or shutil.which(ff):
            try:
                r = subprocess.run(
                    [ff, "-version"], capture_output=True, text=True,
                    timeout=5, encoding="utf-8", errors="replace",
                )
                has_libass = "libass" in r.stdout
                return {"installed": True, "has_libass": has_libass, "path": ff}
            except Exception:
                continue
    return {"installed": False, "has_libass": False, "path": None}


def check_whisper() -> dict:
    """Check if any Whisper model is cached locally.
    Returns: {installed, models: [str]}
    """
    if sys.platform == "darwin" and platform_machine() == "arm64":
        if importlib.util.find_spec("mlx_whisper") is None:
            return {"installed": False, "models": [], "backend": "mlx"}
        models = _cached_whisper_models()
        return {"installed": True, "models": models, "backend": "mlx"}
    return {"installed": len(_cached_whisper_models()) > 0,
            "models": _cached_whisper_models(), "backend": "faster"}


def platform_machine() -> str:
    try:
        import platform
        return platform.machine()
    except Exception:
        return ""


def _cached_whisper_models() -> list:
    cache_dir = os.path.expanduser("~/.cache/huggingface/hub")
    models = []
    if os.path.exists(cache_dir):
        whisper_dirs = glob.glob(
            os.path.join(cache_dir, "models--*whisper*"), recursive=False
        )
        if not whisper_dirs:
            whisper_dirs = glob.glob(
                os.path.join(cache_dir, "**/*whisper*"), recursive=True
            )
        for d in whisper_dirs:
            name = os.path.basename(d).replace("models--", "").replace("--", "/")
            for m in ["tiny", "base", "small", "medium", "large"]:
                if m in name and m not in models:
                    models.append(m if m != "large" else "large-v3")
    return list(set(models))


def check_resemblyzer() -> dict:
    """Check if resemblyzer model is cached."""
    try:
        from resemblyzer import VoiceEncoder  # noqa: F401
        return {"ready": True}
    except ImportError:
        return {"ready": False}


def preload_resemblyzer():
    """Download resemblyzer model silently in background."""
    try:
        from resemblyzer import VoiceEncoder
        VoiceEncoder()
    except Exception:
        pass


def check_sensevoice() -> dict:
    """Check if SenseVoice (funasr) is installed."""
    try:
        import funasr  # noqa: F401
        return {"ready": True}
    except ImportError:
        return {"ready": False}


def check_qwen_asr() -> dict:
    """Check if Qwen3-ASR is installed (MLX on Apple Silicon, or PyTorch)."""
    try:
        from mlx_qwen3_asr import transcribe  # noqa: F401
        return {"ready": True, "backend": "mlx"}
    except ImportError:
        pass
    try:
        from qwen_asr import Qwen3ASRModel  # noqa: F401
        return {"ready": True, "backend": "pytorch"}
    except ImportError:
        return {"ready": False}


def check_qwen_aligner() -> dict:
    """Check if Qwen3-ForcedAligner model is available locally."""
    try:
        from backend.core.qwen_aligner import is_qwen_aligner_available
        return {"ready": is_qwen_aligner_available()}
    except ImportError:
        return {"ready": False}


def check_all() -> dict:
    return {
        "ffmpeg": check_ffmpeg(),
        "whisper": check_whisper(),
        "resemblyzer": check_resemblyzer(),
        "sensevoice": check_sensevoice(),
        "qwen_asr": check_qwen_asr(),
        "qwen_aligner": check_qwen_aligner(),
    }
