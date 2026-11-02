"""Whisper backend adapter.

Mac Apple Silicon uses mlx-whisper. Other platforms keep faster-whisper so the
Windows CUDA path stays unchanged.
"""
from __future__ import annotations

import logging
import os
import platform
import subprocess
import tempfile
from dataclasses import dataclass

_log = logging.getLogger("whisper_runner")

_LANGUAGE_PROBE_FFMPEG_TIMEOUT = 60

MLX_WHISPER_MODELS = {
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v2": "mlx-community/whisper-large-v2-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
}


@dataclass
class WhisperInfo:
    language: str | None = None
    language_probability: float = 0.0
    duration: float = 0.0
    backend: str = "unknown"


@dataclass
class WhisperSegment:
    start: float
    end: float
    text: str
    words: list


# ═══════════════════════════════════════════════════════════════════
# G11/P444：语言只准被决定一次。
# 多点采样的证据与裁决——纯数据 + 纯函数，不加载任何模型。
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class LanguageSample:
    """一个采样点的完整证据。

    低概率或投了反对票的样本**仍然有效**（valid=True），照常计入分母；
    只有技术原因（无人声 / 截取失败 / 模型没有返回概率）才置 valid=False。
    """
    position_s: float
    language: str | None
    probabilities: dict
    valid: bool = True
    invalid_reason: str | None = None

    def as_payload(self) -> dict:
        return {
            "position_s": float(self.position_s),
            "language": self.language,
            "probabilities": {str(k): float(v) for k, v in (self.probabilities or {}).items()},
            "valid": bool(self.valid),
            "invalid_reason": self.invalid_reason,
        }

    @classmethod
    def from_payload(cls, raw):
        return cls(
            position_s=float(raw["position_s"]),
            language=raw["language"],
            probabilities=dict(raw["probabilities"]),
            valid=bool(raw["valid"]),
            invalid_reason=raw["invalid_reason"],
        )


class LanguageEvidenceMissing(Exception):
    """一个采样点都没听出语言——这个文件我们根本听不了。

    2026-08-29 之前这个异常还负责「分票」（任何分歧即失败）。
    那条路已删，改为多数票。现在它只剩一种触发条件：**零有效票**。
    """

    def __init__(self, decision):
        self.decision = decision
        super().__init__(
            f"没有任何有效语言样本：计划={decision.expected_count} "
            f"有效={decision.valid_count} 后端={decision.backend}")


@dataclass(frozen=True)
class LanguageDecision:
    """整片语言的唯一决定。locked=False 时不得交付。"""
    language: str | None
    locked: bool
    split: bool
    expected_count: int = 0
    valid_count: int = 0
    samples: tuple = ()
    # 决定由谁产生，它自己最清楚：Mac 走 MLX、Windows 走 faster-whisper，
    # 出了错要知道当时是哪个引擎在听，否则两个系统的表现会被混为一谈。
    backend: str = ""

    def as_payload(self) -> dict:
        """完整采样证据一并落盘——出事之后要能回溯每个点看到了什么。"""
        return {
            "language": self.language,
            "locked": bool(self.locked),
            "split": bool(self.split),
            "expected_count": int(self.expected_count),
            "valid_count": int(self.valid_count),
            "samples": [s.as_payload() for s in self.samples],
            "backend": self.backend,
        }

    @classmethod
    def from_payload(cls, raw):
        return cls(
            language=raw["language"],
            locked=bool(raw["locked"]),
            split=bool(raw["split"]),
            expected_count=int(raw["expected_count"]),
            valid_count=int(raw["valid_count"]),
            samples=tuple(LanguageSample.from_payload(s) for s in raw["samples"]),
            backend=raw.get("backend", ""),
        )


@dataclass(frozen=True)
class SamplePlan:
    """采样计划。mode="whole" 表示整段已被完整覆盖，只算一票。"""
    mode: str
    count: int
    positions: tuple


