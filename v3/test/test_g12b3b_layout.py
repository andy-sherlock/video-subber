"""G12 第三批 B · 排版（施工合同 L-1～L-15）。

合同来源：pipelines/方案-排版换屏对位-2026-09-25.md §3、§9、§11–§15；执行顺序决定 35。
整片逐屏证据另见 pipelines/复查-第26与第2件/_施工_排版对账.py（与实验最终组合逐屏对账）。
"""

from types import SimpleNamespace

import pytest

from backend.core import display_timeline as dt


def _seg(start, end, en, zh, words, *, speaker_change=False):
    return SimpleNamespace(start=start, end=end, text=en, zh=zh, words=words,
                           speaker_change=speaker_change, uncertain=False)


def _even_words(tokens, start, end, gap=0.0):
    step = (end - start) / len(tokens)
    return [{"word": t, "text": t, "start": round(start + i * step, 3),
             "end": round(start + (i + 1) * step - gap, 3)} for i, t in enumerate(tokens)]


def _without_jieba(monkeypatch):
    for cache in (dt._jieba_bounds, dt._jieba_tokens, dt._pos_tags):
        cache.cache_clear()
    monkeypatch.setattr(dt, "_jieba", lambda: None)


# ── L-1 改动 1：逗号那一刀找附近的英文标点当路标 ──

def test_comma_cut_uses_nearby_english_punctuation():
    en = "The leopard gecko is just chilling, but the other one keeps running around the tank"
    words = _even_words(en.split(), 0.0, 8.0)
    zh = "这只豹纹守宫就在那里悠闲地待着，另一只却一直在缸里跑来跑去"
    entries = dt.build_display_timeline([_seg(0.0, 8.0, en, zh, words)])
    chilling = next(w for w in words if w["word"] == "chilling,")
    assert len(entries) == 2
    assert entries[0].end == pytest.approx(chilling["end"])


# ── L-2 §11：词中间的破折号也是路标 ──

def test_inner_dash_word_is_split_for_layout_only():
    words = [{"word": "s'more—chocolate", "text": "s'more—chocolate", "start": 10.0, "end": 11.4}]
    pieces = dt._split_inner_dash(words[0])
    assert [p["word"] for p in pieces] == ["s'more—", "chocolate"]
    assert pieces[0]["start"] == 10.0 and pieces[1]["end"] == 11.4
    assert pieces[0]["end"] == pytest.approx(10.0 + 1.4 * 5 / 14)
    assert words[0]["word"] == "s'more—chocolate"          # 原词不改


def test_inner_dash_becomes_cut_point():
    en = "I got a cup of milk, and then I got this little s'more—chocolate chip cookie s'more with melted chocolate"
    words = _even_words(en.split(), 0.0, 9.0)
    zh = "我点了一杯牛奶，然后点了这个小小的棉花糖夹心——巧克力豆饼干棉花糖夹心，里面有融化的巧克力"
    entries = dt.build_display_timeline([_seg(0.0, 9.0, en, zh, words)])
    smore = next(w for w in words if w["word"].startswith("s'more—"))
    boundaries = {round(e.end, 3) for e in entries}
    cut = smore["start"] + (smore["end"] - smore["start"]) * 5 / 14
    assert round(cut, 3) in boundaries


# ── L-3 改动 2：第 k 个中文句号对第 k 个英文句号 ──

def test_sentence_cut_pairs_kth_periods():
    en = "We stopped here to look at the lake for a while. That's the second part of our walk today."
    words = _even_words(en.split(), 0.0, 8.0)
    zh = "我们在这里停下来看了一会儿湖。这是我们今天走的第二段路。"
    entries = dt.build_display_timeline([_seg(0.0, 8.0, en, zh, words)])
    on_word = next(w for w in words if w["word"] == "while.")
    assert entries[0].end == pytest.approx(on_word["end"])
    assert entries[1].text.startswith("这是")


# ── L-4 §15.1 + L-5 改动 3：润色删掉的句号排版不认；原词不改 ──

