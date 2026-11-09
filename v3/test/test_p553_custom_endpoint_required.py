"""P553（2026-11-09）：「添加」自定义平台时没填平台地址 → 拦下并提示「请填写平台地址」。

以前空地址也能加进列表，点「开始」时后台静默跳过这个平台，最后报「还没有填写 API Key」，
把用户引去重新申请 Key。现在在唯一的入口（「添加」/编辑后「保存」都是 apiAddBtn）拦住。

做法：把 index.html 里 apiAddBtn 的点击处理函数原样抠出来，用 node 真跑。
"""

import json
import re
import subprocess
from pathlib import Path

HTML = (Path(__file__).parent.parent / "frontend" / "index.html").read_text(encoding="utf-8")


def _add_handler_source():
    m = re.search(r"\$\('apiAddBtn'\)\.addEventListener\('click',(\(\)=>\{.*?\n\})\);", HTML, re.S)
    assert m, "找不到「添加」按钮的点击处理"
    return m.group(1)


_STUB = """
const vals=%(vals)s;
const els={};
function el(id){return els[id]=els[id]||{value:vals[id]||'',style:{display:'none'},disabled:false}}
const $=el;
let toasts=[];const showToast=t=>toasts.push(t);
let saved=0;
const State={settings:{apiKeys:[]}};
let _editingKeyIdx=%(editing)s;
if(_editingKeyIdx>=0)State.settings.apiKeys.push({platform:'custom',key:'old',api_endpoint:'https://old.example/v1'});
const _readModelFromForm=()=>vals.model||'';
const _isCustomPlaceholder=()=>false;const _customModelCacheStaleForForm=()=>false;
const setApiCustomModelVisible=()=>{};
const cancelEdit=()=>{_editingKeyIdx=-1};
const buildKeyList=()=>{};const saveConfig=()=>{saved++};const refreshStartBtn=()=>{};const syncContextAwareBtn=()=>{};
(%(handler)s)();
console.log(JSON.stringify({keys:State.settings.apiKeys,toasts,saved}));
"""


def _click_add(vals, editing=-1):
    src = _STUB % {"vals": json.dumps(vals), "editing": editing, "handler": _add_handler_source()}
    r = subprocess.run(["node", "-e", src], text=True, capture_output=True, check=False)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_custom_without_endpoint_is_blocked_with_hint():
    out = _click_add({"apiPlatform": "custom", "apiKeyInput": "fake-key", "apiEndpoint": "   ", "model": "m1"})
    assert out["keys"] == []
    assert out["saved"] == 0
    assert out["toasts"] == ["请填写平台地址"]


def test_editing_custom_and_clearing_endpoint_is_blocked_too():
    """编辑已有的自定义平台、把地址删空后点「保存」：同样拦下，原条目不被改坏。"""
    out = _click_add({"apiPlatform": "custom", "apiKeyInput": "fake-key", "apiEndpoint": "", "model": "m1"}, editing=0)
    assert out["keys"] == [{"platform": "custom", "key": "old", "api_endpoint": "https://old.example/v1"}]
    assert out["saved"] == 0
    assert out["toasts"] == ["请填写平台地址"]


def test_custom_with_endpoint_is_added():
    out = _click_add({"apiPlatform": "custom", "apiKeyInput": "fake-key",
                      "apiEndpoint": " https://api.example/v1 ", "model": "m1"})
    assert out["keys"] == [{"platform": "custom", "name": "", "key": "fake-key",
                            "api_endpoint": "https://api.example/v1", "model": "m1"}]
    assert out["saved"] == 1
    assert out["toasts"] == []


def test_normal_platform_needs_no_endpoint():
    out = _click_add({"apiPlatform": "deepseek", "apiKeyInput": "fake-key", "apiEndpoint": "", "model": "deepseek-chat"})
    assert [k["platform"] for k in out["keys"]] == ["deepseek"]
    assert out["toasts"] == []
