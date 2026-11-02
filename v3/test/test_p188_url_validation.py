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
import socket
import tempfile


def _tree_manifest(root: Path):
    """Capture names and metadata without reading large user media files."""
    if not root.exists():
        return ()
    return tuple(
        (
            str(path.relative_to(root)),
            path.lstat().st_mode,
            path.lstat().st_size,
            path.lstat().st_mtime_ns,
            path.lstat().st_ctime_ns,
        )
        for path in sorted(root.rglob("*"))
    )


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

    def test_download_video_endpoint_unpacks_split_download_result(self):
        """P371：接口收到 (video, audio) 后只把 video 字符串返回前端。"""
        from fastapi.testclient import TestClient
        from backend import main

        with patch.object(main, "_verify_safe_url", return_value=True), \
             patch("backend.core.video_downloader.is_ytdlp_available", return_value=True), \
             patch("backend.core.video_downloader.download_video",
                   return_value=("/tmp/movie_video.mp4", "/tmp/movie_audio.m4a")):
            resp = TestClient(main.app, raise_server_exceptions=False).post(
                "/api/download-video",
                json={"url": "https://example.com/movie"},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["path"], "/tmp/movie_video.mp4")
        self.assertEqual(resp.json()["filename"], "movie_video.mp4")

    def test_https_url_accepted(self):
        """HTTPS validation proceeds without touching real jobs, logs, or network."""
        from backend import main
        from fastapi.testclient import TestClient

        user_jobs = Path.home() / ".video-subber" / "jobs"
        user_logs = Path.home() / ".video-subber" / "logs"
        jobs_before = _tree_manifest(user_jobs)
        logs_before = _tree_manifest(user_logs)
        original_state = (
            main.JOBS_DIR,
            main.LOG_DIR,
            main.JOBS,
            main.GLOBAL_HASH_INDEX,
            main.JOB_ID_TO_HASH,
        )
        worker_starts = []

        def fake_worker_start(job_id, job_dir, video_path, config, **kwargs):
            worker_starts.append((job_id, job_dir, video_path, config, kwargs))
            return kwargs.get("run_id") or "test-run"

        public_dns = [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
             ("93.184.216.34", 0)),
        ]
        with tempfile.TemporaryDirectory(prefix="p188-url-validation-") as tmp:
            temp_root = Path(tmp)
            temp_jobs = temp_root / "jobs"
            temp_logs = temp_root / "logs"
            temp_jobs.mkdir()
            temp_logs.mkdir()
            isolated_jobs = {}
            isolated_hash_index = {}
            isolated_job_to_hash = {}

            with patch.object(main, "JOBS_DIR", temp_jobs), \
                 patch.object(main, "LOG_DIR", temp_logs), \
                 patch.object(main, "JOBS", isolated_jobs), \
                 patch.object(main, "GLOBAL_HASH_INDEX", isolated_hash_index), \
                 patch.object(main, "JOB_ID_TO_HASH", isolated_job_to_hash), \
                 patch.object(main.worker_manager, "start", side_effect=fake_worker_start), \
                 patch("socket.getaddrinfo", return_value=public_dns) as dns_lookup, \
                 patch("subprocess.Popen", side_effect=AssertionError("测试不得启动子进程")) as popen, \
                 patch("backend.core.video_downloader.download_video",
                       side_effect=AssertionError("测试不得执行真实下载")) as download:
                resp = TestClient(main.app).post(
                    "/api/jobs",
                    json={"url": "https://www.youtube.com/watch?v=abc", "config": {}},
                )

                self.assertNotEqual(
                    resp.status_code, 400,
                    "https:// URL should not be rejected as invalid protocol",
                )
                job_id = resp.json()["id"]
                self.assertEqual(set(isolated_jobs), {job_id})
                self.assertTrue((temp_jobs / job_id / "meta.json").is_file())
                self.assertEqual(len(worker_starts), 1)
                self.assertEqual(worker_starts[0][0], job_id)
                self.assertEqual(worker_starts[0][1], temp_jobs / job_id)
                self.assertEqual(worker_starts[0][2], "")
                self.assertEqual(list(temp_logs.iterdir()), [])
                self.assertEqual(dns_lookup.call_args.args[0], "www.youtube.com")
                popen.assert_not_called()
                download.assert_not_called()

        self.assertIs(main.JOBS_DIR, original_state[0])
        self.assertIs(main.LOG_DIR, original_state[1])
        self.assertIs(main.JOBS, original_state[2])
        self.assertIs(main.GLOBAL_HASH_INDEX, original_state[3])
        self.assertIs(main.JOB_ID_TO_HASH, original_state[4])
        self.assertEqual(_tree_manifest(user_jobs), jobs_before)
        self.assertEqual(_tree_manifest(user_logs), logs_before)

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
