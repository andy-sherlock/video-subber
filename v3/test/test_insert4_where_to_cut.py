"""插入④ 施工 B「切在哪」：P487、P495-4、P529、P532、P534 + 合并规矩统一（C11）。

方案：pipelines/插入4-在哪切-正式方案-2026-10-23.md；合同增量见 pipelines/bug-pipeline-2026-10-23-插入4-施工B.md
（C6″ 用户看片后定「有 AI 记号就先试 AI 那一刀」；C3′ 三行拆屏同序；C8/C12 删除；C9′ 原话进 debug.log；
C11 合并规矩统一用户看片 1 好 1 差 3 平，2026-10-24 撤掉）。
"""
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core import display_timeline as dt
from backend.core import sentence_splitter as ss
from backend.translation.base import TranslationResult

DATA = Path(__file__).parent / 'display_data' / '标点对齐'
M = dt.SPLIT_MARK


def _segs(film, n):
    return [SimpleNamespace(**s) for s in
            json.loads((DATA / film / f'第{n}遍' / '显示输入.json').read_text(encoding='utf-8'))]


def _screens(segs):
    return [(round(e.start, 2), round(e.end, 2), e.text) for e in dt.build_display_timeline(segs)]


def _window(screens, t0, t1):
    return [s for s in screens if s[1] > t0 and s[0] < t1]


def _even_words(n, start, end):
    step = (end - start) / n
    return [{"word": f"w{i}", "text": f"w{i}", "start": start + i * step, "end": start + i * step + step * 0.8}
            for i in range(n)]


# ── C2「——」只在后面切（P487） ─────────────────────────────────

def test_cut_inside_double_dash_is_forbidden():
    text = "还有这个小棉花糖夹心——巧克力饼干"
    mid = text.index("——") + 1
    assert dt._cut_forbidden(text, mid)
    assert not dt._cut_forbidden(text, mid + 1)          # 「——」后面可以切


def test_cut_forbidden_keeps_number_rule():
    assert dt._cut_forbidden("起飞时间是10:25。", "起飞时间是10:25。".index(":") + 1)
    assert not dt._cut_forbidden("甲,乙", 2)


def test_legal_cut_refuses_inside_double_dash():
    zh = "还有这个小棉花糖夹心——巧克力饼干我们都很喜欢"
    words = _even_words(12, 0.0, 6.0)
    mid = zh.index("——") + 1
    for idx in range(1, 12):
        assert dt._legal_cut(0.0, 6.0, zh, words, mid, idx, False, None) is None


@pytest.mark.parametrize("n", [1, 2])
def test_newzealand_never_splits_double_dash(n):
    screens = _screens(_segs('新西兰', n))
    bad = [(a, b) for a, b in zip(screens, screens[1:]) if a[2].endswith("—") and b[2].startswith("—")]
    assert bad == []


# ── C4 只数汉字（P529） ─────────────────────────────────────

def test_long_segment_counts_han_only():
    assert ss._han_count is dt._han_count                 # 和显示层同一个数法，不写第二份
    sixteen = SimpleNamespace(zh="对我来说，开始改变的时刻是在大学时。")   # 18 个字符、16 个汉字
    seventeen = SimpleNamespace(zh="对我来说，开始改变的时刻是在大学时候。")
    assert not ss._is_long_zh_segment(sixteen)
    assert ss._is_long_zh_segment(seventeen)


# ── C5 提示词 + C9′ 原话进 debug.log（P532、P534） ────────────────

class _Fake:
    name = "fake"

    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def translate_raw(self, prompt, max_tokens=8192):
        self.prompts.append(prompt)
        return TranslationResult(translated=[self.reply], backend_name="fake", tokens_used={})


def _seg(zh, text):
    return SimpleNamespace(id=0, start=0.0, end=4.0, zh=zh, text=text, words=[], speaker_change=False)


def test_prompt_gives_english_and_always_cuts(caplog):
    zh = "我觉得有时候最好的旅行决定就是多做你已经热爱的事"
    en = "I think sometimes the best travel decision is just doing more of what you already love."
    reply = "[0.1] 我觉得有时候最好的旅行决定\n[0.2] 就是多做你已经热爱的事"
    fake = _Fake(reply)
    with caplog.at_level(logging.DEBUG, logger=ss.__name__):
        out = ss._llm_batch_split([_seg(zh, en)], [fake], on_wait=lambda *a, **k: None)
    prompt = fake.prompts[0]
    assert en in prompt                                   # AI 看得到英文原话
    assert "需要几刀就给几刀" in prompt and "不算标点" in prompt
    assert "直接跳过" not in prompt                         # 不许「找不到切点就跳过」
    assert reply in caplog.text                           # 原话进日志（任务的 debug.log 收 root 全量 DEBUG）
    assert M in out[0][0].zh


