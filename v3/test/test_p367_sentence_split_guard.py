"""P367：LLM 断句只能切分，不能增删改译文。"""

from pathlib import Path
import re
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.translation.base import TranslationResult
from backend.core.sentence_splitter import _llm_batch_split


class FakeSegment:
    def __init__(self, zh):
        self.id = 7
        self.start = 0.0
        self.end = 6.0
        self.text = "source"
        self.zh = zh
        self.speaker_change = False
        self.uncertain = False
        self.words = []


class RawLLM:
    name = "split-fake"

    def __init__(self, raw):
        self.raw = raw

    def translate_raw(self, prompt, max_tokens=4096):
        return TranslationResult(translated=[self.raw], backend_name=self.name)


def _joined(result):
    return re.sub(r"\s+", "", "".join(part.zh for part in result[0]))


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("[1.1] 这是原文前半\n[1.2] 这是原文后半", id="outside"),
        pytest.param("[0.1] 这是原文前半\n[0.1] 这是原文后半", id="duplicate"),
        pytest.param("[0.1] 这是原文前半\n[0.3] 这是原文后半", id="non-contiguous"),
        pytest.param("[0.1] This is original\n[0.2] text", id="english"),
        pytest.param("[0.1] 这是原文 ---\n[0.2] 前半这是原文后半", id="dash"),
        pytest.param("[0.1] 建议：这是原文前半\n[0.2] 这是原文后半", id="review"),
        pytest.param("[0.1] 这是改写前半\n[0.2] 这是原文后半", id="rewritten"),
    ],
)
def test_invalid_llm_split_falls_back_without_losing_original(raw):
    original = "这是原文前半，这是原文后半而且长度超过二十个汉字"

    result = _llm_batch_split([FakeSegment(original)], RawLLM(raw), on_wait=lambda *a: None)

    assert _joined(result) == re.sub(r"\s+", "", original)


def test_valid_llm_split_really_applies():
    original = "这是原文前半这是原文后半而且长度超过二十个汉字"
    raw = "[0.1] 这是原文前半\n[0.2] 这是原文后半而且长度超过二十个汉字"

    result = _llm_batch_split([FakeSegment(original)], RawLLM(raw), on_wait=lambda *a: None)

    # G5-1 类型 C：原断言逐条比对拆出的两个分片
    #   ["这是原文前半", "这是原文后半而且长度超过二十个汉字"]。
    # 那两条分片的时间是 `_build_split_segments` 按字数比例造出来的——R-11 禁止。
    # 现在这一层只保证「模型的合法回答被接受、译文一个字不丢」；
    # 真正的分屏由 display_timeline 用真实原词边界完成。
    # P367 要保的「合法拆分不被守卫误杀」这一点没变：译文必须完整拼回原文。
    assert len(result[0]) == 1, "这一层不得再产生按字数分时间的分屏"
    assert _joined(result) == original
