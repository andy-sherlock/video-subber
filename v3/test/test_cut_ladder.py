"""切点梯子（用户 2026-10-12 定）：把"太长的一屏"切成两屏、每屏一行。

2026-10-13 改成汉字口径（字数 = 汉字个数，读速 = 汉字数 ÷ 秒）并修了 `_ratio_index`（英文词算 1 个字符），
验收靶心换成仓库外面的新冻结文件（绝对路径，不在就明确报错，不静默跳过），由
`重新冻结-汉字口径-2026-10-13.py` 生成，答案清单是**独立重算**的（不调用 `_cut_ladder`）：
  靶子清单-汉字口径.json、冻结答案清单-汉字口径.json、V5基线-汉字口径-梯子关-六遍逐屏.json、基线违规名单-汉字口径.json。
旧的 262 / 237·20·5 / 058031b 基线 / 违规名单文件原样保留、只读，不再当验收标准。
读速只有一份：dt._han_speed(start, end, text)。旧的字宽读速 dt._screen_reading_speed 只留给梯子之前的排版阶段。
"""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core import display_timeline as dt

EXP = Path(__file__).parent / 'display_data'   # 随仓库带的显示层测试数据（原在实验文件夹 显示实验-2026-10-07）
INV = EXP / '双行调查'
FILMS = ('新片', '新西兰', '欧洲女孩')
EPS = 1e-9

FROZEN = {  # 文件名 -> 指纹（2026-10-13 重新冻结，原因见施工记录）
    # 2026-10-24 插入④施工B 重新冻结（用户同意换答案）：C2「——」不从中间切，新西兰两遍各 3 处「——」及邻屏变化；
    # 新西兰2 1124.32 两屏并成一屏、成为新靶子 T266（A 会切开）。旧值：靶子 c7399470… 答案 b5e0fa03… 基线 1e30a0ca…
    # 2026-10-27 插入④施工B 时间错位（D4 不推后 / D9 话说完就收 / D16 合并门槛 20）重新冻结，用户看过逐屏变化后同意：
    # 靶子 188→194（去 T104 T158 T170 T263，新增 T267–T276）。旧值：靶子 15ed2454… 答案 9c434d25… 基线 9366a5ab… 违规 c37413d4…
    # 2026-10-27 再冻结：C19（D4 例外，为凑 0.84 秒最多推后下一屏 0.1 秒，用户看并排视频后同意）。
    # 靶子、答案不变；基线 86aacaba… → f7484c12…、违规 bf38e186… → a1d114f1…
    # 2026-10-27 晚 C19 第 6 条（只救本身是完整一句的短屏，用户看 A9 后定）：基线 f7484c12… → a82a5327…、违规 a1d114f1… → bbb94cee…
    # 2026-10-28 E1（C19 推后上限 0.1→0.15，用户看 ⑩ 试排视频后定）：靶子 194→193（去 T273 新西兰 31:33「哦耶 炸鱼薯条…」，拆成三屏不再超长）；
    #   靶子 e95191f0…→216a310c…、答案 43bc5b44…→33335c23…、基线 a82a5327…→b9fd1aa3…，违规不变
    '靶子清单-汉字口径.json': '216a310c34ca349293d8991460fa3f8b10553d9bb60781533f54a6f2fa33f2bf',
    '冻结答案清单-汉字口径.json': '33335c2356a33b5382e1eec7ccc81ed5ec7d086a57e8ded3903f0eaa3d85792d',
    'V5基线-汉字口径-梯子关-六遍逐屏.json': 'b9fd1aa392b1fe2392379a87206a425a8d04d48162a63b5be59d9aec737d7889',
    '基线违规名单-汉字口径.json': 'bbb94cee4b964626b97b76becfcc476ad49bec29b841e3b0f1296d9b3062765e',
}


def _load(name):
    path = INV / name
    if not path.exists():
        raise AssertionError(f'冻结文件不在：{path}（不许静默跳过）')
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == FROZEN[name], f'{name} 指纹变了——冻结文件被改过'
    return json.loads(raw.decode('utf-8'))


TARGETS = _load('靶子清单-汉字口径.json')
ANSWER = _load('冻结答案清单-汉字口径.json')
BASE_DOC = _load('V5基线-汉字口径-梯子关-六遍逐屏.json')
BASE = BASE_DOC['遍']
BASE_TOTAL = sum(len(v) for v in BASE.values())
BASE_BAD = _load('基线违规名单-汉字口径.json')

DUNHAO_EXPECTED = []     # 汉字口径之后没有靶子在切开后丢顿号；以后出现了先停下来查，别急着往里加编号

