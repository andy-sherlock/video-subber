"""TDD tests for P161: transcription pipeline wiring fix.

Written BEFORE code changes. Each test must FAIL against current code.

Subtask ②: L1 must NOT filter when aligner_result is None/empty.
           (Currently the fallback path filters single-signal non-human.)
Subtask ④: filter_hallucinations must work correctly when called with
           aligner data from standard tier (filter side).
"""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class DummySeg:
    def __init__(self, text, start=0.0, end=1.0, sensevoice_tag=None):
        self.text = text
        self.start = start
        self.end = end
        self.sensevoice_tag = sensevoice_tag
        self.filtered = False
        self.filter_reason = None
        self.uncertain = False
        self.words = []
        self.qwen_text = None
        self.id = 0
        self.speaker_change = False
        self.zh = ""


class TestP161_NoFallback(unittest.TestCase):
    """P183: L1 single-signal fallback added.

    When aligner data is unavailable (None or empty), L1 now trusts
    SenseVoice alone for non-human tags. Non-Speech SV tags result in
    filtering even without FA confirmation.
    """

    def test_nonhuman_tag_no_aligner_marked_not_filtered(self):
        """music tag + no aligner → 只标记可疑（G12 第二批推翻 P183 方案 A）。

        真实日志：SenseVoice 判非人声被对齐器推翻 3492 次，真删 0 次。
        「对齐器不可用时单独相信 SV」= 相信一个所有可核对场合都错了的信号。
        """
        from backend.core.hallucination_filter import _layer1_dual_signal

        seg = DummySeg("some text", start=1.0, end=3.0,
                       sensevoice_tag="music")
        result = _layer1_dual_signal(seg, aligner_result=None)

        self.assertFalse(seg.filtered, "单一声音标签不得拥有删除权")
        self.assertTrue(seg.uncertain)
        self.assertFalse(result)

    def test_nonhuman_tag_empty_aligner_marked_not_filtered(self):
        """noise tag + empty aligner → 只标记可疑。空对齐器与 None 同一处理。"""
        from backend.core.hallucination_filter import _layer1_dual_signal

        seg = DummySeg("noise text", start=0.0, end=2.0,
                       sensevoice_tag="noise")
        result = _layer1_dual_signal(seg, aligner_result=[])

        self.assertFalse(seg.filtered)
        self.assertTrue(seg.uncertain)
        self.assertFalse(result)

    def test_speech_tag_no_aligner_still_passes(self):
        """Speech tag + no aligner → still passes (regression check)."""
        from backend.core.hallucination_filter import _layer1_dual_signal

        seg = DummySeg("hello world", start=0.0, end=2.0,
                       sensevoice_tag="Speech")
        result = _layer1_dual_signal(seg, aligner_result=None)

        self.assertFalse(seg.filtered)
        self.assertFalse(result)

    def test_no_tag_no_aligner_skips(self):
        """No sensevoice_tag → L1 skips entirely (unchanged behavior)."""
        from backend.core.hallucination_filter import _layer1_dual_signal

        seg = DummySeg("text", sensevoice_tag=None)
        result = _layer1_dual_signal(seg, aligner_result=None)

        self.assertFalse(seg.filtered)
        self.assertFalse(result)

    def test_dual_signal_still_marks_when_both_agree(self):
        """双信号都说非人声 → 仍然只标记，不删除。

        「两个信号一致」在这里是假象：第二个信号只是「这段没有对齐词」，
        它不是独立证据，是第一个信号的缺席。**缺席不是证据。**
        """
        from backend.core.hallucination_filter import _layer1_dual_signal

        seg = DummySeg("music noise", start=1.0, end=3.0,
                       sensevoice_tag="music")
        aligner_result = [
            {"word": "far away", "start": 10.0, "end": 10.5},
        ]
        result = _layer1_dual_signal(seg, aligner_result=aligner_result)

        self.assertFalse(seg.filtered)
        self.assertTrue(seg.uncertain)
        self.assertFalse(result)

    def test_dual_signal_sv_wrong_clears_tag(self):
        """SV says music but aligner found words → SV wrong, clear tag."""
        from backend.core.hallucination_filter import _layer1_dual_signal

        seg = DummySeg("real speech", start=1.0, end=3.0,
                       sensevoice_tag="music")
        aligner_result = [
            {"word": "real", "start": 1.2, "end": 1.8},
        ]
        result = _layer1_dual_signal(seg, aligner_result=aligner_result)

        self.assertFalse(seg.filtered)
        self.assertIsNone(seg.sensevoice_tag)


class TestP161_Integration(unittest.TestCase):
    """Integration: non-human segments survive to filter when not pre-trimmed."""

    def test_nonhuman_segment_reaches_filter_with_aligner(self):
        """Non-human tagged segment + aligner confirms speech → kept."""
        from backend.core.hallucination_filter import filter_hallucinations

        seg = DummySeg("real words here spoken clearly",
                       start=0.0, end=3.0, sensevoice_tag="music")

        aligner_result = [
            {"word": "real", "start": 0.1, "end": 0.5},
            {"word": "words", "start": 0.6, "end": 1.0},
        ]

        result = filter_hallucinations([seg], source_lang="en",
                                       aligner_result=aligner_result)

        self.assertEqual(len(result), 1,
                         "Segment should survive: aligner overrides SV")
        self.assertIsNone(result[0].sensevoice_tag)

    def test_mistagged_real_speech_survives_l1(self):
        """被 SenseVoice 误标的真实语音必须存活。

        ⚠️ **注意这条测试自己的用例文字**：
        `"this is actual speech that sensevoice mistagged"`
        ——当初写测试的人已经写明这是被误标的真实语音，却断言它应该被删掉。
        真实日志证实了这种误标发生了 3492 次，而真删 0 次。
        """
        from backend.core.hallucination_filter import filter_hallucinations

        seg = DummySeg("this is actual speech that sensevoice mistagged",
                       start=0.0, end=3.0, sensevoice_tag="noise")
        seg.qwen_text = "this is actual speech"

        result = filter_hallucinations([seg], source_lang="en",
                                       aligner_result=None)

        self.assertEqual(len(result), 1, "被误标的真实语音不得被删")
        self.assertTrue(seg.uncertain, "但要标可疑，噪音穿透由标记承接")


if __name__ == '__main__':
    unittest.main()
