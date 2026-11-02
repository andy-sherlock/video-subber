"""A11：缓存软链接防线（发布阻塞项）。

任务目录、cache 根、版本目录、checkpoint 文件、checkpoint 临时文件——
任一是软链接，都必须在 mkdir / exists / touch / iterdir / stat / rmtree /
rmdir / read / unlink / write / replace 之前被拒绝。

威胁模型边界：本批只防「访问前已经存在的软链接」，不防「运行途中被替换成
软链接」的竞态（那需要无跟随打开策略，不在本批范围）。
"""

import json
import os

import pytest

from backend import main
from backend.core import checkpoint as checkpoint_module
from backend.core.checkpoint import (
    Checkpoint,
    UnsafeCachePath,
    cleanup_old_versions,
    ensure_no_symlink,
    find_versioned_cache,
)


def _outside_victim(tmp_path):
    """任务目录之外的"受害者"目录。

    结构照抄 _cleanup_old_cache 真正会走的层级 cache/<版本目录>/<中间产物>，
    否则拦截失效也测不出来（顶层文件会被 is_dir 过滤掉，得到假绿）。
    """
    victim = tmp_path / "victim"
    version = victim / "important"
    version.mkdir(parents=True)
    target = version / "audio.wav"
    target.write_bytes(b"precious user data")
    old = 1  # 1970 年，必然早于 30 天截止线
    os.utime(target, (old, old))
    return victim, target


def _job_with_symlinked_cache(tmp_path, victim, job_id="a11job"):
    job_dir = tmp_path / "jobs" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "meta.json").write_text(
        json.dumps({"status": "done", "file_hash": "f" * 64, "config": {}}),
        encoding="utf-8",
    )
    (job_dir / "cache").symlink_to(victim, target_is_directory=True)
    return job_dir


# ── 单一校验入口 ─────────────────────────────────────────────────────────

