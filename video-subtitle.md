# 视频中文字幕生成工具

## 项目背景

将任意语言视频自动生成流畅中文字幕的本地工具。核心价值是把分散的工具（转录、翻译、字幕制作）串成一条一站式自动化流水线，让用户"拖进去，等一会，就能看懂。"

**载体**：V1 命令行工具 → V2 叠加本地 Web UI（浏览器操作 localhost）

**一句话定义**：「拖进去，等一会，就能看懂。」

---

## 版本规划

| 版本 | 内容 | 预计耗时 |
|------|------|----------|
| **V1** | 命令行工具。faster-whisper + deep-translator + ffmpeg。`--subtitles yes/no`。SRT 输出。 | 已完成 |
| **V1.1** | 小迭代。VAD 废弃→五层后处理过滤、Baidu `\n` 批量翻译+双后端竞速、resemblyzer 说话人变化检测、checkpoint 哈希验证、双日志系统。 | ✅ 已验证（287段，翻译100%） |
| **V1.2** | 小迭代。RMS 四特征双信号交叉验证（P025）——替代 nsp 单信号过滤。纯物理信号，不依赖 AI 模型。 | ✅ 已实现 |
| **V1.3 🔴 最终** | **V1 线终点**。单趟自适应阈值 + 四信号多数投票 + FILTER_CONFIG 统一管理。动态阈值只紧不松安全钳。 | ✅ 已锁定 |
| **V2** | Web UI（FastAPI + 单页 HTML）。拖拽上传 + 环境检测 + 极速/质量双翻译模式 + 免费/付费分层 UI + LLM 上下文感知翻译 + 字幕样式预览。**以 V1.3 为框架，不动 V1.3 锁定功能。** | ✅ V2.01 框架已锁定（23 文件，功能待填充） |
| **V2.01** | **框架基准版本**。7 区域 UI 布局 + 9 路由 + 前后端动态同步 + 上下文感知流程 + 开发者模式。后续 V2 改动在此框架上叠加。 | ✅ 已保存至 `v2/v2.01/` |
| **V2.02** | **智能缓存失效**。设置变更时自动清除受影响缓存 + 前端自动重跑。config 写入 checkpoint meta。 | ✅ 已保存至 `v2/v2.02/` |
| **V2.03** | **上传哈希去重 + 自动恢复**。上传时后端 SHA256 查重 → 命中则自动恢复历史进度（配置同步 UI 后锁定）。处理中所有设置灰色不可修改。修复 P032（双 Worker 并行）和 P033（恢复后 UI 未同步）。 | ✅ 已保存至 `v2/v2.03/` |
| **V2.04** | **审查修复 + 恢复机制重构**。① 后端：GLOBAL_HASH_INDEX 内存索引 O(1) 查重 + interrupted 自动重启 + video_filename 源头补全 ② 前端：d.found 三路归一路（卡片存在→高亮 / 不存在→兜底）+ showToast 替代 alert + 选择性 lockUI + 按钮锁死 ③ Bug 修复：cancelCard 重复函数名 SyntaxError + syncUI const 重赋值 TypeError + {} falsy + fadein keyframes ④ 审查：Steve Jobs 三轮 + 对方工程师三轮 → 3 不修 1 修（✅ emoji）⑤ syncUI 职责拆分延后到 V2.06。迭代记录 → `v2/v2.04/V2.04-迭代记录.md` | ✅ 已保存至 `v2/v2.04/` |
| **V2.05** | **片头净化 + 双日志修复**。方案 D+F 组合管道——large-v3 合并型片头段的预处理净化。双 Handler 分层日志（user.log + debug.log）+ RotatingFileHandler 磁盘保护。修复 P053、P054。 | ✅ 已保存至 `v2/v2.05/` |
| **V2.06** | **代码审查修复 + LLMBackend 架构重构**。① 9 个 Bug 全歼（R01-R09）：路由装饰器修复、流式上传+5GB 限制、激活码吊销修复、死代码清理、GPU 加速、按钮文案统一、远程 URL 默认值、LLMBackend 基类架构、jobId 死变量清理 ② 前端 7 处同步：自定义平台下拉+端点/模型输入框+切换逻辑+字段收集+显示格式、上传区 5GB 提示+413 错误展示 ③ `LLMBackend` 基类驱动 7 个后端（6 原生 + `CustomOpenAIBackend`），每个 ~35 行，`translate_raw`/`translate_batch` 全平台零遗漏。详见 `code-review-report-2026-05-22.md` / `steve-jobs-solution-2026-05-22.md` / `problems.md` P086-P094。| ✅ 已保存至 `v2/v2.06/` |
| **V2.07** | **遗留卫生债务清理 + 前后端一致性审查 + Windows 跨平台适配**。**Phase 1（三方审查）**：① P045 双 Worker 护卫 ② P051 STAGE_CACHE `dict[str,list[str]]` ③ syncUI 拆 3 函数+try/catch ④ P059 非 JSON→debug.log ⑤ P054 print→logging 8 处。**Phase 2（P096 · 16 项）**：前后端一致性 + UX 修复（详见 problems.md P096）。**Phase 3**：_real_stdout NameError + 开发者入口可见 + LLM 区域分层 + 弹窗 CSS 修复 + modelHint 隐藏。**Phase 4（P097 · 7 项 Windows 适配）**：W01 subprocess UTF-8 encoding + W02 Windows 字体 "Microsoft YaHei" + W03 ASS 路径反斜杠转义 + W04 Windows FFmpeg 路径检测 + W05 终端打印异常兜底 + W06 文件锁异常处理 + W07 模拟路径跨平台 | ✅ 已保存至 `v2/v2.07/` |
| **V2.08** | **V1.3 模块迁移 + 完全自包含 + QA审查修复**。Phase 1：新建 `backend/core/`（extractor / subtitler / checkpoint / vad_filter），删除所有跨目录路径注入，`Segment` 移入 `transcriber_v2.py`。V2.08 起无需 v1/ 目录即可运行，具备 EXE 打包条件。`subtitler.py` 同步 W01-W04 跨平台修复。Phase 2（QA审查）：🔴本地路径不复制视频文件（只算hash引用原路径）、🔴Worker crash自动报错建议换模型、⚠️reconnectJobs处理error状态、⚠️多job恢复不互相覆盖设置、💡进度时间按模型动态估算。 | ✅ 已保存至 `v2/v2.08/` |
| **V2.09** | **配置感知去重 + 进度文字去专业化**。同视频不同设置=新任务，同视频同设置=复用结果。进度条全部换成用户能看懂的大白话。 | ✅ Git 提交 `97b39e2` |
| **V2.10** | **说话人检测 V2 + 进程存活检测 + tier UI 修复 + 翻译兜底 + 前端体验统一**。6 个 bug 修复（P100-P105）：Google 翻译分隔符回归、配置哈希补全、说话人假阳性横杠、前端误报+漏报崩溃。前端 8 项体验优化：免费/付费 tier UI 修复、字幕样式确认按钮、设置变更统一刷新、表单控件高度统一、减少动画偏好、版本号。后端移除 RMS 物理特征检测模块（净减少 ~400 行代码）。 | ✅ 已保存至 `v2/v2.10/` |
| **V3** | **云端语料库 + RAG + 积分体系 + 配置感知去重**。用户账号登录。多语言视频支持。**以 V2.10 为框架。** | 2-4 周 |
| **V4** | 准确性评价系统 + 多语言自动检测。**以 V1.3 为框架。** | 2-3 周 |

### V3 预登记功能：配置感知哈希去重（方案 B）

