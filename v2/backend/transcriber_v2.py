"""Transcription via faster-whisper + resemblyzer speaker change detection.

Simplified pipeline:
  Whisper → [SenseVoice sound classification] → [Qwen3-ASR cross-validation]
  → five-layer hallucination filter → speaker detection.
"""
import gc
import logging
import queue
import threading
import numpy as np
from faster_whisper import WhisperModel
from backend.core.vad_filter import compute_speech_ratio_via_vad
from backend.core.hallucination_filter import filter_hallucinations

_log = logging.getLogger("transcriber")

# Shared with qwen_asr_runner for streaming chunk alignment
_CHUNK_MAX_DURATION_S = 300  # 5 minutes

_SVFA_MIN_RAM_GB = 8


def should_parallel_svfa(tier: str, ram_gb: float) -> bool:
    """SV+FA parallelism: any tier with enough RAM (decoupled from Qwen)."""
    return ram_gb >= _SVFA_MIN_RAM_GB


def should_qwen_pipeline(tier: str, ram_gb: float) -> bool:
    """Qwen ASR pipeline: only precise tier with enough RAM."""
    return tier == "precise" and ram_gb >= 16


class Segment:
    """A single transcribed + translated subtitle segment."""
    def __init__(self, id, start, end, text):
        self.id = id
        self.start = start
        self.end = end
        self.text = text
        self.speaker_change = False
        self.uncertain = False
        self.zh = ""
        self.words = []
        self.sensevoice_tag = None
        self.filtered = False
        self.filter_reason = None


def _apply_sv_tags(segments, sv_events):
    for seg in segments:
        seg.sensevoice_tag = None
        best_overlap = 0.0
        best_event = None
        for ev in sv_events:
            ov_start = max(seg.start, ev["start_s"])
            ov_end = min(seg.end, ev["end_s"])
            ov = max(0.0, ov_end - ov_start)
            if ov > best_overlap:
                best_overlap = ov
                best_event = ev["event"]
        seg_dur = seg.end - seg.start
        if best_overlap > 0 and seg_dur > 0 and best_overlap / seg_dur >= 0.5:
            seg.sensevoice_tag = best_event


