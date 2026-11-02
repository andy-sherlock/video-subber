"""G12 第二批段三：保全零时长词证据 + 词级游程判据。

**零时长词是什么**：模型输出每个词时给一个开始和结束时间。两个时间填了同一个数
（比如都是 7.28 秒），说明**模型自己也不知道这个词是什么时候说的**。

**为什么要保全它们**：长视频路径在进过滤器**之前**就把它们全扔了
（`_clip_chunk_segments(drop_zero_duration=True)`），短视频路径则全部保留
——同一件事两套行为。两个真实任务实测 655 个零时长词：

    成串的（幻觉）   34 个（5%）
    散落的（正常词） 621 个（95%），含 airport／bridge／climbing／Excellent
                     等 42 个长度 >4 的实词

**扔掉它们等于两件事一起做错**：长视频永久少字（P430 的原始主诉），
同时把幻觉证据一并销毁——过滤器只看到 2 个孤立的 `Oh`，
看不出那是一次连续 20 次的幻觉。

Run: cd v3 && .venv/bin/python -m pytest test/test_g12b_evidence_preservation.py -v
"""
# P464：`run_qwen_asr` 现在返回 QwenAsrOutcome（块 + 完整性状态），
# 不再是裸列表。stub 必须跟上真模块的契约，否则测试就在撒谎。
def _Outcome(**kw):
    """惰性构造真的 QwenAsrOutcome。

    **不能在顶层 `from backend.core.qwen_asr_runner import ...`**：
    多个测试用 `sys.modules` 把该模块换成假模块；本文件若在那期间被导入
    （例如被别的测试 `from test.xxx import _stub_environment` 拉进来），
    顶层 import 会拿到假模块并炸在 `(unknown location)`。
    `transcriber_v2` 顶层持有真类的引用，不受 sys.modules 替换影响。
    """
    from backend.transcriber_v2 import QwenAsrOutcome
    return QwenAsrOutcome(**kw)
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest


class FakeSegment:
    def __init__(self, id=0, start=0.0, end=1.0, text="hello",
                 sensevoice_tag=None, qwen_text=None, words=None):
        self.id = id
        self.start = start
        self.end = end
        self.text = text
        self.speaker_change = False
        self.uncertain = False
        self.zh = ""
        self.words = words or []
        self.sensevoice_tag = sensevoice_tag
        self.qwen_text = qwen_text
        self.filtered = False
        self.filter_reason = None


def _seg(id, start, end, text):
    return FakeSegment(id=id, start=start, end=end, text=text)


def _w(text, start, end):
    return {"text": text, "start": start, "end": end}


# ══════════════════════════════════════════════════════════════════
# C1 · `drop_zero_duration` 开关必须整个消失
# ══════════════════════════════════════════════════════════════════

class TestC1SwitchGone(unittest.TestCase):

    def test_clip_chunk_segments_has_no_drop_switch(self):
        """一个参数分叉两种行为，两个消费者各走一边——P430 的原始形状。

        没有人验证过「L1 为什么必须扔零时长词」。对照实验现在做完了：
        只改 L1 的对齐证据 → **0 条改判**。开关整个不需要。
        """
        import inspect
        from backend.core import qwen_asr_runner

        sig = inspect.signature(qwen_asr_runner._clip_chunk_segments)
        self.assertNotIn("drop_zero_duration", sig.parameters)

    def test_chunk_words_has_no_drop_switch(self):
        import inspect
        from backend.core import qwen_asr_runner

        sig = inspect.signature(qwen_asr_runner._chunk_words)
        self.assertNotIn("drop_zero_duration", sig.parameters)

    def test_zero_duration_word_is_kept_by_clipping(self):
        from backend.core import qwen_asr_runner

        segments = [
            {"text": "airport", "start": 1.0, "end": 1.4},
            {"text": "bridge", "start": 2.0, "end": 2.0},   # 零时长实词
        ]
        kept = qwen_asr_runner._clip_chunk_segments(
            segments, extract_start=0.0, t_start=0.0, t_end=10.0)

        self.assertEqual([s["text"] for s in kept], ["airport", "bridge"],
                         "零时长实词被丢弃；长视频因此比短视频少字")


# ══════════════════════════════════════════════════════════════════
# C2 · 零时长词必须能给所在段提供对齐证据
# ══════════════════════════════════════════════════════════════════