**来源：** V2.04 迭代——用户发现完成的任务锁死 UI，无法改设置重新跑。方案 A 已修复（解锁 + 允许重跑）。方案 B 作为长期架构升级延后到 V3。

**问题：** 方案 A 的简单解锁有一个局限——如果用户用相同配置跑了两次，系统不会提醒"这个配置你做过了"，会直接创建第二个相同配置的 job。对本地工具来说浪费算力，对 V3 云端版本来说浪费 API 配额。

**方案：** 将哈希索引的 key 从纯 `file_hash`（视频SHA256）升级为 `file_hash + config_hash`（视频 + 参数联合哈希）。

- `file_hash` = 视频文件 SHA256（不变）
- `config_hash` = `sha256(json.dumps(config, sort_keys=True))[:10]`——将模型、翻译模式、上下文感知开关等全部参数序列化后取哈希
- `task_key = f"{file_hash}_{config_hash}"`

**效果：**
- 同一视频 + 同一配置 → 同一个 task_key → 命中旧任务 → "这个配置你做过了，直接下载"
- 同一视频 + 不同配置 → 不同的 task_key → 创建新任务 → 新旧卡片共存可对比
- 用户改回原来的配置 → task_key 重新匹配 → 系统识别："这个配置之前做过"
- 用户用 5 种配置跑同一视频 → 5 条独立的 task_key，5 张卡片，结果互不覆盖

**改动范围：**
- `GLOBAL_HASH_INDEX` key 从 `file_hash` 改为 `task_key`
- `POST /api/jobs` 查重逻辑升级
- `startup()` 磁盘重建索引同步升级
- `meta.json` 增加 `config_hash` 和 `task_key` 字段
- 前端 d.found 增加 done 时"这个配置做过了"的提示逻辑

**延后理由（2026-05-22）：** V2 阶段用户基数 = 1（本地工具），重复配置浪费算力不是当前瓶颈。方案 A 已解决"无法重跑"的核心痛点。方案 B 的配置感知智能更适合 V3 云端——有 API 费用、有账号体系、有多用户并发。届时用户的每一次重复配置都意味着真金白银的浪费。

**⚠️ V3 落地架构避坑（2026-05-22 补充）：** 不要建 N×M 独立索引表。推荐「1 个视频主节点 + M 个配置子挂载」的树形结构——`file_hash` 作为根节点，下面挂多个 `config_hash` 子节点。这样既能享受"配置感知智能去重"，又能避免 N 个视频 × M 种配置带来的索引条目爆炸。关系型设计而非笛卡尔积。

### 版本迭代硬性规则（2026-05-21 生效）

1. **永远从上一个版本复制。** 做 V2.03 → 复制 V2.02 全部文件 → 在 V2.03 里改。不碰 V2.01 及更早版本。
2. **需要改旧代码时，复制到新版本再改。** 旧版本锁定了不改？复制一份进新版本，在新版本里改。原文件永远不动。
3. **旧版本文件夹只读保留。** 不删除、不修改、不覆盖。效果不好随时换回去。

---

## 关键决策

### 产品决策

- **字幕状态**：用户主动选择「有字幕」/「无字幕」，系统检测轨道判断硬/软字幕
- **OCR 方案**：**已砍掉**。理由：(a) 增大应用体积 (b) 增加性能占用 (c) 用户自己知道视频有没有字幕，点一下即可
- **参考项目**：Violin（MIT 开源），研究其 extractor.py / ffmpeg_utils.py / 翻译批处理代码模式，自行重写等价逻辑，不将其作为依赖导入

### 模型决策

- **V1 主引擎**：faster-whisper（CTranslate2 后端，Apple Silicon 原生加速）
- **备选引擎**：whisper.cpp（同一套 Whisper 模型，CoreML 加速，速度更快但需编译安装）
- 两者共用同一套 Whisper 模型权重文件（tiny/base/small/medium/large-v3），无需重复下载

**本地语音转录工具对比：**

| | faster-whisper | whisper.cpp | WhisperX | OpenAI Whisper | SenseVoice |
|------|------|------|------|------|------|
| 底层 | CTranslate2 | 纯 C++ | faster-whisper + 对齐 | PyTorch 原版 | 阿里达摩院 |
| 速度 | 快（3-5x 实时） | 最快（CoreML，5-10x） | 中等 | 慢（0.5-1x） | 快 |
| 内存 | 低 | 最低 | 中 | 高 | 低 |
| 俄语 | 高 | 高 | 高 | 最高 | 中 |
| 多语言 | 99 种 | 99 种 | 99 种 | 99 种 | 中/日/韩/英/粤 |
| 说话人分离 | ❌ 无（需外挂）| ❌ 无（需外挂）| ⚠️ 内置 pyannote（v1.1 已弃用：4.x API 不兼容 + 41 分钟超时）| ❌ 无（需外挂）| ❌ 无（需外挂）|
| 安装 | pip | 需编译 | pip | pip | pip |
| 维护 | 活跃 | 活跃 | 中 | 官方 | 活跃 |

- **弱势语言**：所有 Whisper 系工具对印地语、泰米尔语等支持不如俄语/日语。差距来自模型本身，换引擎不解决问题。V3 后可做"多模型按语言自动路由"
- **片头识别问题（P013，v1.1 已修复）**：VAD 调参方案已被实测推翻（调至 0.4 仍误拦 0-36s 对话，Whisper 对同段给 no_speech_prob=0.00）。v1.1 改为**后处理过滤**：`no_speech_prob > 0.6` + `avg_logprob < -1.0` + 多语言关键词黑名单 + 重复字符检测 + 片头加强。详见后处理过滤规则表
- **翻译质量与模型关系（P014）**：模型越大 → 转录越准 → 翻译越准。Tiny 速度最快但翻译质量最低。V1 默认 medium，如需更高准度用 large-v3
- **V1 选择逻辑**：faster-whisper 安装最简单，先跑通。如果速度不满意，切 whisper.cpp（模型文件通用）
- **说话人识别（V1.1，resemblyzer）**：用 `resemblyzer` 做相邻段声纹比较（Speaker Change Detection），检测到说话人变化时字幕自动加 `- ` 前缀。**已废弃方案**：`whisperx`（依赖 pyannote.audio，4.x API 不兼容 + 25 分钟音频 CPU 满载 41 分钟超时崩溃，详见 P010）
- **背景音过滤**（V2，需重新设计）：原方案用滑动条控制 VAD 阈值来实现背景音过滤。但 v1.1 实测已证明 VAD 不可靠（误拦 0-36s 清晰对话）。**V2 的滑动条应改为控制后处理过滤的严格程度**而非 VAD 阈值。具体来说：滑动条映射到 `no_speech_threshold` 范围（0.4-0.8），往左拉 → 阈值 0.4（激进，丢得多），往右拉 → 阈值 0.8（保守，丢得少）

### 翻译决策

**三层翻译后端：**

| 层级 | 引擎 | 费用 | API Key 存储位置 |
|------|------|------|------|
| 免费版 | Google Translate / 百度翻译 | 免费 | 无需 Key |
| ¥1 档 | 用户自备 Claude/GPT Key | 用户自担 API 费 | **本地**（`~/.video-subber/config.json`），不上传服务器 |
| ¥3 档 | 平台提供的 Key | 平台扣配额 | **服务器**（仅平台 Key，不含用户 Key） |

