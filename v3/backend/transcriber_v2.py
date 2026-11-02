"""Transcription pipeline with two modes.

Language (G11): decided exactly ONCE, before any engine starts.
  multi-point sampling (3/5/7 positions) → single locked LanguageDecision
  → passed BY VALUE to every consumer; no rebindable language variable exists
  → split vote resolved by majority (2026-08-29); only zero evidence blocks delivery

Standard mode:
  LanguageDecision → route by locked language (no probability threshold)
  → SenseVoice sound classification + P275 voice timeline
  → L1 dual-signal filter (SenseVoice × FA) → assembly → L4/L5/L3
  → artifact language consistency gate → deliver

Precise mode:
  LanguageDecision → Whisper ∥ Qwen3-ASR (parallel, dual engine, both receive it)
  → SenseVoice sound classification + P275 voice timeline
  → L1 dual-signal filter → assembly → L2 cross-validation → L4/L5/L3
  → artifact language consistency gate → deliver

FA source: Mac (MLX) = Qwen internal; Windows (PyTorch) = external Qwen3-FA.
Speaker detection (resemblyzer) runs after filtering in both modes.
"""
import bisect
import hashlib
import logging
import threading
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
import numpy as np
from backend.core.whisper_runner import (
    transcribe_whisper,
    release_model as release_whisper_model,
    should_parallel_asr,
    sample_language_multipoint,
    LanguageEvidenceMissing,
)
from backend.core.vad_filter import compute_speech_ratio_via_vad
from backend.core.hallucination_filter import (
    filter_hallucinations, find_impossible_instant_runs, time_overlap,
    _has_timestamp_segments, _qwen_is_reliable,
)
from backend.core.sound_map import canonical_json_bytes
# P464：块 + 完整性状态的载体。**必须顶层导入**——测试会用假模块替换
# `sys.modules['backend.core.qwen_asr_runner']`，函数内 import 会拿不到这个类。
from backend.core.qwen_asr_runner import QwenAsrOutcome

_log = logging.getLogger("transcriber")

_SVFA_MIN_RAM_GB = 8

TRANSCRIPTION_WORD_SOURCES_BY_PRIMARY = {
    "qwen": frozenset({
        "qwen_word_timestamps",
        "external_forced_alignment",
    }),
    "whisper": frozenset({"whisper_word_timestamps"}),
    "dual_failed": frozenset({"emergency_combined"}),
}


def should_parallel_svfa(tier: str, ram_gb: float) -> bool:
    """SV+FA parallelism: any tier with enough RAM (decoupled from Qwen)."""
    return ram_gb >= _SVFA_MIN_RAM_GB


def should_qwen_pipeline(tier: str, ram_gb: float) -> bool:
    """Qwen ASR pipeline: standard and precise tiers with enough RAM."""
    return tier in ("standard", "precise") and ram_gb >= 16


def should_load_whisper(tier: str) -> bool:
    """Whisper runs as a full transcription engine in precise mode."""
    return tier == "precise"


QWEN_PRIMARY_LANGS = {"en", "ja", "ko", "zh"}


def choose_standard_primary_engine(detected_lang: str) -> str:
    """按**已锁定的语言**选标准模式主引擎。

    G11：这里原本有一个 `STANDARD_ROUTE_MIN_PROB = 0.70` 的把握度门槛，
    它会用一个数字否定已经算出来的语言——事故 fc441fb713 的第二刀。
    **删除而非调低**：同一支素材七点实测的 en 把握度从 0.3319 到 0.9421 都有，
    同一门槛换一个采样位置就得到相反路由，问题不在数字大小而在这个结构。
    把握度仍然作为证据保留在 LanguageDecision 里，只是不再参与路由。
    """
    if not detected_lang:
        return "whisper"
    return "qwen" if detected_lang in QWEN_PRIMARY_LANGS else "whisper"


def log_sensevoice_unavailable() -> None:
    _log.warning(
        "SenseVoice 不可用：当前 Python 环境无法 import funasr，"
        "跳过声音分类和人声地图"
    )


def choose_transcription_primary(
    transcription_tier: str,
    standard_primary: str | None,
    detected_lang: str,
    qwen_chunks: list | None,
) -> tuple[str, bool]:
    """Return (primary_engine, keep_qwen_chunks_for_l2)."""
    if transcription_tier == "standard":
        return ("qwen", False) if standard_primary == "qwen" else ("whisper", False)
    if detected_lang in QWEN_PRIMARY_LANGS and qwen_chunks:
        return "qwen", True
    return "whisper", True


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
        # 插入④ 施工 B：AI 拆句每一刀的「刀前词｜刀后词」{刀的位置: (刀前词, 刀后词) 或 None}。
        # 只活在内存里（拆句 → 排版），不落盘；没走 AI 拆句的句子是 None。
        self.ai_cut_words = None


@dataclass
class TranscriptionReceipt:
    routed_primary: str
    actual_primary: str
    word_source: str
    source_text: str
    source_words: list
    source_fingerprint: str = ""
    # G2/B9+B10：副引擎的完整转录，以及「这份参照能不能用」。两者都在转录阶段
    # 一次算好，润色阶段只读。刻意分成两个字段：「有没有文本」和「能不能信」
    # 是两件事，合成一个就分不开了（副引擎复读时有文本但不可信）。
    # **不进 source_fingerprint**——那是主引擎收据的身份，副引擎的东西掺进去
    # 会让所有既有转录存档指纹不符、直接抛异常。
    reference_text: str = ""
    reference_usable: bool = False
    # ── G11：语言必须留证。出事之后要能回溯「决定是什么、引擎实际收到什么」。 ──
    # `language_decision` 是冻结的 LanguageDecision（含完整采样证据）；
    # `engine_received_language` 是 {引擎名: 实收语言}，用来验证消费端边界。
    language_decision: object = None
    engine_received_language: dict = field(default_factory=dict)
    # G11-B 第二轮：**本次哪些引擎的文字进了正式链路**。
    # 这是缓存复用检查的唯一依据——只看 `actual_primary` 会漏掉一整类：
    # 精准模式下 Qwen **无条件运行**（没有语言条件），即使主稿最终来自 Whisper，
    # Qwen 的文字仍会进五层过滤、影响最终字幕。它不是主引擎，却是正式链路的一部分。
    engines_in_text_chain: tuple = ()
    # 交付前产物一致性检查的结果。"ok" = 已跑且通过——
    # 空字符串意味着**根本没跑**，那是报警器没接线，和"通过"完全是两回事。
    artifact_language_check: str = ""
    # 转录请求指纹：音频身份 + 转录前已知输入 + 转录缓存合同版本。
    # 与 source_fingerprint 是两回事——后者只标识主引擎产出的那份词流。
    transcription_request_fingerprint: str = ""
    # 观察证据（**只留证，不参与裁决**）：
    #   voice_gaps —— 复用 _make_voice_timeline 的人声缺口结果（4-3）
    #   sound      —— SenseVoice 原始事件与运行统计（4-4）
    observation_evidence: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.routed_primary not in {"qwen", "whisper", "dual_failed"}:
            raise ValueError("主引擎收据路由无效")
        if self.actual_primary not in {"qwen", "whisper", "dual_failed"}:
            raise ValueError("主引擎收据实际引擎无效")
        if self.word_source not in TRANSCRIPTION_WORD_SOURCES_BY_PRIMARY[
            self.actual_primary
        ]:
            raise ValueError("主引擎收据词流来源无效")
        self.source_words = [
            {
                "text": str(word.get("text") or word.get("word") or ""),
                "start": float(word["start"]),
                "end": float(word["end"]),
            }
            for word in self.source_words
        ]
        normalized_text = "".join(
            character.casefold()
            for character in unicodedata.normalize("NFKC", str(self.source_text))
            if unicodedata.category(character)[0] in {"L", "N"}
        )
        normalized_words = "".join(
            character.casefold()
            for word in self.source_words
            for character in unicodedata.normalize("NFKC", word["text"])
            if unicodedata.category(character)[0] in {"L", "N"}
        )
        if normalized_text != normalized_words:
            raise ValueError("主引擎收据原词不守恒")
        payload = {
            "source_text": self.source_text,
            "source_words": self.source_words,
            "routed_primary": self.routed_primary,
            "actual_primary": self.actual_primary,
            "word_source": self.word_source,
        }
        encoded = canonical_json_bytes(payload)
        expected = hashlib.sha256(encoded).hexdigest()
        if self.source_fingerprint and self.source_fingerprint != expected:
            raise ValueError("主引擎收据指纹不符")
        self.source_fingerprint = expected


@dataclass
class TranscriptionEnvelope:
    segments: list
    receipt: TranscriptionReceipt

    def __iter__(self):
        return iter(self.segments)

    def __len__(self):
        return len(self.segments)

    def __getitem__(self, item):
        return self.segments[item]

    def __setitem__(self, item, value):
        self.segments[item] = value


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


def _build_voice_timeline(sv_events, audio_duration):
    """Convert SenseVoice events into voice/non-voice region lists.

    Merges overlapping or adjacent (gap <= 1s) Speech events.
    """
    if audio_duration <= 0:
        return {"voice_regions": [], "non_voice_regions": [], "voice_ratio": 0.0}

    speech = sorted(
        [(ev["start_s"], ev["end_s"]) for ev in (sv_events or []) if ev.get("event") == "Speech"],
        key=lambda x: x[0],
    )

    merged = []
    for start, end in speech:
        if merged and start <= merged[-1][1] + 1.0:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    non_voice = []
    prev = 0.0
    for start, end in merged:
        if start > prev:
            non_voice.append((prev, start))
        prev = end
    if prev < audio_duration:
        non_voice.append((prev, audio_duration))

    voice_total = sum(e - s for s, e in merged)
    return {
        "voice_regions": merged,
        "non_voice_regions": non_voice,
        "voice_ratio": voice_total / audio_duration,
    }


def _segment_center(seg) -> float:
    return (getattr(seg, "start", 0.0) + getattr(seg, "end", 0.0)) / 2.0


def _region_containing_time(regions, point: float):
    for start, end in regions or []:
        if start <= point <= end:
            return (start, end)
    return None


def _segment_voice_region(seg, voice_timeline):
    if not voice_timeline:
        return None
    return _region_containing_time(
        voice_timeline.get("voice_regions", []), _segment_center(seg))


def _segment_in_non_voice(seg, voice_timeline) -> bool:
    if not voice_timeline:
        return False
    return _region_containing_time(
        voice_timeline.get("non_voice_regions", []), _segment_center(seg)) is not None


def _same_voice_region(left, right, voice_timeline) -> bool:
    left_region = _segment_voice_region(left, voice_timeline)
    if not left_region:
        return False
    return left_region == _segment_voice_region(right, voice_timeline)


def summarize_voice_timeline_quality(segments, voice_timeline, lang="auto"):
    """Summarize subtitle coverage over the SenseVoice voice timeline."""
    voice_regions = list((voice_timeline or {}).get("voice_regions", []))
    non_voice_short = 0
    for seg in segments or []:
        if _is_short_fragment(seg, lang) and _segment_in_non_voice(seg, voice_timeline):
            non_voice_short += 1

    uncovered = []
    for start, end in voice_regions:
        covered = False
        for seg in segments or []:
            overlap = max(
                0.0,
                min(getattr(seg, "end", 0.0), end) - max(getattr(seg, "start", 0.0), start),
            )
            if overlap > 0:
                covered = True
                break
        if not covered:
            uncovered.append((start, end))

    longest = None
    if uncovered:
        longest = max(uncovered, key=lambda region: region[1] - region[0])

    return {
        "voice_region_count": len(voice_regions),
        "uncovered_voice_region_count": len(uncovered),
        "non_voice_short_fragment_count": non_voice_short,
        "longest_uncovered_voice_region": longest,
    }


