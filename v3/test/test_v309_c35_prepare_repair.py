"""V3.09-C3.5 交货 ⑤-⑥：/api/prepare 一键准备 + /api/repair 文件级修复。

规格来源：pipelines/frontend-pipeline-2026-07-01-v309-c3-ui-redesign.md ㉔+㉕+㉗。
- prepare：检测四模型缺哪个下哪个，幂等；ffmpeg/yt-dlp 只检测归 unfixable。
- prepare stop：保留 partial，不写 ready manifest。
- /api/models/status 扩展四模型状态（current_item/item_index/total/percent/speed_text/overall_percent）。
- repair：manifest v2 → 文件级定位（先删坏文件 → allow_patterns 只补 → 复验）；
  manifest 缺失/旧 v1/复验仍坏/旧共享缓存 → 整模型兜底。
- allow_patterns 静默 0 文件 → 复验失败转兜底。
- P316：repair/delete 遇运行中任务拒绝。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def _reset_prepare_state():
    from backend import prepare_manager as pm
    pm._prepare.update({
        "running": False, "stop": False, "queue": [], "index": 0,
        "current": None, "states": {}, "unfixable": [], "error": None,
    })
    pm._repair_state.clear()


class _RegistryTmpDirs(unittest.TestCase):
    """公共脚手架：把自管目录/两个旧缓存都指到临时目录。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.models_root = root / "managed"
        self.ms_cache = root / "ms-cache"
        self.hf_cache = root / "hf-cache"
        from backend import model_manager
        from backend import model_registry as reg
        self._patches = [
            patch.object(reg, "MODELS_ROOT", self.models_root),
            patch.object(model_manager, "MODELSCOPE_CACHE_DIR", self.ms_cache),
            patch.object(model_manager, "CACHE_DIR", self.hf_cache),
        ]
        for p in self._patches:
            p.start()
        _reset_prepare_state()

    def tearDown(self):
        _reset_prepare_state()
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def _make_managed_ready(self, key: str):
        from backend import model_registry as reg
        entry = reg.get_entry(key)
        d = Path(entry["local_dir"])
        d.mkdir(parents=True, exist_ok=True)
        (d / entry["probe_file"]).write_bytes(b"probe-content")
        (d / "model.safetensors").write_bytes(b"weights-content")
        reg.write_manifest_v2(key)
        return d


# ═══════════════════════════════════════════════════════════════════
# ⑤ /api/prepare
# ═══════════════════════════════════════════════════════════════════

class TestAssessEnvironment(unittest.TestCase):

    def test_missing_and_ready_split_in_fixed_order(self):
        from backend import prepare_manager as pm
        resolvable = {"whisper": True, "qwen_asr": False,
                      "qwen_fa": False, "sensevoice": True}
        with patch("backend.model_registry.is_model_resolvable",
                   side_effect=lambda k, *a, **kw: resolvable[k]), \
             patch("backend.env_check.check_ffmpeg",
                   return_value={"installed": True}), \
             patch("backend.env_check.check_ytdlp",
                   return_value={"ready": True}):
            result = pm.assess_environment()
        self.assertEqual(result["missing"], ["qwen_asr", "qwen_fa"])
        self.assertEqual(result["ready"], ["whisper", "sensevoice"])
        self.assertEqual(result["unfixable"], [])

    def test_ffmpeg_ytdlp_detect_only_go_unfixable(self):
        from backend import prepare_manager as pm
        with patch("backend.model_registry.is_model_resolvable",
                   return_value=True), \
             patch("backend.env_check.check_ffmpeg",
                   return_value={"installed": False}), \
             patch("backend.env_check.check_ytdlp",
                   return_value={"ready": False}):
            result = pm.assess_environment()
        keys = {u["key"] for u in result["unfixable"]}
        self.assertEqual(keys, {"ffmpeg", "ytdlp"})
        for u in result["unfixable"]:
            self.assertTrue(u["reason"])


