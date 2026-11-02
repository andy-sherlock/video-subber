"""永不双行（用户 2026-10-12 定）：任何输出屏都不许含换行符。"""
import json
from pathlib import Path
from types import SimpleNamespace

from backend.core import display_timeline as dt

EXP = Path(__file__).parent / 'display_data'   # 随仓库带的显示层测试数据（原在实验文件夹 显示实验-2026-10-07）


def test_six_replays_have_no_newline_in_any_screen():
    total = 0
    for film in ('新片', '新西兰', '欧洲女孩'):
        for n in (1, 2):
            segs = json.loads((EXP / f'重排/{film}/V4-第{n}遍/显示输入.json').read_text(encoding='utf-8'))
            ents = dt.build_display_timeline([SimpleNamespace(**s) for s in segs])
            total += len(ents)
            assert all('\n' not in e.text for e in ents), f'{film}第{n}遍有两行屏'
    assert total > 4000


def test_unsplittable_long_text_is_one_line_not_wrapped():
    zh = "好吧。在那些选项里，我选了“Momo怀旧杰克逊高地视频”。然后我们就出发去了机场，一路上都很顺利。"
    seg = SimpleNamespace(id=0, start=177.47, end=183.39, text="x", zh=zh, words=[{"word": "w0", "start": 177.47, "end": 183.39}],
                          speaker_change=False, uncertain=False)
    assert all('\n' not in e.text for e in dt.build_display_timeline([seg]))


def test_guard_collapses_any_leftover_newline():
    e = dt.DisplayEntry(start=0.0, end=2.0, text="前半句\n后半句")
    out = dt._no_double_line([e])
    assert out[0].text == "前半句后半句" and out[0].start == 0.0 and out[0].end == 2.0