def _load_speaker_change_dependencies():
    try:
        from resemblyzer import VoiceEncoder, preprocess_wav
        import librosa
    except ImportError:
        _log.warning("未安装 resemblyzer/librosa，跳过说话人检测")
        return None
    return VoiceEncoder, preprocess_wav, librosa


def _create_voice_encoder(VoiceEncoder):
    try:
        return VoiceEncoder()
    except Exception:
        return None


def _get_speaker_embedding(audio_path, start, end, encoder, preprocess_wav, librosa):
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


def _collect_speaker_similarities(audio_path, segments, encoder, preprocess_wav, librosa):
    prev_emb = None
    sims = []  # (segment_index, similarity_score)
    for seg in segments:
        emb = _get_speaker_embedding(audio_path, seg.start, seg.end, encoder, preprocess_wav, librosa)
        if emb is not None and prev_emb is not None:
            sim = float(np.dot(emb, prev_emb))
            sims.append((seg, sim))
        if emb is not None:
            prev_emb = emb
    return sims


def _find_speaker_change_threshold(scores):
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
    return best_thr


def _apply_speaker_change_threshold(sims, best_thr):
    changes = 0
    for seg, sim in sims:
        if sim < best_thr:
            seg.speaker_change = True
            changes += 1
    return changes


def _clear_speaker_changes(sims):
    for seg, _ in sims:
        seg.speaker_change = False


def _speaker_gap_check_allows_split(scores, sims):
    sorted_all = np.sort(scores)
    score_gaps = np.diff(sorted_all)
    gap_ratio = 0.0

    if len(score_gaps) >= 3:
        median_gap = float(np.median(score_gaps))
        max_gap = float(score_gaps.max())
        if median_gap > 1e-6:
            gap_ratio = max_gap / median_gap
            if gap_ratio < 3.0:
                _log.info("说话人检测: 间隙检测未通过（%.1f倍 < 3倍），判定语气波动，全部取消", gap_ratio)
                _clear_speaker_changes(sims)
                return False, gap_ratio
    return True, gap_ratio


def _speaker_group_gap_allows_split(scores, best_thr, sims):
    group_gap = 0.0
    above = [s for s in scores if s >= best_thr]
    below = [s for s in scores if s < best_thr]
    if above and below:
        group_gap = float(np.mean(above) - np.mean(below))
        if group_gap < 0.15:
            _log.info("说话人检测: 安全钳未通过（组间距%.3f < 0.15），判定语气波动，全部取消", group_gap)
            _clear_speaker_changes(sims)
            return False, group_gap
    return True, group_gap


def _detect_speaker_changes(audio_path: str, segments: list) -> list:
    """Compare adjacent segment voiceprints via resemblyzer.
    Sets segment.speaker_change=True where voice changes.
    Threshold is auto-detected from score distribution — no magic number.
    """
    deps = _load_speaker_change_dependencies()
    if deps is None:
        return segments
    VoiceEncoder, preprocess_wav, librosa = deps

    encoder = _create_voice_encoder(VoiceEncoder)
    if encoder is None:
        return segments

    sims = _collect_speaker_similarities(audio_path, segments, encoder, preprocess_wav, librosa)
    if len(sims) < 2:
        _log.info("说话人检测: 有效相似度不足，跳过")
        return segments

    if len(segments) < 5:
        _log.info("说话人检测: 段数不足5（%d段），跳过", len(segments))
        return segments

    scores = np.array([s for _, s in sims], dtype=np.float64)
    score_range = float(scores.max() - scores.min())
    score_std = float(scores.std())

    if score_range < 0.01 or score_std < score_range * 0.15:
        _log.info("说话人检测: 单人（std=%.3f, range=%.3f），无横杠", score_std, score_range)
        return segments

    best_thr = _find_speaker_change_threshold(scores)
    changes = _apply_speaker_change_threshold(sims, best_thr)

    split_allowed, gap_ratio = _speaker_gap_check_allows_split(scores, sims)
    if not split_allowed:
        return segments

    split_allowed, group_gap = _speaker_group_gap_allows_split(scores, best_thr, sims)
    if not split_allowed:
        return segments

    _log.info("说话人检测完成: %d 处变化 (阈值=%.3f, 间隙比=%.1f, 组间差=%.3f)",
              changes, best_thr, gap_ratio, group_gap)
    return segments


_SENTENCE_ENDERS = ("。", "！", "？", ".", "!", "?", "…")
_JA_STRONG_ENDERS = ("よ", "ね", "な", "わ", "ぞ", "ぜ", "か", "だ", "ます", "たい")
_JA_WEAK_ENDERS = ("の", "さ", "で")
_JA_STRONG_ENDER_GAP = 0.15
_JA_WEAK_ENDER_GAP = 0.3


def _flatten_qwen_words(qwen_chunks):
    words = []
    for chunk in (qwen_chunks or []):
        if not isinstance(chunk, dict):
            continue
        for w in chunk.get("segments") or []:
            text = w.get("text", "").strip()
            if text:
                words.append({
                    "text": text,
                    "start": w.get("start", 0.0),
                    "end":   w.get("end", 0.0),
                })
    words.sort(key=lambda w: w["start"])
    return words


def _flatten_primary_segment_words(segments):
    words = []
    for segment in segments or []:
        for raw in getattr(segment, "words", None) or []:
            text = str(raw.get("text") or raw.get("word") or "").strip()
            if not text:
                continue
            words.append({
                "text": text,
                "start": float(raw["start"]),
                "end": float(raw["end"]),
            })
    words.sort(key=lambda word: word["start"])
    return words


def _primary_source_text(primary_engine, qwen_chunks, whisper_segments, lang):
    if primary_engine == "qwen":
        parts = [
            str(chunk.get("text") or "").strip()
            for chunk in qwen_chunks or []
            if isinstance(chunk, dict) and str(chunk.get("text") or "").strip()
        ]
    elif primary_engine == "whisper":
        parts = [
            str(getattr(segment, "text", "") or "").strip()
            for segment in whisper_segments or []
            if str(getattr(segment, "text", "") or "").strip()
        ]
    else:
        return "".join(
            str(word.get("text") or word.get("word") or "")
            for word in _flatten_primary_segment_words(whisper_segments)
        ) if lang in ("ja", "zh", "ko") else " ".join(
            str(word.get("text") or word.get("word") or "")
            for word in _flatten_primary_segment_words(whisper_segments)
        )
    joiner = "" if lang in ("ja", "zh", "ko") else " "
    return joiner.join(parts).strip()


def _delivery_source_text(segments, lang) -> str:
    """正式交付词流的正文：**从过滤后的段逐词拼回**，与 `source_words` 同源。

    G12 第二批段二新增。**不要用 `_primary_source_text` 代替它。**

    `_primary_source_text` 的 qwen 分支读的是 `qwen_chunks`——**过滤之前**的东西，
    正是本段要消灭的那份。它唯一正确的分支是 `else`（原为 `dual_failed` 专用），
    但要走到那里必须谎报 `primary_engine="dual_failed"`，并把过滤后的通用段列表
    塞进一个名叫 `whisper_segments` 的形参。能跑通，但它在撒谎——
    下一个人读到会以为主引擎真的是 dual_failed。

    与 `_flatten_primary_segment_words` 严格同源：两者都只读 `segment.words`，
    因此 `TranscriptionReceipt` 的守恒检查恒成立。**改动其中一个必须同时改另一个**
    ——守恒对不上是硬崩（`ValueError("主引擎收据原词不守恒")`），没有中间态。
    """
    joiner = "" if lang in ("ja", "zh", "ko") else " "
    return joiner.join(
        str(word.get("text") or word.get("word") or "")
        for word in _flatten_primary_segment_words(segments)
    ).strip()


class DeliveryStreamContamination(RuntimeError):
    """裁决为 drop 的词出现在了正式交付词流里。

    这正是本批要修的原缺陷的形状：L5 删掉了 13.886 秒那条 `Oh`，
    成品 SRT 第 2 条却原样复活。**报警器必须比缺陷活得久。**
    """


def _alnum_only(text: str) -> str:
    """只留字母与数字，与 `TranscriptionReceipt` 的守恒检查同一套规则。"""
    return "".join(
        ch.casefold()
        for ch in unicodedata.normalize("NFKC", str(text or ""))
        if unicodedata.category(ch)[0] in {"L", "N"}
    )


def _assert_delivery_not_silently_empty(segments, source_words) -> None:
    """交付前闸门：存活段还有文字时，正式交付词流不得是空的。

    **要防的真实故障**：`_delivery_source_text` 与 `_flatten_primary_segment_words`
    都只读 `segment.words`。段上没有 `words` 时两者会**一起变成空**，
    于是收据的守恒检查平凡通过（空 == 空），**整份转录静默变成空内容**。
    改动前那条路是响亮失败的（正文来自段文字、词流为空 → 守恒当场抛错），
    这道闸门把那份响亮还回来。**静默比崩溃坏得多。**

    生产可达：`_whisper_output_to_segments:1248` 是条件赋值（`if _words is not None`），
    Whisper 不给词级时间戳时段上就没有 `words`。

    ⚠️ **本函数刻意不检查「段文字与词流逐字相符」**（2026-09-03 闸门 2 真跑撤回）。
    第一版这么查了，把真实的 30 分钟任务整条拦停：实测 23652 vs 23645，差 7 个字母数字。
    根因是**那个不变量本来就不成立**，不是缺陷：

      1. `_assemble_qwen_words_on_whisper_boundaries:1134-1136` 在某个 Whisper 段
         没有任何 Qwen 词映射过来时，拿 Whisper 的文字造一个**不带词**的段；
      2. `merge_short_subtitle_fragments:721-726` 会把那种段并进相邻的带词段——
         **文字接上了，词一个没加**。

    那些 Whisper 兜底文字**从来就不进交付流**（成品字幕由 Qwen 词流断句而来），
    这是既有设计不是回归。要求它们被词流覆盖是错的。
    **接管者**：「静默变空」这条——那才是当初真正要防的故障形状。
    """
    if not source_words and any(
        str(getattr(seg, "text", "") or "").strip() for seg in segments or []
    ):
        raise DeliveryStreamContamination(
            "存活段仍有文字，正式交付词流却是空的——"
            "整份转录会静默变成空内容。最常见的原因是所有段都没有词级时间戳"
        )


def _assert_dropped_text_absent(dropped_segments, source_words) -> None:
    """交付前闸门：被裁决删除的词，不得出现在正式交付词流里。

    比对的是**词身份**（文字 + 起始时间），不是文字本身——同一个词在别处
    合法出现是常态（`the` 在一部片子里出现几百次），只有「同一个词、同一个起始时间」
    才叫复活。
    """
    if not dropped_segments:
        return

    def _key(word):
        return (
            str(word.get("text") or word.get("word") or ""),
            round(float(word.get("start", 0.0)), 3),
        )

    delivered = {_key(word) for word in source_words or []}
    for seg in dropped_segments:
        for word in getattr(seg, "words", None) or []:
            if _key(word) in delivered:
                raise DeliveryStreamContamination(
                    f"裁决为删除的词出现在正式交付词流里："
                    f"{word.get('text')!r} @ {word.get('start')}s"
                    f"（原因 {getattr(seg, 'filter_reason', None)!r}）"
                )


def _drop_impossible_instant_runs(words):
    """摘掉物理上不可能的同词游程，返回新的词流。

    **整条都要摘时保留原样。** 这不是保守，是一条硬边界：
    `_finalize_routing_after_alignment:1460` 的早退条件是

        if route.actual_primary != "qwen" or route.source_words:
            return route

    它靠 `source_words` **非空**来早退，而本函数跑在它**之前**。
    把词流摘成空会让早退失效，函数继续往下走，
    **把 `actual_primary` 静默改成 whisper 或 dual_failed**
    ——路由决策被幻觉过滤器当成副作用改掉了。

    整片坍缩确实存在（P148：任务 `97c57255ec` 整片重复
    `1056234897 нет ты меня прости`），那种输入交给既有的坍缩检测
    `_qwen_is_reliable` 与下游闸门处理，不在这里悄悄换引擎。
    """
    if not words:
        return words
    impossible = find_impossible_instant_runs(words)
    if not impossible:
        return words
    if len(impossible) >= len(words):
        _log.warning(
            "词级游程判据要摘掉全部 %d 个词——整片疑似坍缩。"
            "保留原样，不在此处改写主引擎路由", len(words))
        return words
    _log.info("词级游程判据摘除 %d 个词（共 %d 个）", len(impossible), len(words))
    return [word for index, word in enumerate(words) if index not in impossible]