ID_OF = {(t['片'], t['遍'], round(t['开始秒'], 2)): t['编号'] for t in TARGETS}
KEY_OF = {v: k for k, v in ID_OF.items()}
A_SPLIT = set(ANSWER['A会切开'])
B_EXTRA = set(ANSWER['B比A多切开'])
NEVER = set(ANSWER['永远切不开'])

_CACHE = {}
_DUNHAO = []   # 第一屏结尾被去掉顿号的靶子编号


def _replay():
    """六遍离线重放（实验版本地数据，不联网）。返回 {'片-第n遍': (屏列表, 质量摘要)}。"""
    if _CACHE:
        return _CACHE
    for film in FILMS:
        for n in (1, 2):
            segs = json.loads((EXP / f'重排/{film}/V4-第{n}遍/显示输入.json').read_text(encoding='utf-8'))
            rep = dt.TimelineReport()
            ents = dt.build_display_timeline([SimpleNamespace(**s) for s in segs], report=rep)
            _CACHE[f'{film}-第{n}遍'] = (ents, rep)
    return _CACHE


def _violations(start, end, text):
    """违规口径：短于 0.84 秒、长于 7 秒（1.5 毫秒容差）；每秒汉字 >9。"""
    kinds = set()
    if end - start < dt.MIN_DISPLAY_S - 0.0015:
        kinds.add('短')
    if end - start > dt.MAX_DISPLAY_S + 0.0015:
        kinds.add('长')
    if dt._han_speed(start, end, text) > dt.READING_CPS + EPS:
        kinds.add('快')
    return kinds


def _align():
    """把输出和基线按"片+遍+开始秒"逐屏对齐。返回 (切开的靶子{编号: (第一屏, 第二屏)}, 保持一行的靶子{编号},
    非靶子屏列表, 新屏列表[(编号, 屏)], 其余屏列表[(键, 屏)])。同时做 5.4 第 1、2 条的逐屏检查。"""
    run = _replay()
    split, kept, new_screens, rest = {}, set(), [], []
    dunhao = []
    total = 0
    for key, base in BASE.items():
        film, n = key.split('-第'); n = int(n.rstrip('遍'))
        out = run[key][0]
        total += len(out)
        j = 0
        for b in base:
            tid = ID_OF.get((film, n, b[0]))
            if tid is None:
                o = out[j]; j += 1
                assert (round(o.start, 2), round(o.end, 2), o.text, o.long_ok) == tuple(b), \
                    f'非靶子屏变了：{key} 开始 {b[0]}：基线 {b} 现在 {(o.start, o.end, o.text, o.long_ok)}'
                rest.append(((film, n, b[0]), o))
                continue
            o = out[j]
            assert round(o.start, 2) == b[0], f'{tid} 的第一屏开始秒变了'
            if round(o.end, 2) == b[1] and o.text == b[2]:
                kept.add(tid); j += 1
                rest.append(((film, n, b[0]), o))
                continue
            o2 = out[j + 1]; j += 2
            # 屏尾不留顿号是显示层本来就有的规则（用户 2026-10-12 裁决：不改代码，检查放过它）。
            # 只放过一种情况：第一屏结尾恰好少了一个"、"；别的任何位置丢字、多字都报错。
            first, second, want = (''.join(x.split()) for x in (o.text, o2.text, b[2]))
            if first + second != want:
                assert first + '、' + second == want, f'{tid} 切开后文字对不上（丢字或重复）'
                dunhao.append(tid)
            assert round(o2.end, 2) == b[1], f'{tid} 第二屏结束秒不等于基线结束秒'
            assert o.end == o2.start, f'{tid} 两屏没有首尾相接'
            split[tid] = (o, o2)
            new_screens += [(tid, o), (tid, o2)]
        assert j == len(out), f'{key} 输出屏数和基线对不上'
    _DUNHAO[:] = dunhao
    return split, kept, new_screens, rest, total


# ───────────────────────── 读速只有一份（汉字口径） ─────────────────────────

def test_ladder_speed_counts_han_only_and_differs_from_the_old_width_speed():
    assert dt._han_speed(0.0, 1.0, "甲乙 丙丁") == dt._han_speed(0.0, 1.0, "甲乙丙丁") == 4.0   # 空格不算
    assert dt._han_speed(0.0, 1.0, "甲乙 abc 丙丁，") == 4.0                                      # 英文、标点不算
    assert dt._screen_reading_speed(0.0, 1.0, "甲乙 abc 丙丁，") > 4.0                           # 旧字宽读速会把它们算进去


