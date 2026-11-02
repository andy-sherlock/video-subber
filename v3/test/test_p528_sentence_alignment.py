"""P528 方案 B：句末换屏不再「第几个句号配第几个句号」硬配，改为快速门 + 允许跳过的顺序对齐。

真实现场：新片第 1 份 1058.64～1070.18 秒一屏挂 11.54 秒；足球段 1113.928～1120.168 秒第二屏开得太晚。
方案：pipelines/P528-正式方案-会话二-2026-10-23.md 第 5 节、合同 A1–A10。
锁定值：pipelines/P528-锁定清单-会话二-2026-10-23.md（24 屏，写死在下面，不许用修后输出回写）。
"""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core import display_timeline as dt

DATA = Path(__file__).parent / 'display_data' / '标点对齐'
FOOTBALL = DATA / '足球段' / '显示输入.json'      # 用户私人材料，只在本机；不进代码库


def _segs(path):
    return [SimpleNamespace(**s) for s in json.loads(path.read_text(encoding='utf-8'))]


def _build(name):
    if name == '足球段':
        return dt.build_display_timeline(_segs(FOOTBALL))
    return dt.build_display_timeline(_segs(DATA / name[:-1] / f'第{name[-1]}遍' / '显示输入.json'))


def _window(entries, t0, t1):
    return [(round(e.start, 3), round(e.end, 3), e.text, e.long_ok)
            for e in entries if min(e.end, t1) - max(e.start, t0) > 1e-9]


# ── A9：锁定清单 24 屏（start、end、text、long_ok；不比全片序号） ─────────────────
LOCKED = {
    'L1a': ('新西兰1', 2537.170, 2543.000, [
        (2537.170, 2538.530, '嗯 这个看起来不错', False),
        (2540.370, 2541.410, '嗯 有点酸', False),
        (2542.160, 2543.000, '不过很好吃', False)]),
    'L1b': ('新西兰2', 2537.170, 2543.000, [
        (2537.170, 2538.530, '嗯 这个看起来很不错', False),
        (2540.370, 2541.410, '嗯 有点酸', False),
        (2542.160, 2543.000, '不过很好吃', False)]),
    'L2a': ('欧洲女孩1', 1738.304, 1742.464, [
        (1738.304, 1739.664, '我就是把所有的东西反复练', False),
        (1739.664, 1742.464, '自己找书自己学', False)]),
    'L2b': ('欧洲女孩2', 1738.304, 1742.464, [
        (1738.304, 1739.664, '我就是把所有的东西反复练', False),
        (1739.664, 1742.464, '我自己找书 自己学', False)]),
    # 2026-10-27 C19（为凑 0.84 秒最多推后下一屏 0.1 秒）重定，用户看并排视频后同意：原锁定
    # (944.500, 946.500, '挺有意思 两款我都喜欢') 拆成两屏，「两款我都喜欢」对上 “I like” 945.70 开口
    'L3': ('新西兰1', 942.100, 946.500, [
        (942.100, 944.020, '嗯', False),
        (944.500, 945.340, '挺有意思', False),
        (945.700, 946.540, '两款我都喜欢', False)]),
    'L4': ('新片1', 1052.880, 1080.982, [
        (1052.880, 1055.360, '我回到英国 参加了考试', False),
        (1055.440, 1057.680, '考得很好 拿到了很好的成绩', False),
        (1058.640, 1062.800, '炫耀一下 但我确实拿到了好成绩', False),
        (1063.622, 1064.742, '然后我想 好吧', False),
        (1064.742, 1066.982, '如果我要听懂这些人说的西班牙语', False),
        (1066.982, 1070.182, '我得试着多听母语者的内容', False),
        (1070.182, 1072.262, '不能一直停留在学习者材料上', False),
        (1072.982, 1074.902, '于是我开始听', False),
        (1074.902, 1078.662, '一些比较简单的母语者内容', False),
        (1079.062, 1080.982, '我在几个视频里提过', False)]),
    'L5': ('足球段', 1113.928, 1120.168, [
        (1113.928, 1117.368, '我爱足球 但那已经做过了', False),
        (1117.368, 1120.168, '我大多数爱好 我觉得都已经做过了', False)]),
}


def test_locked_list_has_24_screens():
    # 2026-10-27 L3 一屏拆成两屏（C19，用户同意）：24 → 25
    assert sum(len(v[3]) for v in LOCKED.values()) == 25


@pytest.mark.parametrize("key", sorted(LOCKED))
def test_locked_screens(key):
    name, t0, t1, want = LOCKED[key]
    if name == '足球段' and not FOOTBALL.exists():
        pytest.skip(f'足球段是用户私人材料，本环境没有 {FOOTBALL}——L5 必须在用户本机实际跑到')
    assert _window(_build(name), t0, t1) == want


# ── 验收规则第 2 版第 5 条：四指标每个文件都不比改前差（短屏每文件允许多 1） ────────────
HAN = re.compile(r"[一-鿿]")


def _metrics(screens):
    d = [b - a for a, b, _ in screens]
    return (max(d), sum(x > 7.0 + 1e-6 for x in d),
            sum(len(HAN.findall(t)) / x > 9 for (_, _, t), x in zip(screens, d) if x > 0),
            sum(x < 0.84 - 1e-6 for x in d))


