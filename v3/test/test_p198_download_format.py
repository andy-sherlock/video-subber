"""P198: yt-dlp downloads video and audio as separate files (split-download)."""
import sys
import types
import importlib
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call

sys.path.insert(0, str(Path(__file__).parent.parent))

_fake_yt_dlp = types.ModuleType("yt_dlp")
_mock_ydl_cls = MagicMock()
_fake_yt_dlp.YoutubeDL = _mock_ydl_cls
_had_yt_dlp = "yt_dlp" in sys.modules
_original_yt_dlp = sys.modules.get("yt_dlp")
sys.modules["yt_dlp"] = _fake_yt_dlp


def teardown_module():
    if _had_yt_dlp:
        sys.modules["yt_dlp"] = _original_yt_dlp
    else:
        sys.modules.pop("yt_dlp", None)
    if "backend.core.video_downloader" in sys.modules:
        importlib.reload(sys.modules["backend.core.video_downloader"])


class TestSplitDownload(unittest.TestCase):
    """Verify yt-dlp downloads video-only and audio-only as separate files."""

    def setUp(self):
        _mock_ydl_cls.reset_mock()
        mock_ydl = MagicMock()
        _mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        _mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {"title": "test", "ext": "mp4"}
        self._call_count = 0
        def _prep_filename(info):
            self._call_count += 1
            if self._call_count == 1:
                return "/tmp/test_video.mp4"
            return "/tmp/test_audio.m4a"
        mock_ydl.prepare_filename.side_effect = _prep_filename

    def test_returns_tuple_of_video_and_audio(self):
        """download_video must return (video_path, audio_path) tuple."""
        from backend.core.video_downloader import download_video
        result = download_video("https://example.com/video", "/tmp")
        self.assertIsNotNone(result)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        video_path, audio_path = result
        self.assertEqual(video_path, "/tmp/test_video.mp4")
        self.assertEqual(audio_path, "/tmp/test_audio.m4a")

    def test_video_format_is_best_video_only(self):
        """First download must request video-only stream."""
        from backend.core.video_downloader import download_video
        download_video("https://example.com/video", "/tmp")
        first_call_opts = _mock_ydl_cls.call_args_list[0][0][0]
        self.assertIn("bestvideo", first_call_opts["format"])
        self.assertNotIn("bestaudio", first_call_opts["format"])

    def test_audio_format_is_best_audio_only(self):
        """Second download must request audio-only stream."""
        from backend.core.video_downloader import download_video
        download_video("https://example.com/video", "/tmp")
        self.assertGreaterEqual(len(_mock_ydl_cls.call_args_list), 2)
        second_call_opts = _mock_ydl_cls.call_args_list[1][0][0]
        self.assertIn("bestaudio", second_call_opts["format"])

    def test_video_fail_returns_none(self):
        """If video download fails, return None (not a partial tuple)."""
        mock_ydl = MagicMock()
        _mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.extract_info.side_effect = Exception("network error")

        from backend.core.video_downloader import download_video
        result = download_video("https://example.com/video", "/tmp")
        self.assertIsNone(result)

    def test_audio_fail_returns_video_with_none_audio(self):
        """If audio download fails, return (video_path, None)."""
        mock_ydl = MagicMock()
        _mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        call_count = [0]
        def _extract(url, download=True):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"title": "test", "ext": "mp4"}
            raise Exception("audio unavailable")
        mock_ydl.extract_info.side_effect = _extract
        mock_ydl.prepare_filename.return_value = "/tmp/test_video.mp4"

        from backend.core.video_downloader import download_video
        result = download_video("https://example.com/video", "/tmp")
        self.assertIsNotNone(result)
        video_path, audio_path = result
        self.assertEqual(video_path, "/tmp/test_video.mp4")
        self.assertIsNone(audio_path)


if __name__ == '__main__':
    unittest.main()
