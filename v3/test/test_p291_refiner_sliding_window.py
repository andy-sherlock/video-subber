"""TDD tests for P291: Refiner sliding-window smoothing.

Pins the agreed P291 scope:
- Refiner no longer sends all segments in one LLM call. It chunks into
  non-overlapping active windows, each padded with read-only context on
  both sides (RC1).
- The broken single-mode candidate filters are removed (RC2).
- Smoothing (Pass 2) runs in ALL modes; dual mode runs conflict resolution
  (Pass 1) first, then smoothing (RC3).
- Failed windows must NOT clear uncertain (no fake "verified" data).
- Corrections referencing read-only context seg_ids are rejected.
- "改了就清、没改保留": a smoothed segment clears [?]; an untouched one keeps it.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from types import SimpleNamespace


class DummySeg:
    def __init__(self, text, start=0.0, end=1.0, id=0):
        self.id = id
        self.text = text
        self.start = start
        self.end = end
        self.l2_status = None
        self.filtered = False
        self.filter_reason = None
        self.uncertain = False
        self.sensevoice_tag = None
        self.bgm_override = False
        self.whisper_text = ""


class RecordingLLM:
    """Fake LLM backend recording every translate_raw_json call.

    response_fn(call_index, prompt, system_prompt) -> raw JSON str.
    Default returns empty corrections. raise_on = set of call indices to raise.
    """
    def __init__(self, response_fn=None, raise_on=None):
        self.name = "fake-llm"
        self.calls = []
        self._response_fn = response_fn
        self._raise_on = set(raise_on or [])

    def translate_raw_json(self, prompt, system_prompt=None):
        idx = len(self.calls)
        self.calls.append((prompt, system_prompt))
        if idx in self._raise_on:
            # D-1（合同变更）：断网由付费请求函数原地等，不再算窗口失败；
            # 窗口失败只剩回复格式不对（解析失败）这一种。
            return "not json: simulated window failure"
        if self._response_fn:
            return self._response_fn(idx, prompt, system_prompt)
        return '{"corrections": []}'


def _jp_segs(n, start_id=0):
    """n Japanese subtitle segments with NO punctuation (Qwen-style)."""
    return [DummySeg(f"父さんとお母さん今{i}", start=float(i), end=float(i) + 0.5,
                     id=start_id + i) for i in range(n)]


def _dual_segs(n):
    """n segments；配合 `_dual_receipt()` 走 dual 模式。

    G2/B1+B10：模式原来靠给每段挂 `whisper_text`（按时间重叠拼出来的参照）
    来触发，那套拼接已删。现在模式只看转录阶段填好的收据，与段上的字段无关。
    """
    return [
        DummySeg(f"テキスト{i}", start=float(i) * 0.5, end=float(i) * 0.5 + 0.4, id=i)
        for i in range(n)
    ]


def _dual_receipt():
    """一份「副引擎可用」的收据——dual 模式的唯一开关。"""
    return SimpleNamespace(
        reference_text=" ".join(f"てきすと{i}" for i in range(10)),
        reference_usable=True,
    )


# ── RC1: chunking ──────────────────────────────────────────────────────────

class TestChunkRefinerWindows(unittest.TestCase):
    def test_non_overlapping_active_with_context_pad(self):
        from backend.worker import _chunk_refiner_windows

        segs = [DummySeg(f"s{i}", id=i) for i in range(250)]
        windows = _chunk_refiner_windows(segs, window_size=100, context_pad=20)

        self.assertEqual(len(windows), 3)
        self.assertEqual([w.active[0].id for w in windows], [0, 100, 200])
        self.assertEqual([w.active[-1].id for w in windows], [99, 199, 249])

        # active regions never overlap and cover every segment exactly once
        active_ids = [s.id for w in windows for s in w.active]
        self.assertEqual(len(active_ids), 250)
        self.assertEqual(len(set(active_ids)), 250)

        # context pads
        self.assertEqual([s.id for s in windows[0].context_before], [])
        self.assertEqual([s.id for s in windows[0].context_after], list(range(100, 120)))
        self.assertEqual([s.id for s in windows[1].context_before], list(range(80, 100)))
        self.assertEqual([s.id for s in windows[-1].context_after], [])

    def test_short_input_is_single_window(self):
        from backend.worker import _chunk_refiner_windows

        segs = [DummySeg(f"s{i}", id=i) for i in range(50)]
        windows = _chunk_refiner_windows(segs, window_size=120, context_pad=20)

        self.assertEqual(len(windows), 1)
        self.assertEqual(len(windows[0].active), 50)
        self.assertEqual(windows[0].context_before, [])
        self.assertEqual(windows[0].context_after, [])


# ── RC2: broken filters removed ──────────────────────────────────────────────

class TestBrokenFiltersRemoved(unittest.TestCase):
    def test_looks_broken_sentence_is_gone(self):
        import backend.worker as worker
        self.assertFalse(hasattr(worker, "_looks_broken_sentence"))

    def test_looks_too_short_fragment_is_gone(self):
        import backend.worker as worker
        self.assertFalse(hasattr(worker, "_looks_too_short_fragment"))


# ── prompt: context partition ────────────────────────────────────────────────

class TestPromptContextPartition(unittest.TestCase):
    def test_context_segments_marked_read_only_not_correctable(self):
        from backend.worker import _build_refiner_prompt

        active = [DummySeg("これは本文です", id=5)]
        ctx_before = [DummySeg("前の文脈", id=4)]
        ctx_after = [DummySeg("次の文脈", id=6)]

        user, _system = _build_refiner_prompt(
            active, "single", context_before=ctx_before, context_after=ctx_after,
        )

        # active segment rendered as a correctable "段落 N:" line
        self.assertIn("段落 5:", user)
        # context segments present for the LLM to read...
        self.assertIn("前の文脈", user)
        self.assertIn("次の文脈", user)
        # ...but NOT as correctable "段落 N:" lines
        self.assertNotIn("段落 4:", user)
        self.assertNotIn("段落 6:", user)


# ── RC1 core regression: long input is windowed, not one shot ────────────────

class TestLongInputIsWindowed(unittest.TestCase):
    def test_300_japanese_segments_trigger_multiple_llm_calls(self):
        from backend.worker import _run_refiner

        segs = _jp_segs(300)  # no whisper_text -> single mode
        llm = RecordingLLM()

        _run_refiner(segs, llm, window_size=120, context_pad=20)

        # 300 / 120 -> 3 windows; single mode = smoothing pass only = 3 calls.
        # Before P291 this was exactly 1 call (and returned corrections=0).
        self.assertGreaterEqual(len(llm.calls), 3)
        self.assertGreater(len(llm.calls), 1)


# ── pass structure ──────────────────────────────────────────────────────────

class TestPassStructure(unittest.TestCase):
    def test_single_mode_runs_one_smoothing_pass(self):
        from backend.worker import _run_refiner

        segs = _jp_segs(3)  # no whisper_text -> single
        llm = RecordingLLM()

        result = _run_refiner(segs, llm, window_size=120, context_pad=20)

        self.assertEqual(result["mode"], "single")
        self.assertEqual(len(llm.calls), 1)
        # smoothing prompt is the proofreader prompt
        self.assertIn("校对", llm.calls[0][1])

    def test_dual_mode_runs_conflict_then_smoothing(self):
        from backend.worker import _run_refiner

        segs = _dual_segs(3)
        llm = RecordingLLM()

        result = _run_refiner(segs, llm, receipt=_dual_receipt(),
                              window_size=120, context_pad=20)

        self.assertEqual(result["mode"], "dual")
        self.assertEqual(len(llm.calls), 2)
        # Pass 1 带副引擎参照（B 块），Pass 2 只有 A 块。
        # G2/B3+B5：旧断言认的是「终审裁判 / 校对」两套系统提示词，
        # 那两套已经并成一套（`_REFINER_COMMON_RULES`），改认 B 块在不在。
        self.assertIn("B 块", llm.calls[0][0])
        self.assertNotIn("B 块", llm.calls[1][0])
        self.assertIn("校对", llm.calls[1][1])


# ── failed window must not fabricate "verified" data ────────────────────────

class TestFailedWindowKeepsUncertain(unittest.TestCase):
    def test_failed_dual_window_keeps_uncertain_others_cleared(self):
        from backend.worker import _run_refiner

        segs = _dual_segs(5)  # dual mode
        for s in segs:
            s.uncertain = True

        # window_size=2 -> active windows [0,1] [2,3] [4]; 3 Pass-1 calls.
        # raise on the 2nd Pass-1 call (index 1) -> window [2,3] not judged.
        llm = RecordingLLM(raise_on={1})

        _run_refiner(segs, llm, receipt=_dual_receipt(),
                     window_size=2, context_pad=1)

        by_id = {s.id: s for s in segs}
        # failed window: uncertain preserved (never judged)
        self.assertTrue(by_id[2].uncertain)
        self.assertTrue(by_id[3].uncertain)
        # successful windows: dual judge passed -> uncertain cleared
        self.assertFalse(by_id[0].uncertain)
        self.assertFalse(by_id[1].uncertain)
        self.assertFalse(by_id[4].uncertain)


# ── context seg_id correction must be rejected ──────────────────────────────

class TestContextSegIdRejected(unittest.TestCase):
    def test_correction_for_context_seg_id_is_not_applied(self):
        from backend.worker import _run_refiner

        segs = _jp_segs(4)  # single mode; ids 0..3
        originals = {s.id: s.text for s in segs}

        def respond(idx, prompt, system_prompt):
            # On the FIRST window (active 0,1; context_after 2,3) try to edit
            # seg_id 2, which is read-only context here. Also a small valid
            # punctuation edit on active seg 0 (close enough to pass 0.80 valve).
            if idx == 0:
                return ('{"corrections": ['
                        '{"seg_id": 0, "fixed_text": "父さんとお母さん、今0。"},'
                        '{"seg_id": 2, "fixed_text": "父さんとお母さん、今2。"}]}')
            return '{"corrections": []}'

        llm = RecordingLLM(response_fn=respond)
        _run_refiner(segs, llm, window_size=2, context_pad=2)

        by_id = {s.id: s for s in segs}
        # active-region edit applied
        self.assertEqual(by_id[0].text, "父さんとお母さん、今0。")
        # context-region edit rejected -> seg 2 unchanged
        self.assertEqual(by_id[2].text, originals[2])


# ── 改了就清、没改保留 ───────────────────────────────────────────────────────

class TestUncertainClearPolicy(unittest.TestCase):
    def test_smoothed_segment_clears_uncertain(self):
        from backend.worker import _run_refiner

        seg = DummySeg("父さんとお母さん今", id=1)
        seg.uncertain = True

        def respond(idx, prompt, system_prompt):
            return '{"corrections": [{"seg_id": 1, "fixed_text": "父さんとお母さん、今。"}]}'

        llm = RecordingLLM(response_fn=respond)
        _run_refiner([seg], llm, window_size=120, context_pad=20)

        self.assertEqual(seg.text, "父さんとお母さん、今。")
        self.assertFalse(seg.uncertain)

    def test_untouched_segment_keeps_uncertain(self):
        from backend.worker import _run_refiner

        seg = DummySeg("父さんとお母さん今", id=1)
        seg.uncertain = True

        llm = RecordingLLM()  # empty corrections
        _run_refiner([seg], llm, window_size=120, context_pad=20)

        self.assertEqual(seg.text, "父さんとお母さん今")
        self.assertTrue(seg.uncertain)


# ── progress visibility (Jobs/Gates: 14 calls must not look frozen) ──────────

class TestProgressCallback(unittest.TestCase):
    def test_progress_callback_fires_per_window(self):
        from backend.worker import _run_refiner

        segs = _jp_segs(300)  # single mode, 3 windows
        llm = RecordingLLM()
        seen = []

        def progress_cb(done, total, pass_name):
            seen.append((done, total, pass_name))

        _run_refiner(segs, llm, window_size=120, context_pad=20, progress_cb=progress_cb)

        self.assertGreaterEqual(len(seen), 3)


if __name__ == "__main__":
    unittest.main()
