# Video Subber V1.1 — 改动说明

**版本基础**：从 v1 复制。v1.1 在 v1 CLI 基础上做转录质量修复 + 翻译引擎重写 + 说话人变化检测。

**改动主线**：修复 problems.md 中 P010 / P013 / P015 / P016 / P017 / P018 / P019 / P020。

**最终验证**（2026-05-20）：287 段俄语视频，翻译 287/287 = 100%，0 幻觉，217 处说话人变化，resemblyzer 0.01 秒加载。

---

## 改 1：VAD 废弃 → 五层后处理过滤（P013 + P015 + P017 + P020）

### 修改文件
- `video_subber/transcriber.py`
- `video_subber/vad_filter.py`
- `video_subber/cli.py`

### 核心改动

**A. VAD 默认关闭（P013 + P015 实测结论）**
- silero-vad 对本项目俄语视频 0-36s 清晰对话产生严重误拦（Whisper 给同段 no_speech_prob=0.00）
- VAD 仅保留"语音占比"统计作为健康检查（<20% 警告）
- 用户可通过 `--vad-threshold 0.4` 显式启用内置 VAD

**B. 五层后处理过滤（不拦截输入，只过滤输出）**

| 层 | 信号 | 规则 | 来源 |
|---|---|---|---|
| L1a | `no_speech_prob` | > 0.6 → 丢 | P013 |
| L1b | `avg_logprob` | < -1.0 → 丢 | **P020 外部评审采纳** |
| L2 | 关键词黑名单 | 含 18 个中/英/俄/日词 → 丢 | P017 |
| L3 | 重复字符比 | 唯一/总字符 < 0.3 → 丢 | **P020 外部评审采纳** |
| L4 | 片头加强 | 前 3 条 < 4 字符或命中 L2 → 丢 | P013 |
| L5 | 最短长度 | < 2 字符 → 丢 | P013 |

**C. 多语言关键词黑名单（P017）**
- 中文：音乐 掌声 笑声 鼓掌 谢谢观看 请订阅 字幕组 字幕由
- 英语：music ♪ ♫ [music] [applause] [laughter]
- 俄语：музыка МУЗЫКА аплодисменты смех
- 日语：音楽 拍手 笑い声

**D. CLI 新增参数**
- `--vad-threshold`（默认 0.0 = 关，0.4 启用）
- `--no-speech-threshold`（默认 0.6）
- `--no-filter-keywords`（关闭 L2+L4 关键词）

### 外部方案评审贡献（2026-05-20）
- 核心原则："不拦截输入，只过滤输出，多信号交叉验证"
- `avg_logprob`：Whisper 免费送的置信度信号，< -1.0 表示"很确定自己没听对"
- 重复字符检测：幻觉常表现为同一字重复多次（唯一字符比极低）
- VAD 争议已通过实证解决：对方初始推荐 VAD 前置过滤，我们通过对照实验证明不可行，对方已修正为后置过滤方案，双方方案达成一致

---

## 改 2：说话人变化检测——pyannote → resemblyzer（P010）

### 修改文件
- `video_subber/transcriber.py`（新增 `_detect_speaker_changes`，移除 `_diarize_segments`）
- `video_subber/subtitler.py`（`speaker_change` 布尔标记替代 speaker 标签）
- `video_subber/cli.py`（`--no-speaker-detect` / `--speaker-threshold` 替代 `--diarize`）

### 核心改动

**A. 为什么放弃 pyannote**
- 41 分钟 CPU 满载后崩溃（API 不兼容 + 全局聚类太慢）
- 4 个模型条款需手动接受、Token 配置门槛高

**B. resemblyzer 替代方案**
- 模型大小：几 MB（vs pyannote 500MB）
- 加载时间：0.01 秒（vs pyannote 41 分钟）
- 算法：相邻段声纹比较（vs pyannote 全局聚类）
- `speaker_change` 布尔标记替代 speaker 标签

**C. subtitler.py 渲染**
- `speaker_change=True` → 行首加 `- ` 前缀
- `speaker_change=False` → 正常显示

**D. CLI 新参数**
- `--no-speaker-detect`：关闭说话人变化检测（默认开启）
- `--speaker-threshold`（默认 0.75）：余弦相似度阈值，越低越容易判换人

### 验证结果
- ✅ 287 段俄语对话，检测到 217 处说话人变化
- ✅ SRT 中 341 行带 `- ` 前缀
- ✅ 加载时间 0.01 秒

---

## 改 3：翻译引擎重写（P004 + P016 + P019）

### 修改文件
- `video_subber/translator.py`（完全重写）

### 核心改动

**A. Baidu `\n` 原生批量（P016）**
- 关键发现：`trans_result` 是数组，我们一直只取了 `[0]`，后面的全丢了
- 用 `\n` 拼接原文 → API 返回多个 `{"src":"...","dst":"..."}` 条目
- 不再使用自定义分隔符（`|||` `@@@` `【】` 均被证实不可靠）

**B. 双后端竞速（每批均执行）**
- Google ∥ Baidu 并行线程，先返回有效结果者胜出
- 不可因某次 Baidu 先返回就默认"以后都用百度"

**C. 字符上限 2000（P019）**
- 原 5000 → 改为 2000（Baidu API 安全范围）
- 287 句 = 4 批（5000 时仅 2 批，但依赖 API 容错）

**D. 翻译失败可见**
- 失败的段标注 `[翻译失败]`，出现在字幕中（不跳过）

### 验证结果
- ✅ 287/287 = 100% 翻译成功率，0 失败

---

## 改 4：checkpoint 哈希验证（P018）

### 修改文件
- `video_subber/cli.py`

