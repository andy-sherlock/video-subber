"""P346 C4: URL 任务卡死修复的护栏测试。

背景：C4 桌面 QA 发现 URL 任务下载完视频后长时间停在"处理中"，worker 与 ffmpeg
仍活着但任务不进入完成/失败/可重试。根因是 extractor.py 的 ffprobe/ffmpeg 子进程调用
没有 timeout、没有 stdin 隔离，一旦外部进程卡住，worker 就无限期阻塞。

四个红灯覆盖四处 Python 改动：
1. ffprobe(get_video_resolution) 必须带 timeout，且超时仍保持 (1920,1080) 兜底合同。
2. ffmpeg(extract_audio) 必须带 timeout + stdin 隔离，超时抛清晰 RuntimeError。
3. 启动扫描把 running/queued 改 interrupted 时，必须回写磁盘 meta.json。
4. 可选音频流下载失败只打人话日志，不再打 WARNING + traceback。

P198 的"音频流失败仍返回 (video, None) 且从原视频抽音频"合同由既有的
test_p198_download_format.py 守护，本轮保持绿。
"""
import sys
import json
import logging
import shutil
import subprocess
import tempfile
import types
import importlib
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.core import extractor


class TestStructuredBurnErrors(unittest.TestCase):
    def test_burn_timeout_is_structured_and_keeps_user_message_clean(self):
        from backend.core.subtitler import burn_subtitles, SubtitleOutputError

        with tempfile.TemporaryDirectory() as d:
            ass = Path(d) / "input.ass"
            ass.write_text("[Script Info]", encoding="utf-8")
            with patch(
                "backend.core.subtitler.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=7200),
            ):
                with self.assertRaises(SubtitleOutputError) as ctx:
                    burn_subtitles("input.mp4", str(ass), str(Path(d) / "out.mp4"))
        self.assertEqual(ctx.exception.stage, "burn")
        self.assertEqual(ctx.exception.failure_kind, "output_burn_timeout")
        self.assertIn("SRT", ctx.exception.user_message)

    def test_burn_timeout_removes_half_written_output(self):
        """P365：ffmpeg 超时留下的半截视频不能被下次恢复当成成品。"""
        from backend.core.subtitler import burn_subtitles, SubtitleOutputError

        with tempfile.TemporaryDirectory() as d:
            ass = Path(d) / "input.ass"
            out = Path(d) / "out.mp4"
            ass.write_text("[Script Info]", encoding="utf-8")

            def timeout_after_writing(*args, **kwargs):
                out.write_bytes(b"half")
                raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=7200)

            with patch("backend.core.subtitler.subprocess.run", side_effect=timeout_after_writing):
                with self.assertRaises(SubtitleOutputError):
                    burn_subtitles("input.mp4", str(ass), str(out))

            self.assertFalse(out.exists())

    def test_burn_nonzero_exit_is_structured_and_stderr_is_technical_only(self):
        from backend.core.subtitler import burn_subtitles, SubtitleOutputError

        with tempfile.TemporaryDirectory() as d:
            ass = Path(d) / "input.ass"
            ass.write_text("[Script Info]", encoding="utf-8")
            with patch("backend.core.subtitler.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess(
                    args=[], returncode=1, stdout="", stderr="SAR DAR codec bitrate"
                )
                with self.assertRaises(SubtitleOutputError) as ctx:
                    burn_subtitles("input.mp4", str(ass), str(Path(d) / "out.mp4"))
        self.assertEqual(ctx.exception.failure_kind, "output_burn_failed")
        self.assertIn("SAR DAR codec bitrate", ctx.exception.technical_detail)
        self.assertNotIn("SAR", ctx.exception.user_message)

    def test_burn_ffmpeg_missing_is_structured(self):
        from backend.core.subtitler import burn_subtitles, SubtitleOutputError

        with tempfile.TemporaryDirectory() as d:
            ass = Path(d) / "input.ass"
            ass.write_text("[Script Info]", encoding="utf-8")
            with patch("backend.core.subtitler._find_ffmpeg", return_value="missing-ffmpeg"), \
                 patch("backend.core.subtitler.shutil.which", return_value=None), \
                 patch("backend.core.subtitler.os.path.isfile", return_value=False):
                with self.assertRaises(SubtitleOutputError) as ctx:
                    burn_subtitles("input.mp4", str(ass), str(Path(d) / "out.mp4"))
        self.assertEqual(ctx.exception.failure_kind, "ffmpeg_not_installed")


