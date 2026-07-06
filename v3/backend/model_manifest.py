"""manifest v2 — 逐文件 size+sha256 指纹的生成与校验（C3.5 ㉗）。

manifest v2 = v1 字段 + files: [{path, size, sha256}]。
排除规则：点号开头的路径成分（.msc / .mv / ._____temp/ 等 ModelScope 元数据
与临时文件）和 manifest.json 自身。

校验分两档：
- 结构校验（check_hash=False）：文件在 + size 对。/api/env 日常用，秒回。
- 全量校验（check_hash=True）：再算 sha256。只在 repair 流程里跑。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

MANIFEST_NAME = "manifest.json"

_HASH_CHUNK = 1024 * 1024


def safe_member(model_dir: Path, rel: str) -> Path:
    """把 manifest 里的相对路径安全地拼到模型目录内。

    P317: manifest.json 是本地文件，被篡改成 `../`/绝对路径/盘符时，
    repair 的删文件一步可删到目录之外。这里禁绝对路径、`..`、空路径，
    并要求拼接后仍在模型目录内，否则抛 ValueError。
    """
    if not rel or not isinstance(rel, str):
        raise ValueError(f"非法的 manifest 路径：{rel!r}")
    p = Path(rel)
    if p.is_absolute() or p.drive or p.anchor:
        raise ValueError(f"manifest 路径不允许绝对路径：{rel!r}")
    if ".." in p.parts:
        raise ValueError(f"manifest 路径不允许 '..'：{rel!r}")
    base = Path(model_dir)
    resolved_base = base.resolve()
    resolved_target = (resolved_base / p).resolve()
    if resolved_base != resolved_target and resolved_base not in resolved_target.parents:
        raise ValueError(f"manifest 路径越出模型目录：{rel!r}")
    return base / p


def manifest_paths_safe(manifest: dict) -> bool:
    """整份 manifest 的 files 路径是否都安全（用相对根 '.' 校验形状）。"""
    for f in manifest.get("files", []):
        try:
            safe_member(Path("."), f.get("path", ""))
        except ValueError:
            return False
    return True


def manifest_well_formed(manifest: dict) -> bool:
    """v2 manifest 每个 file 都齐 path/size/sha256，才能做文件级修复。

    P317/P318：缺字段的 manifest（篡改或旧格式残留）不可信，走整模型兜底，
    避免 `f["size"]` 之类 KeyError 把 repair 打断、状态卡死。
    """
    files = manifest.get("files")
    if not files:
        return False
    for f in files:
        if not isinstance(f, dict):
            return False
        if f.get("path") is None or f.get("size") is None or f.get("sha256") is None:
            return False
    return True


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _include_in_manifest(rel_path: Path) -> bool:
    if any(part.startswith(".") for part in rel_path.parts):
        return False
    if rel_path.name in (MANIFEST_NAME, MANIFEST_NAME + ".tmp"):
        return False
    return True


def build_files_list(model_dir: Path) -> list[dict]:
    """逐文件计算 size+sha256。下载成功当场调用，一次性成本（12GB 约几十秒）。"""
    model_dir = Path(model_dir)
    files = []
    for path in sorted(model_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(model_dir)
        if not _include_in_manifest(rel):
            continue
        files.append({
            "path": rel.as_posix(),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    return files


def write_manifest(model_dir: Path, payload: dict) -> None:
    """原子写入：先写临时文件，再 rename。"""
    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    tmp = model_dir / f"{MANIFEST_NAME}.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(model_dir / MANIFEST_NAME)


def read_manifest(model_dir: Path) -> dict | None:
    try:
        return json.loads((Path(model_dir) / MANIFEST_NAME)
                          .read_text(encoding="utf-8"))
    except Exception:
        return None


def find_corrupt_files(model_dir: Path, manifest: dict,
                       check_hash: bool = True,
                       only: list[str] | None = None) -> list[str]:
    """对照 manifest files 定位坏文件（缺失 / size 不符 / sha256 不符）。

    size 先行（快），size 对上才算 sha256（慢但准）。
    only: 只复验这些相对路径（repair 补完坏文件后的复验用）。
    """
    model_dir = Path(model_dir)
    wanted = set(only) if only is not None else None
    bad = []
    for f in manifest.get("files", []):
        rel = f.get("path")
        if wanted is not None and rel not in wanted:
            continue
        # P317 闭环：日常 ready 判定也走 safe_member——路径穿越/绝对路径一律判坏，
        # 不再让 model_dir/rel 直接命中目录外文件把篡改模型误判为 ready。
        try:
            path = safe_member(model_dir, rel)
        except ValueError:
            bad.append(rel)
            continue
        size = f.get("size")
        want = f.get("sha256")
        if (size is None or want is None
                or not path.is_file() or path.stat().st_size != size):
            bad.append(rel)
            continue
        if check_hash:
            if sha256_file(path) != want:
                bad.append(rel)
    return bad


def structure_ok(model_dir: Path, manifest: dict) -> bool:
    """结构校验：manifest 有 files 且每个文件存在、size 一致。不算哈希。"""
    if not manifest.get("files"):
        return False
    return not find_corrupt_files(model_dir, manifest, check_hash=False)
