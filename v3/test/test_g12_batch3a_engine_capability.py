"""G12 第三批 A · P463：Windows 上主引擎交空字幕，而且零提示。

**用户看到的**：Windows 用户转 20 分钟以上的视频，拿到一个**空字幕文件**，
没有任何错误提示。

**必然路径**（不是边缘情况）：

    语言识别 → en，而 QWEN_PRIMARY_LANGS = {en, ja, ko, zh}
      ↓ choose_standard_primary_engine 只问语言，从不问平台
    release_whisper_model()          ← Whisper 被卸载，没有退路了
      ↓
    Windows 走 PyTorch 分支 → raw_segments = []（代码里就是这么写的）
      ↓
    _refresh_chunk_delivery 的判据写的是 `is None`，拦不住空列表
      ↓ 正文被空词表重建成 ""
    三个返回点 `if text.strip()` 把整块滤掉 → 剩 0 块

**根因一句话**：路由拿「是什么语言」当「能不能干这活」用。
字幕 = 文字 + 时间，**给不了时间的后端不具备主引擎资格**——这跟语言无关。

**⚠️ 判据只能加在标准模式那一处 `if` 条件里，禁止加进 `is_qwen_asr_available()`。**
三个调用点是三条性质完全不同的路径：

    :1937  标准模式        Qwen 是主引擎（release_whisper_model 卸掉退路）  ← 只有这里要拦
    :2102  精准并行线程    Whisper 完整跑，Qwen 只是交叉验证的辅助          ← 拦了就是伤害
    :1368  精准串行        同上（Mac 上这是主路径，不是兜底）               ← 拦了就是伤害

加进 `is_qwen_asr_available()` 会一刀切掉三条，**Windows 精准模式静默失去交叉验证**，
而且 **Mac 上永远测不出来**（MLX 判据恒为真，三条路径行为一个字不变）。
G12 第一批栽的就是这个形状。

Run: cd v3 && .venv/bin/python -m pytest test/test_g12_batch3a_engine_capability.py -v
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest

from backend.core import qwen_asr_runner
from backend import transcriber_v2


def _decision(lang: str):
    """够用的假 LanguageDecision：路由只读 language / samples / 计数。"""
    sample = SimpleNamespace(valid=True, language=lang)
    return SimpleNamespace(language=lang, samples=[sample] * 7,
                           expected_count=7, valid_count=7)


class TestBackendCapabilityGate(unittest.TestCase):
    """T1 / A1 A4 A6：给不了词级时间的后端，四种语言全部不得当主引擎。"""

    def _route(self, lang):
        """跑一次标准模式路由，返回 (主引擎, Whisper 是否被卸载)。"""
        released = {"called": False}

        def _fake_release():
            released["called"] = True

        with mock.patch.object(transcriber_v2, "release_whisper_model", _fake_release), \
             mock.patch.object(qwen_asr_runner, "run_qwen_asr",
                               lambda *a, **k: []), \
             mock.patch.object(qwen_asr_runner, "is_qwen_asr_available",
                               lambda: True):
            primary = transcriber_v2._run_standard_language_router(
                "fake.wav", _decision(lang),
                use_qwen=True, filter_keywords=True,
                cache_dir=None, progress_callback=None, _qwen_holder={})
        return primary, released["called"]

    def test_t1_pytorch_backend_downgrades_all_four_languages(self):
        """en / ja / ko / zh 四种语言都在规则表里，PyTorch 下必须全部走 Whisper。

        **不是英语专属**——外审第二轮的更正：PyTorch 分支对所有语言都返回空词表。
        """
        with mock.patch.object(qwen_asr_runner, "_USE_MLX", False), \
             mock.patch.object(qwen_asr_runner, "_USE_PYTORCH", True):
            for lang in ("en", "ja", "ko", "zh"):
                with self.subTest(language=lang):
                    primary, _ = self._route(lang)
                    self.assertEqual(primary, "whisper",
                                     f"{lang}: PyTorch 给不了词级时间，不能当主引擎")

    def test_t1_pytorch_backend_does_not_unload_whisper(self):
        """A6：降级时 `release_whisper_model()` 绝不能执行——那是在拆退路。"""
        with mock.patch.object(qwen_asr_runner, "_USE_MLX", False), \
             mock.patch.object(qwen_asr_runner, "_USE_PYTORCH", True):
            _, released = self._route("en")
        self.assertFalse(released,
                         "降级走 Whisper 时把 Whisper 卸载了，等于两边都没有")

    def test_t4_mlx_backend_behavior_unchanged(self):
        """A5：Mac 走 MLX，能给词级时间，行为必须一个字不变。"""
        with mock.patch.object(qwen_asr_runner, "_USE_MLX", True), \
             mock.patch.object(qwen_asr_runner, "_USE_PYTORCH", False):
            primary, released = self._route("en")
        self.assertEqual(primary, "qwen", "MLX 能给词级时间，仍然当主引擎")
        self.assertTrue(released, "MLX 路径原本就会卸载 Whisper 省内存，不得改变")


class TestCapabilityGateStaysOutOfAvailability(unittest.TestCase):
    """T2 / A2：能力判据**禁止**加进 `is_qwen_asr_available()`。

    加进去会一刀切掉精准模式的两条辅助路径，
    而 Mac 上（MLX 恒为真）永远测不出来。
    """

    def test_t2_availability_unaffected_by_word_timing_capability(self):
        with mock.patch.object(qwen_asr_runner, "_USE_MLX", False), \
             mock.patch.object(qwen_asr_runner, "_USE_PYTORCH", True):
            self.assertTrue(
                qwen_asr_runner.is_qwen_asr_available(),
                "PyTorch 后端下 Qwen 仍然『可用』——它只是不能当主引擎，"
                "辅助与交叉验证的用途必须保留")

    def test_t2_precise_mode_parallel_thread_still_starts_on_pytorch(self):
        """精准模式的并行门不得被能力判据误伤。"""
        with mock.patch.object(qwen_asr_runner, "_USE_MLX", False), \
             mock.patch.object(qwen_asr_runner, "_USE_PYTORCH", True), \
             mock.patch.object(transcriber_v2, "should_parallel_asr", lambda: True):
            self.assertTrue(
                transcriber_v2._should_parallel_qwen(
                    use_qwen=True, filter_keywords=True, use_whisper=True),
                "Windows 精准模式仍要启动 Qwen 线程做交叉验证")


class TestAuxiliaryTextSurvivesEmptyWordList(unittest.TestCase):
    """T3 / A3：`raw_segments=[]` 时保留原始正文供辅助检查。

    **这条和「改路由」是一对，不能只做一半**：不改这里，
    Windows 的 Qwen 辅助文字会被空词表当场重建成 ""，
    「保留 Qwen 当辅助」就成了一句空话。
    """

    def test_t3_empty_word_list_keeps_original_text(self):
        chunk = {"t_start": 0.0, "t_end": 300.0,
                 "raw_segments": [],          # ← 真实形状。第一批的测试喂的是 None，
                 "raw_text": "hello world",   #    代码写错的地方测试原样抄了一遍
                 "text": "hello world",
                 "segments": []}

        refreshed = qwen_asr_runner._refresh_chunk_delivery([chunk], "en")

        self.assertTrue(refreshed[0]["text"].strip(),
                        "空词表不得把正文重建成空串——那正是整片字幕消失的最后一步")
        self.assertFalse(refreshed[0].get("punctuation_attached"),
                         "这块确实绕过了挂标点，必须如实记成 False")


if __name__ == "__main__":
    unittest.main()
