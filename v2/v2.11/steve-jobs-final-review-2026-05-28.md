# Steve Jobs 最终审查 — V2.10 上线就绪度

审查日期：2026-05-28
已阅读：Code Review / Security Audit / Design Review / Health / QA / Engineering Review（6 份）

---

## 一句话判断

**This product has a soul. But it's not ready to ship. Three things stand between you and launch. Fix them, then get it in front of real users.**

---

## 我喜欢的地方

"拖进去，等一会，就能看懂。"

这句话不是 marketing copy。这是产品的整个定义。iPod 是 "1,000 songs in your pocket"——不是"5GB 容量的便携式 MP3 播放器"。你的产品也是一样——"Drag it in, wait, and you understand"——不是"基于 Whisper 模型的视频字幕自动生成工具"。如果你不能用一句话定义一个产品，这个产品就有问题。你做到了。

**设计有意图。** Design Score B+, AI Slop 评分 A-。你知道这两者的区别吗？大多数开发者的界面看起来像是 AI 吐出来的——紫色渐变、三个特性卡片、圆形图标。你的界面看起来像是一个人坐在那里想过的。Apple 的配色系统（`#f5f5f7`, `#0071e3`）、有层次的圆角（6px → 10px → 12px → 16px → 20px）、暗色模式下的表面层级——这些不是偶然的。这是有意为之。

**架构干净。** 7 个 LLM 后端，每个 ~35 行，共享一个基类。加一个新平台只需 ~25 行代码。这不是巧合——这是好的设计。`LLMBackend` 基类、子类只重写三个方法、`translate_raw` 和 `translate_batch` 由基类统一提供——这才是我说的"模块化"。不是把代码拆成 50 个文件就叫模块化。真正的模块化是：每个模块只做一件事，可以独立替换。

**前后端一致性做对了。** 14 个 API 端点全部通过，字段名一致，错误消息中文化。你知道有多少产品在上线前一天才发现前端发 `burn` 后端读 `burn_subtitles` 吗？很多。你提前检查了。

---

## 挡在发布面前的东西

### 🔴 必须修（2 小时）

**1. `os.getuid()` — 这是砖墙，不是 bug。**

`key_manager.py:24`。一行代码。Windows 上 `import key_manager` 直接崩溃。不是某个功能坏了——是整个应用不存在。用户双击 EXE，什么都不会发生。打包一个在目标平台上无法启动的 EXE，这是不可接受的。

修它。`os.getuid()` → `getpass.getuser()`。10 分钟。

**2. 版本号 "2.08" 到处都是。**

`main.py:469` 返回 `"version": "2.08"`。前端标题写 `v2.10`。`.version` 文件强制写回 `"2.08"` 每次启动。

如果你连版本号都搞不清楚，用户怎么报告 bug？"我在 2.08 上遇到问题"——但根本没有 2.08 这个公开发布版本。这是注意力的细节。而细节就是一切。

**3. 死代码。**

orchestrator.py 383-384 行有两行永远不可达的代码——`return` 在上面已经把函数结束了。main.py 有 `UploadFile, File, Form` 三个死导入。concurrent.futures 导入了但从未使用。

死代码不是无害的。它让阅读代码的人停下来思考"这段逻辑是做什么的？"——然后发现什么都不做。每一次停顿都是对信任的消耗。删掉它们。

---

### 🟡 上线前至少要开始做

**4. 零测试。不是开玩笑。**

Health 报告给 5.5/10，其中 20% 权重的"测试覆盖"得了 0 分。

我知道你会说什么——"这是 v1.0，我们手动测试过了，流水线跑通了。"当我做初代 Mac 的时候，我们也没有自动化测试。我们用人肉测试。但当时我们有 100 个 A player，而且软件只有几百 K。

你现在有 4,681 行代码。你的加密模块（`key_manager.encrypt/decrypt`）如果坏了——用户的 API Key 全部丢失。你的配置哈希函数（`_config_hash`）如果坏了——所有缓存去重逻辑崩溃。你的字幕断句函数（`_split_zh_into_sentences`）如果坏了——用户看到的字幕是一团乱麻。

