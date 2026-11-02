"""V3.09-C3 收口 + UI 定稿施工批（管道 ㊵ 步）。

四个独立 commit 的测试，先 A 后 B：
- A1 修复状态生命周期：问题1（fallback 前进度归零）、问题2（start_prepare 清 repair
  旧残渣）、收口点①（前端恢复轮询按 repair 来源）、收口点②（repair 结束原子落态：
  先备 ready/error 最后翻 repairing:False）、收口点③（前端 pollPrepareUntilDone
  按来源门控修复失败提示）。
- A2 运行中锁反转（后续追加）。
- B5 字幕样式合并（后续追加）。
- B6 翻译分区（后续追加）。
"""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

_HTML = (Path(__file__).parent.parent / "frontend" / "index.html")


def _reset_prepare_state():
    from backend import prepare_manager as pm
    pm._prepare.update({
        "running": False, "stop": False, "queue": [], "index": 0,
        "current": None, "states": {}, "unfixable": [], "error": None,
    })
    pm._repair_state.clear()


class _RegistryTmpDirs(unittest.TestCase):
    """自管目录/两个旧缓存都指到临时目录（复制自 c35 prepare_repair 脚手架）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.models_root = root / "managed"
        self.ms_cache = root / "ms-cache"
        self.hf_cache = root / "hf-cache"
        from backend import model_manager
        from backend import model_registry as reg
        self._patches = [
            patch.object(reg, "MODELS_ROOT", self.models_root),
            patch.object(model_manager, "MODELSCOPE_CACHE_DIR", self.ms_cache),
            patch.object(model_manager, "CACHE_DIR", self.hf_cache),
        ]
        for p in self._patches:
            p.start()
        _reset_prepare_state()

    def tearDown(self):
        _reset_prepare_state()
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def _make_managed_ready(self, key: str):
        from backend import model_registry as reg
        entry = reg.get_entry(key)
        d = Path(entry["local_dir"])
        d.mkdir(parents=True, exist_ok=True)
        (d / entry["probe_file"]).write_bytes(b"probe-content")
        (d / "model.safetensors").write_bytes(b"weights-content")
        reg.write_manifest_v2(key)
        return d


class _RecordingState(dict):
    """记录写入顺序，验证收口点②的原子落态。"""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._events = []

    def __setitem__(self, k, v):
        self._events.append((k, v))
        super().__setitem__(k, v)

    def update(self, *a, **k):
        d = dict(*a, **k)
        for kk, vv in d.items():
            self._events.append((kk, vv))
        super().update(d)


# ═══════════════════════════════════════════════════════════════════
# A1 · 问题1：文件级修复转整模型兜底前，进度必须归零
# ═══════════════════════════════════════════════════════════════════

class TestA1RepairProgressReset(_RegistryTmpDirs):

    def test_fallback_resets_progress_before_full_download(self):
        """回调只在新百分比更大时更新（model_manager 323 行）；文件级把进度推到
        90% 后转兜底，若不归零，兜底的低百分比永远盖不过 90%，横幅卡在旧高位。"""
        from backend import prepare_manager as pm
        from backend import model_registry as reg
        d = self._make_managed_ready("sensevoice")
        (d / "model.safetensors").write_bytes(b"weights-CONTENT")  # 同尺寸坏

        seen = {}

        def fake_download(repo, tgt, allow_patterns=None, state=None, **kw):
            if allow_patterns:
                # 文件级：模拟回调把进度推到高位后静默 0 文件（复验仍坏→兜底）
                if state is not None:
                    state["progress"] = 90
                    state["downloaded_bytes"] = 999999
                    state["speed_bytes_per_sec"] = 5_000_000
                    state["speed_text"] = "5.0 MB/s"
                return
            # 兜底调用：进入时进度必须已归零
            seen["progress"] = state.get("progress")
            seen["downloaded"] = state.get("downloaded_bytes")
            seen["speed_bps"] = state.get("speed_bytes_per_sec")
            entry = reg.get_entry("sensevoice")
            (Path(tgt) / entry["probe_file"]).write_bytes(b"probe-content")
            (Path(tgt) / "model.safetensors").write_bytes(b"weights-content")

        plan = pm._plan_repair("sensevoice")
        state = {}
        with patch.object(pm, "_snapshot_download", side_effect=fake_download):
            pm._execute_repair(plan, state)

        self.assertTrue(state.get("fallback"), "复验仍坏必须转兜底")
        self.assertEqual(seen.get("progress"), 0,
                         "转整模型兜底前必须把 progress 归零")
        self.assertEqual(seen.get("downloaded"), 0,
                         "转整模型兜底前必须把 downloaded_bytes 归零")
        self.assertEqual(seen.get("speed_bps"), 0,
                         "转整模型兜底前必须把 speed 归零")


# ═══════════════════════════════════════════════════════════════════
# A1 · 问题2：start_prepare 清 repair 旧残渣（尤其 error）
# ═══════════════════════════════════════════════════════════════════

class TestA1StartPrepareClearsStaleRepair(_RegistryTmpDirs):

    def test_clears_stale_repair_error_when_not_repairing(self):
        """上一轮修复失败留下 _repair_state.error；下次一键准备结束不该误弹修复失败。"""
        from backend import prepare_manager as pm
        pm._repair_state.clear()
        pm._repair_state.update({"repairing": False, "ready": False,
                                 "error": "旧修复失败", "target": "sensevoice"})
        with patch.object(pm, "assess_environment",
                          return_value={"missing": [], "ready": ["whisper"],
                                        "unfixable": []}):
            pm.start_prepare()
        self.assertFalse(pm._repair_state.get("error"),
                         "start_prepare 必须清掉已结束修复的旧 error 残渣")

    def test_keeps_active_repair_state(self):
        """正在修复时 start_prepare 不许清状态，也不许启动准备。"""
        from backend import prepare_manager as pm
        pm._repair_state.clear()
        pm._repair_state.update({"repairing": True, "target": "sensevoice"})
        with patch.object(pm, "assess_environment",
                          return_value={"missing": [], "ready": [],
                                        "unfixable": []}):
            result = pm.start_prepare()
        self.assertEqual(pm._repair_state.get("target"), "sensevoice",
                         "正在修复的状态不能被 start_prepare 清掉")
        self.assertIn("error", result)

    def test_checks_active_repair_before_environment_assessment(self):
        """正在修复时，一键准备不应先读模型目录；否则会和 repair 删除/重下同目录打架。"""
        from backend import prepare_manager as pm
        pm._repair_state.clear()
        pm._repair_state.update({"repairing": True, "target": "sensevoice"})
        with patch.object(pm, "assess_environment",
                          side_effect=AssertionError("不该在修复中做环境评估")):
            result = pm.start_prepare()
        self.assertIn("正在修复模型", result.get("error", ""))


# ═══════════════════════════════════════════════════════════════════
# A1 · 收口点②：repair 结束原子落态——先备 ready/error，最后翻 repairing:False
# ═══════════════════════════════════════════════════════════════════

class TestA1AtomicRepairLanding(_RegistryTmpDirs):

    def _assert_repairing_flips_last(self, state):
        events = state._events
        false_idxs = [i for i, (k, v) in enumerate(events)
                      if k == "repairing" and v is False]
        self.assertTrue(false_idxs, "落态必须把 repairing 翻成 False")
        r_idx = max(false_idxs)
        landed = [i for i, (k, v) in enumerate(events)
                  if (k == "ready" and v) or (k == "error" and v)]
        self.assertTrue(landed, "落态必须写 ready 或 error")
        self.assertLess(max(landed), r_idx,
                        "必须先备好 ready/error，最后一步才翻 repairing:False")

    def test_file_success_lands_ready_before_repairing_false(self):
        from backend import prepare_manager as pm
        d = self._make_managed_ready("sensevoice")
        (d / "model.safetensors").write_bytes(b"weights-CONTENT")

        def fake_download(repo, tgt, allow_patterns=None, state=None, **kw):
            (Path(tgt) / "model.safetensors").write_bytes(b"weights-content")

        plan = pm._plan_repair("sensevoice")
        state = _RecordingState()
        with patch.object(pm, "_snapshot_download", side_effect=fake_download):
            pm._execute_repair(plan, state)
        self.assertTrue(state.get("ready"))
        self._assert_repairing_flips_last(state)

    def test_full_success_lands_ready_before_repairing_false(self):
        from backend import prepare_manager as pm
        from backend import model_registry as reg
        import json
        entry = reg.get_entry("sensevoice")
        d = Path(entry["local_dir"])
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps(
            {"status": "ready", "repo": entry["repo"]}))  # 旧 v1 → full

        def fake_download(repo, tgt, allow_patterns=None, state=None, **kw):
            (Path(tgt) / entry["probe_file"]).write_bytes(b"probe-content")
            (Path(tgt) / "model.safetensors").write_bytes(b"weights-content")

        plan = pm._plan_repair("sensevoice")
        state = _RecordingState()
        with patch.object(pm, "_snapshot_download", side_effect=fake_download):
            pm._execute_repair(plan, state)
        self.assertTrue(state.get("ready"))
        self._assert_repairing_flips_last(state)

    def test_exception_lands_error_before_repairing_false(self):
        from backend import prepare_manager as pm
        from backend import model_registry as reg
        import json
        entry = reg.get_entry("sensevoice")
        d = Path(entry["local_dir"])
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps(
            {"status": "ready", "repo": entry["repo"]}))  # 旧 v1 → full

        def fake_download(repo, tgt, allow_patterns=None, state=None, **kw):
            raise RuntimeError("boom")

        plan = pm._plan_repair("sensevoice")
        state = _RecordingState()
        with patch.object(pm, "_snapshot_download", side_effect=fake_download):
            pm._execute_repair(plan, state)
        self.assertTrue(state.get("error"))
        self.assertFalse(state.get("repairing"))
        self._assert_repairing_flips_last(state)


# ═══════════════════════════════════════════════════════════════════
# A1 · 收口点①③：前端按来源门控修复失败提示（prepare 来源不读 repair.error）
# ═══════════════════════════════════════════════════════════════════

class TestA1FrontendSourceGating(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.html = _HTML.read_text(encoding="utf-8")
        cls.compact = cls.html.replace(" ", "")

    def _fn_body(self, name):
        m = re.search(r"function " + name + r"\([^)]*\)\{(.*?)\n\}",
                      self.html, re.S)
        self.assertIsNotNone(m, f"找不到函数 {name}")
        return m.group(1).replace(" ", "")

    def test_poll_gates_repair_error_by_source(self):
        """收口点③：只有修复发起的轮询结束才读 p.repair.error。"""
        body = self._fn_body("pollPrepareUntilDone")
        self.assertIn("source==='repair'", body,
                      "pollPrepareUntilDone 必须按来源门控修复失败提示")

    def test_repair_flow_uses_repair_source(self):
        body = self._fn_body("repairFlow")
        self.assertIn("pollPrepareUntilDone(onReady,'repair')", body,
                      "修复发起必须标记 repair 来源")

    def test_start_prepare_uses_prepare_source(self):
        body = self._fn_body("startPrepareNow")
        self.assertIn("pollPrepareUntilDone(null,'prepare')", body,
                      "一键准备发起必须标记 prepare 来源，不读 repair.error")

    def test_recovery_poll_uses_repair_source_when_repairing(self):
        """收口点①：页面刷新恢复轮询，正在修复时也要按 repair 来源，否则真失败被吞。"""
        self.assertIn(
            "pollPrepareUntilDone(null,(p.repair&&p.repair.repairing)?'repair':'prepare')",
            self.compact,
            "恢复轮询必须按 p.repair.repairing 决定来源")


# ═══════════════════════════════════════════════════════════════════
# A2 · 运行中锁反转：默认锁每个 .acc-body + 豁免 acc-head + 抽屉外锁 mainTierToggle
#      + 关键写配置函数 settingsLocked 兜底（收口点③④）
# ═══════════════════════════════════════════════════════════════════

class TestA2LockInversion(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.html = _HTML.read_text(encoding="utf-8")
        cls.compact = cls.html.replace(" ", "")

    def _fn_body(self, name):
        m = re.search(r"function " + name + r"\([^)]*\)\{(.*?)\n\}",
                      self.html, re.S)
        self.assertIsNotNone(m, f"找不到函数 {name}")
        return m.group(1).replace(" ", "")

    def test_lockui_locks_acc_body_containers(self):
        """问题3+4：lockUI 锁每个 .acc-body 容器（默认全锁 + 豁免 acc-head）。"""
        body = self._fn_body("lockUI")
        self.assertIn(".acc-body", body, "lockUI 必须锁 .acc-body 容器")
        self.assertIn("disabled-ctl", body)

    def test_lockui_keeps_maintiertoggle_outside_lock(self):
        """收口点③：mainTierToggle 在主界面不在 .acc-body，保留抽屉外小名单。"""
        body = self._fn_body("lockUI")
        self.assertIn("'mainTierToggle'", body,
                      "lockUI 必须额外锁抽屉外的 mainTierToggle")

    def test_lockui_drops_handwritten_drawer_whitelist(self):
        """删掉抽屉内那堆手写 id 白名单（改由 .acc-body 容器覆盖）。"""
        body = self._fn_body("lockUI")
        for token in ["'subToggle'", "'apiKeyInput'", "'ctxToggle'",
                      "'tierToggle'", "'speakerToggle'", "'outputModeSelect'"]:
            self.assertNotIn(token, body,
                             f"lockUI 不该再手写白名单 {token}（.acc-body 已覆盖）")

    def test_deletekey_guarded_by_settingslocked(self):
        """收口点④：deleteKey 内部先判 settingsLocked 兜底（脚本直调也拦住）。"""
        body = self._fn_body("deleteKey")
        self.assertIn("if(settingsLocked)return", body,
                      "deleteKey 必须先判 settingsLocked")

    def test_savepath_save_guarded_by_settingslocked(self):
        """收口点④：已打开的保存路径输入框 blur 触发 saveConfig 前判 settingsLocked。"""
        self.assertIn("v!==current&&!settingsLocked", self.compact,
                      "savePath 保存必须被 settingsLocked 兜底")

    def test_drawer_lock_banner_visible_only_while_locked(self):
        """任务运行中打开抽屉时，抽屉内部也要解释为什么设置改不了。"""
        self.assertIn('id="drawerLockBanner"', self.html)
        self.assertIn("任务进行中，设置暂不可改", self.html)
        self.assertIn(".drawer-lock-banner{display:none", self.compact,
                      "drawerLockBanner 初始必须隐藏，未锁定时不可见")
        lock_body = self._fn_body("lockUI")
        unlock_body = self._fn_body("unlockUI")
        self.assertIn("drawerLockBanner", lock_body)
        self.assertIn("style.display='block'", lock_body)
        self.assertIn("drawerLockBanner", unlock_body)
        self.assertIn("style.display='none'", unlock_body)


# ═══════════════════════════════════════════════════════════════════
# B5 · 字幕样式合进"字幕与输出"：砍独立模块（6→5），搬进 output acc-body，
#      烧录显示样式控件 / 封装·SRT 隐藏并留灰字解释（互斥出现）
# ═══════════════════════════════════════════════════════════════════

class TestB5SubtitleStyleMerged(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.html = _HTML.read_text(encoding="utf-8")
        cls.compact = cls.html.replace(" ", "")

    def test_style_accordion_module_removed(self):
        """砍独立"字幕样式"手风琴模块（6→5）。"""
        self.assertNotIn('data-acc="style"', self.html,
                         "独立字幕样式手风琴模块应已砍掉")
        self.assertNotIn('id="accBodyStyle"', self.html)

    def test_style_mounted_into_output_body(self):
        """styleSubSection 搬进 output 的 acc-body，紧跟输出方式下面。"""
        self.assertNotIn("place('accBodyStyle'", self.compact,
                         "不该再把样式搬进独立 accBodyStyle")
        self.assertIn(
            "place('accBodyOutput',['outputPanel','styleSubSection','subtitleMarkerNote']",
            self.compact, "样式、字幕标记说明搬进 accBodyOutput（styleModeHint 已删）")

    def test_style_mode_hint_removed(self):
        """Fix 1：非烧录模式不再显示「字幕样式·仅烧录可用」灰框——彻底删除，
        不再对封装/SRT 解释烧录功能。整个 styleModeHint 元素和文案都不该存在。"""
        self.assertNotIn('id="styleModeHint"', self.html)
        self.assertNotIn("字幕样式 · 仅烧录可用", self.html)
        self.assertNotIn("封装和 SRT 不会改写画面样式", self.html)
        self.assertNotIn("style-disabled-entry", self.html,
                         "灰框专用 CSS 删元素后应一并清掉，不留孤儿样式")

    def test_output_change_toggles_style_section_only(self):
        """Fix 1：烧录显示样式控件、非烧录隐藏；
        灰框已删，全文件不该再有任何 styleModeHint 引用（含 909/1945/1588/HTML）。

        **2026-09-07 断言迁移**：这段显隐原本写在 outputModeSelect 的 change
        监听器里，与另外两处（加载配置、syncOutputModeUI）各写一遍——
        外审第五轮抓到「改一处漏两处」（拖 mp3 时按钮说生成字幕、
        底下还挂着烧录警告）。现已全部收进 `syncOutputModeUI()` 一处，
        change 监听器只负责调用它。断言跟着搬到新位置，**意图不变**：
        烧录才显示样式控件。
        """
        self.assertIn("styleSec.style.display=mode==='burn'?'block':'none'",
                      self.compact, "烧录才显示样式控件（现由 syncOutputModeUI 统一控制）")
        self.assertNotIn("styleModeHint", self.compact,
                         "styleModeHint 已彻底删除，任何引用（DOM/handler/init/place）都不该留")


# ═══════════════════════════════════════════════════════════════════
# B6 · 翻译拆成两个独立手风琴：免费翻译 / 智能翻译。
#      免费区放策略 + 引擎顺序；智能区点击模块后直接填 API Key，不再二次展开。
# ═══════════════════════════════════════════════════════════════════

class TestB6TranslationSplit(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.html = _HTML.read_text(encoding="utf-8")
        cls.compact = cls.html.replace(" ", "")

    def test_no_llm_wording_in_translation_ui(self):
        """全程无"LLM"字样（用户可见文案，含设置区与降级弹窗）。"""
        for gone in ['LLM 智能翻译', '暂未配置 LLM API Key', '可用 LLM 平台',
                     'LLM 翻译失败', 'LLM 翻译全部失败', '上下文感知：LLM']:
            self.assertNotIn(gone, self.html, f"翻译 UI 仍有 LLM 字样：{gone}")

    def test_old_unified_translate_intro_removed(self):
        self.assertNotIn('id="translateIntro"', self.html,
                         "统一翻译说明应删除，信息由免费/智能两个模块自己承担")
        self.assertNotIn(
            '默认可直接用免费翻译。添加 API Key 后会优先使用智能翻译；智能翻译不可用时，会改用免费翻译继续。',
            self.html)

    def test_smart_and_free_sections_distinct_titles(self):
        """两个分区标题层级拉开（trans-section-title，不再 12px 同色灰 card-label）。"""
        self.assertIn('id="smartTransSection"', self.html)
        self.assertIn('id="freeTransSection"', self.html)
        self.assertIn('trans-section-title', self.html)
        self.assertIn('>智能翻译<', self.html)
        self.assertIn('>免费翻译<', self.html)

    def test_smart_subtitle_plain_language(self):
        self.assertIn('用 Claude、GPT 这类大模型翻译，更懂上下文和专业词，需要你自己的 API Key。',
                      self.html)

    def test_static_translate_sections_keep_free_before_smart(self):
        """静态区块仍按免费在前、智能在后，便于 mount 后保持同样阅读顺序。"""
        self.assertIn('id="translateStrategy"', self.html)
        self.assertIn('>翻译策略<', self.html)
        i_strategy = self.html.index('id="translateStrategy"')
        i_free = self.html.index('id="freeTransSection"')
        i_smart = self.html.index('id="smartTransSection"')
        self.assertLess(i_strategy, i_free, "翻译策略必须在免费翻译区上方")
        self.assertLess(i_free, i_smart, "免费翻译必须在智能翻译区上方")

    def test_contextaware_moved_into_smart_section(self):
        """contextAware(ctxToggle) 必须在智能翻译区，高级模块已经删除。"""
        i_smart = self.html.index('id="smartTransSection"')
        i_note = self.html.index('id="subtitleMarkerNote"')
        i_ctx = self.html.index('id="ctxToggle"')
        self.assertTrue(i_smart < i_ctx < i_note, "ctxToggle 必须落在智能翻译区内")
        self.assertNotIn('id="advCard"', self.html, "高级设置卡片应删除")
        self.assertNotIn('data-acc="advanced"', self.html, "高级手风琴模块应删除")

    def test_free_section_priority_list_always_visible(self):
        """免费区只留 Google/Bing/有道拖拽顺序，且始终可见（不被 translationMode 门控）。"""
        self.assertIn('class="priority-list show"', self.html)
        self.assertNotIn("$('priorityList').classList.toggle('show'", self.html,
                         "免费区拖拽顺序不该再被 translationMode 门控隐藏")
        self.assertNotIn("pl.classList.toggle('show'", self.html)

    def test_mount_translate_uses_new_blocks(self):
        self.assertIn("place('accBodyFreeTranslate',['translateStrategy','freeTransSection']",
                      self.compact)
        self.assertIn("place('accBodySmartTranslate',['smartTransSection']",
                      self.compact)
        self.assertNotIn("accBodyTranslate", self.html,
                         "旧的大翻译模块 body 应删除")


class TestB6SmartTranslationCompact(unittest.TestCase):
    """B6 补强：智能翻译模块一打开就直接填 API Key，不再二次展开。"""

    @classmethod
    def setUpClass(cls):
        cls.html = _HTML.read_text(encoding="utf-8")
        cls.compact = cls.html.replace(" ", "")
        cls.worker = (Path(__file__).parent.parent / "backend" / "worker.py").read_text(
            encoding="utf-8")

    def test_smart_section_directly_shows_config_body(self):
        self.assertNotIn('id="smartTransConfigToggle"', self.html,
                         "点开智能翻译模块后不该再多点一次配置按钮")
        self.assertNotIn('id="smartTransSummary"', self.html,
                         "免费翻译模块已经说明默认路径，智能区不再重复提示")
        self.assertNotIn("smartTransExpanded", self.html)
        self.assertNotIn("toggleSmartTransConfig", self.html)
        self.assertIn('id="smartTransConfigBody"', self.html,
                      "API Key 表单和上下文开关仍放在智能翻译配置体")
        self.assertIn("smart-trans-config", self.html)
        self.assertIn(".smart-trans-config{display:block", self.compact,
                      "智能翻译模块展开后配置体直接可见")

    def test_existing_handler_ids_stay_inside_smart_config_body(self):
        """保持原 id 不变，但把付费配置细节收进 smartTransConfigBody。"""
        body_start = self.html.index('id="smartTransConfigBody"')
        body_end = self.html.index('id="subtitleMarkerNote"')
        for element_id in [
            'id="ctxToggle"', 'id="apiKeyInput"', 'id="apiPlatform"',
            'id="apiModel"', 'id="apiCustomModel"', 'id="apiModelName"',
            'id="apiEndpoint"', 'id="apiName"', 'id="apiAddBtn"',
            'id="apiKeyList"',
        ]:
            idx = self.html.index(element_id)
            self.assertTrue(body_start < idx < body_end,
                            f"{element_id} 必须仍在智能翻译配置体里，不能改 id")

    def test_free_translation_stays_visible_outside_paid_config_body(self):
        smart_start = self.html.index('id="smartTransSection"')
        priority = self.html.index('id="priorityList"')
        self.assertLess(priority, smart_start,
                        "免费翻译区必须在付费配置体之外，始终可见")

    def test_worker_user_visible_fallback_messages_have_no_llm_word(self):
        """后端发给前端的进度/弹窗文案也不能带用户可见的 LLM。"""
        for gone in ["LLM 翻译全部失败", "LLM 翻译未能完成"]:
            self.assertNotIn(gone, self.worker)


class TestC3FinalInformationArchitecture(unittest.TestCase):
    """本轮最终信息架构：主界面展示轻量运行环境，抽屉只留 4 个模块。"""

    @classmethod
    def setUpClass(cls):
        cls.html = _HTML.read_text(encoding="utf-8")
        cls.compact = cls.html.replace(" ", "")

    def _fn_body(self, name):
        m = re.search(r"function " + name + r"\([^)]*\)\{(.*?)\n\}",
                      self.html, re.S)
        self.assertIsNotNone(m, f"找不到函数 {name}")
        return m.group(1).replace(" ", "")

    def _fn_source(self, name):
        m = re.search(r"function " + name + r"\([^)]*\)\{.*?\n\}",
                      self.html, re.S)
        self.assertIsNotNone(m, f"找不到函数 {name}")
        return m.group(0)

    def test_drawer_has_four_modules_in_final_order(self):
        order = re.findall(r'<section class="acc-item" data-acc="([^"]+)"',
                           self.html)
        self.assertEqual(
            order,
            ["voice", "free-translate", "smart-translate", "output"],
            "抽屉最终顺序必须是：语音识别 → 免费翻译 → 智能翻译 → 字幕与输出")
        for gone in ['data-acc="env"', 'data-acc="translate"',
                     'data-acc="advanced"']:
            self.assertNotIn(gone, self.html)

    def test_env_status_line_lives_on_main_screen_not_drawer(self):
        self.assertIn('id="envPill"', self.html)
        self.assertIn("env-status-line", self.html)
        self.assertNotIn("顶栏就绪药丸", self.html,
                         "运行环境已改成顶部文字状态行，注释也不能再叫药丸")
        for label in ["语音识别", "识别增强", "声音分类", "视频处理"]:
            self.assertIn(label, self.html)
        for state_id in ["envStatevoice", "envStateenhance",
                         "envStatesound", "envStatevideo"]:
            self.assertIn(f'id="{state_id}"', self.html,
                          "静态环境状态 id 应与 renderEnvStatusLine 生成结果一致")
        for stale_id in ["envStateVoice", "envStateEnhance",
                         "envStateSound", "envStateVideo"]:
            self.assertNotIn(f'id="{stale_id}"', self.html,
                             "静态环境状态 id 不应和刷新后的 id 大小写不一致")
        self.assertNotIn("drawerComponentList", self.html,
                         "运行环境组件明细不再放进抽屉")
        render = self._fn_body("renderEnv")
        self.assertIn("renderEnvStatusLine(items)", render,
                      "renderEnv 应刷新主界面的轻量环境状态行")
        self.assertIn('onclick="repairFlow()"', render,
                      "异常横幅必须提供一键修复入口")
        self.assertIn("constrepairable=", render,
                      "异常横幅应先判断是否有模型类问题可修复")
        self.assertIn("it.repairable===true", render,
                      "一键修复只能看组件自己声明的可修复状态，不能只排除 video")
        self.assertIn("repairable?", render,
                      "只有模型文件问题才显示一键修复；组件未安装时不应显示")

    def test_repair_button_only_targets_missing_model_files(self):
        """一键修复只修模型文件，不承诺安装 Python 包或 FFmpeg。"""
        body = self._fn_body("_envComponents")
        self.assertIn(
            "if(o.installed_pkg===false)return{dot:'err',text:copy.no_pkg,repairable:false,preparable:true}",
            body)
        self.assertIn(
            "if(o.model_ready===false)return{dot:'err',text:copy.no_model,repairable:true,preparable:true}",
            body)
        # 语音识别（whisper）与其余三项同口径：走 split()，包缺失不可修复、模型缺失可修复。
        # 旧的单 installed 布尔把"包缺失"也判成可修复，与 point 3 边界冲突。
        self.assertIn("split(wh,_COMP_COPY.whisper)", body,
                      "voice 分支必须复用 split()，让 whisper 也区分包缺失/模型缺失")
        self.assertNotIn("repairable:wh.installed===false", body,
                         "旧的单 installed 布尔判定必须删除，改由 split() 统一处理")
        self.assertIn("repairable:asr.repairable||align.repairable", body)
        self.assertIn("repairable:false", body,
                      "视频处理组件和组件未安装类问题不能被一键修复覆盖")

    def test_whisper_component_has_split_copy(self):
        """whisper 也要有 no_pkg/no_model 双文案，供 split() 区分包缺失与模型缺失。"""
        self.assertIn("whisper:{ok:'语音识别已就绪'", self.compact)
        self.assertIn("no_pkg:'语音识别组件未安装'", self.compact)
        self.assertIn("no_model:'语音识别模型未下载'", self.compact)

    def test_p357_whisper_runtime_not_ready_marks_voice_unavailable(self):
        """P357: 包和模型都在但 Metal 不可用时，顶部语音识别不能显示绿色。"""
        body = self._fn_body("_envComponents")
        self.assertIn("runtime:'语音识别运行环境不可用'", self.compact)
        self.assertIn(
            "if(o.runtime_ready===false)return{dot:'err',text:copy.runtime||copy.bad,detail:o.runtime_error||'',repairable:false,preparable:false}",
            body)
        self.assertIn("preparable:asr.preparable||align.preparable", body)

    def test_p357_start_button_uses_runtime_modal_before_prepare_modal(self):
        """P357: runtime_ready=false 应提示启动环境问题，而不是第一次使用下载模型。"""
        self.assertIn("function envRuntimeBlocked()", self.html)
        runtime_body = self._fn_body("envRuntimeBlocked")
        self.assertIn(
            "wh.installed_pkg!==false&&wh.model_ready!==false&&wh.runtime_ready===false",
            runtime_body)

        click = re.search(
            r"async function startJob\(\)\{(.*?)\n  // ── 本地路径模式",
            self.html, re.S)
        self.assertIsNotNone(click, "找不到开始按钮点击处理函数")
        click_body = click.group(1).replace(" ", "")
        self.assertIn("envRuntimeBlocked()", click_body)
        self.assertIn("openRuntimeBlockedModal()", click_body)
        self.assertLess(click_body.index("envRuntimeBlocked()"),
                        click_body.index("envNeedsPrepare()"),
                        "运行态不可用要先于一键准备判断")

        modal_body = self._fn_body("openRuntimeBlockedModal")
        self.assertIn("模型已经下载", modal_body)
        self.assertIn("普通桌面或Tauri环境", modal_body)
        for forbidden in ["第一次使用", "一键准备", "下载语音识别模型"]:
            self.assertNotIn(forbidden, modal_body)

    def test_p357_env_needs_prepare_keeps_runtime_out_of_download_path(self):
        """P357: runtime_ready=false 不是下载缺失，不能走 openPrepareModal。"""
        body = self._fn_body("envNeedsPrepare")
        self.assertIn(
            "if(wh.installed_pkg===false||wh.model_ready===false)returntrue",
            body)
        self.assertIn("if(wh.runtime_ready===false)returnfalse", body)
        self.assertNotIn("d.whisper&&d.whisper.installed===false", body)

    def test_p357_runtime_state_executes_as_blocked_not_prepare(self):
        """P357: 直接喂入三层状态，确认真实 JS 返回不可运行而不是一键准备。"""
        copy_match = re.search(r"const _COMP_COPY=\{.*?\n\};", self.html, re.S)
        self.assertIsNotNone(copy_match, "找不到 _COMP_COPY")
        js = "\n".join([
            copy_match.group(0),
            self._fn_source("_envComponents"),
            "let latestEnvStatus=null;",
            self._fn_source("envRuntimeBlocked"),
            self._fn_source("envNeedsPrepare"),
            """