class TestC2AlignmentBoundary(unittest.TestCase):
    """`_segment_has_alignment` 用严格不等号 `w.start < seg.end and w.end > seg.start`。

    **零宽区间里放不进任何东西**——由零时长词组成的段（`start == end`）
    因此被判成「没有对齐证据」，而那个段**就是由那些词组成的**。这是事实错误。

    后果：L1 决策矩阵里两条本来不可达的分支变得可达，
    实测会命中 `'We apologize for that'`（真实空乘广播）、`'ten'`、`'to'`。
    """

    def test_zero_duration_word_inside_a_zero_width_segment_counts(self):
        from backend.core.hallucination_filter import _segment_has_alignment

        seg = _seg(0, 7.28, 7.28, "oh oh oh")
        alignment = [_w("oh", 7.28, 7.28)]

        self.assertTrue(_segment_has_alignment(seg, alignment),
                        "段就是由这些词组成的，说它没有对齐证据是事实错误")

    def test_zero_duration_words_at_segment_boundaries_count(self):
        """`'We apologize for that'` 的形状：段有宽度，但词全在端点上。"""
        from backend.core.hallucination_filter import _segment_has_alignment

        seg = _seg(0, 81.97, 82.05, "We apologize for that")
        alignment = [_w("We", 81.97, 81.97), _w("that", 82.05, 82.05)]

        self.assertTrue(_segment_has_alignment(seg, alignment))

    def test_normal_words_still_use_strict_overlap(self):
        """正常词不许放宽——只碰到端点不算重叠，否则相邻段会互相「借」证据。"""
        from backend.core.hallucination_filter import _segment_has_alignment

        seg = _seg(0, 10.0, 12.0, "hello")
        touching_only = [_w("neighbour", 12.0, 13.0)]

        self.assertFalse(_segment_has_alignment(seg, touching_only))

    def test_far_away_zero_duration_word_does_not_count(self):
        from backend.core.hallucination_filter import _segment_has_alignment

        seg = _seg(0, 10.0, 12.0, "hello")
        far = [_w("elsewhere", 900.0, 900.0)]

        self.assertFalse(_segment_has_alignment(seg, far))


# ══════════════════════════════════════════════════════════════════
# C3 · 词级游程扫描：≥3 个同词落在同一时间点 → 整条游程删除
# ══════════════════════════════════════════════════════════════════