- **翻译后端可插拔接口**：所有翻译调用通过同一个抽象接口（合同）进行。V1 只实现 Google 免费翻译。V2 提供 API Key 入口——用户粘贴任意大模型 Key（Claude/GPT/Gemini/DeepSeek/豆包等），系统通过同一接口调用。核心流水线一行不动，只新增遵循同一合同的后端类
- **上下文感知翻译**（V2 付费版，Musk 建议）：用户接入任意大模型 API Key（Claude/GPT/Gemini/DeepSeek/豆包等），系统分两步：(1) LLM 先读全文理解语境 (2) 基于理解逐段翻译。V2 提供 API 入口，不内置任何后后端

### 数据决策

- **数据护城河**（Gates 建议）：每处理一个视频，积累「原文 → 译文」对照数据
- **匿名分享**：用户可选择上传翻译数据至云端语料库，默认关
- **垂直领域数据库**：(a) 特摄 (b) 儿童 (c) 动漫 (d) 武打
- **RAG 检索增强**：不训练模型，而是将领域语料作为参考示例写入翻译 prompt 中

### 批量翻译规则（全版本通用，P004·P011·v1.1 实测锁定）

- **Baidu \n 原生批量**（2026-05-20 实测验证）：将多条字幕用换行符 `\n` 拼接，Baidu API 返回 `trans_result` 数组，每个条目对应一行原文。**禁止使用自定义分隔符**（`|||` `@@@` `【】` 均已被证实会被 API 丢弃）
- **每批上限 2000 字符**（Baidu API 安全范围，P019。超出则按 `\n` 边界切分，不在行中间切断）
- **双后端竞速**（每次翻译均执行，非一次选定）：每条/每批翻译同时启动 Google 和 Baidu 两个并行线程，先返回有效结果者胜出。不可因为上次百度先返回就默认只用百度
- **QPS 限制**：批次间 `sleep(1)` 遵守 Baidu 1 QPS 限制
- **重试**：失败后指数退避重试，最多 3 次
- **翻译失败标记**：失败的段落标注为 `[翻译失败]`，必须显示在字幕中（不跳过）

### 翻译缓存规则（全版本通用，P011）

- 每步产出（音频 / 转录 JSON / 翻译 JSON）必须持久化到视频同目录的 `{视频名}.video-subber-cache/` 中
- 下次运行同一视频时，先检查缓存文件是否存在——存在则跳过该步，**禁止重复调用 API**
- 报错/崩溃时不清除缓存，下次运行自动复用
- 仅在全部成功且用户明确传 `--keep-cache` 时才保留缓存，否则成功后清缓存以保持目录整洁

### 架构决策

- **模块化架构**：7 个独立模块（音频提取 / 字幕检测 / 转录 / 上下文理解 / 翻译 / 字幕烧录 / 数据积累），每个可单独暴露
- **翻译后端可插拔**：抽象接口（见下方），所有后端遵循同一合同，加新后端不动流水线代码
- **未来可 API 化**：V1 本地工具，架构预留给将来——任何模块都可作为 API 暴露

### 付费决策

- **保留付费墙**（Musk 建议经用户审视后**推翻**）
- 逻辑：砍掉付费墙，免费用户能用的功能完全一样。留着付费墙，部分用户可付费升级，免费用户无损失。砍＝净亏损。
- **免费版**：Google Translate / 百度翻译。零配置。完整流水线。
- **¥1 档**：用户自备 API Key，存本地缓存文件，不上传服务器。用户自己承担 API 费用。
- **¥3 档**：平台提供 API Key，从平台配额扣。需账号登录，服务器记录每用户 Token 消耗。

---

## 服务器存储（V3 起）

| 存服务器 | 不存服务器 |
|------|------|
| 用户账号 + 密码哈希 | **用户自备的 API Key**（存本地 `~/.video-subber/config.json`） |
| 积分余额 + 积分流水 | 视频文件（始终本地处理） |
| 平台 Key 配额 + 每用户消耗明细 | 翻译中间文件 |
| 匿名语料库（原文 → 译文 + 领域标签 + 准确度评分） | 用户私有的翻译历史 |
| 准确性评分数据 | |

---

## 翻译后端可插拔接口

```python
# 抽象接口。所有翻译引擎遵循此合同。

class TranslationBackend(ABC):
    def translate_batch(self, texts, context="") -> TranslationResult: ...

# 免费后端（2 个）
class GoogleBackend(TranslationBackend): ...   # deep-translator
class BaiduBackend(TranslationBackend): ...    # Baidu \n 批量 API

# LLM 后端基类（V2.06 重构）+ 7 个实现
class LLMBackend(TranslationBackend):          # 公共 prompt 构建 + translate_raw
class ClaudeBackend(LLMBackend): ...           # ~35 行
class GPTBackend(LLMBackend): ...
class GeminiBackend(LLMBackend): ...
class DeepSeekBackend(LLMBackend): ...
class DoubaoBackend(LLMBackend): ...
class GLMBackend(LLMBackend): ...
class CustomOpenAIBackend(LLMBackend): ...     # 用户填 API URL + model
```

---

## 流水线架构

```
视频输入
  ├─ 模块 1: ffmpeg 音频提取            → 独立 / 可单独暴露
  ├─ 模块 2: 字幕轨道检测               → 独立 / 可单独暴露
  ├─ 模块 3: faster-whisper 转录        → 独立 / 可单独暴露
  ├─ 模块 3.5: 后处理过滤（五层六条规则） → v1.1 新增：no_speech_prob + avg_logprob
  │                                          + 关键词黑名单 + 重复检测 + 片头加强
  ├─ 模块 3.6: 说话人变化检测（resemblyzer）→ v1.1 新增：相邻段声纹比较，speaker_change
  ├─ 模块 4: 翻译（Baidu \n 批量 + 双后端）→ 独立 / 可单独暴露
  ├─ 模块 5: 字幕断句 + 时间轴分配          → 独立 / 可单独暴露
  ├─ 模块 6: ffmpeg 字幕烧录               → 独立 / 可单独暴露
  └─ 模块 7: 对照数据积累                  → 独立 / 可单独暴露
     ↓
  输出: SRT 字幕文件 + 可选烧录 MP4
```

### VAD 语音检测规则（v1.1 已废弃，由后处理过滤替代）

**v1.1 实测结论**：silero-vad 对本项目的 25 分钟俄语视频产生严重误拦（0-36s 清晰对话全部丢失，而 Whisper 对同一段给出 no_speech_prob=0.00 的极高置信度）。故 **VAD 默认关闭**，不做事前过滤。详见 [P013](problems.md) / [P015](problems.md)。

**VAD 当前用途**：仅作"语音占比"统计→ 占比 < 20% 警告用户可能为纯音乐，不参与音频切分。

### 后处理过滤规则（v1.1，全版本通用——五层六条规则）

核心原则：**不拦截输入，只过滤输出，多信号交叉验证**（2026-05-20 外部方案评审采纳）。共 5 层 6 条规则（L1 拆为 a/b 两个信号，共享同一层）。

| 层级 | 信号 | 规则 | 拦截场景 |
|------|------|------|---------|
| **L1a** | `no_speech_prob` | nsp > 0.6 **且** `_is_music_like()` 返回 True（三物理特征多数投票）→ 丢弃 | 两法官一致判"非人声" |
| **L1b** | `no_speech_prob` + 物理特征交叉 | nsp 和 `_is_music_like()` 意见冲突 → 保留并标记 `[?]` | 只要一方认为是人声就保留 |
| **L2** | 关键词黑名单 | 含 18 个中/英/俄/日关键词 → 丢弃 | "音乐" "♪" "МУЗЫКА" "拍手" 等 |
| **L3** | 重复字符比 | 唯一字符/总字符 < 0.3 → 丢弃 | "哈哈哈哈" "嘟嘟嘟" 等幻觉（P020） |
| **L4** | 片头加强 | 前 3 条：(字符 < 4 或命中 L2) **且** nsp > 0.2 → 丢弃 | 片头碎段（P023 已修） |
| **L5** | 最短长度 | < 2 字符 → 丢弃 | 单字噪音 |

