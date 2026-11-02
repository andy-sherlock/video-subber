"""回归测试：P193 L2 Pass 1 删除 / P194 错误消息 / P195 静默异常

Run: cd v3 && .venv/bin/python -m pytest test/test_p193_p194_p195.py -v
"""
import json
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


# ── P194: 退出码交给主服务统一收敛 ──

class TestP194_ErrorMessageExitCode(unittest.TestCase):

    def test_error_message_contains_exit_code(self):
        from unittest.mock import MagicMock
        from backend.worker_manager import WorkerManager

        wm = WorkerManager()
        mock_proc = MagicMock()
        mock_proc.returncode = 42
        mock_proc.stdout = iter([])
        mock_proc.wait.return_value = None

        exits = []
        wm.set_exit_callback(lambda *args: exits.append(args))

        wm._read_stdout("test-job", mock_proc)

        self.assertEqual(exits, [("test-job", None, mock_proc, 42, None)])


class TestWorkerManagerNoMainImport(unittest.IsolatedAsyncioTestCase):

    async def test_forward_uses_registered_callback_without_importing_main(self):
        """WorkerManager 不应反向导入 backend.main 才能转发进度。"""
        from unittest.mock import AsyncMock, patch
        import importlib.abc
        import sys
        from backend.worker_manager import WorkerManager

        class BlockBackendMain(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "backend.main":
                    raise AssertionError("worker_manager must not import backend.main")
                return None

        saved_main = sys.modules.pop("backend.main", None)
        blocker = BlockBackendMain()
        sys.meta_path.insert(0, blocker)
        try:
            wm = WorkerManager()
            captured = []
            msg = {"type": "progress", "percent": 17, "stage": "asr", "message": "running"}
            def _callback(job_id, message):
                captured.append((job_id, message))
                # P422：回调现在返回父进程核对过的公开快照，广播用它。
                return {"id": job_id, "status": "running"}

            wm.set_progress_callback(_callback)

            with patch("backend.ws_manager.ws_manager.send", new=AsyncMock()) as send:
                await wm._forward("job-1", msg)

            # 保护权交接（2026-08-19 · G9/P422）：本条测试保护的是
            # 「worker_manager 不 import main，只用注册进来的回调」。这一点没变，
            # 仍然由上面的 blocker + captured 断言保护。
            # 变的是广播内容——运行中不再转发 Worker 原始消息，而是广播父进程
            # 返回的公开快照，好让 WebSocket 和查询接口是同一个函数的同一个返回值。
            self.assertEqual(captured, [("job-1", msg)])
            send.assert_awaited_once()
            sent_job_id, sent_payload = send.await_args.args
            self.assertEqual(sent_job_id, "job-1")
            self.assertEqual(sent_payload["type"], "state")
            self.assertEqual(sent_payload["state"]["status"], "running")
        finally:
            sys.meta_path.remove(blocker)
            if saved_main is not None:
                sys.modules["backend.main"] = saved_main


class TestMainWorkerProgressCallback(unittest.TestCase):

    def test_sync_job_from_worker_message_updates_jobs(self):
        """main 注册给 WorkerManager 的回调应保持原有 JOBS 同步行为。"""
        import backend.main as main

        import tempfile, pathlib
        _tmp = pathlib.Path(tempfile.mkdtemp())
        (_tmp / "job-1").mkdir()
        (_tmp / "job-1" / "meta.json").write_text(
            json.dumps({"status": "queued", "run_id": "run-1", "config": {}}),
            encoding="utf-8")
        _saved_dir = main.JOBS_DIR
        main.JOBS_DIR = _tmp
        old_jobs = dict(main.JOBS)
        try:
            main.JOBS.clear()
            # P388：任务必须持有权威运行身份，消息才会被接受。
            main.JOBS["job-1"] = {
                "id": "job-1",
                "status": "queued",
                "progress": 0,
                "stage": "",
                "message": "",
                "run_id": "run-1",
            }

            main._sync_job_from_worker_message(
                "job-1",
                {"type": "progress", "run_id": "run-1", "percent": 17,
                 "stage": "asr", "message": "running"},
            )

            # 保护权交接（2026-08-19 · G9/P422）：本条保护的是
            # 「main 注册的回调真的把 Worker 消息同步进了 JOBS」。这一点没变。
            # JOBS 只保存原始任务事实；固定公开状态由纯函数即时生成。
            state = main.job_state.build_public_task_state(main.JOBS["job-1"])
            self.assertEqual(state["percent"], 17)
            self.assertEqual(state["phase"], "asr")
            self.assertEqual(state["display_message"], "running")
            self.assertEqual(main.JOBS["job-1"]["status"], "running")
            self.assertGreaterEqual(state["revision"], 1)
        finally:
            main.JOBS_DIR = _saved_dir
            main.JOBS.clear()
            main.JOBS.update(old_jobs)

    def test_sync_job_from_worker_message_preserves_done_error_and_token_usage(self):
        """main 回调应保留原有 done/error/token_usage 同步行为。"""
        import backend.main as main
        from unittest.mock import patch

        old_jobs = dict(main.JOBS)
        try:
            main.JOBS.clear()
            main.JOBS["job-1"] = {"id": "job-1", "status": "running",
                                  "progress": 50, "run_id": "run-1"}

            with patch.object(main, "_persist_job_status", return_value=True):
                main._sync_job_from_worker_message(
                    "job-1",
                    {"type": "token_usage", "run_id": "run-1",
                     "usage": {"total_tokens": 123}},
                )
                self.assertEqual(main.JOBS["job-1"]["token_usage"], {"total_tokens": 123})

                main._sync_job_from_worker_message(
                    "job-1",
                    {
                        "type": "done",
                        "run_id": "run-1",
                        "result_video": "out.mp4",
                        "result_srt": "out.srt",
                        "failed_segments": 2,
                        "segment_count": 10,
                        "success_count": 8,
                    },
                )
                self.assertEqual(main.JOBS["job-1"]["status"], "done")
                self.assertEqual(main.JOBS["job-1"]["progress"], 100)
                # P388：Worker 自报的路径不具权威性。这两个相对路径落在任务
                # 目录之外、也没通过内容校验，因此不得被登记为可下载成品。
                self.assertIsNone(main.JOBS["job-1"]["result_video"])
                self.assertIsNone(main.JOBS["job-1"]["result_srt"])
                self.assertIsNone(main.JOBS["job-1"]["result_run_id"])
                self.assertFalse(main.JOBS["job-1"]["output_ready"])
                # 统计类字段不受成品校验影响，照常同步。
                self.assertEqual(main.JOBS["job-1"]["failed_segments"], 2)
                self.assertEqual(main.JOBS["job-1"]["segment_count"], 10)
                self.assertEqual(main.JOBS["job-1"]["success_count"], 8)

                main._sync_job_from_worker_message(
                    "job-1",
                    {"type": "error", "run_id": "run-1", "message": "boom"},
                )
                self.assertEqual(main.JOBS["job-1"]["status"], "error")
                self.assertEqual(main.JOBS["job-1"]["error"], "boom")
        finally:
            main.JOBS.clear()
            main.JOBS.update(old_jobs)


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
