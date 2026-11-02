"""G12 第三批 B 收尾补充（P475 / P472 / P473 / P478）。

唯一施工依据：`pipelines/G12-第三批B-收尾补充-锁定方案-2026-09-17.md`。
每条测试的名字前缀对着管道文件 `bug-pipeline-2026-09-17-G12-batch3B-收尾补充.md`
第 ⑤.5 步施工映射的行（T1-1 … T5-1）。
"""
import asyncio
import copy
import json
import logging
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config_manager as cm
from backend.core import display_timeline as dt
from backend.core import sentence_splitter as ss
from backend.transcriber_v2 import Segment
from backend.translation.base import TranslationResult


def _seg(i, start, end, text, zh, words=None, speaker_change=False):
    s = Segment(id=i, start=start, end=end, text=text)
    s.zh = zh
    s.speaker_change = speaker_change
    if words is None:
        toks = text.split() or ["w"]
        step = (end - start) / len(toks)
        words = [{"word": t, "start": start + k * step, "end": start + (k + 1) * step}
                 for k, t in enumerate(toks)]
    s.words = words
    return s


class _ReplyBackend:
    """只回放一段固定回复的替身。不联网、不调任何模型。"""

    name = "reply-stub"

    def __init__(self, reply):
        self.reply = reply

    def translate_raw(self, prompt, max_tokens=4096):
        return TranslationResult(translated=[self.reply], backend_name=self.name)


def _split(zh, reply, caplog=None):
    seg = _seg(0, 0.0, 6.0, "so the reason I started doing gymnastics was my mom", zh)
    if caplog is not None:
        caplog.set_level(logging.DEBUG, logger="backend.core.sentence_splitter")
    out = ss._llm_batch_split([seg], _ReplyBackend(reply), on_wait=lambda *a: None)
    assert len(out) == 1, "每条输入句子对应一组输出"
    return seg, out[0]


# ══════════════════════════════════════════════════════════════════
# T1（P475）：AI 断句的断点用内部记号，观众看不到
# ══════════════════════════════════════════════════════════════════

def test_t1_1_marker_is_unit_separator_defined_in_display_layer():
    """记号只在显示层定义一次，断句模块引用同一个常量。"""
    assert dt.SPLIT_MARK == "\x1f"
    assert ss._SPLIT_MARK is dt.SPLIT_MARK


def test_t1_2_ai_break_marker_keeps_every_original_char():
    """模型只按原文断开（标点没动）→ 断开处插记号，原文每个字符都在。"""
    zh = "其实，我之所以开始练体操是因为我妈妈觉得我太好动了"
    seg, parts = _split(zh, "[0.1] 其实，我之所以开始练体操\n[0.2] 是因为我妈妈觉得我太好动了")

    assert len(parts) == 1
    assert parts[0].zh == "其实，我之所以开始练体操" + dt.SPLIT_MARK + "是因为我妈妈觉得我太好动了"
    assert parts[0].zh.replace(dt.SPLIT_MARK, "") == zh
    assert seg.zh == zh, "原片段对象不许被改"


def test_t1_2_ai_break_between_english_words_keeps_space():
    """断点落在两个英文单词之间：原文的空格必须保留，渲染后不许粘成 TameImpala。"""
    zh = "我那时候最喜欢的乐队是Tame Impala和一些别的乐队"
    seg, parts = _split(zh, "[0.1] 我那时候最喜欢的乐队是Tame\n[0.2] Impala和一些别的乐队")

    out = parts[0].zh
    assert dt.SPLIT_MARK in out, f"断点没有被记下来：{out!r}"
    assert out.replace(dt.SPLIT_MARK, "") == zh
    assert dt._render_line(out) == zh, "渲染后英文单词粘在了一起"


def test_t1_2_no_marker_when_parts_do_not_align_with_translation():
    """分片和译文对不上（只可能出现在不执行的预拆分路径）→ 原译文原样，不插记号。"""
    seg = _seg(30, 1.0, 2.0, "B", "丙丁")
    out = ss._build_split_segments(seg, ["甲", "乙"])

    assert len(out) == 1
    assert out[0].zh == "丙丁"


def test_t1_3_render_line_removes_marker():
    """生成最终文字时删掉记号：紧跟逗号时逗号照奈飞规则变一个空格，否则不留痕迹。"""
    m = dt.SPLIT_MARK
    assert dt._render_line("我之所以" + m + "开始练体操") == "我之所以开始练体操"
    assert dt._render_line("其实，" + m + "我之所以") == "其实 我之所以"
    assert dt._render_line(m + "开头" + m) == "开头"