def test_refiner_removed_period_is_ignored_and_words_untouched():
    en_raw = "a lot of people think. chickens are dumb but they are really smart animals"
    words = _even_words(en_raw.split(), 0.0, 6.0)
    refined = "A lot of people think chickens are dumb, but they are really smart animals."
    zh = "很多人觉得鸡很笨，但它们其实是非常聪明的动物。"
    before = [dict(w) for w in words]
    dt.build_display_timeline([_seg(0.0, 6.0, refined, zh, words)])
    assert words == before                                    # R-2：原词逐字段不变
    # 句号那一刀的英文候选里不再有 think.（改动 3 在副本上去掉了它）
    copy_words = dt._layout_segments([_seg(0.0, 6.0, refined, zh, words)], polish_marks=True)[0].words
    ends = [j + 1 for j, w in enumerate(copy_words[:-1]) if dt._EN_PERIOD_END.search(dt._w_text(w))]
    assert ends == []


def test_polish_marks_live_only_on_layout_copies():
    words = _even_words("We went. home now".split(), 0.0, 2.0)
    seg = _seg(0.0, 2.0, "We went home now.", "我们回家了。", words)
    copies = dt._layout_segments([seg], polish_marks=True)
    assert "_pe" in copies[0].words[0] and "_pe" not in words[0]
    assert copies[0].words[1]["word"] == "went"               # 改动 3：副本上去掉了句号
    assert words[1]["word"] == "went."


# ── L-6 改动 4：7 秒窄例外 ──

def test_trusted_screen_whose_comma_cuts_fragment_may_exceed_7s():
    en = "for example I think when I was little I really loved telling lies to everyone"
    words = _even_words(en.split(), 0.0, 7.68, gap=0.02)
    zh = "比如说，我觉得我小时候特别爱撒谎"
    entries = dt.build_display_timeline([_seg(0.0, 7.68, en, zh, words)])
    assert len(entries) == 1 and entries[0].end - entries[0].start > 7.0
    assert entries[0].long_ok is True
    dt.assert_timeline_valid(entries, srt=True)


def test_untrusted_long_word_is_still_capped_at_7s():
    words = [{"word": "I", "text": "I", "start": 0.0, "end": 5.0},
             {"word": "already", "text": "already", "start": 5.1, "end": 5.5},
             {"word": "got", "text": "got", "start": 5.6, "end": 5.9},
             {"word": "bitten.", "text": "bitten.", "start": 6.0, "end": 7.9}]
    entries = dt.build_display_timeline([_seg(0.0, 7.9, "I already got bitten.", "我已经被咬了几口了", words)])
    assert entries[0].end - entries[0].start == pytest.approx(7.0)
    assert entries[0].long_ok is False


def test_self_check_rejects_long_screen_without_flag():
    with pytest.raises(dt.DisplayTimelineError):
        dt.assert_timeline_valid([dt.DisplayEntry(0.0, 7.5, "一句话")])
    dt.assert_timeline_valid([dt.DisplayEntry(0.0, 7.5, "一句话", long_ok=True)])


# ── L-7 §12 一行优先：可拆可不拆的那一刀要每块达标 ──

def test_one_line_preference_splits_when_both_pieces_qualify():
    en = "someone told me in a video that the plural of momo has no s it is just momo"
    words = _even_words(en.split(), 0.0, 4.0)
    zh = "有人在视频里告诉我，“Momo”复数不加s 就是momo"
    entries = dt.build_display_timeline([_seg(0.0, 4.0, en, zh, words)])
    assert [e.text for e in entries] == ["有人在视频里告诉我", "“Momo”复数不加s 就是momo"]


def test_one_line_preference_splits_two_lines_at_original_break_v5():
    en = "okay so that is the plan for the rest of our long day in the mountains here"
    words = _even_words(en.split(), 0.0, 4.0)
    zh = "好吧，这就是我们在山里剩下这一整天的全部安排"
    entries = dt.build_display_timeline([_seg(0.0, 4.0, en, zh, words)])
    # V5 显示规则（问题 1/2）：无标点的两行屏，原换行处能拆就拆成两屏（各一行），不再留两行屏
    assert [e.text for e in entries] == ["好吧 这就是我们在山里", "剩下这一整天的全部安排"]


# ── L-8 规则 D：两个汉字之间只断在词缝上 ──

def test_rule_d_breaks_only_at_word_boundaries():
    """规则 D 只在分词工具切出的词缝上断，不切开一个词。

    已知残留（2026-09-28 施工，乔布斯、马斯克一致选甲，⑪ 登记为新问题）：方案 §12.8 的真活测试句
    「这其实算是我们在皇后镇到目前为止最美的一个早晨」，分词工具切成「皇后/镇到」、不认识这个地名，
    付费验证过的实验代码与正式代码都换成「皇后｜镇到」。属规则 D 已接受的约 28% 换行不理想；
    不为单个地名加词典补丁。"""
    text = "这其实算是我们在皇后镇到目前为止最美的一个早晨"
    wrapped = dt.wrap_lines(text)
    first = wrapped.split("\n")[0]
    assert len(wrapped.split("\n")) == 2
    assert len(first) in dt._jieba_bounds(text)            # 断点落在词缝上
    assert "目前\n为止" not in wrapped and "早\n晨" not in wrapped


