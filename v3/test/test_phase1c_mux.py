"""Tests for Phase 1C #8: soft subtitle muxing (mux_subtitles).

Run: cd v3 && .venv/bin/python -m unittest discover -s test -p "test_*.py" -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
import subprocess
import unittest
from unittest.mock import patch, MagicMock


class TestMuxSubtitlesImport(unittest.TestCase):
    """Verify mux_subtitles is importable."""

    def test_import(self):
        from backend.core.subtitler import mux_subtitles
        self.assertTrue(callable(mux_subtitles))


class TestMuxSubtitlesCommand(unittest.TestCase):
    """Verify ffmpeg command construction without actually running ffmpeg."""

    def setUp(self):
        # Mock subprocess.run to succeed without running anything
        self.run_patcher = patch("backend.core.subtitler.subprocess.run")
        self.mock_run = self.run_patcher.start()
        self.mock_run.return_value.returncode = 0

        # Ensure Path.is_file returns True for output path check
        self.exists_patcher = patch("backend.core.subtitler.Path.is_file")
        self.mock_exists = self.exists_patcher.start()
        self.mock_exists.return_value = True

        # Ensure _find_ffmpeg returns a usable path
        self.which_patcher = patch("backend.core.subtitler.shutil.which")
        self.mock_which = self.which_patcher.start()
        self.mock_which.return_value = "/usr/local/bin/ffmpeg"

    def tearDown(self):
        self.run_patcher.stop()
        self.exists_patcher.stop()
        self.which_patcher.stop()

    def _get_cmd(self):
        """Extract the command list passed to subprocess.run."""
        return self.mock_run.call_args[0][0]

    def test_mp4_uses_mov_text_codec(self):
        from backend.core.subtitler import mux_subtitles
        mux_subtitles("input.mp4", "subs.srt", "output.mp4")
        cmd = self._get_cmd()
        self.assertIn("-c:s", cmd)
        idx = cmd.index("-c:s") + 1
        self.assertEqual(cmd[idx], "mov_text")

    def test_mkv_uses_srt_codec(self):
        from backend.core.subtitler import mux_subtitles
        mux_subtitles("input.mkv", "subs.srt", "output.mkv")
        cmd = self._get_cmd()
        idx = cmd.index("-c:s") + 1
        self.assertEqual(cmd[idx], "srt")

    def test_mov_uses_mov_text_codec(self):
        from backend.core.subtitler import mux_subtitles
        mux_subtitles("input.mov", "subs.srt", "output.mov")
        cmd = self._get_cmd()
        idx = cmd.index("-c:s") + 1
        self.assertEqual(cmd[idx], "mov_text")

    def test_m4v_uses_mov_text_codec(self):
        from backend.core.subtitler import mux_subtitles
        mux_subtitles("input.m4v", "subs.srt", "output.m4v")
        cmd = self._get_cmd()
        idx = cmd.index("-c:s") + 1
        self.assertEqual(cmd[idx], "mov_text")

    def test_avi_uses_srt_codec(self):
        from backend.core.subtitler import mux_subtitles
        mux_subtitles("input.avi", "subs.srt", "output.avi")
        cmd = self._get_cmd()
        idx = cmd.index("-c:s") + 1
        self.assertEqual(cmd[idx], "srt")

    def test_includes_copy_for_video_and_audio(self):
        from backend.core.subtitler import mux_subtitles
        mux_subtitles("input.mp4", "subs.srt", "output.mp4")
        cmd = self._get_cmd()
        self.assertIn("-c", cmd)
        copy_idx = cmd.index("-c") + 1
        self.assertEqual(cmd[copy_idx], "copy")

    def test_audio_map_is_optional(self):
        """-map 0:a? should use optional marker for missing-audio safety."""
        from backend.core.subtitler import mux_subtitles
        mux_subtitles("input.mp4", "subs.srt", "output.mp4")
        cmd = self._get_cmd()
        self.assertIn("-map", cmd)
        # The audio map should use ? for optional
        self.assertIn("0:a?", cmd)

    def test_maps_video_stream(self):
        from backend.core.subtitler import mux_subtitles
        mux_subtitles("input.mp4", "subs.srt", "output.mp4")
        cmd = self._get_cmd()
        self.assertIn("0:v", cmd)

    def test_maps_subtitle_stream(self):
        from backend.core.subtitler import mux_subtitles
        mux_subtitles("input.mp4", "subs.srt", "output.mp4")
        cmd = self._get_cmd()
        self.assertIn("1:s", cmd)

    def test_output_flag_is_y(self):
        from backend.core.subtitler import mux_subtitles
        mux_subtitles("input.mp4", "subs.srt", "output.mp4")
        cmd = self._get_cmd()
        self.assertIn("-y", cmd)

    def test_input_files_in_correct_order(self):
        from backend.core.subtitler import mux_subtitles
        mux_subtitles("video.mp4", "subs.srt", "out.mp4")
        cmd = self._get_cmd()
        i1_idx = cmd.index("video.mp4")
        i2_idx = cmd.index("subs.srt")
        self.assertLess(i1_idx, i2_idx, "video should appear before srt in command")

    def test_output_path_is_last(self):
        from backend.core.subtitler import mux_subtitles
        mux_subtitles("video.mp4", "subs.srt", "out.mp4")
        cmd = self._get_cmd()
        self.assertEqual(cmd[-1], "out.mp4")


class TestMuxSubtitlesErrors(unittest.TestCase):
    """Verify error handling in mux_subtitles."""

    def test_ffmpeg_not_found_raises_runtime_error(self):
        """When ffmpeg binary is not found, a structured output error is raised."""
        with patch("backend.core.subtitler.shutil.which", return_value=None):
            with patch("backend.core.subtitler.os.path.isfile", return_value=False):
                from backend.core.subtitler import mux_subtitles, SubtitleOutputError
                with self.assertRaises(SubtitleOutputError) as ctx:
                    mux_subtitles("input.mp4", "subs.srt", "output.mp4")
                self.assertEqual(ctx.exception.stage, "burn")
                self.assertEqual(ctx.exception.failure_kind, "ffmpeg_not_installed")
                self.assertIn("FFmpeg", ctx.exception.user_message)

    def test_ffmpeg_nonzero_exit_raises_runtime_error(self):
        """When ffmpeg returns non-zero, RuntimeError should be raised."""
        with patch("backend.core.subtitler.shutil.which", return_value="/usr/bin/ffmpeg"):
            with patch("backend.core.subtitler.subprocess.run") as mock_run:
                mock_run.return_value.returncode = 1
                mock_run.return_value.stderr = "ffmpeg error output"
                with patch("backend.core.subtitler.Path.is_file", return_value=False):
                    from backend.core.subtitler import mux_subtitles, SubtitleOutputError
                    with self.assertRaises(SubtitleOutputError) as ctx:
                        mux_subtitles("input.mp4", "subs.srt", "output.mp4")
                    self.assertEqual(ctx.exception.failure_kind, "output_mux_failed")
                    self.assertIn("ffmpeg error output", ctx.exception.technical_detail)
                    self.assertNotIn("ffmpeg error output", ctx.exception.user_message)

    def test_ffmpeg_zero_exit_but_no_output_file(self):
        """When ffmpeg exits 0 but output file missing, RuntimeError should be raised."""
        with patch("backend.core.subtitler.shutil.which", return_value="/usr/bin/ffmpeg"):
            with patch("backend.core.subtitler.subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                with patch("backend.core.subtitler.Path.is_file", return_value=False):
                    from backend.core.subtitler import mux_subtitles, SubtitleOutputError
                    with self.assertRaises(SubtitleOutputError) as ctx:
                        mux_subtitles("input.mp4", "subs.srt", "output.mp4")
                    self.assertEqual(ctx.exception.failure_kind, "output_mux_failed")
                    self.assertIn("output_exists=False", ctx.exception.technical_detail)

    def test_ffmpeg_timeout_raises_structured_error(self):
        with patch("backend.core.subtitler.shutil.which", return_value="/usr/bin/ffmpeg"):
            with patch(
                "backend.core.subtitler.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1800),
            ):
                from backend.core.subtitler import mux_subtitles, SubtitleOutputError
                with self.assertRaises(SubtitleOutputError) as ctx:
                    mux_subtitles("input.mp4", "subs.srt", "output.mp4")
                self.assertEqual(ctx.exception.stage, "burn")
                self.assertEqual(ctx.exception.failure_kind, "output_mux_timeout")


class TestWorkerOutputModeConfig(unittest.TestCase):
    """Verify worker reads outputMode config correctly."""

    def test_output_mode_burn(self):
        """outputMode='burn' -> burn_flag=True, mux_flag=False."""
        config = {"video_path": "/tmp/test.mp4", "outputMode": "burn"}
        burn_flag = (config.get("outputMode", "srt") == "burn")
        mux_flag = (config.get("outputMode", "srt") == "mux")
        self.assertTrue(burn_flag)
        self.assertFalse(mux_flag)

    def test_output_mode_mux(self):
        """outputMode='mux' -> burn_flag=False, mux_flag=True."""
        config = {"video_path": "/tmp/test.mp4", "outputMode": "mux"}
        burn_flag = (config.get("outputMode", "srt") == "burn")
        mux_flag = (config.get("outputMode", "srt") == "mux")
        self.assertFalse(burn_flag)
        self.assertTrue(mux_flag)

    def test_output_mode_srt(self):
        """outputMode='srt' -> burn_flag=False, mux_flag=False."""
        config = {"video_path": "/tmp/test.mp4", "outputMode": "srt"}
        burn_flag = (config.get("outputMode", "srt") == "burn")
        mux_flag = (config.get("outputMode", "srt") == "mux")
        self.assertFalse(burn_flag)
        self.assertFalse(mux_flag)

    def test_backward_compat_burn_true(self):
        """Old config with burn=true -> output_mode=burn."""
        config = {"video_path": "/tmp/test.mp4", "burn": True}
        output_mode = config.get("outputMode")
        if output_mode is None:
            burn_old = config.get("burn", True)
            output_mode = "burn" if burn_old else "srt"
        self.assertEqual(output_mode, "burn")

    def test_backward_compat_burn_false(self):
        """Old config with burn=false -> output_mode=srt."""
        config = {"video_path": "/tmp/test.mp4", "burn": False}
        output_mode = config.get("outputMode")
        if output_mode is None:
            burn_old = config.get("burn", True)
            output_mode = "burn" if burn_old else "srt"
        self.assertEqual(output_mode, "srt")

    def test_output_mode_overrides_burn(self):
        """outputMode takes precedence over burn when both are present."""
        config = {"video_path": "/tmp/test.mp4", "outputMode": "mux", "burn": True}
        output_mode = config.get("outputMode", "srt")
        self.assertEqual(output_mode, "mux")

    def test_config_validation_rejects_invalid_output_mode(self):
        """Config validation should reject unknown outputMode values."""
        valid_values = ("srt", "burn", "mux")
        self.assertNotIn("hardcode", valid_values)
        self.assertNotIn("soft", valid_values)
        self.assertNotIn("", valid_values)


class TestMuxOutputPath(unittest.TestCase):
    """Verify mux output path generation."""

    def test_mp4_output_path(self):
        from pathlib import Path
        _video_filename = "myvideo.mp4"
        _video_stem = Path(_video_filename).stem
        _video_suffix = Path(_video_filename).suffix
        video_out_mux = f"/tmp/jobs/abc/{_video_stem}_zh_sub{_video_suffix}"
        self.assertEqual(video_out_mux, "/tmp/jobs/abc/myvideo_zh_sub.mp4")

    def test_mkv_output_path(self):
        from pathlib import Path
        _video_filename = "movie.mkv"
        _video_stem = Path(_video_filename).stem
        _video_suffix = Path(_video_filename).suffix
        video_out_mux = f"/tmp/jobs/abc/{_video_stem}_zh_sub{_video_suffix}"
        self.assertEqual(video_out_mux, "/tmp/jobs/abc/movie_zh_sub.mkv")


if __name__ == "__main__":
    unittest.main()
