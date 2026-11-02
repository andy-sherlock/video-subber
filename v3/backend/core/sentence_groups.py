"""把词级识别结果组成翻译前的连续句子组。

这里不重新识别、不改写原文，也不生成新的时间。它只在已有词对象之间
选择边界，并把首词和末词的时间带到现有 ``Segment`` 上。
"""

import copy
import json
import logging
import math
import re

_log = logging.getLogger(__name__)


class SentenceGroupingError(ValueError):
    """词级输入不能安全组成句子组时抛出的可见错误。"""


def _latest_safe_cut_index(current):
    """在 ``current`` 里从后往前找最近一个「切开后左右两边都有正时长」的位置。

    P415-A：上限理由（时间/词数/字数）触发、但当前位置是零时长词时，
    不能在这里切——会造出 0.00 秒的尾组。往前找一个合法位置切，
    这样上限保护仍然生效，又不制造坏组。找不到就返回 None（整组不切）。
    """
    for cut in range(len(current) - 1, 0, -1):
        left, right = current[:cut], current[cut:]
        if left[-1]["end"] > left[0]["start"] and right[-1]["end"] > right[0]["start"]:
            return cut
    return None


_END_MARKS = ".?!。！？｡．"
_CJK_LANGUAGES = {"zh", "zh-cn", "zh-tw", "ja", "ko"}
_PAUSE_THRESHOLD_S = 0.45
_DEFAULT_MAX_DURATION_S = 20.0
_DEFAULT_MAX_WORDS = 80
_DEFAULT_MAX_CHARS = 240
_DEFAULT_MAX_CJK_CHARS = 120

# 这些词后面的英文句号很可能是模型误加的句号。尤其是 "of."，
# 是 P389 真实任务中已经看到的例子。它们不是被删除，只是不把它们当作
# 绝对句末；后面的停顿和更可靠的标点仍然可以决定边界。
_SUSPICIOUS_FINAL_WORDS = {
    "a", "an", "and", "be", "for", "from", "in", "is", "of", "on",
    "or", "that", "the", "this", "then", "to", "with",
}
_ENGLISH_WEAK_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "because", "but",
    "can", "could", "did", "do", "does", "for", "from", "had", "has",
    "have", "he", "her", "here", "how", "i", "if", "in", "is", "it",
    "like", "may", "me", "might", "must", "my", "no", "not", "of", "on",
    "or", "our", "shall", "should", "so", "some", "that", "the", "then",
    "there", "these", "they", "this", "to", "was", "we", "were", "what",
    "when", "where", "which", "who", "why", "will", "with", "would", "you",
}
# P390/A3：语义闸的否决表。它只回答一个是非题——「这一组的最后一个词，
# 是不是一个『话没说完』的词」。它不做决策（断不断仍由 LLM 决定），只行使否决权，
# 所以是个封闭集合，不会像上面几张决策表那样无限膨胀。
# 刻意不收 this / that / then / it / here / there / for / about —— 这些词
# 真的可以结束一句话（"Look at this." "What is it for?"），收进来会误杀好断点。
_UNFINISHED_FINAL_WORDS = {
    # 连接词：桥属于它要通向的那一岸
    "and", "but", "or", "so", "because", "if", "when", "although", "though",
    "since", "unless", "whereas",
    # 冠词 / 限定词
    "a", "an", "the",
    # 介词
    "of", "to", "with", "from", "in", "on", "at", "into", "onto", "upon", "than",
    "as",
    # 系动词与助动词
    "is", "are", "was", "were", "be", "been", "being", "am",
    "has", "have", "had", "will", "would", "can", "could",
    "shall", "should", "must", "may", "might", "do", "does", "did",
}
# G12 第三批 B（A+ §4 规则 1、§10、§12 规则 4）。
# 「任何标点」= 句末类 + 逗号类（含破折号、连字符；允许后跟引号括号），中日文标点一并收下。
_ANY_PUNCT_END = re.compile(r"[.?!…,;:—–\-。！？、，；：][\"'”’)\]」』）》]*$")
_COMMA_CLASS_END = re.compile(r"[,;:—–、，；：][\"'”’)\]」』）》]*$")
# §10：只在这里用，**不**并进 `_UNFINISHED_FINAL_WORDS`（备用线路的回退规则也读那张表）。
_A10_CONNECTORS = {"and", "but", "so", "or", "because", "then", "if", "when"}
_A10_FIRST_PERSON = {"i", "i'm", "i've", "i'll", "i'd"}
_A4_MAX_COMMA_GROUP_WORDS = 4
# P390/A5：本地回退遇到这些词结尾的强边界时，把断点前移一位，
# 让连接词落到下一组的开头，而不是挂在上一组的尾巴上。
_LEADING_CONNECTORS = {
    "and", "but", "or", "so", "because", "if", "when", "while",
    "although", "though", "since", "unless", "whereas", "that",
}
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "etc", "e.g", "i.e",
    "vs", "st", "no", "fig", "inc", "ltd",
}
_TRAILING_CLOSERS = "\"'”’」』）》)]}"


