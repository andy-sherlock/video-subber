# P283 问号噪音 bug 修复流水线 — 2026-06-18

## 元信息
- 涉及问题：P283
- 涉及文件：
  - `v2/backend/transcriber_v2.py`
  - `v2/backend/core/hallucination_filter.py`
  - `v2/backend/core/subtitler.py`
  - `v2/backend/worker.py`
  - `v2/test/test_p281_voice_timeline_merge.py`
  - `v2/test/test_hallucination_filter.py`
- 当前进度：③/⑩

## ① investigate — 2026-06-18
**执行人**：Codex  
**依赖类型**：📖 读指定文件  
**读了什么**：
- `v2/backend/transcriber_v2.py:369-423` — 短词合并与非人声区短词标疑似
- `v2/backend/transcriber_v2.py:1179-1190` — 人声地图接入合并与质量日志
- `v2/backend/core/hallucination_filter.py:160-167` — L4 CJK 重复标疑似
- `v2/backend/core/hallucination_filter.py:696-716` — L3 语速异常标疑似
- `/Users/andy/.video-subber/logs/5c67f7a69b/debug.log:590-720` — 本次真实重跑问号来源日志
- `/Users/andy/Downloads/Video-Subber/FJIN-141.srt` — 本次真实输出字幕
- `/Users/andy/Downloads/Video-Subber/FJIN-141_上下文_20260618_104539.srt` — 上次输出字幕

### 结论（下一步只看这里）
本次问号反弹不是 Qwen 原始识别变差。6/17 job `28a951e756` 和 6/18 job `5c67f7a69b` 的 Qwen 原始输出都为 25 个 chunk、合计 2776 个 raw segments；SenseVoice 都为 1088 segments。变化发生在后处理阶段。

真实对比：
- 6/17：971 段，91 个 `[?]`，`<0.3s 且 <=2字` 为 114。
- 6/18：922 段，129 个 `[?]`，`<0.3s 且 <=2字` 为 85。

所以 P281 Phase 2 的“短词合并”方向有效，碎片减少；但“非人声区短词标疑似”把内部不确定性暴露给用户，造成问号噪音。

问号来源拆解：
- 总 `[?]`：129。
- L4 CJK 重复：日志匹配到 36 个最终问号。
- L3 语速异常：日志匹配到 45 个最终问号。
- 剩余约 50 个没有 L1/L3/L4 日志来源，结合 `P275 人声地图质量: 非人声短词=80` 和 `merge_short_subtitle_fragments()` 的代码，来源是 `voice_timeline_non_voice_short_fragment`。

根因有三层：
1. `merge_short_subtitle_fragments()` 在 `transcriber_v2.py:416-418` 对“孤立短词 + 落入非人声区”直接设置 `seg.uncertain=True`。这把 P275/P281 的内部质量信号变成了用户可见 `[?]`。
2. L1 已经有 459 次 `SV误判清除`，说明 Qwen 内部时间戳认为这些 BGM 区域里确实有词；但 P281 又用同一份粗人声地图给短词打问号，前后策略打架。
3. L3 在 `hallucination_filter.py:711-716` 对 0.08s 短词按字符/秒标疑似。真实样例有 `哦`、`喜欢`、`还有`、`啊`，这更像切分太碎，不应直接向用户显示问号。

修复原则：
- `[?]` 只用于强风险：L4 明显重复、L1 双信号强冲突、L2 双稿低可信。
- P275/P281 的“非人声短词”应保留为内部 `filter_reason` 或质量日志，不应默认 `uncertain=True`。
- L3 对短词只做内部原因记录；如果短词已经合并失败，也不要因为 0.08s 直接给用户显示 `[?]`。真正要处理的是合并，不是问号。

建议修复：
1. `merge_short_subtitle_fragments()`：非人声区孤立短词不再设置 `uncertain=True`，只设置 `filter_reason="voice_timeline_non_voice_short_fragment"`，并保留质量统计。
2. `_layer3_speech_rate()`：对 CJK/日语短词增加豁免。若 `units <= 3` 且 `duration < 0.35`，不设置 `uncertain=True`，只记录 `filter_reason` 或跳过。`duration < 0.1 and units > 2` 的硬删除也要避免误删短词，可改成仅在 `units > 6` 这类明显不可能时才过滤。
3. 保留 L4 重复标疑似，因为 `あああ`、`はははは...` 这类重复更接近模型幻觉或噪声，需要用户可见提示。

验收口径：
- 单元测试证明非人声短词不再直接产生用户可见问号。
- 单元测试证明日语短词不会因为 0.08s 被 L3 标问号。
- L4 重复仍然保留 `[?]`。
- 真实重跑后本次新增的 50 个左右非 L3/L4 问号应明显下降，超短碎片不能明显反弹。

