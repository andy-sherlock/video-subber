"""P203: Archive filenames must include timestamp to avoid overwriting."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestArchiveNaming(unittest.TestCase):
    """Verify that archiving old output files uses unique names."""

    @patch("backend.worker.shutil")
    @patch("backend.worker.time")
    def test_archive_name_includes_timestamp(self, mock_time, mock_shutil):
        """When dest exists, archived name must contain a timestamp."""
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir)
            video_stem = "testvideo"
            ctx_suffix = "逐段"

            existing_file = save_path / "testvideo_逐段_zh.mp4"
            existing_file.write_text("old")

            dest_v = save_path / "testvideo_逐段_zh.mp4"

            mock_time.strftime.return_value = "20260609_120000"

            from backend.worker import _archive_path
            archived = _archive_path(save_path, video_stem, ctx_suffix, "_zh.mp4")

            self.assertIn("20260609_120000", str(archived),
                          "Archive path must include timestamp for uniqueness")

    @patch("backend.worker.time")
    def test_two_archives_differ(self, mock_time):
        """Two archives at different times must produce different names."""
        from backend.worker import _archive_path

        mock_time.strftime.side_effect = ["20260609_120000", "20260609_120100"]

        a1 = _archive_path(Path("/tmp"), "vid", "逐段", "_zh.mp4")
        a2 = _archive_path(Path("/tmp"), "vid", "逐段", "_zh.mp4")

        self.assertNotEqual(a1, a2,
                            "Archives at different times must have different names")


if __name__ == '__main__':
    unittest.main()