const sample={
  ffmpeg:{installed:true},
  whisper:{
    installed:false,
    installed_pkg:true,
    model_ready:true,
    runtime_ready:false,
    runtime_error:"No Metal device available"
  },
  sensevoice:{installed_pkg:true,model_ready:true,ready:true},
  qwen_asr:{installed_pkg:true,model_ready:true,ready:true},
  qwen_aligner:{installed_pkg:true,model_ready:true,ready:true}
};
latestEnvStatus=sample;
const voice=_envComponents(sample).find(it=>it.key==="voice");
console.log(JSON.stringify({
  voiceDot:voice.dot,
  voiceText:voice.text,
  voicePreparable:voice.preparable,
  runtimeBlocked:envRuntimeBlocked(),
  needsPrepare:envNeedsPrepare()
}));
""",
        ])
        result = subprocess.run(["node", "-e", js], text=True,
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["voiceDot"], "err")
        self.assertIn("运行环境不可用", data["voiceText"])
        self.assertFalse(data["voicePreparable"])
        self.assertTrue(data["runtimeBlocked"])
        self.assertFalse(data["needsPrepare"])

    def test_p357_status_line_stays_visible_when_partly_blocked(self):
        """P357: 有组件异常时，顶部四个状态点仍要显示，不能整行藏掉。"""
        body = self._fn_body("renderEnv")
        self.assertNotIn(
            "$('envPill').classList.toggle('show',allReady)",
            body,
            "状态行不能再按 allReady 显隐，否则有红点时四个点全消失")
        self.assertIn(
            "$('envPill').classList.add('show')",
            body,
            "renderEnv 应展示状态行，让 3绿1红 露出来")

    def test_p357_render_env_keeps_dots_visible_on_runtime_block(self):
        """P357: runtime_ready=false 时 envPill 仍可见，四个点里只有语音识别红。"""
        copy_match = re.search(r"const _COMP_COPY=\{.*?\n\};", self.html, re.S)
        self.assertIsNotNone(copy_match, "找不到 _COMP_COPY")
        js = "\n".join([
            "function mkEl(){const s=new Set();return{className:'',innerHTML:'',"
            "classList:{add:c=>s.add(c),remove:c=>s.delete(c),"
            "toggle:(c,v)=>{v?s.add(c):s.delete(c);return s.has(c)},"
            "contains:c=>s.has(c)}};}",
            "const _els={envPill:mkEl(),envBanner:mkEl()};",
            "const $=id=>_els[id];",
            "const document={getElementById:()=>null,createElement:()=>({})};",
            "const window={};",
            "let _prepareBannerActive=false,_envCheckedOnce=false,latestEnvStatus=null;",
            copy_match.group(0),
            self._fn_source("_envComponents"),
            self._fn_source("renderEnvStatusLine"),
            self._fn_source("renderEnv"),
            """