def test_frozen_targets_are_193_and_answer_adds_up():
    # 2026-10-24 +T266（插入④施工B「——」连带变化）；2026-10-27 188→194（时间错位：D4/D9/D16，用户同意）；
    # 2026-10-28 194→193（E1 推后上限 0.15，T273 拆开不再是靶子）
    assert len(TARGETS) == 193
    assert len(A_SPLIT) == 183 and len(B_EXTRA) == 5 and len(NEVER) == 5
    assert not (A_SPLIT & B_EXTRA) and not (A_SPLIT & NEVER) and not (B_EXTRA & NEVER)
    assert len(A_SPLIT | B_EXTRA | NEVER) == 193
    assert NEVER == {'T062', 'T133', 'T160', 'T267', 'T270'}   # 2026-10-27 +T267 T270（时间错位施工新出现的靶子）
    assert all(t['汉字数'] > 16 or t['每秒汉字'] > 9 for t in TARGETS)        # 每个靶子都真的需要梯子


# ───────────────────────── 切开的是哪些屏 ─────────────────────────

def test_only_listed_targets_lose_a_trailing_dunhao_and_nothing_else_is_lost():
    """用户裁决（2026-10-12）只放过"第一屏结尾少一个顿号"；实测的名单见 DUNHAO_EXPECTED，多一个少一个都要停下来查。
    这条变红时：先查数据有没有变、梯子有没有改动，别急着改编号。"""
    _align()
    assert _DUNHAO == DUNHAO_EXPECTED


def test_t140_becomes_two_one_line_screens():
    split, _kept, _new, _rest, _t = _align()
    tid = ID_OF[('新西兰', 2, 368.23)]
    a, b = split[tid]
    assert '\n' not in a.text and '\n' not in b.text
    assert dt._han_count(a.text) <= dt.LINE_MAX_HAN and dt._han_count(b.text) <= dt.LINE_MAX_HAN


def test_t160_stays_one_line_and_is_on_the_books():
    run = _replay()
    tid = ID_OF[('新西兰', 2, 1032.05)]
    assert tid in NEVER
    ents, rep = run['新西兰-第2遍']
    assert any(abs(r[0] - 1032.05) < 0.006 for r in rep.unsplit_wide_screens), '账上没有 T160'
    row = next(r for r in rep.unsplit_wide_screens if abs(r[0] - 1032.05) < 0.006)
    assert row[1] == 12 and abs(row[2] - 9.375) < 0.01               # (开始秒, 汉字数, 每秒汉字)


def test_ledger_lists_exactly_the_kept_targets():
    run = _replay()
    keys = set()
    for key, (_ents, rep) in run.items():
        film, n = key.split('-第'); n = int(n.rstrip('遍'))
        keys |= {(film, n, round(r[0], 2)) for r in rep.unsplit_wide_screens}
    _split, kept, *_ = _align()
    assert {ID_OF[k] for k in keys} == kept == NEVER


def test_splits_exactly_the_frozen_188():
    split, kept, _new, _rest, total = _align()
    assert set(split) == A_SPLIT | B_EXTRA
    assert kept == NEVER
    assert total == BASE_TOTAL + 188   # 2026-10-28 E1：189→188（T273 不再是靶子）


def test_the_ab_switch_is_gone_and_the_fourth_level_is_always_on():
    """搬家前清理：A/B 开关整个删掉，只留 B 行为。变量没了、函数不再有 rescue 参数，第 4 级永远在。"""
    import inspect
    assert not hasattr(dt, 'LADDER_RESCUE')
    assert 'rescue' not in inspect.signature(dt._cut_ladder).parameters


# ───────────────────────── 5.4 四条检查 ─────────────────────────

def test_new_screens_are_all_legal():
    split, _kept, new_screens, _rest, _t = _align()
    for tid, s in new_screens:
        assert '\n' not in s.text
        assert s.end - s.start >= dt.MIN_DISPLAY_S - EPS, f'{tid} 太短'
        assert s.end - s.start <= dt.MAX_DISPLAY_S + EPS, f'{tid} 太长'
        cap = dt.READING_CPS_RESCUE if tid in B_EXTRA else dt.READING_CPS
        assert dt._han_speed(s.start, s.end, s.text) <= cap + EPS, f'{tid} 读太快'
        assert dt._han_count(s.text) <= dt.LINE_MAX_HAN, f'{tid} 超过一行 16 个汉字'
    # 不切在词中间（汉字之间的切口必须是分词工具认的词缝）
    for tid, (a, b) in split.items():
        base = BASE[next(k for k in BASE if k.startswith(KEY_OF[tid][0]) and k.endswith(f'第{KEY_OF[tid][1]}遍'))]
        text = next(x[2] for x in base if x[0] == KEY_OF[tid][2])
        found = False
        for q in range(1, len(text)):
            if text[:q].strip().rstrip('、') == a.text and text[q:].strip() == b.text:
                lq = len(text[:q].rstrip()); rq = q + len(text[q:]) - len(text[q:].lstrip())
                found = True
                if lq == rq and dt._is_han(text[lq - 1]) and dt._is_han(text[rq]):
                    assert lq in dt._jieba_bounds(text), f'{tid} 切在词中间'
                break
        assert found, f'{tid} 找不到切口位置'


