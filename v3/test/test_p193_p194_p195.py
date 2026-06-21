"""回归测试：P193 L2 Pass 1 删除 / P194 错误消息 / P195 静默异常

Run: cd v2 && .venv/bin/python -m pytest test/test_p193_p194_p195.py -v
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest


class FakeSegment:
    def __init__(self, id=0, start=0.0, end=1.0, text="",
                 sensevoice_tag=None, qwen_text=None):
        self.id = id
        self.start = start
        self.end = end
        self.text = text
        self.sensevoice_tag = sensevoice_tag
        self.qwen_text = qwen_text
        self.filtered = False
        self.filter_reason = None
        self.uncertain = False
        self.words = []


# ── P193: L2 不应因 SV 音频标签而标记 uncertain ──

class TestP193_L2NoSenseVoiceCrossLayer(unittest.TestCase):

    def test_bgm_tag_with_qwen_text_not_marked_uncertain(self):
        """SV 说 BGM 但 Qwen 听到了文字 → L2 不应标 uncertain。"""
        from backend.core.hallucination_filter import _layer2_cross_validate

        segs = [
            FakeSegment(id=0, start=0.0, end=5.0,
                        text="Color is darling and beautiful",
                        sensevoice_tag="BGM",
                        qwen_text="Color is darling and beautiful"),
            FakeSegment(id=1, start=5.0, end=10.0,
                        text="The sky is very blue today",
                        sensevoice_tag="Speech",
                        qwen_text="The sky is very blue today"),
        ]
        result, _ = _layer2_cross_validate(segs, "en")
        seg0 = result[0]
        self.assertFalse(seg0.filtered,
                         "L2 不应因 SV 标签 BGM 过滤有 Qwen 文本的段")
        self.assertFalse(seg0.uncertain,
                         "L2 不应因 SV 标签 BGM 标记 uncertain")


# ── P194: 错误消息应包含退出码 ──

class TestP194_ErrorMessageExitCode(unittest.TestCase):

    def test_error_message_contains_exit_code(self):
        from unittest.mock import MagicMock, patch, AsyncMock
        from backend.worker_manager import WorkerManager
        import asyncio

        wm = WorkerManager()
        mock_proc = MagicMock()
        mock_proc.returncode = 42
        mock_proc.stdout = iter([])
        mock_proc.wait.return_value = None

        captured_msgs = []

        loop = asyncio.new_event_loop()
        wm._main_loop = loop
        wm._job_dirs = {"test-job": None}

        original_forward = wm._forward
        async def capture_forward(job_id, msg):
            captured_msgs.append(msg)
        wm._forward = capture_forward

        # 让 loop 在另一个线程跑以处理 coroutine
        import threading
        def run_loop():
            loop.run_forever()
        t = threading.Thread(target=run_loop, daemon=True)
        t.start()

        try:
            wm._read_stdout("test-job", mock_proc)
            import time; time.sleep(0.1)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)
            loop.close()

        self.assertTrue(len(captured_msgs) > 0, "应发送错误消息")
        msg_text = captured_msgs[0].get("message", "")
        self.assertNotIn("内存不足", msg_text,
                         "不应猜测 OOM 原因")
        self.assertIn("42", msg_text,
                       "错误消息应包含退出码")


# ── P195: 翻译引擎加载失败应写日志 ──

class TestP195_LLMBackendWarning(unittest.TestCase):

    def test_import_error_logs_warning(self):
        """已知平台但 import 失败 → 应写 warning。"""
        from unittest.mock import patch
        from backend.translation.orchestrator import _build_llm_backends

        api_keys = [{"platform": "claude", "key": "test-key"}]
        with patch.dict("sys.modules", {"backend.translation.claude": None}):
            with self.assertLogs("backend.translation.orchestrator",
                                 level="WARNING") as cm:
                result = _build_llm_backends(api_keys)
            self.assertEqual(result, [])
            self.assertTrue(
                any("skipped" in msg for msg in cm.output),
                f"ImportError 应产生含 'skipped' 的 warning，实际: {cm.output}")

    def test_construction_exception_logs_warning(self):
        """import 成功但构造函数抛异常 → 应写 warning。"""
        from unittest.mock import patch, MagicMock
        from backend.translation.orchestrator import _build_llm_backends

        fake_module = MagicMock()
        fake_module.DeepSeekBackend = MagicMock(
            side_effect=RuntimeError("模拟构造失败"))

        api_keys = [{"platform": "deepseek", "key": "test-key"}]
        with patch.dict("sys.modules",
                        {"backend.translation.deepseek": fake_module}):
            with patch("backend.translation.deepseek.DeepSeekBackend",
                       side_effect=RuntimeError("模拟构造失败"),
                       create=True):
                with self.assertLogs("backend.translation.orchestrator",
                                     level="WARNING") as cm:
                    result = _build_llm_backends(api_keys)
                self.assertEqual(result, [])
                self.assertTrue(
                    any("skipped" in msg for msg in cm.output),
                    f"构造异常应产生含 'skipped' 的 warning，实际: {cm.output}")


if __name__ == "__main__":
    unittest.main()
