"""Environment checks: ffmpeg, whisper, resemblyzer."""
import importlib.util
import subprocess, shutil, os, sys
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
    """Check the Whisper runtime package AND local models.

    C3.5 收口：与其余三组件同口径拆分——installed_pkg（运行时包可用，只用
    find_spec 探测、不 import）+ model_ready（本地有模型）。installed =
    installed_pkg AND model_ready AND runtime_ready，保留给旧调用点（前端 blocking 门控、模型滑块）。
    前端据 installed_pkg/model_ready 区分"包缺失不可修复"与"模型缺失可修复"。
    runtime_ready 代表当前启动环境真能运行该后端；macOS MLX 会轻量触发 Metal。
    Returns: {installed, installed_pkg, model_ready, runtime_ready, runtime_error,
              models: [str], backend}
    """
    if sys.platform == "darwin" and platform_machine() == "arm64":
        installed_pkg = importlib.util.find_spec("mlx_whisper") is not None
        backend = "mlx"
    else:
        installed_pkg = importlib.util.find_spec("faster_whisper") is not None
        backend = "faster"
    models = _cached_whisper_models() if installed_pkg else []
    model_ready = len(models) > 0
    runtime_ready = False
    runtime_error = None
    if installed_pkg and model_ready:
        if backend == "mlx":
            runtime_ready, runtime_error = _check_mlx_runtime()
        else:
            runtime_ready = True
    return {"installed": installed_pkg and model_ready and runtime_ready,
            "installed_pkg": installed_pkg, "model_ready": model_ready,
            "runtime_ready": runtime_ready, "runtime_error": runtime_error,
            "models": models, "backend": backend}


def _check_mlx_runtime() -> tuple[bool, str | None]:
    try:
        mx = importlib.import_module("mlx.core")
        probe = mx.array([1.0])
        mx.eval(probe)
        return True, None
    except Exception as exc:
        return False, str(exc)


def platform_machine() -> str:
    try:
        import platform
        return platform.machine()
    except Exception:
        return ""


def _cached_whisper_models() -> list:
    try:
        from backend import model_manager
        return [
            mid for mid in model_manager.MODELS
            if model_manager._find_ready_model_dir(mid) is not None
        ]
    except Exception:
        return []


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


def _registry_model_ready(model_key: str) -> bool:
    """C3.5 (㉕): 权重是否已在本地可离线加载——注册表统一判定。"""
    try:
        from backend import model_registry
        return model_registry.is_model_resolvable(model_key)
    except Exception:
        return False


def check_sensevoice() -> dict:
    """Check SenseVoice: funasr importable AND model weights present locally.

    C3.5 (㉕): 包可导入 ≠ ready。ready = installed_pkg AND model_ready，
    消灭"import 成功=就绪"的假绿灯。
    """
    try:
        import funasr  # noqa: F401
        installed_pkg = True
    except ImportError:
        installed_pkg = False
    model_ready = _registry_model_ready("sensevoice")
    return {"ready": installed_pkg and model_ready,
            "installed_pkg": installed_pkg, "model_ready": model_ready}


def check_qwen_asr() -> dict:
    """Check Qwen3-ASR: backend package importable AND weights present locally.

    Imports can raise beyond ImportError (e.g. mlx raises RuntimeError when no
    Metal device is present), so treat any failure as "this backend unusable"
    and fall through rather than letting it escape and sink the health check.
    """
    installed_pkg = False
    backend = None
    try:
        from mlx_qwen3_asr import transcribe  # noqa: F401
        installed_pkg, backend = True, "mlx"
    except Exception:
        pass  # mlx unusable (not installed, or no Metal) → try pytorch
    if not installed_pkg:
        try:
            from qwen_asr import Qwen3ASRModel  # noqa: F401
            installed_pkg, backend = True, "pytorch"
        except Exception:
            pass
    model_ready = _registry_model_ready("qwen_asr")
    result = {"ready": installed_pkg and model_ready,
              "installed_pkg": installed_pkg, "model_ready": model_ready}
    if backend:
        result["backend"] = backend
    return result


def check_qwen_aligner() -> dict:
    """Check Qwen3-ForcedAligner: qwen-asr importable AND weights present.

    Frontend/API status key remains qwen_aligner; registry model key is qwen_fa.
    """
    try:
        from backend.core.qwen_aligner import (
            _has_qwen_asr_package, is_qwen_aligner_available)
        return {"ready": is_qwen_aligner_available(),
                "installed_pkg": _has_qwen_asr_package,
                "model_ready": _registry_model_ready("qwen_fa")}
    except ImportError:
        return {"ready": False, "installed_pkg": False, "model_ready": False}


def check_ytdlp() -> dict:
    """Check if yt-dlp is installed (for online video download).

    P174: a missing yt-dlp used to surface as a misleading OOM error.
    """
    try:
        from backend.core.video_downloader import is_ytdlp_available
        return {"ready": is_ytdlp_available()}
    except ImportError:
        return {"ready": False}


def check_all() -> dict:
    # Isolate each check: one checker raising must never sink the whole summary.
    checks = {
        "ffmpeg": check_ffmpeg,
        "whisper": check_whisper,
        "resemblyzer": check_resemblyzer,
        "sensevoice": check_sensevoice,
        "qwen_asr": check_qwen_asr,
        "qwen_aligner": check_qwen_aligner,
        "ytdlp": check_ytdlp,
    }
    result = {}
    for name, fn in checks.items():
        try:
            result[name] = fn()
        except Exception as e:
            result[name] = {"ready": False, "error": type(e).__name__}
    return result


def _entry_ok(d: dict) -> bool:
    """A check entry is ready if it reports ready=True or installed=True."""
    return bool(d.get("ready", d.get("installed", False)))


def health_summary_line(status: dict) -> str:
    """One-line ✅/❌ summary of check_all() output, for startup logging."""
    return "  ".join(
        f"{'✅' if _entry_ok(d) else '❌'} {name}"
        for name, d in status.items()
    )


def missing_checks(status: dict) -> list:
    """Names of checks that are not ready, for startup warnings."""
    return [name for name, d in status.items() if not _entry_ok(d)]
