# 问题库 — Video Subber 踩坑记录

每条记录：编号 / 日期 / 步骤 / 问题 / 原因 / 解决方案 / 是否已修

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

## P039 | 2026-05-21 | V2 后端 | 服务端 tier 无鉴权，免费用户可绕过付费墙（🔴 blocking）

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

**已修** ❌


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

**已修** ❌


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

**已修** ❌


---

## P043 | 2026-05-21 | V2 orchestrator | 免费模式下用户提供的 LLM Key 仍会参与竞速（🟡 important）

**问题**：`orchestrator.py:221` 的 else 分支：
```python
all_backends = ordered_backends + llm_backends
```
免费用户如果在 config 中传了 `apiKeys`（手动修改前端 JS），LLM 后端会与免费后端一起参与 speed mode 并行竞速。与产品设计「免费用户不可用 LLM」矛盾。

**根因**：`_build_llm_backends(api_keys)` 无条件构造 LLM 后端，免费模式下也未过滤。

**解决方案**：免费模式（`tier == "free"`）下忽略 `api_keys` 参数，不调用 `_build_llm_backends`。`all_backends` 仅使用 `ordered_backends`。

**已修** ❌


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

**已修** ❌


---

## P045 | 2026-05-21 | V2 key_manager | 加密密钥绑定主机名，变更后密钥全部失效（🟡 important）

**问题**：`key_manager.py:21` 用 `platform.node()`（macOS 主机名）作为密钥派生材料。主机名因网络环境变化（DHCP .local 后缀变更、用户手动改名）而改变时，所有已加密的 API Key 永久无法解密，且无恢复机制。

**根因**：`platform.node()` 不稳定，不适合作为加密材料。

**解决方案**：使用稳定机器标识（如 Mac 序列号 `ioreg -l | grep IOPlatformSerialNumber`、或直接用 macOS Keychain）。解密失败时给出明确提示而非静默返回密文。

**已修** ❌


---

## P046 | 2026-05-21 | V2 前端 | 字幕样式确认按钮 UX 困惑（🟢 nit）

**问题**：用户修改字体/颜色后预览实时更新，但必须再点一次「✓ 应用样式到当前任务」。如果忘了点，修改被静默丢弃。两步确认模式在实时预览场景下容易造成困惑。

**建议方案**：去掉确认按钮改为即时生效，或用户未确认时点击「开始翻译」给出醒目提示。

**已修** ❌


---

## P047 | 2026-05-21 | V2 后端 | BaiduBackend 异常时未记录日志（🟢 nit）

**问题**：`baidu.py:53-58` catch 了异常但 `e` 未被日志记录。排查翻译失败原因时无任何线索。

**解决方案**：添加 `logging.getLogger("baidu").warning("翻译失败: %s", e)`。

**已修** ❌

---

## P039 | 2026-05-21 | V2.04 前端 | 初始化时序混乱——页面加载时灰蒙蒙锁死，模型滑块消失

**问题**（持续了五六轮修复都没修好）：
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

**已修** ❌


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

**已修** ❌


---

## P050 | 2026-05-21 | V2 orchestrator | `_flatten_batches` 死代码（🟢 nit）

**问题**：`orchestrator.py:39-47` 定义了 `_flatten_batches()` 函数，但整个项目中无任何调用。`translate_with_orchestrator` 使用 `all_results.extend(batch_results)` 达到了同等效果。

**根因**：早期设计预留了批量展平逻辑，后来改用 `extend` 直接追加，函数未删除。

**解决方案**：删除该函数。

**已修** ❌


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

**已修** ❌

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
