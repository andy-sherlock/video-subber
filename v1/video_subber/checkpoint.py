"""Checkpoint/resume support."""
import json
import hashlib
import os
from pathlib import Path


class Checkpoint:
    def __init__(self, path: str, input_file: str):
        self.path = Path(path)
        self.input_file = input_file
        self.hash = _sha256_full(input_file)[:16] if os.path.exists(input_file) else ""

    def load(self) -> str:
        """Return current stage: start, extract_audio, transcribe, translate, burn, done."""
        if not self.path.exists():
            return "start"
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            print("  ⚠️ 断点文件损坏，重新开始")
            self.path.unlink(missing_ok=True)
            return "start"

        if data.get("input_hash") != self.hash:
            print("  ⚠️ 文件已变更，重新开始")
            self.path.unlink(missing_ok=True)
            return "start"

        stage = data.get("stage", "start")
        if stage != "done":
            r = input(f"  检测到断点 (已完成: {stage})，是否继续？[Y/n] ")
            if r.lower() == "n":
                self.path.unlink(missing_ok=True)
                return "start"
        return stage

    def save(self, stage: str, meta: dict = None):
        data = {"input_file": self.input_file, "input_hash": self.hash,
                "stage": stage, "meta": meta or {}}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def _sha256_full(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