你不需要 100% 覆盖率。你需要覆盖 4 个函数：加密、去重、断句、错误翻译。写 4 个单元测试。总共 50 行代码。这就够了——对你现在的规模来说。

**5. 激活码 "ANDY" 和 "MENG" 硬编码在源码里。**

任何人反编译你的 EXE（PyInstaller 打包的 EXE 用 `pyinstxtractor` 解包只需要 30 秒）就能看到这两个码。

这是安全问题吗？不完全是——你的产品卖 ¥1-3，没人会费劲破解。但这是品味问题。把开发者名字写死在产品代码里，这是业余行为。改成环境变量注入。

---

### 🟢 可以等（公网发布前）

**6. Windows 适配测试。**

QA 报告的最大盲区：一切测试都在 macOS 上完成。字体回退（PingFang SC → Microsoft YaHei → Segoe UI）、路径分隔符（`\` vs `/`）、ffmpeg 位置检测——这些逻辑已经写了，但从未在 Windows 上跑过。

你需要一台 Windows VM。或者找一个有 Windows 笔记本的朋友。花一个下午。跑一遍完整流水线。你会发现问题——一定会有。但这些问题不一定阻塞内测。

**7. EV Code Signing 证书。**

没有签名的 EXE 在 Windows 上会被 SmartScreen 拦截。用户双击后会看到蓝色的"Windows 已保护你的电脑"警告，然后需要点"更多信息"→"仍要运行"。70% 的用户在这一步放弃。签名证书 ~$300-500/年。对于公开上线来说这是必须的。对于内测——告诉用户忽略警告就行了。

**8. 前端 13 处 console.log。**

不是安全问题。不是功能问题。是 polish 问题。一个产品如果 console 里全是 debug 日志，就感觉像半成品。删掉它们——或者在 build step 里剥离。

---

## 我的最终判断

**现在可以内测。不能公网发布。**

修完 C1（`os.getuid()`）、C2/C3（版本号）、C4（死代码），然后在 10 个受信用户中做内测。不要等所有东西都完美了再发布——完美是迭代出来的，不是设计出来的。

让真实用户告诉你什么最重要。他们会发现我没发现的问题、你的代码审查没发现的问题、QA 测试没发现的问题。六份审查报告加起来不如 10 个真实用户用一周。

**Minimum viable launch checklist:**

| # | 任务 | 时间 | 阻塞 |
|---|------|------|------|
| 1 | 修 `os.getuid()` → `getpass.getuser()` | 10 min | 内测 |
| 2 | 版本号 "2.08" → "2.10" 全部替换 | 10 min | 内测 |
| 3 | 删 orchestrator 不可达代码 + main.py 死导入 | 10 min | 内测 |
| 4 | `key_manager` + `_config_hash` + `_friendly_error` 单测 | 1 h | 公网 |
| 5 | Windows VM 上跑一遍完整流水线 | 2 h | 公网 |
| 6 | 激活码改为全环境变量，删除硬编码 | 30 min | 公网 |
| 7 | PyInstaller .spec + Windows 打包 | 1 h | 公网 |
| 8 | EV Code Signing | 1-3 天 | 公网 |

---

## One more thing...

你知道吗——这个产品的 27 项功能清单，从 V2.01 到 V2.10 迭代了 10 个版本。每次只做一件事，做好，锁定，再进入下一个。这是对的。

但 10 个版本之后，你的版本号还写着 "2.08"。你的 health endpoint 还在说 2.08。你的 `.version` 文件还在强制写 2.08。你写的每一行代码都经过仔细思考，但你忘了更新三个字符串。

这就是为什么细节重要。不是因为这些字符串本身重要——是因为如果你忘了更新版本号，你还会忘了什么？

修掉它。然后把这个产品交到用户手里。用户不需要完美的产品——用户需要一个有用的产品。你这个产品有用。

**Verdict: APPROVED for private beta. Fix the 3 blockers, ship to 10 users, iterate.**
