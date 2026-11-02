"""P533：数字中间的半角冒号、逗号（10:25、3:2、1,000）不是句子边界，不许当切点。

真实现场：新西兰两份翻译约 75.6 秒「我们很乐意说明预计起飞时间是10:25。」被切成「…10:」｜「25」。
数字判断全项目只有一份（`_number_punct_cut`）；下面每条旁路各自单独打到，证明它真的走到了。
方案：pipelines/P528-正式方案-会话二-2026-10-23.md 第 6 节、合同 A10。
"""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core import display_timeline as dt

DATA = Path(__file__).parent / 'display_data' / '标点对齐'


def _words(times):
    return [{"word": f"w{i}", "start": a, "end": b} for i, (a, b) in enumerate(times)]


def _even(n, start, step):
    return [(start + i * step, start + i * step + step * 0.9) for i in range(n)]


def _build(name, n):
    segs = json.loads((DATA / name / f'第{n}遍' / '显示输入.json').read_text(encoding='utf-8'))
    return dt.build_display_timeline([SimpleNamespace(**s) for s in segs])


# ── 共用判断本身 ──────────────────────────────────────────────

@pytest.mark.parametrize("text", ["起飞时间是10:25。", "比分3:2", "一共1,000人", "１０:２５"])
def test_number_inner_punct_is_not_a_cut(text):
    p = re.search(r"[:,]", text).end()
    assert dt._number_punct_cut(text, p)


@pytest.mark.parametrize("text", ["说明:内容", "甲,乙", "时间是10：25", "到了10: 然后", "第3,还有", "现在,25岁"])
def test_ordinary_punct_unchanged(text):
    p = re.search(r"[:,：]", text).end()
    assert not dt._number_punct_cut(text, p)


# ── 隐藏断点记号夹在数字里（⑦.5 Codex 发现，用户 2026-10-23 定一起修） ──────────
M = dt.SPLIT_MARK


@pytest.mark.parametrize("text,p", [
    (f"10:{M}25", 3),          # 记号在冒号后，切点在冒号后
    (f"10:{M}25", 4),          # 切点落在记号后（三行拆屏回算位置会跨过记号）
    (f"10{M}:25", 4),          # 记号在冒号前
    (f"1,{M}000", 2),
])
def test_split_mark_inside_number_still_protected(text, p):
    assert dt._number_punct_cut(text, p)


@pytest.mark.parametrize("text,p", [(f"说明:{M}内容", 3), (f"说明{M}:内容", 4), (f"10:{M}然后", 3)])
def test_split_mark_next_to_ordinary_punct_unchanged(text, p):
    assert not dt._number_punct_cut(text, p)


@pytest.mark.parametrize("core", [f"10:{M}25", f"10{M}:25", f"1,{M}000"])
def test_full_layout_keeps_number_with_split_mark(core):
    """完整排版：Codex 复现用的输入（20 个等距原词覆盖 8 秒）不再被切成「…10:」｜「25…」。"""
    zh = "我们很乐意向大家说明预计起飞是" + core + "然后马上就会开始登机了"
    words = [{"word": f"w{i}", "start": 10 + i * 0.4, "end": 10 + i * 0.4 + 0.36} for i in range(20)]
    seg = SimpleNamespace(id=0, start=10.0, end=18.0, text="x", zh=zh, words=words)
    texts = [e.text.replace(M, "") for e in dt.build_display_timeline([seg])]
    assert any(core.replace(M, "") in t for t in texts), texts


# ── 旁路一：逗号分支（完整排版，真实输入） ─────────────────────────

@pytest.mark.parametrize("n", [1, 2])
def test_real_input_keeps_1025_on_one_screen(n, monkeypatch):
    """完整 build_display_timeline：含「10:25」的那句不再被切成「10:」｜「25」。
    同时数被拒的次数，证明拒绝发生在 `_legal_cut`，不是因为前置条件提前返回。"""
    rejected = []
    orig = dt._number_punct_cut

    def spy(text, p):
        r = orig(text, p)
        if r:
            rejected.append(text)
        return r
    monkeypatch.setattr(dt, "_number_punct_cut", spy)
    entries = _build('新西兰', n)
    hits = [e for e in entries if 75 < e.start < 80]
    assert any("10:25" in e.text for e in hits), [e.text for e in hits]
    assert not any(e.text.endswith("10:") or e.text.strip() == "25" for e in hits)
    assert any("10:25" in t for t in rejected)