> **avg_logprob 已废弃**（P022）：该信号衡量"听清每个词没有"，不是"有没有人说话"——用于过滤非人声是用错工具。3 分钟校准片的 logprob 阈值无法代表 25 分钟完整视频，导致 5 模型全部恶化。v1.1 最终只保留 `no_speech_prob` 单一过滤开关。
>
> **RMS 三特征交叉验证（V1.2 已实现，P025）**：L1 已从单信号改为双信号交叉验证。三个物理特征（RMS 能量方差、频谱质心均值、零交叉率均值）多数投票判"像音乐"，与 Whisper nsp 交叉验证：
> - 两个法官都说"不是人声" → 丢弃
> - 一个说是、一个说不是 → 保留，字幕末尾追加 `[?]` 标记
> - 冲突裁决原则：**只要有人认为是人声，就保留**（宁可多给，不能少给——假阳性可忍，假阴性不可忍）
> - 所有模型统一 nsp=0.6（P026 结论：per-model 校准在两个极端失效）
> - 三个物理信号阈值待实测校准（P029 六段素材法），当前使用默认保守值

### 说话人变化检测（v1.1 新增，V2.08 Guard C 重构）

使用 `resemblyzer` 替代 `pyannote.audio`（后者 4.x API 不兼容 + 41 分钟超时崩溃）：
- 对相邻字幕段提取声纹向量（VoiceEncoder）
- Otsu 自适应阈值切分相似度分数（每个视频独立计算）
- 字幕渲染时 `speaker_change=True` 的段加 `- ` 前缀
- **Guard B**：段数 < 5 直接跳过（数据不足不做推断）
- **Guard C（V2，借鉴 V1.3 动态阈值思路）**：两道独立检查，任一未通过即取消全部横杠：
  - 间隙检测（相对）：排序分数后计算相邻间距，max_gap / median_gap < 3× → 无裂缝 → 单人
  - 安全钳（绝对）：Otsu 切分后两组均值差距 < 0.15 → 距离太近 → 单人
- 详情见 [P010](problems.md) / [P020](problems.md) / [P103](problems.md)

### 字幕特殊标记（全版本通用）

字幕中可能出现以下特殊标记，帮助用户理解系统对内容的确定程度：

| 标记 | 含义 | 触发条件 |
|------|------|----------|
| `- ` 行首横杠 | 说话人切换 | resemblyzer 声纹比较 + Otsu 自适应阈值 + Guard C 双重验证（间隙检测 + 安全钳） |
| `[?]` 行尾问号 | 系统不确定是否真的有人在说话 | Whisper 和 RMS 两个法官意见冲突——一个说是人声，一个说不是。**宁可多给，不能少给：只要有一方认为是人声就保留，标记出来让用户自行判断** |

> 用户看到 `[?]` 时：如果画面中确实有人在说话 → 字幕是对的，忽略问号。如果画面中没有人说话 → 这是误判，可以反馈给我们改善系统。

### 字幕时长与断句规则（全版本通用）

- 单条字幕最短 1 秒，最长 6 秒
- **按句切分**：即使原文 segment 只有 3 秒，如果包含多个完整句子（以 。！？. ! ? 为界），每条句子独立成一个字幕，各自按字符数比例分配时间戳
- 例：一个 3 秒 segment 含 3 句话 → 切成 3 条字幕各 ~1 秒，而非一整条从第 1 秒挂到第 3 秒
- 超出 6 秒的单句按标点二次拆分（。！？→ 再 ，；、→ 再 jieba 分词边界）

### 字幕烧录选项（用户可选）

- **默认不烧录**：工具输出 SRT 外挂字幕文件。用户本地播放器可自动加载。保持原视频画质和编码不变
- **可选烧录**：用户勾选「烧录字幕到视频」后，ffmpeg 将字幕文字写入每一帧画面，变成硬字幕。**依赖 ffmpeg 含 libass**（基础版 ffmpeg 不支持——需 `brew install ffmpeg-full`）
- **烧录警告**（选烧录后在勾选框下方醒目展示，非弹窗）：「⚠️ 烧录后字幕将永久固定在视频画面上，无法更改、无法去除。」
- **字幕位置规则（锁定）**：
  - 用户选择「无字幕」→ 中文字幕烧录在画面**底部**
  - 用户选择「有字幕」且视频已有硬字幕 → 中文字幕烧录在画面**顶部**（保留原字幕）
  - 用户选择「有字幕」且为软字幕 → 原软字幕被替换，中文字幕封装进字幕轨道

---

## V4 准确性校准循环

```
翻译完成 → 用户看字幕 → 评分（比如 70%）
     ↓
低分触发校准：
  1. 系统将该条「原文→译文」标记为低质量
  2. 去云端语料库搜索：同语言、同领域、类似句子的高质量翻译（≥90%）
  3. 对比差异，提取原因：
     - 术语翻错？（"変身"→"改变形态" 应为 "变身"）
     - 语境错？（战斗场景翻成日常语气）
     - 断句问题？（一句拆两段，失去连贯）
  4. 修正规则写入 RAG 知识库
  5. 下次翻译同类型内容时，RAG 检索到规则 → prompt 加上修正提示
```

**正循环**：用户越多 → 评分数据越多 → 校准越精准 → 翻译质量随时间上升而非衰减。

---

## 积分体系（V3）

- 用户上传翻译数据至云端语料库 → 获得积分
- 积分 → 兑换平台 API 额度（¥3 档）
- 效果：用户可以用自己的翻译数据"付钱"，而不是用真金白银
- 正循环：翻译越多 → 贡献语料越多 → 积分越多 → 免费用付费功能

---

## 版本锁定规则（2026-05-20 生效）

V1.1 已验证通过，以下模块锁定为基础功能。后续 V2/V3/V4 **只能调用或扩展，不能修改内部逻辑**：

| 锁定的模块 | 文件 | 验证状态 |
|---|---|---|
| 五层后处理过滤（含 L4 nsp 保底 P023、L3 长文本修复 P030） | transcriber.py | ✅ 282保留/22丢弃(1+21)/25存疑 |
| RMS 四信号双法官交叉验证（P025） | transcriber.py | ✅ L1a 仅误杀 1 段 |
| Baidu \n 批量翻译 + 双后端竞速 | translator.py | ✅ 100%成功率 |
| resemblyzer 说话人变化检测 | transcriber.py | ✅ 0.01s加载 |
| checkpoint SHA256 哈希 + 版本化缓存 | checkpoint.py / cli.py | ✅ 最多5版本 |
| 双日志系统（user.log + debug.log）| cli.py / transcriber.py / translator.py / checkpoint.py | ✅ 自动存入 logs/ |
| 多语言关键词黑名单 | transcriber.py | ✅ 18词4语言 |
| 字幕断句两遍切分 | subtitler.py | ✅ |

**需要改基础模块时**：先提示用户 → 说明原因 → 获得同意 → 再动手。

---

## V2 详细方案（2026-05-21 最终确认）

### 一、环境检测（Web UI 顶部）

页面顶部环境检测栏，检测四项。用户不需要理解模型含义，只需要看到是否就绪 + 如何安装。

