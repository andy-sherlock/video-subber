"""Five-layer hallucination filter.

Processing order: L1 → L2 → L4 → L5 → L3

**G12 第二批：删除权已收窄到三类，没有例外。**

| # | 分支 | 位置 | 判据性质 |
|---|---|---|---|
| 1 | 空文字 | `_layer4_regex` | 结构性不可能 |
| 2 | 纯符号（无字母数字）**且不是无障碍标记** | `_layer4_regex` | 结构性不可能 |
| 3 | ≥3 个同词落在同一零宽瞬间 | `find_impossible_instant_runs`（词级） | 物理不可能 |

**共同性质：这些内容物理上不可能是字幕。** 不是「看起来可疑」。
**三条判据一个阈值都没有**——白名单里最后一个数字（`dur < 0.1` 的 `0.1` 与 `2`）
已随时长分支交出删除权一并消失。**不要再添。**

段级只剩两条：**一条看有没有字，一条看是不是符号。** 其余全部只标记。

**已收回删除权的**（全部改为只标记 `uncertain`）：
- L4 关键词子串 —— 12 次开火 0 次正确，**常量与分支整个删除**
- L4 重复模式 / 重复词 / 循环句式 —— 删掉的是 `'Canada. Canada.'`、
  `'Oh yeah. Oh yeah.'`、`'Meow Meow'`、`'hey hey hey'`（**全是真话**）；
  它们抓到的成串幻觉由**词级游程判据**接管
  （实测 `'oh' ×20 起1.44s 整条删除`——用户报的那两条假字幕正是它抓的）
- **L3 的两条时长分支（`dur < 0.1 且词数 > 2`、`dur <= 0`）** —— 2026-09-04。
  此前 26 次开火**一次都没记过删了什么**；补 `text=` 日志重跑两支真实素材后
  五次开火全部现形，**五次全是真话**（`'We apologize for that.'`、
  `'Five, seven,'`、`'a bit.'`、`'Chto ma?'`、`'thank you.'`）。
  它还与段三自相矛盾：段三证明「零时长 ≠ 没说过」并保下 449 个零时长词，
  这里却把由这些词组成的段整条删掉。**同一个信号，在词那层救，在段这层杀。**
  0 秒字幕由 `display_timeline.py:341` 的 0.84 秒补救兜住，不会进成品
- **L4 无障碍标记（括号描述 + 音乐符号）** —— 与产品裁决「无障碍字幕保留」冲突。
  判定收进 `is_accessibility_marker()`，**两条分支共用同一个函数**，
  「只改一处忘了另一处」写不出来。
  ⚠️ **这一条是清理规则重复，不是用户可见的修复**：那 22 次删掉的 `♪♪♪`
  全部是 `words=0` 的段，**文字进不了成品**——删和不删用户拿到的字幕一样。
  施工时的理由「修的是从没开过火的那条」**只数了次数**，见下方三问表第 3 问
- L5 相邻重复 —— 正常素材唯一一次开火删的就是真人连说的第二句 `Canada.`
- **L1 的两条丢弃分支** —— 最后一处「单一弱证据持有删除权」。
  留着它，「单一弱证据没有删除权」这句话在代码里就是假的。
  **一条原则留着一个例外，那不叫原则，叫口号。**
- 语速异常本来就只标记，未动

> **判据统一**：文字长得像重复、含某个关键词、被括号包住、是音乐符号、
> 与邻居雷同、一个声音标签说不是人声——**全是弱证据，只能标可疑。**

> ⚠️ **第 3、4 条的日志此前不记 text**（第 4 条根本没有日志），
> 26 次开火 26 次不知道删了什么。2026-09-04 已补 `text=`。
> 本项目所有删除分支的日志**必须带 text**。

### 判一条删除分支该不该留，三问缺一不可

| # | 问题 | 犯过的错 |
|---|---|---|
| 1 | **它开火几次？** | —— |
| 2 | **它删掉的是什么？** | 关键词分支只数了次数，12 次开火 0 次正确，三个月没人发现 |
| 3 | **它删掉的东西，到得了用户手里吗？** | 无障碍标记那一刀看了内容，但那 22 条全是 `words=0` 的段，**进不了成品**；理由因此是错的 |

**三问是递进的，前两问答对了照样会栽在第三问上。**
同一天 P460 栽的也是第三问的变体——**在 `transcript.json`（组句前的中间产物）
量了一个数字，而用户看到的数字在组句之后才算出来**。

> **一句话**：数次数 → 看内容 → **看它到不到用户**。
> 前两步是这批建立的方法论，第三步是这批栽在上面之后补的。

### 明确接受的选择（不是欠账）

**单条成对的片尾幻觉删不掉**，例如 `'Thank you for watching Thank you for watching'`。

区分它和 `'Canada. Canada.'` 只能靠内容或阈值——前者是本批以 12:0 的战绩废掉的
关键词匹配，后者是本批一直拒绝引入的东西。而这种形状在 12 个真实任务里
**从未出现过**。

**这是一个已裁决的选择，不是待办**：宁可放过一类从未出现过的片尾幻觉，
也不再删掉真人说的话。
**重开条件**：日志里真的出现这种形状，且用户报告它进了成品。

Processing order 里 L3 排在最后是**执行顺序，不是数据依赖**：L3 只做段内判断，
不读任何跨段信息。真依赖只有一条：L5 必须等 L4，因为 L4 删段会改变「相邻」关系。
"""
import logging
import re
import unicodedata
from bisect import bisect_right
from collections import Counter
from difflib import SequenceMatcher

_log = logging.getLogger("hallucination_filter")


def _stem_chinese_text(text):
    return set(re.findall(r'[一-龥]', text))


_EN_SUFFIXES_4 = ('tion', 'ment', 'ness', 'able', 'ible')
_EN_SUFFIXES_3 = ('ful', 'ous', 'ive', 'ize', 'ise', 'ity', 'ing')
_EN_SUFFIXES_2 = ('ed', 'er', 'ly', 'al')


def _normalize_english_three_suffix_stem(word):
    if word.endswith('i') and len(word) > 2:
        return word
    if len(word) >= 3 and word[-1] == word[-2]:
        return word[:-1]
    return word


def _strip_english_suffix(word):
    if word.endswith(_EN_SUFFIXES_4):
        return word[:-4]
    if word.endswith(_EN_SUFFIXES_3):
        return _normalize_english_three_suffix_stem(word[:-3])
    if word.endswith(_EN_SUFFIXES_2):
        return word[:-2]
    if word.endswith('es'):
        return word[:-2]
    if word.endswith('s') and not word.endswith('ss'):
        return word[:-1]
    return word


def _stem_english_word(word):
    if len(word) >= 4:
        word = _strip_english_suffix(word)
    return word


def _stem_english_text(text):
    words = re.findall(r"[a-z0-9']+", text)
    return {_stem_english_word(w) for w in words}


