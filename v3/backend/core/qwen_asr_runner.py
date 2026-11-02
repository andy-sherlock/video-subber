"""Qwen3-ASR cross-validation transcription via MLX (Apple Silicon) or PyTorch (Windows).

MLX backend (mlx-qwen3-asr): returns word-level timestamps via internal FA.
PyTorch backend (qwen-asr): returns text only, L2 assigns to windows by stem similarity.

For audio longer than 20 minutes, chunks are formed at Whisper segment boundaries
(batched into ~5-minute groups) and each chunk is transcribed independently.
"""
import difflib
from dataclasses import dataclass
import gc
import json
import logging
import os
import subprocess
import tempfile
import time
import unicodedata
from pathlib import Path

from backend.core.checkpoint import UnsafeCachePath, ensure_no_symlink

from backend.core.safe_archive import write_json_archive

_log = logging.getLogger("qwen_asr_runner")

# ── Chunking constants ──
_CHUNK_MAX_DURATION_S = 300       # 5 minutes per chunk
_LONG_AUDIO_THRESHOLD_S = 1200    # 20 minutes — switch to chunked mode
_AUDIO_PAD_S = 5.0                # pronunciation buffer on each side
_FFPROBE_DURATION_TIMEOUT = 30
_FFMPEG_CHUNK_TIMEOUT = 300

_USE_MLX = False
_USE_PYTORCH = False
# Placeholders so these names always exist as module attributes even when the
# MLX import fails (no Metal). Functions reference them and tests patch them;
# a real import below overwrites them on Apple Silicon.
_mlx_transcribe = None
# Imports can raise beyond ImportError (mlx raises RuntimeError with no Metal
# device); catch broadly so a missing/unusable backend can't crash module import
# and break test collection — just fall through to the other backend.
try:
    from mlx_qwen3_asr import transcribe as _mlx_transcribe  # noqa: F401
    _USE_MLX = True
except Exception:
    pass
try:
    from qwen_asr import Qwen3ASRModel  # noqa: F401
    _USE_PYTORCH = True
except Exception:
    pass


def _audio_extract_bounds(start_s: float, end_s: float) -> tuple[float, float]:
    """Return the padded ffmpeg extraction range for one ASR chunk."""
    extract_start = max(0.0, start_s - _AUDIO_PAD_S)
    extract_end = end_s + _AUDIO_PAD_S
    return extract_start, extract_end


def is_qwen_asr_available() -> bool:
    return _USE_MLX or _USE_PYTORCH


@dataclass
class QwenAsrOutcome:
    """Qwen 转录的结果 **+ 它覆盖没覆盖完整输入**。

    P464：以前这里只返回一个块列表，上层无从判断「这是全部，还是只有开头」。
    于是模型加载失败时，已经转好的前半段被当成完整字幕交付、任务显示「完成」，
    用户看到后半段凭空消失，没有任何东西告诉他这是残缺的。

    **`complete` 与 `chunks` 是两件事，不能互相推导**：
    全片没人说话时 `chunks` 为空但转录**是完整的**；
    线程还没跑时 `chunks` 也是空的，但那是**未知**。
    「未知」和「很小」必须分开表达——本项目已经在采样时长上栽过一次同形状的跟头。
    """
    chunks: list
    complete: bool
    completed_until: float | None = None
    failure_reason: str | None = None

    @classmethod
    def not_run(cls) -> "QwenAsrOutcome":
        """还没跑过。**不是「跑完了但没结果」**——后者的 complete 为 True。

        **`completed_until` 留空是这里唯一的身份标记**：
        `None` = 压根没动手（未安装 / 线程没跑）→ 正常路由，不提示用户；
        `0.0`  = 动手了但一秒都没成（模型加载失败等）→ 是故障，必须说。
        两者都 `chunks=[] complete=False`，**靠这个字段区分**。
        """
        return cls(chunks=[], complete=False, failure_reason="尚未运行")


def chunks_are_complete(done_idx: int, results: list, time_chunks: list) -> bool:
    """转录是否覆盖了完整输入——**只看 checkpoint 账本，不看交付了几条字幕**。

    ⚠️ **不能用过滤后的返回列表长度来判断**：没有人说话的块合法为空，
    拿列表长度比会把带长静音的视频误判成失败。

    三个条件缺一不可，第二条防的是账本自相矛盾的坏 checkpoint。
    """
    planned = len(time_chunks or [])
    if not 0 <= done_idx <= planned:
        return False
    if len(results or []) != done_idx:
        return False
    return done_idx == planned


def qwen_provides_word_timings() -> bool:
    """当前 Qwen 后端能不能给出**词级时间**。

    字幕 = 文字 + 时间。**给不了时间的后端不具备主引擎资格**——这跟语言无关。
    MLX 分支产出词级时间戳；PyTorch 分支 `raw_segments = []`（见 `_transcribe_chunk`，
    代码注释自己写着 "PyTorch backend: segments is []"）。

    ⚠️ **这个判据只用于主引擎选择，绝不能并进 `is_qwen_asr_available()`。**
    三个调用点是三条性质不同的路径：标准模式 Qwen 当主引擎（要拦），
    精准模式的并行线程与串行兜底只拿它做交叉验证（拦了就是伤害）。
    并进可用性判断会一刀切掉后两条，**而 Mac 上 MLX 恒为真，永远测不出来**。
    """
    return bool(_USE_MLX)


def _load_qwen_model():
    """Load Qwen3-ASR model (MLX or PyTorch depending on platform).

    C3.5 (㉖): the local path comes from the model registry — managed dir
    first, legacy cache read-only fallback. Missing everywhere raises, so
    the runner never silently downloads at task time.
    """
    from backend.model_registry import resolve_model_path
    model_path = resolve_model_path("qwen_asr")
    if _USE_MLX:
        # P515/Z6：交「目录」给识别包，由它按目录加载并缓存。交已加载的对象时，
        # 识别包的分词表一律按默认 repo 名去公共缓存找、找不到就联网。
        return model_path
    else:
        from qwen_asr import Qwen3ASRModel
        return Qwen3ASRModel.from_pretrained(model_path)


# Both backends (MLX mlx-qwen3-asr, PyTorch qwen-asr) require full language
# names ("English"), not short codes ("en").
_LANG_NAME = {
    "zh": "Chinese", "en": "English", "ja": "Japanese",
    "ko": "Korean", "de": "German", "fr": "French",
    "es": "Spanish", "it": "Italian", "pt": "Portuguese",
    "ru": "Russian",
}


def _resolve_language(lang: str | None) -> str | None:
    """Map short language code to full name for v0.0.6 transcribe()."""
    if not lang or lang == "auto":
        return None
    return _LANG_NAME.get(lang, lang)


_DIAGNOSTIC_SENTENCE_ENDERS = frozenset(".!?…。！？")


def _is_unicode_punctuation(character: str) -> bool:
    """Return whether one character belongs to Unicode's punctuation class."""
    return bool(character) and unicodedata.category(character).startswith("P")


def _is_ascii_alnum(character: str) -> bool:
    """Return whether one character is an ASCII letter or digit."""
    return bool(character) and character.isalnum() and character.isascii()


def _punctuation_counts(text: str) -> dict:
    """Count all Unicode punctuation for the diagnostic cache."""
    by_character = {}
    for char in text or "":
        if _is_unicode_punctuation(char):
            by_character[char] = by_character.get(char, 0) + 1
    return {
        "total": sum(by_character.values()),
        "sentence_enders": sum(
            count for char, count in by_character.items()
            if char in _DIAGNOSTIC_SENTENCE_ENDERS
        ),
        "by_character": by_character,
    }


