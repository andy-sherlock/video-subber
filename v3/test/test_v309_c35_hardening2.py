"""V3.09-C3.5 加固批第二轮（Codex 复审新发现 P1×3 + P2，管道 ㉜ 步）。

- P1-a: manifest 路径穿越没闭环——日常 ready 判定（structure_ok）没查路径安全，
  被篡改成 ../ 且外部文件 size 对上时 is_model_ready 误判 True。
- P1-b: repair 占位后 _plan_repair 抛异常没 finally 清理，_repair_state 卡 repairing。
- P1-c: Whisper 分支 stop 只在入口查一次，start_download 之后不再查，残漏窗口。
- P2: delete 接口没纳入 prepare/repair 互斥，与整模型修复并发写同目录。
"""
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def _reset_prepare_state():
    from backend import prepare_manager as pm
    pm._prepare.update({
        "running": False, "stop": False, "queue": [], "index": 0,
        "current": None, "states": {}, "unfixable": [], "error": None,
        "whisper_model_id": None,
    })
    pm._repair_state.clear()


class _RegistryTmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.models_root = root / "managed"
        from backend import model_manager
        from backend import model_registry as reg
        self._patches = [
            patch.object(reg, "MODELS_ROOT", self.models_root),
            patch.object(model_manager, "MODELSCOPE_CACHE_DIR", root / "ms"),
            patch.object(model_manager, "CACHE_DIR", root / "hf"),
        ]
        for p in self._patches:
            p.start()
        _reset_prepare_state()

    def tearDown(self):
        _reset_prepare_state()
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def _make_managed_ready(self, key):
        from backend import model_registry as reg
        entry = reg.get_entry(key)
        d = Path(entry["local_dir"])
        d.mkdir(parents=True, exist_ok=True)
        (d / entry["probe_file"]).write_bytes(b"probe-content")
        (d / "model.safetensors").write_bytes(b"weights-content")
        reg.write_manifest_v2(key)
        return d


# ═══════════════════════════════════════════════════════════════════
# P1-a: 日常 ready 判定必须查路径安全
# ═══════════════════════════════════════════════════════════════════

class TestReadyCheckRejectsTraversal(_RegistryTmp):

    def test_tampered_manifest_path_not_ready(self):
        from backend import model_registry as reg
        entry = reg.get_entry("qwen_asr")
        d = Path(entry["local_dir"])
        d.mkdir(parents=True)
        # 目录之外放一个 size=5 的文件，manifest 用 ../ 指向它
        outside = self.models_root / "outside.bin"
        outside.write_bytes(b"12345")
        (d / "manifest.json").write_text(json.dumps({
            "status": "ready", "version": 2, "repo": entry["repo"],
            "files": [{"path": "../outside.bin", "size": 5, "sha256": "x"}],
        }))
        self.assertFalse(reg.is_model_ready("qwen_asr"),
                         "manifest 路径穿越不得被判 ready")
        with self.assertRaises(reg.ModelNotReady):
            reg.resolve_model_path("qwen_asr")

    def test_structure_ok_false_on_unsafe_path(self):
        from backend import model_manifest as mm
        d = self.models_root / "x"
        d.mkdir(parents=True)
        manifest = {"files": [{"path": "../evil", "size": 1, "sha256": "y"}]}
        self.assertFalse(mm.structure_ok(d, manifest))

    def test_missing_size_field_counts_as_corrupt(self):
        from backend import model_manifest as mm
        d = self.models_root / "y"
        d.mkdir(parents=True)
        (d / "model.pt").write_bytes(b"w")
        manifest = {"files": [{"path": "model.pt"}]}  # 缺 size/sha256
        self.assertEqual(mm.find_corrupt_files(d, manifest, check_hash=False),
                         ["model.pt"])

    def test_missing_sha256_field_not_ready_even_without_hashing(self):
        """日常 ready 不算 sha256，但 sha256 字段本身必须存在。"""
        from backend import model_registry as reg
        entry = reg.get_entry("sensevoice")
        d = Path(entry["local_dir"])
        d.mkdir(parents=True)
        (d / entry["probe_file"]).write_bytes(b"p")
        (d / "model.pt").write_bytes(b"w")
        (d / "manifest.json").write_text(json.dumps({
            "status": "ready", "version": 2, "repo": entry["repo"],
            "files": [{"path": "model.pt", "size": 1}],  # 缺 sha256
        }))
        self.assertFalse(reg.is_model_ready("sensevoice"),
                         "缺 sha256 字段的 manifest 不得被判 ready")


# ═══════════════════════════════════════════════════════════════════
# P1-b: repair 占位后异常必须复位，不能永久卡 repairing
# ═══════════════════════════════════════════════════════════════════