class TestC3ImpossibleInstantRun(unittest.TestCase):
    """**判据里没有任何阈值。**

    「连续 ≥3 个同词落在同一个时间点上」和 `dur <= 0` 是同一个性质：
    **物理上不可能**。一个瞬间放不下九个词，放不下两个，一个都放不下。

    从这个不可能的核心向两侧扩展到相邻的同词 token，整条游程删除
    ——那两个时间正常的长 `Oh` 就是靠扩展带走的。

    **两个真实任务 8 处同词游程的实测**（`norm` 后比较，忽略大小写与尾标点）：

    | 游程 | 同一瞬间最长连号 | 判定 |
    |---|---|---|
    | `oh` ×20 | **9** | 删（幻觉） |
    | `well`／`really`／`mmm`／`i`／`you`／`it's`／`the` ×3 | **1** | 留（真话） |

    **9 对 1。中间没有可调的余地。**
    """

    # 真实任务 1a1b9f8145 的前 20 个词，逐字取自 qwen_raw.json
    REAL_OH_RUN = (
        [_w("Oh", 1.44, 4.24)]
        + [_w("oh", 7.28, 7.28)] * 9
        + [_w("Oh", 13.886, 21.806)]
        + [_w("oh", 21.806, 21.806)] * 6
        + [_w("oh", 22.366, 22.366)] * 3
    )

    def test_real_hallucination_run_is_fully_removed(self):
        from backend.core.hallucination_filter import find_impossible_instant_runs

        words = list(self.REAL_OH_RUN) + [
            _w("What's", 31.61, 31.9), _w("up", 31.9, 32.1),
        ]
        removed = find_impossible_instant_runs(words)

        self.assertEqual(removed, set(range(20)),
                         "整条 20 词游程必须一起走，包括两个时间正常的长 Oh")

    def test_both_long_oh_are_carried_out_by_extension(self):
        """用户报的就是这两条：1.44–4.24 与 13.886–21.806。"""
        from backend.core.hallucination_filter import find_impossible_instant_runs

        removed = find_impossible_instant_runs(list(self.REAL_OH_RUN))

        self.assertIn(0, removed, "第一条长 Oh（1.44–4.24）没被带走")
        self.assertIn(10, removed, "第二条长 Oh（13.886–21.806）没被带走")

    def test_real_speech_runs_are_untouched(self):
        """真实任务里另外 7 处同词游程，同一瞬间最长连号都是 1。"""
        from backend.core.hallucination_filter import find_impossible_instant_runs

        for label, run in (
            ("well", [_w("Well", 712.86, 713.1), _w("well", 713.2, 713.5),
                      _w("Well", 713.6, 713.9)]),
            # `I` ×3 含一个零时长词——**一个不够，必须 ≥3 个同瞬间**
            ("i", [_w("I", 2779.43, 2779.6), _w("I", 2779.6, 2779.6),
                   _w("I", 2779.7, 2780.23)]),
            ("it's", [_w("it's", 1178.61, 1178.8), _w("it's", 1178.9, 1178.9),
                      _w("It's", 1179.0, 1179.3)]),
        ):
            with self.subTest(run=label):
                self.assertEqual(find_impossible_instant_runs(run), set(),
                                 f"{label} 是真人说的话，不许删")

    def test_two_words_at_the_same_instant_are_not_enough(self):
        """两个不够。判据是 ≥3，和白名单其余各条一样，划在物理不可能那条线上。"""
        from backend.core.hallucination_filter import find_impossible_instant_runs

        words = [_w("ha", 5.0, 5.0), _w("ha", 5.0, 5.0), _w("ha", 5.2, 5.5)]

        self.assertEqual(find_impossible_instant_runs(words), set())

    def test_different_words_at_the_same_instant_are_not_a_run(self):
        """同一瞬间但不是同一个词——不成游程，不删。"""
        from backend.core.hallucination_filter import find_impossible_instant_runs

        words = [_w("a", 5.0, 5.0), _w("b", 5.0, 5.0), _w("c", 5.0, 5.0)]

        self.assertEqual(find_impossible_instant_runs(words), set())

    def test_case_and_trailing_punctuation_are_ignored_when_grouping(self):
        from backend.core.hallucination_filter import find_impossible_instant_runs

        words = [_w("Oh,", 5.0, 5.0), _w("oh", 5.0, 5.0), _w("OH.", 5.0, 5.0)]

        self.assertEqual(find_impossible_instant_runs(words), {0, 1, 2})

    # ── 走查 Q6「分成几份了吗」的三问 ──────────────────────────

    def test_each_run_is_judged_on_its_own(self):
        """① 单独判断了吗——一条游程命中，不得牵连另一条无关游程。"""
        from backend.core.hallucination_filter import find_impossible_instant_runs

        words = (
            [_w("oh", 5.0, 5.0)] * 3
            + [_w("hello", 10.0, 10.4)]
            + [_w("yes", 20.0, 20.3), _w("yes", 20.4, 20.7), _w("yes", 20.8, 21.1)]
        )
        self.assertEqual(find_impossible_instant_runs(words), {0, 1, 2})

    def test_two_separate_bad_runs_are_both_removed(self):
        """② 单独记账了吗——两条都命中时，两条都要走，不能只记住最后一条。"""
        from backend.core.hallucination_filter import find_impossible_instant_runs

        words = (
            [_w("oh", 5.0, 5.0)] * 3
            + [_w("hello", 10.0, 10.4)]
            + [_w("um", 30.0, 30.0)] * 4
        )
        self.assertEqual(find_impossible_instant_runs(words), {0, 1, 2, 4, 5, 6, 7})

    def test_malformed_word_does_not_abort_the_whole_scan(self):
        """③ 一份出事，其他份怎么办——只跳过坏的那个，其余照常裁定。

        P404 最狠的那处形状：一次无关调用断连，真正待处理的那一半
        一次机会都没得到就被跳过。**不许整体放弃。**
        """
        from backend.core.hallucination_filter import find_impossible_instant_runs

        words = [
            {"text": "broken"},                      # 没有时间字段
            _w("oh", 5.0, 5.0), _w("oh", 5.0, 5.0), _w("oh", 5.0, 5.0),
        ]
        self.assertEqual(find_impossible_instant_runs(words), {1, 2, 3})

    def test_empty_and_short_input_is_safe(self):
        from backend.core.hallucination_filter import find_impossible_instant_runs

        self.assertEqual(find_impossible_instant_runs([]), set())
        self.assertEqual(find_impossible_instant_runs([_w("a", 0.0, 1.0)]), set())


