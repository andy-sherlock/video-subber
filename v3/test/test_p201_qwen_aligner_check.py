"""P201: is_qwen_aligner_available must check model files, not just imports.
Also: check_all() must include qwen_aligner key."""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_fake_modules():
    """Build fake torch/transformers/qwen_asr modules for isolation."""
    fake_torch = types.ModuleType("torch")
    fake_transformers = types.ModuleType("transformers")
    mock_autoconfig = MagicMock()
    fake_transformers.AutoConfig = mock_autoconfig
    fake_qwen_asr = types.ModuleType("qwen_asr")
    return fake_torch, fake_transformers, mock_autoconfig, fake_qwen_asr


class TestQwenAlignerAvailability(unittest.TestCase):

    def test_returns_false_when_model_not_downloaded(self):
        """Packages installed but model not cached locally → must return False."""
        fake_torch, fake_transformers, mock_autoconfig, fake_qwen_asr = _make_fake_modules()
        mock_autoconfig.from_pretrained.side_effect = OSError("not found locally")

        fake_modules = {
            "torch": fake_torch,
            "transformers": fake_transformers,
            "qwen_asr": fake_qwen_asr,
        }
        with patch.dict(sys.modules, fake_modules):
            import importlib
            import backend.core.qwen_aligner as mod
            importlib.reload(mod)
            result = mod.is_qwen_aligner_available()

        self.assertFalse(result,
                         "Must return False when model files are not downloaded locally")

    def test_returns_true_when_model_cached(self):
        """Packages installed and model cached → must return True."""
        fake_torch, fake_transformers, mock_autoconfig, fake_qwen_asr = _make_fake_modules()
        mock_autoconfig.from_pretrained.side_effect = None
        mock_autoconfig.from_pretrained.return_value = MagicMock()

        fake_modules = {
            "torch": fake_torch,
            "transformers": fake_transformers,
            "qwen_asr": fake_qwen_asr,
        }
        with patch.dict(sys.modules, fake_modules):
            import importlib
            import backend.core.qwen_aligner as mod
            importlib.reload(mod)
            result = mod.is_qwen_aligner_available()

        self.assertTrue(result, "Must return True when model is cached locally")


class TestCheckAllIncludesAligner(unittest.TestCase):

    def test_check_all_has_qwen_aligner_key(self):
        """check_all() output must include 'qwen_aligner' key."""
        from backend.env_check import check_all
        result = check_all()
        self.assertIn("qwen_aligner", result,
                      "check_all() must include qwen_aligner in its output dict")


if __name__ == '__main__':
    unittest.main()