| 检测项 | 检测方式 | 状态 |
|--------|----------|------|
| **FFmpeg（视频处理）** | `ffmpeg -version` | 绿：已就绪 / 红：未安装 |
| **libass（烧录模块）** | `ffmpeg -version` 输出中检查 `libass` | 绿：已就绪 / 黄：缺 libass（自动切 SRT 模式，禁用烧录选项） |
| **Whisper 转录模型** | 检查 `~/.cache/huggingface/hub/` 下是否有 Whisper 模型文件 | 绿：已安装 / 红：未安装 |
| **resemblyzer 说话人检测** | `from resemblyzer import VoiceEncoder` | 绿：已就绪 / 黄：未安装（可选·首次启动自动下载） |

- 未就绪时旁边出现「如何安装 ▸」按钮，点击展开安装命令，再点收起
- **缺 libass 时自动防护**（V2.07）：检测到 libass 缺失 → 自动切换输出方式为"仅生成 SRT" → 禁用烧录选项下拉框。避免用户跑完全流程最后一步才失败

---

### 二、Web UI 布局（从上到下，7 个区域）

**UI 总原则**：越简洁越清晰。所有人看到同一套 UI，付费功能灰色可见但不可操作。

#### 区域 1：视频上传

大号虚线边框方框，居中图标 + "拖视频到这里，或点击选择"。接受 MP4 / TS / MKV / AVI / MOV / WMV / FLV / WebM。选择文件后显示文件名 + 大小。

#### 区域 2：字幕状态 + 输出方式

上传区正下方，两个设置项：

**字幕状态**：两个按钮切换「无字幕」「有字幕」——告诉系统原视频是否已有字幕，决定烧录时中文字幕放底部还是顶部。

**输出方式**：下拉菜单
- 「仅生成 SRT 字幕文件」
- 「烧录字幕到视频」
- 选烧录时，下方出现红色警告：「⚠️ 烧录后字幕永久固定在视频画面上，无法更改或去除。」

#### 区域 3：模型选择

一条水平横线，上面 5 个可拖拽/可点击的圆点，对应 5 个 Whisper 模型：`tiny` `base` `small` `medium` `large-v3`。圆点均匀分布，与下方箭头左右对齐。

```
     ●────●────●────●────●

         快              慢
         ←───────────────→
         低准确度        高准确度

         当前：medium（推荐，~1.5GB）
```

- 横线下方**一个**双向箭头 `←→`，宽度与上方横线对齐
- 箭头**上方**两端标注：左「快」、右「慢」
- 箭头**下方**两端标注：左「低准确度」、右「高准确度」
- 箭头下方居中加粗：当前选中的模型名称 + 大小提示，拖动圆点松手后实时更新
- 默认选中 `medium`。用户不需要理解模型名，只需理解「往左拖 = 快但不太准」「往右拖 = 慢但很准」

设计要点：圆点横线、箭头、标注文字三者宽度一致、居中对齐，视觉对称。

#### 区域 4：翻译模式

两个按钮切换：

**极速模式**（默认选中）：所有启用的翻译后端并行发送请求，谁先返回有效结果用谁。

**质量优先模式**：按用户设定的优先级依次尝试。下方出现优先级列表 Google / Bing / Baidu / 有道，用户可上下拖动排序。

模式选择自动保存到 `~/.video-subber/config.json`，下次打开恢复。

**关于字幕中的 `[?]` 标记**（翻译模式下方，ℹ️ 图标 + 浅灰色小字）：

如果生成的字幕句尾出现 `[?]`，表示系统对该段翻译的准确性存疑。画面中确实有人在说话 → 字幕大概率是对的，可忽略。画面中没有人说话 → 系统可能误判，欢迎反馈给我们改进。

#### 区域 5：API Key 管理（付费功能区）

**顶部提示**：
- 免费用户：灰字「升级到 ¥1/¥3 档即可启用 LLM 智能翻译」
- ¥1 档用户：正常显示，所有控件可操作
- ¥3 档用户：「✅ LLM 智能翻译已启用，由平台提供 API」

**Key 添加行**（免费用户全部灰色不可操作，¥1 档可用）：

```
[平台下拉: Claude/GPT/Gemini/DeepSeek/豆包]  [命名输入框: 给这个Key起个名字]  [Key输入框: 粘贴API Key]  [添加]
```

- 命名 + 平台双重标识——平台决定发到哪个 API 端点，命名让用户在列表里一眼认出
- 后缀「添加」按钮

**已添加的 Key 列表**：每条显示平台名称 + 用户起的名字 + Key 后四位打码（如 `···a3f2`）+ 删除按钮。免费用户列表为空，添加行灰色。

#### 区域 6：高级设置（可折叠）

默认折叠状态，标题行「高级设置 ▸」，点击展开。免费用户可展开**看到**内容，但全部灰色不可操作。

展开后内容：
- **上下文感知翻译**：两个单选按钮
  - 「逐段翻译」——每条字幕单独翻译，速度快
  - 「上下文感知」——LLM 先理解全文语境再逐段翻译，更准确但耗时更长

#### 区域 7：字幕样式预览

页面最底部：
- 字体 / 字号 / 颜色 / 描边颜色 / 背景色 五个下拉或色块选择器
- 下方黑底预览框：显示示例字幕文字（中日混合），实时反映所有样式设置
- 字幕位置预览（底部/顶部，取决于「有无字幕」选择）

---

### 三、翻译后端（两类，共 9 个）

**免费后端（2 个）**：Google / Baidu。全部通过免费 API 调用，无需 Key。~~Bing / 有道~~（V2.02 已删除——API 太贵）。

**LLM 后端（7 个）**：Claude / GPT / Gemini / DeepSeek / 豆包 / GLM / 自定义平台（OpenAI 兼容）。¥1 档用户自备 Key，¥3 档平台提供 Key。全部继承 `LLMBackend` 基类，每个 ~35 行。自定义平台可接入 Kimi、Ollama、OpenRouter 等任意 OpenAI 兼容 API。

---

### 四、两套翻译流程

**免费用户流程**（与 V1.3 一致）：
```
转录 → 翻译（极速/质量模式，使用免费后端）→ 字幕生成
```

**付费用户流程（LLM）**——逐段翻译：
```
转录 → LLM 逐段翻译 → 字幕生成
```

**付费用户流程（LLM）**——上下文感知翻译：
```
转录完成
  → Step 1: LLM 读全文，理解语境（理解主题/人物/术语，不需要翻译）
  → Step 2: LLM 基于语境理解，逐段翻译（每段带前后各一段作为上下文）
  → 送入字幕生成（subtitler.py，V1.3 锁定模块）
```

---

### 五、极速模式 vs 质量优先模式

**极速模式**（默认）：所有启用的后端并行发送请求。任何一个先返回有效结果 → 立即采用，终止其他等待中的请求。全部失败 → 标记 `[翻译失败]`。

**质量优先模式**：按用户拖拽排序的优先级列表串行尝试。优先级 1 失败 → 自动降级到优先级 2 → … → 全部失败则标记 `[翻译失败]`。不并行，串行降级，优先质量而非速度。

---

### 六、API Key 加密存储 + Token 用量上报

**Key 加密**：
- 所有 LLM Key 用 `cryptography.fernet` 加密后存 `~/.video-subber/config.json`
- 加密密钥绑定本机（machine-specific key derivation）
- Key 只在内存中明文存在，不写入任何日志（`user.log` / `debug.log` 中用正则匹配 `sk-` `ai-` 等前缀打码）

**Token 用量上报**（不入本地、不显前端）：
- 子进程每次 LLM 调用完成后记录：`{job_id, platform, model, input_tokens, output_tokens, duration_ms}`
- 全部收集在内存中（Python list），不落盘
- 任务完成后 POST 到平台服务器 `POST /api/usage`，发送成功则丢弃
- 发送失败 → 重试 3 次，仍失败写入本地临时文件（容灾兜底），下次启动时补发
- **前端不展示任何 Token 用量信息**——用户无需关心底层消耗