### 核心改动
- `checkpoint.py` 中的 SHA256 哈希验证 + 阶段恢复已实现但从未被 CLI 调用
- 启动时对比输入文件哈希与缓存中的旧哈希 → 不一致则自动清除缓存重跑
- `--force` 仍强制清除

---

## 改 5：condition_on_previous_text + log_prob_threshold（P013）

### 修改文件
- `video_subber/transcriber.py`

### 核心改动
- `condition_on_previous_text=False`：防止 Whisper 片头幻觉"传播"到后续段（外部建议采纳）
- `log_prob_threshold=-1.0`：过滤低置信度段

---

## 改 6：`[翻译失败]` 在字幕中可见

### 修改文件
- `video_subber/subtitler.py`

移除了 `if text == "[翻译失败]": continue` 的跳过逻辑。翻译失败的段会作为字幕显示。

---

## CLI 完整参数清单（v1.1 最终）

```
video-subber <input> --subtitles yes/no [options]

转录控制：
  --model {tiny,base,small,medium,large-v3}   默认 medium
  --lang LANG                                  默认 auto
  --no-vad                                     跳过 VAD 占比检测
  --vad-threshold 0.0                          内置 VAD 阈值 (0=关, 0.4 启用)
  --no-speech-threshold 0.6                    no_speech_prob 阈值
  --no-filter-keywords                         关闭关键词黑名单

说话人变化检测：
  --no-speaker-detect                          关闭检测 (默认开启)
  --speaker-threshold 0.75                     余弦相似度阈值

翻译/烧录：
  --backend {google,baidu}                     默认 baidu
  --no-burn                                    仅 SRT
  -o, --output PATH                            输出路径

缓存：
  --keep-cache                                 保留缓存
  --force                                      清除缓存重跑
```

---

## 与各版本的关系

- **v1**：原始 CLI（外层 VAD + 基础概率过滤）—— 只读保留
- **v1.1**：在 v1 基础上修复 P010/P013/P015/P016/P017/P018/P019/P020
- **v2**：Web UI 分支（独立路线）

---

## 2026-05-20 后验证改动（v1.1 线内）

以下改动在 v1.1 已验证通过后追加，不影响已锁定的基础模块核心逻辑。

### 改 7：L4 片头加强 — 加 no_speech_prob 保底（P023）

**修改文件**：`video_subber/transcriber.py`

L4 规则从 `(短 or 命中L2) → 丢弃` 改为 `(短 or 命中L2) and no_speech_prob > 0.2 → 丢弃`。

场景：片头音乐后有人喊短称呼（如 `"Саш!"` 3 字符、`"小明!"` 3 字符）。旧规则因 < 4 字符直接丢弃。新规则下若 `no_speech_prob ≤ 0.2`（Whisper 很确定是人声），即使短也保留。

### 改 8：版本化缓存（P018 扩展）

**修改文件**：`video_subber/checkpoint.py`、`video_subber/cli.py`

缓存目录从平铺结构改为按文件 SHA256 哈希分版本子目录存储。同名但内容不同的文件各自保留独立缓存，最多 5 个版本（`--max-cache-versions` 可调）。拖错文件不会覆盖旧缓存，再拖回原文件可断点续传。

### P024（已记录，无需修改）

L3 重复字符检测对"小小""丹丹"等叠字人名安全——`len > 4` 前置条件天然保护短名。暂无代码修改。

### 改 9：双日志系统（user.log + debug.log）

**修改文件**：`video_subber/cli.py`、`video_subber/transcriber.py`、`video_subber/translator.py`、`video_subber/checkpoint.py`

日志统一输出到 `{视频所在目录}/logs/` 文件夹：

| 文件 | 内容 | 写给谁 |
|------|------|--------|
| `{视频名}_{时间戳}_user.log` | 终端输出的完整副本 | 用户——看流程进度和统计数字 |
| `{视频名}_{时间戳}_debug.log` | 代码级细节 | 开发者——定位 bug |

**user.log** — 通过 `_TeeOutput` 类劫持 `sys.stdout`，所有 `print()` 自动双写。跟终端看到的完全一致。

**debug.log** — 使用 Python 标准 `logging` 模块，记录：
- 每个 segment 的 `start / end / no_speech_prob / avg_logprob / text` 和过滤决策（`DROP L1a / DROP L2 / DROP L4 / DROP L5 / KEEP`）
- 说话人变化检测（每处变化的 segment ID + 相似度值）
- 翻译每批的字符数、尝试次数、失败原因
- 缓存命中/新建/清理等版本管理操作
- 所有异常的错误详情

**不干扰终端**：debug.log 只写文件，不在屏幕上刷屏。

---

## 已通过的验证

- [x] P013 片头幻觉：0 条（五层过滤 + 多语言黑名单）
- [x] P015 早期对话：首条 6.48s（0-6s 被 L1/L2 正确过滤）
- [x] P010 说话人变化：217 处，341 行 `- ` 前缀（resemblyzer 0.01s）
- [x] 翻译：287/287 = 100%（Baidu \n 批量 + Google ∥ Baidu 双后端）
- [x] P016 批量翻译根因：trans_result 是数组
- [x] P017 多语言黑名单：0 条俄语音乐幻觉
- [x] P018 checkpoint 哈希 → 升级为版本化缓存（5 版本上限）
- [x] P019 字符上限：2000 安全范围
- [x] P020 avg_logprob + 重复字符检测：重复字符检测保留，avg_logprob 已废弃
- [x] P022 avg_logprob 导致 5 模型恶化：砍掉，只保留 no_speech_prob
- [x] P023 L4 + no_speech_prob 保底：短人称不再被误杀
- [x] P024 L3 叠字名安全评估：已确认无需修改