# 计划点数的阶梯。CI-03：只有总时长 ≥ 点数 × 探测窗长才取该点数。
_SAMPLE_LADDER = (7, 5, 3)


def plan_sample_positions(duration_s: float, probe_seconds: int = 30) -> SamplePlan:
    """按时长推导采样点数与位置。

    CI-03：**边界写成推导规则，不写死数字**，跟着探测窗长自动走。
    ① 总时长 ≥ 点数 × 窗长 才取该点数（阶梯 7 → 5 → 3）；
    ② 阶梯都放不下时降到能容纳的最大点数；
    ③ 连 2 个不重叠样本都放不下 → 整段已被完整覆盖分支，只算一票，
       **不重复同一片段伪造多票**。
    """
    duration = max(0.0, float(duration_s))
    window = max(1, int(probe_seconds))

    # ⚠️ 「时长未知」和「视频很短」是两件事，不能当成同一件。
    # `get_video_duration()` 在 ffprobe 失败时**静默返回 0.0**（不抛异常）。
    # 若把 0.0 当成极短视频走整段分支，就只取 1 个样本；单点必然自洽 →
    # locked=True → 照常交付——**第一刀（只听开头 30 秒）原样从侧门复活**。
    if duration <= 0.0:
        return SamplePlan(mode="unknown", count=0, positions=())

    capacity = int(duration // window)
    if capacity < 2:
        return SamplePlan(mode="whole", count=1, positions=(0.0,))

    count = next((n for n in _SAMPLE_LADDER if n <= capacity), capacity)
    # 均匀铺开且窗口互不重叠：把 count 个窗放进 duration，间隔取剩余空间均分。
    span = duration - count * window
    gap = span / (count - 1) if count > 1 else 0.0
    positions = tuple(round(i * (window + gap), 3) for i in range(count))
    return SamplePlan(mode="sampled", count=count, positions=positions)


def resolve_language_decision(samples, expected_count=None, backend="") -> LanguageDecision:
    """从多点采样证据得出唯一决定：**多数说了算**。

    2026-08-29 方向变更，推翻「任何分歧即失败」。
    推翻的理由：多点采样存在的目的就是不让单点误判说了算。
    让一个听岔的点拥有否决权，等于把要防的东西请回来当裁判——
    七点里六点听到英语，答案就是英语，这不叫「意见不一致」。

    平票取最早出现的那一种。七/五/三都是奇数，两种语言之间不可能平票；
    这条只在无效样本把有效数拉成偶数、或出现三种以上语言时生效。
    规则必须确定，不能落到字典顺序上。

    唯一没有答案的情况：**一票都没有**（读不出时长 → 零采样点，
    或全部探测失败）。那说明这个文件我们根本听不了，locked=False。
    """
    samples = list(samples)
    planned = len(samples) if expected_count is None else int(expected_count)

    votes = {}
    for sample in samples:
        if sample.valid and sample.language:
            votes.setdefault(sample.language, []).append(sample.position_s)

    # 票多者胜；票数相同取最早出现的（-min(positions) 让位置越早排越前）。
    language = max(
        votes, key=lambda code: (len(votes[code]), -min(votes[code]))
    ) if votes else None

    return LanguageDecision(
        language=language,
        locked=language is not None,
        # 分歧仍如实记进证据——出事之后要能回溯每个点听到了什么。
        # 但它只是记录，不再阻止交付，更不会送到用户眼前。
        split=len(votes) > 1,
        expected_count=planned,
        valid_count=sum(len(positions) for positions in votes.values()),
        samples=tuple(samples),
        backend=backend,
    )


class _MlxLanguageProber:
    """MLX 探测器：模型在 __enter__ 里加载一次，之后每个采样点复用。"""

    def __init__(self, model_size):
        self._model_size = model_size
        self._model = None
        self._mx = None
        self._audio_api = None

    def __enter__(self):
        try:
            import mlx.core as mx
            from mlx_whisper.audio import (
                load_audio, log_mel_spectrogram, pad_or_trim,
                N_FRAMES, N_SAMPLES,
            )
            from mlx_whisper.load_models import load_model
        except ImportError as exc:
            raise RuntimeError(
                "Mac 端需要安装 mlx-whisper 才能检测语言。请先安装依赖并下载模型。"
            ) from exc
        self._mx = mx
        self._audio_api = (load_audio, log_mel_spectrogram, pad_or_trim, N_FRAMES, N_SAMPLES)
        # CI-02：整轮只加载一次。
        self._model = load_model(resolve_whisper_model_path(self._model_size), dtype=mx.float16)
        return self

    def __exit__(self, *exc):
        self._model = None
        return False

    def __call__(self, audio_path, start_s, probe_seconds):
        load_audio, log_mel_spectrogram, pad_or_trim, N_FRAMES, N_SAMPLES = self._audio_api
        clip = _create_language_probe_clip(audio_path, probe_seconds, start_s=start_s)
        try:
            dtype = self._mx.float16
            mel = log_mel_spectrogram(
                load_audio(clip), n_mels=self._model.dims.n_mels, padding=N_SAMPLES)
            mel_segment = pad_or_trim(mel, N_FRAMES, axis=-2).astype(dtype)
            _, probs = self._model.detect_language(mel_segment)
            if not probs:
                return None, {}
            return max(probs, key=probs.get), {k: float(v) for k, v in probs.items()}
        finally:
            try:
                os.unlink(clip)
            except FileNotFoundError:
                pass


class _FasterLanguageProber:
    """faster-whisper 探测器：模型只构造一次；截片能力在此一并补齐。

    CI-02：原 `detect_language_with_faster_whisper` 无 `probe_seconds`，
    直接把整个文件交给 `model.transcribe()`，既无法按位置取样也无法复用模型。
    """

    def __init__(self, model_size):
        self._model_size = model_size
        self._model = None

    def __enter__(self):
        from faster_whisper import WhisperModel
        # CI-02：整轮只构造一次。
        self._model = WhisperModel(
            resolve_whisper_model_path(self._model_size),
            device="auto", compute_type="auto")
        return self

    def __exit__(self, *exc):
        self._model = None
        return False

    def __call__(self, audio_path, start_s, probe_seconds):
        clip = _create_language_probe_clip(audio_path, probe_seconds, start_s=start_s)
        try:
            segments_iter, info = self._model.transcribe(
                clip, beam_size=5, language=None,
                condition_on_previous_text=True,
                log_prob_threshold=-1.0, word_timestamps=False)
            del segments_iter
            language = getattr(info, "language", None)
            probability = float(getattr(info, "language_probability", 0.0) or 0.0)
            if not language:
                return None, {}
            return language, {language: probability}
        finally:
            try:
                os.unlink(clip)
            except FileNotFoundError:
                pass


def _open_language_prober(model_size):
    """打开一个持有已加载模型的探测器。两个后端输出相同的证据结构。"""
    if choose_whisper_backend() == "mlx":
        return _MlxLanguageProber(model_size)
    return _FasterLanguageProber(model_size)


def sample_language_multipoint(
    audio_path: str, model_size: str, duration_s: float, probe_seconds: int = 30
) -> LanguageDecision:
    """多点采样 → 唯一 LanguageDecision。整轮只加载一次模型（CI-02）。"""
    plan = plan_sample_positions(duration_s, probe_seconds)
    if plan.mode == "unknown":
        _log.warning("G11 音频时长未知（探测失败），无法规划采样点——判为不确定")
        return LanguageDecision(
            language=None, locked=False, split=False,
            expected_count=0, valid_count=0,
            samples=(), backend=choose_whisper_backend())
    samples = []
    with _open_language_prober(model_size) as probe:
        for position in plan.positions:
            try:
                language, probabilities = probe(audio_path, position, probe_seconds)
            except Exception as exc:  # 技术原因才算无效
                _log.warning("语言采样点 %.1fs 失败：%s", position, exc)
                samples.append(LanguageSample(
                    position_s=position, language=None, probabilities={},
                    valid=False, invalid_reason="probe_failed"))
                continue
            if not language or not probabilities:
                samples.append(LanguageSample(
                    position_s=position, language=None, probabilities={},
                    valid=False, invalid_reason="no_probabilities"))
                continue
            samples.append(LanguageSample(
                position_s=position, language=language, probabilities=probabilities))
    decision = resolve_language_decision(
        samples, expected_count=plan.count, backend=choose_whisper_backend())
    _log.info(
        "G11 多点采样：模式=%s 计划=%d 有效=%d 语言=%s 锁定=%s 分票=%s",
        plan.mode, decision.expected_count, decision.valid_count,
        decision.language, decision.locked, decision.split)
    return decision


def choose_whisper_backend() -> str:
    """Return the Whisper implementation for the current platform."""
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "mlx"
    return "faster"


def should_parallel_asr() -> bool:
    """Unified memory (Apple Silicon) cannot truly parallelise two large models."""
    return choose_whisper_backend() != "mlx"


def resolve_whisper_model_path(model_size: str) -> str:
    """Resolve a Whisper model name to a ready local directory.

    Lazy import avoids a circular import: model_manager needs choose_whisper_backend()
    to build platform-specific download metadata.
    """
    from backend.model_manager import resolve_whisper_model_path as _resolve
    return _resolve(model_size, backend=choose_whisper_backend())


def _normalize_words(words) -> list:
    normalized = []
    for word in words or []:
        if isinstance(word, dict):
            normalized.append({
                "word": word.get("word") or word.get("text") or "",
                "start": word.get("start", 0.0),
                "end": word.get("end", 0.0),
                "probability": word.get("probability", 0.0),
            })
        else:
            normalized.append({
                "word": getattr(word, "word", ""),
                "start": getattr(word, "start", 0.0),
                "end": getattr(word, "end", 0.0),
                "probability": getattr(word, "probability", 0.0),
            })
    return normalized


def _normalize_segment(seg) -> WhisperSegment:
    if isinstance(seg, dict):
        return WhisperSegment(
            start=float(seg.get("start", 0.0)),
            end=float(seg.get("end", 0.0)),
            text=(seg.get("text") or "").strip(),
            words=_normalize_words(seg.get("words") or []),
        )
    return WhisperSegment(
        start=float(getattr(seg, "start", 0.0)),
        end=float(getattr(seg, "end", 0.0)),
        text=(getattr(seg, "text", "") or "").strip(),
        words=_normalize_words(getattr(seg, "words", []) or []),
    )


def _dict_get(result, key, default=None):
    if isinstance(result, dict):
        return result.get(key, default)
    return getattr(result, key, default)


def transcribe_with_mlx_whisper(
    audio_path: str,
    model_size: str,
    language: str | None,
    vad_threshold: float = 0.0,
) -> tuple[list[WhisperSegment], WhisperInfo]:
    """Run mlx-whisper and return the same shape the app expects."""
    try:
        import mlx_whisper
    except ImportError as exc:
        raise RuntimeError(
            "Mac 端需要安装 mlx-whisper 才能转录。请先安装依赖并下载模型。"
        ) from exc

    if vad_threshold > 0:
        _log.warning("mlx-whisper 不支持当前 faster-whisper VAD 参数，已忽略 vad_threshold=%.2f", vad_threshold)

    model_path = resolve_whisper_model_path(model_size)
    result = mlx_whisper.transcribe(
        audio_path,
        path_or_hf_repo=model_path,
        language=language,
        word_timestamps=True,
    )
    raw_segments = _dict_get(result, "segments", []) or []
    segments = [_normalize_segment(seg) for seg in raw_segments]
    detected_lang = _dict_get(result, "language")
    if language:
        lang_prob = 1.0
    else:
        try:
            import mlx.core as mx
            from mlx_whisper.audio import (
                load_audio, log_mel_spectrogram, pad_or_trim,
                N_FRAMES, N_SAMPLES,
            )
            from mlx_whisper.load_models import load_model
            dtype = mx.float16
            model = load_model(model_path, dtype=dtype)
            audio_data = load_audio(audio_path)
            mel = log_mel_spectrogram(audio_data, n_mels=model.dims.n_mels, padding=N_SAMPLES)
            mel_seg = pad_or_trim(mel, N_FRAMES, axis=-2).astype(dtype)
            _, probs = model.detect_language(mel_seg)
            if not probs or detected_lang not in probs:
                raise RuntimeError(
                    f"mlx-whisper 完整转录后无法获取语言概率 (language={detected_lang})"
                )
            lang_prob = float(probs[detected_lang])
        except ImportError as exc:
            raise RuntimeError(
                "mlx-whisper 完整转录后无法获取语言概率：底层模块导入失败"
            ) from exc
    return segments, WhisperInfo(
        language=detected_lang,
        language_probability=lang_prob,
        duration=float(_dict_get(result, "duration", 0.0) or 0.0),
        backend="mlx",
    )


def transcribe_with_faster_whisper(
    audio_path: str,
    model_size: str,
    language: str | None,
    vad_threshold: float = 0.0,
) -> tuple[list[WhisperSegment], WhisperInfo]:
    """Run faster-whisper. This is the unchanged Windows/Linux path."""
    from faster_whisper import WhisperModel

    model_path = resolve_whisper_model_path(model_size)
    model = WhisperModel(model_path, device="auto", compute_type="auto")
    vad_kwargs = {}
    if vad_threshold > 0:
        vad_kwargs = {
            "vad_filter": True,
            "vad_parameters": {
                "threshold": vad_threshold,
                "min_speech_duration_ms": 250,
                "min_silence_duration_ms": 100,
            },
        }
    segments_iter, info = model.transcribe(
        audio_path,
        beam_size=5,
        language=language,
        condition_on_previous_text=True,
        log_prob_threshold=-1.0,
        word_timestamps=True,
        **vad_kwargs,
    )
    segments = [_normalize_segment(seg) for seg in segments_iter]
    return segments, WhisperInfo(
        language=getattr(info, "language", None),
        language_probability=float(getattr(info, "language_probability", 0.0) or 0.0),
        duration=float(getattr(info, "duration", 0.0) or 0.0),
        backend="faster",
    )


def _create_language_probe_clip(
    audio_path: str, probe_seconds: int = 30, start_s: float = 0.0
) -> str:
    """Extract a short mono WAV clip for language detection.

    G11：`start_s` 是第一刀的根治点——原实现无起始位置参数，
    整片语言只能由开头 30 秒单点决定（事故 fc441fb713 即由此而来）。
    `-ss` 放在 `-i` 之前，走关键帧快速定位，代价与开头截取相当。
    """
    fd, probe_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, float(start_s)):.3f}",
        "-i",
        audio_path,
        "-t",
        str(probe_seconds),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        probe_path,
    ]
    try:
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=_LANGUAGE_PROBE_FFMPEG_TIMEOUT,
            check=True,
        )
    except subprocess.TimeoutExpired as exc:
        try:
            os.unlink(probe_path)
        except FileNotFoundError:
            pass
        raise RuntimeError(
            f"语言探测音频截取超时（超过 {_LANGUAGE_PROBE_FFMPEG_TIMEOUT} 秒）。请重新提交任务。"
        ) from exc
    except subprocess.CalledProcessError as exc:
        try:
            os.unlink(probe_path)
        except FileNotFoundError:
            pass
        stderr_tail = (exc.stderr or "")[-300:]
        raise RuntimeError(f"语言探测音频截取失败: {stderr_tail}") from exc
    return probe_path