_RU_SUFFIXES = (
    'его', 'ого',                # adj genitive - P160: missing in v1
    'ами', 'ями', 'ов', 'ев', 'ей', 'ег',
    'ах', 'ях', 'ом', 'ем', 'ой', 'ей',
    'ую', 'юю', 'ая', 'яя', 'ое', 'ее',
    'ые', 'ие', 'ым', 'им', 'их', 'ых',
    'ешь', 'ет', 'ем', 'ете', 'ут', 'ют',
    'ишь', 'ит', 'им', 'ите', 'ат', 'ят',
    'ся', 'сь',                   # reflexive - P160: missing in v1
    'л', 'ла', 'ло', 'ли',
    'ть', 'ти', 'а', 'я', 'о', 'е', 'ы', 'и', 'ь',
)

_RU_SHORT_SUFFIXES = ('а', 'я', 'о', 'е', 'ы', 'и', 'ь')


def _strip_russian_suffix(word):
    stemmed = False
    for suff in _RU_SUFFIXES:
        if word.endswith(suff):
            word = word[:-len(suff)]
            stemmed = True
            break
    if not stemmed:
        for suff in _RU_SHORT_SUFFIXES:
            if word.endswith(suff) and len(word) - len(suff) >= 3:
                word = word[:-len(suff)]
                break
    if word.endswith('ь') and len(word) > 3:
        word = word[:-1]
    return word


def _stem_russian_text(text):
    words = re.findall(r'[а-яё0-9]+', text)
    stems = []
    for w in words:
        if len(w) > 3:
            w = _strip_russian_suffix(w)
        stems.append(w)
    return set(stems)


def _stem_japanese_korean_text(text, lang):
    words = re.findall(r'[぀-ゟ゠-ヿ一-龯가-힣]+', text)
    if lang == "ja":
        ja_particles = {'は', 'が', 'を', 'に', 'の', 'で', 'へ', 'と', 'から',
                        'まで', 'です', 'ます', 'た'}
        return {w for w in words if w not in ja_particles}
    return set(words)


class LightweightStemmer:
    """Zero-dependency multi-language stemmer (P160).

    Desktop-app safe — no NLTK, no model downloads, no network required.
    Each language gets a different key because word morphology differs:

      zh — no stemming; extract individual characters
      en — strip common suffixes (-ing/-ed/-es/-s/-tion/-ment/-ness/-ly
            -able/-ible/-ful/-ous/-ive/-ize/-ise/-ity/-al)
      ru — manual Snowball-esque suffix list (case/tense/aspect)
      ja/ko — filter grammatical particles, keep content words
    """
    @staticmethod
    def clean_and_stem(text, lang):
        if not text or not text.strip():
            return set()
        text = text.lower()

        # Chinese: extract individual hanzi characters
        if lang == "zh":
            return _stem_chinese_text(text)

        # English: lightweight suffix stripping
        if lang == "en":
            return _stem_english_text(text)

        # Russian: manual suffix stripping (Snowball-inspired, P160)
        if lang == "ru":
            return _stem_russian_text(text)

        # Japanese/Korean: filter grammatical particles
        if lang in ("ja", "ko"):
            return _stem_japanese_korean_text(text, lang)

        # Default: split on whitespace
        return set(text.split())


# ── G12 第二批段一：`HALLUCINATION_KEYWORDS` 已删除，不要加回来 ──
#
# 它做的是**子串匹配整句删除**：句子里出现「音楽」两个字，整句就没了。
# 真实任务 `fc441fb713` 因此删掉了五句正常日语，例如
# 「私たちは、映画、音楽、シリーズで学校で。」（我们通过电影、音乐、剧集在学校学的）。
#
# 全库 12 次开火的实际战果：
#   5 次 —— 删掉真人说的整句（误删）
#   7 次 —— 删掉歌词与 `Music` 标记，而产品已裁决无障碍字幕要保留
#   0 次 —— 正确
#
# 这两类误删今天都没伤到用户，只是因为过滤结果从未进过成品（P447）。段二接通它。
#
# **为什么是删代码而不是改成「只标记」**：留一个只标记的关键词分支，
# 等于留一条随时能被加回删除权的路。下一个人会问「既然标了为什么不删」。
# 守着这件事的是 `test_g12b_delete_permission.py::TestA1KeywordConstantGone`。

# P250: thresholds in speech-units/sec. CJK (zh/ja) counts characters
# (1 char = 1 syllable); other languages count whitespace-split words.
# An English word averages 5-6 chars, so the old char-based en=25 fired on
# normal speech (~4.5 words/sec); 8 words/sec is the repetition-hallucination
# floor.
_SPEECH_RATE_THRESHOLDS = {
    "zh": 15,    # chars/sec
    "ja": 15,    # chars/sec
    "en": 8,     # words/sec
    "ru": 8,     # words/sec
    "auto": 7,   # words/sec (split fallback)
}


def count_speech_units(text: str, lang: str) -> int:
    """Speech units for speech-rate estimation (P250).

    CJK languages are syllable-timed and unspaced: one character ≈ one
    syllable, so count characters (matches the original len() behaviour for
    zh/ja exactly). Everything else is counted by whitespace-split words.
    """
    t = text.strip()
    if not t:
        return 0
    if lang in ("zh", "ja"):
        return len(t)
    return len(t.split())

_RE_BRACKET = re.compile(r"^\s*[\[\(（【].+[\]\)）】]\s*$")

# ── 无障碍标记：一律保留，一条都不删。这条规则在项目里只此一份 ──
#
# ⚠️ **这是一次「清理规则重复」，不是一次用户可见的修复。**
# 施工时的理由写的是「音符开火 22 次、括号描述 0 次，修错了那一条」
# ——**那个理由只数了次数**。真跑之后发现：那 22 次删的 `♪♪♪` 全部是
# `words=0` 的段（Whisper 兜底段），**文字根本进不了成品**。
# 删和不删，用户拿到的字幕一模一样。**收益是防未来的写法漂移，不是当下的改善。**
# 别照着「开火 22 次」这个数字去理解本次改动，也别照这个模式再做一遍。
#
# 2026-09-02 产品裁决：「无障碍字幕保留」。G12 第二批收尾时发现，
# **同一条裁决被执行了两遍，两遍结论相反**：
#   括号描述 `[Music]`  → 收尾时改成只标记 ✅   开火 **0 次**
#   音符     `♪♪♪`      → 仍在删除        ❌   开火 **22 次**（均不进成品）
#
# 根因是这条裁决没有落点——它写在文档里，靠人每次记得。于是每冒出一种新写法
# （`(音乐)`、`[SOUND]`、`🎶`）就要重新吵一遍。
# 按「防护优先于补救」：这是**自己种的**病，防护做到**第 1 层**——
# 规则收进一个函数，两个分支都必须经过它，「只改一处忘了另一处」写不出来。
# ⚠️ 只放音乐符号族。`🔊`/`👏` 这类**没有在任何真实素材里出现过**，
# 加它们等于凭想象扩权——本项目的判据是「这件事在真实用户身上发生过吗」。
# 真实数据只有三种写法：♪♪♪（11 次）、🎵（6 次）、♪♪（5 次）。
_ACCESSIBILITY_SYMBOLS = "♪♫♬♩🎵🎶🎼"


