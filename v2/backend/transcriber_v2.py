"""Transcription via faster-whisper + resemblyzer speaker change detection.

v1.1: VAD 默认关闭 + 三层后处理过滤 + condition_on_previous_text=False
      + resemblyzer 说话人变化检测（相邻段声纹比较）。
v1.2: 新增 logging 代码级调试日志 + RMS 三特征双信号交叉验证（P025）。
"""
import logging
import wave
import numpy as np
from faster_whisper import WhisperModel
from backend.core.vad_filter import compute_speech_ratio_via_vad

_log = logging.getLogger("transcriber")


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


# ── 过滤阈值配置（全部阈值收进一个盒子，一处修改全局生效）──
FILTER_CONFIG = {
    "rms_var":       0.0005,  # RMS 能量方差 < 此值 → 能量太平稳 → 像音乐（2026-05-21 电话音频实测校准）
    "cent_mean":     1200,    # 频谱质心均值 < 此值(Hz) → 缺高频辅音 → 像音乐
    "zcr_mean":      0.06,    # 零交叉率均值 < 此值 → 无辅音摩擦 → 像音乐（2026-05-21 电话音频实测校准）
    "kurtosis":      1.5,     # 频谱峰度(Fisher) > 此值 → 有共振峰 → 像音乐
    "nsp":           0.6,     # no_speech_prob > 此值 → Whisper 判"非人声"
    "intro_nsp":     0.2,     # L4 片头加强：nsp > 此值 且 (短/幻觉) → 丢弃
}

HALLUCINATION_KEYWORDS = (
    "音乐", "music", "♪", "♫", "[music]", "[applause]", "[laughter]",
    "掌声", "笑声", "鼓掌", "谢谢观看", "请订阅", "字幕组", "字幕由",
    "музыка", "МУЗЫКА", "аплодисменты", "смех",
    "音楽", "拍手", "笑い声",
)


class Segment:
    def __init__(self, id, start, end, text):
        self.id = id
        self.start = start
        self.end = end
        self.text = text
        self.speaker_change = False
        self.uncertain = False   # RMS 和 Whisper 意见冲突 → 字幕标 [?]
        self.zh = ""


def _compute_features(y: np.ndarray, sr: int) -> dict | None:
    """提取 4 个物理特征值，不做投票。返回 dict 或 None（太短/缺库）。

    四个信号：
    1. RMS 能量方差 — 说话能量跳来跳去（高方差），音乐平稳（低方差）
    2. 频谱质心 — 说话高频成分多（质心高），音乐质心低
    3. 零交叉率 — 说话有很多高频辅音，ZCR 高；音乐低
    4. 频谱峰度 — 音乐频谱有尖锐共振峰（峰度高），说话平坦（峰度低）
    """
    try:
        import librosa
    except ImportError:
        return None

    dur = len(y) / sr
    if dur < 0.3:
        return None

    rms = librosa.feature.rms(y=y, frame_length=512, hop_length=256)[0]
    rms_var = float(np.var(rms)) if len(rms) > 1 else 0.0

    cent = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    cent_mean = float(np.mean(cent)) if len(cent) > 0 else 0.0

    zcr = librosa.feature.zero_crossing_rate(y, frame_length=512, hop_length=256)[0]
    zcr_mean = float(np.mean(zcr)) if len(zcr) > 0 else 0.0

    kurt = 0.0
    spec = np.abs(np.fft.rfft(y))[1:]
    if len(spec) > 10:
        mean = np.mean(spec)
        std = np.std(spec)
        if std > 1e-10:
            kurt = float(np.mean((spec - mean) ** 4) / (std ** 4) - 3)

    return {"rms_var": rms_var, "cent_mean": cent_mean, "zcr_mean": zcr_mean, "kurtosis": kurt}