def detect_language_with_mlx_whisper(
    audio_path: str,
    model_size: str,
    language: str | None,
    probe_seconds: int = 30,
) -> WhisperInfo:
    """Detect language with mlx-whisper using only a short audio clip.

    Calls model.detect_language() directly instead of transcribe(),
    because mlx_whisper.transcribe() does not return language_probability.
    """
    try:
        import mlx.core as mx
        from mlx_whisper.audio import (
            load_audio, log_mel_spectrogram, pad_or_trim,
            N_FRAMES, N_SAMPLES,
        )
        from mlx_whisper.load_models import load_model
    except ImportError as exc:
        raise RuntimeError(
            "Mac 端需要安装 mlx-whisper 才能检测语言。请先安装依赖并下载模型。"
        ) from exc

    model_path = resolve_whisper_model_path(model_size)
    probe_path = _create_language_probe_clip(audio_path, probe_seconds)
    try:
        dtype = mx.float16
        model = load_model(model_path, dtype=dtype)
        audio = load_audio(probe_path)
        mel = log_mel_spectrogram(audio, n_mels=model.dims.n_mels, padding=N_SAMPLES)
        mel_segment = pad_or_trim(mel, N_FRAMES, axis=-2).astype(dtype)

        if language:
            return WhisperInfo(
                language=language,
                language_probability=1.0,
                backend="mlx",
            )

        _, probs = model.detect_language(mel_segment)
        if not probs:
            raise RuntimeError(
                "mlx-whisper model.detect_language() 返回空概率表，无法判断语言"
            )
        best_lang = max(probs, key=probs.get)
        best_prob = float(probs[best_lang])
        _log.info("MLX 语言探测: %s (概率 %.4f)", best_lang, best_prob)
        return WhisperInfo(
            language=best_lang,
            language_probability=best_prob,
            backend="mlx",
        )
    finally:
        try:
            os.unlink(probe_path)
        except FileNotFoundError:
            pass


