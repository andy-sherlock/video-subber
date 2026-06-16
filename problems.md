# 问题库 — Video Subber 踩坑记录

每条记录：编号 / 日期 / 步骤 / 问题 / 原因 / 解决方案 / 是否已修

---

## 🔧 遗留未修（5 个 · V3.08+ 处理）

### 代码 Bug

| # | 编号 | 定位 | 问题 |
|---|------|------|------|
| 1 | P181 | `frontend/index.html` | handleFoundJob 跳过 connectCard |
| 2 | P245 | `main.py:69` | GLOBAL_HASH_INDEX 无锁 |

### 架构债

| # | 编号 | 定位 | 问题 |
|---|------|------|------|
| 3 | P191 | `transcriber_v2.py` | transcribe() 圈复杂度 41 |
| 4 | P219 | 6 文件 | 6 个函数复杂度过高（codegraph 审计） |

### 功能未实现

| # | 编号 | 定位 | 问题 |
|---|------|------|------|
| 5 | P223 | `qwen_aligner.py` | FA 分段对齐并行化（⏸ V3.10+） |

### 待修 Bug（后续批次新增）

| # | 编号 | 定位 | 问题 |
|---|------|------|------|
| ~~6~~ | ~~P258~~ | ~~`worker.py:587-595`~~ | ~~UI [?] 百分比分子分母不匹配 → 误报 15%~~ ✅ 已修（分子分母改为同一阶段计算） |
| 7 | P259 | `hallucination_filter.py` | 跨语言 L3 语速阈值未用真实多语言视频验证（🟡 MEDIUM） |
| ~~8~~ | ~~P260~~ | ~~`worker.py:431-511`~~ | ~~P247 Refiner 放错位置~~ ✅ 已修（Refiner 在翻译前执行） |
| ~~9~~ | ~~P261~~ | ~~`transcriber_v2.py:760-768`~~ | ~~P254 合成段导致 L1 失效~~ ✅ 已修（L1 在 Whisper 原稿上跑，组装在后） |

### 平台适配（V3.09 独立分支）

| # | 编号 | 定位 | 问题 |
|---|------|------|------|
| — | P266 | `transcriber_v2.py:253` | **Mac 端 Whisper 换 mlx-whisper**。faster-whisper 在 Mac 上无 GPU 后端（CTranslate2 不支持 Metal），50min 视频需 100min。换 Apple 官方的 mlx-whisper（Metal GPU），预计 ~5min。先不动模型（保持 large-v3），引擎换完拿到加速后再评估切 turbo。单独开 Git 分支，V3.09 执行。 |
| — | P267 | `transcriber_v2.py` + `qwen_asr_runner.py` | **Windows 端双引擎适配**（2026-06-13 调研完成）。方案：① **Whisper**：沿用 faster-whisper + CUDA + int8_float16 + batch_size=8（全球最快 Whisper 实现，RTF 0.02，1h 音频约 1.3min，不需要换）；② **Qwen3-ASR**：用官方 qwen-asr + PyTorch CUDA（0.6B=2-3GB 显存, 1.7B=4-5GB 显存），备选 Qwen3-ASR-GGUF 混合引擎（ONNX encoder + llama.cpp decoder，RTF 0.05，快 7.5 倍但社区维护）。待 Mac 端 P266 完成后 Windows 真机实测。V3.09 执行。 |

### Whisper 模型选型参考（2026-06-13 调研）

**术语：**
- **fp16** = 全精度（16 位浮点），最准但吃显存
- **int8** = 压缩版（8 位整数），精度损失极小（WER 几乎不变），显存省 35%
- **batch=N** = N 路并行处理，快但更吃显存

**各模型对比（日语基准）：**

| 模型 | 参数量 | 速度 | 日语 JSUT CER | 日语 TEDx CER | 多语言 WER | 要点 |
|------|--------|------|-------------|-------------|-----------|------|
| **large-v3**（当前） | 1.55B | 基准（1x） | — | **9.2%** | **12.3%** | 质量天花板 |
| large-v3-turbo | 809M | **快 4-6x** | 12.0% | 9.5%（+0.3） | 14.8%（+2.5） | 速度/质量最优平衡 |
| large-v2 | 1.55B | ~1x | — | 9.9%（+0.7） | — | 上代，比 turbo 慢且差 |
| medium | 769M | 快 | — | 11.0%（+1.8） | — | 不推荐（turbo 全方位碾压） |
| distil-large-v3 | 756M | 最快 | 不支持 | 不支持 | — | 仅英文，不考虑 |

**结论：**
- **turbo 在日语上退化极小**（CER 9.2→9.5%，仅 +0.3 点），但速度快 4-6 倍
- turbo 比 v2 更快、更准——v2 没有存在的理由
- **注意**：turbo 在日语音频中有英単語幻覚（hallucination）报告，且翻译任务退化。我们只用转录（不用翻译），但幻觉问题需要在实测中验证
- **策略**：P266/P267 先稳住 large-v3 换引擎，引擎换完后如果还嫌慢，切 turbo 实测日语素材，幻觉不严重就直接换

> **已关闭/已修（从原 15 个批次中清除）**：P225 ✅（TDD 修复）· P242 ✅（死代码，换库后删除）· P250 ✅ · P251 ✅ · P253 ✅ · P254 ✅ · P196 ✅（双签审查已写入 CLAUDE.md）· P247 ✅ · P252 ✅（走查 2.0 已激活）· P265 ✅（中点落入 + CJK 无空格拼接）
>
> **已关闭（换 MLX ASR 库自然解决 · 2026-06-11）**：P179 · P212 · P149/P171。
>
> **其他未修问题**（UX 7 个 → V3.09 PySide6 / 功能缺失 3 个 / 环境打包 3 个 / 外部限制 1 个）不在本批次，各自跟版本走。

---

## P001 | 2026-05-19 | 环境搭建 | pip install 不可达 pypi.org

**问题**：`pip install` / `uv pip install` 报 SSL EOF，pypi.org 不可达。

**原因**：国内网络环境，pypi.org 被墙。

**解决方案**：用清华镜像：
```
uv pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple <package>
```

**已修** ✅

---

## P002 | 2026-05-19 | 环境搭建 | rm -rf .venv 被权限拒绝

**问题**：想删除旧的虚拟环境重建，`rm -rf .venv` 被 Claude Code 安全策略阻止。

**解决方案**：用 `uv venv --clear` 替代，不需要手动删。

**已修** ✅

---

## P003 | 2026-05-19 | 环境搭建 | source .venv/bin/activate 找不到文件

**问题**：在非项目目录下执行 `source .venv/bin/activate` 报 "no such file"。

**原因**：`.venv` 是相对路径，切换到其他目录后失效。

**解决方案**：始终在项目根目录 `/Users/andy/Andy-agent/video-subtitle/` 下执行激活命令，或用绝对路径：
```
source /Users/andy/Andy-agent/video-subtitle/.venv/bin/activate
```

**已修** ✅

---

## P004 | 2026-05-19 | Step 3 翻译 | deep-translator GoogleTranslator 100% 失败

**问题**：用 `deep_translator.GoogleTranslator` 批量翻译，318/318 条返回空或失败。

**原因推测**：
1. 国内网络间歇阻断 Google
2. 请求频率过高触发限流
3. deep-translator 依赖 Google 网页抓取，可能版本有 bug

**解决方案**：
1. 改用 `translate.googleapis.com` 直连 API（非网页抓取）
2. 每条翻译间隔 0.15 秒防止限流
3. 失败时自动重试最多 3 次，指数退避
4. 每 20 条打印一次进度

**关键代码**：
```python
import urllib.request, urllib.parse, json
def translate_google(text, timeout=10):
    q = urllib.parse.quote(text)
    url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=zh-CN&dt=t&q={q}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=timeout)
    data = json.loads(resp.read().decode())
    return "".join([s[0] for s in data[0] if s[0]])
```

**已修** ✅（方案已切换，验证通过）

---

## P005 | 2026-05-19 | 整体流程 | 一次请求都没验证就先跑全量

**问题**：翻译脚本写好直接跑 318 条，结果全部失败，浪费了等待时间。

**教训**：先跑 1-2 条测试，成功后再跑全量。

**已修** ✅（流程已改为先 `test_translate.py` 测试，再全量）

---

## P006 | 2026-05-19 | Step 2 转录 | 首次运行等待极久

**问题**：faster-whisper 首次运行需要从 HuggingFace 下载 1.5GB 模型，国内下载可能很慢。

**教训**：
1. 首次运行前提醒用户"首次运行需下载 ~1.5GB 模型"
2. V1 CLI 需自带模型下载进度条
3. 从 `HF_TOKEN` 或镜像加速下载

**已修** ✅（设计文档已覆盖模型下载 UX，提示 + 进度条）

---

## 经验总结

1. 国内开发环境：PIP 用清华镜像 / Google API 用 translate.googleapis.com 直连 / HuggingFace 下载慢需预留时间
2. 跑脚本前：先测 1 条 → 成功 → 再跑全量。不写 300+ 的脚本一上来就跑
3. venv 路径：始终用绝对路径，或在项目根目录操作
4. 每条批量 API 调用的间隔是必须的——0.15 秒/条是 Google Translate 的安全线

---

## P007 | 2026-05-20 | Step 4 烧录 | ffmpeg 基础版缺 libass，连续 4 次烧录失败

**问题**：`ffmpeg -vf subtitles=xxx.srt` 报 "No option name near" / "Error parsing filterchain"。换了绝对路径、相对路径、不同引号、ASS 格式均失败。

**根因**：Homebrew `brew install ffmpeg` 安装的基础版未编译 `--enable-libass`。`subtitles` 和 `ass` 滤镜均不存在。

**解决方案**：`brew install ffmpeg-full`（keg-only，路径 `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`）。验证：`ffmpeg -version | grep libass` 有输出 = 合格。

**教训**：V2 Web UI 必须检测 ffmpeg 是否含 libass，基础版虽然能提取音频但无法烧录字幕。

**已修** ✅

---

## P008 | 2026-05-20 | Step 4 烧录 | ASS 字幕跑到画面右侧而非顶部

**问题**：用户反馈成品视频中中文字幕显示在画面**右侧**，而非要求的**顶部**。原视频底部已有英文硬字幕。

**根因**：ASS Alignment 编号采用小键盘布局：7=左上、8=上中、9=右上 / 4=左中、5=中中、6=右中 / 1=左下、2=下中、3=右下。代码中误写 `Alignment=6`（右中），正确应为 `Alignment=8`（上中）。

**用户反馈**：「我要求的是在下方已有字幕的情况下，将新字幕生成在视频的最上方，但你却生成到了视频的最右方」

**修复**：ASS Style 行 `Alignment=6` → `Alignment=8`，MarginV=50（距顶部 50px）。

**已修** ✅

---

## P009 | 2026-05-20 | Step 4 烧录 | 任务通知"完成"但 ffmpeg 仍在编码

**问题**：后台 bash 任务通知 "completed (exit code 0)"，于是打开"成品"文件。QuickTime 报："The file isn't compatible with QuickTime Player"。风扇仍在狂转。

**根因**：bash wrapper 进程退出，但 ffmpeg 子进程仍在编码。半成品文件（142MB）的 MP4 容器头未写完。实际 ffmpeg 进程仍在 602% CPU 运行中。最终完成后文件为 686MB。

**教训**：不要信任后台任务通知的"completed"。长期编码任务需额外检查：`ps aux | grep ffmpeg` 确认进程已退出 + `ffprobe` 验证文件完整性。

**已修** ✅

---

## P010 | 2026-05-20 | Step 2 转录 | 多人对话被合并为一句字幕

**问题**：视频中一男一女对话，Whisper 将两人的话合并在同一个 segment 中（13.8 秒长段包含多轮对话）。中文字幕两人台词挤在一起，句意混乱。

**用户反馈**：「视频中是一男一女在对话，你把两句话放在同一个句子里面，导致句意混乱」

**用户要求**：
- (a) 双行字幕：上方男声、下方女声
- (b) 或不同说话人前加横杠（-）标识切换；同人连续说话不加

**根因**：Whisper 不区分说话人（无 speaker diarization）。它只按音频段落切分，不识别"不同人"。

**研究结论**：`pyannote.audio` + `whisperx` 可实现说话人分离，无需 LLM。V1 接入。

**方案**：V1 用 whisperx（Whisper + pyannote 集成），自动标注 SPEAKER_00 / SPEAKER_01。字幕渲染时不同说话人间自动换行或加 - 前缀。

**状态**：待实现（V1）

**综合解决方案（2026-05-20 专业团队评审）**：

【技术事实澄清】Whisper 模型本身在架构上**不具备**说话人分离能力——它只按"停顿"切音频段。有时不同 speaker 被切到不同 segment 是因为他们之间恰好有停顿（运气），不是 Whisper "认出"了两个人。必须叠加 `pyannote.audio` 做说话人 diarization 才能真正分离。

【方案】接入 `whisperx`（Whisper + pyannote.audio 集成），分 4 步实施：

**Step 1 · 环境准备（需用户配合）**
- 在 HuggingFace 上接受 `pyannote/speaker-diarization-3.1` 模型条款（必须，否则模型无法下载）
- 申请 HuggingFace Access Token（settings → access tokens → read 权限即可）
- 把 Token 写入 `~/.video-subber/config.json`（格式：`{"hf_token": "hf_xxx"}`）
- 额外下载量：~500MB（pyannote 模型）

**Step 2 · 转录引擎切换**
- whisperx 内部就使用 faster-whisper，可平滑替换
- 输出格式从 `[{start, end, text}]` 升级为 `[{start, end, text, speaker}]`
- Segment 类增加 `speaker: str` 属性

**Step 3 · 字幕渲染层处理 speaker 标签**

| 场景 | 渲染规则 |
|------|----------|
| 同一 segment 内有 2 个 speaker | 拆成两条字幕，时间戳按字数比例分配 |
| 跨 segment 同一 speaker 连续 | 不加任何前缀 |
| 跨 segment 不同 speaker 切换 | 行首加 `- ` 标识切换 |
| Speaker 数量 = 1（独白） | 完全不标 speaker，行为同当前 |

**Step 4 · CLI 参数**
- `--diarize` / `--no-diarize`（默认 on）
- `--max-speakers N`（pyannote 自动判断 3 人以上场景常误判，给用户兜底，默认 2）
- HF Token 缺失时自动降级为无 diarize 模式 + 提示用户

【风险】
- pyannote 自动数 speaker 不稳，3 人以上常误判 → 先支持 1-2 人对话
- diarize 增加约 1.5x 处理时间（25 分钟视频多 3-5 分钟）
- HF Token / 模型条款是硬门槛，必须用户手动处理

【验证标准】
- ✅ 一男一女对话视频：原 13.8 秒合并段被拆成 ≥2 条独立字幕
- ✅ 字幕渲染：不同 speaker 之间有 `- ` 前缀 或 分行
- ✅ HF Token 缺失时，自动降级而非崩溃

【优先级】独立较大改动，单独排期。在 P015+P013 修复并验证后再做

---

## P011 | 2026-05-20 | V1 CLI | 翻译结果缓存丢失导致重复调用 API

**问题**：VAD 模型下载失败后翻译实际已成功（2/2），字幕文件已生成。但修复 VAD 后重新运行 CLI，之前的翻译结果被丢弃，重新调 Google API。导致网络一波动就翻译失败（0/2），而实际上之前的翻译结果是好的。

**根因**：
1. 翻译结果（translated.json）存在临时目录 `.video-subber-work/` 中，不是视频同目录
2. 中间文件不是按"文件是否存在"判断，而是按 checkpoint stage 判断
3. `--keep-temp` 不传就删临时目录，已完成的翻译也一起删了

**用户反馈**：「为什么不记录这个翻译结果？既然已经生成了字幕，为什么程序重来还要再调用一次 API？」

**修复（V1.1）**：
1. 缓存目录改为视频同目录下的 `{视频名}.video-subber-cache/`，不与临时文件混放
2. 每步开始前先检查缓存文件是否已存在（`transcript.json` / `translated.json` / `audio.wav`）——存在就跳过，不重复跑
3. 报错时不删缓存，下次运行自动复用
4. 仅在全部成功且未传 `--keep-cache` 时才清缓存

**教训**：API 调用（尤其是免费 API）是不可靠资源。每次成功的结果都应持久化。重跑时默认复用已有数据，不重调 API。

**已修** ✅（CLI 缓存逻辑已重写）

---

## P012 | 2026-05-20 | Step 4 烧录 | 长字幕从第 1 秒挂到第 6 秒不切换——缺少逗号级切分

**问题**：Trump 视频第一段英文原字幕（底部）在 6 秒内切换了多次，但中文字幕（顶部）一条"我刚从中国回来，我会见了习主席，他在外面努力工作，我们在一起度过了一段愉快的时光。"从第 0 秒一直挂到第 6 秒，全程不动。

**用户反馈**：「底部自带的英文字幕都换了好几次了，但你上面的中文字幕一直没有变。你还是没有做翻译的切割。」

**根因**：断句函数 `_split_zh_into_sentences()` 只按句末标点（。！？）切分。但 Whisper 转录出来的长段中大部分是逗号分隔的子句——整条翻译没有句号，函数跳过了切分。

**修复（V1.2）**：两遍切分——Pass 1 按句末标点（。！？）切；Pass 2 对仍然超过 20 字的长段按逗号（，,;；、）切。时间戳按字符数比例分配。

**效果对比**（Trump 视频）：
- 改前：2 条字幕（1 条 6 秒 + 1 条 6 秒）
- 改后：**7 条字幕**（每条 1-5 秒，逐逗号跟随画面节奏）

**教训**：翻译结果的断句粒度必须匹配原字幕的切换节奏。仅按句号切分不够——中文逗号子句也需独立成条。

**已修** ✅（`subtitler.py` 两遍切分已实现）

---

## P013 | 2026-05-20 | 整体 | 5 模型对比——多数模型未识别片头，Tiny 识别了但输出"欢快的音乐"

**测试条件**：25 分钟俄语视频，底部英文字幕，开头有片头音乐。5 个模型依次跑：tiny / base / small / medium / large-v3。

**结果**：
- **Base / Small / Large-v3**：完全未识别片头——字幕从第 0 秒开始覆盖了片头音乐
- **Medium**：识别出片头段，但时间戳过长（整个片头+第一句对话被合并）
- **Tiny**：识别出片头，但输出了一条字幕"欢快的音乐"

**用户反馈**：「非人物说话的时候不要给字幕，这是第一点」

**根因分析**：
1. VAD（silero-vad）未能有效过滤片头音乐——片头 BGM 可能含有人声或类人声元素，VAD 将其误判为"有人声"
2. VAD 阈值（当前 0.5）对音乐类非语音内容的敏感度过高
3. 不同模型对同一音频的置信度判断不同——Tiny 给了较低的置信度（更容易被 no_speech_prob 过滤），而 Large-v3 给了高置信度（更难过滤）

**修复方向**：
1. 调低 VAD 阈值（0.5 → 0.3），减少音乐被误判为语音
2. 在转录完成后检查前 3 条 segment 的文本——如果包含音乐相关关键词或纯标点，自动丢弃
3. 增加 `--vad-threshold` 参数让用户可调

**状态**：部分修复 ✅（方案方向正确，但需调参数）

**v1.1 实测验证（2026-05-20）**：
- 用同一俄语视频跑 v1.1（faster-whisper 内置 VAD threshold=0.4）→ 结果**更差**：第一条字幕从 36.95s 开始，0-36s 清晰俄语对话全部丢失
- 无 VAD 对照：0s 就开始有对话，"Саш, привет. Я к тебе по делу." no_speech_prob=0.00
- 结论：silero-vad（不管是外层还是内置）对这个视频的 0-36s 段误判。**VAD 默认关掉**，改为全量传给 Whisper，靠三层后处理过滤（no_speech_prob + 关键词 + 片头加强）拦截幻觉

**方案迭代 B（基于实测）**：
- VAD 默认关闭（`vad_threshold=0.0`），用户显式传 `--vad-threshold 0.4` 才启用
- 新增 `condition_on_previous_text=False`（外部建议采纳）：防止 Whisper 片头幻觉"传播"到后续段
- 新增 `log_prob_threshold=-1.0`：过滤低置信度段
- 三层后处理过滤 + 无 VAD 全量转录 → P013 和 P015 不再打架

---

## P015 | 2026-05-20 | VAD | 滑窗 VAD 过滤过度——54.6 秒正常对话被误拦

**测试条件**：25 分钟俄语视频，对话清晰响亮，背景音仅片头有一段音乐。VAD 阈值 0.5，`min_speech_duration_ms=500`。

**现象**：VAD 滑窗版过滤后只剩 250 段（Phase 1 无 VAD 为 318 段），第一条字幕从 54.6 秒开始。前半段对话全部丢失，画面无字幕。

**用户反馈**：「对话声音比较大，背景声音基本上只有片头有一个，为什么会误判？」

**根因分析**：
1. `min_speech_duration_ms=500` 过滤了短于 0.5 秒的语音段。俄语对话中快速短回应（如 "да"）被丢弃
2. 阈值 0.5 对清晰对话可能偏严——滑窗模式下 VAD 对短暂停顿更敏感
3. 短回应被连续丢弃 → 累积丢失 54.6 秒

**v1.1 实测验证（2026-05-20）**：

【无 VAD 对照实验】提取前 60s 音频，用 `--no-vad` 跑 Whisper：
- 第 0 秒起就有对话："Саш, привет. Я к тебе по делу." no_speech_prob=0.00
- 0-60s 共 8 段清晰对话，全部 no_speech_prob=0.00
- 8 段末尾被 L1 概率过滤掉 1 段（非语音）

【VAD 启用（threshold=0.4）】：
- **0-36s 全部被拦截**，Whisper 根本没转录这些段
- 36.95s 才出现第一条字幕
- no_speech_prob=0.00 的段被 VAD 事前过滤——这正是"事后判断优于事前判断"的实证

【结论】**silero-vad 不管谁调用（外层/内置），对这个视频都误拦了**。核心原因是 silero-vad 基于波形判断（事前），而 no_speech_prob 基于 Whisper 完整的语言理解（事后）。对 0-36s 这段，silero-vad 误判"不是人声"，但 Whisper 概率给了 0.00（极其确定是人声）。事后判断比事前判断准。

【最终方案】VAD 默认关闭，全量传给 Whisper，靠三层后处理过滤：
- L1 `no_speech_prob > 0.6` 拒绝
- L2 关键词黑名单拒绝
- L3 前 3 条加强审查
- 新增 `condition_on_previous_text=False`（防止幻觉传播）+ `log_prob_threshold=-1.0`（低置信度过滤）
- VAD 仅作"语音占比"统计 + 极低占比（<20%）警告
- 用户可通过 `--vad-threshold 0.4` 显式启用（VAD 密度错判的场景）

---

## P014 | 2026-05-20 | 整体 | Tiny 翻译准确度最低；翻译质量随模型增大而提升

**测试条件**：同上 5 模型对比。

**结果**：
- Tiny：识别了片头（唯一一个），但翻译质量是五个模型中最差的
- 模型越大 → 转录越准 → 翻译越准（因为翻译吃的是转录后的文字）

**根因**：Tiny 模型参数量最少（~39M），对俄语的识别错误率最高。Google Translate 收到的俄语原文已有错，翻出来的中文自然更差。这是一个级联错误——上游转录的误差被下游翻译放大。

**教训**：转录模型选择是"速度 vs 准确率"的权衡。Tiny 适合快速验证流程，不适合最终产出。推荐日常使用 **medium** 或 **small**。

**状态**：已知特性，非 bug。V1 默认 medium，已是最低推荐。

---

## P016 | 2026-05-20 | 翻译 | Baidu 批量翻译连续失败——根因是只取了 trans_result[0]

**现象**：5 种分隔符（`【】`、`|||`、`@@@`、`\n`、编号格式）全部无法正确拆分回原文。284 句只成功 4-15 句。

排查过程：
1. `【】` → 1 条（Baidu 吞掉了分隔符）
2. `|||` → 1 条（同样被吞）
3. `@@@` → 15 条（小批量 3 条可行，大批量不行）
4. 编号 `[1] [2] [3]` → 只翻第一条
5. `\n` 单字符测试 → 只取到 1 条

**真根因**：Baidu 翻译 API 的 `trans_result` **本身就是数组**！当 `q` 参数包含 `\n` 时，API 会逐行翻译，返回多个 `{"src": "...", "dst": "..."}` 条目。而我们代码写的是 `data["trans_result"][0]["dst"]`——**只取了第一条，把后面的全扔了**。

**证据**：
```json
// API 实际返回（3 行俄语）
{"trans_result": [
  {"src": "Саш, привет", "dst": "莎莎，你好。"},
  {"src": "Я к тебе по делу", "dst": "我来找你。"},
  {"src": "Собачек надо покормить", "dst": "狗狗要喂"}
]}
// 我们只读了 [0] → 永远只有 1 条
```

**解决方案（v1.1）**：
1. 用 `\n` 拼接原文，5000 字符/批（API 原生支持，不需自定义分隔符）
2. 遍历 `data["trans_result"]` 所有条目，每个条目的 `dst` 即对应行的译文
3. Google ∥ Baidu 双线程竞速（每批都同时调用，先返回者胜出）
4. 批次间 `sleep(1)` 遵守 1 QPS 限制

**效果**：284 句 → 2 批，**284/284 全部成功（100%）**，0 失败。

**已修** ✅

---

## P017 | 2026-05-20 | 片头 | 关键词黑名单仅含中英文，漏掉了俄语"МУЗЫКА"

**现象**：v1.1 跑 tiny 模型，第一条字幕从 0.0s 开始："ТРЕВОЖНАЯ МУЗЫКА"（俄语"紧张的音乐"），翻译后为"焦虑的音乐"。L2 关键词黑名单没有拦截——因为检查的是原文（俄语），黑名单只有中文和英文关键词。

**用户反馈**：「如果是音乐的话，只要不是人物对话，就直接把这个字幕给取消掉。」

**根因**：`_is_hallucination()` 对原文做关键词匹配，原文是俄语时中文/英文关键词命中不了。需要一个跨语言的音乐/掌声/笑声关键词集合。

**修复（v1.1）**：黑名单扩展至 4 种语言：
```
中文: 音乐 掌声 笑声 鼓掌 谢谢观看 请订阅 字幕组 字幕由
英语: music ♪ ♫ [music] [applause] [laughter]
俄语: музыка МУЗЫКА аплодисменты смех
日语: 音楽 拍手 笑い声
```

**验证标准**：重新跑同一视频，片头不再有"ТРЕВОЖНАЯ МУЗЫКА"。

**已修** ✅

---

## P018 | 2026-05-20 | CLI | checkpoint.py 已实现但 CLI 从未调用 ck.load()

**问题**：`checkpoint.py` 在 v1 就存在，SHA256 哈希验证、阶段恢复全部实现。但 `cli.py` 只用 `ck.save()` 保存阶段，从未调 `ck.load()` 做启动时的哈希验证。判断缓存是否有效用的是"文件是否存在"而非 checkpoint 阶段——如果同名但内容不同的文件，会错误复用旧缓存。

**修复（v1.1）**：CLI 启动时调 `ck.load()` 验证输入文件 SHA256 哈希。哈希匹配 → 复用缓存；哈希不匹配 → 自动清除缓存重跑。`--force` 仍强制清除。

**已修** ✅

---

## P019 | 2026-05-20 | 翻译 | 批量字符上限误设为 5000

**问题**：translator.py 中 `BATCH_CHAR_LIMIT = 5000`，超出 Baidu API 安全范围。284/284 全部成功是因为 Baidu 容错了超限请求，但不能依赖容错。

**修复（v1.1）**：`BATCH_CHAR_LIMIT` 从 5000 改为 **2000**。284 句从 2 批变为 4-5 批（多 3-4 秒但不会丢内容）。

**已修** ✅

---

## P020 | 2026-05-20 | 转录 | 后处理过滤缺少 avg_logprob 和重复字符检测

**来源**：外部方案评审——对方提出"不拦截输入，只过滤输出，多信号交叉验证"原则。

**发现**：Whisper 除了 `no_speech_prob` 还会输出 `avg_logprob`（平均对数概率）。`avg_logprob` 越接近 0 表示 Whisper 越确定自己听对了，< -1.0 表示很不确定。这是免费送的第二个信号，但我们没用上。另外缺少重复字符检测——Whisper 幻觉有时表现为同一个字重复多次（如"哈哈哈哈哈哈"或"嘟嘟嘟嘟嘟"），唯一字符比很低。

**修复（v1.1）**：
1. **avg_logprob 双重验证**：`avg_logprob < -1.0` 的段直接丢弃（与 `no_speech_prob > 0.6` 并列 L1 过滤）
2. **重复字符检测**：文本 > 4 字符且 `唯一字符数 / 总字符数 < 0.3` → 判定为幻觉，写入 `_is_hallucination()` 函数

**已修** ✅

---

## P021 | 2026-05-20 | 整体 | 片尾部分无字幕——既无翻译也无"[翻译失败]"提示

**现象**：用户反馈成品视频中片尾有一段内容没有中文字幕，上面也没有显示"[翻译失败]"标记。

**初步数据**：视频总长 1483.92s，Whisper 最后一条转录在 1480.64s 结束，最后 3.3 秒无字幕覆盖。最后 10 条翻译均成功。

**可能根因**（待确认）：
1. 片尾 1480s 后可能是纯画面/字幕滚动，Whisper 未检测到语音
2. L1/L2 过滤可能误拦了片尾的某些短句
3. 烧录时间戳可能未覆盖到视频最末尾

**状态**：已记录，待用户确认片尾具体内容后排查

---

## P022 | 2026-05-20 | 转录 | avg_logprob 过滤导致 5 模型全部恶化

**现象**：校准后 5 模型各出问题——tiny 首条 3:20、base 提早 1-2s、small 首条 40s、medium 首条 1min+、large-v3 首条 0s。

**根因**：`avg_logprob` 衡量的是"Whisper 对自己听到的词有多确定"，不是"有没有人说话"。用 logprob 过滤非人声是用错工具。3 分钟校准片的 logprob 分布不能代表 25 分钟完整视频，导致每个模型的阈值都比校准前严了 0.22-0.62，大面积误杀真实对话。

**修复**：砍掉 `avg_logprob` 过滤（删除 `if avg_logprob < model_logp: continue`），所有模型统一用 `no_speech_prob=0.6`。后续如需调，只调这一个变量，方向单一可控。

**已修** ✅

---

## P023 | 2026-05-20 | L4 片头加强 | 片头短人名可能被误杀

**问题**：L4 规则「前 3 条 + 字符 < 4 → 丢弃」。但如果片头音乐结束后，第一句对话是一个很短的称呼（如 `"Саш!"` 3 字符、`"小明!"` 3 字符），它恰好是前 3 条且 < 4 字符 → 被 L4 误杀。

**根因**：L4 规则没有区分「碎片幻觉」和「短但真实的对话」。`no_speech_prob` 可以用来区分——真实对话的 nsp 通常很低（< 0.2），幻觉碎片的 nsp 通常偏高。

**候选方案**：L4 加上 `no_speech_prob` 保底——`no_speech_prob > 0.2` 才丢弃，≤ 0.2 说明 Whisper 很确定是人声，保留。

**最终方案（2026-05-20）**：采用候选方案。L4 规则由 `(短 or 命中L2) → 丢弃` 改为 `(短 or 命中L2) and no_speech_prob > 0.2 → 丢弃`。只有 Whisper 自己也觉得"不太确定是人声"时（nsp > 0.2）才丢弃；若 Whisper 很确定这是人声（nsp ≤ 0.2），即使片头短称呼也保留。

**修改文件**：`transcriber.py` L4 行，加 `and no_speech_prob > 0.2`

**已修** ✅

---

## P024 | 2026-05-20 | L3 重复字符检测 | 叠字人名「小小」「丹丹」的边界情况

**问题**：L3 规则「len > 4 且 唯一/总 < 0.3 → 丢弃」。用户担心"小小""丹丹"等叠字人名被误判。

**分析**："小小"（2 字符）、"丹丹"（2 字符）、"丹丹！"（3 字符）、"我叫丹丹"（4 字符）都不会触发 L3，因为 `len > 4` 是前置条件。更长的人名如"你好丹丹你好"（6 字符，4 种唯一，比例 0.67）也远高于 0.3 阈值。当前规则对中文叠字名安全。

**关注点**：如果将来支持含大量重复音的语言（某些南岛语系等），0.3 阈值可能需要按语言调整。

**状态**：已记录，当前无需修改

---

## P025 | 2026-05-20 | 转录 | 过滤仅靠 Whisper 自身——缺乏交叉验证机制

**问题**：当前 L1 过滤完全依赖 Whisper 自己的 `no_speech_prob`——Whisper 出题（转录）、答卷（输出文字）、打分（给置信度），三个环节都是它自己。如校准阈值从 0.6 调到 0.52，Whisper 不会说"这个阈值有问题"——它只管给分，你用什么阈值切是你的事。缺乏第二个独立信号做交叉验证。

**提案（待定）**：引入第二工具与 Whisper 互相审核：

**最终候选方案：RMS 能量方差交叉验证**（Jobs 建议，2026-05-20）

不引入新 AI 模型。用纯信号处理判断音频是"说话模式"还是"音乐/噪音模式"。**三个物理特征一次性实现，多数投票决**（Jobs 决策，2026-05-20）：

| 特征 | 测什么 | 说话 | 音乐 | 默认阈值 |
|------|--------|------|------|----------|
| RMS 能量方差 | 响度稳不稳 | 跳来跳去（高方差）| 平稳（低方差）| < 0.005 |
| 频谱质心均值 | 缺不缺高频辅音 | 辅音高，质心高 | 缺辅音，质心低 | < 1200 |
| 零交叉率均值 | 有没有辅音摩擦 | 辅音多，ZCR 高 | 无摩擦，ZCR 低 | < 0.04 |

> **cent_mean vs cent_var**：用均值，不用方差。方差容易被一两声爆音（拍桌子、关门）愚弄——瞬间飙高后回落导致方差很大。均值在 3 秒内平均，一次噪音改变不了整体结论。均值更鲁棒。

≥ 2 票判"像音乐" → `_is_music_like()` 返回 True。阈值先用默认值，后续用 P029 六段素材校准法调到最坏情况分界线。

**难度**：三个特征 ≈ 同一个函数的三个参数。多一个特征不增加架构复杂度。不分阶段，一次写完。

交叉验证规则：

```
L1a 新规则（替代旧版 nsp > 0.6 单信号丢弃）：

  nsp > 0.6  AND  RMS说"像音乐"   → 丢弃  ✅（两个法官一致）
  nsp > 0.6  AND  RMS说"像说话"   → 保留，标记 [?]
  nsp ≤ 0.6  AND  RMS说"像音乐"   → 保留，标记 [?]
  nsp ≤ 0.6  AND  RMS说"像说话"   → 保留  ✅（两个法官一致）
```

**冲突裁决原则（Jobs 建议，2026-05-20）**：只要有一个法官认为"这是人声"——就保留。假阳性（多一行不该有的字幕）可以忍受，假阴性（少一行应该有的字幕）不可忍受——用户永远不会知道自己被偷走了什么信息。**宁可多给，不能少给。**

**`[?]` 标记**：意见冲突时在字幕文本末尾追加 `[?]`（方括号包裹问号）。用户看到此标记即知"系统不确定这段是否真的有人在说话，请自行判断"。同时在字幕渲染文档中向用户说明此标记含义。

**待定问题**：
- `[?]` 放在字幕行的开头还是末尾？（候选：末尾）
- 是否同时引入 silero-vad 做第三方仲裁？（暂无必要，先跑通双信号）

**RMS 特征阈值校准方案（Jobs 建议，2026-05-20）**：

不拍脑袋设阈值。用**已知标签的音频做测量**——六段音频，找音乐和对话之间的"最坏情况分界线"。

**校准素材（6 段，每段 15-20 秒）**：

| 类型 | 段数 | 内容 | 目的 |
|------|------|------|------|
| 纯音乐 A | 1 | 管弦乐/钢琴（最平稳）| 获得音乐 RMS 的下界 |
| 纯音乐 B | 1 | 流行歌曲（有节奏但不跳）| 覆盖中等能量变化 |
| 纯音乐 C | 1 | 动作配乐（鼓点重，能量波动大）| 获得音乐的**最坏情况**——最像说话的音乐 |
| 纯对话 A | 1 | 单人独白（持续说话，停顿少）| 获得对话的**最坏情况**——最像音乐的对话 |
| 纯对话 B | 1 | 两人对话（一人一句，交替停顿）| 覆盖正常对话节奏 |
| 纯对话 C | 1 | 情绪激动喊叫（能量跳跃剧烈）| 覆盖高能量对话 |

**校准方法**：

1. 将每段切成 3 秒小段，逐段算三个特征值（rms_var / cent_mean / zcr_mean）
2. 取**所有音乐段的最大值**——这是"最吵、最跳、最像说话的音乐"
3. 取**所有对话段的最小值**——这是"最平稳、最低沉、最像音乐的对话"
4. 阈值 = (音乐最大值 + 对话最小值) / 2

```
例：
  音乐 rms_var 最大值: 0.008（动作配乐的峰值——最像说话的音乐）
  对话 rms_var 最小值: 0.010（独白的谷值——最像音乐的对话）
  → rms_var 阈值 = (0.008 + 0.010) / 2 = 0.009
```

**核心原则**：不是求平均，是**找最坏情况**。阈值卡在"最像说话的音乐"和"最像音乐的对话"的中间。宁可让音乐多一些通过（然后被 `[?]` 标记），不能让对话被误杀。

**语言差异**：先用一个语言校准，跑跨语言视频看 `[?]` 是否增多。增多 → 做 per-language 校准；没增多 → 阈值够通用。不提前解决没有出现的问题。

详见 P029。

**未来扩展：频谱质心作为第三信号**（Jobs 建议，2026-05-20，针对武打场景）

武打音效（拳脚、击打）的 RMS 能量也是跳来跳去的——和说话很像，RMS 单独区分不了。但可以引入**频谱质心**（spectral centroid）：乐器/音乐的频谱是有组织的（能量集中在某些频率），打斗音效的频谱是混乱的（全频段爆发）。不需要新模型——`librosa.feature.spectral_centroid` 一行代码，同样 < 1 毫秒。

当 RMS 无法判断时（方差在模糊区间），频谱质心做第三票裁决。详情见 [P027](problems.md)。

**状态**：✅ V1.2 已实现（2026-05-20）。三物理特征（RMS 方差 + 频谱质心 + 零交叉率）多数投票判"像音乐"，与 Whisper nsp 双信号交叉验证。冲突时保留并标记 `[?]`。

---

## P026 | 2026-05-20 | 转录 | 3 分钟校准片的 nsp 阈值在 25 分钟完整视频上失效

**现象**：5 模型按校准阈值跑完整 25 分钟视频后，出现各种问题——small 前 40s 无字幕、medium 前 20s 无字幕、tiny 1:50-3:40 空白、large-v3 片头出字幕+间歇空白。但 base（nsp=0.62，最接近默认 0.6）效果最好。

**关键数据**：small（nsp=0.79）和 medium（nsp=0.95）的 L1 过滤了 **0 段**——nsp 阈值太松，过滤完全没干活。但前几十秒仍然没字幕。说明不是过滤问题，是 Whisper 模型本身对这视频的开头不敏感，调整 nsp 救不了。

**根因**：3 分钟校准片段的 nsp 分布不能代表 25 分钟完整视频。校准值在两个极端——有的太松（small/medium nsp 阈值形同虚设），有的太严（large-v3 杀 52 段，tiny 杀 74 段）。全用 `nsp=0.6` 反而最稳。

**结论**：per-model 校准方向正确，但需要更长的校准音频或不同的校准策略。当前建议所有模型统一用 `nsp=0.6`。

**状态**：已记录，待修复

---

## P027 | 2026-05-20 | 转录 | RMS 无法区分"说话"和"武打音效"——需频谱质心做第三信号

**问题**：武打场景中，拳脚击打的音效也是间歇性爆发的（瞬间大、间隙小），RMS 能量方差会误判为"像说话"。RMS 一个人分不清"一个人在说话"和"两个人在打架"。

**根因**：RMS 只看响度的变化频率——说话和打斗都是"跳来跳去"的模式。但 RMS 不看"声音的频率组成"——这正是区分两者的关键。

**候选方案：频谱质心（Spectral Centroid）**（Jobs 建议，2026-05-20）

频谱质心衡量的是"声音的能量集中在高频还是低频"：

| 声音类型 | 频谱特征 |
|----------|----------|
| 音乐/乐器 | 频谱有组织，能量集中在特定频率（如钢琴的中频、弦乐的高频） |
| 说话 | 频谱有规律变化，元音低频、辅音高频，但整体有语言结构 |
| 拳脚打斗 | 频谱混乱，全频段瞬间爆发，没有组织性 |

实现：`librosa.feature.spectral_centroid`，纯信号处理，一行代码，< 1 毫秒。不需要新模型。

**使用场景**：仅当 RMS 方差处于模糊区间时（即 RMS 不确定自己判断对不对），启用频谱质心做第三票裁决。正常情况只用 Whisper + RMS 双信号。

**状态**：已记录，方案待定。仅当实测中发现武打场景确实产生幻觉字幕时才实施。不提前实施。

---

## P028 | 2026-05-20 | 字幕渲染 | `[?]` 标记需在用户文档中说明含义

**问题**：RMS 交叉验证方案中，两法官意见冲突时字幕追加 `[?]` 标记。此标记需要让用户理解其含义——否则用户看到问号会困惑。

**方案**：在 V2 Web UI 中，字幕预览界面旁加一句说明：「带 `[?]` 的字幕表示系统不确定该段是否真的有人在说话。请根据画面自行判断。」同时在本项目 README/CLI help 中注明。

**状态**：已记录，待 V2 UI 实现时加入

---

## P029 | 2026-05-20 | 校准 | RMS 三特征阈值校准——六段素材法

**问题**：RMS 三个特征（rms_var / cent_mean / zcr_mean）各需要一个阈值。拍脑袋设 0.005/1200/0.04 不可靠——这些值在钢琴独奏上对，在动作配乐上可能错。

**方案：六段素材校准法**（Jobs 建议，2026-05-20）

**校准素材（6 段，每段 15-20 秒）**：

| # | 类型 | 内容 | 为什么选这个 |
|---|------|------|-------------|
| 1 | 纯音乐 | 管弦乐/钢琴（最平稳）| 获得音乐方差的下界 |
| 2 | 纯音乐 | 流行歌曲（有节奏但不跳）| 覆盖中等能量变化 |
| 3 | 纯音乐 | 动作配乐（鼓点重，能量波动大）| **最坏情况——最像说话的音乐** |
| 4 | 纯对话 | 单人独白（持续说话，停顿少）| **最坏情况——最像音乐的对话** |
| 5 | 纯对话 | 两人对话（一人一句，交替停顿）| 覆盖正常对话节奏 |
| 6 | 纯对话 | 情绪激动喊叫（能量跳跃剧烈）| 覆盖高能量对话 |

**校准步骤**：

1. 每段切成 3 秒小段，逐段算三个特征
2. 取所有**音乐段的最大值**（最像说话的音乐长什么样）
3. 取所有**对话段的最小值**（最像音乐的对话长什么样）
4. 阈值 = (音乐最大值 + 对话最小值) / 2

```
例：
  音乐 rms_var 最大值: 0.008（段 3 动作配乐的峰值）
  对话 rms_var 最小值: 0.010（段 4 独白的谷值）
  → rms_var 阈值 = 0.009
```

**核心原则**：不是求平均——是**找最坏情况**。阈值卡在"最像说话的音乐"和"最像音乐的对话"的正中间。宁可让音乐多一些通过（被 `[?]` 标记），不能让对话被误杀。

**语言差异处理**：先用一个语言（如俄语）校准。跑跨语言视频（如日语）看 `[?]` 是否异常增多。增多 → per-language 校准；没增多 → 阈值通用。不提前解决未出现的问题。

**状态**：方案已定，未实施。待 RMS `_is_music_like` 函数实装后进行校准。

---

## P030 | 2026-05-21 | L3 重复字符检测 | 长文本公式系统性失效——唯一字符比随文本增长自然衰减

**现象**：tiny 模型跑 25 分钟俄语视频，对话中间有字幕丢失。debug.log 显示 L2 杀了 21 段（过滤摘要: 保留=282 丢弃=22 L1a=1 L2=21 L4=0 存疑=25），其中约 7 段是真实俄语对话被误杀，但这些段**没有命中任何关键词黑名单**。

**根因**：`len(set(t)) / len(t) < 0.3` 在长文本上系统性失效。俄语只有 33 个西里尔字母，加上标点空格最多约 40 种唯一字符。分母（总字符数）可以无限增长，分子被封顶在 ~40。短文本 20 字符：20/20=1.0 没事。中文本 80 字符：40/80=0.5 没事。长文本 200 字符：40/200=0.2 < 0.3 → 误杀。文本越长比值越低——**这是数学必然，不是因为文字在重复**。

此外 debug.log 截断 `text[:80]` 加剧了调试困难——日志展示的截断文本比值可能 > 0.3，但完整文本远低于 0.3。

**解决方案**：加长度上限——只在 ≤ 30 字符的短文本上做重复检测。

```python
# 旧
if len(t) > 4 and len(set(t)) / len(t) < 0.3:

# 新
if 4 < len(t) <= 30 and len(set(t)) / len(t) < 0.3:
```

**理由**：所有真正的重复幻觉（"哈哈哈哈"=6 字符、"Аааааа"=15 字符、"да-да-да"=9 字符）全部在 30 字符以内。超过 30 字符的长段对话是真实语言，不需要重复检测。

**修改文件**：`transcriber.py` `_is_hallucination()` 函数，一行改动。

**已修** ✅

---

## P031 | 2026-05-21 | V2 架构 | CLI 的 `sys.stdout` 劫持无法迁移到 Web 环境

**问题**：V1.3 用 `_TeeOutput` 劫持全局 `sys.stdout` 实现双日志（终端 + user.log）。这个方案依赖三个前提：(1) 单进程 (2) 单任务 (3) 有终端。V2 用 FastAPI Web 服务器——一个进程长期存活，可能同时处理多个任务，且没有"终端"stdout。

**根因**：
- `sys.stdout` 是进程级全局变量。多任务同时修改 → 日志互相串写入错误文件
- FastAPI 没有终端——用户看的是浏览器进度条，不是终端文字
- `_TeeOutput` 的"双写终端+文件"在 Web 环境下只剩"写文件"有意义

**解决方案**：V2 废弃 `_TeeOutput`。每个任务创建一个独立 `logging.FileHandler`，绑定到该任务的 logger。用户日志和调试日志都用 Python logging 直接写文件，不劫持 stdout。十行代码的事，但**必须在搭框架前想清楚**。

**V2 优先级**：🔴 最高——框架第一行代码之前必须先确定日志方案。

**已修** ✅（V2.05+ — `_TeeOutput` 已废弃，worker.py 使用 `_setup_file_log()` + `FileHandler` / `RotatingFileHandler` 独立日志方案）

---

## P032 | 2026-05-21 | V2 前端 | 环境检测安装指引：FFmpeg 缺失和缺 libass 显示相同命令

**问题**：FFmpeg 完全未安装 和 FFmpeg 已安装但缺 libass 两个场景，都显示 `brew install ffmpeg-full`。用户看不出来这行命令到底是在装什么。

**原因**：两种情况用了同一段 guide 文字，没有区分场景。

**解决方案**：分三种状态各自写指引：
- 红（未安装）：完整安装说明，含 macOS `brew install ffmpeg-full` + Windows 下载链接 + 解释为什么必须用 ffmpeg-full
- 黄（缺 libass）：说明当前是基础版，只能出 SRT 无法烧录。如需烧录运行 `brew install ffmpeg-full`
- 绿（已就绪）：无需指引

**已修** ✅

---

## P033 | 2026-05-21 | V2 前端 | env bar 渲染函数中 whClass/whTxt/whGuide 未初始化，JS 报错导致整个页面崩溃

**问题**：`renderEnv()` 函数中，当 Whisper 已安装时（`wh.installed === true`），`whClass`、`whTxt`、`whGuide` 三个变量从未声明，直接在模板字符串中使用 `undefined`。同时旧版 innerHTML 代码残留在函数闭合 `}` 之外，引用函数内局部变量导致 JS 报错。整个页面所有 JS（模型滑块、翻译模式、事件绑定）全部崩掉。

**原因**：编辑 `renderEnv` 函数时有旧代码残留未删除，Whisper 分支缺少变量初始化语句 `let whClass='ok',whTxt='Whisper 模型已安装',whGuide='';`。

**解决方案**：
1. 在所有分支前初始化三个变量
2. 删除函数外残留的旧版 innerHTML 代码
3. fetch 增加 `{cache:'no-store'}` 防止浏览器缓存

**已修** ✅

---

## P034 | 2026-05-21 | V2 前端 | 模型选择滑块标注位置错误

**问题**：箭头上下应该分别标注：上方「快⇔慢」、下方「低准确度⇔高准确度」。但实际渲染时标注全部在箭头下方。

**原因**：HTML 中 `model-arrow-labels-top`（快/慢标注）放在了 `model-arrow-bar`（箭头线）的**后面**，DOM 顺序决定了视觉顺序——写在后面的元素渲染在下方。

**解决方案**：将 `model-arrow-labels-top` 移到 `model-arrow-bar` 前面，使其渲染在箭头上方。

**已修** ✅

---

## P035 | 2026-05-21 | V2 后端 | 翻译后端前后端不同步

**问题**：后端 Bing 和 Youdao 是空壳（`translate_batch` 直接返回 `[翻译失败]`），但前端仍然显示这 4 个免费后端选项，用户选了也无法正常工作。

**原因**：`/api/backends` 返回的是硬编码的全部后端列表，没有「是否可用」的标记。前后端缺乏统一的启用/禁用机制。

**解决方案**：
1. `TranslationBackend` ABC 新增 `enabled` 属性（默认 True）
2. Bing/Youdao 覆盖为 `False`
3. `/api/backends` 实例化所有后端并只返回 `enabled=True` 的
4. orchestrator 的 `FREE_BACKENDS` 同样过滤
5. 后续加新后端时：后端类写好了 → `enabled=True` → 前后端自动同步出现

**已修** ✅

---

## P036 | 2026-05-21 | V2 架构 | 上下文感知翻译：语境摘要获取后未传入翻译调用

**问题**：`orchestrator.py` 中 `context_aware=True` 时会调用 `understand_context()` 获取语境摘要，但后续的 `_run_speed_mode_batch` / `_run_quality_mode_batch` 并没有把这个语境传给任何一个后端的 `translate_batch`。语境摘要拿到后就丢弃了。

**原因**：`translate_batch` 接口没有 `context` 参数，批量翻译函数也不接受 context。

**解决方案**：
1. `TranslationBackend.translate_batch()` 增加可选 `context=""` 参数
2. 批量翻译函数增加 `context` 参数并下传
3. LLM 后端在收到 context 时，prompt 前面加 `Context: {context}\n\n`
4. orchestrator 在 context-aware 模式时传入 `context_summary`

**已修** ✅

---

## P037 | 2026-05-21 | V2 整体 | V2.01 框架版本已保存

**版本**：V2.01（2026-05-21）
**范围**：V2 框架层——前端 UI 布局 + 后端 API 路由 + 翻译后端调度架构。内部功能（Bing/有道 API 实现、真实 LLM 翻译验证）待后续版本完成。
**适用规则**：后续所有 V2 改动在此框架基础上增量叠加，不动框架结构。

---

## P038 | 2026-05-21 | V2 设计 | Jobs 评审 #4：字幕样式预览不应独立成区

**问题**：字幕样式选择（字体/字号/颜色/描边）和黑底预览框在页面上占据一个完整区域。但只有用户选择「烧录字幕到视频」时这个功能才有意义，选「仅生成 SRT」的用户根本不需要看到。

**Jobs 方案**：把字幕样式藏在输出方式下方。用户选「烧录到视频」→ 下面展开样式选项。选「仅 SRT」→ 完全隐藏。

**状态**：✅ 已实现（2026-05-21）

---

## P039 | 2026-05-21 | V2 设计 | Jobs 评审 #6：用户界面文字说明要简洁，且不应有专业名词

**问题**：进度文字用的是工程师语言——「正在提取音频…」「Whisper 转录中… 第 87/287 段」「正在生成 ASS 字幕…」。用户不知道什么是音频提取、什么是 Whisper、什么是 ASS。

**Jobs 方案**：说人话。「正在理解你的视频…」「正在翻译…第 87/287 句」「快好了…」。让用户觉得你在跟他说话，不是在给日志文件写记录。

**状态**：📋 已记录，待实现（worker.py 进度文字尚未修改）

---

## P040 | 2026-05-21 | V2 设计 | Jobs 评审：环境检测合并为一行「翻译引擎」

**问题**：环境检测栏显示「FFmpeg（含 libass）已就绪」和「Whisper 模型已安装」两个独立状态。用户不知道 FFmpeg 和 Whisper 是什么，也不关心它们是两个东西。

**Jobs 方案**：合并为一行。用户只需要知道「翻译引擎」是否就绪。
- 🟢 翻译引擎已就绪
- 🔴 翻译引擎未下载 · 点击下载（约 1.5GB，仅需一次）

**用户修正**：Whisper → 改称「语音识别模块」。

**状态**：✅ 已实现（2026-05-21）

---

## P041 | 2026-05-21 | V2 设计 | 环境检测三个模块独立显示，友好名称+技术名词括号标注

**问题**：环境检测栏合并成一行「翻译引擎」后，用户不知道具体缺什么。而且没有区分三个独立模块：视频处理（FFmpeg）、烧录（libass）、语音识别（Whisper）。

**方案**：三行独立显示。每行格式：`友好名称（技术名词）状态`。
- 🟢 视频处理模块（FFmpeg）已就绪
- 🟢 烧录模块（libass）已就绪
- 🟢 语音识别模块（Whisper）已就绪
- 缺少时：🔴 视频处理模块（FFmpeg）未安装 · 如何安装 ▸

**状态**：✅ 已实现（2026-05-21）

---

## P042 | 2026-05-21 | V2 Worker | `sys.stdout = stderr_progress(...)` 导致 stdout 变为 None，进度消息全部静默丢失

**问题**：Worker 运行中（400% CPU），但前端永远显示「正在准备中」。API 查询显示进度停在 10%。

**原因**：`worker.py` 中 13 处调用写成了 `sys.stdout = stderr_progress(...)`。`stderr_progress()` 函数末尾会 `sys.stdout = sys.stderr` 并返回 None。赋值后 `sys.stdout = None`，后续所有 `emit()` 写入 None 全部静默失败，进度 JSON 永远到不了 WorkerManager。

**解决方案**：全部 13 处去掉 `sys.stdout =`，改为直接调用 `stderr_progress(...)`。

**已修** ✅

---

## P043 | 2026-05-21 | V2 架构 | WebSocket 跨 event loop 发送失败，前端收不到进度

**问题**：后端 `/api/jobs/{id}` 显示进度正常更新，但前端进度条不动。WorkerManager 的 `_read_stdout` 在子线程中用 `asyncio.new_event_loop()` 创建了独立事件循环，而 WebSocket 连接绑定在 uvicorn 主事件循环上。跨 loop 调用 `send_json()` 失败。

**原因**：`WorkerManager._read_stdout` 创建了独立的 asyncio event loop，`ws_manager.send()` 内部的 `websocket.send_json()` 必须在 WebSocket 所属的 loop 上执行。

**解决方案**：
1. WorkerManager 改为线程安全的消息队列（`queue.Queue`）
2. `_read_stdout` 将消息放入队列
3. FastAPI startup 时启动 `consume_messages()` 异步任务，在主 loop 上消费队列并转发到 WebSocket
4. 前端增加 HTTP 轮询兜底（每 2 秒 `GET /api/jobs/{id}`）

**已修** ✅

---

## P044 | 2026-05-21 | V2 架构 | 页面刷新后 job ID 丢失，无法重连进行中的任务

**问题**：用户上传视频后刷新页面或重新打开，看不到正在进行的任务进度。job ID 只存在 JavaScript 变量中，刷新后消失。

**解决方案**：
1. 任务开始时 `localStorage.setItem('activeJob', jobId)`
2. 任务完成/失败时 `localStorage.removeItem('activeJob')`
3. 页面加载时检查 activeJob，如存在则自动调用 `reconnectJob()` 重连 WebSocket 并恢复进度条
4. `reconnectJob()` 同时查询 `/api/jobs/{id}` 获取当前进度

**已修** ✅

---

## P045 | 2026-05-21 | V2 架构 | 两个 Worker 同时跑 + 旧 Worker stdout 管道无人读取导致阻塞

**问题**：服务器重启后，旧 Worker 进程仍在运行，新上传又启动了第二个 Worker。两个 Worker 同时加载 large-v3 模型，内存占用 ~12GB。旧 Worker 的 stdout 管道因旧服务器已关闭而无人读取，管道缓冲区满后 Worker 写入阻塞。

**解决方案**：重启服务器前先 `kill` 所有旧 Worker 进程。后续考虑在 WorkerManager 启动时检测并清理僵尸 Worker。

**V2.07 真正修复**：抽 `WorkerManager._kill_if_alive(job_id)` 私有方法，`start()` 和 `cancel()` 共用。`start()` 开头先 pop+kill+wait 旧进程再启动；`cancel()` 也补上 `wait`（之前只 kill 不 wait——这是 P045 的根因之二，客户端 cancel 完立刻 start 仍会撞上）。

**已修** ✅（V2.07 代码修复，不再依赖手动清理）

---

## P046 | 2026-05-21 | V2 缓存 | 缓存仅按视频文件哈希索引，不检查设置是否变更

**问题**：用户用 medium 模型跑了一半，取消后改成 large-v3 重跑。系统发现同一视频的 transcript.json 已存在 → 跳过转录 → 用户最终拿到的是 medium 的结果。设置变更被静默忽略。

**根因**：Checkpoint 只存 `input_hash`（视频 SHA256），不存 config。缓存命中只看文件是否存在，不看上次用什么参数生成。

**Jobs 方案**：「config 变了 → 缓存作废 → 重跑。不要按步骤做细粒度判断——用户不在乎内部有几个阶段。」

**用户修正**：「不同的设置影响不同的步骤。字体颜色变了不应该重跑转录。」

**最终方案（Jobs + 用户共识）**：
1. 每个 `ck.save(stage, meta=config)` 写入完整设置
2. Resume 时对比新旧 config，按 SETTING_STAGE 映射找到最早受影响的步骤
3. 清除受影响步骤及之后的所有缓存
4. 前端：设置控件 change 时自动 cancel + restart

**映射关系**：
| 设置 | 影响步骤 |
|------|---------|
| subtitles | extract_audio |
| model | transcribe |
| translationMode/Priority/apiKeys/tier/contextAware | translate |
| style/burn | burn |

**已修** ✅（V2.02）

---

## P047 | 2026-05-21 | V2 架构 | WebSocket 跨 event loop 推送失败——Reader 线程独立 loop 无法访问 WebSocket 连接

**问题**：前端显示「准备中」但后台 Worker 正常运行。进度数据已由 Reader 线程读取并更新至 JOBS dict，但无法推送到浏览器。

**根因**：WorkerManager 的 `_read_stdout` 在子线程中通过 `asyncio.new_event_loop()` 创建独立事件循环。WebSocket 连接在 uvicorn 主 loop 中创建。Python asyncio 不允许跨 loop 使用 WebSocket——在子线程的 loop 中调用 `send_json()`，不报错不崩溃，但消息不到达。

**方案 A（已采用）**：`asyncio.run_coroutine_threadsafe(ws.send_json(msg), main_loop)`——Reader 线程不自己发消息，把发消息的任务扔给主 loop 执行。一行代码。

**方案 C（兜底，已采用）**：HTTP 轮询——前端每 2 秒 `GET /api/jobs/{id}`。不依赖 WebSocket，不依赖线程通信。

**方案 B（V3 保留）**：Redis pub/sub——Worker 直接写入 Redis，FastAPI 订阅 Redis 通道自动收。适合 CPU 满载的云服务器场景。V2 不用（本地工具装外部依赖太重）。

**已修** ✅（方案 A + C 双通道，V2.02 后续版本）


---

## P032 | 2026-05-21 | V2 Web | 同一视频重复上传导致双 Worker 并行转录

**问题**：用户上传同一视频两次（关闭页面后重新打开 → 再次上传），后端启动了 2 个 Worker 同时处理同一文件。每个 Worker 吃 6GB 内存（large-v3 模型），合计 12GB + 550% CPU。

**根因**：`POST /api/jobs` 没有去重逻辑。每次上传都创建新 job 目录、启动新 Worker。前端 `activeJob` 存在 localStorage 中，页面刷新后丢失。

**解决方案**（V2.03）：
1. 上传完成后后端计算文件 SHA256，存入 `meta.json`
2. 每次创建 job 前扫描所有历史 job 的 `meta.json`，查重
3. 命中 → 返回 `{found: true, job_id, status, config}`，前端自动跳转进度页
4. 未命中 → 创建新 job
5. 处理中锁定所有设置（前端 `.section.locked` pointer-events:none）
6. `ACTIVE_HASHES` 内存字典追踪活跃 job，完成后清理

**已修** ✅（V2.03）

---

## P033 | 2026-05-21 | V2.03 前端 | 配置恢复后 UI 未同步——JS 状态已更新但 HTML 仍显示默认值

**问题**：用户重新上传同一视频，哈希命中，`State.settings` 被后端返回的 config 正确覆盖（如 model: large-v3）。但页面上的模型圆点、字幕状态、翻译模式等 HTML 控件全部停留在默认值（如 medium）。用户看到的是错误的配置信息。

**根因**：`buildModelSlider()` 等 UI 渲染函数只在页面初始加载时执行一次。配置恢复时只更新了 JS 内存中的 `State.settings`，无人触发 HTML 重绘。

**解决方案**（V2.03）：
1. 新增 `syncUI()` 函数，负责将 `State.settings` 的全部字段同步到 HTML：
   - `buildModelSlider()` — 重建模型圆点
   - 字幕状态 toggle 按钮同步
   - 输出方式 select 同步
   - 翻译模式 toggle + 优先级列表显隐同步
   - 上下文感知 toggle 同步
   - API Key 列表 + 优先级列表重建
   - 样式预览更新
2. 在 `d.found` 分支中，`State.settings` 更新后立即调用 `syncUI()`

**已修** ✅（V2.03）

---

## P034 | 2026-05-21 | V2.03 后端 | 每次上传扫描所有 meta.json → IO 炸弹

**问题**：`_find_job_by_hash` 每次被调用时遍历 `JOBS_DIR` 下所有 job 目录，逐个打开并解析 `meta.json`。历史任务积累到几百个后，单次上传的哈希查重就要读几百个文件，同步阻塞 FastAPI 事件循环。

**根因**：磁盘扫描放在请求热路径上，没有内存缓存。

**解决方案**（V2.04）：
1. 启动时 `@app.on_event("startup")` 一次性扫描磁盘，构建 `GLOBAL_HASH_INDEX`（file_hash → {job_id, status, config}）
2. `POST /api/jobs` 改为 `GLOBAL_HASH_INDEX.get(file_hash)` O(1) 内存查询
3. 状态变更时 `_persist_job_status` 同步更新内存索引（双写一致）

**已修** ✅（V2.04）

---

## P035 | 2026-05-21 | V2.03 后端 | 后端重启后 interrupted 任务成死局——Worker 未重启

**问题**：后端进程重启后，旧 Worker 子进程跟着死掉。磁盘上 cache 和 meta.json 还在，但没人干活。`_find_job_by_hash` 正确识别了 `interrupted` 状态，但 `create_job` 只返回了状态给前端——没有重新拉起 Worker。前端连上 WebSocket 后进度条永远卡住。

**根因**：`interrupted` 分支缺少 `worker_manager.start()` 调用。

**解决方案**（V2.04）：
```python
if status == "interrupted":
    job_dir = JOBS_DIR / jid
    video_path = next(job_dir.glob("input.*"))
    JOBS[jid] = {"id": jid, "status": "running", ...}
    _persist_job_status(jid, "running")
    worker_manager.start(jid, job_dir, str(video_path), existing["config"])
    return {"found": True, "job_id": jid, "status": "running", ...}
```

**已修** ✅（V2.04）

---

## P036 | 2026-05-21 | V2.03 后端 | GLOBAL_HASH_INDEX 只建不更新——内存索引成死数据

**问题**：`GLOBAL_HASH_INDEX` 在启动时加载，但 Worker 运行期间状态变化（queued→running→done）后，索引里的 status 字段从未更新。导致后续查重返回过期状态（如已完成的任务仍显示 queued），前端据此做出错误决策。

**根因**：`_persist_job_status` 只写 meta.json，不更新内存索引。磁盘和内存双写不一致。

**解决方案**（V2.04）：
1. 新增 `_update_hash_index(job_id, status)` 函数
2. 新增 `JOB_ID_TO_HASH` 反向索引（job_id → file_hash），实现 O(1) 反查
3. `_persist_job_status` 写盘后立即调 `_update_hash_index` 同步内存

**已修** ✅（V2.04）

---

## P037 | 2026-05-21 | V2.03 前端 | 四个控制流瑕疵 + UI 渲染散落

**问题**：
1. `lockUI()` 在 fetch 之后调用——点击到请求发出之间有 gap，用户可在此期间改设置
2. `State.videoFile = null` 在 fetch 之后立即执行——后续 `d.found` 分支使用 `file.name` 时可能出现状态不一致
3. catch 里无条件 `unlockUI()`——多任务并发时一次上传失败就解锁所有设置
4. 任务完成后 `sbtn.disabled` 未恢复——用户无法连续上传下一个视频
5. done 状态的卡片 UI 更新逻辑散落在 sbtn handler、handleCardMsg、reconnectJobs 三处

**解决方案**（V2.04）：
1. `lockUI()` 移到 fetch 前
2. `State.videoFile = null` 移到全部卡片逻辑完成后
3. catch 里检查 `Object.keys(activeJobs).length === 0` 再 unlock
4. fetch 成功后 `sbtn.disabled = false`
5. 抽取 `renderCardDone(jid, msg)` 统一三处调用

**已修** ✅（V2.04）
---

## P048 | 2026-05-21 | V2 前端 | tier 三数据源异步竞态——URL paid_3 被服务器 free 覆盖，LLM 从未触发

**问题**：打开 `?tier=paid_3`，上传视频后后端收到的 config 始终是 `tier: free`，LLM 大模型从未被调用。修了三次都没修好。

**根因**：tier 有三个数据源——localStorage、服务器 config.json、URL 参数。每个都在不同时间点写入 `State.settings.tier`，异步回调的到达顺序不确定。localStorage 里存着 `free`，服务器的 DEFAULT_CONFIG 里也存着 `free`，URL 的 `paid_3` 在同步阶段设置后，被稍后到达的异步 `fetch('/api/config')` 回调用 `Object.assign` 覆盖。第三次修改时又把 `/api/backends` 的 fetch 移入 `loadConfig` 内部，导致 API 平台下拉框空白（`updateBackendLists` 未执行）。

**乔布斯**：一个值不应该有三个数据源。tier 不是偏好，是会话选择。删掉 tier 的持久化。

**工程师 1**：用 async/await + initApp() 线性化流程，确保 URL 参数在所有异步操作完成后最终覆盖。

**工程师 2**：折中方案——服务器不存 tier，前端 URL > localStorage > free 一行搞定。保留 localStorage 记忆，防止用户下次不带参数访问时退回 free。

**最终方案（三人共识）**：
1. `config_manager.py` DEFAULT_CONFIG 删掉 `"tier": "free"`——服务器不存 tier
2. `loadConfig()` 回到同步函数，一行决胜负：`State.settings.tier = urlTier || State.settings.tier || 'free'`
3. 独立的 `/api/backends` fetch 恢复——tier 已同步定型，无竞态
4. localStorage 保留 tier 记忆——下次不带参数打开，自动恢复上次层级

**已修** ✅


---

## P038 | 2026-05-21 | V2.04 前端 | localStorage 残留旧 activeJobs 导致页面加载时自动 lockUI

**问题**：开发测试期间 localStorage 中累积了旧的 `activeJobs` 条目。用户打开页面时 `reconnectJobs()` 检测到这些条目，尝试重连 → 调用 `lockUI()` 锁定所有设置控件。此时 `/api/backends` 的 fetch 尚未返回，`updateBackendLists` 未执行。结果：
- 模型滑块被 `disabled-ctl` 灰化（opacity 0.45，看起来像消失了）
- API 平台下拉框还是空的（fetch 未完成）
- 翻译模式无法切换（paid_3 模式下本就隐藏，但 free 模式下也被锁）

**根因**：`reconnectJobs` 对失效的 job ID 只做了 catch 降级处理，但依然在重连过程中调用了 `lockUI()`。开发阶段频繁的页面重载导致 localStorage 积累了大量僵尸条目。

**解决方案**（V2.04）：
1. 后端新增 `POST /api/reset` —— 清除所有 job 目录、内存索引、磁盘缓存
2. 前端新增 `?fresh` URL 参数 —— 页面加载时跳过 `reconnectJobs()`，从干净状态开始
3. 前端 init 时检测 `?fresh` → 调 `localStorage.clear()` → `location.replace(location.pathname)` 抹掉 URL 参数后重新加载

**已修** ✅（V2.04）


---

## P164 | 2026-05-21 | V2 后端 | 服务端 tier 无鉴权，免费用户可绕过付费墙（🔴 blocking）

**问题**：整个 tier 体系完全依赖客户端传入。攻击路径：
```
浏览器 ?tier=paid_3 → /api/backends?tier=paid_3 直接返回付费能力
→ 前端 config 写入 tier:"paid_3" → POST /api/jobs 将 config 原样传入 Worker
→ Worker 传给 orchestrator → orchestrator 信任 tier，注入平台 LLM Key + 启用上下文感知
```
每一步都没有服务端验证。免费用户改 URL 参数或前端 JS 就能使用全部付费功能。

**根因**：V2 设计阶段未规划用户认证系统（V3 才做），但付费分层逻辑已实现。`/api/backends` 的 `tier` 参数、`/api/jobs` 的 config.tier 均无服务端校验。

**解决方案**（待定）：
1. 短期：加环境变量 `VIDEO_SUBBER_TIER` 控制全局 tier，所有请求以此为准，忽略客户端传入值
2. 长期（V3）：完整账号系统 + JWT token，每个请求从 token 中提取 tier

**已过时**（V3 砍掉付费墙，tier 体系已删除。orchestrator.py 注释："Tier system removed"）


---

## P040 | 2026-05-21 | V2.04 前端 | cancelCard() 函数定义错误，取消功能完全失效（🔴 blocking）

**问题**：`frontend/index.html` 中 `cancelCard` 函数体嵌套了同名内层函数声明，实际逻辑永不执行：
```javascript
function cancelCard(jid){function cancelCard(jid){  // 外层只声明内层函数
    fetch('/api/jobs/'+jid,{method:'DELETE'}).catch(()=>{});
    removeCard(jid);
}                                                    // 内层从未被调用
```
用户点击卡片上的 ✕ 按钮时，外层 cancelCard 执行 → 只定义了一个内层函数 → 直接返回 undefined。fetch DELETE 和 removeCard 不执行。Worker 进程继续运行，job 目录不清理。

**根因**：推测为编辑时误操作——可能先写了内层函数体，然后 IDE 自动补全或手动补了外层函数声明，未删除重复的 function 行。

**解决方案**：删除重复的 `function cancelCard(jid){`，保留单层函数定义。

**已修** ✅（V2.04 修订 — 删除重复内层函数声明）


---

## P041 | 2026-05-21 | V2 后端 | 硬编码 API 密钥在源码中（🔴 blocking）

**问题**：三个生产密钥以明文硬编码：
- `orchestrator.py:17` — GLM 平台 API Key（¥3 档平台提供）
- `baidu.py:5-6` — 百度翻译 APP_ID + SECRET

任何能访问代码/仓库的人可直接拿走使用。如果代码已推送到 GitHub，密钥已泄露。

**根因**：开发便利优先，密钥直接写在源码中未做环境变量抽象。

**解决方案**：
1. **立即轮换**这三个密钥（在百度/GLM 后台重新生成，旧 key 作废）
2. 改用 `os.environ.get("BAIDU_APP_ID")` 等环境变量
3. 检查 git history 确认是否已推送，如已推送需清理历史或视为已泄露
4. 添加 `.env.example` 模板文件，`.env` 加入 `.gitignore`

**已修** ✅（V3 删除 PLATFORM_KEYS，改为 _ALL_FREE 字典。orchestrator.py:16-20 不再硬编码任何 API Key）


---

## P042 | 2026-05-21 | V2 orchestrator | Token 用量统计触发重复 LLM API 调用（🟡 important）

**问题**：`orchestrator.py:259-267` 在翻译完成后，为获取 token 用量又调用了一次 `backend.translate_batch(batch)`：
```python
if backend and backend.is_llm:
    r = backend.translate_batch(batch)  # 第二次 API 调用，只为拿 tokens_used
```
等于每批翻译消耗**双倍** LLM API 费用。

**根因**：`_run_speed_mode_batch` / `_run_quality_mode_batch` 返回的是翻译文本列表，丢弃了 `TranslationResult` 对象中的 `tokens_used` 字段。统计用量时只能重新调用。

**解决方案**：修改两个 mode 函数，返回 `(translated_list, TranslationResult | None)` 而非 `(translated_list, backend_name | None)`。在第一次调用时就拿到 token 信息。

**已修** ✅（V3 重构 _run_speed_mode_batch 直接返回 r.tokens_used，一次 API 调用同时拿到翻译+用量。旧的双调用死代码已删除）


---

## P043 | 2026-05-21 | V2 orchestrator | 免费模式下用户提供的 LLM Key 仍会参与竞速（🟡 important）

**问题**：`orchestrator.py:221` 的 else 分支：
```python
all_backends = ordered_backends + llm_backends
```
免费用户如果在 config 中传了 `apiKeys`（手动修改前端 JS），LLM 后端会与免费后端一起参与 speed mode 并行竞速。与产品设计「免费用户不可用 LLM」矛盾。

**根因**：`_build_llm_backends(api_keys)` 无条件构造 LLM 后端，免费模式下也未过滤。

**解决方案**：免费模式（`tier == "free"`）下忽略 `api_keys` 参数，不调用 `_build_llm_backends`。`all_backends` 仅使用 `ordered_backends`。

**已过时**（V3 砍掉付费墙，所有用户均可自备 Key 使用 LLM。orchestrator.py:1-2："Tier system removed — LLM access gated by whether user provides API keys"）


---

## P044 | 2026-05-21 | V2 worker | 烧录完成但 checkpoint 写入前崩溃导致重复烧录（🟡 important）

**问题**：`worker.py:264-286` 的步骤顺序：
```
build_srt → build_ass_styled → burn_subtitles（耗时长）
→ stderr_progress("burn", 98, ...)
→ ck.save("done", ...)  ← checkpoint 在最后
```
若 `burn_subtitles` 成功但 `ck.save("done")` 之前进程崩溃，`output_zh.mp4` 已存在但 checkpoint 不是 "done"。用户重新提交同一视频时走完整流水线，重复烧录。

**根因**：checkpoint 写入在烧录之后，中间无保护。

**解决方案**：烧录完成后立即 `ck.save("burn", meta=config)`，然后 `ck.save("done", meta=config)`。或在步骤判断中加「输出文件已存在 → 跳过」逻辑。

**已修** ✅（2026-06-05。worker.py:493-502 — 烧录前检查 video_out 是否存在，封装前检查 video_out_mux 是否存在。存在则跳过，避免崩溃恢复后重复编码。5 个回归测试，全量 278 测试通过）


---

## P045 | 2026-05-21 | V2 key_manager | 加密密钥绑定主机名，变更后密钥全部失效（🟡 important）

**问题**：`key_manager.py:21` 用 `platform.node()`（macOS 主机名）作为密钥派生材料。主机名因网络环境变化（DHCP .local 后缀变更、用户手动改名）而改变时，所有已加密的 API Key 永久无法解密，且无恢复机制。

**根因**：`platform.node()` 不稳定，不适合作为加密材料。

**解决方案**：使用稳定机器标识（如 Mac 序列号 `ioreg -l | grep IOPlatformSerialNumber`、或直接用 macOS Keychain）。解密失败时给出明确提示而非静默返回密文。

**已过时** ✅（2026-06-05 核查）。V3 已实质性砍掉加密方案——`encrypt_api_key`/`decrypt_api_key` 在整个项目中无任何调用方（grep 全项目零结果）。API Key 现明文存储于 `~/.video-subber/config.json`。`key_manager.py` 中仅 `scrub_logs`（日志脱敏）仍在使用，加密相关函数为死代码。清理归入 V3.08（目录整理时顺带删）。


---

## P046 | 2026-05-21 | V2 前端 | 字幕样式确认按钮 UX 困惑（🟢 nit）

**问题**：用户修改字体/颜色后预览实时更新，但必须再点一次「✓ 应用样式到当前任务」。如果忘了点，修改被静默丢弃。两步确认模式在实时预览场景下容易造成困惑。

**建议方案**：去掉确认按钮改为即时生效，或用户未确认时点击「开始翻译」给出醒目提示。

**已过时**（V3.09 PySide6 重写前端，字幕样式交互将随新 UI 重新设计）


---

## P047 | 2026-05-21 | V2 后端 | BaiduBackend 异常时未记录日志（🟢 nit）

**问题**：`baidu.py:53-58` catch 了异常但 `e` 未被日志记录。排查翻译失败原因时无任何线索。

**解决方案**：添加 `logging.getLogger("baidu").warning("翻译失败: %s", e)`。

**已过时**（V3 砍掉百度翻译，BaiduBackend 类已从代码库中移除）

---

## P163 | 2026-05-21 | V2.04 前端 | 初始化时序混乱——页面加载时灰蒙蒙锁死，模型滑块消失
1. Whisper 模型选择滑块（5 个圆点）消失
2. API Key 平台下拉框空白
3. 翻译模式无法点击切换
4. 页面整体灰蒙蒙锁死

**根因链**（三层叠加）：
1. **localStorage 残留旧 activeJobs** → 页面加载时 `reconnectJobs()` 检测到 → 调 `lockUI()` 灰化所有控件（P038）
2. **初始化逻辑散落在 `<script>` 底部裸跑**，没有 `DOMContentLoaded` 保护——DOM 未挂载完毕时 `document.getElementById` 可能返回 null
3. **`fetch('/api/backends')` 是异步的，但 `buildModelSlider()` 和 `updateBackendLists` 不等待数据返回就执行**——虽然 buildModelSlider 用的是硬编码常量（不依赖异步数据），但 `updateBackendLists` 需要 API 返回数据才能填充下拉框和设置 tier 显示

三层叠加导致：加了 `?fresh` 清除了 localStorage，但如果浏览器缓存了旧版 HTML，或者 DOM 没就绪就执行了 JS，问题依然复发。

**工程师的三次迭代诊断**：
- 第一版 B（错误）：认为是异步竞争导致 TypeError 崩溃——事实错误，buildModelSlider 不依赖异步数据
- 第二版 B（有坑）：提供全量重写代码，addJobCard 丢了下载按钮、WebSocket URL 写错、lockUI 扩大化
- 第三版 B（最终版）：仅替换 Init 区域，用 `DOMContentLoaded` + `async/await` 外壳包裹初始化序列。内部函数全部保留不动
- Claude（A）：指出根因是 localStorage 残留，但修复后未验证浏览器缓存是否导致新代码未加载
- Jobs（第三方）：裁决 A 的根因 + B 的架构外壳，指出所有人忽略了浏览器缓存问题

**最终解决方案**（V2.04 修复）：
1. 用 `document.addEventListener('DOMContentLoaded', async () => {...})` 包裹全部初始化逻辑
2. `?fresh` 处理用 `await fetch('/api/reset')` + `return` 硬阻断
3. 链式 async/await：先等 `/api/backends` 返回 → 再调 `updateBackendLists` → 再 `buildModelSlider` → 最后 `reconnectJobs`
4. 所有渲染函数加 `typeof === 'function'` 空值检查
5. 内部函数（addJobCard、connectCard、lockUI 等）**一个字不动**

**教训**：
- 浏览器缓存是第一个要排除的因素——`Cmd + Shift + R` 强刷必须在每次验证前执行
- 前端初始化必须用 `DOMContentLoaded` 包裹，裸跑在 `<script>` 底部的代码在 DOM 未就绪时会静默失败
- 异步数据加载必须 `await`，不能"发射后不管"
- 改动面越小越安全——只换外壳、不动血肉

**已过时**（V3.09 PySide6 重写前端，HTML 前端及 init 逻辑将废弃）


---

## P048 | 2026-05-21 | V2.04 main.py | `/api/reset` 无鉴权，任何人可清空全部任务（🔴 blocking）

**问题**：新增的 `POST /api/reset` 端点无任何访问控制。任何人发送 `POST /api/reset` 即可：
- 杀死所有正在运行的 Worker 子进程
- 清空所有内存索引（JOBS、GLOBAL_HASH_INDEX、JOB_ID_TO_HASH）
- 删除所有 job 目录及缓存

这在生产环境中是严重的拒绝服务漏洞。

**根因**：reset 端点设计用于开发和测试（配合 `?fresh` 前端参数），但未加 `?dev` 或 token 校验。

**解决方案**：
1. 短期：加 `?dev=true` 参数校验，仅开发模式下可用
2. 或：要求前端传 `X-Reset-Token` header，服务端从环境变量读取校验
3. 长期（V3）：仅管理员账号可调用

**已修** ✅（V3 删除 /api/reset 端点。grep main.py 无匹配结果）

---

## P165 | 2026-06-06 | LLM 翻译 | 内容闸 + LLM 健康检查应前移到 P1 之后——浪费 token（🟡 important）

**问题**：当前内容闸（`_is_valid_translation`，CJK 比例 < 10% 判定非中文）只在 P2 滑窗翻译的每行输出做逐行检查。但 LLM 健康检查在 `translate_with_orchestrator` 中没有前置检查——P1 跑完直接跑 P2，如果 LLM 在 P2 全部失败（如 DeepSeek 间歇性俄语照抄），P1 的 token 也白花了。

**具体缺陷**：
1. P1 输出需要中文（术语提取要求"原文→中文翻译"，语境摘要要求"中文，100字内"），但当前代码没有检查 P1 输出是否真的含中文
2. P1 本身既是功能调用，也是天然的烟雾测试——如果 LLM 摘要都返回不了中文，后面翻译肯定废了
3. 内容闸放在 P2 每行检查是对的（细粒度兜底），但缺少 P1 层面的粗粒度前置检查（省 token）

**根因**：`translate_with_orchestrator` 中，P1（`extract_terminology` + `_generate_context_summary`）的返回值没有做目标语言校验就进入了 P2。

**解决方案**（2026-06-06 Jobs 讨论锁定）：
1. P1 之后加一层粗粒度内容闸：语境摘要 CJK 比例 < 10% → 判定 LLM 不可靠 → 降级免费引擎，不跑 P2
2. P1 超时/异常 → `except Exception` 已有处理，自动进入降级链（现状正确，保持）
3. P2 逐行 `_is_valid_translation` 保留，作为细粒度兜底
4. 不需要额外加"预检"步骤——P1 本身就是烟雾测试

**归属**：V3.07

**已修** ✅（2026-06-07 — orchestrator.py P1 摘要后 CJK<10% → `continue` 跳到降级链下一个 LLM）

---

## P166 | 2026-06-06 | 幻觉过滤 | L4 正则硬过滤在双模型确认后仍丢弃——处理顺序与规则力度需调整（🟡 important）

**问题**：当前五层过滤处理顺序为 L1 → L4 → L5 → L2 → L3。L4（正则规则匹配）在 L2（Whisper vs Qwen3-ASR 交叉验证）之前执行——L4 做丢弃决策时不知道 L2 是否已双模型确认该文本。L4 的规则是单模型时代产物，在双模型架构下部分规则会误杀真实对话。

**具体场景**：
- 说话人大笑说"哈哈哈哈哈" → Whisper+P6转录为"哈哈哈哈哈"→ L2 交叉验证：两个模型一致，文本可信 → 但 L4 重复模式正则跑在 L2 前面 → 检测到重复字符 → 直接丢弃
- 两个独立 AI 都确认的内容，被一条正则规则推翻

**根因**：L4 规则无差别硬丢弃，不区分"单模型时代的高风险模式"和"双模型确认后的真实内容"。

**解决方案**（2026-06-06 plan-eng-review 锁定）：

**1. 调整处理顺序：L1 → L2 → L4 → L5 → L3**
- L2 提前，先完成交叉验证，让 L4 知道双模型是否确认了该文本

**2. L4 规则分两类**：

| L4 规则 | 双模型确认后 | 原因 |
|---------|:--:|------|
| 纯符号 | 仍丢弃 | 两个模型都只出符号 = AI 崩溃 |
| 括号描述 "(掌声)" | 仍丢弃 | Whisper 内部格式泄露 |
| 循环句式 | 仍丢弃 | 安全兜底 |
| 关键词黑名单 | **不丢** | 双模型确认可信度 > 关键词猜测 |
| 重复模式 "哈哈哈" | **不丢** | 双模型确认可信度 > 重复检测 |
| 重复词 | **不丢** | 双模型确认可信度 > 重复检测 |

**3. 改动面**：单文件 `hallucination_filter.py`，`filter_hallucinations()` 调整执行顺序 + L4 加条件判断。~10 行。

**归属**：V3.07

**已修** ✅（2026-06-07 TDD 通过，顺序 L1→L2→L4→L5→L3，l2_status 标注，6 测试）

---

## P049 | 2026-05-21 | V2 前端 | DOMContentLoaded init 与内联脚本双重执行（🟡 important）

**问题**：P038 修复时新增了 `DOMContentLoaded` async init 外壳（line 778-845），但旧的内联脚本代码（line 382 `reloadEnv()`、line 414 `fetch('/api/backends')`、line 448 `buildModelSlider()`）未被删除。导致：

1. `/api/backends` 被 fetch **两次**（line 414 内联 + line 805 DOMContentLoaded）
2. `buildModelSlider()` 跑了**两次**（line 448 + line 817）
3. `loadConfig()` 在 DOMContentLoaded 中未被 await（line 801），与 `/api/backends` fetch 并发竞速——可能出现 `updateBackendLists` 用默认 tier="free" 渲染 UI 后，`loadConfig` 才完成并覆盖 tier 为 paid_1/paid_3

**根因**：增量修补——加了新 init 外壳但未做减法清理旧代码。

**解决方案**：
1. 删除 line 414 的裸 `fetch('/api/backends')` 和 line 448 的裸 `buildModelSlider()`
2. DOMContentLoaded 中将 `loadConfig()` 改为 `await loadConfig()`（先等 config 就绪，再用最终 tier 调 `/api/backends`）
3. `reloadEnv()` 保留在 DOMContentLoaded 外（环境检测不依赖 API 数据，可尽早触发）

**已过时**（V3.09 PySide6 重写前端，HTML init 双重执行问题不复存在）


---

## P050 | 2026-05-21 | V2 orchestrator | `_flatten_batches` 死代码（🟢 nit）

**问题**：`orchestrator.py:39-47` 定义了 `_flatten_batches()` 函数，但整个项目中无任何调用。`translate_with_orchestrator` 使用 `all_results.extend(batch_results)` 达到了同等效果。

**根因**：早期设计预留了批量展平逻辑，后来改用 `extend` 直接追加，函数未删除。

**解决方案**：删除该函数。

**已修** ✅（V3 重写 orchestrator.py，_flatten_batches 函数已不存在。grep 无结果）


---

## P051 | 2026-05-21 | V2 worker | `STAGE_CACHE` 缺失 subtitle 和 burn 阶段（🟢 nit）

**问题**：`worker.py:125-129` 的 `STAGE_CACHE` 只定义了 3 个阶段（extract_audio / transcribe / translate），但 `SETTING_STAGE`（line 113-123）中另有 `"style" → "subtitle"` 和 `"burn" → "burn"` 两条映射。当字幕样式或烧录设置变更时，缓存失效循环走到 `if s in STAGE_CACHE:`（line 159）会静默跳过——字幕和烧录阶段的缓存无法清除。

**根因**：`STAGE_CACHE` 只覆盖了有磁盘缓存文件的阶段。subtitle 和 burn 阶段输出文件（output.srt / output_zh.mp4）存在 job_dir 而非 cache 目录，未纳入统一管理。

**解决方案**：`STAGE_CACHE` 补上 subtitle 和 burn 的缓存路径，或改为在对应设置变更时手动清理 output.srt / output_zh.mp4。

**V2.07 修复**：结构改为 `dict[str, list[str]]`（一个 stage 可有多个产物），subtitle 同时清 `.srt`（job_dir）和 `.ass`（cache 里，烧录用），burn 清最终 `.mp4`。**关键修正**：乔布斯方案漏了 `.ass` ——只清 `.srt` 后 `.ass` 还在缓存，烧录会复用旧 `.ass` 导致样式仍不生效。

**已修** ✅（V2.07）


---

## P052 | 2026-05-21 | V2 google.py | GoogleBackend 逐条翻译无限流控制（🟢 nit）

**问题**：`google.py:15-16` 对每条 text 逐一发 HTTP 请求，循环内无任何延迟。大批量场景（200+ 条字幕）可能触发 Google Translate API 限流，导致后续全部返回 `[翻译失败]`。对比 BaiduBackend 使用 `\n` 批量 API 天然规避了此问题。

**根因**：Google Translate 的免费 Web API（`translate.googleapis.com`）无批量接口，只能逐条请求。但代码中未加请求间隔。

**解决方案**：每条翻译后 `time.sleep(0.15)`（V1 已验证的安全间隔），或当结果连续失败 3 条时暂停 5 秒后重试。

**已修** ✅（V3 GoogleBackend 改用 `|||` 分隔符批量拼接全部文本，一次 HTTP 请求完成所有翻译。google.py:14-25）

---

## P040 | 2026-05-22 | V2.05 前端 | localStorage 被清除后无法自动恢复——缺后备恢复路径

**问题**：用户清浏览器缓存、换浏览器、或用隐私模式后，`localStorage` 中的 `activeJobs` 丢失。打开页面看到的是空白初始界面——必须手动拖入同一个视频，触发后端哈希查重才能恢复之前的进度和设置。

**根因**：`reconnectJobs()` 完全依赖 `localStorage` 作为任务追踪的唯一源头。磁盘上 `~/.video-subber/jobs/` 里的 `meta.json` 明明记录了未完成任务的全部信息（status、config、file_hash），但前端没有主动向后端查询的路径。`GLOBAL_HASH_INDEX` 在内存中已索引了所有历史任务，但没有 API 暴露给前端。

**解决方案**（V2.05）：
1. 后端新增 `GET /api/active-jobs` —— 遍历 `GLOBAL_HASH_INDEX`，返回所有 status 为 queued/running/interrupted 的任务（job_id、status、config、progress）
2. 前端 Init 生命周期中，`reconnectJobs()` 之前加后备恢复逻辑：
   - 检查 `localStorage.getItem('activeJobs')`
   - 为空 → `fetch('/api/active-jobs')` 从后端磁盘捞回未完成任务
   - 有结果 → 写回 `localStorage` → `reconnectJobs()` 正常恢复
3. `reconnectJobs()` 本身不动——它只管读 localStorage 重连，不管数据来源

**三条恢复路径（改动后）：**

| 场景 | 恢复路径 | 用户操作 |
|------|----------|----------|
| localStorage 有 activeJobs | `reconnectJobs()` | 零 |
| localStorage 空，磁盘有未完成任务 | `GET /api/active-jobs` → 写回 → `reconnectJobs()` | 零 |
| localStorage 空，磁盘也无任务 | 初始界面，等待用户操作 | 零 |
| 以上皆失败，拖入同一视频 | 哈希查重（`POST /api/jobs`）| 一次拖拽 |

**修改文件**：`backend/main.py`（新增 endpoint）、`frontend/index.html`（Init 区域加后备恢复）

**已修** ✅（V2.05）

---

## P053 | 2026-05-22 | V2.05 transcriber_v2 | large-v3 片头合并——预处理净化管道（方案 D+F 组合）

**问题**：large-v3 将片头音乐（0-5s）与第一句对话（5-8.76s）合并为一个长 segment。合并段内同时含音乐和人声 → nsp 判人声（段内确有对话）+ 物理特征判音乐（段内确有片头）→ 双法官冲突 → 标 [?] 保留。L4 片头加强规则因文本长度 > 4 字符而无法拦截。

**根因**：Whisper 不同模型的 segmentation 行为不可控。medium 切出独立短段 → L4 拦截；large-v3 合并大段 → L4 绕过。五层过滤系统本身正确（L1a=0 零误杀），但接收的输入粒度不同导致漏网。

**解决方案**（V2.05）：
在 Whisper 输出与五层过滤之间插入预处理净化管道（Phase 2.5）。仅在前 3 段 + nsp 判人声 + 物理判音乐时触发：

| 路径 | 条件 | 做法 |
|------|------|------|
| **方案 D（主力）** | seg.words（词级时间戳）可用 | `seg._replace(start=words[0].start)` 缩进至人声起点 |
| **方案 F（兜底）** | 词戳不可用 | `_find_audio_mutation_point()` 用 RMS 滑动窗口找能量突变点 → 切掉音乐前缀 |

关键设计：
- 使用 `._replace()` 安全更新 namedtuple，保留 no_speech_prob / avg_logprob 等元数据
- 切分后对新段重新提取物理特征，确保下游过滤拿到干净信号
- 音乐前缀在预处理层直接丢弃（不入下游），伴 `[方案D/F]` 标签日志可追溯
- 正常段 no-op 零开销原样通过
- 五层过滤规则、FILTER_CONFIG、Worker 调用参数均一行未改

**改动范围**：`v2/backend/transcriber_v2.py` — 3 处改动，+~65 行代码
1. `model.transcribe(word_timestamps=True)` — 开启词级时间戳
2. Phase 2.5 净化预处理管道 — ~50 行
3. `_find_audio_mutation_point()` 辅助函数 — ~40 行

**参与人员**：产品方向 Steve Jobs（乔布斯视角 skill）、架构方案来自外部工程师（7 方案评估 + ._replace() 修正）、最终实现 Claude Code

**已修** ✅（V2.05）


---

## P054 | 2026-05-22 | V2.05 worker.py | 上游模块 print() 泄漏——静默丢失日志（卫生债务）

**问题**：Worker 第 96 行 `sys.stdout = sys.stderr` 将 stdout 重定向以保护 JSON 进度协议。但 transcriber、translator 等上游模块中残存的 `print()` 语句因此流向 stderr，被主进程 `_read_stderr` 线程读走后在终端闪过，**不进入任何日志文件，永久丢失审计痕迹**。

**现状**：日志主管道已通过双 Handler 方案修复（P053）。此问题属于**卫生债务**，不影响核心功能。

**解决方案**：后续迭代中，逐步将上游模块中用户可感知的关键 `print()` 替换为 `logging.getLogger(__name__).info()`。不需要全量重构——只换步骤进度、过滤摘要、完成提示。

**优先级**：低（卫生问题，非功能缺陷）

**V2.07 修复**：`grep -rn "print("` 全量扫描后分类——`transcriber_v2.py` 8 处用户感知 print（加载模型/检测语言/过滤摘要/说话人/VAD）替换为 `_log.info/warning`；`worker.py:228` 缓存清除提示替换为 `_log.info`；`worker_manager.py:93` 协议性 stderr 转发保留 print 但附加 `_log.debug`。同步更新 `worker.py:71-73` 的架构债务注释。

**已修** ✅（V2.07）

---

## P041 | 2026-05-22 | V2.05 前端 | done 分支清空 State.videoFile 导致重跑按钮无响应

**问题**：任务完成后，用户拖入同一视频想用新设置重跑。第一次点击 → 哈希命中 → done 分支提示"可修改设置后重新开始" → 按钮变亮。用户点第二次 → 页面无任何反应。

**根因**：done 分支在 return 前执行了 `State.videoFile = null`，把用户手中的文件引用清空了。sbtn handler 第一行 `if (!State.videoFile) return` 直接静默退出。用户看到亮着的按钮却点不动——按钮文字暗示"点一下就行"，实际必须重新拖入视频。

**解决方案**（V2.05）：
1. 删除 `State.videoFile = null` 和 `$('dzFile').style.display = 'none'` —— 文件引用保留
2. 按钮文案从「重新开始翻译」改为「用新设置重跑」—— 明示用户新设置会被应用
3. 用户改完设置 → 直接点按钮 → 文件还在 → `force=true` 发出 → 新任务创建

**修改文件**：`frontend/index.html` done 分支（删两行，改一行文案）

**已修** ✅（V2.05）


---

## P055 | 2026-05-22 | V2.05 main.py | 取消任务后日志目录未同步清理（🔴 blocking）

**问题**：`cancel_job` 端点只删了 `jobs/{id}/`（通过 `worker_manager.cancel()`），`logs/{id}/` 目录残留在磁盘上。

**根因**：`cancel_job` 只调了 `worker_manager.cancel()` → `shutil.rmtree(job_dir)`，遗漏了日志目录。

**解决方案**（V2.05）：
在 `cancel_job` 中 `worker_manager.cancel()` 之后加：
```python
time.sleep(0.1)  # 给 Worker 子进程释放文件句柄
log_dir = LOG_DIR / job_id
if log_dir.exists():
    shutil.rmtree(log_dir, ignore_errors=True)
```
`LOG_DIR` 在 `main.py` 顶部定义，与 `JOBS_DIR` 对称。

**已修** ✅（V2.05）


---

## P056 | 2026-05-22 | V2.05 main.py | V2.04 旧版单日志残留（🟡 important）

**问题**：V2.04 日志格式为扁平单文件 `logs/job-{id}.log`。V2.05 已升级为目录结构，但旧文件未清理。

**解决方案**（V2.05）：
`startup()` 开头：`for old_log in LOG_DIR.glob("job-*.log"): old_log.unlink()`

**已修** ✅（V2.05）


---

## P057 | 2026-05-22 | V2.05 main.py | 缺少 .version 文件（🟡 important）

**问题**：无文件标记数据格式版本。V3 升级时无法判断是否需要迁移。

**解决方案**（V2.05）：
`startup()` 末尾写入 `~/.video-subber/.version` JSON，`datetime.now(timezone.utc)`（Python 3.12+ 兼容），仅版本变化时写入保留真实升级时间戳。

**已修** ✅（V2.05）


---

## P058 | 2026-05-22 | V2.05 worker.py | 任务完成后 input.ts 永驻磁盘（🟡 important）

**问题**：每任务存 ~700MB 视频副本，完成后不删。5 任务 = 3.5GB+，用户不知情。

**根因**（Jobs 第一性原理）：done 之后 `input.ts` 不被任何代码路径读取——查重走 `GLOBAL_HASH_INDEX`（`meta.json`），不依赖文件。V2 无重跑 UI。不需要 30 天定时器。

**解决方案**（V2.05）：`ck.save("done")` 后加 `Path(video_path).unlink(missing_ok=True)`

**已修** ✅（V2.05）


---

## P059 | 2026-05-22 | V2.06 排期 | ctranslate2 C++ 层 stdout 污染 IPC 管道（🟢 nit）

**问题**：faster-whisper 底层 ctranslate2 可能直接向 stdout/stderr 写原始信息。当前 `worker_manager.py:65` 用 `json.JSONDecodeError: pass` 兜底不崩溃，但丢失 C 层错误信息。

**处置**：排期 V2.06。

**V2.07 修复**：`_read_stdout` 的 `except json.JSONDecodeError: pass` 改为 `_log.debug("[worker:%s] non-JSON stdout (likely C++ layer): %s", job_id, line[:200])`，C++ 层任何输出都进 debug.log。同时 `_read_stderr` 也附加 `_log.debug` 写盘，stderr 不再只在终端闪过。模块顶部一次性 `_log = logging.getLogger(__name__)`。

**已修** ✅（V2.07）

---

## P042 | 2026-05-22 | V2.05 前端 | done 分支三条子路径互相冲突——重跑失败 + 视频按钮误显示 + syncUI 覆盖新设置

**问题**：任务完成后，用户想用新设置重跑同一个视频。拖入 → 点第一次 → Toast 提示已完成 → 改设置 → 点第二次「用新设置重跑」→ 又弹已完成，新任务没创建。同时完成卡片上出现视频下载按钮（用户选的是仅 SRT，没烧录过视频），点进去返回 `{"error":"not found"}`。

**三层根因**：

1. **`State.videoFile` 被无故清空。** done 分支在 return 前写了 `State.videoFile = null`。用户点第二次时 sbtn handler 第一行 `if (!State.videoFile) return` 直接静默退出。按钮亮着但点多少次都没反应。（详见 P041）

2. **`syncUI` 在 done 状态覆盖了用户的新设置。** syncUI 原本在 status 判断之前无条件执行，用旧 config 把用户刚选的设置（如逐段翻译）盖回了旧设置（上下文感知）。

3. **浏览器缓存导致 force 令牌未加载。** 前两层修完后代码逻辑是完整的——第 602 行设 `State.forceRerun = true`，第 584 行读它并设 `cfg.force = true`，后端第 175 行接收。但浏览器缓存了旧版前端，这三行在浏览器内存里不存在，config payload 里没有 `"force": true`。

4. **视频下载按钮硬编码。** `renderCardDone(d.job_id, {result_video: true})` 写死了 true，未读取用户真实配置 `burn: false`。

**四次修复**：

| # | 修复 | 位置 |
|---|------|------|
| 1 | 删掉 `State.videoFile = null` 和 `$('dzFile').style.display = 'none'` | done 分支 return 前 |
| 2 | syncUI 从无条件执行移到 `if(!card)` 分支（仅 running/queued） | sbtn handler d.found 块 |
| 3 | `{result_video: !!(d.config && d.config.burn)}` 替代硬编码 true | done 分支 renderCardDone 调用 |
| 4 | 浏览器 `Cmd + Shift + R` 强刷 | — |

**排查此类问题的标准流程（后续复用）**：

1. 打开 DevTools → Network 标签 → 勾选 Disable cache
2. 找到 `/api/jobs` 请求 → 看 Request Payload 的 config 字段
3. 对比 payload 与磁盘上 `index.html` 的对应行代码——如果不一致，浏览器缓存是元凶
4. 确认后端对应的接收代码也存在且逻辑正确
5. `Cmd + Shift + R` 强刷后复测

**已修** ✅（V2.05）

---

## P060 | 2026-05-22 | orchestrator.py | context_aware 参数被接收但从未参与分支判断 + P4 双倍扣费（🟡 important）

**问题**：两个关联 bug，同一病根。

**Bug 1 — 前端开关无效**：付费用户在高级设置面板切换「逐段翻译 / 上下文感知」，翻译行为完全相同。`context_aware` 参数在 `translate_with_orchestrator()` 签名和 docstring 中存在，但函数体内无任何分支判断引用它。判断条件 `if tier in ("paid_1", "paid_3") and llm_backends:` 只检查了 tier 和是否有 Key。已通过两次真实运行验证（同一视频、同模型 large-v3，contextAware=true 和 false 均产生 1 次 LLM API 调用，走同一条 `_translate_whole_with_markers()` 路径）。

**Bug 2 — P4 双倍扣费**：免费路径批处理循环中，batch 函数返回的 `TranslationResult` 对象在半路被丢弃（只取了 `r.translated` 文本），外层代码为了获取 token 统计数据又调用了一次 `backend.translate_batch(batch)`。每批翻译消耗双倍 LLM API 费用。

**根因**：
1. `_run_speed_mode_batch` 和 `_run_quality_mode_batch` 的返回值丢弃了 `TranslationResult.tokens_used`
2. `context_aware` 参数被接收但从未参与分支判断

**解决方案（三合一）**：

| # | 改动 | 说明 |
|---|------|------|
| 1 | context_aware 分支 | L268 引入判断：true → `_translate_whole_with_markers()`（整篇上下文感知），false → `_split_into_batches` + 逐批 LLM（逐段翻译）|
| 2 | batch 函数签名升级 | `_run_speed_mode_batch` 返回值 2→3（加 `tokens_used`），`_run_quality_mode_batch` 返回值 3→4（加 `tokens_used`）。新增 `_merge_tokens()` 安全累加。6 处调用点联动解构。删除二次 API 调用的 P4 死代码 |
| 3 | LLM RPM 缓冲 | 逐段 LLM 路径 batch 间 `sleep(1.5)`（免费路径保持 `sleep(1)`）|
| 附带 | 删除重复赋值 | 原 L271-272 `retry_batches = _split_into_batches(...)` 连着写了两遍，删掉一行 |

**改动文件**：`v2/backend/translation/orchestrator.py`（单文件，不碰前端、不碰 V1.3）

**已修** ✅（V2.05）

---

# V2.05 全面代码审查修复记录（2026-05-22）

以下 27 条问题（C01-C20 + U1-U7）来源于 code-review-excellence + Steve Jobs 视角的全面审查。
每条包含：问题定位 → 解决方案 → 修改代码 → 涉及文件。

---

## P061 | 2026-05-22 | 🔴 orchestrator.py | C01 — GLM 平台 API Key 硬编码在源码中

**问题**：`PLATFORM_KEYS` 列表第 16-18 行写死了真实的 GLM API Key。任何人拿到代码仓库就能盗刷额度。

**解决方案**：改为从环境变量 `VSUB_PLATFORM_KEYS` 读取，部署时在启动脚本中设置。付费三档用户的自动注入逻辑不变。

**修改位置**：`v2/backend/translation/orchestrator.py` 第 16-18 行

```python
# 替换为：
def _load_platform_keys() -> list[dict]:
    raw = _os.environ.get("VSUB_PLATFORM_KEYS", "[]")
    try:
        return _json.loads(raw)
    except Exception:
        return []

PLATFORM_KEYS: list[dict] = _load_platform_keys()
```

**已修** ✅（V2.05）

---

## P062 | 2026-05-22 | 🔴 main.py | C02 — CORS 允许任意来源

**问题**：`allow_origins=["*"]` 允许任意网站向 localhost API 发请求，恶意网页可通过 CSRF 清空用户数据。

**解决方案**：限制为本地回环地址。

**修改位置**：`v2/backend/main.py` 第 19 行

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8766", "http://localhost:8766"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**已修** ✅（V2.05）

---

## P063 | 2026-05-22 | 🔴 worker.py | C03 — 不可达死代码

**问题**：第 249 行 `if segments is None:` 永远为 True——上方 if/else 分支均保证 `segments` 已赋值。死代码 7 行。

**解决方案**：物理删除第 249-254 行。

**修改位置**：`v2/backend/worker.py`，删除第 249-254 行

**已修** ✅（V2.05）

---

## P064 | 2026-05-22 | 🔴 main.py | C04 — 开发者激活码硬编码

**问题**：三个激活码 `VSUB-DEV-001/002/003` 直接写在源码第 121-125 行。任何人读代码即可进入开发者模式。

**解决方案**：改为从环境变量 `VSUB_DEV_CODES` 逗号分隔读取，未设置时为空集合。

**修改位置**：`v2/backend/main.py` 第 121-125 行

```python
_raw_dev = _os.environ.get("VSUB_DEV_CODES", "")
_LOCAL_CODES: set[str] = set(
    c.strip().upper() for c in _raw_dev.split(",") if c.strip()
) if _raw_dev else set()
```

**已修** ✅（V2.05）

---

## P065 | 2026-05-22 | 🔴 worker.py | C05/C13 — 任务完成后静默删除用户视频

**问题**：第 336 行 `Path(video_path).unlink(missing_ok=True)` 在流水线完成后删除用户上传的原始视频。用户想换模型重跑必须重新上传。

**解决方案**：删除 unlink 调用，保留视频直至用户主动取消任务。

**修改位置**：`v2/backend/worker.py` 第 333-336 行，删除 `Path(video_path).unlink(missing_ok=True)`

**已修** ✅（V2.05）

---

## P066 | 2026-05-22 | 🟡 key_manager.py | C06 — 密钥派生未使用 PBKDF2

**问题**：`_derive_key()` 只做一次 SHA256，无盐值无迭代。文档承诺的是 PBKDF2 机器绑定。

**解决方案**：使用 `hashlib.pbkdf2_hmac` 加入固定盐值和 10 万次迭代。

**修改位置**：`v2/backend/key_manager.py` 第 20-24 行

```python
_SALT = b'video-subber-v2-fixed-salt-2026'

def _derive_key() -> bytes:
    material = f"{platform.node()}-{os.getuid()}".encode()
    key_bytes = hashlib.pbkdf2_hmac('sha256', material, _SALT, 100_000, dklen=32)
    return base64.urlsafe_b64encode(key_bytes)
```

**已修** ✅（V2.05）

---

## P067 | 2026-05-22 | 🟡 worker_manager.py + main.py | C07/C12 — cancel 删除所有缓存

**问题**：`WorkerManager.cancel()` 不仅 kill 子进程，还 `shutil.rmtree(job_dir)` 删除全部缓存。用户误点取消后已处理到 80% 的进度全部白费。

**解决方案**：
- `worker_manager.py`：cancel 只杀进程，不删目录
- `main.py`：cancel_job 只删 input 视频 + 日志，保留 cache/ 下所有中间产物

**修改位置 1**：`v2/backend/worker_manager.py` 第 40-47 行

```python
def cancel(self, job_id: str):
    proc = self._processes.pop(job_id, None)
    if proc:
        proc.kill()
    self._job_dirs.pop(job_id, None)  # 不调用 rmtree
```

**修改位置 2**：`v2/backend/main.py` cancel_job 端点（第 536-552 行）

```python
@app.delete("/api/jobs/{job_id}")
def cancel_job(job_id: str):
    # ... worker_manager.cancel(job_id) ...
    # 仅删除 input 视频，保留缓存
    job_dir = JOBS_DIR / job_id
    if job_dir.exists():
        for input_file in job_dir.glob("input.*"):
            input_file.unlink(missing_ok=True)
    return {"ok": True}
```

**已修** ✅（V2.05）

---

## P068 | 2026-05-22 | 🟡 orchestrator.py | C08 — 极速模式超时仅 30 秒

**问题**：`_run_speed_mode_batch` 给所有线程 30 秒总超时。LLM 后端处理大批次可能需 60-120 秒。

**解决方案**：改为 120 秒 deadline，有效结果到达后提前跳出。

**修改位置**：`v2/backend/translation/orchestrator.py` 第 121-122 行

```python
deadline = time.time() + 120
for t in threads:
    remaining = deadline - time.time()
    if remaining <= 0:
        break
    t.join(timeout=remaining)
    if done_event.is_set():
        break
```

**已修** ✅（V2.05）

---

## P069 | 2026-05-22 | 🟡 google.py | C09 — Google 翻译逐条发送

**问题**：Google 后端逐条发 HTTP 请求，287 条 = 287 次请求。Baidu 已用 `\n` 批量拼接，Google 未同步。

**解决方案**：用 `|||` 分隔符拼接所有文本，一次 HTTP 请求翻完，再按分隔符拆开并按原始索引回填。

**修改位置**：`v2/backend/translation/google.py`，完整替换 `translate_batch` 方法

```python
def translate_batch(self, texts, source_lang="auto", target_lang="zh", context=""):
    SEPARATOR = " ||| "
    non_empty = [(i, t.strip()) for i, t in enumerate(texts) if t.strip()]
    results = ["" if not t.strip() else "[翻译失败]" for t in texts]
    if not non_empty:
        return TranslationResult(translated=[""] * len(texts), ...)
    joined = SEPARATOR.join(t for _, t in non_empty)
    # ... 一次 HTTP 请求，拆分后按 non_empty 索引回填 ...
```

**已修** ✅（V2.05）

---

## P070 | 2026-05-22 | 🟡 usage_reporter.py | C10 — 日志 count 始终为 0

**问题**：`_pending_usage.clear()` 在 `_log.info(..., len(_pending_usage))` 之前执行，count 永远为 0。

**解决方案**：先保存 count，再 clear。

**修改位置**：`v2/backend/usage_reporter.py` 第 57-59 行

```python
count = len(_pending_usage)
_pending_usage.clear()
_log.info("Token usage reported: %d records", count)
```

**已修** ✅（V2.05）

---

## P071 | 2026-05-22 | 🟡 worker.py | C11 — stderr_progress() sys.stdout 竞态

**问题**：`stderr_progress()` 临时切换 `sys.stdout` 全局变量，多线程场景下会破坏 JSON lines 协议。

**解决方案**：直接向保存的 `_real_stdout` 写入，不切换全局变量。同时修复 token_usage 发送的同样问题。

**修改位置**：`v2/backend/worker.py`
- `stderr_progress()` 函数（第 370-374 行）：直接 `_real_stdout.write(msg)` 
- token_usage 发送（第 275-277 行）：同上

**已修** ✅（V2.05）

---

## P072 | 2026-05-22 | 🟡 index.html | C14 — 按钮文案始终显示「开始翻译」

**问题**：用户选择「仅生成 SRT」时按钮仍显示「开始翻译」，语义不准确。

**解决方案**：抽象 `refreshStartBtn()` 函数，根据 `State.settings.burn` 动态切换文案。

**修改位置**：`v2/frontend/index.html`

```javascript
function refreshStartBtn(){
  if(!State.videoFile){sbtn.disabled=true;sbtn.textContent='选择视频文件开始';return}
  sbtn.disabled=false;
  sbtn.textContent=State.settings.burn?'开始翻译并烧录':'开始生成字幕';
}
```

**已修** ✅（V2.05）

---

## P073 | 2026-05-22 | 🟡 transcriber_v2.py + worker.py | C15 — 模型下载无进度反馈

**问题**：首次运行下载 Whisper 模型（1.5-3GB）时前端无任何提示，用户以为卡死。

**解决方案**：模型加载前通过 `progress_callback(-1)` 告知前端正在下载。Worker 的 `_trans_progress` 处理 -1 信号。

**修改位置 1**：`v2/backend/transcriber_v2.py` 第 268-269 行之间
```python
if progress_callback:
    progress_callback(-1)
model = WhisperModel(model_size, device="cpu", compute_type="int8")
```

**修改位置 2**：`v2/backend/worker.py` `_trans_progress` 函数
```python
def _trans_progress(n):
    if n == -1:
        stderr_progress("transcribe", 10, "正在下载语音识别模型（首次约需 2-5 分钟）…", ...)
        return
    # ... 原有逻辑 ...
```

**已修** ✅（V2.05）

---

## P074 | 2026-05-22 | 🟢 main.py | C16 — 两个 startup 事件处理器

**问题**：`_startup_dev`（第 220 行）和 `startup`（第 605 行）分散两处，执行顺序无保证。

**解决方案**：合并为单个 `startup` 函数，dev 初始化在前，索引重建在后。

**修改位置**：`v2/backend/main.py`
- 删除 `_startup_dev` 函数
- 将其逻辑合并到 `startup` 函数开头
- 删除重复的第二个 `@app.on_event("startup")` 块

**已修** ✅（V2.05）

---

## P075 | 2026-05-22 | 🟢 env_check.py | C17 — ffmpeg 路径仅适配 macOS Apple Silicon

**问题**：硬编码 `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`，Intel Mac 和 Linux 不可用。

**解决方案**：多路径搜索 + `shutil.which` fallback。

**修改位置**：`v2/backend/env_check.py` 第 13-14 行

```python
possible = []
for prefix in ["/opt/homebrew/opt/ffmpeg-full/bin", "/usr/local/opt/ffmpeg-full/bin",
               "/opt/homebrew/bin", "/usr/local/bin"]:
    candidate = os.path.join(prefix, "ffmpeg")
    if os.path.exists(candidate):
        possible.append(candidate)
possible.append("ffmpeg")  # PATH fallback
for ff in possible:
```

**已修** ✅（V2.05）

---

## P076 | 2026-05-22 | 🟢 worker.py | C18 — config 读取无 schema 校验

**问题**：config.json 字段类型错误时静默降级使用默认值，用户不自知。

**解决方案**：5 个关键字段（burn, model, translationMode, subtitles, tier）类型和值域强校验。

**修改位置**：`v2/backend/worker.py` 第 133 行之后

```python
def _validate_config(cfg: dict):
    if "burn" in cfg and not isinstance(cfg.get("burn"), (bool, type(None))):
        raise ValueError(f"config.burn 应为 bool")
    if "model" in cfg and cfg["model"] not in ("tiny", "base", "small", "medium", "large-v3", None):
        raise ValueError(f"config.model 无效: {cfg['model']}")
    if "translationMode" in cfg and cfg["translationMode"] not in ("speed", "quality", None):
        raise ValueError(f"config.translationMode 无效")
    if "subtitles" in cfg and cfg["subtitles"] not in ("yes", "no", None):
        raise ValueError(f"config.subtitles 无效")
    if "tier" in cfg and cfg["tier"] not in ("free", "paid_1", "paid_3", None):
        raise ValueError(f"config.tier 无效")

_validate_config(config)
```

**已修** ✅（V2.05）

---

## P077 | 2026-05-22 | 🟢 index.html | C19 — saveConfig 静默吞错

**问题**：`fetch('/api/config', ...).catch(()=>{})` 失败时无提示。用户以为保存成功，下次启动配置丢失。

**解决方案**：重试 2 次 + 降级 warn 日志。

**修改位置**：`v2/frontend/index.html` `saveConfig` 函数

```javascript
async function saveConfig(){
  localStorage.setItem('video-subber-config', JSON.stringify(cfg));
  for(let attempt=0; attempt<2; attempt++){
    try{const r=await fetch('/api/config',{...}); if(r.ok) return}catch(e){
      if(attempt===0) await new Promise(r=>setTimeout(r,1000));
    }
  }
  console.warn('配置保存到后端失败，已仅存储于浏览器本地');
}
```

**已修** ✅（V2.05）

---

## P078 | 2026-05-22 | 🟢 index.html | C20 — 样式变更回调重复

**问题**：4 个下拉选择器各自内联匿名回调，重复的 `updatePreview()` + 状态重置逻辑。

**解决方案**：提取命名函数 `onStyleChanged()`。

**修改位置**：`v2/frontend/index.html` 第 616 行

```javascript
function onStyleChanged(){updatePreview();State.settings.styleConfirmed=false;$('styleConfirmed').style.display='none'}
['styleFont','styleSize','styleColor','styleOutline'].forEach(id=>$(id).addEventListener('change',onStyleChanged));
```

**已修** ✅（V2.05）

---

## P079 | 2026-05-22 | 🟡 worker.py | U1 — 技术性错误直透给用户

**问题**：Worker 异常（RuntimeError 等）原样透传到前端，用户看到技术错误不知道该怎么办。

**解决方案**：用户友好错误映射函数 `_friendly_error()`，覆盖无语音、文件缺失、内存不足、超时、限流、认证失败等高频场景。

**修改位置**：`v2/backend/worker.py` 第 365-367 行异常处理

```python
def _friendly_error(raw: str) -> str:
    if "未检测到任何语音内容" in raw:
        return "视频中没有检测到语音。可能原因：视频为纯音乐、静音、或音频格式损坏。"
    if "No such file" in raw or "FileNotFound" in raw:
        return "缺少必要的模型文件。请检查网络连接，模型将在下次启动时自动下载。"
    if "Memory" in raw or "memory" in raw:
        return "内存不足，无法处理该视频。请尝试使用更小的模型（如 tiny 或 base）。"
    # ... 超时、限流、认证失败等 ...
    return f"处理过程中断。错误详情：{raw[:80]}"

except Exception as e:
    emit("error", message=_friendly_error(str(e)), failed_segments=[])
```

**已修** ✅（V2.05）

---

## P080 | 2026-05-22 | 🟡 index.html | U5 — 取消按钮太小

**问题**：`.job-card-cancel` 可点击区域仅 16px 宽，触摸屏/触控板上难以点中。

**解决方案**：扩大内边距和最小宽高，增加 hover 背景反馈。

**修改位置**：`v2/frontend/index.html` CSS 第 166-167 行

```css
.job-card-cancel{background:none;border:none;color:var(--sub);cursor:pointer;font-size:16px;padding:6px 10px;min-width:34px;min-height:34px;border-radius:6px;line-height:1;transition:all .15s}
.job-card-cancel:hover{color:var(--red);background:rgba(255,59,48,.08)}
```

**已修** ✅（V2.05）

---

## P081 | 2026-05-22 | 🟡 index.html | U6 — 保存路径用原生 prompt() 弹窗

**问题**：`prompt()` 弹窗无法粘贴路径、无法自动补全、可能被浏览器拦截。

**解决方案**：替换为页面内联 input 元素，支持 Enter 确认、Escape 取消、失焦自动保存。

**修改位置**：`v2/frontend/index.html` 第 517 行

```javascript
$('savePathBtn').addEventListener('click',()=>{
  const input=document.createElement('input');
  input.type='text';input.value=current;
  input.addEventListener('blur',()=>{/* 保存并替换回 display */});
  input.addEventListener('keydown',e=>{if(e.key==='Enter')input.blur();...});
  display.style.display='none';display.parentNode.insertBefore(input,display.nextSibling);
  input.focus();input.select();
});
```

**已修** ✅（V2.05）

---

## P082 | 2026-05-22 | 🟡 index.html | U7 — 样式确认按钮语义模糊

**问题**：按钮文案「✓ 应用样式到当前任务」让用户困惑：是应用到正在跑的任务，还是下一个任务？

**解决方案**：改为「✓ 确认样式（下次任务生效）」，确认提示改为「✓ 样式已保存，将在下次生成字幕时应用」。

**修改位置**：`v2/frontend/index.html` 第 243-244 行

**已修** ✅（V2.05）

---

# V2.05 最终架构决议（2026-05-22 · 三工程师审查闭环）

以下三条不是 bug，而是经过 Steve Jobs + 工程师A + 工程师B 三人多轮审查后锁定的架构方案。

---

## P083 | 2026-05-22 | 架构决议 | C01 平台 Key 分发方案 — GitHub 远程拉取 + 环境变量后门

**问题演进**：
1. V2.04：GLM Key 硬编码在 `orchestrator.py`。任何人读代码即可盗刷。
2. V2.05 初版修复：改为 `VSUB_PLATFORM_KEYS` 环境变量。但桌面用户不可能自己设环境变量——UX 灾难。
3. 三工程师审查：工程师A 发现 bootstrap 问题（`_REMOTE_BASE` 也是环境变量）。工程师B 提出 GitHub 免费私有仓库作远程配置中心。

**最终方案**：
- `_REMOTE_BASE` 默认值写死为 GitHub raw URL（URL 不保密，仓库权限控访问）
- 应用启动时后台线程拉取 `platform_keys.json` → 缓存到内存 + 72h 本地文件兜底
- 环境变量 `VSUB_PLATFORM_KEYS` 保留——仅开发调试用，优先级高于远程拉取
- 部署只需在 GitHub 私有仓库创建 `platform_keys.json`

**涉及文件**：`main.py`（`_REMOTE_BASE` 默认值 + `_fetch_platform_keys()` + `get_platform_keys()`）、`orchestrator.py`（`_get_platform_keys()` 替代静态常量 + 补 `import os as _os, json as _json`）

**安全边界（V2）**：`platform_keys.json` 在 GitHub 私有仓库中明文存储，安全依赖仓库权限。V2 用户基数=1，可接受。V3 加公钥加密层。

**已修** ✅（V2.05）

---

## P084 | 2026-05-22 | 架构决议 | C04 激活码系统最终设计 — 远程拉取 + 机器指纹绑定 + 环境变量后门

**问题演进**：
1. V2.04：三个激活码 `VSUB-DEV-001/002/003` 硬编码。任何人读代码即可进入开发者模式。
2. V2.05 初版修复：改为 `VSUB_DEV_CODES` 环境变量。但开发者不应手动设环境变量才能激活。
3. 三工程师审查：保留原有的三层系统（远程拉取 + 24h 缓存 + 机器指纹绑定），仅把内置码从硬编码改为环境变量注入。

**最终方案**：
- 激活码来源：GitHub `activation_codes.json`（生产）+ `VSUB_DEV_CODES` 环境变量（开发后门）
- 验证流程：用户输入码 → 查找（远程缓存 + 环境变量码）→ 命中 → 机器指纹绑定 → 写入 `activation.json`
- 日常恢复：启动 → 读取 `activation.json` → 指纹匹配 → 自动进入开发者模式（离线可用）
- 引导码通过 Slack/微信等带外渠道分发，不在源码中

**涉及文件**：`main.py`（`_LOCAL_CODES` 从环境变量读、`_all_valid_codes()` 合并逻辑不变、`_fetch_remote_codes()` 不变、`/api/dev/activate` 不变）

**与旧版的差异**：仅删除了 `VSUB-DEV-001/002/003` 三个写死的码。其余逻辑完全不变。

**已修** ✅（V2.05）

---

## P085 | 2026-05-22 | 架构决议 | `_REMOTE_BASE` 入口 URL Bootstrap 问题

**问题**：远程拉取系统依赖 `_REMOTE_BASE` 指向 GitHub，但原默认值是空字符串——这本身也是环境变量。如果用户不设，整个远程拉取链路全部失效。工程师A 指出这是「鸡生蛋蛋生鸡」的 bootstrap 问题。

**解决方案**：给 `_REMOTE_BASE` 一个生产默认值（GitHub raw URL），环境变量保留作为开发覆盖。

```python
_REMOTE_BASE = _os.environ.get(
    "VSUB_REMOTE_CONFIG_URL",
    "https://raw.githubusercontent.com/YOUR_USERNAME/video-subber-config/main",
)
```

**原则**：URL 不是秘密。仓库权限才是门禁。

**已修** ✅（V2.05）
---

## P086 | 2026-05-22 | 🔴 main.py:338 | R01 — `/api/dev/activate` 缺少路由装饰器

**问题**：前端激活弹窗调 `POST /api/dev/activate` 得到 404，开发者模式无法通过 UI 激活。

**原因**：`dev_activate` 函数定义存在，但漏写了 `@app.post("/api/dev/activate")` 装饰器。疑为之前合并 startup 函数时误删。

**解决方案**：在函数定义上方补上 `@app.post("/api/dev/activate")` 装饰器。

**已修** ✅（V2.06）

---

## P087 | 2026-05-22 | 🔴 main.py:483 | R02 — 上传视频整个读入内存，OOM 风险

**问题**：`content = await file.read()` 将整个视频文件一次性读入 Python 进程内存。4GB 视频 → 4GB+ RAM → OOM Killer 杀进程。

**原因**：为计算 SHA256 去重，先把整个文件读进内存再写盘。SHA256 可流式计算，不需要全量加载。

**解决方案**：先创建 `job_dir`，8MB 分块流式写入最终路径 `video_path`，同时流式计算 SHA256。5GB 安全上限，超限直接 `rmtree` + 413 响应。前端同步加「最大 5GB」提示和 413 错误展示。

**已修** ✅（V2.06）

---

## P088 | 2026-05-22 | 🟡 main.py:167 | R03 — 远程激活码永久污染本地集合

**问题**：`_fetch_remote_codes()` 中 `_LOCAL_CODES.update(remote_codes)` 将远程码永久写进本地 set。从 GitHub 吊销的码永不失效，24h TTL 形同虚设。

**原因**：远程码已通过 `_ACTIVATION_CACHE`（带 TTL）持久化，`_all_valid_codes()` 会正确读取它。额外写入 `_LOCAL_CODES` 是多余且有害的操作。

**解决方案**：删除 `_LOCAL_CODES.update(remote_codes)` 一行。

**已修** ✅（V2.06）

---

## P089 | 2026-05-22 | 🟡 orchestrator.py:368-375 | R04 — `fallback_backends` 死代码

**问题**：`fallback_backends` 永远为空列表 `[]`，第 368-375 行的 if 条件永远为 False，8 行死代码。

**原因**：早期设计的 fallback 机制后来被 `all_backends = ordered_backends` 替代，旧逻辑未清理。

**解决方案**：删除死代码块。

**已修** ✅（V2.06）

---

## P090 | 2026-05-22 | 🟡 transcriber_v2.py:273 | R05 — WhisperModel 硬编码 CPU

**问题**：`device="cpu"` 强制用 CPU 推理，Apple Silicon GPU/ANE 闲置。速度慢 2-3 倍。

**原因**：可能是早期调试时的临时设置，提交时未改回。

**解决方案**：改为 `device="auto", compute_type="auto"`。

**已修** ✅（V2.06）

---

## P091 | 2026-05-22 | 🟡 index.html:750 | R06 — 上传失败后按钮文案硬编码

**问题**：catch 块中 `sbtn.textContent='开始翻译'` 未调用统一的 `refreshStartBtn()`，选了"仅生成 SRT"的用户看到错误文案。

**原因**：`refreshStartBtn()` 是后加的抽象，catch 块是重构遗漏。

**解决方案**：改为 `refreshStartBtn()` + 413 错误 toast 提示 + `!r.ok` 提前拦截。

**已修** ✅（V2.06）

---

## P092 | 2026-05-22 | 🟢 main.py:109 | R07 — `_REMOTE_BASE` 占位符 URL

**问题**：默认值 `YOUR_USERNAME/video-subber-config` 是占位符。未配环境变量时每次启动 3 个后台线程去解析不存在的域名，共 15 秒卡顿。

**原因**：开发时写了占位符，注释说发布前改，未改。

**解决方案**：默认值改为空字符串 `""`。`_fetch_remote_json` 已有空值守卫。

**已修** ✅（V2.06）

---

## P093 | 2026-05-22 | 🔴 架构 | R08 — LLM 后端架构重构

**问题**：6 个 LLM 后端各自重复实现 `translate_batch`（~100行/个）。`translate_raw` 只 GLM 有，其他 5 个缺失→上下文感知翻译不可用。加新平台需再复制且易漏。前端无自定义平台入口。

**原因**：`TranslationBackend` 只定义接口签名，LLM 间的共享逻辑未提取中间层。

**解决方案**：
- `base.py`：新增 `LLMBackend(TranslationBackend)` 基类，统一实现 `translate_raw` / `translate_batch`
- 6 个后端全部重构为 `LLMBackend` 子类（各 ~35 行）：Claude 重写 `_build_body`（无 temperature），Gemini 重写 `_call_api`（URL 含 Key）
- 新建 `custom.py`：`CustomOpenAIBackend`，用户前端填入 URL+模型名即可接入任意 OpenAI 兼容平台
- `orchestrator.py`：`_build_llm_backends` 加 custom 分支
- 前端同步：加自定义平台下拉+端点/模型输入框+切换逻辑+字段收集+显示格式

**已修** ✅（V2.06）

---

## P094 | 2026-05-22 | 🟢 index.html:654,774 | R09 — `jobId` 死变量 + `unlockUI` 冗余判断

**问题**：`jobId` 从未被赋值（永远 null），`unlockUI` 中 `if(!jobId)` 永远为 true。多任务架构已用 `activeJobs` 管理。

**原因**：单任务→多任务迁移时，旧变量未清理。

**解决方案**：删除 `let jobId=null` 和 `unlockUI` 中的 `if(!jobId)` 判断。`checkAllDone()` 已正确管理按钮状态。

**已修** ✅（V2.06）

---

## P095 | 2026-05-23 | 🔴 架构 | 本地路径直读模式——解决大文件上传困境

**问题**：用户有 4K/8K 高清视频（10GB-50GB+），既不想压缩画质又想加字幕。旧方案 `file.read()` 全量加载→OOM，流式上传虽解决内存问题但对超大文件仍需等待上传完成。本地 Web 应用的前后端在同一台机器上，HTTP 上传是多余的——文件本来就在磁盘上。

**原因**：设计沿用了"远程 Web 应用"的思维模式——前端必须把文件通过 HTTP 发给后端。但对于本地应用，Python 可以直接用 `open()` 读取任意本地路径的文件。

**解决方案**：
- 前端：上传区下方加「本地文件路径」输入框。用户粘贴 `/Users/xxx/video.mp4` 或 `D:\Movies\video.mp4`。路径模式下前端发送 JSON（含 `local_path`）而非 FormData，0 秒"上传"。
- 后端 `create_job`：支持两种 Content-Type——`application/json`（本地路径模式）和 `multipart/form-data`（文件上传模式）。本地路径模式：`os.path.isfile()` 验证→8MB 分块流式读本地文件→同时算 SHA256+写入 job_dir。后续去重/任务创建逻辑两种模式共享。
- 两种模式并存：拖拽/点击 = 普适场景；粘贴路径 = 大文件逃生通道。
- 移除硬 5GB 限制：`MAX_UPLOAD_BYTES` 删除。上传提示从「最大 5GB」改为「建议不超过 5GB」。流式读写已保内存安全，无需硬阻断。

**已修** ✅（V2.06）

## P096 | 2026-05-23 | 🔴 前后端一致性审查 | V2.07 — 16 项修复批次

**问题**：前后端全面审查发现 16 项问题，涵盖功能缺失、数据不同步、UX 认知断裂。

**修复清单**：

1. **模型列表不同步**：前端 `onPlatformChange()` 使用硬编码 `PLATFORM_MODELS`，后端 `/api/backends` 动态下发的模型列表被忽略。→ 删除硬编码，改用 `State._llmModels`。
2. **下载接口无磁盘回退**：后端重启后 `JOBS` 内存空，`download_video`/`download_srt` 返回 404。→ 添加 `job_dir.glob()` 磁盘回退。
3. **`token_usage` 消息被丢弃**：Worker 发送 `token_usage` 类型，`_tracking_send` 不处理。→ 新增 handler。
4. **resemblyzer 环境未展示**：后端返回状态，前端不渲染。→ 添加第四个环境指示点。
5. **版本号三处不一致**：统一为 "2.07"。
6. **`outlineWidth` 无前端控件**：→ 添加 1-4px 选择器。
7. **Tier 描述与实际不符**：→ 修正注释为 "Google + Baidu"。
8. **样式"确认"按钮认知断裂**：→ 改为即时生效。
9. **缺 libass 仍可选烧录**：→ 无 libass 时自动切 SRT 并禁用。
10. **取消任务无二次确认**：→ 添加 `confirm()` 对话框。
11. **配置加载后 UI 不刷新**：→ 添加 `syncUI()` 调用。
12. **开发者入口不可达**：→ 始终显示齿轮按钮。
13. **大文件上传无进度**：→ 改用 `XMLHttpRequest` 显示上传百分比。
14. **`forceRerun` 残留**：→ 选新文件时重置。
15. **"有字幕"开关缺说明**：→ 添加说明文字。
16. **`contextAware` 生产模式下无意义**：→ 无 LLM 时禁用按钮。

**涉及文件**：`main.py`、`index.html`（v2.07 → 同步到 v2/）
**已修** ✅（V2.07）


## P097 | 2026-05-24 | 🔴 跨平台兼容性审查 | Windows 用户适配 — 7 项修复批次

**问题**：开发环境为 macOS，目标用户为 Windows，发现 7 项跨平台兼容问题。

**修复清单**：

1. **W01 🔴 subprocess 中文 UTF-8 编码崩溃**：`worker_manager.py` `subprocess.Popen(text=True)` 未指定 encoding，英文 Windows（cp1252）读中文 JSON 抛 `UnicodeDecodeError`。→ 添加 `encoding="utf-8"` + `env={"PYTHONIOENCODING":"utf-8"}`。
2. **W02 🔴 默认字体 "PingFang SC" Windows 不存在**：`config_manager.py` 硬编码 macOS 字体，Windows 无此字体导致中文字幕可能乱码或方块字。→ `sys.platform == "win32"` 时改用 `"Microsoft YaHei"`。
3. **W03 🔴 Windows 路径反斜杠破坏 ffmpeg ASS 滤镜**：V1.3 `subtitler.py`（锁定）构造 `-vf ass=C:\...\file.ass`，冒号被 ffmpeg 解析为参数分隔符，烧录失败。→ 在 V2 `worker.py` 预处理：`replace("\\","/").replace(":","\:")` 后再传入。
4. **W04 🟡 FFmpeg 检测不覆盖 Windows 常见安装路径**：`env_check.py` 搜索路径全为 macOS Homebrew 路径，Windows 未加 PATH 时显示"未安装"。→ 添加 `C:\ffmpeg\bin`、`C:\Program Files\ffmpeg\bin` 等 Windows 路径，`subprocess.run` 加 `encoding="utf-8", errors="replace"`。
5. **W05 🟡 Worker stderr 中文打印在英文 Windows 终端崩溃**：`worker_manager.py` `print()` 在 cp1252 终端输出中文抛 `UnicodeEncodeError`。→ 加 try/except，降级为 `[encoding error]` 提示。
6. **W06 🟢 启动时删除旧日志未处理 Windows 文件锁**：`main.py` `old_log.unlink()` 在文件被锁时抛 `PermissionError`。→ 包裹 try/except OSError。
7. **W07 🟢 模拟模式路径写死 macOS 路径**：`main.py` dev simulate 返回 `/usr/local/bin/ffmpeg`。→ `sys.platform == "win32"` 时返回 Windows 路径。

**涉及文件**：`worker_manager.py`、`config_manager.py`、`worker.py`、`env_check.py`、`main.py`（v2.07 → 同步到 v2/）
**已修** ✅（V2.07）


## P098 | 2026-05-24 | 🔴 全面QA审查 | V2.08 — 5项修复批次（乔布斯+专业QA双审查）

**问题**：经乔布斯视角产品审查 + 专业QA代码审查，发现5个问题。

**修复清单**：

1. **🔴 本地路径模式复制视频文件占三倍空间**
   - 问题：用户输入本地路径后，后端把整个视频复制到 jobs 目录（5GB→15GB），严重浪费磁盘
   - 原因：SHA256 计算和文件存储写在同一个 with 循环里
   - 方案：改为流式只读计算 hash（不写副本），worker 直接引用原始文件路径
   - 文件：`main.py` create_job 本地路径分支
   
2. **🔴 Worker 进程 crash 后任务永远卡在 running**
   - 问题：OOM 等原因导致子进程崩溃时，主进程不发送 error 消息，前端进度条永远不动
   - 原因：`_read_stdout` 线程读完 stdout 后直接退出，不检查 returncode
   - 方案：stdout 读完后 `proc.wait()`，非零退出码时发送 error 消息建议换小模型
   - 文件：`worker_manager.py` _read_stdout 方法

3. **⚠️ 刷新页面后 error 状态任务消失**
   - 问题：任务报错后刷新页面，reconnectJobs 不处理 error 状态，任务静默移除
   - 原因：reconnectJobs 只处理 running/done/interrupted，缺少 error 分支
   - 方案：新增 error 状态处理，显示失败原因和错误信息
   - 文件：`index.html` reconnectJobs 函数

4. **⚠️ 多 job 恢复时全局设置被最后一个覆盖**
   - 问题：有多个不同设置的任务时，刷新页面后 UI 显示最后恢复任务的设置
   - 原因：每个 reconnect 的 job 都执行 `State.settings={...State.settings,...j.config}`
   - 方案：用 `firstReconnect` 标志，只让第一个恢复的 job 同步设置
   - 文件：`index.html` reconnectJobs 函数

5. **💡 进度时间估计对非 large-v3 模型不准确**
   - 问题：选 tiny 模型也显示"约需 20-40 分钟"
   - 原因：时间估计硬编码为 large-v3 的数值
   - 方案：根据实际模型选择动态显示对应时间范围 + 模型加载消息改为中性表述
   - 文件：`worker.py` transcribe 阶段进度消息

**涉及文件**：`main.py`、`worker_manager.py`、`worker.py`、`index.html`
**已修** ✅（V2.08）

---

## P099 | 2026-05-24 | 🔴 V2.08迁移回退 | 字幕生成丢失词级对齐 + [?]标记清除 + 开发者弹窗失效

**问题**：V2.07→V2.08 架构独立化迁移时，`worker.py` 丢失了三段关键代码，导致：
1. 中文字幕没有按英文词时间戳切块（`_word_align_segments` 函数整体丢失）
2. 每条字幕后带 `[ ? ]` 标记（`s.uncertain = False` 清除逻辑丢失）
3. 翻译批量分隔符 `|||` 泄漏到字幕文本中
4. 开发者齿轮按钮点击无弹窗（`devLoginOverlay` 内联 `style="display:none"` 优先级高于 CSS `.active{display:flex}`）

**原因**：
- V2.08 从 V2.07 复制 `worker.py` 时，只保留了核心导入迁移（V1.3→backend.core），但遗漏了 V2.07 后期新增的 `_split_zh_simple()`、`_word_align_segments()` 函数和 Step 4 前的 `uncertain=False` 清除逻辑
- 前端 `devLoginOverlay` div 同时有 CSS class 规则（`.dev-login-overlay{display:none}`）和 HTML 内联 `style="display:none"`，内联样式优先级高于任何 CSS 选择器，`openDevLogin()` 添加 `.active` class 无法覆盖

**解决方案**：
1. `worker.py`：恢复 `import re as _re`、`_ZH_SENT_RE`、`_split_zh_simple()`、`_word_align_segments()` 四段代码
2. `worker.py`：Step 4 前添加 `for s in segments: s.uncertain = False` + `subtitle_segments = _word_align_segments(segments)`
3. `worker.py`：`build_srt`/`build_ass`/`build_ass_styled` 改用 `subtitle_segments` 参数
4. `index.html`：移除 `devLoginOverlay` 上的 `style="display:none"`，让 CSS `.dev-login-overlay{display:none}` 控制初始隐藏

**涉及文件**：`worker.py`、`index.html`
**已修** ✅（V2.08）

---

## P100 | 2026-05-26 | 🔴 google.py | Google 翻译 `|||` 分隔符空格被吃掉（P016 回归）

**问题**：Google Translate API 翻译后将 ` ||| `（两侧有空格）改为 ` |||`（右侧无空格）。`split(" ||| ")` 匹配失败，返回 1 段结果而非 N 段。代码判定 "split count doesn't match" 直接返回全部 `[翻译失败]`。

**根因**：P016（V1.1）已记录 `|||` 分隔符在翻译引擎中不可靠，但 P069（V2.05）实现 Google 批量时仍使用 `|||`，且 split 用精确匹配而非 regex 容错。**已知风险被遗忘导致回归。**

**解决方案**：精确 split 失败时，用 `re.split(r'\s*\|\|\|\s*', text)` 重试。
```python
parts = [p.strip() for p in translated_full.split(SEPARATOR)]
if len(parts) != len(non_empty):
    parts = [p.strip() for p in re.split(r'\s*\|\|\|\s*', translated_full)]
```

**涉及文件**：`v2/backend/translation/google.py`
**已修** ✅

---

## P101 | 2026-05-26 | 🔴 main.py | `_config_hash()` 缺 style 和 savePath（R03 后端遗漏）

**问题**：审查报告 R03 指出 `_settingsMatchDone()` 缺少 style/savePath 比较，前端已修复。但后端 `_config_hash()` 同样遗漏这两个字段，导致同视频不同样式被去重为同一任务，用户改样式后重跑返回旧结果。

**根因**：R03 修复只改了前端，没有同步修后端的 `_config_hash()`。前后端对"相同配置"的定义不一致。

**解决方案**：`_config_hash()` 的 `relevant` 字典加入 `style` 和 `savePath`。

**涉及文件**：`v2/backend/main.py:39-49`
**已修** ✅

---

## P102 | 2026-05-26 | 🟡 百度翻译 | 敏感词过滤 error_code 20003

**问题**：百度翻译 API 对包含政治敏感内容的文本（如 "President Xi" + "China"）返回 `error_code: 20003, error_msg: Hit sensitive word`，拒绝翻译。

**根因**：百度 API 内容审核策略，非代码 bug。

**影响**：当 Google 同时因分隔符问题失败时，两个免费后端同时失败 → 0% 翻译成功 → 触发质量门控 → 任务报错。P100 修复 Google 后，Google 可兜底百度的敏感词失败。

**解决方案**：无需修代码。P100 修复后 Google 已能正确兜底。可考虑在日志中提示用户"百度因内容审核拒绝翻译"而非通用的"翻译失败"。

**已知** ✅（无需代码修复）

---

## P103 | 2026-05-27 | 🔴 说话人检测 | 假阳性横杠（同一人语气变化被误判为换人）

**问题**：resemblyzer 声纹比较在同一说话人语气变化（平静→激动、停顿后继续）时产生假阳性，在字幕前错误添加 `- ` 横杠前缀。短视频尤其明显（样本太少，Otsu 阈值不可靠）。

**根因**：自适应阈值算法在数据不足时不稳定；未设置对不合理结果的安全检查。

**解决方案**：B+C 组合方案：
1. Guard B：段数 < 5 时直接跳过说话人检测（数据不足不做推断）
2. Guard C（V2，替换旧版固定0.10门槛）：两道独立检查，任一未通过即取消全部横杠：
   - **间隙检测（相对检查）**：将所有相似度分数排序后计算相邻间距，最大间距 / 中间间距 < 3倍 → 无裂缝 → 判定单人语气波动
   - **安全钳（绝对检查）**：Otsu 切分后两组平均分差距 < 0.15 → 两组距离太近 → 判定单人语气波动
   - 设计理由：间隙检测用相对倍数（不随视频分数范围变化），安全钳用绝对距离兜底（防止间距比例大但实际距离小的假阳性）。两道检查从不同角度验证，互不重复

**涉及文件**：`v2/backend/transcriber_v2.py:_detect_speaker_changes()`
**已修** ✅

---

## P104 | 2026-05-27 | 🔴 前端 | "任务可能已中断"误报（false positive stall warning）

**问题**：前端每 2 秒轮询后端 `GET /api/jobs/{id}`，如果 `last_progress` 时间戳超过 60 秒未更新，就显示黄色警告"⚠️ 任务可能已中断，建议取消后重试"。但 Whisper large-v3 模型处理长视频时，进度回调每 10 段才触发一次，间隔可达 7 分钟。结果后台正常工作时前端反复误报，用户被吓到后取消任务，白白浪费已完成的进度。

**根因**：前端用"距离上次进度消息的时间"来猜测进程状态，而不是直接查询进程是否存活。60 秒的超时阈值对 large-v3 + 长视频场景远远不够。

**解决方案**：
1. `worker_manager.py` 新增 `is_alive(job_id)` 方法，通过 `subprocess.Popen.poll()` 直接向操作系统查询进程是否存活
2. `main.py` 的 `GET /api/jobs/{id}` 接口返回新字段 `process_alive: bool`（内存路径和磁盘 fallback 路径均已覆盖）
3. `index.html` 前端轮询逻辑：用 `j.process_alive === false` 替换 `(Date.now()/1000 - j.last_progress) > 60` 的猜测逻辑

**涉及文件**：`v2/backend/worker_manager.py`、`v2/backend/main.py:get_job()`、`v2/frontend/index.html:connectCard()`
**已修** ✅

---

## P105 | 2026-05-27 | 🔴 前端 | 进程崩溃时前端无确定性提醒（missing crash notification）

**问题**：当 worker 进程被系统杀死（OOM kill、segfault）时，不会发出任何 JSON 消息。旧逻辑中前端仅在 60 秒超时后显示"可能已中断"——措辞含糊且不可靠。用户无法区分"正在慢速处理"和"确实已崩溃"。

**根因**：前端没有查询进程实际存活状态的能力。后端 `worker_manager._read_stdout()` 虽然能在进程退出后检测 `proc.returncode != 0` 并广播 error 事件，但前端的 60 秒猜测逻辑在此之前就已开火，产生误导。

**解决方案**：与 P104 同一组修复。前端现在基于 `process_alive` 字段判断：
- `process_alive === true` → 不管等多久都显示"处理中…"（消灭误报）
- `process_alive === false` 且 status 仍为 running → 红色提示"处理异常中断，请取消后重试"（确定性崩溃通知）

**涉及文件**：同 P104
**已修** ✅

## P127 | 2026-06-02 | 翻译引擎 | 腾讯交互翻译 TranSmart API 需要登录
**问题**：逆向 `yi.qq.com/api/imt` 和 `transmart.qq.com/api/imt` 均返回 `Session-Out` / SSL EOF。Bob 插件（2024 年版）使用的 `yi.qq.com` 域名已出现 SSL 证书过期问题（V2EX 2025-04 有报告）。`transmart.qq.com` 返回 `{"ret_code":"Session-Out","message":"Session was expired"}`
**原因**：腾讯在 2025 年后给 TranSmart API 加了登录认证，免费无 Key 调用不再可行
**解决方案**：放弃 TranSmart，改用有道 `aidemo.youdao.com/trans`（同样国内可访问、免费、无需 Key）
**已修** ✅

**已修** ✅

## P129 | 2026-06-03 | 翻译 | LLM 术语提取正则 PatternError 导致所有 LLM 假死

**问题**：12 次测试所有 LLM（Gemini/自定义/DeepSeek）翻译全部失败，降级到免费引擎。日志只显示"异常，尝试下一个"。

**原因**：`extract_terminology()` 第 214 行正则 `(.+?)\s*[→->]+\s*(.+)` 中，`[→->]` 字符类的 `→` (U+2192) 到 `>` (U+003E) 被 Python re 当作无效字符范围，抛出 `PatternError: bad character range →-> at position 9`。该异常发生在 try/except 块之外（正则代码未被包裹），直接传播到外层主循环。所有 LLM 在术语提取步骤死于同一正则 bug。

**排查过程**：① 发现 prompt 与解析器格式矛盾 ② 发现日志盲区，补全三步日志 ③ 补全异常详情记录 ④ 补全 translate_raw 成功路径日志 ⑤ 第 12 次测试日志首次暴露 `PatternError`

**解决方案**：改 `[→->]+` 为 `[-→>]+`（`-` 放在字符类开头，作为文本字符而非范围运算符）

**已修** ✅

## P128 | 2026-06-02 | 翻译引擎 | Bing"免费"翻译实为逆向 Edge 浏览器
**问题**：上一轮分析中错误推荐"加 Bing 作为 Phase 1A 第一优先级免费引擎"。用户指出正规 Bing API（Azure Translator）需要注册+付费
**原因**：pyvideotrans 等竞品的"免费微软翻译"是逆向 Edge 浏览器内置翻译（`edge.microsoft.com/translate/auth` → `api-edge.cognitive.microsofttranslator.com`），不是官方 API。与之前砍掉剪映/必剪逆向 API 的理由相同
**解决方案**：① 承认所有竞品的"免费翻译"都是逆向接口（包括我们已有的 Google）② 实现 Bing 逆向引擎（`bing.py`）+ 有道逆向引擎（`youdao.py`）③ 定性为"尽力而为"降级方案，不作为核心保证 ④ 做好多引擎降级链：Google → Bing → 有道 → 百度 BYOK
**已修** ✅

---

## P130 | 2026-06-03 | 版本号 | main.py 硬编码版本号导致前端永远显示旧版本
**问题**：V3.01 迭代完成后，前端版本号仍显示 "v3.0"。用户以为版本号会自动跟着迭代更新，实际不会
**原因**：`main.py` startup 里硬编码 `"3.0"` 写到 `~/.video-subber/.version` 文件，`_detect_version()` 优先读这个文件就返回了，永远不会走到后面的 pyproject.toml 读取。版本号分散在三处（main.py 硬编码、.version 文件、pyproject.toml），互相不同步
**解决方案**：① `_detect_version()` 从四级 fallback（.version→git tag→目录扫描→pyproject.toml）简化为只读 `pyproject.toml`（唯一版本号来源）② startup 写 .version 改为写检测结果而非硬编码值 ③ 每次迭代时 Claude 自动更新 pyproject.toml 版本号作为流程的一部分
**已修** ✅

---

## P131 | 2026-06-03 | 快照 | v2.10/v2.11 快照包含 .venv 占 1.85GB 磁盘
**问题**：v2.10 和 v2.11 快照各 926MB，因为创建快照时把 `.venv/`（Python 虚拟环境，含 PyTorch 237MB、ONNX 27MB 等第三方库）也复制进去了
**原因**：早期快照用 `cp -r` 全量复制，没有排除 .venv。v2.12 起改用 rsync 排除，本问题不再复发
**解决方案**：删除 `v2/v2.10/.venv/` 和 `v2/v2.11/.venv/`，释放 1.85GB。.venv 不是源代码，可随时用 `uv sync` 重建
**已修** ✅

---

## P132 | 2026-06-03 | 目录结构 | V3 代码放在 v2/ 文件夹内，目录命名混乱
**问题**：V3 的活跃代码在 `v2/` 目录下开发，V3.01 快照也存放在 `v2/v3.01/`。用户指出 V2 和 V3 应该是同级文件夹，当前结构不直观
**原因**：V2→V3 升级时没有新建 `v3/` 目录，直接在 `v2/` 里继续开发并改版本号。`v2/` 从"V2 版本代码"变成了"活跃代码目录"，但文件夹名仍叫 v2，造成语义混乱
**解决方案**：**推迟到 V3 Phase 2（PySide6 桌面应用重写前端）时一并整理**。届时需要：① 将 `v2/` 重命名或迁移为 `v3/` ② 将 v2.XX 快照移到与 v3/ 同级 ③ 更新 CLAUDE.md 和所有路径引用。当前阶段（Phase 1B 多模型转录）不动目录结构，避免引入不必要的变更风险
**⏸ 未修**（计划推迟到 Phase 2）

---

## P133 | 2026-06-04 | 模型下载 | HuggingFace 国内被墙，无镜像配置

**问题**：Whisper 五个型号和 Qwen3-ASR、Qwen3-ForcedAligner 都从 HuggingFace（huggingface.co）下载。HuggingFace 在国内被墙，用户直接下载会失败或极慢。

**原因**：代码里 `snapshot_download("Systran/faster-whisper-*")` 和 `Qwen3ASR.from_pretrained(...)` 全部直连 HuggingFace，没有镜像配置。

**解决方案**：设置 HuggingFace 镜像环境变量：
```
HF_ENDPOINT=https://hf-mirror.com
```
hf-mirror.com 是 HuggingFace 国内镜像站，设置后所有下载请求自动走镜像，无需代理。做模型下载按钮时一并处理。

**⏸ 未修**

---

## P134 | 2026-06-04 | 模型下载 | 千问两个模型 + SenseVoice 无下载按钮

**问题**：目前只有 Whisper 五个型号有下载按钮（`/api/models/download/{model_id}` + 前端圆点 UI）。Qwen3-ASR（转录交叉验证 3.5GB）、Qwen3-ForcedAligner（词级时间戳对齐 979MB）、SenseVoice（声音分类）都是第一次使用时自动静默下载。用户界面卡住不动，不知道在下载、看不到进度、不知道要等多久。

**原因**：三模型接入时没有同步做下载按钮和进度 UI。`from_pretrained` 和 `funasr.AutoModel` 自带自动下载，但交互体验差。

**解决方案**：
- Qwen3-ASR：模型名 `Qwen/Qwen3-ASR-1.7B`（PyTorch）/ `mlx-community/Qwen3-ASR-1.7B-bf16`（MLX），走 HuggingFace，下载方式与 Whisper 一致
- Qwen3-ForcedAligner：模型名待定（V3.04 实现时确定），同样走 HuggingFace
- SenseVoice：走 ModelScope（modelscope.cn）国内可直连，下载方式与 HuggingFace 不同（`funasr.AutoModel` 自动管理）
- 三种模型都加到 `/api/models/status` 返回列表 + 前端圆点下载 UI
- Whisper 的进度条是模拟的，新模型下载建议也沿用模拟进度——能告诉用户"在动"就行

**⏸ 未修**（待做模型下载功能时统一处理）

---

## P135 | 2026-06-04 | 打包 | Resemblyzer 说话人识别模型需内置

**问题**：Resemblyzer 模型很小（几十 MB），但每次 `VoiceEncoder()` 首次调用时也会走网络自动下载。应该直接打入安装包。

**原因**：Resemblyzer 不是用户可选的功能——系统自动用它检测说话人切换。它不需要用户手动下载。

**解决方案**：打包 EXE/DMG 时把 resemblyzer 的模型文件预置到安装包内。用户安装即用，不触发网络下载。

**⏸ 未修**（V4 打包阶段处理）

---

## P136 | 2026-06-04 | Phase 1D | Qwen3-FA 对齐结果全丢 — 读了不存在的字段

**问题**：`apply_aligner_timestamps()` 第 249 行读 `seg.zh`（中文翻译），但该函数在翻译**之前**被调用，此时 segment 只有 `seg.text`（原文）。每个 segment 的 `zh` 都是空字符串，全部被 `if txt_len == 0: continue` 跳过。精确时间戳全丢了——Qwen3-FA 模型白跑了。

**原因**：设计文档最初把 Qwen3-FA 放在翻译之后，Steve Jobs 审查后改为转录之后。但 `apply_aligner_timestamps` 代码没有跟着改——它仍然假设翻译已跑完、`seg.zh` 已填好。

**解决方案**：`seg.zh` → `seg.text`（三处：变量名、长度计算、分词）。对齐器对原文文本做对齐，所以分配器也应该读原文文本。

**已修** ✅ V3.04

---

## P137 | 2026-06-04 | Phase 1D | [?] 标记全灭（第1刀）— worker 无条件重置 uncertain

**问题**：`worker.py:456-457` 有一行 `for s in segments: s.uncertain = False`，在字幕生成前把所有 segment 的不确定标记全部重置。幻觉过滤辛苦标记的 `seg.uncertain = True` 被全部消灭。`qm_count` 永远是 0，`[?]` 永远不会出现在字幕里，前端阈值警告永远不会触发。

**原因**：这行代码是 V3 早期的遗留物——当时还没有双信号过滤和 `[?]` 标记功能。它本来是保证"翻译后的 segment 状态干净"，但在 Phase 1D 引入 `uncertain` 标记后变成了杀手。

**解决方案**：删除这行循环。`uncertain` 标记从幻觉过滤产出、到字幕生成、到 `qm_count` 统计，全程保留。

**已修** ✅ V3.04

---

## P138 | 2026-06-04 | Phase 1D | [?] 标记全灭（第2刀）— sentence_splitter 硬编码 False

**问题**：`sentence_splitter.py` 三处创建新 Segment 时硬编码 `sub.uncertain = False`（第 83、185、274 行）。拆句后所有子 segment 的标记全丢。

**原因**：`Segment.__init__` 没有 `uncertain` 字段——它是 `hallucination_filter` 动态加上去的。创建新 Segment 时自然没有这个属性，代码随手补了 `False`。

**解决方案**：三处改为 `sub.uncertain = getattr(seg, 'uncertain', False)`——从父 segment 继承标记。

**已修** ✅ V3.04

---

## P139 | 2026-06-04 | Phase 1D | 空对齐列表触发假阳性洪水

**问题**：`_layer1_dual_signal` 的门控条件是 `if aligner_result is not None:`。空列表 `[]` 不是 `None`，所以进入了双信号路径。空列表里没有任何词，`has_alignment` 对所有 segment 返回 False。所有"人声"segment 都被标记 `uncertain = True`。好视频被标红"音频质量差"。

**原因**：Python 中 `[] is not None` 为 True。空列表是有值的——只是里面没东西。门控逻辑只区分了"有没有传参数"，没区分"传了参数但数据为空"。

**解决方案**：门控改为 `if aligner_result is not None and len(aligner_result) > 0`。空列表走单信号降级路径。

**已修** ✅ V3.04

---

## P140 | 2026-06-04 | Phase 1D | GPU 泄漏 — aligner.close() 不在 finally

**问题**：`transcriber_v2.py` 中 `aligner.close()` 放在 `try` 块内部的 `if` 分支里。如果 `apply_aligner_timestamps()` 抛异常，`close()` 永远不会执行。Qwen3-FA 模型（~1GB）一直蹲在内存/显存里直到 worker 进程结束。连续处理多个视频 → 内存累积 → OOM。

**原因**：`close()` 写在正常流程的末尾，没有放在 `finally` 块里。异常路径绕过了它。

**解决方案**：`aligner` 变量提前声明为 `None`，`close()` 移到 `finally` 块。不管成功、失败、还是跳过，模型一定释放。

**已修** ✅ V3.04

---

## P141 | 2026-06-04 | Phase 1D | self._processor 未初始化导致降级路径崩溃

**问题**：`QwenAligner.__init__` 的 `except ImportError` 只设了 `self._model = None`，没设 `self._processor`。后续代码如果访问 `self._processor`（即使是 `hasattr` 检查），在没有 PyTorch 的电脑上会触发 `AttributeError`——降级路径本身崩了。

**原因**：漏写了一行。`except` 块应该把所有属性都设为安全默认值。

**解决方案**：加一行 `self._processor = None`。

**已修** ✅ V3.04

---

## P142 | 2026-06-04 | Phase 1D | L2 交叉验证未拦截空字符串 qwen_text

**问题**：`_layer2_cross_validate()` 只检查 `qwen_text is None`，不检查 `qwen_text == ""`。空字符串被喂给 `SequenceMatcher`，与正常文本比较返回相似度 0.0，误标 `[?]`。

**原因**：Python 中 `"" is not None`。保护条件 `is None` 不能同时拦截空字符串。

**解决方案**：`if not qwen_text: return`——同时拦截 None 和空字符串。

**已修** ✅ V3.04

---

## P143 | 2026-06-04 | Phase 1D | QwenAligner.close() 未释放 self._processor

**问题**：`close()` 释放了模型但没释放 processor（tokenizer/feature extractor）。多次对齐后累积内存。

**原因**：`except ImportError` 设了 `self._processor = None`，但 `close()` 没做同样清理。

**解决方案**：`close()` 中 `del self._processor`，用 `hasattr` 保护。

**已修** ✅ V3.04

---

## P144 | 2026-06-04 | Phase 1D | SenseVoice 标签在旧时间戳上判断，对齐后可能偏移

**问题**：SenseVoice 用 Whisper 原始时间戳做声音分类。之后 Qwen3-FA 修改了时间戳。L1 双信号在新时间戳上检查旧标签——边界偏移几十毫秒。

**原因**：Phase 1D 在 SenseVoice 之后插入了 Qwen3-FA，改变了时间戳。

**解决方案**：调换执行顺序——SenseVoice 移到 Qwen3-FA 之后，同时 Qwen3-FA 对齐置信度作为独立信号④喂给 L2。已合并进 P152+P144（V3.07 转录流水线重构）。

**归属**：V3.07（合并进 P152+P144 转录流水线重构）

**已修** ✅（2026-06-07 ARCH ⑦ 正式修复：FA 对齐先于 SV 贴标，_apply_sv_tags 拆为模块级函数在 Step 5 调用。347 测试全过）

---

## P145 | 2026-06-04 | Phase 1D | apply_aligner_timestamps docstring 错误

**问题**：docstring 写"Matches aligned words against each segment's Chinese text (seg.zh)"，但代码读的是 `seg.text`（Bug 1 修复后遗留）。

**原因**：Bug 1 改了代码但忘了改注释。

**解决方案**："Chinese text (seg.zh)" → "transcription text (seg.text)"。

**已修** ✅ V3.04

---

## P146 | 2026-06-04 | 🔴 服务器重启后任务恢复全链路缺陷（6 个子问题）

**发现场景**：处理俄语长视频时服务器意外重启。重新拖入视频恢复——前端进度条卡住，API 返回 progress=0。排查发现恢复链路存在 6 个独立但互相放大的缺陷。

### P146a：恢复时误删刚上传的视频
**问题**：`create_job` 检测到旧任务中断 → 删除刚创建的新临时目录（含刚上传的视频）→ 切换到旧目录 → 旧视频在取消时已被删除 → Worker 拿着不存在路径启动 → 立即崩溃。
**原因**：恢复逻辑假设旧目录一定有 `input.*`。取消时的清理与恢复逻辑互相矛盾。
**解决方案**：删临时目录前先检查旧目录有无视频。没有则把新视频搬过去。

### P146b：`is_alive()` 只查花名册不查操作系统
**问题**：`worker_manager.is_alive()` 只查 `self._processes` 字典。服务器重启后字典为空——旧 Worker 明明在跑，但返回 False。
**原因**：P104（V2.10）修了前端但后端实现只查自己启动过的进程。
**解决方案**：用 `psutil.pid_exists()` 问操作系统，不用自己的字典。

### P146c：恢复后 Worker 没在 JOBS 正确登记
**问题**：WebSocket 推前端正常（进度条动），但 API 轮询永远 progress=0。JOBS 更新路径偏差。
**解决方案**：确保 Worker 启动时 JOBS 登记完成，`_forward_to_ws` 加容错。

### P146d：进度消息不写日志
**问题**：进度回调走 stdout（随服务器丢失），日志只记关键节点。事后无法查看中间进度。
**解决方案**：`stderr_progress()` 同时写一份到日志文件。

### P146e：转录中途不存 checkpoint
**问题**：checkpoint 只在转录全部完成后保存。中途崩溃全丢，第二轮从头开始。
**解决方案**：每 50 段保存一次中间 checkpoint。

### P146f：长音频 chunk 5 分钟过保守
**问题**：`_CHUNK_MAX_DURATION_S = 300`。模型支持 20 分钟，应改为 15 分钟。2 小时视频从 24 chunk 降到 8 chunk。
**解决方案**：`300 → 900`。

**已修** ✅（V3.05 已修复。main.py:175-209 启动时扫描全部历史 job 重建 GLOBAL_HASH_INDEX；main.py:656-675 interrupted 任务自动重启 Worker；/api/active-jobs 端点就绪）

## P147 | 2026-06-05 | Phase 1D 转录 | 千问 ASR 重复坍缩/生成失控（13.5 分钟卡死）
**测试**：job 97c57255ec，24 分钟俄语成人视频，V3.05，tier=precise。
**问题**：千问 ASR 在视频前 2 分钟后陷入重复坍缩，从 120s 一路到结尾每段都吐同一串幻觉 `1056234897 НЕТ ТЫ МЕНЯ ПРОСТИ`。chunk5 单块卡 13.5 分钟。最终 198 段里 116 段（58%）qwen_text 是这串垃圾；整体仅 4% 千问输出可用（9/198 ok，41 lowsim，116 garbage，32 empty）。
**原因**：`qwen_asr_runner.py` 里 `model.transcribe(chunk_path)` **零参数调用**，全吃默认值：
  - `max_tokens` 不传 → 自动 = 时长×50 ≈ 15000/块，无熔断 → 卡死后狂吐到 15000 才停（13.5 分钟来源）
  - `repetition_penalty` = 1.2 默认，太弱，掰不开强重复环
  - `repetition_context_size` = 100，重复周期超窗口后惩罚"失忆"
  - `temperature` = 0.0 贪心，最易掉进确定性重复环
  - `language` 不传 → 每块独立重判语言（本次判对了 ru，但仍是稳定性隐患）
**解决方案（初版·错误）**：~~TDD `_qwen_gen_kwargs(duration_s, language, use_mlx)` —— `max_tokens=时长×10` 熔断（floor 256）+ `repetition_penalty 1.4` + `repetition_context_size 256` + `language` 透传。~~ ← 此方案矫枉过正，把千问从"乱说话"变成了"不说话"，直接导致 P156。

**🔴 教训**：一次改了三个参数（token 预算、惩罚力度、惩罚窗口），无法隔离哪个参数解决了坍缩、哪个参数造成了副作用。修 bug 应该一次只改一个参数，验证通过后再改下一个。

**解决方案（终版·2026-06-05 二次校准）**：对照竞品 WhisperJAV（`repetition_penalty=1.1` + `max_tokens_per_second=20`）发现 P147 在两个维度同时处于极端值。修正为：
  - `_QWEN_TOKENS_PER_SEC` 10 → **20**（给正常转录足够呼吸空间；5min×20=6000 token，坍缩时 6000 就熔断 vs 旧版 15000）
  - `_QWEN_REPETITION_PENALTY` 1.4 → **1.1**（对齐 WhisperJAV；温柔提醒不误伤俄语词尾屈折的重复音节）
  - `_QWEN_REPETITION_WINDOW` 256 → 保持（范围本身合理，问题在惩罚力度）
  - 不加 4096 硬上限（速率限制 20t/s 本身就是熔断器，再套一层会误伤正常内容）
详见 P156 终版分析。
**已修** ✅（2026-06-05，V3.05，二次校准后真·终版）

## P148 | 2026-06-05 | Phase 1D 过滤 | L2 交叉验证无脑信任千问 → 77% 字幕误盖 [?]
**问题**：最终 SRT 225 块里 175 块（77%）盖 `[?]`，但字幕文字内容（Whisper 的）完全正确，翻译 198/198 成功。问号全是误标。
**原因**：`hallucination_filter.py:168 _layer2_cross_validate` 拿 Whisper 文本和千问文本逐字符比相似度，<0.3 就标 uncertain → 加 [?]。三个缺陷：
  ① 用千问前没有任何"体检"，只判 `if not qwen_text`（是否为空），不判千问本身是否是坍缩垃圾；
  ② 分歧时单向甩锅——永远标"这段（Whisper）可疑"，从无"也许千问疯了别信它"的分支；
  ③ L4 已有重复坍缩检测（`_RE_REPEATED_PATTERN`）却只查 `seg.text`，从不查 `qwen_text`。
  根因：交叉验证假设"两个裁判都清醒"，没有"裁判坏了"的后路。千问崩 → L2 从幻觉过滤器变成问号喷射器。
**解决方案**：`hallucination_filter.py` 新增 `_qwen_is_reliable()`——非空 qwen_text 里同一串占比 >30% 即判千问坍缩，L2 整体跳过（`_layer2_cross_validate` 加 `qwen_reliable` 参数）。TDD：`test_phase1d_qwen_health.py`（RED→GREEN）。
**验证**：① 全套 233 测试通过；② 拿本任务真实 transcript.json 重放，`[?]` 从 159/198(80%) 降到 17/198(8%)，剩余 17 为合理的 L3 语速标记。整片端到端重跑留待 P147 一起做。
**已修** ✅（2026-06-05，V3.05）

## P149 | 2026-06-05 | Phase 1D 过滤 | L2 逐字符相似度对两 ASR 输出天然爱误判
**问题**：即便千问没崩，L2 也会过度盖问号。本次 41 段（20%）qwen_text 是真内容、没崩，只因和 Whisper 断句/用词/标点不同，相似度仍 <0.3 → 照样盖 [?]。
**原因**：两个 ASR 模型对同一句的切分天然不同，逐字符 `SequenceMatcher` 相似度本就偏低，正确句子也常 <0.3。0.3 阈值 + 逐字符比对的设计本身偏激进。
**解决方案**：V3.07 已将 L2 从逐字符 `SequenceMatcher` 比对替换为 30 秒窗口 `LightweightStemmer` 词干比对。不再逐句比对相似度，而是把两个模型在 30 秒内的所有文字聚合后提词根比对共享率。断句差异不再影响结果。
**✅ 已修**（V3.07 L2 重构为词干窗口比对）

## P150 | 2026-06-05 | 转录 | condition_on_previous_text 从 v1.1 的 False 回归成 v2/ 的 True
**问题**：`transcriber_v2.py:188` 现为 `condition_on_previous_text=True`。而 v1.1（P013）当年"外部建议采纳"明确改成 False，防止 Whisper 片头幻觉传播到后续段。v2/v3 重写时翻回了 True。
**原因**：True 是用户为换准确率主动改的（False 每段独立、丢上下文、准确率低）——属权衡，非纯 bug。但本片大量重复非语音段，正是 True 最易翻车的内容类型，而唯一兜底的 L2 又坏了。本次没咬到（Whisper 输出干净），属埋雷。
**解决方案**：用户确认——**保持 True，不动。** 主动改为 True 是为了换准确率（False 每段独立、丢上下文），这是清醒的权衡。P148 修好后 L2 现有健康检查兜底，安全网在线。（Steve Jobs 审查：它没坏，别动它。）
**已修** ✅（确认保持 True，无需改动）

## P151 | 2026-06-05 | 千问 | 招牌 context（上下文偏置）本地 MLX 包没实现
**问题**：想用千问 context（喂背景/人名/术语纠偏）提准确率，但本地 `qwen3_asr_mlx` 0.1.1 的 `transcribe()` 没这个参数，`build_prompt` 只能塞语言。
**原因**：context 是千问模型本身的能力，官方开源版（transformers/vllm）和云端 Flash 都支持，但你装的这个社区 MLX 小包没实现。
**解决方案**：用户决策——**延期到 V3 后期，Windows 系统实测。** Mac MLX 版缺 context 是平台限制，非 bug。Windows 用户用的官方 transformers 版理论上支持 context，届时实测验证。不需在 Mac 上换包。
**⏸ 未修**（延期到 V3 Windows 实测。2026-06-05 核查确认：第三方 MLX 包限制，非本项目代码问题。Mac 路径无法修复，等 Windows PyTorch 路径实测后再评估是否需要自行封装）

## P152 | 2026-06-05 | 转录调度 | Whisper 和千问没真正并行，修复方案：错开流水线

**问题**：tier=precise 的"并行模式"实际只让 SenseVoice(~2min) 和千问重叠。最贵的 Whisper（本次 37 分钟）单独串行先跑，跑完才启动后两者——省时没省到刀刃上（本次 53min 总耗时里，真正重叠的只有 ~2min）。

**原因**：千问被设计成用 Whisper 的段落边界切块（`segments_hint=results`），必须等 Whisper 跑完才能启动。SenseVoice∥千问 只是"都在 Whisper 之后又互相独立"的顺手并行，不是有意识的设计。

**修复方案（用户提出）**：Whisper 先启动 → 先判语言（~8 秒）拿到 ru → Whisper 边转边产段落，约 2 分钟后已有 ~10 段 → 千问拿这 10 段当一块、带着 language 开始转录 → 之后千问跟在 Whisper 后约 2 分钟流水线推进。两个最耗时的转录模型由此并发，省下 ~16 分钟（本次 53min→约 37min），且 language 传得过去。

**工程改动点**：① 让千问按时间/段落流式切块、不再整体依赖 Whisper 跑完；② Whisper 消费改为流式 yield 段落给千问；③ 先单独判语言再启动两路全量转录。

**前置条件（关键）**：**必须先端到端验证 P147/P148，确认千问不再坍缩。** 否则只是让一个 4% 有用、还拖垮 Whisper 的功能跑得更快——优化负价值功能的速度仍是负价值（Steve Jobs 审查结论：先跑测试，数字说话）。

**已修** ✅（2026-06-07，V3.07 ARCH ⑦ 生产者-消费者流水线。Whisper 边转录边喂千ASR，并行省时 ~13min。347 测试全过）

## P154 | 2026-06-05 | 配置 | API Key 加密导致 key 反复丢失

**问题**：用户每次重新打开前端，DeepSeek API key 都是空的。`config.json` 里 `apiKeys=[]`。

**原因**：`config_manager.save_config` 用 Fernet 加密 key 后存盘，`load_config` 解密。加密密钥从 `platform.node()-getpass.getuser()-uuid.getnode()` 派生——主机名/Wi-Fi MAC/Docker 网络接口任一变化 → 派生密钥变化 → 已存的 key 解不开 → `decrypt_api_key` 失败被 `except Exception: pass` 静默吞掉 → 前端看到空 key。

**根因**：产品是纯本地桌面应用，所有数据都在本地——不存在"config.json 被他人拷走"的威胁模型。加密防的是不存在的攻击者，却给真实用户造成了反复丢 key 的摩擦。

**解决方案（Steve Jobs + 用户决策）**：砍掉加密。明文存 key。用户自己负责自己电脑的安全。TDD：`test_plaintext_keys.py`（RED→GREEN，3 测试）。

**改动**：`config_manager.py` 删除 `encrypt_api_key`/`decrypt_api_key` 调用；文件内 `from .key_manager import` 两个 import 随加密逻辑一并移除。`key_manager.py` 保留（log scrub 功能仍在使用）。

**已修** ✅（2026-06-05，V3.05）

## P155 | 2026-06-05 | 版本管理 | Git 提交落后两个版本

**问题**：`v2/` 的 git log 最新提交是 V3.03。V3.04 和 V3.05 的版本快照文件夹（`v2/v3.04/`、`v2/v3.05/`）已手动复制创建，14 个活跃文件已修改，但从未 `git add` / `git commit`。git 不知道这两个版本存在。

**原因**：版本迭代靠"手动复制上一版本文件夹→在新文件夹改"完成，复制之后缺了 git commit 环节。当别人问我"最新版本是什么"时，我读到的是 git log 里的 V3.03，而不是磁盘上的 V3.05，这个差距就是缺少 git commit。

**解决方案**：① 每次版本迭代后执行 git commit，把当前所有改动（活跃代码 + 快照文件夹 + 问题库/主档案/progress/task_plan）一起提交；② 主档案 `video-subtitle.md` 记录规则：当用户说"版本迭代"时，等同于触发 git commit；③ commit message 格式固定为 `VX.XX: <一句话版本内容简述>`。

**已修** ✅（2026-06-05，当前改动已 commit）

## P156 | 2026-06-05 | 千问 | P147 修复后坍缩归零，但交叉验证映射率骤降：仅 12/186 段有 qwen_text

**测试**：job 0a73f157b2，P147/P148 修复后首次端到端运行。同视频、同 large-v3、同 tier=precise。

**现象**：
- ✅ P147 目标达成：千问坍缩归零（garbage 58% → 0%），chunk5 从 13.5 分钟降到 2.2 分钟
- ✅ P148 目标达成：SRT `[?]` 77% → 11%
- ⚠️ **新问题**：千问交叉验证映射率从旧版的 185/217（85%）降到 12/186（6%）。千问只产出了 12 句完整文本（旧版 185 句），其余 158/170 段的 qwen_text 为空 → L2 交叉验证对 93% 的段形同虚设

**根因分析（初版·猜测阶段）**：
- user.log 写着 `Qwen->Whisper 使用 1:1 映射 (qwen=12 whisper=186)`——千问转出了 12 个句子，Whisper 有 186 段
- `max_tokens=时长×10` 将每 chunk 上限从 ~15000 压到 ~3000 → 可能对正常转录也偏紧（5 分钟俄语演讲可能需要 3000+ token）
- 也有可能千问转录本身变保守了（repetition_penalty 1.4 + max_tokens 限制 → 正常输出也被提前截断）

**解决方案（初版·推测）**：~~调松 max_tokens（时长×15 或 ×20）或修对齐逻辑。~~ ← 跑偏了。对齐没问题，千问 raw 输出本身就是 12 句。

---

### 🔴 终版根因分析（2026-06-05 二次排查）

**确认事实**：
1. 日志 `qwen=12` 是千问 raw 转录结果——不是对齐丢了，是千问本身就只产出了 12 个句子
2. 千问3对俄语的识别能力不差——FLEURS WER=5.99（约94%正确率），30种语言排第13名，中上水平。Whisper large-v3 俄语 WER=~5.0，差不了一个百分点。**俄语不是根因。**
3. 竞品 WhisperJAV 千问配置：`repetition_penalty=1.1` + `max_tokens_per_audio_second=20.0` + 不设 `repetition_context_size`。他们的千问正常工作。

**根因确认：P147 三个参数同时向极端方向收紧，形成叠加压制效应：**

| 参数 | P147 前（工作） | P147（坏了） | WhisperJAV（工作） | 问题 |
|------|----------------|------------|-------------------|------|
| `_QWEN_REPETITION_PENALTY` | 1.2 | **1.4** | 1.1 | **主犯**。1.4 对俄语词尾屈折的重复音节过度惩罚。千问发现"说什么都被罚"→提前放弃生成 |
| `_QWEN_TOKENS_PER_SEC` | 50 | **10** | 20 | **从犯**。5min×10=3000 token。正常俄语 5min 需要 2000-4000，3000 刚好在边界，无余量 |
| `_QWEN_REPETITION_WINDOW` | 100 | 256 | 不设 | 放大了惩罚范围。配合 1.4 的力度，256 token 窗口内所有重复音节都被扫射 |

**协同效应**：1.4 让千问不敢说话 + 10t/s 让它没时间说话 = 93% 输出消失。不是"对齐问题"，不是"俄语太差"，是 P147 用大锤把千问的嘴堵上了。

**🔴 P147 教训**：一次改了三个参数，无法隔离哪个解决了坍缩、哪个造成了副作用。修 bug 应该一次改一个参数，验证通过后再改下一个。

### ✅ 终版修复方案（2026-06-05 二次校准）

**改动**：`v2/backend/core/qwen_asr_runner.py` 两行常量

| 行号 | 参数 | 当前值 | 改为 | 理由 |
|------|------|--------|------|------|
| 49 | `_QWEN_REPETITION_PENALTY` | 1.4 | **1.1** | 对齐 WhisperJAV。温柔提醒不误伤俄语正常重复 |
| 51 | `_QWEN_TOKENS_PER_SEC` | 10 | **20** | 对齐 WhisperJAV。5min×20=6000 token（3倍余量）。坍缩时 6000 就熔断 vs 旧版 15000 |
| 50 | `_QWEN_REPETITION_WINDOW` | 256 | **不动** | 范围本身合理，问题在惩罚力度 |
| 53 | `_QWEN_MIN_TOKENS` | 256 | **不动** | 兜底值合理 |
| — | （不新增硬上限） | — | **不做** | 速率限制 20t/s 本身就是熔断器。加硬上限会重复 P156 的截断 |

**设计原则**（Steve Jobs 审查）：不要用一个参数做两件事。`repetition_penalty` 只管说话习惯（温柔提醒），`TOKENS_PER_SEC` 只管呼吸空间和熔断。各管各的。

**验证计划**：
1. 改完两行→同一条俄语视频重跑 tier=precise→看 `qwen=X` 回到 180+ 
2. 确认坍缩率保持 0%
3. 端到端确认 `[?]` 比例不恶化
4. 拿一条日语视频再跑（日语重复结构比俄语多，测 1.1 是否防得住坍缩）
5. 全套 233 测试确无退化

**回滚**：日语坍缩重现→按语言动态调整 `repetition_penalty`（日语 1.15，其他 1.1）

**已修** ✅（2026-06-05，V3.05，二次校准）

## P157 | 2026-06-05 | 翻译 | DeepSeek 返回俄语照抄被判"170/170成功"
**问题**：job 0a73f157b2，DeepSeek 翻译返回 170 条俄语原文（零中文），系统报告"170/170 成功"。烧录字幕全是俄语，没有翻译。

**原因**：`_translate_one_chunk()` 只校验"返回格式有没有 [编号]"——编号存在就判成功。从不检查内容是不是真的翻译（中文字符是否存在）。

**解决方案（Steve Jobs + 用户决策）**：`orchestrator.py` 新增 `_is_valid_translation()`——返回文本的中文字符占比 < 10% 即判失败（`continue` 跳过该条 → 保留为 `[翻译失败]` → 质量闸 (<50% 即删缓存抛异常）自动触发降级链 → Google/Bing/有道免费引擎重新翻译。TDD: `test_translation_content_gate.py` (5 tests)。

**改动**：`backend/translation/orchestrator.py` — 新增 `_is_valid_translation()` + `_TRANSLATION_CJK_MIN_RATIO = 0.1`；`_translate_one_chunk()` 解析 zh 后加校验门（~6 行）。

**已修** ✅（2026-06-05，V3.05）

## P158 | 2026-06-05 | 前端 | 字幕默认字号 20px 只有行业标准 ~40%
**问题**：用户感觉烧录字幕比预览小很多。实际测量 20px 只有 Netflix(42px)/YouTube(48px) 的 ~40%。没有缩放 bug——视频 1920×1080、PlayRes 1920×1080、缩放倍率 1.0，20px 真实就是 20px。

**原因**：默认字号从项目第一天就设错了。前端可选值最大仅 28px（仍低于行业最低标准 34px）。

**解决方案（Steve Jobs + 用户决策）**：① `config_manager.py` DEFAULT_CONFIG size 20→42；② 前端 select 改六档 34/38/42/46/50/56，默认 42；③ 无旧用户，不需配置迁移。

**改动**：`backend/config_manager.py:13` (1 行) + `frontend/index.html:297` (1 行)。

**已修** ✅（2026-06-05，V3.05）

## P159 | 2026-06-05 | 前端/后端 | 转录进度信息粒度过粗——子阶段不可见

**问题**：前端进度条在"转录中"阶段卡住不动——tier=precise 时转录阶段内部有三个子阶段（Whisper 转录 ~30min → SenseVoice 声音分类 → Qwen3 交叉验证），但 Worker 始终向前端发同一条步骤消息 "转译中"。用户看到进度条停在"已识别 370 段"不动，风扇响了又停了，不知道是还在跑还是已经崩了。

**影响**：
- Whisper 跑 37 分钟期间，进度条文字完全不变
- Qwen3 和 SenseVoice 并行跑时，前端无法区分谁在跑
- 用户只能靠听风扇判断——"风扇大声=在跑，风扇小声=卡死？"

**原因**：`worker.py` 的 `emit("progress")` 在转录阶段只发了单一的步骤描述。后端有子阶段的信息（日志里写着"Whisper 模型已释放""Qwen3 chunk 1/5""SenseVoice chunk 2/5"），但这些信息没有通过 WebSocket 推给前端。

**解决方案**（Steve Jobs 审查）：
- **只改后端，不改前端**——前端已经有进度显示机制，收到什么 `step` 就显示什么。问题在后端没发不同的文字
- Worker 在转录阶段改 emit，把 `step` 从统一的 "转译中" 改为子阶段描述（"Whisper 转录中…""交叉验证中…(Qwen3)""声音分类中…(SenseVoice)"）
- 不加新字段、不改数据结构、不碰前端代码

**推迟原因**：当前前端是临时 HTML（即将被 PySide6 替换）。在即将废弃的代码上加 UI 优化是浪费——就像给纸板模型喷漆。

**归属**：V3.09（PySide6 前端重写时一并实现）

**⏸ 未修**

## P160 | 2026-06-05 | 千问对齐 | 千问产 2062 短句 vs Whisper 266 段 → 量级差触相似度匹配 → 映射率仅 35%

**测试**：job 1b520de51c，同视频、同 large-v3、同 tier=precise。P156 修复后（1.1 + 20t/s）首次端到端。

**现象**：
- ✅ 千问坍缩归零，5 个 chunk 全部均匀完成
- ✅ 千问 raw 产出 2062 句（P156 只有 12 句，恢复正常）
- ⚠️ 映射率仅 92/266（35%），远低于旧版 85%（185/217）

**原因**：千问 2062 句 vs Whisper 266 段——数量差超过 20% 阈值，触发相似度匹配（而非 1:1 映射）。`SequenceMatcher` 阈值 0.2 对俄语词形变化不友好（同一个词的变格形式被认作不同文本，相似度 < 0.2 被丢弃）。

**对比旧版 85%**：旧版千问产出的是整句（每一句能对应一个 Whisper segment）；新版参数更宽松后，千问倾向输出更多短句/短语，导致句数爆炸、对齐失效。

**解决方案候选**：
- A) 提高千问句间合并（连续短句合并后再对齐）
- B) 降低相似度阈值 0.2→0.15（俄语宽松版）
- C) 改词级比对（P149 备选方案）
- D) 先不动——35% 的交叉验证覆盖也比 6% 强，且翻译 254/254 全成功，终端用户无感

**已修** ✅（2026-06-07 TDD 通过，SequenceMatcher→LightweightStemmer）

### 未解决部分

P160 原包含一个「终版架构（三层防线流水线）」设计方案（SenseVoice→重复检测→千问 B+A），该方案为架构愿景，未实施。

其中核心痛点——逐段映射导致 L2 数据被截流——已由 **P251**（砍掉 `align_by_similarity`）跟踪。
剩余的三层防线架构设计留待 V4 或后续大版本统一规划。

## P161 | 2026-06-05 | 接线冲突 | transcriber_v2.py 提前删段 → 新 L2 双重确认被架空

**问题**：当前 `transcriber_v2.py:292` 的 `trim_segments_by_sensevoice()` 在调用 `filter_hallucinations()` 之前就物理删除了 109 段非人声内容（job `1b520de51c` 实测数据）。新 L2 的第一层"双重确认防线"（SenseVoice + 千问联合判断）永远收不到这 109 段数据——千问没法给它们做二次确认。

**影响**：新 L2 的双重确认逻辑代码本身是正确的，但被上游截流了。当前不影响核心功能——剩下的 266 段人声区仍然完整进入窗口比对。但"非人声区域双重确认"的设计意图没有完全落地。

**深度分析**（2026-06-06 · Steve Jobs + 用户审查）：

P161 不是"缺一个功能"，而是三个历史遗留问题叠加：

**问题一：保安有两处可以独自删段。** 第一道门在 `transcriber_v2.py:292`——`trim_segments_by_sensevoice()` 在审稿之前就物理删除段。第二道门在 `hallucination_filter.py:169-175`——L1 单信号 fallback，当校对员（王五/Qwen3-FA）没交报告时，保安一个人决定删除。两道门都违背了"双确认才删除"的原则。

**问题二：并行搭档搭错人。** 代码里已有并行执行（`transcriber_v2.py:245-262`），但把保安（SenseVoice）和第二个速记员（李四/Qwen3-ASR）配成一对。保安应该跟校对员（王五/Qwen3-FA）搭档——两者回答同一个问题："这段到底有没有人在说话？"

**问题三：校对员只在精确档上班。** 标准模式下保安来上班了，但校对员不来（`transcriber_v2.py:323` 限制了 `transcription_tier == "precise"`）。保安没搭档→备用条款生效→一个人说了算。而标准模式是大多数用户选的。

**解决方案**（Steve Jobs + 用户决策·V3.07 执行）：

- ① **砍掉第一道门**：删掉 `trim_segments_by_sensefuture` 调用。保安只贴标签（`seg.sensevoice_tag`），不撕稿子。
- ② **砍掉第二道门的备用条款**：删掉 L1 的单信号降级路径。双信号是唯一路径——没有双信号就没有删除判决。不是"改软"，是彻底删除。修完后标准和精确档保安和王五都在，不存在保安一个人在场的情况。
- ③ **修正并行搭档**：把并行执行的配对从"保安+李四"改成"保安+王五"。李四跟张三搭档（维度A的内容对比）。
- ④ **王五在标准档也上班**：Qwen3-FA 不再锁在精确档。标准和精确都需要维度B。修完后：
  - 标准 = 张三一人转录 + 保安+王五人声确认
  - 精确 = 张三+李四双转录 + 保安+王五人声确认
  - 维度A（内容对比）仅精确档开，维度B（人声确认）两个档都开

**不改的东西**：维度A和维度B的判断逻辑（2×2 矩阵、窗口词干比对）已在 `hallucination_filter.py` 中，不需要重写。快速模式不受影响。翻译管线、字幕生成、烧录完全不碰。

**归属**：V3.07（后端精度收尾批次）
**已修** ✅（2026-06-07 TDD 通过，4 子任务全部完成，8 测试）


## P162 | 2026-06-05 | 翻译 | P157 根因 — DeepSeek prompt 缺目标语言声明

**问题**：P157 的内容闸正确拦截了 DeepSeek 返回俄语原文的症状，但根因未修——`_SUBTITLE_PROMPT` 从未显式声明"翻译成简体中文"。V2.12 时代 prompt 短（4 条规则），DeepSeek 能从"你是专业视频字幕翻译"推断目标语言。V3 重构后 prompt 大幅扩展（+内容类型策略 +术语表 +上下文摘要），DeepSeek (`deepseek-chat`) 在复杂多段 prompt 上指令跟随能力弱于 GPT-4/Claude，面对俄语输入 + 没明确目标语言的复杂 prompt → 选择了最安全的做法：保留原文。

**证据**：
- DeepSeek 后端代码（`deepseek.py`、`base.py`）V2.08→V3.06 **零改动**——模型、API、temperature 完全一样
- `_SUBTITLE_PROMPT` 从 V2.12 的 4 行简短规则变为 V3 的多段复杂结构
- 两个版本都没写"翻译成中文"，但简短版 DeepSeek 能推断，复杂版推断失败
- 用户四次测试：前两次（V3.01-V3.03？）翻译正常，后两次（V3.05+）返回俄语——与 prompt 在 V3.01 就变了矛盾，需用户确认前两次测试是否也是俄语视频+DeepSeek

**解决方案**（Steve Jobs 锁定）：新增 `_TARGET_LANGUAGE` 变量，嵌入 prompt——今天值是 `"简体中文"`，未来从 config 读用户选择的语言。一个变量，一个 prompt，不和未来冲突。

```python
_TARGET_LANGUAGE = "简体中文"

_SUBTITLE_PROMPT = (
    f"你是专业视频字幕翻译，所有翻译的目标语言为{_TARGET_LANGUAGE}。翻译规则：\n"
    ...
)
```

**归属**：V3.07（与 P149/P161 同批）

**已修** ✅（2026-06-07 TDD 通过，`_TARGET_LANGUAGE = "简体中文"` 嵌入 prompt）

---

## P167 | 2026-06-07 | SenseVoice | SenseVoice 标签匹配用严格包含——跨边界段落漏贴标签
**问题**：transcriber_v2.py 第 289 行，给段落贴 SenseVoice 标签时，要求段落完全落在事件范围内（`ev["start_s"] <= seg.start and seg.end <= ev["end_s"]`）。如果段落跨越事件边界（比如音乐事件到 2.5 秒，段落从 2.3 到 4.1 秒），标签不会被贴上。
**原因**：条件判断用了"完全包含"而非"有重叠"
**解决方案**：改为 overlap 匹配，取最大重叠事件，≥50% 重叠率才贴标签
**归属**：V3.07 第一批 [BUG]（与 P161/P166/P160 同批）
**已修** ✅（2026-06-07 TDD 通过，overlap ≥50% 匹配，5 测试）

---

## P168 | 2026-06-07 | 千问分段 | 分段转录时间戳偏移 5 秒——缓冲区未修正
**问题**：qwen_asr_runner.py 第 302 行和第 399 行，分段转录时提取音频多切了 5 秒前导缓冲（`extract_start = max(0, t_start - 5)`），但回算时间戳时用的是 `t_start` 而非 `extract_start`，导致所有词的时间戳系统性偏后 5 秒
**原因**：偏移量用错变量
**解决方案**：传入 `extract_start = max(0, t_start - _AUDIO_PAD_S)` 做偏移基准。改 2 个函数各 2 行
**归属**：V3.07 第三批 [ARCH]（P152+P144 流水线重构时一并修）
**已修** ✅（2026-06-07 TDD 通过，extract_start 替代 t_start，347 测试全过）

---

## P169 | 2026-06-07 | 千问对齐 | run_qwen_asr 直接路径缺 return_time_stamps=True
**问题**：qwen_asr_runner.py 第 529 行，run_qwen_asr() 调用 model.transcribe() 时未传 return_time_stamps=True，导致 PyTorch 路径永远拿不到时间戳，退化为文字相似度对齐
**原因**：遗漏参数（transcribe_raw 有传，run_qwen_asr 没传）
**解决方案**：在 PyTorch 分支加 `return_time_stamps=True`。1 行改动
**归属**：V3.07 第三批 [ARCH]（P152+P144 流水线重构时一并修）
**已修** ✅（2026-06-07 TDD 通过，return_time_stamps 统一放 _qwen_gen_kwargs()，347 测试全过）

---

## P170 | 2026-06-07 | 千问对齐 | run_qwen_asr 对空段落列表未做防护
**问题**：qwen_asr_runner.py 第 508 行，`total_duration = segments[-1].end` 未检查 segments 是否为空。纯静音音频经 Whisper 返回 0 段落时，触发 IndexError 崩溃
**原因**：缺空列表检查
**解决方案**：第 508 行前加 `if not segments: return segments`。1 行
**归属**：V3.07 HARDEN 就地修
**已修** ✅（2026-06-07 — run_qwen_asr() 第 544-545 行加 `if not segments: return segments`。_chunk_segments() 第 207 行已有同名防护，现覆盖全部入口）

---

## P171 | 2026-06-07 | 幻觉过滤 | L2 窗口分数只用 Whisper 词干数做分母——短窗口偏向高分
**问题**：hallucination_filter.py 第 377 行，score = len(shared) / len(w_stems)。Whisper 在某窗口只出 3 个词时，3/3=1.0=绿灯，即使这 3 个词是幻觉
**原因**：分母只取一侧，未考虑双方词数差异
**解决方案**：两步修复——
1. MIN_STEMS=5 门槛：词干数<5 时标记 LOW_DATA 而非 GREEN/YELLOW
2. Steve Jobs 审查（2026-06-07）：分母从 `min(w, q)` 改为 `max(w, q)`。当 Qwen 6 字/Whisper 20 字/相同 6 字时，旧分数 6/6=100%（假绿灯），新分数 6/20=30%（正确暴露信息缺失）。不需要新参数——`max` 自动惩罚量级不平衡
**归属**：V3.07 第二批 [FEATURE]
**已修** ✅（MIN_STEMS 门槛 + max 分母，385 测试全过）

---

## P172 | 2026-06-07 | 幻觉过滤 | L4 重复词正则漏 2 次重复
**问题**：hallucination_filter.py 第 122 行，`_RE_REPEATED_WORDS` 正则要求至少重复 3 次（`\2{2,}`），"谢谢 谢谢"这种 2 次重复不会被捕获
**原因**：正则门槛偏高
**解决方案**：将 `{2,}` 改为 `{1,}`。但需评估误杀率（"你好 你好"可能是正常对话）
**归属**：V3.07 HARDEN 就地修（评估后决定）
**已修** ✅（2026-06-07 — 正则从 `^((\S+)\s+)\2{2,}\2*$` 改为 `^(\S+)(\s+\1)+$`。原结构对有空格分隔的 3+ 词根本不上匹配——不仅是门槛问题。新正则同时修复了匹配逻辑和门槛问题）

---

## P173 | 2026-06-07 | 千问对齐 | 模型加载/释放代码重复 4 处
**问题**：qwen_asr_runner.py 中 4 个函数各自写了一套 MLX/PyTorch 分支加载模型的代码（第 264、347、471、517 行），模型 ID 散布在 4 处
**原因**：历史堆叠，未提取公共函数
**解决方案**：提取 `_load_qwen_model()` 公共函数，4 处统一调用
**归属**：V3.07 HARDEN [CLEANUP]
**已修** ✅（2026-06-07 TDD 通过，归入 ARCH ⑦ P152+P144 流水线重构，347 测试全过）

---

## P174 | 2026-06-07 | 功能 | YouTube/B站 链接直传——下载到缓存自动入流水线
**问题**：当前 video_downloader.py 已实现 yt-dlp 下载能力，但下载路径是用户可见的 ~/Downloads/Video-Subber/，且下载后不自动进入流水线，需要用户手动操作
**解决方案**（2026-06-07 实现）：
1. main.py `create_job` 新增 URL 模式：`{"url": "https://..."}` → 创建 job，config 中设 `source_url`，hash 基于 URL 字符串
2. worker.py 管线启动前检测 `config["source_url"]`：`stderr_progress("download", ...)` 推送进度 → `download_video()` 下载到 job_dir → 更新 `video_path` 和 `video_filename` → 继续正常流水线
3. video_downloader.py 无改动，`download_video(url, output_dir)` 直接复用
4. 旧 `/api/download-video` 端点保留（独立下载功能）
**前端**：URL 输入框留给 V3.09 PySide6
**归属**：V3.07 第二批 [FEATURE]
**已修** ✅

---

## P175 | 2026-06-07 | P152 审查 | Whisper 模型释放时机——流水线模式下必须等 generator 迭代完
**问题**：plan-eng-review 审查 P152 流式并行方案时确认——Whisper generator 引用了 model 对象，generator 迭代完之前不能 del model，否则 segfault
**结论**：当前代码 line 229-231 的 `del model; gc.collect()` 位置就在 for 循环之后，已经安全。流水线模式下不需改动此处。Whisper 在 GPU VRAM（~3GB），千ASR 在 CPU RAM（~4.7GB），不冲突
**归属**：V3.07 第三批 [ARCH]（P152 实现时确认即可）
**已修** 🔧（无需改代码，实现时确认位置）

---

## P176 | 2026-06-07 | P152 审查 | Producer-Consumer 同步机制选型——主线程和千ASR线程怎么配合
**问题**：流水线模式下主线程跑 Whisper 产出 segments，消费者线程跑千ASR 处理。需要一个同步机制让消费者知道"有新数据了"
**方案**：`threading.Event` + `threading.Lock` + 共享 `results` 列表。Event 做通知（producer 每 10 段 set 一次唤醒 consumer），Lock 保护 results 列表的并发读写。比 `queue.Queue` 改动更小——results 列表已经存在，consumer 需要读 segments 的 .start/.end 来分块
**归属**：V3.07 第三批 [ARCH]（P152 实现）
**已修** ✅（2026-06-07 — queue.Queue + threading.Thread 在 ARCH ⑦ 流水线重构中实现）

---

## P177 | 2026-06-07 | P152 审查 | 消费者动态分批逻辑——攒够多少段才处理一批
**问题**：`_chunk_segments()` 需要一次性拿到所有 segments 才能分批。流水线模式下 segments 逐渐到达，consumer 需要自己判断"够一批了"
**方案**：
1. Consumer 每次被 Event 唤醒后检查新增 segments 的累计时长
2. 累计时长 >= 300s（5 分钟）→ 取出这批，调千ASR 处理
3. Producer 结束后设 done Event → consumer 处理剩余的（不够 5 分钟的尾批）
4. 不复用 `_chunk_segments()`，consumer 自己维护 `processed_up_to` 指针
**归属**：V3.07 第三批 [ARCH]（P152 实现）
**已修** ✅（2026-06-07 — 动态分批 + 尾批处理在 ARCH ⑦ 流水线重构中实现）

---

## P178 | 2026-06-07 | Steve Jobs 审查 ARCH ⑨ | _CHUNK_SIZE / _OVERLAP_SIZE 死代码残留
**问题**：ARCH ⑨ 完成后，`_translate_whole_with_markers()` 已改用 `_compute_chunk_size(llm)` 动态计算分块大小，但 `orchestrator.py:326-328` 的 `_CHUNK_SIZE = 1200` 和 `_OVERLAP_SIZE = int(_CHUNK_SIZE * _OVERLAP_RATIO)` 仍在文件中，已成为死代码。
**原因**：`_compute_chunk_size()` 内部用 `max_input_tokens` 动态算，旧常量不再被任何代码引用。
**解决方案**：删除 `_CHUNK_SIZE` 和 `_OVERLAP_SIZE` 两行。`_OVERLAP_RATIO = 0.2` 保留（`_translate_whole_with_markers()` 中 `overlap_size = int(chunk_size * _OVERLAP_RATIO)` 仍在使用）。
**归属**：V3.07 第四批 HARDEN 就地清理
**已修** ✅（2026-06-07 — 删除 `_CHUNK_SIZE = 1200` 和 `_OVERLAP_SIZE = int(_CHUNK_SIZE * _OVERLAP_RATIO)`，保留 `_OVERLAP_RATIO = 0.2`（_translate_whole_with_markers 仍使用））

---

## P179 | 2026-06-07 | L2 时间维度 | L2 未使用 Whisper 已有的逐字时间戳（🟡 important）

**背景**：Steve Jobs 审查发现——段落级时间戳无法区分「老张在静音区编了内容」和「小李漏听了」。但 Whisper 已输出逐字时间戳（`word_timestamps=True`，存于 `segment.words`），SenseVoice 已有人声区间地图。两样数据都在，L2 只是没接上线。

**问题**：L2 当前只用 segment.text 做窗口级词干比对，完全没用 `.words` 里的逐字时间戳。无法判断「老张的字出现在没人说话的时段」。

**解决方案**（Steve Jobs 锁定）：
1. L2 拿到每个 window 的 segment.words
2. 每个字的 start/end 跟 SenseVoice 人声区间比对
3. 落在人声区外的字 → 标记「时间戳异常」
4. L2 输出两维：内容一致性 + 时间有效性（人声区内字数 ÷ 总字数）
5. 不依赖 Qwen FA——Whisper 自己的逐字时间戳 Mac 上就有

**❌ 已关闭（2026-06-11 换库自然解决）**。换 MLX ASR 库（gabrimatic→moona3k）后千问自带词级时间戳，L2 归窗从相似度猜窗口变为精确时间归窗（`win = int(seg["start"] // 30)`），不再需要 Whisper 逐字时间戳来补充时间维度。详见 `MLX-ASR-换库方案.md`。关联关闭：[[P149]]、[[P171]]、[[P212]]

---

## P180 | 2026-06-07 | Qwen ASR 逐字时间戳 | Windows 上有逐字时间戳但未结构化存储（🟢 架构预留）

**背景**：Qwen ASR PyTorch 路径通过 `align_by_timestamps()` 用了逐字时间戳做对齐（qwen_asr_runner.py:135），但最终只存 `qwen_text` 字符串——逐字时间戳被丢弃了。Windows 上 Qwen+FA 的时间戳是「百分百正确」的，不该丢掉。

**问题**：Qwen 的逐字时间戳没有像 Whisper `.words` 那样结构化存储到 Segment。

**解决方案**（Steve Jobs 锁定）：
1. Qwen ASR 转录完成后，逐字时间戳存到 `segment.qwen_words`（与 `.words` 同构）
2. L2 同时用 Whisper `.words` 和 Qwen `.qwen_words` 双向时间验证
3. Qwen FA 只跑一次——时间戳既是验证依据也是字幕渲染依据
4. 砍掉单独给 Whisper 做 FA 对齐的第二次调用

**归属**：V3.09

---

## P181 | 2026-06-07 | 前端重连 | handleFoundJob 跳过 connectCard 导致卡片进度永不更新（🟡 important）

**问题**：`reconnectJobs` 先调 `addJobCard` 创建卡片并在 `activeJobs[jid]` 中注册条目，随后用户提交同一任务触发 hash 命中 → `handleFoundJob` 看到 `activeJobs[jid]` 已存在就跳过 `connectCard()`。结果 WebSocket 和 HTTP 轮询都没启动，卡片永远停在"正在恢复处理…"，后端进度正常但不推送。

**触发条件**：
1. 页面刷新时 `reconnectJobs` 创建了卡片（`addJobCard` → `activeJobs[jid]` 已存在）
2. 用户重新提交同一任务（hash 命中）→ `handleFoundJob` else 分支 → 看到 `activeJobs[d.job_id]` 已存在 → 跳过 `connectCard`
3. 卡片上的 `activeJobs[jid]` 是 `addJobCard` 创建的空壳（`ws:null, pollTimer:null`），没有实际连接

**根因**：`handleFoundJob` 只检查 `activeJobs` 中是否存在该 job_id，不检查是否已建立连接。`addJobCard` 创建的初始 entry 和 `connectCard` 建立的连接态无法区分。

**历史关联**：P043（WebSocket 跨 event loop 失败）、P035（后端重启后 interrupted 任务不重连）均表现为"后端进度正常但前端不更新"。本次根因不同——不是 WebSocket/event loop 问题，而是重连入口被活跃条目空壳误判为"已连接"。

**解决方案**（待定）：
1. `reconnectJobs` 的 error/interrupted 分支不再调用 `removeActiveJobId`（因为它不清理 `activeJobs` 对象），改为调用 `delete activeJobs[jid]` + `removeActiveJobId`
2. 或：`handleFoundJob` 的 else 分支加判断 `if(!activeJobs[d.job_id] || !activeJobs[d.job_id].ws)` → 强制重连
3. 或：统一清理函数——`removeCard` 已正确清理，error/interrupted 分支改用 `removeCard`

**⏸ 未修**（待 V3.07 后期或 V3.08 修复）

---

## P182 | 2026-06-07 | V3.07 审查 | 流水线尾巴丢失——短视频千ASR 交叉验证完全失效（🔴 critical）

**问题**：P152 生产者-消费者流水线按 300s（5 分钟）为阈值分批发送。循环结束后只发 `None` 终止信号，最后一批不足 300s 的段落从未入队。

**触发条件**：
1. precise 模式 + 16GB+ 内存 → 启用流水线
2. 视频总长 < 5 分钟：一批都发不出去，千ASR 白加载
3. 视频总长 > 5 分钟但非 5 的整数倍：最后 1-4 分钟的段落丢失交叉验证

**根因**：`transcriber_v2.py:273-281`，循环结束后直接 `qwen_queue.put(None)`，没有先 flush 剩余段落。同时 `qwen_thread` 是 truthy，所以串行回退路径（`if not qwen_thread:`）也不会运行。

**影响**：短视频在精准模式下完全丧失千ASR 交叉验证能力。长视频丢失尾部验证。

**解决方案**：在 finally 块中，发 None 之前先把 `results[chunk_start_idx:]` 入队：
```python
finally:
    if chunk_start_idx < len(results):
        remaining = results[chunk_start_idx:]
        qwen_queue.put((list(remaining), remaining[0].start, remaining[-1].end))
    qwen_queue.put(None)
```

**已修** ✅ (2026-06-07 TDD: test/test_p182_tail_flush.py)

---

## P183 | 2026-06-07 | V3.07 审查 | L1 单信号兜底删除——FA 失败时噪音段穿透（🟡 important）

**问题**：P161 删除了 L1 的 SenseVoice 单信号回退。正常流程中 SenseVoice 和 FA 总是一起工作（标准/精准模式），所以没问题。但如果 FA 加载失败或运行崩溃，`aligner_result` 为 None，L1 直接跳过——SenseVoice 检测到的非人声段（音乐、噪音）会穿透所有五层过滤。

**触发条件**：标准/精准模式下，SenseVoice 正常但 FA 模型加载失败（磁盘空间不足、模型文件损坏等）

**根因**：`hallucination_filter.py:147-148`，`aligner_result` 为 None 时 L1 返回 False（不过滤）。删除单信号兜底后，缺少 FA 失败时的防护。

**解决方案**（待用户确认方向）：
- 方案 A：FA 失败时恢复 L1 单信号兜底（SenseVoice 说非人声就丢）
- 方案 B：FA 失败时不丢段落，但标 uncertain（黄色警告）
- 需要在 `transcriber_v2.py` 调用 `filter_hallucinations` 之前检测 FA 状态

**已修** ✅ (2026-06-07 TDD: test/test_p183_l1_fallback.py)

---

## P184 | 2026-06-07 | V3.07 审查 | transcriber_v2.py Step 注释编号混乱

**问题**：代码中 Step 5（SenseVoice 标签）出现在 Step 4（幻觉过滤）之前，且有两个 Step 5。重构后注释未更新。

**文件**：`transcriber_v2.py:418-431`

**解决方案**：按实际执行顺序重新编号（Step 4 → Step 5 → Step 6）

**已修** ✅ (2026-06-07: Step 4→6, Step 5→7) | 2026-06-07 | V3.07 审查 | 未使用的 import _chunk_segments

**问题**：`transcriber_v2.py:234` 导入了 `_chunk_segments` 但流水线路径中从未使用。

**解决方案**：删除该 import

**已修** ✅ (2026-06-07: removed unused _chunk_segments import)

---

## P186 | 2026-06-07 | V3.07 审查 | 循环依赖——翻译层 import 服务器模块

**问题**：`translation/base.py:67` 用 `from backend.main import _MODEL_CONTEXT_CACHE` 让翻译层依赖 Web 服务器内部数据结构。lazy import 避免了启动崩溃，但架构上不干净。

**解决方案**：将 `_MODEL_CONTEXT_CACHE` 移到共享模块（如 `backend/model_cache.py`），翻译层和 main.py 都从那里读取。

**已修** ✅ (2026-06-07: extracted _MODEL_CONTEXT_CACHE to backend/model_cache.py, updated main.py + translation/base.py)

---

## P187 | 2026-06-07 | V3.07 审查 | orchestrator.py _compute_chunk_size 后缺空行

**问题**：`orchestrator.py:338-339`，`_compute_chunk_size()` 函数定义后紧跟 `_SEAM_CONTEXT = 50`，中间没有 PEP 8 要求的两个空行。

**解决方案**：加一个空行

**已修** ✅ (2026-06-07: added blank line)

---

## P188 | 2026-06-07 | V3.07 安全审查 | P174 URL 输入无校验（🟡 中等）

**问题**：`main.py:588` 的 `source_url = body.get("url", "").strip()` 对用户传入的 URL 没做任何格式/协议校验。理论上可传入 `file:///etc/passwd`、`ftp://`、空白字符填充的恶意 URL 等。虽然 yt-dlp 自身对 URL 有一定校验（只支持已知视频站点的 extractor），但依赖下游库做安全校验不是好实践。

**原因**：P174 URL 直传功能新增时只做了功能实现，未加输入校验层。

**解决方案**：在 `create_job()` 入口处加 URL 校验——至少检查协议为 `http://` 或 `https://`，可选地检查域名非 `localhost`/`127.0.0.1`/内网 IP 段（防 SSRF）。

**已修** ✅ (2026-06-07 TDD: test/test_p188_url_validation.py)

---

## P189 | 2026-06-07 | V3.07 安全审查 | job_id 路径遍历理论风险（🟢 低）

**问题**：`main.py:779-791` 的 download 端点用 `JOBS_DIR / job_id` 拼接路径，`job_id` 来自 URL 路径参数。虽然 FastAPI 的 URL 路径参数默认不含 `/`（所以 `../` 遍历不通过 URL 层），但直接用字符串拼路径未做 `.resolve()` 校验是不够严谨的安全实践。当前为桌面应用（localhost），攻击面极小。

**原因**：本应用设计为本地桌面工具，API 仅监听 localhost，所以安全设计没考虑外部攻击者。但如未来开放远程访问或被其他本地进程调用，此处可被利用。

**解决方案**：在 download/cancel 等端点中加 `resolved = (JOBS_DIR / job_id).resolve()`，确认 `resolved` 仍在 `JOBS_DIR.resolve()` 之下。不紧急，可留到安全加固阶段。

**已修** ✅ (2026-06-07: .resolve() guard added to 3 download endpoints)

---

## P190 | 2026-06-07 | V3.07 安全审查 | 远程配置 URL 由环境变量控制（🟢 信息）

**问题**：`main.py:116-119` 的 `_REMOTE_BASE` 通过 `VSUB_REMOTE_CONFIG_URL` 环境变量覆盖默认值。如果攻击者能控制环境变量（恶意 .env 文件、共享主机等），可将模型列表/价格数据重定向到恶意服务器，影响模型选择和 context window 计算。当前默认值指向 GitHub 公开仓库，正常使用下无风险。

**原因**：设计合理（允许自部署配置源），但缺少文档说明此风险。

**解决方案**：纯信息项，无需修改。如后续发布，可在文档中说明此环境变量的安全含义。

**已修** ℹ️ 关闭（信息项，无需代码改动）

---

## P191 | 2026-06-07 | V3.07 架构分析 | transcribe() 圈复杂度 41（🟡 架构债）

**问题**：`transcriber_v2.py:167` 的 `transcribe()` 函数圈复杂度高达 41（项目最高），集成了 Whisper 转录、SenseVoice 降噪、FA 对齐、Qwen ASR 交叉验证（含生产者-消费者模式）、说话人检测等逻辑。P182 的 trailing chunk bug 正好藏在此函数内，高复杂度增加了隐藏 bug 的概率。

**原因**：V3 阶段持续叠加功能（P152 生产者-消费者、P167 SV 标签、说话人检测），功能堆积在一个函数中未做拆分。

**解决方案**：留到 V3.08 架构整理时拆分——将 SenseVoice 线程启动、FA 线程启动、生产者-消费者队列管理、说话人检测各提取为独立函数，`transcribe()` 只做编排（目标复杂度 ≤15）。

**⏸ 未修**（架构债，不阻塞当前版本。Steve Jobs 审查：V3.08 第一天做，不要发布前拆最复杂函数）
**V3.08 待办**：拆分成 5-6 个独立函数，目标复杂度 ≤15

---

## P192 | 2026-06-07 | V3.07 架构分析 | 快照目录被索引工具扫描的噪音（🟢 信息）

**问题**：hex-graph 索引了所有只读快照目录（v2.10/v2.11/v2.12/v3.01-v3.06），导致报告 1092 个"未使用导出"和 254 个"克隆组"。这些全是跨版本快照的正常重复，不是代码问题。

**原因**：项目版本管理使用文件夹快照方式，索引工具无法区分活跃代码和只读快照。

**解决方案**：在项目根目录添加 `.hexignore` 或类似机制排除快照目录。或在分析时使用 `scope: "backend/"` 限定范围。纯信息项。

**已修** ℹ️ 关闭（信息项，加 .hexignore 或分析时限定 scope）

---

## P193 | 2026-06-07 | V3.07 端到端测试 | L2 Pass 1 用 Qwen3-ASR 转录文本交叉验证 SenseVoice，英文视频 [?] 率 64%

**问题**：London Vlog 英文测试视频 253/393 条字幕被标记 [?]（64%），中文视频仅 5-15%。`hallucination_filter.py` L2 Pass 1（第 307-317 行）将 SenseVoice 音频分类与 Qwen3-ASR 转录文本做对比——两个不同维度的信号。SenseVoice 判 BGM 且 Qwen3-ASR 恰好也有输出（即使它在幻觉，如 "Color is darling"），就标"分歧"→ 打 [?]。

**流程分析**：
- L1（SenseVoice × Qwen3-FA 对齐）设计正确：音频分类 vs 强制对齐——FA 能在音频中找到词的时间对齐 = 音频里真的有语音。四向判断覆盖所有情况。
- L2 Pass 1 篡越了 L1 的职责：SenseVoice 的"人声 vs 非人声" L1 已经用 FA 对齐数据回答过了。L2 又用 Qwen3-ASR 转录文本再答一次——但 Qwen3-ASR 在背景音乐下也产生幻觉，它的输出不能作为"语音存在"的证据。
- L1 的正确结论被 L2 的错误交叉验证覆盖：不可靠的信号（Qwen3-ASR 转录）推翻了另一个信号（SenseVoice 分类），混乱标记给了用户。

**为什么中文没暴露**：SenseVoice 和 Qwen3-ASR 都面向中文优化。英文背景音场景两个模型同时失准——SenseVoice 误判 BGM、Qwen3-ASR 产生幻觉文本，"分歧"恰好触发 L2 Pass 1 所有条件，问题被放大。

**解决方案**：删除 `hallucination_filter.py` 第 314-325 行整个 Pass 1 块（含 `seg.filtered = True` 的"双确认"分支和 `seg.uncertain = True` 的"分歧"分支）。删后 L2 只剩 Pass 2 窗口词干比对——文字比文字，不再跨层污染。SenseVoice 交叉验证只走 L1（SenseVoice × Qwen3-FA 对齐，第 127-168 行）。

**2026-06-08 诊断审计**：代码核查确认 Pass 1 代码在当前 `hallucination_filter.py` 中**不存在**。L2 (`_layer2_cross_validate`, L287-401) 仅做窗口词干比对（Whisper × Qwen），无 SenseVoice 交叉验证。此问题已在早期修复中解决。

**已修** ✅


## P194 | 2026-06-08 | V3.07 HARDEN /review | worker_manager.py 非零退出一律报"内存不足"，误导用户

**问题**：worker_manager.py:93-97 — Worker 非零退出且未发送 done/error 消息时，fallback 错误统一报"处理过程异常中断。可能原因：内存不足。建议选择更小的模型"。但实际上 yt-dlp 未安装、FFmpeg 崩溃、视频文件损坏、权限不足等任何原因导致的非零退出都触发同一消息。

**根因**：P174 新增 URL 模式后，worker 退出原因多了 yt-dlp 相关的可能性，但错误消息仍假设"模型太大 → OOM"这一个原因。

**解决方案**：改为 `f"处理过程异常中断（退出码 {proc.returncode}），请查看日志排查"` — 报告实际退出码，引导用户查日志。

**2026-06-08 诊断审计**：代码核查确认 `worker_manager.py:94-96` 已改为 `f"处理过程异常中断（退出码 {proc.returncode}），请查看 debug.log 日志排查具体原因。"`，与方案一致。

**已修** ✅


## P195 | 2026-06-08 | V3.07 HARDEN /review | orchestrator.py `_build_llm_backends` 静默吞异常

**问题**：orchestrator.py:90-93 — `_build_llm_backends()` 中 `except ImportError: pass` 和 `except Exception: pass` 无条件静默丢弃所有错误。用户添加自定义 API Key 后，若 URL 格式错误、模型名不存在、或后端构造失败，无任何日志提示，后端静默不出现在翻译池中。

**根因**：开发期便利代码——加新后端时缺依赖不阻塞，但生产环境中静默失败是坏 UX。

**解决方案**：两处 `pass` 改为 `_log.warning("LLM backend %s skipped: %s", platform, e)`，至少留一条 debug.log 线索。

**2026-06-08 诊断审计**：代码核查确认 `orchestrator.py:90-93` 两处 except 块已包含 `_log.warning("LLM backend %s skipped: %s", platform, e)`，与方案一致。

**已修** ✅


## P196 | 2026-06-08 | 全局流程设计 | Bug 修复流水线缺少独立的诊断复核和方案复核

**问题**：当前 Bug 修复流水线 `① investigate → ② plan-eng-review → ③ ln-021-codegraph → ④ /review → ...` 中，`investigate` 同时输出诊断和修复方案，`plan-eng-review` 一次审查同时看诊断和方案。存在两个盲区：

1. **诊断未独立复核** — 诊断人（investigate）自己说的根因没人独立验证。如果根因找错了，后面全在错误路线上修。
2. **方案未独立复核** — 同一个 skill（investigate）出诊断又出方案，同一个 skill（plan-eng-review）审诊断又审方案。"自证其好"——介绍方案时只讲好的那点，隐藏了方案的漏洞。

**真实教训**：用户经历过大量这样的场景——修复方案被诊断人推销得很好，用户被说服→修完→测试不过或引入新问题→再查→发现当初修复方案本身有缺陷。浪费一整圈。

**现有工具箱**：
- 工程工具：`investigate`（诊断+方案）、`plan-eng-review`（工程审查）、`ln-021-codegraph`（调用图/影响面）、`/review`（diff 审查）、`test-driven-development`（写测试+修代码）、`verification-before-completion`（全量回归）
- 人格 skill：`steve-jobs-perspective`（产品/设计判断）、`elon-musk`（第一性原理/工程直觉）、`bill-gates`（系统思维/架构安全）、`feynman`（逻辑严密/可解释性）

**核心约束**：
- 人格 skill 覆盖判断力盲区（方向对吗？太复杂吗？值得修吗？），不可替代工程工具
- 工程工具覆盖技术盲区（修A坏B吗？波及谁？diff隐藏bug？），不可替代人格
- 相邻步骤不能是同一个 skill

### 方案A：诊断+方案二合一，审查分拆给不同人（Steve Jobs 推荐）

```
① investigate → 诊断+修复方案（原生行为，不阉割）

② 双签审查（并行）：
   主审 plan-eng-review → 审方案技术正确性（修法对吗？边界全吗？）
   副审 steve-jobs-perspective → 审诊断逻辑（根因真对吗？方向对吗？太复杂吗？）

③ 双签影响面（并行）：
   主审 ln-021-codegraph → 调用图/波及范围
   副审 bill-gates → 系统层面会炸吗？架构安全吗？

④ 双签 diff + 简化（并行）：
   主审 /review（全项目）→ diff 扫雷
   副审 elon-musk → 有更简单修法吗？能删代码而不是加代码吗？

⑤ test-driven-development → 写失败测试 → 写修复代码 → 测试通过

⑥ verification-before-completion → 全量回归
```

**优点**：investigate 原生行为，不阉割。工程工具主审（守门员），人格副审（魔鬼代言人）。不多步骤。
**缺点**：诊断人=方案人（同一条思维链）。审的人也同时审诊断和方案，可能审方案时忘了再审诊断。

### 方案B：诊断和方案拆成两步（用户倾向，更安全但更重）

```
① investigate(Phase 1-3 only) → 只诊断，输出根因假设，不修复

② 双签诊断复核（并行）：
   主审 plan-eng-review → 审根因找对了吗？证据充分吗？
   副审 steve-jobs-perspective → 审方向对吗？值得修吗？

③ plan-eng-review（新 session，换人）→ 基于已核准诊断，出修复方案

④ 双签方案复核（并行）：
   主审 /review → diff 审查
   副审 elon-musk → 更简单修法？

⑤ test-driven-development → 写测试 + 修代码

⑥ verification-before-completion → 全量回归
```

**优点**：诊断人不管方案，方案人不能改诊断，两个决策点独立。每一步责任清晰。
**缺点**：多一步。investigate 被阉割（原生四阶段连续流程：调查→分析→假设→实现）。步骤③还是 plan-eng-review，与②同一个 skill。

### 当前状态

**决定**：2026-06-11 采用方案A（Steve Jobs 推荐）。CLAUDE.md 已同步更新四处：
- Bug 修复流水线：①②③④ 改为双签审查
- 开发线 HEAL 阶段：同步双签
- 开发线 HARDEN 阶段：走查改为五问
- 管道文件规则：Bug 修复流水线表格同步

**✅ 已修**

---

## P197 | 2026-06-08 | 前端 | localStorage 剥离 api_key 值后覆盖磁盘，API Key 再次丢失

**问题**：用户重新打开前端后，`~/.video-subber/config.json` 中 `apiKeys` 变成空数组 `[]`。需要重新输入 DeepSeek API Key。

**根因**：P154 砍掉了 Fernet 加密（正确），但漏了第二个数据丢失渠道——前端 `saveConfig()` 存 localStorage 时故意剥离了 `api_key` 字段（安全考虑，只存 {platform, name, model, api_endpoint}），而 `loadConfig()` 在服务器 fetch 返回之前就先用 localStorage 的值覆盖了 `State.settings`。三种触发路径：

| 路径 | 触发条件 | 机制 |
|------|---------|------|
| **A** | 服务器未启动时打开页面 | localStorage 剥离版覆盖 State → `fetch('/api/config')` 失败 `.catch(()=>{})` → State 里 apiKeys 无 key 值 → 任何 UI 交互触发 `saveConfig()` → 空 key 写回磁盘 |
| **B** | 服务器启动中但 fetch 慢 | localStorage 先覆盖 State → 在 fetch 回调到达前，用户操作（模型滑块/env bar）触发 `saveConfig()` → 剥离版写回磁盘覆盖真实数据 |
| **C** | 浏览器换了/清了 localStorage | localStorage 为空 → State 初始 `apiKeys:[]` → 服务器未启动 → 同上 |

**代码位置**：
- [index.html:1032](v2/frontend/index.html#L1032) `saveConfig()` — localStorage 剥离 key 值：`apiKeys.map(k=>({platform, name, model, api_endpoint}))` 无 `api_key`
- [index.html:1035-1041](v2/frontend/index.html#L1035-L1041) `loadConfig()` — 先同步用 localStorage 覆盖 State，再异步从服务器拉完整数据

**历史关联**：P048（tier 三数据源竞态，2026-05-21）是同根因——多个数据源（localStorage/服务器/URL参数）在不同时间点写入 `State.settings`，异步回调到达顺序不确定，旧值覆盖新值。P048 修了 tier 一个字段，但没有推广到所有字段。P154（加密导致 key 丢失，2026-06-05）是另一个并发的根因（加密层），两个 bug 叠加让 key 丢了两次。

**为什么之前没发现**：
1. P048 修的是 tier 竞态，方案是删掉 tier 持久化、loadConfig 改同步——但没有审视「localStorage 剥离策略是否安全」这个更底层的问题
2. P154 的加密丢失是更频繁的触发路径（每次重启都可能变），覆盖了 localStorage 竞态的症状——加密修好后，第二个 bug 才暴露出来
3. `/review` 和 `/code-review` 都是 diff-based，只查新增/修改行。这个 bug 是两段旧代码的交互（`saveConfig` L1032 + `loadConfig` L1035）——各自单独看都没问题，只有放在一起走时序才出 bug。diff-based 审查工具系统性看不到这类问题
4. 测试覆盖的是后端 config_manager（P154 修复时加了 `test_plaintext_keys.py`），没覆盖前端 JS 的 localStorage↔服务器同步逻辑

**Steve Jobs 诊断（2026-06-08）**：

> 这不是 P154 和 P197 两个 root cause，是一个 root cause——数据没有 owner。每个字段应该只有一个 truth source。三个 source 互相覆盖的设计，不修掉这个根，还会出 P198、P199、P200。

**为什么修了三次都没根除**：
1. 每次修的是「当前这个 bug 的表象」，没人停下来问「这个设计哪里错了」
2. P048 修 tier 竞态时，正确的判断是「一个值不应该有三个数据源」——但修复局限在 tier 一个字段，没人追问 apiKeys、model、style 等其他 11 个字段是否有同样的问题
3. 用 debugger 思维修 bug（找到 symptom → 找 root cause → 修掉），而不是 architect 思维（修完后问：这个 root cause 的模式在整个系统里还出现在哪里？）
4. 每次修 bug 顺便加了新代码（fallback、保护逻辑），这些新代码变成了下一个 bug 的原材料

**Jobs 方案：三步减法（只删不增）**

```
Step 1: 给每个字段定 owner
  apiKeys → server only（敏感数据，浏览器不应存）
  其他 10 个字段 → server, localStorage 做展示缓存不是 truth

Step 2: 砍掉 loadConfig() 的 localStorage → State 覆盖
  index.html:1035-1036 — 删除:
    const raw = localStorage.getItem('video-subber-config');
    if(raw){ Object.assign(State.settings, JSON.parse(raw)) }
  改为: 服务器返回后一次性 Object.assign, catch 分支保持 State 初始值不动

Step 3: 砍掉 saveConfig() 的 apiKeys 剥离 + 不再存 apiKeys 到 localStorage
  index.html:1032 — 从:
    const safeCfg = {...cfg, apiKeys: (cfg.apiKeys||[]).map(k=>({platform,name,model,api_endpoint}))}
  改为:
    const localCfg = {...cfg}; delete localCfg.apiKeys;
    localStorage.setItem('video-subber-config', JSON.stringify(localCfg));
```

**效果**：改前三数据源互相覆盖靠异步回调顺序决胜负 → 改后每字段一个 truth source。apiKeys 只走服务器。localStorage 不再触碰敏感数据。

**原则**：
- 不新增任何文件、模块、抽象
- 不新写 fallback 逻辑
- 改动范围：`index.html` 中 `saveConfig()` 和 `loadConfig()` 共三段删除
- 验证方式：打开页面 → 输入 API Key → 保存 → 刷新 → key 还在

**Jobs 修正（第二轮）**：用户提出是否应该全删重写。Jobs 否决——

> 重写是当代码烂到无法修补时才做的事。你的代码不烂——它只是承载了已经过时的假设。把过时的部分剪掉，剩下的就是新设计。
>
> V2.03-V2.05 时期的产品是浏览器里的 Web 应用。localStorage 会丢、浏览器会换、用户可能多设备打开。所以需要 localStorage↔服务器同步层。这些假设在当时是对的。
>
> 现在 V3.07 正在往 V3.09 PySide6 桌面应用走。用户一台电脑一个窗口。localStorage 永远不会被其他浏览器访问。产品形态变了，但同步层的代码没跟着变——还在为一个已经不存在的威胁模型做防御。
>
> 砍掉那 5 行同步胶水代码就是重新设计。新的设计思路：State.settings 的 truth source 是服务器。localStorage 只缓存非敏感 UI 偏好。apiKeys 不经过 localStorage。

**决策** ⏸ 方案已锁定（三步减法，不重写）。待用户确认后按 bug 修复流水线执行

**已修** ✅（2026-06-09 TDD：loadConfig 删 apiKeys 后再 Object.assign + configReady 防竞态）


---

## P198 | 2026-06-08 | V3.07 下载 | yt-dlp format="best" 默认下载最低画质（约 480p-720p）

**问题**：`video_downloader.py:43` 使用 `"format": "best"`，这是 yt-dlp 的"最佳单文件（音画合并）"选择器。YouTube 在 1080p 及以上使用 DASH 协议，音视频流分开存储——`best` 永远选不到 1080p+ 的视频流。用户实际下载到的是 480p 或 720p 的合并文件。

**竞品做法（2026-06-08 代码核查）**：

| 竞品 | Star | 画质配置 | 默认 | 可调？ |
|------|------|---------|------|--------|
| **VideoLingo** | ⭐17,277 | `bestvideo[height<=1080]+bestaudio/best` | 1080p | ✅ |
| **Linly-Dubbing** | ⭐3,233 | `bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best` | 1080p | ✅ |
| **generate-subtitles** | ⭐808 | `bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best` | 720p | ❌ |
| **pyvideotrans** | 知名 | 不内置下载，用户提供视频 | — | — |
| **autosub** | ⭐1,969 | 不内置下载，用户提供视频 | — | — |

**方案（Jobs 审查 + 用户确认）**：

分别下载最高画质视频流 + 最高音质音频流，音频直接进转录管线（无需额外剥离音轨），最后 ffmpeg 将视频+音频+字幕合成。**不给用户画质选项——默认最高。**

```
bestvideo → 最高画质视频（无音频）→ 保留待合成
bestaudio → 最高音质音频         → 送入转录+翻译管线

ffmpeg 合成: 视频流 + 翻译音频 + 字幕 → 最终成品
```

**好处**：
1. 不做无用功：现在下载 720p 合并文件→剥离音频→扔掉视频数据。新方案只下载需要的
2. 质量层级正确：转录用最高音质音频（准确率↑），最终输出最高画质视频（用户满意↑）
3. 不引入新工具：ffmpeg 合成已在管线中（V3.03 软字幕封装），只多喂更好的视频流

**实施**：改 `video_downloader.py:43` 一行：
```python
# 旧
"format": "best",
# 新
"format": "bestvideo+bestaudio/best",
```
`/best` 降级：合并不了时回退到最佳合并文件。

**已修** ✅（2026-06-09 重构：分轨下载 video-only + audio-only，音频直送转录，最终合并 video+audio+字幕）


---

## P199 | 2026-06-08 | V3.07 转录管线 | standard 模式 SenseVoice + FA 串行执行（应并行）

> **2026-06-08 诊断审计纠正**：原描述称"四模型全部串行"——事实错误。Standard 模式只跑三个模型（Whisper、SenseVoice、FA），不跑 Qwen3-ASR（`transcriber_v2.py:384` 仅 `precise` 模式才启动千问）。需要并行的是 SenseVoice + FA 两个模型，不是三个。

**问题**：`transcriber_v2.py` standard 模式执行路径：
```
Whisper → SenseVoice（串行）→ Qwen3-FA（串行）
```
SenseVoice 和 Qwen3-FA 串行执行，但两者互不依赖——都只需要 Whisper 的输出（SV 需 segments，FA 需全文 text），不需要对方的结果。

**依赖分析**（与 findings.md 设计图一致）：

```
SenseVoice:  需要音频 + Whisper segments → Whisper 结束后即可运行
Qwen3-FA:    需要音频 + Whisper text    → Whisper 结束后即可运行
两者之间：    没有互相依赖关系（设计图 Step 3 明确标注并行）
```

**正确做法**：Whisper 结束后，SenseVoice + FA 同时启动（两个线程），等全部完成。

**代码位置**：
- `transcriber_v2.py:366-374`：SenseVoice 串行路径（`if not use_parallel`）
- `transcriber_v2.py:401-419`：FA 串行路径（standard/precise 共用）
- 两者顺序执行，中间无数据依赖

**修复方向**：
1. Standard 模式下 SenseVoice + Qwen3-FA 并行启动（合计 ~2.8GB，8GB 机器也够）
2. 不涉及 Qwen3-ASR，不需要改 producer-consumer 管道

**已修** ✅（2026-06-09 TDD：should_parallel_svfa() 独立判定，SV+FA 线程启动与 Qwen pipeline 解耦）

---

## P200 | 2026-06-08 | V3.07 转录管线 | precise 模式 Qwen3-ASR join 阻塞 SenseVoice 启动

**问题**：`transcriber_v2.py:291-293` precise 并行模式中，Whisper 转录结束后代码先 `qwen_thread.join()` 等待 Qwen3-ASR 完全结束，然后才启动 SenseVoice 和 Qwen3-FA 线程（line 335-363）。

```
当前执行：
  Whisper ████████████████
           │ (Qwen3-ASR 消费者已在跑)
  Qwen3-ASR  ████████████████████
                                │ join() ← SenseVoice 空等
  SenseVoice                        ████████
  Qwen3-FA                           ████████

正确执行：
  Whisper ████████████████
           │ (Qwen3-ASR 消费者已在跑)
  Qwen3-ASR  ████████████████████
  SenseVoice                    ████████  ← 立即启动，不等 Qwen
  Qwen3-FA                      ████████  ← 立即启动
```

**根因**：`qwen_thread.join()` 写在 SenseVoice 启动之前。应该把 SenseVoice + FA 的线程启动移到 Qwen join 之前。

**与 P199 的关系**：P199 是 standard 模式全串行，P200 是 precise 模式的局部串行瓶颈。两个问题互相独立，但根因相同——没有正确建模四个模型之间的依赖关系。

**已修** ✅（2026-06-09 TDD：should_qwen_pipeline() 独立判定，SV+FA 线程启动移到 qwen_thread.join() 之前）

---

## P201 | 2026-06-08 | 端到端实测 | FA 模型缺失导致 SenseVoice 单信号模式误杀 85% 段落

**问题**：London Vlog（带背景音乐的英文视频，precise 模式）实测，Whisper 转录 208 段，L1 过滤后只剩 31 段，177 段真人对话被误杀（如 "Hey Taylor, what time is it?"）。翻译完的字幕只剩视频最后几分钟的做饭片段，前面 20 分钟内容全部丢失。

**根因**（四级级联，与 P199/P200 共同暴露）：

1. Qwen3-ForcedAligner 模型从未下载（~1.84GB）。`env_check.py` 未检测 FA 状态，`is_qwen_aligner_available()` 只查 Python 包不查模型文件。
2. FA 缺失 → `aligner_result` 始终为 None → L1 退化为 P183 单信号兜底模式。
3. SenseVoice 对带 BGM 的 vlog 天然误判——分不清"纯背景音乐"和"人声+背景音乐"。22 分钟视频仅产出 5 个粗粒度标签（BGM:4, Speech:1），被标记为 BGM 的时间段跨度极大。
4. 单信号模式下 L1 无条件信任 SenseVoice → 所有标 BGM 的段落硬丢弃。208 → 31 段，L2 的 46 个验证窗口里 40 个变成空的（内容已被 L1 杀光，无东西可比）。

**同一视频的前一天测试为什么没暴露**：Jun 7 用 large-v3 跑同一视频，464 段，SenseVoice 产出 BGM:3/Speech:2。因为分段更细，`_apply_sv_tags` 的 ≥50% 重叠匹配结果不同——恰好大部分段落没落在 BGM 标签覆盖区，L1 触发 0 次。两次结果的差异纯粹来自 SenseVoice 5 个粗标签在 200-464 段上的随机分布——这一次运气好，下一次全杀。这恰恰证明了单信号模式不可靠。

**主修复**：下载 Qwen3-ForcedAligner 模型（~1.84GB，HuggingFace `Qwen/Qwen3-ForcedAligner-0.6B`，走 `hf-mirror.com` 镜像）。FA 就位后 L1 自动回到双信号路径——SV 说 BGM 但 FA 找到了词对齐 → 保留段落，只有两个法官一致确认才丢弃。

**防御纵深**（P183 改进，FA 下载后不再触发，优先级降低）：P183 的单信号兜底应改为软标记（只标 `[?]` 不硬丢弃）。SenseVoice 单独判断不足以作为硬过滤依据。但 FA 就位后此路径不会触发，可在后续版本中作为安全网改进。

**环境检测补漏**：`env_check.py` 需新增 `check_qwen_aligner()` 检测项，检查模型文件是否已下载（非仅检查 Python 包）。

**已修** ✅（2026-06-09 TDD：is_qwen_aligner_available() 用 AutoConfig local_files_only 检查，env_check 新增 check_qwen_aligner。注：仅检测，未下载模型）

---

## P202 | 2026-06-09 | /review 全项目审查 | _friendly_error 错误提示"一刀切"——含 memory 的错误全报内存不足

**问题**：`worker.py:591-605` 的 `_friendly_error()` 用 `"Memory" in raw or "memory" in raw` 匹配错误。任何错误信息只要包含 "memory" 这个词（如 shared_memory、memory_map、yt-dlp 内部报错含 memory allocation），全部被翻译为"内存不足，无法处理该视频"。用户被误导。

**根因**：字符串匹配太宽泛。"memory" 出现在很多非 OOM 的错误消息里。

**解决方案**：改用更精确的匹配模式：只匹配 `"MemoryError"` 或 `"Cannot allocate memory"` 或 `"killed"` + `"memory"` 组合，过滤掉无关的 memory 提及。

**已修** ✅（2026-06-09 TDD：_friendly_error 改为精确关键词匹配 memoryerror/out of memory/cannot allocate memory）

---

## P203 | 2026-06-09 | /review 全项目审查 | 文件归档覆盖旧版本——归档文件名固定，重复处理同一视频丢失上次结果

**问题**：`worker.py:549-565` 保存处理结果时，如果输出文件夹已有同名文件，先 `.rename()` 为归档文件名。但归档文件名是固定的（`{视频名}_{上下文/逐段}_zh.mp4`），如果上次归档的文件还在，本次 `.rename()` 直接覆盖，用户丢失之前的处理结果。

**根因**：归档命名方案不含时间戳或序号，无法区分不同次处理。

**解决方案**：归档文件名加时间戳后缀，如 `{视频名}_{模式}_zh_20260609_143022.mp4`，避免覆盖。

**已修** ✅（2026-06-09 TDD：_archive_path() 加 _%H%M%S 时间戳后缀）

---

## P204 | 2026-06-09 | FA 模型下载 | 依赖链发现——FA 模型不是独立 transformers 模型，需 qwen-asr 包注册模型类型

**问题**：尝试用 `AutoModel.from_pretrained("Qwen/Qwen3-ForcedAligner-0.6B")` 直接下载 FA 模型，报 `KeyError: 'qwen3_asr'`。无论装 transformers 5.9.0、5.10.2（最新 release），还是从 GitHub main 分支装 dev 版，全部不认识 `qwen3_asr` 这个模型类型。查 transformers 源码的 `models/` 目录，确认所有版本（包括最新 dev）的模型列表里都没有 `qwen3_asr`。

**根因**（2026-06-09 阅读模型 README 后确认）：

FA 模型**不是**通过 `transformers.AutoModel.from_pretrained()` 直接加载的。它依赖 Qwen 官方发布的 `qwen-asr` Python 包来注册模型类型。

两个关键事实：
1. Qwen3-ForcedAligner 的 `config.json` 中 `model_type: "qwen3_asr"`，`architectures: ["Qwen3ASRForConditionalGeneration"]`，`transformers_version: "4.57.6"`——但这个 `qwen3_asr` 类型**不是 transformers 内置的**，而是 `qwen-asr` 包注册进去的
2. 模型 README 明确写了安装方式：`pip install -U qwen-asr`，然后通过 `qwen-asr` 包的 API 加载模型，不是直接调 transformers

**依赖冲突叠加（三轮才通）**：

下载前发现三层坑：

| 第几坑 | 包名 | 它要的 tokenizers 版本 | 实际情况 |
|--------|------|----------------------|---------|
| 1 | transformers 5.9.0（旧） | `0.22.0 ~ 0.23.0` | 当前装的是 0.23.1 → 报错 |
| 2 | qwen3-asr-mlx（已装） | `>= 0.23.0` | 降级 tokenizers 后它不干 |
| 3 | transformers 全版本 | 无 `qwen3_asr` 支持 | 升级到最新 dev 版也不认 |

解决路径：
```
tokenizers 0.23.1 → 降到 0.22.2（满足 transformers）
transformers 5.9.0 → 升到 5.10.2（看最新版是否支持 qwen3_asr）→ 不支持
transformers 5.10.2 → 从 GitHub 源码装 dev 版 → 仍不支持
最终发现：必须装 qwen-asr 包，它注册 qwen3_asr 类型后 transformers 才认识
```

**当前项目的 `qwen_aligner.py` 问题**：

项目代码 `v2/backend/core/qwen_aligner.py:47` 用的是：
```python
self._model = AutoModel.from_pretrained(model_id, ...)
```
这条路径在没有 `qwen-asr` 包的情况下**永远跑不通**。正确做法要么：
- 装 `qwen-asr` 包（它会注册 `qwen3_asr` 到 transformers），然后用 `AutoModel` 加载
- 或者改用 `qwen-asr` 包自己的 API 加载模型

**打包参考（重要）**：

为 Windows 用户打包时，FA 模型依赖链是：
```
qwen-asr 包（注册模型类型）
    ↓ 安装后
transformers.AutoModel.from_pretrained("Qwen/Qwen3-ForcedAligner-0.6B") 才认识它
    ↓ 首次调时
HuggingFace 自动下载模型文件（~1.84GB）到 HF_HOME 缓存
```

打包时需要：
1. 安装 `qwen-asr`（不只是 transformers）
2. 解决 tokenizers 版本兼容（qwen3-asr-mlx 要 >=0.23.0，transformers 要 0.22.0-0.23.0——选择 0.23.0 刚好满足两边，或确认 qwen3-asr-mlx 在 Windows 上不存在，只处理 transformers + qwen-asr 的兼容）
3. FA 模型文件 ~1.84GB，需提前下载或通过镜像加速（国内用 hf-mirror.com）
4. `env_check.py` 的 `check_qwen_aligner()` 目前只查 `transformers.AutoConfig.from_pretrained(local_files_only=True)`——这意味着模型必须先手动下载到缓存，否则始终返回"不可用"。需要在首次启动时自动触发下载或给明确指引

**✅ 已修**（`qwen_aligner.py` 已 `import qwen_asr`，`is_qwen_aligner_available()` 已修正为两步检测，AutoConfig 可正确识别 `qwen3_asr` 类型）

---

## P205 | 2026-06-09 | FA 模型下载 | qwen_aligner.py 缺少 `import qwen_asr.core.transformers_backend`，env_check 返回假阴性

**问题**：FA 模型已下载到 HuggingFace 缓存（1.7GB），`qwen-asr` 包也已安装，但 `is_qwen_aligner_available()` 仍然返回 `False`，报 `KeyError: 'qwen3_asr'`——transformers 不认识这个模型类型。

**根因**：`qwen-asr` 包的 `__init__.py` 只导出了 `Qwen3ASRModel` 和 `Qwen3ForcedAligner` 两个类，**没有导入 `core.transformers_backend` 子模块**。模型类型注册代码在 `transformers_backend/__init__.py` 里（导入时会自动执行 `AutoConfig.register("qwen3_asr", Qwen3ASRConfig)` 等注册逻辑），但因为这个子模块未被导入，注册从未触发。

`qwen_aligner.py` 的 `is_qwen_aligner_available()` 调用 `AutoConfig.from_pretrained("Qwen/Qwen3-ForcedAligner-0.6B", local_files_only=True)` 时，transformers 查找 `CONFIG_MAPPING["qwen3_asr"]` → KeyError → 返回 False。这是**假阴性**——模型文件实际存在，只是类型没注册。

**解决**：在 `qwen_aligner.py` 开头加：
```python
try:
    import qwen_asr.core.transformers_backend  # noqa: F401
except ImportError:
    pass
```
`import` 语句执行时自动触发子模块 `__init__.py` 中的模型类型注册。之后 `AutoConfig.from_pretrained()` 能正确识别 `qwen3_asr` → `is_qwen_aligner_available()` 返回 True。

**教训**：`qwen-asr` 包的模型类型注册是**惰性的**（不自动触发），依赖上层代码显式 import `transformers_backend` 子模块。这是一种脆弱的设计——`is_qwen_aligner_available()` 本身是"检查是否可用"，但它依赖一个要先 import 才能用的模块，形成了**循环悖论**：检查模型可用 → 需要先注册类型 → 注册类型需要 import → import 需要包已安装 → 检查包安装又是检测的一环。

更稳健的做法是分两步检测：
1. 先检查 `qwen-asr` 包是否可导入（如果不可导入 → 直接 False）
2. 导入后注册类型，再检查模型文件是否存在

**已修** ✅（`qwen_aligner.py` 已加入 import，`is_qwen_aligner_available()` 返回 True）

---

## P206 | 2026-06-09 | FA 模型加载 | 争议——项目在用 AutoModel 修路，但官方 API 是 Qwen3ForcedAligner（乔布斯审查发现）

**来源**：乔布斯（steve-jobs-perspective Skill）联网查阅 Qwen3-ASR GitHub README 和 PyPI 后提出。

**争议核心**：

P205 的修复路径是：`import qwen_asr.core.transformers_backend` → 注册 `qwen3_asr` 类型 → `AutoModel.from_pretrained()` 能加载模型。

但 Qwen 官方文档（[GitHub README](https://github.com/QwenLM/Qwen3-ASR)）唯一列出的加载方式是：

```python
from qwen_asr import Qwen3ForcedAligner

model = Qwen3ForcedAligner.from_pretrained(
    "Qwen/Qwen3-ForcedAligner-0.6B",
    dtype=torch.bfloat16,
    device_map="cuda:0",
)

results = model.align(audio="...", text="...", language="Chinese")
```

**不是** `AutoModel.from_pretrained()`。不是 transformers 的 Auto API。

**为什么这是一条歧路**：

即使 `import transformers_backend` 注册类型后 `AutoModel` 能加载模型权重，`AutoModel` 返回的是裸 `Qwen3ASRForConditionalGeneration` 实例——它没有 `model.align()` 方法。项目当前 `qwen_aligner.py` 的 `align()` 方法自己手写了前向传播和 token 解码逻辑（~100 行），等于**自己重写了 Qwen 官方已经封装好的功能**。

两条路对比：

| | P205 路径（当前） | 官方 API |
|---|---|---|
| 加载方式 | `AutoModel.from_pretrained()` + 手动 import backend | `Qwen3ForcedAligner.from_pretrained()` |
| 对齐调用 | 项目手写的 `align()` 方法 | `model.align(audio, text, language)` |
| 依赖 import hack | 需要 `import qwen_asr.core.transformers_backend` | 不需要 |
| 与官方同步 | 官方 API 变了项目要手动追 | 自动跟随 qwen-asr 版本 |

**乔布斯带联网查到的额外事实**：

| # | 事实 | P204/P205 是否记录 |
|---|------|-------------------|
| 1 | `qwen-asr==0.0.6` 精确绑定 `transformers==4.57.6`（不是 >=，是 ==） | ❌ 未记录 |
| 2 | `qwen-asr` 额外依赖 gradio、flask、nagisa、soynlp——桌面应用不需要 | ❌ 未记录 |
| 3 | 官方推荐中国大陆用 ModelScope 下载，不是 hf-mirror | ❌ P204 写的是 hf-mirror |
| 4 | 官方有 Docker 镜像，可绕开依赖地狱 | ❌ 未记录 |
| 5 | `qwen3-asr-mlx` 不需要 transformers——与 qwen-asr 的唯一共公依赖是 tokenizers==0.23.0 | ❌ 未记录 |

**tokenizers 精确交集**：

```
transformers==4.57.6 → tokenizers >=0.22.0, <=0.23.0
qwen3-asr-mlx        → tokenizers >=0.23.0
                        交集 = 0.23.0（唯一解）
```

**决定**：待定。两个选项：
- **A**：改用官方 `Qwen3ForcedAligner.from_pretrained()` + `model.align()`，删除手写的 `align()` 逻辑。正确但需要改 `qwen_aligner.py`
- **B**：保持 P205 方案（import backend + AutoModel + 手写 align）。能跑但从此与官方 API 分叉

**✅ 已修**（`qwen_aligner.py:57` 已改用 `Qwen3ForcedAligner.from_pretrained()` + 官方 `model.align()` API，不再手写 forward pass。AutoModel 路径已废弃）


## P207 | 2026-06-09 | 🔴 blocking FA 模型（校对员）静默造假——对齐失败后做除法生成均匀假时间戳

**来源**：2026-06-09 实测发现。用 Gradio 内置测试音频 `test_audio.wav`（1.068s）+ 文本 "hello world this is a test" 调 `QwenAligner.align()`。

**现象**：

日志输出：
```
WARNING: Qwen3-FA 对齐失败，降级到均匀分配: _forward_unimplemented() got an unexpected keyword argument 'input_ids'
```

返回结果：
```
hello  0.000s → 0.178s
world  0.178s → 0.356s
this   0.356s → 0.534s
is     0.534s → 0.712s
a      0.712s → 0.890s
test   0.890s → 1.068s
```

**每个词精确地 0.178 秒。** 6 个词 × 0.178s = 1.068s。这是 1.068 ÷ 6，不是对齐。

**根因**：

`qwen_aligner.py` 的 `align()` 方法有两条代码路径：

1. **第一条路（第 91-155 行）**：尝试真正干活。用 `AutoModel.from_pretrained()` 加载裸 `Qwen3ASRForConditionalGeneration` 实例，手写 forward pass 调 `self._model(**inputs)`。传了一个模型不认识的参数 `input_ids` → 模型拒收 → 抛异常。

2. **第二条路（第 157-177 行）**：兜底方案。catch 异常 → 印一条 warning → `per_word = duration / len(words)` → 每个词一样长。

**为什么这是严重问题**：

- **不是 crash**。程序不崩、不报错。用户和上层代码完全不知道对齐失败了。
- **不是降级**。均匀分配不是"不够好的对齐"，是**根本没有对齐**。每个词的时间戳是假数据。
- **污染 L1 双信号交叉验证**。L1 需要两个独立信号——SenseVoice（声学角度）和 FA（语言学角度）——互相验证"这段音频是不是真有人在说话"。FA 在一切段上都返回"对齐成功"（因为它从不查字典，只做除法），双信号变成单信号。任何被 SenseVoice 漏掉的非人声段都会静默通过 L1。
- **`is_qwen_aligner_available()` 说"可用"**。它只检查模型文件在不在硬盘上，不检查模型产出的是不是假数据。整条检测链路给了绿灯，但灯后面是悬崖。

**验证数据**：

| 指标 | 实际对齐 | FA 造假输出 |
|------|---------|------------|
| "hello" 时长 | 真实时长（不确定） | 0.178s（精确除法） |
| "test" 时长 | 真实时长（不确定） | 0.178s（精确除法） |
| 词间差异 | 有大有小 | 全部相同 |

**解决方案**：

换官方 API。`qwen-asr` 包已经装了，官方接口就在那里：

```python
from qwen_asr import Qwen3ForcedAligner

model = Qwen3ForcedAligner.from_pretrained("Qwen/Qwen3-ForcedAligner-0.6B")
results = model.align(audio="audio.wav", text="hello world", language="Chinese")
```

改 `qwen_aligner.py`：
1. `__init__`：`AutoModel.from_pretrained()` → `Qwen3ForcedAligner.from_pretrained()`
2. `align()`：删掉 100 行手写 forward pass + 除法 fallback，换成 `self._model.align(audio_path, text, language)`

**影响范围**：一个文件（`qwen_aligner.py`），不改其他文件。API 签名不变，调用方无感知。

**与 P206 的关系**：P206 记录了"两条路选哪条"的架构争议。P207 记录了**实测确认**——当前路径已证实产出假数据。P206 是理论争论，P207 是实证结论。

**关联**：[[P206]]（架构争议）、task_plan.md P207（执行清单）、L1 双信号交叉验证（受污染方）

**Jobs 解决方案（2026-06-09）**：

核心思路：只做减法，不做加法。把冒牌货换成正品。Qwen 官方已经把路修好了——不需要自己铺路，只需要走上去。

**已修** ✅（2026-06-09 TDD 修复。手写 forward pass + 三条造假 fallback → 官方 `Qwen3ForcedAligner.from_pretrained()` + `model.align()`。279→200 行，8 新增测试，419 全量零回归）

**Jobs 后续审查发现（2026-06-09）**：

> 造假停了，但质量控制还没开始。你们把冒牌货换成了正品，但还没设质检员。

三个遗留问题 → [[P212]]、[[P213]]、[[P214]]

---

## P212 | 2026-06-09 | 🟡 FA 对齐精度无真实语音验证——Jobs 审查发现 #1

**来源**：2026-06-09 Steve Jobs 审查 P207 修复。

**问题**：P207 的反造假测试用 440Hz 正弦波做测试音频，验证了时间戳不再是均匀分配的。但**没有用真实人声录音验证对齐器的时间戳是否准确**。正弦波不是人说话——对齐器在正弦波上返回的"非均匀"时间戳仍然是垃圾数据，只是换了一种形状的垃圾。

**Jobs 原话**：就像你问一个人"现在几点了"，他不再每次都回答"3点整"，而是真的看了看手表——但他戴的是坏表。

**缺少的测试**：一段真实人声录音（有人说 "hello world this is a test of the alignment system"），人工标注每个词的起始时间。跑对齐器。比较偏差。如果偏差超过 200ms → 测试失败。

**当前状态**：知道对齐器不再造假，不知道对齐器是否准确。

**解决方案**：
1. 录一段短音频（真人说 2-3 秒的话），用 Audacity 手动标注词边界（gold standard）
2. 写测试：P207 对齐器输出 vs 人工标注，偏差 < 200ms
3. 如果官方模型不准 → 记入问题库，至少知道准确度边界

**关联**：[[P207]]（FA 官方 API 替换）、[[P213]]（词数漂移）、[[P214]]（静默降级）

**❌ 已关闭（2026-06-11 换库自然解决）**。换 MLX ASR 库后外部 FA 调用（`QwenAligner` + `align_segments`）被删除——千问内部 FA 已出词级时间戳，L1 直接用 Qwen segments 做双信号验证。FA 准确性验证并入启动健康检查（心机音频一次性跑三个模型）。详见 `MLX-ASR-换库方案.md` §七「删减清单」。关联关闭：[[P179]]

---

## P213 | 2026-06-09 | 🟡 `apply_aligner_timestamps` 词数假设漂移——Jobs 审查发现 #2

**来源**：2026-06-09 Steve Jobs 审查 P207 修复。

**问题**：`apply_aligner_timestamps()` 第 268 行（旧行号）用 `len(txt.split())` 计算每段 segment 的词数，然后按这个词数去切分 `aligned_words` 列表。这假设 Python 的 `str.split()` 词数 = FA 模型输出的对齐词数。

**风险**：官方 Qwen 模型有自己的 tokenizer。它可能把 "don't" 拆成 ["don", "'t"]，把中文句子拆成跟 `str.split()` 完全不同的粒度。一旦词数对不上，第二个 segment 开始所有的对齐时间戳全部漂移。

**历史**：这个 bug 在旧的手写对齐器时代就一直存在，但因为旧对齐器的 forward pass 从来没成功过（始终走 fallback），所以从未被触发。P207 让对齐器第一次真正工作了——这个 bug 现在有机会造成实际损害。

**Jobs 原话**：这不是你引入的 bug，是之前就有的。但之前那个手写对齐器根本不工作，所以这个 bug 从来没被触发过。现在对齐器真的在干活了——这个 bug 第一次有机会造成损害。

**当前状态**：无保护。词数不匹配时静默漂移。

**解决方案**：
1. `apply_aligner_timestamps()` 末尾加断言/检查：`word_idx != total_words` → 打 WARNING 日志
2. 或者：按文本相似度匹配而非按词数切分（更准但更复杂）

**修复**：`apply_aligner_timestamps()` 改为按时间范围匹配（非词数切分），新增 `unmatched` 计数器，未匹配段打 WARNING `"P213: %d/%d 段未匹配到对齐词，保留原始时间戳"`。2026-06-10 TDD 修复。

**已修** ✅

**关联**：[[P207]]（FA 官方 API 替换）、[[P212]]（精度验证）、[[P214]]（静默降级）

---

## P214 | 2026-06-09 | 🟡 FA 对齐失败时静默降级——Jobs 审查发现 #3

**来源**：2026-06-09 Steve Jobs 审查 P207 修复。

**问题**：`transcriber_v2.py` 第 433-434 行，FA 对齐失败时：

```python
except Exception as e:
    _log.warning("Qwen3-FA 对齐失败，保留原始时间戳: %s", e)
```

对齐失败 → 写一条 debug 日志 → 继续用 Whisper 的粗时间戳。用户拿到的字幕时间轴精度从「词级精确」降到了「句级近似」，但**用户完全不知道**。

**Jobs 原话**：这是"静默降级"。比之前好（至少时间戳是真的 Whisper 时间戳，不是编的），但仍然是静默的。

**对比旧行为**：旧代码对齐失败 → 静默返回假均匀时间戳。新代码对齐失败 → 静默返回 Whisper 粗时间戳。两者都是静默的，区别是新代码不造假。

**当前状态**：用户无感知。Log 文件里有 warning，用户不会去看。

**解决方案**：
1. 最小方案：对齐失败时给 segments 打一个标记（如 `seg._fa_aligned = False`），L1/L2 过滤时可以据此调整策略
2. 或者：在 `align()` 返回值中加质量标记字段（如 `{"word": ..., "start": ..., "end": ..., "_quality": "aligned"}` vs `"_quality": "degraded"`）
3. 前端：未来 V3.09 可以在进度详情中显示"⚠ 词级对齐失败，使用粗时间戳"

**关联**：[[P207]]（FA 官方 API 替换）、[[P212]]（精度验证）、[[P213]]（词数漂移）

**改什么**：只改一个文件 `qwen_aligner.py`。其他所有文件不动（QwenAligner 对外接口保持不变）。

**修复**：`transcribe()` 新增 `_fa_quality` 变量跟踪 FA 对齐状态（`"official"` / `"fallback"` / `"not_attempted"`），赋给每个 segment 的 `seg._fa_quality`。降级时打 WARNING `"⚠ 词级对齐未成功，字幕使用 Whisper 粗时间戳"`。2026-06-10 TDD 修复。

**已修** ✅

**第一步：换加载方式。** `__init__` 里 `AutoModel.from_pretrained()` → `Qwen3ForcedAligner.from_pretrained()`。`from qwen_asr import Qwen3ForcedAligner` 替换 `from transformers import AutoModel, AutoProcessor`。

**第二步：换对齐方式。** `align()` 里删掉 100 行手写 forward pass（用 `<timestamp>` 标记包词、调 `self._model(**inputs)`、从 logits 猜时间戳），换成一行 `self._model.align(audio_path, text, language)`。

**第三步：删掉除法 fallback。** 当前代码对齐失败后偷偷做 `duration / len(words)` 返回假数据，只印 warning 不报错。这个 fallback 必须删。宁可没数据（让 L1 正确收到"校对员未确认"），不用假数据（让 L1 以为校对员确认了所有段）。造假的结果是用假结果做真决策。

**第四步：加薄薄的格式适配层。** 官方 `model.align()` 返回的字段名可能与现有格式有差异（比如 `"text"` vs `"word"`）。在 `align()` 内部统一转换为 `[{"word": str, "start": float, "end": float}]`。这不是 fallback——这是适配层，5 行代码。

**第五步：验证。** 用同上测试音频 + "hello world this is a test" 再跑一遍。旧假结果：6 个词每个精确 0.178 秒。新真结果：6 个词时长不等。不一样长 = 对了。

**不改的东西**：
- transcriber_v2.py（并行/串行两处调用）→ 不改，QwenAligner 外壳不变
- hallucination_filter.py（L1 双信号）→ 不改，只收到真数据而非假数据
- `is_qwen_aligner_available()` 逻辑 → 检查方式从 AutoConfig 换成 `Qwen3ForcedAligner.from_pretrained()` 的文件检测
- `apply_aligner_timestamps()` → 不改，输入格式不变
- `extract_segment_time()` → 不改

**风险提示**：改完后跑全量 419 测试确认零回归。预计 30 分钟。

**⏸ 未修**

**教训 · 三条规则（Jobs 2026-06-09）**：

> P207 的根因不是一行代码写错了——是三个环节各漏了一道闸门：加模型时没看官方文档、加载后没做真活测试、失败后被兜底代码静默掩盖。以下三条规则防止下次重蹈覆辙。

| # | 规则 | P207 怎么犯的 |
|---|------|-------------|
| 1 | **加新模型前，先去模型作者的 GitHub 仓库看 README**。找有没有官方 Python 包、有没有专门的加载方式。不要默认所有模型都用 AutoModel 加载。 | 看了 HuggingFace 页面（写的是 AutoModel），没看 Qwen GitHub（写的是 Qwen3ForcedAligner） |
| 2 | **模型加载后做"真活测试"**。用一段已知的测试数据跑一遍，检查输出是不是真实的——不只是格式对不对，是结果本身有没有被偷偷替换成假数据（如除法均匀分配）。 | `is_qwen_aligner_available()` 只查文件在不在，不查结果真不真 |
| 3 | **降级方案必须在用户界面上可见**。如果主方案失败、走了备用方案——这件事必须出现在用户能看见的地方。如果备用方案在伪造数据（如除法兜底），不允许存在——直接报错。 | warning 藏在 debug.log 深处，除法假数据静默返回 |

**关联**：全局 CLAUDE.md「新 AI 模型引入规则」


## P215 | 2026-06-09 | 🟡 `_quality: "aligned"` 名不副实——暗示质量已验证，实际只表达了数据来源（Jobs 审查发现 #4）

**来源**：2026-06-09 Steve Jobs 审查 P207 修复后代码。

**问题**：`qwen_aligner.py` 第 112 行，格式转换层给每个对齐词加了 `"_quality": "aligned"`。这个名字暗示"对齐结果经过了质量验证"——但实际上没有。官方 API 不返回置信度，代码没有做任何质量检查。`"aligned"` 只是在说"我们调了官方 API 拿到了输出"。

用户（或下游的 L1 双信号安检）看到 `"aligned"` 会以为这个时间戳是经过确认的。但实际上你无法判断"模型精确找到了词的位置"和"模型在瞎猜，只是这次没做除法"。

**解决方案**：改一个字。`"aligned"` → `"official"`。`"official"` 说的是数据来源——"这是官方 API 的输出"。坦诚，不假装有质量控制。同时给降级路径（FA 失败回退 Whisper 时间戳时）加 `"_quality": "fallback"` 标记，让下游能区分两种来源。

**影响范围**：一行（`qwen_aligner.py:112`）。改名不影响任何下游代码——`_quality` 字段目前无消费方。

**关联**：[[P207]]、[[P214]]

**已修** ✅ `_quality` → `"official"`


## P216 | 2026-06-09 | 🟡 FA 对齐成败用户无感知——成功/失败都只在内部日志里，进度消息和完成摘要不含此信息（Jobs 审查发现 #5）

**来源**：2026-06-09 Steve Jobs 审查 P207 修复后代码。

**问题**：FA 对齐成功 → `_log.info("对齐完成")`。FA 对齐失败 → `_log.warning("对齐失败")`。两者都只在内部日志里。用户通过进度条和任务完成摘要看不到 FA 是否成功。

对齐精度是产品体验的一部分——FA 成功意味着字幕时间轴是词级精度的，FA 失败意味着回退到 Whisper 粗时间戳。用户应该知道这个差别。

**涉及位置**：
- `transcriber_v2.py:368` 并行路径 FA 失败
- `transcriber_v2.py:434` 串行路径 FA 失败
- `transcriber_v2.py:413` 并行路径 FA 成功
- `transcriber_v2.py:430` 串行路径 FA 成功

**解决方案**：
- FA 成功时，通过进度消息追加"字幕已精细对齐"
- FA 失败时，通过进度消息追加"对齐未成功，使用速记员原始时间戳"

两行消息。不增加复杂度。

**关联**：[[P207]]、[[P214]]、[[P215]]

**已修** ✅ `_fa_holder["_quality"]` 标记 in transcriber_v2.py


## P217 | 2026-06-09 | 🟢 两个 FA 可用性测试 mock 未适配新代码（测试适配）

**来源**：P207 修复后 `test_p201_qwen_aligner_check.py` 和 `test_p207_fa_official_api.py` 各有一个测试失败。

**问题**：`is_qwen_aligner_available()` 新增了 `_has_qwen_asr_package` 前置检查→测试的 mock 设置未适配新路径，导致断言失败。代码本身正确，只是测试的 mock 没跟上代码变更。

**解决方案**：两个测试的 mock setup 更新以匹配新检查链。

**关联**：[[P207]]

**已修** ✅ mock qwen_asr module in test setup


## P218 | 2026-06-09 | Web UI 环境检测 | 声音分类/交叉验证/说话人检测/对齐器四个模块显示"未安装"

**问题**：Web UI 环境栏显示"声音分类模块未安装""交叉验证模块未安装""说话人检测模块未安装""Qwen 对齐器未安装"，四个红点。

**原因**：uvicorn 后端进程是用系统 Python（`/opt/homebrew/Cellar/python@3.13/...`）启动的，系统 Python 里没有任何项目依赖包。所有包都在项目 venv（`v2/.venv/`）里，但启动时没激活 venv。`env_check.py` 的 `check_sensevoice()` / `check_qwen_asr()` / `check_resemblyzer()` / `check_qwen_aligner()` 都是 `import` 检测，import 失败就返回 `{"ready": False}`。

**解决方案**：
1. 杀旧进程，用 venv Python 重启 → 六个模块全绿
2. 创建 `v2/run.sh` 启动脚本，写死 venv Python 路径，防止再走错门

**已修** ✅

**教训**：成熟项目应提供启动脚本锁定 Python 路径，避免"忘了激活 venv"这类低级错误。`pyproject.toml` 中缺少 `funasr` 依赖声明（venv 里手动装的），重建环境时可能漏装。


## P219 | 2026-06-09 | codegraph 审计 | 6 个函数复杂度过高，需在 V3.08 拆分

**来源**：`ln-021-codegraph` 全局审计发现。

**问题**：以下 6 个函数复杂度过高，维护和理解困难：

| # | 文件 | 函数 | 复杂度 | 说明 |
|---|------|------|--------|------|
| 1 | [transcriber_v2.py](v2/backend/transcriber_v2.py#L167) | `transcribe` | 41 | 🔴 最高。转录主入口，混合了模型加载、Whisper 转录、Qwen 交叉验证、SenseVoice 声音分类、FA 对齐、结果合并——一个函数干了 6 件事 |
| 2 | [sensevoice_runner.py](v2/backend/core/sensevoice_runner.py#L142) | `run_sensevoice` | 22 | 声音分类执行器 |
| 3 | [hallucination_filter.py](v2/backend/core/hallucination_filter.py#L287) | `_layer2_cross_validate` | 19 | L2 交叉验证核心 |
| 4 | [qwen_asr_runner.py](v2/backend/core/qwen_asr_runner.py#L535) | `run_qwen_asr` | 19 | Qwen ASR 执行器 |
| 5 | [orchestrator.py](v2/backend/translation/orchestrator.py#L665) | `translate_with_orchestrator` | 17 | 翻译调度器 |
| 6 | [subtitle_style.py](v2/backend/subtitle_style.py#L13) | `build_ass_styled` | 16 | ASS 字幕样式构建 |

**影响**：
- 改一个功能容易不小心影响另一个（耦合太紧）
- 新人看懂 `transcribe()` 需要读 200+ 行，心智负担大
- 写单元测试困难——一个函数干太多事，mock 要设满

**解决方案**：逐个拆分，每个函数只做一件事。`transcribe()` 优先级最高（复杂度 41，远超标号 10 的健康线）。搬家到 v3/ 之后执行，纯重构，不改行为，不增删功能。

**关联**：[[P132]] V3.08 目录整理

**⏸ 未修**

---

## P220 | 2026-06-09 | 🔴 FA 对齐器长音频 O(n²) 内存爆炸——Transformer 自注意力天花板

**来源**：2026-06-09 P207 修复后首次实战测试。22 分钟英文 vlog，Whisper 转录 402 段，FA 对齐器用官方 API 加载成功，但在 `model.align()` 内部崩了。

**现象**：
```
Qwen3-FA 并行对齐失败: Invalid buffer size: 18.82 GiB
```

**根因**：不是音频加载问题，不是内存泄漏。是 PyTorch 的 `scaled_dot_product_attention` 崩了。

完整调用链：`Qwen3ForcedAligner.align()` → `self.model.thinker()` → transformer 自注意力层 → `torch.nn.functional.scaled_dot_product_attention` → 崩。

FA 对齐器把整个 22 分钟音频（~68,000 个时间帧）+ 全部 15,655 个字符文本（~5,000 个 token）一次性塞进自注意力矩阵。73,000² = 53 亿个元素 × 4 字节 ≈ 21 GB——接近 18.82 GiB。

这是 Transformer 注意力机制 O(n²) 的物理天花板——不是 bug，不是参数设错了。每个字在做决定的时候都要看一眼所有其他字和所有时间帧。这张交叉引用表的大小随输入长度呈平方增长。

**为什么短音频不崩**：TDD 测试用 2.5 秒音频——约 130 个 token——130²=16,900 元素 = 几十 KB。随便跑。

**为什么转录不崩**：转录是顺序的——听一句写一句，不需要回头看。对齐是交叉的——每个字 × 每个时间点。

**解决方案（用户提出，2026-06-09）**：
不按时间分段（SenseVoice 的 300 秒一刀切），按 Whisper 的句子分段。

Whisper 已经给出了 402 个句子段落，每段带时间戳（如"第 10-15 秒，hello world this is a test"）。每段独立喂给 FA 对齐器——5 秒音频 + 一句话文字。每段的注意力矩阵只有几百格，跟 TDD 测试一样小。402 段逐句处理，内存不超 2GB。全部跑完拼回去。

好处：句子边界和语言边界对齐，不会一个词被切成两半。每个 chunk 独立，以后还能并行。

**已修** ✅（2026-06-09 TDD 修复。新增 `align_segments()` 函数，按 Whisper 句子逐段对齐。每段 ~5 秒，402 段内存 <2GB。`transcriber_v2.py` 并行+串行两条路径均已切换。421 全量零回归）

**关联**：[[P207]]（FA 官方 API 替换）、[[P212]]（FA 精度验证）、[[P213]]（词数漂移）、[[P214]]（静默降级）

---

## P221 | 2026-06-09 | 🔴 Qwen ASR MLX 版被传了不认识的参数——线内联引入的新 bug

**来源**：2026-06-09 P207 `_qwen_gen_kwargs` 函数内联时引入。

**现象**（第三次测试，12:30）：
```
TypeError: Qwen3ASR.transcribe() got an unexpected keyword argument 'return_time_stamps'
```
5 个音频 chunk 全部因此跳过，精准模式退化为标准模式。

**根因**：旧代码的处理方式是——只在非 MLX 环境下才传 `return_time_stamps`，MLX 环境下根本不提这个参数。因为千问 ASR 的苹果芯片专版（MLX 版）不认识 `return_time_stamps`——连 `False`（"不要时间戳"）都不认识。

内联时写出了 `return_time_stamps=not _USE_MLX`。MLX 环境下 `_USE_MLX=True`，所以 `not True` = `False`——千问 MLX 版收到 `return_time_stamps=False`，说"我不知道这个参数"，拒收。

旧代码：
```python
if not use_mlx:
    kwargs["return_time_stamps"] = True    # 只有非 MLX 才加这个 key
```

新代码（有 bug）：
```python
return_time_stamps=not _USE_MLX   # MLX 时变成 return_time_stamps=False——MLX 不收
```

**为什么测试没发现**：422 个单元测试没有加载真实 MLX 模型——MLX 只在 Apple Silicon 上有，测试环境无法模拟。

**为什么还没修**：发现 bug 后用户指令"不要做任何操作，不要影响当前任务"，修复未执行。

**解决方案**：MLX 路径不传 `return_time_stamps`（根本不提），非 MLX 路径传 `return_time_stamps=True`。四处调用点的条件改为只传 `language`，仅在非 MLX 时加 `return_time_stamps`。

**关联**：[[P207]]（FA 修复触发工具箱升级）、[[P220]]（FA 内存爆炸——同一次测试的另一个崩溃）

**已修** ✅（2026-06-09 TDD 修复。4 个调用点改为条件展开：`**({"return_time_stamps": True} if not _USE_MLX else {})`，MLX 时不传参数。421 全量零回归）

---

## P222 | 2026-06-09 | 🔴 Qwen ASR v0.0.6 语言名校验——简写"en"不被接受

**来源**：2026-06-09 第五次测试（14:38），P221 `return_time_stamps` 修复后发现的新问题。

**现象**：
```
ValueError: Unsupported language: En. Supported: ['Chinese', 'English', ...]
```

**根因**：v0.0.6 的 `Qwen3ASRModel.transcribe()` 新增了语言名校验。我们传的是 ISO 简写 "en"，新版内部做首字母大写后变成 "En"，然后跟完整语言名列表比较——"En" ≠ "English"，报错。

旧版 API 不校验语言名，传什么接受什么。新版要求完整语言名。

**解决方案**：
`qwen_aligner.py` 里已有 `_LANG_MAP` 做简写→完整名的映射（`"en": "English"`）。Qwen ASR 调用时复用这个映射。或者在 4 个调用点把 `language` 从简写转成完整名。

**关联**：[[P207]]（FA 修复触发工具箱升级）、[[P221]]（return_time_stamps 错误——同一次升级的连锁反应）

**已修** ✅（2026-06-09 TDD 修复。新增 `_resolve_language()` 函数 + `_LANG_NAME` 映射表，4 个调用点改为 `language=_resolve_language(language)`。"en"→"English"。421 全量零回归）

---

## P223 | 2026-06-09 | 🟢 FA 分段对齐并行化——自适应线程数

**来源**：2026-06-09 P220（FA 分段对齐）Jobs 审查。当前串行处理 551 段耗时 105 秒，可并行加速。

**背景**：Python 有 GIL——多线程在纯 CPU 计算上无法真正并行。但 FA 对齐的 GPU 计算阶段（MPS/CUDA）会释放 GIL，因此多线程可以并行等待 GPU，实现加速。

**三种用户场景**：
| 场景 | GPU | 推荐并行数 | 原因 |
|------|-----|-----------|------|
| Mac + MPS | 有 | 2-4 | GPU 排队是瓶颈，超 4 路无益 |
| Windows + NVIDIA | 有 | 2-4 | 同上 |
| Windows 纯 CPU | 无 | 1 | CPU 计算不释放 GIL，多线程全排队，无加速 |

**方案**：
1. 启动时检测是否有 GPU（`torch.cuda.is_available()` / `torch.backends.mps.is_available()`）
2. 有 GPU → 用 `ThreadPoolExecutor(max_workers=4)` 并行
3. 无 GPU → 串行（1 路）
4. 预留环境变量覆盖（如 `FA_PARALLEL=2`），便于用户自行调优

**优先级**：🟢 非阻塞。FA 对齐与 SenseVoice 并行运行，105 秒 vs 总耗时 12 分钟，不是瓶颈。Windows 真机测试（P209）时一并验证最佳并行数。

**关联**：[[P220]]（FA 分段对齐）、[[P209]]（Windows 真机打包测试）

**⏸ 未修**（V3.10 或更后）

---

## P224 | 2026-06-09 | 🔴 FA 分段对齐时间戳偏移丢失——所有字幕叠在 0 秒

**来源**：2026-06-09 第六次测试视频回看发现。所有中文字幕堆叠在视频开头几秒，后面的画面无字幕。

**现象**：SRT 文件中前十条字幕全部从 00:00:00 开始：
```
1  00:00:00,320  →  00:00:01,439
2  00:00:00,160  →  00:00:02,000
3  00:00:00,160  →  00:00:00,560
...
```

**根因**：P220 的 `align_segments` 把 551 个 Whisper 句子段分别喂给 FA。FA 对每段独立工作——不知道这段在视频的哪个位置，只返回相对于片段起点的时间（0-5 秒）。`apply_aligner_timestamps` 把这些相对时间直接写进了字幕——忘了加上每段的原始起点偏移。

FA 没有错——它本来就是这样设计的（无状态，每次独立调用）。错在程序没有把每段的原始起点时间（Whisper 给的 `seg.start`）加回去。

**用户理解**：FA 每次被调用就像一个没有记忆的人——拿到一段音频，兢兢业业找到每个词在这段里的位置，然后清空大脑等下一段。它不知道"这段是从第 22 秒开始的"——它只知道"这段 5 秒的音频，第一个词在 0.3 秒"。551 段全被当成从 0 秒开始。

**解决方案**：`align_segments` 返回对齐结果时，每个词的 `start/end` 加上该段的 `seg.start` 偏移。或修改 `apply_aligner_timestamps` 在处理时加上偏移。

**关联**：[[P220]]（FA 分段对齐）、[[P207]]（FA 官方 API 替换）

**已修** ✅（2026-06-09 TDD 修复。`align_segments()` 中对齐结果每个词的 start/end 加上 `seg.start` 偏移。421 全量零回归）

---

## P225 | 2026-06-09 | 🔴 千问 v0.0.6 PyTorch 路径返回列表但代码当单个对象取——输出全空

**来源**：2026-06-09 investigate 诊断路2A（通用路+Mac MPS）空输出问题。

**根因**：v0.0.6 的 `Qwen3ASRModel.transcribe()` 返回 `List[ASRTranscription]`（列表），但我们的代码在非 MLX 路径上直接对列表取 `.text`——列表没有 `.text` 属性。

```python
result = model.transcribe(...)   # → List[ASRTranscription]
# 代码写的是:
full_text = result.text if hasattr(result, 'text') else ""  # → ""
# 应该写:
full_text = result[0].text if result else ""
```

`hasattr(result, 'text')` 对列表返回 False → `full_text = ""` → 日志显示"无文字,跳过"。

旧版 MLX 的 `transcribe()` 返回单个对象（有 `.text`），所以代码在 `_USE_MLX=True` 时正常工作。P207 升级到 v0.0.6 + MLX 包丢失后走了 PyTorch 路径——新旧 API 返回格式不兼容。

**影响范围**：4 个调用点全部受影响，导致千问在非 MLX 路径上完全不工作：
- producer loop 文本提取（行 283）
- `_process_single_chunk` 串行路径（行 347）
- `run_qwen_asr` 两处文本提取（行 509、558）
- 时间戳访问 `result.time_stamps` 也同样受影响（行 358、576）

**大白话解释**：旧版千问是"问一个人，得一个答案"。问它一段音频说了什么，它直接回答你——`.text` 就能拿到文字。新版千问（v0.0.6）是"问一群人，拿一个盒子"。它把答案装在盒子里还给你——你得打开盒子拿第一个——`[0].text`。我们的代码还在对着盒子问"你说了什么"——盒子不说话。所以千问跑完了，但我们拿不到文字，以为它什么都没说。

**解决方案**：4 处调用点全部改为从列表中取第一个元素，或统一封装兼容新旧 API。

**已修** ✅（2026-06-09 TDD 修复。全部非 MLX 分支改为 `result[0].text`/`result[0].time_stamps`/`result[0].language`。420 全量零回归。1 个 mock 测试需适配新版 API）

**关联**：[[P207]]（FA 升级触发）、[[P221]]（return_time_stamps 错误）、[[P222]]（语言名错误）

---

## P226 | 2026-06-09 | 🔴 千问 MLX 加速包丢失——`qwen3-asr-mlx` 从未列入依赖清单

**来源**：2026-06-09 调查千问 ASR 空输出问题时发现。

**现象**：`_USE_MLX = False`，千问走了 PyTorch+MPS 路径（慢且 P225 bug 导致空输出）。

**根因**：`qwen3-asr-mlx` 从未被写进 `pyproject.toml`。该包是早期手动安装的，一直在系统里默默工作。P207 修复 FA 时安装了 `qwen-asr==0.0.6`，依赖树重新整理后该包被清掉。

**可用版本**：`qwen3-asr-mlx 0.1.1`（在 PyPI 上）。

**解决方案**：安装 `qwen3-asr-mlx==0.1.1`，写入 `pyproject.toml`。代码不需要改——第 33 行 `from qwen3_asr_mlx import Qwen3ASR` 会自动检测并设 `_USE_MLX = True`。

**关联**：[[P207]]（FA 升级触发）、[[P225]]（同一次升级的连锁反应）

**已修** ✅（2026-06-09 TDD 修复。qwen3-asr-mlx==0.1.1 已安装。写入 pyproject.toml。420 全量零回归。但触发了 [[P227]]）


---

## P227 | 2026-06-09 | 🔴 tokenizers 版本冲突——MLX 要 ≥0.23.0，FA 要 ≤0.23.0

**来源**：2026-06-09 P226 修复（装 qwen3-asr-mlx）触发。第七次测试 FA 对齐被跳过。

**现象**：`is_qwen_aligner_available()` 返回 False。FA 模型的 `AutoConfig.from_pretrained()` 抛出版本检查错误：`tokenizers>=0.22.0,<=0.23.0 is required, but found tokenizers==0.23.1`。该异常被 `except (ImportError, OSError)` 捕获，FA 静默跳过。

**tokenizers 版本变迁**：

| 时机 | 版本 | 原因 |
|------|------|------|
| 原始环境 | 0.23.1 | 项目初始安装 |
| P207（装 qwen-asr 0.0.6） | 0.22.2 ↓ | qwen-asr 拉了兼容的旧版 |
| P226（装 qwen3-asr-mlx 0.1.1） | 0.23.1 ↑ | MLX 要求 ≥0.23.0，pip 装了最新版 |

**根因**：两个包对 tokenizers 的要求形成夹逼：
- `qwen3-asr-mlx==0.1.1` → `tokenizers>=0.23.0`（写在 pip 元数据中，package label）
- `transformers`（FA 模型运行时检查）→ `tokenizers>=0.22.0,<=0.23.0`（写在源代码中，not visible to pip）

理论交集版本是 0.23.0，但 **tokenizers 0.23.0 从未在 PyPI 发布**（版本号从 0.22.2 直接跳到 0.23.1）。pip 看到 `≥0.23.0` 就装了 0.23.1，超过 FA 的 0.23.0 上限。

**关键认知**：FA 的版本上限（≤0.23.0）是 transformers 在**运行时**抛出的检查，不是 pip 元数据中的声明。无论先装后装还是同时装，pip 都看不到这个限制——它只处理元数据中声明的依赖。

**解决方案**：降级 `qwen3-asr-mlx` 到 0.1.0（其 tokenizers 要求仅为 `>=0.15.0`），锁 `tokenizers==0.22.2`。
- MLX 0.1.0 ↔ 0.1.1 功能等价，API 相同（`Qwen3ASR.from_pretrained()` + `transcribe()`），仅依赖版本下限不同
- tokenizers==0.22.2 同时满足：MLX 0.1.0（≥0.15.0 ✅）、FA/transformers（0.22.0~0.23.0 ✅）
- 改动：pyproject.toml 改一行（`qwen3-asr-mlx==0.1.1` → `0.1.0`），加一行（`tokenizers==0.22.2`）

**关联**：[[P207]]（qwen-asr 升级）、[[P226]]（MLX 安装）、V3.10（全版号锁定）

**已修** ✅（2026-06-09 降级 MLX 0.1.1→0.1.0，锁 tokenizers 0.22.2。431 测试零回归。FA 可用性检测恢复。）

## P228 | 2026-06-09 | 🔴 字幕时间戳漂移约1秒

**来源**：V3.07 端到端实测。应用 P207/P220 FA 修复后首次完整跑通，用户观看最终视频发现字幕与语音不同步。

**现象**：字幕有时提前约1秒出现（人还没说话字幕先出来），偶尔延迟。大部分偏差为提前。

**根因**：`apply_aligner_timestamps()` 用 Python `str.split()` 数字数来把 FA 对齐词分配给段落。Python 拆词与 FA 内部分词粒度不一致（如 "we're" Python 计为1词，FA 可能拆为2词），515段累积词数漂移，段 N 可能拿到段 N-2 的时间戳。

**解决方案**：改用时间范围匹配。不再数字数，直接筛选对齐词列表中时间戳落在每个段落的绝对时间范围内的词。

**已修** ✅（2026-06-09 TDD 修复。`qwen_aligner.py:236-270` 重写。7 新增测试 + 442 全量零回归。）

## P229 | 2026-06-09 | 🟡 背景音乐段存疑率过高（54% [?]）

**来源**：V3.07 端到端实测。

**现象**：最终输出 54% 字幕标记为存疑 [?]，但用户观看后发现这些段落的翻译大部分是对的。标记与实际质量不匹配。

**根因**：L2 窗口比对用 0.35 统一阈值判断所有窗口。背景音乐干扰下 Qwen ASR 转写质量必然下降——不是 Whisper 错了，而是 Qwen 自己也听不清。L2 没有区分"Qwen 确认转写错误"与"Qwen 因音频条件限制无法确认"。

**解决方案**：L1 在推翻 SV 判定时标记 `seg.bgm_override = True`。L2 检测到窗口内有 BGM 覆盖段时，将 GREEN/YELLOW 阈值从 0.35 降到 0.20。同时更新 L2 日志增加 `bgm` 字段。

**已修** ✅（2026-06-09 TDD 修复。`hallucination_filter.py:164-166, 340-373`。4 新增测试 + 442 全量零回归。）

## P230 | 2026-06-09 | 🟡 DeepSeek 翻译 2 段未被解析（489/491）

**来源**：V3.07 端到端实测。上次测试（8b61066356）已观察到，本次深入分析根因。

**现象**：DeepSeek 翻译了 491 段中的 489 段，2 段被标记为"翻译失败"后由免费引擎补全。

**根因**：(A) 翻译解析正则只认 `[数字] 内容` 一种格式，LLM 偶尔的格式偏差（多空格、中文括号）导致匹配失败；(B) 短句中英文专有名词过多导致中文字符比例不足 10%，被误判为"未翻译"；(C) `reflection.py` 质量反思模块已编写但从未接入流程。

**解决方案**：(A) 格式解析放宽——支持 `[50]` `[ 50 ]` `【50】` `50.` 四种格式；(B) 短句（<15字）中文比例降至 5%，长句保持 10%；(C) 失败段 ≤10 时用最简提示词单独重发给 LLM，重发失败再走免费引擎兜底。

**已修** ✅（2026-06-09 TDD 修复。`orchestrator.py:455-470, 492-508, 789-806`。442 全量零回归。）

---

## UX 体验问题（Steve Jobs 审查 2026-06-09）

以下问题从用户操作视角发现——代码逻辑正确但用户体验错误。

## P231 | 2026-06-09 | 🔴 UX：进度条是假的（P0）

**现象**：用户点"开始"后，界面显示"正在识别语音…"持续 25 分钟，进度从 10% 跳至 25% 后长时间不动。实际五个步骤（Whisper → Qwen → FA → SV → 过滤）全包在 "transcribe" 一个阶段里。

**方案**：拆为四个子阶段，通过 WebSocket 推送真实进度——"正在将声音转成文字"→"正在核对文字准确度"→"正在给每个词标记时间"→"正在过滤不可靠的字幕"。改动 `worker.py` stderr_progress + `transcriber_v2.py` 增加 progress_callback。

**⏸ 未修**

## P232 | 2026-06-09 | 🔴 UX：警告信息制造焦虑（P0）

**现象**：> "⚠ 音频质量较差（54% 字幕存疑），建议手动校对后使用"。用户直觉认为"一半都是错的，这工具不能用"。实际翻译大多正确，只是 BGM 导致 Qwen 无法确认。

**方案**：重写警告文案——"字幕处理完成。您的视频全程有背景音乐，部分字幕的校对标记可能偏多。这不影响翻译质量——标记 [?] 的段落翻译大多数是准确的。" 改动 `showQmWarning()`（index.html:1276）。

**⏸ 未修**

## P233 | 2026-06-09 | 🟡 UX：设置面板在运行期间被锁死（P1）

**现象**：用户点击"开始"后 30 分钟内，所有设置控件（输出方式、模型选择、翻译模式等）全灰不可操作。如果 2 分钟后发现选错输出模式，必须取消整个任务重来，代价 30 分钟。

**方案**：允许修改"当前任务尚未用到的设置"；添加排队修改提示。改动 `lockUI()`（index.html:1151）。

**⏸ 未修**

## P234 | 2026-06-09 | 🟡 UX：模型和翻译故障对用户不可见（P1）

**现象**：FA 加载失败、Qwen 降级、LLM 翻译切免费引擎——全部静默。用户只知道"字幕不太准"，不知道原因。后端日志写了一切，前端从不显示。

**方案**：任务完成后推送处理摘要——每个子系统的可用状态和结果。如"Whisper ✅ Qwen ✅ FA ✅ | 翻译: DeepSeek (489/491) | 存疑: 54%（BGM导致，翻译可靠）"。后端汇总 JSON 通过 WebSocket 推送。

**⏸ 未修**

## P235 | 2026-06-09 | 🟡 UX：已完成任务阻塞新任务（P1）

**现象**：处理完一个视频后想用不同设置重新处理，系统显示"检测到任务已完成"但开始按钮灰掉。用户必须手动清缓存才能重新开始。

**方案**：已完成任务显示显式"重新处理"按钮，自动清除旧缓存并提交新任务。改动 `handleFoundJob()`（index.html:1069）。

**⏸ 未修**

## P236 | 2026-06-09 | 🔵 UX：下载按钮和输出文件不清晰（P2）

**现象**：显示"下载视频"和"下载 SRT"两个按钮。非技术用户不知道 SRT 是什么。"封装字幕"模式下视频已含字幕，为什么还要下载 SRT？

**方案**：根据输出模式调整按钮文案——烧录："下载视频（已烧录字幕）| 下载字幕文件（SRT 格式）"；封装："下载视频（可开关字幕）| 下载字幕文件"。改动 `renderCardDone()`（index.html:1260）。

**⏸ 未修**

## P237 | 2026-06-09 | 🔴 系统日志是"日记"不是"黑匣子"（P0）

**来源**：V3.07 第二次端到端实测（job `896557f4d8`）。同一视频、同一代码，第一次 SenseVoice 正常，第二次 5 个分块全部 BrokenPipe 崩溃。日志只能看到"崩了"，看不到崩时内存/GPU/步骤/segments 数量——根因全靠猜。

**根因**：日志设计是"事件记录"模式（日记），不是"状态快照"模式（黑匣子）。崩溃时没有自动抓取系统参数。

**最终方案（方案 C · Jobs + 双方工程师三轮审查定稿）**：Post-mortem Dump（死后遗言）。atexit handler + global current_step + try/except 单次快照。40 行代码，零新文件，全部内联在 worker.py。不去每步前后抓 14 个快照点——只在进程死的时候抓一次。

**实现 Checklist**：
```
□ 1. worker.py 顶部: current_step = "init"
□ 2. 每个关键步骤前: current_step = "..."（赋值前如果是在函数内部 → global current_step）
     transcribe / sensevoice / hallucination / translate / subtitle
     五处赋值，每处一行
□ 3. 写 _crash_snapshot():
     import psutil, threading, sys
     proc = psutil.Process()
     return {
         "time": time.time(),
         "step": current_step,
         "memory_mb": proc.memory_info().rss / 1024 / 1024,
         "segments": len(segments) if "segments" in dir() else 0,
         "mlx_available": "mlx" in sys.modules,
         "threads": threading.active_count(),
     }
□ 4. except Exception 块中: debug.log 写入 JSON dump + traceback
□ 5. 手动触发崩溃验证快照写入
□ 6. 不建新文件；不建 utils/
```

**代码量**：~45 行。零新文件。零新依赖。

**⚠️ 部分实现，未闭环**（2026-06-14 复查）：`worker.py` 已有 `current_step` 和 `_crash_snapshot()`，但异常处理处只写 traceback，没有调用 `_crash_snapshot()` 把内存、线程、步骤等状态写进 debug.log。因此 P237 不能算已修，仍保留待做。

## P238 | 2026-06-09 | 🔴 SenseVoice 因 tqdm 进度条 BrokenPipe 全部崩溃

**来源**：V3.07 第二次端到端实测（896557f4d8）。SV 5 个分块全部 BrokenPipe，标签分布为 {}。

**现象**：SV 加载正常（7.9s），但 5 个分块全部 BrokenPipeError 崩溃，标签分布为空。FA 对齐等其他步骤正常。

**完整堆栈跟踪（铁证）：**
```
sensevoice_runner.py:88 model.generate()
  → funasr/auto/auto_model.py:477 generate() → self.inference()
    → funasr/auto/auto_model.py:578 inference() → pbar.update()
      → tqdm/std.py:1242 update() → self.refresh()
        → tqdm/std.py:1347 refresh() → self.display()
          → tqdm/std.py:1495 display() → self.sp()
            → tqdm/std.py:459 print_status() → fp_write()
              → tqdm/std.py:452 fp_write() → fp.write(str(s))
                → BrokenPipeError: [Errno 32] Broken pipe
```

**根因：** funasr 库内置 tqdm 进度条，默认输出到 stderr。uvicorn --reload 模式下，服务重启时 stderr 管道可能半断开。tqdm 写进度条时发现管道断了 → BrokenPipeError。第一个 chunk 的写操作打断管道后，后续 4 个 chunk 全部复用同一个坏管道 → 全部崩溃。

**修复方案：** 在 `AutoModel()` 构造时传入 `disable_pbar=True`。一行。SV 不需要进度条——它运行在后台 worker 中，没有人在看。

```python
# v2/backend/core/sensevoice_runner.py
model = AutoModel(
    model="iic/SenseVoiceSmall",
    trust_remote_code=True,
    device=device,
    disable_pbar=True,    # 关闭 tqdm 进度条，避免 stderr BrokenPipe
)
```

**源码验证：** `funasr/auto/auto_model.py` 中 `__init__` 将 `disable_pbar` 存入 `self.kwargs`，`inference()` 从 `self.kwargs.get("disable_pbar", False)` 读取。同一对象同一字典同一条路径，构造一处全局生效，无需在 `generate()` 中重复传入。

**教训：** BrokenPipeError 在这个项目中多次出现（之前 --reload 也触发过），本质原因是后台 worker 的标准输出/标准错误管道在服务重启或代码变更后处于半连接状态。任何依赖 stderr 输出的第三方库都可能触发此问题。不要臆想病因——翻堆栈、找到实际崩的那一行、针对那一行修。

**已修** ✅（2026-06-10，`disable_pbar=True` 加到两处 AutoModel 调用）

## P239 | 2026-06-09 | 🔴 worker stderr_progress 因 BrokenPipe 崩溃导致翻译阶段未执行

**来源**：V3.07 第二次端到端实测（896557f4d8）。转录全部正常完成（返回 440 段），但在发送最后一条进度消息时 worker 崩溃，翻译和烧录阶段从未启动。无 SRT 输出文件。

**完整堆栈：**
```
worker.py:350 stderr_progress("transcribe", 35, f"语音识别完成: 共 {total} 句")
worker.py:626 _real_stdout.flush()
BrokenPipeError: [Errno 32] Broken pipe
```

**根因：** `_real_stdout` 是 worker 子进程向 uvicorn 父进程报告进度的管道（模块级保存 `sys.stdout`）。任务运行期间我们创建了 `v2/docs/` 下的文件，uvicorn `--reload` 检测到文件变更后重启了父进程。父进程死时关闭了管道的读端，子进程（worker）在第 39 分钟发送最后一条进度时发现管道对面没人了 → BrokenPipeError。

**教训：** uvicorn `--reload` 不是为长时间运行的任务设计的。它监控整个工作目录（包括 docs/ 等非代码文件），任何文件变更都会触发重启。`--reload` 模式下启动的任务没有可靠性保证——父进程随时可能被 reload 杀死，导致子进程管道断裂。生产环境去掉 `--reload` 后此问题不存在。

**修复方案：** `stderr_progress` 中的 `_real_stdout.write()` 和 `_real_stdout.flush()` 用 try/except BrokenPipeError 包裹，进度汇报失败不应导致任务崩溃——静默跳过，翻译继续。

**关联：** P238（同一次测试中 SV tqdm 也因管道断崩溃——同一根管道链上的两次断裂）

**已修** ✅（2026-06-10，`stderr_progress` 用 try/except 包裹 BrokenPipeError + OSError）

---

## P240 | 2026-06-10 | 🔴 安全：llm_fallback_decision 路径穿越漏洞（C1）

**来源**：`/review` 全项目审查。

**问题**：`main.py:850-856` 的 `llm_fallback_decision()` 端点接收用户传入的 `job_id` 参数，直接拼接 `JOBS_DIR / job_id` 构建路径，没有做路径穿越验证。攻击者可传 `../../etc/passwd` 等 job_id，读取或操作任意文件。

**对比**：同文件中的 `download_video`（L794）、`download_srt`（L812）、`cancel_job`（L837）三个端点已有 `.resolve()` + `.startswith()` 防护，唯独 `llm_fallback_decision` 和 `get_job` 遗漏了。

**根因**：P189 修复路径穿越时漏了这两个端点。

**修复方案**：加入与其他端点相同的 `.resolve()` + `.startswith(JOBS_DIR.resolve())` 校验。

**修复**：抽象 `_validate_job_path(job_id)` 辅助函数，`llm_fallback_decision` 调用后校验。2026-06-10 TDD 修复。

**已修** ✅

---

## P241 | 2026-06-10 | 🟡 翻译：P230 retry 硬编码"英文→中文"（H1）

**来源**：`/review` 全项目审查。

**问题**：`orchestrator.py:799` retry 逻辑中硬编码 `"将以下英文翻译成中文"`，当源语言不是英文时（如俄语、日语视频），retry prompt 与实际语种不匹配，翻译质量下降或 LLM 拒绝执行。

**根因**：P230 修复 retry 时直接写死了语种，没有从上下文取实际 source/target language。

**修复方案**：从 `_TARGET_LANGUAGE` 或传入参数中获取目标语言，构造正确的 retry prompt。

**修复**：retry prompt 改为 `f"将以下内容翻译成{_TARGET_LANGUAGE}。只输出译文，不要解释。\n\n{texts[fi]}"`。2026-06-10 TDD 修复。

**已修** ✅

---

## P242 | 2026-06-10 | 🟡 转录：transcribe_raw() MLX 后端崩溃（H2）

**来源**：`/review` 全项目审查。

**问题**：`qwen_asr_runner.py:502` 使用 `result[0]` 取 MLX 后端的转录结果，但 MLX 后端返回的是对象而非列表，导致 `TypeError`。当前 MLX 后端不可用时静默跳过，但一旦有人用 MLX 后端就会崩溃。

**修复方案**：加 `isinstance(result, list)` 判断，MLX 后端返回对象时用 `result.text` 取值。

**❌ 已关闭（2026-06-11 换库 + bug 流水线确认）**。bug 流水线 `plan-eng-review` 已确认 `transcribe_raw()`（L459-517）和 `_transcribe_raw_chunked()`（L248-326）全 backend 无任何调用者——死代码。换 MLX ASR 库（gabrimatic→moona3k）后旧库整体删除，P242 描述的崩溃路径自然消失。

---

## P243 | 2026-06-10 | 🟡 安全：/api/llm-models 自定义端点 SSRF（M2）

**来源**：`/review` 安全专项审查。

**问题**：`main.py` 的 `/api/llm-models` 端点允许用户传入自定义 API base URL，后端直接用 `httpx.get()` 请求该 URL。没有校验 URL scheme（允许 file://）或目标 IP（允许 127.0.0.1、内网地址），攻击者可利用此端点探测内网服务或读取本地文件。

**修复方案**：校验 URL 必须为 https://（或 http://），解析后校验目标 IP 不在私有地址段（10.x、172.16-31.x、192.168.x、127.x）。

**修复**：新增 `_verify_safe_url(url)` 辅助函数，scheme 校验 + `ipaddress` 模块拦截私有/环回/保留地址。`list_llm_models` 调用前校验。2026-06-10 TDD 修复。

**已修** ✅

---

## P244 | 2026-06-10 | 🟡 安全：get_job 缺少路径穿越验证（M3）

**来源**：`/review` 全项目审查。

**问题**：`main.py:757-780` 的 `get_job()` 端点同样没有路径穿越校验。虽然只做读取操作（读 config.json），但攻击者可通过构造 job_id 读取任意目录下的 config.json 文件。

**修复方案**：加入与其他端点相同的 `.resolve()` + `.startswith()` 校验。

**修复**：`get_job` 调用 `_validate_job_path(job_id)` 校验后再读 meta.json。2026-06-10 TDD 修复。

**已修** ✅

---

## P245 | 2026-06-10 | 🔵 并发：GLOBAL_HASH_INDEX 无锁保护（M1）

**来源**：`/review` 并发专项审查。

**问题**：`main.py` 中 `GLOBAL_HASH_INDEX`（dict）在 FastAPI 的异步端点中被多个协程同时读写（`create_job` 写入、`get_job` 读取），没有锁保护。虽然 CPython GIL 在单线程 asyncio 下不太会出问题，但 `dict.update()` + 后续的 `json.dump()` 写文件操作不是原子的，极端情况下可能写入不完整数据。

**修复方案**：用 `asyncio.Lock` 包裹对 `GLOBAL_HASH_INDEX` 的写操作。

**❌ 已关闭（2026-06-11 bug 流水线判假 bug）**。bug 流水线影响面分析已验证：GLOBAL_HASH_INDEX 全部访问点都在 asyncio 事件循环单线程内；create_job 的 check(L707 get)→set(L752) 区间无 await（rmtree/write_text/worker_manager.start 全同步）→ 单线程原子，并发请求排队串行不交错。加 threading.Lock 防御不存在的竞态。

---

## P246 | 2026-06-10 | 🟡 设计一致性：L4"重复模式"和"重复词"缺少 L2 GREEN 豁免

**来源**：设计一致性走查。

**问题**：`hallucination_filter.py` `_layer4_regex()` 中 L222 的"重复模式"和 L228 的"重复词"两条规则缺少 `l2_status != "GREEN"` 豁免检查。设计图 P166 要求：如果 L2 交叉验证确认了双模型一致（GREEN），关键词黑名单 / 重复模式 / 重复词都不应丢弃。关键词黑名单（L201）已有此检查，但重复模式和重复词漏掉了。

**修复方案**：L222 和 L228 前各加 `getattr(seg, "l2_status", None) != "GREEN"` 条件。

**修复**：L222（重复模式）和 L228（重复词）各加 `if getattr(seg, "l2_status", None) != "GREEN":` 守卫。2026-06-10 TDD 修复。

**已修** ✅

---

## P247 | 2026-06-12 | 🟡 MEDIUM Transcript Refiner——LLM 双稿裁判 + 边界修复 + 润色去问号

**来源**：2026-06-12 ASR 分支实测（London VLOG），Jobs + 用户七轮方案讨论定稿。

**一句话**："LLM sees both transcripts, judges discrepancies, fixes boundaries, polishes text."

**实测数据**（同一视频三次测试）：

| 版本 | [?] 数 | [?] 率 | 主要成因 |
|------|--------|--------|---------|
| 1.srt（旧版）| 283 | 25.5% | P250 语速单位错 + P251 管道堵死 |
| 2.srt（旧版）| 299 | 26.8% | 同上 |
| 3.srt（本次）| 77 | 7.1% | 主要是短词（FA 最小词数限制） |

**三层方案**：

**第一层：代码（L1 短词豁免 + 多语言支持）**
在 `hallucination_filter.py` 新增 `_is_short_word_exempt(text, lang)`：
- zh/ja → `len(text) <= 4`（字符数）
- 其他语言 → `len(text.split()) <= 3`（词数）
短词豁免 → 不打 [?]，直接通过。

**第二层：LLM 双稿裁判（核心创新）**
LLM 同时收到两份 SRT：
- 主稿：千问的句级字幕（Whisper 骨架组装后，可能有边界偏差和个别错词）
- 参考稿：Whisper 的原始句级字幕
LLM 对比两份稿子——发现千问写 "claw"、Whisper 写 "clock"，前面在问 "What time is it?" → 判 Whisper 的 "clock" 正确。修改主稿。

**第三层：边界修复 + 润色**
LLM 通读全文，发现格子边界处"末尾多了一个不属于这句话的词"——把它移到下一句开头。**只改文字，不改时间戳。** 同时加标点、去口语词、理断句。

**Prompt（双稿版）**：
```
You are an expert subtitle editor. You receive TWO transcripts of the same audio.
SRT A (primary, more accurate overall) is your working copy.
SRT B (reference) comes from a different transcriber — use it to spot errors.

CRITICAL RULES:
1. Do not alter, merge, or delete any ID or Timestamp lines.
2. Compare A against B: when A has a suspicious word, check B's version.
   Use context to decide which is correct. Fix A if needed.
3. Fix boundary errors: if a word at the end of one segment clearly belongs
   to the next, move it. Only change text — keep timestamps as-is.
4. Fix disfluencies, add punctuation, smooth phrasing. Do NOT rewrite meaning.
5. For segments ending with "[?]", judge based on context — remove or correct.
6. Short utterances have been pre-validated — only process [?] on longer ones.
7. Output ONLY the valid SRT. No conversational intros or outros.

[SRT A — Primary (Qwen)]
...
[SRT B — Reference (Whisper)]
...
```

**流水线位置**：位于 L2 交叉验证之后、翻译开始之前。修改后的流程：
```
转录 → P254 路由 → L1（千问原稿）→ 词变句子 → L2 交叉验证 → P247 Refiner（双稿裁判+润色）→ 翻译
```

**Refiner 的 `_build_llm_backends` 需加 else 日志**：当前无 else → 静默跳过。需加 `_log.warning` 以便排查。

**代码量**：~95 行。零新文件。零新依赖。

**✅ 已修**（2026-06-13）。Plan C 单段隔离安全阀实现：`_clean_dist()` 归一化编辑距离（去空格+去标点后比较），阈值 0.80，逐段拦截而非全局回滚。FJIN-141 实测 843 个 `[?]` 降到 0。后续由 P263+P264 双稿裁判重构替代原有全篇 SRT 方案。

## P248 | 2026-06-10 | 🟡 设计一致性：P3 反思仅在翻译失败时触发

**来源**：设计一致性走查。

**问题**：`worker.py:438` 的 P3 反思判断条件为 `api_keys and failed and translation_mode == "quality"`，即翻译全部成功时 P3 不运行。设计图说 P3 是 quality 模式通用审查（术语/语气/补漏/纠错），不应限定于有失败段。当前导致最需要 P3 的场景（翻译全成功但可能有术语不一致/语气问题）被跳过。

**修复方案**：将 L438 条件改为 `if api_keys and translation_mode == "quality":`，去掉 `and failed` 约束。

**修复**：worker.py L438 条件去掉 `and failed`。2026-06-10 TDD 修复。

**已修** ✅

---

## P249 | 2026-06-10 | 🟡 yt-dlp 不读系统代理，国内用户下载 YouTube 走不了翻墙

**来源**：V3.07 端到端实测。三次 YouTube 下载全部中断——yt-dlp 不知道系统有 FlClash 代理在 127.0.0.1:7890。

**问题**：`video_downloader.py` 调用 yt-dlp 时没有传代理。yt-dlp 只认 `HTTP_PROXY`/`HTTPS_PROXY` 环境变量或 `--proxy` 参数。macOS 系统代理设置（FlClash/V2Ray/Surge 等写入的 `scutil --proxy`）Python 不会自动读取。

**三种用户场景**：
1. 电脑装翻墙软件（FlClash/V2Ray/ClashX）→ 系统代理有值，但 Python 看不见 → 下载不走代理
2. 软路由翻墙 → 网关就是代理，系统代理通常为空 → 不需要额外配置，流量自动走
3. 无翻墙 → 系统代理空 → 直接连，连不上正常

**修复方案**：下载前自动检测系统代理，按优先级：① 读 `HTTP_PROXY`/`HTTPS_PROXY` 环境变量 → ② macOS 执行 `scutil --proxy` 解析 HTTPProxy+HTTPPort → ③ Windows 读注册表 → ④ 都没查到就不传代理。检测到代理后通过 yt-dlp 的 `proxy` 参数传入。用户不需要手动填任何东西。

**已修** ✅（2026-06-10 TDD 修复）

---

## P250 | 2026-06-11 | 🔴 L3 语速检测单位用错 + 阈值过低，批量制造假 [?]（203/299）

**来源**：V3.07 端到端实测 b1a602b59f（London Vlog, 22min 英文）。

**问题**：505 段字幕中 299 段标记 [?]（59%）。逐层拆解后发现 L3 贡献了 203 个 [?]（68%），远超 L1（5 个）和 L2（~90 个）。

### 根因（两个独立设计错误，叠加放大）

**错误 1 — 单位用错了（这是设计错误，不是参数问题）：**

L3 用 `len(text)` 数「字符」做语速单位——这个函数对所有语言都数字符。

但「字符」在中英文里不是同一种东西：
- 中文：一个字 = 一个独立意思。「我是中国人」= 5 个有意义的单位，用「字/秒」合理
- 英文：一个字母 ≠ 一个意思。`I'm Chinese` = 10 个字符（I ' m 空格 C h i n e s e），但实际只有 3 个单词（I / am / Chinese）。`len()` 把字母当「字」用——量的是重量，报的是个数

用 `len()` 数英文相当于：中文用「笔画数」算语速——「我」（7 画）、「是」（9 画），正常人 1 秒说 3 个字 = 3 单位/秒，但用笔画算就是 24 单位/秒——直接爆表。**英文拿字母当单位，跟中文拿笔画当单位是一样的错误。**

英文的平均单词长度是 5-6 个字符（含空格和标点约 6-7）。这意味着同样的 `len()` 值，英文实际包含的「有意义单位」只有中文的 1/5 到 1/6。如果你 1 秒说了 3 个英文单词（约 19 个字符），`len()` 告诉你 19——中文标准看 19 是超速，英文标准看 3 个单词是慢走。

**错误 2 — 阈值也不对：**

即使修了单位，英文现在的阈值 25 也太低。按单词算正常人 3-4 单词/秒（快一点到 6-7），25 字/秒 ÷ 6 字符/词 ≈ 4 单词/秒。这个水平的语速不应该打问号。

### 数据说话

同一个英文句子 `"The beautiful architecture of this building is incredible"`：
- `len()` = 55 个字符 → 55/2 秒 = 27.5 cps → 超 25 → `[?]`
- `len(text.split())` = 9 个单词 → 9/2 秒 = 4.5 wps → 正常语速
- 这句**没有任何问题**，被打 `[?]` 纯粹因为拿错了尺子

### 影响

L3 的批量假警报淹没了 L1 和 L2 真正有意义的标记。用户看到 59% [?] 率以为整个系统都在瞎猜——实际上 L1 几乎全通过，L2 对了一半以上。

### 最终方案：按语种查表分治（Best Practice，已定稿）

**核心洞察**：不存在「万能公式」。必须用 Whisper 返回的语言标签 `lang_code` 查表，每个语种定义自己的计数方式 + 阈值。

**为什么空格检测失败了**：
- 韩语有空格但分的是「语节」（eojeol），不是单词 → 按空格算会严重低估语速
- 泰语没空格但字符是辅音+元音+声调的组合，不是音节 → 按字符算会严重高估语速

**为什么按音节算是对的**：L3 想量的东西是「一个人在 X 秒内发出了多少个语音单位」。这个单位在所有语言里都是**音节**（syllable）。中文 1 字 ≈ 1 音节，韩语 1 方块 ≈ 1 音节，英文 1 词 ≈ 1.5 音节。

**为什么中英阈值不同**：中文是「音节计时语言」（syllable-timed），每个音节等长，像机关枪往外崩，极限可达 12-15 音节/秒。英语/俄语是「重音计时语言」（stress-timed），轻重交替有弱读，生理极限约 10 音节/秒。这是语言学实测数据，不是随便拍的。

### 实现（修订版 · 含工程师审查修正）

**修正记录**：
- 修正 1（致命 Bug）：泰语 `split()` 在无空格文本上永远返回 1，改为 `len(clean_text) / 3.2`（泰语平均 3-3.5 字符/音节）
- 修正 2（隐患）：中日文 `len(text.strip())` 会计数标点符号（，。？！），改为正则只计汉字+假名+字母+数字
- 修正 3（隐患）：物理极限粗筛 `text_len > 5` 对英文过严（`"Apple!"` 6 字符但 2 音节），改为 `units > 2`（0.1 秒内人类不可能发出 >2 个音节）

#### 计数函数

```python
import re

def count_speech_units(text: str, lang: str) -> float:
    """按语言返回语速计数单位。

    - 中日：字数（1 字/假名 ≈ 1 音节）
    - 韩语：谚文方块数（1 方块 ≈ 1 音节）
    - 空格语言：单词数（直接数，不乘系数）
    - 泰语：字符数 / 3.2（临时估算音节）
    - 兜底：单词数（英文模式）
    """
    
    if lang in ("zh", "ja"):
        # 只计汉字、假名、字母、数字，过滤标点符号
        # 标点（，。？！等）不发音，不应计入语速
        valid = re.findall(
            r'[一-龥぀-ゟ゠-ヿa-zA-Z0-9]', text
        )
        return len(valid)
    
    elif lang == "ko":
        # 韩语：1 谚文方块 ≈ 1 音节
        # 只数方块字 + 汉字 + 字母 + 数字，过滤空格标点
        valid = re.findall(
            r'[가-힣一-龥a-zA-Z0-9]', text
        )
        return len(valid)
    
    elif lang in ("en", "fr", "es", "de", "pt", "it", "nl", "ru", "pl", "cs"):
        # 空格分词语言：直接数单词数，不乘系数
        # 每个语言有独立阈值，不需要统一换算成「音节」
        return len(text.strip().split())
    
    elif lang == "th":
        # 泰语：无空格，pythainlp 分词前用字符数 / 3.2 估算音节
        # 泰语平均每音节 3-3.5 个字符（含辅音+元音+声调符号）
        # 绝不能 split()——找不到空格会返回 1，L3 彻底失效
        clean = re.sub(r'\s+', '', text)
        return len(clean) / 3.2
    
    else:
        # 兜底（Whisper 极少返回 auto）：英文模式，split 按空格分词
        return len(text.strip().split())
```

#### 改 L3 函数

```python
def _layer3_speech_rate(seg, source_lang: str = "auto") -> None:
    dur = seg.end - seg.start
    
    if dur <= 0:
        seg.filtered = True
        seg.filter_reason = "layer3_impossible_duration"
        return
    
    units = count_speech_units(seg.text, source_lang)
    
    # 物理极限粗筛：0.1 秒内人类不可能发出 > 2 个语音单位
    # 不可以用 len(text) 代替——英文 "Apple!"=6 字符但只有 1 个单词
    if dur < 0.1 and units > 2:
        seg.filtered = True
        seg.filter_reason = "layer3_impossible_duration"
        _log.info("L3 不可能时长: start=%.2f dur=%.3f units=%.1f lang=%s",
                  seg.start, dur, units, source_lang)
        return
    
    rate = units / dur
    threshold = _SPEECH_RATE_THRESHOLDS.get(source_lang,
                                            _SPEECH_RATE_THRESHOLDS["auto"])
    if rate > threshold:
        seg.uncertain = True
        _log.info("L3 语速异常: start=%.2f rate=%.1f threshold=%d lang=%s units=%.1f",
                  seg.start, rate, threshold, source_lang, units)
```

#### 阈值表

```python
_SPEECH_RATE_THRESHOLDS = {
    # 每个语言用自己的原生单位，不强制统一成「音节」
    "zh": 15,     # 字/秒
    "ja": 15,     # 字/秒
    "ko": 12,     # 方块/秒（≈ 音节/秒）
    "th": 12,     # 近似音节/秒（泰语返回音节单位，不能用 auto 的单词阈值）
    "en": 7,      # 单词/秒
    "fr": 7,
    "es": 7,
    "de": 7,
    "ru": 7,
    "auto": 7,    # 兜底：单词/秒
}
```

| 语言 | 计数方式 | 单位 | 异常红线 |
|------|---------|------|---------|
| `zh`（中文） | 正则计汉字 | 字/秒 | > 15 |
| `ja`（日语） | 正则计假名+汉字 | 字/秒 | > 15 |
| `ko`（韩语） | 正则计谚文方块 | 方块/秒 | > 12 |
| `th`（泰语） | `len(clean) / 3.2` | 近似音节/秒 | > 12 |
| `en`/`fr`/`es`/`de`/`ru` 等 | `split()` 数单词 | 单词/秒 | > 7 |
| `auto`（兜底） | `split()` 数单词 | 单词/秒 | > 7 |

**设计决策 1：去掉了 ×1.5 系数。** 每个语言已经有独立阈值，不需要把所有单位都换算成「音节」。中文数的是字，阈值就是字/秒；英文数的是单词，阈值就是单词/秒。

**设计决策 2：`th` 必须显式写在字典里。** 上一版靠 `.get()` 兜底到 `auto`——当时 `auto` 是 10（音节/秒），与泰语返回的近似音节单位一致。去掉 ×1.5 后 `auto` 变成了 7（单词/秒），泰语的音节单位不能再继承单词阈值——用 7 单词/秒去卡音节语速会大面积误报。加入 `"th": 12`，与韩语同级（音节计时语言）。

**⏸ 未修**（方案已定稿，代码未实施）

---

## P251 | 2026-06-11 | 🔴 `align_by_similarity` 逐段映射不在设计图上，堵死 L2 管道导致 12/46 窗口 NO_QWEN

**来源**：V3.07 端到端实测 b1a602b59f（London Vlog, 22min 英文）。L2 数据：46 窗口，NO_QWEN:12。

### 问题诊断

L2 是 30 秒窗口纯文字对比——把两个模型在同一个 30 秒区间里的所有文字倒在一起，提词根、比重合。不管 Whisper 说了几句、Qwen 说了几句。

但代码多了一步不在设计图上的操作：`align_by_similarity()` / `_match_qwen_to_whisper()`。这一步把每个 Qwen 句子**贴到某一个 Whisper 碎片上**。一个 Qwen 句子只匹配一个碎片。

**实际数据**（b1a602b59f chunk 1）：Qwen 31 个句子，Whisper 122 个碎片。映射后最多 31 个碎片有 `qwen_text`，剩下 91 个空着。5 个 chunk 全部走相似度匹配，总共 124 个 Qwen 句子 → 最多贴到 124 个碎片，524 个碎片中约 400 个 `qwen_text = None`。

L2 窗口聚合时读 `seg.qwen_text`——如果某个 30 秒窗口里所有碎片的 `qwen_text` 都是 None，窗口就是 NO_QWEN（静默通过，不打 [?]、不报错）。

### 根因

**映射本身不该存在。** 这是旧架构的尸体——某个早期版本可能逐段比对（segment vs segment），需要把 Qwen 句子分到碎片上。后来设计改了（30 秒窗口聚合），映射代码没删，堵在管道中间把 Qwen 输出截流了。

用户设计意图：
```
Whisper 转录 → Qwen 转录 → SV+FA 启动 → L1 交叉验证 → L2 窗口聚合纯文字对比
                                                      ↑ 不需要逐段映射
```

代码实际执行：
```
Whisper 转录 → Qwen 转录 → align_by_similarity（不在设计图上！）→ L2 被饿死
```

### 影响

NO_QWEN 窗口静默通过，完全没有交叉验证。比打 [?] 更危险——给用户 "L2 验证过了" 的错觉。12/46 窗口（26%）受影响。

### 定位

- [qwen_asr_runner.py:85-115](v2/backend/core/qwen_asr_runner.py#85) `_match_qwen_to_whisper()` — 逐句匹配
- [qwen_asr_runner.py:136-157](v2/backend/core/qwen_asr_runner.py#136) `align_by_similarity()` — 调度 1:1 或相似度匹配
- [qwen_asr_runner.py:377-379](v2/backend/core/qwen_asr_runner.py#377) `_process_single_chunk()` — chunk 转录后调用映射
- [hallucination_filter.py:341](v2/backend/core/hallucination_filter.py#341) — L2 从 `seg.qwen_text` 读数据，被映射截流

### 修复方案（Steve Jobs 裁决：方案 C — 砍掉映射）

**方案 C（推荐）**：直接砍掉 `align_by_similarity` / `align_by_timestamps` / `_match_qwen_to_whisper` 三个函数。`run_qwen_asr()` 返回每个 chunk 的全文 + 时间范围。L2 `_layer2_cross_validate()` 不再从 `seg.qwen_text` 读，改为按窗口时间直接从 Qwen chunk 输出里取。

这样做：
- 删 ~80 行代码
- 删 `Segment.qwen_text` 字段
- Qwen 的每个 chunk 全文（~300 秒 = ~10 个窗口）全部参与对应窗口的比对
- L2 不再是「拼残缺碎片」→ 变成真正的「窗口纯文字对比」

**方案 A**（凑合）：修映射公式，把 Qwen 全文均匀撒进所有碎片。治标不治本——映射这步本身多余。

**方案 B**（折中）：`run_qwen_asr()` 返回全文+时间范围列表，L2 同时读 `seg.qwen_text` 和 chunk 全文。兼容但不干净。

### 为什么不是修公式

改分母（Jaccard 之类）修不了根本问题。124 个句子最多只能贴到 124 个碎片——无论公式多好，524 个碎片里永远有 400 个空着。管道堵的不是公式，是多了一个本不该存在的步骤。

**⏸ 未修**

## P252 | 2026-06-11 | 🔴 走查盲区：Q1 只查编排函数内部，查不到模块间管道里的多余步骤

**来源**：P251 诊断过程中发现。`align_by_similarity` 不在设计图上，按道理走查 Q1（「代码里多了哪一步？」）应该抓住它——但没抓住。

### 为什么漏了

走查的检查范围是编排函数 `filter_hallucinations()` 内部。这个函数里的步骤：L1 → L2 → L4 → L5 → L3。每一步都在设计图上，顺序也对。Q1-Q3 全部通过。

但 `align_by_similarity` 不在 `filter_hallucinations()` 里面。它在 `run_qwen_asr()` 里面——Qwen 转录完成后、数据送入 L2 之前。它坐在两个模块之间的**管道**里，不在任何一个模块的**盒子**里。

走查看的是盒子内部。管道里的东西——看不见。

### 这不是第一次

同一个盲区造成了三个事故：

| 事故 | 多出来的步骤 | 藏在哪里 |
|------|------------|---------|
| P193 | L2 Pass 1 跨层比对（SV标签 × Qwen文本） | `_layer2_cross_validate()` 内部多出一个 Pass |
| P199/P200 | 四模型全串行（设计图箭头画错） | 编排函数的调度顺序，但代码跟图一致 → 前三问通过 |
| P251 | `align_by_similarity` 逐段映射 | Qwen ASR 模块和过滤器模块之间的管道，不在编排函数里 |

**共同模式**：多余的东西不在「指挥官喊了什么步骤」里，而在「数据传递的路上经了谁的手」里。

### 修复（流程修复，不是代码修复）

走查升级为 **走查 2.0**（三个零件一套，已在 CLAUDE.md 记录）：

- **零件 A**：走查不止走编排函数，也走数据流。拿一个关键数据包从出生追到消费。
- **零件 B**：设计图箭头必须写字。A 输出什么格式 → B 期望什么格式，格式对不上就是裂缝。
- **零件 C**：新增 Q5「数据路上经过了几只手？每只手读/改/扔？哪只手图上没有？」

### 安装时间

**待安装。** 下次修复 bug 或版本迭代进入 HARDEN 阶段时激活。届时走查按 2.0 流程执行，不再用旧五步法。

**✅ 已修**（2026-06-11 走查 2.0 已激活，CLAUDE.md 走查规范已替换为六步法 + Q1-Q5）

## P253 | 2026-06-11 | 🟡 翻译滑窗阈值用上下文上限而非疲劳区间——长视频翻译质量可能下降

**来源**：2026-06-11 审查翻译模块滑窗逻辑时发现。

**问题**：`orchestrator.py:329-338` `_compute_chunk_size()` 的公式是 `(max_input_tokens - 18000) // 8`，clamped [200, 3000]。这是在算「上下文还剩多少空间」——只要装得下，就一次性全塞进去。

但 LLM 存在「迷失在中间」(Lost in the Middle) 现象：输入超过上下文窗口的 30-40% 后，模型对中间部分内容的关注度显著下降。当前公式完全没有考虑这个疲劳区间。

**实例**：32K 模型，当前允许 1750 行/批。1750 行英文字幕 ≈ 14000 tokens，占上下文的 44%——已经踩进疲劳区。但 505 段（约 8000 tokens，25%）一次性喂入是安全的。

**根因**：旧公式在设计时只考虑了「会不会炸」（超过上下文上限），没有考虑「会不会累」（超过疲劳区间）。

**修复方案**：把公式从「上下文上限」改为「疲劳上限」。疲劳系数取 30%。

```python
def _compute_chunk_size(llm) -> int:
    ctx = getattr(llm, 'max_input_tokens', 32768) or 32768
    fatigue = int(ctx * 0.30)       # 30% 疲劳系数
    available = fatigue - 2000      # prompt 开销
    raw = available // 8
    return max(200, min(raw, 1200))
```

**效果对比**：

| 模型 | 旧公式 | 新公式 |
|------|--------|--------|
| 8K | 200 | 200 |
| 32K | 1750 | 950 |
| 64K+ | 3000（上限） | 1200（上限） |

**改动范围**：`v2/backend/translation/orchestrator.py`，一个函数，~5 行。

**⏸ 未修**

## P254 | 2026-06-11 | 🟡 HIGH 转录模型智能路由——按源语言切换主引擎 + Whisper 骨架组装句级字幕

**来源**：2026-06-11 model-comparison.md §9.3。Jobs + 双方工程师三轮审查定稿。2026-06-12 流水线架构重设计。

**问题**：Whisper 永远主引擎。英语 Qwen3 WER 7.39% vs Whisper 9.90%，更准更快的模型当配角。且千问只输出词级时间戳（无天然句级分割），切换后缺乏句级字幕切分能力。

**最终方案（综合 P254+P261+P247）**：

**第一步：语言路由**——在 transcriber_v2.py 两个引擎都跑完后，if 语句交换主副引擎（英语/日/韩/中 → Qwen 主）。

**第二步：L1 先验证**——L1（SenseVoice + Qwen FA）直接对千问原始词级数据做交叉验证，不被任何重装操作干扰（P261）。

**第三步：Whisper 骨架组装**——L1 完成后，用 Whisper 的 570 个句级时间窗口做"筐"，把千问的词按时间落入筐里组成句级字幕。时间戳用千问词级精度。边界处可能偏差 0.1-0.3 秒（1-2 词错位），交给 P247 Refiner 修复。

**实现 Checklist**：
```
□ 1. _qwen_chunks_to_segments(qwen_chunks, whisper_segments=results):
     - 打平千问所有词级时间戳到一个总清单
     - 遍历 Whisper 每个时间窗口 [t_start, t_end]
     - 收集所有开始时间落在窗口里的千问词
     - 拼接成句子，时间戳取首词 start 和末词 end
     - 无千问词时用 Whisper 原文兜底
□ 2. 语言路由:
     if detected_lang in {"en", "ja", "ko", "zh"} and qwen_chunks:
         保存 _qwen_raw（供 L1/L2 交叉验证）
         primary = _qwen_chunks_to_segments(qwen_chunks, whisper_segments=results)
         results = primary; qwen_chunks = list(_qwen_raw)
□ 3. 保留原始 Qwen 词级时间戳供 L1/L2
```

**依赖**：换 MLX ASR 库（moona3k）。Qwen 独立检测语言/分块/出时间戳。

**代码量**：~60 行。零新文件。

**流水线位置**：转录 → P254 路由 → L1（千问原稿）→ 词变句子 → L2 → P247 Refiner → 翻译

**✅ 已修**（框架已实现，待 P260/P261 配套修改完成）

## P255 | 2026-06-12 | 🟡 MEDIUM Windows 端千问 ASR 时间戳未启用——L2 归窗走猜测路径

**来源**：2026-06-12 V3.07 换库后架构审查（Steve Jobs 视角）发现。

**问题**：`qwen_asr_runner.py:191-197` PyTorch 路径调用 `model.transcribe()` 时没有传 `return_time_stamps=True`，返回 `segments = []`（无词级时间戳）。导致 Windows 端 L2 归窗只能走 `_assign_by_similarity()`（基于词干相似度猜测放置），而不是 MLX 路径的 `_assign_by_timestamps()`（精确时间戳放置）。

**影响**：
- Windows 用户（主要用户群）的 L2 交叉验证精度低于 Mac 开发环境
- `_assign_by_similarity()` 是猜测逻辑，跨窗口误放会降低窗口词干重叠分数
- 存在两套完全不同的归窗代码路径，维护成本翻倍

**验证方式**：测试文件 `test_arch7_pipeline.py:43` 已记录 `v0.0.6 API: audio, context, language, return_time_stamps`——库 API 支持该参数。需在 Windows 真机上验证：① 传 `return_time_stamps=True` 后返回格式是什么 ② 时间戳精度是否与 MLX 路径一致

**修复方案**：
1. 在 Windows 机器上测试 `return_time_stamps=True` 的返回格式
2. 确认格式后修改 `_transcribe_chunk()` 和 `run_qwen_asr()` 的 PyTorch 路径，提取词级时间戳
3. 如果格式与 MLX 一致（`[{text, start, end}]`），可以删除 `_assign_by_similarity()` 整条代码路径

**前置条件**：需要 Windows 真机 + `qwen-asr==0.0.6` 环境

**⏸ 未修**（待 Windows 真机测试）

## P256 | 2026-06-12 | 🟡 MEDIUM L2 固定 30 秒窗口切割句子——智能窗口边界对齐

**来源**：2026-06-12 V3.07 换库后架构审查（用户提出）。

**问题**：L2 交叉验证的 30 秒窗口是固定切割（0-30s, 30-60s, 60-90s...）。当句子跨越窗口边界时，会导致：
- 句子被切成两半分到不同窗口，词干重叠分数被人为拉低
- 同一段语音的文字在两个模型的归窗不一致
- 例：Whisper 句子 28-31s 被 overlap 放入窗口 0，千问词 "大概率" 在 30.2s 被放入窗口 1

**方案**：占比滑动 + 千问画线。

**核心设计决策**（2026-06-12 锁定）：
1. **窗口边界由千问的时间戳决定**（千问有词级时间戳，精度最高）
2. **占比滑动规则**：分界线处如果有句子被切断，看千问这个句子在分界线两边的占比。占比大的那一边"吃掉"整个句子——窗口边界向反方向滑动
   - 例1：千问句子 28-31s 骑在 30s 分界线上，28-30s 占 2/3 → 窗口向后滑到 31s（前一个窗口吃掉整句）
   - 例2：千问句子 29.5-35s 骑在 30s 分界线上，29.5-30s 占 1/11 → 窗口向前滑到 29.5s（后一个窗口吃掉整句）
3. **Whisper 文字归到千问画好的窗口里**（Whisper 不参与画线决策）
4. 窗口大小有硬约束：最短 20 秒、最长 40 秒，超出范围强制切

**为什么不需要两个模型投票**：两个模型听到同一段音频，句子的起止时间方向几乎不会相反。差别只在精度（千问 28.3-31.1s vs Whisper 28-32s），不在方向。用精度更高的那个画线就够了。

**好处**：
- 每个窗口内都是完整句子，词干对比更可靠
- 消除因窗口边界切割导致的假 YELLOW
- 一个模型画线、另一个跟着走——零冲突解决逻辑

**验证策略**：Mac 端先验证逻辑正确性，Windows 端再验证体验一致性。两端都必须通过。

**前置条件**：P255（Windows 端千问时间戳启用）——没有词级时间戳就无法知道千问句子的结尾位置

**✅ 已修（Mac 端）**（2026-06-12）。裁剪版 6 步流水线完成：office-hours → TDD（21 新测试）→ 双签影响面 → 双签 diff（7 findings）→ 设计一致性走查 → verification（512/513 全量零回归）。Windows 端依赖 P255 完成后启用。

**审查发现（待未来版本清理）**：
- `_find_best_window` 死代码 L556-557（bisect_right 重复计算）
- BGM 检查 O(W×S) 性能（L605-608，每个窗口遍历全部 segments）
- `WINDOW_SIZE` / `_SMART_WINDOW_DEFAULT` 双源常量
- chunk→segments 三次迭代可提取公共 helper
- `_has_timestamp_segments` 多次调用可缓存
- `_get_audio_duration` 与 `extractor.py:get_video_duration` 重复


## P258 | 2026-06-12 | 🟡 LOW UI [?] 百分比计算 bug——分子分母用不同阶段的段数，误报 15%

**来源**：2026-06-12 ASR 分支实测后 SRT 对比分析。用户发现 UI 显示 15%，但实际只有 7.1%。

**问题**：`worker.py:350` 在句子拆分**前**取了 `total = len(segments)`（520 段），但 `worker.py:587` 在句子拆分**后**数了 `qm_count`（77 个）。前端 `r = qm_count / segment_count = 77/520 = 14.8% ≈ 15%`。但拆分后实际段数是 1083，正确比例应为 `77/1083 = 7.1%`。

**根因**：T4 LLM 句子拆分（L501-511）把一个原始段切成多行字幕，子段全部继承 `uncertain=True`。`total` 变量在拆分前就赋值了，拆分后没更新。分母 520、分子 77——两个数字来自不同的阶段，不匹配。

**修复**：把 `qm_count` 提前到 L350（与 `total` 同时、句子拆分前），删掉 L587 的重新计数。`total`（分母）和 `qm_count`（分子）都基于拆分前的段数——一致、干净、不影响"翻译失败"显示。

**✅ 已修**（2026-06-12）


## P259 | 2026-06-12 | 🟡 MEDIUM 跨语言 L3 语速阈值未用真实多语言视频验证

**来源**：2026-06-12 用户提出。相关问题在 problems.md L525、L603、L698 三处提及但从未正式编号。

**问题**：P250 修复后，L3 已按语言分 5 个阈值：

| 语言 | 阈值 | 单位 | 依据 |
|------|------|------|------|
| zh | 15 | 字/秒 | 音节计时语言，极限 12-15 |
| ja | 15 | 字/秒 | 同上 |
| en | 8 | 词/秒 | 重音计时语言，极限 ~10 |
| ru | 8 | 词/秒 | 同上 |
| auto | 7 | 词/秒 | 兜底 |

这些阈值是基于语言学理论推算的——**从未用真实的多语言视频验证过。** 只测过英文（London VLOG），中文/日文/俄文视频的实际 L3 表现未知。

**历史上三次提到这个问题都说了同一句话**（L525/L603/L698）：
> "不提前解决没有出现的问题。"

但现在情况变了——P250 已经修完，阈值字典已经存在，只需跑几个不同语言的测试视频看 L3 是否有异常误报/漏报。验证成本极低（跑 4-5 个不同语言视频看 `[?]` 率是否正常即可），不再需要"等出现再说"。

**验证方法**：
1. 准备 5 个语言的测试视频：英文 ✅（已测）、中文、日文、俄文、一个 auto 兜底语言（如泰文）
2. 各跑一次 precise tier 完整流水线
3. 检查 L3 语速异常的日志数量——如果某个语言的异常数量异常高（>50 条）或异常低（0 条），说明阈值不合理

**⏸ 未修**（待有对应语言测试视频时执行）


## P260 | 2026-06-12 | ~~🔴 P0~~ ✅ P247 Refiner 放错位置——LLM 英文校对在翻译之后执行

**来源**：2026-06-12 Run 7 实测发现问题（用户指出）。

**问题**：P247 Refiner（LLM 转录后英文校对）被放在了 worker.py 的 Step 4（字幕生成），而 Step 4 排在 Step 3（翻译）之后。用户的需求是"转录完成后用 LLM 校对英文原文"——校对必须在翻译之前。

**当前错误流程**：
```
转录 → 翻译（拿着错误英文翻成了错误中文）→ 字幕生成（这时候才校对英文，来不及了）
```

**最终方案（综合 P247/P254/P261）**：

P247 Refiner 的位置从 Step 4（字幕生成/翻译后）移到 L2 交叉验证之后、翻译开始之前。完整流水线：

```
转录 → P254 路由 → L1（千问原稿）→ Whisper 骨架组装词变句子 → L2 交叉验证 → P247 Refiner（双稿裁判+边界修复+润色）→ 翻译
```

**同时修复**：
- Refiner 的 `_build_llm_backends(api_keys)` 可能返回空列表，`if _backends:` 需加 `else: _log.warning(...)` 以便排查
- `TranslationResult` 拆盒子已修（`hasattr(_refined, "text")`）
- Qwen 加载失败静默问题已修（`exc_info=True`）

**✅ 已修**（2026-06-14）。Refiner 在 worker.py:431-511 执行，翻译在 line 513 开始。


## P261 | 2026-06-12 | ~~🔴 P0~~ ✅ P254 合成段导致 L1 失效——需改为"先验证再合成"

**来源**：2026-06-12 Run 7 实测——370 段中 360 段被 L1 打 `[?]`（97%）。2026-06-12 用户 + Jobs 流水线架构定稿。

**问题**：当前 Whisper 骨架方案（Run 7）的流程是"先合成再验证"——先把千问的词按 Whisper 时间窗口重新打包，再送给 L1。L1 不认识合成段 → 97% [?]。

**最终方案（"先验证再合成"）**：
1. L1 交叉验证时，拿到的仍然是千问的原始词级数据（`_qwen_raw`），不被任何人重新包装
2. L1 验证通过后，再用 Whisper 的句子边界把千问的词组装成句级字幕
3. 时间戳用千问自己的词级时间戳确定句子的开始和结束时间
4. 边界偏差（0.1-0.3 秒，1-2 词错位）交给 P247 Refiner 通过双稿裁判修复

**完整流水线位置（L1 和组装可并行）**：
```
转录 → P254 路由 → 保留 _qwen_raw
                  ├─→ L1 看 _qwen_raw 原稿（不打 [?]）     ← 并行
                  └─→ Whisper 骨架组装（_qwen_chunks_to_segments） ← 并行
                  ↓（汇合）
                  L2 交叉验证 → P247 Refiner（双稿裁判+边界修复）→ 翻译
```

**关键区别**：旧方案是"改好了再给校对员看"——校对员看不懂（97% [?]）。新方案是"校对员先看原稿，看完了再誊写到正式卷子上"。

**需改动文件**：transcriber_v2.py 的 `_qwen_chunks_to_segments()` 和 P254 路由逻辑——拆分为两步：L1 用 `_qwen_raw`，组装用 whisper_segments。

**✅ 已修**（2026-06-14）。L1 在 transcriber_v2.py:760-768 对 Whisper 原稿跑交叉验证，组装在 line 770 之后执行。L1 标记通过 bisect 时间重叠搬运到组装后的段落（line 773-821）。

## P262 | 2026-06-13 | 🔴 P1 `_is_short_word_exempt` 被误删，`[?]` 率飙到 12.9%

**问题**：2026-06-12 TDD 修改中删除了 `_is_short_word_exempt` 函数（调用处 hallucination_filter.py:191 + 定义处 :200-210）。该函数的作用是：英文 ≤3 词 / 中日文 ≤4 字的极短句，L1 直接放行不打 `[?]`。原因是强制对齐算法在 1-2 词的短句上物理层面无法可靠对准时间戳（需要 ≥3-4 词才有足够音素样本）。

**现象**：396 段字幕中 46 段带 `[?]`（12.9%），全部来自短句的中文翻译（"好吧""天哪""自己""火车"等）。这些大部分是真人说话，不是幻觉。

**解决方案**：
1. 恢复 `_is_short_word_exempt`，但不是原样恢复——加固：短词 + SenseVoice 标签="Speech" → 豁免；短词 + 标签="Music/Noise" → 不豁免
2. 即：`if len(text.split()) <= 3 and tag == "Speech": return False`

**影响文件**：hallucination_filter.py
**状态**：✅ 已修（2026-06-13）



## P268 | 2026-06-14 | 🔴 P0 L1 承载段错误：应固定使用 SenseVoice × Qwen 时间戳验证

**问题**：L1 的设计原则不应该跟“谁是主引擎”绑定。不管最终字幕文字来自 Qwen 还是 Whisper，L1 都应该用 SenseVoice 判断某个时间范围是不是人声，再用 Qwen ASR 内部 FA 时间戳确认这段时间有没有 Qwen 词，因为当前规划里 Qwen 时间戳是最可靠的时间证据。当前代码实际仍然让 L1 遍历 Whisper 原稿段：SenseVoice 标签先贴到 `_whisper_segments`，L1 再对 `_whisper_segments` 调 `_layer1_dual_signal()`，最后把过滤/不确定标记按时间重叠搬运到 Qwen 组装段。

**为什么这是 bug**：

1. **和设计不一致**：`MLX-ASR-换库方案.md` 明确写“L1: SV标签 × Qwen时间戳（不用外部FA）”，并写“Qwen segments 供 L1+L2 共用”。P254/P261 也写“L1（千问原稿）”“L1 看 `_qwen_raw` 原稿”。但当前实现是“Qwen 时间戳作为证据，Whisper 段作为工作台”。

2. **Whisper 质量会决定 L1 是否有效**：FJIN-141 第一次测试里 Whisper large-v3 输出 3375 段，SenseVoice 因此处理 25 chunks，L1 有大量判断；第二次 Whisper tiny 只输出 3 段，SenseVoice 只处理 2 chunks，L1 只出现 2 条“SV误判清除”，几乎没有起到过滤作用。Qwen 两次都稳定输出 25 chunks / 5132 segments，但 L1 没直接消费这些 Qwen 原稿段。这个问题跟主引擎无关，只要 L1 的承载段来自 Whisper，L1 就会被 Whisper 段数和质量拖住。

3. **后续没有第二次 L1 兜底**：`filter_hallucinations(... skip_l1=(_which_primary == "qwen"))` 在 Qwen 主引擎时跳过过滤器内部 L1。也就是说前面这次基于 Whisper 段的 L1 一旦失效，后面不会重新用 Qwen 段补跑。

**关键代码证据**：

- `v2/backend/transcriber_v2.py:612-624`：Qwen 主引擎时保存 `_whisper_segments = results` 和 `_qwen_raw = qwen_chunks`。
- `v2/backend/transcriber_v2.py:652-665`：`_sv_target = _whisper_segments if _whisper_segments else results`，SenseVoice 的分段目标仍是 Whisper 段。
- `v2/backend/transcriber_v2.py:760-768`：L1 构造 `_l1_align = _qwen_segments_as_alignment(qwen_chunks)`，但循环对象是 `for _ws in _whisper_segments`。
- `v2/backend/transcriber_v2.py:770-821`：L1 标记通过时间重叠从 Whisper 段搬运到 Qwen 组装段。
- `v2/backend/transcriber_v2.py:823-828`：Qwen 主引擎时后续过滤器跳过 L1。

**测试日志证据**：

- 第一次 FJIN-141：`/Users/andy/.video-subber/logs/4754e3947e/debug.log` 显示 Whisper 转录 3375 段，SenseVoice 分段处理 25 chunks / 3375 segments，L1 大量输出“SV误判清除/双确认丢弃”，最终返回 2249 段。
- 第二次 FJIN-141：`/Users/andy/.video-subber/logs/b544184558/user.log` 显示 Whisper 转录 3 段，Qwen 仍输出 25 chunks，SenseVoice 分段处理 2 chunks / 3 segments，L1 只输出 2 条“SV误判清除”，最终返回 944 段。
- 两次 transcript 对比：第一次 2249 段中 1418 段纯数字；第二次 944 段纯数字为 0，但 883 段 uncertain，且 944 段全部没有 `whisper_text`。

**根因**：P261 修复时只解决了“先组装再 L1 导致 97% [?]”的问题，但实现选择了一个半修方案：把 L1 提前到组装前跑，同时仍然沿用 Whisper 原稿段作为 L1 的承载段。这样确实避免了“合成段不被 L1 认识”，但没有完成设计要求的“L1 固定看 Qwen 时间戳 + SenseVoice 声音事件”。当 Whisper 输出异常时，SenseVoice 覆盖和 L1 有效性仍然被 Whisper 拖垮。

**修复方向**：

1. **SenseVoice 脱离 Whisper 段**：改为按固定时间窗口或 Qwen 时间范围跑全音频，产出可按时间查询的声音事件。固定窗口指每隔一段时间切一块音频，比如 30 秒或 60 秒，不依赖 Whisper 分出来多少段。
2. **L1 直接验证 Qwen 段**：从 `_qwen_raw` 构造 Qwen-side segments，给每个 Qwen 段按时间贴 SenseVoice 标签，再用 Qwen 内部 FA 时间戳判断这段有没有词。不能再遍历 `_whisper_segments`。
3. **Whisper 降级为参考稿**：Whisper 可以继续用于参考边界、双稿裁判或少数语言回退，但不能决定 L1 有没有工作对象。
4. **补测试**：新增一个回归测试，模拟 Qwen 有 5132 个词级段、Whisper 只有 3 段，断言 L1 仍会覆盖 Qwen 时间范围，而不是只处理 3 个 Whisper 段。

**成人影片场景影响分析**：

日本成人影片里有大量非对白声音：叫声、喘息、笑声、肉体碰撞声、床/环境摩擦声、背景音乐。L1 修正后可以明显改善“非人声声音被误转成字幕”的问题，但不能单独保证字幕完全正确。

理想 L1 对四类情况的处理应该是：

1. **真实对白**：SenseVoice 判断为 Speech，Qwen 时间戳里有词 → 保留。这是最稳定的情况。
2. **碰撞/摩擦/背景音乐**：SenseVoice 判断为非 Speech，Qwen 时间戳里没有词 → 删除。这是 P268 修完后最直接改善的部分。
3. **叫声/喘息但不是对白**：SenseVoice 可能判断为 Speech 或 Breath，Qwen 可能输出很短的“あ”“ん”“はぁ”等词。当前 L1 如果只看“Qwen 有时间戳”会倾向保留，仍可能产出无意义字幕。这里需要在 L1 后增加“对白价值判断”：短促、重复、无语义的叫声默认不翻译或只按配置保留。
4. **噪声触发 Qwen 幻觉**：肉体碰撞声、节奏性摩擦声可能让 ASR 幻听成短词或数字。即使 Qwen 给了时间戳，也不等于这是真对白。SenseVoice 非 Speech + Qwen 只有 1-2 个短 token 时，不能直接判定“SV 错了”，应标记为可疑或删除。

所以 P268 的修复目标不是“所有成人片字幕立刻正确”，而是先让 L1 的工作对象正确：全程覆盖 Qwen 时间线，不再被 Whisper 段数拖垮。修完后还需要一层“对白/非对白”策略，专门处理叫声、喘息和撞击声。

**实际修复（2026-06-14）**：

1. `transcriber_v2.py` 新增 `_qwen_chunks_to_segments()`，从 Qwen 词级时间戳构造 L1 工作段。
2. `transcriber_v2.py` 新增 `_propagate_l1_marks()`，把 L1 结果从 Qwen L1 段搬到最终组装段。
3. SenseVoice 的输入源改为 Qwen L1 段；L1 循环对象也改为 Qwen L1 段，不再遍历 `_whisper_segments`。
4. `_whisper_is_usable()` 替换旧健康判断：Whisper 段数太少、覆盖率太低、数字幻觉太多、重复坍缩太严重时，不能再当可靠参考。
5. L1 增加 SV 误杀保护：SenseVoice 标非人声但段内有 CJK 文本时，不直接删除，改标 `uncertain`。

**验证**：
- `test/test_p268_p269_pipeline.py` 覆盖 Qwen L1 时间线、L1 标记搬运、Whisper 健康判断。
- 相关路径验证：`110 passed, 2 warnings`。
- 完整真实 FJIN-141 长视频端到端待在有 Metal/GPU/API key 的开发环境重跑。

**状态**：✅ 已修（2026-06-14，真实长视频待重跑）

## P269 | 2026-06-14 | 🔴 P1 P247 Refiner 缺少单稿语义疏通模式

**问题**：P247 Refiner 的历史目标是“LLM 双稿裁判 + 边界修复 + 润色去问号”。当 Qwen 和 Whisper 两份稿子都可用时，LLM 应对比两份稿子，修正 `claw/clock` 这类单词误听；当一个模型崩掉、只剩 Qwen 单稿时，LLM 也应对 Qwen 单稿做句意疏通：补标点、理顺明显乱序、修正明显不成句的碎片、处理 `[?]` 不确定段。

当前代码只实现了“双稿冲突裁判”的窄版：

- `v2/backend/worker.py:431-444` 构造 prompt 时，只有 `seg.text` 和 `seg.whisper_text` 不一致才标 `[CONFLICT]`。
- `v2/backend/worker.py:436-437`：如果没有 `whisper_text`，直接把 Qwen 文本当普通上下文写入，不标冲突。
- `v2/backend/worker.py:456-458`：system prompt 写明“只有当 Qwen 明显听错而 Whisper 听对时才修正”，并要求“绝对不要抄写或输出没有任何冲突的上下文段落”。
- `v2/backend/worker.py:481-487`：只要 LLM 返回了 `corrections`，未被纠正的段会被统一清掉 `uncertain=False`，等于把“没有冲突”当作“裁判通过”。

**实测证据**：

- 第一次 FJIN-141：Whisper large-v3 输出 3375 段，但大量数字幻觉；P247 日志 `corrections=0`，没有修正数字污染。
- 第二次 FJIN-141：Whisper tiny 只输出 3 段，最终 944 个 transcript 段全部 `whisper_text=""`，其中 883 段 `uncertain=True`；P247 仍日志 `corrections=0`。
- 第二次 transcript 样例已经出现明显碎片：`"少しですかうんお"`、`"父さんとお母さん今"`、`"ククだよ"`。这类句子没有参考稿时，当前 Refiner 不会做任何疏通。

**根因**：P263/P264 重构时，为了解决“全篇双稿塞给 LLM 导致注意力稀释”和“LLM 大改字幕”的风险，把 Refiner 收缩成 JSON corrections 模式和单段安全阀。这个改动保住了安全性，但砍掉了 P247 原设计里的“全局润色 / 边界修复 / 单稿语义疏通”能力。项目记录把 P247 标为 ✅ 已修，实际只修了“双稿冲突裁判 + 安全阀”，没有覆盖“参考稿缺失时怎么办”。

**解决方案**：

1. **给 Refiner 加模式判断**：
   - 双稿模式：有足够 `whisper_text` 且 Whisper 健康时，继续走现有 `[CONFLICT]` 裁判。
   - 单稿模式：`whisper_text` 覆盖率过低、Whisper 段数过少、或 Whisper 被健康检查判坏时，改为 Qwen 单稿疏通。

2. **单稿模式只处理高风险段**：
   - `uncertain=True` 的段；
   - 长度极短但上下文明显不完整的段；
   - 语序明显破碎的段；
   - L3/L4 标记过但未删除的段；
   - 不处理稳定长句，避免 LLM 过度改写。

3. **单稿模式输出仍用 JSON corrections**：
   - 只返回需要改的段：`{"corrections":[{"seg_id":12,"fixed_text":"...","reason":"broken_sentence"}]}`。
   - 未返回的段保持原样，不自动清掉 `uncertain`。

4. **安全阀分级**：
   - 双稿裁判：继续用 `_clean_dist()` 阈值 0.80。
   - 单稿疏通：允许更大改动，但只限同一段内部，不允许跨段合并、删除时间戳、改 ID。若需要跨段合并，先记录为组装问题，不在 Refiner 偷偷做。

5. **日志必须可见**：
   - 打印 `P247 Refiner mode=dual/single`。
   - 打印 `whisper_coverage`、`uncertain_count`、`candidate_count`、`corrections_count`。
   - 如果单稿模式跳过，要写明原因。

**和 P268 的区别**：

- P268 修 L1：让 SenseVoice × Qwen 时间戳覆盖全片，不再被 Whisper tiny 的 3 段拖垮。
- P269 修 Refiner：当 Whisper 参考稿缺失或崩坏时，让 LLM 仍能对 Qwen 单稿做有限疏通，而不是 `corrections=0` 后直接放行。

**实际修复（2026-06-14）**：

1. `worker.py` 新增 `_choose_refiner_mode()`：Whisper 健康时走 dual，不健康时走 single。
2. `worker.py` 新增 `_select_refiner_segments()`：single 模式只发送 `uncertain`、过短、明显碎句的候选段。
3. `worker.py` 新增 `_build_refiner_prompt()`：dual 继续双稿裁判，single 只做 Qwen 单稿有限疏通。
4. `worker.py` 新增 `_apply_refiner_corrections()`：single 模式 `corrections=0` 不清 `uncertain`；只清实际修正过的段。
5. single prompt 明确：不新增剧情、不跨段合并、不把叫声/喘息扩写成完整对白。

**验证**：
- `test/test_p268_p269_pipeline.py` 覆盖 single/dual 问号清理行为。
- worker 冒烟测试生成 SRT 后，`uncertain=True` 的段保留为 `世界。 [?]`。
- 完整真实 FJIN-141 长视频端到端待在有 Metal/GPU/API key 的开发环境重跑。

**状态**：✅ 已修（2026-06-14，真实长视频待重跑）

## P270 | 2026-06-14 | 🟡 P1 L4 CJK 重复短句被当结构性垃圾硬删

**问题**：L4 的重复正则会把 1-4 个字符组成的重复串直接删除，例如：

- `もっともっともっと`
- `違う違う違う`
- `やだ やだ やだ`
- `哈哈哈哈哈哈`

这些在日语/中文/韩语里可能是真实短句、反应词或叫声，不应该和数字幻觉、ASCII 垃圾重复用同一条硬删规则处理。

**原因**：`_RE_REPEATED_PATTERN` 和 `_RE_REPEATED_WORDS` 只看“重复形状”，不看字符类型。旧逻辑在非 GREEN 段里直接 `filtered=True`。

**解决方案**：

1. 新增 `_has_cjk_text()`，先用 NFKC 标准化，避免把全角数字 `１２３` 误判成 CJK。
2. 新增 `_mark_cjk_repetition_uncertain()`。
3. CJK 重复：不硬删，标 `uncertain=True`，`filter_reason="layer4_cjk_repetition_uncertain"`。
4. 非 CJK 重复、数字重复、符号重复：继续硬删。

**验证**：
- `もっともっともっと` → 保留，标 `uncertain`。
- `やだ やだ やだ` → 保留，标 `uncertain`。
- `１２３１２３１２３` → NFKC 后变 `123123123`，继续删除。
- `hey hey hey` → 继续删除。
- 相关路径验证：`110 passed, 2 warnings`。

**状态**：✅ 已修（2026-06-14）

## P263 + P264 | 2026-06-13 | 🔴 P1 双稿裁判重构——完整稿子 + 差异标注 + 锚点分组

**合并**：原 P263（准确率）和 P264（token 浪费）实为一个问题的两面：双稿裁判把 356 段全塞给 LLM 导致注意力稀释。合并为一条，统一方案解决。

### 问题

Refiner 将 356 段 × 两份稿子 = 约 2500 行 SRT 一次性发给 LLM。其中大部分段 Qwen 和 Whisper 的文字完全一样，不需要裁判。真正有差异的只有 ~20 段。但 LLM 在 2500 行的海洋里注意力被稀释，"claw"和"clock"只差一个字母，LLM 未能发现。

### 根本原因

1. **无预过滤**：所有段一视同仁发给 LLM，80%+ token 浪费在不需要裁判的段上
2. **无差异标注**：两份稿子平铺，LLM 不知道去哪找差异，只能全篇扫描
3. **裁判 ≠ 翻译**：裁判只需要"读"标注处并做选择题，不需要像翻译那样逐段"理解+产出"。不会疲劳，不需要滑窗

### 解决方案：完整稿子 + 差异标注 + 锚点分组

**核心思路**（乔布斯方案）：LLM 相当于批卷老师——不需要通篇阅读，只看到有标注的地方，对比两个版本，回头看上下文判断。给 LLM 完整主引擎稿子做背景，只在有差异的地方标注参考版本。

#### Step 0：程序比对（每次必做，零 LLM 成本）

```python
for seg in segments:
    seg.text == seg.whisper_text → 一样，跳过
    seg.text != seg.whisper_text → 不一样，标记为"锚点"
```

#### Step 1：Token 估算与阈值判断

```
Token 估算：总段数 × 25 token/段（含标注，经验值，实际 15-35）
安全阈值：50000 token（DeepSeek 64K 窗口留 25% 余量给回复）
触发分组：总Token > 50000 → 进入分组模式，否则单批模式

参考数据（22分钟视频实测）：
  356 段 → 800 行 SRT → ~15000 token → 单批模式即可

各语言差异：
  字符数不影响判断——所有参数（阈值、安全区）都按"段数"算，不按字数
  段数 = 说话时间 / 平均每段时长（2-4秒），与语言无关
  ±50 段 ≈ 前后各 3 分钟上下文，任何语言都够用
```

#### Step 2：单批模式（总Token ≤ 50000，覆盖 90% 场景）

Prompt 格式：
```
你是字幕校对裁判。下面是完整字幕稿（[Primary]）。
只关注标注了 ⚠️ 的地方——那里有一份 [Reference] 版本供你对比。
没有标注的段直接保留，不要改。

段0: こんにちは
段1: 今日はいい天気ですね
段2: そうですね
...
段25: ⚠️ 私は時計を持っている
    [参考] 私は爪を持っている
段26: じゃあ行きましょう
...

规则：
1. 只判断 ⚠️ 标注的段。其余直接照抄。
2. 对比两个版本，根据上下文选更合理的。
3. 不确定就保留 Primary。
4. 输出格式：每行一段，只输出文字。
```

#### Step 3：锚点分组模式（总Token > 50000，2小时+ 电影专用）

```
算法：
1. 每个锚点画"安全区"：前后各 ±50 段（≈3分钟上下文）
2. 安全区有重叠 → 合并为"任务区"
3. 每个任务区单独发送 LLM（格式同单批模式）

示例（2400 段，35 个锚点）：
  任务区1: 段 0-210   （8个锚点）
  任务区2: 段 350-720 （12个锚点）
  任务区3: 段 890-1300（9个锚点）
  任务区4: 段 1600-2100（6个锚点）

为什么需要 ±50 段而非 ±20 段：
  电影中可能有长对话场景。3 分钟安全区覆盖绝大多数对话上下文。
  极端情况（"开头问时间，20分钟后说想要手表"）不需要远端上下文——
  LLM 判断 "clock vs claw" 只需要常识 + 附近 5 段，不需要 20 分钟前的对话。

为什么不切固定大小的块：
  固定块可能在标注句需要的上游上下文中间切断。
  锚点驱动分组 = 让内容决定在哪切，不是人为画线。
```

#### Step 4：解析 LLM 返回结果

LLM 返回每行一段文字。按段号顺序写回 `seg.text`，带 `[?]` 的保持不确定标记。

### 影响文件

- `worker.py`：Refiner prompt 构建逻辑（预过滤 + 标注生成 + 锚点分组 + prompt 拼装）
- 不涉及其他文件

### 实施优先级（用户决定一次性全部实现）

| 功能 | 说明 |
|------|------|
| 预过滤 + 差异标注 | 程序比对 seg.text vs seg.whisper_text，只标注差异 |
| 单批模式 | 完整稿子 + 标注，一次 LLM 调用 |
| Token 估算 | 根据段数估算 token，判断走单批还是分组 |
| 锚点分组 | 超长视频自动分任务区，每区单独调用 LLM |

**覆盖场景**（所有语言通用）：
- 日剧 40 分钟 → 单批，一次 LLM 调用
- 电影 1.5 小时 → 单批，一次 LLM 调用
- 电影 2 小时 → 单批或刚好触达分组
- 电影 2.5 小时 → 锚点分组，3-5 次 LLM 调用

**状态**：✅ 已修（2026-06-13）



## P265 | 2026-06-13 | 🔴 P1 Qwen 词级重组产出碎片化文本（CJK 语言）

**问题**：`transcriber_v2.py` 的 `_qwen_chunks_to_segments()` 函数在重组 Qwen 词级时间戳到 Whisper 句级骨架时，产出碎片化文本。50 分钟日语视频（FJIN-141）1486 段字幕中，899 段有实际文字，其中 453 段（50.4%）带不该有的分词空格，416 段（46%）内容与 Whisper 原文错位。

**原因**：两个 bug 叠加——

1. **Bug 1（第 216 行）**：`" ".join(w["text"] for w in in_range)` 用空格拼接 Qwen 词。英语正确，但日语/中文/韩语不需要空格。产出如 `おじ さん の チンチン 舐め` 而非 `おじさんのチンチン舐め`，翻译引擎收到碎片文本后截断翻译。

2. **Bug 2（第 214 行）**：`in_range = [w for w in qwen_words if w["end"] >= t0 and w["start"] <= t1]` 用简单时间重叠分配词。Qwen 和 Whisper 对同一段音频切分方式不同，同一个词可能横跨两个 Whisper 窗口边界，导致同一个词被分配到多个窗口、或一句话的词被打散到好几个窗口。

**解决方案**：
- Bug 1：对 CJK 语言用 `"".join()`，对英语等西方语言保持 `" ".join()`。语言信息在 `detected_lang` 变量（第 281 行）已检测。
- Bug 2：用最大重叠比例分配词（一个词只归属重叠最多的窗口），或分配完后合并相邻不完整句子，或换组装策略。

**涉及文件**：`transcriber_v2.py:175-229`（`_qwen_chunks_to_segments` 函数），调用位置第 517 行。

**详细文档**：`P255-重组碎片化问题-工程师文档.md`（自包含，可直接交给外部工程师阅读）

**解决方案（实际采用）**：
- Bug 1：加 `lang` 参数，CJK 用 `"".join()`，其他语言保持 `" ".join()`
- Bug 2：中点落入判定 `t0 <= (w["start"] + w["end"]) / 2 <= t1`，每个词最多归属一个窗口
- 防御性排序 `qwen_words.sort(key=lambda w: (w["start"] + w["end"]) / 2)`
- 调用处（第 517 行）传入 `lang=detected_lang`

**状态**：✅ 已修（2026-06-13）


## P271 | 2026-06-14 | 🟡 P1 当前版本测试被旧版本目录污染，导致全量验证无法给绿灯

**问题**：在 `video-subtitle/v2/` 根目录直接跑完整测试时，pytest 会递归扫到 `v2.10/v2.11/v2.12/v3.01/v3.02/v3.03/v3.04/v3.05` 等旧版本目录里的测试文件。这些旧测试和当前版本测试文件重名，会触发 `import file mismatch`，导致当前版本全量测试无法得到可靠结论。

**原因**：当前 `v2/` 目录同时放了活跃代码和多个旧版本快照。根目录没有明确的 pytest 收集范围，测试工具会默认往下递归，把历史快照里的 `test_*.py` 也当成当前版本测试。

**影响**：这不是字幕生成逻辑本身的 bug，但会阻塞当前版本验证。P268/P269/P270 的相关路径测试已经通过，真实 FJIN-141 还需要重跑；如果全量 pytest 一直被旧目录干扰，就无法判断“当前代码是否全绿”。

**解决方案**：
1. 在当前活跃版本目录加测试范围配置，只收集当前版本的 `test/`，不递归旧版本快照。
2. 保留旧版本目录，不删除历史；只是让当前版本测试不要自动扫进去。
3. 后续版本整理时，把旧版本快照统一移入只读归档区，避免活跃代码目录和历史快照混放。

**状态**：⏳ 代码已修，待真实 FJIN-141 重跑验证（2026-06-15：新增 `extract_start` 回填、padding 裁剪、裁剪后 text 重建；相关窄测试 23 条通过；全量仍受 Metal/MLX 与 SenseVoice/funasr 环境问题阻塞）


## P272 | 2026-06-15 | 🔴 P0 FJIN-141 新跑结果出现翻译接缝审查污染字幕

**问题**：2026-06-14 23:55 新跑的 `65b1f1b3db/FJIN-141.srt` 中，字幕后半段出现大量“版本A/版本B/选B”这类审查解释文字。抽样统计：`translated.json` 里至少 90 条中文译文包含这类解释文本。用户实际观察到 49:37 附近有人说话但字幕缺失，随后说话内容延迟出现；50:00 附近多行字幕瞬间闪过。

**证据**：
- 新结果：`/Users/andy/.video-subber/jobs/65b1f1b3db/FJIN-141.srt`
- 旧结果：`/Users/andy/.video-subber/archives/cache-clear-2026-06-14-p268-rerun/b544184558/FJIN-141.srt`
- 新日志：`/Users/andy/.video-subber/logs/65b1f1b3db/debug.log:1598-1611`
- 代码位置：`v2/backend/translation/orchestrator.py:393-461`

**原因**：`_review_seam()` 的 prompt 要求“输出格式：[编号] 最终译文”，但 LLM 返回了“[783] 版本A...版本B...选B”这种解释。解析器 `_INDEXED_LINE_RE` 只检查行首是否有 `[编号]`，没有检查内容是不是纯译文，于是把解释当成正式字幕写回 `translated`。这不是翻译引擎失败，而是接缝审查的输出没有被约束和校验。

**影响**：字幕会把审查说明当正文显示；接缝区被污染后，用户看到的不是翻译，而是内部评审文字。这个问题独立于 ASR，必须先修，否则即使 ASR 时间轴修好，字幕后半段仍会被解释文本污染。

**解决方案**：
1. `_review_seam()` 返回内容必须做“解释文本过滤”：包含 `版本A`、`版本B`、`选A`、`选B`、`更自然`、`更符合` 等审查措辞时，不允许写回字幕。
2. 接缝审查失败或返回解释时，保留已有版本，不覆盖 `translated`。
3. 加测试：模拟 `_review_seam()` 返回解释文本，确认不会污染字幕；模拟返回纯译文，确认可正常写回。

**状态**：⏸ 未修

**真实重跑复核（2026-06-15，`e5494b352d`）**：
- 最新 SRT 和 `translated.json` 未再出现“版本A/版本B/选A/选B/字幕质量审查员”等解释文本，说明最终字幕暂时没有被污染。
- 但 `debug.log` 里仍能看到模型返回“字幕质量审查员”“版本A/版本B/选A”这类内容，说明根因还在，只是这次没有写进最终 SRT。
- 结论：P272 从“污染进入成品”降级为“LLM 仍会输出解释，成品暂时被挡住”。不能标已修，仍需修 `_review_seam()` 的输出过滤和失败回退。


## P273 | 2026-06-15 | 🔴 P0 Qwen 主引擎组装仍有长空洞和瞬闪短字幕

**问题**：P268/P269/P270 修完后，FJIN-141 新跑结果从 944 段变成 1049 段，但 49:20-50:20 仍然有明显时间轴问题：49:37 左右有人说话但没有字幕；50:01 附近连续出现多条 0.07s-0.25s 的短字幕，肉眼看起来就是“三行字幕一下子过去”。

**证据**：
- 新 `transcript.json`：`/Users/andy/.video-subber/jobs/65b1f1b3db/cache/c2e90d73d46a/transcript.json`
- 旧 `transcript.json`：`/Users/andy/.video-subber/archives/cache-clear-2026-06-14-p268-rerun/b544184558/cache/c2e90d73d46a/transcript.json`
- 新结果 49:20-50:20：14 段，全部 `uncertain=True`，其中 3 段短于 0.2 秒，最大空洞 6.96 秒；全片 1049 段中 157 段短于 0.2 秒，433 个大于 3 秒空洞。
- 对应代码：`v2/backend/transcriber_v2.py:406-445` 的 `_assemble_qwen_primary()`。

**原因**：Qwen 主引擎现在按词级时间戳组装字幕，断句条件主要是：词间停顿 `gap >= 0.5s`、句尾标点、单段超过 8 秒、单段超过 40 字。成人片这种音频里有大量短促语气词、叫声、停顿和断续说话，词级组装会产生两个坏结果：一是把真实短句拆成 0.08s 这种看不见的字幕；二是把一串内容推到后面的长段里，用户感觉“刚才说的话过一会儿才出现”。

**补充诊断（2026-06-15）**：外部工程师复核 49:37-50:00 后指出同一根因链条：
1. `_words_to_segment()` 当前没有把 Qwen 词级时间戳保存到 `seg.words`，导致组装后的段 `words=[]`。
2. `word_align_segments()` 本来可以用 `seg.words` 做更细的时间切分，但遇到 `words=[]` 会直接跳过。
3. `_build_split_segments()` 和 `_rule_based_split()` 只能按中文字数比例分配时间，导致“一字长挂”和“多句瞬闪”：例如一个 7.47 秒长段被翻译拆成 5 句后，第一句“ 不 ”占满大部分时间，后面几句被挤到 0.1 秒级。
4. 因此 P268/P269/P270 修的是“保留/过滤哪些段”，但还没修“段本身怎么切、词级时间戳怎么传下去”。

**二次审查补充（2026-06-15）**：外部工程师进一步指出更上游的问题：`_SENTENCE_ENDERS = ("。", "！", "？", "!", "?", "…")` 对 Qwen 日语输出基本失效。FJIN-141 的 Qwen 日语原文几乎不带句末标点，所以 `_assemble_qwen_primary()` 的句末标点断句命中率为 0%。实际只剩 `gap >= 0.5s` 和 `span >= 8.0 / len >= 40` 两条规则在工作，导致连续日语对白被一直攒到硬限制才切开。修复重点应前移：先让组装函数识别日语句尾，而不是主要依赖输出前整形。

**双签审查补充（2026-06-15）**：日语句尾列表需要精简，避免误断。`れ` 会误匹配 `これ/それ/あれ/どれ` 等高频句中词，`くる` 也容易误伤 `〜てくる`，本轮删除。`の/さ/で` 句中用法较多，不能和强句末词同门槛处理，应提高到 `gap >= 0.3s` 才断。最终采用强/弱两级：强句末词 `よ/ね/な/わ/ぞ/ぜ/か/だ/ます/たい` 使用 `gap >= 0.15s`，弱句末词 `の/さ/で` 使用 `gap >= 0.3s`。

**影响**：即使文字识别到了，字幕也不可读。49:37 到 50:00 的问题不是单纯漏识别，而是“组装出的字幕节奏不符合观看需求”。这会直接破坏最终成片体验。

**解决方案**：
1. `_words_to_segment()` 必须保存 `seg.words`，并统一 `text/word/start/end` 字段，保证后续 `word_align_segments()` 和拆句函数能拿到词级时间戳。
2. `_assemble_qwen_primary()` 增加日语句尾 + 微停顿断句，采用两级规则：强句末词 `よ/ね/な/わ/ぞ/ぜ/か/だ/ます/たい` 在 `gap >= 0.15s` 时断；弱句末词 `の/さ/で` 在 `gap >= 0.3s` 时断。这里的“微停顿”是 0.1-0.3 秒级的自然说话间隔，当前 0.5 秒阈值对日语连续对白太宽。
3. 硬限制从 `8s/40字` 调整为更温和兜底，例如日语 `5s/25字`。它只负责兜底，不能替代句尾断句；不采用 `3.5s/18字` 这种过激硬切，避免把一句话从中间切碎。
4. `word_align_segments()` 需要兼容 Qwen `text` 字段，并补充日语句尾检测；否则即使 `seg.words` 有了，原来的英文标点正则仍会找不到边界。
5. `_build_split_segments()` 使用 `seg.words` 分配拆句时间作为后续优化保留；只有 `words=[]` 时才允许退回按字符比例分配并记录 warning。
6. 输出前整形不作为主方案，只保留最小日志和质量统计。先修上游组装，不在 SRT 成品上做复杂时间拉伸/挪移。
7. 保留 P268 的 L1 逻辑不回退；这次只处理 Qwen 主稿的“日语断句、组装成字幕、词级时间戳下传”阶段。

**测试要求**：
1. `_words_to_segment()` 会保留 `seg.words`，字段至少包含 `text/word/start/end`。
2. Qwen 日语无标点时，强句末词 + `gap >= 0.15s` 能断句。
3. 弱句末词 `の/さ/で` 只有 `gap >= 0.3s` 才断，`gap = 0.2s` 不断。
4. `これ` + `gap = 0.2s` 不应该断句，防止未来重新加入 `れ`。
5. 修复后统计 FJIN-141：段数、`>3.5s` 长段数、`<0.1s` 极短段数、49:37-50:00 局部字幕是否恢复。若断太碎，把强句末阈值从 0.15 调到 0.2；若仍有长段，再考虑降到 0.12。

**实现记录（2026-06-15）**：
- 已新增 `v2/test/test_p273_qwen_ja_assembly.py`，覆盖 `seg.words` 保留、强/弱日语句尾断句、`これ + 0.2s` 不误断、英语不触发日语微停顿规则。
- 已修改 `v2/backend/transcriber_v2.py`：新增强/弱日语句尾常量，`_words_to_segment()` 保留 `seg.words`，`_assemble_qwen_primary()` 对日语启用强/弱句尾 + 微停顿断句，并把日语硬限制改为 `5s/25字`。
- 验证：`66 passed in 3.82s`，命令为 `.venv/bin/python -m pytest test/test_p273_qwen_ja_assembly.py test/test_p268_p269_pipeline.py test/test_p251_qwen_window_assign.py test/test_phase1d_qwen_health.py test/test_phase1d_integration.py test/test_p228_timestamp_fix.py -q`。

**真实重跑复核（2026-06-15）**：
- 新结果：`/Users/andy/.video-subber/jobs/723b866ccb/FJIN-141.srt`
- 新日志：`/Users/andy/.video-subber/logs/723b866ccb/user.log`
- 新 transcript：`/Users/andy/.video-subber/jobs/723b866ccb/cache/c2e90d73d46a/transcript.json`
- 结论：P273 修改部分有效，但没有通过真实长视频验证。有效点：`transcript.json` 里 1127/1127 段都有 `words`，说明 Qwen 词级时间戳已保留下来；49:20-50:20 局部从 Run 3 的 0.07s/0.12s/0.17s 连续闪屏，变成 Run 4 只有两个 0.08s 极短段，局部可读性略好；Run 4 未再出现“版本A/版本B/选B”字幕污染。未解决点：全片仍有 179 条短于 0.2s 的字幕、435 个大于 3s 的空洞、最大空洞 67.733s；日志仍有 143 条 `L3 不可能时长` 和 157 条 `L3 语速异常`；49:20-50:20 仍有全部 `uncertain=True` 和 0.08s 短字幕。
- 进一步判断：问题已经不是“词级时间戳没有下传”，而是 Qwen 主引擎组装策略仍会把 1 个词单独切成 0.08s 字幕，并留下几十秒未覆盖区间。下一轮应继续沿 P273 处理：给 `_assemble_qwen_primary()` 加最小时长/短词并入规则，并对超长空洞做日志化诊断，而不是回退 P268 的 L1 逻辑。

**P274 后真实重跑复核（2026-06-15，`e5494b352d`）**：
- 最新结果：`/Users/andy/.video-subber/jobs/e5494b352d/FJIN-141.srt`
- 最新 transcript：`/Users/andy/.video-subber/jobs/e5494b352d/cache/c2e90d73d46a/transcript.json`
- 最新日志：`/Users/andy/.video-subber/logs/e5494b352d/user.log`
- 有效点：最新 `transcript.json` 1099/1099 段都有 `words`；21:39 附近的同一句字幕从旧 run 的 `1306.867-1310.468` 前移到 `1301.867-1305.468`，说明 P274 把 5 秒系统性后移拉回来了。
- 未解决点：全片仍有 188 条短于 0.2s 的字幕；`uncertain` 仍有 846/1099，约 77%。其中 `[?]` 主要来自 L2 的 YELLOW/LOW_DATA，不是翻译阶段或 SRT 阶段临时加的。
- SRT 间隔口径修正：全片仍有 417 个大于 3s、151 个大于 10s 的 SRT 间隔，最大间隔 126.920 秒。但用户人工复核后确认最大间隔 `2187.702-2325.101`（36:27.702-38:45.101）没有人说话，40:10-41:35 也没有对白。因此这些只能叫“字幕间隔”，不能直接叫“漏字幕空洞”。
- 日志侧改善：`L3 不可能时长` 从旧 run 的 143 降到 13，`L3 语速异常` 从 157 降到 83，`L4 CJK重复保留` 从 142 降到 29。说明过滤层压力下降，但字幕最终可读性仍未过关。
- 结论：P273 不是完全无效。P274 后用户人工确认 5 秒延迟已解决，大部分内容已经恢复成句子，不再像早期那样大量连续闪字幕或一句话被拆成小词。P273 剩余问题改为两类：① `[?]` 过多，主要由 L2 低证据窗口打标；② 仍有短字幕，需要等 P275 的人声地图完成后，才能判断哪些 SRT 间隔是真漏字幕，哪些只是正常无对白。下一步先做 P275，再决定是否继续加最小时长/短词并入。

**状态**：⏸ 部分改善，未完全关闭（5 秒延迟已由 P274 修复；句子化明显改善；剩余主问题是 `[?]` 过多和短字幕。SRT 长间隔必须等 P275 人声地图后再判定是否是真漏字幕）


## P274 | 2026-06-15 | 🔴 P0 Qwen 长音频切片 padding 导致字幕时间整体后移

**问题**：FJIN-141 第四次真实重跑 `723b866ccb` 中，用户指出 21:39 人物在说话但没有字幕，后面才出现字幕，22:00 后尤其明显。复查 21:20-22:40 发现，`transcript.json` 和最终 SRT 时间完全一致，说明不是翻译或 SRT 渲染阶段推迟，而是 ASR 源稿时间已经错位。21:39 附近只有 `1297.268-1297.348` 的 0.08s 字幕“か/吗”，前一条到它之间有 18.416s 空洞；`1324.760` 前又有 9.412s 空洞。

**证据**：
- 当前源稿：`/Users/andy/.video-subber/jobs/723b866ccb/cache/c2e90d73d46a/transcript.json`
- 当前字幕：`/Users/andy/.video-subber/jobs/723b866ccb/FJIN-141.srt`
- 当前日志：`/Users/andy/.video-subber/logs/723b866ccb/user.log:9-10` 显示 `7431s > 20min` 后启用 `25 chunks`；`user.log:25` 显示问题时间点所在的 `chunk 5/25` 是 `1200.0s-1500.0s`。
- 代码位置：`v2/backend/core/qwen_asr_runner.py:138-160` 提取切片时前后各加 5s padding；`v2/backend/core/qwen_asr_runner.py:185-190` 回填时间时直接加 `t_start`。
- 对照：Run 3 `65b1f1b3db` 和 Run 4 `723b866ccb` 在 21:20-22:40 的 SRT 时间几乎一致，说明 P273 的词级下传/日语断句修改没有解决这一段的时间骨架。
- 历史证据：P168 已在 2026-06-07 记录过同类问题“分段转录时间戳偏移 5 秒——缓冲区未修正”，当时修的是旧路径。当前 `qwen_asr_runner.py:138-190` 的长音频 Qwen 主引擎路径再次出现 `t_start` 回填，属于同类问题回归或新路径漏修。
- Git 证据：`v3.07` 旧路径 `_process_single_chunk()` 已经在 `f9f203d` 中使用 `extract_start` 回填；`874ab188 feat(V3.07): Mac 端千问 ASR 换库 gabrimatic → moona3k` 新增/改写 `_transcribe_chunk()` 后，MLX `result.segments` 路径改为 `s["start"] + t_start`，没有继承 P168 修法。

**原因**：长音频切片时，代码实际传给 Qwen 的音频不是 `t_start-t_end`，而是 `t_start-5s` 到 `t_end+5s`。模型返回的词时间是相对这个“带 padding 的临时音频”的。当前代码却把每个词的 `start/end` 加上 `t_start`，没有加真正的 `extract_start`，也没有把 padding 区间裁掉。结果是从第二个切片开始，字幕会系统性后移约 5 秒；21:39 位于 `1200-1500s` 这个切片，正好命中。这个错误曾在 P168 旧路径修过，说明当前问题不是新概念，而是新 Qwen 主链/长音频路径没有继承旧修法。P273 造成的 0.08s 短字幕和大空洞会把这个 5 秒后移放大成肉眼可见的“说完后字幕才出现”。

**影响**：这是比 P273 更上游的时间轴错误。只修 `_assemble_qwen_primary()` 的最小时长/短词并入，不能消除 20 分钟后字幕整体偏晚的问题。P273 和 P274 需要一起修：P274 先把词级时间放回真实视频时间；P273 再把词组装成可读字幕。

**解决方案**：
1. `_extract_audio_chunk()` 返回 `extract_start/extract_end`，或新建小结构保存真实提取起点。
2. `_transcribe_chunk()` 的 MLX 时间回填改为 `raw_start + extract_start`，不是 `raw_start + t_start`。
3. 裁掉 padding 区间：只保留落在 `[t_start, t_end]` 的词；跨边界词需要 clamp 到边界，避免相邻 chunk 重复。
4. 加单元测试：`t_start=1200`、`_AUDIO_PAD_S=5`、模型返回 `start=10` 时，绝对时间应为 `1205`；模型返回 `start=3` 时应被识别为 padding 区内容并裁掉或 clamp。
5. 修完后真实重跑 FJIN-141，重点复查 21:39、22:00、49:37、50:00 四个点，统计 `<0.2s`、`>3s` 空洞和最大空洞。
6. 防回归：测试必须覆盖“旧 P168 修过的 PyTorch time_stamps 路径”和“当前 MLX result.segments 路径”，防止以后再换库时只修一边。

**实现记录（2026-06-15）**：
- 已修改 `v2/backend/core/qwen_asr_runner.py`：MLX chunk 时间回填改用 `extract_start`，padding 前后区间会被裁掉，MLX chunk `text` 由裁剪后的 segments 重建。
- 已补 P274 单测，覆盖 padding 前、中、后三段，确认只保留真实 chunk 区间里的文字。
- 相关窄测试 23 条通过；全量验证仍受当前环境 `No Metal device available` 和 SenseVoice/funasr 段错误阻塞，不能给全量绿灯。

**真实重跑复核（2026-06-15，`e5494b352d`）**：
- 21:39 附近：旧 run `723b866ccb` 的 `1297.268-1297.347` 变成最新 `1292.268-1292.347`，正好提前 5 秒。
- 22:00 附近：旧 run `723b866ccb` 的 `1306.867-1310.468` 变成最新 `1301.867-1305.468`，也正好提前 5 秒。
- 结论：P274 的“长音频 chunk padding 导致 5 秒系统性后移”已被真实重跑验证为有效。
- 注意：P274 只负责 5 秒系统性后移。后续不再用单纯 SRT gap 判断 P273 是否漏字幕；用户已确认最大 SRT 间隔 36:27-38:45 是无对白。真实漏字幕必须等 P275 生成人声/非人声时间轴后再判定。

**状态**：✅ 已修（5 秒系统性后移已由 `e5494b352d` 真实重跑验证；剩余 `[?]`、短字幕和 SRT 间隔判定不归 P274）


## P275 | 2026-06-15 | 🔴 P0 L1 只检查已有字幕段，缺少全片人声/非人声时间轴

**问题**：用户人工复核 FJIN-141 最新 run `e5494b352d` 后指出：40:10-41:35 没有对白，因此不能把 SRT 中这段长间隔叫做“漏字幕空洞”。理想 L1 应该在 SenseVoice 和 Qwen FA 交叉验证后，把整条音频切成“人声区”和“非人声区”；只有“人声区内没有字幕”才叫漏字幕。当前代码没有这张全片时间轴，导致诊断报告只能从 SRT 字幕间隔推测，容易把无对白段误报为空洞。

**证据**：
- 最新结果：`/Users/andy/.video-subber/jobs/e5494b352d/FJIN-141.srt`
- 最新日志：`/Users/andy/.video-subber/logs/e5494b352d/user.log`
- `user.log` 显示 SenseVoice 分段处理 25 chunks，并输出标签分布 `{'BGM': 7, 'Speech': 17, 'Breath': 1}`，但最终日志没有输出全片“人声/非人声区间地图”。
- 代码位置：`v2/backend/transcriber_v2.py` 先生成 Qwen L1 段，再给这些已有段贴 SenseVoice 标签，最后 `_propagate_l1_marks()` 把标记搬到组装后的字幕段。
- 代码位置：`v2/backend/core/hallucination_filter.py:_layer1_dual_signal()` 的输入是单个 `seg`，它回答的是“这条字幕段该保留/删除/打问号吗”，不是“这段原始音频有没有人声”。

**原因**：当前 L1 是“字幕段级检查”，不是“全音频人声地图”。它只遍历已经存在的 Qwen/字幕段；如果某段时间没有字幕段，L1 就没有对象可检查，也不会生成“这里确认是非人声”的记录。SenseVoice 虽然跑了全片分段，但结果没有被保存成可查询的人声/非人声 timeline，也没有用于“有对白但没字幕”的漏检诊断。

**影响**：
1. SRT gap 统计只能表示“字幕之间隔了多久”，不能表示“有人说话但没字幕”。
2. 用户人工复核发现最大 gap 实际无对白后，证明当前报告口径会误报。
3. 后续修 P273/P272 时，如果没有 L1 人声地图，就无法自动判断“长间隔是正常静音，还是 ASR 漏识别”。

**解决方案**：
1. L1 增加全片 timeline 输出：基于 SenseVoice chunk 结果生成 `voice_regions` / `non_voice_regions`，至少包含 `start/end/tag/confidence/source`。
2. 把 Qwen FA/词级时间戳叠到这张 timeline 上：有人声 + 有 Qwen 词 → 正常候选；有人声 + 无 Qwen 词 → 记为可能漏字幕；非人声 + 有 Qwen 短词 → 疑似幻觉；非人声 + 无 Qwen 词 → 正常无字幕。
3. 日志新增摘要：全片人声区间数、非人声区间数、有人声但无字幕区间数、最长真实漏字幕区间。
4. SRT 质量报告以后不再直接把 `gap > N` 叫“空洞 bug”；必须先查这个 gap 是否落在人声区。
5. 测试：构造 40:10-41:35 这种非人声长区间，断言不会报“漏字幕”；构造有人声但无 Qwen 字幕的区间，断言会报“可能漏字幕”。

**状态**：⏳ 待按 bug 流水线修复（当前只完成诊断登记，尚未改代码）

**分支规划（2026-06-15）**：
- 当前 ASR 分支不继续改 P275 代码，先提交并合并主线。合并说明必须写清：P274 已修，P273 句子化改善但 `[?]` 仍多，P275 未修。
- 合并主线后新开 `l1-voice-timeline` 分支，完整走 bug 流水线修 P275。
- P275 之后再开 `qwen-standard-mode` 分支，落实两档模式：标准 = Qwen ASR + SenseVoice；精确 = Qwen + Whisper + SenseVoice；删除 fast/极速。
- Whisper/MLX 换库放到 `mlx-asr-library-swap` 分支，不和 P275、模式改造混在一起。