### 涉及文件列表
- `v2/backend/transcriber_v2.py:369-423` — P281 合并逻辑和非人声短词标疑似
- `v2/backend/transcriber_v2.py:1179-1190` — P275 质量统计入口
- `v2/backend/core/hallucination_filter.py:696-716` — L3 语速异常策略
- `v2/backend/core/hallucination_filter.py:160-167` — L4 重复标疑似应保留
- `v2/backend/core/subtitler.py:80-89` — `uncertain=True` 如何变成用户可见 `[?]`
- `v2/backend/worker.py:548` — job 日志中问号统计

## ② 双签审查：plan-eng-review + steve-jobs-perspective — 2026-06-18
**执行人**：Codex  
**依赖类型**：📋 看记录  
**前置阅读**：只读第 ① 步结论，未继续读代码。

### plan-eng-review 结论
方案技术方向正确，但要控制边界：
- 不能删除 P275 的质量判断；只是不让它默认污染用户字幕。
- 不能关闭 L3；只给 CJK/日语短碎片加豁免，保留长文本高语速和明显不可能时长的保护。
- 不能关闭 L4；重复文本是强信号，继续用户可见 `[?]`。
- 测试必须覆盖三个行为：P281 非人声短词不显示问号、L3 日语短词不显示问号、L4 重复仍显示问号。

推荐执行顺序：
1. 先改测试，锁住新产品行为。
2. 再改 `merge_short_subtitle_fragments()`。
3. 再改 `_layer3_speech_rate()`。
4. 最后跑 P281、hallucination、P250、P268/P269 相关测试。

### steve-jobs-perspective 结论
产品判断：问号是给用户看的，不是给工程师看的。现在的 `[?]` 把 SenseVoice、Qwen、P275、L3 的内部争论直接甩到字幕上，这是错误体验。

该砍掉：
- 砍掉“非人声短词默认显示 `[?]`”。
- 砍掉“0.08s 短词默认显示 `[?]`”。

该保留：
- 保留强幻觉提示，比如明显重复的 `あああ`、`はははは...`。
- 保留内部质量日志，让工程师知道哪里风险高。

一句话目标：用户看到的是更干净的字幕；工程师在日志里看到风险地图。

## ③ 双签影响面：ln-021-codegraph + bill-gates — 2026-06-18
**执行人**：Codex  
**依赖类型**：📖🌐 读全项目  
**工具状态**：当前会话没有暴露 `hex-graph` MCP，按 `ln-021-codegraph` 规则使用手工 fallback：`rg` 查引用、`nl -ba` 查行号、按调用链分析。

### ln-021-codegraph 手工影响面
调用链：
1. `transcribe()` 在 `v2/backend/transcriber_v2.py:1179-1180` 调 `merge_short_subtitle_fragments()`。
2. `merge_short_subtitle_fragments()` 在 `v2/backend/transcriber_v2.py:416-418` 给非人声区孤立短词设置 `uncertain=True`。
3. `filter_hallucinations()` 在 `v2/backend/core/hallucination_filter.py:769+` 后续继续跑 L4/L5/L3。
4. `_layer3_speech_rate()` 在 `v2/backend/core/hallucination_filter.py:696-716` 会继续给短词设置 `uncertain=True`。
5. `build_srt()` 经 `v2/backend/core/subtitler.py:80-89` 把 `uncertain=True` 变成字幕文本 `[?]`。
6. `worker.py:548` 统计 `uncertain` 数量用于 UI/日志提示。

受影响测试：
- `v2/test/test_p281_voice_timeline_merge.py`：原测试要求非人声短词标 `uncertain=True`，需要按新产品决策改期望。
- `v2/test/test_hallucination_filter.py`：L3 语速测试需要新增日语短词豁免，不能破坏英文/中文高语速保护。
- `v2/test/test_p250_speech_rate_units.py`：P250 语速单位测试可能受影响，必须回归。
- `v2/test/test_p268_p269_pipeline.py`：L4 重复保留疑似和 Refiner 行为要回归。

### bill-gates 系统风险结论
系统层面风险可控。这个修复不是重做 ASR，也不是重做翻译，只是把“内部质量信号”和“用户可见问号”分开。

关键约束：
- 指标不能丢。`filter_reason` 和 P275 质量日志继续保留，否则以后无法定位。
- 用户体验要降噪。默认把内部不确定性显示成 `[?]` 会让系统看起来不可靠。
- 保留强保护。L4 和长文本不可能语速仍然要能标疑似或过滤。