class TestStartStopPrepare(unittest.TestCase):

    def setUp(self):
        _reset_prepare_state()

    def tearDown(self):
        _reset_prepare_state()

    def test_start_queues_only_missing(self):
        from backend import prepare_manager as pm
        assessment = {"missing": ["qwen_asr", "sensevoice"],
                      "ready": ["whisper", "qwen_fa"], "unfixable": []}
        with patch.object(pm, "assess_environment", return_value=assessment), \
             patch.object(pm, "_start_thread") as spawn:
            result = pm.start_prepare()
        self.assertEqual(result["queued"], ["qwen_asr", "sensevoice"])
        self.assertEqual(result["already_ready"], ["whisper", "qwen_fa"])
        spawn.assert_called_once()

    def test_all_ready_starts_nothing(self):
        from backend import prepare_manager as pm
        assessment = {"missing": [], "ready": list(pm.MODEL_ORDER),
                      "unfixable": []}
        with patch.object(pm, "assess_environment", return_value=assessment), \
             patch.object(pm, "_start_thread") as spawn:
            result = pm.start_prepare()
        self.assertEqual(result["queued"], [])
        spawn.assert_not_called()

    def test_repeat_call_while_running_does_not_requeue(self):
        """幂等：重复调用只补缺失项，不重复起线程。"""
        from backend import prepare_manager as pm
        assessment = {"missing": ["qwen_asr"], "ready": [], "unfixable": []}
        with patch.object(pm, "assess_environment", return_value=assessment), \
             patch.object(pm, "_start_thread") as spawn:
            pm.start_prepare()
            second = pm.start_prepare()
        spawn.assert_called_once()
        self.assertTrue(second["running"])

    def test_stop_keeps_partial_and_no_manifest(self):
        """停止下载：state 归 cancelled，partial 文件保留，不写 ready manifest。"""
        import multiprocessing
        from backend import model_manager
        from backend import prepare_manager as pm
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        from backend import model_registry as reg
        with patch.object(reg, "MODELS_ROOT", Path(tmp.name)):
            entry = reg.get_entry("sensevoice")
            target = Path(entry["local_dir"])
            stop_event = multiprocessing.Event()

            def fake_download(repo, tgt, **kwargs):
                (Path(tgt) / "partial.bin").write_bytes(b"half")
                stop_event.set()
                raise model_manager.DownloadStopped("用户已停止下载")

            state = {}
            with patch.object(pm, "_snapshot_download",
                              side_effect=fake_download):
                pm._download_registry_model("sensevoice", state, stop_event)
            self.assertTrue(state.get("cancelled"))
            self.assertTrue((target / "partial.bin").exists(), "partial 应保留")
            self.assertFalse((target / "manifest.json").exists(),
                             "停止后不得写 ready manifest")

    def test_download_success_writes_manifest_v2(self):
        from backend import prepare_manager as pm
        from backend import model_registry as reg
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch.object(reg, "MODELS_ROOT", Path(tmp.name)):
            entry = reg.get_entry("sensevoice")
            target = Path(entry["local_dir"])

            def fake_download(repo, tgt, **kwargs):
                (Path(tgt) / entry["probe_file"]).write_bytes(b"probe")
                (Path(tgt) / "model.pt.extra").write_bytes(b"weights")

            state = {}
            with patch.object(pm, "_snapshot_download",
                              side_effect=fake_download):
                pm._download_registry_model("sensevoice", state)
            self.assertIsNone(state.get("error"))
            manifest = json.loads((target / "manifest.json").read_text())
            self.assertEqual(manifest["version"], 2)
            self.assertTrue(manifest["files"])
            self.assertTrue(reg.is_model_ready("sensevoice"))


class TestPrepareStatusChannel(unittest.TestCase):

    def setUp(self):
        _reset_prepare_state()

    def tearDown(self):
        _reset_prepare_state()

    def test_status_has_contract_fields(self):
        from backend import prepare_manager as pm
        with patch("backend.model_registry.is_model_resolvable",
                   return_value=False):
            status = pm.get_prepare_status()
        for field in ("running", "current_item", "item_index", "total",
                      "percent", "speed_text", "overall_percent", "models"):
            self.assertIn(field, status)
        self.assertEqual(set(status["models"]), set(pm.MODEL_ORDER))
        for key, info in status["models"].items():
            self.assertIn("display_name", info)
            self.assertIn("ready", info)
            self.assertIn("size_bytes", info)

    def test_api_models_status_backward_compatible_plus_prepare(self):
        """/api/models/status：旧 whisper 型号键保留，新增 prepare 四模型通道。"""
        from backend import main
        from backend import model_manager
        result = main.model_status()
        for mid in model_manager.MODELS:
            self.assertIn(mid, result)
        self.assertIn("prepare", result)
        self.assertIn("models", result["prepare"])


# ═══════════════════════════════════════════════════════════════════
# ⑥ /api/repair：文件级优先 + 整模型兜底
# ═══════════════════════════════════════════════════════════════════