def _apply_punctuation_gap(
    decorated: list,
    previous_index: int | None,
    current_index: int | None,
    previous_text: str,
    current_text: str,
) -> None:
    """Apply one already-classified punctuation gap without changing timing."""
    if previous_index is None:
        if current_index is not None:
            decorated[current_index]["text"] = (
                previous_text + current_text + decorated[current_index]["text"]
            )
        return
    if current_index is None:
        decorated[previous_index]["text"] += previous_text + current_text
        return
    decorated[previous_index]["text"] += previous_text
    decorated[current_index]["text"] = (
        current_text + decorated[current_index]["text"]
    )


def _match_token_with_internal_punctuation(full_text: str, position: int,
                                           token: str) -> int | None:
    """逐字符匹配一个词，允许跳过原文里的**词内标点**；匹配不上返回 None。

    P454：模型返回的词级 token 吞掉了词内标点，`twenty-five` 变成 `twentyfive`。
    这里只放行标点——字母数字对不上一律返回 None，**容忍不得扩大到吞字母**，
    否则「真正的文字对不上」也会被蒙混过去。

    真实任务 1a1b9f8145 全片 7206 词只需放行 33 个字符（`-`×23、`—`×7、`.`×3）。
    游标只前进不回退，因此整趟匹配是线性的，没有回溯，也就没有 ReDoS 的形状。
    """
    cursor = position
    for character in token:
        wanted = character.casefold()
        while (cursor < len(full_text)
               and full_text[cursor].casefold() != wanted
               and _is_unicode_punctuation(full_text[cursor])):
            cursor += 1
        if cursor >= len(full_text) or full_text[cursor].casefold() != wanted:
            return None
        cursor += 1
    return cursor


def _attach_full_text_punctuation(full_text: str, timestamp_segments: list) -> list:
    """Copy punctuation from full text onto matching timed words.

    The timestamp records stay untouched when the words cannot be matched in
    order. In that case the caller keeps the timestamp-only fallback instead
    of inventing sentence boundaries.
    """
    if not full_text or not timestamp_segments:
        return timestamp_segments

    lowered_text = full_text.casefold()
    decorated = [dict(segment) for segment in timestamp_segments]
    in_double_quote = False
    in_single_quote = False

    def _consume_formatting(position: int):
        punctuation = []
        while position < len(full_text):
            char = full_text[position]
            if char.isspace():
                position += 1
                continue
            if _is_unicode_punctuation(char):
                punctuation.append((char, position))
                position += 1
                continue
            break
        return position, punctuation

    def _goes_to_next_word(position: int) -> bool:
        nonlocal in_double_quote, in_single_quote
        character = full_text[position]

        # ASCII quotes are Unicode Po too, but their ownership is stateful and
        # therefore takes precedence over the ordinary Unicode direction table.
        if character == '"':
            if in_double_quote:
                in_double_quote = False
                return False
            in_double_quote = True
            return True
        if character == "'":
            previous = full_text[position - 1] if position > 0 else ""
            following = full_text[position + 1] if position + 1 < len(full_text) else ""
            if not in_single_quote and _is_ascii_alnum(previous):
                return False
            if not in_single_quote:
                in_single_quote = True
                return True
            if _is_ascii_alnum(following):
                return False
            in_single_quote = False
            return False

        return unicodedata.category(character) in {"Ps", "Pi"}

    def _split_punctuation(punctuation):
        if not punctuation:
            return "", ""
        first_next = None
        for index, (_character, position) in enumerate(punctuation):
            goes_to_next = _goes_to_next_word(position)
            if first_next is None and goes_to_next:
                first_next = index
        if first_next is None:
            return "".join(char for char, _ in punctuation), ""
        return (
            "".join(char for char, _ in punctuation[:first_next]),
            "".join(char for char, _ in punctuation[first_next:]),
        )

    def _attach_gap(punctuation, previous_index, current_index):
        if not punctuation:
            return
        previous_text, current_text = _split_punctuation(punctuation)
        _apply_punctuation_gap(
            decorated,
            previous_index,
            current_index,
            previous_text,
            current_text,
        )

    def _without_whitespace(text):
        return "".join(character for character in text if not character.isspace())

    def _log_conservation_error(expected, actual):
        differences = []
        for index in range(max(len(expected), len(actual))):
            expected_character = expected[index:index + 1] or "<EOF>"
            actual_character = actual[index:index + 1] or "<EOF>"
            if expected_character != actual_character:
                differences.append(
                    f"位置 {index}: 输入 {expected_character!r}, 结果 {actual_character!r}"
                )
                if len(differences) >= 5:
                    break
        _log.error(
            "Qwen 挂标点后字符不守恒: %s (输入长度=%d, 结果长度=%d)",
            "; ".join(differences),
            len(expected),
            len(actual),
        )

    cursor = 0
    previous_index = None
    for index, segment in enumerate(timestamp_segments):
        token = str(segment.get("text") or "").strip()
        if not token:
            continue

        position, punctuation = _consume_formatting(cursor)
        token_end = position + len(token)
        if lowered_text[position:token_end] != token.casefold():
            # P454：模型返回的词级 token 会吞掉词内标点
            # （`twenty-five`→`twentyfive`、`plan—yes`→`planyes`、`U.S.`→`US`），
            # 逐字比对必然失配。**只在这里失败之后才重试**，不改成一律容忍：
            # 词表本身带着词内标点时（`rock'n'roll`）走的仍是上面那条原路径。
            token_end = _match_token_with_internal_punctuation(
                full_text, position, token)
            if token_end is None:
                _log.warning("Qwen 完整文本与词级时间无法对应，保留原始词级时间")
                return timestamp_segments
            # 必须换成原文切片，不能只跳过标点——否则那些标点凭空消失，
            # 下面的守恒检查会开火，整块照样退回（等于没修）。
            token = full_text[position:token_end]

        decorated[index]["text"] = token
        _attach_gap(punctuation, previous_index, index)
        previous_index = index
        cursor = token_end

    position, punctuation = _consume_formatting(cursor)
    if position != len(full_text):
        _log.warning("Qwen 完整文本含有未被词级时间覆盖的文字，保留原始词级时间")
        return timestamp_segments
    _attach_gap(punctuation, previous_index, None)

    expected = _without_whitespace(full_text)
    actual = _without_whitespace(
        "".join(str(segment.get("text") or "") for segment in decorated)
    )
    if actual != expected:
        _log_conservation_error(expected, actual)
        return timestamp_segments

    return decorated


class ChunkTranscriptionError(RuntimeError):
    """长音频某一段识别失败。不交付残缺字幕，直接中止本次运行。"""


def _fmt_clock(seconds: float) -> str:
    """把秒变成 mm:ss，用于告诉用户是哪一段失败了。"""
    total = int(float(seconds or 0.0))
    return f"{total // 60:d}:{total % 60:02d}"


def _assert_monotonic_word_stream(word_timestamps: list) -> None:
    """词流 start 必须单调不减，否则拒绝写盘。

    判据与下游 sentence_groups._copy_and_validate_words 保持一致
    （它用 start < previous_start 判倒退，相等合法）。
    """
    previous = None
    for index, word in enumerate(word_timestamps or []):
        try:
            start = float(word["start"])
        except (TypeError, KeyError, ValueError):
            continue
        if previous is not None and start < previous:
            raise ValueError(
                f"Qwen 诊断词流时间倒退：第 {index} 个词从 {previous:.2f} 秒 "
                f"跳回 {start:.2f} 秒，已拒绝写入 qwen_raw.json"
            )
        previous = start