# ══════════════════════════════════════════════════════════════════
# C5 · 零时长词不得在组装时脱离原位（真跑抓到的新病）
# ══════════════════════════════════════════════════════════════════

class TestC5ZeroWidthKeepsItsPlace(unittest.TestCase):
    """`_assemble_qwen_words_on_whisper_boundaries` 用重叠量 `best_ov > 0` 判归属，
    而**零宽区间的重叠量恒为 0**——零时长词因此全部落进 `unmatched`，
    被 `_pause_segment` 组成孤立段追加到末尾，再按 start 排序。

    真跑实测（任务 `1a1b9f8145`）：模型原始顺序是
    `This(33.21-33.37) is(33.37-33.37) our(33.37-33.53)`，
    交付出来变成 **`This our last stop in New York. is`**——`is` 跑到了句尾。

    ⚠️ **这和 `_segment_has_alignment` 是同一个 bug 形状**：零宽区间的重叠判定
    在代码里有两份，段三只修了一份。**这正是本项目反复出现的形状。**
    """

    def test_zero_duration_word_stays_between_its_neighbours(self):
        import backend.transcriber_v2 as tv2

        words = [
            _w("This", 33.21, 33.37),
            _w("is", 33.37, 33.37),      # 零时长
            _w("our", 33.37, 33.53),
            _w("last", 33.53, 33.85),
        ]
        whisper = [tv2.Segment(0, 33.0, 34.0, "This is our last")]

        segments = tv2._assemble_qwen_words_on_whisper_boundaries(
            words, whisper, "en")

        joined = " ".join(s.text for s in segments)
        self.assertEqual(joined, "This is our last",
                         f"零时长词脱离了原位：{joined!r}")

    def test_zero_duration_word_is_not_split_into_its_own_segment(self):
        import backend.transcriber_v2 as tv2

        words = [
            _w("This", 33.21, 33.37),
            _w("is", 33.37, 33.37),
            _w("our", 33.37, 33.53),
        ]
        whisper = [tv2.Segment(0, 33.0, 34.0, "This is our")]

        segments = tv2._assemble_qwen_words_on_whisper_boundaries(
            words, whisper, "en")

        self.assertEqual(len(segments), 1,
                         "零时长词被拆成了独立段；它应当归属所在的 Whisper 段")
        self.assertEqual([w["text"] for w in segments[0].words],
                         ["This", "is", "our"])

    def test_normal_word_outside_every_whisper_span_still_goes_unmatched(self):
        """反向守卫：真正落在所有 Whisper 段之外的词仍走 unmatched 路径。

        没有这一条，一个「把所有词都强行塞进第一个段」的实现也能让上面两条变绿。
        """
        import backend.transcriber_v2 as tv2

        words = [_w("hello", 33.2, 33.5), _w("faraway", 900.0, 900.4)]
        whisper = [tv2.Segment(0, 33.0, 34.0, "hello")]

        segments = tv2._assemble_qwen_words_on_whisper_boundaries(
            words, whisper, "en")

        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[1].text, "faraway")


# ══════════════════════════════════════════════════════════════════
# C4 · 接线：判据必须真的被流水线调用
# ══════════════════════════════════════════════════════════════════
#
# 没有这一条，`find_impossible_instant_runs` 可以是一段**死代码**，
# 而上面 17 条测试照样全绿。第一批的教训：写测试和写代码是同一个脑子，
# 「以为接上了」和「真的接上了」在单元测试里长得一模一样。

