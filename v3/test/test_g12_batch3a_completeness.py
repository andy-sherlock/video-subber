"""G12 第三批 A · P464：断点续跑遇模型加载失败，半部字幕冒充完整成品。

**用户看到的**：转一部长视频，中途关掉软件；再打开时模型因文件损坏／缺失／内存不足
加载失败，**系统把已经转完的开头当成完整字幕交付，任务显示「完成」**。
后半段字幕凭空消失，没有任何东西告诉他这是残缺的。

**根因**：完成条件只判断「有没有结果」，**从不判断「结果覆盖没覆盖完整输入」**。

    except Exception:
        _log.warning("Qwen3-ASR 模型加载失败，跳过交叉验证")
        return [c for c in _refresh_chunk_delivery(...) if c.get("text","").strip()]
        #      ↑ 直接把已有块交出去，没有任何地方问「计划的块都转完了吗」

**不是本批引入**，`5e2fa06` 之前即存在，**跨平台，Mac 同样中招**。
违反 G7（`f890747`）已确立的「某块失败就停止，不交付残缺成品」——
G7 治的是转录**中途**失败，本条是转录**尚未开始**就失败，走的是不同的 return。

**两条外审收窄（都不可省）**：

1. **不能比过滤后的返回列表长度。** 没有人说话的块**合法为空**，
   拿列表长度比会把带长静音的视频误判成失败。要用 checkpoint 账本
   （`done_idx` / `results` / `time_chunks`）。
2. **runner 不许无条件抛错。** 精准模式下 Whisper 可能已经完整跑完，
   Qwen 只是交叉验证的辅助；为了拦住残缺的 Qwen 而抛异常，
   会把已经做好的 Whisper 一起弄死——治病治死了病人。
   runner 只说「这份不完整、不能采用」，由上层判断有没有完整替代品。

Run: cd v3 && .venv/bin/python -m pytest test/test_g12_batch3a_completeness.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest

from backend.core.qwen_asr_runner import QwenAsrOutcome, chunks_are_complete


class TestCompletenessUsesLedgerNotFilteredList(unittest.TestCase):
    """T5 / B1：完成判定用 checkpoint 账本，**不比过滤后的列表**。

    这是本批最容易写反的一条：静音块合法为空，
    用「返回了几条字幕」去判完整性，会把带长静音的视频误判成失败。
    """

    def test_t5_silent_chunks_are_still_complete(self):
        """10 块里 3 块全静音（没有文字）——这是**完整**，不是失败。"""
        time_chunks = [(i * 300.0, (i + 1) * 300.0) for i in range(10)]
        results = [{"text": "" if i in (3, 4, 7) else f"chunk {i}"}
                   for i in range(10)]

        self.assertTrue(
            chunks_are_complete(done_idx=10, results=results, time_chunks=time_chunks),
            "静音块没有文字是正常的，不能因此判定转录不完整")

    def test_t5_short_ledger_is_incomplete(self):
        """只转了 6 块、计划 10 块 —— 这才是不完整。"""
        time_chunks = [(i * 300.0, (i + 1) * 300.0) for i in range(10)]
        results = [{"text": f"chunk {i}"} for i in range(6)]

        self.assertFalse(
            chunks_are_complete(done_idx=6, results=results, time_chunks=time_chunks))

    def test_t5_self_contradictory_ledger_is_incomplete(self):
        """账本自相矛盾（done_idx 说 10，实际只存了 6 块）→ 不得当成完整。"""
        time_chunks = [(i * 300.0, (i + 1) * 300.0) for i in range(10)]
        results = [{"text": f"chunk {i}"} for i in range(6)]

        self.assertFalse(
            chunks_are_complete(done_idx=10, results=results, time_chunks=time_chunks),
            "done_idx 与实际保存数对不上，是坏账本，不能声称完整")


class TestOutcomeCarriesExplicitState(unittest.TestCase):
    """T6 / B2：runner 交出**明确状态**，不抛错、也不让上层猜。"""

    def test_t6_incomplete_outcome_reports_reason_and_position(self):
        outcome = QwenAsrOutcome(
            chunks=[{"text": "前半段"}],
            complete=False,
            completed_until=1800.0,
            failure_reason="模型加载失败",
        )

        self.assertFalse(outcome.complete)
        self.assertEqual(outcome.completed_until, 1800.0,
                         "必须说清处理到哪个时间——用户文案要用它，不能只说『失败』")
        self.assertTrue(outcome.failure_reason,
                        "必须带原因，否则用户会以为是自己文件的问题，重试三次得到三次一样的结果")

    def test_t6_complete_outcome_needs_no_reason(self):
        outcome = QwenAsrOutcome(chunks=[{"text": "全部"}], complete=True)
        self.assertTrue(outcome.complete)
        self.assertIsNone(outcome.failure_reason)


class TestNotRunIsDistinctFromRanEmpty(unittest.TestCase):
    """T8 / B4：「没跑过」和「跑完了但一个块都没有」必须分开表达。

    **同形状的教训已经栽过一次**（`silent-zero-duration-restores-single-point-sampling`）：
    任何「失败时返回一个合法默认值」的地方，都要检查那个默认值
    会不会正好落进某个看起来正常的分支——**「未知」和「很小」必须分开表达。**

    `_qwen_holder.get("data", [])` 的默认值 `[]` 正是这个形状：
    线程还没写入时拿到 `[]`，新的完整性判定会把它当成「转出 0 块」。
    """

    def test_t8_not_run_is_not_complete(self):
        not_run = QwenAsrOutcome.not_run()
        self.assertFalse(not_run.complete,
                         "没跑过不能算完整——那正是「半部冒充完整」的入口")
        self.assertEqual(not_run.chunks, [])

    def test_t8_ran_but_empty_is_complete(self):
        """全片没有人说话：块数为 0，但转录**确实完整跑完了**。"""
        ran_empty = QwenAsrOutcome(chunks=[], complete=True)
        self.assertTrue(ran_empty.complete)

    def test_t8_the_two_are_distinguishable(self):
        """两者的 chunks 都是空列表——**只能靠状态区分，不能靠长度**。"""
        not_run = QwenAsrOutcome.not_run()
        ran_empty = QwenAsrOutcome(chunks=[], complete=True)

        self.assertEqual(len(not_run.chunks), len(ran_empty.chunks))
        self.assertNotEqual(not_run.complete, ran_empty.complete,
                            "长度分不开，状态必须分得开")


if __name__ == "__main__":
    unittest.main()


def _seg(tv2, text="hello", start=0.0, end=1.0):
    s = tv2.Segment(0, start, end, text)
    s.words = [{"text": text, "word": text, "start": start, "end": end}]
    return s


class TestUpperLayerDecidesWhatToDoWithIncompleteQwen(unittest.TestCase):
    """T7 / B3：**runner 只说「这份不完整」，由上层判断有没有完整替代品。**

    外审收窄的理由：精准模式下 Whisper 可能已经完整跑完，Qwen 只是交叉验证的辅助。
    为了拦住残缺的 Qwen 而在 runner 里抛异常，会把已经做好的 Whisper 一起弄死
    ——治病治死了病人。而且 Mac 是串行调用、Windows 是并行线程，
    同一个故障在两个平台会得到不同结果。
    """

    class _DoneThread:
        """已经跑完的 Qwen 线程替身。

        **不能传 None**：精准模式下 `not qwen_thread` 会触发串行兜底、
        重新调用真的 `run_qwen_asr`，把 holder 里的结果覆盖掉——
        第一版测试就是这么假通过的。
        """
        def join(self):
            return None

    def _route(self, tv2, outcome, whisper_segments, tier="precise"):
        return tv2._resolve_routing(
            self._DoneThread(), {"data": outcome}, tier, True, "/tmp/a.wav", "en",
            None, "whisper", True, whisper_segments)

    def test_t7_incomplete_qwen_is_dropped_but_whisper_still_delivers(self):
        """精准模式：Qwen 只转了一半，但 Whisper 完整 → 照常交付 Whisper 字幕。"""
        import backend.transcriber_v2 as tv2

        half = QwenAsrOutcome(
            chunks=[{"text": "前半段", "t_start": 0.0, "t_end": 1800.0,
                     "segments": [{"text": "前半段", "start": 0.0, "end": 1.0}]}],
            complete=False, completed_until=1800.0,
            failure_reason="语音识别模型加载失败")
        whisper = [_seg(tv2, "hello", 0.0, 1.0), _seg(tv2, "world", 1.0, 2.0)]

        route = self._route(tv2, half, whisper)

        self.assertEqual(route.qwen_chunks, [],
                         "不完整的 Qwen 必须被丢弃，不得进入正式交付源")
        self.assertTrue(route.whisper_segments,
                        "Whisper 已经完整跑完，必须照常交付——不能被 Qwen 的故障拖死")

    def test_t7_complete_qwen_is_kept(self):
        """对照组：完整的 Qwen 不受影响。"""
        import backend.transcriber_v2 as tv2

        whole = QwenAsrOutcome(
            chunks=[{"text": "全部", "t_start": 0.0, "t_end": 60.0,
                     "segments": [{"text": "全部", "start": 0.0, "end": 1.0}]}],
            complete=True, completed_until=60.0)

        route = self._route(tv2, whole, [_seg(tv2)])

        self.assertTrue(route.qwen_chunks, "完整的 Qwen 不得被误伤")
