"""G2 · B9 + B10：副引擎参照文本改成「整篇纯文本 + 一个可用性判定」，在转录阶段一次算好。

P425 的病：参照文本按时间重叠拼接（`_backfill_qwen_primary_whisper_text`），
主副两个引擎的切句边界对不上，拼出来的参照跟本段说的不是同一句话。
实测精准模式 134 组里 76 组被判「冲突」，其中 68% 是纯边界错位——
同样的词、不同切法。模型两个窗口都回 `{"corrections": []}`，**它判对了，不是失职**。

改法：副引擎不再按时间切，整篇转录原样交给 AI（合同「乙」）。
两个新字段挂在收据上，转录阶段填好，润色阶段只读不算。

**B10 是 B1 的前置**：判「能不能用副引擎」原来是拿 `seg.whisper_text` 伪造
参照段再判（`worker.py:962-973`）。B1 一删那个字段，伪造的列表恒为空，
`_choose_refiner_mode` 就恒返回 single——**不报错、不崩溃，双引擎静默关闭**。
所以判定必须先搬到转录阶段。
"""
import sys
from pathlib import Path

from test import locked_decision
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from types import SimpleNamespace

from backend.transcriber_v2 import (
    Segment,
    TranscriptionEnvelope,
    TranscriptionReceipt,
    _build_reference_fields,
)
from backend.worker import (
    _choose_refiner_mode,
    _read_transcription_envelope,
    _transcription_envelope_payload,
)


def _ws(text, start, end):
    """一个副引擎（Whisper）段落。"""
    return SimpleNamespace(text=text, start=start, end=end, words=[])


def _receipt(**overrides):
    payload = dict(
        routed_primary="qwen",
        actual_primary="qwen",
        # G11-B：Qwen 出词流的收据必须带实收语言，否则缓存复用会拒绝它。
        engine_received_language={"qwen": "en"},
        word_source="qwen_word_timestamps",
        source_text="hello world",
        source_words=[
            {"text": "hello", "start": 0.0, "end": 0.5},
            {"text": "world", "start": 0.5, "end": 1.0},
        ],
    )
    payload.update(overrides)
    return TranscriptionReceipt(**payload,
               language_decision=locked_decision(),
        artifact_language_check="ok",
           )


class TestB9ReceiptCarriesReferenceText(unittest.TestCase):
    def test_receipt_carries_reference_text(self):
        receipt = _receipt(
            reference_text="hello world how are you",
            reference_usable=True,
        )

        self.assertEqual(receipt.reference_text, "hello world how are you")
        self.assertTrue(receipt.reference_usable)

    def test_reference_fields_default_to_empty_and_unusable(self):
        """没有副引擎时，字段必须存在且是「没有、不可用」，不能是 AttributeError。"""
        receipt = _receipt()

        self.assertEqual(receipt.reference_text, "")
        self.assertFalse(receipt.reference_usable)

    def test_reference_text_does_not_change_source_fingerprint(self):
        """参照文本是副引擎的东西，不属于「主引擎收据」的身份。

        把它算进指纹会让所有既有转录存档指纹不符 → 直接抛异常，
        比重新转录更糟。
        """
        without = _receipt()
        with_reference = _receipt(reference_text="a b c", reference_usable=True)

        self.assertEqual(
            without.source_fingerprint, with_reference.source_fingerprint
        )


class TestB9ReferenceTextSurvivesCache(unittest.TestCase):
    def test_reference_text_survives_cache_roundtrip(self):
        """resume 从 transcript.json 读回时，参照文本必须还在。

        丢了 → 重新拖入之后润色悄悄退化成单引擎。
        """
        segment = Segment(0, 0.0, 1.0, "hello world")
        receipt = _receipt(
            reference_text="hello world how are you",
            reference_usable=True,
        )
        envelope = TranscriptionEnvelope([segment], receipt)

        payload = _transcription_envelope_payload(envelope)
        restored = _read_transcription_envelope(payload)

        self.assertIsNotNone(restored)
        self.assertEqual(
            restored.receipt.reference_text, "hello world how are you"
        )
        self.assertTrue(restored.receipt.reference_usable)


class TestB10ReferenceUsableComputedAtTranscribe(unittest.TestCase):
    def test_reference_usable_computed_at_transcribe(self):
        """判定用**完整的 whisper_segments**，不是伪造出来的参照段。"""
        whisper_segments = [
            _ws("hello world", 0.0, 2.0),
            _ws("how are you today", 2.0, 4.0),
            _ws("the weather is fine", 4.0, 6.0),
        ]

        text, usable = _build_reference_fields(whisper_segments, "en")

        self.assertEqual(text, "hello world how are you today the weather is fine")
        self.assertTrue(usable)

    def test_no_secondary_engine_yields_empty_and_unusable(self):
        text, usable = _build_reference_fields([], "en")

        self.assertEqual(text, "")
        self.assertFalse(usable)

    def test_unusable_secondary_engine_still_reports_its_text(self):
        """副引擎跑飞了（复读）→ 判不可用，但文本照样带回来。

        「有没有文本」和「能不能用」是两件事，合成一个字段以后就分不开了。
        """
        whisper_segments = [_ws("thanks for watching", i, i + 1.0) for i in range(10)]

        text, usable = _build_reference_fields(whisper_segments, "en")

        self.assertTrue(text)
        self.assertFalse(usable)