def is_accessibility_marker(text: str) -> bool:
    """这段文字是不是无障碍标记（音乐符号、括号描述）。

    **是 → 永远只标记 uncertain，永远不删除。**

    两种形状：
      · 括号描述：`[Music]` `（笑）` `【背景音效】` —— 整条被括号包住
      · 音乐符号：`♪♪♪` `🎵` `♪♪` —— 全是符号且其中含音符

    **纯标点不算**（`...` `???` `——`）：它们不携带任何信息，
    删掉不影响任何观众，属于「物理上不可能是字幕」。

    **已知并接受的行为变化**（⑦.5 diff 审查记录）：括号里全是标点的形状
    （`[...]`、`（。。。）`）此前先被「纯符号」判到、直接删除；现在括号判定
    排在前面，它们改为只标记。真实素材开火 **0 次**，harm 是一条噪音字幕
    留在成品里并标存疑——**不为一个零发生的形状加特例**。
    """
    t = (text or "").strip()
    if not t:
        return False
    if _RE_BRACKET.match(t):
        return True
    if any(c.isalnum() for c in t):
        return False
    return any(c in _ACCESSIBILITY_SYMBOLS for c in t)


_RE_REPEATED_PATTERN = re.compile(r"^(.{1,4})\1{2,}$")
_RE_REPEATED_WORDS = re.compile(r"^(\S+)(\s+\1)+$")


def _layer1_dual_signal(seg, aligner_result=None, source_lang="auto") -> bool:
    """L1: SenseVoice + Qwen3-FA dual-signal cross-verification (Phase 1D).

    Four-way decision matrix (requires both signals):
      SenseVoice non-human + no alignment → discard (both confirm)
      SenseVoice non-human + has alignment → keep (SV likely wrong)
      SenseVoice human   + no alignment → keep + mark [?] (possible hallucination)
      SenseVoice human   + has alignment → normal pass (both confirm)

    P183: When aligner_result is None or empty, falls back to single-signal
    — trusts SenseVoice alone for non-Speech tags (no alignment data to cross-check).
    """
    tag = getattr(seg, "sensevoice_tag", None)

    # No SenseVoice data → skip L1
    if tag is None:
        return False

    is_nonhuman = (tag != "Speech")
    has_alignment = _segment_has_alignment(seg, aligner_result)

    # ── G12 第二批收尾：L1 的两条丢弃分支交出删除权，改为只标记 ──
    #
    # 这是**最后一处「单一弱证据持有删除权」**。`layer1_sv_only_nonhuman`
    # 干的是：对齐器不可用时，只凭一个 SenseVoice 声音标签就删掉整条字幕。
    # P445 原文点名过这个形状。
    #
    # 留着它，「单一弱证据没有删除权」这句话在代码里就是假的——
    # **一条原则留着一个例外，那不叫原则，叫口号。**
    # 下一个人读到 P445 再读到这里，会得出唯一合理的结论：这条原则写着好看。
    #
    # 真实素材开火 0 次（日志里只有 `L1 SV误判清除` 1804 次、
    # `L1 疑似幻觉 [?]` 18 次、`L1 短词豁免` 6 次，全是标记或清除）。
    #
    # 原实现对 CJK 已经只标记、对其他语言却删除——同一件事两套行为，
    # 现在统一：所有语言都只标记。
    if not (aligner_result is not None and len(aligner_result) > 0):
        # P183 单信号回退：没有对齐数据时，声音标签是唯一信号——**只标可疑**
        if is_nonhuman:
            seg.uncertain = True
            seg.filter_reason = seg.filter_reason or "layer1_sv_only_nonhuman_uncertain"
            _log.info("L1 单信号疑似非人声（只标记）: start=%.2f tag=%s text=%r",
                      seg.start, tag, seg.text[:50])
        return False

    # ── Dual-signal path (only when we have actual alignment data) ──
    if is_nonhuman and not has_alignment:
        seg.uncertain = True
        seg.filter_reason = seg.filter_reason or "layer1_dual_nonhuman_uncertain"
        _log.info("L1 双信号疑似非人声（只标记）: start=%.2f tag=%s text=%r",
                  seg.start, tag, seg.text[:50])
        return False
    elif is_nonhuman and has_alignment:
        seg.sensevoice_tag = None
        seg.bgm_override = True  # P229: signal L2 to use lower threshold
        _log.info("L1 SV误判清除: start=%.2f tag=%s (对齐器找到了词)",
                  seg.start, tag)
        return False
    elif not is_nonhuman and not has_alignment:
        # P262: 短词豁免——对齐器在 ≤3 个语音单位时物理层面无法可靠对齐。
        # 但只豁免"Speech"标签的短词；Music/Noise 标签的短词仍然标记 [?]
        # （因为背景音中出现的短词更可能是幻觉）。
        if tag == "Speech" and count_speech_units(seg.text, source_lang) <= 3:
            _log.info("L1 短词豁免: start=%.2f tag=%s text=%r",
                      seg.start, tag, seg.text[:50])
            return False
        seg.uncertain = True
        _log.info("L1 疑似幻觉 [?]: start=%.2f tag=%s text=%r",
                  seg.start, tag, seg.text[:50])
        return False
    else:
        return False


def _qwen_segments_as_alignment(qwen_chunks) -> list:
    """Flatten Qwen word-level segments into FA-compatible alignment list.

    MLX path: qwen_chunks carry segments=[{text, start, end}] from internal FA.
    This flattens them into [{text, start, end}] — same shape as external FA's
    aligner_result, so L1's _segment_has_alignment() works unchanged.
    """
    alignment = []
    for chunk in qwen_chunks or []:
        if not isinstance(chunk, dict):
            continue
        for seg in chunk.get("segments") or []:
            start = seg.get("start")
            end = seg.get("end")
            if start is not None and end is not None:
                alignment.append({"text": seg.get("text", ""),
                                  "start": start, "end": end})
    return alignment


# ═══════════════════════════════════════════════════════════════════
# 词级游程判据（G12 第二批段三）
# ═══════════════════════════════════════════════════════════════════

# 「同一瞬间」最少要挤几个词才算物理不可能。
# **这不是可调阈值，是那条线本身**：一个零宽的瞬间放不下三个词，
# 放不下两个，一个都放不下。取 3 与「连续 ≥3 条」的既有语汇保持一致，
# 且实测留出了极大余量——见下表。
_IMPOSSIBLE_INSTANT_MIN = 3