@pytest.mark.parametrize("name", ['新片1', '新片2', '新西兰1', '新西兰2', '欧洲女孩1', '欧洲女孩2'])
def test_four_metrics_not_worse(name):
    before = json.loads((DATA / '改前答案-be62d4f.json').read_text(encoding='utf-8'))['files'][name]['screens']
    old = _metrics([(s['start'], s['end'], s['text']) for s in before])
    new = _metrics([(round(e.start, 3), round(e.end, 3), e.text) for e in _build(name)])
    # 2026-10-27 插入④施工B 时间错位：「每秒汉字 >9 的屏」这一项按用户同意的新值放行——D4 不推后下一屏
    # （用户知道读不完屏增多后同意）+ C19 让短句单独成屏（用户看并排视频后同意）。其余三项照旧不许变差。
    fast_ok = {'新西兰1': 18, '新西兰2': 18, '欧洲女孩2': 3}.get(name, old[2])
    assert new[0] <= old[0] + 1e-9 and new[1] <= old[1] and new[2] <= fast_ok and new[3] <= old[3] + 1, (old, new)


# ── A1/A7：两种排版遍都真的走到对齐（不是前置条件提前返回） ─────────────────────
def test_alignment_reached_in_both_passes(monkeypatch):
    seen = []
    orig = dt._align_sentence_cuts

    def spy(zh, words):
        seen.append(dt._RULES.pref)
        return orig(zh, words)
    monkeypatch.setattr(dt, "_align_sentence_cuts", spy)
    _build('新片1')
    assert True in seen and False in seen


# ── 构造小例子：英文 10 词每 0.5 秒一个（无停顿），中文「我们到了。」+ 16 字 ────────────
ZH = "我们到了。" + "然后去吃饭了然后去逛街了后来回家" + "。"


def _words(texts, *, overlap_at=None):
    out = []
    for i, t in enumerate(texts):
        a, b = i * 0.5, i * 0.5 + 0.45
        if overlap_at is not None and i == overlap_at - 1:
            b = (i + 1) * 0.5 + 0.1      # 左词结束晚于右词开始：这一刀不合法
        out.append({"word": t, "start": a, "end": b})
    return out


EN_LATE = ["We", "arrived,", "and", "then", "we", "ate", "and", "shopped.", "then", "home"]   # 逗号在 2、句号在 8
EN_EARLY = ["We", "arrived.", "and", "then", "we", "ate", "and", "shopped", "then", "home"]   # 句号在 2


def test_count_match_outside_gate_goes_to_alignment():
    """中英各 1 个句号（个数相等），但位置差 0.6 > 0.35：旧代码硬配到第 8 词；B 走对齐，配到第 2 词处的逗号。"""
    found = dt._find_safe_cut(0.0, 5.0, ZH, _words(EN_LATE), dt._SENT_END, require_min_display=False)
    assert found is not None and found[:2] == (5, 2)


def test_count_match_inside_gate_returns_without_alignment(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("位置差在 0.35 以内，不该进对齐")
    monkeypatch.setattr(dt, "_align_sentence_cuts", boom)
    found = dt._find_safe_cut(0.0, 5.0, ZH, _words(EN_EARLY), dt._SENT_END, require_min_display=False)
    assert found is not None and found[:2] == (5, 2)


def test_alignment_pairs_are_strictly_increasing_and_only_zh_sentence_ends():
    zh = "我们到了，先休息。然后去吃饭，吃完了。后来回家"
    words = _words(["We", "arrived,", "rested.", "Then", "we", "ate,", "finished.", "then", "home", "now"])
    pairs = dt._align_sentence_cuts(zh, words)
    assert pairs, "这个例子应该配得上"
    zh_pos, en_idx = zip(*pairs)
    assert list(zh_pos) == sorted(set(zh_pos)) and list(en_idx) == sorted(set(en_idx))
    assert all(zh[p - 1] in "。！？!?" for p in zh_pos)


def test_alignment_empty_when_nothing_pairs():
    assert dt._align_sentence_cuts("我们到了。后来回家", _words(["a", "b", "c", "d", "e", "f"])) == []


def test_illegal_aligned_cut_is_rejected(monkeypatch):
    """A5：对齐给出的切点词时间重叠 → 不合法，不许用；退到现有比例回退，不硬切在那里。"""
    found = dt._find_safe_cut(0.0, 5.0, ZH, _words(EN_LATE, overlap_at=2), dt._SENT_END,
                              require_min_display=False)
    assert found is None or found[1] != 2


def test_polish_periods_step_still_first(monkeypatch):
    """A2：保护遍里润色句号步骤配得上就直接用，不进 B。"""
    def boom(*a, **k):
        raise AssertionError("润色句号步骤已成功，不该进对齐")
    monkeypatch.setattr(dt, "_align_sentence_cuts", boom)
    monkeypatch.setattr(dt, "_RULES", dt._Rules(pref=True))
    words = [dict(w, _pe=(i == 1)) for i, w in enumerate(_words(EN_LATE))]
    found = dt._find_safe_cut(0.0, 5.0, ZH, words, dt._SENT_END, require_min_display=False)
    assert found is not None and found[:2] == (5, 2)


def test_input_unchanged_by_alignment():
    """A4 ①：对齐只读输入，不改中文、词表、词时间。"""
    words = _words(EN_LATE)
    snapshot = json.dumps(words)
    dt._find_safe_cut(0.0, 5.0, ZH, words, dt._SENT_END, require_min_display=False)
    assert json.dumps(words) == snapshot


# ── A10：P533 数字保护接入 B 的中文弱边界 ───────────────────────────────────
def test_zh_bounds_skip_number_punct():
    zh = "起飞时间是10:25，然后登机。后来到了"
    positions = [p for p, _strong, _ratio in dt._align_zh_bounds(zh)]
    assert zh.index(":") + 1 not in positions
    assert zh.index("，") + 1 in positions and zh.index("。") + 1 in positions
