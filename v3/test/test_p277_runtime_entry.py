"""P277: real long-video tests must use v3/.venv without uvicorn reload."""
from pathlib import Path


def test_real_test_runner_uses_v3_venv_without_reload():
    script = Path(__file__).resolve().parent.parent / "run-test.sh"

    assert script.exists()
    text = script.read_text(encoding="utf-8")

    assert '.venv/bin/python' in text
    assert 'backend.main:app' in text
    assert '--reload' not in text
    assert '--reload-dir' not in text