风险点：
- 如果豁免 L3 太宽，可能放过真实幻觉。
- 如果非人声短词完全不记录，后续真实漏问题会没有证据。

因此最小安全修法是：不显示问号，但保留 `filter_reason` 和日志统计。

## ④ 双签 diff + 简化：review + elon-musk — 2026-06-18
**执行人**：Codex  
**依赖类型**：📖🌐 读全项目  
**读了什么**：
- `git status --short` — 当前工作区已有 P276/P277/P281 等大量未提交改动
- `git diff -- backend/transcriber_v2.py backend/core/hallucination_filter.py test/...` — 聚焦 P283 相关文件的既有 diff
- `review` skill 规则
- `elon-musk` skill 规则

### review 结论
当前 diff 很大，不能把 P283 混成一场大重构。P283 必须是小补丁：
- 只改 `v2/backend/transcriber_v2.py:416-418` 的非人声短词外显问号行为。
- 只改 `v2/backend/core/hallucination_filter.py:696-716` 的 L3 短词问号行为。
- 只改对应测试。
- 不碰标准模式路由、SenseVoice 运行、翻译、前端、worker 主流程。

潜在风险：
- 旧测试 `test_short_fragment_in_non_voice_region_is_marked_uncertain_not_deleted` 明确要求 `uncertain=True`，需要按新的产品判断改掉，否则代码和产品方向冲突。
- P250 语速测试可能依赖旧 L3 行为，必须回归。

### elon-musk 简化结论
First principles：`[?]` 是用户界面的一部分，不是 debug flag。内部信号必须和用户界面分离。

最小改法：
- 不加新状态机。
- 不加新配置。
- 不改 SRT 生成。
- 只把弱信号留在 `filter_reason`，不要设置 `uncertain=True`。

## ⑤ 设计一致性走查 — 2026-06-18
**执行人**：Codex  
**依赖类型**：📖 读指定文件  
**设计图**：P283 产品目标：内部质量信号与用户可见 `[?]` 分离。  
**代码入口**：
- `v2/backend/transcriber_v2.py:379-423` `merge_short_subtitle_fragments()`
- `v2/backend/core/hallucination_filter.py:696-716` `_layer3_speech_rate()`
- `v2/backend/core/subtitler.py:80-89` `[?]` 输出入口

### 逐步骤对照
| 设计步骤 | 代码对应 | 一致？ |
|---|---|---|
| 短词先尝试向前合并 | `transcriber_v2.py:390-402` | ✅ 一致 |
| 短词再尝试向后合并 | `transcriber_v2.py:403-415` | ✅ 一致 |
| 合不进去的非人声短词只做内部记录 | `transcriber_v2.py:416-418` | ❌ 当前会设置 `uncertain=True`，会变成用户问号 |
| L3 对明显不可能语速做保护 | `hallucination_filter.py:696-716` | ✅ 大方向一致 |
| L3 不应把 0.08s 日语短词直接暴露给用户 | `hallucination_filter.py:711-716` | ❌ 当前会设置 `uncertain=True` |
| 用户可见问号只来自强风险 | `subtitler.py:80-89` | ⚠️ 输出层只认 `uncertain`，所以上游必须控制好 `uncertain` 来源 |

### 数据流追踪
| 步骤 | 数据包 | 手（函数） | 读/改/扔 | 设计图上？ |
|---|---|---|---|---|
| Qwen 原始段 | 2776 raw segments | `_smart_assemble()` | 改为字幕句 | ✅ |
| 组装后短词 | 922/971 段 | `merge_short_subtitle_fragments()` | 合并；当前还会标问号 | ⚠️ 部分错 |
| 过滤后字幕 | surviving segments | `_layer3_speech_rate()` | 当前短词也会标问号 | ⚠️ 部分错 |
| SRT 输出 | `uncertain=True` | `build_srt()` | 加 `[?]` | ✅ |

### 偏差汇总
| # | 类型 | 描述 | 决定 |
|---|---|---|---|
| 1 | 多了 | P281 非人声短词直接设置 `uncertain=True` | 改为只记录 `filter_reason` |
| 2 | 多了 | L3 对 CJK/日语短词直接设置 `uncertain=True` | 加短词豁免，保留明显长文本异常 |

## ⑥ security-review — 2026-06-18
**执行人**：Codex  
**依赖类型**：📖 读指定文件  

### 结论
本次 P283 不涉及认证、权限、网络输入、文件路径拼接、API Key 使用或用户上传路径。安全审查可跳过深入扫描。

