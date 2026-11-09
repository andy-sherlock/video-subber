"""V3.09 A 批：Whisper 模型从 5 个替换为 3 个。

旧型号 tiny/base/small/medium 全删，新增 large-v3-turbo / large-v2，
保留 large-v3，默认模型改为 large-v2。

覆盖：model_manager.MODELS、config_manager 默认值+旧配置兜底、worker 校验白名单/
预估时间/内存不足提示、whisper_runner 的 MLX 映射、env_check 缓存检测、前端模型列表。
"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

NEW_MODELS = {"large-v3-turbo", "large-v2", "large-v3"}
OLD_MODELS = {"tiny", "base", "small", "medium"}


class TestModelManagerThreeModels(unittest.TestCase):
    """model_manager.MODELS 只剩 3 个新型号，且各有下载源和大小。"""

    def test_models_dict_has_exactly_three_new(self):
        from backend.model_manager import MODELS
        self.assertEqual(set(MODELS.keys()), NEW_MODELS)

    def test_no_old_models_remain(self):
        from backend.model_manager import MODELS
        for old in OLD_MODELS:
            self.assertNotIn(old, MODELS, f"旧型号 {old} 应已删除")

    def test_each_model_has_repo_and_size(self):
        from backend.model_manager import MODELS
        for mid, info in MODELS.items():
            self.assertTrue(info.get("repo"), f"{mid} 缺少下载源 repo")
            self.assertTrue(info.get("size"), f"{mid} 缺少大小")
            self.assertEqual(info.get("name"), mid)


class TestModelDownloadRepoByPlatform(unittest.TestCase):
    """下载源按平台分流：Mac(mlx) → mlx-community，Windows/Linux(faster) → Systran/deepdml。"""

    def test_mac_uses_mlx_repos(self):
        from backend.model_manager import _build_models
        models = _build_models("mlx")
        self.assertEqual(set(models.keys()), NEW_MODELS)
        for mid, info in models.items():
            self.assertIn("mlx-community", info["repo"],
                          f"Mac 端 {mid} 应连 mlx 版，实际 {info['repo']}")

    def test_windows_uses_faster_repos(self):
        from backend.model_manager import _build_models
        models = _build_models("faster")
        self.assertEqual(set(models.keys()), NEW_MODELS)
        for mid, info in models.items():
            self.assertIn("faster", info["repo"],
                          f"Windows 端 {mid} 应连 faster 版，实际 {info['repo']}")
            self.assertNotIn("mlx", info["repo"])

    def test_faster_repos_match_faster_whisper_builtin(self):
        """Windows 下载源必须与 faster-whisper 实际加载源一致，否则下一个跑另一个。"""
        from faster_whisper.utils import _MODELS
        from backend.model_manager import _build_models
        models = _build_models("faster")
        for mid, info in models.items():
            self.assertEqual(
                info["repo"], _MODELS[mid],
                f"{mid} 下载源 {info['repo']} ≠ faster-whisper 加载源 {_MODELS[mid]}")


class TestConfigDefaultModel(unittest.TestCase):
    """默认模型从 medium 改为 large-v2。"""

    def test_default_model_is_large_v2(self):
        from backend.config_manager import DEFAULT_CONFIG
        self.assertEqual(DEFAULT_CONFIG["model"], "large-v2")


class TestLegacyModelCoercion(unittest.TestCase):
    """旧 config 里存的已删型号，加载时兜底成 large-v2，不报错。"""

    def test_coerce_legacy_to_default(self):
        from backend.config_manager import coerce_model
        for old in OLD_MODELS:
            self.assertEqual(coerce_model(old), "large-v2",
                             f"旧型号 {old} 应兜底为 large-v2")

    def test_coerce_valid_passthrough(self):
        from backend.config_manager import coerce_model
        for m in NEW_MODELS:
            self.assertEqual(coerce_model(m), m, f"有效型号 {m} 应原样放行")

    def test_load_config_coerces_stored_medium(self):
        """模拟老 config.json 里存着 medium → load_config 返回 large-v2。"""
        import backend.config_manager as cm
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text(json.dumps({"model": "medium"}), encoding="utf-8")
            with patch.object(cm, "CONFIG_PATH", p):
                cfg = cm.load_config()
        self.assertEqual(cfg["model"], "large-v2")


class TestWorkerValidation(unittest.TestCase):
    """worker._validate_config 只认 3 个新型号；旧型号经兜底后不报错。"""

    def test_validate_accepts_new_models(self):
        from backend.worker import _validate_config
        for m in NEW_MODELS:
            _validate_config({"model": m})  # 不应抛错

    def test_validate_accepts_none(self):
        from backend.worker import _validate_config
        _validate_config({"model": None})  # 不应抛错

    def test_validate_rejects_garbage(self):
        from backend.worker import _validate_config
        with self.assertRaises(ValueError):
            _validate_config({"model": "totally-not-a-model"})

    def test_old_config_does_not_error_after_coercion(self):
        """复现 worker.main() 的顺序：先兜底再校验，老 medium 配置不崩。"""
        from backend.config_manager import coerce_model
        from backend.worker import _validate_config
        config = {"model": "medium"}
        config["model"] = coerce_model(config["model"])
        _validate_config(config)  # 不应抛错
        self.assertEqual(config["model"], "large-v2")


class TestWorkerModelTimeEstimate(unittest.TestCase):
    """worker 的预估时间表只列 3 个新型号。"""

    def test_model_time_has_new_models(self):
        from backend.worker import _MODEL_TIME_EST
        self.assertEqual(set(_MODEL_TIME_EST.keys()), NEW_MODELS)


class TestFriendlyErrorMessage(unittest.TestCase):
    """内存不足提示不再提已删除的 tiny/base。"""

    def test_oom_message_no_old_model_names(self):
        from backend.worker import _friendly_error
        msg = _friendly_error("MemoryError: cannot allocate memory")
        self.assertNotIn("tiny", msg)
        self.assertNotIn("base", msg)


class TestMlxWhisperModels(unittest.TestCase):
    """whisper_runner 的 MLX 映射只剩 3 个新型号。"""

    def test_mlx_models_three_new(self):
        from backend.core.whisper_runner import MLX_WHISPER_MODELS
        self.assertEqual(set(MLX_WHISPER_MODELS.keys()), NEW_MODELS)

    def test_mlx_no_old_models(self):
        from backend.core.whisper_runner import MLX_WHISPER_MODELS
        for old in OLD_MODELS:
            self.assertNotIn(old, MLX_WHISPER_MODELS)


class TestEnvCheckModelDetection(unittest.TestCase):
    """env_check 复用后端 ready 判断，不再自己扫 Hugging Face cache。"""

    def test_detects_ready_new_models(self):
        import backend.env_check as ec
        with patch("backend.model_manager._find_ready_model_dir",
                   side_effect=lambda mid: Path("/ready") / mid):
            models = ec._cached_whisper_models()
        self.assertEqual(set(models), NEW_MODELS)

    def test_non_ready_models_ignored(self):
        import backend.env_check as ec
        with patch("backend.model_manager._find_ready_model_dir", return_value=None):
            models = ec._cached_whisper_models()
        self.assertEqual(models, [])

    def test_check_whisper_splits_pkg_and_model_ready(self):
        """C3.5 收口：check_whisper 与其余三组件同口径，拆 installed_pkg / model_ready，
        installed = installed_pkg AND model_ready。包在但模型缺时 installed 必须 False——
        前端据此判定'模型缺失可修复'而非'包缺失不可修复'。"""
        import backend.env_check as ec
        with patch("backend.env_check.sys.platform", "darwin"), \
             patch("backend.env_check.platform_machine", return_value="arm64"), \
             patch("backend.env_check.importlib.util.find_spec", return_value=object()), \
             patch("backend.env_check._cached_whisper_models", return_value=[]):
            r = ec.check_whisper()
        self.assertTrue(r["installed_pkg"], "包在时 installed_pkg 应为 True")
        self.assertFalse(r["model_ready"], "模型缺时 model_ready 应为 False")
        self.assertFalse(r["installed"], "包在但模型缺时 installed 必须 False")

    def test_check_whisper_pkg_missing_marks_installed_pkg_false(self):
        """包缺失（find_spec 判空）→ installed_pkg False；前端据此不显示一键修复。"""
        import backend.env_check as ec
        with patch("backend.env_check.sys.platform", "darwin"), \
             patch("backend.env_check.platform_machine", return_value="arm64"), \
             patch("backend.env_check.importlib.util.find_spec", return_value=None):
            r = ec.check_whisper()
        self.assertFalse(r["installed_pkg"])
        self.assertFalse(r["model_ready"])
        self.assertFalse(r["installed"])

    def test_check_whisper_marks_mlx_runtime_unusable(self):
        """包和模型都在，但当前进程没有 Metal 时，不能继续报 installed=True。"""
        import backend.env_check as ec

        def fake_import_module(name):
            if name == "mlx.core":
                raise RuntimeError("No Metal device available")
            raise AssertionError(f"unexpected import: {name}")

        with patch("backend.env_check.sys.platform", "darwin"), \
             patch("backend.env_check.platform_machine", return_value="arm64"), \
             patch("backend.env_check.importlib.util.find_spec", return_value=object()), \
             patch("backend.env_check._cached_whisper_models", return_value=["large-v3"]), \
             patch("backend.env_check.importlib.import_module",
                   side_effect=fake_import_module):
            r = ec.check_whisper()

        self.assertTrue(r["installed_pkg"])
        self.assertTrue(r["model_ready"])
        self.assertFalse(r["runtime_ready"])
        self.assertIn("No Metal device available", r["runtime_error"])
        self.assertFalse(r["installed"])


class TestFrontendModelList(unittest.TestCase):
    """前端 index.html 的模型列表/默认值/文案同步到 3 个新型号。"""

    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).parent.parent / "frontend" / "index.html").read_text(encoding="utf-8")

    def test_models_array_has_three_new(self):
        m = re.search(r"const MODELS=\[(.*?)\]", self.html)
        self.assertIsNotNone(m, "找不到 const MODELS 数组")
        arr = m.group(1)
        for new in NEW_MODELS:
            self.assertIn(f'"{new}"', arr, f"前端 MODELS 缺少 {new}")
        for old in OLD_MODELS:
            self.assertNotIn(f'"{old}"', arr, f"前端 MODELS 仍含旧型号 {old}")

    def test_default_model_is_large_v2(self):
        self.assertIn("const DEFAULT_MODEL='large-v2'", self.html)
        self.assertIn('model:DEFAULT_MODEL', self.html)
        self.assertNotIn('model:"medium"', self.html)

    def test_old_hint_text_gone(self):
        self.assertNotIn("medium 约 1.5GB", self.html)

    def test_scene_labels_present(self):
        """每个模型圆点下标注适用场景。"""
        for kw in ("速度最快", "抗杂音", "复杂背景更稳"):
            self.assertIn(kw, self.html, f"模型场景标注缺少「{kw}」")

    def test_arrow_bar_removed(self):
        """旧的「快→慢/低准确度→高准确度」箭头条已移除。"""
        self.assertNotIn("model-arrow-bar", self.html)
        self.assertNotIn("低准确度", self.html)

    def test_missing_dot_background_opaque(self):
        """未下载圆圈用不透明背景盖住连线，横线不能穿过圆圈内部。"""
        m = re.search(r"\.model-dot-circle\.missing\{([^}]*)\}", self.html)
        self.assertIsNotNone(m, "找不到 .model-dot-circle.missing 样式")
        self.assertNotIn("transparent", m.group(1),
                         "未下载圆圈背景仍是 transparent，横线会穿过圆圈")

    def test_dot_line_aligned_by_js(self):
        """连线两端用 JS 测量首尾圆圈中心定位，不再贯穿外伸。"""
        self.assertIn("alignModelLine", self.html)
        self.assertIn("--dot-line-left", self.html)

    def test_scene_label_emphasized(self):
        """场景标注用深色+加粗强调，不再是又小又灰的次要文字。"""
        m = re.search(r"\.model-dot-scene\{([^}]*)\}", self.html)
        self.assertIsNotNone(m, "找不到 .model-dot-scene 样式")
        css = m.group(1)
        self.assertIn("font-weight", css, "场景标注应加粗强调")
        self.assertNotIn("var(--sub)", css, "场景标注不应再用次要灰色")

    def test_download_ui_no_fake_cancel_button(self):
        """P312 后不能保留旧假取消接口和旧提示文案。"""
        self.assertNotIn("model-progress-cancel", self.html)
        self.assertNotIn("cancelModelDownload(", self.html)
        self.assertNotIn("下载中，关闭应用后下次会继续", self.html)

    def test_download_ui_updates_existing_progress_row(self):
        """P309: 下载轮询不能每次 innerHTML 重建进度区，否则动画会反复闪。"""
        self.assertNotIn("area.innerHTML=downloading.map", self.html)
        self.assertIn("updateModelProgressRow", self.html)

    def test_downloading_dot_is_static(self):
        """P309: 下载中的模型圆点应静态高亮，不再无限呼吸闪烁。"""
        m = re.search(r"\.model-dot-circle\.downloading\{([^}]*)\}", self.html)
        self.assertIsNotNone(m, "找不到 .model-dot-circle.downloading 样式")
        self.assertNotIn("animation", m.group(1))

    def test_download_ui_has_real_stop_button(self):
        """P312: C 阶段恢复停止按钮，但必须走 stopModelDownload 真停止接口。"""
        self.assertIn("model-progress-stop", self.html)
        self.assertIn("stopModelDownload(", self.html)
        self.assertNotIn("cancelModelDownload(", self.html)

    def test_download_ui_shows_speed_and_delete_action(self):
        """P311/P313: 下载中显示速度，已下载模型提供删除入口。"""
        self.assertIn("speed_text", self.html)
        self.assertIn("deleteModel(", self.html)
        self.assertIn("删除模型", self.html)
        self.assertIn("currentMd.managed", self.html)

    def test_delete_model_does_not_depend_on_native_confirm(self):
        """Tauri WebView 里 native confirm 可能不弹，删除模型必须用页面内二次确认。"""
        m = re.search(r"async function deleteModel\(modelId\)\{(?P<body>.*?)\n\}", self.html, re.S)
        self.assertIsNotNone(m, "找不到 deleteModel 函数")
        body = m.group("body")
        self.assertNotIn("confirm(", body)
        self.assertIn("pendingDeleteModel", body)
        self.assertIn("确认删除", self.html)

    def test_restored_interrupted_card_can_be_dismissed(self):
        """P315: 恢复出的 interrupted 卡片关闭按钮走 dismiss，不被普通取消确认挡住。"""
        self.assertIn("dismissRestoredCard", self.html)
        self.assertIn("restoredStatus", self.html)

    def test_restored_done_card_close_button_is_visible(self):
        """P315: 恢复出的 done 卡片也必须能显示叉号，否则用户无法关闭缓存卡片。"""
        self.assertIn("job-card-restored", self.html)
        self.assertIn(".job-card-done.job-card-restored .job-card-close{display:flex}", self.html)

    def test_env_hint_uses_current_three_model_size_range(self):
        """V3.09 已删除 tiny/base/small/medium，环境提示不能再写 150MB。"""
        self.assertNotIn("150MB~3GB", self.html)
        self.assertIn("1.6GB~2.9GB", self.html)

    def test_c3_main_ui_has_two_scene_shell_and_settings_drawer(self):
        """C3 定稿：主界面是两幕式，低频设置进入左侧抽屉。"""
        for token in [
            "settings-drawer",
            "edgeHandle",
            "main-action-panel",
            "mainTierToggle",
            "识别模型：",
            "鼠标移到左侧边缘时打开设置",
        ]:
            self.assertIn(token, self.html)

    def test_c3_main_mode_matches_final_preview_not_long_bar(self):
        """C3 定稿：标准/精确是 final 原型里的独立选择区，不是长条说明栏。"""
        self.assertIn('class="mode-row"', self.html)
        self.assertIn('class="mode-hint"', self.html)
        self.assertIn("标准：默认推荐，速度和质量平衡。精确：更慢，字幕准确率更高。", self.html)
        self.assertNotIn("main-tier-row", self.html)
        self.assertNotIn("处理方式</strong><span>标准更省时间", self.html)

    def test_c3_drawer_matches_final_preview_navigation(self):
        """C3 设置抽屉最终版：四个短模块头，点击原地展开，不是旧设置表单铺开。

        ㊳ 用户反馈：旧的「点导航滚到下方」的上下结构改成手风琴（点击原地展开、
        一次只开一个）。此测试同步为断言手风琴结构 + 关键内容仍在。
        """
        for token in [
            "acc-item",
            "acc-head",
            "acc-body",
            "toggleAcc(",
            "语音识别模型",
            "智能翻译",
            "字幕与输出",
            "识别模型、下载模型、说话人检测",
            'data-acc="voice"',
            'data-acc="smart-translate"',
            'data-acc="output"',
        ]:
            self.assertIn(token, self.html)
        # D-6 P503：免费翻译模块已删。
        for removed in ['data-acc="env"', 'data-acc="translate"', 'data-acc="advanced"',
                        'data-acc="free-translate"']:
            self.assertNotIn(removed, self.html)

    def test_c3_main_model_copy_uses_human_labels(self):
        """C3 定稿：主界面不把技术模型名当用户文案。"""
        for text in ["更快", "日常推荐", "复杂背景"]:
            self.assertIn(text, self.html)
        self.assertNotIn("<div class=\"card-label\">Whisper 模型</div>", self.html)

    def test_c3_start_button_is_always_visible_and_has_empty_hint(self):
        """C3 定稿：开始按钮是下一步；没视频时给轻提示，不直接沉默。"""
        self.assertIn("请先拖入视频，或粘贴视频链接", self.html)
        self.assertIn("flashDropzone", self.html)
        self.assertIn("开始生成字幕", self.html)

    def test_p323_frontend_separates_package_and_model_ready_copy(self):
        """P323: 包没装和模型没下载要分开说，不能都叫组件未安装。"""
        self.assertIn("installed_pkg", self.html)
        self.assertIn("model_ready", self.html)
        self.assertIn("语音识别模型未下载", self.html)
        self.assertIn("识别增强组件未安装", self.html)
        self.assertIn("识别增强模型未下载", self.html)
        self.assertIn("时间轴对齐组件未安装", self.html)
        self.assertIn("时间轴对齐模型未下载", self.html)
        self.assertIn("声音分类组件未安装", self.html)
        self.assertIn("声音分类模型未下载", self.html)

    def test_p323_component_names_consistent_between_ok_and_missing(self):
        """B4: 同一组件就绪态和缺失态必须用同一个名字，不能就绪叫「字幕处理」缺失叫「语音识别」。"""
        self.assertIn("识别增强已就绪", self.html)
        self.assertIn("时间轴对齐已就绪", self.html)
        self.assertNotIn("字幕处理组件未安装", self.html)
        self.assertNotIn("字幕处理模型未下载", self.html)

    def test_p324_recovered_interrupted_missing_original_model_modal(self):
        """P324: 恢复旧任务时原模型缺失，要让用户选择重新下载或换模型重新跑。"""
        for text in [
            "之前使用的模型已经不存在",
            "重新下载原模型",
            "换模型重新跑",
            "取消",
        ]:
            self.assertIn(text, self.html)
        self.assertIn("showRecoveryModelMissingModal", self.html)
        self.assertIn("isOriginalModelReady", self.html)
        self.assertNotIn("换模型继续", self.html)

    def test_p321_delete_occupied_error_uses_modal_copy(self):
        """P321: Windows 文件占用要弹人话提示，不只给技术错误。"""
        self.assertIn("模型文件正在被占用", self.html)
        self.assertIn("暂时删不了这个模型", self.html)
        self.assertIn("showModelOccupiedModal", self.html)

    def test_model_status_copy_explains_managed_shared_missing_and_downloading(self):
        """C2: 模型状态必须解释自管、共享缓存、未下载、下载中四种状态。"""
        self.assertIn("formatModelStatusText", self.html)
        for text in [
            "已下载，可在本应用内删除",
            "可使用，但来自共享缓存，不能在这里删除",
            "未下载，点击圆点下载",
            "正在下载",
        ]:
            self.assertIn(text, self.html)

    def test_current_model_area_shows_status_copy(self):
        """C2: 当前模型行不能只显示模型名，还要显示用户能看懂的状态说明。"""
        self.assertIn("model-status-copy", self.html)
        self.assertIn("formatModelStatusText(currentMd)", self.html)

    def test_job_progress_copy_is_humanized_for_poll_and_websocket(self):
        """C2: 轮询和 WebSocket 进度都应走同一个人话文案函数。"""
        self.assertIn("formatJobProgressText", self.html)
        self.assertIn("正在检查转录结果", self.html)
        self.assertIn("正在翻译字幕", self.html)
        self.assertIn("正在生成字幕文件", self.html)
        self.assertIn("正在封装视频", self.html)
        self.assertIn("function setJobProgress(jid,percent,stage,message,detail)", self.html)
        self.assertIn("formatJobProgressText(stage,message,detail)", self.html)
        compact = self.html.replace(" ", "")
        # **保护权交接（2026-08-20 · G9/P422 减法回炉）**：WebSocket 和查询接口
        # 共用固定公开状态，并由唯一入口 applyTaskState 渲染。
        self.assertIn("setJobProgress(jid,state.percent,state.phase,state.display_message", compact)
        # **保护权交接（2026-08-19 · G9/P422）**：运行中的 WebSocket 推的是公开快照（type:'state'），
        # 不再是 Worker 原始消息，因此与查询接口共用同一行渲染调用。
        # 「两条通道都能渲染进度」这件事保护范围没变，实现合一了。
        self.assertIn("if(msg&&msg.type==='state'&&msg.state)applyTaskState(jid,msg.state)", compact)

    def test_frontend_error_copy_gives_next_step(self):
        """C2: 错误提示要告诉用户下一步，不只显示“出错”。"""
        self.assertIn("formatJobErrorText", self.html)
        self.assertIn("请检查网络", self.html)
        self.assertIn("请清理磁盘空间", self.html)
        self.assertIn("请先下载模型", self.html)
        self.assertIn(
            "msg.failure_kind?errorMessage:formatJobErrorText(errorMessage)",
            self.html,
        )

    def test_error_copy_does_not_duplicate_existing_user_advice(self):
        """C2 review: 后端已经给出「请...」建议时，前端不能再重复追加一遍。"""
        m = re.search(r"function formatJobErrorText\(message\)\{(?P<body>.*?)\n\}", self.html, re.S)
        self.assertIsNotNone(m, "找不到 formatJobErrorText 函数")
        body = m.group("body")
        self.assertIn("hasUserAdvice(raw)", body)
        self.assertNotIn("raw.includes('请')", body)
        self.assertLess(
            body.index("if(hasUserAdvice(raw))return raw;"),
            body.index("请检查网络"),
            "去重判断必须在网络/磁盘/模型/链接分支之前，否则会重复追加建议",
        )

    def test_error_copy_does_not_treat_request_as_user_advice(self):
        """C2 review: 「请求」里的「请」不是用户建议，不能跳过兜底提示。"""
        self.assertIn("function hasUserAdvice(raw)", self.html)
        self.assertIn("请检查", self.html)
        self.assertIn("请清理", self.html)
        self.assertIn("请先", self.html)
        self.assertIn("请查看", self.html)
        self.assertNotIn("raw.includes('请')", self.html)

    def test_error_copy_covers_backend_friendly_advice_words(self):
        """C2 review: 前端去重白名单必须覆盖后端真实友好错误里的建议动词。"""
        m = re.search(r"function hasUserAdvice\(raw\)\{(?P<body>.*?)\n\}", self.html, re.S)
        self.assertIsNotNone(m, "找不到 hasUserAdvice 函数")
        body = m.group("body")
        for phrase in ["请检查", "请稍等", "请确认", "请尝试"]:
            self.assertIn(phrase, body)

    def test_burn_complete_progress_keeps_done_copy(self):
        """C2 review: burn 阶段后端发「完成」时，前端不能改写成正在封装视频。"""
        m = re.search(r"function formatJobProgressText\(stage,message,detail\)\{(?P<body>.*?)\n\}", self.html, re.S)
        self.assertIsNotNone(m, "找不到 formatJobProgressText 函数")
        self.assertIn("raw==='完成'", m.group("body"))

    def test_model_status_copy_uses_text_content_not_inner_html(self):
        """C2 review: 模型状态文案应用 textContent 写入，避免未来动态字段变复杂时引入 HTML 通道。"""
        self.assertIn("status.textContent=formatModelStatusText(currentMd)", self.html)
        self.assertNotIn("${formatModelStatusText(currentMd)}</span>", self.html)

    def test_restored_error_card_uses_humanized_error_copy(self):
        """刷新后的旧错误走人话提示，结构化错误保留后端给用户的原文。"""
        self.assertIn("renderCardError(jid,view)", self.html)
        self.assertIn(
            "msg.failure_kind?errorMessage:formatJobErrorText(errorMessage)",
            self.html,
        )


class TestTranscriberDefaultModel(unittest.TestCase):
    """transcriber_v2.transcribe 的默认模型参数同步为 large-v2。"""

    def test_transcribe_default_model_is_large_v2(self):
        import inspect
        from backend.transcriber_v2 import transcribe
        sig = inspect.signature(transcribe)
        self.assertEqual(sig.parameters["model_size"].default, "large-v2")


class TestUploadInputs(unittest.TestCase):
    """V3.09: 本地路径框暂时关闭（Tauri 真拖拽替代），在线链接框保留。"""

    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).parent.parent / "frontend" / "index.html").read_text(encoding="utf-8")

    def test_local_path_row_hidden(self):
        m = re.search(r'<div class="local-path-row"[^>]*>', self.html)
        self.assertIsNotNone(m, "找不到 local-path-row")
        self.assertIn("display:none", m.group(0), "本地路径框应被隐藏")

    def test_local_path_guide_hidden(self):
        m = re.search(r'<div class="local-path-guide"[^>]*>', self.html)
        self.assertIsNotNone(m, "找不到 local-path-guide")
        self.assertIn("display:none", m.group(0), "本地路径指引应被隐藏")

    def test_url_input_row_still_visible(self):
        m = re.search(r'<div class="url-input-row"[^>]*>', self.html)
        self.assertIsNotNone(m, "找不到 url-input-row")
        self.assertNotIn("display:none", m.group(0), "在线链接框不应被关闭")


class TestP308ModelScopeDownload(unittest.TestCase):
    """P308: Whisper 下载改走 ModelScope，完成态由本项目 manifest 控制。"""

    def test_modelscope_declared_in_pyproject(self):
        """干净机器按 pyproject 安装时，下载组件也必须被安装。"""
        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        self.assertIn('"modelscope==1.37.1"', text)

    def test_download_uses_modelscope_not_huggingface(self):
        import inspect
        from backend import model_manager
        src = inspect.getsource(model_manager._download_model)
        self.assertIn("modelscope", src, "下载入口应改用 ModelScope")
        self.assertNotIn("huggingface_hub", src, "Whisper 模型下载不应再依赖 Hugging Face")
        self.assertNotIn("HF_ENDPOINT", src, "Whisper 模型下载不应再靠 hf-mirror 环境变量")

    def test_model_storage_dir_under_video_subber_ready_dir(self):
        import backend.model_manager as mm
        with tempfile.TemporaryDirectory() as d, \
             patch.object(mm, "MODEL_ROOT", Path(d)):
            target = mm._model_storage_dir("large-v2", backend="mlx")
        self.assertEqual(target, Path(d) / "mlx" / "large-v2")

    def test_partial_files_without_manifest_not_downloaded(self):
        import backend.model_manager as mm
        with tempfile.TemporaryDirectory() as d, \
             patch.object(mm, "MODEL_ROOT", Path(d)):
            model_dir = Path(d) / "mlx" / "large-v2"
            model_dir.mkdir(parents=True)
            (model_dir / "config.json").write_text("{}", encoding="utf-8")
            (model_dir / "model.safetensors").write_bytes(b"partial")
            self.assertFalse(mm.is_model_ready("large-v2", backend="mlx"))

    def test_ready_manifest_requires_config_and_weight(self):
        import backend.model_manager as mm
        with tempfile.TemporaryDirectory() as d, \
             patch.object(mm, "MODEL_ROOT", Path(d)):
            model_dir = Path(d) / "mlx" / "large-v2"
            model_dir.mkdir(parents=True)
            mm.write_ready_manifest("large-v2", model_dir, backend="mlx")
            self.assertFalse(mm.is_model_ready("large-v2", backend="mlx"),
                             "只有 manifest、没有 config 和权重文件时不能显示绿点")

    def test_write_manifest_is_atomic_and_marks_ready(self):
        import backend.model_manager as mm
        with tempfile.TemporaryDirectory() as d, \
             patch.object(mm, "MODEL_ROOT", Path(d)):
            model_dir = Path(d) / "mlx" / "large-v2"
            model_dir.mkdir(parents=True)
            (model_dir / "config.json").write_text("{}", encoding="utf-8")
            (model_dir / "model.safetensors").write_bytes(b"complete")
            mm.write_ready_manifest("large-v2", model_dir, backend="mlx")
            self.assertTrue((model_dir / "manifest.json").exists())
            self.assertFalse(list(model_dir.glob("*.tmp")),
                             "manifest 应先写临时文件再 rename，完成后不能残留 tmp")
            self.assertTrue(mm.is_model_ready("large-v2", backend="mlx"))

    def test_whisper_ready_rejects_manifest_without_files(self):
        """P329：顶层 ready 正确，也不能忽略 manifest v2 的 files 清单。"""
        import backend.model_manager as mm
        with tempfile.TemporaryDirectory() as d, \
             patch.object(mm, "MODEL_ROOT", Path(d)):
            model_dir = Path(d) / "mlx" / "large-v2"
            model_dir.mkdir(parents=True)
            (model_dir / "config.json").write_text("{}", encoding="utf-8")
            (model_dir / "model.safetensors").write_bytes(b"complete")
            (model_dir / "manifest.json").write_text(json.dumps({
                "status": "ready",
                "version": 2,
                "model_id": "large-v2",
                "backend": "mlx",
                "repo": mm.MODELS["large-v2"]["repo"],
            }), encoding="utf-8")

            self.assertFalse(mm.is_model_ready("large-v2", backend="mlx"))

    def test_whisper_ready_checks_listed_size_without_hashing(self):
        """P329：日常检查核对文件大小，但不能为绿点计算整模型哈希。"""
        import backend.model_manager as mm
        with tempfile.TemporaryDirectory() as d, \
             patch.object(mm, "MODEL_ROOT", Path(d)), \
             patch("backend.model_manifest.sha256_file") as sha256_file:
            model_dir = Path(d) / "mlx" / "large-v2"
            model_dir.mkdir(parents=True)
            (model_dir / "config.json").write_text("{}", encoding="utf-8")
            (model_dir / "model.safetensors").write_bytes(b"complete")
            (model_dir / "manifest.json").write_text(json.dumps({
                "status": "ready",
                "version": 2,
                "model_id": "large-v2",
                "backend": "mlx",
                "repo": mm.MODELS["large-v2"]["repo"],
                "files": [{
                    "path": "model.safetensors",
                    "size": 999,
                    "sha256": "not-checked-here",
                }],
            }), encoding="utf-8")

            self.assertFalse(mm.is_model_ready("large-v2", backend="mlx"))
            sha256_file.assert_not_called()

    def test_resolve_model_path_requires_ready_model(self):
        import backend.model_manager as mm
        with tempfile.TemporaryDirectory() as d, \
             patch.object(mm, "MODEL_ROOT", Path(d)), \
             patch.object(mm, "_legacy_modelscope_model_dir", return_value=None), \
             patch.object(mm, "_legacy_hf_model_dir", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "请先下载模型"):
                mm.resolve_whisper_model_path("large-v2", backend="mlx")

    def test_start_download_checks_disk_space_before_process(self):
        import backend.model_manager as mm
        with patch.object(mm, "_find_ready_model_dir", return_value=None), \
             patch.object(mm, "_has_enough_disk_space", return_value=False), \
             patch.object(mm.multiprocessing, "Process") as process:
            result = mm.start_download("large-v2")
        self.assertIn("磁盘空间不足", result.get("error", ""))
        process.assert_not_called()


class TestDownloadErrorVisibility(unittest.TestCase):
    """下载失败时后端应在 status 里暴露 error 字段，前端应有对应的错误展示逻辑。"""

    def test_model_status_includes_error_on_failure(self):
        """get_model_status() 应返回 error 字段，让前端能展示失败原因。"""
        from backend.model_manager import get_model_status, _downloads
        # 模拟一次失败的下载
        _downloads["large-v3-turbo"] = {
            "downloading": False,
            "progress": 30,
            "error": "ProxyError: Connection refused",
        }
        try:
            status = get_model_status()
            self.assertIn("error", status["large-v3-turbo"],
                          "get_model_status() 未返回 error 字段，前端无从展示失败原因")
            self.assertEqual(status["large-v3-turbo"]["error"], "ProxyError: Connection refused")
        finally:
            _downloads.pop("large-v3-turbo", None)

    def test_frontend_handles_download_error(self):
        """前端 pollModelProgress 应处理 error 字段并展示 toast 提示。"""
        root = Path(__file__).parent.parent
        html = (root / "frontend" / "index.html").read_text(encoding="utf-8")
        # 轮询停止后应检查 md.error 并给用户提示
        self.assertIn("md.error", html,
                      "前端 pollModelProgress 未检查 md.error，下载失败时用户看不到任何提示")


class TestFriendlyDownloadError(unittest.TestCase):
    """下载失败的原始技术错误要翻译成人话，前端不再做字符串截断 hack。"""

    def test_proxy_error_translated(self):
        from backend.model_manager import _friendly_error
        msg = _friendly_error("ProxyError: Unable to connect to proxy 127.0.0.1:7890")
        self.assertIn("代理", msg, "代理类错误应翻译成提到「代理」的人话")
        self.assertNotIn("ProxyError", msg, "不应把原始异常类名暴露给用户")

    def test_timeout_error_translated(self):
        from backend.model_manager import _friendly_error
        msg = _friendly_error("ReadTimeout: HTTPSConnectionPool timed out")
        self.assertIn("超时", msg)

    def test_connection_error_translated(self):
        from backend.model_manager import _friendly_error
        msg = _friendly_error("MaxRetryError: NewConnectionError failed to establish")
        self.assertIn("连接", msg)

    def test_unknown_error_has_generic_human_message(self):
        from backend.model_manager import _friendly_error
        msg = _friendly_error("SomeWeirdInternalError xyz")
        self.assertNotIn("SomeWeirdInternalError", msg)
        self.assertTrue(any(k in msg for k in ("失败", "重试")),
                        "未知错误也要给一句人能看懂的兜底提示")

    def test_frontend_shows_error_without_truncation_hack(self):
        """前端应直接显示后端给的人话，不再做 split(':').pop().slice() 截断。"""
        root = Path(__file__).parent.parent
        html = (root / "frontend" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("md.error.split", html,
                         "前端仍在截断 error 字符串，后端已翻译成人话，应直接显示")


class TestP321DeleteOccupiedError(unittest.TestCase):
    """P321: 删除模型遇 Windows 文件占用时返回人话，不抛 500。"""

    def test_delete_model_returns_human_message_on_permission_error(self):
        import tempfile
        import backend.model_manager as mm
        with tempfile.TemporaryDirectory() as d, \
             patch.object(mm, "MODEL_ROOT", Path(d)), \
             patch.object(mm.shutil, "rmtree", side_effect=PermissionError("busy")):
            model_dir = mm._model_storage_dir("large-v2", backend="mlx")
            model_dir.mkdir(parents=True)
            result = mm.delete_model("large-v2", backend="mlx")
        self.assertIn("模型文件正在被占用", result.get("error", ""))
        self.assertIn("重启 App", result.get("error", ""))


class TestP322WorkerModelMissingCopy(unittest.TestCase):
    """P322: worker 缺模型文案必须改成一键准备 / 一键修复口径。"""

    def test_model_missing_error_points_to_prepare_or_repair(self):
        from backend.worker import _friendly_error
        msg = _friendly_error("FileNotFoundError: model file missing")
        self.assertIn("一键准备", msg)
        self.assertIn("一键修复", msg)
        self.assertNotIn("下次启动", msg)
        self.assertNotIn("自动下载", msg)


class TestP324RecoverInterruptedMissingOriginalModel(unittest.TestCase):
    """P324: 恢复旧任务前先检查原模型，不要直接启动 worker。"""

    def test_interrupted_job_with_missing_original_model_returns_choice_state(self):
        import tempfile
        import backend.main as main
        with tempfile.TemporaryDirectory() as d:
            jobs_dir = Path(d)
            old_job = jobs_dir / "old-job"
            old_job.mkdir()
            (old_job / "input.mp4").write_bytes(b"video")
            new_job = jobs_dir / "new-job"
            new_job.mkdir()
            existing = {
                "job_id": "old-job",
                "status": "interrupted",
                "config": {"model": "large-v2", "video_filename": "old.mp4"},
            }
            with patch.object(main, "JOBS_DIR", jobs_dir), \
                 patch.object(main, "JOBS", {}), \
                 patch("backend.model_manager.is_model_loadable", return_value=False), \
                 patch.object(main.worker_manager, "start") as start:
                result = main._start_existing_job(existing, old_job / "input.mp4")
        self.assertEqual(result["status"], "model_missing")
        self.assertIn("original_model", result)
        start.assert_not_called()

    def test_interrupted_job_with_legacy_cache_model_recovers(self):
        """B3: 恢复检查必须和 worker 加载用同一把尺子——旧缓存可加载就不该报 model_missing。"""
        import tempfile
        import backend.main as main
        with tempfile.TemporaryDirectory() as d:
            jobs_dir = Path(d)
            old_job = jobs_dir / "old-job"
            old_job.mkdir()
            (old_job / "input.mp4").write_bytes(b"video")
            new_job = jobs_dir / "new-job"
            new_job.mkdir()
            existing = {
                "job_id": "old-job",
                "status": "interrupted",
                "config": {"model": "large-v3", "video_filename": "old.mp4"},
            }
            with patch.object(main, "JOBS_DIR", jobs_dir), \
                 patch.object(main, "JOBS", {}), \
                 patch("backend.model_manager.is_model_ready", return_value=False), \
                 patch("backend.model_manager.is_model_loadable", return_value=True), \
                 patch.object(main, "_persist_job_status"), \
                 patch.object(main.worker_manager, "start") as start:
                result = main._start_existing_job(existing, old_job / "input.mp4")
        self.assertNotEqual(result.get("status"), "model_missing")
        start.assert_called_once()

    def test_is_model_loadable_accepts_legacy_cache(self):
        """B3: is_model_loadable = 自管 ready 或旧缓存可回退，与运行时 resolve 同口径。"""
        import backend.model_manager as mm
        with patch.object(mm, "is_model_ready", return_value=False), \
             patch.object(mm, "_legacy_modelscope_model_dir", return_value=Path("/legacy/x")), \
             patch.object(mm, "_legacy_hf_model_dir", return_value=None):
            self.assertTrue(mm.is_model_loadable("large-v3"))
        with patch.object(mm, "is_model_ready", return_value=False), \
             patch.object(mm, "_legacy_modelscope_model_dir", return_value=None), \
             patch.object(mm, "_legacy_hf_model_dir", return_value=None):
            self.assertFalse(mm.is_model_loadable("large-v3"))


class TestDownloadRealProgress(unittest.TestCase):
    """下载进度按真实字节数计算，不再用假的 pulse 斜坡动画。"""

    def test_parse_size_to_bytes(self):
        from backend.model_manager import _parse_size_to_bytes
        b = _parse_size_to_bytes("~1.6GB")
        self.assertTrue(1.5 * 1024**3 < b < 1.7 * 1024**3,
                        f"'~1.6GB' 应解析为约 1.6GB 的字节数，得到 {b}")

    def test_compute_progress_normal(self):
        from backend.model_manager import _compute_progress
        self.assertEqual(_compute_progress(0, 1600), 0)
        self.assertEqual(_compute_progress(800, 1600), 50)

    def test_compute_progress_caps_at_99(self):
        """下载未确认完成前最高 99%，留到 snapshot_download 返回才置 100。"""
        from backend.model_manager import _compute_progress
        self.assertEqual(_compute_progress(1600, 1600), 99)
        self.assertEqual(_compute_progress(9999, 1600), 99)

    def test_compute_progress_guards_zero_expected(self):
        from backend.model_manager import _compute_progress
        self.assertEqual(_compute_progress(100, 0), 0)

    def test_dir_size_counts_real_bytes(self):
        import tempfile
        from pathlib import Path as _P
        from backend.model_manager import _dir_size_bytes
        with tempfile.TemporaryDirectory() as d:
            (_P(d) / "a.bin").write_bytes(b"x" * 1000)
            sub = _P(d) / "sub"; sub.mkdir()
            (sub / "b.bin").write_bytes(b"y" * 500)
            self.assertEqual(_dir_size_bytes(_P(d)), 1500)

    def test_dir_size_missing_dir_is_zero(self):
        from pathlib import Path as _P
        from backend.model_manager import _dir_size_bytes
        self.assertEqual(_dir_size_bytes(_P("/no/such/dir/xyz")), 0)

    def test_format_speed_text(self):
        from backend.model_manager import _format_speed
        self.assertEqual(_format_speed(0), "等待网络")
        self.assertEqual(_format_speed(2.5 * 1024 * 1024), "2.5 MB/s")

    def test_model_status_includes_download_speed(self):
        from backend.model_manager import get_model_status, _downloads
        _downloads["large-v3-turbo"] = {
            "downloading": True,
            "progress": 12,
            "speed_bytes_per_sec": 2 * 1024 * 1024,
            "speed_text": "2.0 MB/s",
        }
        try:
            status = get_model_status()
            self.assertEqual(status["large-v3-turbo"]["speed_text"], "2.0 MB/s")
            self.assertEqual(status["large-v3-turbo"]["speed_bytes_per_sec"], 2 * 1024 * 1024)
        finally:
            _downloads.pop("large-v3-turbo", None)

    def test_model_status_resets_stale_download_speed(self):
        import backend.model_manager as mm
        mm._downloads["large-v3-turbo"] = {
            "downloading": True,
            "progress": 12,
            "speed_bytes_per_sec": 5 * 1024 * 1024,
            "speed_text": "5.0 MB/s",
            "speed_updated_at": 10.0,
        }
        try:
            with patch.object(mm.time, "monotonic", return_value=20.0):
                status = mm.get_model_status()
            self.assertEqual(status["large-v3-turbo"]["speed_bytes_per_sec"], 0)
            self.assertEqual(status["large-v3-turbo"]["speed_text"], "等待网络")
        finally:
            mm._downloads.pop("large-v3-turbo", None)

    def test_model_status_marks_managed_ready_model(self):
        import tempfile
        import backend.model_manager as mm
        with tempfile.TemporaryDirectory() as d, patch.object(mm, "MODEL_ROOT", Path(d)):
            model_dir = mm._model_storage_dir("large-v2")
            model_dir.mkdir(parents=True)
            (model_dir / "config.json").write_text("{}", encoding="utf-8")
            (model_dir / "weights.safetensors").write_bytes(b"x")
            mm.write_ready_manifest("large-v2", model_dir)
            status = mm.get_model_status()
            self.assertTrue(status["large-v2"]["downloaded"])
            self.assertTrue(status["large-v2"]["managed"])

    def test_cancel_download_sets_stop_event(self):
        import threading
        from backend.model_manager import cancel_download, _downloads
        stop_event = threading.Event()
        _downloads["large-v3-turbo"] = {
            "downloading": True,
            "progress": 33,
            "stop_event": stop_event,
        }
        try:
            result = cancel_download("large-v3-turbo")
            self.assertTrue(result.get("stopping"))
            self.assertTrue(stop_event.is_set())
        finally:
            _downloads.pop("large-v3-turbo", None)

    def test_cancel_download_terminates_real_process(self):
        import multiprocessing
        import time
        from backend.model_manager import cancel_download, _downloads
        stop_event = multiprocessing.Event()
        process = multiprocessing.Process(target=time.sleep, args=(30,), daemon=True)
        process.start()
        _downloads["large-v3-turbo"] = {
            "state": {"downloading": True, "progress": 33},
            "process": process,
            "stop_event": stop_event,
        }
        try:
            result = cancel_download("large-v3-turbo")
            self.assertTrue(result.get("stopping"))
            self.assertTrue(stop_event.is_set())
            process.join(timeout=3)
            self.assertFalse(process.is_alive(), "停止下载必须终止真实下载子进程")
        finally:
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
            _downloads.pop("large-v3-turbo", None)

    def test_download_model_stop_before_manifest_does_not_mark_ready(self):
        import tempfile
        import threading
        from pathlib import Path
        import backend.model_manager as mm

        stop_event = threading.Event()
        state = {}

        def fake_snapshot_download(model_id, local_dir, progress_callbacks):
            target = Path(local_dir)
            target.mkdir(parents=True, exist_ok=True)
            (target / "config.json").write_text("{}", encoding="utf-8")
            (target / "model.safetensors").write_bytes(b"x")
            stop_event.set()

        with tempfile.TemporaryDirectory() as d, \
             patch.object(mm, "MODEL_ROOT", Path(d)), \
             patch("modelscope.hub.snapshot_download.snapshot_download", side_effect=fake_snapshot_download):
            mm._download_model("large-v2", state, stop_event)
            model_dir = mm._model_storage_dir("large-v2")
            self.assertFalse((model_dir / "manifest.json").exists(),
                             "用户停止后不能再写 ready manifest")
            self.assertFalse(mm.is_model_ready("large-v2"))
            self.assertTrue(state.get("cancelled"))

    def test_delete_model_removes_only_project_ready_dir(self):
        import tempfile
        import backend.model_manager as mm
        with tempfile.TemporaryDirectory() as d, patch.object(mm, "MODEL_ROOT", Path(d)):
            model_dir = mm._model_storage_dir("large-v2", backend="mlx")
            model_dir.mkdir(parents=True)
            (model_dir / "config.json").write_text("{}", encoding="utf-8")
            result = mm.delete_model("large-v2", backend="mlx")
            self.assertTrue(result.get("deleted"))
            self.assertFalse(model_dir.exists())

    def test_delete_model_refuses_while_downloading(self):
        from backend.model_manager import delete_model, _downloads
        _downloads["large-v2"] = {"downloading": True}
        try:
            result = delete_model("large-v2")
            self.assertIn("下载中", result.get("error", ""))
        finally:
            _downloads.pop("large-v2", None)

    def test_fake_pulse_ramp_removed(self):
        """旧的假进度斜坡公式已删除，改为 ModelScope 进度回调。"""
        import inspect
        from backend import model_manager
        src = inspect.getsource(model_manager._download_model)
        self.assertNotIn("* 0.15", src, "仍残留假 pulse 斜坡公式")
        self.assertIn("progress_callbacks", src, "未接入 ModelScope 下载进度回调")


class TestC3ReviewFixBatch(unittest.TestCase):
    """C3 收口批只读审查（B1-B5 + 非阻塞项）修复的回归锁。"""

    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).parent.parent / "frontend" / "index.html").read_text(encoding="utf-8")

    # ── B1: 两幕式主界面 ──
    def test_b1_two_scene_classes_and_video_box_exist(self):
        """主区必须有 scene-first / scene-selected / scene-processing 状态和同尺寸视频框。"""
        for token in ["scene-first", "scene-selected", "scene-processing",
                      'id="videoBox"', "setScene(", "换一个",
                      "也可以把新视频拖到这里直接替换"]:
            self.assertIn(token, self.html, f"两幕式缺少 {token}")

    def test_b1_first_scene_hides_mode_and_model_summary(self):
        """第一幕（没有视频）不显示标准/精确和模型摘要，开始按钮保留。"""
        self.assertIn(".app.scene-first .mode-row", self.html)
        self.assertIn(".app.scene-first .main-model-summary", self.html)

    def test_b1_selected_scene_swaps_dropzone_for_video_box(self):
        """第二幕视频框替换拖拽区，链接输入收起。"""
        self.assertIn(".app.scene-selected .dropzone", self.html)
        self.assertIn(".app.scene-selected .url-input-row", self.html)
        self.assertIn(".app.scene-selected #videoBox", self.html)

    def test_b1_env_three_layer_expression(self):
        """检测表达：启动检测横幅、顶部轻量状态行、异常/下载橙色横幅；旧抽屉环境详情删除。"""
        for token in ['id="envBanner"', 'id="envPill"', "env-status-line",
                      "正在检测运行环境", "运行环境未准备好",
                      "语音识别", "识别增强", "声音分类", "视频处理",
                      "renderEnvStatusLine", "repairFlow()"]:
            self.assertIn(token, self.html, f"检测三层表达缺少 {token}")
        self.assertNotIn('id="envBar"', self.html,
                         "就绪态不应再有 7 个环境药丸常驻主界面")
        self.assertNotIn('id="drawerComponentList"', self.html,
                         "运行环境已从抽屉删除，不应再保留抽屉组件明细列表")

    def test_b1_job_card_has_milestone_dots(self):
        """任务卡是进度条 + 5 个里程碑圆点，不是裸进度条。"""
        for token in ["milestone-dot", "updateMilestones",
                      "准备视频", "识别语音", "检查字幕", "生成文件"]:
            self.assertIn(token, self.html, f"里程碑圆点缺少 {token}")

    # ── B2: prepare/repair 接线 ──
    def test_b2_prepare_progress_and_stop_wired(self):
        """一键准备必须有可视进度（当前项/总进度/网速）和停止入口。"""
        for token in ["current_item", "overall_percent", "renderPrepareBanner",
                      "'/api/prepare/stop'"]:
            self.assertIn(token, self.html, f"prepare 进度接线缺少 {token}")

    def test_b2_repair_reachable_and_handles_ambiguous(self):
        """一键修复必须有 UI 入口，并处理 ambiguous 候选列表。"""
        for token in ["repairFlow", "'/api/repair'", "ambiguous", "candidates",
                      ">一键修复<"]:
            self.assertIn(token, self.html, f"repair 接线缺少 {token}")

    def test_b2_job_error_offers_prepare_or_repair_action(self):
        """任务错误提示里出现一键准备/一键修复时，要给可点的按钮。"""
        self.assertIn("renderErrorActions", self.html)

    # ── B5: 加载竞态 ──
    def test_b5_reconnect_waits_for_model_status(self):
        """恢复检查前必须先拿到模型状态，状态未知时不误弹缺模型弹窗。"""
        self.assertIn("await fetchModelStatus()", self.html)
        self.assertIn("if(!md)return true", self.html)

    # ── 非阻塞项 ──
    def test_n1_unreachable_backend_is_explicit(self):
        """连不上后端要明说，不能渲染成「模型未下载」。"""
        self.assertIn("无法连接后端", self.html)
        self.assertIn("renderBackendUnreachable", self.html)

    def test_n6_recovery_redownload_persists_model_choice(self):
        """「重新下载原模型」切换模型后要落盘，刷新不丢。"""
        self.assertIn("State.settings.model=original;saveConfig()", self.html)


class TestDrawerAccordion(unittest.TestCase):
    """审查修复批㊳: 抽屉从「点导航滚到下面」改为「点击原地展开，一次只开一个」。"""

    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).parent.parent / "frontend" / "index.html").read_text(encoding="utf-8")

    def test_accordion_structure_replaces_scroll_nav(self):
        """抽屉是手风琴：每个模块有可点击头 + 就地展开体。"""
        # 最终版：运行环境进主界面，翻译只留智能翻译（D-6 P503 删免费），高级删除，字幕样式并入字幕与输出。
        for token in ['data-acc="voice"',
                      'data-acc="smart-translate"', 'data-acc="output"',
                      "acc-head", "acc-body", "toggleAcc("]:
            self.assertIn(token, self.html, f"手风琴结构缺少 {token}")
        for removed in ['data-acc="env"', 'data-acc="translate"',
                        'data-acc="advanced"', 'data-acc="style"']:
            self.assertNotIn(removed, self.html)

    def test_old_scroll_into_view_nav_removed(self):
        """旧的「点导航 scrollIntoView 到下方 / 展开更多设置」交互必须删除。"""
        self.assertNotIn("drawerEnvDetail').scrollIntoView", self.html)
        self.assertNotIn("drawerVoiceDetail').scrollIntoView", self.html)
        self.assertNotIn("drawerMoreSettings').open=true", self.html)
        self.assertNotIn('id="drawerMoreSettings"', self.html)
        self.assertNotIn(">更多设置</summary>", self.html)

    def test_accordion_opens_one_at_a_time(self):
        """一次只展开一个：toggleAcc 打开目标时必须收起其它模块。"""
        m = re.search(r"function toggleAcc\([^)]*\)\{(.*?)\n\}", self.html, re.S)
        self.assertIsNotNone(m, "找不到 toggleAcc 函数")
        body = m.group(1)
        self.assertIn("acc-item", body)
        self.assertTrue("remove('open')" in body or 'remove("open")' in body,
                        "toggleAcc 未收起其它模块（一次只开一个）")

    def test_open_settings_drawer_expands_model_section(self):
        """主界面「更改」→ openSettingsDrawer('model') 要展开语音识别模型手风琴，不是滚动。"""
        m = re.search(r"function openSettingsDrawer\([^)]*\)\{(.*?)\n\}", self.html, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertIn("toggleAcc", body)
        self.assertNotIn("scrollIntoView", body)


class TestCodexReviewFix2(unittest.TestCase):
    """Codex 复审 C3 修复批报的 2 个阻塞：repair 进度契约错层 + 运行中漏锁抽屉设置。"""

    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).parent.parent / "frontend" / "index.html").read_text(encoding="utf-8")

    def _fn_body(self, name):
        m = re.search(r"function " + name + r"\([^)]*\)\{(.*?)\n\}", self.html, re.S)
        self.assertIsNotNone(m, f"找不到函数 {name}")
        return m.group(1)

    def test_repair_progress_reads_repair_subobject(self):
        """B2 阻塞1: 修复横幅必须读 p.repair 的进度/网速/当前项，不是顶层空字段。"""
        body = self._fn_body("renderPrepareBanner")
        self.assertIn(".repair", body)
        self.assertIn("progress", body, "repair 分支未读进度")
        self.assertIn("models[", body, "repair 分支未用 target 定位当前项人话名")

    def test_repair_failure_surfaced(self):
        """B2 阻塞1: repair 失败（p.repair.error）不能静默消失。"""
        poll = self._fn_body("pollPrepareUntilDone")
        self.assertIn("repair", poll)
        self.assertIn("error", poll, "修复失败未向用户提示")

    def test_lockui_locks_drawer_tier_and_speaker(self):
        """B2 阻塞2 / A2 锁反转: 任务运行中，抽屉里的转录模式/说话人检测必须锁住。
        A2 起改由锁 .acc-body 容器覆盖（tierToggle/speakerToggle 都在 acc-body 内），
        lockUI 只额外手写锁抽屉外的 mainTierToggle。运行时保证不变。"""
        body = self._fn_body("lockUI")
        self.assertIn(".acc-body", body,
                      "lockUI 必须锁 .acc-body 容器（覆盖抽屉内的 tier/speaker）")
        self.assertIn("'mainTierToggle'", body,
                      "lockUI 必须额外锁抽屉外的 mainTierToggle")


class TestN3DeleteErrorNarrowing(unittest.TestCase):
    """非阻塞 3: 只有权限/占用类错误才说「被占用」，其他 OSError 给真实原因。"""

    def test_permission_error_reports_occupied(self):
        import tempfile
        import backend.model_manager as mm
        with tempfile.TemporaryDirectory() as d, \
             patch.object(mm, "MODEL_ROOT", Path(d)), \
             patch.object(mm.shutil, "rmtree", side_effect=PermissionError("busy")):
            model_dir = mm._model_storage_dir("large-v2", backend="mlx")
            model_dir.mkdir(parents=True)
            result = mm.delete_model("large-v2", backend="mlx")
        self.assertIn("模型文件正在被占用", result.get("error", ""))

    def test_other_oserror_not_reported_as_occupied(self):
        import tempfile
        import backend.model_manager as mm
        with tempfile.TemporaryDirectory() as d, \
             patch.object(mm, "MODEL_ROOT", Path(d)), \
             patch.object(mm.shutil, "rmtree", side_effect=OSError("disk I/O error")):
            model_dir = mm._model_storage_dir("large-v2", backend="mlx")
            model_dir.mkdir(parents=True)
            result = mm.delete_model("large-v2", backend="mlx")
        self.assertNotIn("占用", result.get("error", ""))
        self.assertIn("删除模型失败", result.get("error", ""))


if __name__ == "__main__":
    unittest.main()
