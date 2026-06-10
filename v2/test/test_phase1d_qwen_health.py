"""P148: L2 cross-validation must not trust a collapsed Qwen-ASR.

When Qwen-ASR suffers a repetition collapse (its output is dominated by one
identical repeated string), it is an unreliable referee. L2 must detect this
and skip cross-validation, instead of stamping [?] on the correct Whisper text.

Reproduces job 97c57255ec: 116/198 segments got the identical garbage
"1056234897 нет ты меня прости" -> L2 marked 77% of correct subtitles [?].
"""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class DummySeg:
    """Minimal segment for filter testing."""
    def __init__(self, text, start=0.0, end=1.0, sensevoice_tag=None,
                 qwen_text=None):
        self.text = text
        self.start = start
        self.end = end
        self.sensevoice_tag = sensevoice_tag
        self.filtered = False
        self.filter_reason = None
        self.uncertain = False
        self.words = []
        self.qwen_text = qwen_text
        self.zh = None


class TestQwenHealthCheck(unittest.TestCase):
    """P148: collapsed Qwen must not poison L2."""

    def test_collapsed_qwen_does_not_flag_uncertain(self):
        """80% of qwen_text is the same garbage string -> L2 skipped, 0 [?]."""
        from backend.core.hallucination_filter import filter_hallucinations

        garbage = "1056234897 нет ты меня прости"
        segs = []
        # 8/10 segments: Whisper text is good & distinct, Qwen collapsed.
        for i in range(8):
            segs.append(DummySeg(f"正常的第{i}句字幕内容", start=float(i),
                                 end=float(i) + 1.0, sensevoice_tag="Speech",
                                 qwen_text=garbage))
        # 2/10 segments: Qwen agrees with Whisper.
        for i in range(8, 10):
            segs.append(DummySeg(f"正常的第{i}句字幕内容", start=float(i),
                                 end=float(i) + 1.0, sensevoice_tag="Speech",
                                 qwen_text=f"正常的第{i}句字幕内容"))

        result = filter_hallucinations(segs, source_lang="zh")

        flagged = [s for s in result if s.uncertain]
        self.assertEqual(
            flagged, [],
            "Collapsed Qwen (80%% identical garbage) must not cause [?] marks, "
            "got %d flagged" % len(flagged))

    def test_healthy_qwen_flags_window_with_heavy_disagreement(self):
        """P160: all segments in one window, Qwen text is completely different → YELLOW."""
        from backend.core.hallucination_filter import filter_hallucinations

        qwen_texts = ["天地人年月日", "春夏秋冬花", "东西南北中", "金木水火土", "风雨雷电云"]
        segs = []
        for i in range(5):
            segs.append(DummySeg(f"你我他是谁第{i}段", start=float(i),
                                 end=float(i) + 1.0, sensevoice_tag="Speech",
                                 qwen_text=qwen_texts[i]))
        result = filter_hallucinations(segs, source_lang="zh")

        flagged = [s for s in result if s.uncertain]
        self.assertGreater(len(flagged), 0,
                           "Window with zero stem overlap should be YELLOW")


if __name__ == '__main__':
    unittest.main()