const sample={ffmpeg:{installed:true},
 whisper:{installed:false,installed_pkg:true,model_ready:true,
          runtime_ready:false,runtime_error:"No Metal device available"},
 sensevoice:{installed_pkg:true,model_ready:true,ready:true},
 qwen_asr:{installed_pkg:true,model_ready:true,ready:true},
 qwen_aligner:{installed_pkg:true,model_ready:true,ready:true}};
renderEnv(sample);
const h=_els.envPill.innerHTML;
console.log(JSON.stringify({
 pillVisible:_els.envPill.classList.contains('show'),
 dotTotal:(h.match(/env-dot/g)||[]).length,
 errDots:(h.match(/env-dot err/g)||[]).length}));
""",
        ])
        result = subprocess.run(["node", "-e", js], text=True,
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data["pillVisible"], "runtime 挂掉时状态行仍要可见")
        self.assertEqual(data["dotTotal"], 4, "四个检测点都要在")
        self.assertEqual(data["errDots"], 1, "恰好一个红点（语音识别）")

    def test_p358_drawer_open_moves_main_action_to_background(self):
        """P358: 打开设置抽屉后，主按钮应退到背景，不再抢视觉焦点。"""
        self.assertIn(".app.drawer-open .main-action-panel", self.html)
        self.assertIn(".app.drawer-open .main-model-summary", self.html)
        self.assertIn("pointer-events:none", self.html)

        # 不锁死取 .app 的方式（裸 app 会 ReferenceError，须走 document.querySelector）；
        # 抽屉真能打开由 test_p358_drawer_actually_opens_at_runtime 行为验证兜底。
        open_body = self._fn_body("openSettingsDrawer")
        close_body = self._fn_body("closeSettingsDrawer")
        self.assertIn("classList.add('drawer-open')", open_body)
        self.assertIn("classList.remove('drawer-open')", close_body)

    def test_p358_select_uses_uniform_single_down_arrow(self):
        """P358: 输出方式 select 不能露出系统原生上下箭头。"""
        self.assertIn("appearance:none", self.compact)
        self.assertIn("-webkit-appearance:none", self.compact)
        self.assertIn("background-image:", self.compact)
        self.assertNotIn("appearance:auto", self.compact)

    def test_p358_drawer_actually_opens_at_runtime(self):
        """P358: 真实调用 openSettingsDrawer 必须真的打开抽屉。

        静态断言只锁住源码里有 drawer-open 字样，锁不住裸 `app` 未定义
        导致函数第一行就抛 ReferenceError、抽屉根本打不开（QA 真机实测：
        点设置按钮/左侧箭头都没反应）。这条真跑一遍。"""
        open_src = self._fn_source("openSettingsDrawer")
        close_m = re.search(r"function closeSettingsDrawer\(\)\{[^}]*\}", self.html)
        self.assertIsNotNone(close_m, "找不到 closeSettingsDrawer")
        js = "\n".join([
            "function mkEl(){const s=new Set();return{classList:{"
            "add:c=>s.add(c),remove:c=>s.delete(c),contains:c=>s.has(c)}};}",
            "const _els={app:mkEl(),settingsDrawer:mkEl(),settingsScrim:mkEl()};",
            "const document={querySelector:sel=>sel==='.app'?_els.app:null};",
            "const $=id=>_els[id]||null;",
            open_src,
            close_m.group(0),
            """