def test_rest_violations_equal_baseline_minus_split_targets():
    split, _kept, _new, rest, _t = _align()
    gone = {KEY_OF[t] for t in split}
    want = set()
    for kind in ('短于0.84秒', '长于7秒', '每秒汉字大于9'):
        for r in BASE_BAD[kind]:
            k = (r['片'], r['遍'], r['开始秒'])
            if k not in gone:
                want.add((k, {'短于0.84秒': '短', '长于7秒': '长', '每秒汉字大于9': '快'}[kind]))
    got = set()
    for k, o in rest:
        for kind in _violations(o.start, o.end, o.text):
            got.add(((k[0], k[1], k[2]), kind))
    assert got == want, f'多出 {sorted(got - want)} 少了 {sorted(want - got)}'


def test_violation_counts_are_pinned():
    """汉字口径重新冻结后实测：短 1、长 3、每秒汉字>9 共 9，合计 13 个屏（多切的 4 个原本就不在违规名单里；上一条用基线名单独立核对，这条只是把数字钉住）。
    2026-10-27 插入④施工B 时间错位（用户知道代价后同意）：短 0、长 5、快 33，合计 38——快屏增加是 D4 不推后下一屏的代价，⑩ 真实任务再数。"""
    _split, _kept, _new, rest, _t = _align()
    per = {'短': set(), '长': set(), '快': set()}
    for k, o in rest:
        for kind in _violations(o.start, o.end, o.text):
            per[kind].add(k)
    # 2026-10-27 C19（短句单独成屏，用户看片同意）：快 33→36，合计 38→41
    # 同日晚 C19 第 6 条（半截句不单独成屏）：新西兰 42:12「我们吃的第一口」并回去，快 36→35，合计 41→40
    assert (len(per['短']), len(per['长']), len(per['快'])) == (0, 5, 35)
    assert len(per['短'] | per['长'] | per['快']) == 40


def test_no_screen_in_the_six_runs_is_wider_than_one_line():
    """绊线：汉字口径不再把英文字母算宽度，所以"汉字≤16"的屏不再进梯子。六遍真实数据里，每一屏的物理宽度
    （旧字宽，`_units`）必须 ≤22.5（1280 宽画面一行大约放这么多；这个 22.5 来自交接文件，没有重新量）。
    实测最宽 20.5。这条变红 = 出现了"汉字少但英文很长、一行放不下"的屏，要先停下来看，不要改数字。"""
    for key, (ents, _rep) in _replay().items():
        for e in ents:
            assert dt._units(e.text) <= 22.5 + EPS, f'{key} {e.start}: {e.text!r} 宽 {dt._units(e.text)}'


def test_b_extra_4_fastest_block_is_between_9_and_10():
    split_b, *_ = _align()
    speeds = [max(dt._han_speed(s.start, s.end, s.text) for s in split_b[t]) for t in B_EXTRA]
    assert all(dt.READING_CPS < v <= dt.READING_CPS_RESCUE + EPS for v in speeds)


# ───────────────────────── 拆成份三问（造假屏） ─────────────────────────
# 假屏：词表是假英文词，时间由测试自己指定。每条"返回 None"的测试都配对照：同样的文字、同样的词，
# 只改一个变量，结果必须反过来——这样才能证明 None 是被那一条规则挡住的，不是别的提前退出。
# 用拉丁文字造的假屏没有汉字词缝，只有逗号一个切点，行为最容易看清。
# 汉字口径之后假屏也用汉字写（括号里是左块、右块的汉字数）。只有逗号一个切点：测试里用 seams=False 关掉分词词缝。
C_RIGHT_FAST = "我们去超市买东西，然后我们一起回家做饭再看会儿电视"     # 8 | 16
C_LEFT_FAST = "我们今天先去超市买了很多的东西，回家做饭吧"             # 15 | 5
C_TOO_WIDE = "我们今天先去超市买了很多新鲜好吃的东西，然后回家做饭"   # 19 | 6
C_FITS = "我们今天先去超市买了很多东西，然后回家做饭"               # 14 | 6
SHORT = "先去超市买东西，然后回家做饭"                              # 左 7、右 6：宽度、读速都不是问题，只看时间


