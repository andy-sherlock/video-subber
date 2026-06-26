"""TDD regression tests for P188: URL protocol validation.

Bug: create_job() accepts any non-empty string as source_url without checking
the protocol scheme. file://, ftp://, javascript: URLs are passed to yt-dlp
which can cause path traversal or unexpected behavior.

Run: cd v3 && .venv/bin/python -m pytest test/test_p188_url_validation.py -v
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from unittest.mock import patch, MagicMock
import json


class TestUrlValidation(unittest.TestCase):
    """Verify that non-http/https URLs are rejected with 400."""

    def _make_request(self, url_value):
        """Build a FastAPI test request body with the given URL."""
        from backend.main import create_job
        from fastapi.testclient import TestClient
        from backend.main import app

        client = TestClient(app)
        return client.post(
            "/api/jobs",
            json={"url": url_value, "config": {}},
        )

    def test_file_url_rejected(self):
        """file:// URL must be rejected with 400. Currently BUG: accepted."""
        resp = self._make_request("file:///etc/passwd")
        self.assertEqual(
            resp.status_code, 400,
            "P188 BUG: file:// URL should return 400, "
            f"got {resp.status_code}: {resp.json()}"
        )

    def test_ftp_url_rejected(self):
        """ftp:// URL must be rejected with 400."""
        resp = self._make_request("ftp://server/video.mp4")
        self.assertEqual(resp.status_code, 400)

    def test_javascript_url_rejected(self):
        """javascript: URL must be rejected with 400."""
        resp = self._make_request("javascript:alert(1)")
        self.assertEqual(resp.status_code, 400)

    def test_loopback_url_rejected(self):
        """Loopback URLs must be rejected so yt-dlp cannot call local services."""
        resp = self._make_request("http://127.0.0.1:8766/api/config")
        self.assertEqual(resp.status_code, 400)

    def test_private_network_url_rejected(self):
        """Private-network URLs must be rejected before worker download."""
        resp = self._make_request("http://192.168.1.1/video.mp4")
        self.assertEqual(resp.status_code, 400)

    def test_download_video_endpoint_rejects_loopback_before_ytdlp(self):
        """Standalone download endpoint must share the same URL safety check."""
        from fastapi.testclient import TestClient
        from backend.main import app

        client = TestClient(app)
        resp = client.post(
            "/api/download-video",
            json={"url": "http://127.0.0.1:8766/api/config"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_https_url_accepted(self):
        """Valid https:// URL should proceed (returns 200 or 202)."""
        resp = self._make_request("https://www.youtube.com/watch?v=abc")
        # 200/202 = accepted; 400 = rejected; 422 = validation OK but other issue
        self.assertNotEqual(
            resp.status_code, 400,
            "https:// URL should not be rejected as invalid protocol"
        )

    def test_empty_url_falls_through_to_local_path(self):
        """Empty URL should not trigger URL validation (goes to local path check)."""
        resp = self._make_request("")
        # Empty URL → local_path is also empty → 400 with "找不到文件"
        # This should NOT be our new protocol check
        data = resp.json()
        self.assertIn("找不到文件", data.get("error", ""),
                       "Empty URL should hit local-path error, not protocol check")


if __name__ == "__main__":
    unittest.main()
