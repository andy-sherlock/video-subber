"""存档公共层：只管「怎么安全地存」，不管「存的是什么」。

这个软件一跑十几分钟，用户中途关掉是常态不是意外。裸写文件在被打断时会留下
半截文件，把老存档一起赔进去；读的时候不检查，半截文件会让同一个任务每次都崩在
同一行，而断点续跑又会把这个坏目录一直复用下去——那个视频就永久废了。

**这里只有两件事：安全写、安全读。**
版本号对不对、指纹一致吗、字段全不全、多大算太大——那些是每个存档各自的业务规则，
一律留在各自的调用处。把它们搬进来，就会出现「重构时顺手简化掉一条校验，
代码不报错、测试还是绿的、但那条防线已经没了」。
"""

import json
import logging
import os
import stat
import tempfile
from pathlib import Path

_log = logging.getLogger("worker")

# 转录/Qwen 原始/翻译这类大存档的上限。断句收据只有 1.2 KB，用它自己的 64 KB。
# 上限必须是参数：照搬 64 KB 会让 300 KB 的转录存档、171 KB 的 Qwen 原始存档
# 每次都被拒 → 每跑一次都重做十几分钟语音识别，比它想防的问题严重得多。
LARGE_ARCHIVE_MAX_BYTES = 10 * 1024 * 1024


def read_json_archive(path, *, max_bytes: int | None = None):
    """读一份 JSON 存档；**任何读不成功都返回 None，让调用方决定重来。**

    只判断「这份文件能不能安全地读成 JSON」这一件事：
    是不是常规文件（不跟软链接走）、有没有超过上限、能不能解析。
    **刻意不检查字段完整性和内容合理性**——检查越多，误杀正常存档的概率越大，
    而误杀一份转录存档的代价是让用户重跑十几分钟语音识别。
    """
    path = Path(path)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        _log.warning("存档无法检查，按缺失处理 (%s): %s", path.name, exc)
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        _log.warning("存档不是可接受的常规文件，拒绝读取: %s", path.name)
        return None
    if max_bytes is not None and info.st_size > max_bytes:
        _log.warning("存档超过大小上限（%d > %d），拒绝读取: %s",
                     info.st_size, max_bytes, path.name)
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _log.warning("存档损坏或无法读取，按缺失处理 (%s): %s", path.name, exc)
        return None


def write_json_archive(path, payload, *, chmod: int | None = None,
                       indent: int | None = None,
                       allow_nan: bool = True) -> bool:
    """原子写入一份 JSON 存档；写不成功返回 False，且**老存档保持原样**。

    步骤固定四步：确认目标没被做手脚 → 写草稿并确认落盘 → 一瞬间顶替 → 失败清理。
    中途断电或被杀，用户手里剩下的要么是完整的老存档，要么是完整的新存档，
    不会是半截。
    """
    path = Path(path)
    temp_path = None
    try:
        try:
            existing = os.lstat(path)
        except FileNotFoundError:
            existing = None
        if existing is not None and (
            stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode)
        ):
            _log.warning("存档目标不是常规文件，拒绝写入: %s", path.name)
            return False

        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            indent=indent,
            allow_nan=allow_nan,
        ).encode("utf-8")

        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.stem}-", suffix=".tmp", dir=str(path.parent),
        )
        temp_path = Path(temp_name)
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())

        # 写草稿这段时间里目标可能被换成软链接，顶替之前再看一眼。
        try:
            existing = os.lstat(path)
        except FileNotFoundError:
            existing = None
        if existing is not None and stat.S_ISLNK(existing.st_mode):
            _log.warning("存档目标变为软链接，拒绝写入: %s", path.name)
            return False

        os.replace(temp_path, path)
        temp_path = None
        if chmod is not None:
            os.chmod(path, chmod)
        return True
    except (OSError, TypeError, ValueError) as exc:
        _log.warning("存档写入失败，保留原有存档 (%s): %s", path.name, exc)
        return False
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