def _write_qwen_raw_diagnostic(
    cache_dir: str | None,
    *,
    full_text: str,
    word_timestamps: list,
    audio_duration: float,
    model_call_count: int,
    input_ranges: list[dict],
) -> None:
    """Save raw Qwen evidence without changing the transcription result."""
    if not cache_dir:
        return

    # P393/A6：分块路径要把块内相对时间换算回全局时间，那是全流程唯一一次
    # 坐标系变换，也是这个文件唯一会被写坏的方式。以前没有出口检查，坏数据
    # 一路传到断句才被 sentence_groups 拦下，用户看到的「词级时间顺序倒退」
    # 与真正出错的环节隔了两分钟和好几个模块（P393 因此躺了 17 天）。
    # 这里 fail-fast：查出倒退就不写，坏文件根本不产生。
    # ⑥/S2：消息只允许索引与时间数值，禁止拼入词文本——它会经错误通道到达界面。
    _assert_monotonic_word_stream(word_timestamps)

    payload = {
        "full_text": full_text or "",
        "word_timestamps": word_timestamps or [],
        "audio_duration": audio_duration,
        "model_call_count": model_call_count,
        "input_ranges": input_ranges,
        "punctuation_counts": _punctuation_counts(full_text or ""),
    }
    cache_path = Path(cache_dir)
    target_path = cache_path / "qwen_raw.json"
    try:
        # A11：访问缓存目录或目标前统一拒绝预先放置的软链接。
        # write_json_archive 只查目标文件本身，查不到缓存目录被换成软链接的情况。
        ensure_no_symlink(cache_path, target_path)
        cache_path.mkdir(parents=True, exist_ok=True)
    except (OSError, UnsafeCachePath):
        _log.warning("Qwen 原始诊断保存失败", exc_info=True)
        return
    # P391/A9：原来是裸写。写到一半被打断会留下半截文件，而它是 P390 断句的
    # 唯一数据源，坏了整个任务就卡在断句这一步。改用存档公共层原子写。
    if not write_json_archive(
        os.path.join(cache_dir, "qwen_raw.json"), payload, indent=2
    ):
        _log.warning("Qwen 原始诊断保存失败")


def _chunked_diagnostic_payload(qwen_chunks: list, language: str) -> dict:
    """Build the diagnostic payload — full_text and word_timestamps always together.

    P393：以前这里是两个独立函数（`_chunked_raw_text` / `_chunked_word_timestamps`），
    调用方可以各挑各的。词那一半挑了 `raw_segments`——**块内相对时间**，
    没经过 `_clip_chunk_segments` 的全局偏移与重叠裁剪，于是第二块起时间跳回 0。

    根因不是「挑错了那一份」，是**那里有两份可以挑，且两份都能跑通**。
    所以合并成一个函数：两半只能一起产出，不允许分别选择。
    下游 sentence_groups 要求 full_text 与 word_timestamps 逐字配对
    （`:362-367` 全文与词级文字不一致），只洗干净一半会换一个错误接着死。

    ⚠️ 零时长词一律保留（G12 第二批段三删掉了 `drop_zero_duration` 开关）：
    扔掉它们会让长音频比短音频少字（真实素材实测 655 个里 621 个是正常词），
    与「程序丢字就报警」的底线冲突，并且会一并销毁幻觉证据。

    """
    lang = (language or "").lower()
    joiner = "" if lang in ("ja", "zh", "ko", "japanese", "chinese", "korean") else " "
    texts, words = [], []
    for chunk in qwen_chunks:
        if not isinstance(chunk, dict):
            continue
        kept = _chunk_words(chunk)
        words.extend(kept)
        texts.append(_join_segment_texts(kept, language))
    if not words:
        # PyTorch 后端只返回文字、没有词级时间，`kept` 恒为空，按词拼接会得到空全文，
        # 诊断文件就白存了。词流整体为空时下游走不到配对检查
        # （`_copy_and_validate_words` 先抛「词级时间为空」），所以这里回退到模型原文
        # 不会破坏 full_text 与 word_timestamps 的配对约束。
        texts = [(c.get("raw_text") or c.get("text") or "").strip()
                 for c in qwen_chunks if isinstance(c, dict)]
    return {"full_text": joiner.join(t for t in texts if t),
            "word_timestamps": words}


def _chunked_input_ranges(time_chunks: list[tuple[float, float]]) -> list[dict]:
    """Return the padded audio ranges actually handed to the model."""
    return [
        {
            "start": _audio_extract_bounds(t_start, t_end)[0],
            "end": _audio_extract_bounds(t_start, t_end)[1],
        }
        for t_start, t_end in time_chunks
    ]





# ═══════════════════════════════════════════════════════════════════
# Long-audio chunking helpers
# ═══════════════════════════════════════════════════════════════════

