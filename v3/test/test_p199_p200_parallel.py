"""P199+P200+I6: Parallelism decision logic must decouple SV+FA from Qwen flag.
Replaces fragile inspect.getsource tests with behavioral tests."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestParallelDecisions(unittest.TestCase):
    """Test the decoupled parallelism decision functions."""

    def test_svfa_parallel_in_standard_mode(self):
        """SV+FA must use threads even in 'standard' tier with enough RAM."""
        from backend.transcriber_v2 import should_parallel_svfa
        self.assertTrue(should_parallel_svfa("standard", 32),
                        "standard + 32GB must enable SV+FA parallel")

    def test_svfa_parallel_in_precise_mode(self):
        """SV+FA must use threads in 'precise' tier too."""
        from backend.transcriber_v2 import should_parallel_svfa
        self.assertTrue(should_parallel_svfa("precise", 32),
                        "precise + 32GB must enable SV+FA parallel")

    def test_svfa_serial_low_ram(self):
        """SV+FA must NOT use threads when RAM is too low."""
        from backend.transcriber_v2 import should_parallel_svfa
        self.assertFalse(should_parallel_svfa("standard", 6),
                         "6GB RAM must disable SV+FA parallel")

    def test_qwen_pipeline_standard_and_precise(self):
        """Qwen ASR pipeline: standard and precise tiers with enough RAM."""
        from backend.transcriber_v2 import should_qwen_pipeline
        self.assertTrue(should_qwen_pipeline("precise", 32),
                        "precise + 32GB must enable Qwen pipeline")
        self.assertTrue(should_qwen_pipeline("standard", 32),
                        "standard + 32GB must enable Qwen pipeline")

    def test_qwen_pipeline_low_ram(self):
        """Qwen ASR pipeline needs enough RAM even in precise."""
        from backend.transcriber_v2 import should_qwen_pipeline
        self.assertFalse(should_qwen_pipeline("precise", 8),
                         "precise + 8GB must NOT enable Qwen pipeline (needs 16GB)")


class TestSVFALaunchTiming(unittest.TestCase):
    """Architecture: Qwen runs independently, SV+FA launch after Qwen joins.

    New flow (Step 6 independent Qwen):
      1. Qwen thread starts BEFORE Whisper iteration (parallel)
      2. Whisper iterates segments
      3. Qwen thread joins (need timestamps to decide FA skip)
      4. SV (+FA if PyTorch) launch
    SV must appear AFTER qwen_thread.join() because it needs the results list.
    """

    def test_qwen_independent_launch_before_whisper_loop(self):
        """Qwen thread must start before the full Whisper transcription stage.

        V3.08 复杂度拆分：全量 Whisper 转录移入 _run_full_whisper 辅助函数；
        Qwen 并行线程仍在 transcribe() 内联启动，须先于全量 Whisper 调用。"""
        import inspect
        from backend import transcriber_v2 as tv
        src = inspect.getsource(tv.transcribe)

        qwen_start_pos = src.find("qwen_thread = threading.Thread")
        whisper_stage_pos = src.find("_run_full_whisper")

        self.assertNotEqual(qwen_start_pos, -1, "Qwen 并行线程启动应在 transcribe() 内联")
        self.assertNotEqual(whisper_stage_pos, -1, "全量 Whisper 调用应在 transcribe() 内")
        self.assertLess(qwen_start_pos, whisper_stage_pos,
                        "Qwen thread must start BEFORE full Whisper transcription")

    def test_sv_launch_after_qwen_join(self):
        """SV launches after Qwen is resolved (needs timestamp check to skip FA).

        V3.08 复杂度拆分：Qwen 等待/join 移入 _resolve_routing，SV 派发移入
        _run_sensevoice_and_fa；前者须先于后者调用。"""
        import inspect
        from backend import transcriber_v2 as tv
        src = inspect.getsource(tv.transcribe)

        qwen_resolve_pos = src.find("_resolve_routing")
        sv_launch_pos = src.find("_run_sensevoice_and_fa")

        self.assertNotEqual(qwen_resolve_pos, -1, "Qwen 路由/join 应在 transcribe() 内调用")
        self.assertNotEqual(sv_launch_pos, -1, "SV 派发应在 transcribe() 内调用")
        self.assertLess(qwen_resolve_pos, sv_launch_pos,
                        "SV launch (_run_sensevoice_and_fa) must happen AFTER Qwen "
                        "resolution (_resolve_routing, which joins the Qwen thread)")


if __name__ == '__main__':
    unittest.main()