def test_pipeline_removes_the_run_from_the_delivery_stream(monkeypatch, tmp_path):
    """跑真正的 `transcribe()`，断言收据里没有那 20 个 `oh`。

    输入取自真实任务 `1a1b9f8145` 的前 20 个词（逐字取自 `qwen_raw.json`），
    后面接一句真话 `What's up Turtle Gang`（31.61 秒，视频里真人的第一句）。
    """
    import types
    import backend.transcriber_v2 as tv2
    from backend.core.qwen_asr_runner import (
        count_chunks_missing_punctuation as _real_count,
    )
    from backend.core.whisper_runner import LanguageDecision

    words = (
        [_w("Oh", 1.44, 4.24)]
        + [_w("oh", 7.28, 7.28)] * 9
        + [_w("Oh", 13.886, 21.806)]
        + [_w("oh", 21.806, 21.806)] * 6
        + [_w("oh", 22.366, 22.366)] * 3
        + [_w("What's", 31.61, 31.9), _w("up", 31.9, 32.1),
           _w("Turtle", 32.1, 32.4), _w("Gang", 32.4, 32.73)]
    )

    fake_hw = types.ModuleType("backend.core.hardware_detector")
    fake_hw.detect_hardware = lambda: {"ram_gb": 32}
    monkeypatch.setitem(sys.modules, "backend.core.hardware_detector", fake_hw)

    fake_qwen = types.ModuleType("backend.core.qwen_asr_runner")
    fake_qwen.count_chunks_missing_punctuation = _real_count
    fake_qwen.is_qwen_asr_available = lambda: True
    fake_qwen.run_qwen_asr = lambda *_a, **_k: _Outcome(complete=True, chunks=[{
        "text": " ".join(w["text"] for w in words),
        "t_start": 0.0, "t_end": 33.0,
        "segments": [dict(w) for w in words],
    }])
    monkeypatch.setitem(sys.modules, "backend.core.qwen_asr_runner", fake_qwen)

    fake_sv = types.ModuleType("backend.core.sensevoice_runner")
    fake_sv.is_sensevoice_available = lambda: False
    fake_sv.run_sensevoice = lambda *_a, **_k: []
    monkeypatch.setitem(sys.modules, "backend.core.sensevoice_runner", fake_sv)

    monkeypatch.setattr(tv2, "_vad_speech_gate", lambda *_a, **_k: None)
    monkeypatch.setattr(tv2, "release_whisper_model", lambda *_a, **_k: None)
    monkeypatch.setattr(tv2, "should_parallel_asr", lambda: True)
    monkeypatch.setattr(tv2, "_detect_speaker_changes", lambda _p, segs: segs)
    monkeypatch.setattr(
        tv2, "sample_language_multipoint",
        lambda *_a, **_k: LanguageDecision(
            language="en", locked=True, split=False,
            expected_count=7, valid_count=7))

    class _Info:
        language = "en"
        language_probability = 0.99
        duration = 60.0

    monkeypatch.setattr(tv2, "transcribe_whisper", lambda *_a, **_k: ([], _Info()))
    monkeypatch.setattr(
        tv2, "_whisper_output_to_segments",
        lambda _o, _r, _i, n, _cb: n)

    receipt = tv2.transcribe(
        str(tmp_path / "g12b.wav"), model_size="large-v2", skip_vad=True,
        filter_keywords=True, speaker_detect=False,
        transcription_tier="precise", cache_dir=str(tmp_path / "cache")).receipt

    delivered = [w["text"] for w in receipt.source_words]

    assert not [t for t in delivered if t.strip().lower().strip(".,") == "oh"], (
        f"那 20 个 oh 仍在正式交付词流里：{delivered[:8]}"
        "——判据没有被流水线调用，或者调用点在组装之后")
    assert delivered == ["What's", "up", "Turtle", "Gang"], (
        f"真话被误伤或顺序被打乱：{delivered}")