class TestB1ModeReadsReceiptNotSegmentField(unittest.TestCase):
    def test_refiner_mode_uses_receipt_not_segment_field(self):
        """B1 的安全网：所有 seg.whisper_text 都空，只要收据说能用，就得是 dual。

        这条测试红了 = B1 删代码会静默关掉双引擎。
        """
        receipt = _receipt(
            reference_text="hello world how are you",
            reference_usable=True,
        )

        self.assertEqual(_choose_refiner_mode(receipt), "dual")

    def test_mode_is_single_when_receipt_says_unusable(self):
        receipt = _receipt(reference_text="hello world", reference_usable=False)

        self.assertEqual(_choose_refiner_mode(receipt), "single")

    def test_mode_is_single_when_reference_text_is_empty(self):
        """判定说能用、文本却是空的 → 仍然只能单引擎，B 块没东西可发。"""
        receipt = _receipt(reference_text="", reference_usable=True)

        self.assertEqual(_choose_refiner_mode(receipt), "single")


class _RecordingLLM:
    """记下每次调用的提示词，永远回「无需修改」。"""

    def __init__(self):
        self.calls = []

    def translate_raw_json(self, user_prompt, system_prompt=""):
        self.calls.append((user_prompt, system_prompt))
        return '{"corrections": []}'


class _Seg:
    def __init__(self, id, text, start=0.0, end=1.0):
        self.id = id
        self.text = text
        self.start = start
        self.end = end
        self.uncertain = True


class TestB10ReceiptReachesRunRefiner(unittest.TestCase):
    def test_run_refiner_goes_dual_when_receipt_says_usable(self):
        """收据说能用 → 两趟（冲突裁决 + 通顺润色）。

        这条测的是「收据真的传到了 `_run_refiner` 里」，不只是
        `_choose_refiner_mode` 自己算得对。断了这根线，同样是静默退化成单引擎。
        """
        from backend.worker import _run_refiner

        llm = _RecordingLLM()
        receipt = _receipt(reference_text="hello world", reference_usable=True)

        stats = _run_refiner([_Seg(1, "hello world")], llm, receipt=receipt)

        self.assertEqual(stats["mode"], "dual")
        self.assertEqual(len(llm.calls), 2)

    def test_run_refiner_goes_single_without_receipt(self):
        from backend.worker import _run_refiner

        llm = _RecordingLLM()

        stats = _run_refiner([_Seg(1, "hello world")], llm, receipt=None)

        self.assertEqual(stats["mode"], "single")
        self.assertEqual(len(llm.calls), 1)


class TestB1TimeOverlapConcatenationDeleted(unittest.TestCase):
    def test_backfill_function_is_gone(self):
        """按时间重叠拼参照文本的函数必须整个消失，不是留着不调用。

        留着 = 下一个人以为它还是设计的一部分，某次重构又把它接回去。
        """
        import backend.transcriber_v2 as t

        self.assertFalse(hasattr(t, "_backfill_qwen_primary_whisper_text"))

    def test_qwen_primary_assembly_leaves_no_reference_on_segments(self):
        """主引擎组装出来的段，不再挂按时间拼来的参照文本。"""
        from backend.transcriber_v2 import _assemble_qwen_primary

        qwen_words = [
            {"text": "hello", "start": 0.0, "end": 0.5},
            {"text": "world", "start": 0.5, "end": 1.0},
        ]
        whisper_segments = [_ws("hello word", 0.0, 1.0)]

        segments = _assemble_qwen_primary(qwen_words, whisper_segments, "en")

        self.assertTrue(segments)
        for segment in segments:
            self.assertFalse(getattr(segment, "whisper_text", ""))


class TestB2GroupLevelConcatenationDeleted(unittest.TestCase):
    def test_group_has_no_whisper_text_attr(self):
        """组层面的二次拼接（段的参照再按组拼一遍）必须删掉。

        它的唯一消费者是润色的 [CONFLICT] 分支，B3 删掉之后它就是死代码。
        """
        from backend.core.sentence_groups import _attach_segment_metadata

        group = SimpleNamespace(
            start=0.0, end=2.0, speaker_change=False,
            uncertain=False, sensevoice_tag=None,
        )
        segment = SimpleNamespace(
            start=0.0, end=2.0, speaker_change=False,
            uncertain=False, sensevoice_tag=None, whisper_text="hello word",
        )

        _attach_segment_metadata([group], [segment])

        self.assertFalse(hasattr(group, "whisper_text"))

    def test_other_metadata_still_attached(self):
        """B2 只删参照文本那一段，说话人/存疑/音频标签照旧挂上去。"""
        from backend.core.sentence_groups import _attach_segment_metadata

        group = SimpleNamespace(start=0.0, end=2.0)
        segment = SimpleNamespace(
            start=0.0, end=2.0, speaker_change=True,
            uncertain=True, sensevoice_tag="Laughter",
        )

        _attach_segment_metadata([group], [segment])

        self.assertTrue(group.speaker_change)
        self.assertTrue(group.uncertain)
        self.assertEqual(group.sensevoice_tag, "Laughter")


if __name__ == "__main__":
    unittest.main()