---

### 七、架构升级

#### WebSocket 实时进度

替代 HTTP 轮询。后端 `/ws/{job_id}` 端点，步骤变更时主动推送。

| 阶段 | 进度范围 | 显示内容 |
|------|----------|----------|
| 上传 | 0-5% | 上传中… |
| 提取音频 | 5-10% | 正在提取音频… |
| 转录 | 10-40% | 转录中… 第 87/287 段 |
| 翻译 | 40-85% | 翻译中… 第 45/287 段 |
| 字幕生成 | 85-95% | 正在生成字幕文件… |
| 烧录 | 95-100% | 正在烧录字幕到视频… |

#### 子进程 Worker 隔离

流水线在独立子进程中运行（独立的 Python 解释器 + 独立内存空间）。FastAPI 主进程只负责 HTTP/WS 通信 + 任务调度。子进程每完成一步通过 Queue 发进度消息给主进程 → WebSocket 推前端。子进程退出后内存立即归还操作系统。Web 服务器永远轻快响应。

#### 断点恢复 + 逐段错误处理

- V1.3 的 checkpoint 哈希缓存机制直接复用
- 处理中途崩溃或用户关闭页面 → 重新打开，后端检测到同一视频缓存 → 从断点继续
- 单条 segment 翻译失败**不阻塞整个任务**：该条标记 `[翻译失败]`，继续下一条
- 最终汇总「287 条中 285 条成功，2 条失败」，失败明细可展开（编号 + 原文 + 错误原因）

#### ⚠️ V2 开发前置：日志系统从 CLI 切到 Web（P031）

V1.3 用 `_TeeOutput` 劫持 `sys.stdout` 实现双日志。这个方案在 Web 环境下不可用——`sys.stdout` 是进程全局变量，多任务会互相串写。V2 开发时**第一件事**就是废弃 `_TeeOutput`，改用 Python `logging.FileHandler` 为每个任务创建独立日志通道。详见 [P031](problems.md)。

---

### 八、被砍掉的功能

| 功能 | 原因 |
|------|------|
| 背景音过滤滑块 | V1.1 已证明 VAD 不可靠，阈值调整无意义 |
| 后处理阈值手动调整 | 由 V1.3 自适应阈值自动处理，用户不需要管 |
| VAD 阈值暴露给用户 | VAD 已废弃为纯统计用途 |

---

### 九、V2 功能清单（27 项）

| # | 区域 | 功能 | 谁可见 | 状态 |
|---|------|------|--------|------|
| 1 | 上传 | 拖拽 + 点击上传 + 本地路径直读，显示文件名大小 | 所有人 | ✅ |
| 2 | 字幕 | 字幕状态（有无原字幕）+ 说明文字 | 所有人 | ✅ |
| 3 | 字幕 | 输出方式（SRT / 烧录）+ 烧录警告 + 无 libass 自动禁用 | 所有人 | ✅ |
| 4 | 环境 | FFmpeg + libass + Whisper + resemblyzer 四项检测 | 所有人 | ✅ |
| 5 | 环境 | 安装指引（如何安装 ▸） | 所有人 | ✅ |
| 6 | 环境 | resemblyzer 后台静默下载 + 环境栏显示状态 | 所有人 | ✅ |
| 7 | 模型 | Whisper 模型选择滑块（5 点拖拽 + 双向箭头） | 所有人 | ✅ |
| 8 | 翻译 | 极速模式 / 质量优先 切换 | 所有人 | ✅ |
| 9 | 翻译 | 优先级列表拖拽排序（质量模式） | 所有人 | ✅ |
| 10 | 翻译 | `[?]` 标记说明（ℹ️ + 灰色小字） | 所有人 | ✅ |
| 11 | 翻译 | Google / Baidu 免费后端 | 所有人 | ✅ |
| 12 | 付费 | API Key 添加行（平台 + 模型选择 + 命名 + Key + 添加） | ¥1 可操作，免费隐藏 | ✅ |
| 13 | 付费 | 已添加 Key 列表（平台 · 模型 · 命名 · 后四位 · 删除） | ¥1 可操作，免费隐藏 | ✅ |
| 14 | 付费 | ¥3 档提示「平台已启用 LLM」 | ¥3 可见 | ✅ |
| 15 | 高级 | 上下文感知翻译开关（可折叠，无 LLM 时禁用） | 付费可操作，免费禁用 | ✅ |
| 16 | 样式 | 字体 / 字号 / 颜色 / 描边颜色 / 描边宽度 选择器，即时生效 | 所有人 | ✅ |
| 17 | 样式 | 黑底实时预览框（中日混合示例文字） | 所有人 | ✅ |
| 18 | 后端 | 极速模式：N 后端并行竞速 | 所有人 | ✅ |
| 19 | 后端 | 质量优先：固定优先级串行降级 | 所有人 | ✅ |
| 20 | 后端 | ~~Bing + 有道~~ 已删除（V2.02） | — | ✅ 删除 |
| 21 | 后端 | LLM 逐段翻译 | 付费 | ✅ |
| 22 | 后端 | LLM 上下文感知翻译（整篇 marker 方案） | 付费 | ✅ |
| 23 | 后端 | Key 加密存储 + 日志脱敏 | 付费 | ✅ |
| 24 | 后端 | Token 用量上报（内存收集，V3 SERVER_URL 留空） | 付费 | ✅ 框架就绪 |
| 25 | 架构 | WebSocket 实时进度 + HTTP 轮询双通道 | 所有人 | ✅ |
| 26 | 架构 | 子进程 Worker 隔离 + _kill_if_alive 护卫 | 所有人 | ✅ |
| 27 | 架构 | 断点恢复 + 逐段错误明细 + 上传进度百分比 | 所有人 | ✅ |

---

## 功能清单

- [x] ① office-hours — 产出正式设计文档 ✅
- [x] ② plan-ceo-review — 8 提案, 2 纳入 ✅
- [x] ③ plan-eng-review — 架构锁定, 7 模块, 15 测试路径 ✅
- [x] ④ plan-design-review — V2 Web UI 线框图 ✅
- [x] ⑤ plan-devex-review — 3 发现, 无 critical gap ✅
- [x] Phase 1 手动验证：318 段俄语, 305/318 翻译成功 ✅
- [x] V1 CLI 工具 `video-subber` + Web 界面 `video-subber-ui` ✅
- [x] ⑥ V1.2 RMS 四信号交叉验证 — nsp + 4 物理信号双法官（P025）✅
- [x] ⑦ L3 长文本修复 — `len(t) <= 30`（P030）✅
- [ ] ⑧ RMS 特征阈值校准（P029，取决于 [?] 率是否 > 5%）
- [x] V2：27 项功能清单全部完成（V2.01-V2.07 共 7 个迭代），2 免费 + 7 LLM 后端，前后端一致性已审查 ✅
- [ ] V3：云端语料库 + RAG + 积分体系 + 账号系统
- [ ] V4：准确性评价 + 校准循环

---

## 问题库

踩坑记录见 [problems.md](problems.md) —— 每次遇到环境/依赖/API 问题都追加到此文件。

---

## 当前第一优先级

**V2.07 全部功能已完成，前后端一致性审查通过**（2026-05-23：16 项修复，P096）。

### 下一步