def _run_key(word) -> str:
    """游程分组用的词形：忽略大小写与尾部标点。

    收整个词对象而不是文字，是为了和 `_word_bounds` 保持同一套容错：
    非 dict 元素返回空串（不成游程，断开当前游程），
    **而不是抛 AttributeError 中断整条扫描**。
    """
    try:
        text = word.get("text")
    except AttributeError:
        return ""
    return str(text or "").strip().lower().strip(".,!?;:，。！？；：")


def _word_bounds(word):
    """取词的起止时间；字段缺失或不可解析时返回 None（跳过它，不中断扫描）。"""
    try:
        return float(word["start"]), float(word["end"])
    except (KeyError, TypeError, ValueError):
        return None


def find_impossible_instant_runs(words) -> set:
    """找出物理上不可能的同词游程，返回要删除的**词索引集合**。

    **判据里没有任何阈值。**「连续 ≥3 个同词落在同一个时间点上」与
    `_layer3_speech_rate` 的 `dur <= 0` 是同一个性质：**物理上不可能**。
    一个零宽的瞬间放不下三个词。

    找到这个不可能的核心之后，**向两侧扩展到相邻的同词 token**，整条游程一起删。
    真实任务 `1a1b9f8145` 里那两个时间正常的长 `Oh`
    （1.44–4.24 与 13.886–21.806，正是用户报的两条假字幕）就是靠扩展带走的
    ——它们自己的时间没问题，但它们身处一段模型明显失控的连续输出之中。

    **两个真实任务 8 处同词游程的实测**：

    | 游程 | 同一瞬间最长连号 | 判定 |
    |---|---|---|
    | `oh` ×20 | **9** | 删（幻觉） |
    | `well`／`really`／`mmm`／`i`／`you`／`it's`／`the` ×3 | **1** | 留（真话） |

    **9 对 1。中间没有可调的余地**——这就是为什么它不需要阈值。
    （对比：按「零时长占比」判是 90% 对 33%，那才需要在 34–89 之间挑一个数字。）

    **走查 Q6「分成几份了吗」的三个答案**：
      ① 每条游程**单独判断**，一条命中不牵连另一条；
      ② 每条游程的结果**各自记账**，多条命中时全部计入，不互相覆盖；
      ③ 单个词的时间字段坏掉只**跳过它并断开当前游程**，
         其余游程照常裁定——**不整体放弃**（P404 教训）。
    """
    removed = set()
    if not words:
        return removed

    run_start = 0
    run_key = None
    for index in range(len(words) + 1):
        bounds = _word_bounds(words[index]) if index < len(words) else None
        key = _run_key(words[index]) if index < len(words) else ""
        if bounds is not None and key and key == run_key:
            continue
        if run_key is not None and index - run_start >= _IMPOSSIBLE_INSTANT_MIN:
            if _longest_same_instant_streak(
                words[run_start:index]
            ) >= _IMPOSSIBLE_INSTANT_MIN:
                removed.update(range(run_start, index))
                _log.info(
                    "词级游程判定为不可能: %r ×%d 起%.2fs（整条删除）",
                    run_key, index - run_start,
                    _word_bounds(words[run_start])[0],
                )
        if bounds is None:
            run_start, run_key = index + 1, None
        else:
            run_start, run_key = index, key
    return removed


def _longest_same_instant_streak(run) -> int:
    """游程内「连续多少个词挤在同一个零宽瞬间」的最大值。

    ⚠️ **依赖浮点精确相等**（`previous == bounds[0]`）。今天成立，因为这些
    时间戳同源且未经算术：`_clip_chunk_segments` 里越界时是 `end = start`
    直接赋值，不是计算出来的。

    **任何对这些时间戳做算术的改动（重采样、加偏移、四舍五入）都会让连号
    检测静默失效——幻觉重新进成品，而且现有测试全都用精确相同的字面量，
    一条都不会红。** 真要改，先把这里换成带容差的比较。
    """
    best = 0
    current = 0
    previous = None
    for word in run:
        bounds = _word_bounds(word)
        if bounds is None or bounds[1] > bounds[0]:
            current, previous = 0, None
            continue
        current = current + 1 if previous == bounds[0] else 1
        previous = bounds[0]
        best = max(best, current)
    return best


# 零时长词落在区间内时记多少重叠量。
# 取一个极小正值：它必须 >0（这样「点落在区间内」算重叠），
# 又必须输给任何真有宽度的重叠（这样「哪个段更贴合」的比较不受干扰）。
_ZERO_WIDTH_OVERLAP = 1e-9


def time_overlap(word_start, word_end, span_start, span_end) -> float:
    """词与时间区间的重叠量。**零时长词按「点落在区间内（含端点）」算**。

    **这条规则在项目里出现过两次，两次都写错，所以放在这里只此一份**
    （G12 第二批段三）：

      1. `_segment_has_alignment` —— L1 的对齐证据判定
      2. `transcriber_v2._assemble_qwen_words_on_whisper_boundaries` —— 词归属哪个
         Whisper 段

    两处原本都用严格不等号／重叠量 `>0`，而**零宽区间的重叠量恒为 0**，
    于是零时长词一律被判成「不重叠」。后果分别是：

      · L1 把由零时长词组成的段判成「没有对齐证据」，通向丢弃分支
        —— 实测会删掉 `'We apologize for that'`（真实空乘广播）、`'ten'`、`'to'`
      · 组装时零时长词落进 `unmatched`，被组成孤立段追加到末尾再排序
        —— 实测把 `This is our last` 交付成 **`This our last stop… is`**

    **正常词仍走严格重叠**：只碰到端点不算，否则相邻两段会互相「借」证据。
    """
    if word_end > word_start:
        return max(0.0, min(word_end, span_end) - max(word_start, span_start))
    return _ZERO_WIDTH_OVERLAP if span_start <= word_start <= span_end else 0.0


def _segment_has_alignment(seg, aligner_result) -> bool:
    """段的时间范围里有没有对齐证据。

    实测（两个真实任务，段三形状）：修复前 14 条与 1 条段被判成无对齐证据，
    修复后各 0 条；**当前生产形状下判定改变 0 条**（无回归）。
    """
    if not aligner_result:
        return False
    return any(
        time_overlap(w.get("start", 0), w.get("end", 0), seg.start, seg.end) > 0
        for w in aligner_result
    )


