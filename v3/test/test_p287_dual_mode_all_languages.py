"""P287（G2 改写）：非英语语言也必须能进双引擎润色。

**原来这个文件叫 `test_p287_whisper_text_backfill.py`**，锁的是
`_assemble_qwen_primary` 会给每一段回填 `whisper_text`（按时间重叠拼接）。
G2/B1 把那套拼接整个删了——主副两个引擎的切句边界本来就对不齐，拼出来的
参照跟本段说的不是同一句（实测 134 组里 76 组判「冲突」，68% 是纯边界错位）。

**但 P287 真正在护的那件事没变，也不能随文件一起丢**：英语走的是
`_assemble_qwen_on_whisper_boundaries`，日/中/韩走 `_assemble_qwen_primary`，
当年正是后者不回填导致**非英语一律退回单引擎**。所以这里逐条交代接管关系：

| 原测试 | 谁接管 |
|---|---|
| `test_{ja,zh,ko}_qwen_primary_has_whisper_text` | 本文件：判定与语言无关 |
| `test_multi_segment_whisper_overlap` | **无接管**——它锁的就是被删掉的那个 bug 本身 |
| `test_no{,ne}_whisper_segments_no_crash` | 本文件 + `test_g2_p425_reference_text.py::test_no_secondary_engine_yields_empty_and_unusable` |
| `test_refiner_mode_becomes_dual_with_backfill` | `test_g2_p425_reference_text.py::test_refiner_mode_uses_receipt_not_segment_field` |
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from types import SimpleNamespace


def _make_whisper_seg(start, end, text):
    return SimpleNamespace(start=start, end=end, text=text, sensevoice_tag=None)


class TestP287DualModeIsLanguageIndependent(unittest.TestCase):
    """判定改到收据上之后，语言不再影响能不能用双引擎。

    旧设计里判定依赖「组装函数有没有回填 whisper_text」，而组装函数按语言分叉
    ——这正是 P287 当年的病根。新设计在转录阶段一次判定，与组装路径无关。
    """

    def test_dual_mode_available_for_every_language(self):
        from backend.transcriber_v2 import _build_reference_fields
        from backend.worker import _choose_refiner_mode

        cases = {
            "ja": ([_make_whisper_seg(0.0, 1.3, "今日は天気がいい")],
                   "今日は天気がいい"),
            "zh": ([_make_whisper_seg(0.0, 1.3, "今天天气很好")], "今天天气很好"),
            "ko": ([_make_whisper_seg(0.0, 1.3, "오늘 날씨가 좋다")],
                   "오늘 날씨가 좋다"),
            "en": ([_make_whisper_seg(0.0, 1.3, "the weather is fine")],
                   "the weather is fine"),
        }

        for lang, (whisper_segments, expected_text) in cases.items():
            with self.subTest(lang=lang):
                text, usable = _build_reference_fields(whisper_segments, lang)

                self.assertEqual(text, expected_text)
                self.assertTrue(usable)
                receipt = SimpleNamespace(
                    reference_text=text, reference_usable=usable
                )
                self.assertEqual(_choose_refiner_mode(receipt), "dual")

    def test_cjk_reference_text_is_joined_without_spaces(self):
        """中日韩不加空格拼接——加了空格模型会读成断开的词。"""
        from backend.transcriber_v2 import _build_reference_fields

        whisper_segments = [
            _make_whisper_seg(0.0, 0.5, "今日は"),
            _make_whisper_seg(0.5, 1.3, "天気がいい"),
        ]

        text, _usable = _build_reference_fields(whisper_segments, "ja")

        self.assertEqual(text, "今日は天気がいい")


class TestP287QwenPrimaryAssemblyStillSafe(unittest.TestCase):
    """组装函数在没有副引擎时不能崩——标准模式天天走这条路。"""

    def test_no_whisper_segments_no_crash(self):
        from backend.transcriber_v2 import _assemble_qwen_primary

        result = _assemble_qwen_primary(
            [{"text": "テスト", "start": 0.0, "end": 0.5}], [], "ja"
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].text, "テスト")

    def test_none_whisper_segments_no_crash(self):
        from backend.transcriber_v2 import _assemble_qwen_primary

        result = _assemble_qwen_primary(
            [{"text": "テスト", "start": 0.0, "end": 0.5}], None, "ja"
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].text, "テスト")


if __name__ == "__main__":
    unittest.main()
