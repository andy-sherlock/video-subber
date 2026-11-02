"""Checkpoint/resume + versioned cache management.
Migrated from V1.3 in V2.08. Replaced print() with logging.
"""
import json, hashlib, os, shutil, logging, tempfile
from pathlib import Path

_log = logging.getLogger("checkpoint")

MAX_CACHE_VERSIONS = 5
HASH_SHORT_BYTES = 12


# ── A11: 缓存软链接防线（单一校验入口）──
#
# 威胁模型边界：只防「访问前已经存在的软链接」。不防「运行途中被替换成软链接」
# 的竞态——那需要无跟随打开策略，不在本批范围。不得宣称已完全消除该风险。

class UnsafeCachePath(RuntimeError):
    """路径本身是软链接：拒绝访问，绝不跟随。"""


def ensure_no_symlink(*paths) -> None:
    """在 mkdir / exists / touch / iterdir / stat / rmtree / rmdir /
    read / unlink / write / replace 之前统一调用。"""
    for path in paths:
        if path is None:
            continue
        try:
            if Path(path).is_symlink():
                raise UnsafeCachePath(f"路径是软链接，已拒绝访问: {path}")
        except OSError as exc:
            raise UnsafeCachePath(f"无法确认路径是否安全: {path}") from exc


def is_safe_cache_path(*paths) -> bool:
    """只想跳过、不想抛错的调用点用它。"""
    try:
        ensure_no_symlink(*paths)
    except UnsafeCachePath:
        return False
    return True


def get_file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _hash_short(path: str, input_hash: str | None = None) -> str:
    return (input_hash or get_file_hash(path))[:HASH_SHORT_BYTES]


def find_versioned_cache(
    cache_base: Path, input_path: str, *, input_hash: str | None = None
) -> tuple:
    """Find or create a versioned subdirectory for the given input file."""
    h = _hash_short(input_path, input_hash)
    target = cache_base / h
    # A11: 拦截必须早于 exists/touch/mkdir——之前是先访问再返回，太晚。
    ensure_no_symlink(cache_base, target)
    if target.exists():
        _log.debug("缓存命中: hash=%s dir=%s", h, target)
        target.touch()
        return target, False
    _log.info("新缓存: hash=%s dir=%s", h, target)
    target.mkdir(parents=True, exist_ok=True)
    return target, True


def cleanup_old_versions(cache_base: Path, keep: int = MAX_CACHE_VERSIONS) -> None:
    """Remove oldest version subdirectories when count > keep."""
    # A11: iterdir/stat/rmtree/rmdir 之前先确认 cache 根不是软链接。
    ensure_no_symlink(cache_base)
    if not cache_base.exists():
        return
    entries = list(cache_base.iterdir())
    # 任一版本目录是软链接就整体拒绝：rmtree 会顺着它删到任务目录外面。
    ensure_no_symlink(*entries)
    dirs = sorted(
        [d for d in entries if d.is_dir()],
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
    def __init__(self, path: str, input_file: str, *, input_hash: str | None = None):
        self.path = Path(path)
        self.input_file = input_file
        self.hash = (
            (input_hash or get_file_hash(input_file))[:16]
            if os.path.exists(input_file)
            else ""
        )

    def load(self) -> str:
        """Return current stage: start, extract_audio, transcribe, translate, burn, done."""
        # A11: read/unlink 之前先校验。
        ensure_no_symlink(self.path)
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

    def save(self, stage: str, meta: dict = None, *,
             run_id: str = None, result_srt=None, result_video=None,
             output_mode: str = None):
        """原子写入断点。

        A9：done 的完成状态、本轮身份和明确成品路径必须一次写完，不再事后
        直读—改—替换。恢复端凭这三项证明"成品属于这一轮"，而不是靠文件名推导。
        """
        data = {"input_file": self.input_file, "input_hash": self.hash,
                "stage": stage, "meta": meta or {}}
        if run_id is not None:
            data.update({
                "run_id": run_id,
                "result_srt": str(result_srt) if result_srt else None,
                "result_video": str(result_video) if result_video else None,
                # **实际做出来的**输出方式，不是用户当初选的那个。
                # 拖 mp3 选烧录时后端会降级成 srt；凭据若记 burn，
                # 恢复端就会去找一个本来就不会生成的视频，把已完成任务判成没做完。
                # 单独一个字段而不是写进 meta：meta 要拿去做配置变更检测，
                # 改动它会让下次运行误判「配置变了」而作废缓存。
                "output_mode": output_mode,
            })
        # A11: write/replace 之前校验目标与所在目录。
        ensure_no_symlink(self.path, self.path.parent)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 固定 .tmp 名字可被提前放软链接占位；mkstemp 独占创建且名字不可预测。
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".checkpoint-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_name, self.path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