def _words(times):
    return [{"word": f"w{i}", "start": s, "end": e} for i, (s, e) in enumerate(times)]


def _even(n, start, step):
    return [(start + i * step, start + i * step + step * 0.9) for i in range(n)]


def _span(n, a, b):
    return _even(n, a, (b - a) / n)


def _ladder(text, start, end, times, *, seams=True):
    """seams=False：把分词工具的词缝全部关掉，只剩标点这一个切点（汉字没有"纯拉丁字母"那种天然没有词缝的写法，
    用它还原"只有逗号一个切点、行为最容易看清"的假屏环境）。"""
    with pytest.MonkeyPatch.context() as m:
        if not seams:
            m.setattr(dt, '_jieba_bounds', lambda _s: set())
        return dt._cut_ladder(start, end, text, _words(times), False, False)


def _is_one_clean_cut(r, start, end):
    return (r is not None and len(r) == 2 and r[0][0] == start and r[1][1] == end and r[0][1] == r[1][0]
            and '\n' not in r[0][2] + r[1][2])


def test_q1_right_block_too_fast_means_no_cut_at_all():
    # 右半句 13.6 秒才开口，只剩 1.4 秒读 16 个汉字（每秒 11.4）→ 读速超标。两块是一起判的：右块不行，
    # 整刀不成立，返回 None（保持一行），绝不只切前半。
    left = _span(6, 12.0, 12.3)
    assert _ladder(C_RIGHT_FAST, 12.0, 15.0, left + _span(8, 13.6, 14.95), seams=False) is None
    # 对照：左半句一模一样，只把右半句的开口提前到 13.0 → 必须切开
    assert _is_one_clean_cut(_ladder(C_RIGHT_FAST, 12.0, 15.0, left + _span(8, 13.0, 14.95), seams=False), 12.0, 15.0)


def test_q1b_left_block_too_fast_means_no_cut_at_all():
    # 镜像：右半句 12.95~13.3 秒就开口，左块（15 个汉字）最多只有 1.0~1.5 秒，每秒 10 个以上 → 读速超标 → None
    # （时间本身够 0.84 秒，所以挡住它的是读速，不是最短时间——变异 M8 不会让它变红、M9 会）
    left = _span(8, 12.0, 12.5)
    assert _ladder(C_LEFT_FAST, 12.0, 15.0, left + _span(3, 12.95, 13.3), seams=False) is None
    # 对照：左半句一模一样，只把右半句的开口推迟到 13.8 之后 → 必须切开
    assert _is_one_clean_cut(_ladder(C_LEFT_FAST, 12.0, 15.0, left + _span(3, 13.8, 14.9), seams=False), 12.0, 15.0)


def test_q2_a_cut_that_fails_on_time_does_not_poison_the_next_cut():
    # 两个逗号：第一刀在时间上不合法（词扎堆），第二刀合法 → 必须用第二刀，不能因第一刀失败就整屏放弃
    text = "买很多东西，然后回家做饭，再看一会儿电视节目"
    squeezed = [(12.0, 12.018), (12.02, 12.29), (12.32, 12.365), (12.37, 12.415), (12.42, 12.438),
                (12.44, 12.71), (12.74, 12.758), (12.76, 12.778), (12.78, 13.05), (13.08, 13.35),
                (13.38, 13.425), (13.43, 13.79)]
    flat, cuts = dt._segment_cuts(text)
    kw = dict(min_units=5, cps=dt.READING_CPS, best=True)
    assert dt._split_time(12.0, 15.0, flat, _words(squeezed), cuts[0], **kw) is None            # 第一刀单独：不行
    assert dt._split_time(12.0, 15.0, flat, _words(squeezed), cuts[1], **kw) is not None        # 第二刀单独：行
    # 对照：同样的文字，词匀速铺开，第一刀单独就合法 → 上面的 None 确实是"词扎堆"造成的
    assert dt._split_time(12.0, 15.0, flat, _words(_span(12, 12.0, 14.9)), cuts[0], **kw) is not None
    r = _ladder(text, 12.0, 15.0, squeezed)
    assert _is_one_clean_cut(r, 12.0, 15.0)
    assert r[0][2].replace(" ", "") == "买很多东西然后回家做饭" and r[1][2] == "再看一会儿电视节目"


