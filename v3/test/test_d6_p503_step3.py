"""D-6 P503 第 3 步：免费引擎本体、编排、设置、计数、文案删净；删「极速/质量优先」与反思修正（P511）。

合同 K6–K9 + 合同增量 Z1、Z2（pipelines/bug-pipeline-2026-11-04-D6-P503.md ⑤.5 与第 2 步「用户裁决」）。
"""
import inspect
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from backend import config_manager, job_state, main, worker
from backend.translation import orchestrator
from test.test_d1b_frontend import HTML, _function_source, _run_node

BACKEND = Path(__file__).resolve().parents[1] / "backend"
OLD_KEYS = {"translationMode": "quality", "translationPriority": ["youdao", "google"]}


def _backend_sources():
    return {
        p: p.read_text(encoding="utf-8")
        for p in BACKEND.rglob("*.py") if "__pycache__" not in p.parts
    }


# ── K6：三家免费引擎文件与引用全部消失 ──────────────────────────────

def test_free_engine_and_reflection_files_are_gone():
    for name in ("google.py", "bing.py", "youdao.py", "reflection.py"):
        assert not (BACKEND / "translation" / name).exists(), name


def test_backend_source_has_no_free_engine_symbols():
    pattern = re.compile(
        r"GoogleBackend|BingBackend|YoudaoBackend|FREE_BACKENDS|_ALL_FREE|"
        r"_translate_free_only|_run_speed_mode_batch|_run_quality_mode_batch|"
        r"_reflection_review|translationMode|translationPriority|"
        r"auto_fallback|fallback_success|fallback_processed|switching_to_free|free_running|"
        r"免费引擎|免费翻译"
    )
    # 唯一允许出现旧键名的地方：main.py 里「旧键一律丢弃」的那张表。
    hits = [
        f"{p.relative_to(BACKEND)}:{i}: {line.strip()}"
        for p, text in _backend_sources().items()
        for i, line in enumerate(text.splitlines(), 1)
        if pattern.search(line)
        and not line.startswith("_DROPPED_CONFIG_KEYS = ")
    ]
    assert hits == []


def test_orchestrator_entry_has_no_mode_priority_or_free_order():
    params = inspect.signature(orchestrator.translate_with_orchestrator).parameters
    assert "mode" not in params and "priority" not in params
    assert "ordered_backends" not in inspect.signature(orchestrator._translate_llm_per_batch).parameters
    assert "classify_translation_failure" not in orchestrator.__dict__, "孤儿导入要随质量优先一起删"


def test_api_backends_returns_only_llm():
    data = TestClient(main.app).get("/api/backends").json()
    assert "free" not in data
    assert "llm" in data


# ── K7：旧设置键读、写、校验、缓存都不出事 ─────────────────────────

def test_default_config_has_no_mode_or_priority():
    assert "translationMode" not in config_manager.DEFAULT_CONFIG
    assert "translationPriority" not in config_manager.DEFAULT_CONFIG


def test_get_config_hides_old_keys_from_old_config_file():
    config_manager.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config_manager.CONFIG_PATH.write_text(
        json.dumps({**OLD_KEYS, "outputMode": "srt", "configVersion": 2}), encoding="utf-8"
    )
    data = TestClient(main.app).get("/api/config").json()
    assert data["outputMode"] == "srt"
    assert not set(OLD_KEYS) & set(data)


def test_put_config_drops_old_keys():
    resp = TestClient(main.app).put("/api/config", json={**OLD_KEYS, "outputMode": "srt"})
    assert resp.status_code == 200
    on_disk = json.loads(config_manager.CONFIG_PATH.read_text(encoding="utf-8"))
    assert on_disk.get("outputMode") == "srt"
    assert not set(OLD_KEYS) & set(on_disk)


def test_old_task_config_with_old_keys_does_not_fail_validation():
    worker._validate_config({"translationMode": "whatever", "translationPriority": ["google"]})


def test_old_task_config_keys_do_not_invalidate_caches():
    assert "translationMode" not in worker.SETTING_STAGE
    assert "translationPriority" not in worker.SETTING_STAGE

    class _Ck:
        def __init__(self):
            self.calls = []

        def __getattr__(self, name):
            return lambda *a, **k: self.calls.append(name)

    ck = _Ck()
    stage = worker._invalidate_stale_caches(
        "burn", {**OLD_KEYS, "model": "large-v2"}, {"model": "large-v2"},
        worker.SETTING_STAGE, worker.STAGE_ORDER, {}, ck,
    )
    assert stage == "burn" and ck.calls == [], "旧任务带两个旧键不得回退阶段、删缓存"


def test_step_translate_has_no_mode_priority_params():
    params = inspect.signature(worker._step_translate).parameters
    assert "translation_mode" not in params and "translation_priority" not in params


# ── Z1：任务身份键不再含两个旧设置 ─────────────────────────────────

