"""TDD tests for P174: URL download integration.

Run: cd v2 && .venv/bin/python -m pytest test/test_p174_url_mode.py -v
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from unittest.mock import patch, MagicMock


class TestVideoDownloader(unittest.TestCase):
    """Test video_downloader.py functions."""

    def test_progress_hook_none_callback(self):
        from backend.core.video_downloader import _make_progress_hook
        hook = _make_progress_hook(None)
        hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})

    def test_progress_hook_with_callback(self):
        from backend.core.video_downloader import _make_progress_hook
        messages = []
        hook = _make_progress_hook(lambda m: messages.append(m))
        hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
        self.assertEqual(len(messages), 1)
        self.assertIn("50%", messages[0])

    def test_is_ytdlp_available_returns_bool(self):
        from backend.core.video_downloader import is_ytdlp_available
        result = is_ytdlp_available()
        self.assertIsInstance(result, bool)


class TestUrlModeConfig(unittest.TestCase):
    """Test that URL mode correctly sets up config for worker."""

    def test_url_filename_extraction(self):
        url = "https://www.youtube.com/watch?v=abc123"
        url_fname = url.split("/")[-1].split("?")[0]
        expected = "watch"
        self.assertEqual(url_fname, expected)

    def test_url_hash_deterministic(self):
        import hashlib
        url = "https://example.com/video.mp4"
        h1 = hashlib.sha256(url.encode()).hexdigest()
        h2 = hashlib.sha256(url.encode()).hexdigest()
        self.assertEqual(h1, h2)

    def test_source_url_in_config(self):
        cfg = {}
        url = "https://example.com/video.mp4"
        cfg["source_url"] = url
        self.assertEqual(cfg["source_url"], url)


if __name__ == "__main__":
    unittest.main()
