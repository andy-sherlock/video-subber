"""G2 · B3 + B5–B8 + B13 + B15：润色提示词改成「乙」结构。

旧结构（作废）：逐段判断主副引擎文本是否「冲突」，冲突的段拼一个
`[CONFLICT seg_id: N]` 块，把两个引擎对这一段的说法并排给 AI。
它的前提是「两个引擎对同一段的切法一样」——而这个前提不成立。

新结构「乙」：
- **A 块**：本窗口的分组，每行带编号（`段落 N: 文本`）。
- **B 块**：副引擎的转录，**整块给**，不切到段、不带编号。
- 输出仍然是按 `seg_id` 的改动清单，**绝不返回整篇改写文本**——
  返回整篇就需要一个「把改写文本对回段」的对齐部件，那个方案（甲）已被否决。

实测支撑：134 个段落编号，AI 在无编号的整块文本里定位，**零认错**。
人工植入 6 处同音异义错词，给参照抓 6/6，不给参照抓 4/6。
"""
import sys
from pathlib import Path

from test import locked_decision
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest

from backend.worker import _build_refiner_prompt, _slice_reference_text


class _Seg:
    def __init__(self, id, text, start=0.0, end=1.0):
        self.id = id
        self.text = text
        self.start = start
        self.end = end
        self.uncertain = False


class TestB3ConflictBlockDeleted(unittest.TestCase):
    def test_refiner_prompt_has_no_conflict_block(self):
        segments = [_Seg(1, "the fudge stack is ready")]

        user_prompt, _ = _build_refiner_prompt(
            segments, "dual", reference_text="the fudge sack is ready"
        )

        self.assertNotIn("CONFLICT", user_prompt)
        self.assertNotIn("Whisper (参考)", user_prompt)

    def test_normalize_helper_is_gone(self):
        """冲突判定没了，为它服务的归一化函数也不该留着。"""
        import backend.worker as w

        self.assertFalse(hasattr(w, "_normalize_refiner_text"))


class TestB5PromptStructure(unittest.TestCase):
    def test_refiner_prompt_ab_structure(self):
        """A 块每段带编号，B 块整块不带编号。"""
        segments = [_Seg(7, "hello world"), _Seg(8, "how are you")]

        user_prompt, _ = _build_refiner_prompt(
            segments, "dual", reference_text="hello word how are you"
        )

        self.assertIn("段落 7: hello world", user_prompt)
        self.assertIn("段落 8: how are you", user_prompt)
        self.assertIn("hello word how are you", user_prompt)
        # B 块是整块文本，不许被切成带编号的行
        self.assertNotIn("段落 7: hello word", user_prompt)

    def test_refiner_output_format_is_still_seg_id_list(self):
        """输出契约不变：按 seg_id 的改动清单，不是整篇改写。"""
        segments = [_Seg(1, "hello world")]

        _, system_prompt = _build_refiner_prompt(
            segments, "dual", reference_text="hello word"
        )

        self.assertIn("seg_id", system_prompt)
        self.assertIn("corrections", system_prompt)

    def test_prompt_forbids_whole_text_rewrite(self):
        """必须明确禁止整篇返回，否则模型有概率自作主张。"""
        segments = [_Seg(1, "hello world")]

        _, system_prompt = _build_refiner_prompt(
            segments, "dual", reference_text="hello word"
        )

        self.assertIn("整篇", system_prompt)


class TestB6NoCrossSegmentCarry(unittest.TestCase):
    def test_prompt_contains_no_cross_segment_rule(self):
        """P424：润色把下一段的词搬进本段、下一段又保留 → 同一句连出现两次。

        实测加这一句后，偷词 3 次 → 0 次。
        """
        segments = [_Seg(1, "hello world")]

        for mode, kwargs in (("dual", {"reference_text": "hello word"}),
                             ("single", {})):
            with self.subTest(mode=mode):
                _, system_prompt = _build_refiner_prompt(
                    segments, mode, **kwargs
                )
                self.assertIn("不跨段搬运", system_prompt)

    def test_refined_segment_does_not_absorb_next_segment_text(self):
        """行为层：喂一个半截句的夹具，改后文本不许含下一段的内容。"""
        from backend.worker import _apply_refiner_corrections

        segments = [_Seg(1, "I went to the"), _Seg(2, "store yesterday")]
        # 模型如果偷词，会把 seg 1 改成 "I went to the store yesterday"
        corrections = [{"seg_id": 1, "fixed_text": "I went to the"}]

        _apply_refiner_corrections(segments, corrections, "single")

        self.assertNotIn("store yesterday", segments[0].text)
        self.assertEqual(segments[1].text, "store yesterday")


