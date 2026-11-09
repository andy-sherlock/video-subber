"""P352: LLM 批量翻译不能把整批译文塞进第一条。

覆盖点：
1. 逐段 LLM 批量翻译必须用编号对账，保存时不能残留编号。
2. 横杠旧协议、少编号、多编号、重复编号、内部解释文字都必须整批失败。
3. orchestrator 不能把污染译文当成功，必须允许后端链路继续补。
4. 2 秒字幕不能被拆成几十条几十毫秒字幕。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.translation.base import LLMBackend


class FakeLLMBackend(LLMBackend):
    def __init__(self, raw: str, name: str = "fake-llm"):
        self.raw = raw
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def _api_url(self) -> str:
        return "https://example.invalid"

    def _build_headers(self) -> dict:
        return {}

    def _call_api_parse(self, response_data: dict) -> str:
        return self.raw

    def _call_api_raw(self, prompt: str, max_tokens: int = 8192) -> tuple[str, dict]:
        self.last_prompt = prompt
        return self.raw, {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}


class FakeSegment:
    def __init__(self, id=0, start=0.0, end=2.0, text="hello"):
        self.id = id
        self.start = start
        self.end = end
        self.text = text
        self.zh = ""
        self.speaker_change = False
        self.uncertain = False
        self.words = []


class DummyCheckpoint:
    def __init__(self):
        self.saved = []

    def save(self, stage, meta=None):
        self.saved.append((stage, meta))


class TestP352NumberedBatchProtocol(unittest.TestCase):
    def _translate(self, raw, texts=None):
        texts = texts or ["one", "two", "three"]
        return FakeLLMBackend(raw).translate_batch(texts).translated

    def test_numbered_result_is_saved_without_numbers(self):
        result = self._translate("[0] 甲\n[1] 乙\n[2] 丙")

        self.assertEqual(result, ["甲", "乙", "丙"])

    def test_old_dash_batch_pollution_fails_whole_batch(self):
        result = self._translate("甲  \n---  \n乙  \n---  \n丙")

        self.assertEqual(result, ["[翻译失败]", "[翻译失败]", "[翻译失败]"])

    def test_missing_number_fails_whole_batch(self):
        result = self._translate("[0] 甲\n[1] 乙")

        self.assertEqual(result, ["[翻译失败]", "[翻译失败]", "[翻译失败]"])

    def test_extra_number_fails_whole_batch(self):
        result = self._translate("[0] 甲\n[1] 乙\n[2] 丙\n[3] 丁")

        self.assertEqual(result, ["[翻译失败]", "[翻译失败]", "[翻译失败]"])

    def test_duplicate_number_fails_whole_batch(self):
        result = self._translate("[0] 甲\n[1] 乙\n[1] 重复")

        self.assertEqual(result, ["[翻译失败]", "[翻译失败]", "[翻译失败]"])

    def test_dash_residue_inside_translation_fails_whole_batch(self):
        result = self._translate("[0] 甲\n[1] 乙 --- 丙\n[2] 丁")

        self.assertEqual(result, ["[翻译失败]", "[翻译失败]", "[翻译失败]"])

    def test_natural_lines_with_more_natural_or_more_fitting_are_accepted(self):
        result = self._translate("[0] 这样说更自然\n[1] 更符合他的性格\n[2] 普通台词")

        self.assertEqual(result, ["这样说更自然", "更符合他的性格", "普通台词"])

    def test_natural_line_with_more_natural_and_more_fitting_is_accepted(self):
        result = self._translate(
            "[0] 这样翻译更自然，也更符合语境。\n[1] 普通台词",
            texts=["one", "two"],
        )

        self.assertEqual(result, ["这样翻译更自然，也更符合语境。", "普通台词"])

    def test_single_bracket_number_in_normal_subtitle_is_accepted(self):
        result = self._translate("[0] 游戏提示：[1] 按键\n[1] 看屏幕上的[1]\n[2] 普通台词")

        self.assertEqual(result, ["游戏提示：[1] 按键", "看屏幕上的[1]", "普通台词"])

    def test_multiple_normal_screen_numbers_are_accepted(self):
        result = self._translate("[0] 屏幕选项 [1] 和 [2] 都亮了\n[1] 普通台词\n[2] 结束")

        self.assertEqual(result, ["屏幕选项 [1] 和 [2] 都亮了", "普通台词", "结束"])

    def test_structured_number_residue_inside_translation_fails_whole_batch(self):
        result = self._translate("[0] 甲 [0] 残留 [1] 残留\n[1] 乙\n[2] 丙")

        self.assertEqual(result, ["[翻译失败]", "[翻译失败]", "[翻译失败]"])

    def test_internal_explanation_fails_whole_batch(self):
        result = self._translate("[0] 甲\n原因：这里选择更自然说法\n[1] 乙\n[2] 丙")

        self.assertEqual(result, ["[翻译失败]", "[翻译失败]", "[翻译失败]"])

    def test_hard_review_markers_still_fail_whole_batch(self):
        for raw in (
            "[0] 版本A：这句更顺\n[1] 乙",
            "[0] 我选A\n[1] 乙",
            "[0] 选择B 更好\n[1] 乙",
            "[0] 字幕质量审查员：这句需要调整\n[1] 乙",
            "[0] 建议：改成更口语\n[1] 乙",
            "[0] 解释：这里是审查说明\n[1] 乙",
        ):
            with self.subTest(raw=raw):
                result = self._translate(raw, texts=["one", "two"])

                self.assertEqual(result, ["[翻译失败]", "[翻译失败]"])

    def test_empty_input_preserves_empty_slots(self):
        result = self._translate("[0] 甲\n[1] 丙", texts=["one", "", "three"])

        self.assertEqual(result, ["甲", "", "丙"])


class TestP352SentenceSplitterGuard(unittest.TestCase):
    def test_two_second_segment_is_not_split_into_dozen_tiny_subtitles(self):
        from backend.core.sentence_splitter import _build_split_segments

        seg = FakeSegment(id=7, start=6.38, end=8.66, text="source")
        parts = [f"第{i}句" for i in range(60)]
        # G12-B 收尾补充（P475）：正文一律取片段自己的译文，分片只提供断开位置；
        # 生产里只有译文够长的片段才会走到这里，假片段要带上分片来自的那条译文。
        seg.zh = "".join(parts)

        result = _build_split_segments(seg, parts)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].start, 6.38)
        self.assertEqual(result[0].end, 8.66)
        self.assertIn("第0句", result[0].zh)
        self.assertIn("第59句", result[0].zh)


class TestP352WorkerCachePollutionGate(unittest.TestCase):
    def test_old_dirty_cache_is_detected_but_natural_cache_is_not(self):
        from backend.worker import looks_like_batch_pollution

        self.assertTrue(looks_like_batch_pollution([
            {"zh": "甲\n---\n乙\n---\n丙"},
        ]))
        self.assertTrue(looks_like_batch_pollution([
            {"zh": "[0] 甲\n[1] 乙"},
        ]))
        self.assertFalse(looks_like_batch_pollution([
            {"zh": "这样说更自然"},
            {"zh": "游戏提示：[1] 按键"},
        ]))

    def test_polluted_cache_at_subtitle_stage_retranslates_instead_of_reusing_dirty_text(self):
        from backend import worker

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            translated_path = tmp / "translated.json"
            translated_path.write_text(json.dumps([
                {"id": 0, "start": 0.0, "end": 1.0, "text": "one", "zh": "[0] 旧脏缓存\n[1] 也在旧缓存"},
                {"id": 1, "start": 1.0, "end": 2.0, "text": "two", "zh": "正常旧缓存"},
            ], ensure_ascii=False), encoding="utf-8")
            segments = [
                FakeSegment(id=0, start=0.0, end=1.0, text="one"),
                FakeSegment(id=1, start=1.0, end=2.0, text="two"),
            ]

            with patch("backend.translation.orchestrator.translate_with_orchestrator",
                       return_value=(["新译文0", "新译文1"], [], None, {})) as translate:
                failed, sub_splits, ok_count = worker._step_translate(
                    "subtitle",
                    segments,
                    translated_path,
                    [],
                    False,
                    {},
                    tmp,
                    len(segments),
                    DummyCheckpoint(),
                )

        translate.assert_called_once()
        self.assertEqual([s.zh for s in segments], ["新译文0", "新译文1"])
        self.assertEqual(failed, [])
        self.assertEqual(sub_splits, {})
        self.assertEqual(ok_count, 2)

    def test_corrupt_translated_json_retranslates_instead_of_crashing(self):
        from backend import worker

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            translated_path = tmp / "translated.json"
            translated_path.write_text("{not valid json", encoding="utf-8")
            segments = [
                FakeSegment(id=0, start=0.0, end=1.0, text="one"),
                FakeSegment(id=1, start=1.0, end=2.0, text="two"),
            ]

            with patch("backend.translation.orchestrator.translate_with_orchestrator",
                       return_value=(["新译文0", "新译文1"], [], None, {})) as translate:
                failed, sub_splits, ok_count = worker._step_translate(
                    "subtitle",
                    segments,
                    translated_path,
                    [],
                    False,
                    {},
                    tmp,
                    len(segments),
                    DummyCheckpoint(),
                )

        translate.assert_called_once()
        self.assertEqual([s.zh for s in segments], ["新译文0", "新译文1"])
        self.assertEqual(failed, [])
        self.assertEqual(sub_splits, {})
        self.assertEqual(ok_count, 2)

    def test_missing_translated_json_at_subtitle_stage_retranslates_instead_of_crashing(self):
        from backend import worker

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            translated_path = tmp / "translated.json"
            segments = [
                FakeSegment(id=0, start=0.0, end=1.0, text="one"),
                FakeSegment(id=1, start=1.0, end=2.0, text="two"),
            ]

            with patch("backend.translation.orchestrator.translate_with_orchestrator",
                       return_value=(["新译文0", "新译文1"], [], None, {})) as translate:
                failed, sub_splits, ok_count = worker._step_translate(
                    "subtitle",
                    segments,
                    translated_path,
                    [],
                    False,
                    {},
                    tmp,
                    len(segments),
                    DummyCheckpoint(),
                )

            translate.assert_called_once()
            self.assertEqual([s.zh for s in segments], ["新译文0", "新译文1"])
            self.assertTrue(translated_path.exists())
            self.assertEqual(failed, [])
            self.assertEqual(sub_splits, {})
            self.assertEqual(ok_count, 2)


if __name__ == "__main__":
    unittest.main()
