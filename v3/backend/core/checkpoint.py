"""Checkpoint/resume + versioned cache management.
Migrated from V1.3 in V2.08. Replaced print() with logging.
"""
import json, hashlib, os, shutil, logging
from pathlib import Path

_log = logging.getLogger("checkpoint")

MAX_CACHE_VERSIONS = 5
HASH_SHORT_BYTES = 12


def get_file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _hash_short(path: str) -> str:
    return get_file_hash(path)[:HASH_SHORT_BYTES]


def find_versioned_cache(cache_base: Path, input_path: str) -> tuple:
    """Find or create a versioned subdirectory for the given input file."""
    h = _hash_short(input_path)
    target = cache_base / h
    if target.exists():
        _log.debug("缓存命中: hash=%s dir=%s", h, target)
        target.touch()
        return target, False
    _log.info("新缓存: hash=%s dir=%s", h, target)
    target.mkdir(parents=True, exist_ok=True)
    return target, True


def cleanup_old_versions(cache_base: Path, keep: int = MAX_CACHE_VERSIONS) -> None:
    """Remove oldest version subdirectories when count > keep."""
    if not cache_base.exists():
        return
    dirs = sorted(
        [d for d in cache_base.iterdir() if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
    )
    while len(dirs) > keep:
        removed = dirs.pop(0)
        _log.info("清理旧缓存: %s (当前 %d 个, 保留 %d 个)", removed.name, len(dirs) + 1, keep)
        shutil.rmtree(removed, ignore_errors=True)
    if cache_base.exists():
        try:
            cache_base.rmdir()
        except OSError:
            pass


class Checkpoint:
    def __init__(self, path: str, input_file: str):
        self.path = Path(path)
        self.input_file = input_file
        self.hash = get_file_hash(input_file)[:16] if os.path.exists(input_file) else ""

    def load(self) -> str:
        """Return current stage: start, extract_audio, transcribe, translate, burn, done."""
        if not self.path.exists():
            return "start"
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            _log.warning("断点文件损坏，重新开始")
            self.path.unlink(missing_ok=True)
            return "start"

        if data.get("input_hash") != self.hash:
            _log.warning("文件已变更，重新开始")
            self.path.unlink(missing_ok=True)
            return "start"

        return data.get("stage", "start")

    def save(self, stage: str, meta: dict = None):
        data = {"input_file": self.input_file, "input_hash": self.hash,
                "stage": stage, "meta": meta or {}}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