class TestB7HomophoneRule(unittest.TestCase):
    def test_prompt_contains_homophone_rule(self):
        """参照文本的真实价值就在同音异义：给参照抓 6/6，不给抓 4/6。"""
        segments = [_Seg(1, "hello world")]

        _, system_prompt = _build_refiner_prompt(
            segments, "dual", reference_text="hello word"
        )

        self.assertIn("同音", system_prompt)


class TestB8SingleEngineHasNoBBlock(unittest.TestCase):
    def test_single_engine_prompt_has_no_b_block(self):
        segments = [_Seg(1, "hello world")]

        user_prompt, _ = _build_refiner_prompt(segments, "single")

        self.assertIn("段落 1: hello world", user_prompt)
        self.assertNotIn("另一套语音识别", user_prompt)

    def test_single_engine_ignores_reference_text_if_passed(self):
        """单引擎模式下就算把参照传进来也不许发出去。"""
        segments = [_Seg(1, "hello world")]

        user_prompt, _ = _build_refiner_prompt(
            segments, "single", reference_text="hello word from whisper"
        )

        self.assertNotIn("hello word from whisper", user_prompt)


class TestB13B15ReferenceSlicing(unittest.TestCase):
    def test_b_block_is_windowed_not_full(self):
        """B13：10 个窗口，单窗口的 B 块必须远小于全文。

        发整篇 → 每个窗口都带一份全文 → O(n²)。
        两小时的视频估 10 窗口 × 76K 字符。
        """
        full = " ".join(f"word{i}" for i in range(1000))

        piece = _slice_reference_text(full, 0.0, 10.0, total_end=100.0)

        self.assertTrue(piece)
        self.assertLess(len(piece), len(full) * 0.5)

    def test_b_block_range_follows_time_not_index(self):
        """B15：按时间比例切，不按段序号均分。

        构造：窗口覆盖媒体的最后 10% 时间。参照文本的结尾部分才是对的答案。
        按序号均分会切到中间去。
        """
        full = " ".join(f"w{i}" for i in range(100)) + " ENDMARKER"

        piece = _slice_reference_text(full, 90.0, 100.0, total_end=100.0)

        self.assertIn("ENDMARKER", piece)

    def test_slice_covers_start_of_media(self):
        full = "STARTMARKER " + " ".join(f"w{i}" for i in range(100))

        piece = _slice_reference_text(full, 0.0, 10.0, total_end=100.0)

        self.assertIn("STARTMARKER", piece)

    def test_slice_has_margin_on_both_sides(self):
        """±30% 余量：切窄了会把该看的词切掉，主副引擎的边界本来就对不齐。"""
        full = " ".join(f"w{i}" for i in range(100))

        piece = _slice_reference_text(full, 40.0, 50.0, total_end=100.0)

        # 窗口占 10%，加两侧 30% 余量 → 明显宽于 10%
        self.assertGreater(len(piece), len(full) * 0.10)

    def test_single_window_covering_all_time_gets_everything(self):
        full = "alpha beta gamma"

        piece = _slice_reference_text(full, 0.0, 100.0, total_end=100.0)

        self.assertEqual(piece.strip(), full)

    def test_empty_reference_stays_empty(self):
        self.assertEqual(_slice_reference_text("", 0.0, 10.0, total_end=100.0), "")

    def test_zero_total_end_returns_full_text(self):
        """时长信息不可信时，宁可整篇给，也不要切出一段错的。"""
        full = "alpha beta gamma"

        self.assertEqual(
            _slice_reference_text(full, 0.0, 0.0, total_end=0.0), full
        )

    def test_slice_works_on_cjk_text_without_spaces(self):
        """中日韩整篇没有空格，切分照样要生效。

        ⑦.5 diff 审查发现的缺陷：切点原来无条件对齐到词边界（往左退到上一个
        空白、往右进到下一个空白）。日文/中文整篇一个空格都没有，两个循环
        直接跑到头尾 → **每个窗口都拿到全文**，B13 要防的 O(n²) 原样存在。
        本项目的实测任务里就有日语（3739d2e686），这条不是理论情况。

        前面所有切分测试都用英文空格分词，所以一条都没照出来。
        """
        full = "今日は天気がいいですね" * 40

        piece = _slice_reference_text(full, 0.0, 10.0, total_end=100.0)

        self.assertTrue(piece)
        self.assertLess(len(piece), len(full) * 0.5)

    def test_cjk_slice_still_follows_time_position(self):
        """无空格文本的切分位置也得跟着时间走，不能随便切一段。"""
        full = "起" + "中" * 200 + "終"

        head = _slice_reference_text(full, 0.0, 5.0, total_end=100.0)
        tail = _slice_reference_text(full, 95.0, 100.0, total_end=100.0)

        self.assertIn("起", head)
        self.assertNotIn("終", head)
        self.assertIn("終", tail)
        self.assertNotIn("起", tail)

    def test_slice_does_not_cut_words_in_half(self):
        full = " ".join(f"word{i}" for i in range(200))

        piece = _slice_reference_text(full, 40.0, 50.0, total_end=100.0)

        for token in piece.split():
            self.assertIn(token, full.split())