class TestRepairPlan(_RegistryTmpDirs):

    def test_plan_locates_deleted_file(self):
        from backend import prepare_manager as pm
        d = self._make_managed_ready("sensevoice")
        (d / "model.safetensors").unlink()
        plan = pm._plan_repair("sensevoice")
        self.assertEqual(plan["mode"], "file")
        self.assertEqual(plan["bad_files"], ["model.safetensors"])
        self.assertEqual(plan["download_size_bytes"], len(b"weights-content"))

    def test_plan_locates_same_size_tamper(self):
        from backend import prepare_manager as pm
        d = self._make_managed_ready("sensevoice")
        (d / "model.safetensors").write_bytes(b"weights-CONTENT")
        plan = pm._plan_repair("sensevoice")
        self.assertEqual(plan["mode"], "file")
        self.assertEqual(plan["bad_files"], ["model.safetensors"])

    def test_plan_locates_size_mismatch(self):
        from backend import prepare_manager as pm
        d = self._make_managed_ready("sensevoice")
        (d / "model.safetensors").write_bytes(b"x")
        plan = pm._plan_repair("sensevoice")
        self.assertEqual(plan["mode"], "file")
        self.assertEqual(plan["bad_files"], ["model.safetensors"])

    def test_plan_manifest_missing_goes_full(self):
        from backend import prepare_manager as pm
        from backend import model_registry as reg
        entry = reg.get_entry("sensevoice")
        Path(entry["local_dir"]).mkdir(parents=True)
        plan = pm._plan_repair("sensevoice")
        self.assertEqual(plan["mode"], "full")
        self.assertEqual(plan["download_size_bytes"], entry["size_bytes"])

    def test_plan_old_v1_manifest_goes_full(self):
        from backend import prepare_manager as pm
        from backend import model_registry as reg
        entry = reg.get_entry("sensevoice")
        d = Path(entry["local_dir"])
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps(
            {"status": "ready", "repo": entry["repo"]}))  # v1：无 files
        plan = pm._plan_repair("sensevoice")
        self.assertEqual(plan["mode"], "full")

    def test_plan_intact_model_reports_already_ready(self):
        from backend import prepare_manager as pm
        self._make_managed_ready("sensevoice")
        plan = pm._plan_repair("sensevoice")
        self.assertEqual(plan.get("status"), "already_ready")

    def test_plan_legacy_cache_broken_goes_full_to_managed(self):
        """坏的是旧共享缓存 → 只读不删，全量下到自管目录（㉗ 兜底分支）。"""
        from backend import prepare_manager as pm
        legacy = self.ms_cache / "models" / "iic" / "SenseVoiceSmall"
        legacy.mkdir(parents=True)
        (legacy / "model.pt").write_bytes(b"old-cache-weights")
        plan = pm._plan_repair("sensevoice")
        self.assertEqual(plan["mode"], "full")
        self.assertIn(str(self.models_root), str(plan["entry"]["local_dir"]))


