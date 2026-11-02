"""P384: 用户日志按读者分流 + 错误终态兜底。

三条合同（来自 2026-07-20 乔布斯视角施工裁决）：
A 清空——技术日志(Traceback/路径/内部编号)只进 debug.log，不污染 user.log。
B 求助清单——明确写给用户的事件进 user.log，用人话不带技术细节。
C 终态兜底——error 终态确保 user.log 存在且非空；已有内容不覆盖（成功/partial 由调用点保证不触发）。
"""
import logging
import tempfile
import unittest
from pathlib import Path

import backend.worker as worker
import backend.main as main


class ReaderSplitTest(unittest.TestCase):
    """A + B：技术日志与用户日志按读者分流。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._orig = worker.LOG_DIR
        worker.LOG_DIR = self.tmp
        self.user_path, self.debug_path = worker._setup_file_log("job_split")

    def tearDown(self):
        for h in (worker._file_handler_user, worker._file_handler_debug):
            if h:
                for lg in (logging.getLogger(), logging.getLogger("user")):
                    if h in lg.handlers:
                        lg.removeHandler(h)
                h.close()
        worker.LOG_DIR = self._orig

    def test_technical_out_user_plain(self):
        logging.getLogger("worker").error(
            "Traceback (most recent call last): File /Users/x/backend/worker.py, line 42"
        )
        logging.getLogger("user").warning("视频下载失败。请检查链接是否复制完整。")
        worker._file_handler_user.flush()
        worker._file_handler_debug.flush()
        user_text = Path(self.user_path).read_text(encoding="utf-8")
        debug_text = Path(self.debug_path).read_text(encoding="utf-8")
        # B：用户事件进 user.log
        self.assertIn("视频下载失败", user_text)
        # A：技术日志不污染 user.log
        self.assertNotIn("Traceback", user_text)
        self.assertNotIn(".py", user_text)
        # 排查能力不受损：技术日志仍进 debug.log
        self.assertIn("Traceback", debug_text)


class ErrorTerminalBackfillTest(unittest.TestCase):
    """C：error 终态兜底，确保 user.log 存在且非空、已有内容不覆盖。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._orig = main.LOG_DIR
        main.LOG_DIR = self.tmp

    def tearDown(self):
        main.LOG_DIR = self._orig

    def test_backfill_creates_nonempty_user_log(self):
        main._ensure_error_user_log("job_err")
        user_log = self.tmp / "job_err" / "user.log"
        self.assertTrue(user_log.is_file())
        self.assertGreater(user_log.stat().st_size, 0)

    def test_backfill_preserves_existing(self):
        d = self.tmp / "job_has"
        d.mkdir(parents=True)
        (d / "user.log").write_text("视频下载失败。请检查链接。\n", encoding="utf-8")
        main._ensure_error_user_log("job_has")
        self.assertIn("视频下载失败", (d / "user.log").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