附带发现：本轮读取 `5c67f7a69b/config.json` 时看到 job 配置内仍保存明文 API Key。该问题不属于 P283，但应另记安全问题处理，不能混进本次问号修复。

## ⑦ test-driven-development — 2026-06-18
**执行人**：Codex  
**依赖类型**：📖 读指定文件  
**读了什么**：
- `v2/test/test_p281_voice_timeline_merge.py`
- `v2/test/test_hallucination_filter.py`
- `v2/backend/transcriber_v2.py:379-423`
- `v2/backend/core/hallucination_filter.py:696-720`

### RED：先写失败测试
新增/调整两个测试：
1. P281 非人声短词测试：非人声区孤立短词应保留 `filter_reason=voice_timeline_non_voice_short_fragment`，但不再设置 `uncertain=True`。
2. L3 日语短词测试：`好き` 这种 0.08s 的日语短词不应直接变成用户可见 `[?]`。

执行命令：
```bash
./.venv/bin/python -m pytest test/test_p281_voice_timeline_merge.py test/test_hallucination_filter.py::TestLayer3SpeechRate::test_japanese_short_fragment_does_not_show_user_uncertain -q
```

RED 结果：
- `2 failed, 1 passed`
- 失败点正好对应旧行为：P281 仍在设置 `uncertain=True`；L3 仍在把日语短词设置 `uncertain=True`。

### 修复代码
只改两处：
1. `v2/backend/transcriber_v2.py:416-418`：非人声区孤立短词只记录 `filter_reason`，不再设置 `uncertain=True`。
2. `v2/backend/core/hallucination_filter.py:700-702`：对 `zh/ja/ko` 且 `units <= 3` 且 `dur < 0.35` 的短碎片，只记录 `filter_reason=layer3_short_cjk_fragment`，不显示用户问号。

### GREEN：最小测试通过
执行命令：
```bash
./.venv/bin/python -m pytest test/test_p281_voice_timeline_merge.py test/test_hallucination_filter.py::TestLayer3SpeechRate::test_japanese_short_fragment_does_not_show_user_uncertain -q
```

GREEN 结果：
- `3 passed in 1.37s`

## ⑧ verification-before-completion — 2026-06-18
**执行人**：Codex  
**依赖类型**：🏃 跑应用  

### 相关回归测试
执行命令：
```bash
./.venv/bin/python -m pytest test/test_p281_voice_timeline_merge.py test/test_hallucination_filter.py test/test_p250_speech_rate_units.py test/test_p268_p269_pipeline.py -q
```

结果：
- `83 passed in 1.41s`

### 编译检查
执行命令：
```bash
./.venv/bin/python -m py_compile backend/transcriber_v2.py backend/core/hallucination_filter.py
```

结果：
- 通过，无语法错误。

### 验收结论
P283 代码层面已修复：P275/P281 的非人声短词、L3 的 CJK 短碎片，不再默认变成用户可见 `[?]`。  
但真实视频效果还必须重新跑 FJIN-141 验收，因为本次修复影响的是最终 SRT 的标疑似数量。

## ⑨ QA（Web 服务健康检查）— 2026-06-18
**执行人**：Codex  
**依赖类型**：🏃 跑应用  

### 检查结果
- 旧监听 PID `20935` 已停止。它占用 8766，但健康检查不可访问，属于旧服务残留。
- 新服务已通过 `screen` 会话 `video-subtitle-v307-test` 启动。
- 新监听 PID：`41198`。
- 启动命令：`v2/run-test.sh`，固定使用 `v2/.venv/bin/python`，不带 `--reload`。
- 日志显示：`Uvicorn running on http://0.0.0.0:8766`。
- 健康接口确认：`{"ok":true,"version":"3.07"}`。

### 结论
Web 服务已重新加载 P283 新代码。下一次从页面上传测试视频时，不会再跑 10:45 旧服务。

## ⑩ 端到端真实视频验收 — 2026-06-18
**执行人**：Codex  
**依赖类型**：🏃 跑应用  

### 状态
本轮未自动重跑 2 小时 FJIN-141。原因：真实端到端需要用户从 Web 页面重新上传/触发，且要保留这次输出与 6/17、6/18 两次字幕做人工对比。

### 下一轮验收指标
- `[?]` 数量应明显低于 6/18 的 `129/922`。
- `<0.3s 且 <=2字` 的超短段不能明显反弹。
- 单字段数量需要和 6/17、6/18 对比，不能只看文件大小。
- `P275 人声地图质量` 仍应输出：人声区、无字幕人声区、非人声短词、最长疑似漏字幕区间。
- L4 明显重复和强风险可以继续保留 `[?]`，不能为了降噪把强保护全关掉。