def detect_language_with_faster_whisper(
    audio_path: str,
    model_size: str,
    language: str | None,
) -> WhisperInfo:
    """Detect language with faster-whisper without consuming all segments."""
    from faster_whisper import WhisperModel

    model_path = resolve_whisper_model_path(model_size)
    model = WhisperModel(model_path, device="auto", compute_type="auto")
    try:
        segments_iter, info = model.transcribe(
            audio_path,
            beam_size=5,
            language=language,
            condition_on_previous_text=True,
            log_prob_threshold=-1.0,
            word_timestamps=False,
        )
        del segments_iter
        return WhisperInfo(
            language=getattr(info, "language", None),
            language_probability=float(getattr(info, "language_probability", 0.0) or 0.0),
            duration=float(getattr(info, "duration", 0.0) or 0.0),
            backend="faster",
        )
    finally:
        del model


def detect_language_with_whisper(
    audio_path: str,
    model_size: str,
    language: str | None,
    probe_seconds: int = 30,
) -> WhisperInfo:
    backend = choose_whisper_backend()
    if backend == "mlx":
        return detect_language_with_mlx_whisper(
            audio_path, model_size, language, probe_seconds)
    return detect_language_with_faster_whisper(audio_path, model_size, language)


def release_model():
    """Release mlx-whisper's cached model from memory.

    mlx-whisper keeps the model in a class variable (ModelHolder.model) after
    transcribe(). gc.collect() alone cannot free it. This function clears that
    reference so the ~3 GB model memory is actually reclaimed.
    """
    backend = choose_whisper_backend()
    if backend == "mlx":
        try:
            from mlx_whisper.transcribe import ModelHolder
            ModelHolder.model = None
            ModelHolder.model_path = None
        except (ImportError, AttributeError):
            pass
    import gc
    gc.collect()
    _log.info("Whisper 模型内存已释放")


def transcribe_whisper(
    audio_path: str,
    model_size: str,
    language: str | None,
    vad_threshold: float = 0.0,
) -> tuple[list[WhisperSegment], WhisperInfo]:
    backend = choose_whisper_backend()
    if backend == "mlx":
        return transcribe_with_mlx_whisper(audio_path, model_size, language, vad_threshold)
    return transcribe_with_faster_whisper(audio_path, model_size, language, vad_threshold)