class TestFfprobeTimeoutGuard(unittest.TestCase):
    """红灯1：ffprobe 必须带 timeout，且超时保持默认分辨率兜底合同。"""

    def test_get_video_resolution_passes_positive_timeout(self):
        """ffprobe 调用必须带正数 timeout，否则会永久阻塞。"""
        with patch("backend.core.extractor.subprocess.run") as m:
            m.return_value = subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout='{"streams":[{"width":640,"height":480}]}', stderr="")
            extractor.get_video_resolution("x.mp4")
        self.assertTrue(m.called)
        timeout = m.call_args.kwargs.get("timeout")
        self.assertIsNotNone(timeout, "ffprobe 调用必须带 timeout")
        self.assertGreater(timeout, 0)

    def test_get_video_resolution_returns_default_on_timeout(self):
        """兜底合同：ffprobe 超时时仍返回 (1920,1080)，不抛异常。"""
        with patch("backend.core.extractor.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=30)):
            result = extractor.get_video_resolution("x.mp4")
        self.assertEqual(result, (1920, 1080))


class TestExtractAudioTimeout(unittest.TestCase):
    """红灯2：ffmpeg 必须带 timeout + stdin 隔离，超时抛清晰错误。"""

    def test_extract_audio_passes_timeout_and_isolates_stdin(self):
        """ffmpeg 调用必须带 timeout，并隔离 stdin（-nostdin 或 stdin=DEVNULL）。"""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "audio.wav"

            def _run(cmd, **kw):
                Path(cmd[-2]).write_bytes(b"WAVDATA")
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout="", stderr="")

            with patch("backend.core.extractor.subprocess.run", side_effect=_run) as m:
                extractor.extract_audio("in.mp4", str(out))
        timeout = m.call_args.kwargs.get("timeout")
        self.assertIsNotNone(timeout, "ffmpeg 调用必须带 timeout")
        self.assertGreater(timeout, 0)
        cmd = m.call_args.args[0] if m.call_args.args else m.call_args.kwargs.get("args", [])
        stdin_kw = m.call_args.kwargs.get("stdin")
        self.assertTrue(
            stdin_kw == subprocess.DEVNULL or "-nostdin" in cmd,
            "ffmpeg 必须隔离 stdin（加 -nostdin 或 stdin=subprocess.DEVNULL）")

    def test_extract_audio_forces_wav_format(self):
        """写 .tmp 后缀后 ffmpeg 无法从扩展名推断格式，必须显式 -f wav（防回归）。"""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "audio.wav"

            def _run(cmd, **kw):
                Path(cmd[-2]).write_bytes(b"WAVDATA")
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout="", stderr="")

            with patch("backend.core.extractor.subprocess.run", side_effect=_run) as m:
                extractor.extract_audio("in.mp4", str(out))
        cmd = m.call_args.args[0] if m.call_args.args else m.call_args.kwargs.get("args", [])
        self.assertIn("-f", cmd)
        self.assertEqual(cmd[cmd.index("-f") + 1], "wav",
                         "写 .tmp 时必须 -f wav 强制格式，否则 ffmpeg 无法识别输出")

    def test_extract_audio_raises_clear_error_on_timeout(self):
        """ffmpeg 超时必须抛清晰 RuntimeError（worker 会转成 error 状态）。"""
        with patch("backend.core.extractor.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1800)):
            with self.assertRaises(RuntimeError) as ctx:
                extractor.extract_audio("in.mp4", "/tmp/p346_out.wav")
        self.assertIn("超时", str(ctx.exception))


class TestRestartReconcileWriteback(unittest.TestCase):
    """红灯3：启动扫描把 running 改 interrupted 时，必须回写磁盘 meta.json。"""

    def test_reconcile_running_to_interrupted_persists_to_disk(self):
        from backend.main import _reconcile_job_status_on_disk
        with tempfile.TemporaryDirectory() as d:
            job_dir = Path(d)
            meta = {"status": "running", "file_hash": "abc", "config": {}}
            (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

            new_status = _reconcile_job_status_on_disk(job_dir, dict(meta))

            self.assertEqual(new_status, "interrupted")
            on_disk = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(on_disk["status"], "interrupted",
                             "磁盘 meta.json 必须与内存状态一致")

    def test_reconcile_does_not_trust_unverified_output_files(self):
        from backend.main import _reconcile_job_status_on_disk
        with tempfile.TemporaryDirectory() as d:
            job_dir = Path(d)
            srt = job_dir / "watch.srt"
            video = job_dir / "watch_zh.mp4"
            srt.write_text("1\n", encoding="utf-8")
            video.write_bytes(b"video")
            meta = {
                "status": "running",
                "file_hash": "abc",
                "config": {"outputMode": "burn"},
                "result_srt": str(srt),
                "result_video": str(video),
            }
            (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

            new_status = _reconcile_job_status_on_disk(job_dir, dict(meta))

            self.assertEqual(new_status, "interrupted")
            on_disk = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(on_disk["status"], "interrupted")


class TestAudioDegradeLogDenoise(unittest.TestCase):
    """红灯4：可选音频流失败只打人话日志，不再打 WARNING + traceback。"""

    def test_audio_stream_failure_logs_without_warning(self):
        fake = types.ModuleType("yt_dlp")

        class _FakeYDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_info(self, url, download=True):
                raise Exception("Requested format is not available")

            def prepare_filename(self, info):
                return ""

        fake.YoutubeDL = _FakeYDL
        vd = importlib.import_module("backend.core.video_downloader")
        with patch.dict(sys.modules, {"yt_dlp": fake}):
            with self.assertLogs("backend.core.video_downloader", level="DEBUG") as cm:
                result = vd._download_stream(
                    "https://x/v.mp4", "/tmp", "bestaudio", "audio", None)

        self.assertIsNone(result)
        warnings = [r for r in cm.records if r.levelno >= logging.WARNING]
        self.assertEqual(warnings, [],
                         "可选音频流失败不应打 WARNING/traceback，应是人话 info 日志")


class TestExtractAudioAtomicOutput(unittest.TestCase):
    """阻塞1：ffmpeg 先写临时文件，只有成功才原子替换成最终 audio.wav。
    超时/非0/空输出都不能留下半截 audio.wav，也不能误删已有的旧 audio.wav。"""

    def _run_writes_target(self, content, returncode):
        """构造一个 side_effect：往 ffmpeg 的输出目标（cmd[-2]）写 content，再按 returncode 返回。"""
        def _run(cmd, **kw):
            Path(cmd[-2]).write_bytes(content)
            return subprocess.CompletedProcess(
                args=cmd, returncode=returncode, stdout="", stderr="boom")
        return _run

    def test_nonzero_exit_does_not_expose_final_audio(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "audio.wav"
            with patch("backend.core.extractor.subprocess.run",
                       side_effect=self._run_writes_target(b"\x00\x00", 1)):
                with self.assertRaises(RuntimeError):
                    extractor.extract_audio("in.mp4", str(out))
            self.assertFalse(out.exists(), "非0退出不应留下最终 audio.wav")
            self.assertFalse((Path(d) / "audio.wav.tmp").exists(), "失败应清理临时文件")

    def test_timeout_does_not_expose_final_audio(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "audio.wav"

            def _run(cmd, **kw):
                Path(cmd[-2]).write_bytes(b"\x00")
                raise subprocess.TimeoutExpired(cmd, timeout=1800)

            with patch("backend.core.extractor.subprocess.run", side_effect=_run):
                with self.assertRaises(RuntimeError):
                    extractor.extract_audio("in.mp4", str(out))
            self.assertFalse(out.exists(), "超时不应留下最终 audio.wav")
            self.assertFalse((Path(d) / "audio.wav.tmp").exists())

    def test_zero_size_output_treated_as_failure(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "audio.wav"
            with patch("backend.core.extractor.subprocess.run",
                       side_effect=self._run_writes_target(b"", 0)):
                with self.assertRaises(RuntimeError):
                    extractor.extract_audio("in.mp4", str(out))
            self.assertFalse(out.exists(), "0 字节输出应按失败处理，不暴露 audio.wav")

    def test_failure_keeps_existing_good_cache(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "audio.wav"
            out.write_bytes(b"GOOD-OLD-CACHE")
            with patch("backend.core.extractor.subprocess.run",
                       side_effect=self._run_writes_target(b"\x00", 1)):
                with self.assertRaises(RuntimeError):
                    extractor.extract_audio("in.mp4", str(out))
            self.assertTrue(out.exists(), "失败不应误删已有的旧 audio.wav")
            self.assertEqual(out.read_bytes(), b"GOOD-OLD-CACHE", "旧的好缓存必须原样保留")

    def test_stale_tmp_does_not_pollute_final(self):
        """上次进程被强杀留下的旧 tmp，不能在本次 ffmpeg 没写新内容时被 replace 成最终 audio.wav。"""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "audio.wav"
            (Path(d) / "audio.wav.tmp").write_bytes(b"STALE-TMP")

            def _run(cmd, **kw):
                # 本次 ffmpeg 返回 0，但没有写任何新内容（极端边缘）
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

            with patch("backend.core.extractor.subprocess.run", side_effect=_run):
                with self.assertRaises(RuntimeError):
                    extractor.extract_audio("in.mp4", str(out))
            self.assertFalse(out.exists(), "旧 tmp 不能被 replace 成最终 audio.wav")

    def test_success_replaces_tmp_to_final(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "audio.wav"
            with patch("backend.core.extractor.subprocess.run",
                       side_effect=self._run_writes_target(b"WAVDATA", 0)):
                result = extractor.extract_audio("in.mp4", str(out))
            self.assertEqual(result, str(out))
            self.assertTrue(out.exists())
            self.assertEqual(out.read_bytes(), b"WAVDATA")
            self.assertFalse((Path(d) / "audio.wav.tmp").exists(), "成功后临时文件应已 replace 掉")


class TestGetJobAuthoritativeConvergence(unittest.TestCase):
    """阻塞2：/api/jobs 权威收敛——worker 已死但状态仍 running/queued 时，
    后端把状态收敛成 interrupted 并回写 meta.json，前端不再只靠本地 deadPolls。"""

    def _make_job(self, d, job_id, status):
        job_dir = Path(d) / job_id
        job_dir.mkdir()
        (job_dir / "meta.json").write_text(
            json.dumps({"status": status, "config": {}}), encoding="utf-8")
        return job_dir

    def test_dead_worker_running_job_converges_to_interrupted(self):
        from backend import main
        with tempfile.TemporaryDirectory() as d:
            job_id = "p346conv01"
            job_dir = self._make_job(d, job_id, "running")
            main.JOBS[job_id] = {"id": job_id, "status": "running", "progress": 40,
                                 "stage": "extract", "message": "", "error": None}
            try:
                with patch.object(main, "JOBS_DIR", Path(d)), \
                     patch.object(main.worker_manager, "is_alive", return_value=False):
                    result = main.get_job(job_id)
                self.assertEqual(result["state"]["status"], "interrupted")
                self.assertFalse(main.JOBS[job_id]["process_alive"])
                on_disk = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
                self.assertEqual(on_disk["status"], "interrupted",
                                 "后端必须权威回写磁盘 meta.json")
            finally:
                main.JOBS.pop(job_id, None)

    def test_alive_worker_running_job_stays_running(self):
        from backend import main
        with tempfile.TemporaryDirectory() as d:
            job_id = "p346conv02"
            self._make_job(d, job_id, "running")
            main.JOBS[job_id] = {"id": job_id, "status": "running", "progress": 40,
                                 "stage": "extract", "message": "", "error": None}
            try:
                with patch.object(main, "JOBS_DIR", Path(d)), \
                     patch.object(main.worker_manager, "is_alive", return_value=True):
                    result = main.get_job(job_id)
                self.assertEqual(result["state"]["status"], "running", "worker 还活着不应误判中断")
                self.assertTrue(main.JOBS[job_id]["process_alive"])
            finally:
                main.JOBS.pop(job_id, None)


class TestExtractAudioRealFfmpeg(unittest.TestCase):
    """真 ffmpeg 集成测试（轻量、无 ffmpeg 时跳过）。守住 mock 测不到的真实行为——
    这次 `-f wav` 回归就是 mock subprocess.run 漏掉的（Codex 复审建议补此测试）。"""

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "需要本机 ffmpeg/ffprobe")
    def test_extract_audio_produces_valid_wav_with_real_ffmpeg(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "tiny.mp4"
            gen = subprocess.run([
                "ffmpeg", "-nostdin", "-y",
                "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=5",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                "-shortest", str(src),
            ], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
            self.assertEqual(gen.returncode, 0, f"生成测试视频失败: {gen.stderr[-300:]}")

            out = Path(d) / "audio.wav"
            result = extractor.extract_audio(str(src), str(out))

            self.assertEqual(result, str(out))
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 0)
            self.assertFalse((Path(d) / "audio.wav.tmp").exists(),
                             "成功后临时文件应已 replace 掉")
            # 真正打开成 WAV 校验：缺 -f wav 时 ffmpeg 认不出 .tmp 格式，这里会炸
            with wave.open(str(out), "rb") as w:
                self.assertEqual(w.getframerate(), 16000)
                self.assertEqual(w.getnchannels(), 1)
                self.assertGreater(w.getnframes(), 0)


if __name__ == "__main__":
    unittest.main()