def _is_music_like(feats: dict, thresholds: dict | None = None,
                    is_phone: bool = False) -> bool:
    """用已算好的特征值 + 阈值做多信号多数投票。
    正常模式: 4 信号 ≥2 票判音乐。
    电话模式: 只用 RMS 方差 + 频谱峰度，2/2 全票才判音乐。
              （频谱质心和零交叉率在电话窄带上不可靠，关掉）"""
    if feats is None:
        return False
    t = thresholds or FILTER_CONFIG
    if is_phone:
        # 电话音频：质心和 ZCR 被频带压缩，不可靠 → 只用 RMS 方差 + 峰度，2/2
        votes = sum([
            feats["rms_var"] < t["rms_var"],
            feats["kurtosis"] > t["kurtosis"],
        ])
        return votes >= 2
    votes = sum([
        feats["rms_var"] < t["rms_var"],
        feats["cent_mean"] < t["cent_mean"],
        feats["zcr_mean"] < t["zcr_mean"],
        feats["kurtosis"] > t["kurtosis"],
    ])
    return votes >= 2


def _detect_phone_audio(y: np.ndarray, sr: int) -> bool:
    """检测整段音频是否为电话质量（4kHz 以上几乎无能量）。"""
    if y is None or len(y) < sr:
        return False
    spec = np.abs(np.fft.rfft(y))
    freqs = np.fft.rfftfreq(len(y), 1.0 / sr)
    above_4k = spec[freqs > 4000]
    total = np.sum(spec)
    if total == 0:
        return False
    ratio = float(np.sum(above_4k) / total)
    is_phone = ratio < 0.01
    _log.debug("电话音频检测: 4kHz以上能量=%.4f%% → %s", ratio * 100, "电话" if is_phone else "正常")
    return is_phone


def _find_dynamic_thresholds(feats_list: list[dict]) -> dict:
    """从当前视频的物理特征分布中自动找音乐/对话分界线。

    对每个特征排序 → 找相邻最大间隙 → 间隙处设阈值。
    如果分布是单峰的（全对话/全音乐），退回 FILTER_CONFIG 默认值。
    """
    if len(feats_list) < 10:
        _log.debug("动态阈值: 样本太少(%d), 使用 FILTER_CONFIG", len(feats_list))
        return dict(FILTER_CONFIG)

    def _find_gap(values, name, default):
        arr = np.sort(values)
        if len(arr) < 5:
            return default

        # 音乐值永远在低端 → 只看下半部分找间隙（上半部分全是对话，间隙无意义）
        mid = len(arr) // 2
        lower = arr[:mid]
        gaps = np.diff(lower)
        if len(gaps) == 0:
            return default
        gap_idx = int(np.argmax(gaps))

        # 单峰检测：如果下半部分的最大最小值比 < 3x，说明没有明显的音乐群
        low_ratio = lower[-1] / max(lower[0], 1e-12)
        if low_ratio < 3:
            _log.debug("动态阈值: %s 低值区单峰(ratio=%.1f), 使用默认值 %.6f", name, low_ratio, default)
            return default

        threshold = float(lower[gap_idx] + gaps[gap_idx] * 0.3)
        # 安全钳：阈值不能比默认值松（只紧不松），防止对话被误判为音乐
        threshold = min(threshold, default)
        _log.debug("动态阈值: %s gap=%.6f 默认=%.6f → 动态=%.6f (low_ratio=%.0f)",
                   name, gaps[gap_idx], default, threshold, low_ratio)
        return threshold

    rms_vals = [f["rms_var"] for f in feats_list]
    cent_vals = [f["cent_mean"] for f in feats_list]
    zcr_vals = [f["zcr_mean"] for f in feats_list]

    return {
        "rms_var":   _find_gap(rms_vals,  "rms_var",  FILTER_CONFIG["rms_var"]),
        "cent_mean": _find_gap(cent_vals, "cent_mean", FILTER_CONFIG["cent_mean"]),
        "zcr_mean":  _find_gap(zcr_vals,  "zcr_mean",  FILTER_CONFIG["zcr_mean"]),
        "kurtosis":  FILTER_CONFIG["kurtosis"],   # 不动态调
        "nsp":       FILTER_CONFIG["nsp"],
        "intro_nsp": FILTER_CONFIG["intro_nsp"],
    }


