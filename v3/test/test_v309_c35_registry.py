"""V3.09-C3.5 交货 ①-④：模型注册表 / 运行时加载改走注册表 / manifest v2 / env 升级。

规格来源：pipelines/frontend-pipeline-2026-07-01-v309-c3-ui-redesign.md ㉔+㉕+㉖+㉗+㉘。
- 注册表四模型（whisper/qwen_asr/qwen_fa/sensevoice）字段完整，Windows 产品口径仓库。
- 三个 runner 不再硬编码仓库名，加载前走 resolve_model_path(model_key)。
- resolve 顺序：自管目录 → 旧缓存只读回退 → 报错引导一键准备（禁止运行时联网下载）。
- manifest v2 写 files:[{path,size,sha256}]，排除点号开头/临时/manifest 自身。
- 三种破坏（删文件/同尺寸篡改/异尺寸篡改）都能定位。
- /api/env 不再把 import 成功当 ready（installed_pkg / model_ready 拆开）。
- 锁定 qwen-asr / transformers / tokenizers / modelscope 版本不升级。
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

FOUR_KEYS = ("whisper", "qwen_asr", "qwen_fa", "sensevoice")
BACKEND_DIR = Path(__file__).parent.parent / "backend"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ═══════════════════════════════════════════════════════════════════
# ① 模型注册表
# ═══════════════════════════════════════════════════════════════════

class TestRegistryTable(unittest.TestCase):
    """注册表四模型字段完整，Windows 产品口径（㉕+㉘）。"""

    def test_model_keys_fixed_order(self):
        from backend import model_registry as reg
        self.assertEqual(tuple(reg.MODEL_KEYS), FOUR_KEYS)

    def test_entry_fields_complete(self):
        from backend import model_registry as reg
        for key in FOUR_KEYS:
            entry = reg.get_entry(key, whisper_model_id="large-v2")
            for field in ("model_key", "display_name", "repo", "size_bytes",
                          "local_dir", "manifest_path", "probe_file"):
                self.assertTrue(entry.get(field), f"{key} 缺字段 {field}")
            self.assertGreater(entry["size_bytes"], 100 * 1024 ** 2,
                               f"{key} 预计大小不像实测值")

    def test_static_repos_match_contract(self):
        from backend import model_registry as reg
        self.assertEqual(reg.get_entry("qwen_asr")["repo"], "Qwen/Qwen3-ASR-1.7B")
        self.assertEqual(reg.get_entry("qwen_fa")["repo"], "Qwen/Qwen3-ForcedAligner-0.6B")
        self.assertEqual(reg.get_entry("sensevoice")["repo"], "iic/SenseVoiceSmall")

    def test_whisper_entry_delegates_to_model_manager(self):
        """whisper 沿用 _build_models 平台分流机制（㉘），注册表不另起炉灶。"""
        from backend import model_manager
        from backend import model_registry as reg
        entry = reg.get_entry("whisper", whisper_model_id="large-v2")
        self.assertEqual(entry["repo"], model_manager.MODELS["large-v2"]["repo"])
        self.assertIn("large-v2", str(entry["local_dir"]))

    def test_windows_whisper_repo_is_systran(self):
        """Windows 产品口径：large-v2 下载源 = Systran/faster-whisper-large-v2。"""
        from backend.model_manager import _build_models
        self.assertEqual(_build_models("faster")["large-v2"]["repo"],
                         "Systran/faster-whisper-large-v2")


class TestResolveModelPath(unittest.TestCase):
    """resolve 口径：非 Whisper 只认自管 ready；Whisper 暂留旧缓存兼容。"""

    def test_registry_docstring_matches_no_legacy_fallback_contract(self):
        """注册表顶部说明不能继续写非 Whisper 三模型会回退旧缓存。"""
        from backend import model_registry as reg
        doc = reg.__doc__ or ""
        self.assertIn("非 Whisper", doc)
        self.assertIn("自管 ready", doc)
        self.assertIn("Whisper", doc)
        self.assertIn("旧缓存兼容", doc)
        self.assertNotIn("旧共享缓存只读兼容回退", doc)
        self.assertNotIn("旧缓存都没有", reg.ModelNotReady.__doc__ or "")

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

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def _make_managed_ready(self, key: str):
        from backend import model_registry as reg
        entry = reg.get_entry(key)
        d = Path(entry["local_dir"])
        d.mkdir(parents=True, exist_ok=True)
        (d / entry["probe_file"]).write_bytes(b"{}")
        (d / "model.safetensors").write_bytes(b"weights")
        reg.write_manifest_v2(key)
        return d

    def test_managed_ready_dir_wins(self):
        from backend import model_registry as reg
        d = self._make_managed_ready("qwen_asr")
        self.assertEqual(reg.resolve_model_path("qwen_asr"), str(d))

    def test_legacy_modelscope_cache_no_longer_resolves(self):
        """P320：旧共享缓存回退已删除，只认自管目录，未就绪抛 ModelNotReady。"""
        from backend import model_registry as reg
        legacy = self.ms_cache / "models" / "Qwen" / "Qwen3-ASR-1.7B"
        legacy.mkdir(parents=True)
        (legacy / "config.json").write_bytes(b"{}")
        with self.assertRaises(reg.ModelNotReady):
            reg.resolve_model_path("qwen_asr")

    def test_legacy_hf_cache_no_longer_resolves(self):
        """P320：HF 旧缓存同样不再回退。"""
        from backend import model_registry as reg
        snap = (self.hf_cache / "models--Qwen--Qwen3-ForcedAligner-0.6B"
                / "snapshots" / "abc123")
        snap.mkdir(parents=True)
        (snap / "config.json").write_bytes(b"{}")
        with self.assertRaises(reg.ModelNotReady):
            reg.resolve_model_path("qwen_fa")

    def test_missing_everywhere_raises_guiding_error(self):
        from backend import model_registry as reg
        with self.assertRaises(reg.ModelNotReady) as ctx:
            reg.resolve_model_path("sensevoice")
        self.assertIn("一键准备", str(ctx.exception))

    def test_is_model_resolvable(self):
        from backend import model_registry as reg
        self.assertFalse(reg.is_model_resolvable("qwen_asr"))
        self._make_managed_ready("qwen_asr")
        self.assertTrue(reg.is_model_resolvable("qwen_asr"))

    def test_corrupt_managed_size_mismatch_not_ready(self):
        """结构校验：manifest size 对不上 → 不 ready（回退旧缓存/报错）。"""
        from backend import model_registry as reg
        d = self._make_managed_ready("qwen_asr")
        (d / "model.safetensors").write_bytes(b"tampered-longer-content")
        self.assertFalse(reg.is_model_ready("qwen_asr"))
        with self.assertRaises(reg.ModelNotReady):
            reg.resolve_model_path("qwen_asr")


# ═══════════════════════════════════════════════════════════════════
# ② 运行时加载改走注册表（5 个硬编码点）
# ═══════════════════════════════════════════════════════════════════

class TestRunnersNoHardcode(unittest.TestCase):
    """qwen_asr_runner.py:60/64、qwen_aligner.py:31、sensevoice_runner.py:70/155（㉖）。"""

    def test_sources_contain_no_repo_literals(self):
        core = BACKEND_DIR / "core"
        self.assertNotIn("Qwen/Qwen3-ASR-1.7B",
                         (core / "qwen_asr_runner.py").read_text(encoding="utf-8"))
        self.assertNotIn("Qwen/Qwen3-ForcedAligner-0.6B",
                         (core / "qwen_aligner.py").read_text(encoding="utf-8"))
        self.assertNotIn("iic/SenseVoiceSmall",
                         (core / "sensevoice_runner.py").read_text(encoding="utf-8"))

    def test_qwen_asr_mlx_branch_uses_resolved_path(self):
        from backend.core import qwen_asr_runner as runner
        fake_load = MagicMock(return_value=("model", "config"))
        with patch.object(runner, "_USE_MLX", True), \
             patch.object(runner, "_mlx_load_model", fake_load), \
             patch("backend.model_registry.resolve_model_path",
                   return_value="/fake/qwen_asr") as res:
            runner._load_qwen_model()
        res.assert_called_once_with("qwen_asr")
        fake_load.assert_called_once_with("/fake/qwen_asr")

    def test_qwen_asr_pytorch_branch_uses_resolved_path(self):
        from backend.core import qwen_asr_runner as runner
        with patch.object(runner, "_USE_MLX", False), \
             patch("qwen_asr.Qwen3ASRModel") as fake_cls, \
             patch("backend.model_registry.resolve_model_path",
                   return_value="/fake/qwen_asr"):
            runner._load_qwen_model()
        fake_cls.from_pretrained.assert_called_once_with("/fake/qwen_asr")

    def test_aligner_default_uses_resolved_path(self):
        with patch("qwen_asr.Qwen3ForcedAligner") as fake_cls, \
             patch("backend.model_registry.resolve_model_path",
                   return_value="/fake/qwen_fa") as res:
            fake_cls.from_pretrained.return_value = MagicMock()
            from backend.core.qwen_aligner import QwenAligner
            QwenAligner()
        res.assert_called_once_with("qwen_fa")
        fake_cls.from_pretrained.assert_called_once_with("/fake/qwen_fa")

    def test_sensevoice_load_uses_resolved_path(self):
        from backend.core import sensevoice_runner as runner
        fake_funasr = MagicMock()
        with patch.dict(sys.modules, {"funasr": fake_funasr}), \
             patch("backend.model_registry.resolve_model_path",
                   return_value="/fake/sensevoice"):
            model = runner._load_sensevoice_model("cpu")
        self.assertIsNotNone(model)
        _, kwargs = fake_funasr.AutoModel.call_args
        self.assertEqual(kwargs.get("model"), "/fake/sensevoice")

    def test_sensevoice_chunked_shares_single_load_point(self):
        """chunked 路径复用 _load_sensevoice_model，不留第二个硬编码加载点。"""
        from backend.core import sensevoice_runner as runner
        segs = [MagicMock(start=0.0, end=10.0)]
        with patch.object(runner, "_load_sensevoice_model",
                          return_value=None) as load:
            result = runner._run_sensevoice_chunked("a.wav", "cpu", segs)
        load.assert_called_once_with("cpu")
        self.assertIsNone(result)

    def test_resolve_failure_means_no_silent_network_download(self):
        """自管目录和旧缓存都没有 → 加载失败（跳过），绝不联网下载（㉖ 硬规则）。"""
        from backend.core import sensevoice_runner as runner
        from backend.model_registry import ModelNotReady
        fake_funasr = MagicMock()
        with patch.dict(sys.modules, {"funasr": fake_funasr}), \
             patch("backend.model_registry.resolve_model_path",
                   side_effect=ModelNotReady("未就绪")):
            model = runner._load_sensevoice_model("cpu")
        self.assertIsNone(model)
        fake_funasr.AutoModel.assert_not_called()

    def test_aligner_availability_uses_registry(self):
        """is_qwen_aligner_available 不再 AutoConfig 硬编码仓库名，改走注册表。"""
        from backend.core import qwen_aligner
        with patch("backend.model_registry.is_model_resolvable",
                   return_value=False) as res:
            self.assertFalse(qwen_aligner.is_qwen_aligner_available())
        res.assert_called_once_with("qwen_fa")


# ═══════════════════════════════════════════════════════════════════
# ③ manifest v2 生成与校验
# ═══════════════════════════════════════════════════════════════════

class TestManifestV2(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "config.json").write_bytes(b'{"a":1}')
        (self.dir / "model.safetensors").write_bytes(b"weights-content")
        # ModelScope 元数据与临时文件：不得进 manifest
        (self.dir / ".msc").write_bytes(b"x")
        (self.dir / ".mv").write_bytes(b"x")
        temp_dir = self.dir / "._____temp"
        temp_dir.mkdir()
        (temp_dir / "partial.bin").write_bytes(b"x")

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_files_list_content_and_exclusions(self):
        from backend import model_manifest as mm
        files = mm.build_files_list(self.dir)
        by_path = {f["path"]: f for f in files}
        self.assertEqual(set(by_path), {"config.json", "model.safetensors"})
        self.assertEqual(by_path["config.json"]["size"], 7)
        self.assertEqual(by_path["config.json"]["sha256"], _sha256(b'{"a":1}'))
        self.assertEqual(by_path["model.safetensors"]["sha256"],
                         _sha256(b"weights-content"))

    def test_manifest_json_excludes_itself(self):
        from backend import model_manifest as mm
        (self.dir / "manifest.json").write_text("{}")
        paths = {f["path"] for f in mm.build_files_list(self.dir)}
        self.assertNotIn("manifest.json", paths)

    def test_registry_write_manifest_v2(self):
        from backend import model_registry as reg
        with patch.object(reg, "MODELS_ROOT", self.dir.parent):
            reg.write_manifest_v2("qwen_asr", model_dir=self.dir)
        manifest = json.loads((self.dir / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "ready")
        self.assertEqual(manifest["version"], 2)
        self.assertEqual(manifest["repo"], "Qwen/Qwen3-ASR-1.7B")
        self.assertEqual(len(manifest["files"]), 2)

    def test_whisper_ready_manifest_upgraded_to_v2(self):
        """现有 Whisper 下载路径同步升级写 v2（㉗ 交货顺序③）。"""
        from backend import model_manager
        model_manager.write_ready_manifest("large-v2", self.dir, backend="faster")
        manifest = json.loads((self.dir / "manifest.json").read_text())
        self.assertEqual(manifest["version"], 2)
        paths = {f["path"] for f in manifest["files"]}
        self.assertIn("config.json", paths)
        # 旧字段保留，向后兼容 _manifest_matches
        self.assertEqual(manifest["model_id"], "large-v2")
        self.assertEqual(manifest["backend"], "faster")

    def _write_manifest(self):
        from backend import model_manifest as mm
        payload = {"status": "ready", "version": 2,
                   "files": mm.build_files_list(self.dir)}
        mm.write_manifest(self.dir, payload)
        return payload

    def test_locate_deleted_file(self):
        from backend import model_manifest as mm
        manifest = self._write_manifest()
        (self.dir / "model.safetensors").unlink()
        self.assertEqual(mm.find_corrupt_files(self.dir, manifest),
                         ["model.safetensors"])

    def test_locate_same_size_tamper(self):
        """同尺寸篡改只有 sha256 能抓到（SDK 不自愈的那种坏法）。"""
        from backend import model_manifest as mm
        manifest = self._write_manifest()
        (self.dir / "model.safetensors").write_bytes(b"weights-CONTENT")  # 同长度
        self.assertEqual(mm.find_corrupt_files(self.dir, manifest),
                         ["model.safetensors"])
        # 结构校验（不算哈希）抓不到同尺寸篡改——这正是 env 只做结构校验的边界
        self.assertEqual(
            mm.find_corrupt_files(self.dir, manifest, check_hash=False), [])

    def test_locate_size_mismatch_without_hashing(self):
        from backend import model_manifest as mm
        manifest = self._write_manifest()
        (self.dir / "config.json").write_bytes(b"{}")
        self.assertEqual(
            mm.find_corrupt_files(self.dir, manifest, check_hash=False),
            ["config.json"])


# ═══════════════════════════════════════════════════════════════════
# ④ /api/env 升级：import 成功 ≠ ready
# ═══════════════════════════════════════════════════════════════════

class TestEnvUpgrade(unittest.TestCase):

    def test_sensevoice_import_alone_is_not_ready(self):
        from backend import env_check
        with patch("backend.model_registry.is_model_resolvable",
                   return_value=False):
            result = env_check.check_sensevoice()
        self.assertIn("installed_pkg", result)
        self.assertFalse(result["model_ready"])
        self.assertFalse(result["ready"])

    def test_qwen_asr_import_alone_is_not_ready(self):
        from backend import env_check
        with patch("backend.model_registry.is_model_resolvable",
                   return_value=False):
            result = env_check.check_qwen_asr()
        self.assertIn("installed_pkg", result)
        self.assertFalse(result["model_ready"])
        self.assertFalse(result["ready"])

    def test_ready_requires_pkg_and_model(self):
        from backend import env_check
        with patch("backend.model_registry.is_model_resolvable",
                   return_value=True):
            sv = env_check.check_sensevoice()
            qa = env_check.check_qwen_asr()
        self.assertTrue(sv["model_ready"])
        self.assertEqual(sv["ready"], sv["installed_pkg"])
        self.assertTrue(qa["model_ready"])
        self.assertEqual(qa["ready"], qa["installed_pkg"])

    def test_qwen_aligner_reports_split_fields(self):
        from backend import env_check
        with patch("backend.model_registry.is_model_resolvable",
                   return_value=False):
            result = env_check.check_qwen_aligner()
        self.assertIn("installed_pkg", result)
        self.assertFalse(result["model_ready"])
        self.assertFalse(result["ready"])


# ═══════════════════════════════════════════════════════════════════
# 版本锁：不升级 qwen-asr / transformers / tokenizers / modelscope（㉗ 验收）
# ═══════════════════════════════════════════════════════════════════

class TestPackagePins(unittest.TestCase):

    def test_locked_versions_unchanged(self):
        import importlib.metadata as md
        expected = {
            "qwen-asr": "0.0.6",
            "transformers": "4.57.6",
            "tokenizers": "0.22.2",
            "modelscope": "1.37.1",
        }
        for pkg, ver in expected.items():
            self.assertEqual(md.version(pkg), ver,
                             f"{pkg} 版本变了——㉗ 验收禁止顺手升级，须单独报批")


class TestSameEnvOfflineLoad(unittest.TestCase):
    """Qwen ASR + Qwen FA 同环境离线加载（真模型，重；默认跳过）。"""

    @unittest.skipUnless(os.environ.get("VSUB_E2E_MODELS"),
                         "真实模型加载耗时数十秒，设 VSUB_E2E_MODELS=1 启用")
    def test_qwen_asr_and_fa_load_in_same_process_offline(self):
        os.environ["HF_HUB_OFFLINE"] = "1"
        from backend.model_registry import resolve_model_path
        from qwen_asr import Qwen3ASRModel, Qwen3ForcedAligner
        asr = Qwen3ASRModel.from_pretrained(resolve_model_path("qwen_asr"))
        self.assertIsNotNone(asr)
        fa = Qwen3ForcedAligner.from_pretrained(resolve_model_path("qwen_fa"))
        self.assertIsNotNone(fa)


if __name__ == "__main__":
    unittest.main()
