"""P159: Config save/load must preserve apiKeys across save cycles.

Reproduces the race condition: frontend init calls saveConfig() with empty
apiKeys before loadConfig() returns real data from server. The empty array
overwrites the user's real keys on disk.

Regression guard: save_config → load_config roundtrip must preserve keys.
"""
import json
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

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


class TestConfigPreservesApiKeys(unittest.TestCase):

    def setUp(self):
        import backend.config_manager as cm
        if cm.CONFIG_PATH.exists():
            cm.CONFIG_PATH.unlink()

    def test_save_then_load_preserves_key(self):
        """save key → load → key survives."""
        from backend.config_manager import save_config, load_config

        save_config({
            "apiKeys": [
                {"platform": "deepseek", "name": "",
                 "key": "sk-test-preserve-key",
                 "model": "deepseek-chat"},
            ]
        })
        loaded = load_config()
        keys = loaded.get("apiKeys", [])
        self.assertEqual(len(keys), 1)
        self.assertEqual(keys[0]["key"], "sk-test-preserve-key")

    def test_race_condition_empty_array_overwrites(self):
        """Simulate the bug: save real key → save empty → key is gone.

        This proves the disk-level behavior IS destructive. The fix is
        preventing the empty save from happening in the frontend — not
        in making the backend defend against it (it can't — it trusts
        the caller).
        """
        from backend.config_manager import save_config, load_config

        # User entered their key → saved
        save_config({
            "apiKeys": [
                {"platform": "deepseek", "name": "",
                 "key": "sk-real-user-key", "model": "deepseek-chat"},
            ]
        })

        # Page init dispatches change event → saveConfig() with empty State
        save_config({"apiKeys": []})

        # Now load — key is gone
        loaded = load_config()
        keys = loaded.get("apiKeys", [])
        self.assertEqual(len(keys), 0,
                         "Empty save DID overwrite real key — frontend must "
                         "NEVER call saveConfig() with uninitialized State")


if __name__ == '__main__':
    unittest.main()