# ── C6′ 记号紧贴逗号：同一刀用同一个数 ───────────────────────────

def test_segment_cuts_puts_cut_after_mark_next_to_comma():
    flat, cuts = dt._segment_cuts(f"对我来说，{M}开始改变的时刻是在大学时。")
    assert cuts == [6]
    assert dt._segment_cuts("对我来说，开始改变")[1] == [5]       # 没有记号的照旧


def test_mark_after_comma_still_splits_at_comma():
    segs = _segs('新片', 1)
    i = next(k for k, s in enumerate(segs) if s.zh == "对我来说，开始改变的时刻是在大学时。")
    segs[i].zh = f"对我来说，{M}开始改变的时刻是在大学时。"
    texts = [s[2] for s in _window(_screens(segs), segs[i].start, segs[i].end)]
    assert "对我来说" in texts and "开始改变的时刻是在大学时" in texts


# ── C6″ 有 AI 记号先试 AI 那一刀（用户看片判「AI 优先更好」的两处，锁 0a 原型的样子） ──

def _with_0a(film, n):
    segs = _segs(film, n)
    zh = json.loads((DATA / film / f'第{n}遍' / '0a拆句.json').read_text(encoding='utf-8'))['zh']
    assert len(zh) == len(segs)
    for s, z in zip(segs, zh):
        s.zh = z
    return segs


AI_FIRST_LOCKED = {
    ('新西兰', 1, 267.0, 270.9): [(267.3, 269.8, '可以说 游览新西兰最好的方式'), (269.8, 270.82, '就是自驾游')],
    ('欧洲女孩', 1, 305.5, 311.8): [(305.64, 309.22, '所以我觉得 只要住在一个'), (309.22, 311.8, '必须说英语的地方')],
}


@pytest.mark.parametrize("key", list(AI_FIRST_LOCKED))
def test_ai_cut_first_matches_what_user_watched(key):
    film, n, t0, t1 = key
    assert _window(_screens(_with_0a(film, n)), t0, t1) == AI_FIRST_LOCKED[key]


def test_ladder_falls_back_to_nearest_legal_when_ai_cut_illegal():
    # AI 记号后面只剩「小狗」2 个汉字：那一刀右边不够 3 字，不合法 → 取离它最近的合法刀。
    # 词缝那一级要求每块 ≥5 个汉字，最近的合法刀在「看到了｜一只可爱小狗」；改前是读速平均的「小路上｜看到了…」
    text = f"我们骑车在小路上看到了一只可爱{M}小狗"
    words = _even_words(16, 0.0, 6.0)
    out = dt._cut_ladder(0.0, 6.0, text, words, False, False)
    assert out is not None and len(out) == 2
    assert out[0][2] == "我们骑车在小路上看到了"


# ── C13 需要三屏以上：继续用剩下的 AI 记号切 ──────────────────────

def test_three_screens_all_on_ai_marks():
    a, b, c = "今天早上我们一大早就出发去了湖边", "沿着湖边慢慢走了很久看了很多风景", "最后在小镇上找了一家店吃了午饭"
    text = a + M + b + M + c
    words = _even_words(40, 0.0, 14.0)
    out = dt._cut_ladder(0.0, 14.0, text, words, False, False)
    assert out is not None
    assert [t for _s, _e, t in out] == [a, b, c]


# ── C3′ 三行拆屏：AI 记号排在标点前面 ────────────────────────────

def test_three_line_split_prefers_ai_mark(monkeypatch):
    text = f"我们今天去了海边，看了日出然后{M}一起回家吃了早饭然后又出门"
    p_punct, p_ai = text.index("，") + 1, text.index(M) + 1
    monkeypatch.setattr(dt, "_wrap_break_positions",
                        lambda t: (["一", "二", "三"], [(p_punct, "标点"), (p_ai, "AI标记")]))
    monkeypatch.setattr(dt, "_RULES", dt._Rules(pref=False))
    words = _even_words(20, 0.0, 10.0)
    out = dt._split_three_line_screen((0.0, 10.0, text, False, words))
    assert len(out) == 2 and out[0][2] == text[:p_ai].strip()