def _words_to_segment(words, seg_id, joiner):
    text = joiner.join(w["text"] for w in words)
    seg = Segment(id=seg_id, text=text,
                  start=words[0]["start"], end=words[-1]["end"])
    seg.words = [
        {
            "word": w.get("word") or w.get("text", ""),
            "text": w.get("text", ""),
            "start": w.get("start", 0.0),
            "end": w.get("end", 0.0),
        }
        for w in words
    ]
    seg.whisper_text = ""
    return seg


def _is_short_fragment(seg, lang):
    text = (getattr(seg, "text", "") or "").strip()
    if not text:
        return False
    duration = max(0.0, getattr(seg, "end", 0.0) - getattr(seg, "start", 0.0))
    if lang in ("ja", "zh", "ko", "auto"):
        return len(text) <= 1 or (duration < 0.35 and len(text) <= 3)
    return duration < 0.25 and len(text.split()) <= 1


def _merge_whisper_text(target, source, joiner, prepend=False):
    target_text = (getattr(target, "whisper_text", "") or "").strip()
    source_text = (getattr(source, "whisper_text", "") or "").strip()
    if not source_text:
        return
    if target_text:
        if prepend:
            target.whisper_text = f"{source_text}{joiner}{target_text}".strip()
        else:
            target.whisper_text = f"{target_text}{joiner}{source_text}".strip()
    else:
        target.whisper_text = source_text


def merge_short_subtitle_fragments(segments, lang, max_gap=1.0,
                                   voice_timeline=None, voice_region_gap=2.0):
    """Merge tiny standalone subtitle fragments into nearby segments."""
    if not segments:
        return segments
    merged = []
    joiner = "" if lang in ("ja", "zh", "ko") else " "
    i = 0
    while i < len(segments):
        seg = segments[i]
        if _is_short_fragment(seg, lang):
            prev_gap = seg.start - merged[-1].end if merged else None
            if merged and (
                prev_gap <= max_gap or (
                    prev_gap <= voice_region_gap
                    and _same_voice_region(merged[-1], seg, voice_timeline)
                )
            ):
                prev = merged[-1]
                prev.text = f"{prev.text}{joiner}{seg.text}".strip()
                prev.words = [
                    *[dict(word) for word in getattr(prev, "words", [])],
                    *[dict(word) for word in getattr(seg, "words", [])],
                ]
                _merge_whisper_text(prev, seg, joiner)
                prev.end = max(prev.end, seg.end)
                prev.uncertain = getattr(prev, "uncertain", False) or getattr(seg, "uncertain", False)
                i += 1
                continue
            next_gap = segments[i + 1].start - seg.end if i + 1 < len(segments) else None
            if i + 1 < len(segments) and (
                next_gap <= max_gap or (
                    next_gap <= voice_region_gap
                    and _same_voice_region(seg, segments[i + 1], voice_timeline)
                )
            ):
                nxt = segments[i + 1]
                nxt.text = f"{seg.text}{joiner}{nxt.text}".strip()
                nxt.words = [
                    *[dict(word) for word in getattr(seg, "words", [])],
                    *[dict(word) for word in getattr(nxt, "words", [])],
                ]
                _merge_whisper_text(nxt, seg, joiner, prepend=True)
                nxt.start = min(seg.start, nxt.start)
                nxt.uncertain = getattr(nxt, "uncertain", False) or getattr(seg, "uncertain", False)
                i += 1
                continue
            if _segment_in_non_voice(seg, voice_timeline):
                seg.filter_reason = "voice_timeline_non_voice_short_fragment"
        merged.append(seg)
        i += 1
    for idx, seg in enumerate(merged):
        seg.id = idx
    return merged


def _should_break_ja_sentence(prev_text, gap):
    text = (prev_text or "").strip()
    if not text:
        return False
    if gap >= _JA_STRONG_ENDER_GAP and text.endswith(_JA_STRONG_ENDERS):
        return True
    if gap >= _JA_WEAK_ENDER_GAP and text.endswith(_JA_WEAK_ENDERS):
        return True
    return False


def _sv_tag_at(time_point, whisper_segments, ws_starts):
    if not ws_starts:
        return None
    idx = bisect.bisect_right(ws_starts, time_point) - 1
    if idx >= 0:
        ws = whisper_segments[idx]
        if ws.start <= time_point <= ws.end:
            return getattr(ws, "sensevoice_tag", None)
    return None


