"""TDD tests for P268/P269 pipeline fixes.

These tests pin the agreed bugfix scope:
- Qwen provides the L1 timeline.
- L1 marks propagate from Qwen L1 segments to assembled subtitle segments.
- L4 keeps CJK repeated speech as uncertain instead of hard-deleting it.
- Refiner single mode does not clear uncertain marks when it made no correction.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from types import SimpleNamespace


class DummySeg:
    def __init__(self, text, start=0.0, end=1.0, id=0, l2_status=None):
        self.id = id
        self.text = text
        self.start = start
        self.end = end
        self.l2_status = l2_status
        self.filtered = False
        self.filter_reason = None
        self.uncertain = False
        self.sensevoice_tag = None
        self.bgm_override = False


class TestP268QwenL1Timeline(unittest.TestCase):
    def test_qwen_chunks_to_segments_builds_l1_timeline(self):
        from backend.transcriber_v2 import _qwen_chunks_to_segments

        qwen_chunks = [{
            "segments": [
                {"text": "やめて", "start": 10.0, "end": 10.6},
                {"text": "もっと", "start": 11.3, "end": 11.8},
            ],
        }]

        result = _qwen_chunks_to_segments(qwen_chunks, "ja")

        self.assertEqual([s.text for s in result], ["やめて", "もっと"])
        self.assertEqual([(s.start, s.end) for s in result], [(10.0, 10.6), (11.3, 11.8)])

    def test_l1_marks_propagate_from_qwen_sources(self):
        from backend.transcriber_v2 import _propagate_l1_marks

        source = DummySeg("やめて", start=10.0, end=12.0, id=1)
        source.filtered = True
        source.filter_reason = "layer1_dual_confirm_nonhuman"
        source.uncertain = True
        source.bgm_override = True

        assembled = [DummySeg("やめて", start=10.2, end=11.8, id=10)]

        _propagate_l1_marks([source], assembled)

        self.assertTrue(assembled[0].filtered)
        self.assertEqual(assembled[0].filter_reason, "layer1_dual_confirm_nonhuman")
        self.assertTrue(assembled[0].uncertain)
        self.assertTrue(assembled[0].bgm_override)


class TestP268WhisperHealth(unittest.TestCase):
    def test_tiny_three_segments_is_unusable(self):
        from backend.transcriber_v2 import _whisper_is_usable

        tiny = [
            DummySeg("one", 0.0, 20.0),
            DummySeg("two", 20.0, 50.0),
            DummySeg("three", 50.0, 85.0),
        ]

        self.assertFalse(_whisper_is_usable(tiny, total_end=7431.0))

    def test_digit_hallucination_is_unusable(self):
        from backend.transcriber_v2 import _whisper_is_usable

        segs = [DummySeg("12345", i, i + 1) for i in range(25)]
        segs += [DummySeg("normal speech", 25 + i, 26 + i) for i in range(25)]

        self.assertFalse(_whisper_is_usable(segs, total_end=50.0))

    def test_reasonable_whisper_is_usable(self):
        from backend.transcriber_v2 import _whisper_is_usable

        segs = [DummySeg(f"normal speech {i}", i, i + 1) for i in range(40)]

        self.assertTrue(_whisper_is_usable(segs, total_end=40.0))


class TestP268P269FilterProtection(unittest.TestCase):
    def test_l1_nonhuman_cjk_text_becomes_uncertain_not_filtered(self):
        from backend.core.hallucination_filter import _layer1_dual_signal

        seg = DummySeg("やめて", start=10.0, end=11.0)
        seg.sensevoice_tag = "Music"
        alignment = [{"text": "別", "start": 20.0, "end": 20.5}]

        result = _layer1_dual_signal(seg, alignment, "ja")

        self.assertFalse(result)
        self.assertFalse(seg.filtered)
        self.assertTrue(seg.uncertain)
        # G12 第二批收尾：CJK 专用豁免已取消——所有语言一视同仁只标记，
        # 原因串随之统一。本条守的「CJK 文本不得被删」一点没变。
        self.assertEqual(seg.filter_reason, "layer1_dual_nonhuman_uncertain")

    def test_l4_cjk_repeated_pattern_is_uncertain_not_filtered(self):
        from backend.core.hallucination_filter import _layer4_regex

        seg = DummySeg("もっともっともっと", l2_status="YELLOW")

        result = _layer4_regex(seg)

        self.assertFalse(result)
        self.assertFalse(seg.filtered)
        self.assertTrue(seg.uncertain)

    def test_l4_cjk_repeated_words_are_uncertain_not_filtered(self):
        from backend.core.hallucination_filter import _layer4_regex

        seg = DummySeg("やだ やだ やだ", l2_status="YELLOW")

        result = _layer4_regex(seg)

        self.assertFalse(result)
        self.assertFalse(seg.filtered)
        self.assertTrue(seg.uncertain)

    def test_l4_fullwidth_digits_still_normalized_and_marked(self):
        """本条真正守的是 **NFKC 归一化**：全角数字要能被重复模式匹配到。

        闸门 2 真跑补漏后重复模式只标记不删除，归一化本身一点没变。
        断言从「删除」改为「标可疑」，守的东西不变。
        """
        from backend.core.hallucination_filter import _layer4_regex

        seg = DummySeg("１２３１２３１２３", l2_status="YELLOW")

        _layer4_regex(seg)

        self.assertFalse(seg.filtered, "重复模式已交出删除权")
        self.assertTrue(seg.uncertain, "全角数字必须先归一化再被匹配到")


class TestP269RefinerSingleMode(unittest.TestCase):
    def test_both_engine_versions_reach_the_model_in_dual_mode(self):
        """两个引擎听成 claw / clock 时，两个版本都必须到模型面前。

        G2/B1+B3+B5：这条测试原名 `test_whisper_text_switches_refiner_to_dual_
        conflict_prompt`，靠给段挂 `whisper_text` 触发 dual、再断言出现
        `[CONFLICT seg_id: 3]` 块。那套按时间重叠拼参照 + 拼冲突块的做法已删
        （主副引擎切句边界对不齐，134 组里 76 组判「冲突」，68% 是纯边界错位）。

        **但这条测试真正在护的东西没变**：主引擎的 claw 在 A 块，副引擎的
        clock 在 B 块，模型两个都看得到。改的是怎么送，不是送不送。
        """
        from backend.worker import _build_refiner_prompt, _choose_refiner_mode

        seg = DummySeg("I wish I had claw", start=0.0, end=2.0, id=3)
        receipt = SimpleNamespace(
            reference_text="I wish I had clock", reference_usable=True,
        )

        mode = _choose_refiner_mode(receipt)
        user_prompt, _system_prompt = _build_refiner_prompt(
            [seg], mode, reference_text=receipt.reference_text,
        )

        self.assertEqual(mode, "dual")
        self.assertIn("段落 3: I wish I had claw", user_prompt)
        self.assertIn("clock", user_prompt)
        self.assertNotIn("CONFLICT", user_prompt)

    def test_single_mode_corrections_zero_keeps_uncertain(self):
        from backend.worker import _apply_refiner_corrections

        seg = DummySeg("父さんとお母さん今", id=7)
        seg.uncertain = True

        _apply_refiner_corrections([seg], [], mode="single")

        self.assertTrue(seg.uncertain)
        self.assertEqual(seg.text, "父さんとお母さん今")

    def test_dual_mode_corrections_zero_clears_uncertain(self):
        from backend.worker import _apply_refiner_corrections

        seg = DummySeg("hello world", id=8)
        seg.uncertain = True

        _apply_refiner_corrections([seg], [], mode="dual")

        self.assertFalse(seg.uncertain)

    def test_single_mode_only_clears_corrected_segment(self):
        from backend.worker import _apply_refiner_corrections

        a = DummySeg("父さんとお母さん今", id=1)
        b = DummySeg("やだ", id=2)
        a.uncertain = True
        b.uncertain = True

        _apply_refiner_corrections(
            [a, b],
            [{"seg_id": 1, "fixed_text": "父さんとお母さん、今。"}],
            mode="single",
        )

        self.assertEqual(a.text, "父さんとお母さん、今。")
        self.assertFalse(a.uncertain)
        self.assertTrue(b.uncertain)


if __name__ == "__main__":
    unittest.main()
