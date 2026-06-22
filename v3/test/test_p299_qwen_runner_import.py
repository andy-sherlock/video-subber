"""P299（外部工程师审查 2026-06-22）：qwen_asr_runner 顶层 MLX 导入在无 Metal 环境
（mlx import 抛 RuntimeError）必须满足两点：
  1. 模块顶层代码不崩——否则整套测试在收集阶段就被打断；
  2. _mlx_load_model / _mlx_transcribe 仍作为名字存在（占位 None）——否则旧测试
     patch 它们时 AttributeError。

本机有 Metal，用「在隔离命名空间里 exec 模块源码 + 塞一个 import 即抛 RuntimeError 的
假 mlx」复现无 Metal 场景。刻意不用 reload / pop sys.modules——那会污染父包属性、带崩
别的测试（如 test_phase1c 的 reload）。exec 进一个一次性 dict，零 sys.modules 污染。"""
import sys
import types
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_MODULE = "backend.core.qwen_asr_runner"


class _Metalless(types.ModuleType):
    """假 mlx_qwen3_asr：任何属性访问都抛 RuntimeError，模拟无 Metal 设备。"""
    def __getattr__(self, name):
        raise RuntimeError("No Metal device available")


class TestQwenRunnerImportResilience(unittest.TestCase):

    def test_module_top_level_survives_mlx_runtime_error(self):
        spec = importlib.util.find_spec(_MODULE)
        source = Path(spec.origin).read_text(encoding="utf-8")
        ns = {"__name__": _MODULE, "__file__": spec.origin}

        # 在隔离命名空间里跑模块顶层代码，mlx 导入会抛 RuntimeError
        with patch.dict(sys.modules, {"mlx_qwen3_asr": _Metalless("mlx_qwen3_asr")}):
            exec(compile(source, spec.origin, "exec"), ns)   # 1. 不崩

        self.assertFalse(ns["_USE_MLX"])                     # 2. MLX 标记不可用
        self.assertIn("_mlx_load_model", ns)                 # 3. 占位仍在 → 旧测试可 patch
        self.assertIn("_mlx_transcribe", ns)
        self.assertIsNone(ns["_mlx_load_model"])
        self.assertIsNone(ns["_mlx_transcribe"])


if __name__ == '__main__':
    unittest.main()
