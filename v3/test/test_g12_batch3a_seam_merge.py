"""G12 第三批 A · P465：五分钟接缝处同一个词被两块各留一份。

**用户看到的**：47 分钟成品第 457 条 —— `我我当时想："这怎么越来越严重了！`

**根因**：每块音频前后各多切 5 秒（`_AUDIO_PAD_S`）给发音留缓冲，
`_clip_chunk_segments` 的裁剪判据是
`if abs_end <= t_start or abs_start >= t_end: continue`
——**它只排除「完全在块外」的词。横跨边界的词，两块都判定「不在我外面」，于是两边都保留。**

**全片实测（任务 1a1b9f8145，47 分钟 10 块）真正会双留的只有 3 处**：

    接缝  900   左 'hoppyer'  899.700–900.260   右 'hopier'   899.720–900.280   同一个词，两种拼写
    接缝 1500   左 'I'       1499.822–1500.062  右 'I'       1499.800–1500.040  同一个词 → 成品「我我」
    接缝 2400   左 'something'                  右 'something'                  同一个词
    接缝  600   左 'people'   599.784–600.024   右 'who'      599.960–600.120   ← 两个不同的词，都必须留

**三条被真实数据推翻、不得复现的方案**（详见
`pipelines/bug-pipeline-2026-09-05-G12-batch3A.md`）：

1. **中点判定**：900 秒处 `hopier` 中点正好 `900.000000` 撞线 → 双留。
   改按开始时间判定，则 600 秒的 `who` 被两块都扔掉 → 丢词。
2. **方案 B（重叠区整段只采信一块）**：它把接缝搬到 `bd+5`，那正是左块音频的悬崖边。
   305 秒实测块0 的 `but` 是 `304.920→304.920`（**零时长残废版**），
   块1 的是 `304.840→305.080`（完整版）——**B 会保留残废版、扔掉完好版。**
3. **IoU 阈值**：外审给出反例 `to` vs `the` IoU=0.667（两个不同的词），
   而真正对应的 `to` 因零时长 IoU=0。**两次独立识别后时间会漂移、错位、出现零时长。**

**最终方案（零阈值）**：接缝两侧的词排成两队做序列对齐；
对齐上的删右块那份，对不上的两边都留。靠的是上下文，不是时间。

**⚠️ 施工陷阱**：判断必须用**裁剪前**的 `raw_segments`。
裁剪后三组重复词的时间被压成首尾相接（左块 end=bd、右块 start=bd），
任何基于时间重叠的判断都会永远判「不重复」——写出来是跑不生效的空壳。

Run: cd v3 && .venv/bin/python -m pytest test/test_g12_batch3a_seam_merge.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest

from backend.core.qwen_asr_runner import _AUDIO_PAD_S, _merge_chunk_seams


def _rel(abs_t: float, t_start: float) -> float:
    """把绝对时间换算成 raw_segments 里的块内相对时间。"""
    return abs_t - max(0.0, t_start - _AUDIO_PAD_S)


def _seg(text: str, abs_start: float, abs_end: float, t_start: float) -> dict:
    return {"text": text,
            "start": _rel(abs_start, t_start),
            "end": _rel(abs_end, t_start)}


def _clip(raw: list, t_start: float, t_end: float) -> list:
    """复刻 _clip_chunk_segments 的裁剪结果（本测试只需要它的输出形状）。"""
    es = max(0.0, t_start - _AUDIO_PAD_S)
    out = []
    for s in raw:
        a = s["start"] + es
        b = s["end"] + es
        if b <= t_start or a >= t_end:
            continue
        start = max(a, t_start)
        end = min(b, t_end)
        if end <= start:
            end = start
        out.append({"text": s["text"], "start": start, "end": end})
    return out


def _chunk(t_start: float, t_end: float, words: list) -> dict:
    """words: [(text, abs_start, abs_end), ...] —— 同时生成 raw_segments 与裁剪后 segments。"""
    raw = [_seg(t, a, b, t_start) for (t, a, b) in words]
    return {"t_start": t_start, "t_end": t_end,
            "raw_segments": raw,
            "segments": _clip(raw, t_start, t_end),
            "text": " ".join(w["text"] for w in _clip(raw, t_start, t_end))}


def _texts(chunk: dict) -> list:
    return [s["text"] for s in chunk["segments"]]


# ── 真实数据（任务 1a1b9f8145 的 raw_segments，绝对时间）──────────────

SEAM_1500_LEFT = [
    ("hot", 1499.422, 1499.662),
    ("I", 1499.822, 1500.062),      # ← 跨界
    ("was", 1500.062, 1500.142),
    ("like", 1500.142, 1500.382),
]
SEAM_1500_RIGHT = [
    ("hot", 1499.400, 1499.720),
    ("I", 1499.800, 1500.040),      # ← 跨界，与左块是同一个词
    ("was", 1500.040, 1500.200),
    ("like", 1500.200, 1500.440),
]

SEAM_900_LEFT = [
    ("bit", 899.460, 899.620),
    ("hoppyer", 899.700, 900.260),  # ← 跨界
    ("than", 900.420, 900.660),
]
SEAM_900_RIGHT = [
    ("bit", 899.480, 899.640),
    ("hopier", 899.720, 900.280),   # ← 跨界，同一个词的另一种拼写
    ("than", 900.440, 900.680),
]

SEAM_600_LEFT = [
    ("talked", 599.544, 599.784),
    ("to", 599.784, 599.784),       # 零时长词，真实存在
    ("people", 599.784, 600.024),   # ← 跨界
    ("who", 600.024, 600.104),
    ("are", 600.104, 600.264),
]
SEAM_600_RIGHT = [
    ("talked", 599.560, 599.720),
    ("to", 599.800, 599.800),
    ("people", 599.800, 599.960),
    ("who", 599.960, 600.120),      # ← 跨界，与左块保留的 people 是不同的词
    ("are", 600.120, 600.280),
]


class TestSeamMergeRemovesDuplicate(unittest.TestCase):
    """T9 / C1 C3：真实重复的接缝，删右块副本。"""

    def test_t9_identical_word_at_1500_keeps_only_left(self):
        """成品「我我当时想」的那一处：`I` 只能剩一个。"""
        left = _chunk(1200.0, 1500.0, SEAM_1500_LEFT)
        right = _chunk(1500.0, 1800.0, SEAM_1500_RIGHT)

        merged = _merge_chunk_seams([left, right])

        self.assertIn("I", _texts(merged[0]), "左块的 I 必须保留")
        self.assertNotIn("I", _texts(merged[1]),
                         "右块的 I 是同一个词的第二份，必须删掉——留着就是成品里的「我我」")

    def test_t9_different_spelling_at_900_keeps_only_left(self):
        """`hoppyer` / `hopier`：文字只有 0.77 相似，但前后 `bit`/`than` 精确对齐。"""
        left = _chunk(600.0, 900.0, SEAM_900_LEFT)
        right = _chunk(900.0, 1200.0, SEAM_900_RIGHT)

        merged = _merge_chunk_seams([left, right])

        self.assertIn("hoppyer", _texts(merged[0]))
        self.assertNotIn("hopier", _texts(merged[1]),
                         "双侧有 bit/than 支撑的一词换一词，是同一个词的两次识别")


class TestSeamMergeKeepsDistinctWords(unittest.TestCase):
    """T10 / C2：两个不同的词都必须留——这是本方案最容易做错的一侧。"""

    def test_t10_people_and_who_at_600_both_survive(self):
        left = _chunk(300.0, 600.0, SEAM_600_LEFT)
        right = _chunk(600.0, 900.0, SEAM_600_RIGHT)

        merged = _merge_chunk_seams([left, right])

        self.assertIn("people", _texts(merged[0]),
                      "左块跨界的 people 必须留")
        self.assertIn("who", _texts(merged[1]),
                      "右块跨界的 who 是另一个词，删掉就是丢词")


class TestSeamMergePreservesClippedTiming(unittest.TestCase):
    """T11 / C4：保留左块**裁剪后**的时间，不得把原始时间写回成品。"""

    def test_t11_left_word_keeps_clipped_end_not_raw_end(self):
        """左块 `I` 原始 end=1500.062，而右块下一个 `was` 从 1500.040 开始。

        把原始时间写回去会造成 0.022 秒重叠。必须保留裁剪后的 end=1500.0。
        """
        left = _chunk(1200.0, 1500.0, SEAM_1500_LEFT)
        right = _chunk(1500.0, 1800.0, SEAM_1500_RIGHT)

        merged = _merge_chunk_seams([left, right])

        kept = [s for s in merged[0]["segments"] if s["text"] == "I"][0]
        self.assertAlmostEqual(kept["end"], 1500.0, places=6,
                               msg="必须是裁剪后的 1500.0，不是原始的 1500.062")


class TestSeamMergeIsolatesFailures(unittest.TestCase):
    """T12 / C5：一个接缝出事，其余接缝照常合并。

    P404 最狠的一处正是这个形状——一次无关调用断连，
    真正待恢复的那一半一次机会都没得到就被跳过。
    切一刀就多一棵树的分叉，9 个接缝是 9 个独立分叉。
    """

    def test_t12_one_broken_seam_does_not_stop_the_others(self):
        good_left = _chunk(1200.0, 1500.0, SEAM_1500_LEFT)
        good_right = _chunk(1500.0, 1800.0, SEAM_1500_RIGHT)
        # 中间插一块坏数据：raw_segments 缺失，该接缝无法对齐
        broken = {"t_start": 900.0, "t_end": 1200.0,
                  "raw_segments": None, "segments": [], "text": ""}

        merged = _merge_chunk_seams([broken, good_left, good_right])

        self.assertNotIn("I", _texts(merged[2]),
                         "前一个接缝坏掉，不得影响 1500 这个接缝的合并")


class TestSeamMergeOneToTwoIsOutOfScope(unittest.TestCase):
    """T14 / C7：一词变两词——**2026-09-05 外审裁决本批不处理**，两边都保留。

    这不是期望行为，是被明确裁定的暂不做：没有真实案例，
    强行处理会扩大删除权。将来要改这里，请先读管道文件的裁决记录。
    """

    def test_t14_one_to_two_replacement_keeps_both_sides_2026_09_05_ruling(self):
        left = _chunk(1200.0, 1500.0, [
            ("hot", 1499.422, 1499.662),
            ("cannot", 1499.822, 1500.062),   # ← 跨界，一个词
            ("was", 1500.062, 1500.142),
        ])
        right = _chunk(1500.0, 1800.0, [
            ("hot", 1499.400, 1499.720),
            ("can", 1499.800, 1500.020),      # ← 真的跨界（否则裁剪先把它扔了，测不到 1↔2）
            ("not", 1500.020, 1500.140),
            ("was", 1500.140, 1500.300),
        ])

        merged = _merge_chunk_seams([left, right])

        self.assertIn("cannot", _texts(merged[0]))
        self.assertIn("can", _texts(merged[1]),
                      "1↔2 替换不在本批范围内，必须两边都保留")


if __name__ == "__main__":
    unittest.main()


class TestSeamMergeIsWiredIntoDelivery(unittest.TestCase):
    """T13 / C6：接缝合并必须落在 `_refresh_chunk_delivery` 这条**三出口共用**的路径上。

    **为什么这条测试比它看起来重要**：本批同时要把
    `_TRANSCRIPTION_CONTRACT_VERSION` +1。项目学习库里有一条 2026-09-02 的警告：

        提高转录合同版本作废 transcript.json，**恰好把执行推进全缓存命中分支**——
        改好的算法用旧 qwen_chunks.json 重跑不会生效。

    `:919`（全缓存命中）、`:934`（模型加载失败）、`:972`（正常完成）三个返回点
    的返回语句完全相同，都经过 `_refresh_chunk_delivery`。
    **合并写在转录循环里的话，老任务重跑一行都不会执行，而且表面完全正常。**
    """

    def test_t13_refresh_chunk_delivery_applies_seam_merge(self):
        from backend.core.qwen_asr_runner import _refresh_chunk_delivery

        left = _chunk(1200.0, 1500.0, SEAM_1500_LEFT)
        right = _chunk(1500.0, 1800.0, SEAM_1500_RIGHT)

        refreshed = _refresh_chunk_delivery([left, right], "en")

        left_i = _texts(refreshed[0]).count("I")
        right_i = _texts(refreshed[1]).count("I")
        self.assertEqual(left_i + right_i, 1,
                         "经过交付路径之后，跨接缝的 I 必须只剩一个——"
                         "两个就是成品里的「我我」，零个就是丢词")


# ── 真实数据（任务 80703166ff「30 Minute American English」，第二支长视频）────
#
# ⑩ 端到端在这支素材上抓到的缺陷：5 个接缝有 3 个删掉、**2 个漏网**，
# 漏的两个恰好都是**句末词**——用户最容易看见的位置
# （`All right, nice. nice. Yeah` / `I love funny friends, friends, but funny`）。
#
# 上面 1a1b9f8145 的三组重复词（`I` / `hoppyer` / `something`）
# 全部落在句子中间，**一个都没挂标点**，所以整套测试全绿。
# 挂标点是常态而不是例外，这批数据里连 `right` 都变成了 `right,`。

SEAM_600_PUNCT_LEFT_RAW = [
    ("best", 598.63, 598.87),
    ("friend", 598.87, 599.11),
    ("All", 599.51, 599.67),
    ("right", 599.67, 599.91),
    ("nice", 599.91, 600.31),        # ← 跨界，正式词流里会变成 `nice.`
]
SEAM_600_PUNCT_LEFT_TEXT = "best friend. All right, nice."

SEAM_600_PUNCT_RIGHT_RAW = [
    ("All", 599.56, 599.72),
    ("right", 599.72, 599.88),
    ("nice", 599.96, 600.36),        # ← 跨界，与左块是同一个词
    ("Yeah", 600.52, 600.76),
    ("I", 600.76, 600.84),
]
SEAM_600_PUNCT_RIGHT_TEXT = "All right, nice. Yeah, I"


def _chunk_raw(t_start: float, t_end: float, words: list, raw_text: str) -> dict:
    """只给原始材料，`segments` 交给生产代码自己挂标点后生成。

    **测试必须走这条路**：`_chunk()` 直接照抄 raw 的文字当 segments，
    等于假设「挂标点不改写文字」——而那正是这个 bug 的病因。
    """
    return {"t_start": t_start, "t_end": t_end, "raw_text": raw_text,
            "raw_segments": [_seg(t, a, b, t_start) for (t, a, b) in words]}


class TestSeamMergeSurvivesPunctuationRewrite(unittest.TestCase):
    """T19 / C6：挂标点改写了正式词流的文字，接缝合并不能因此瞎掉。

    **病的形状**：`raw_segments` 里是 `nice`，正式 `segments` 里是 `nice.`。
    C6 那道闸拿 raw 的文字去正式词流里找**完全相等**的词，找不到，于是放行不删。
    结果是**只有挂了标点的重复词漏网**——也就是句末的词。

    同一件事在代码里存了两份（原始文字 / 挂过标点的文字），其中一份被改写了，
    比对的那一处忘了跟着改——问题库里反复出现的同一个形状。
    """

    def test_t19_sentence_final_duplicate_is_removed(self):
        from backend.core.qwen_asr_runner import _refresh_chunk_delivery

        left = _chunk_raw(300.0, 600.0, SEAM_600_PUNCT_LEFT_RAW,
                          SEAM_600_PUNCT_LEFT_TEXT)
        right = _chunk_raw(600.0, 900.0, SEAM_600_PUNCT_RIGHT_RAW,
                           SEAM_600_PUNCT_RIGHT_TEXT)

        refreshed = _refresh_chunk_delivery([left, right], "en")

        merged = _texts(refreshed[0]) + _texts(refreshed[1])
        self.assertEqual(
            sum(1 for t in merged if t.strip(" ,.!?") == "nice"), 1,
            f"跨接缝的 nice 必须只剩一个，实际拼接结果：{' '.join(merged)}")

    def test_t19_punctuation_is_actually_attached_in_this_fixture(self):
        """守住这条测试自己的前提——素材必须真的挂上了标点。

        挂标点一旦失效，上面那条会因为「文字恰好相同」而变成假绿。
        """
        from backend.core.qwen_asr_runner import _refresh_chunk_delivery

        left = _chunk_raw(300.0, 600.0, SEAM_600_PUNCT_LEFT_RAW,
                          SEAM_600_PUNCT_LEFT_TEXT)
        refreshed = _refresh_chunk_delivery([left], "en")
        self.assertIn("nice.", _texts(refreshed[0]),
                      "本组素材的正式词流必须是挂过标点的 `nice.`，否则这条测试测了个寂寞")