def test_config_hash_ignores_old_keys():
    base = {"model": "large-v2", "outputMode": "srt"}
    assert main._config_hash(base) == main._config_hash({**base, **OLD_KEYS})


def test_public_config_has_no_old_keys():
    assert not set(OLD_KEYS) & set(main._public_config({**OLD_KEYS, "model": "large-v2"}))


# ── K8：公开状态无免费字段，requested_tier 保留 ─────────────────────

def test_public_translation_summary_has_no_fallback_fields():
    state = job_state.build_public_task_state({
        "status": "done",
        "translation_summary": {
            "requested_tier": "api_context", "api_success_count": 3, "api_total_count": 3,
            "fallback_success": 1, "auto_fallback": True,
        },
    })
    summary = state["result"]["quality"]["translation_summary"]
    assert summary["requested_tier"] == "api_context"
    assert "fallback_success" not in summary and "auto_fallback" not in summary


# ── K9：前端无免费区与策略开关；旧设置仍能读回 ─────────────────────

def test_frontend_has_no_free_translation_ui():
    for word in ("免费翻译", "免费补全", "translateStrategy", "freeTransSection", "priority-item",
                 "buildPriorityList", "modeToggle", "translationMode", "translationPriority",
                 "accBodyFreeTranslate", "'basic'", "auto_fallback", "fallback_success"):
        assert word not in HTML, word


def test_settings_from_server_are_applied_without_translation_mode():
    js = _function_source("loadConfig") + r"""
const State={settings:{outputMode:'burn',model:'large-v2'}};
const localStorage={getItem:()=>null};
let synced=0;function syncUI(){synced++}function updateSavePathDisplay(){}
function fetch(){return Promise.resolve({json:()=>Promise.resolve({outputMode:'srt',model:'large-v3'})})}
loadConfig();
setTimeout(()=>console.log(JSON.stringify({s:State.settings,synced})),20);
"""
    data = _run_node(js)
    assert data["s"]["outputMode"] == "srt" and data["s"]["model"] == "large-v3", \
        "设置不靠 translationMode 判断，删掉它后用户设置必须照样读回"
    assert data["synced"] == 1


def test_settings_from_server_error_response_is_ignored():
    js = _function_source("loadConfig") + r"""
const State={settings:{outputMode:'burn'}};
const localStorage={getItem:()=>null};
function syncUI(){}function updateSavePathDisplay(){}
function fetch(){return Promise.resolve({json:()=>Promise.resolve({detail:'Internal Server Error'})})}
loadConfig();
setTimeout(()=>console.log(JSON.stringify(State.settings)),20);
"""
    assert _run_node(js) == {"outputMode": "burn"}


# ── Z2 + Z3：弹窗原因不明时指向 Key/余额/网络 ──────────────────────
# Z3（⑤.17 用户定）：软件已自动在用户填的 Key 之间轮换过，多数人只有一个 Key，
# 「换一个 AI 平台」是布置作业，去掉；写「切换翻译引擎」的 8 条新任务走不到，删，
# 旧任务存的这些原因落到兜底句。

PARTIAL_FALLBACK = "请检查 Key、余额和网络后重试"


def test_partial_unknown_reason_points_to_key_balance_network():
    js = _function_source("partialReason") + r"""
console.log(JSON.stringify([partialReason('unknown'),partialReason('something_new'),partialReason(undefined)]));
"""
    assert _run_node(js) == [PARTIAL_FALLBACK] * 3


def test_partial_reason_has_no_engine_or_platform_switch_advice():
    source = _function_source("partialReason")
    assert "切换翻译引擎" not in source
    assert "换一个 AI 平台" not in source
    kinds = ["rate_limit", "connection", "timeout", "service_error", "request_rejected",
             "numbering_format", "empty_translation", "quality_rejected"]
    js = source + f"\nconsole.log(JSON.stringify({kinds!r}.map(partialReason)));"
    assert _run_node(js) == [PARTIAL_FALLBACK] * len(kinds), \
        "升级前旧任务存的这些原因要落到兜底句"
    js = source + "\nconsole.log(JSON.stringify([partialReason('auth'),partialReason('insufficient_balance')]));"
    assert _run_node(js) == ["API Key 无效，请检查或更换 Key 后重试", "账户余额不足，请充值后重试"]


# ── Z4：任务出错卡片的超时 / 限流提示不再叫用户「切换翻译引擎」 ──────────
# 免费引擎删了，「翻译引擎」用户已经找不到；软件本身会在用户填的 Key 之间轮换。

def test_timeout_and_rate_limit_errors_do_not_mention_engine_switch():
    timeout = worker._friendly_error("ReadTimeout: read timed out")
    limited = worker._friendly_error("HTTP 429 Too Many Requests")
    assert timeout == "连接翻译服务器超时。请检查网络后重试。"
    assert limited == "翻译请求过于频繁，已被限流。请稍等片刻后重试。"
    assert "翻译引擎" not in inspect.getsource(worker._friendly_error)
