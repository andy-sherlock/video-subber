"""P299（外部工程师审查 2026-06-22）：qwen_asr_runner 顶层 MLX 导入遇非 ImportError
（如无 Metal 环境抛 RuntimeError）不得崩掉整个模块——否则任何 import 该 runner 的测试
在收集阶段就被打断。修法：模块级 try 改 except Exception，落 PyTorch 兜底。"""
import sys
import types
import importlib
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestQwenRunnerImportResilience(unittest.TestCase):

    def test_module_imports_when_mlx_raises_runtime_error(self):
        """mlx 导入抛 RuntimeError（无 Metal）时，模块仍能加载，_USE_MLX=False。"""
        import backend.core.qwen_asr_runner as mod

        class _Metalless(types.ModuleType):
            def __getattr__(self, name):
                raise RuntimeError("No Metal device available")

        try:
            with patch.dict(sys.modules, {"mlx_qwen3_asr": _Metalless("mlx_qwen3_asr")}):
                importlib.reload(mod)            # 当前代码会在这里抛 RuntimeError（红）
                self.assertFalse(mod._USE_MLX)   # 修复后：MLX 标记不可用，已落兜底
        finally:
            importlib.reload(mod)                # 还原真实模块状态，别污染其它测试


if __name__ == '__main__':
    unittest.main()