def _get_audio_duration(audio_path: str) -> float:
    """Get audio duration in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        audio_path,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=_FFPROBE_DURATION_TIMEOUT,
        )
        if result.returncode != 0:
            raise ValueError(result.stderr)
        return float(result.stdout.strip())
    except (ValueError, AttributeError, subprocess.TimeoutExpired):
        _log.warning("ffprobe 无法获取音频时长: %s", audio_path)
        return 0.0


def _chunk_by_time(total_duration, max_duration=_CHUNK_MAX_DURATION_S):
    """Split audio into fixed-interval time chunks (no Whisper segments needed)."""
    chunks = []
    t = 0.0
    while t < total_duration:
        end = min(t + max_duration, total_duration)
        chunks.append((t, end))
        t = end
    return chunks


def _chunk_segments(segments, max_duration=_CHUNK_MAX_DURATION_S):
    """Group segments into batches of ~5 minutes each.

    Returns list of (start_idx, end_idx, start_time, end_time) tuples.
    end_idx is exclusive.  Batches break at natural segment boundaries
    (never mid-segment), so each chunk starts and ends on a sentence break.

    Args:
        segments: list of objects with .start / .end attributes (seconds).
        max_duration: target max seconds per chunk (default 300 = 5 min).
    """
    if not segments:
        return []

    chunks = []
    batch_start = 0
    batch_start_time = segments[0].start

    for i in range(1, len(segments)):
        batch_duration = segments[i].end - batch_start_time
        if batch_duration >= max_duration:
            chunks.append((
                batch_start, i,
                batch_start_time,
                segments[i - 1].end,
            ))
            batch_start = i
            batch_start_time = segments[i].start

    # ── Final batch ──
    if batch_start < len(segments):
        chunks.append((
            batch_start, len(segments),
            batch_start_time,
            segments[-1].end,
        ))

    return chunks


def _extract_audio_chunk(audio_path: str, start_s: float, end_s: float,
                         output_path: str) -> None:
    """Extract an audio slice via ffmpeg with 5 s padding on each side.

    The padding is a pronunciation buffer — it ensures words at the edges
    are fully captured, without providing semantic context (which is not
    needed for transcription).
    """
    extract_start, extract_end = _audio_extract_bounds(start_s, end_s)
    duration = extract_end - extract_start

    cmd = [
        "ffmpeg", "-nostdin", "-y",
        "-ss", str(extract_start),
        "-t", str(duration),
        "-i", audio_path,
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        output_path,
    ]
    try:
        subprocess.run(
            cmd,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=_FFMPEG_CHUNK_TIMEOUT,
            check=True,
        )
    except subprocess.TimeoutExpired as exc:
        try:
            os.unlink(output_path)
        except FileNotFoundError:
            pass
        raise RuntimeError(
            f"音频切块超时（超过 {_FFMPEG_CHUNK_TIMEOUT} 秒）。请重新提交任务。"
        ) from exc
    except subprocess.CalledProcessError as exc:
        try:
            os.unlink(output_path)
        except FileNotFoundError:
            pass
        stderr_tail = (exc.stderr or b"")[-300:]
        if isinstance(stderr_tail, bytes):
            stderr_tail = stderr_tail.decode("utf-8", errors="replace")
        raise RuntimeError(f"音频切块失败: {stderr_tail}") from exc


def _clip_chunk_segments(raw_segments: list, extract_start: float,
                         t_start: float, t_end: float) -> list:
    """把块内相对时间映射到整段音频，并裁掉落在块外的部分。

    **G12 第二批段三：`drop_zero_duration` 开关已删除，零时长词一律保留。**

    原来那一行 `if end <= start: continue` 同时干两件事——裁边界，
    **顺便**扔掉模型给出的零时长词（`start == end`）。两个职责缠在一起，
    于是长视频路径扔、短视频路径留，同一件事两套行为。

    「零时长」的含义：模型输出一个词时给了开始和结束时间，两个填成同一个数，
    等于**模型自己也不知道这个词是什么时候说的**。那是证据，不是垃圾。

    两个真实任务实测 655 个零时长词：
      成串的（幻觉）   34 个（5%）
      散落的（正常词） 621 个（95%），含 airport／bridge／climbing／Excellent
                       等 42 个长度 >4 的实词

    **扔掉它们等于两件事一起做错**：长视频永久少字（P430 的原始主诉），
    同时销毁幻觉证据——过滤器只看到 2 个孤立的 `Oh`，
    看不出那是一次连续 20 次的幻觉。

    **P430 要求的对照实验已完成**（拖了三个月）：把零时长词加进 L1 的对齐证据，
    两个真实任务**各 0 条改判**。开关整个不需要。

    成串的幻觉由谁接管：
      · 全部落在同一瞬间的段（`start == end`）→ `_layer3_speech_rate` 的 `dur <= 0`
      · 跨段的同词游程 → `hallucination_filter.find_impossible_instant_runs`
    """
    segments = []
    for s in raw_segments:
        abs_start = float(s["start"]) + extract_start
        abs_end = float(s["end"]) + extract_start
        if abs_end <= t_start or abs_start >= t_end:
            continue
        start = max(abs_start, t_start)
        end = min(abs_end, t_end)
        if end <= start:
            end = start
        segments.append({"text": s["text"], "start": start, "end": end})
    return segments


def _chunk_words(chunk: dict) -> list:
    """挂标点 → 加全局偏移 → 裁重叠。这个序列全项目只此一份。

    P393：`_transcribe_chunk` 和诊断汇总都需要走这三步，只有最后一个参数不同。
    若各写一份，两处相距约 200 行，今天一致、将来改一处就漂移——
    那正是本 bug 的形状（同一件事有两份，一份忘了改）的第七次复发，
    而且是最难发现的一种：不会立刻失败，只会静默降低长视频字幕质量。

    标点只存在于整串 `raw_text` 里，把它映射到词上的唯一手段就是
    `_attach_full_text_punctuation`，所以挂标点必须在裁剪之前。
    """
    return chunk_words_checked(chunk)[0]


def attach_full_text_punctuation_checked(
        full_text: str, timestamp_segments: list) -> tuple[bool, list]:
    """挂标点，并**明确告诉调用方成没成功**。

    P457：`_attach_full_text_punctuation` 失败时原样返回输入——一个看起来成功的
    返回值。调用方无从区分「挂上了」和「没挂上」，于是也无从按块记账，
    十块全失败和一块失败在数据里长得一模一样。这里把两态分开。

    没有正文或没有词时返回 `True`：那是「没有可挂的」，不是「挂失败了」。
    """
    if not full_text or not timestamp_segments:
        return True, timestamp_segments
    result = _attach_full_text_punctuation(full_text, timestamp_segments)
    return result is not timestamp_segments, result


def chunk_words_checked(chunk: dict) -> tuple[list, bool]:
    """`_chunk_words` 的两态版本：同时交出词流和本块的挂标点结果。"""
    raw_segments = chunk.get("raw_segments")
    if raw_segments is None:
        # 旧格式 checkpoint 或 PyTorch 路径：没有词级时间。
        # 这条路完全绕过挂标点，因此记为「没挂上」，不是「没有可挂的」。
        return list(chunk.get("segments") or []), False
    extract_start, _extract_end = _audio_extract_bounds(
        float(chunk.get("t_start") or 0.0), float(chunk.get("t_end") or 0.0))
    attached, punctuated = attach_full_text_punctuation_checked(
        chunk.get("raw_text") or "", raw_segments)
    return _clip_chunk_segments(
        punctuated, extract_start,
        float(chunk.get("t_start") or 0.0),
        float(chunk.get("t_end") or 0.0)), attached


def _join_segment_texts(segments: list, language: str) -> str:
    lang = (language or "").lower()
    joiner = "" if lang in ("ja", "zh", "ko", "japanese", "chinese", "korean") else " "
    return joiner.join((s.get("text") or "").strip()
                       for s in segments if (s.get("text") or "").strip())


def _release_recognizer_cache():
    """⑦.5 R3（用户定）：识别包会把按目录加载的识别模型、对齐模型留在自己的缓存里备用，
    实测识别完仍占约 6.55 GB（改前我们自己加载、用完即放，只常驻对齐模型 1.84 GB）。
    识别结束让它放掉。清理方法不公开：找得到才调，找不到就跳过（包版本由锁文件固定）。"""
    if not _USE_MLX:
        return
    try:
        from mlx_qwen3_asr.load_models import _ModelHolder
    except Exception:
        return
    clear = getattr(_ModelHolder, "clear", None)
    if callable(clear):
        clear()
    try:
        import mlx.core as mx
        if callable(getattr(mx, "clear_cache", None)):
            mx.clear_cache()
    except Exception:
        pass


def _managed_aligner_path():
    """P515：MLX 识别包默认按 repo 名去公共缓存找对齐模型，找不到就联网下载。
    一律交给它「一键准备」下好的自管目录，运行时不联网（P320）。"""
    from backend.model_registry import resolve_model_path
    return resolve_model_path("qwen_fa")


def _transcribe_chunk(model, audio_path, t_start, t_end, language,
                      chunk_idx=0, total_chunks=1):
    """Transcribe one audio chunk.

    MLX: returns {text, segments, t_start, t_end} with word-level timestamps.
    PyTorch: returns {text, t_start, t_end} (no timestamps).
    Segments' start/end are offset by the padded extract_start to map to the
    full audio timeline, then clipped back to the requested chunk bounds.
    """
    fd, chunk_path = tempfile.mkstemp(
        suffix=".wav", prefix=f"qwen_chunk_{chunk_idx}_")
    os.close(fd)
    try:
        extract_start, _extract_end = _audio_extract_bounds(t_start, t_end)
        _extract_audio_chunk(audio_path, t_start, t_end, chunk_path)
        if _USE_MLX:
            result = _mlx_transcribe(
                chunk_path,
                model=model,
                language=_resolve_language(language),
                return_timestamps=True,
                forced_aligner=_managed_aligner_path(),
            )
            raw_full_text = result.text or ""
            full_text = raw_full_text
            raw_segments = result.segments or []
            # P393：挂标点→偏移→裁剪的序列统一走 _chunk_words，全项目只此一份。
            # L1 消费这里的 segments，维持历史行为（扔掉零时长词）。
            segments = _chunk_words(
                {"raw_text": raw_full_text, "raw_segments": raw_segments,
                 "t_start": t_start, "t_end": t_end},
                )
            # P411/C6：这里的 full_text 是第一遍按当前块反向拼出的局部字段。
            # 若第一遍自检退回未挂标点词流，这个字段可以暂时缺少标点；原始全文
            # 仍由 raw_full_text 写入 qwen_raw.json，第二遍从完整原文重新挂标点。
            # 不要把这个局部降级字段当成最终字幕原文，也不要在这里跨块传递引号状态。
            full_text = _join_segment_texts(segments, language)
        else:
            result = model.transcribe(
                chunk_path,
                language=_resolve_language(language),
            )
            raw_full_text = result[0].text if result else ""
            full_text = raw_full_text
            raw_segments = []
            segments = []

        if not full_text or not full_text.strip():
            _log.info("Qwen3-ASR chunk %d/%d 无文字, 跳过",
                      chunk_idx + 1, total_chunks)
            return {"text": "", "raw_text": raw_full_text,
                    "raw_segments": raw_segments, "segments": [],
                    "t_start": t_start, "t_end": t_end}

        _log.info("Qwen3-ASR chunk %d/%d 完成 (%.1fs–%.1fs, %d segments)",
                  chunk_idx + 1, total_chunks, t_start, t_end, len(segments))
        # 进度百分比
        if total_chunks > 0:
            pct = min(99, round((chunk_idx + 1) / total_chunks * 100))
            _log.info("Qwen3-ASR 进度: %d%% (%d/%d chunks)", pct, chunk_idx + 1, total_chunks)
        return {"text": full_text, "raw_text": raw_full_text,
                "raw_segments": raw_segments, "segments": segments,
                "t_start": t_start, "t_end": t_end}
    except Exception as exc:
        _log.warning("Qwen3-ASR chunk %d/%d 失败",
                     chunk_idx + 1, total_chunks, exc_info=True)
        # G7：以前这里返回一个空块让流程继续，最终交付一份中间缺 5 分钟、
        # 却看起来完整的字幕，用户毫不知情。那个行为从来没有被决策过——
        # 它只是「别让程序崩」的默认结果。
        # 现在直接抛出去：长音频不交付残缺成品。已成功的块留在 checkpoint 里，
        # 用户重试时从这一块继续，前面的不重跑。
        raise ChunkTranscriptionError(
            f"语音识别第 {chunk_idx + 1} 段（{_fmt_clock(t_start)}–"
            f"{_fmt_clock(t_end)}）失败"
        ) from exc
    finally:
        try:
            os.unlink(chunk_path)
        except OSError:
            pass


def _qwen_chunk_ckpt_path(cache_dir: str | None) -> str | None:
    if not cache_dir:
        return None
    return os.path.join(cache_dir, "qwen_chunks.json")


def _refresh_chunk_delivery(qwen_chunks: list, language) -> list:
    """用**当前**的挂标点规则重算每块的正式 `text`/`segments`。

    A9：缓存里存的 `text`/`segments` 是当初算好的。全缓存命中、模型加载失败、
    以及断点续跑完成这三个返回点原本直接把它们交出去，**完全绕过 `_chunk_words`**。
    而同一分支里的诊断路径走 `_chunked_diagnostic_payload` → `_chunk_words`，会重算。

    于是改好挂标点算法之后会出现最难发现的一种假象：
    **诊断文件显示标点全回来了，正式字幕仍是一个都没有。**
    提高转录合同版本让 `transcript.json` 作废、强制重转录，恰好把执行推进这些分支，
    而验收往往就在有旧缓存的那台机器上做——第八个「代码改了、结果没变」。

    没有 `raw_segments` 的块（旧格式 checkpoint / PyTorch 路径）没有可重算的原始材料，
    但它们**确实完全绕过了挂标点**，所以照样记成「没挂上」——
    ⑦.5 读代码时发现原实现直接放行、不设字段，会被当成挂上了，用户不会被告知。
    """
    refreshed = []
    for index, chunk in enumerate(qwen_chunks or []):
        if not isinstance(chunk, dict):
            # ⑦.5：这里原来是静默 continue。改动前那行是
            # `[c for c in qwen_chunks if c.get(...)]`，非字典会抛 AttributeError
            # 当场炸——**别把响亮的崩溃换成无声丢失一整块（5 分钟）字幕。**
            _log.warning("Qwen 第 %d 块不是合法结果（%s），已丢弃这一段字幕",
                         index, type(chunk).__name__)
            continue
        if not chunk.get("raw_segments"):
            # 没有可重算的原始材料。两种真实形状都走这里：
            #   `None` —— 旧格式 checkpoint
            #   `[]`   —— **Windows 的 PyTorch 分支**（:719 硬编码返回空词表）
            #
            # **P463：原判据写的是 `is None`，空列表滑过去继续往下走，
            # 正文被空词表重建成 ""，整块随后被三个返回点的 `if text.strip()` 滤掉
            # ——这是 Windows 整片字幕消失的最后一步。**
            #
            # 空列表**不是**「没有文字」，只是「没有词级时间」。正文原样留下，
            # 供精准模式的交叉验证使用；能不能当正式字幕由主引擎路由决定，不在这里判。
            bypassed = dict(chunk)
            bypassed["punctuation_attached"] = False
            refreshed.append(bypassed)
            continue
        words, attached = chunk_words_checked(chunk)
        updated = dict(chunk)
        updated["segments"] = words
        updated["text"] = _join_segment_texts(words, language)
        # P457：把「这一块挂上了没有」写进块本身。
        # 只留一行日志的后果已经验证过：真实任务打了 20 次警告，三个月无人发现，
        # 而且十块全失败和一块失败在数据里长得一模一样。
        updated["punctuation_attached"] = attached
        refreshed.append(updated)
    # P465：逐块重算做完之后，再做**跨块**的接缝去重。
    #
    # **落点必须在这里，不能在转录循环里。** 三个返回点
    # （:919 全缓存命中 / :934 模型加载失败 / :972 正常完成）都经过本函数，
    # 而本批同时会把 `_TRANSCRIPTION_CONTRACT_VERSION` +1 作废 transcript.json
    # ——那恰好把执行推进全缓存命中分支。写在循环里的话，
    # **老任务重跑一行都不会执行，而且表面完全正常**（同 A9 那个陷阱）。
    refreshed = _merge_chunk_seams(refreshed)
    missing = count_chunks_missing_punctuation(refreshed)
    if missing:
        _log.warning("Qwen 挂标点失败 %d/%d 块，这些块的字幕将缺少句末标点",
                     missing, len(refreshed))
    return refreshed


def count_chunks_missing_punctuation(qwen_chunks: list) -> int:
    """数有几块没挂上标点。没有这个字段的块按「挂上了」算（旧数据兼容）。

    **只数会进正式词流的块**（有词级时间的）。

    G12 第三批 A：P463 修完后，Windows 精准模式仍保留 Qwen 做交叉验证，
    那些块 `raw_segments=[]` → 被标 `punctuation_attached=False`，
    但**它们根本不产出字幕**（Whisper 才是正式来源）。
    照旧数会得到「10 块缺少句末标点」，而那 10 块的字幕压根不存在——**假警报**。

    **与第二批那一刀同形状**：无障碍标记「音符开火 22 次」，
    真跑发现全是 `words=0`、根本进不了成品的段。
    判一条统计该不该报，要问**它数的东西到不到得了用户手里**。

    ⚠️ **两个字段都要看，缺一不可**——真警报的形状不止一种：
      · 短音频路径的块只有 `segments`，**没有 `raw_segments` 字段**；
      · 长音频挂标点失败的块有 `raw_segments`，而 `segments` 可能为空。
    只看其中一个都会把一类真警报收掉（两种都被真实测试当场抓到过）。
    只有**两个都空**的块（Windows PyTorch 辅助块）才真的产不出字幕。
    """
    return sum(
        1 for chunk in (qwen_chunks or [])
        if isinstance(chunk, dict)
        and chunk.get("punctuation_attached") is False
        and (chunk.get("segments") or chunk.get("raw_segments"))
    )


# 接缝比对要忽略的**首尾**标点。英文 + 中日韩全角，两套都要有。
#
# 外审④：原来只有英文那一套 `" ,.!?\"'"`。于是正式词流里的 `好。`
# 和原始词流里的 `好` 对不上，**中文和日文的句末重复词一个都删不掉**——
# 而这个项目的主力语言正是中日韩（`QWEN_PRIMARY_LANGS`）。
# ⑩ 修的是同一个 bug 的英文那一半：想到了挂标点会改写文字，
# 补的字符集却只有英文。
_SEAM_TRIM_CHARS = " ,.!?\"'" + "。、，！？；：、「」『』（）《》…—·"


def _seam_norm(text: str) -> str:
    """接缝对齐用的词规范化：小写 + 去**首尾**标点（英文与中日韩全角都算）。

    **不做 Unicode 归一化，不去词内标点**——`we're`、`twenty-five` 的标点
    是真实内容，去掉会把两个不同的词拉成同一个。
    只 `strip` 首尾，所以词内的撇号、连字符一个都不会动。
    规范化结果**只用于比较**，写回成品的始终是原始文字。
    """
    return text.lower().strip(_SEAM_TRIM_CHARS)


def _seam_abs_words(chunk: dict):
    """取一块**裁剪前**的词，时间换算成整段音频的全局绝对时间。

    **为什么必须用裁剪前的**：裁剪会把跨界词截断到分界线
    （左块 `end=bd`、右块 `start=bd`），三组真实重复词裁剪后首尾相接，
    任何基于时间重叠的判断都会永远判「不重复」——那是个跑不生效的空壳。
    """
    raw = chunk.get("raw_segments")
    if raw is None:
        return None
    es = max(0.0, float(chunk.get("t_start") or 0.0) - _AUDIO_PAD_S)
    return [(s["text"], float(s["start"]) + es, float(s["end"]) + es) for s in raw]


def _merge_one_seam(left: dict, right: dict) -> int:
    """合并**一个**接缝，返回删掉的重复词数。

    只删两种情况：① 对齐为 `equal` 的相同词；
    ② **紧邻前后都有非空 `equal` 支撑**的一词换一词（`hoppyer`/`hopier`）。
    多词对多词、一词对多词、只有单侧上下文——一律不删。
    """
    bd = float(left.get("t_end") or 0.0)
    lwords = _seam_abs_words(left)
    rwords = _seam_abs_words(right)
    if not lwords or not rwords:
        return 0  # 没有原始材料可比对 → 两边都保留

    lo, hi = bd - _AUDIO_PAD_S, bd + _AUDIO_PAD_S
    lwin = [w for w in lwords if w[2] >= lo and w[1] <= hi]
    rwin = [w for w in rwords if w[2] >= lo and w[1] <= hi]
    if not lwin or not rwin:
        return 0

    lcross = {k for k, w in enumerate(lwin) if w[1] < bd < w[2]}
    rcross = {k for k, w in enumerate(rwin) if w[1] < bd < w[2]}
    if not lcross or not rcross:
        return 0  # 没有两边都保留的跨界词，不存在重复

    # autojunk=False 锁死行为：默认 True 会在序列长度 >=200 时忽略高频元素。
    # 实测 ±5 秒窗口最长 40 个词，当前触发不到——**显式写死是防窗口/素材变化后漂移**。
    ops = difflib.SequenceMatcher(
        None, [_seam_norm(w[0]) for w in lwin], [_seam_norm(w[0]) for w in rwin],
        autojunk=False).get_opcodes()

    pair = {}
    for n, (tag, a1, a2, b1, b2) in enumerate(ops):
        if tag == "equal":
            for k in range(a2 - a1):
                pair[a1 + k] = b1 + k
        elif tag == "replace" and (a2 - a1) == 1 and (b2 - b1) == 1:
            prev_ok = n > 0 and ops[n - 1][0] == "equal" and ops[n - 1][2] > ops[n - 1][1]
            next_ok = (n + 1 < len(ops) and ops[n + 1][0] == "equal"
                       and ops[n + 1][2] > ops[n + 1][1])
            if prev_ok and next_ok:
                pair[a1] = b1

    victims = []
    for k in sorted(lcross):
        j = pair.get(k)
        if j is None or j not in rcross:
            continue
        # C6：左右候选都必须已经进入裁剪后的正式词流，才有「重复」可言。
        #
        # **比对必须过 `_seam_norm`。** `raw_segments` 里是 `nice`，
        # 正式词流里挂完标点已经变成 `nice.` ——拿原始文字做字符串相等，
        # 找不到，于是**只有挂了标点的重复词漏网，也就是句末的词**。
        # ⑩ 在第二支长视频（80703166ff）上抓到：5 个接缝漏了 2 个，
        # 拼出来是 `All right, nice. nice. Yeah` 和 `funny friends, friends, but`。
        ltext, rtext = _seam_norm(lwin[k][0]), _seam_norm(rwin[j][0])
        if not any(_seam_norm(s.get("text") or "") == ltext
                   and abs(float(s.get("end", -1)) - bd) < 1e-6
                   for s in (left.get("segments") or [])):
            continue
        victims.append(rtext)

    if not victims:
        return 0

    kept, removed = [], 0
    for s in (right.get("segments") or []):
        # 同上：`victims` 装的是归一化后的词，右块正式词流的文字同样挂过标点。
        key = _seam_norm(s.get("text") or "")
        if (removed < len(victims) and key in victims
                and abs(float(s.get("start", -1)) - bd) < 1e-6):
            victims.remove(key)
            removed += 1
            continue
        kept.append(s)
    right["segments"] = kept
    return removed


def _merge_chunk_seams(qwen_chunks: list) -> list:
    """P465：删掉五分钟接缝处被两块各识别一次的重复词。

    **一句话**：接缝两侧的词排成两队做序列对齐；对齐上的删右块那份，
    对不上的两边都留。**靠上下文，不靠时间**——两次独立识别的时间会漂移、
    错位、甚至出现零时长，任何阈值都会在某个真实接缝上翻车。

    **逐接缝独立**：单个接缝异常只让**那一个**退回「两边都保留」，
    不得中断或跳过其余接缝。9 个接缝是 9 个独立分叉——
    P404 最狠的一处正是让一次无关失败拖垮了真正要处理的那一半。
    """
    chunks = [c for c in (qwen_chunks or [])]
    total = 0
    for i in range(len(chunks) - 1):
        left, right = chunks[i], chunks[i + 1]
        if not isinstance(left, dict) or not isinstance(right, dict):
            continue
        try:
            total += _merge_one_seam(left, right)
        except Exception:
            # 这一个接缝退回「两边都保留」——宁可留一处重复，也不让它拖垮其余接缝。
            _log.warning("接缝 %s 合并失败，该接缝保留两边原样",
                         left.get("t_end"), exc_info=True)
    if total:
        _log.info("接缝去重: 删掉 %d 个跨块重复词", total)
    return chunks


def _load_qwen_chunk_ckpt(ckpt_path: str | None, time_chunks: list) -> tuple[list, int]:
    """Load saved Qwen chunk results. Returns (saved_chunks, resume_index).

    Validates that saved chunks match the current time_chunks plan — if the
    chunking changed (different audio, different segment boundaries), the
    checkpoint is discarded.
    """
    if not ckpt_path or not os.path.exists(ckpt_path):
        return [], 0
    try:
        with open(ckpt_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        saved_plan = data.get("time_chunks", [])
        saved_chunks = data.get("results", [])
        done_idx = data.get("done_idx", 0)
        if len(saved_plan) != len(time_chunks):
            _log.info("Qwen 分段计划变更 (%d→%d chunks)，丢弃旧 checkpoint",
                      len(saved_plan), len(time_chunks))
            return [], 0
        for i, (saved, current) in enumerate(zip(saved_plan, time_chunks)):
            if abs(saved[0] - current[0]) > 0.1 or abs(saved[1] - current[1]) > 0.1:
                _log.info("Qwen chunk %d 时间不匹配，丢弃旧 checkpoint", i)
                return [], 0
        # P393/A7：诊断词流改为从 raw_segments 重算后，缺这两个字段的旧格式
        # checkpoint 会算出空词流，把「时间倒退」换成「词级时间为空」——
        # 对用户同样是拿不到字幕。沿用既有的「计划变更就整份丢弃」处理方式。
        if any(not isinstance(c, dict)
               or "raw_segments" not in c or "raw_text" not in c
               for c in saved_chunks[:done_idx]):
            _log.info("Qwen checkpoint 缺少原始词字段（旧格式），丢弃并重跑")
            return [], 0
        # 外审①：账本（`done_idx`）和货（`results` 长度）来自同一个文件的
        # 两个字段，**原来从来没有互相核对过**。真实形状有两种：
        #   · 账本 10、实际 6 —— 调用方 `resume_idx >= len(time_chunks)` 成立，
        #     跳过全部转录，把 6 块当完整成品交出去。**P464 原病换了个入口。**
        #   · 账本 6、实际 10 —— 写入顺序是先写 results 后更新 done_idx，
        #     中途被打断就是这样；多出来的块没经过完成确认，不能算数。
        #
        # **修在返回值本身（第 1 层）**：让这个函数不可能交出对不上的一对值，
        # 下游任何消费者都不必再自己提防。取两者较小的那个，
        # 已经转好的块全部保留——不丢用户已经等到的成果。
        honest_idx = min(done_idx, len(saved_chunks))
        if honest_idx != done_idx:
            _log.warning(
                "Qwen checkpoint 账本与实际不符（账本 %d、实际存下 %d），按 %d 续跑",
                done_idx, len(saved_chunks), honest_idx)
        saved_chunks = saved_chunks[:honest_idx]
        # 日志说实话：原来这里打的是 `done_idx`，账本吹牛时它跟着吹
        # ——真实复现里它打的是「恢复 10/10 chunks」，而磁盘上只有 6 块。
        _log.info("Qwen 断点续跑: 恢复 %d/%d chunks", honest_idx, len(time_chunks))
        return saved_chunks, honest_idx
    except Exception:
        _log.warning("Qwen chunk checkpoint 损坏，从头开始", exc_info=True)
        return [], 0


def _save_qwen_chunk_ckpt(ckpt_path: str | None, time_chunks: list,
                           results: list, done_idx: int) -> None:
    if not ckpt_path:
        return
    data = {
        "time_chunks": list(time_chunks),
        "results": results,
        "done_idx": done_idx,
    }
    # P458：这个函数在分块循环里每块调一次。裸写会先清空旧文件再写，
    # 中途被打断就留下半截 JSON，读取端只能整份丢弃、整片重转录
    # （真实任务实测约 19 分钟）。走存档公共层：先写草稿、落盘、再一瞬间顶替。
    if not write_json_archive(ckpt_path, data):
        _log.warning("Qwen chunk checkpoint 保存失败")


def _run_qwen_asr_chunked(audio_path: str, total_duration: float,
                           segments: list = None, language=None,
                           cache_dir: str = None,
                           progress_callback=None) -> list:
    """Chunked cross-validation for long audio.

    Returns list of {text, segments, t_start, t_end}. MLX results include
    word-level timestamps in segments; PyTorch results have segments=[].

    When segments is provided, chunks at Whisper segment boundaries.
    When segments is None, chunks at fixed 5-minute intervals.

    cache_dir: if provided, saves per-chunk progress to qwen_chunks.json
    so a crashed run can resume from the last completed chunk.
    """
    if segments:
        raw_chunks = _chunk_segments(segments)
        time_chunks = [(t_start, t_end) for (_s, _e, t_start, t_end) in raw_chunks]
        _log.info("Qwen3-ASR 分段交叉验证: %d chunks (%d segments)",
                  len(time_chunks), len(segments))
    else:
        time_chunks = _chunk_by_time(total_duration)
        _log.info("Qwen3-ASR 独立分段: %d chunks (%.0fs 音频)",
                  len(time_chunks), total_duration)

    ckpt_path = _qwen_chunk_ckpt_path(cache_dir)
    qwen_chunks, resume_idx = _load_qwen_chunk_ckpt(ckpt_path, time_chunks)

    if progress_callback and resume_idx > 0:
        try:
            progress_callback({
                "completed_segments": resume_idx,
                "processed_duration": float(time_chunks[resume_idx - 1][1]),
            })
        except Exception:
            pass

    if resume_idx >= len(time_chunks):
        _log.info("Qwen3-ASR 全部 chunks 已有缓存，跳过转录")
        _write_qwen_raw_diagnostic(
            cache_dir,
            audio_duration=total_duration,
            model_call_count=0,
            input_ranges=_chunked_input_ranges(time_chunks),
            **_chunked_diagnostic_payload(qwen_chunks, language),
        )
        # 走到这里的前提就是 resume_idx >= len(time_chunks)——计划的块全在缓存里。
        #
        # **这句话成立，依赖 `_load_qwen_chunk_ckpt` 保证返回值自洽**
        # （账本 `done_idx` 与实际存下的块数取小者）。外审①之前它不保证：
        # 账本说 10、实际 6，`resume_idx` 照样是 10，这里就把 6 块当完整交出去。
        # 旁边两个返回点（:1196 / :1240）调 `chunks_are_complete()` 现算，
        # 这里靠上游的不变量——**动上游那个函数时，这一行会跟着失守。**
        return QwenAsrOutcome(
            chunks=[c for c in _refresh_chunk_delivery(qwen_chunks, language)
                    if c.get("text", "").strip()],
            complete=True,
            completed_until=float(time_chunks[-1][1]) if time_chunks else None)

    t0 = time.time()
    try:
        model = _load_qwen_model()
    except Exception:
        _log.warning("Qwen3-ASR 模型加载失败，跳过交叉验证", exc_info=True)
        _write_qwen_raw_diagnostic(
            cache_dir,
            audio_duration=total_duration,
            model_call_count=0,
            input_ranges=_chunked_input_ranges(time_chunks),
            **_chunked_diagnostic_payload(qwen_chunks, language),
        )
        # P464：这里可能只转了开头几块。**不能把已有块直接当成完整成品交出去**——
        # 那正是「半部字幕冒充完整、任务显示完成」的入口。
        # 完整性只看 checkpoint 账本，不看交付了几条字幕（静音块合法为空）。
        return QwenAsrOutcome(
            chunks=[c for c in _refresh_chunk_delivery(qwen_chunks, language)
                    if c.get("text", "").strip()],
            complete=chunks_are_complete(resume_idx, qwen_chunks, time_chunks),
            completed_until=(float(time_chunks[resume_idx - 1][1])
                             if resume_idx > 0 else 0.0),
            failure_reason="语音识别模型加载失败")
    _log.info("Qwen3-ASR 模型加载耗时 %.1fs", time.time() - t0)

    model_call_count = 0
    # G7：某块失败时 _transcribe_chunk 会抛 ChunkTranscriptionError，循环就此中断。
    # 此时 checkpoint 停在最后一次成功保存的位置，也就是失败块之前——
    # **断点重试是这个中断的自然结果，不需要额外记账**。
    # （曾经为「失败块不推进 done_idx」写过一套前缀判断 + 保存截断，
    #  在失败即中止之后它没有任何输入能触发，已删除。）
    for idx in range(resume_idx, len(time_chunks)):
        t_start, t_end = time_chunks[idx]
        result = _transcribe_chunk(model, audio_path, t_start, t_end,
                                   language, idx, len(time_chunks))
        model_call_count += 1
        qwen_chunks.append(result)
        _save_qwen_chunk_ckpt(ckpt_path, time_chunks, qwen_chunks, idx + 1)
        if progress_callback:
            try:
                progress_callback({
                    "completed_segments": idx + 1,
                    "processed_duration": float(t_end),
                })
            except Exception:
                pass

    del model
    gc.collect()
    _release_recognizer_cache()

    _log.info("Qwen3-ASR 分段交叉验证完成: %d chunks 有文字",
              sum(1 for c in qwen_chunks if c.get("text", "").strip()))
    _write_qwen_raw_diagnostic(
        cache_dir,
        audio_duration=total_duration,
        model_call_count=model_call_count,
        input_ranges=_chunked_input_ranges(time_chunks),
        **_chunked_diagnostic_payload(qwen_chunks, language),
    )
    # 循环正常跑完 → 计划的块全部转完。
    return QwenAsrOutcome(
        chunks=[c for c in _refresh_chunk_delivery(qwen_chunks, language)
                if c.get("text", "").strip()],
        complete=chunks_are_complete(len(time_chunks), qwen_chunks, time_chunks),
        completed_until=float(time_chunks[-1][1]) if time_chunks else None)


# ── Public entry point ──

def _resolve_qwen_duration(audio_path: str, segments: list):
    """拿到总时长：segments 模式取末段 end；独立模式 ffprobe。
    空 segments 或 ffprobe 失败返回 None（主函数据此返回 []）。"""
    if segments is not None:
        if not segments:
            return None
        return segments[-1].end
    try:
        return _get_audio_duration(audio_path)
    except Exception:
        _log.warning("无法获取音频时长，跳过 Qwen 交叉验证")
        return None


def _qwen_transcribe(model, audio_path: str, language):
    """MLX/PyTorch 两后端转录；失败返回 None，成功返回 (full_text, ts_segments)。"""
    t1 = time.time()
    try:
        if _USE_MLX:
            result = _mlx_transcribe(
                audio_path,
                model=model,
                language=_resolve_language(language),
                return_timestamps=True,
                forced_aligner=_managed_aligner_path(),
            )
            detected_lang = result.language
            full_text = result.text or ""
            raw_segments = result.segments or []
            ts_segments = [
                {"text": s["text"], "start": s["start"], "end": s["end"]}
                for s in raw_segments
            ]
        else:
            result = model.transcribe(
                audio_path,
                language=_resolve_language(language),
            )
            detected_lang = result[0].language if result and hasattr(result[0], 'language') else None
            full_text = result[0].text if result else ""
            ts_segments = []
    except Exception as exc:
        # P515：写出真实原因（例如对齐模型加载失败），不再只记一句。
        _log.warning("Qwen3-ASR 转录失败，跳过交叉验证: %s: %s", type(exc).__name__, exc)
        return None
    _log.info("Qwen3-ASR 转录耗时 %.1fs, language=%s, %d segments",
              time.time() - t1, detected_lang or "unknown", len(ts_segments))
    return full_text, ts_segments


def run_qwen_asr(audio_path: str, segments: list = None, language=None,
                  cache_dir: str = None, progress_callback=None) -> list:
    """Return Qwen-ASR output as a list of {text, segments, t_start, t_end}.

    MLX backend: segments contain word-level timestamps [{text, start, end}].
    PyTorch backend: segments is [].
    Returns [] when Qwen is unavailable or produced nothing.

    When segments is None, Qwen runs independently — gets audio duration via
    ffprobe and chunks by fixed time intervals if needed.

    cache_dir: path to the job's versioned cache directory. When provided,
    long-audio chunked mode saves per-chunk progress so a crashed run can
    resume from the last completed chunk.
    """
    if not is_qwen_asr_available():
        _log.warning("Qwen3-ASR 未安装，跳过交叉验证")
        return QwenAsrOutcome(chunks=[], complete=False,
                              failure_reason="Qwen3-ASR 未安装")

    total_duration = _resolve_qwen_duration(audio_path, segments)
    if total_duration is None:
        return QwenAsrOutcome(chunks=[], complete=False,
                              completed_until=0.0,
                              failure_reason="无法确定音频时长")

    if total_duration > _LONG_AUDIO_THRESHOLD_S:
        _log.info("音频 %.0fs > 20min，启用 Qwen 分段对齐", total_duration)
        return _run_qwen_asr_chunked(audio_path, total_duration,
                                     segments=segments, language=language,
                                     cache_dir=cache_dir,
                                     progress_callback=progress_callback)

    t0 = time.time()
    try:
        model = _load_qwen_model()
    except Exception:
        _log.warning("Qwen3-ASR 模型加载失败，跳过交叉验证", exc_info=True)
        # 动手了（加载模型是转录的第一步），只是一秒都没成——不是「没跑过」。
        return QwenAsrOutcome(chunks=[], complete=False,
                              completed_until=0.0,
                              failure_reason="语音识别模型加载失败")
    _log.info("Qwen3-ASR 模型加载耗时 %.1fs", time.time() - t0)

    out = _qwen_transcribe(model, audio_path, language)
    del model
    gc.collect()
    _release_recognizer_cache()
    if out is None:
        return QwenAsrOutcome(chunks=[], complete=False,
                              completed_until=0.0,
                              failure_reason="语音识别未返回结果")
    full_text, ts_segments = out

    # P411/C10：先保存模型返回的完整 raw_text 和未加工词，再做第一遍挂标点。
    # 后续第二遍只读这份原始证据，不读取第一遍的局部 full_text。
    _write_qwen_raw_diagnostic(
        cache_dir,
        full_text=full_text,
        word_timestamps=ts_segments,
        audio_duration=total_duration,
        model_call_count=1,
        input_ranges=[{"start": 0.0, "end": float(total_duration)}],
    )
    # P457：短音频路径也要按块记账。⑦.5 发现原来这里直接调
    # `_attach_full_text_punctuation`，返回的块没有 `punctuation_attached`，
    # 而计数把「没这个字段」当成「挂上了」——**这条路是绝大多数视频走的**
    # （长音频门槛是 20 分钟），等于新功能在最常见的场景下永远不开火。
    punctuation_attached, ts_segments = attach_full_text_punctuation_checked(
        full_text, ts_segments)

    if not full_text or not full_text.strip():
        # 跑完了，全片没有人说话。**这是完整的**——静音不是失败，
        # 拿「有没有文字」判完整性正是 P464 收窄掉的那种写法。
        return QwenAsrOutcome(chunks=[], complete=True,
                              completed_until=float(total_duration))

    _log.info("Qwen3-ASR 交叉验证完成: %d 字, %d segments",
              len(full_text), len(ts_segments))
    if progress_callback:
        try:
            progress_callback({
                "completed_segments": 1,
                "processed_duration": float(total_duration),
            })
        except Exception:
            pass
    return QwenAsrOutcome(
        chunks=[{"text": full_text, "segments": ts_segments,
                 "t_start": 0.0, "t_end": total_duration,
                 "punctuation_attached": punctuation_attached}],
        complete=True, completed_until=float(total_duration))
