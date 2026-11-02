"""G12 第三批 A · E2/T17：失败的时候，界面上得说人话。

**最初那个 bug 的原始症状就是一句谎话**：

    处理失败了，我们没能识别出具体原因。请先重试一次……

**系统是知道原因的。** 它知道模型加载失败了，知道只处理到 30 分 00 秒。它就是不说。
用户等了半小时，拿到一句谎话，然后重试三次，得到三次同样的谎话。

本批那句话是「**不给用户假的成品**」。假成品有两种：
① 给了假的东西（空字幕 / 半部冒充完整 / 「我我当时想」）—— P463/P464/P465 已修
② **失败了但不说为什么** —— 就是这一条。解释也是成品的一部分。

**三种终态（外审裁定）**：

| 情况 | 处置 |
|---|---|
| Windows 因能力规则正常选 Whisper | **不提示**。正常路由不是降级，说了反而吓人 |
| 精准模式 Qwen 挂了但 Whisper 完整 | 任务**完成** + 持久摘要「识别增强未完成，已使用完整的语音识别结果」 |
| 没有任何完整识别结果 | 红色失败 + 说清**处理到哪、有没有成品、下一步做什么** |

**用户文案不出现「块」**——块数留日志。用户只需要知道处理到视频的什么位置。

⚠️ **本文件只覆盖后端状态的生成与持久化。**
界面上那句话到底显示出来没有、刷新后还在不在，**Python 测不了**——
真证据在 ⑨ 界面 QA。

Run: cd v3 && .venv/bin/python -m pytest test/test_g12_batch3a_terminal_message.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest

from backend.core.qwen_asr_runner import QwenAsrOutcome
from backend.transcriber_v2 import describe_transcription_shortfall


class TestShortfallIsSilentWhenNothingWentWrong(unittest.TestCase):
    """情况一：正常路由**不提示**。"""

    def test_complete_outcome_says_nothing(self):
        whole = QwenAsrOutcome(chunks=[{"text": "x"}], complete=True,
                               completed_until=600.0)
        self.assertIsNone(describe_transcription_shortfall(whole, whisper_ok=True))

    def test_never_run_says_nothing(self):
        """Windows 因能力规则走 Whisper，Qwen 压根没跑——这是正常路由，不是故障。"""
        self.assertIsNone(
            describe_transcription_shortfall(QwenAsrOutcome.not_run(), whisper_ok=True))


class TestShortfallExplainsWhenEnhancementFailedButOutputIsFine(unittest.TestCase):
    """情况二：增强挂了，但主识别完整 → 任务照常完成，只说明一句。"""

    def test_incomplete_qwen_with_good_whisper_is_informational(self):
        half = QwenAsrOutcome(chunks=[{"text": "前半"}], complete=False,
                              completed_until=1800.0,
                              failure_reason="语音识别模型加载失败")

        note = describe_transcription_shortfall(half, whisper_ok=True)

        self.assertIsNotNone(note)
        self.assertEqual(note["severity"], "info",
                         "有完整成品，不能报红——那会让用户以为字幕坏了")
        self.assertNotIn("Qwen", note["message"])
        self.assertNotIn("Whisper", note["message"])
        self.assertNotIn("块", note["message"], "用户文案不出现『块』，块数留日志")


class TestShortfallIsFatalWhenNothingIsComplete(unittest.TestCase):
    """情况三：什么完整结果都没有 → 红色失败，且必须说清三件事。"""

    def test_no_complete_result_is_fatal_and_explains_itself(self):
        half = QwenAsrOutcome(chunks=[{"text": "前半"}], complete=False,
                              completed_until=1800.0,
                              failure_reason="语音识别模型加载失败")

        note = describe_transcription_shortfall(half, whisper_ok=False)

        self.assertEqual(note["severity"], "error")
        self.assertIn("30:00", note["message"],
                      "必须说清处理到视频的什么位置——用时间，不用块数")
        self.assertIn("模型", note["message"], "必须带原因，否则用户会以为是自己文件的问题")
        self.assertEqual(note["completed_until"], 1800.0,
                         "结构化字段要一起交出去，界面刷新后靠它重建同一句话")

    def test_message_uses_time_not_chunk_count(self):
        half = QwenAsrOutcome(chunks=[], complete=False, completed_until=125.0,
                              failure_reason="语音识别模型加载失败")
        note = describe_transcription_shortfall(half, whisper_ok=False)
        self.assertIn("02:05", note["message"])
        self.assertNotIn("块", note["message"])


class TestShortfallIsSerialisable(unittest.TestCase):
    """持久化：必须是纯数据，能进收据、能写盘、刷新后重建同一句话。

    **第一批栽过一次**：「有几块没挂上标点」只塞进识别阶段 35% 的进度文案，
    任务一跑完就被后续消息覆盖，**用户刷新页面就再也看不到**。
    """

    def test_note_is_plain_json_data(self):
        import json
        half = QwenAsrOutcome(chunks=[], complete=False, completed_until=60.0,
                              failure_reason="语音识别模型加载失败")
        note = describe_transcription_shortfall(half, whisper_ok=False)
        json.dumps(note, ensure_ascii=False)   # 不可序列化就会在这里炸


if __name__ == "__main__":
    unittest.main()