def test_level1_requires_five_units_per_block_so_a_small_block_cut_is_not_chosen():
    # 第一个逗号的左块只有 3 字宽、时间合法；但第 1 级要求每块 ≥5 字宽，所以必须选第二刀。
    # （去掉最少字数检查，它就会改选第一刀——比如读速更舒服的小块那刀。）
    text = "好的啊，我们去超市买东西，然后回家做饭"
    times = [(12.0, 12.45), (12.5, 12.95), (13.0, 13.45), (13.5, 13.95), (14.0, 14.36),
             (14.4, 14.76), (14.8, 14.89), (14.9, 14.99), (15.0, 15.09), (15.1, 15.19)]
    flat, cuts = dt._segment_cuts(text)
    assert dt._split_time(12.0, 15.3, flat, _words(times), cuts[0], min_units=3, cps=dt.READING_CPS, best=True) is not None
    r = _ladder(text, 12.0, 15.3, times)
    assert _is_one_clean_cut(r, 12.0, 15.3) and r[1][2] == '然后回家做饭' and r[0][1] > 14.0


def test_q3_window_too_short_means_no_cut_but_longer_window_cuts():
    text = "看风景等等之前，先调整一下"
    # 整屏只有 1.28 秒：两块各要 ≥0.84 秒，物理上放不下 → None（保持一行）
    assert _ladder(text, 10.0, 11.28, _span(8, 10.0, 11.28)) is None
    # 对照：同样的文字、同样的词，窗口放大到 4 秒，必须切得开
    assert _is_one_clean_cut(_ladder(text, 10.0, 14.0, _span(8, 10.0, 14.0)), 10.0, 14.0)


def test_q3b_left_block_shorter_than_min_display_means_no_cut():
    # 词全挤在前 0.5 秒：切点最晚 12.8 秒，左块不足 0.84 秒 → None
    assert _ladder(SHORT, 12.0, 15.0, _span(10, 12.0, 12.5)) is None
    # 对照：词铺到 13.5 秒，左块够长 → 必须切开
    assert _is_one_clean_cut(_ladder(SHORT, 12.0, 15.0, _span(10, 12.0, 13.5)), 12.0, 15.0)


def test_q4_a_block_longer_than_7_seconds_is_refused():
    # 词全在前 1.2 秒，切点最晚 13.5 秒；窗口 9 秒 → 右块 ≥7.5 秒，超过每屏 7 秒上限 → None
    words = _span(10, 12.0, 13.2)
    assert _ladder(SHORT, 12.0, 21.0, words) is None
    # 对照：窗口缩到 7 秒，右块 ≤6.7 秒 → 必须切开
    assert _is_one_clean_cut(_ladder(SHORT, 12.0, 19.0, words), 12.0, 19.0)


def test_q5_a_block_longer_than_16_han_is_refused():
    assert dt._han_count(C_TOO_WIDE.split("，")[0]) == 19
    assert _ladder(C_TOO_WIDE, 12.0, 15.0, _span(12, 12.0, 14.9), seams=False) is None      # 左块 19 个汉字 > 16
    # 对照：左块缩到 14 个汉字，同样匀速 → 必须切开
    assert _is_one_clean_cut(_ladder(C_FITS, 12.0, 15.0, _span(12, 12.0, 14.9), seams=False), 12.0, 15.0)


def test_q5b_english_letters_do_not_count_toward_the_16():
    # 汉字口径：英文名再长也不算。左块 8 个汉字 + 很长的英文名，物理上宽 >16，但汉字数只有 8 → 允许切
    text = "我在Trader Joe's Whole Foods买了很多东西，然后我们回家做饭了"
    flat, cuts = dt._segment_cuts(text)
    assert dt._units(flat[:cuts[0]]) > 16 and dt._han_count(flat[:cuts[0]]) == 8
    assert _is_one_clean_cut(_ladder(text, 0.0, 4.0, _span(14, 0.0, 4.0), seams=False), 0.0, 4.0)


def test_q6_rescue_level_cuts_only_when_the_speed_is_between_9_and_10():
    # 左块 15 个汉字，最晚只能在 13.58 秒切 → 每秒 9.49：前三级（上限 9）切不开，第 4 级（救援上限 10）切得开
    words = _span(8, 12.0, 12.5) + _span(3, 13.58, 14.4)
    with pytest.MonkeyPatch.context() as m:       # 对照：把第 4 级的上限压回 9，同样的屏必须切不开
        m.setattr(dt, 'READING_CPS_RESCUE', dt.READING_CPS)
        assert _ladder(C_LEFT_FAST, 12.0, 14.45, words, seams=False) is None
    r = _ladder(C_LEFT_FAST, 12.0, 14.45, words, seams=False)
    assert _is_one_clean_cut(r, 12.0, 14.45)
    assert dt.READING_CPS < dt._han_speed(r[0][0], r[0][1], r[0][2]) <= dt.READING_CPS_RESCUE