# ── 旁路二：两行屏 / 切点梯子用的 `_split_time` ─────────────────────

def test_split_time_refuses_number_inner_cut():
    flat = "我们先去超市买了很多东西在10:25然后回家做一桌好吃的饭菜"
    words = _words(_even(14, 12.0, 0.25))
    p = flat.index(":") + 1
    assert dt._split_time(12.0, 15.6, flat, words, p, min_units=1) is None
    assert dt._split_time(12.0, 15.6, flat, words, p, min_units=1, best=True, han=True) is None
    control = flat.replace("10:25", "说明:好")
    assert dt._split_time(12.0, 15.6, control, words, control.index(":") + 1, min_units=1) is not None


# ── 旁路三：两行屏候选 / 梯子自然切点 `_segment_cuts` ─────────────────

def test_segment_cuts_skip_number_inner_punct():
    flat, cuts = dt._segment_cuts("时间是10:25，我们一起走吧")
    assert flat.index(":") + 1 not in cuts
    assert flat.index("，") + 1 in cuts


def test_segment_cuts_skip_line_break_inside_number():
    flat, cuts = dt._segment_cuts("时间是10:\n25，我们一起走吧")
    assert flat == "时间是10:25，我们一起走吧"
    assert flat.index(":") + 1 not in cuts


# ── 旁路四：三行拆屏 `_english_cut_at` ─────────────────────────────

def test_english_cut_at_refuses_number_inner_cut():
    zh = "我们很乐意向在场的各位旅客说明一下今天预计的起飞时间是10:25然后大家可以先去候机厅里面休息一下等广播通知登机"
    words = _words(_even(24, 12.0, 0.5))
    p = zh.index(":") + 1
    assert dt._english_cut_at(12.0, 24.0, zh, words, p) is None
    control = zh.replace("10:25", "说明:好")
    assert dt._english_cut_at(12.0, 24.0, control, words, control.index(":") + 1) is not None


# ── 旁路五：7 秒窄例外 `_every_comma_cut_fragments` ────────────────────

def test_long_screen_check_does_not_count_number_punct_as_comma():
    zh = "我们很乐意向大家说明预计起飞是10:25然后马上就会开始登机了"
    assert dt._every_comma_cut_fragments(zh)                  # 没有真正的逗号 → 视作「没有好逗号可切」
    control = zh.replace("10:25", "现在,然后")                 # 真逗号两边都 ≥6 字 → 有好逗号可切
    assert not dt._every_comma_cut_fragments(control)


# ── 无数字标点的屏不许借机变化（六份公开材料整片对照改前答案） ─────────────

P533_CHANGES = {   # 六份材料里 P533 带来的全部变化（2026-10-23 施工 A 实测）；别的屏一屏都不许变
    # 2026-10-27 插入④施工B 时间错位 D9（每屏在最后一个英文词说完时结束，用户同意）：合起来那屏的结束
    # 79.886 → 78.206（数字规则本身的效果不变，只是这屏收得早了）。旧值 (75.566, 79.886, …)
    '新西兰1': ([(75.566, 78.206, '我们很乐意说明预计起飞时间是10:', False), (78.206, 79.886, '25', False)],
              [(75.566, 78.206, '我们很乐意说明预计起飞时间是10:25', False)]),
    '新西兰2': ([(75.566, 78.206, '我们很乐意说明预计起飞时间为10:', False), (78.206, 79.886, '25', False)],
              [(75.566, 78.206, '我们很乐意说明预计起飞时间为10:25', False)]),
}


def test_only_number_screens_change(monkeypatch):
    """同一份代码，数字保护关掉 / 打开各排一遍：两遍之间只许差 P533_CHANGES 那几屏。
    （P528 之后改前答案已不是 P533 的对照尺子，所以用「开关这一条规则」来量它单独的影响。）"""
    for film in ('新片', '新西兰', '欧洲女孩'):
        for n in (1, 2):
            new = [(round(e.start, 3), round(e.end, 3), e.text, e.long_ok) for e in _build(film, n)]
            with monkeypatch.context() as m:
                m.setattr(dt, "_number_punct_cut", lambda text, p: False)
                old = [(round(e.start, 3), round(e.end, 3), e.text, e.long_ok) for e in _build(film, n)]
            gone = [s for s in old if s not in set(new)]
            added = [s for s in new if s not in set(old)]
            assert (gone, added) == P533_CHANGES.get(f'{film}{n}', ([], [])), f'{film}{n}'
