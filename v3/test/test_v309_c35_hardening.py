"""V3.09-C3.5 加固批（Codex 审查阻塞 1-4，管道 ㉛ 步）。

P317 manifest 路径穿越、P318 并发锁、P319 停止竞态、P320 删旧缓存回退、
P321/P322 非阻塞项已按 ㉚ 定稿撤回，留 C4。
"""
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

BACKEND_DIR = Path(__file__).parent.parent / "backend"


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
# P317 — manifest 路径穿越
# ═══════════════════════════════════════════════════════════════════

class TestManifestPathSafety(unittest.TestCase):

    def test_safe_member_accepts_normal_relative(self):
        from backend import model_manifest as mm
        base = Path("/tmp/model")
        self.assertEqual(mm.safe_member(base, "sub/config.json"),
                         base / "sub" / "config.json")

    def test_safe_member_rejects_parent_traversal(self):
        from backend import model_manifest as mm
        with self.assertRaises(ValueError):
            mm.safe_member(Path("/tmp/model"), "../evil")

    def test_safe_member_rejects_absolute(self):
        from backend import model_manifest as mm
        with self.assertRaises(ValueError):
            mm.safe_member(Path("/tmp/model"), "/etc/passwd")

    def test_safe_member_rejects_empty(self):
        from backend import model_manifest as mm
        with self.assertRaises(ValueError):
            mm.safe_member(Path("/tmp/model"), "")

    def test_manifest_paths_safe_flags_tampered(self):
        from backend import model_manifest as mm
        good = {"files": [{"path": "config.json", "size": 1, "sha256": "x"}]}
        bad = {"files": [{"path": "../x", "size": 1, "sha256": "x"}]}
        self.assertTrue(mm.manifest_paths_safe(good))
        self.assertFalse(mm.manifest_paths_safe(bad))


class TestRepairRejectsTamperedManifest(_RegistryTmp):

    def test_tampered_manifest_falls_back_to_full_not_unlink(self):
        """含 ../ 路径的 manifest → 不做文件级 unlink，转整模型兜底。"""
        from backend import prepare_manager as pm
        from backend import model_registry as reg
        entry = reg.get_entry("sensevoice")
        d = Path(entry["local_dir"])
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({
            "status": "ready", "version": 2, "repo": entry["repo"],
            "files": [{"path": "../../evil", "size": 1, "sha256": "x"}],
        }))
        plan = pm._plan_repair("sensevoice")
        self.assertEqual(plan["mode"], "full")
        self.assertEqual(plan["bad_files"], [])


# ═══════════════════════════════════════════════════════════════════
# P318 — 并发锁：repairing 占位先于慢校验
# ═══════════════════════════════════════════════════════════════════

class TestConcurrencyGuard(_RegistryTmp):

    def test_repairing_flag_set_before_slow_plan(self):
        """repair_model 必须在 _plan_repair（慢 sha256）之前就置 repairing=True。"""
        from backend import prepare_manager as pm
        d = self._make_managed_ready("sensevoice")
        (d / "model.safetensors").unlink()
        seen = {}

        real_plan = pm._plan_repair

        def spy_plan(*a, **kw):
            seen["repairing_during_plan"] = pm._repair_state.get("repairing")
            return real_plan(*a, **kw)

        with patch.object(pm, "_plan_repair", side_effect=spy_plan), \
             patch.object(pm, "_start_thread"):
            pm.repair_model(target="sensevoice")
        self.assertTrue(seen["repairing_during_plan"],
                        "慢校验运行时 repairing 标志必须已置位（否则并发窗口）")

    def test_second_repair_while_repairing_rejected(self):
        from backend import prepare_manager as pm
        pm._repair_state["repairing"] = True
        result = pm.repair_model(target="sensevoice")
        self.assertIn("error", result)

    def test_prepare_rejected_while_repairing(self):
        from backend import prepare_manager as pm
        pm._repair_state["repairing"] = True
        with patch.object(pm, "assess_environment",
                          return_value={"missing": ["qwen_asr"],
                                        "ready": [], "unfixable": []}), \
             patch.object(pm, "_start_thread") as spawn:
            result = pm.start_prepare()
        self.assertIn("error", result)
        spawn.assert_not_called()

    def test_model_op_lock_exists(self):
        from backend import prepare_manager as pm
        self.assertIsInstance(pm._MODEL_OP_LOCK,
                              type(threading.Lock()))