def test_t1_3_marker_break_tier_matches_space():
    """记号的断行等级和原来的空格完全一样：逗号后 2、中文旁 3、英文词间 5。"""
    m = dt.SPLIT_MARK
    for with_space, with_mark in (
        ("不知道多久了， 但租了辆车", "不知道多久了，" + m + "但租了辆车"),
        ("我们到了之后 然后进去了", "我们到了之后" + m + "然后进去了"),
        ("乐队是Tame Impala", "乐队是Tame" + m + "Impala"),
    ):
        k = with_space.index(" ")
        assert dt._break_tier(with_mark, k) == dt._break_tier(with_space, k), with_mark
        assert dt._break_tier(with_mark, k + 1) == dt._break_tier(with_space, k + 1), with_mark


def test_t1_3_ratio_index_ignores_marker_in_whitespace_round():
    """比例找词的去空白一轮不数记号（与原来不数拼接空格一致）。"""
    m = dt.SPLIT_MARK
    marked = "我之所以" + m + "开始练体操"
    plain = "我之所以开始练体操"
    for pos_plain in range(1, len(plain)):
        pos_marked = pos_plain + (1 if pos_plain >= 4 else 0)
        assert dt._ratio_index(marked, pos_marked, 8, ignore_whitespace=True) == \
            dt._ratio_index(plain, pos_plain, 8, ignore_whitespace=True)


def test_t1_3_ai_break_not_shown_when_line_not_broken():
    """真实例型（30 分钟 16:40）：原译文“其实，我之所以开始练体操”，AI 在“我之所以”后断开。
    这一屏一行放得下，不该在“我之所以”后面多出一个空格。"""
    zh = "其实，我之所以" + dt.SPLIT_MARK + "开始练体操"
    entries = dt.build_display_timeline([_seg(0, 0.0, 3.0, "so the reason I started gymnastics", zh)])

    assert [e.text for e in entries] == ["其实 我之所以开始练体操"]


def test_t1_3_ai_break_still_used_as_line_break():
    """放不进一行、两个汉字之间没有标点时，记号仍是合法断行处，换行代替记号。"""
    zh = "我那时候每天放学以后都会去体育馆" + dt.SPLIT_MARK + "练习到很晚才回家吃饭"
    assert dt.wrap_lines(zh) == "我那时候每天放学以后都会去体育馆\n练习到很晚才回家吃饭"


def test_t1_4_written_files_have_no_marker(tmp_path):
    """三个写出器写出的文件里都没有记号。"""
    from backend.core.subtitler import build_ass, build_srt
    from backend.subtitle_style import build_ass_styled

    zh = "其实，我之所以" + dt.SPLIT_MARK + "开始练体操"
    entries = dt.build_display_timeline([_seg(0, 0.0, 3.0, "so the reason I started gymnastics", zh)])
    srt, ass, styled = tmp_path / "a.srt", tmp_path / "a.ass", tmp_path / "s.ass"
    build_srt(entries, str(srt))
    build_ass(entries, str(ass))
    build_ass_styled(entries, str(styled))

    for path in (srt, ass, styled):
        text = path.read_text(encoding="utf-8")
        assert dt.SPLIT_MARK not in text, path.name
        assert "其实 我之所以开始练体操" in text, path.name


# ══════════════════════════════════════════════════════════════════
# T2（P472）：设置只存改过的项；读取时逐项补默认值；旧整份快照里的 42 当默认值
# ══════════════════════════════════════════════════════════════════

_ROOT = Path(__file__).resolve().parent.parent
_KEY = {"platform": "deepseek", "name": "", "key": "sk-test-not-real", "model": "deepseek-chat"}


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    """每条测试一个临时配置文件；真实的 ~/.video-subber/config.json 不碰。"""
    path = tmp_path / "config.json"
    monkeypatch.setattr(cm, "CONFIG_PATH", path)
    return path


def _frontend_payload(**changes):
    """照前端 `saveConfig` 发出的 11 个键组一份整份设置（`index.html:1543`），值取默认值。"""
    style = {"font": cm.DEFAULT_CONFIG["style"]["font"], "size": 72, "color": "white",
             "outlineColor": "black", "outlineWidth": 2, "bgColor": "transparent"}
    payload = {"translationMode": "speed", "translationPriority": ["google", "bing", "youdao"],
               "model": "large-v2", "contextAware": False, "apiKeys": [], "style": style,
               "outputMode": "burn", "subtitles": "no", "savePath": "~/Downloads/Video-Subber",
               "speakerDetect": False, "transcriptionTier": "standard"}
    style_changes = changes.pop("style", {})
    payload.update(changes)
    payload["style"] = {**style, **style_changes}
    return payload


