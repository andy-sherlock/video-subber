"""TDD tests for #9: yt-dlp video download.

Run: cd v2 && .venv/bin/python -m unittest test.test_phase1c_ytdlp -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestYtDlpAvailability(unittest.TestCase):
    """Tests for yt-dlp detection."""

    def test_is_available(self):
        from backend.core.video_downloader import is_ytdlp_available
        self.assertIsInstance(is_ytdlp_available(), bool)


class TestProgressHook(unittest.TestCase):
    """Tests for yt-dlp progress hook."""

    def test_callback_fires_on_downloading(self):
        from backend.core.video_downloader import _make_progress_hook
        calls = []

        def cb(msg):
            calls.append(msg)

        hook = _make_progress_hook(cb)
        hook({"status": "downloading", "downloaded_bytes": 1000,
              "total_bytes": 10000, "filename": "test.mp4"})
        self.assertEqual(len(calls), 1)
        self.assertIn("10%", calls[0])

    def test_callback_ignores_other_statuses(self):
        from backend.core.video_downloader import _make_progress_hook
        calls = []

        def cb(msg):
            calls.append(msg)

        hook = _make_progress_hook(cb)
        hook({"status": "finished"})
        hook({"status": "processing"})
        self.assertEqual(len(calls), 0)

    def test_no_callback_creates_null_hook(self):
        from backend.core.video_downloader import _make_progress_hook
        hook = _make_progress_hook(None)
        hook({"status": "downloading", "downloaded_bytes": 100,
              "total_bytes": 1000, "filename": "x.mp4"})


class TestDownloadFailure(unittest.TestCase):
    """Tests for graceful download failure."""

    def test_invalid_url_returns_none(self):
        from backend.core.video_downloader import download_video
        result = download_video("not_a_valid_url_xyz123://fake", "/tmp")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