class TestRepairStateCleanupOnError(_RegistryTmp):

    def test_plan_exception_clears_repair_state(self):
        from backend import prepare_manager as pm
        d = self._make_managed_ready("sensevoice")
        (d / "model.safetensors").unlink()
        with patch.object(pm, "_plan_repair", side_effect=KeyError("size")):
            result = pm.repair_model(target="sensevoice")
        self.assertIn("error", result)
        self.assertFalse(pm._repair_state.get("repairing"),
                         "_plan_repair 抛异常后必须复位，否则后续永久拒绝")

    def test_second_repair_works_after_plan_exception(self):
        from backend import prepare_manager as pm
        from backend import model_registry as reg
        d = self._make_managed_ready("sensevoice")
        (d / "model.safetensors").unlink()
        with patch.object(pm, "_plan_repair", side_effect=RuntimeError("boom")):
            pm.repair_model(target="sensevoice")
        # 第二次不应被"正在下载/修复中"永久拒绝
        with patch.object(pm, "_start_thread"):
            r2 = pm.repair_model(target="sensevoice")
        self.assertNotIn("正在下载/修复中", str(r2.get("error", "")))

    def test_malformed_manifest_missing_size_goes_full(self):
        """缺 size 字段的 manifest → 不 KeyError，转整模型兜底。"""
        from backend import prepare_manager as pm
        from backend import model_registry as reg
        entry = reg.get_entry("sensevoice")
        d = Path(entry["local_dir"])
        d.mkdir(parents=True)
        (d / entry["probe_file"]).write_bytes(b"p")
        (d / "manifest.json").write_text(json.dumps({
            "status": "ready", "version": 2, "repo": entry["repo"],
            "files": [{"path": "model.pt"}],  # 缺 size
        }))
        plan = pm._plan_repair("sensevoice")
        self.assertEqual(plan["mode"], "full")


# ═══════════════════════════════════════════════════════════════════
# P1-c: Whisper 分支 stop 补查
# ═══════════════════════════════════════════════════════════════════

class TestWhisperStopWindow(unittest.TestCase):

    def setUp(self):
        _reset_prepare_state()

    def tearDown(self):
        _reset_prepare_state()

    def test_whisper_stop_after_start_download_cancels(self):
        from backend import model_manager
        from backend import prepare_manager as pm
        pm._prepare.update({"stop": False, "whisper_model_id": "large-v2",
                            "states": {}})

        def fake_start(mid):
            pm._prepare["stop"] = True  # stop 落在 start_download 之后
            return {"ok": True, "progress": 0}

        with patch.object(model_manager, "start_download",
                          side_effect=fake_start), \
             patch.object(model_manager, "cancel_download") as cancel:
            pm._download_one("whisper")
        cancel.assert_called_once_with("large-v2")


# ═══════════════════════════════════════════════════════════════════
# P2: delete 与 prepare/repair 互斥
# ═══════════════════════════════════════════════════════════════════

class TestDeleteConcurrencyGuard(unittest.TestCase):

    def setUp(self):
        _reset_prepare_state()

    def tearDown(self):
        _reset_prepare_state()

    def test_delete_rejected_while_repairing(self):
        from backend import main, prepare_manager as pm
        pm._repair_state["repairing"] = True
        result = main.model_delete("large-v2")
        self.assertIn("error", result)

    def test_delete_rejected_while_preparing(self):
        from backend import main, prepare_manager as pm
        pm._prepare["running"] = True
        result = main.model_delete("large-v2")
        self.assertIn("error", result)

    def test_op_in_progress_helper(self):
        from backend import prepare_manager as pm
        self.assertFalse(pm.is_model_op_in_progress())
        pm._prepare["running"] = True
        self.assertTrue(pm.is_model_op_in_progress())

    def test_prepare_cannot_start_during_delete_check_delete_window(self):
        """delete 的检查+删除期间，prepare 不允许插队启动。"""
        from backend import main, model_manager, prepare_manager as pm

        spawned_during_delete = {}
        prepare_thread = {}

        def fake_delete(_model_id):
            def try_start_prepare():
                pm.start_prepare()

            t = threading.Thread(target=try_start_prepare)
            prepare_thread["thread"] = t
            t.start()
            time.sleep(0.05)
            spawned_during_delete["value"] = (
                pm._prepare["running"] or bool(spawn.call_args_list)
            )
            return {"ok": True, "deleted": True}

        assessment = {"missing": ["sensevoice"], "ready": [], "unfixable": []}
        with patch.object(pm, "assess_environment", return_value=assessment), \
             patch.object(pm, "_start_thread") as spawn, \
             patch.object(model_manager, "delete_model", side_effect=fake_delete), \
             patch.object(main, "_has_running_jobs", return_value=False):
            result = main.model_delete("large-v2")
            prepare_thread["thread"].join(timeout=1)

        self.assertTrue(result["ok"])
        self.assertFalse(
            spawned_during_delete["value"],
            "delete 检查通过到真正删除之间，prepare 不应插队启动",
        )
        self.assertTrue(spawn.called, "delete 完成后 prepare 可以继续启动")


if __name__ == "__main__":
    unittest.main()
