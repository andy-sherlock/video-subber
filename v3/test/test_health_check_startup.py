"""启动健康检查：check_all() 必须含 yt-dlp 状态（P174：yt-dlp 缺失曾被误报为内存不足）；
health_summary_line() 把 check_all 结果转成一行 ✅/❌ 摘要供启动日志打印。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestYtdlpInCheckAll(unittest.TestCase):

    def test_check_all_includes_ytdlp_key(self):
        from backend.env_check import check_all
        result = check_all()
        self.assertIn("ytdlp", result,
                      "check_all() 必须包含 ytdlp 状态")

    def test_check_ytdlp_shape(self):
        from backend.env_check import check_ytdlp
        result = check_ytdlp()
        self.assertIn("ready", result)
        self.assertIsInstance(result["ready"], bool)

    def test_check_ytdlp_reflects_availability(self):
        from backend import env_check
        with patch("backend.core.video_downloader.is_ytdlp_available", return_value=False):
            self.assertEqual(env_check.check_ytdlp(), {"ready": False})
        with patch("backend.core.video_downloader.is_ytdlp_available", return_value=True):
            self.assertEqual(env_check.check_ytdlp(), {"ready": True})


class TestHealthSummaryLine(unittest.TestCase):

    def test_marks_ready_and_missing(self):
        from backend.env_check import health_summary_line
        line = health_summary_line({
            "ffmpeg": {"installed": True, "has_libass": True},
            "whisper": {"installed": False, "models": []},
            "ytdlp": {"ready": True},
            "sensevoice": {"ready": False},
        })
        self.assertIn("✅ ffmpeg", line)
        self.assertIn("❌ whisper", line)
        self.assertIn("✅ ytdlp", line)
        self.assertIn("❌ sensevoice", line)

    def test_empty_status(self):
        from backend.env_check import health_summary_line
        self.assertEqual(health_summary_line({}), "")


class TestMissingChecks(unittest.TestCase):

    def test_lists_only_not_ready(self):
        from backend.env_check import missing_checks
        missing = missing_checks({
            "ffmpeg": {"installed": True},
            "whisper": {"installed": False, "models": []},
            "ytdlp": {"ready": False},
            "sensevoice": {"ready": True},
        })
        self.assertEqual(set(missing), {"whisper", "ytdlp"})


class TestCheckAllResilience(unittest.TestCase):
    """工程师审查 2026-06-22：单个检查抛非 ImportError 异常，不得拖垮整份健康摘要。"""

    def test_check_qwen_asr_survives_non_import_error(self):
        """mlx import 在无 Metal 环境抛 RuntimeError，check_qwen_asr 必须返回 not-ready，不能往上抛。"""
        import types
        from backend import env_check

        class _Metalless(types.ModuleType):
            def __getattr__(self, name):
                raise RuntimeError("Metal device not found")

        # mlx 抛 RuntimeError；qwen_asr 设为 None → import 抛 ImportError（强制走 not-ready）
        with patch.dict(sys.modules, {"mlx_qwen3_asr": _Metalless("mlx_qwen3_asr"),
                                      "qwen_asr": None}):
            result = env_check.check_qwen_asr()
        self.assertFalse(result["ready"])

    def test_check_all_survives_throwing_checker(self):
        """任一检查函数抛异常，check_all 仍返回完整字典（该键标记 not-ready），其余检查照常。"""
        from backend import env_check
        with patch.object(env_check, "check_qwen_asr", side_effect=RuntimeError("boom")):
            result = env_check.check_all()
        self.assertIn("qwen_asr", result)
        self.assertFalse(result["qwen_asr"]["ready"])
        self.assertIn("ytdlp", result)  # 其余检查没被带崩


if __name__ == '__main__':
    unittest.main()
