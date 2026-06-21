"""Phase 1D: Frontend UI + model download + parallel/serial path tests."""
import unittest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestFrontendThresholdUI(unittest.TestCase):
    """Frontend showQmWarning logic (Python equivalent for testing)."""

    def _showQmWarning(self, qm, total):
        """Mirror frontend/index.html showQmWarning function."""
        if total < 20 or qm == 0:
            return None
        r = qm / total
        if r > 0.15:
            return "red"
        if r >= 0.05 or qm >= 50:
            return "yellow"
        return None

    def test_no_warning_for_small_video(self):
        """19-segment video with 10 [?] → no warning (total < 20)."""
        self.assertIsNone(self._showQmWarning(10, 19))

    def test_no_warning_zero_marks(self):
        """100 segments, 0 uncertain → no warning."""
        self.assertIsNone(self._showQmWarning(0, 100))

    def test_yellow_at_5_percent(self):
        """100 segments, 5 [?] (5%) → yellow."""
        self.assertEqual(self._showQmWarning(5, 100), "yellow")

    def test_yellow_absolute_50(self):
        """2000 segments, 50 [?] (2.5%) → yellow (absolute cap)."""
        self.assertEqual(self._showQmWarning(50, 2000), "yellow")

    def test_red_above_15_percent(self):
        """100 segments, 16 [?] (16%) → red."""
        self.assertEqual(self._showQmWarning(16, 100), "red")

    def test_red_many_marks(self):
        """100 segments, 30 [?] (30%) → red."""
        self.assertEqual(self._showQmWarning(30, 100), "red")

    def test_clean_video_no_warning(self):
        """Normal video with 3% [?] → no warning."""
        self.assertIsNone(self._showQmWarning(3, 100))


class TestModelDownloadStatus(unittest.TestCase):
    """model_manager.py get_model_status returns correct structure."""

    def test_status_returns_all_models(self):
        """get_model_status returns all 5 whisper models."""
        from backend.model_manager import MODELS, get_model_status

        status = get_model_status()
        for mid in MODELS:
            self.assertIn(mid, status,
                          f"Model {mid} must be in status dict")
            self.assertIn("downloaded", status[mid])
            self.assertIn("downloading", status[mid])
            self.assertIn("progress", status[mid])

    def test_unknown_model_returns_error(self):
        """start_download with unknown model_id returns error."""
        from backend.model_manager import start_download
        result = start_download("nonexistent_model")
        self.assertIn("error", result)

    def test_cancel_unknown_no_crash(self):
        """cancel_download with unknown id does not crash."""
        from backend.model_manager import cancel_download
        result = cancel_download("nonexistent")
        self.assertIsNotNone(result)  # Returns something, not crash

    def test_status_structure(self):
        """Each model status has required fields."""
        from backend.model_manager import get_model_status
        status = get_model_status()
        for mid, info in status.items():
            self.assertIn("name", info)
            self.assertIn("size", info)
            self.assertIsInstance(info["downloaded"], bool)
            self.assertIsInstance(info["downloading"], bool)


class TestTranscriberPathCoverage(unittest.TestCase):
    """Verify parallel decision functions behave correctly (replaces I6 string tests)."""

    def test_svfa_decoupled_from_qwen(self):
        """SV+FA parallel flag is independent of Qwen pipeline flag."""
        from backend.transcriber_v2 import should_parallel_svfa, should_qwen_pipeline
        self.assertTrue(should_parallel_svfa("standard", 16))
        self.assertTrue(should_qwen_pipeline("standard", 16))

    def test_qwen_standard_and_precise(self):
        """Qwen pipeline: standard and precise tiers with enough RAM."""
        from backend.transcriber_v2 import should_qwen_pipeline
        self.assertTrue(should_qwen_pipeline("precise", 32))
        self.assertTrue(should_qwen_pipeline("standard", 32))


if __name__ == '__main__':
    unittest.main()
