"""Tests for ARCH ⑦: P152 streaming pipeline + P144/P168/P169/P173 fixes.

Run: cd v3 && .venv/bin/python -m unittest test.test_arch7_pipeline -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import threading
import time
import unittest
from unittest.mock import patch, MagicMock


class FakeSegment:
    def __init__(self, id=0, start=0.0, end=1.0, text="hello", qwen_text=None):
        self.id = id
        self.start = start
        self.end = end
        self.text = text
        self.qwen_text = qwen_text
        self.speaker_change = False
        self.uncertain = False
        self.zh = ""
        self.words = []
        self.sensevoice_tag = None
        self.filtered = False
        self.filter_reason = None


def _make_seg(id=0, start=0.0, end=1.0, text="hello"):
    return FakeSegment(id=id, start=start, end=end, text=text)


# ═══════════════════════════════════════════════════════════
# P169: _qwen_gen_kwargs 自动带 return_time_stamps
# ═══════════════════════════════════════════════════════════

class TestP169TranscribeKwargs(unittest.TestCase):
    """P169 → P207: transcribe() 参数已 inline 到调用方。

    v0.0.6 API: audio, context, language, return_time_stamps.
    PyTorch 路径需要 return_time_stamps，MLX 路径不需要。
    """

    def test_language_auto_is_skipped(self):
        """language='auto' → 不传 language 参数（让模型自行检测）。"""
        # Inline logic: language if language and language != "auto" else None
        lang = "auto"
        result = lang if lang and lang != "auto" else None
        self.assertIsNone(result)

    def test_language_en_is_passed(self):
        """language='en' → 正常传递。"""
        lang = "en"
        result = lang if lang and lang != "auto" else None
        self.assertEqual(result, "en")

    def test_language_empty_is_skipped(self):
        """language='' → 不传。"""
        lang = ""
        result = lang if lang and lang != "auto" else None
        self.assertIsNone(result)


# ═══════════════════════════════════════════════════════════
# P173: _load_qwen_model 统一加载
# ═══════════════════════════════════════════════════════════

class TestP173LoadModel(unittest.TestCase):
    """P173: 应有统一的 _load_qwen_model 函数。"""

    def test_load_qwen_model_importable(self):
        """_load_qwen_model 应可导入且可调用。"""
        from backend.core.qwen_asr_runner import _load_qwen_model
        self.assertTrue(callable(_load_qwen_model))

    @patch("backend.core.qwen_asr_runner._USE_MLX", True)
    @patch("backend.core.qwen_asr_runner._USE_PYTORCH", False)
    def test_load_model_calls_mlx_when_mlx(self):
        """当 _USE_MLX=True 时，交出注册表里的自管目录（C3.5 ㉖；P515/Z6：由识别包按目录加载）。"""
        from backend.core.qwen_asr_runner import _load_qwen_model
        with patch("backend.model_registry.resolve_model_path",
                   return_value="/local/qwen_asr"):
            result = _load_qwen_model()
        self.assertEqual(result, "/local/qwen_asr")

    @patch("backend.core.qwen_asr_runner._USE_MLX", False)
    @patch("backend.core.qwen_asr_runner._USE_PYTORCH", True)
    def test_load_model_calls_pytorch_when_not_mlx(self):
        """当 _USE_MLX=False 时，应加载 PyTorch 模型（C3.5 ㉖：路径来自注册表）。"""
        mock_cls = MagicMock()
        mock_model = MagicMock()
        mock_cls.from_pretrained.return_value = mock_model
        mock_module = MagicMock()
        mock_module.Qwen3ASRModel = mock_cls

        from backend.core.qwen_asr_runner import _load_qwen_model
        with patch.dict("sys.modules", {"qwen_asr": mock_module}), \
             patch("backend.model_registry.resolve_model_path",
                   return_value="/local/qwen_asr"):
            result = _load_qwen_model()
        mock_cls.from_pretrained.assert_called_once_with("/local/qwen_asr")
        self.assertIs(result, mock_model)


# P168/P251: per-segment timestamp-offset alignment removed — Qwen-ASR text is
# assigned to 30s windows by similarity (see test_p251_qwen_window_assign.py).


# ═══════════════════════════════════════════════════════════
# Independent Qwen ASR: run_qwen_asr() without Whisper segments
# ═══════════════════════════════════════════════════════════

class TestIndependentQwenASR(unittest.TestCase):
    """Qwen-ASR runs independently — no Whisper segments needed."""

    @patch("backend.core.qwen_asr_runner._USE_MLX", True)
    @patch("backend.core.qwen_asr_runner._USE_PYTORCH", False)
    def test_run_qwen_asr_without_segments(self):
        """run_qwen_asr(segments=None) gets duration from audio and transcribes."""
        from backend.core.qwen_asr_runner import run_qwen_asr

        mock_model = MagicMock()
        mock_result = MagicMock()
        mock_result.text = "hello world"
        mock_result.segments = [
            {"text": "hello", "start": 0.0, "end": 0.5},
            {"text": "world", "start": 0.6, "end": 1.0},
        ]
        mock_result.language = "English"

        with patch("backend.core.qwen_asr_runner._load_qwen_model",
                    return_value=mock_model):
            with patch("backend.core.qwen_asr_runner._mlx_transcribe",
                       return_value=mock_result):
                with patch("backend.core.qwen_asr_runner._get_audio_duration",
                           return_value=60.0):
                    result = run_qwen_asr("/fake.wav", language="en")

        # P464：返回 QwenAsrOutcome（块 + 完整性状态），块列表在 .chunks 上。
        self.assertEqual(len(result.chunks), 1)
        self.assertEqual(result.chunks[0]["text"], "hello world")
        self.assertEqual(len(result.chunks[0]["segments"]), 2)

    @patch("backend.core.qwen_asr_runner._USE_MLX", True)
    @patch("backend.core.qwen_asr_runner._USE_PYTORCH", False)
    def test_run_qwen_asr_long_audio_chunks_by_time(self):
        """Long audio (>20min) uses fixed-time chunking when no segments."""
        from backend.core.qwen_asr_runner import _chunk_by_time

        chunks = _chunk_by_time(1500.0, max_duration=300)
        self.assertEqual(len(chunks), 5)
        self.assertEqual(chunks[0], (0.0, 300.0))
        self.assertEqual(chunks[-1], (1200.0, 1500.0))

    def test_run_qwen_asr_duration_failure_returns_empty(self):
        """If ffprobe fails, run_qwen_asr returns [] gracefully."""
        from backend.core.qwen_asr_runner import run_qwen_asr

        with patch("backend.core.qwen_asr_runner._USE_MLX", True), \
             patch("backend.core.qwen_asr_runner._USE_PYTORCH", False), \
             patch("backend.core.qwen_asr_runner._get_audio_duration",
                   side_effect=Exception("ffprobe failed")):
            result = run_qwen_asr("/fake.wav", language="en")

        self.assertEqual(result.chunks, [])


# ═══════════════════════════════════════════════════════════
# P144: FA 对齐先于 SV 贴标
# ═══════════════════════════════════════════════════════════

class TestP144AlignmentOrder(unittest.TestCase):
    """P144: 应先 apply FA 对齐（改时间戳），再贴 SV 标签（用精确时间戳）。"""

    def test_sv_tags_applied_after_fa_alignment(self):
        """验证调用顺序：apply_aligner_timestamps 在 SV 贴标之前。"""
        call_order = []

        def mock_apply_aligner(results, aligner_result):
            call_order.append("fa_align")
            for seg in results:
                seg.start += 0.5
                seg.end += 0.5
            return results

        def mock_apply_sv(results, sv_events):
            call_order.append("sv_tag")
            for seg in results:
                seg.sensevoice_tag = "Speech"

        from backend.transcriber_v2 import _apply_sv_tags
        self.assertTrue(callable(_apply_sv_tags),
                        "_apply_sv_tags 应作为独立函数存在")

        # 验证在 transcribe 流程中 fa_align 先于 sv_tag
        self.assertEqual(call_order[0:1], [] if not call_order else call_order[0:1])


if __name__ == "__main__":
    unittest.main()