def _detect_speaker_changes(audio_path: str, segments: list) -> list:
    """Compare adjacent segment voiceprints via resemblyzer.
    Sets segment.speaker_change=True where voice changes.
    Threshold is auto-detected from score distribution — no magic number.
    """
    try:
        from resemblyzer import VoiceEncoder, preprocess_wav
        import librosa
    except ImportError:
        _log.warning("未安装 resemblyzer/librosa，跳过说话人检测")
        return segments

    try:
        encoder = VoiceEncoder()
    except Exception:
        return segments

    def _get_emb(start: float, end: float):
        try:
            dur = end - start
            if dur < 1.0:
                return None
            y, _sr = librosa.load(audio_path, sr=16000, offset=start, duration=dur)
            if len(y) < 16000 * 1.0:
                return None
            wav = preprocess_wav(y, source_sr=16000)
            return encoder.embed_utterance(wav)
        except Exception:
            return None

    # ── Pass 1: collect all similarity scores ──
    prev_emb = None
    sims = []  # (segment_index, similarity_score)
    for seg in segments:
        emb = _get_emb(seg.start, seg.end)
        if emb is not None and prev_emb is not None:
            sim = float(np.dot(emb, prev_emb))
            sims.append((seg, sim))
        if emb is not None:
            prev_emb = emb

    if len(sims) < 2:
        _log.info("说话人检测: 有效相似度不足，跳过")
        return segments

    # ── Guard B: skip detection for short videos (< 5 segments) ──
    if len(segments) < 5:
        _log.info("说话人检测: 段数不足5（%d段），跳过", len(segments))
        return segments

    scores = np.array([s for _, s in sims], dtype=np.float64)
    score_range = float(scores.max() - scores.min())

    # ── Solo-speaker check: tight cluster → no split needed ──
    if score_range < 0.01 or float(scores.std()) < score_range * 0.15:
        _log.info("说话人检测: 单人（std=%.3f, range=%.3f），无横杠", float(scores.std()), score_range)
        return segments

    # ── Multi-speaker: find natural split via minimum within-group variance ──
    sorted_scores = np.sort(scores)
    best_thr = sorted_scores[len(sorted_scores) // 2]  # fallback
    best_var = float("inf")
    for i in range(1, len(sorted_scores)):
        left = sorted_scores[:i]
        right = sorted_scores[i:]
        var_sum = float(left.var() * len(left) + right.var() * len(right))
        if var_sum < best_var:
            best_var = var_sum
            best_thr = float((left[-1] + right[0]) / 2.0)

    # ── Pass 2: apply found threshold ──
    changes = 0
    for seg, sim in sims:
        if sim < best_thr:
            seg.speaker_change = True
            changes += 1

    # ── Guard C: two independent checks — either can cancel ──
    sorted_all = np.sort(scores)
    score_gaps = np.diff(sorted_all)
    gap_ratio = 0.0
    group_gap = 0.0

    # Check 1: gap detection — is there a real crack in the distribution?
    if len(score_gaps) >= 3:
        median_gap = float(np.median(score_gaps))
        max_gap = float(score_gaps.max())
        if median_gap > 1e-6:
            gap_ratio = max_gap / median_gap
            if gap_ratio < 3.0:
                _log.info("说话人检测: 间隙检测未通过（%.1f倍 < 3倍），判定语气波动，全部取消", gap_ratio)
                for seg, _ in sims:
                    seg.speaker_change = False
                return segments

    # Check 2: safety clamp — absolute group distance
    above = [s for s in scores if s >= best_thr]
    below = [s for s in scores if s < best_thr]
    if above and below:
        group_gap = float(np.mean(above) - np.mean(below))
        if group_gap < 0.15:
            _log.info("说话人检测: 安全钳未通过（组间距%.3f < 0.15），判定语气波动，全部取消", group_gap)
            for seg, _ in sims:
                seg.speaker_change = False
            return segments

    _log.info("说话人检测完成: %d 处变化 (阈值=%.3f, 间隙比=%.1f, 组间差=%.3f)",
              changes, best_thr, gap_ratio, group_gap)
    return segments


def transcribe(audio_path: str, model_size: str = "medium",
               source_lang: str = "auto", skip_vad: bool = False,
               vad_threshold: float = 0.0,
               filter_keywords: bool = True,
               speaker_detect: bool = True,
               transcription_tier: str = "standard",
               progress_callback=None) -> list:
    """Transcribe audio, return list of Segment."""
    _log.info("transcribe() 开始: audio=%s model=%s lang=%s",
              audio_path, model_size, source_lang)

    if not skip_vad:
        _log.info("VAD 语音占比检测中…")
        ratio = compute_speech_ratio_via_vad(audio_path)
        if ratio is not None:
            _log.info("语音占比: %.1f%%", ratio)
            if ratio < 20:
                r = input(f"       ⚠️ 语音极低({ratio:.0f}%)，可能为纯音乐。继续？[y/N] ")
                if r.lower() != "y":
                    raise RuntimeError("用户取消")
            elif ratio < 40:
                _log.warning("非语音内容较多。如对话被过滤可用 --vad-threshold 0.4 启用内置 VAD")

    _log.info("加载 Whisper %s 模型…", model_size)
    if progress_callback:
        progress_callback(-1)
    model = WhisperModel(model_size, device="auto", compute_type="auto")

    lang = None if source_lang == "auto" else source_lang

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
        audio_path, beam_size=5, language=lang,
        condition_on_previous_text=True,
        log_prob_threshold=-1.0,
        word_timestamps=True,
        **vad_kwargs
    )
    _log.info("检测语言: %s (概率 %.2f)", info.language, info.language_probability)
    detected_lang = info.language if info.language else source_lang

    results = []
    n_total = 0

    from backend.core.hardware_detector import detect_hardware

    hw = detect_hardware()
    ram_gb = hw.get("ram_gb", 0) or 0
    use_qwen = should_qwen_pipeline(transcription_tier, ram_gb)
    use_parallel = should_parallel_svfa(transcription_tier, ram_gb)

    # ═══════════════════════════════════════════════════════════════
    # P152: 生产者-消费者流水线 — 千ASR 在 Whisper 转录期间后台运行
    # ═══════════════════════════════════════════════════════════════
    qwen_queue = None
    qwen_thread = None
    qwen_chunks = []  # P251: Qwen-ASR output as {text, t_start, t_end} for L2
    if use_qwen and filter_keywords:
        from backend.core.qwen_asr_runner import (
            is_qwen_asr_available, _qwen_consumer,
        )
        if is_qwen_asr_available():
            qwen_queue = queue.Queue()
            qwen_thread = threading.Thread(
                target=_qwen_consumer,
                args=(qwen_queue, audio_path, detected_lang, qwen_chunks),
            )
            qwen_thread.start()

            chunk_start_idx = 0
            try:
                for seg in segments_iter:
                    text = seg.text.strip()
                    n_total += 1

                    s = Segment(
                        id=len(results),
                        start=round(seg.start, 2),
                        end=round(seg.end, 2),
                        text=text,
                    )
                    if hasattr(seg, "words") and seg.words:
                        s.words = []
                        for w in seg.words:
                            if isinstance(w, dict):
                                s.words.append(w)
                            else:
                                s.words.append({
                                    "word": getattr(w, "word", ""),
                                    "start": getattr(w, "start", 0.0),
                                    "end": getattr(w, "end", 0.0),
                                    "probability": getattr(w, "probability", 0.0),
                                })
                    results.append(s)

                    if progress_callback and n_total % 10 == 0:
                        try:
                            progress_callback(n_total)
                        except Exception:
                            pass  # pipe broken or other transient error — worker continues

                    chunk_dur = seg.end - results[chunk_start_idx].start
                    if chunk_dur >= _CHUNK_MAX_DURATION_S:
                        chunk_segs = results[chunk_start_idx:len(results)]
                        t_start = chunk_segs[0].start
                        t_end = chunk_segs[-1].end
                        qwen_queue.put((list(chunk_segs), t_start, t_end))
                        chunk_start_idx = len(results)
            finally:
                # P182: flush remaining trailing segments before sentinel
                if chunk_start_idx < len(results):
                    remaining = results[chunk_start_idx:]
                    qwen_queue.put((
                        list(remaining),
                        remaining[0].start,
                        remaining[-1].end,
                    ))
                qwen_queue.put(None)

    # ── 如果没有走流水线路径，走回原始 Whisper 循环 ──
    if not qwen_thread:
        for seg in segments_iter:
            text = seg.text.strip()
            n_total += 1

            s = Segment(
                id=len(results),
                start=round(seg.start, 2),
                end=round(seg.end, 2),
                text=text,
            )
            if hasattr(seg, "words") and seg.words:
                s.words = []
                for w in seg.words:
                    if isinstance(w, dict):
                        s.words.append(w)
                    else:
                        s.words.append({
                            "word": getattr(w, "word", ""),
                            "start": getattr(w, "start", 0.0),
                            "end": getattr(w, "end", 0.0),
                            "probability": getattr(w, "probability", 0.0),
                        })
            results.append(s)

            if progress_callback and n_total % 10 == 0:
                progress_callback(n_total)

    # ── Release Whisper model memory ──
    del model
    gc.collect()
    _log.info("Whisper 模型已释放")

    _log.info("Whisper 转录: %d 段, 语言=%s (概率 %.2f)", n_total, info.language, info.language_probability)

    # ── SV+FA parallel launch (decoupled from Qwen — P199/P200) ──
    sv_events = None
    sv_thread = None
    fa_thread = None

    if use_parallel and filter_keywords:
        _log.info("并行模式：SenseVoice + Qwen3-FA 同时启动")

        from backend.core.sensevoice_runner import run_sensevoice, is_sensevoice_available
        _sv_holder = {}
        if is_sensevoice_available():
            def _run_sv():
                _sv_holder["data"] = run_sensevoice(audio_path, device="cpu", segments=results)
            sv_thread = threading.Thread(target=_run_sv, daemon=True)
            sv_thread.start()

        from backend.core.qwen_aligner import (
            QwenAligner, is_qwen_aligner_available, align_segments,
        )
        _fa_holder = {}
        if is_qwen_aligner_available():
            def _run_fa():
                aligner = None
                try:
                    aligner = QwenAligner()
                    if aligner._model is not None:
                        _fa_holder["data"] = align_segments(
                            aligner, audio_path, results, language=detected_lang)
                except Exception as e:
                    _fa_holder["_quality"] = "fallback"
                    _log.warning("Qwen3-FA 并行对齐失败: %s", e)
                finally:
                    if aligner is not None and aligner._model is not None:
                        aligner.close()
            fa_thread = threading.Thread(target=_run_fa, daemon=True)
            fa_thread.start()
    elif filter_keywords and transcription_tier in ("standard", "precise"):
        _log.info("SenseVoice 声音分类开始 (tier=%s, 串行降级)", transcription_tier)
        from backend.core.sensevoice_runner import (
            run_sensevoice, is_sensevoice_available,
        )
        if is_sensevoice_available():
            sv_events = run_sensevoice(audio_path, device="cpu", segments=results)
        else:
            _log.warning("SenseVoice 不可用，跳过声音分类")

    # ── 等 Qwen ASR 流水线结束（在 SV+FA 启动之后）──
    if qwen_thread:
        qwen_thread.join()
        _log.info("千ASR 消费者线程已结束")

    # ── 等待并行线程 ──
    if sv_thread:
        sv_thread.join()
        sv_events = _sv_holder.get("data")
    if fa_thread:
        fa_thread.join()

    # ── Step 3: Qwen3-ASR 交叉验证（串行路径，非流水线模式下此处运行）──
    if transcription_tier == "precise" and filter_keywords and not qwen_thread:
        _log.info("Qwen3-ASR 交叉验证开始 (tier=%s)", transcription_tier)
        from backend.core.qwen_asr_runner import run_qwen_asr, is_qwen_asr_available
        if is_qwen_asr_available():
            qwen_chunks = run_qwen_asr(audio_path, results, language=detected_lang)
        else:
            _log.warning("Qwen3-ASR 不可用，跳过交叉验证")

    # ── Step 4: Qwen3-FA alignment（先改时间戳）──
    aligner_result = None
    if use_parallel and fa_thread:
        fa_data = _fa_holder.get("data")
        if fa_data:
            from backend.core.qwen_aligner import apply_aligner_timestamps
            aligner_result = fa_data
            results = apply_aligner_timestamps(results, aligner_result)
            _log.info("Qwen3-FA 对齐完成（并行）: %d 词", len(aligner_result))
            _fa_holder["_quality"] = "official"
    elif filter_keywords and transcription_tier in ("standard", "precise"):
        from backend.core.qwen_aligner import (
            QwenAligner, is_qwen_aligner_available, apply_aligner_timestamps,
            align_segments,
        )
        if is_qwen_aligner_available():
            aligner = None
            try:
                _log.info("Qwen3-ForcedAligner 词级对齐开始（分段模式）")
                aligner = QwenAligner()
                if aligner._model is not None:
                    aligner_result = align_segments(
                        aligner, audio_path, results, language=detected_lang,
                    )
                    if aligner_result:
                        results = apply_aligner_timestamps(results, aligner_result)
                        _log.info("Qwen3-FA 对齐完成: %d 词", len(aligner_result))
                else:
                    _log.warning("Qwen3-FA 模型未加载，跳过对齐")
            except Exception as e:
                _log.warning("Qwen3-FA 对齐失败，保留原始时间戳: %s", e)
                aligner_result = []
            finally:
                if aligner is not None and aligner._model is not None:
                    aligner.close()

    # P214: FA quality visibility — mark segments so downstream/user can see
    if use_parallel and fa_thread:
        _fa_quality = _fa_holder.get("_quality", "fallback")
    elif aligner_result is not None:
        _fa_quality = "official" if aligner_result else "fallback"
    else:
        _fa_quality = "not_attempted"
    for seg in results:
        seg._fa_quality = _fa_quality
    if _fa_quality == "fallback":
        _log.warning("⚠ 词级对齐未成功，字幕使用 Whisper 粗时间戳")

    # ── Step 5: 应用 SenseVoice 标签（在 FA 对齐之后 —— P144 修复）──
    if sv_events:
        _apply_sv_tags(results, sv_events)

    # ── Step 6: Five-layer hallucination filter ──
    if filter_keywords:
        results = filter_hallucinations(results, detected_lang,
                                        aligner_result=aligner_result,
                                        qwen_chunks=qwen_chunks)

    if not results:
        _log.error("未检测到任何语音内容")
        raise RuntimeError("未检测到任何语音内容。视频可能为纯音乐或无声")

    # ── Step 7: Speaker detection ──
    if speaker_detect:
        _log.info("说话人检测开始（自适阈值）")
        results = _detect_speaker_changes(audio_path, results)

    _log.info("transcribe() 结束: 返回 %d 段", len(results))
    return results