def _legacy_snapshot(size):
    """旧版本整份写盘的形状（与本机真实配置同形：11 个键、无版本号、带一条 Key）。"""
    return _frontend_payload(apiKeys=[_KEY], style={"size": size})


def test_t2_1_save_keeps_only_changed_items_with_version(cfg_path):
    cm.save_config(_frontend_payload(translationMode="quality", apiKeys=[_KEY], style={"size": 56}))

    assert json.loads(cfg_path.read_text(encoding="utf-8")) == {
        "translationMode": "quality", "apiKeys": [_KEY], "style": {"size": 56},
        "configVersion": cm.CONFIG_VERSION,
    }


def test_t2_1_save_style_compared_per_key(cfg_path):
    cm.save_config(_frontend_payload(style={"color": "yellow"}))

    saved = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert saved["style"] == {"color": "yellow"}, "样式里没改的项不许写盘"


def test_t2_1_save_overwrites_items_reset_to_default(cfg_path):
    cm.save_config(_frontend_payload(translationMode="quality", style={"size": 42}))
    cm.save_config(_frontend_payload())

    assert json.loads(cfg_path.read_text(encoding="utf-8")) == {"configVersion": cm.CONFIG_VERSION}


def test_t2_1_save_ignores_version_sent_in_payload(cfg_path):
    """⑥ 实现约束 1：版本号由代码写，请求体里带来的不算数。"""
    cm.save_config({**_frontend_payload(), "configVersion": 1})

    assert json.loads(cfg_path.read_text(encoding="utf-8"))["configVersion"] == cm.CONFIG_VERSION


@pytest.mark.parametrize("content", [
    None, b"not json", b"\xff\xfe\x00broken", b"[1, 2]", b"null", b'"text"',
], ids=["no-file", "not-json", "bad-encoding", "list", "null", "string"])
def test_t2_2_load_missing_or_broken_file_returns_fresh_defaults(cfg_path, content):
    """合同增量 14：读取对任何内容都不抛错——抛错会让设置接口报 500，下次保存把 Key 覆盖掉。"""
    if content is not None:
        cfg_path.write_bytes(content)

    assert cm.load_config() == cm.DEFAULT_CONFIG


def test_t2_2_load_style_not_dict_keeps_api_keys(cfg_path):
    cfg_path.write_text(json.dumps({"configVersion": cm.CONFIG_VERSION, "style": "oops",
                                    "apiKeys": [_KEY]}), encoding="utf-8")

    cfg = cm.load_config()
    assert cfg["style"] == cm.DEFAULT_CONFIG["style"]
    assert cfg["apiKeys"] == [_KEY]


def test_t2_2_load_partial_style_filled_per_key(cfg_path):
    cfg_path.write_text(json.dumps({"configVersion": cm.CONFIG_VERSION, "style": {"size": 56}}),
                        encoding="utf-8")

    assert cm.load_config()["style"] == {**cm.DEFAULT_CONFIG["style"], "size": 56}


def test_t2_2_load_result_is_deep_copy(cfg_path):
    """合同增量 9：改读出来的设置，不许改到默认值。"""
    before = copy.deepcopy(cm.DEFAULT_CONFIG)
    for _ in range(2):   # 第一次没有文件，第二次有一个只含版本号的文件
        cfg = cm.load_config()
        cfg["style"]["size"] = 1
        cfg["apiKeys"].append(_KEY)
        cfg["translationPriority"].append("x")
        assert cm.DEFAULT_CONFIG == before
        cfg_path.write_text(json.dumps({"configVersion": cm.CONFIG_VERSION}), encoding="utf-8")


def test_t2_2_get_config_has_no_version(cfg_path):
    """合同增量 15：设置接口不返回版本号——否则它会随前端整份设置进每个任务的配置。"""
    from backend import main

    cfg_path.write_text(json.dumps({"configVersion": cm.CONFIG_VERSION, "translationMode": "quality"}),
                        encoding="utf-8")
    body = main.get_config()
    assert "configVersion" not in body
    assert body["translationMode"] == "quality"
    assert body["style"] == cm.DEFAULT_CONFIG["style"]