def test_level2_small_block_cut_is_used_when_level1_has_nothing():
    # 左块只有 3 字宽：第 1 级（每块≥5）过不了，第 2 级（每块≥3）才行 → 必须在逗号处切，且排在词缝（第 3 级）前面
    text = "好的啊，我们去超市买东西了"
    r = _ladder(text, 12.0, 15.0, _span(10, 12.0, 14.9))
    assert _is_one_clean_cut(r, 12.0, 15.0) and r[0][2] == "好的啊" and r[1][2] == "我们去超市买东西了"


def test_level3_word_seam_cut_is_used_when_there_is_no_punctuation():
    # 纯汉字、没有标点：自然切点一个都没有，只能靠分词工具认的词缝（第 3 级）
    text = "我们先去超市买很多东西然后回家做饭"
    assert dt._segment_cuts(text)[1] == []
    r = _ladder(text, 12.0, 16.0, _span(12, 12.0, 15.9))
    assert _is_one_clean_cut(r, 12.0, 16.0)
    assert len(r[0][2].replace(" ", "")) in dt._jieba_bounds(text)         # 切口是词缝，不在词中间


def test_when_two_cuts_are_legal_the_gentler_one_is_chosen():
    # 两个逗号都合法：第一刀较快块读速 7.83，第二刀 6.8 → 必须选第二刀（不是"第一个合法的"）
    text = "我们先去超市买东西，然后回家做饭，再看一会儿电视吧"
    times = [(12.0, 12.09), (12.1, 12.325), (12.35, 12.575), (12.6, 12.825), (12.85, 13.57), (13.65, 14.1),
             (14.15, 14.177), (14.18, 14.9), (14.98, 15.07), (15.08, 15.107), (15.11, 15.335), (15.36, 15.387)]
    flat, cuts = dt._segment_cuts(text)
    kw = dict(min_units=5, cps=dt.READING_CPS, best=True)
    assert all(dt._split_time(12.0, 15.49, flat, _words(times), p, **kw) is not None for p in cuts)   # 两刀单独都合法
    r = _ladder(text, 12.0, 15.49, times)
    assert _is_one_clean_cut(r, 12.0, 15.49) and r[1][2] == "再看一会儿电视吧"                      # 选了第二个逗号


def test_split_time_old_callers_get_exactly_the_old_answers():
    # 具体数值来自提交 058031b 的老函数（加三个参数之前）。旧调用者不传新参数，答案必须逐位相同。
    flat = "我们先去超市买很多东西，然后回家做一桌好吃的饭菜"
    p = flat.index("，") + 1
    even = _words(_even(12, 12.0, 0.25))
    assert dt._split_time(12.0, 15.0, flat, even, p) == (13.5, 0.0)
    assert dt._split_time(12.0, 15.0, flat, even, p, pause=True) is None            # 匀速说话，没有停顿
    gap = _words([(12.0 + i * 0.25, 12.0 + i * 0.25 + 0.22) for i in range(6)]
                 + [(13.85 + i * 0.25, 13.85 + i * 0.25 + 0.22) for i in range(6)])
    assert dt._split_time(12.0, 15.5, flat, gap, p, pause=True) == (13.85, 0.0)      # 逗号处停了 ≥0.30 秒


# ───────────────────────── 第 1 步：切不开的屏必须被看见（日志） ─────────────────────────

def test_unsplittable_screen_leaves_a_log_line(caplog):
    """T160（新西兰第 2 遍 1032.05 秒）切不开、保持一行：必须在日志里留一行，不能只写进没人读的账本。
    日志条数必须等于账本条数（同一份数据写两处，不许变成两份）。"""
    import logging
    segs = json.loads((EXP / '重排/新西兰/V4-第2遍/显示输入.json').read_text(encoding='utf-8'))
    rep = dt.TimelineReport()
    with caplog.at_level(logging.INFO, logger=dt._log.name):
        dt.build_display_timeline([SimpleNamespace(**s) for s in segs], report=rep)
    lines = [r.getMessage() for r in caplog.records if '切点梯子' in r.getMessage()]
    assert any('1032.05' in m for m in lines), lines
    assert all(('汉字' in m and '每秒' in m) for m in lines)
    assert len(lines) == len(rep.unsplit_wide_screens) > 0

