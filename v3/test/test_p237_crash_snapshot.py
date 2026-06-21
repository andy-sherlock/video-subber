"""P237: worker crashes should write a safe crash snapshot."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_crash_snapshot_json_excludes_secrets_and_large_payloads():
    from backend.worker import _safe_crash_snapshot_json

    raw = _safe_crash_snapshot_json()
    data = json.loads(raw)

    assert "time" in data
    assert "step" in data
    assert "memory_mb" in data
    assert "apiKeys" not in raw
    assert "Authorization" not in raw
    assert "transcript" not in raw
    assert "translated" not in raw


def test_log_crash_snapshot_writes_marker(caplog):
    from backend.worker import _log_crash_snapshot

    _log_crash_snapshot()

    assert "crash_snapshot" in caplog.text