def test_t2_3_legacy_snapshot_42_reads_as_72_without_writing(cfg_path):
    """旧版本整份写盘的 42 当默认值（72）；只在读取时换，不写盘，Key 原样。"""
    raw = json.dumps(_legacy_snapshot(42), ensure_ascii=False, indent=2).encode("utf-8")
    cfg_path.write_bytes(raw)

    cfg = cm.load_config()
    assert cfg["style"]["size"] == 72
    assert cfg["style"] == cm.DEFAULT_CONFIG["style"]
    assert cfg["apiKeys"] == [_KEY]
    assert cfg_path.read_bytes() == raw, "读取不许改写用户的配置文件"


def test_t2_3_versioned_42_is_kept(cfg_path):
    """迁移过（有版本号）之后手动选的 42 必须保留。"""
    cfg_path.write_text(json.dumps({"configVersion": cm.CONFIG_VERSION, "style": {"size": 42}}),
                        encoding="utf-8")

    assert cm.load_config()["style"]["size"] == 42


def test_t2_3_legacy_snapshot_other_size_kept(cfg_path):
    cfg_path.write_text(json.dumps(_legacy_snapshot(56)), encoding="utf-8")

    assert cm.load_config()["style"]["size"] == 56


def test_t2_3_config_api_roundtrip_legacy_42(cfg_path):
    """老用户：打开设置看到 72 → 改选 42 并保存 → 再打开仍是 42 → 改回 72 → 文件里不再存字号。"""
    from backend import main

    cfg_path.write_text(json.dumps(_legacy_snapshot(42)), encoding="utf-8")
    shown = main.get_config()
    assert shown["style"]["size"] == 72

    payload = {k: shown[k] for k in _frontend_payload()}
    payload["style"] = {**shown["style"], "size": 42}
    asyncio.run(main.save_config(copy.deepcopy(payload)))
    saved = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert saved["configVersion"] == cm.CONFIG_VERSION
    assert saved["style"] == {"size": 42}
    assert saved["apiKeys"] == [_KEY]
    assert main.get_config()["style"]["size"] == 42

    payload["style"]["size"] = 72
    asyncio.run(main.save_config(copy.deepcopy(payload)))
    assert "style" not in json.loads(cfg_path.read_text(encoding="utf-8"))
    assert main.get_config()["style"]["size"] == 72