class TestRepairExecute(_RegistryTmpDirs):

    def test_file_mode_deletes_bad_redownloads_and_reverifies(self):
        from backend import prepare_manager as pm
        d = self._make_managed_ready("sensevoice")
        (d / "model.safetensors").write_bytes(b"weights-CONTENT")  # 同尺寸篡改
        from backend import model_registry as reg
        good = d / reg.get_entry("sensevoice")["probe_file"]
        mtime_before = good.stat().st_mtime_ns

        calls = []

        def fake_download(repo, tgt, allow_patterns=None, **kwargs):
            calls.append(allow_patterns)
            self.assertFalse((Path(tgt) / "model.safetensors").exists(),
                             "必须先删坏文件再补下载（SDK 不自愈同尺寸坏文件）")
            (Path(tgt) / "model.safetensors").write_bytes(b"weights-content")

        plan = pm._plan_repair("sensevoice")
        state = {}
        with patch.object(pm, "_snapshot_download", side_effect=fake_download):
            pm._execute_repair(plan, state)

        self.assertEqual(calls, [["model.safetensors"]],
                         "allow_patterns 应只含坏文件")
        self.assertTrue(state.get("ready"))
        self.assertFalse(state.get("fallback"))
        self.assertEqual(good.stat().st_mtime_ns, mtime_before,
                         "好文件零触碰")

    def test_silent_zero_files_reverify_fails_then_fallback(self):
        """allow_patterns 静默 0 文件 → 复验失败 → 转整模型兜底（㉗ SDK 行为 3）。"""
        from backend import prepare_manager as pm
        d = self._make_managed_ready("sensevoice")
        (d / "model.safetensors").write_bytes(b"weights-CONTENT")

        def fake_download(repo, tgt, allow_patterns=None, **kwargs):
            if allow_patterns:
                return  # SDK 静默 0 文件：不报错、什么都不下
            # 兜底全量：写齐所有文件
            from backend import model_registry as reg
            entry = reg.get_entry("sensevoice")
            (Path(tgt) / entry["probe_file"]).write_bytes(b"probe-content")
            (Path(tgt) / "model.safetensors").write_bytes(b"weights-content")

        plan = pm._plan_repair("sensevoice")
        state = {}
        with patch.object(pm, "_snapshot_download", side_effect=fake_download):
            pm._execute_repair(plan, state)
        self.assertTrue(state.get("fallback"), "复验仍坏必须转兜底")
        self.assertTrue(state.get("ready"))
        from backend import model_registry as reg
        self.assertTrue(reg.is_model_ready("sensevoice"))

    def test_full_mode_rewrites_manifest_v2(self):
        from backend import prepare_manager as pm
        from backend import model_registry as reg
        entry = reg.get_entry("sensevoice")
        d = Path(entry["local_dir"])
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps(
            {"status": "ready", "repo": entry["repo"]}))  # 旧 v1

        def fake_download(repo, tgt, allow_patterns=None, **kwargs):
            (Path(tgt) / entry["probe_file"]).write_bytes(b"probe-content")
            (Path(tgt) / "model.safetensors").write_bytes(b"weights-content")

        plan = pm._plan_repair("sensevoice")
        state = {}
        with patch.object(pm, "_snapshot_download", side_effect=fake_download):
            pm._execute_repair(plan, state)
        self.assertTrue(state.get("ready"))
        manifest = json.loads((d / "manifest.json").read_text())
        self.assertEqual(manifest["version"], 2)
        self.assertTrue(manifest["files"])


class TestRepairEntryPoint(_RegistryTmpDirs):

    def test_repair_returns_contract_fields(self):
        from backend import prepare_manager as pm
        d = self._make_managed_ready("sensevoice")
        (d / "model.safetensors").unlink()
        with patch.object(pm, "_start_thread") as spawn:
            result = pm.repair_model(target="sensevoice")
        self.assertEqual(result["target"], "sensevoice")
        self.assertEqual(result["download_size_bytes"], len(b"weights-content"))
        self.assertTrue(result["job_id"])
        spawn.assert_called_once()

    def test_repair_without_target_multiple_broken_is_ambiguous(self):
        """无 target 且坏项不唯一 → ambiguous 候选列表，不自动修不猜（㉕）。"""
        from backend import prepare_manager as pm
        with patch("backend.model_registry.is_model_resolvable",
                   return_value=False), \
             patch.object(pm, "_start_thread") as spawn:
            result = pm.repair_model(target=None)
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(len(result["candidates"]), 4)
        for c in result["candidates"]:
            for field in ("model_key", "display_name", "reason",
                          "download_size_bytes"):
                self.assertIn(field, c)
        spawn.assert_not_called()

    def test_repair_rejected_while_preparing(self):
        from backend import prepare_manager as pm
        pm._prepare["running"] = True
        result = pm.repair_model(target="sensevoice")
        self.assertIn("error", result)


class TestRunningJobGuardP316(unittest.TestCase):
    """P316：有运行中任务时 repair/delete 拒绝并提示。"""

    def setUp(self):
        from backend import main
        self._saved = dict(main.GLOBAL_HASH_INDEX)
        main.GLOBAL_HASH_INDEX.clear()
        main.GLOBAL_HASH_INDEX["tk1"] = {
            "job_id": "j1", "status": "running", "config": {}}

    def tearDown(self):
        from backend import main
        main.GLOBAL_HASH_INDEX.clear()
        main.GLOBAL_HASH_INDEX.update(self._saved)

    def test_repair_rejected_when_job_running(self):
        from backend import main
        result = main.api_repair({"target": "sensevoice"})
        self.assertIn("error", result)
        self.assertIn("任务", result["error"])

    def test_delete_rejected_when_job_running(self):
        from backend import main
        result = main.model_delete("large-v2")
        self.assertIn("error", result)
        self.assertIn("任务", result["error"])

    def test_allowed_after_jobs_finish(self):
        from backend import main
        main.GLOBAL_HASH_INDEX["tk1"]["status"] = "done"
        self.assertFalse(main._has_running_jobs())


if __name__ == "__main__":
    unittest.main()
