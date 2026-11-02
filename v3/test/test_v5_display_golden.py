"""V5 显示层验收 A：用 V4 的缓存重排，逐屏对照"梯子关掉"的基线（4444 屏）。
基线 `V5基线-汉字口径-梯子关-六遍逐屏.json` 由 `重新冻结-汉字口径-2026-10-13.py` 把 `_ladder_needed` 恒置 False 跑出来
（2026-10-13：汉字口径 + `_ratio_index` 英文词算 1 个字符之后重新冻结；旧基线 058031b 是旧口径，已不能用，原样留着）。

切点梯子之后的口径：非靶子屏逐屏不许变；靶子屏（靶子清单-汉字口径.json）允许不变，或切成两屏；
屏数 = 基线屏数 + 切开个数。梯子本身的验收（切开的编号集合、违规名单等）在 test_cut_ladder.py。"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core import display_timeline as dt

EXP = Path(__file__).parent / 'display_data'   # 随仓库带的显示层测试数据（原在实验文件夹 显示实验-2026-10-07）
INV = EXP / '双行调查'
GOLD = json.loads((INV / 'V5基线-汉字口径-梯子关-六遍逐屏.json').read_text(encoding='utf-8'))['遍']
TARGET_KEYS = {(t['片'], t['遍'], round(t['开始秒'], 2))
               for t in json.loads((INV / '靶子清单-汉字口径.json').read_text(encoding='utf-8'))}


@pytest.mark.parametrize('key', sorted(GOLD))
def test_v5_screens_match_baseline(key):
    film, n = key.split('-第'); n = int(n.rstrip('遍'))
    segs = json.loads((EXP / f'重排/{film}/V4-第{n}遍/显示输入.json').read_text(encoding='utf-8'))
    ents = dt.build_display_timeline([SimpleNamespace(**s) for s in segs])
    assert all('\n' not in e.text for e in ents), '永不双行：成品里还有换行'
    got = [[round(e.start, 2), round(e.end, 2), e.text, e.long_ok] for e in ents]
    want = GOLD[key]
    j, splits = 0, 0
    for w in want:
        if (film, n, w[0]) in TARGET_KEYS and got[j] != w:
            assert got[j][0] == w[0] and got[j + 1][1] == w[1], f'靶子 {w[0]} 切开后首尾对不上'
            j += 2; splits += 1
        else:
            assert got[j] == w, f'非靶子屏变了：基线 {w} 现在 {got[j]}'
            j += 1
    assert j == len(got) == len(want) + splits, f'屏数 {len(got)} != {len(want)} + {splits}'
