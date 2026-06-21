"""API key storage must survive machine identity changes.

config_manager save_config / load_config: keys stored in plaintext, never encrypted.
If a cryptography Fernet key derivation changes (new hostname, Wi-Fi MAC), keys
must still be readable — unlike the old encrypted-at-rest design that silently
dropped them.
"""
import json
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── temporary config file so we don't touch the real ~/.video-subber ──
_ORIG = None


def setUpModule():
    global _ORIG
    import backend.config_manager as cm
    _ORIG = cm.CONFIG_PATH
    cm.CONFIG_PATH = Path(tempfile.mkdtemp()) / "config.json"


def tearDownModule():
    import backend.config_manager as cm
    if _ORIG is not None:
        cm.CONFIG_PATH = _ORIG


class TestConfigPlaintextKeys(unittest.TestCase):

    def setUp(self):
        import backend.config_manager as cm
        if cm.CONFIG_PATH.exists():
            cm.CONFIG_PATH.unlink()

    # ── RED tests (plaintext storage not yet implemented) ──

    def test_save_and_reload_preserves_key(self):
        """save_config + load_config returns the exact same key that was saved."""
        from backend.config_manager import save_config, load_config

        cfg = {
            "apiKeys": [
                {"platform": "deepseek", "name": "",
                 "key": "sk-plaintext-test-key-abc123",
                 "model": "deepseek-chat"},
            ]
        }
        save_config(cfg)
        loaded = load_config()

        keys = loaded.get("apiKeys", [])
        self.assertEqual(len(keys), 1, "should have 1 key after reload")
        self.assertEqual(keys[0]["key"], "sk-plaintext-test-key-abc123",
                         "key must survive save→reload unchanged (plaintext)")

    def test_reload_survives_machine_identity_change(self):
        """Key survives even when cryptography gets unavailable at load time."""
        from backend.config_manager import save_config, load_config

        cfg = {
            "apiKeys": [
                {"platform": "deepseek", "name": "",
                 "key": "sk-survive-identity-change",
                 "model": "deepseek-chat"},
            ]
        }
        save_config(cfg)

        # Simulate "cryptography unavailable" — old behaviour would silently
        # drop the encrypted key. New behaviour keeps the plaintext.
        with patch("backend.key_manager._get_fernet", return_value=None):
            loaded = load_config()

        keys = loaded.get("apiKeys", [])
        self.assertEqual(len(keys), 1)
        self.assertEqual(keys[0]["key"], "sk-survive-identity-change")

    def test_stored_file_is_plaintext(self):
        """config.json on disk must contain the key in plaintext, not ciphertext."""
        from backend.config_manager import save_config, CONFIG_PATH

        cfg = {
            "apiKeys": [
                {"platform": "openai", "name": "",
                 "key": "sk-readable-on-disk",
                 "model": "gpt-4o"},
            ]
        }
        save_config(cfg)

        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        keys = raw.get("apiKeys", [])
        self.assertGreaterEqual(len(keys), 1)
        self.assertEqual(keys[0]["key"], "sk-readable-on-disk",
                         "config.json must contain the plaintext key, "
                         "not a Fernet ciphertext blob")


if __name__ == '__main__':
    unittest.main()