def _language_uses_spaces(language: str, words: list[dict]) -> bool:
    normalized = (language or "auto").lower().replace("_", "-")
    if normalized in _CJK_LANGUAGES:
        return False
    if normalized != "auto":
        return True
    joined = "".join(str(word.get("text", "")) for word in words)
    return not bool(re.search(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]", joined))


def _normalise_content(text: str) -> str:
    return "".join(str(text or "").split())


def _copy_and_validate_words(words) -> list[dict]:
    if not isinstance(words, list) or not words:
        raise SentenceGroupingError("词级时间为空，不能建立句子组")

    normalized = []
    previous_start = None
    for index, raw in enumerate(words):
        if not isinstance(raw, dict):
            raise SentenceGroupingError(f"第 {index} 个词不是词对象")
        text = raw.get("text") or raw.get("word") or ""
        if not isinstance(text, str) or not text.strip():
            raise SentenceGroupingError(f"第 {index} 个词没有文字")
        try:
            start = float(raw["start"])
            end = float(raw["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SentenceGroupingError(f"第 {index} 个词没有有效时间") from exc
        if not math.isfinite(start) or not math.isfinite(end) or end < start:
            raise SentenceGroupingError(f"第 {index} 个词时间无效: {start}-{end}")
        if previous_start is not None and start < previous_start:
            raise SentenceGroupingError("词级时间顺序倒退")
        previous_start = start
        item = dict(raw)
        item["word"] = str(raw.get("word") or text)
        item["text"] = text
        item["start"] = start
        item["end"] = end
        normalized.append(item)
    return normalized


def _join_words(words: list[dict], use_spaces: bool) -> str:
    values = [word["text"].strip() for word in words]
    return (" " if use_spaces else "").join(values).strip()


def _has_reliable_sentence_end(text: str, language: str) -> bool:
    value = text.rstrip()
    while value and value[-1] in _TRAILING_CLOSERS:
        value = value[:-1].rstrip()
    if not value or value[-1] not in _END_MARKS:
        return False
    if language in _CJK_LANGUAGES:
        return True
    if value[-1] != ".":
        return True

    return not _is_suspicious_period(value, language)


def _is_suspicious_period(text: str, language: str) -> bool:
    value = text.rstrip()
    while value and value[-1] in _TRAILING_CLOSERS:
        value = value[:-1].rstrip()
    if language in _CJK_LANGUAGES or not value.endswith("."):
        return False
    core = value[:-1].lower()
    if re.search(r"\d\.\d$", value) or core in _ABBREVIATIONS:
        return True
    if core in _SUSPICIOUS_FINAL_WORDS:
        return True
    # e.g. / i.e. / U.S. 等多点缩写不作为句末。
    return core.count(".") >= 1


def _bare_word(text: str) -> str:
    """去掉两端标点、转小写——判断「这是哪个词」时统一用这一个函数。"""
    return re.sub(r"^[^\w]+|[^\w]+$", "", str(text).strip().lower())


def _is_weak_english_word(text: str, language: str) -> bool:
    if language in _CJK_LANGUAGES:
        return False
    return _bare_word(text) in _ENGLISH_WEAK_WORDS


def _pause_boundary_is_safe(previous_text: str, current_text: str, language: str) -> bool:
    """停顿遇到功能词时先保留上下文，避免单独翻译 is/So 这类词。"""
    return not (
        _is_weak_english_word(previous_text, language)
        or _is_weak_english_word(current_text, language)
    )


def _breaks_creating_unmarked_single_word_groups(
    words: list[dict],
    break_after_indices: list[int],
    language: str,
) -> set[int]:
    """找出把「没有结束信号的单个原词」拆成一句的那些断点。

    P395：从「整窗作废」改成「点名到具体断点」——剔除它，这个词就并入下一组，
    不必因为一处不合格就丢掉整份答案。
    """
    offenders = set()
    group_start = 0
    for break_index in break_after_indices:
        group_words = words[group_start:break_index + 1]
        group_start = break_index + 1
        if len(group_words) != 1:
            continue

        only_word = group_words[0]
        if _has_reliable_sentence_end(only_word["text"], language):
            continue
        if break_index + 1 < len(words):
            next_word = words[break_index + 1]
            gap = next_word["start"] - only_word["end"]
            if (
                gap >= _PAUSE_THRESHOLD_S
                and _pause_boundary_is_safe(
                    only_word["text"],
                    next_word["text"],
                    language,
                )
            ):
                continue
        offenders.add(break_index)
    return offenders


def find_unfinished_group_endings(
    words: list[dict],
    break_after_indices,
    *,
    language: str = "auto",
    max_duration: float = _DEFAULT_MAX_DURATION_S,
    max_words: int = _DEFAULT_MAX_WORDS,
    max_chars: int | None = None,
) -> list[tuple[int, str]]:
    """P390/A3 语义闸：找出「话没说完就断了」的组尾。

    这是全项目唯一的那道语义闸：LLM 的答案和本地回退（备用线路）的答案都经
    ``resolve_llm_primary_breaks`` 过它；缓存读回的是已过闸的最终断点，不再过。
    它只否决，不决策——返回空列表就是放行。

    只判断两件事：
      ① 组尾词是不是「话没说完的词」（连接词/冠词/介词/系动词）
      ② 整组是不是「只有一两个功能词、又没有句末标点」（如 "and then."）

    CJK 不适用英文词表，直接放行。
    """
    normalized_language = (language or "auto").lower().replace("_", "-")
    if normalized_language in _CJK_LANGUAGES:
        return []
    if not isinstance(break_after_indices, (list, tuple)):
        return []

    flagged = []
    group_start = 0
    for break_index in break_after_indices:
        # 索引合法性由调用方保证；这里只跳过不认识的，不改分组起点。
        if type(break_index) is not int or not 0 <= break_index < len(words):
            continue
        group_words = words[group_start:break_index + 1]
        group_start = break_index + 1
        if not group_words:
            continue

        last_text = str(group_words[-1].get("text") or "")
        bare = _bare_word(last_text)
        # 疑问句天生就以助动词和介词结尾："What do you do?" "Who are you with?"
        # 这些是完整的一句话，不能因为最后一个词在表里就拦下来。
        if last_text.rstrip(_TRAILING_CLOSERS).rstrip().endswith(("?", "？")):
            continue
        if bare in _UNFINISHED_FINAL_WORDS:
            flagged.append((break_index, bare))
            continue
        if (
            len(group_words) <= 2
            and not _has_reliable_sentence_end(last_text, normalized_language)
            and all(
                _is_weak_english_word(word.get("text", ""), normalized_language)
                for word in group_words
            )
        ):
            flagged.append((break_index, bare))
    flagged.extend(_aplus_unfinished_cuts(
        words, break_after_indices, {index for index, _ in flagged},
        normalized_language, max_duration, max_words, max_chars,
    ))
    return flagged


def _aplus_unfinished_cuts(words, break_after_indices, already_removed,
                           language, max_duration, max_words, max_chars):
    """A+ 规则 1、§10、规则 4，按顺序、带状态地判断每一刀。

    丙（A+ §16、§15.1）：撤一刀之前先算撤完这一组会不会超三条上限，超了就留下。
    组长从这一组**真正的开头**累计——撤掉的刀不重置起点；右端取**下一个实际保留**
    的刀（原有判断已撤的刀跳过），不能算到一个马上就会消失的断点。
    """
    use_spaces = _language_uses_spaces(language, words)
    if max_chars is None:
        max_chars = _DEFAULT_MAX_CHARS if use_spaces else _DEFAULT_MAX_CJK_CHARS

    def fits(start, end):
        group = words[start:end + 1]
        return (group[-1]["end"] - group[0]["start"] <= max_duration
                and len(group) <= max_words
                and len(_join_words(group, use_spaces)) <= max_chars)

    cuts = [index for index in break_after_indices
            if type(index) is int and 0 <= index < len(words) - 1]
    removed = []
    group_start = 0
    for position, cut in enumerate(cuts):
        if cut in already_removed:
            continue
        last = str(words[cut].get("text") or "").strip()
        following = str(words[cut + 1].get("text") or "").strip()
        reason = None
        if not _ANY_PUNCT_END.search(last) and following[:1].islower():
            reason = "规则1"
        elif (not _ANY_PUNCT_END.search(last)
              and _bare_word(last) in _A10_CONNECTORS
              and following.lower() in _A10_FIRST_PERSON):
            reason = "§10"
        elif (_COMMA_CLASS_END.search(last)
              and cut - group_start + 1 <= _A4_MAX_COMMA_GROUP_WORDS):
            reason = "规则4"
        if reason is not None:
            next_kept = next(
                (index for index in cuts[position + 1:]
                 if index not in already_removed),
                len(words) - 1,
            )
            if fits(group_start, next_kept):
                removed.append((cut, reason))
                continue
        group_start = cut + 1
    return removed


def parse_llm_primary_break_response(
    raw_response: str,
    *,
    allowed_range: range,
    dropped: list[tuple[object, str]] | None = None,
) -> list[int]:
    """读取 LLM 断点，并只删除其中不能使用的单个编号。

    ``allowed_range`` 必须由构造提示词的同一份词表生成。这样单词区间没有
    合法断点时可以明确传入空范围，解析器不会自己再算一遍边界。

    ``dropped`` 只用于留下诊断证据，不参与判断结果。
    """
    try:
        payload = json.loads(raw_response)
    except (TypeError, json.JSONDecodeError) as exc:
        raise SentenceGroupingError("LLM 断点回答不是合法 JSON 对象") from exc

    if not isinstance(payload, dict) or set(payload) != {"break_after_indices"}:
        raise SentenceGroupingError("LLM 断点回答只能包含 break_after_indices")
    break_indices = payload.get("break_after_indices")
    if not isinstance(break_indices, list):
        raise SentenceGroupingError("LLM 断点索引必须是列表")

    if not isinstance(allowed_range, range):
        raise SentenceGroupingError("LLM 断点允许范围无效")

    validated = []
    previous_break = None
    for break_index in break_indices:
        if type(break_index) is not int:
            if dropped is not None:
                dropped.append((break_index, "非整数"))
            continue
        if break_index not in allowed_range:
            if dropped is not None:
                dropped.append((break_index, "超出允许范围"))
            continue
        if previous_break is not None and break_index <= previous_break:
            if dropped is not None:
                reason = "重复" if break_index == previous_break else "顺序倒退"
                dropped.append((break_index, reason))
            continue
        validated.append(break_index)
        previous_break = break_index
    return validated


def build_llm_primary_boundary_prompt(
    word_timestamps: list[dict],
    *,
    language: str = "auto",
    full_text: str | None = None,
    start_index: int = 0,
    end_index: int | None = None,
    max_words: int = _DEFAULT_MAX_WORDS,
    sound_hints=(),
) -> tuple[str, str, range]:
    """构造只含连续原始词的 LLM 断句请求。

    P395：不再有「窗口」。``start_index`` / ``end_index`` 是本次发送的范围，
    **起点由上一次模型自己选的断点决定，不是算术算出来的**；范围内任意两词之间
    都可以断，因此**不再列举可选索引**——那一行在整篇模式下是 9724 个字符、
    占词内容的 45%，却只表达「随便选」这一个意思。

    P409：原词**原样**发给模型，标点和大小写都不再擦除（原 ``_llm_display_word``
    已删除）。P390 擦除的动机是 ASR 标点不可靠，但它连真句号和真句首大写一起擦掉，
    模型看不见句子在哪结束，只能猜。改为把标点原样给出、并在提示词里声明它可能不准。
    """
    words = _copy_and_validate_words(word_timestamps)
    if full_text is not None:
        if _normalise_content(full_text) != _normalise_content(
            "".join(word["text"] for word in words)
        ):
            raise SentenceGroupingError("全文与词级文字不一致")

    if end_index is None:
        end_index = len(words) - 1
    if (
        type(start_index) is not int
        or type(end_index) is not int
        or start_index < 0
        or end_index < start_index
        or end_index >= len(words)
    ):
        raise SentenceGroupingError("LLM 断句范围无效")

    # 词本身可以展示到 end_index，但最后一个词后面没有下一个词，不能在它
    # 后面断开。range(start, end) 正好表示所有合法的「在这个词后断开」编号；
    # 当区间只有一个词时，它自然是空范围。
    allowed_break_range = range(start_index, end_index)
    if allowed_break_range:
        allowed_range_text = (
            f"{allowed_break_range.start} 到 {allowed_break_range.stop - 1}"
        )
    else:
        allowed_range_text = "没有可用的断点编号"

    hint_by_index = {}
    for hint in sound_hints or ():
        hint_index = getattr(hint, "word_index", None)
        gap = getattr(hint, "gap", None)
        if (
            type(hint_index) is not int
            or not isinstance(gap, str)
            or re.fullmatch(r"\d+\.\d{3}", gap) is None
        ):
            raise SentenceGroupingError("声音提示格式无效")
        # 最后一个展示词只是给模型读上下文，它的后继词不在本批请求里，
        # 因而它后面的 gap 既不可判断也不是合法断点，不能跨块泄露。
        if hint_index in allowed_break_range:
            hint_by_index[hint_index] = gap

    def _prompt_view(text):
        return (
            str(text)
            .replace("\\", "\\\\")
            .replace("\r", "\\r")
            .replace("\n", "\\n")
            .replace("\t", "\\t")
        )

    indexed_words = "\n".join(
        (
            f"{index} [gap={hint_by_index[index]}]: "
            if index in hint_by_index
            else f"{index}: "
        )
        + _prompt_view(words[index]["text"])
        for index in range(start_index, end_index + 1)
    )
    system_prompt = (
        "你是一个字幕断句助手。"
        "只返回严格 JSON 对象，且唯一字段必须是 "
        '{"break_after_indices": [整数, ...]}。'
        "不得改写、新增、删除或重排原词；不得返回标点文本、翻译、"
        "时间、最终字幕或解释。\n"
        "行首整数是词编号；方括号内是系统字段，不是原文；"
        "冒号后的一个空格到行尾全部是原词。"
        "gap 是参考，不是强制断点。\n"
        # P409：标点原样给出，但必须同时声明它不可靠——否则模型会盲信 ASR
        # 打错的句号（如 `English became. More widely spoken`）。实测证明
        # 「给标点 + 声明不可靠」优于「替模型擦掉标点」：切错 11 处 → 3 处。
        "这是一段语音转录。标点和大小写来自语音识别系统的自动标注，"
        "可能不准确。请根据语义判断句子边界——标点仅供参考，不要盲目信任。\n"
        # P409：原来这里有一张规则表（逐条列举 but/and/so/because 等连接词、
        # 每组至少 3 词、正例反例）。清单反而把模型框死——清单外的词
        # （during / into / that's）被当成「可以在前面断」。砍掉规则表后，
        # 模型自己的语言能力接管，这四处全部不再断错。
        "每组应该是一个完整的意思，不要把一个完整的短语或语法结构从中间切开。"
        # P395/6a 保留词数上限：让模型一次断对，而不是让程序事后按时间戳补刀
        # ——那把刀不看语义，必然劈开词组。
        # **刻意不给「不超过 20 秒」**：模型只看得稀疏的词间停顿证据，
        # 没有完整词级时间；让它遵守一个测不了的约束只会让它瞎猜。
        # 20 秒仍由程序硬限制负责。
        # P409 撤掉原本并列的「不超过 240 个字符」：实测该约束从未单独生效
        # （4 次机械补刀全部由 20 秒触发，唯一超 240 的那组同时也超了 20 秒），
        # 而模型拿回标点后自己就断得更碎，机械补刀 4 次 → 1 次。
        # 词数上限保留：英文路径它确实用不满（43/80），但 CJK 路径一词一字，
        # 80 词可能先于 120 字符上限触发，不能拿英文样本判它死刑。
        f"每组不超过 {max_words} 个原始词。"
    )
    user_prompt = (
        f"源语言：{language or 'auto'}\n"
        "在任意两个相邻词之间断开都可以，请按完整意思选择位置。\n"
        "返回的索引是「在这个词之后断开」。\n"
        f"允许返回的断点编号范围：{allowed_range_text}。最后一个词只用于阅读，不能返回它的编号。\n"
        "连续原始词：\n"
        f"{indexed_words}"
    )
    return system_prompt, user_prompt, allowed_break_range


def enforce_llm_break_hard_limits(
    full_text: str,
    word_timestamps: list[dict],
    *,
    break_after_indices: list[int] | tuple[int, ...],
    language: str = "auto",
    max_duration: float = _DEFAULT_MAX_DURATION_S,
    max_words: int = _DEFAULT_MAX_WORDS,
    max_chars: int | None = None,
) -> tuple[list[int], set[int]]:
    """清掉会造出零时长组的断点，再为过长组补最少安全断点。

    P395/6b：返回 ``(最终断点, 硬限制补出来的断点)``。补刀必须单独计数——
    它按设计不看语义（只找时间戳不重叠的位置），落在词组中间是常态。
    验收时把它和模型自己选的断点混在一起统计，会得出「模型断得很差」的错误结论，
    甚至诱使人为了让数字好看去调松硬限制。
    """
    if not isinstance(break_after_indices, (list, tuple)):
        raise SentenceGroupingError("断点索引必须是列表")
    if max_duration <= 0 or max_words <= 0:
        raise ValueError("句子组长度限制必须大于零")

    # G12 第三批 B（A+ §11）：词在识别阶段已带好标点，这里不再贴第二遍。
    words = _copy_and_validate_words(word_timestamps)
    if _normalise_content(full_text) != _normalise_content(
        "".join(word["text"] for word in words)
    ):
        raise SentenceGroupingError("全文与词级文字不一致")

    normalized_language = (language or "auto").lower().replace("_", "-")
    use_spaces = _language_uses_spaces(normalized_language, words)
    if max_chars is None:
        max_chars = _DEFAULT_MAX_CHARS if use_spaces else _DEFAULT_MAX_CJK_CHARS

    llm_breaks = []
    previous_break = -1
    for break_index in break_after_indices:
        if type(break_index) is not int:
            raise SentenceGroupingError("断点索引必须是整数")
        if break_index <= previous_break:
            raise SentenceGroupingError("断点索引必须严格递增")
        if break_index < 0 or break_index >= len(words) - 1:
            raise SentenceGroupingError("断点索引越界")
        llm_breaks.append(break_index)
        previous_break = break_index

    def remove_nonpositive_group_breaks(
        breaks: list[int],
    ) -> tuple[list[int], set[int]]:
        """撤销造成零时长组的断点；每撤一刀后按新分组重新检查。"""
        cleaned = list(breaks)
        removed = set()
        while cleaned:
            group_start = 0
            for position, group_end in enumerate([*cleaned, len(words) - 1]):
                if words[group_end]["end"] > words[group_start]["start"]:
                    group_start = group_end + 1
                    continue

                # 中间坏组撤掉自己的结尾刀，最后坏组撤掉前一刀。
                break_position = (
                    position if position < len(cleaned) else position - 1
                )
                merged_start = (
                    cleaned[break_position - 1] + 1 if break_position else 0
                )
                merged_end = (
                    cleaned[break_position + 1]
                    if break_position + 1 < len(cleaned)
                    else len(words) - 1
                )
                if any(
                    word["end"] > words[merged_end]["end"]
                    for word in words[merged_start:merged_end + 1]
                ):
                    raise SentenceGroupingError(
                        "撤销零时长断点会让字幕结束时间早于组内词时间"
                    )
                removed.add(cleaned.pop(break_position))
                break
            else:
                return cleaned, removed
        return cleaned, removed

    llm_breaks, rejected_breaks = remove_nonpositive_group_breaks(llm_breaks)

    def exceeds_limit(start_index: int, end_index: int) -> bool:
        group_words = words[start_index:end_index + 1]
        group_text = _join_words(group_words, use_spaces)
        return (
            group_words[-1]["end"] - group_words[0]["start"] > max_duration
            or len(group_words) > max_words
            or len(group_text) > max_chars
        )

    def safe_break_before(exclusive_end: int, start_index: int) -> int | None:
        """G12 第三批 B（A+ 规则 3）：在同样的安全条件下往回依次找
        可靠句末 → 逗号类 → ≥0.45 秒停顿；都没有才退回「超限前最近的安全位置」。"""
        safe = []
        for break_index in range(exclusive_end - 1, start_index - 1, -1):
            if break_index in rejected_breaks:
                continue
            if words[break_index]["end"] <= words[start_index]["start"]:
                continue
            if (
                break_index + 1 < len(words)
                and words[break_index]["end"] > words[break_index + 1]["start"]
            ):
                continue
            safe.append(break_index)
        for preferred in (
            lambda i: _has_reliable_sentence_end(words[i]["text"], normalized_language),
            lambda i: bool(_COMMA_CLASS_END.search(words[i]["text"].strip())),
            lambda i: (i + 1 < len(words)
                       and words[i + 1]["start"] - words[i]["end"] >= _PAUSE_THRESHOLD_S),
        ):
            for break_index in safe:
                if preferred(break_index):
                    return break_index
        return safe[0] if safe else None

    resolved_breaks = []
    added_by_limits = set()
    group_start = 0
    for target_end in [*llm_breaks, len(words) - 1]:
        current_start = group_start
        while current_start <= target_end:
            first_excess = None
            for candidate_end in range(current_start, target_end + 1):
                if exceeds_limit(current_start, candidate_end):
                    first_excess = candidate_end
                    break
            if first_excess is None:
                if target_end < len(words) - 1:
                    resolved_breaks.append(target_end)
                break

            safe_break = safe_break_before(first_excess, current_start)
            if safe_break is None:
                raise SentenceGroupingError("硬限制无法在不破坏词级时间的前提下切分")
            resolved_breaks.append(safe_break)
            added_by_limits.add(safe_break)
            current_start = safe_break + 1
        group_start = target_end + 1

    resolved_breaks, removed_breaks = remove_nonpositive_group_breaks(
        resolved_breaks
    )
    added_by_limits.difference_update(removed_breaks)

    # 第二次撤刀后只检查，不再补第三轮；冲突交给现有整篇规则回退。
    group_start = 0
    for group_end in [*resolved_breaks, len(words) - 1]:
        if exceeds_limit(group_start, group_end):
            raise SentenceGroupingError("撤销零时长断点后无法满足硬限制")
        group_start = group_end + 1

    return resolved_breaks, added_by_limits


def _group_id(start_word_index: int) -> int:
    """返回稳定的整数 ID；负数命名空间避免旧碎片快照误命中。"""
    return -(start_word_index + 1)


def build_sentence_groups(
    full_text: str,
    word_timestamps: list[dict],
    *,
    language: str = "auto",
    max_duration: float = _DEFAULT_MAX_DURATION_S,
    pause_threshold: float = _PAUSE_THRESHOLD_S,
    max_words: int = _DEFAULT_MAX_WORDS,
    max_chars: int | None = None,
) -> list:
    """用已有词对象建立连续句子组。

    ``full_text`` 只用于校验，不会被重新切词后拿来生成字幕文字。最终
    文字始终由原词对象按语言规则拼回，因此每个词的索引和时间都能追溯。
    """
    # G12 第三批 B（A+ §11）：词在识别阶段已带好标点，这里不再贴第二遍。
    words = _copy_and_validate_words(word_timestamps)
    if _normalise_content(full_text) != _normalise_content(
        "".join(word["text"] for word in words)
    ):
        raise SentenceGroupingError("全文与词级文字不一致")

    normalized_language = (language or "auto").lower().replace("_", "-")
    use_spaces = _language_uses_spaces(normalized_language, words)
    if max_duration <= 0 or max_words <= 0:
        raise ValueError("句子组长度限制必须大于零")
    if max_chars is None:
        max_chars = _DEFAULT_MAX_CHARS if use_spaces else _DEFAULT_MAX_CJK_CHARS

    groups = []
    current = []
    current_start_index = 0

    def flush():
        nonlocal current
        if not current:
            return
        from backend.transcriber_v2 import Segment

        start_index = current_start_index
        end_index = start_index + len(current) - 1
        segment = Segment(
            id=_group_id(start_index),
            start=current[0]["start"],
            end=current[-1]["end"],
            text=_join_words(current, use_spaces),
        )
        segment.words = [dict(word) for word in current]
        segment.start_word_index = start_index
        segment.end_word_index = end_index
        segment.source_text = segment.text
        segment.is_sentence_group = True
        groups.append(segment)
        current = []

    for index, word in enumerate(words):
        if not current:
            current_start_index = index
            current.append(word)
            continue

        previous = current[-1]
        candidate = current + [word]
        gap = word["start"] - previous["end"]
        candidate_text = _join_words(candidate, use_spaces)
        too_long = (
            word["end"] - current[0]["start"] > max_duration
            or len(current) >= max_words
            or len(candidate_text) > max_chars
        )
        suspicious_period = _is_suspicious_period(
            previous["text"], normalized_language
        )
        pause_limit = pause_threshold * 1.5 if suspicious_period else pause_threshold
        strong_boundary = _has_reliable_sentence_end(
            previous["text"], normalized_language
        )
        pause_boundary = gap >= pause_limit and _pause_boundary_is_safe(
            previous["text"], word["text"], normalized_language
        )
        # P415-A：这里曾经是 `boundary_before_word = strong_boundary or
        # pause_boundary or too_long`，然后遇到零时长词就把整个布尔置为 False。
        # 后果是「已经超过 20 秒」这个理由被零时长特例一并取消——日文真实任务
        # 因此交付过 20.30775 秒的字幕。
        #
        # 正确的分法：零时长词只能取消**语义类**理由（句号、停顿）——那两条
        # 是「这里断句更自然」的偏好，往后推一个词没有代价。
        # 它**绝不能取消上限理由**（时间 / 词数 / 字数）——那三条是保护。
        semantic_boundary = strong_boundary or pause_boundary
        zero_duration_word = word["end"] <= word["start"]
        zero_duration_current = current[-1]["end"] <= current[0]["start"]
        if zero_duration_word or zero_duration_current:
            semantic_boundary = False
        boundary_before_word = semantic_boundary or too_long

        if boundary_before_word and (zero_duration_word or zero_duration_current):
            # 上限已触发，但在这里切会让零时长词孤立成 0.00 秒组。
            # 先往前找最近的合法切点（切完左右两边都有正时长）；
            # 找不到就整组不切，交给显示层的 7 秒封顶接管——
            # 绝不为了「总得切一刀」而产生 0 秒尾字幕。
            safe = _latest_safe_cut_index(current)
            if safe is None:
                boundary_before_word = False
            else:
                tail = current[safe:]
                del current[safe:]
                flush()
                current_start_index = index - len(tail)
                current = tail
                boundary_before_word = False
        if boundary_before_word:
            # P390/A5：强边界的前一个词是连接词时，把断点前移一位，让它归到
            # 下一组的开头。桥属于它要通向的那一岸，留在出发岸桥就断了。
            # 只在移动之后左边仍是一个有效组（有词、时间不倒退）时才移。
            if (
                len(current) >= 2
                and _bare_word(previous["text"]) in _LEADING_CONNECTORS
                and current[-2]["end"] > current[0]["start"]
            ):
                connector = current.pop()
                flush()
                current_start_index = index - 1
                current.append(connector)
            else:
                flush()
                current_start_index = index
        current.append(word)

    flush()

    # 强句末后若只留下一个话语连接词（如 So），把它和下一个已有词组
    # 合并；不改词、不改时间，只避免把连接词单独送去翻译。
    if use_spaces:
        index = 0
        while index < len(groups) - 1:
            left = groups[index]
            if (
                len(left.words) <= 2
                and all(
                    _is_weak_english_word(word["text"], normalized_language)
                    for word in left.words
                )
                and not _has_reliable_sentence_end(
                    left.words[-1]["text"], normalized_language
                )
            ):
                right = groups[index + 1]
                merged_words = left.words + right.words
                merged_text = _join_words(merged_words, use_spaces)
                if (
                    right.end - left.start <= max_duration
                    and len(merged_words) <= max_words
                    and len(merged_text) <= max_chars
                ):
                    left.words = [dict(word) for word in merged_words]
                    left.end = right.end
                    left.text = merged_text
                    left.source_text = merged_text
                    left.end_word_index = right.end_word_index
                    del groups[index + 1]
                    continue
            index += 1

    if not groups or any(not group.text.strip() for group in groups):
        raise SentenceGroupingError("生成了空句子组")
    if any(group.end <= group.start for group in groups):
        raise SentenceGroupingError("句子组没有有效时间")
    # P482：前组结束可以晚于后组开始（识别给的词时间倒挂），这里不查也不修——
    # 两屏不重叠只由显示层 `_repair_times` 负责。
    return groups


def rebuild_sentence_groups_from_raw_break_indices(
    full_text: str,
    word_timestamps: list[dict],
    *,
    break_after_indices: list[int] | tuple[int, ...],
    language: str = "auto",
) -> list:
    """只按外部已验证的原词索引重建句子组，不调用规则断句。"""
    if not isinstance(break_after_indices, (list, tuple)):
        raise SentenceGroupingError("断点索引必须是列表")

    # G12 第三批 B（A+ §11）：词在识别阶段已带好标点，这里不再贴第二遍。
    words = _copy_and_validate_words(word_timestamps)
    if _normalise_content(full_text) != _normalise_content(
        "".join(word["text"] for word in words)
    ):
        raise SentenceGroupingError("全文与词级文字不一致")

    previous_break = -1
    validated_breaks = []
    for break_index in break_after_indices:
        if type(break_index) is not int:
            raise SentenceGroupingError("断点索引必须是整数")
        if break_index <= previous_break:
            raise SentenceGroupingError("断点索引必须严格递增")
        if break_index < 0 or break_index >= len(words) - 1:
            raise SentenceGroupingError("断点索引越界")
        validated_breaks.append(break_index)
        previous_break = break_index

    normalized_language = (language or "auto").lower().replace("_", "-")
    use_spaces = _language_uses_spaces(normalized_language, words)
    from backend.transcriber_v2 import Segment

    groups = []
    start_index = 0
    for end_index in [*validated_breaks, len(words) - 1]:
        group_words = words[start_index:end_index + 1]
        if not group_words:
            raise SentenceGroupingError("生成了空句子组")
        if group_words[-1]["end"] <= group_words[0]["start"]:
            raise SentenceGroupingError("断点会让零时长词单独成为句子组")
        group = Segment(
            id=_group_id(start_index),
            start=group_words[0]["start"],
            end=group_words[-1]["end"],
            text=_join_words(group_words, use_spaces),
        )
        group.words = [dict(word) for word in group_words]
        group.start_word_index = start_index
        group.end_word_index = end_index
        group.source_text = group.text
        group.is_sentence_group = True
        groups.append(group)
        start_index = end_index + 1

    if not groups or any(not group.text.strip() for group in groups):
        raise SentenceGroupingError("生成了空句子组")
    if any(group.end <= group.start for group in groups):
        raise SentenceGroupingError("句子组没有有效时间")
    # P482：同 build_sentence_groups——组间重叠只交给显示层 `_repair_times`。
    return groups


def resolve_llm_primary_breaks(
    full_text: str,
    word_timestamps: list[dict],
    *,
    break_after_indices: list[int] | tuple[int, ...],
    language: str = "auto",
    max_duration: float = _DEFAULT_MAX_DURATION_S,
    max_words: int = _DEFAULT_MAX_WORDS,
    max_chars: int | None = None,
    sound_hints=(),
) -> tuple[list[int], set[int]]:
    """对一份完整的模型断点做语义闸 + 硬限制，返回 (最终断点, 硬限制补的)。

    P395：不再逐窗处理，也不再「一处不合格就整份作废」。语义不合格的断点
    **直接剔除**——剔除一定对，重问可能又错，而且整篇模式下重问要把
    全部原词重发一遍。
    """
    if not isinstance(break_after_indices, (list, tuple)):
        raise SentenceGroupingError("断点索引必须是列表")

    # G12 第三批 B（A+ §11）：词在识别阶段已带好标点，这里不再贴第二遍。
    words = _copy_and_validate_words(word_timestamps)
    if _normalise_content(full_text) != _normalise_content(
        "".join(word["text"] for word in words)
    ):
        raise SentenceGroupingError("全文与词级文字不一致")

    normalized_language = (language or "auto").lower().replace("_", "-")

    selected = []
    previous_break = -1
    for break_index in break_after_indices:
        if type(break_index) is not int:
            raise SentenceGroupingError("断点索引必须是整数")
        if break_index <= previous_break:
            raise SentenceGroupingError("断点索引必须严格递增")
        if break_index < 0 or break_index >= len(words) - 1:
            raise SentenceGroupingError("断点索引越界")
        selected.append(break_index)
        previous_break = break_index

    # P390/A3：语义闸只回答一个是非题——「这一组的最后一个词，是不是一个
    # 『话没说完』的词」。P395/3c：拦下就剔除这个断点，不反馈重试。
    unfinished = {
        break_index
        for break_index, _word in find_unfinished_group_endings(
            words, selected, language=normalized_language,
            max_duration=max_duration, max_words=max_words, max_chars=max_chars,
        )
    }
    if unfinished:
        selected = [index for index in selected if index not in unfinished]

    # 没有结束信号的单词组：同样点名剔除，不作废整份答案。
    single_word_offenders = _breaks_creating_unmarked_single_word_groups(
        words, selected, normalized_language,
    )
    if single_word_offenders:
        selected = [
            index for index in selected if index not in single_word_offenders
        ]

    # G12 第三批 B（A+ 规则 2）：长时间安静、且安静前的词带句末或逗号类标点 → 一定切。
    # 加在两道剔除闸之后，所以不会被它们撤掉；零时长保护仍优先（硬限制里照旧撤）。
    forced = {
        hint.word_index for hint in sound_hints or ()
        if 0 <= hint.word_index < len(words) - 1
        and _ANY_PUNCT_END.search(str(words[hint.word_index]["text"]).strip())
    }
    selected = sorted(set(selected) | forced)

    final, added = enforce_llm_break_hard_limits(
        full_text,
        word_timestamps,
        break_after_indices=selected,
        language=language,
        max_duration=max_duration,
        max_words=max_words,
        max_chars=max_chars,
    )
    dropped = sorted(forced - set(final))
    if forced:
        _log.info("A+ 规则2 停顿处强制切 %d 刀，其中被零时长保护撤掉 %d 刀%s",
                  len(forced), len(dropped),
                  f"（位置 {dropped}）" if dropped else "")
    return final, added


def group_segments_for_translation(
    segments: list,
    *,
    language: str = "auto",
    full_text: str | None = None,
    max_duration: float = _DEFAULT_MAX_DURATION_S,
    pause_threshold: float = _PAUSE_THRESHOLD_S,
    max_words: int = _DEFAULT_MAX_WORDS,
    max_chars: int | None = None,
) -> list:
    """把已有小 Segment 展平后重新按词索引组句。

    每个输入 Segment 都必须有词对象，且 Segment.text 必须与自己的词对象
    一致。这样缓存恢复和精准模式都不会悄悄丢掉没有时间的文字。
    """
    if not isinstance(segments, list):
        raise SentenceGroupingError("字幕片段不是列表")
    all_words = []
    for segment_index, segment in enumerate(segments):
        words = getattr(segment, "words", None)
        if not words:
            raise SentenceGroupingError(
                f"第 {segment_index} 个字幕片段没有词级时间，不能建立句子组"
            )
        normalized_words = _copy_and_validate_words(list(words))
        segment_text = getattr(segment, "text", "") or ""
        if _normalise_content(segment_text) != _normalise_content(
            "".join(word["text"] for word in normalized_words)
        ):
            raise SentenceGroupingError(
                f"第 {segment_index} 个字幕片段文字与词级文字不一致"
            )
        all_words.extend(normalized_words)

    if not all_words:
        return []
    source_text = full_text if full_text is not None else "".join(
        word["text"] for word in all_words
    )
    groups = build_sentence_groups(
        source_text,
        all_words,
        language=language,
        max_duration=max_duration,
        pause_threshold=pause_threshold,
        max_words=max_words,
        max_chars=max_chars,
    )

    _attach_segment_metadata(groups, segments)
    return groups


def _attach_segment_metadata(groups: list, segments: list) -> None:
    """把已有片段的附加信息映射到新组，不参与文字和时间生成。"""
    if not segments:
        return
    # 保留原有字幕的说话人变化、不确定标记和音频标签；这些是元数据，
    # 不参与句子文字和时间的生成。raw 词数可能比旧容器多，所以按时间
    # 交集映射，而不是按旧词数组下标映射。
    for group in groups:
        matched = [
            segment for segment in segments
            if float(getattr(segment, "start", 0.0)) < group.end
            and float(getattr(segment, "end", 0.0)) > group.start
        ]
        if matched:
            first = matched[0]
        else:
            first = min(
                segments,
                key=lambda segment: abs(float(getattr(segment, "start", 0.0)) - group.start),
            )
        group.speaker_change = bool(getattr(first, "speaker_change", False))
        group.uncertain = any(
            getattr(segment, "uncertain", False) for segment in matched
        )
        group.sensevoice_tag = getattr(first, "sensevoice_tag", None)
        # G2/B2：这里原来还把各段的「参照文本」再按组拼一遍。它唯一的消费者是
        # 润色的 [CONFLICT] 分支（B3 已删），段级参照本身也已删（B1）。


def group_qwen_raw_with_llm_primary_boundaries(
    segments: list,
    full_text: str,
    word_timestamps: list[dict],
    *,
    break_after_indices: list[int] | tuple[int, ...],
    language: str = "auto",
    max_duration: float = _DEFAULT_MAX_DURATION_S,
    max_words: int = _DEFAULT_MAX_WORDS,
    max_chars: int | None = None,
    sound_hints=(),
) -> tuple[list, list[int], set[int]]:
    """用 LLM 主裁索引建立组，再把旧片段的非文字元数据挂回去。

    P395：返回 ``(句子组, 最终断点, 硬限制补出来的断点)``——第三项让验收能把
    模型自己选的断点和机械补刀分栏统计。
    """
    break_indices, added_by_limits = resolve_llm_primary_breaks(
        full_text,
        word_timestamps,
        break_after_indices=break_after_indices,
        language=language,
        max_duration=max_duration,
        max_words=max_words,
        max_chars=max_chars,
        sound_hints=sound_hints,
    )
    groups = group_qwen_raw_from_break_indices(
        segments,
        full_text,
        word_timestamps,
        break_after_indices=break_indices,
        language=language,
    )
    return groups, break_indices, added_by_limits


def group_qwen_raw_from_break_indices(
    segments: list,
    full_text: str,
    word_timestamps: list[dict],
    *,
    break_after_indices: list[int] | tuple[int, ...],
    language: str = "auto",
) -> list:
    """按已验证的原词索引重建组，并仅挂回旧片段附加信息。"""
    groups = rebuild_sentence_groups_from_raw_break_indices(
        full_text,
        word_timestamps,
        break_after_indices=break_after_indices,
        language=language,
    )
    _attach_segment_metadata(groups, segments)
    return groups


def group_qwen_raw_for_translation(
    segments: list,
    full_text: str,
    word_timestamps: list[dict],
    *,
    language: str = "auto",
    max_duration: float = _DEFAULT_MAX_DURATION_S,
    pause_threshold: float = _PAUSE_THRESHOLD_S,
    max_words: int = _DEFAULT_MAX_WORDS,
    max_chars: int | None = None,
    sound_hints=(),
) -> list:
    """备用线路（没配 API、AI 失败或答案未通过校验）的唯一入口。

    G12 第三批 B（A+「备用线路」）：规则组句先出断点，再把这些断点当成 AI 断点，
    走和 AI 路线**完全相同**的规则 1/§10/规则 4/丙、规则 2、规则 3——不另写检查。
    """
    groups = build_sentence_groups(
        full_text,
        word_timestamps,
        language=language,
        max_duration=max_duration,
        pause_threshold=pause_threshold,
        max_words=max_words,
        max_chars=max_chars,
    )
    rule_breaks = [group.end_word_index for group in groups[:-1]]
    try:
        adjusted, final_breaks, added = group_qwen_raw_with_llm_primary_boundaries(
            segments,
            full_text,
            word_timestamps,
            break_after_indices=rule_breaks,
            language=language,
            max_duration=max_duration,
            max_words=max_words,
            max_chars=max_chars,
            sound_hints=sound_hints,
        )
    except SentenceGroupingError as exc:
        # 规则组句本身已经合法；规则调整做不到就保留它，不让备用线路失败。
        _log.warning("A+ 备用线路规则调整失败，保留规则组句原断点: %s", exc)
        _attach_segment_metadata(groups, segments)
        return groups
    _log.info("A+ 备用线路经规则调整：原 %d 刀，撤 %d 刀，补 %d 刀（其中保险丝 %d 刀）",
              len(rule_breaks), len(set(rule_breaks) - set(final_breaks)),
              len(set(final_breaks) - set(rule_breaks)), len(added))
    return adjusted


_RAW_SENTENCE_END = re.compile(r"[.?!。！？][\"'”’)\]」』）》]*$")
_ANY_SENTENCE_END_MARK = re.compile(r"[.?!…。！？]")


def merge_groups_split_on_refiner_removed_periods(
    groups: list,
    *,
    language: str = "auto",
    max_duration: float = _DEFAULT_MAX_DURATION_S,
    max_words: int = _DEFAULT_MAX_WORDS,
    max_chars: int | None = None,
) -> list:
    """G12 第三批 B（A+ §13 + §15.2）：润色之后、翻译之前，把切在假句号上的两组并回去。

    识别在句中错加句号 → 分组照着切成两组 → 润色把句号删了（例：`There's definitely.`
    `A few.` → 「肯定有」「几个」两个碎屏）。判断：前一组组尾词在识别原文里带句末标点，
    而润色文里与它一一对应的词没有句末标点 → 并。连续并组时从这一串第一组开头累计，
    用已经合成的整组查三条上限，超了就保留这一刀。只读润色结果，不另存状态；
    同样的输入永远得到同样的结果（重试 / 接受部分结果时要能复现）。
    """
    from backend.core.refined_alignment import one_to_one_refined_tokens

    if len(groups) < 2:
        return list(groups)
    normalized = (language or "auto").lower().replace("_", "-")
    use_spaces = normalized not in _CJK_LANGUAGES
    joiner = " " if use_spaces else ""
    if max_chars is None:
        max_chars = _DEFAULT_MAX_CHARS if use_spaces else _DEFAULT_MAX_CJK_CHARS

    def cut_on_removed_period(group) -> bool:
        words = list(getattr(group, "words", None) or [])
        if not words:
            return False
        raw_last = str(words[-1].get("text") or "").strip()
        if not _RAW_SENTENCE_END.search(raw_last):
            return False
        aligned = one_to_one_refined_tokens(
            [str(w.get("text") or "") for w in words], getattr(group, "text", ""))
        refined_last = aligned[-1]
        refined_tokens = str(getattr(group, "text", "") or "").split()
        # 两个条件都要：组尾原词的句号被润色删了（方案 §13 逐词比对），**且**润色文结尾
        # 也不是句末（实验写法）。只看前者会误并「What a perfect day.」这类润色在句尾
        # 补了词、话其实已说完的组（施工 ⑦ 小步 3，9-23 新西兰 4 处）。
        return (refined_last is not None
                and not _ANY_SENTENCE_END_MARK.search(refined_last)
                and bool(refined_tokens)
                and not _ANY_SENTENCE_END_MARK.search(refined_tokens[-1]))

    def fits(words) -> bool:
        return (words[-1]["end"] - words[0]["start"] <= max_duration
                and len(words) <= max_words
                and len(_join_words(words, use_spaces)) <= max_chars)

    merged = [groups[0]]
    chain = [groups[0]]
    for current in groups[1:]:
        previous = chain[-1]
        combined_words = [w for g in chain for w in g.words] + list(current.words)
        if cut_on_removed_period(previous) and fits(combined_words):
            chain.append(current)
            merged[-1] = _merged_group(chain, joiner)
        else:
            chain = [current]
            merged.append(current)
    if len(merged) != len(groups):
        _log.info("A+ §13 润色后并组：%d 组 → %d 组", len(groups), len(merged))
    return merged


def _merged_group(chain: list, joiner: str):
    first, last = chain[0], chain[-1]
    group = copy.copy(first)
    group.end = last.end
    group.text = joiner.join(str(g.text or "").strip() for g in chain)
    group.words = [dict(w) for g in chain for w in g.words]
    group.end_word_index = last.end_word_index
    group.source_text = joiner.join(
        str(getattr(g, "source_text", "") or "").strip() for g in chain)
    group.uncertain = any(getattr(g, "uncertain", False) for g in chain)
    return group