def _is_repetition_pattern(text):
    text = text.strip()
    if not text:
        return False
    words = text.split()
    if len(words) >= 4:
        top_count = Counter(words).most_common(1)[0][1]
        if top_count / len(words) > 0.6:
            return True
    sample = text[:50]
    for length in range(2, min(10, len(sample) // 3 + 1)):
        for i in range(len(sample) - 3 * length + 1):
            if (sample[i:i + length]
                    == sample[i + length:i + 2 * length]
                    == sample[i + 2 * length:i + 3 * length]):
                return True
    return False


def _resolve_whisper_total_end(whisper_segments, total_end):
    if total_end is None:
        total_end = max((getattr(s, "end", 0.0) for s in whisper_segments), default=0.0)
    return total_end


def _whisper_has_enough_long_media_coverage(whisper_segments, total_end):
    # Tiny/failed Whisper on long media often returns only a few broad segments.
    if total_end >= 300 and len(whisper_segments) < 20:
        return False

    if total_end >= 300:
        covered = sum(max(0.0, getattr(s, "end", 0.0) - getattr(s, "start", 0.0))
                      for s in whisper_segments)
        if total_end > 0 and covered / total_end < 0.30:
            return False
    return True


def _collect_digit_whisper_segments(whisper_segments):
    digit_segs = []
    for i, s in enumerate(whisper_segments):
        cleaned = unicodedata.normalize("NFKC", s.text.strip())
        if cleaned.isdigit():
            digit_segs.append((i, int(cleaned)))
    return digit_segs


def _has_too_many_digit_segments(digit_segs, total_segments):
    return len(digit_segs) / total_segments >= 0.2


def _has_repeated_whisper_texts(non_empty):
    texts = [unicodedata.normalize("NFKC", s.text.strip()) for s in non_empty]
    top_count = Counter(texts).most_common(1)[0][1]
    return top_count / len(texts) >= 0.30


def _has_long_sequential_digit_run(digit_segs):
    max_run = 0
    run = 0
    for j in range(1, len(digit_segs)):
        ip, vp = digit_segs[j - 1]
        ic, vc = digit_segs[j]
        if ic == ip + 1 and 0 < vc - vp <= 3:
            run += 1
            max_run = max(max_run, run)
        else:
            run = 0

    return max_run >= 5


def _whisper_is_usable(whisper_segments, total_end=None):
    if not whisper_segments:
        return False

    total_end = _resolve_whisper_total_end(whisper_segments, total_end)
    if not _whisper_has_enough_long_media_coverage(whisper_segments, total_end):
        return False

    non_empty = [s for s in whisper_segments if getattr(s, "text", "").strip()]
    if not non_empty:
        return False

    if len(whisper_segments) < 5:
        return True

    digit_segs = _collect_digit_whisper_segments(whisper_segments)
    if _has_too_many_digit_segments(digit_segs, len(whisper_segments)):
        return False

    if _has_repeated_whisper_texts(non_empty):
        return False

    return not _has_long_sequential_digit_run(digit_segs)


def _build_reference_fields(whisper_segments, lang):
    """G2/B9+B10：副引擎的完整转录 + 可用性判定，转录阶段一次算好。

    判定拿的是**完整的 whisper_segments**。原来的做法是在润色阶段用
    `seg.whisper_text` 反过来伪造一批参照段再判（`worker.py:962-973`），
    那批伪造段是按时间重叠拼出来的，一旦 B1 删掉拼接就恒为空 →
    判定恒为 single → 双引擎静默关闭。所以判定必须待在有真数据的地方。
    """
    if not whisper_segments:
        return "", False
    text = _primary_source_text("whisper", [], whisper_segments, lang)
    total_end = max([segment.end for segment in whisper_segments] + [0.0])
    usable = bool(text) and _whisper_is_usable(
        whisper_segments, total_end=total_end
    )
    return text, usable


def _pause_segment(words, start_id, lang, pause_threshold=0.5):
    if not words:
        return []
    words = sorted(words, key=lambda w: w["start"])
    joiner = "" if lang in ("ja", "zh", "ko") else " "

    segments = []
    buf = [words[0]]

    for i in range(1, len(words)):
        prev, curr = words[i - 1], words[i]
        gap = curr["start"] - prev["end"]

        should_break = gap >= pause_threshold
        if prev["text"].rstrip().endswith(_SENTENCE_ENDERS):
            should_break = True

        text_so_far = joiner.join(w["text"] for w in buf)
        span = curr["start"] - buf[0]["start"]
        if span >= 8.0 or len(text_so_far) >= 40:
            should_break = True

        if should_break:
            segments.append(_words_to_segment(buf, start_id + len(segments), joiner))
            buf = [curr]
        else:
            buf.append(curr)

    if buf:
        segments.append(_words_to_segment(buf, start_id + len(segments), joiner))
    return segments


def _qwen_chunks_to_segments(qwen_chunks, lang):
    words = _flatten_qwen_words(qwen_chunks)
    if words:
        return _pause_segment(words, 0, lang)

    coarse_segments = []
    for chunk in qwen_chunks or []:
        if not isinstance(chunk, dict):
            continue
        text = str(chunk.get("text") or "").strip()
        try:
            start = float(chunk["t_start"])
            end = float(chunk["t_end"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not text or not np.isfinite(start) or not np.isfinite(end) or end <= start:
            continue
        coarse_segments.append(
            Segment(len(coarse_segments), start, end, text)
        )
    return coarse_segments


def _propagate_l1_marks(l1_sources, assembled):
    if not l1_sources or not assembled:
        return assembled

    l1_sources = sorted(l1_sources, key=lambda s: s.start)
    l1_starts = [s.start for s in l1_sources]
    for asm in assembled:
        asm_duration = asm.end - asm.start

        if asm_duration <= 0:
            asm.filtered = False
            asm.filter_reason = None
            asm.uncertain = False
            continue

        lo = max(0, bisect.bisect_right(l1_starts, asm.start) - 1)
        hi = min(len(l1_sources), bisect.bisect_right(l1_starts, asm.end) + 1)

        filtered_overlap = 0.0
        best_reason = None
        best_reason_ov = 0.0
        inherited_uncertain = False
        inherited_bgm = False

        for k in range(lo, hi):
            src = l1_sources[k]
            ov = min(asm.end, src.end) - max(asm.start, src.start)
            if ov <= 0:
                continue

            if getattr(src, "filtered", False):
                filtered_overlap += ov
                if ov > best_reason_ov:
                    best_reason_ov = ov
                    best_reason = getattr(src, "filter_reason", "L1")

            if getattr(src, "uncertain", False):
                inherited_uncertain = True
            if getattr(src, "bgm_override", False):
                inherited_bgm = True

        if filtered_overlap / asm_duration > 0.5:
            asm.filtered = True
            asm.filter_reason = best_reason
        else:
            asm.filtered = False
            asm.filter_reason = None

        asm.uncertain = inherited_uncertain
        if inherited_bgm:
            asm.bgm_override = True

    return assembled


def _find_uncovered_gaps(segments, total_start, total_end, min_gap=3.0):
    if not segments:
        if total_end - total_start >= min_gap:
            return [(total_start, total_end)]
        return []

    intervals = sorted([(s.start, s.end) for s in segments])
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    gaps = []
    cursor = total_start
    for start, end in merged:
        if start - cursor >= min_gap:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if total_end - cursor >= min_gap:
        gaps.append((cursor, total_end))
    return gaps


def _qwen_primary_assembly_params(lang):
    joiner = "" if lang in ("ja", "zh", "ko") else " "
    base_pause = 0.5
    max_span = 5.0 if lang == "ja" else 8.0
    max_chars = 25 if lang == "ja" else 40
    return joiner, base_pause, max_span, max_chars


def _qwen_primary_pause_threshold(prev, curr, whisper_segments, ws_starts, base_pause):
    gap = curr["start"] - prev["end"]
    threshold = base_pause
    if ws_starts and gap > 0:
        mid = (prev["end"] + curr["start"]) / 2
        tag = _sv_tag_at(mid, whisper_segments, ws_starts)
        if tag in ("<|Breath|>", "<|Crying|>", "<|Laughter|>"):
            threshold = base_pause * 1.5
    return threshold


def _qwen_primary_buffer_exceeds_limits(buf, curr, joiner, max_span, max_chars):
    text_so_far = joiner.join(w["text"] for w in buf)
    span = curr["start"] - buf[0]["start"]
    return span >= max_span or len(text_so_far) >= max_chars


def _should_break_qwen_primary(prev, curr, buf, lang, params, whisper_segments, ws_starts):
    joiner, base_pause, max_span, max_chars = params
    gap = curr["start"] - prev["end"]
    threshold = _qwen_primary_pause_threshold(prev, curr, whisper_segments, ws_starts, base_pause)

    if gap >= threshold:
        return True
    if prev["text"].rstrip().endswith(_SENTENCE_ENDERS):
        return True
    if lang == "ja" and _should_break_ja_sentence(prev.get("text", ""), gap):
        return True
    return _qwen_primary_buffer_exceeds_limits(buf, curr, joiner, max_span, max_chars)


def _split_qwen_primary_words(qwen_words, whisper_segments, lang):
    params = _qwen_primary_assembly_params(lang)
    joiner = params[0]
    ws_starts = [ws.start for ws in whisper_segments] if whisper_segments else []

    segments = []
    buf = [qwen_words[0]]

    for i in range(1, len(qwen_words)):
        prev, curr = qwen_words[i - 1], qwen_words[i]
        if _should_break_qwen_primary(prev, curr, buf, lang, params, whisper_segments, ws_starts):
            segments.append(_words_to_segment(buf, len(segments), joiner))
            buf = [curr]
        else:
            buf.append(curr)

    if buf:
        segments.append(_words_to_segment(buf, len(segments), joiner))

    return segments


def _assemble_qwen_primary(qwen_words, whisper_segments, lang):
    # G2/B1：这里原来还有一步 `_backfill_qwen_primary_whisper_text`，
    # 把与本段时间重叠的副引擎段拼成「参照文本」挂在段上。主副引擎的切句
    # 边界本来就对不上，拼出来的参照跟本段说的不是同一句——实测 134 组里
    # 76 组被判「冲突」，其中 68% 是纯边界错位。参照文本改由收据整篇提供
    # （`_build_reference_fields`），这里不再逐段拼。
    if not qwen_words:
        return []

    return _split_qwen_primary_words(qwen_words, whisper_segments, lang)


def _assemble_qwen_words_on_whisper_boundaries(
    qwen_words, whisper_segments, lang
):
    if not whisper_segments:
        return _pause_segment(qwen_words, 0, lang)

    joiner = "" if lang in ("ja", "zh", "ko") else " "
    seg_starts = [ws.start for ws in whisper_segments]

    word_to_seg = {}
    for w_idx, w in enumerate(qwen_words):
        right = bisect.bisect_right(seg_starts, w["end"])
        left = max(0, bisect.bisect_left(seg_starts, w["start"]) - 1)

        best_idx, best_ov = None, 0.0
        for s_idx in range(left, min(right + 1, len(whisper_segments))):
            ws = whisper_segments[s_idx]
            # 零时长词的重叠量恒为 0，不能用裸的 min/max 差值判归属——
            # 那会让它们全部落进 unmatched，被组成孤立段追加到末尾再排序，
            # 于是脱离原位。真跑实测：`This is our last` 交付成
            # `This our last stop… is`。规则只此一份，见 time_overlap 的说明。
            ov = time_overlap(w["start"], w["end"], ws.start, ws.end)
            if ov > best_ov:
                best_ov = ov
                best_idx = s_idx

        if best_idx is not None and best_ov > 0:
            word_to_seg[w_idx] = best_idx

    seg_to_words = defaultdict(list)
    for w_idx, s_idx in word_to_seg.items():
        seg_to_words[s_idx].append(w_idx)

    matched = set()
    segments = []
    for idx, ws in enumerate(whisper_segments):
        w_indices = seg_to_words.get(idx, [])
        if w_indices:
            words = sorted(
                [qwen_words[i] for i in w_indices],
                key=lambda w: w["start"])
            matched.update(w_indices)
            text = joiner.join(w["text"] for w in words)
            seg = Segment(id=idx, text=text,
                          start=words[0]["start"], end=words[-1]["end"])
            seg.words = [dict(word) for word in words]
        else:
            seg = Segment(id=idx, text=ws.text,
                          start=ws.start, end=ws.end)
        seg.whisper_text = ws.text
        seg.sensevoice_tag = getattr(ws, "sensevoice_tag", None)
        segments.append(seg)

    unmatched = [qwen_words[i]
                 for i in range(len(qwen_words)) if i not in matched]
    if unmatched:
        extra = _pause_segment(unmatched, len(segments), lang)
        segments.extend(extra)
        segments.sort(key=lambda s: s.start)

    return segments


def _assemble_emergency_fallback(
    qwen_words,
    whisper_segments,
    lang,
    total_end,
    qwen_coarse_segments=(),
):
    segments = []

    for ws in whisper_segments:
        if ws.text.strip().isdigit() or _is_repetition_pattern(ws.text):
            continue
        seg = Segment(id=len(segments), text=ws.text,
                      start=ws.start, end=ws.end)
        seg.words = [dict(word) for word in getattr(ws, "words", None) or []]
        seg.whisper_text = ws.text
        seg.uncertain = True
        segments.append(seg)

    if not segments and not qwen_words:
        for source in qwen_coarse_segments or ():
            text = str(getattr(source, "text", "") or "").strip()
            if not text:
                continue
            seg = Segment(
                id=len(segments),
                text=text,
                start=float(source.start),
                end=float(source.end),
            )
            seg.uncertain = True
            segments.append(seg)

    covered = [(s.start, s.end) for s in segments]
    orphans = [
        w for w in qwen_words
        if not _is_repetition_pattern(w["text"])
        and not any(s <= w["start"] and w["end"] <= e for s, e in covered)
    ]
    if orphans:
        extra = _pause_segment(orphans, len(segments), lang)
        for s in extra:
            s.uncertain = True
        segments.extend(extra)

    segments.sort(key=lambda s: s.start)
    gaps = _find_uncovered_gaps(segments, 0.0, total_end)
    for gap_start, gap_end in gaps:
        seg = Segment(id=len(segments),
                      text="[此时段字幕暂不可用]",
                      start=gap_start, end=gap_end)
        seg.uncertain = True
        seg.whisper_text = ""
        segments.append(seg)

    segments.sort(key=lambda s: s.start)
    return segments


def _smart_assemble(
    qwen_chunks,
    whisper_segments,
    lang,
    formal_qwen_words=None,
):
    qwen_words = (
        list(formal_qwen_words)
        if formal_qwen_words is not None
        else _flatten_qwen_words(qwen_chunks)
    )
    total_end = 0.0
    if whisper_segments:
        total_end = whisper_segments[-1].end
    elif qwen_words:
        total_end = qwen_words[-1]["end"]

    if lang == "en" and _whisper_is_usable(
        whisper_segments, total_end=total_end
    ):
        return _assemble_qwen_words_on_whisper_boundaries(
            qwen_words, whisper_segments, lang
        )
    return _assemble_qwen_primary(qwen_words, whisper_segments, lang)


def _vad_speech_gate(audio_path: str, skip_vad: bool) -> None:
    """VAD 语音占比 gate：语音极低时询问用户，较低时告警。"""
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


def _whisper_words_to_dicts(seg):
    """把 Whisper 段的词级时间戳转为 dict 列表；无词时返回 None。"""
    if not getattr(seg, "words", None):
        return None
    words = []
    for w in seg.words:
        if isinstance(w, dict):
            words.append(w)
        else:
            words.append({
                "word": getattr(w, "word", ""),
                "start": getattr(w, "start", 0.0),
                "end": getattr(w, "end", 0.0),
                "probability": getattr(w, "probability", 0.0),
            })
    return words


def _whisper_output_to_segments(whisper_output, results, info, n_total,
                                progress_callback):
    """把 Whisper 段迭代为 Segment 追加进 results（原地），返回累计段数。"""
    for seg in whisper_output:
        text = seg.text.strip()
        n_total += 1

        s = Segment(
            id=len(results),
            start=round(seg.start, 2),
            end=round(seg.end, 2),
            text=text,
        )
        _words = _whisper_words_to_dicts(seg)
        if _words is not None:
            s.words = _words
        results.append(s)

        if progress_callback and n_total % 10 == 0:
            try:
                progress_callback({
                    "completed_segments": n_total,
                    "processed_duration": max(0.0, float(seg.end)),
                })
            except Exception:
                pass
        _audio_duration = getattr(info, "duration", 0) or 0
        if n_total % 50 == 0 and _audio_duration > 0 and seg.end > 0:
            pct = min(99, round(seg.end / _audio_duration * 100))
            _log.info("Whisper 进度: %d%% (音频 %ds/%ds, 已识别 %d 段)",
                      pct, int(seg.end), int(_audio_duration), n_total)
    return n_total


def _collect_qwen_chunks(qwen_thread, qwen_holder, transcription_tier,
                         filter_keywords, audio_path, detected_lang, cache_dir,
                         progress_callback=None):
    """Phase F：等待 Qwen 线程 → 取结果；精确模式无线程时串行兜底跑一次。"""
    if qwen_thread:
        qwen_thread.join()
        _log.info("Qwen3-ASR 独立线程已结束")
    # P464/B4：**默认值不能是 `[]`**。线程还没写入时拿到空列表，
    # 新的完整性判定会把「没跑过」和「跑完了但一个块都没有」当成同一件事。
    # 「未知」和「很小」必须分开表达——本项目在采样时长上栽过同形状的跟头。
    # `QwenAsrOutcome` 没有定义 `__bool__`/`__len__`，任何实例都是真值，
    # 所以只有真正缺键（None）才会落到 not_run()。
    outcome = qwen_holder.get("data") or QwenAsrOutcome.not_run()

    # ── Serial Qwen fallback (non-pipeline mode, precise only) ──
    if transcription_tier == "precise" and filter_keywords and not qwen_thread:
        _log.info("Qwen3-ASR 交叉验证开始 (tier=%s, 串行)", transcription_tier)
        from backend.core.qwen_asr_runner import run_qwen_asr, is_qwen_asr_available
        if is_qwen_asr_available():
            # G11-B：语言本来就传对了，这里只把「Qwen 实际收到什么」补进收据。
            # Mac（MLX）上 should_parallel_asr() 恒为 False，串行**不是兜底，是主路径**——
            # 少了这一行，每一台 Mac 精准模式的收据里都没有 Qwen 的实收语言证据。
            qwen_holder["received_language"] = detected_lang
            outcome = run_qwen_asr(audio_path, language=detected_lang,
                                   cache_dir=cache_dir,
                                   progress_callback=progress_callback)
        else:
            _log.warning("Qwen3-ASR 不可用，跳过交叉验证")
    return outcome


def _format_position(seconds: float) -> str:
    """把秒数写成用户看得懂的位置。**用时间，不用块数。**"""
    minutes, secs = divmod(int(max(0.0, seconds or 0.0)), 60)
    return f"{minutes:02d}:{secs:02d}"


def describe_transcription_shortfall(qwen_outcome, whisper_ok: bool):
    """转录有没有欠账，以及**怎么跟用户说**。没有欠账时返回 None。

    P462 那个 bug 的原始症状是一句谎话：
    「处理失败了，我们没能识别出具体原因。」
    **系统是知道原因的**——它知道模型加载失败、知道只处理到哪一秒，它就是不说。
    用户等了半小时拿到一句谎话，然后重试三次得到三次同样的谎话。

    本批那句话是「不给用户假的成品」。**解释也是成品的一部分。**

    三种终态（外审裁定）：

    | 情况 | 处置 |
    |---|---|
    | 正常路由（含 Windows 因能力规则走 Whisper） | **不提示**——不是降级，说了反而吓人 |
    | 增强未完成但主识别完整 | 任务**完成** + 一句说明 |
    | 没有任何完整识别结果 | **红色失败** + 处理到哪、有没有成品、下一步做什么 |

    **返回纯数据**：要进收据、要写盘、刷新后靠它重建同一句话。
    第一批栽过一次——「有几块没挂上标点」只塞进 35% 的进度文案，
    任务跑完就被后续消息覆盖，用户刷新页面再也看不到。
    """
    if qwen_outcome is None or qwen_outcome.complete:
        return None
    if not qwen_outcome.chunks and qwen_outcome.completed_until is None:
        # 压根没跑 = 正常路由（引擎不适用 / 未安装 / 能力不够），不是故障。
        #
        # **两个条件缺一不可**：`chunks` 空但 `completed_until` 有值，
        # 说明**跑过、只是一块都没成**——那是故障，必须说。
        # 只看 `chunks` 会把这种情况和「没跑过」混为一谈。
        return None

    until = float(qwen_outcome.completed_until or 0.0)
    reason = qwen_outcome.failure_reason or "未知原因"
    if whisper_ok:
        # 有完整成品，不能报红——那会让用户以为字幕坏了。
        # 文案不提引擎名字：用户不知道 Qwen 和 Whisper 是什么。
        return {"severity": "info",
                "message": "识别增强未完成，已使用完整的语音识别结果。",
                "completed_until": until,
                "reason": reason}
    # `until == 0` = 动手了但一秒都没成。**别把内部标记原样翻译给用户**——
    # 「已处理到 00:00」读起来像个 bug，说「未能开始」才是人话。
    where = (f"已处理到 {_format_position(until)}，未生成字幕。"
             if until > 0 else "未能开始识别。")
    return {"severity": "error",
            "message": (f"语音识别未完成：{where}"
                        f"{reason}，请完成运行环境准备后重试。"),
            "completed_until": until,
            "reason": reason}


@dataclass
class _Routing:
    """Phase F+G+H 路由结果：等 Qwen → 选主引擎 → FA 需求 → L1 段。"""
    qwen_chunks: list
    whisper_segments: list
    routed_primary: str
    actual_primary: str
    qwen_raw: list
    has_qwen_timestamps: bool
    need_external_fa: bool
    qwen_l1_segments: list
    source_text: str
    source_words: list
    word_source: str
    # E2：转录欠账的用户可见说明。None = 没有欠账，什么都不用说。
    # **必须是纯数据**——它要进收据、写盘、刷新后重建同一句话。
    transcription_shortfall: dict = None

    @property
    def which_primary(self):
        return self.actual_primary


def _resolve_routing(qwen_thread, qwen_holder, transcription_tier,
                     filter_keywords, audio_path, detected_lang, cache_dir,
                     standard_primary, use_whisper, results,
                     progress_callback=None) -> "_Routing":
    """等待 Qwen → 按模式/语言选主引擎 → 判定外部 FA 需求 → 准备 L1 段。"""
    qwen_outcome = _collect_qwen_chunks(
        qwen_thread, qwen_holder, transcription_tier, filter_keywords,
        audio_path, detected_lang, cache_dir, progress_callback)
    # 显式解包：下游所有消费点继续拿块列表，完整性状态单独走。
    # **不把状态藏进 list 里**——那是另一种「让上层猜」。
    qwen_chunks = qwen_outcome.chunks

    # ── P464/B3：不完整的 Qwen 不得作为正式交付源 ──
    # 处置放在**这一层**，不放在 runner 里：只有这里知道有没有完整的 Whisper 可以替代。
    # runner 里无条件抛错会把已经跑完的 Whisper 一起弄死——治病治死了病人；
    # 而且 Mac 是串行调用、Windows 是并行线程，同一个故障两个平台结果还不一样。
    #
    # 丢弃之后走既有的回退逻辑（P254：Qwen 无产出 → 回退 Whisper 主引擎）。
    if qwen_chunks and not qwen_outcome.complete:
        _log.warning(
            "Qwen 结果不完整（只处理到 %.0fs，原因：%s），"
            "丢弃、不作为正式交付源——半部字幕不能冒充完整成品",
            qwen_outcome.completed_until or 0.0,
            qwen_outcome.failure_reason or "未知")
        qwen_chunks = []

    # ── P254/P276 智能路由：按模式和源语言选择主引擎 ──
    _whisper_segments = list(results or [])
    _routed_primary, _keep_qwen_for_l2 = choose_transcription_primary(
        transcription_tier, standard_primary, detected_lang, qwen_chunks)
    qwen_ok = bool(qwen_chunks) and _qwen_is_reliable(qwen_chunks)
    total_end = 0.0
    if _whisper_segments:
        total_end = max(segment.end for segment in _whisper_segments)
    whisper_ok = _whisper_is_usable(
        _whisper_segments, total_end=total_end
    )
    if _routed_primary == "qwen":
        _actual_primary = (
            "qwen" if qwen_ok else "whisper" if whisper_ok else "dual_failed"
        )
    else:
        _actual_primary = (
            "whisper" if whisper_ok else "qwen" if qwen_ok else "dual_failed"
        )

    _qwen_raw = list(qwen_chunks or [])
    if _actual_primary == "qwen":
        _log.info("%s 模式: %s → Qwen3-ASR 主引擎", transcription_tier, detected_lang)
        qwen_chunks = list(_qwen_raw)
        _source_words = _flatten_qwen_words(_qwen_raw)
        _word_source = (
            "qwen_word_timestamps"
            if _source_words
            else "external_forced_alignment"
        )
    elif _actual_primary == "whisper":
        if transcription_tier == "precise" and detected_lang in QWEN_PRIMARY_LANGS and not qwen_chunks:
            _log.warning(
                "P254: 语言=%s 在 QWEN_PRIMARY_LANGS 中，但 Qwen3-ASR 无产出，"
                "回退到 Whisper 主引擎。请检查 Qwen 模型是否正常加载。",
                detected_lang)
        _log.info("%s 模式: Whisper 主引擎", transcription_tier)
        qwen_chunks = list(qwen_chunks or [])
        _source_words = _flatten_primary_segment_words(results)
        _word_source = "whisper_word_timestamps"
    else:
        _log.warning("Qwen 与 Whisper 均未通过可靠性检查，使用可见的紧急合并结果")
        emergency_segments = _assemble_emergency_fallback(
            _flatten_qwen_words(_qwen_raw),
            _whisper_segments,
            detected_lang,
            total_end,
        )
        _source_words = _flatten_primary_segment_words(emergency_segments)
        _word_source = "emergency_combined"

    _source_text = _primary_source_text(
        _actual_primary,
        _qwen_raw,
        emergency_segments if _actual_primary == "dual_failed" else results,
        detected_lang,
    )

    # ── Determine if external FA is needed ──
    # MLX path: Qwen segments carry word-level timestamps from internal FA → skip external FA
    # PyTorch path: no Qwen timestamps → need external FA for L1
    has_qwen_timestamps = _has_timestamp_segments(qwen_chunks)
    need_external_fa = not has_qwen_timestamps

    _qwen_l1_segments = []
    if _actual_primary == "qwen":
        _qwen_l1_segments = _qwen_chunks_to_segments(qwen_chunks, detected_lang)
        if not _qwen_l1_segments:
            _qwen_l1_segments = list(results)

    return _Routing(
        qwen_chunks,
        _whisper_segments,
        _routed_primary,
        _actual_primary,
        _qwen_raw,
        has_qwen_timestamps,
        need_external_fa,
        _qwen_l1_segments,
        _source_text,
        _source_words,
        _word_source,
        describe_transcription_shortfall(qwen_outcome, whisper_ok=whisper_ok),
    )


def _finalize_routing_after_alignment(route, aligner_result, detected_lang):
    """只有真实词时间产生后，才把 PyTorch Qwen 写成正式词流。"""
    if route.actual_primary != "qwen" or route.source_words:
        return route

    aligned_words = []
    alignment_valid = bool(aligner_result)
    for raw in aligner_result or []:
        if not isinstance(raw, dict):
            alignment_valid = False
            break
        text = str(raw.get("text") or raw.get("word") or "").strip()
        try:
            start = float(raw["start"])
            end = float(raw["end"])
        except (KeyError, TypeError, ValueError, OverflowError):
            alignment_valid = False
            break
        if (
            not text
            or not np.isfinite(start)
            or not np.isfinite(end)
            or end < start
        ):
            alignment_valid = False
            break
        aligned_words.append({"text": text, "start": start, "end": end})

    if alignment_valid:
        try:
            candidate = TranscriptionReceipt(
                routed_primary=route.routed_primary,
                actual_primary="qwen",
                word_source="external_forced_alignment",
                source_text=route.source_text,
                source_words=aligned_words,
            )
        except ValueError:
            alignment_valid = False
        else:
            route.word_source = candidate.word_source
            route.source_words = candidate.source_words
            return route

    whisper_words = _flatten_primary_segment_words(route.whisper_segments)
    whisper_text = _primary_source_text(
        "whisper", [], route.whisper_segments, detected_lang
    )
    total_end = max(
        [segment.end for segment in route.whisper_segments] + [0.0]
    )
    if whisper_words and _whisper_is_usable(
        route.whisper_segments, total_end=total_end
    ):
        try:
            candidate = TranscriptionReceipt(
                routed_primary=route.routed_primary,
                actual_primary="whisper",
                word_source="whisper_word_timestamps",
                source_text=whisper_text,
                source_words=whisper_words,
            )
        except ValueError:
            pass
        else:
            route.actual_primary = candidate.actual_primary
            route.word_source = candidate.word_source
            route.source_text = candidate.source_text
            route.source_words = candidate.source_words
            return route

    route.actual_primary = "dual_failed"
    route.word_source = "emergency_combined"
    route.source_text = ""
    route.source_words = []
    return route


def _start_sv_thread(audio_path, _sv_target, _sv_holder):
    """启动 SenseVoice 后台线程，结果写入 _sv_holder["data"]。

    G11-B：分块运行事实写进同一个 _sv_holder["run"]——复用已有的共享通道，
    不新造第二条。"""
    from backend.core.sensevoice_runner import run_sensevoice

    def _run_sv():
        try:
            _sv_holder["data"] = run_sensevoice(
                audio_path, device="cpu", segments=_sv_target,
                run_stats=_sv_holder.setdefault("run", {}))
        except Exception as _sv_exc:
            _log.error("SenseVoice 线程异常: %s", _sv_exc, exc_info=True)
    t = threading.Thread(target=_run_sv, daemon=True)
    t.start()
    return t


def _start_fa_thread(audio_path, _fa_target, detected_lang, _fa_holder):
    """启动 Qwen3-FA 并行对齐线程，结果写入 _fa_holder["data"]。"""
    from backend.core.qwen_aligner import QwenAligner, align_segments

    def _run_fa():
        aligner = None
        try:
            aligner = QwenAligner()
            if aligner._model is not None:
                _fa_holder["data"] = align_segments(
                    aligner, audio_path, _fa_target, language=detected_lang)
        except Exception as e:
            _fa_holder["_quality"] = "fallback"
            _log.warning("Qwen3-FA 并行对齐失败: %s", e)
        finally:
            if aligner is not None and aligner._model is not None:
                aligner.close()
    t = threading.Thread(target=_run_fa, daemon=True)
    t.start()
    return t


def _dispatch_sv_fa(audio_path, detected_lang, filter_keywords, use_parallel,
                    transcription_tier, has_qwen_timestamps, need_external_fa,
                    _sv_target, _fa_target):
    """Phase I：派发 SenseVoice + 外部 FA（并行）；或串行降级直接拿 sv_events。
    返回 (sv_thread, _sv_holder, fa_thread, _fa_holder, sv_events)。"""
    sv_thread = None
    fa_thread = None
    _sv_holder = {}
    _fa_holder = {}
    sv_events = None
    if use_parallel and filter_keywords:
        from backend.core.sensevoice_runner import is_sensevoice_available
        if is_sensevoice_available():
            if has_qwen_timestamps:
                _log.info("SenseVoice 声音分类已启动（Qwen 内部 FA 已提供时间戳，跳过外部 FA）")
            else:
                _log.info("SenseVoice 声音分类与 Qwen3-FA 同时启动")
            sv_thread = _start_sv_thread(audio_path, _sv_target, _sv_holder)
        else:
            log_sensevoice_unavailable()

        if need_external_fa:
            from backend.core.qwen_aligner import is_qwen_aligner_available
            if is_qwen_aligner_available():
                fa_thread = _start_fa_thread(
                    audio_path, _fa_target, detected_lang, _fa_holder)
    elif filter_keywords and transcription_tier in ("standard", "precise"):
        _log.info("SenseVoice 声音分类开始 (tier=%s, 串行降级)", transcription_tier)
        from backend.core.sensevoice_runner import (
            run_sensevoice, is_sensevoice_available,
        )
        if is_sensevoice_available():
            sv_events = run_sensevoice(
                audio_path, device="cpu", segments=_sv_target,
                run_stats=_sv_holder.setdefault("run", {}))
        else:
            log_sensevoice_unavailable()
    return sv_thread, _sv_holder, fa_thread, _fa_holder, sv_events


def _await_sv_fa_threads(sv_thread, _sv_holder, fa_thread, sv_events):
    """Phase J：等待并行 SV/FA 线程结束，回收 sv_events。"""
    if sv_thread:
        sv_thread.join()
        sv_events = _sv_holder.get("data")
        if sv_events is None:
            _log.warning("SenseVoice 线程结束但未返回事件，跳过人声地图")
    if fa_thread:
        fa_thread.join()
    return sv_events


def _serial_fa_align_and_apply(audio_path, _fa_target, detected_lang,
                               use_whisper, results):
    """Phase K 串行分支：外部 FA 分段对齐并应用时间戳。
    返回 (aligner_result, results)。"""
    from backend.core.qwen_aligner import (
        QwenAligner, is_qwen_aligner_available, apply_aligner_timestamps,
        align_segments,
    )
    aligner_result = None
    if is_qwen_aligner_available():
        aligner = None
        try:
            _log.info("Qwen3-ForcedAligner 词级对齐开始（分段模式）")
            aligner = QwenAligner()
            if aligner._model is not None:
                aligner_result = align_segments(
                    aligner, audio_path, _fa_target, language=detected_lang,
                )
                if aligner_result:
                    _fa_target = apply_aligner_timestamps(_fa_target, aligner_result)
                    if use_whisper:
                        results = _fa_target
                    _log.info("Qwen3-FA 对齐完成: %d 词", len(aligner_result))
            else:
                _log.warning("Qwen3-FA 模型未加载，跳过对齐")
        except Exception as e:
            _log.warning("Qwen3-FA 对齐失败，保留原始时间戳: %s", e)
            aligner_result = []
        finally:
            if aligner is not None and aligner._model is not None:
                aligner.close()
    return aligner_result, results


def _apply_fa_timestamps(fa_thread, _fa_holder, need_external_fa, filter_keywords,
                         transcription_tier, audio_path, _fa_target, detected_lang,
                         use_whisper, results):
    """Phase K：应用外部 FA 时间戳（并行结果优先，否则串行对齐）。
    返回 (aligner_result, results)。"""
    aligner_result = None
    if fa_thread:
        fa_data = _fa_holder.get("data")
        if fa_data:
            from backend.core.qwen_aligner import apply_aligner_timestamps
            aligner_result = fa_data
            _fa_target = apply_aligner_timestamps(_fa_target, aligner_result)
            if use_whisper:
                results = _fa_target
            _log.info("Qwen3-FA 对齐完成（并行）: %d 词", len(aligner_result))
            _fa_holder["_quality"] = "official"
    elif need_external_fa and filter_keywords and transcription_tier in ("standard", "precise"):
        aligner_result, results = _serial_fa_align_and_apply(
            audio_path, _fa_target, detected_lang, use_whisper, results)
    return aligner_result, results


def _annotate_fa_quality(results, has_qwen_timestamps, fa_thread, _fa_holder,
                         aligner_result):
    """Phase L (P214)：标注每段 FA 质量，fallback 时告警。"""
    if has_qwen_timestamps:
        _fa_quality = "qwen_internal"
    elif fa_thread:
        _fa_quality = _fa_holder.get("_quality", "fallback")
    elif aligner_result is not None:
        _fa_quality = "official" if aligner_result else "fallback"
    else:
        _fa_quality = "not_attempted"
    for seg in results:
        seg._fa_quality = _fa_quality
    if _fa_quality == "fallback":
        _log.warning("⚠ 词级对齐未成功，字幕使用 Whisper 粗时间戳")


def _run_sensevoice_and_fa(audio_path, detected_lang, filter_keywords,
                           use_parallel, use_whisper, transcription_tier,
                           has_qwen_timestamps, need_external_fa,
                           _which_primary, _qwen_l1_segments, results):
    """Phase I+J+K+L 协调：派发 → 等待 → 应用 FA → 标注质量。
    返回 (sv_events, aligner_result, results, _sv_target, sv_run_stats)。"""
    # P268: SenseVoice follows the Qwen timeline when Qwen is primary.
    _sv_target = _qwen_l1_segments if _which_primary == "qwen" and _qwen_l1_segments else results
    # FA alignment target: standard mode has no Whisper segments, align Qwen segments instead
    _fa_target = _sv_target if _which_primary == "qwen" else results

    sv_thread, _sv_holder, fa_thread, _fa_holder, sv_events = _dispatch_sv_fa(
        audio_path, detected_lang, filter_keywords, use_parallel,
        transcription_tier, has_qwen_timestamps, need_external_fa,
        _sv_target, _fa_target)
    sv_events = _await_sv_fa_threads(sv_thread, _sv_holder, fa_thread, sv_events)
    aligner_result, results = _apply_fa_timestamps(
        fa_thread, _fa_holder, need_external_fa, filter_keywords,
        transcription_tier, audio_path, _fa_target, detected_lang,
        use_whisper, results)
    _annotate_fa_quality(
        results, has_qwen_timestamps, fa_thread, _fa_holder, aligner_result)
    return sv_events, aligner_result, results, _sv_target, _sv_holder.get("run")


def _make_voice_timeline(sv_events, results, _qwen_l1_segments):
    """Phase N（P275）：从 SenseVoice 事件构建人声地图，返回 timeline 或 None。"""
    _voice_timeline = None
    if sv_events:
        _audio_dur = 0.0
        if results:
            _audio_dur = max(s.end for s in results)
        elif _qwen_l1_segments:
            _audio_dur = max(s.end for s in _qwen_l1_segments)
        if _audio_dur > 0:
            _voice_timeline = _build_voice_timeline(sv_events, _audio_dur)
            _log.info("P275 人声地图: %.1f%% 人声, %d 个语音区间, %d 个非语音区间",
                      _voice_timeline["voice_ratio"] * 100,
                      len(_voice_timeline["voice_regions"]),
                      len(_voice_timeline["non_voice_regions"]))
    return _voice_timeline


def _run_l1_dual_signal(filter_keywords, _which_primary, _qwen_l1_segments,
                        qwen_chunks, aligner_result, detected_lang):
    """Phase P（P268）：L1 双信号交叉验证（SenseVoice × FA，平台无关）。"""
    if filter_keywords and _which_primary == "qwen" and _qwen_l1_segments:
        from backend.core.hallucination_filter import (
            _layer1_dual_signal, _qwen_segments_as_alignment,
        )
        # Mac (MLX): Qwen 内置 FA → 从 qwen_chunks 提取对齐数据
        # Windows (PyTorch): 外部 FA → 用 aligner_result
        if _has_timestamp_segments(qwen_chunks):
            _l1_align = _qwen_segments_as_alignment(qwen_chunks)
        elif aligner_result:
            _l1_align = aligner_result
        else:
            _l1_align = None
        for _qseg in _qwen_l1_segments:
            if not getattr(_qseg, "filtered", False):
                _layer1_dual_signal(_qseg, _l1_align, detected_lang)


def build_sound_observation(sv_events, run_stats=None):
    """把 SenseVoice 的原始事件整理成**观察证据**——只留证，不参与任何裁决。

    合同红线 16：SenseVoice 不得传播成票数、不得升格为第二裁决者、不得写死事件数量。
    事件数由素材时长与分块结果决定；事故任务里那 852 个 `Speech`
    是 7 个粗事件按时间重叠传播出来的，**不是 852 票**。

    2026-08-30（G11-B）删掉了 `covered_seconds` 与 `coverage_ratio`。
    它们算的是「返回事件占了多少秒」，却极易被读成「SenseVoice 实际处理了多少音频」。
    这两件事不是一回事：一块音频可以被完整处理却只返回很少事件。
    而且当时的分母用的是**最后一条字幕的结束时间**，不是真实音频长度——
    尾部漏转时分母被缩短，覆盖率假高。**一个没人消费、还一直在撒谎的数字，删掉，不修。**

    取而代之的是 `run`：直接记录事实——计划几块、成功几块、失败几块、失败的是哪几分钟。
    `run` 缺失表示**历史存档没有记录**，不得默认解释为 failed=0。
    """
    events = []
    for raw in sv_events or []:
        tags = raw.get("tags") or {}
        events.append({
            "start_s": float(raw.get("start_s") or 0.0),
            "end_s": float(raw.get("end_s") or 0.0),
            "language": tags.get("language", "unknown"),
            "event": raw.get("event") or tags.get("event") or "unknown",
        })
    out = {"event_count": len(events), "events": events}
    if run_stats:
        out["run"] = dict(run_stats)
    return out


def _assemble_and_merge(_which_primary, _qwen_raw, _whisper_segments,
                        _qwen_l1_segments, results, detected_lang,
                        _voice_timeline, formal_qwen_words=None):
    """Phase Q（P254）：Qwen 主引擎组装骨架 → 短字幕合并 → 人声地图质量日志。"""
    if _which_primary == "qwen":
        _assembled = _smart_assemble(
            _qwen_raw,
            whisper_segments=_whisper_segments,
            lang=detected_lang,
            formal_qwen_words=formal_qwen_words,
        )
        _propagate_l1_marks(_qwen_l1_segments, _assembled)
        results = _assembled
    elif _which_primary == "whisper":
        results = list(_whisper_segments)
    elif _which_primary == "dual_failed":
        total_end = max(
            [segment.end for segment in (_whisper_segments or [])]
            + [word["end"] for word in _flatten_qwen_words(_qwen_raw)]
            + [0.0]
        )
        results = _assemble_emergency_fallback(
            _flatten_qwen_words(_qwen_raw),
            _whisper_segments,
            detected_lang,
            total_end,
            qwen_coarse_segments=_qwen_l1_segments,
        )
    results = merge_short_subtitle_fragments(
        results, detected_lang, voice_timeline=_voice_timeline)
    _vt_quality = None
    if _voice_timeline:
        # G11 4-3：**复用这份已有结果**，不新建第二套「大段缺失」检测。
        # 它原本只进了一条 _log.info——无返回、无收据、无人消费，
        # 等于「大段有人声却没有文字」在交付前根本没有接管者。
        _vt_quality = summarize_voice_timeline_quality(
            results, _voice_timeline, detected_lang)
        _log.info(
            "P275 人声地图质量: 人声区=%d, 无字幕人声区=%d, 非人声短词=%d, 最长疑似漏字幕=%s",
            _vt_quality["voice_region_count"],
            _vt_quality["uncovered_voice_region_count"],
            _vt_quality["non_voice_short_fragment_count"],
            _vt_quality["longest_uncovered_voice_region"],
        )
    return results, _vt_quality


def _apply_five_layer_filter(results, detected_lang, aligner_result, qwen_chunks,
                             use_whisper, _whisper_segments, _which_primary,
                             transcription_tier, filter_keywords):
    """Phase R：五层幻觉过滤（L2-L5；L1 已跑则跳过；标准模式无第二信号跳 L2）。"""
    # Standard mode: no Whisper → L2 cross-validation has no second signal, skip it
    _filter_qwen = qwen_chunks if use_whisper else None
    _alt_segs = _whisper_segments if _which_primary == "qwen" else None
    if filter_keywords:
        results = filter_hallucinations(results, detected_lang,
                                        aligner_result=aligner_result,
                                        qwen_chunks=_filter_qwen,
                                        skip_l1=(_which_primary == "qwen"),
                                        skip_l2=(transcription_tier == "standard"),
                                        alt_segments=_alt_segs)
    return results


class TranscriptionShortfallError(RuntimeError):
    """一条字幕都没产出，**而且我们知道为什么**。

    外审②：原来这里只抛一个裸 `RuntimeError("未检测到任何语音内容")`。
    模型加载失败、半部结果按合同丢弃、`results` 为空——走的是同一句话。
    用户等了半小时，看到「视频可能为纯音乐或无声」，
    **和修之前那句「我们没能识别出具体原因」一样是谎话，只是换了个说法。**

    异常带着结构化说明往上走，worker 的异常出口才能把它交给前端，
    刷新页面之后也重建得出来。
    """

    def __init__(self, message: str, shortfall: dict | None = None):
        super().__init__(message)
        self.shortfall = shortfall


def _finalize_segments(results, speaker_detect, audio_path, shortfall):
    """Phase S+T：空检查 + 说话人检测。

    `shortfall` **必填**（可以是 None，但必须显式传）：
    这批栽的坑全是「东西写好了没接上」，给默认值等于允许下一个人忘掉它，
    而忘掉之后这条路会**静默**退回那句笼统的错话。少传就 TypeError。
    """
    if not results:
        if shortfall and shortfall.get("message"):
            # 系统知道处理到哪一秒、为什么停的——就说出来。
            _log.error("未产出字幕：%s", shortfall.get("reason") or "原因见说明")
            raise TranscriptionShortfallError(shortfall["message"], shortfall)
        _log.error("未检测到任何语音内容")
        raise RuntimeError("未检测到任何语音内容。视频可能为纯音乐或无声")
    # ── Step 7: Speaker detection ──
    if speaker_detect:
        _log.info("说话人检测开始（自适阈值）")
        results = _detect_speaker_changes(audio_path, results)
    return results


def _should_parallel_qwen(use_qwen, filter_keywords, use_whisper):
    """Phase C 条件：是否启动 Qwen 并行线程（与 Whisper 同跑）。"""
    if not (use_qwen and filter_keywords and use_whisper and should_parallel_asr()):
        return False
    from backend.core.qwen_asr_runner import is_qwen_asr_available
    return is_qwen_asr_available()


def _run_standard_language_router(audio_path, decision,
                                  use_qwen, filter_keywords, cache_dir,
                                  progress_callback, _qwen_holder):
    """Phase D：标准模式按**已锁定的语言**选主引擎；语言适合则切 Qwen。

    G11：语言探测已移出本函数——多点采样在 `transcribe()` 更早的位置完成
    （CI-06：必须先于 Qwen 线程启动）。本函数只读 `decision`，不产生也不改写语言。
    返回 `_standard_primary`；切 Qwen 时结果写入 `_qwen_holder`。
    """
    detected_lang = decision.language
    _standard_primary = choose_standard_primary_engine(detected_lang)
    _agree = sum(1 for s in decision.samples
                 if s.valid and s.language == detected_lang)
    _log.info(
        "标准模式: 语言=%s（%d/%d 点投它，共 %d 点有效），主引擎=%s",
        detected_lang, _agree, decision.expected_count,
        decision.valid_count, _standard_primary)

    if _standard_primary == "qwen":
        from backend.core.qwen_asr_runner import (
            run_qwen_asr, is_qwen_asr_available, qwen_provides_word_timings)
        # P463：主引擎必须产出**文字 + 时间**。给不了词级时间的后端
        # （Windows 的 PyTorch 分支）没有主引擎资格——这跟语言无关，
        # `QWEN_PRIMARY_LANGS` 里四种语言一视同仁。
        #
        # **判据加在这里，不加进 `is_qwen_asr_available()`**：精准模式的
        # 并行线程与串行兜底仍要用 Qwen 做交叉验证，一刀切会静默削掉它们，
        # 而 Mac 上 MLX 恒为真、永远测不出来。
        #
        # 不满足就落到下面的 `else`——**`release_whisper_model()` 在 `if` 内部，
        # 降级时根本不执行，退路自动保住。**
        if (use_qwen and filter_keywords and is_qwen_asr_available()
                and qwen_provides_word_timings()):
            release_whisper_model()
            _log.info("标准模式: 语言适合 Qwen，停止 Whisper，切换 Qwen")
            _log.info("标准模式: Qwen 使用锁定语言=%s", detected_lang)
            _qwen_holder["received_language"] = detected_lang
            _qwen_holder["data"] = run_qwen_asr(
                audio_path, language=detected_lang,
                cache_dir=cache_dir,
                progress_callback=progress_callback)
        else:
            _log.warning("标准模式: Qwen3-ASR 不可用或内存不足，继续 Whisper 主引擎")
            _standard_primary = "whisper"
    return _standard_primary


def _run_full_whisper(audio_path, model_size, decision, vad_threshold,
                      results, n_total, progress_callback):
    """Phase E：加载 Whisper 全量转录 → 段迭代追加进 results → 释放模型。

    G11 第三刀的根治：原实现把 `lang`（用户请求，恒为 None）而不是已决定的语言
    传给引擎，引擎于是自行重猜，再用重猜结果**覆写**已锁定的语言——
    事故 fc441fb713 的 `检测语言: ja (概率 0.36)` 就是这一行。
    现在引擎收到**已锁定的语言**，且**本函数不再返回语言**：没有返回值就没有覆写。
    返回 (info, n_total)。
    """
    _log.info("加载 Whisper %s 模型…", model_size)
    if progress_callback:
        progress_callback(-1)
    whisper_output, info = transcribe_whisper(
        audio_path,
        model_size=model_size,
        language=decision.language,
        vad_threshold=vad_threshold,
    )
    n_total = _whisper_output_to_segments(
        whisper_output, results, info, n_total, progress_callback)

    release_whisper_model()
    _log.info("Whisper 模型已释放")
    return info, n_total


# ═══════════════════════════════════════════════════════════════════
# G11 施工项 4：交付前的产物一致性检查
# ═══════════════════════════════════════════════════════════════════
# 只保留**已有英转日事故对应的假名回归检查**。
#
# 定位要说准：字符统计是确定的，但「假名达到多少算矛盾」是有阈值的产品门槛，
# **不是无阈值规则**。它是已发生事故的内容回归检查，不是通用零误报语言检测器。
#
# 明确不做（三条都是未经校准的语言猜测，会误拦）：
#   特殊字符表（英语正文合法出现 Beyoncé、café、El Niño、Straße）、
#   变音符号密度（阈值无本项目数据支撑）、高频功能词分布（受主题与短文本干扰）。
#
# 覆盖范围不得说满：它只覆盖「以大量假名为表现的已知英转日事故类型」。
# 英语被产成大量韩文或简体中文不一定触发；同为拉丁字母的英→法/德/西完全抓不到。
# 剩余部分由「残余风险」条目接管。
class ArtifactLanguageMismatch(Exception):
    """成品与语言决定确定矛盾——不交付。

    与「零语言证据」同一处置：走现有 `error` 终态 + 明确 `failure_kind`，
    **不新增 unresolved 状态**、不写半份成品。
    """

    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


_KANA_RATIO_LIMIT = 0.05
_MIN_TEXT_FOR_RATIO = 40


def check_artifact_language_consistency(decision, delivery_source_text):
    """比对「决定说什么」与「最终正式交付源稿是什么」。

    参数刻意叫 `delivery_source_text`——**当前唯一正式交付源稿**，
    由调用方在交付前绑定。G12-C 会把权威词流迁移到安全过滤与局部重跑之后，
    届时把新的源稿传进来即可，检查本身不必改。
    **不得写死 `receipt.source_text`**，否则迁移后报警器会一直安静而用户拿到不同内容。

    返回 None 表示通过；返回一句人话表示确定矛盾。
    """
    text = delivery_source_text or ""
    if len(text) < _MIN_TEXT_FOR_RATIO:
        return None                      # 太短，占比没有统计意义
    language = getattr(decision, "language", None)
    if language in ("ja", None):
        return None                      # 决定就是日语时假名合法
    kana = sum(1 for ch in text if 0x3040 <= ord(ch) <= 0x30FF)
    ratio = kana / len(text)
    if ratio < _KANA_RATIO_LIMIT:
        return None
    return (
        f"语言判定为{language}，但字幕原文里 {ratio:.0%} 是日文假名——"
        "两者矛盾，不能当成成品交付。"
    )


def transcribe(audio_path: str, model_size: str = "large-v2",
               skip_vad: bool = False,
               vad_threshold: float = 0.0,
               filter_keywords: bool = True,
               speaker_detect: bool = True,
               transcription_tier: str = "standard",
               progress_callback=None,
               cache_dir: str = None) -> TranscriptionEnvelope:
    """转录音频，返回片段与主引擎正式词流的单一信封。

    G11 核心承诺：**这里语言只准被决定一次。**
    `source_lang` 外部覆盖入口已撤销——软件自己识别语言，不让用户替它裁决。
    语言由多点采样一次性决定，随后以**冻结的 `LanguageDecision`** 形式按值传给
    每一个消费者；函数体内不保留可被重新赋值的语言变量（CI-07）。
    """
    _log.info("transcribe() 开始: audio=%s model=%s", audio_path, model_size)

    _vad_speech_gate(audio_path, skip_vad)

    from backend.core.hardware_detector import detect_hardware

    hw = detect_hardware()
    ram_gb = hw.get("ram_gb", 0) or 0
    use_qwen = should_qwen_pipeline(transcription_tier, ram_gb)
    use_parallel = should_parallel_svfa(transcription_tier, ram_gb)
    use_whisper = should_load_whisper(transcription_tier)
    use_standard_router = transcription_tier == "standard"

    # ═══════════════════════════════════════════════════════════════
    # G11：语言在这里被决定一次。CI-06 要求它必须先于任何引擎启动——
    # 原实现里 Qwen 线程在语言产生之前就出发，闭包读到的是 "auto"。
    # ═══════════════════════════════════════════════════════════════
    from backend.core.extractor import get_video_duration

    decision = sample_language_multipoint(
        audio_path, model_size, get_video_duration(audio_path))
    if not decision.locked:
        # 一票都没有 → 这个文件我们听不了 → 不写正式信封、不交付。
        # 分票不走这里：多数说了算（2026-08-29 方向变更）。
        raise LanguageEvidenceMissing(decision)

    results = []
    n_total = 0
    _standard_primary = None
    info = None

    # ═══════════════════════════════════════════════════════════════
    # Qwen-ASR: start early. In precise mode, runs in a parallel
    # thread alongside Whisper. In standard mode, runs synchronously
    # as the sole ASR engine.
    # ═══════════════════════════════════════════════════════════════
    qwen_thread = None
    _qwen_holder = {}
    # ⚠️ 这里原有一段 P296（2026-07-20）的设计说明，主张「刻意不等 Whisper 的语言探测、
    # 让 Qwen 收到 auto 自行检测」，并断言「这不是竞态遗留」。**G11 已推翻它，原文删除。**
    #
    # 推翻的两个理由，都是实测出来的：
    #  ① 精准模式下 Qwen 的文字会经 `_filter_qwen`（见 _apply_five_layer_filter）
    #     进入五层过滤并影响最终成品——**它不是纯观察信号**，必须服从 LanguageDecision。
    #  ② 原写法用闭包读外部的 `detected_lang`，而另一条路径会重新赋值同一个变量。
    #     那**就是**竞态；它不出事只因为线程启动后微秒级即取值，而 Whisper 要跑几分钟。
    #     靠时序差侥幸躲开的东西不叫设计。
    #
    # 现在：语言在更早的位置被决定一次，**按值**传进线程（args=），线程不再读外部变量。
    if _should_parallel_qwen(use_qwen, filter_keywords, use_whisper):
        from backend.core.qwen_asr_runner import run_qwen_asr

        def _run_qwen(_language):
            # CI-06：按值接收，不得闭包读外部可变变量——原实现在这里与 :1839
            # 的重新赋值构成数据竞争，只靠时序差侥幸不出事。
            _qwen_holder["received_language"] = _language
            _qwen_holder["data"] = run_qwen_asr(
                audio_path, language=_language,
                cache_dir=cache_dir,
                progress_callback=progress_callback)
        qwen_thread = threading.Thread(
            target=_run_qwen, args=(decision.language,), daemon=True)
        qwen_thread.start()
        _log.info("Qwen3-ASR 独立线程已启动（与 Whisper 并行）")

    # ═══════════════════════════════════════════════════════════════
    # Whisper: short language probe in standard mode, full engine in precise mode.
    # ═══════════════════════════════════════════════════════════════
    if use_standard_router:
        _standard_primary = _run_standard_language_router(
            audio_path, decision, use_qwen,
            filter_keywords, cache_dir, progress_callback, _qwen_holder)

    # 消费端边界的账本：记录每个**产出正式或参考文字**的引擎实际收到什么语言。
    # 记录点就是传参点——某处若传了别的东西，这本账立刻看得出来。
    engine_received_language = {}

    if use_whisper or _standard_primary == "whisper":
        info, n_total = _run_full_whisper(
            audio_path, model_size, decision, vad_threshold,
            results, n_total, progress_callback)
        engine_received_language["whisper"] = decision.language
    else:
        _log.info("标准模式: 跳过 Whisper 加载")

    if use_whisper or _standard_primary == "whisper":
        _log.info("Whisper 转录: %d 段, 语言=%s (概率 %.2f)", n_total, info.language, info.language_probability)

    # ── Phase F+G+H: 等 Qwen → 路由选主引擎 → FA 需求 → L1 段 ──
    _route = _resolve_routing(
        qwen_thread, _qwen_holder, transcription_tier, filter_keywords,
        audio_path, decision.language, cache_dir, _standard_primary,
        use_whisper, results, progress_callback)
    from backend.core.qwen_asr_runner import (
        count_chunks_missing_punctuation as _count_chunks_missing_punctuation,
    )
    qwen_chunks = _route.qwen_chunks
    _whisper_segments = _route.whisper_segments
    _which_primary = _route.which_primary
    _qwen_raw = _route.qwen_raw
    has_qwen_timestamps = _route.has_qwen_timestamps
    need_external_fa = _route.need_external_fa
    _qwen_l1_segments = _route.qwen_l1_segments

    # ── G12 第二批段三：词级游程判据 ──
    #
    # 段三保留了零时长词（模型自己定不出时间的词）。95% 是散落的正常词，
    # 直接进交付流补回长视频少掉的字；剩下 5% 是成串的幻觉，在这里摘掉。
    #
    # 判据没有阈值：**连续 ≥3 个同词落在同一个零宽瞬间**——和 `dur <= 0`
    # 同一个性质，物理上不可能。找到这个核心后向两侧扩展到相邻同词，整条走。
    # 用户报的那两个时间正常的长 `Oh` 就是靠扩展带走的。
    #
    # 位置必须在这里：**词流可得、且尚未组装成段**。组装之后就只剩段级视角，
    # 跨段的同词游程（本例横跨 0–22.4 秒共 20 个 token）再也拼不回来。
    # ⚠️ 摘除**不得**把词流摘成空：`_finalize_routing_after_alignment`（下方 :2154）
    # 靠 `source_words` 非空来早退，摘空会让它改写 `actual_primary`。
    # 那条边界写在 `_drop_impossible_instant_runs` 里，守着它的是
    # `test_g12b_evidence_preservation.py::test_scan_never_empties_the_word_stream`。
    _route.source_words = _drop_impossible_instant_runs(_route.source_words)

    # ── Phase I+J+K+L: SenseVoice + FA 派发/等待/应用/质量标注 ──
    from backend.core.sensevoice_runner import is_sensevoice_available
    will_run_sensevoice = bool(
        filter_keywords
        and (use_parallel or transcription_tier in ("standard", "precise"))
        and is_sensevoice_available()
    )
    if progress_callback and will_run_sensevoice:
        progress_callback({"phase": "analyzing_sound"})

    sv_events, aligner_result, results, _sv_target, _sv_run_stats = _run_sensevoice_and_fa(
        audio_path, decision.language, filter_keywords, use_parallel, use_whisper,
        transcription_tier, has_qwen_timestamps, need_external_fa,
        _which_primary, _qwen_l1_segments, results)

    _finalize_routing_after_alignment(
        _route, aligner_result, decision.language
    )
    _which_primary = _route.which_primary

    # ── Phase M: 已删除（G11 / CI-01）──
    # 原为「标准模式无 Whisper 时，从 SenseVoice 事件推断语言」。
    # 它是第 4 个语言写入点，且位置夹在消费者中间——同一次运行里前三只手拿到 "auto"、
    # 后五只手拿到 SenseVoice 推断值，语言在一次转录内部就是分裂的。
    # 已随 CI-01 整段删除；字母保留空位，不重排，以免历史记录里的 Phase 引用全部失效。
    # SenseVoice 的语言标签现在只作观察证据留在收据里（build_sound_observation）。

    # ── Phase N (P275): 人声地图 ──
    _voice_timeline = _make_voice_timeline(sv_events, results, _qwen_l1_segments)

    # ── Phase O: 应用 SenseVoice 标签到 L1 源段 ──
    if sv_events:
        _apply_sv_tags(_sv_target, sv_events)

    # ── Phase P (P268): L1 双信号交叉验证 ──
    _run_l1_dual_signal(filter_keywords, _which_primary, _qwen_l1_segments,
                        qwen_chunks, aligner_result, decision.language)

    # ── Phase Q (P254): 组装 + 短字幕合并 ──
    results, _voice_gap_quality = _assemble_and_merge(
        _which_primary, _qwen_raw, _whisper_segments, _qwen_l1_segments,
        results, decision.language, _voice_timeline,
        formal_qwen_words=_route.source_words,
    )

    # ── Phase R: 五层幻觉过滤 ──
    _pre_filter_segments = list(results)
    results = _apply_five_layer_filter(
        results, decision.language, aligner_result, qwen_chunks, use_whisper,
        _whisper_segments, _which_primary, transcription_tier, filter_keywords)

    # ── Phase S+T: 空检查 + 说话人检测 ──
    results = _finalize_segments(results, speaker_detect, audio_path,
                                 _route.transcription_shortfall)

    # ── G12 第二批段二：正式交付词流在这里**唯一地**重新导出 ──
    #
    # 原来这里只有 `if _route.actual_primary == "dual_failed":` 一个分支会重算，
    # 其余主引擎的收据停留在 `_resolve_routing` 里、**过滤之前**算好的那一份。
    # 后果是过滤器的裁决从未进入成品：L5 删掉 13.886 秒那条 `Oh`
    # （`debug.log:999`），成品 SRT 第 2 条却原样复活。三个月无人发现。
    #
    # 两个字段**必须在这一处、从同一份 `results` 一起导出**。只改一个会让
    # `TranscriptionReceipt` 的守恒检查当场抛 ValueError——那是好事（fail-fast），
    # 但也意味着**没有中间态可以试探**。
    _route.source_words = _flatten_primary_segment_words(results)
    _route.source_text = _delivery_source_text(results, decision.language)
    _assert_delivery_not_silently_empty(results, _route.source_words)
    _assert_dropped_text_absent(
        [s for s in _pre_filter_segments if getattr(s, "filtered", False)],
        _route.source_words,
    )

    _log.info("transcribe() 结束: 返回 %d 段", len(results))
    _reference_text, _reference_usable = _build_reference_fields(
        _whisper_segments, decision.language
    )
    if "received_language" in _qwen_holder:
        engine_received_language["qwen"] = _qwen_holder["received_language"]

    # 谁产出了文字，谁就在名单里——按**实际有没有产出**判断，不按路由意图。
    _engines_in_text_chain = tuple(
        name for name, produced in (
            ("whisper", bool(_whisper_segments)),
            ("qwen", bool(_route.qwen_chunks or _route.qwen_raw)),
        ) if produced)

    # ── 交付前最后一道闸 ──
    # 检查对象是**当前唯一正式交付源稿**。G12 第二批段二完成迁移后，
    # `_route.source_text` 已经是过滤与裁决之后的那一份，这里不需要再改。
    # （变量原名 `_delivery_source_text`，与新增的模块级同名函数冲突，故改名。）
    _delivery_text = _route.source_text
    _artifact_conflict = check_artifact_language_consistency(
        decision, _delivery_text)
    if _artifact_conflict:
        _log.error("G11 产物一致性检查开火：%s", _artifact_conflict)
        raise ArtifactLanguageMismatch(_artifact_conflict)

    receipt = TranscriptionReceipt(
        routed_primary=_route.routed_primary,
        actual_primary=_route.actual_primary,
        word_source=_route.word_source,
        source_text=_route.source_text,
        source_words=_route.source_words,
        reference_text=_reference_text,
        reference_usable=_reference_usable,
        language_decision=decision,
        engine_received_language=engine_received_language,
        engines_in_text_chain=_engines_in_text_chain,
        artifact_language_check="ok",
        observation_evidence={
            "voice_gaps": _voice_gap_quality,
            "sound": build_sound_observation(sv_events, _sv_run_stats),
            # P457：挂标点失败的块数。只写日志等于没说——真实任务打了 20 次
            # 警告、三个月无人发现。放进收据，用户可见文案和验收都从这里读。
            "punctuation": {
                "missing_chunks":
                    _count_chunks_missing_punctuation(qwen_chunks),
            },
            # E2：转录欠账的用户可见说明（None = 没有欠账）。
            # **放进收据 = 持久化**：随 transcript.json 落盘，刷新页面后
            # 能重建同一句话。第一批栽过一次——同类信息只塞进识别阶段 35%
            # 的进度文案，任务跑完就被后续消息覆盖，用户再也看不到。
            "shortfall": _route.transcription_shortfall,
        },
    )
    return TranscriptionEnvelope(results, receipt)