def test_pipeline_keeps_scattered_zero_duration_words(monkeypatch, tmp_path):
    """反向守卫：散落的零时长真词必须留在交付流里。

    没有这一条，一个「把所有零时长词一律摘掉」的实现也能让上面那条变绿
    ——而那正是段三要修的原缺陷（长视频永久少 621 个真词）。
    """
    import types
    import backend.transcriber_v2 as tv2
    from backend.core.qwen_asr_runner import (
        count_chunks_missing_punctuation as _real_count,
    )
    from backend.core.whisper_runner import LanguageDecision

    # 取自真实任务的实词，其中 bridge / climbing 时间戳是坏的
    words = [
        _w("We", 10.0, 10.2), _w("crossed", 10.2, 10.6),
        _w("the", 10.6, 10.7), _w("bridge", 10.7, 10.7),
        _w("and", 10.9, 11.0), _w("started", 11.0, 11.4),
        _w("climbing", 11.4, 11.4), _w("up", 11.6, 11.8),
    ]

    fake_hw = types.ModuleType("backend.core.hardware_detector")
    fake_hw.detect_hardware = lambda: {"ram_gb": 32}
    monkeypatch.setitem(sys.modules, "backend.core.hardware_detector", fake_hw)

    fake_qwen = types.ModuleType("backend.core.qwen_asr_runner")
    fake_qwen.count_chunks_missing_punctuation = _real_count
    fake_qwen.is_qwen_asr_available = lambda: True
    fake_qwen.run_qwen_asr = lambda *_a, **_k: _Outcome(complete=True, chunks=[{
        "text": " ".join(w["text"] for w in words),
        "t_start": 0.0, "t_end": 12.0,
        "segments": [dict(w) for w in words],
    }])
    monkeypatch.setitem(sys.modules, "backend.core.qwen_asr_runner", fake_qwen)

    fake_sv = types.ModuleType("backend.core.sensevoice_runner")
    fake_sv.is_sensevoice_available = lambda: False
    fake_sv.run_sensevoice = lambda *_a, **_k: []
    monkeypatch.setitem(sys.modules, "backend.core.sensevoice_runner", fake_sv)

    monkeypatch.setattr(tv2, "_vad_speech_gate", lambda *_a, **_k: None)
    monkeypatch.setattr(tv2, "release_whisper_model", lambda *_a, **_k: None)
    monkeypatch.setattr(tv2, "should_parallel_asr", lambda: True)
    monkeypatch.setattr(tv2, "_detect_speaker_changes", lambda _p, segs: segs)
    monkeypatch.setattr(
        tv2, "sample_language_multipoint",
        lambda *_a, **_k: LanguageDecision(
            language="en", locked=True, split=False,
            expected_count=7, valid_count=7))

    class _Info:
        language = "en"
        language_probability = 0.99
        duration = 60.0

    monkeypatch.setattr(tv2, "transcribe_whisper", lambda *_a, **_k: ([], _Info()))
    monkeypatch.setattr(
        tv2, "_whisper_output_to_segments",
        lambda _o, _r, _i, n, _cb: n)

    receipt = tv2.transcribe(
        str(tmp_path / "g12b.wav"), model_size="large-v2", skip_vad=True,
        filter_keywords=True, speaker_detect=False,
        transcription_tier="precise", cache_dir=str(tmp_path / "cache")).receipt

    delivered = [w["text"] for w in receipt.source_words]

    assert "bridge" in delivered, f"零时长实词 bridge 丢了：{delivered}"
    assert "climbing" in delivered, f"零时长实词 climbing 丢了：{delivered}"


# ══════════════════════════════════════════════════════════════════
# C5（⑦.5 diff 审查发现）· 摘词不得把交付词流摘成空
# ══════════════════════════════════════════════════════════════════

def test_scan_never_empties_the_word_stream():
    """整条都要摘时必须保留原样——否则会**静默改写主引擎路由**。

    `_finalize_routing_after_alignment:1460` 的早退条件是

        if route.actual_primary != "qwen" or route.source_words:
            return route

    它靠 `source_words` **非空**来早退，而词级摘除跑在它**之前**
    （`transcribe()` 里摘除在 `:2129`，该函数在 `:2154`）。

    整片坍缩的输入会被摘成空列表 → 早退失效 → 函数继续往下走，
    **把 `actual_primary` 静默改成 whisper 或 dual_failed**。
    P148 记过这种形状：任务 `97c57255ec` 整片重复
    `1056234897 нет ты меня прости`。

    **幻觉过滤器不该有权改路由决策。** 整条都要摘时保留原样并告警，
    交给既有的坍缩检测（`_qwen_is_reliable`）与下游闸门处理。

    这条是 ⑦.5 diff 审查读代码读出来的——**没有任何测试会红，
    真跑也碰不到**（要一整片视频全是同一个词落在同一瞬间）。
    """
    import backend.transcriber_v2 as tv2

    collapsed = [_w("oh", 5.0, 5.0) for _ in range(6)]
    kept = tv2._drop_impossible_instant_runs(collapsed)

    assert kept == collapsed, "整片坍缩被摘空，主引擎路由会被静默改写"


def test_scan_still_removes_when_something_survives():
    """反向守卫：只要有幸存者，该摘的照摘。

    没有这一条，一个「永远不摘」的实现也能让上面那条变绿。
    """
    import backend.transcriber_v2 as tv2

    words = [_w("oh", 5.0, 5.0) for _ in range(4)] + [_w("hello", 10.0, 10.4)]
    kept = tv2._drop_impossible_instant_runs(words)

    assert [w["text"] for w in kept] == ["hello"]


def test_scan_returns_input_unchanged_when_nothing_impossible():
    import backend.transcriber_v2 as tv2

    words = [_w("hello", 0.0, 0.4), _w("world", 0.4, 0.9)]

    assert tv2._drop_impossible_instant_runs(words) == words


if __name__ == "__main__":
    unittest.main()