# ── C10 预拆分通道（当前生产不可达）同样受 C6″ 约束 ───────────────────

def test_precomputed_splits_marks_are_used_by_layout():
    seg = SimpleNamespace(id=7, start=0.0, end=6.0, text="x", speaker_change=False, uncertain=False,
                          zh="我觉得有时候最好的旅行决定就是多做你已经热爱的事",
                          words=_even_words(12, 0.0, 6.0))
    out = ss.llm_split_segments([seg], None, pre_splits={7: ["我觉得有时候最好的旅行决定", "就是多做你已经热爱的事"]},
                                on_wait=lambda *a, **k: None)
    assert M in out[0].zh
    texts = [e.text for e in dt.build_display_timeline(out)]
    assert "我觉得有时候最好的旅行决定" in texts


# ── C7 没有 AI 记号时：除 C2（「——」）的预期变化外，逐屏和改前一样（C11 用户 2026-10-24 看片后撤掉） ──

# 2026-10-24 实测并逐屏核对：只有 C2（新西兰 6 处「——」及邻屏）
EXPECTED_CHANGES = {k: ([tuple(x) for x in v['gone']], [tuple(x) for x in v['added']]) for k, v in json.loads(
    (DATA / '插入4B-预期变化.json').read_text(encoding='utf-8'))['files'].items()}


@pytest.mark.parametrize("film,n", [(f, n) for f in ('新片', '新西兰', '欧洲女孩') for n in (1, 2)])
def test_no_ai_input_only_expected_changes(film, n):
    before = json.loads((DATA / '改前答案-86e264b.json').read_text(encoding='utf-8'))['files'][f'{film}{n}']['screens']
    b = {(round(s['start'], 2), round(s['end'], 2), s['text']) for s in before}
    a = set(_screens(_segs(film, n)))
    gone, added = sorted(b - a), sorted(a - b)
    assert (gone, added) == EXPECTED_CHANGES.get(f'{film}{n}', ([], []))


# ── ⑦.5 Codex 发现：原换行在记号前面时，「原换行位置」与切点候选也要是同一个数 ─────────

def test_original_break_matches_cut_when_mark_follows_newline():
    text = f"对我来说，\n{M}开始改变的时刻是在大学时。"
    flat, cuts = dt._segment_cuts(text)
    assert dt._original_break(text, flat) in cuts


# ── ⑦.5 #4（Codex + 独立审查员，用户 2026-10-24 定只修这一条）：AI 记号紧跟英文词 / 数字 / 「——」时，
#    梯子也要看得见这一刀（以前切点候选只认「汉字后面的空白」） ─────────────────────────

@pytest.mark.parametrize("left,right", [
    ("我们今天一起看Netflix", "后来大家都觉得这部电影非常精彩"),
    ("他说这个夹心——", "巧克力饼干我们都很喜欢吃的那种"),
    ("那一年是2019", "我第一次去日本旅行看了很多樱花"),
])
def test_ai_mark_after_latin_digit_or_dash_is_seen_by_ladder(left, right):
    text = left + M + right
    words = _even_words(16, 0.0, 6.0)
    out = dt._cut_ladder(0.0, 6.0, text, words, False, False)
    assert out is not None and out[0][2] == left


# ── 合同增量 C9″（⑨ 实测发现）：第三方 nagisa 导入时 basicConfig(level=INFO) 把根日志盖成 INFO，
#    debug.log 从 9 月起收不到任何 DEBUG。必须在全新进程里走真实 `import backend.worker` 才看得见 ──

def test_job_debug_log_receives_debug_after_real_worker_import(tmp_path):
    import subprocess
    import sys
    code = (
        "import logging, sys, pathlib\n"
        "import backend.worker as w\n"
        f"w.LOG_DIR = pathlib.Path({str(tmp_path)!r})\n"
        "u, d = w._setup_file_log('c9')\n"
        "logging.getLogger('backend.core.sentence_splitter').debug('AI 拆句原话（1 条）：探针')\n"
        "print(d)\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parent.parent,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    debug_log = Path(r.stdout.strip().splitlines()[-1]).read_text(encoding="utf-8")
    assert "[DEBUG] backend.core.sentence_splitter: AI 拆句原话（1 条）：探针" in debug_log
    assert "探针" not in r.stderr                         # 控制台（第三方挂的那个输出）仍只打 INFO
