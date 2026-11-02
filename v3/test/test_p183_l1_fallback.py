"""TDD regression tests for P183: L1 single-signal fallback on FA failure.

Bug: In _layer1_dual_signal(), when aligner_result is None/empty, it returns False
(skip L1) without checking SenseVoice tags. Non-human audio tagged by SenseVoice
(e.g., "Music", "BGM", "Laughter") is NOT filtered when FA is unavailable.

Run: cd v3 && .venv/bin/python -m pytest test/test_p183_l1_fallback.py -v
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest


class FakeSegment:
    """Minimal segment matching what _layer1_dual_signal reads."""
    def __init__(self, text="hello", sensevoice_tag=None, words=None):
        self.text = text
        self.sensevoice_tag = sensevoice_tag
        self.words = words or []
        self.filtered = False
        self.filter_reason = None
        self.uncertain = False
        self.start = 0.0
        self.end = 1.0


class TestL1SingleSignalFallback(unittest.TestCase):
    """Verify L1 still filters non-Speech audio when FA is unavailable."""

    def test_fa_none_sv_nonhuman_is_marked_not_discarded(self):
        """FA=None + SV=Music → 只标记可疑，不删除。

    ⚠️ **G12 第二批收尾：本条断言已翻转，方向从 P183 的方案 A 改为方案 B。**

    P183（2026-06-07）当初把两个方案摆在一起选过：
      方案 A：FA 失败时恢复 L1 单信号兜底（SenseVoice 说非人声就丢）← 当初选的
      方案 B：FA 失败时不丢段落，但标 uncertain            ← 现在改成这个

    翻转依据是真实日志里的一组数字：
      SenseVoice 判「非人声」、被对齐器推翻（=SV 错了）： **3492 次**
      SenseVoice 判「非人声」、真的删掉了：                 **0 次**

    每一次能核对的场合，SenseVoice 的非人声判断都是错的。方案 A 的意思是
    「对齐器不可用时，单独相信这个信号」——相信一个在所有可核对场合
    都错了 3492 次的信号。段三保留零时长词之后段数变多，同一个 47 分钟视频的
    计数从 749 涨到 1688：**FA 一旦失败，方案 A 会删掉一千多段真话。**

    P183 要防的「噪音穿透」没有消失，它由「标记 + 前端存疑提示」承接，
    而不是由删除承接。**单一弱证据没有删除权。**
        """
        from backend.core.hallucination_filter import _layer1_dual_signal

        seg = FakeSegment(text="♪", sensevoice_tag="Music")

        result = _layer1_dual_signal(seg, aligner_result=None)

        self.assertFalse(result, "L1 不得再拥有删除权")
        self.assertFalse(seg.filtered)
        self.assertTrue(seg.uncertain, "噪音穿透由「标记」承接，不由删除承接")

    def test_fa_empty_sv_nonhuman_is_marked_not_discarded(self):
        """FA=[] + SV=Applause → 只标记可疑。空对齐器与 None 同一处理。"""
        from backend.core.hallucination_filter import _layer1_dual_signal

        seg = FakeSegment(text="clap", sensevoice_tag="Applause")

        result = _layer1_dual_signal(seg, aligner_result=[])

        self.assertFalse(result, "L1 不得再拥有删除权")
        self.assertFalse(seg.filtered)
        self.assertTrue(seg.uncertain)

    def test_fa_none_sv_speech_should_keep(self):
        """FA=None + SV=Speech → keep (not filtered)."""
        from backend.core.hallucination_filter import _layer1_dual_signal

        seg = FakeSegment(text="hello world", sensevoice_tag="Speech")

        result = _layer1_dual_signal(seg, aligner_result=None)

        self.assertFalse(result, "Speech with FA=None should not be filtered")
        self.assertFalse(seg.filtered)

    def test_fa_none_sv_tag_none_skip(self):
        """FA=None + SV tag=None → skip L1 (no SV data at all)."""
        from backend.core.hallucination_filter import _layer1_dual_signal

        seg = FakeSegment(text="test", sensevoice_tag=None)

        result = _layer1_dual_signal(seg, aligner_result=None)

        self.assertFalse(result, "No SV tag at all should skip L1")


if __name__ == "__main__":
    unittest.main()
