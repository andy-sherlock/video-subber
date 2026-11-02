"""P458：分块进度存档必须原子写，不能裸写。

`_save_qwen_chunk_ckpt` 在分块循环里每块调一次（47 分钟视频 = 10 次）。
裸 `open(w)` 会先把旧文件清空再写，中途被打断就留下半截 JSON；
读取端只能整份丢弃，用户损失的是**已完成的全部块**——本任务实测重转录约 19 分钟。

项目已有 `core/safe_archive.write_json_archive`（先写草稿 → 落盘 → 一瞬间顶替），
且本模块第 21 行就已经 import 了它，只是这一处写入没用。
"""

import json
from pathlib import Path

from backend.core import qwen_asr_runner


def test_chunk_checkpoint_write_goes_through_the_atomic_archive_layer(monkeypatch, tmp_path):
    """证明这次写入**真的命中**了 write_json_archive，不只是「测试通过」。"""
    calls = []

    def spy(path, payload, **kwargs):
        calls.append((str(path), payload))
        Path(path).write_text(json.dumps(payload), encoding="utf-8")
        return True

    monkeypatch.setattr(qwen_asr_runner, "write_json_archive", spy)

    ckpt = tmp_path / "qwen_chunks.json"
    qwen_asr_runner._save_qwen_chunk_ckpt(
        str(ckpt), [[0.0, 300.0]], [{"text": "hi"}], 1)

    assert len(calls) == 1, "存档没有走原子写公共层"
    assert calls[0][1]["done_idx"] == 1
    assert json.loads(ckpt.read_text(encoding="utf-8"))["done_idx"] == 1


def test_module_has_no_raw_archive_write_left(tmp_path):
    """源码断言：本模块内不得再出现裸 open(...,"w") + json.dump 的存档写法。

    防的是「重构时顺手换回裸写、代码不报错、测试还是绿的」——
    项目已有此类断言先例（test_a21_no_second_mapping_implementation）。
    """
    import inspect
    import re

    # ⑦.5：范围收到**目标函数**。原来是整模块级封杀，两个方向都会误判——
    # 把 ckpt_path 改名或换单引号，裸写就溜回来而测试照样绿；
    # 而将来任何一处与存档无关的 json.dump 都会莫名其妙打红这条。
    source = inspect.getsource(qwen_asr_runner._save_qwen_chunk_ckpt)
    assert not re.search(r"open\([^)]*,\s*['\"]w", source), "分块存档又用回了裸写"
    assert "json.dump(" not in source, "分块存档函数里不应再有裸 json.dump"
    assert "write_json_archive" in source, "分块存档没走原子写公共层"