class _RecordingLLM:
    def __init__(self):
        self.calls = []

    def translate_raw_json(self, user_prompt, system_prompt=""):
        self.calls.append(user_prompt)
        return '{"corrections": []}'


class TestB13WindowedReferenceInRunRefiner(unittest.TestCase):
    """B13 的接线：切分必须真的用在 `_run_refiner` 里，不只是函数写对了。"""

    def _receipt(self, reference_text):
        from backend.transcriber_v2 import TranscriptionReceipt

        return TranscriptionReceipt(
            routed_primary="qwen",
            actual_primary="qwen",
            word_source="qwen_word_timestamps",
            source_text="a",
            source_words=[{"text": "a", "start": 0.0, "end": 1.0}],
            reference_text=reference_text,
            reference_usable=True,
                   language_decision=locked_decision(),
        artifact_language_check="ok",
               )

    def test_each_window_gets_its_own_reference_slice(self):
        from backend.worker import _run_refiner

        segments = [_Seg(i, f"seg{i}", start=float(i), end=float(i) + 1.0)
                    for i in range(10)]
        full = "HEAD " + " ".join(f"w{i}" for i in range(200)) + " TAIL"
        llm = _RecordingLLM()

        _run_refiner(segments, llm, receipt=self._receipt(full),
                     window_size=5, context_pad=0)

        dual_calls = [c for c in llm.calls if "B 块" in c]
        self.assertEqual(len(dual_calls), 2)
        self.assertIn("HEAD", dual_calls[0])
        self.assertNotIn("TAIL", dual_calls[0])
        self.assertIn("TAIL", dual_calls[1])
        self.assertNotIn("HEAD", dual_calls[1])

    def test_smoothing_pass_never_carries_b_block(self):
        """第二趟是通顺润色（单引擎结构），不带 B 块。"""
        from backend.worker import _run_refiner

        segments = [_Seg(0, "seg0", start=0.0, end=1.0)]
        llm = _RecordingLLM()

        _run_refiner(segments, llm, receipt=self._receipt("reference words"),
                     window_size=5, context_pad=0)

        self.assertEqual(len(llm.calls), 2)
        self.assertIn("B 块", llm.calls[0])
        self.assertNotIn("B 块", llm.calls[1])


if __name__ == "__main__":
    unittest.main()