| 优先级 | 任务 | 版本 |
|--------|------|------|
| 🔴 立即 | 实际运行 V2.07 完整流水线，端到端验证所有修复生效 | V2.07 |
| 🟡 短期 | 自动化测试（`_is_music_like` 纯函数测试 + 翻译 orchestrator 集成测试） | V2 |
| 🟡 短期 | 用 medium 模型跑完整流水线，验证翻译质量 | V2 |
| 🟢 中期 | V3 云端语料库 + RAG + 积分体系 + 账号系统 | V3 |
| 🟢 中期 | V3 配置感知哈希去重（方案 B，树形结构） | V3 |
| ⚠️ 待定 | P029 六段素材校准（取决于 [?] 率是否 > 5%） | V1.2 |
| ⚠️ 待定 | per-language 阈值评估（取决于跨语言 [?] 是否飙升） | V2+ |

---

## 本次会话改动摘要（2026-05-21）

### 上午 — V2 方案确认
- V2 详细方案写入（27 项功能清单 + 7 区域 UI 布局 + 架构设计），用户逐项确认后定稿
- 同步到 Obsidian vault（`视频中文字幕生成.md`），含 diff 格式（新增/替换/删除）
- 自定义 Prompt 模板功能被砍（用户从未要求）

### 下午 — V2 代码实现
| 文件 | 说明 |
|------|------|
| `v2/pyproject.toml` | 项目依赖（fastapi, uvicorn, python-multipart, faster-whisper, cryptography 等） |
| `v2/backend/__init__.py` | 包初始化 |
| `v2/backend/main.py` | FastAPI 应用：9 个路由 + WebSocket + 静态文件挂载 + startup preload |
| `v2/backend/env_check.py` | 环境检测：check_ffmpeg / check_whisper / check_resemblyzer / preload |
| `v2/backend/ws_manager.py` | WebSocket 连接注册表 + 广播 |
| `v2/backend/worker_manager.py` | 子进程管理：spawn / stdout JSON 读取 / cancel |
| `v2/backend/worker.py` | 子进程 Worker：monkey-patch input() → import V1.3 → 流水线执行 → JSON lines 输出 |
| `v2/backend/config_manager.py` | `~/.video-subber/config.json` CRUD |
| `v2/backend/key_manager.py` | Fernet 加密/解密 + mask + 日志正则脱敏（cryptography 缺失时降级） |
| `v2/backend/usage_reporter.py` | Token 用量内存收集 → POST 服务器 → fallback 文件。**SERVER_URL 留空，V3 上线填 URL 即可启用** |
| `v2/backend/subtitle_style.py` | `build_ass_styled()`：import V1.3 helper，动态 ASS 头（字体/字号/颜色/描边） |
| `v2/backend/translation/__init__.py` | 翻译包 |
| `v2/backend/translation/base.py` | `TranslationBackend` ABC + `TranslationResult` dataclass |
| `v2/backend/translation/google.py` | Google 免费 Web API 后端 |
| `v2/backend/translation/baidu.py` | 百度 \n 批量 API 后端（移植 V1.3 逻辑） |
| `v2/backend/translation/bing.py` | Bing 后端占位 |
| `v2/backend/translation/youdao.py` | 有道后端占位 |
| `v2/backend/translation/orchestrator.py` | 极速模式（并行竞速）+ 质量优先（串行降级）+ 分批 + 上下文感知 |
| `v2/backend/translation/claude.py` | Claude API 后端 + `understand_context` |
| `v2/backend/translation/gpt.py` | GPT API 后端 + `understand_context` |
| `v2/backend/translation/gemini.py` | Gemini API 后端 + `understand_context` |
| `v2/backend/translation/deepseek.py` | DeepSeek API 后端 + `understand_context` |
| `v2/backend/translation/doubao.py` | 豆包 API 后端 + `understand_context` |
| `v2/frontend/index.html` | 单页应用：7 区域 UI + WebSocket + AppState + localStorage + 暗色模式 |

**验证结果**：
- `/api/health` → `{"ok":true,"version":"2.07"}`
- `/api/env` → ffmpeg=True, whisper=True, resemblyzer=True（四项全部显示在环境栏）
- HTML 页面正常加载，标题 `Video Subber V2`
- resemblyzer 后台静默加载（0.01s）

## 历史会话改动摘要（2026-05-20）

| 改动 | 文件 | 问题编号 |
|------|------|----------|
| L4 加 `no_speech_prob > 0.2` 保底 | transcriber.py | P023 |
| 版本化缓存（SHA256 分目录，最多 5 版本） | checkpoint.py / cli.py | P018 扩展 |
| 双日志系统（user.log + debug.log → logs/） | cli.py / transcriber.py / translator.py / checkpoint.py | 新增 |
| L3 叠字名安全性评估 | — | P024（无需修改） |
| RMS 四信号双法官交叉验证（+频谱峰度） | transcriber.py | P025/P027 |
| 过滤阈值集中管理 `FILTER_CONFIG` | transcriber.py | — |
| L3 长文本修复 `len(t) <= 30` | transcriber.py | P030 |
| 六段素材校准方案 | problems.md P029 | P025/P029 |
| V2 清单精简（14→9 项，砍 5 项） | video-subtitle.md | — |
| V2 翻译后端方案（4 平台并行 + 质量开关，竞速统计→V3） | video-subtitle.md | — |
| RMS 三特征双信号交叉验证（V1.2） | transcriber.py / subtitler.py / vad_filter.py | P025 已实现 |
| per-model 校准阈值废弃，统一 nsp=0.6 | transcriber.py | P026 |


## 本次会话改动摘要（2026-05-22）

### 代码审查
- 首轮完整审查（code-review-excellence skill）：16 后端 + 1 前端，检出 12 个问题（3🔴 + 5🟡 + 4🟢），已修 1 个（P040 cancelCard）
- 第二轮完整审查：新增 4 个问题（P048-P052）
- 生成工程师交付报告 `v2-bug-report.md`（14 条问题，含代码、根因、修复建议、优先级）

### large-v3 片头合并问题
- 根因分析：large-v3 将片头音乐+第一句对话合并为长段 → L4 绕过
- Steve Jobs 视角评审：拒绝方案 A（打补丁）、方案 B（退回 medium）、方案 C（VAD 已证失效）
- 外部工程师 7 方案评估表（D 词戳修剪、E 语速推定、F 波形切割、G Prompt 引导）
- 终审方案：D+F 组合管道（D 主力 + F 兜底），插在 Whisper 输出后、五层过滤前

### V2.05 实现
| 文件 | 改动 |
|------|------|
| `v2/backend/transcriber_v2.py` | +`word_timestamps=True`（1 行） |
| `v2/backend/transcriber_v2.py` | +Phase 2.5 净化预处理管道（~50 行） |
| `v2/backend/transcriber_v2.py` | +`_find_audio_mutation_point()`（~40 行） |
| `problems.md` | +P053（large-v3 片头合并修复记录） |
| `video-subtitle.md` | +V2.05 版本条目 |
| `v2-bug-report.md` | 代码审查问题报告（14 条，供外部工程师） |
| `large-v3-intro-merge-issue.md` | 片头问题技术报告（供外部工程师） |
| `v2.05-intro-fix-plan.md` | 最终实现方案（供外部工程师审核） |

**关键约束**：五层过滤规则 0 行改动，FILTER_CONFIG 0 行改动，Worker 调用 0 行改动。预处理管道与下游完全解耦。


---

## 前端调试标准流程（2026-05-22 · P042 经验总结）

当用户报告"代码改了但页面行为没变"时，按以下顺序排查：

