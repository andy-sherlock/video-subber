"""翻译结果统一转成目标写法（G12 第三批 B · 排版方案 §13.3 / L-17）。

真实问题：新西兰片 31:26 译文出现「開心」——翻译模型偶尔吐繁体字。
在字幕步骤开头、第 22 件断句（llm_split_segments）之前统一转一次；逐段、上下文感知、
重试恢复的译文都从这里过。「目标写法」参数为将来让用户选字幕写法留口，本批只传简体。

转换用 zhconv（纯 Python，维基 + OpenCC 词表，最长匹配）。加载失败 → 原文不动、
只记一次日志、任务照常完成。
"""

import logging
import warnings

_log = logging.getLogger(__name__)

DEFAULT_TARGET = "zh-hans"

_converter = None
_load_failed = False


def _import_zhconv():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")      # zhconv 导入 pkg_resources 时的弃用提醒
        import zhconv
    return zhconv.convert


def _get_converter():
    global _converter, _load_failed
    if _converter is None and not _load_failed:
        try:
            _converter = _import_zhconv()
        except Exception as exc:
            _load_failed = True
            _log.warning("繁简转换不可用，译文保持原样：%s", exc)
    return _converter


def to_script(text: str, target: str = DEFAULT_TARGET) -> str:
    global _load_failed
    convert = _get_converter()
    if convert is None or _load_failed or not text:
        return text
    try:
        return convert(text, target)
    except Exception as exc:     # 词典在第一次转换时才读；读坏了也只能原样不动（⑦.5 ③）
        _load_failed = True
        _log.warning("繁简转换不可用，译文保持原样：%s", exc)
        return text


def convert_segments(segments, pre_splits=None, target: str = DEFAULT_TARGET) -> None:
    """就地把每段译文和翻译步骤预拆的碎片转成 ``target`` 写法。"""
    for seg in segments:
        seg.zh = to_script(getattr(seg, "zh", ""), target)
    for key, parts in (pre_splits or {}).items():
        pre_splits[key] = [to_script(p, target) for p in parts]