def _frontend_defaults():
    """在 node 里真的执行 `index.html` 的默认设置字面量，拿到前端默认值。"""
    html = (_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    model = re.search(r"const DEFAULT_MODEL='([^']+)';", html).group(1)
    block = re.search(r"\n  settings:(\{.*?\n  \})\n\};", html, re.S).group(1)
    out = subprocess.run(
        ["node", "-e", f"const DEFAULT_MODEL={json.dumps(model)};"
                       f"process.stdout.write(JSON.stringify({block}))"],
        text=True, capture_output=True, check=True)
    saved_keys = re.findall(r"(\w+):State\.settings\.\w+",
                            re.search(r"function saveConfig\(\)\{\n  const cfg=\{(.*?)\};", html).group(1))
    return json.loads(out.stdout), saved_keys


def test_t2_4_frontend_saved_keys_have_same_backend_defaults():
    """合同增量 8：前端保存的每个键，后端都有同样的默认值。

    少一个，这一项就又会被整份存盘、以后改默认值时老用户收不到（P472 复发）。
    Windows 的默认字体前后端本来就不同（锁定方案变更记录 T2-6，已知残留），只在 Windows 上豁免。"""
    front, saved_keys = _frontend_defaults()

    assert len(saved_keys) == 11 and set(saved_keys) <= set(front)
    for key in saved_keys:
        assert key in cm.DEFAULT_CONFIG, f"后端默认值缺少 {key}"
        if key == "style":
            for skey, value in front["style"].items():
                if skey == "font" and sys.platform == "win32":
                    continue
                assert cm.DEFAULT_CONFIG["style"].get(skey) == value, f"style.{skey} 前后端默认值不同"
        else:
            assert cm.DEFAULT_CONFIG[key] == front[key], f"{key} 前后端默认值不同"


# ══════════════════════════════════════════════════════════════════
# T5（P478）：“白色描边”“无描边”在成片里真的生效
# ══════════════════════════════════════════════════════════════════

_ASS_STYLE_FIELDS = ("Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
                     "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
                     "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, "
                     "Encoding").split(", ")


def _styled_ass_style(tmp_path, **style):
    """用生产写出器写一份带样式的字幕文件，读回它的样式行。"""
    from backend.subtitle_style import build_ass_styled

    entries = dt.build_display_timeline([_seg(0, 0.0, 2.0, "hello there.", "你好。")])
    path = tmp_path / "styled.ass"
    build_ass_styled(entries, str(path), style=style)
    line = next(l for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("Style: "))
    return dict(zip(_ASS_STYLE_FIELDS, line[len("Style: "):].split(",")))


def test_t5_1_outline_white_writes_white(tmp_path):
    style = _styled_ass_style(tmp_path, outlineColor="white", outlineWidth=3)
    assert style["OutlineColour"] == "&H00FFFFFF"
    assert style["Outline"] == "3"


def test_t5_1_outline_none_writes_zero_width(tmp_path):
    style = _styled_ass_style(tmp_path, outlineColor="none", outlineWidth=3)
    assert style["Outline"] == "0", "选了无描边，成片里不许再画描边"
    assert style["Shadow"] == "2", "阴影照旧"


def test_t5_1_outline_black_and_unknown_stay_black(tmp_path):
    for style_in in ({"outlineColor": "black", "outlineWidth": 2},
                     {"outlineColor": "purple", "outlineWidth": 2},
                     {"outlineWidth": 2}):
        style = _styled_ass_style(tmp_path, **style_in)
        assert style["OutlineColour"] == "&H00000000", style_in
        assert style["Outline"] == "2", style_in


def test_t5_1_text_and_back_colour_unchanged_after_table_trim(tmp_path):
    """删掉颜色表里没人用的描边列之后，字色和阴影底色一个都不许变。"""
    for color, primary in (("white", "&H00FFFFFF"), ("yellow", "&H0000FFFF"),
                           ("green", "&H0000FF00"), ("cyan", "&H00FFFF00"), ("unknown", "&H00FFFFFF")):
        style = _styled_ass_style(tmp_path, color=color)
        assert style["PrimaryColour"] == primary, color
        assert style["BackColour"] == "&H80000000", color
    assert _styled_ass_style(tmp_path, bgColor="yellow")["BackColour"] == "&H80000000"


# ══════════════════════════════════════════════════════════════════
# T3（P473）：设置里的样式预览按成片比例画；点一下放大
# ══════════════════════════════════════════════════════════════════

_HTML = (_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


def _js_function(name):
    match = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\([^)]*\)\{{.*?\n\}}", _HTML, re.S)
    assert match, f"index.html 里找不到函数 {name}"
    return match.group(0)


def _js_const(name):
    match = re.search(rf"\nconst {re.escape(name)}=.*?;\n", _HTML, re.S)
    assert match, f"index.html 里找不到常量 {name}"
    return match.group(0)


def _run_node(js):
    out = subprocess.run(["node", "-e", js], text=True, capture_output=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


_DOM_STUB = r"""
function element(id){
  const classes=new Set(),listeners={},props={};
  return {id,value:'',dataset:{},children:[],tagName:'DIV',classes,props,
    style:{display:'',setProperty(k,v){props[k]=String(v)},getPropertyValue(k){return props[k]??''}},
    classList:{add:c=>classes.add(c),remove:c=>classes.delete(c),contains:c=>classes.has(c),
               toggle:(c,on)=>{((on===undefined)?!classes.has(c):on)?classes.add(c):classes.delete(c)}},
    addEventListener(t,fn){(listeners[t]=listeners[t]||[]).push(fn)},
    fire(t,ev){(listeners[t]||[]).forEach(fn=>fn(ev))},
    closest(){return null},removeAttribute(){},
    cloneNode(){const c=element(null);c.clonedFrom=id;return c},
    replaceChildren(...kids){this.children=kids}};
}
const els={};
for(const id of ['styleFont','styleSize','styleColor','styleOutline','styleOutlineWidth','stylePreview',
                 'styleConfirmed','previewZoomOverlay','previewZoomMount','infoModalOverlay','subToggle'])els[id]=element(id);
const $=id=>els[id];
const document=element('document');
const State={settings:{subtitles:'no'}};
function refreshStartBtn(){}
function setInfoModal(){throw new Error('放大预览不许碰失败提示弹窗')}
function closeInfoModal(){throw new Error('放大预览不许碰失败提示弹窗')}
function setStyle(font,size,color,outline,width){
  els.styleFont.value=font;els.styleSize.value=String(size);els.styleColor.value=color;
  els.styleOutline.value=outline;els.styleOutlineWidth.value=String(width);
}
function snap(){const p=els.stylePreview;return {px:parseFloat(p.style.getPropertyValue('--sub-px')),
  ow:p.style.getPropertyValue('--sub-outline-w'),oc:p.style.getPropertyValue('--sub-outline-c'),
  line:parseFloat(p.style.getPropertyValue('--sub-line')),
  color:p.style.color,font:p.style.fontFamily,top:p.classes.has('top')}}
"""


def _preview_env(*extra):
    return "\n".join([_DOM_STUB, _js_const("SUB_FRAME"), _js_const("SUB_FONT_FACTOR"),
                      _js_function("_readStyleFromDOM"), _js_function("updatePreview"), *extra])


def test_t3_1_preview_factor_table_covers_every_font_option():
    """字幕引擎的字号口径随字体不同（实测：苹方 0.708，黑体 / 宋体 / 楷体约 0.99）。
    字体下拉框里的每一项都要有系数，否则预览大小是错的。"""
    select = re.search(r'<select id="styleFont">(.*?)</select>', _HTML).group(1)
    options = re.findall(r"<option>([^<]+)</option>", select)
    table = _run_node(_js_const("SUB_FONT_FACTOR") + "console.log(JSON.stringify(SUB_FONT_FACTOR))")

    assert set(table) == set(options)
    assert table["PingFang SC"] == 0.708
    assert table["Noto Sans CJK SC"] == table["PingFang SC"], "本机没装思源黑体，字幕引擎退回苹方"
    for font in ("Heiti SC", "Songti SC", "Kaiti SC"):
        assert 0.98 <= table[font] <= 1.0, font


def test_t3_1_preview_constants_match_ass_writer(tmp_path):
    """预览的画面基准、左右 / 底边距、阴影，与样式写出器写进字幕文件的一致。"""
    frame = _run_node(_js_const("SUB_FRAME") + "console.log(JSON.stringify(SUB_FRAME))")
    style = _styled_ass_style(tmp_path)
    header = (tmp_path / "styled.ass").read_text(encoding="utf-8")

    assert f"PlayResX: {frame['width']}\n" in header
    assert (style["MarginL"], style["MarginR"], style["MarginV"]) == \
        (str(frame["marginX"]), str(frame["marginX"]), str(frame["marginV"]))
    assert style["Shadow"] == str(frame["shadow"])


def test_t3_1_update_preview_sets_scaled_size():
    data = _run_node(_preview_env("""
setStyle('PingFang SC',72,'white','black',2);updatePreview();const pingfang=snap();
setStyle('Songti SC',72,'yellow','black',2);updatePreview();const songti=snap();
setStyle('Noto Sans CJK SC',42,'white','black',2);updatePreview();const noto=snap();
console.log(JSON.stringify({pingfang,songti,noto}));
"""))
    assert data["pingfang"]["px"] == pytest.approx(72 * 0.708)
    assert data["pingfang"]["font"] == "PingFang SC"
    # 字幕引擎的行距等于字号本身（ASS 字号口径），换算成网页行高就是 1 ÷ 系数
    assert data["pingfang"]["line"] == pytest.approx(1 / 0.708)
    assert data["songti"]["line"] == pytest.approx(1 / 0.990, abs=0.01)
    assert data["songti"]["px"] == pytest.approx(72 * 0.990, abs=0.8)
    assert data["songti"]["color"] == "#ff0"
    assert data["noto"]["px"] == pytest.approx(42 * 0.708)


def test_t3_2_subtitles_toggle_moves_preview_to_top():
    """原视频有字幕时成片把中文放顶部（`worker.py:3389`）；切换开关，预览立刻跟着换位置。"""
    toggle = re.search(r"\n(\$\('subToggle'\)\.addEventListener\('click',.*?)\n", _HTML).group(1)
    data = _run_node(_preview_env(toggle, """
setStyle('PingFang SC',72,'white','black',2);updatePreview();const before=snap().top;
const button={tagName:'BUTTON',dataset:{v:'yes'},classList:{add(){},remove(){}},parentElement:{querySelectorAll:()=>[]}};
els.subToggle.fire('click',{target:button});
const afterYes=snap().top;
button.dataset.v='no';els.subToggle.fire('click',{target:button});
console.log(JSON.stringify({before,afterYes,afterNo:snap().top}));
"""))
    assert data == {"before": False, "afterYes": True, "afterNo": False}


def test_t3_3_preview_outline_follows_ass_rules(tmp_path):
    """预览的描边和成片同一规则（P478）：白色描边画白边，无描边宽度 0，其余黑边。

    这条规则前端预览和后端写出器各写了一份（⑦.5 审查发现）。期望值直接取后端
    `build_ass_styled` 写出的样式行，后端改了规则而预览没跟上，这条就会红。"""
    data = _run_node(_preview_env("""
const out={};
for(const [name,outline,width] of [['black','black',2],['white','white',3],['none','none',3]]){
  setStyle('PingFang SC',72,'white',outline,width);updatePreview();out[name]=snap();
}
console.log(JSON.stringify(out));
"""))
    colour_to_css = {"&H00FFFFFF": "#fff", "&H00000000": "#000"}
    for name, outline, width in (("black", "black", 2), ("white", "white", 3), ("none", "none", 3)):
        ass = _styled_ass_style(tmp_path, outlineColor=outline, outlineWidth=width)
        assert data[name]["ow"] == ass["Outline"], name
        if ass["Outline"] != "0":
            assert data[name]["oc"] == colour_to_css[ass["OutlineColour"]], name
    assert (data["white"]["oc"], data["none"]["ow"]) == ("#fff", "0")


def test_t3_4_preview_zoom_open_close_does_not_touch_info_modal():
    """点小画面放大；点遮罩、点关闭按钮、按 Esc 都能关；全程不碰失败提示用的那个弹窗。"""
    data = _run_node(_preview_env(_js_function("openPreviewZoom"), _js_function("closePreviewZoom"),
                                  _js_function("bindStylePreview"), """
bindStylePreview();
const overlay=els.previewZoomOverlay,mount=els.previewZoomMount;
const state=()=>({open:overlay.classes.has('active'),kids:mount.children.length,
                  cloned:mount.children.length?mount.children[0].clonedFrom:null});
document.fire('keydown',{key:'Escape'});const escWhileClosed=state();
els.stylePreview.fire('click',{target:els.stylePreview});const opened=state();
const inner=element('inner');overlay.fire('click',{target:inner,currentTarget:overlay});const clickInside=state();
overlay.fire('click',{target:overlay,currentTarget:overlay});const backdrop=state();
els.stylePreview.fire('click',{target:els.stylePreview});
const btn=element('btn');btn.closest=sel=>sel==='.preview-zoom-close'?btn:null;
overlay.fire('click',{target:btn,currentTarget:overlay});const button=state();
els.stylePreview.fire('click',{target:els.stylePreview});
document.fire('keydown',{key:'Escape'});const esc=state();
console.log(JSON.stringify({escWhileClosed,opened,clickInside,backdrop,button,esc,
  infoModal:[...els.infoModalOverlay.classes]}));
"""))
    closed = {"open": False, "kids": 0, "cloned": None}
    assert data["escWhileClosed"] == closed
    assert data["opened"] == {"open": True, "kids": 1, "cloned": "stylePreview"}
    assert data["clickInside"]["open"] is True, "点放大画面里面不许关掉"
    assert data["backdrop"] == closed
    assert data["button"] == closed
    assert data["esc"] == closed
    assert data["infoModal"] == []


def test_t3_5_startup_restores_jobs_even_if_preview_throws():
    """合同增量 13：页面启动时预览出错，正在跑的任务照样恢复，并在控制台报错。"""
    body = re.search(r"document\.addEventListener\('DOMContentLoaded',async\(\)=>\{\n(.*?)\n\}\);",
                     _HTML, re.S).group(1)
    data = _run_node(r"""
const calls=[],errors=[];console.error=(...a)=>errors.push(a.map(String).join(' '));
const $=()=>null;
function mountSettingsDrawer(){} function syncOutputModeUI(){} function loadConfig(){}
function updateBackendLists(){} function buildModelSlider(){}
function updatePreview(){throw new Error('preview boom')}
async function fetchModelStatus(){}
async function restoreActiveJobsAtStartup(){calls.push('restore')}
fetch=()=>Promise.resolve({ok:false,json:async()=>({})});
(async()=>{
""" + body + r"""
})().then(()=>console.log(JSON.stringify({calls,errors})),
          e=>console.log(JSON.stringify({calls,errors,crashed:String(e)})));
""")
    assert data["calls"] == ["restore"], data
    assert any("preview boom" in e for e in data["errors"]), "预览出错必须在控制台看得见"


def test_t3_6_confirmed_indicator_hidden_only_by_style_change():
    """“样式已确认”只在样式下拉框变化时隐藏；切换“原视频有字幕”不动它。"""
    toggle = re.search(r"\n(\$\('subToggle'\)\.addEventListener\('click',.*?)\n", _HTML).group(1)
    data = _run_node(_preview_env(_js_function("openPreviewZoom"), _js_function("closePreviewZoom"),
                                  _js_function("bindStylePreview"), toggle, """
bindStylePreview();setStyle('PingFang SC',72,'white','black',2);updatePreview();
els.styleConfirmed.style.display='block';
const button={tagName:'BUTTON',dataset:{v:'yes'},classList:{add(){},remove(){}},parentElement:{querySelectorAll:()=>[]}};
els.subToggle.fire('click',{target:button});const afterToggle=els.styleConfirmed.style.display;
els.styleSize.value='42';els.styleSize.fire('change',{target:els.styleSize});
console.log(JSON.stringify({afterToggle,afterStyleChange:els.styleConfirmed.style.display,px:snap().px}));
"""))
    assert data["afterToggle"] == "block"
    assert data["afterStyleChange"] == "none"
    assert data["px"] == pytest.approx(42 * 0.708), "改字号后预览要跟着重画"


# ══════════════════════════════════════════════════════════════════
# ⑤.5 落地核验：从出字幕的生产入口 `worker._step_subtitle` 一路跑到写出的文件
# （正常运行、断点续跑、部分失败重试、接受部分结果都经过这里：worker.py 主流程第 4 步）
# ══════════════════════════════════════════════════════════════════

def test_landing_t1_step_subtitle_ai_split_leaves_no_space_or_marker(tmp_path, monkeypatch):
    """用户填了 API Key、字幕有长句 → AI 断句 → 出字幕。

    这一屏在逗号处换行（等级更高），AI 的断点落在第二行中间、没被用来换行——
    正是 P475 露出空格的形状：旧代码写出“每天放学 都去练习到很晚”。"""
    from backend import worker
    from backend.translation import orchestrator

    zh = "我那时候特别喜欢体操，每天放学都去练习到很晚"
    reply = "[0.1] 我那时候特别喜欢体操，每天放学\n[0.2] 都去练习到很晚"
    monkeypatch.setattr(orchestrator, "_build_llm_backends", lambda keys: [_ReplyBackend(reply)])
    segs = [_seg(0, 0.0, 5.0, "I really loved gymnastics back then and practiced late every day after school", zh)]
    srt, ass = tmp_path / "a.srt", tmp_path / "a.ass"
    worker._step_subtitle(segs, [{"platform": "deepseek", "key": "stub"}], {}, str(srt), str(ass),
                          True, "no", None, 1920, 1080)

    srt_text, ass_text = srt.read_text(encoding="utf-8"), ass.read_text(encoding="utf-8")
    # 插入④ 施工 B D1：AI 句只用 AI 那一刀（每天放学｜都去），程序不再在逗号处另加一刀。
    # 本条原意不变：AI 断点不在成品里露出空格或记号。
    for text in (srt_text, ass_text):
        assert "我那时候特别喜欢体操 每天放学" in text and "都去练习到很晚" in text, text
        assert "每天放学 都" not in text
    assert "\x1f" not in srt_text and "\x1f" not in ass_text


@pytest.mark.parametrize("outline, colour, width", [("white", "&H00FFFFFF", "3"), ("none", "&H00000000", "0")])
def test_landing_t5_step_subtitle_burn_uses_chosen_outline(tmp_path, outline, colour, width):
    """用户选白色描边 / 无描边并烧录 → 出字幕这一步写进样式文件的描边就是选的那个。"""
    from backend import worker

    segs = [_seg(0, 0.0, 2.0, "hello there.", "你好。")]
    srt, ass = tmp_path / "a.srt", tmp_path / "a.ass"
    worker._step_subtitle(segs, [], {}, str(srt), str(ass), True, "no",
                          {"outlineColor": outline, "outlineWidth": 3}, 1920, 1080)

    line = next(l for l in ass.read_text(encoding="utf-8").splitlines() if l.startswith("Style: "))
    style = dict(zip(_ASS_STYLE_FIELDS, line[len("Style: "):].split(",")))
    assert (style["OutlineColour"], style["Outline"]) == (colour, width)