def _is_hallucination(text: str) -> bool:
    t = text.strip().lower()
    if not t:
        return True
    for kw in HALLUCINATION_KEYWORDS:
        if kw.lower() in t:
            return True
    if all(not c.isalnum() and c not in "你我他她它的了是有不在" for c in t):
        return True
    # 重复字符检测: 超过 70% 字符是重复的 → 幻觉
    if 4 < len(t) <= 30 and len(set(t)) / len(t) < 0.3:
        return True
    return False


def _detect_speaker_changes(audio_path: str, segments: list,
                             threshold: float = 0.75) -> list:
    """Compare adjacent segment voiceprints via resemblyzer.
    Sets segment.speaker_change=True where voice changes.
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
            if dur < 0.3:
                return None
            y, _sr = librosa.load(audio_path, sr=16000, offset=start, duration=dur)
            if len(y) < 16000 * 0.3:
                return None
            wav = preprocess_wav(y, source_sr=16000)
            return encoder.embed_utterance(wav)
        except Exception:
            return None

    prev_emb = None
    changes = 0
    for seg in segments:
        emb = _get_emb(seg.start, seg.end)
        if emb is not None and prev_emb is not None:
            sim = float(np.dot(emb, prev_emb))
            if sim < threshold:
                seg.speaker_change = True
                changes += 1
                _log.debug("说话人变化: seg[%d] start=%.2f sim=%.3f < %.2f",
                           seg.id, seg.start, sim, threshold)
        if emb is not None:
            prev_emb = emb

    _log.info("说话人检测完成: %d 处变化 (threshold=%.2f)", changes, threshold)
    if changes > 0:
        _log.info("检测到 %d 处说话人变化", changes)
    return segments


def transcribe(audio_path: str, model_size: str = "medium",
               source_lang: str = "auto", skip_vad: bool = False,
               vad_threshold: float = 0.0,
               no_speech_threshold: float = 0.6,
               filter_keywords: bool = True,
               speaker_detect: bool = True,
               speaker_threshold: float = 0.75,
               progress_callback=None) -> list:
    """Transcribe audio, return list of Segment."""
    _log.info("transcribe() 开始: audio=%s model=%s lang=%s",
              audio_path, model_size, source_lang)
    if not skip_vad:
        _log.info("VAD 语音占比检测中…")
        ratio = compute_speech_ratio_via_vad(audio_path)
        _log.debug("VAD 语音占比: %s%%", f"{ratio:.1f}" if ratio else "N/A")
        if ratio is not None:
            _log.info("语音占比: %.1f%%", ratio)
            if ratio < 20:
                r = input(f"       ⚠️ 语音极低({ratio:.0f}%)，可能为纯音乐。继续？[y/N] ")
                if r.lower() != "y":
                    raise RuntimeError("用户取消")
            elif ratio < 40:
                _log.warning("非语音内容较多。如对话被过滤可用 --vad-threshold 0.4 启用内置 VAD")

    _log.info("加载 Whisper %s 模型…", model_size)
    # 首次运行时需从 HuggingFace 下载模型文件（medium ~1.5GB, large-v3 ~3GB）。
    # 通过 progress_callback(-1) 告知前端正在下载，避免用户以为卡死。
    if progress_callback:
        progress_callback(-1)
    model = WhisperModel(model_size, device="auto", compute_type="auto")
    _log.debug("模型已加载: %s (device=cpu compute_type=int8)", model_size)

    # 加载完整音频供 _is_music_like 按段切片（wave 标准库，零新依赖）
    is_phone = False
    try:
        wf = wave.open(audio_path, "rb")
        _full_audio = np.frombuffer(
            wf.readframes(wf.getnframes()), dtype=np.int16
        ).astype(np.float32) / 32768.0
        _full_sr = wf.getframerate()
        wf.close()
        _log.debug("完整音频已加载: %d 采样 @ %d Hz", len(_full_audio), _full_sr)
        is_phone = _detect_phone_audio(_full_audio, _full_sr)
    except Exception:
        _full_audio = None
        _full_sr = 16000
        _log.warning("音频加载失败，降级为纯 nsp 单信号过滤")

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
        condition_on_previous_text=False,
        log_prob_threshold=-1.0,
        word_timestamps=True,
        **vad_kwargs
    )
    _log.info("检测语言: %s (概率 %.2f)", info.language, info.language_probability)
    _log.info("检测语言: %s (概率 %.2f)", info.language, info.language_probability)

    # --- Phase 1: Whisper 转录 + 顺手收集物理特征（一趟完成）---
    raw_items = []  # [(seg, features_dict | None), ...]
    n_total = 0

    for seg in segments_iter:
        text = seg.text.strip()
        n_total += 1
        feats = None
        if _full_audio is not None and seg.end > seg.start:
            start_samp = max(0, int(seg.start * _full_sr))
            end_samp = min(len(_full_audio), int(seg.end * _full_sr))
            chunk = _full_audio[start_samp:end_samp]
            if len(chunk) > 0:
                feats = _compute_features(chunk, _full_sr)
        raw_items.append((seg, feats))
        if progress_callback and n_total % 10 == 0:
            progress_callback(n_total)

    # --- Phase 2: 从当前视频分布动态算阈值 ---
    all_feats = [f for _, f in raw_items if f is not None]
    thresholds = _find_dynamic_thresholds(all_feats)
    nsp_threshold = no_speech_threshold if no_speech_threshold != 0.6 else thresholds["nsp"]
    _log.debug("最终阈值: nsp=%.2f rms=%.6f cent=%.0f zcr=%.4f kurt=%.1f",
               nsp_threshold, thresholds["rms_var"], thresholds["cent_mean"],
               thresholds["zcr_mean"], thresholds["kurtosis"])

    # =================================================================
    # Phase 2.5: 片头净化重分段器（方案 D + 方案 F 组合管道）
    #
    # 场景：large-v3 等大模型会将片头音乐（0-5s）和第一句对话（5-8s）合并成
    #       一个长 segment。nsp 说"有人声"（段内确有对话），物理特征说"像音乐"
    #       （段内确有片头音乐）→ 双法官冲突 → 原逻辑标 [?] 保留。
    #
    # 本层在过滤之前预处理：仅对前 3 段 + nsp 判人声 + 物理判音乐 的合并段触发。
    #   有词戳 → 方案 D：用 Whisper 自己的词级时间戳修正 seg.start
    #   无词戳 → 方案 F：用 RMS 滑动窗口找音频能量突变点，切除音乐前缀
    #
    # 关键约束：使用 ._replace() 而非 obj.attr = value（Segment 是 namedtuple，不可变）。
    #           切分后对新段重新提取物理特征，确保下游过滤拿到干净的信号。
    # =================================================================
    purified_items = []
    _purify_seen = 0

    for seg, feats in raw_items:
        no_speech_prob = getattr(seg, "no_speech_prob", 0.0)
        nsp_high = no_speech_prob > nsp_threshold
        sounds_like_music = _is_music_like(feats, thresholds, is_phone)

        # 仅片头前 3 段 + nsp 判人声 + 物理判音乐 → 典型的合并型片头
        if _purify_seen < 3 and (not nsp_high and sounds_like_music):

            # ── 主力：方案 D（词级时间戳修剪）──
            if hasattr(seg, "words") and seg.words and len(seg.words) > 0:
                voice_start = seg.words[0].start
                if voice_start - seg.start > 1.0:
                    _log.info(
                        "[方案D] 片头合并段: 时间轴 %.2fs → %.2fs "
                        "(第一个词: %r, 剪除 %.2fs 音乐前缀)",
                        seg.start, voice_start,
                        seg.words[0].word,
                        voice_start - seg.start,
                    )

                    # 使用 ._replace() 安全更新 namedtuple 的 start 字段
                    seg = seg._replace(start=voice_start)

                    # 重新提取缩进后时间区间的物理特征
                    if _full_audio is not None:
                        start_samp = max(0, int(seg.start * _full_sr))
                        end_samp = min(len(_full_audio), int(seg.end * _full_sr))
                        chunk = _full_audio[start_samp:end_samp]
                        feats = (
                            _compute_features(chunk, _full_sr)
                            if len(chunk) > 0 else None
                        )

                    purified_items.append((seg, feats))
                    _purify_seen += 1
                    continue

            # ── 兜底：方案 F（局部波形突变点切割）──
            transition_time = _find_audio_mutation_point(
                seg.start, seg.end, _full_audio, _full_sr
            )
            if transition_time and transition_time > seg.start + 0.5:
                _log.info(
                    "[方案F] 片头合并段: %.2fs → %.2fs "
                    "(RMS 突变点 %.2fs) → 切割并丢弃 %.2fs-%.2fs 音乐前缀",
                    seg.start, seg.end,
                    transition_time,
                    seg.start, transition_time,
                )

                # 用 ._replace() 生成仅保留对话部分的 segment
                # 音乐前缀直接在预处理层丢弃，不入下游过滤
                dialogue_seg = seg._replace(start=transition_time)

                # 重新提取纯人声区间的物理特征
                if _full_audio is not None:
                    start_samp = max(0, int(transition_time * _full_sr))
                    end_samp = min(len(_full_audio), int(seg.end * _full_sr))
                    chunk = _full_audio[start_samp:end_samp]
                    dialogue_feats = (
                        _compute_features(chunk, _full_sr)
                        if len(chunk) > 0 else None
                    )
                else:
                    dialogue_feats = None

                purified_items.append((dialogue_seg, dialogue_feats))
                _purify_seen += 1
                continue

        # 正常段：no-op，原样放行
        purified_items.append((seg, feats))
        _purify_seen += 1

    # 用净化后的 items 替换原始 raw_items，无缝对接到下游五层过滤
    raw_items = purified_items
    # =================================================================

    # --- Phase 3: 用动态阈值过滤（物理特征已在 Phase 1 算好，零额外计算）---
    results = []
    dropped_no_speech = 0
    dropped_keyword = 0
    dropped_intro = 0
    disputed = 0

    for seg, feats in raw_items:
        text = seg.text.strip()
        seen_count = len(results) + dropped_no_speech + dropped_keyword + dropped_intro + disputed
        no_speech_prob = getattr(seg, "no_speech_prob", 0.0)
        avg_logprob = getattr(seg, "avg_logprob", 0.0)

        # L1: 双信号交叉 — nsp + 物理特征（特征已在 Phase 1 算好）
        nsp_high = no_speech_prob > nsp_threshold
        sounds_like_music = _is_music_like(feats, thresholds, is_phone)
        unc = False

        if nsp_high and sounds_like_music:
            _log.debug("seg[%d] DROP L1a  start=%.2f end=%.2f nsp=%.4f music=1  text=%r",
                       seen_count, seg.start, seg.end, no_speech_prob, text[:80])
            dropped_no_speech += 1
            continue
        elif nsp_high and not sounds_like_music:
            unc = True
            _log.debug("seg[%d] KEEP [?] start=%.2f end=%.2f nsp=%.4f music=0 DISPUTE  text=%r",
                       seen_count, seg.start, seg.end, no_speech_prob, text[:80])
        elif not nsp_high and sounds_like_music:
            unc = True
            _log.debug("seg[%d] KEEP [?] start=%.2f end=%.2f nsp=%.4f music=1 DISPUTE  text=%r",
                       seen_count, seg.start, seg.end, no_speech_prob, text[:80])

        if filter_keywords and _is_hallucination(text):
            _log.debug("seg[%d] DROP L2   start=%.2f end=%.2f nsp=%.4f  text=%r",
                       seen_count, seg.start, seg.end, no_speech_prob, text[:80])
            dropped_keyword += 1
            continue

        if seen_count < 3:
            if (len(text) < 4 or (filter_keywords and _is_hallucination(text))) and no_speech_prob > thresholds["intro_nsp"]:
                _log.debug("seg[%d] DROP L4   start=%.2f end=%.2f nsp=%.4f  text=%r",
                           seen_count, seg.start, seg.end, no_speech_prob, text[:80])
                dropped_intro += 1
                continue

        if len(text) < 2:
            _log.debug("seg[%d] DROP L5   start=%.2f end=%.2f nsp=%.4f  text=%r",
                       seen_count, seg.start, seg.end, no_speech_prob, text[:80])
            continue

        _log.debug("seg[%d] KEEP      start=%.2f end=%.2f nsp=%.4f  text=%r",
                   seen_count, seg.start, seg.end, no_speech_prob, text[:80])
        s = Segment(
            id=len(results),
            start=round(seg.start, 2),
            end=round(seg.end, 2),
            text=text
        )
        if unc:
            s.uncertain = True
            disputed += 1
        results.append(s)

    total_dropped = dropped_no_speech + dropped_keyword + dropped_intro
    _log.info("过滤摘要: 总=%d 保留=%d 丢弃=%d (L1a=%d L2=%d L4=%d) 存疑=%d",
              n_total, len(results), total_dropped, dropped_no_speech, dropped_keyword, dropped_intro, disputed)
    if total_dropped > 0 or disputed > 0:
        parts = [f"非语音 {dropped_no_speech}", f"关键词 {dropped_keyword}", f"片头 {dropped_intro}"]
        if disputed > 0:
            parts.append(f"存疑 {disputed}")
        _log.info("过滤: %d 段 (%s)", total_dropped, ' / '.join(parts))

    if not results:
        _log.error("未检测到任何语音内容")
        raise RuntimeError("未检测到任何语音内容。视频可能为纯音乐或无声")

    if speaker_detect:
        _log.info("说话人检测开始 (threshold=%.2f)", speaker_threshold)
        results = _detect_speaker_changes(audio_path, results, threshold=speaker_threshold)

    _log.info("transcribe() 结束: 返回 %d 段", len(results))
    return results


def _find_audio_mutation_point(
    start: float,
    end: float,
    full_audio,
    sr: int,
    window_sec: float = 0.5,
) -> float | None:
    """方案 F 核心：用滑动窗口扫描 RMS 能量突变点。

    将 [start, end] 区间按 window_sec 切分为小块，逐块计算 RMS 能量。
    以开头若干窗口的 RMS 均值作为音乐基线，向后扫描——
    找到第一块 RMS 突然增大 3 倍以上的位置，即音乐→人声的边界。

    Args:
        start: 段起始时间（秒）
        end: 段结束时间（秒）
        full_audio: 完整音频的 float32 numpy 数组
        sr: 采样率
        window_sec: 滑动窗口宽度（秒），默认 0.5

    Returns:
        突变点时间（秒），未找到返回 None。
    """
    import numpy as np

    duration = end - start
    if duration < 2.0:
        return None

    start_samp = max(0, int(start * sr))
    end_samp = min(len(full_audio), int(end * sr))
    chunk = full_audio[start_samp:end_samp]
    if len(chunk) < sr * 1.0:
        return None

    win_samples = int(window_sec * sr)
    num_windows = len(chunk) // win_samples
    if num_windows < 3:
        return None

    rms_vals = []
    for i in range(num_windows):
        w = chunk[i * win_samples : (i + 1) * win_samples].astype(np.float32)
        rms = float(np.sqrt(np.mean(w ** 2)) + 1e-12)
        rms_vals.append(rms)

    # 以前 3 个窗口的 RMS 均值作为音乐基线
    baseline = np.mean(rms_vals[:3]) if rms_vals[0] > 1e-10 else rms_vals[0]

    # 从第 2 个窗口开始扫描：RMS 突然增大 3 倍 → 音乐→对话突变点
    for i in range(1, len(rms_vals)):
        if rms_vals[i] > baseline * 3.0:
            return start + i * window_sec

    return None
