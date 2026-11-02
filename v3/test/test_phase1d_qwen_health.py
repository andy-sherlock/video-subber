"""P148: L2 cross-validation must not trust a collapsed Qwen-ASR.

When Qwen-ASR suffers a repetition collapse (its output is dominated by one
identical repeated string), it is an unreliable referee. L2 must detect this
and skip cross-validation, instead of stamping [?] on the correct Whisper text.

Reproduces job 97c57255ec: 116/198 segments got the identical garbage
"1056234897 нет ты меня прости" -> L2 marked 77% of correct subtitles [?].

P251: Qwen reaches L2 / the collapse check via a qwen_chunks list, not
per-segment qwen_text.
"""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class DummySeg:
    """Minimal segment for filter testing."""
    def __init__(self, text, start=0.0, end=1.0, sensevoice_tag=None):
        self.text = text
        self.start = start
        self.end = end
        self.sensevoice_tag = sensevoice_tag
        self.filtered = False
        self.filter_reason = None
        self.uncertain = False
        self.words = []
        self.zh = None


def _chunk(text, start, end):
    return {"text": text, "t_start": start, "t_end": end}


class TestQwenHealthCheck(unittest.TestCase):
    """P148: collapsed Qwen must not poison L2."""

    def test_collapsed_qwen_does_not_flag_uncertain(self):
        """80% of qwen chunks are the same garbage string -> L2 skipped, 0 [?]."""
        from backend.core.hallucination_filter import filter_hallucinations

        garbage = "1056234897 нет ты меня прости"
        # G12 第二批段一：原来这十句是 `f"正常的第{i}句字幕内容"`，只差一个字符，
        # 相似度恰好 0.9，会被收权后的 L5 整段标为疑似重复游程。
        # 那是**合成数据的产物**——三个真实任务里 ≥3 条的游程数为 0，
        # 完全相同与 ≥0.9 两种判据结果一致。
        # 修数据、不改断言：这条测试守的是「Qwen 坍缩时 L2 不得乱标」，
        # 断言强度一点不减，只是把不真实的输入换成真实形状的字幕。
        distinct = [
            "今天我们要去南岛自驾", "第一站是皇后镇", "路上风景特别好",
            "中午在湖边吃了午饭", "下午开始下雨了", "晚上住在一家小旅馆",
            "第二天去了米尔福德峡湾", "船上看到了海豹", "回程堵了两个小时",
            "这趟旅行值得再来一次",
        ]
        segs, qwen_chunks = [], []
        # 8/10: Whisper text is good & distinct, Qwen collapsed to garbage.
        for i in range(8):
            segs.append(DummySeg(distinct[i], start=float(i),
                                 end=float(i) + 1.0, sensevoice_tag="Speech"))
            qwen_chunks.append(_chunk(garbage, float(i), float(i) + 1.0))
        # 2/10: Qwen agrees with Whisper.
        for i in range(8, 10):
            segs.append(DummySeg(distinct[i], start=float(i),
                                 end=float(i) + 1.0, sensevoice_tag="Speech"))
            qwen_chunks.append(_chunk(distinct[i], float(i), float(i) + 1.0))

        result = filter_hallucinations(segs, source_lang="zh", qwen_chunks=qwen_chunks)

        flagged = [s for s in result if s.uncertain]
        self.assertEqual(
            flagged, [],
            "Collapsed Qwen (80%% identical garbage) must not cause [?] marks, "
            "got %d flagged" % len(flagged))

    def test_healthy_qwen_flags_window_with_heavy_disagreement(self):
        """P160: all segments in one window, Qwen text is completely different → YELLOW."""
        from backend.core.hallucination_filter import filter_hallucinations

        qwen_texts = ["天地人年月日", "春夏秋冬花", "东西南北中", "金木水火土", "风雨雷电云"]
        segs, qwen_chunks = [], []
        for i in range(5):
            segs.append(DummySeg(f"你我他是谁第{i}段", start=float(i),
                                 end=float(i) + 1.0, sensevoice_tag="Speech"))
            qwen_chunks.append(_chunk(qwen_texts[i], float(i), float(i) + 1.0))
        result = filter_hallucinations(segs, source_lang="zh", qwen_chunks=qwen_chunks)

        flagged = [s for s in result if s.uncertain]
        self.assertGreater(len(flagged), 0,
                           "Window with zero stem overlap should be YELLOW")


if __name__ == '__main__':
    unittest.main()