# ── L-9 §12 第 3 步：推迟下一句借时间 ──

def test_short_screen_never_pushes_next_screen_later():
    # 插入④ 施工 B D4（用户 2026-10-26 定，管所有屏）：不再推迟下一屏来给短屏补时间（0f：195 屏被推后出现）
    fixed = [(0.0, 0.6, "好的。", False, []), (0.6, 3.0, "我们现在出发去山顶看日出吧。", False, [])]
    out = dt._repair_times(fixed, dt.TimelineReport())
    assert all(r[0] in (0.0, 0.6) for r in out)


# ── L-10 §14.3：分词工具失败三种情况都退回原来的换行、任务不失败、只记一次日志 ──

@pytest.mark.parametrize("failure", ["import", "dictionary", "posseg"])
def test_jieba_failure_falls_back_to_original_wrapping(monkeypatch, caplog, failure):
    for cache in (dt._jieba_bounds, dt._jieba_tokens, dt._pos_tags):
        cache.cache_clear()
    monkeypatch.setattr(dt, "_JIEBA", None)
    monkeypatch.setattr(dt, "_JIEBA_FAILED", False)
    text = "这其实算是我们在皇后镇到目前为止最美的一个早晨"
    expected = dt._base_wrap(text)
    if failure == "import":
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if name == "jieba":
                raise ImportError("no jieba")
            return real_import(name, *a, **k)
        monkeypatch.setattr(builtins, "__import__", fake_import)
    elif failure == "dictionary":
        class _Broken:
            def cut(self, text):
                raise FileNotFoundError("dict.txt")
        monkeypatch.setattr(dt, "_jieba", lambda: _Broken() if not dt._JIEBA_FAILED else None)
    else:
        import jieba.posseg
        monkeypatch.setattr(jieba.posseg, "cut", lambda text: (_ for _ in ()).throw(FileNotFoundError("dict")))
    with caplog.at_level("WARNING"):
        wrapped = dt.wrap_lines(text)
        dt.wrap_lines(text + "啊")
    if failure != "posseg":
        assert wrapped == expected
    assert caplog.text.count("分词工具不可用") == 1
    for cache in (dt._jieba_bounds, dt._jieba_tokens, dt._pos_tags):
        cache.cache_clear()


# ── L-11 §13.2 + §14.1：三行屏拆两屏；开书名号跟后文 ──

def test_three_line_screen_is_split_at_existing_break(monkeypatch):
    _without_jieba(monkeypatch)
    zh = "第一部电影真的非常好看 《第二部电影也真的非常好看》 最后还有另外一部电影"
    words = _even_words(["word"] * 40, 0.0, 6.0)
    out = dt._split_three_line_screen((0.0, 6.0, zh, False, words))
    assert len(out) == 2
    assert all(not r[2].rstrip().endswith("《") and r[2].count("《") == r[2].count("》") for r in out)


# ── L-12 §14.2：书名号里的换行最后才考虑 ──

def test_wrap_prefers_break_outside_book_title():
    text = "我读了《保持冷静迎接明天》觉得这本书非常好看"
    first = dt.wrap_lines(text).split("\n")[0]
    assert first.count("《") == first.count("》")


# ── L-14 §15.2：AI 标记落在「不许换行」名单上不当断点 ──

def test_ai_mark_after_no_break_word_is_not_used():
    text = "我们把" + dt.SPLIT_MARK + "这个东西推荐给了很多来这里旅游的朋友们"
    assert dt._tier_marked(text, 3) is None
    assert dt._tier_marked("我们去了" + dt.SPLIT_MARK + "很多地方", 4) is not None


# ── L-13 / L-15 §14.4、§15.3：书名号里的逗号不当拆屏点 ──

def test_comma_inside_book_title_is_not_a_cut_point():
    zh = "《Just Be Cool, Jenna Sakai》那些都是长篇章节书"
    cuts = [m.start() for m in dt._COMMA.finditer(zh)]
    assert cuts == []
    assert [m.start() for m in dt._COMMA.finditer("我读了，《书》")] == [3]