def _layer4_regex(seg) -> bool:
    t = seg.text.strip()
    if not t:
        seg.filtered = True
        seg.filter_reason = "layer4_regex"
        return True

    # 无障碍标记先于一切删除判定 —— 音符和括号描述都在这里被拦下，只标记不删。
    # 判定唯一来源是 `is_accessibility_marker()`，见其 docstring。
    if is_accessibility_marker(t):
        seg.uncertain = True
        seg.filter_reason = seg.filter_reason or "layer4_accessibility_uncertain"
        _log.info("L4 无障碍标记（只标记）: start=%.2f text=%r", seg.start, t[:50])
        return False

    tl = t.lower()
    if all(not c.isalnum() and c not in "你我他她它的了是有不在" for c in tl):
        seg.filtered = True
        seg.filter_reason = "layer4_regex"
        _log.info("L4 纯符号: start=%.2f text=%r", seg.start, t[:50])
        return True

    # ── 下面三条都是「文字长得像重复」——弱证据，**只标记，不删除** ──
    #
    # G12 第二批段一（闸门 2 真跑补漏）。P445 早就写明：
    #   「可直接清理：空文字、纯结构符号。只能标记可疑：普通词语、**重复句**、语速异常。」
    # 段一第一版只做了 L5（段级重复），这三条一条没动，理由是「开火次数少」
    # ——**只数了次数，没看删了什么**。段二接通交付流之后，漏洞对用户生效。
    #
    # 全库审计（这三条分支删掉的**全部**内容）：
    #   'Canada. Canada.'    真话（真人连说两次，闸门 2 真跑三次全部命中）
    #   'Oh yeah. Oh yeah.'  真话
    #   'Meow Meow'          真话（学猫叫）
    #   'oh, oh, oh, …' ×2   幻觉 —— 但它们 `start == end`，**L3 的 `dur <= 0` 照样删**
    #
    # 循环句式 2 次开火 2 次删真话 0 次正确；重复模式抓到的幻觉由 L3 兜住。
    # **接管者是 L3 的结构性判据，不是没人管。**
    t_nospace = unicodedata.normalize("NFKC", t.replace(" ", ""))
    if getattr(seg, "l2_status", None) != "GREEN":
        if len(t_nospace) >= 3 and _RE_REPEATED_PATTERN.match(t_nospace):
            seg.uncertain = True
            seg.filter_reason = seg.filter_reason or "layer4_repetition_uncertain"
            _log.info("L4 重复模式（只标记）: start=%.2f text=%r", seg.start, t[:50])
            return False

    if getattr(seg, "l2_status", None) != "GREEN":
        t_normalized = unicodedata.normalize("NFKC", t)
        if _RE_REPEATED_WORDS.match(t_normalized):
            seg.uncertain = True
            seg.filter_reason = seg.filter_reason or "layer4_repetition_uncertain"
            _log.info("L4 重复词（只标记）: start=%.2f text=%r", seg.start, t[:50])
            return False

    half = len(t) // 2
    if half >= 5:
        first_half = t[:half].strip()
        second_half = t[half:].strip()
        if first_half and first_half == second_half:
            seg.uncertain = True
            seg.filter_reason = seg.filter_reason or "layer4_repetition_uncertain"
            _log.info("L4 循环句式（只标记）: start=%.2f text=%r", seg.start, t[:50])
            return False

    return False


# L5 的两个阈值，都由真实素材标定，不是拍出来的。
#
# 相似度 0.9：第一批 ⑤ D 节实测——完全相同 25 处 vs ≥0.9 共 26 处，
#             只差 1 处，无副作用，因此保留。
# 连续 3 条：三个长视频实测——两个正常素材的游程最长只有 2（零误伤），
#           已知幻觉素材有 3/3/3/5/6/6/11（命中全部 7 处长游程）。
#
# ⚠️ **「间隔 ≤0.6 秒」这一条经实测方向相反，已砍掉，不要加回来。**
#    已知幻觉素材 7 处长游程只有 1 处满足 ≤0.6 秒
#    （其余间隔 9.10／2.14／23.50／15.44／2.30／3.26 秒）；
#    而正常素材里唯一 ≤0.6 秒的，是真人连说两次 `Canada.`（间隔 0.156 秒）。
#    加上它，覆盖率从 7/25 掉到 1/25，同时把真话推进删除面。
_L5_SIMILARITY = 0.9
_L5_MIN_RUN = 3


def _texts_are_similar(left: str, right: str) -> bool:
    left, right = left.strip(), right.strip()
    if left == right:
        return True
    return SequenceMatcher(None, left, right).ratio() >= _L5_SIMILARITY


def _layer5_dedup(segments: list) -> list:
    """L5：**只标记，不删除**（G12 第二批段一收回删除权）。

    原实现是「相邻两条文字相同就删掉后一条」，**没有任何数量、时间或相似度约束**。
    真实任务 `80703166ff`（30 分钟正常英语对话）全片只开火一次，
    删的就是真人连说的第二句 `Canada.`（100.02 秒）——正常素材上的准确率 0/1。

    现在它只做一件事：把「连续 ≥3 条、彼此相似度 ≥0.9」的游程整段标为可疑，
    一条都不删。**两条不算游程**——那正是 `Canada.` 的形状。

    返回值仍是完整列表（调用方 `filter_hallucinations` 会在 L3 之后统一按
    `filtered` 收口），这样 L3 看到的相邻关系与改动前一致。
    """
    if len(segments) < _L5_MIN_RUN:
        return segments

    run_start = 0
    for index in range(1, len(segments) + 1):
        if index < len(segments) and _texts_are_similar(
            segments[index].text, segments[index - 1].text
        ):
            continue
        run_length = index - run_start
        if run_length >= _L5_MIN_RUN:
            for seg in segments[run_start:index]:
                seg.uncertain = True
            _log.info(
                "L5 疑似重复游程: start=%.2f 条数=%d text=%r（只标记，未删除）",
                segments[run_start].start, run_length,
                segments[run_start].text[:50],
            )
        run_start = index
    return segments


# P148: fraction of identical qwen_text above which Qwen-ASR is deemed collapsed
# (repetition collapse) and therefore an untrustworthy cross-validation referee.
_QWEN_COLLAPSE_RATIO = 0.3

# P251: local sentence split for L2 Qwen handling. Kept here (not imported from
# qwen_asr_runner) because qwen_asr_runner already imports from this module —
# importing back would create a circular import.
_L2_SENTENCE_SPLIT = re.compile(r"[。！？.!?；;]+")


def _split_for_l2(text: str) -> list:
    return [p.strip() for p in _L2_SENTENCE_SPLIT.split(text or "") if p.strip()]


