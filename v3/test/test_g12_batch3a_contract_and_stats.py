"""G12 第三批 A · 跨问题共同要求：缓存合同版本 + 缺标点统计的假警报。

**E1 — 缓存合同版本必须 +1（本批统一一次）**

`worker.py` 的注释自己写着触发条件：「主引擎路由、正式词流选择、过滤或对齐规则
改变时必须加版本」。本批**既改主引擎路由（P463）、又改接缝词流（P465）**，两条都命中。

**漏掉的后果**：用户升级软件后，老任务直接复用修复前的 `transcript.json`，
新路由和新接缝规则**一行都不会执行**——而且表面完全正常。

**E2 — 缺标点统计在本批之后会变成假警报源**

`count_chunks_missing_punctuation` 的结果进收据的 `observation_evidence`，
注释写着「用户可见文案和验收都从这里读」。

P463 修完后，Windows 精准模式仍保留 Qwen 做交叉验证，那些块 `raw_segments=[]`、
被标成 `punctuation_attached=False`。但**它们根本不进正式字幕**（Whisper 才是正式来源），
于是用户会看到「10 块缺少句末标点」，而那 10 块的字幕**压根不存在**。

**与第二批栽过的坑同形状**：无障碍标记那一刀「音符开火 22 次」，
真跑发现那 22 条全是 `words=0` 的段、根本进不了成品。**判一条统计该不该报，
要问它数的东西到不到得了用户手里。**

Run: cd v3 && .venv/bin/python -m pytest test/test_g12_batch3a_contract_and_stats.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest

from backend.core.qwen_asr_runner import count_chunks_missing_punctuation


class TestTranscriptionContractVersionBumped(unittest.TestCase):
    """E1 / T13：本批改了主引擎路由与接缝词流，合同版本必须 +1。"""

    def test_e1_contract_version_is_at_least_3(self):
        from backend.worker import _TRANSCRIPTION_CONTRACT_VERSION

        self.assertGreaterEqual(
            _TRANSCRIPTION_CONTRACT_VERSION, 3,
            "G12 第一批用的是 2。本批既改路由（P463）又改接缝词流（P465），"
            "不 +1 的话老任务会复用修复前的 transcript.json，改动一行都不会跑")


class TestMissingPunctuationCountIgnoresChunksThatNeverShip(unittest.TestCase):
    """E3 / T18：只统计**会进正式词流**的块，否则就是假警报。"""

    def test_e3_chunk_without_word_timings_is_not_counted(self):
        """Windows 精准模式的 Qwen 辅助块：没有词级时间 → 产不出字幕 → 不该报。"""
        auxiliary_only = [{
            "text": "这段文字只用于交叉验证",
            "raw_segments": [],              # ← 连原始材料都没有（Windows PyTorch 分支）
            "segments": [],
            "punctuation_attached": False,
        }]

        self.assertEqual(
            count_chunks_missing_punctuation(auxiliary_only), 0,
            "这一块的字幕根本不存在，报『它缺少句末标点』是假警报——"
            "与第二批那 22 条 words=0 的段是同一个形状")

    def test_e3_real_subtitle_chunk_missing_punctuation_is_still_counted(self):
        """对照组：真的会进成品、又确实没挂上标点的块，必须照报不误。"""
        will_ship = [{
            "text": "这段会变成字幕",
            "raw_segments": [{"text": "这段会变成字幕", "start": 0.0, "end": 1.0}],
            # 挂标点失败时 segments 也可能是空的——**判据必须看 raw_segments**，
            # 否则这条真警报会被一起收掉（P454 的测试当场抓到过）。
            "segments": [],
            "punctuation_attached": False,
        }]

        self.assertEqual(
            count_chunks_missing_punctuation(will_ship), 1,
            "收窄不能把真警报一起收掉——P457 加这个统计就是因为"
            "「只写日志等于没说」，真实任务打了 20 次警告三个月无人发现")

    def test_e3_mixed_batch_counts_only_shippable(self):
        chunks = [
            {"text": "a", "raw_segments": [], "punctuation_attached": False},
            {"text": "b", "raw_segments": [{"text": "b", "start": 0.0, "end": 1.0}],
             "punctuation_attached": False},
            {"text": "c", "raw_segments": [{"text": "c", "start": 1.0, "end": 2.0}],
             "punctuation_attached": True},
        ]
        self.assertEqual(count_chunks_missing_punctuation(chunks), 1)


if __name__ == "__main__":
    unittest.main()