# ═══════════════════════════════════════════════════════════════════
# P319 — 停止竞态：stop 已置位时不再启动下一项
# ═══════════════════════════════════════════════════════════════════

class TestStopRace(_RegistryTmp):

    def test_download_one_skips_when_stop_already_set(self):
        from backend import prepare_manager as pm
        pm._prepare["stop"] = True
        with patch("multiprocessing.Process") as proc:
            pm._download_one("sensevoice")
        proc.assert_not_called()

    def test_run_prepare_stops_before_next_item(self):
        """stop 在项间到达 → 后续项不启动进程。"""
        from backend import prepare_manager as pm
        pm._prepare.update({"queue": ["qwen_asr", "sensevoice"], "index": 0})
        started = []

        def fake_download_one(key):
            started.append(key)
            pm._prepare["stop"] = True  # 第一项跑完时用户点了停止

        with patch.object(pm, "_download_one", side_effect=fake_download_one):
            pm._run_prepare()
        self.assertEqual(started, ["qwen_asr"], "停止后第二项不应启动")


# ═══════════════════════════════════════════════════════════════════
# P320 — 删除旧共享缓存回退（只认自管目录）
# ═══════════════════════════════════════════════════════════════════

class TestNoLegacyFallback(_RegistryTmp):

    def test_modelscope_cache_no_longer_resolves(self):
        from backend import model_registry as reg
        legacy = self.ms_cache / "models" / "Qwen" / "Qwen3-ASR-1.7B"
        legacy.mkdir(parents=True)
        (legacy / "config.json").write_bytes(b"{}")
        with self.assertRaises(reg.ModelNotReady):
            reg.resolve_model_path("qwen_asr")

    def test_hf_cache_no_longer_resolves(self):
        from backend import model_registry as reg
        snap = (self.hf_cache / "models--Qwen--Qwen3-ForcedAligner-0.6B"
                / "snapshots" / "abc")
        snap.mkdir(parents=True)
        (snap / "config.json").write_bytes(b"{}")
        with self.assertRaises(reg.ModelNotReady):
            reg.resolve_model_path("qwen_fa")

    def test_managed_still_resolves(self):
        from backend import model_registry as reg
        d = self._make_managed_ready("sensevoice")
        self.assertEqual(reg.resolve_model_path("sensevoice"), str(d))

    def test_is_model_resolvable_equals_managed_ready(self):
        from backend import model_registry as reg
        legacy = self.ms_cache / "models" / "iic" / "SenseVoiceSmall"
        legacy.mkdir(parents=True)
        (legacy / "model.pt").write_bytes(b"x")
        self.assertFalse(reg.is_model_resolvable("sensevoice"))
        self._make_managed_ready("sensevoice")
        self.assertTrue(reg.is_model_resolvable("sensevoice"))

    def test_legacy_dir_helper_removed(self):
        from backend import model_registry as reg
        self.assertFalse(hasattr(reg, "_legacy_dir"),
                         "旧缓存回退 helper 应已删除")


# 注：P321（delete rmtree 兜底）/ P322（worker 缺模型文案）是 3 个非阻塞项里的两个，
# 已按定稿撤回本批、留 C4 与 interrupted 弹窗一起实现（管道 ㉚ 定稿 + ㉜ 撤回记录），
# 故此处不再测试；本批只认领阻塞项 P317-P320 + 复审 P1×3/P2。


if __name__ == "__main__":
    unittest.main()