let threw=null;
try{openSettingsDrawer();}catch(e){threw=e.message;}
const opened={
  app:_els.app.classList.contains('drawer-open'),
  drawer:_els.settingsDrawer.classList.contains('open'),
  scrim:_els.settingsScrim.classList.contains('open')
};
closeSettingsDrawer();
const closed={
  app:_els.app.classList.contains('drawer-open'),
  drawer:_els.settingsDrawer.classList.contains('open')
};
console.log(JSON.stringify({threw,opened,closed}));
""",
        ])
        result = subprocess.run(["node", "-e", js], text=True,
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertIsNone(data["threw"],
                          f"openSettingsDrawer 抛错，抽屉打不开：{data['threw']}")
        self.assertTrue(data["opened"]["app"], "打开后 .app 应有 drawer-open")
        self.assertTrue(data["opened"]["drawer"], "打开后抽屉应 open")
        self.assertTrue(data["opened"]["scrim"], "打开后遮罩应 open")
        self.assertFalse(data["closed"]["app"], "关闭后 .app 不应残留 drawer-open")
        self.assertFalse(data["closed"]["drawer"], "关闭后抽屉应收起")

    def test_p359_running_job_card_has_percent_and_stop_button(self):
        """P359: 用户开始任务后，任务卡要同时看到数字百分比和停止任务按钮。"""
        add_body = self._fn_body("addJobCard")
        self.assertIn("job-card-percent", add_body)
        self.assertIn("id=\"pct-${jid}\"", add_body)
        self.assertIn("job-card-stop", add_body)
        self.assertIn("停止任务", add_body)
        self.assertIn("onclick=\"cancelCard('${jid}')\"", add_body)

        self.assertIn("function setJobProgress(jid,percent,stage,message,detail)", self.html)
        # **保护权交接（2026-08-20 · G9/P422 减法回炉）**：WebSocket 和查询接口
        # 共用固定公开状态，并由唯一入口 applyTaskState 渲染。
        self.assertIn("setJobProgress(jid,state.percent,state.phase,state.display_message", self.compact)
        # **保护权交接（2026-08-19 · G9/P422）**：运行中的 WebSocket 推的是公开快照（type:'state'），
        # 不再是 Worker 原始消息，因此与查询接口共用同一行渲染调用。
        # 「两条通道都能渲染进度」这件事保护范围没变，实现合一了。
        self.assertIn("if(msg&&msg.type==='state'&&msg.state)applyTaskState(jid,msg.state)", self.compact)

    def test_p359_done_card_hides_milestones_and_keeps_output_entry(self):
        """P359: 完成后里程碑圆点退场，只保留完成文案和成品入口。

        B2 起应用内不再提供下载，成品已交付到用户目录，入口改为
        「在文件夹中显示」，显隐只看后端的 can_show_output_folder。"""
        body = self._fn_body("renderCardDone")
        self.assertNotIn("finishMilestones(jid)", body)
        self.assertIn("hideMilestones(jid)", body)
        self.assertIn("已完成，成品已保存到目标文件夹", body)
        self.assertIn("can_show_output_folder", body)
        self.assertIn("job-card-result", self.html)

    def test_p341_alignment_fallback_notice_is_visible_on_job_card(self):
        """P341: Qwen3-FA 对齐失败后，粗时间兜底要显示给用户看。"""
        self.assertIn("job-card-quality", self.html)
        self.assertIn("时间轴对齐失败，已使用粗时间继续", self.html)
        self.assertIn("fa_quality", self.html)
        self.assertIn("alignment_fallback", self.html)
        self.assertIn("showAlignmentFallbackNotice", self.html)

    def test_tier_toggle_removed_and_main_tier_is_independent(self):
        for gone in [r"\btierPanel\b", r"\btierNote\b", r"#tierToggle"]:
            self.assertIsNone(re.search(gone, self.html),
                              "抽屉里的标准/精确已删除，不能留下悬空引用")
        self.assertIsNone(
            re.search(r"(?<!main)\btierToggle\b", self.html),
            "抽屉里的 tierToggle 已删除，但 mainTierToggle 必须保留")
        main_body = re.search(
            r"\$\('mainTierToggle'\)\.addEventListener\('click',e=>\{(.*?)\n\}\);",
            self.html, re.S)
        self.assertIsNotNone(main_body, "mainTierToggle handler 必须存在")
        self.assertIsNone(
            re.search(r"(?<!main)\btierToggle\b", main_body.group(1)),
            "mainTierToggle 不应再同步已删除的 tierToggle")

    def test_speaker_toggle_mounted_at_bottom_of_voice_module(self):
        self.assertIn('id="voiceSpeakerPanel"', self.html)
        self.assertIn('id="speakerToggle"', self.html)
        panel = re.search(
            r'<div class="card settings-panel" id="voiceSpeakerPanel">(.*?)</div>\s*</div>',
            self.html, re.S)
        self.assertIsNotNone(panel, "说话人检测面板必须存在")
        self.assertEqual(
            panel.group(1).count("说话人检测"), 1,
            "面板只保留控件名称，不能再显示重复的小标题")
        self.assertIn("识别模型、下载模型、说话人检测", self.html,
                      "语音识别模块折叠时也要让用户知道里面有说话人检测")
        self.assertIn("place('accBodyVoice',['voiceSpeakerPanel']",
                      self.compact,
                      "说话人检测应挂到语音识别模块底部")

    def test_subtitle_marker_note_moved_to_output_bottom(self):
        self.assertIn(
            "place('accBodyOutput',['outputPanel','styleSubSection','subtitleMarkerNote']",
            self.compact,
            "字幕标记说明必须放到字幕与输出模块底部")


class TestApplePolishCraftBatch(unittest.TestCase):
    """Apple 极简工艺收口批（2026-07-05）静态护栏。

    锁住本批四件事，防止将来回填：去 emoji/符号、圆角进变量、颜色进变量、
    5 个 API 常驻 label、env dot 未被 ②-3 中性化。③b 字符图标 SVG 统一仍
    后置，此处顺带锁住那批字符图标未被提前删。
    """

    @classmethod
    def setUpClass(cls):
        cls.html = _HTML.read_text(encoding="utf-8")
        cls.compact = cls.html.replace(" ", "")

    def test_removed_emoji_symbols_absent(self):
        # 本批清掉的用户可见 emoji/符号：全文件（含 <script>）不应再出现。
        for ch in ("🧪", "💡", "✅", "⚠", "ℹ", "✓"):
            self.assertNotIn(ch, self.html, f"回填了本批已删的 emoji/符号：{ch}")

    def test_localpath_hint_emdash_replaced(self):
        # 本地路径提示：em-dash 换成逗号，旧的 "💡 适用于" 前缀已删。
        self.assertNotIn("💡 适用于", self.html)
        self.assertIn("适用于 5GB 以上的大文件，无需上传", self.html)

    def test_border_radius_only_vars_or_exceptions(self):
        # 裸 border-radius:Npx 只允许例外档：细条 1/2/3px + .env-item 胶囊 20px。
        # 其余一律走 var(--r-sm/md/lg)。
        raw = set(re.findall(r"border-radius:\s*(\d+)px", self.html))
        leaked = sorted(raw - {"1", "2", "3", "20"}, key=int)
        self.assertEqual(
            leaked, [],
            f"出现未收敛的裸圆角 {leaked}px，应改用 var(--r-sm/md/lg)")
        self.assertIn("--r-sm:8px", self.compact)
        self.assertIn("--r-md:12px", self.compact)
        self.assertIn("--r-lg:16px", self.compact)
        self.assertIn("border-radius:var(--r-sm)", self.compact)

    def test_five_api_field_labels_present(self):
        # 5 个 API 输入框必须各有常驻 label。不再限定 label 总数——平台/模型下拉
        # 也补了 label 让整行对齐（见 API 行对齐修复），所以只查这 5 个 input 的 label 在。
        for target in ("apiKeyInput", "apiCustomModel", "apiModelName",
                       "apiEndpoint", "apiName"):
            self.assertIn(f'for="{target}"', self.html,
                          f"缺 {target} 的常驻 label")
        # url/localPath 已各自带说明 span，不得再加重复 label。
        for not_labeled in ("urlInput", "localPathInput"):
            self.assertNotIn(f'class="api-field-label" for="{not_labeled}"', self.html)

    def test_api_row_platform_model_labeled_for_alignment(self):
        # API 行对齐修复：平台/模型下拉也包进 .api-field 且有 label，让整行统一
        # “label 在上、控件在下”，命名/自定义模型/模型名/平台地址不再比裸下拉低。
        self.assertIn('for="apiPlatform"', self.html, "平台下拉缺 label")
        self.assertIn('for="apiModel"', self.html, "模型下拉缺 label")
        # 模型下拉的容器随 setApiInlineFieldVisible('apiModel',...) 显隐，避免藏起来时留孤儿 label。
        self.assertIn('id="apiModelField"', self.html)
        self.assertIn("setApiInlineFieldVisible('apiModel'", self.html)

    def test_colors_use_vars_not_duplicate_hex(self):
        # 手写重复红绿已改成变量（消灭同色两值）。
        self.assertNotIn("#ff453a", self.html)
        self.assertNotIn("#30d158", self.html)

    def test_env_dot_ok_not_neutralized(self):
        # ②-3 就绪态中性化不在本批：.env-dot.ok 仍是绿色，dot 语义未动。
        self.assertIn(".env-dot.ok{background:var(--green)}", self.compact)

    def test_stage3b_icon_chars_preserved(self):
        # ③b 字符图标 SVG 统一仍后置，本批不得提前删这些字符图标。
        for ch in ("≡", "▶", "⚙"):
            self.assertIn(ch, self.html, f"③b 后置图标被提前删了：{ch}")


class TestModelSceneLabelCorrectness(unittest.TestCase):
    """三档模型文案定位护栏（Pxxx，2026-07-05）。

    设计文档定：large-v3 = 高准确度（音频清晰时字幕更准），large-v2 才是抗噪/复杂背景。
    施工曾把 large-v3 写成"复杂背景更稳"（与设计相反，且与 large-v2 撞车）。
    此护栏锁死 large-v3 不得再标"复杂背景"，large-v2 的"复杂背景更稳"保留。
    """

    @classmethod
    def setUpClass(cls):
        cls.html = _HTML.read_text(encoding="utf-8")
        cls.compact = cls.html.replace(" ", "")

    def test_large_v3_not_labeled_complex_background(self):
        # large-v3 三处文案都不许再出现"复杂背景"。
        self.assertNotIn('"large-v3":"复杂背景', self.compact,
                         "large-v3 又被标成复杂背景了（它需要干净音频，抗噪差）")
        self.assertIn('"large-v3":"高准确度"', self.compact)
        self.assertIn("音频清晰时字幕更准", self.html)

    def test_large_v2_keeps_noise_resistant_label(self):
        # large-v2 才是抗噪那个，"复杂背景更稳"必须保留。
        self.assertIn('"large-v2":"日常推荐（默认下载，复杂背景更稳）"', self.compact)


class TestC3VisualQABatch(unittest.TestCase):
    """C3 收尾视觉 QA（2026-07-05）护栏：字幕样式字段名、API 行拆两行、切平台清残留。"""

    @classmethod
    def setUpClass(cls):
        cls.html = _HTML.read_text(encoding="utf-8")

    def test_subtitle_style_selects_have_field_names(self):
        # B（Codex 第 6 条）：字幕样式区 5 个下拉各有字段名（字体/字号/颜色/描边/粗细）。
        self.assertEqual(self.html.count('class="style-field"'), 5)
        for name in ("字体", "字号", "颜色", "描边", "粗细"):
            self.assertIn(f">{name}</span>", self.html, f"字幕样式缺字段名：{name}")

    def test_api_config_split_into_multiple_rows(self):
        # C（Codex 第 5 条）：API 配置拆行——Key 行 + 平台/模型行 + 字段/动作行，≥3 个 api-line。
        self.assertGreaterEqual(self.html.count('class="api-line"'), 3)

    def test_platform_change_clears_stale_custom_model(self):
        # A（P344）：切平台时清掉旧模型选项，避免旧 __custom__ 残留误显自定义模型框。
        self.assertIn("每次切平台清掉旧模型选项", self.html)


class TestC3Fix2ApiModelName(unittest.TestCase):
    """Fix 2（2026-07-06）：custom 平台无模型列表时，用户在 apiModelName 手填的
    模型名必须存进 entry.model。此前添加/保存只读 apiModel/apiCustomModel，忽略
    apiModelName，空值还兜底成 gpt-4o。锁三处（add / _getFormEntry / editKey）
    走同一读模型 helper，且空值提示不兜 gpt-4o。"""

    @classmethod
    def setUpClass(cls):
        cls.html = _HTML.read_text(encoding="utf-8")
        cls.compact = cls.html.replace(" ", "")

    def _fn_body(self, name):
        m = re.search(r"function " + name + r"\([^)]*\)\{(.*?)\n\}",
                      self.html, re.S)
        self.assertIsNotNone(m, f"找不到函数 {name}")
        return m.group(1).replace(" ", "")

    def test_shared_model_reader_helper_exists(self):
        """统一读模型 helper：custom 无列表读 apiModelName，判自身 inline 可见性。"""
        body = self._fn_body("_readModelFromForm")
        self.assertIn("apiModelName", body, "helper 必须读 apiModelName")
        self.assertIn("apiModelNameField", body,
                      "helper 必须判 apiModelNameField 可见性（自身 inline style）")
        self.assertNotIn("getComputedStyle", body,
                         "可见性判自身 inline style，不用 getComputedStyle（抽屉折叠会误判）")
        self.assertIn("__custom__", body, "helper 仍要处理 apiModel 选「其他模型…」")

    def test_getformentry_uses_shared_reader(self):
        """_getFormEntry 用同一 helper，脏检查才能识别 apiModelName 变化。"""
        body = self._fn_body("_getFormEntry")
        self.assertIn("_readModelFromForm", body,
                      "_getFormEntry 必须用同一读模型 helper")

    def test_add_button_uses_shared_reader(self):
        """添加/保存按钮用同一 helper 写 entry.model。"""
        # apiAddBtn 是箭头 handler，_fn_body 匹配不到；helper 被定义+两处调用 ≥3 次。
        self.assertGreaterEqual(self.compact.count("_readModelFromForm"), 3,
                                "helper 应被 _getFormEntry 和添加按钮共同调用")

    def test_editkey_uses_restore_edit_model_for_backfill(self):
        """P2 修复（Codex 2026-07-06 第三轮）：editKey 同步段必须用 _restoreEditModel()
        统一回填，替代只手写 apiModelName。否则下拉型编辑（普通平台 claude/gpt，或 custom
        签名匹配有真实列表）打开后下拉停在 onPlatformChange 默认选的 best model，要等 fetch
        返回才选回 k.model，慢网窗口点保存会误存 best model。_restoreEditModel 同时覆盖
        下拉（选中 k.model）和手填框（回填 apiModelName），且必须在 onPlatformChange 之后、
        fetchAndFillModels 之前（同步立即回填，不等 fetch）。"""
        body = self._fn_body("editKey")
        opc_idx = body.find("onPlatformChange()")
        restore_idx = body.find("_restoreEditModel()")
        fetch_idx = body.find("fetchAndFillModels()")
        self.assertNotEqual(restore_idx, -1,
                            "editKey 同步段必须调 _restoreEditModel 统一回填下拉/手填框")
        self.assertLess(opc_idx, restore_idx,
                        "_restoreEditModel 要在 onPlatformChange 之后")
        self.assertLess(restore_idx, fetch_idx,
                        "_restoreEditModel 要在 fetchAndFillModels 之前（同步立即回填）")

    def test_restore_edit_model_handles_apimodelname(self):
        """_restoreEditModel 对 apiModelNameField 可见的情况回填 apiModelName，
        不再往隐藏的 apiModel 塞「已下线」幽灵选项。"""
        body = self._fn_body("_restoreEditModel")
        self.assertIn("apiModelName", body,
                      "_restoreEditModel 必须处理 apiModelNameField 可见时的回填")

    def test_no_gpt4o_fallback(self):
        """custom 无模型列表且模型名为空时提示，不能兜底成 gpt-4o。"""
        self.assertNotIn("entry.model='gpt-4o'", self.compact,
                         "空模型名必须提示「请输入模型名称」，不能兜底成 gpt-4o")

    def test_fetch_empty_or_fail_clears_custom_registry(self):
        """根因修复（Codex 2026-07-06）：后端 /api/backends 默认给 custom 一个
        custom-default，所以 State._llmModels.custom 初始就非空。/api/llm-models
        返回空或 catch 失败时，custom 必须清掉旧模型注册，否则 onPlatformChange
        仍认为有列表、apiModelNameField 不显示，保存会误存 custom-default 而不是
        用户手填的模型名。空返回分支 + catch 分支两处都要清。"""
        body = self._fn_body("fetchAndFillModels")
        self.assertGreaterEqual(
            body.count("if(platform==='custom')_clearCustomModelRegistry()"), 2,
            "空返回和 catch 失败两个分支，custom 都要清掉旧模型注册（含默认 custom-default）")

    def test_fetch_clears_registry_before_reevaluating(self):
        """清空必须发生在 onPlatformChange 之前——清了旧列表，onPlatformChange 才会
        重新判定为「无列表」并显示 apiModelNameField。"""
        body = self._fn_body("fetchAndFillModels")
        del_idx = body.find("if(platform==='custom')_clearCustomModelRegistry()")
        self.assertNotEqual(del_idx, -1, "必须有 custom 清空逻辑")
        # 清空之后仍要重算平台状态并回填编辑态
        self.assertIn("onPlatformChange();_restoreEditModel();",
                      body[del_idx:],
                      "清空后必须重算 onPlatformChange 并回填 _restoreEditModel")
        # 成功分支不受影响：仍写入拉取到的模型列表
        self.assertIn("State._llmModels[platform]=d.models", body,
                      "成功分支仍写入模型列表，清空只发生在空/失败分支")

    def test_restore_skips_ghost_option_when_field_visible(self):
        """编辑 custom 无列表 key + 拉取失败后：_restoreEditModel 判 apiModelNameField
        可见就回填 k.model 并 return，不再往隐藏的 apiModel 塞「已下线」幽灵选项。"""
        body = self._fn_body("_restoreEditModel")
        vis_idx = body.find("apiModelNameField")
        ghost_idx = body.find("insertBefore")
        self.assertNotEqual(vis_idx, -1, "必须先判 apiModelNameField 可见")
        self.assertNotEqual(ghost_idx, -1, "有列表分支仍会塞已下线选项")
        self.assertLess(vis_idx, ghost_idx,
                        "apiModelNameField 可见分支（含 return）必须在塞幽灵选项 insertBefore 之前")

    def test_onplatformchange_treats_custom_default_as_placeholder(self):
        """慢网窗口修复（Codex 2026-07-06 第二轮）：custom-default 是后端 /api/backends
        给 custom 的占位符，不是真实模型。onPlatformChange 必须把它当「无列表」处理——
        显示 apiModelNameField 手填框、不进 apiModel 下拉。这样 llm-models 请求还没回来
        的慢网窗口里，界面也已经是手填态，用户此刻点添加不会误存 custom-default。"""
        body = self._fn_body("onPlatformChange")
        self.assertIn("filter(m=>m&&m.id!=='custom-default')", body,
                      "onPlatformChange 必须把 custom-default 从 custom 模型列表中过滤掉")
        self.assertIn("models=[]", body,
                      "custom 模型缓存不可信时按无列表处理（models 置空→显示手填框）")

    def test_custom_default_never_saved_as_model(self):
        """保存路径硬兜底：即使 UI 哪天又漏了，custom-default 也绝不能进 entry.model。
        _isCustomPlaceholder 被 _readModelFromForm 和 apiAddBtn 的 needModel 共用。"""
        helper = self._fn_body("_isCustomPlaceholder")
        self.assertIn("custom-default", helper,
                      "_isCustomPlaceholder 判 apiModel.value==='custom-default'")
        read_body = self._fn_body("_readModelFromForm")
        self.assertIn("_isCustomPlaceholder", read_body,
                      "_readModelFromForm 必须用 _isCustomPlaceholder 兜底回退手填")
        # 定义 1 + _readModelFromForm 1 + apiAddBtn needModel 1 ≥ 3
        self.assertGreaterEqual(self.compact.count("_isCustomPlaceholder"), 3,
                                "_isCustomPlaceholder 应被读模型和 needModel 共同使用")

    def test_fetch_has_stale_request_guard(self):
        """stale request guard：旧请求晚返回不能覆盖新状态。fetchAndFillModels 开头
        快照 platform/key/endpoint，await 回来后写 State/删注册前先校验这三者仍是当时
        那次请求的，不一致就直接丢弃（return），不动 State、不 onPlatformChange。"""
        body = self._fn_body("fetchAndFillModels")
        self.assertIn("_isStale", body, "fetchAndFillModels 必须有 stale request guard")
        self.assertIn("apiPlatform').value!==platform", body,
                      "stale guard 要校验平台未变")
        self.assertIn("apiKeyInput').value.trim()!==key", body,
                      "stale guard 要校验 key 未变")
        self.assertIn("apiEndpoint').value.trim()!==endpoint", body,
                      "stale guard 要校验 custom endpoint 未变")

    def test_custom_model_cache_bound_to_key_and_endpoint(self):
        """第三轮修复：custom 的真实模型列表不能只按 platform 缓存。
        同一个 custom 平台，endpoint/key A 拉到的模型不能在 endpoint/key B 的慢网窗口里
        继续显示并被保存。缓存必须绑定当前 key + endpoint。"""
        match_body = self._fn_body("_customModelCacheMatches")
        self.assertIn("sig.key===key", match_body,
                      "custom 模型缓存签名必须绑定 key")
        self.assertIn("sig.endpoint===endpoint", match_body,
                      "custom 模型缓存签名必须绑定 endpoint")

        stale_body = self._fn_body("_customModelCacheStaleForForm")
        self.assertIn("$('apiPlatform').value==='custom'", stale_body,
                      "只有 custom 平台需要用 key + endpoint 判断模型列表是否过期")
        self.assertIn("!_customModelCacheMatches($('apiKeyInput').value.trim(),$('apiEndpoint').value.trim())",
                      stale_body,
                      "custom 当前表单签名不匹配时，旧模型列表必须视为过期")

        platform_body = self._fn_body("onPlatformChange")
        self.assertIn("_customModelCacheStaleForForm()", platform_body,
                      "onPlatformChange 只有在 custom 模型缓存签名匹配当前表单时才可用旧列表")

        fetch_body = self._fn_body("fetchAndFillModels")
        self.assertIn("State._llmModelSigs[platform]={key,endpoint}", fetch_body,
                      "拉取成功后必须记录 custom 模型列表对应的 key + endpoint 签名")
        self.assertIn("!_customModelCacheMatches(key,endpoint)", fetch_body,
                      "发起 custom 拉取前，签名不匹配要先清掉旧列表，关闭慢网窗口")

    def test_save_path_treats_stale_custom_cache_as_manual_model(self):
        """保存路径也要兜底：即使极端情况下 UI 还没重算，custom 旧列表签名不匹配时
        也不能从 apiModel 下拉读旧模型，必须回退 apiModelName 并要求用户填写。"""
        read_body = self._fn_body("_readModelFromForm")
        self.assertIn("_customModelCacheStaleForForm()", read_body,
                      "_readModelFromForm 必须把 custom 过期缓存当成手填态")
        # 定义 1 + onPlatformChange 1 + _readModelFromForm 1 + apiAddBtn needModel 1
        self.assertGreaterEqual(self.compact.count("_customModelCacheStaleForForm()"), 4,
                                "过期 custom 缓存必须同时约束 UI 重算和保存 needModel")

    def test_clearing_custom_registry_clears_signature_too(self):
        """清 custom 模型注册时必须同时清签名。只删 State._llmModels.custom 会留下
        一个看似匹配的旧签名，后续列表重建时容易误判为可信。"""
        body = self._fn_body("_clearCustomModelRegistry")
        self.assertIn("deleteState._llmModels.custom", body,
                      "清注册要删 custom 模型列表")
        self.assertIn("deleteState._llmModelSigs.custom", body,
                      "清注册要删 custom 模型签名")

    def test_editkey_reevaluates_after_setting_key_endpoint(self):
        """N2 修复：editKey 里 onPlatformChange 必须在 apiKeyInput/apiEndpoint 赋值之后跑。
        否则第一次重画用的是上一次残留的表单值判断「显示下拉还是手填框」，可见性不准，
        编辑已存 custom key 时手填框该显示却没显示、k.model 回填被跳过。"""
        body = self._fn_body("editKey")
        key_idx = body.find("apiKeyInput').value=k.key")
        ep_idx = body.find("apiEndpoint').value=k.api_endpoint")
        opc_idx = body.find("onPlatformChange()")
        self.assertNotEqual(key_idx, -1, "editKey 要设 apiKeyInput")
        self.assertNotEqual(ep_idx, -1, "editKey 要设 apiEndpoint")
        self.assertNotEqual(opc_idx, -1, "editKey 要调 onPlatformChange")
        self.assertLess(key_idx, opc_idx, "apiKeyInput 赋值必须在 onPlatformChange 之前")
        self.assertLess(ep_idx, opc_idx, "apiEndpoint 赋值必须在 onPlatformChange 之前")

    def test_fetch_pre_clear_restores_edit_model(self):
        """N1 修复：fetchAndFillModels 发起前若签名不匹配，先 _clearCustomModelRegistry()
        + onPlatformChange() 重画，这一步也必须配套 _restoreEditModel() 回填编辑态——
        否则编辑 custom key 的慢网窗口里手填框会空着，用户点保存被误报「请输入模型名称」。
        与成功/失败分支的 onPlatformChange();_restoreEditModel(); 保持一致。"""
        body = self._fn_body("fetchAndFillModels")
        self.assertIn(
            "_clearCustomModelRegistry();onPlatformChange();_restoreEditModel();",
            body,
            "发起前 clear+重画后必须配套 _restoreEditModel 回填编辑态")


if __name__ == "__main__":
    unittest.main()