def _qwen_is_reliable(qwen_chunks: list) -> bool:
    """Detect Qwen-ASR repetition collapse (P148, P251).

    Qwen-ASR now hands L2 a list of per-chunk dicts {text, t_start, t_end}
    instead of per-segment qwen_text. Split all chunk text into sentences;
    when one identical sentence dominates (> _QWEN_COLLAPSE_RATIO of the
    non-empty sentences, e.g. job 97c57255ec rehearsed
    "1056234897 нет ты меня прости"), Qwen is an unreliable referee — skip L2.
    """
    sentences = []
    for chunk in qwen_chunks or []:
        if isinstance(chunk, dict):
            sentences.extend(_split_for_l2(chunk.get("text")))
    sentences = [s for s in sentences if s]
    if len(sentences) < 5:
        return True  # too little data to judge — don't disable L2
    most_common, count = Counter(sentences).most_common(1)[0]
    if count / len(sentences) > _QWEN_COLLAPSE_RATIO:
        _log.warning("L2 跳过：千问疑似坍缩 (同一句占 %d/%d=%.0f%%): %r",
                     count, len(sentences), 100 * count / len(sentences),
                     most_common[:40])
        return False
    return True


def _qwen_word_from_segment(seg):
    text = (seg.get("text") or "").strip()
    start = seg.get("start")
    end = seg.get("end")
    if text and start is not None and end is not None:
        return {"text": text, "start": start, "end": end}
    return None


def _extract_qwen_word_chunks(qwen_chunks):
    all_words = []
    for chunk in qwen_chunks:
        if not isinstance(chunk, dict):
            continue
        chunk_words = []
        for seg in chunk.get("segments") or []:
            word = _qwen_word_from_segment(seg)
            if word:
                chunk_words.append(word)
        if chunk_words:
            all_words.append(chunk_words)
    return all_words


def _append_split_qwen_sentence(sentences, buf_texts, buf_start, end):
    text = " ".join(buf_texts).strip()
    clean = [p.strip() for p in _L2_SENTENCE_SPLIT.split(text)
             if p.strip()]
    if clean:
        sentences.append({"text": clean[0], "start": buf_start, "end": end})


def _append_trailing_qwen_sentence(sentences, buf_texts, buf_start, end):
    text = " ".join(buf_texts).strip()
    if text:
        sentences.append({"text": text, "start": buf_start, "end": end})


def _qwen_words_to_sentences(chunk_words):
    sentences = []
    buf_texts = []
    buf_start = None
    for word in chunk_words:
        if buf_start is None:
            buf_start = word["start"]
        buf_texts.append(word["text"])
        if _L2_SENTENCE_SPLIT.search(word["text"]):
            _append_split_qwen_sentence(sentences, buf_texts, buf_start,
                                        word["end"])
            buf_texts = []
            buf_start = None
    if buf_texts:
        _append_trailing_qwen_sentence(sentences, buf_texts, buf_start,
                                       chunk_words[-1]["end"])
    return sentences


def _extract_qwen_sentences(qwen_chunks):
    """P256: merge word-level Qwen timestamps into sentence-level.

    Each Qwen chunk has segments=[{text, start, end}] at word level.
    This groups consecutive words into sentences (split by _L2_SENTENCE_SPLIT)
    and returns [{text, start, end}] at sentence level.
    """
    if not qwen_chunks:
        return []
    all_words = _extract_qwen_word_chunks(qwen_chunks)
    if not all_words:
        return []
    sentences = []
    for chunk_words in all_words:
        sentences.extend(_qwen_words_to_sentences(chunk_words))
    return sentences


_SMART_WINDOW_DEFAULT = 30
_SMART_WINDOW_MIN = 20
_SMART_WINDOW_MAX = 40