def test_ensure_no_symlink_rejects_a_symlinked_path(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    ensure_no_symlink(real)  # 正常目录必须放行

    with pytest.raises(UnsafeCachePath):
        ensure_no_symlink(link)


def test_ensure_no_symlink_checks_every_argument(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    with pytest.raises(UnsafeCachePath):
        ensure_no_symlink(real, link)


# ── checkpoint.py 四个入口 ───────────────────────────────────────────────

def test_find_versioned_cache_rejects_symlinked_cache_root(tmp_path):
    """拦截必须发生在 exists/touch/mkdir 之前。"""
    victim, target = _outside_victim(tmp_path)
    cache_base = tmp_path / "cache"
    cache_base.symlink_to(victim, target_is_directory=True)
    source = tmp_path / "input.mp4"
    source.write_bytes(b"video")

    with pytest.raises(UnsafeCachePath):
        find_versioned_cache(cache_base, str(source))
    assert target.exists()


def test_find_versioned_cache_rejects_symlinked_version_dir(tmp_path):
    victim, target = _outside_victim(tmp_path)
    cache_base = tmp_path / "cache"
    cache_base.mkdir()
    source = tmp_path / "input.mp4"
    source.write_bytes(b"video")
    version = cache_base / checkpoint_module._hash_short(str(source))
    version.symlink_to(victim, target_is_directory=True)

    with pytest.raises(UnsafeCachePath):
        find_versioned_cache(cache_base, str(source))
    assert target.exists()


def test_cleanup_old_versions_never_deletes_through_a_symlink(tmp_path):
    victim, target = _outside_victim(tmp_path)
    cache_base = tmp_path / "cache"
    cache_base.mkdir()
    (cache_base / "link").symlink_to(victim, target_is_directory=True)
    for name in ("a", "b", "c"):
        (cache_base / name).mkdir()

    with pytest.raises(UnsafeCachePath):
        cleanup_old_versions(cache_base, keep=1)
    assert target.exists()


def test_cleanup_old_versions_rejects_symlinked_cache_root(tmp_path):
    victim, target = _outside_victim(tmp_path)
    cache_base = tmp_path / "cache"
    cache_base.symlink_to(victim, target_is_directory=True)

    with pytest.raises(UnsafeCachePath):
        cleanup_old_versions(cache_base, keep=1)
    assert target.exists()


def test_checkpoint_load_rejects_symlinked_checkpoint_file(tmp_path):
    victim, target = _outside_victim(tmp_path)
    outside = victim / "checkpoint.json"
    outside.write_text(json.dumps({"stage": "done", "input_hash": "x"}), "utf-8")
    source = tmp_path / "input.mp4"
    source.write_bytes(b"video")
    ck_path = tmp_path / "checkpoint.json"
    ck_path.symlink_to(outside)

    with pytest.raises(UnsafeCachePath):
        Checkpoint(str(ck_path), str(source)).load()
    assert outside.exists()


def test_checkpoint_save_rejects_symlinked_checkpoint_file(tmp_path):
    victim, _target = _outside_victim(tmp_path)
    outside = victim / "checkpoint.json"
    outside.write_text("{}", encoding="utf-8")
    source = tmp_path / "input.mp4"
    source.write_bytes(b"video")
    ck_path = tmp_path / "checkpoint.json"
    ck_path.symlink_to(outside)

    with pytest.raises(UnsafeCachePath):
        Checkpoint(str(ck_path), str(source)).save("done")
    assert outside.read_text(encoding="utf-8") == "{}"


def test_checkpoint_save_does_not_use_a_predictable_tmp_name(tmp_path):
    """固定 .tmp 名字可被提前放软链接占位；必须用不可碰撞的独占临时名。

    走的是带身份的 done 写入——即本批要合并进 save() 的那条路径，
    也正是原先 _stamp_checkpoint_ownership 用可预测 .tmp 名的地方。
    """
    victim, target = _outside_victim(tmp_path)
    source = tmp_path / "input.mp4"
    source.write_bytes(b"video")
    ck_path = tmp_path / "checkpoint.json"
    (tmp_path / "checkpoint.json.tmp").symlink_to(target)

    Checkpoint(str(ck_path), str(source)).save(
        "done", run_id="run-1", result_srt="/j/a.srt", result_video=None,
    )

    assert target.read_bytes() == b"precious user data"
    assert json.loads(ck_path.read_text(encoding="utf-8"))["run_id"] == "run-1"


def test_checkpoint_save_writes_ownership_in_the_same_safe_call(tmp_path):
    """A9：身份、成品路径、完成状态一次写入，不再事后直读直改。"""
    source = tmp_path / "input.mp4"
    source.write_bytes(b"video")
    ck_path = tmp_path / "checkpoint.json"

    Checkpoint(str(ck_path), str(source)).save(
        "done", meta={"outputMode": "srt"},
        run_id="run-77", result_srt="/j/a.srt", result_video=None,
    )

    data = json.loads(ck_path.read_text(encoding="utf-8"))
    assert data["stage"] == "done"
    assert data["run_id"] == "run-77"
    assert data["result_srt"] == "/j/a.srt"
    assert data["result_video"] is None


def test_worker_no_longer_bypasses_checkpoint_encapsulation():
    """_stamp_checkpoint_ownership 的直读—改—替换路径必须收回。"""
    from backend import worker

    assert not hasattr(worker, "_stamp_checkpoint_ownership")


# ── main.py 四个访问点 ───────────────────────────────────────────────────

def test_cleanup_old_cache_never_deletes_outside_the_job_dir(tmp_path, monkeypatch):
    """已实测复现过的越界删除：每次启动后台静默运行，必须堵死。"""
    victim, target = _outside_victim(tmp_path)
    _job_with_symlinked_cache(tmp_path, victim)
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path / "jobs")

    main._cleanup_old_cache()

    assert target.exists(), "cache 软链接导致任务目录外的文件被删除"


def test_cleanup_old_cache_skips_a_symlinked_job_dir(tmp_path, monkeypatch):
    victim, target = _outside_victim(tmp_path)
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    real_job = tmp_path / "real_job"
    real_job.mkdir()
    (real_job / "meta.json").write_text(
        json.dumps({"status": "done", "file_hash": "f" * 64, "config": {}}),
        encoding="utf-8",
    )
    cache = real_job / "cache" / "v1"
    cache.mkdir(parents=True)
    (cache / "audio.wav").write_bytes(b"x")
    os.utime(cache / "audio.wav", (1, 1))
    (jobs_root / "linked").symlink_to(real_job, target_is_directory=True)
    monkeypatch.setattr(main, "JOBS_DIR", jobs_root)

    main._cleanup_old_cache()

    assert (cache / "audio.wav").exists(), "软链接任务目录不得被清理流程跟随"
    assert target.exists()


def test_startup_scan_skips_symlinked_job_dirs(tmp_path, monkeypatch):
    victim, _target = _outside_victim(tmp_path)
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    real_job = tmp_path / "outside_job"
    real_job.mkdir()
    (real_job / "meta.json").write_text(
        json.dumps({"status": "done", "file_hash": "f" * 64, "config": {}}),
        encoding="utf-8",
    )
    (jobs_root / "linked").symlink_to(real_job, target_is_directory=True)
    monkeypatch.setattr(main, "JOBS_DIR", jobs_root)
    monkeypatch.setattr(main, "GLOBAL_HASH_INDEX", {})
    monkeypatch.setattr(main, "JOB_ID_TO_HASH", {})

    main._scan_jobs_into_index()

    assert main.GLOBAL_HASH_INDEX == {}, "软链接任务目录不得进入索引"


def test_matching_checkpoint_skips_symlinked_checkpoint(tmp_path):
    """恢复与 resume 都经过它：checkpoint 是软链接时必须安全失败。

    其余匹配条件（输入文件在任务目录内、哈希一致、版本目录名一致、配置哈希
    一致）全部构造成成立，这样唯一能拒绝它的就只剩软链接校验本身。
    """
    victim, _target = _outside_victim(tmp_path)
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    source = job_dir / "input.mp4"
    source.write_bytes(b"video")
    full_hash = checkpoint_module.get_file_hash(str(source))
    input_hash = full_hash[:16]
    outside = victim / "checkpoint.json"
    outside.write_text(json.dumps({
        "input_file": str(source), "input_hash": input_hash,
        "stage": "done", "meta": {},
    }), encoding="utf-8")
    cache_dir = job_dir / "cache" / input_hash[:12]
    cache_dir.mkdir(parents=True)
    meta = {"config": {}, "file_hash": full_hash}

    # 先证明"除了软链接，其它条件都成立"：真文件时必须匹配成功。
    real = cache_dir / "checkpoint.json"
    real.write_text(outside.read_text(encoding="utf-8"), encoding="utf-8")
    assert main._matching_checkpoint(job_dir, meta) is not None

    real.unlink()
    real.symlink_to(outside)

    assert main._matching_checkpoint(job_dir, meta) is None


def test_worker_checkpoint_read_rejects_symlink(tmp_path):
    """worker 直读 checkpoint 的那一处也必须走同一道校验。"""
    from backend import worker

    victim, _target = _outside_victim(tmp_path)
    outside = victim / "checkpoint.json"
    outside.write_text(json.dumps({"meta": {"stolen": True}}), encoding="utf-8")
    ck_path = tmp_path / "checkpoint.json"
    ck_path.symlink_to(outside)

    assert worker._read_checkpoint_meta(str(ck_path)) == {}