1. **排除浏览器缓存。** DevTools → Network → 勾选 Disable cache。`Cmd + Shift + R` 强刷。
2. **验证 payload。** 找到 `/api/jobs` 请求 → Request Payload → config 字段。对比 payload 内容与磁盘上 `index.html` 对应代码行的逻辑。若 payload 缺少磁盘代码应有的字段（如 `force: true`），说明浏览器加载的是旧版——重复步骤 1。
3. **验证后端接收。** 确认后端对应端点（`/api/jobs`）的接收逻辑与前端 payload 字段名一致。
4. **验证磁盘代码。** `grep` 确认关键逻辑行确实存在于磁盘文件中，排除编辑器未保存或文件路径错误。

**常见误判避免：**
- 不要只读磁盘代码就断言"逻辑没问题"——必须同时看浏览器实际运行的版本
- 不要在 Network 未开的情况下猜测"应该发出去了"
- 如果前后端都确认无误，优先怀疑中间层（缓存、代理、CDN）

---

## V2.06 前端修改说明（2026-05-22）

### 修改 1：上传区加 5GB 提示 + 413 错误拦截

**为什么改**：后端 R02 新增了 5GB 上传上限（流式写入 + 超限 413 响应）。前端需要提前告知用户边界，并在后端拒绝时展示友好提示而非静默失败。

**改动点**：
- `dz-hint` 文本从 `MP4 / TS / MKV / AVI / MOV / WMV / FLV / WebM` → 追加 ` · 最大 5GB`
- `sbtn` click handler：`fetch` 返回后先检查 `!r.ok` → 解析 error JSON → `showToast()` → 恢复按钮状态 → return
- catch 块：`sbtn.textContent='开始翻译'` → `refreshStartBtn()` + `showToast(msg)`

### 修改 2：自定义平台 Key 的 UI 支持

**为什么改**：后端 R08 新增了 `LLMBackend` 基类架构和 `CustomOpenAIBackend`——用户填入任意 OpenAI 兼容的 API URL 即可接入任意大模型平台（Kimi、Ollama、OpenRouter 等）。前端之前没有对应的 UI 入口。

**改动点**：
- `LLM_BACKEND_LIST` 加 `{"id": "custom", "name": "自定义平台 (OpenAI 兼容)", "models": [...]}`
- `apiRow` 加两个隐藏 input：`apiEndpoint`（API 完整地址）、`apiModelName`（模型名）
- `updateModelSelect()`：选 custom 时隐藏预设模型下拉，显示端点/模型名输入框
- `apiAddBtn` click：custom 时从输入框收集 `api_endpoint` 和 `model`，非 custom 时走原逻辑
- `buildKeyList()`：custom 平台显示 `自定义(gpt-4o)` 格式，区分于预设平台

### 修改 3：`jobId` 死变量 + `unlockUI` 冗余逻辑清理

**为什么改**：多任务架构（job cards）上线后，`jobId` 变量从未被赋值（永远 null），`unlockUI` 中的 `if(!jobId)` 永远为 true，靠巧合工作而非设计。按钮状态已由 `checkAllDone()` 正确管理。

**改动点**：
- 删除 `let jobId=null,ws=null;`
- `unlockUI()`：删除 `if(!jobId)sbtn.disabled=false;`

### 修改 4：本地文件路径直读——大视频零上传（V2.06 新增）

**为什么改**：5GB 硬限制是旧代码 `file.read()` 全量加载的遗留物。R02 流式上传已解决内存问题，但 4K/8K 时代的高清电影动辄 10GB-50GB+，用户既想保留完美画质又想加字幕。既然前后端都在同一台机器上（本地 Web 应用），让用户通过 HTTP 把 50GB 文件从磁盘读到内存再写回磁盘是荒谬的——Python 可以直接 `open()` 读取本地文件。

**产品设计（Steve Jobs 方案）**：
- 拖拽/点击上传：保留，给 90% 普通用户。简单、直觉。
- 粘贴本地路径：新增，给 10% 大文件用户。零上传时间，零内存压力。
- 硬 5GB 限制→软建议：「建议不超过 5GB」而非「最大 5GB」。
- 两种模式并存，互不冲突。

**前端改动点**：
- 上传区下方加 `localPathInput` 文本框：`placeholder="如 /Users/andy/Movies/video.mp4 或 D:\Movies\video.mp4"`
- `State` 加 `localPath` 字段
- `localPathInput.addEventListener('input')`：检测到路径→清掉 videoFile→启用按钮。清空→恢复默认。
- `selectFile()`：选中文件时清掉 localPath（两种模式互斥）
- `sbtn click handler`：拆分为两条路径——`if(State.localPath)`→JSON POST（含 `local_path`）→后端直读本地文件；`else`→FormData POST→流式上传。共享 `handleFoundJob()` 去重逻辑。
- `dz-hint`：`最大 5GB`→`建议不超过 5GB`

**后端改动点**：
- `create_job` 参数从 `(file: UploadFile, config: Form)` 改为 `(request: Request)`，手动解析 Content-Type
- `application/json`→本地路径模式：`os.path.isfile()` 验证→8MB 分块流式读本地文件→同时 SHA256+写入 job_dir
- `multipart/form-data`→文件上传模式：保持流式上传逻辑
- 删除 `MAX_UPLOAD_BYTES` 硬限制
- 两种模式共享后续的去重索引+任务创建代码

**用户效果**：
- 普通视频（几百 MB）：拖进去→上传→处理。体验不变。
- 大视频（10GB+）：复制路径→粘贴→点开始。0 秒"上传"，直接开始转录。画质零压缩。
- 用户在文件管理器里 Cmd+C 复制视频路径→切到浏览器 Cmd+V 粘贴→点按钮。两步，三秒。

---

## 本次会话改动摘要（2026-05-23）

### V2.07 Phase 2 — 前后端一致性审查 + 16 项修复（P096）

全面审查 V2.07 前端 `index.html`（58KB）和后端 8 个 Python 模块，从**开发者视角**（前后端功能一致性）和**用户视角**（操作体验）两个维度检查。

| 改动 | 文件 | 分类 |
|------|------|------|
| 模型列表从硬编码 `PLATFORM_MODELS` 改为后端动态数据 `State._llmModels` | index.html | 一致性 |
| 下载接口添加 `job_dir.glob()` 磁盘回退（重启后可下载） | main.py | 一致性 |
| `_tracking_send` 新增 `token_usage` 消息处理 | main.py | 一致性 |
| 环境栏新增第四项 resemblyzer 状态指示 | index.html | 一致性 |
| 版本号统一为 `"2.07"`（health + .version） | main.py | 一致性 |
| 新增描边宽度选择器 (1-4px) | index.html + subtitle_style.py | 一致性 |
| Tier 注释修正为 "Google + Baidu" | main.py | 文档 |
| 样式更改即时生效，去掉确认门槛 | index.html | 用户体验 |
| 无 libass 时自动切 SRT + 禁用烧录选项 | index.html | 用户体验 |
| 取消任务前弹出确认对话框 | index.html | 用户体验 |
| 服务端配置加载后 `syncUI()` 刷新界面 | index.html | 用户体验 |
| 开发者齿轮按钮始终可见（不再鸡蛋问题） | index.html | 用户体验 |
| 文件上传改用 `XMLHttpRequest`，显示上传百分比 | index.html | 用户体验 |
| 选择新文件时重置 `forceRerun` | index.html | 逻辑修复 |
| "有字幕"开关旁添加说明文字 | index.html | 用户体验 |
| 无 LLM 时禁用"上下文感知"按钮 | index.html | 用户体验 |

**验证**：Python 语法 + JS 语法全部通过。v2.07/ → v2/ 已同步。