def _build_smart_boundaries(qwen_chunks, total_duration):
    """P256: build window boundaries aligned to Qwen sentence edges.

    Starts from fixed 30s cuts, then slides each cut point to avoid splitting
    a Qwen sentence. The sentence's occupancy ratio decides which side absorbs
    it. Windows are clamped to [20s, 40s].

    Returns a sorted list of floats: [0, b1, b2, ..., total_duration].
    """
    ws = _SMART_WINDOW_DEFAULT
    if total_duration <= ws:
        return [0.0, total_duration]

    fixed = [i * ws for i in range(1, int(total_duration // ws) + 1)
             if i * ws < total_duration]
    if not fixed:
        return [0.0, total_duration]

    sentences = _extract_qwen_sentences(qwen_chunks)

    boundaries = list(fixed)
    for i, b in enumerate(boundaries):
        straddlers = [s for s in sentences
                      if s["start"] < b < s["end"]]
        if not straddlers:
            continue
        s = straddlers[0]
        left_ratio = (b - s["start"]) / (s["end"] - s["start"])
        new_b = s["end"] if left_ratio >= 0.5 else s["start"]

        prev = boundaries[i - 1] if i > 0 else 0.0
        nxt = boundaries[i + 1] if i + 1 < len(boundaries) else total_duration
        win_left = new_b - prev
        win_right = nxt - new_b
        if win_left < _SMART_WINDOW_MIN or win_right < _SMART_WINDOW_MIN:
            continue
        if win_left > _SMART_WINDOW_MAX or win_right > _SMART_WINDOW_MAX:
            continue
        boundaries[i] = new_b

    return [0.0] + boundaries + [total_duration]


def _has_timestamp_segments(qwen_chunks):
    """Check if any chunk carries word-level timestamp segments (MLX path)."""
    for chunk in qwen_chunks or []:
        if isinstance(chunk, dict) and chunk.get("segments"):
            return True
    return False


def _assign_qwen_to_windows(qwen_chunks, whisper_windows, window_size, language,
                            boundaries=None):
    """Place Qwen-ASR text into 30s windows.

    Two paths:
      - Timestamp path (MLX): each segment has start/end → precise window index.
      - Similarity path (PyTorch fallback): stem overlap heuristic.
    """
    num_windows = len(whisper_windows)
    qwen_windows = ["" for _ in range(num_windows)]
    if not qwen_chunks or num_windows == 0:
        return qwen_windows

    if _has_timestamp_segments(qwen_chunks):
        return _assign_by_timestamps(qwen_chunks, num_windows, window_size,
                                     boundaries=boundaries)

    return _assign_by_similarity(qwen_chunks, whisper_windows, window_size, language)


def _assign_by_timestamps(qwen_chunks, num_windows, window_size, boundaries=None):
    """Place Qwen segments into windows using their precise start times."""
    qwen_windows = ["" for _ in range(num_windows)]
    last = num_windows - 1
    for chunk in qwen_chunks:
        if not isinstance(chunk, dict):
            continue
        for seg in chunk.get("segments") or []:
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            if boundaries:
                win = min(last, max(0, bisect_right(boundaries, seg["start"]) - 1))
            else:
                win = min(last, max(0, int(seg["start"] // window_size)))
            qwen_windows[win] += " " + text
    return qwen_windows


def _assign_by_similarity(qwen_chunks, whisper_windows, window_size, language):
    """Fallback: place Qwen text into windows by stem similarity (PyTorch)."""
    num_windows = len(whisper_windows)
    qwen_windows = ["" for _ in range(num_windows)]

    w_stems = [LightweightStemmer.clean_and_stem(w or "", language)
               for w in whisper_windows]

    for chunk in qwen_chunks:
        if not isinstance(chunk, dict):
            continue
        text = (chunk.get("text") or "").strip()
        if not text:
            continue
        last = num_windows - 1
        lo = min(last, max(0, int((chunk.get("t_start") or 0) // window_size)))
        hi = min(last, max(lo, int((chunk.get("t_end") or 0) // window_size)))
        cursor = lo
        for sentence in _split_for_l2(text):
            q_stems = LightweightStemmer.clean_and_stem(sentence, language)
            if not q_stems:
                continue
            best_win = cursor
            best_score = -1.0
            for w in range(cursor, hi + 1):
                ws = w_stems[w]
                score = len(q_stems & ws) / len(q_stems) if ws else 0.0
                if score > best_score:
                    best_score = score
                    best_win = w
            qwen_windows[best_win] += " " + sentence
            cursor = best_win
    return qwen_windows


def _l2_find_best_window(start, end, boundaries, num_windows):
    """P256：给定段 [start,end]，返回重叠最大的窗口下标。"""
    idx = max(0, bisect_right(boundaries, start) - 1)
    best = min(idx, num_windows - 1)
    max_overlap = 0
    lo = max(0, bisect_right(boundaries, start) - 1)
    hi = min(num_windows - 1, bisect_right(boundaries, end) - 1)
    for w in range(lo, hi + 1):
        ws = boundaries[w]
        we = boundaries[w + 1]
        overlap = min(end, we) - max(start, ws)
        if overlap > max_overlap:
            max_overlap = overlap
            best = w
    return best


def _score_l2_window(w_text, q_text, language, win_has_bgm,
                     current_threshold, min_stems):
    """对单窗口的主/次稿做词干重叠打分，返回 window_stats 条目。"""
    _BGM_THRESHOLD = 0.20
    if not w_text and not q_text:
        return {"status": "EMPTY", "score": 1.0,
                "w_count": 0, "q_count": 0, "shared": 0}

    if not q_text:
        return {"status": "NO_QWEN", "score": 1.0,
                "w_count": 0, "q_count": 0, "shared": 0}

    w_stems = LightweightStemmer.clean_and_stem(w_text, language)
    q_stems = LightweightStemmer.clean_and_stem(q_text, language)

    shared = w_stems & q_stems
    score = len(shared) / max(len(w_stems), len(q_stems)) if (w_stems or q_stems) else 0.0

    effective_threshold = _BGM_THRESHOLD if win_has_bgm else current_threshold

    ws_entry = {"score": score, "w_count": len(w_stems),
                "q_count": len(q_stems), "shared": len(shared),
                "bgm": win_has_bgm}

    if len(w_stems) < min_stems:
        ws_entry["status"] = "LOW_DATA"
    elif score >= effective_threshold:
        ws_entry["status"] = "GREEN"
    else:
        ws_entry["status"] = "YELLOW"

    return ws_entry


def _annotate_l2_segments(segments, window_stats, boundaries, num_windows):
    """把每个存活段标注 l2_status；YELLOW/LOW_DATA 置 uncertain。"""
    for seg in segments:
        if seg.filtered:
            continue
        win = _l2_find_best_window(seg.start, seg.end, boundaries, num_windows)
        ws = window_stats.get(win)
        if ws:
            seg.l2_status = ws["status"]
            if ws["status"] in ("YELLOW", "LOW_DATA"):
                seg.uncertain = True
        else:
            seg.l2_status = "NO_DATA"


def _layer2_cross_validate(segments: list, language: str = "auto",
                           custom_thresholds: dict = None, qwen_chunks=None,
                           alt_segments=None):
    """P160/P256: window stem-overlap cross-validation (B+A architecture).

    P256: windows align to Qwen sentence boundaries (smart boundaries)
    instead of fixed 30s cuts. Falls back to fixed 30s when no Qwen
    timestamps are available.

    P289: alt_segments — when the routing layer swaps the primary engine
    (e.g. Qwen primary), pass the secondary engine's raw segments here
    so L2 compares two different sources instead of the same one.

    Returns (annotated_segments, window_stats).
    """
    if not segments:
        return segments, {}

    # ── Per-language thresholds ──
    thresholds = {
        "zh": 0.55, "en": 0.35, "ru": 0.28,
        "ja": 0.45, "ko": 0.45,
    }
    if custom_thresholds:
        thresholds.update(custom_thresholds)
    current_threshold = thresholds.get(language, 0.30)

    # ── P256: smart window boundaries ──
    WINDOW_SIZE = 30
    total_duration_s = max(getattr(s, "end", 0) for s in segments)
    if total_duration_s <= 0:
        return segments, {}

    boundaries = _build_smart_boundaries(qwen_chunks, total_duration_s)
    num_windows = len(boundaries) - 1

    primary_windows = ["" for _ in range(num_windows)]
    source_segments = alt_segments if alt_segments else segments
    for seg in source_segments:
        if getattr(seg, "filtered", False):
            continue
        win = _l2_find_best_window(seg.start, seg.end, boundaries, num_windows)
        if win < num_windows:
            primary_windows[win] += " " + seg.text

    secondary_windows = _assign_qwen_to_windows(
        qwen_chunks, primary_windows, WINDOW_SIZE, language,
        boundaries=boundaries)

    # ── Stem + score each window ──
    MIN_STEMS = 5
    window_stats = {}
    for idx in range(num_windows):
        w_text = primary_windows[idx].strip()
        q_text = secondary_windows[idx].strip()
        win_has_bgm = any(
            getattr(seg, "bgm_override", False)
            for seg in segments
            if not seg.filtered and _l2_find_best_window(
                seg.start, seg.end, boundaries, num_windows) == idx
        )
        window_stats[idx] = _score_l2_window(
            w_text, q_text, language, win_has_bgm, current_threshold, MIN_STEMS)

    # ── P149 calibration diagnostics ──
    for idx in sorted(window_stats):
        ws = window_stats[idx]
        t0 = boundaries[idx]
        t1 = boundaries[idx + 1]
        _log.debug("L2 窗口[%d] %.1f-%.1fs | w=%d q=%d shared=%d score=%.3f → %s",
                   idx, t0, t1, ws["w_count"], ws["q_count"],
                   ws["shared"], ws["score"], ws["status"])

    # ── Annotate surviving segments with l2_status ──
    _annotate_l2_segments(segments, window_stats, boundaries, num_windows)

    _log.info("L2 窗口比对完成: %d 窗口 (P256 智能边界), 阈值=%.2f, 最低词干=%d, %s",
              num_windows, current_threshold, MIN_STEMS,
              {s: sum(1 for v in window_stats.values() if v["status"] == s)
               for s in ("GREEN", "YELLOW", "LOW_DATA", "EMPTY", "NO_QWEN")})

    return segments, window_stats


def _layer3_speech_rate(seg, source_lang: str = "auto") -> None:
    dur = seg.end - seg.start
    units = count_speech_units(seg.text, source_lang)

    if source_lang in ("zh", "ja", "ko") and units <= 3 and dur < 0.35:
        seg.filter_reason = seg.filter_reason or "layer3_short_cjk_fragment"
        return

    # ── 时长异常：**只标记，不删除**（2026-09-04，读完真实内容之后定的）──
    #
    # 这两条曾是删除分支，26 次开火**一次都没记过删了什么**（下面那条根本没有日志）。
    # 补上 `text=` 重跑两支真实素材，五次开火全部现形：
    #
    #   'Five, seven,'            dur=0.000  真人数数
    #   'We apologize for that.'  dur=0.080  真实空乘广播
    #   'a bit.'                  dur=0.000  真话
    #   'Chto ma?'                dur=0.000  含糊但有人声
    #   'thank you.'              dur=0.000  落在成品 708.6→711.8 的空档里，
    #                                        前后是「你很有艺术气质」「我来自罗马尼亚」
    #
    # **五比零。** 与本批其它四条同一个形状：关键词 12:0、循环句式 2:0、
    # L5 0:1、SenseVoice 3492:0。
    #
    # 更要紧的是它和段三**自相矛盾**：段三证明「零时长 = 模型不知道它什么时候说的，
    # 不等于它没说」，据此保下 449 个零时长词；而这里把由这些词组成的段整条删掉。
    # 同一个信号，在词那层救，在段这层杀。
    #
    # **接管者**：
    #   · 成串幻觉 → 词级游程判据（真实素材实测：`'oh' ×20 起1.44s 整条删除`，
    #     用户报的那两条假字幕正是它抓的，**不是这里**）
    #   · 0 秒字幕 → `display_timeline.py:341` 的 0.84 秒补救 + 合并，
    #     并如实报「N 条字幕显示偏短」。留下零时长段不会产生 0 秒字幕。
    #
    # **已知并接受的残留**：零时长且只有两个同词的段（如 `'oh, oh'`）不再被删——
    # 词级判据要求同一瞬间 ≥3 个同词。这是 P459「单条成对的幻觉删不掉」的延伸，
    # 12 支真实任务里 0 次出现。**不为零发生的形状留一条会删真话的分支。**
    if dur < 0.1 and units > 2:
        seg.uncertain = True
        seg.filter_reason = seg.filter_reason or "layer3_impossible_duration_uncertain"
        _log.info("L3 不可能时长（只标记）: start=%.2f dur=%.3f units=%d text=%r",
                  seg.start, dur, units, seg.text.strip()[:50])
        return

    if dur <= 0:
        seg.uncertain = True
        seg.filter_reason = seg.filter_reason or "layer3_impossible_duration_uncertain"
        _log.info("L3 零时长段（只标记）: start=%.2f dur=%.3f units=%d text=%r",
                  seg.start, dur, units, seg.text.strip()[:50])
        return

    rate = units / dur
    threshold = _SPEECH_RATE_THRESHOLDS.get(source_lang, _SPEECH_RATE_THRESHOLDS["auto"])
    if rate > threshold:
        seg.uncertain = True
        _log.info("L3 语速异常: start=%.2f rate=%.1f threshold=%d lang=%s",
                  seg.start, rate, threshold, source_lang)


def _initialize_filter_state(segments):
    for seg in segments:
        if not hasattr(seg, "filtered"):
            seg.filtered = False
        if not hasattr(seg, "filter_reason"):
            seg.filter_reason = None
        if not hasattr(seg, "uncertain"):
            seg.uncertain = False


def _effective_l1_alignment(aligner_result, qwen_chunks):
    effective_alignment = aligner_result
    if not effective_alignment and _has_timestamp_segments(qwen_chunks):
        effective_alignment = _qwen_segments_as_alignment(qwen_chunks)
        _log.info("L1 使用 Qwen 内部 FA 时间戳: %d 词",
                  len(effective_alignment))
    return effective_alignment


def _apply_layer1(segments, source_lang, aligner_result, qwen_chunks, skip_l1):
    if skip_l1:
        return
    effective_alignment = _effective_l1_alignment(aligner_result, qwen_chunks)
    for seg in segments:
        if not seg.filtered:
            _layer1_dual_signal(seg, effective_alignment, source_lang)


def _apply_layer2(segments, source_lang, qwen_chunks, skip_l2, alt_segments):
    qwen_reliable = _qwen_is_reliable(qwen_chunks)
    if skip_l2:
        _log.info("L2 跳过: 单主引擎模式无第二份参考稿")
    elif qwen_reliable:
        segments, _ignore_stats = _layer2_cross_validate(
            segments, source_lang, qwen_chunks=qwen_chunks,
            alt_segments=alt_segments)
    return segments


def _apply_layer4(segments):
    for seg in segments:
        if not seg.filtered:
            _layer4_regex(seg)


def _apply_layer3(segments, source_lang):
    for seg in segments:
        if not seg.filtered:
            _layer3_speech_rate(seg, source_lang)


def _renumber_segments(segments):
    for i, seg in enumerate(segments):
        seg.id = i
    return segments


def filter_hallucinations(segments: list, source_lang: str = "auto",
                          aligner_result=None, qwen_chunks=None,
                          skip_l1: bool = False, skip_l2: bool = False,
                          alt_segments=None) -> list:
    """Five-layer hallucination filter. Order: L1→L2→L4→L5→L3.

    Args:
        segments: List of Segment objects.
        source_lang: Detected source language for L3 speech-rate thresholds.
        aligner_result: Optional Qwen3-FA alignment data (list of aligned word
            dicts). When provided, L1 uses dual-signal (SenseVoice + Qwen3-FA).
            When None, L1 skips entirely.
        qwen_chunks: Optional Qwen3-ASR output as a list of {text, t_start,
            t_end} dicts (P251). L2 assigns this text to 30s windows by
            similarity instead of reading a per-segment qwen_text. When None
            or empty, L2 runs with no Qwen cross-validation signal.

    Returns:
        Only segments with filtered=False, re-numbered.
    """
    if not segments:
        return []

    _initialize_filter_state(segments)

    # When Qwen chunks carry word-level timestamps (MLX path), derive
    # alignment data from them — no external FA needed for L1.
    _apply_layer1(segments, source_lang, aligner_result, qwen_chunks, skip_l1)

    surviving = [s for s in segments if not s.filtered]
    surviving = _apply_layer2(
        surviving, source_lang, qwen_chunks, skip_l2, alt_segments)

    _apply_layer4(surviving)

    surviving = [s for s in surviving if not s.filtered]
    surviving = _layer5_dedup(surviving)

    _apply_layer3(surviving, source_lang)

    result = [s for s in surviving if not s.filtered]
    return _renumber_segments(result)