# ───────────────────────── 输出指纹（钉住"切在哪一刀"） ─────────────────────────
# 上面的测试钉住"哪些靶子会切开"，钉不住"切在哪"。这里把六遍全部输出屏（开始秒、结束秒、文字、long_ok，
# 浮点用 repr 精确到最后一位）加上账本算成一个指纹。梯子的切点选择、延后、平手判据只要有一点变，
# 它就会变红。变红时：先查这是不是有意的改动；是的话，重算新指纹，换进 FINGERPRINT，并把新指纹和原因写进施工记录。
# 注意：这个指纹只证明"和 2026-10-13 那次一样"，不证明那次是对的。"对不对"由冻结答案清单、靶子测试和违规检查负责；
# 指纹管的是"切在哪一刀"（旧测试管"切谁"），两类测试分工不同，不是重复，别删。
INPUT_SHA256 = {   # 六遍的显示输入（输入变了要明确报"输入被改过"，不要混进切点的账里）
    '新片-第1遍': '1beb9764a92f3b8d7d39deb9f041a9ce7e5347a5a4fc47a91d34a4dc7cffbcbc',
    '新片-第2遍': '09e56bb1a080a5fdc02c9b50d8b18c5806c7923eee3412eb39b2c0cd3cf3b009',
    '新西兰-第1遍': '2dead4ed32bc3daa3fdb7ff58ef26dc2d04be0024cd32881401fee84e6d660a2',
    '新西兰-第2遍': 'e09bf50389f251eb8f1aa24d0aa572eba910a0eb4a83a0f66549cffcd12bbd47',
    '欧洲女孩-第1遍': '3cef124c63a886fd4b53ffe03a46b07bc858680929891972bcceca2ff270ca3d',
    '欧洲女孩-第2遍': '5bf74b6612f71561b9a3aad217b2486192bb13b148f2bd0f32499da60897ac62',
}
# 2026-10-23 P528 方案 B：3ac4a7a0…（4445 + 184 屏）。2026-10-24 插入④施工B（用户同意换答案）：C2「——」不从中间切，
# 最终输出只在新西兰两遍各 3 处「——」及邻屏变化，屏数不变（4444 + 185）。
# 2026-10-27 插入④施工B 时间错位（D4 不推后 / D9 话说完就收 / D16 合并门槛 20，用户看过逐屏变化后同意）：4418 + 189 屏。上一版 9ed96eba…，4629 屏
# 2026-10-27 C19（为凑 0.84 秒最多推后下一屏 0.1 秒，短句单独成屏，用户看并排视频后同意）：4607 → 4639 屏。上一版 fd6beb6e…
# 2026-10-27 晚 C19 第 6 条（只救完整一句，新西兰 42:12 两遍各少 1 屏）：上一版 1c8d6ea3…，4639 屏
# 2026-10-28 E1（C19 推后上限 0.1→0.15，用户看 ⑩ 试排视频后定）：新西兰、欧洲女孩多救回短句单独成屏。上一版 a165dc74…，4638 屏
FINGERPRINT = ('3a32881c89e19124f870cc10fc00c81b73f2d92d059a299615b64d5c276ec802', 4668)
# 上一版指纹（2026-10-13 汉字口径 + _ratio_index）：2823b7eb…，4628 屏，已作废
# 旧指纹（2026-10-12，字宽口径，A 4679 屏 / B 4699 屏）：920d05d8… / be0ce009…，已作废，留在这里做记录


def _fingerprint():
    run = _replay()
    rows = []
    for key in sorted(run):
        ents, rep = run[key]
        rows.append([key, [[repr(e.start), repr(e.end), e.text, e.long_ok] for e in ents],
                     [[repr(x) for x in r] for r in rep.unsplit_wide_screens]])
    digest = hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return digest, sum(len(r[1]) for r in rows)


def test_the_six_display_inputs_are_unchanged():
    for key, want in INPUT_SHA256.items():
        film, n = key.split('-第'); n = n.rstrip('遍')
        path = EXP / f'重排/{film}/V4-第{n}遍/显示输入.json'
        assert hashlib.sha256(path.read_bytes()).hexdigest() == want, f'{key} 的显示输入被改过——不是梯子的问题'


def test_output_fingerprint_pins_where_every_cut_lands():
    assert _fingerprint() == FINGERPRINT, \
        '六遍输出指纹变了：先确认是不是有意改动，再更新 FINGERPRINT 并写进施工记录'
