# Design Review — V2.11 上线前审查

审查日期：2026-05-28
审查范围：`frontend/index.html`（995 行，内联 CSS + HTML + JS）
桌面窗口：`desktop.py` 配置 1100x750（最小 800x500）

---

## Design Score: B

| 维度 | 评分 | 说明 |
|------|------|------|
| 整体印象 | B+ | 干净、克制、设计语言明确 |
| 7 区域布局 | B | 单列 680px 合理，小窗口下拥挤 |
| 视觉一致性 | B | CSS 变量体系好，但行内样式泛滥 |
| 交互逻辑 | B- | 状态覆盖全面，但细节毛刺多 |
| 窗口适配 | B- | 800x500 最小窗口太紧，modal 和 toast 不受窗口尺寸影响 |
| **综合** | **B** | 可用但有 19 个值得修的设计问题 |

---

## AI Slop Score: A-

| 检测项 | 结果 |
|--------|------|
| 紫色/蓝色渐变背景 | 未检出 |
| 3 列 feature 卡片 + emoji 图标 | 未检出 |
| 均匀圆角（AI 模板特征） | 未检出 -- 6/10/12/16/20px 有层次 |
| 装饰性 blob / 浮动形状 | 未检出 |
| 通用 hero 文案 | 未检出 -- "拖进去，等一会，就能看懂" 独特 |
| `system-ui` / `Inter` 字体 | 检出 `-apple-system` -- 桌面工具场景合理 |
| 大量功能型 emoji | 检出 5 处（💡⚠ℹ🔧✓）-- 阈值内，非装饰滥用 |
| 模板化区域节奏 | 未检出 -- 单页工具应用，布局有意图 |

唯一的 AI Slop 减分项是 5 处 emoji 略多，以及 `-apple-system` 字体在 Windows 上回退到 Segoe UI 体验不统一。

---

## 1. 桌面窗口适配

### 1.1 宽度适配

```
desktop.py: 1100x750 / min 800x500
CSS:        .app { max-width:680px; padding:32px 20px 60px }
```

| 窗口宽度 | 内容区 | 侧边留白 | 判定 |
|----------|--------|----------|------|
| 1100px | 680px | 210px/侧 | 宽裕，呼吸感好 |
| 800px (min) | 680px | 60px/侧 | 偏紧但可行 |

**结论：宽度在 800-1100px 范围内无溢出风险。** `max-width:680px` + `padding:20px*2` = 720px < 800px 最小窗口宽度。

### 1.2 高度适配 -- 主要问题

**D1 -- 500px 最小高度严重不足 (line 28, line 158)**

实际内容估算（全部区域折叠后展开）：
- Header: ~72px (title + subtitle)
- Env bar: ~38px (4 个药丸标签)
- Region 1 上传区: ~160px (dropzone 44px padding * 2 + 路径行 + 指引)
- Region 2a 字幕设置: ~120px
- Region 2b 字幕样式: ~200px (5 selects + preview + confirm)
- Region 3 模型滑块: ~130px
- Region 4 翻译模式: ~80px (toggle + info)
- Region 5 API Key: ~120px (collapsed)
- Region 6 高级设置: ~40px (collapsed)
- Start 按钮: ~60px
- Job cards: dynamic
- 容器 padding: 32px top + 60px bottom = 92px
- Cards margin: 7 * 10px = 70px

**总计：~1182px** 内容在一个 500px 高的窗口中。用户首次打开时，即使所有可折叠区域都收起，能看到的也只有上传区和 1-2 个设置卡片。**必须滚动**。

**建议：** `min_size` 从 `(800, 500)` 提高到 `(800, 650)`，或将区域 2b/5/6 默认折叠。

### 1.3 Modal / Overlay 适配

| Overlay | 行号 | 宽度 | 窗口适配 |
|---------|------|------|----------|
| `.error-overlay` | 159-163 | `max-width:400px` | 在 800px 窗口中居中，OK |
| `.dev-login-overlay` | 165-169 | `max-width:360px;width:90%` | 响应式宽度，OK |
| `#llmFallbackOverlay` | 382-391 | `max-width:360px;width:90%` | 复用 dev-login 样式，OK |

Modal 全部使用 `position:fixed;inset:0` 覆盖整个 webview viewport，不受内容区 `max-width:680px` 限制。在 800x500 最小窗口中仍然可以完整显示（error box ~150px 高 + dev login box ~280px 高，都 < 500px）。

**结论：Modal 无窗口适配问题。**

### 1.4 Toast 适配

**D2 -- Toast 硬编码 fixed 定位，无窗口边界安全区 (line 772-776)**

```javascript
// line 774
t.style.cssText='position:fixed;bottom:20px;right:20px;...'
```

Toast 在 800px 窗口中有足够空间，但 `right:20px` 在 webview 环境中可能与窗口装饰（滚动条）重叠。无 `max-width` 限制，长消息会撑到窗口边缘。

---

## 2. 7 区域逐区审查

### Region 1: 上传区 (lines 226-246)

**D3 -- 暗色模式下虚线边框不可见 (line 51, line 18-22)**

```css
/* line 51 */
.dropzone{border:2px dashed var(--border);...}
```

暗色模式下 `--border: #636366` vs `--card: #2c2c2e`，对比度仅约 2.1:1。虚线在暗色卡片上几乎看不到。日间模式 `--border: #c7c7cc` vs `--card: #fff` 对比度约 2.5:1 同样偏低 -- 但在日间模式下更明显因为绝对亮度更高。

**建议：** 虚线边框单独设色，暗色模式下 `border-color: #636366`（当前值）应改为 `#8e8e93` 或更高。

**D4 -- 展开指引无 max-height / overflow (line 60-62)**

```css
/* line 61 */
.local-path-guide .expand{display:none;margin-top:4px;line-height:1.8;padding:8px 12px;...}
```

点击展开时内容追加到文档流中，无 `max-height` 过渡也无 `overflow-y:auto`。当前内容很短（约 4 行），不构成实际问题。但如果未来内容增长，这是隐患。

### Region 2a: 字幕设置 (lines 249-272)

**D5 -- Burn warning 显示/隐藏无过渡 (line 266, line 589)**

```javascript
// line 589
$('burnWarn').style.display=State.settings.burn?'block':'none';
```

警告框突然出现/消失，无任何高度/透明度过渡。视觉上生硬。

### Region 2b: 字幕样式 (lines 275-289)

**D6 -- 字幕预览框背景硬编码 (line 127)**

```css
/* line 127 */
.style-preview-box{background:#1a1a1a;color:#fff;...}
```

日间模式下 `#1a1a1a` 是页面中唯一的大面积深色块，视觉上割裂但可以接受（模拟视频黑底）。暗色模式下，`#1a1a1a` 与 `--bg: #1c1c1e` 几乎无法区分 -- 预览框失去边界感。

**建议：** 暗色模式下给预览框添加 `border:1px solid #555` 或浅色 outline。

**D7 -- 5 个 select 在窄屏下换行不整齐 (line 277-283)**

5 个 `<select>` 元素（字体/字号/颜色/描边/描边宽度）放在一个 `flex-wrap:wrap` 的 `.row` 中。在 680px 宽度下可以一行排开（~550px 总宽）。但在 480px 模拟窗口下会换行成 3+2 或 2+2+1，看起来凌乱。

### Region 3: Whisper 模型滑块 (lines 292-302)

**D8 -- 圆点无 hover 状态 (line 82-84, line 598)**

```css
/* line 82-84 */
.model-dot-circle{width:20px;height:20px;border-radius:50%;background:var(--card);border:2px solid var(--border);transition:all .2s}
.model-dot-circle.active{...}
```

`.model-dot-circle` 只有 `.active` 状态样式，没有 `:hover` 伪类。鼠标悬停时无视觉反馈。对比 toggle 按钮有完整的 `.active` 颜色变化。

**建议：** 添加 `.model-dot-wrap:hover .model-dot-circle { border-color: var(--blue); }`

**D9 -- 等距视觉可能误导性能差异的非线性 (line 79, line 594-600)**

5 个圆点均匀分布在线条上。但 tiny (150MB) 到 base (300MB) 差距 150MB，medium (1.5GB) 到 large-v3 (3GB) 差距 1.5GB -- 差了 10 倍。视觉等距暗示性能等距，实际不是。这是产品设计选择而非视觉 bug，但值得注意。

### Region 4: 翻译模式 (lines 305-315)

**D10 -- Priority 拖拽排序反馈弱 (line 97-98, line 615-618)**

```css
/* line 97 */
.priority-item{...cursor:grab;...}
```

`dragstart` 时只设置 `opacity:0.5`（line 615），被拖拽元素没有"抬起"效果（`box-shadow` / `scale`）。用户在拖拽时可能不确定是否成功开始拖拽。

**建议：** 拖拽中的元素添加 `box-shadow: 0 4px 12px rgba(0,0,0,0.15); transform: scale(1.02);`

### Region 5: API Key 管理 (lines 318-333)

**D11 -- API 行元素动态显示/隐藏造成布局跳动 (line 107-116, line 630-669)**

```css
/* line 115 */
.api-custom-model{display:none;...}
.api-custom-model.visible{display:inline-block}
```

用户在平台下拉中选择不同平台时，模型下拉框、自定义模型输入框、API 端点输入框会突然出现/消失。Flex 布局中的其他元素会随之跳动。无过渡也无占位保留。

**建议：** 使用 `visibility:hidden` + `position:absolute` 或保留 flex 占位来避免跳动。

**D12 -- API 行在小窗口下换行凌乱 (line 107-108, line 322-329)**

```html
<!-- line 322-329: 6 个元素在一个 flex-wrap row 里 -->
<select id="apiPlatform"><!-- ~100px --></select>
<select id="apiModel" style="display:none"><!-- ~120px --></select>
<input id="apiCustomModel" ...><!-- ~120px -->
<input id="apiModelName" ... style="display:none"><!-- 隐藏 -->
<input class="api-name-input" ...><!-- max-width:100px -->
<input type="password" id="apiKeyInput" ...><!-- flex:1 -->
<input id="apiEndpoint" ... style="display:none"><!-- 隐藏 -->
<button ...>添加</button>
```

可见元素（platform select + name input + key input + button）在 680px 宽度可以一行排开。但在 480px 下会换行。换行后 button 可能会单独一行 -- 看起来像是布局错误。

**D13 -- API Key 密码框无显示/隐藏切换 (line 327)**

```html
<input type="password" id="apiKeyInput" placeholder="粘贴 API Key">
```

用户粘贴 API Key 后无法确认内容是否正确。`type="password"` 是正确的安全选择，但缺少一个眼睛图标切换明文/密文。

### Region 6: 高级设置 (lines 336-348)

**D14 -- 非 dev 模式下整个区域被隐藏 (line 522)**

```javascript
// line 522
$('advCard').style.display='none';
```

对于非 dev 模式用户，高级设置区域完全不显示。但高级设置中只有"上下文感知翻译"这一项，且它依赖 LLM。正确做法是保留折叠区域但禁用其中的控件（灰色+locked），并附说明"需要 LLM API Key"。

**当前行为：** 免费用户看不到 `advCard`。他们不会知道这个功能存在。

### Region 7: 任务卡片 + 进度条 (lines 177-193, 907-918)

**D15 -- 主进度条与卡片进度条高度不一致 (line 141 vs line 183)**

```css
/* line 141 -- main progress section */
.progress-bar{height:5px;...}
/* line 183 -- job card */
.job-card .progress-bar{height:4px;...}
```

5px vs 4px 是无意的差异还是刻意的层级区分？如果是刻意的，应该在注释中说明。

**D16 -- 进度条 pulse 动画仅限 transcribe 阶段 (line 143, line 940)**

```javascript
// line 940
if(msg.stage==='transcribe'&&msg.percent>=10&&msg.percent<35){
  if(fill)fill.classList.add('working')
}else{
  if(fill)fill.classList.remove('working')
}
```

`working` class 只在 `transcribe` 阶段 (10-35%) 才添加脉冲动画。其他阶段（extract 0-5%, translate 35-80%, subtitle 80-90%, burn 90-100%）进度条是静态的。用户可能以为处理卡住了--尤其是长耗时阶段（translate 可能占 45% 的时间却没有动画）。

**建议：** 所有 `running/queued` 状态都添加 pulse 动画，或者只在"等待"状态（排队、模型下载）使用动画。

---

## 3. 视觉一致性

### 3.1 颜色系统

**Strengths:** 12 个 CSS 自定义属性定义完善，日间/暗色双主题覆盖。

**违规（硬编码颜色）：**

| 位置 | 行号 | 代码 | 问题 |
|------|------|------|------|
| 字幕预览框 | L127 | `background:#1a1a1a;color:#fff` | 不响应暗色模式 |
| Toast | L774 | `background:#333;color:#fff` | 不响应主题 |
| Error overlay | L159 | `background:rgba(0,0,0,.4)` | 日间/暗色统一用黑色遮罩 |
| Dev login overlay | L165 | `background:rgba(0,0,0,.5)` | 与 error overlay 透明度不一致 |
| Dropzone hover | L52 | `background:rgba(0,113,227,.04)` | 硬编码蓝色 alpha |

**D17 -- 两个 overlay 背景透明度不一致 (line 159 vs line 165)**

Error overlay 用 `0.4` 透明度，dev login overlay 用 `0.5`。功能相同的遮罩层，视觉参数应统一。

### 3.2 按钮样式

三种按钮体系共存：

| 类型 | CSS 类 / 来源 | 使用位置 |
|------|--------------|---------|
| 主按钮 | `.btn-primary` | 开始按钮、添加 API Key |
| Toggle 分段 | `.toggle button` | 字幕有无、翻译模式、上下文感知 |
| Outline 次级 | `.style-confirm-btn` | 样式确认 |
| **行内样式按钮** | 无类 | dev logout (L219), save path (L270), dev activate (L374), dev cancel (L375), LLM fallback 两个 (L387-388) |

**D18 -- 5 个按钮无类名，使用行内 style (lines 219, 270, 374-375, 387-388)**

这些按钮各自独立写了一套 inline CSS。如果将来要统一调整按钮风格（比如把圆角从 6px 改成 8px），需要改 5 个不同位置。维护成本高。

### 3.3 输入框样式

基础规则（L72）定义了 `min-height:36px; padding:8px 12px`，但以下输入框用行内样式覆盖：

- L58: local path input -- `padding:6px 10px; font-size:12px`（与基础规则不同）
- L325-328: API 行的所有输入框 -- `font-size:12px; padding:6px 10px`（与基础规则不同）
- L372: dev code input -- 独立的完整行内样式
- L579: save path 动态创建的 input -- 行内样式

**D19 -- 输入框 padding/font-size 不一致 (L72 vs L58, L325-328)**

基础规则 `padding:8px 12px` vs 实际输入框 `padding:6px 10px`。在同一个页面中，有的输入框 8px padding，有的 6px。这种微妙的不一致让页面视觉上"不对劲"但用户很难说出原因。

### 3.4 间距体系

使用的间距值（从 CSS 中提取）：

```
3px, 4px, 5px, 6px, 8px, 10px, 12px, 14px, 16px, 18px, 20px, 
22px, 24px, 28px, 32px, 44px, 60px
```

**17 种不同的间距值** 分布在一个 995 行的单文件中。没有形成 4px 基础网格或 8px 基础网格。大多数值集中在 4-20px 范围，但 3px、5px、14px、18px、22px 各出现 1-2 次 -- 这些很可能是临时调整而非刻意设计。

### 3.5 字体大小

```
10px, 11px, 12px, 13px, 14px, 15px, 16px, 18px, 20px, 24px, 28px
```

**11 种字体大小。** 没有形成清晰的 typographic scale。14px 和 15px、12px 和 13px 之间的差异在视觉上几乎不可分辨。建议收敛到 5-6 个层级。

---

## 4. 交互逻辑

### 4.1 Free / Paid_1 / Paid_3 三态切换

**当前实现 (lines 509-532):**

```javascript
// line 510-513: paid_3
$('apiTip').innerHTML='...（平台提供 API，无需配置）';
$('apiRow').style.display='none';
// line 515-517: paid_1
$('apiRow').style.display='flex';
// line 519-523: free (非 dev)
$('apiRow').style.display='none';
$('advCard').style.display='none';
```

**问题：**

1. **Tier 切换没有 UI** -- 对普通用户而言，tier 完全不可见。`paid_1` 和 `paid_3` 的区别只在 dev 工具条中的一个文字标签里。用户不知道自己当前在哪个 tier，也不知道升级后能得到什么。

2. **无过渡动画** -- API row、advCard 的显示/隐藏是瞬时的（`display:none` / `display:flex`），无任何过渡。

3. **Tier 切换驱动 UI 变化时，没有 toast 或通知**告诉用户发生了什么。URL 参数 `?tier=paid_3` 被静默应用。

4. **`advCard` 对免费用户完全不可见**（见 D14）-- 用户不知道这个功能存在。

### 4.2 灰色不可操作状态 (lines 172-174, 843-858)

```css
/* line 172-173 */
.disabled-ctl{pointer-events:none;opacity:.45}
.disabled-ctl input,.disabled-ctl select,.disabled-ctl button{cursor:not-allowed}
```

**实现正确：** `pointer-events:none` 阻止所有交互，`opacity:0.45` 视觉表达禁用。

**问题：**
- 有 7 个元素被 `lockUI()` 锁定：subToggle, burnSelect, modelSection, modeToggle, priorityList, apiPlatform, apiName, apiKeyInput, apiAddBtn, ctxToggle, styleConfirmBtn
- 但 `savePathBtn`（L270）没有被锁定 -- 任务进行中用户仍然可以改保存路径
- `dz`（上传区）也没有被 `lockUI()` 锁定 -- 注释掉的代码（line 857 的 `.locked-dz` 清理）暗示曾经有过但被移除了
- 锁定后只在 start 按钮下方显示一个 banner（"任务进行中，设置暂不可修改"）-- 但没有在被锁定的区域内显示任何锁定图标或说明

**建议：** 在每个被锁定区域的卡片标题旁加一个小锁图标，并确保所有可修改设置的控件都被覆盖。

### 4.3 Toast 通知 (lines 772-776)

```javascript
function showToast(msg){
  const t=document.createElement('div');t.textContent=msg;
  t.style.cssText='position:fixed;bottom:20px;right:20px;background:#333;color:#fff;padding:10px 20px;border-radius:5px;z-index:9999;animation:fadein .3s';
  document.body.appendChild(t);setTimeout(()=>t.remove(),2000);
}
```

**问题清单：**

| 问题 | 细节 |
|------|------|
| 无退出动画 | `t.remove()` 瞬间消失，建议先 `opacity:0; transition:opacity .3s` 再 remove |
| 多 Toast 重叠 | 无堆叠逻辑，第 2 个 Toast 会盖在第 1 个上面 |
| 无关闭按钮 | 用户无法手动关闭，只能等 2 秒 |
| 无类型区分 | 所有 Toast 同样的样式，无法区分成功/错误/信息 |
| 硬编码颜色 | `#333` / `#fff` 不响应暗色模式 |
| `z-index:9999` | 任意大值，与 modal 的 `z-index:99/100` 对比显得突兀 |

### 4.4 任务卡片进度条 (lines 141-148, 183-186, 907-967)

**正面：**
- 每个任务独立卡片，有自己的进度条、状态文字、下载链接、关闭按钮
- `flash-highlight` 动画（line 146）用于已完成任务的视觉反馈 -- 好细节
- `processDead` 检测（line 926）-- 进程异常退出时进度条变红 -- 好边界处理
- `interrupted` 状态（line 767）-- 黄色进度条 + "任务中断"提示 -- 状态覆盖完整

**问题：**

1. **D16 (重复):** pulse 动画只在 transcribe 阶段
2. **进度条 transition 0.3s 与实际 2s 轮询不匹配** -- 进度条会在 0.3s 内跳到新位置，然后静止 1.7s 再跳。视觉上是 "停顿式" 更新。
3. **无 indeterminate 状态** -- 当阶段从 "queued" 进入 "running" 但还没有第一个进度数据时，进度条从 0% 开始。没有表示"正在准备，不知道要多久"的加载条样式。
4. **卡片关闭按钮在 done 后被隐藏** (line 193: `.job-card-done .job-card-close{display:none}`) -- 完成后无法关闭/清理卡片。只能通过刷新页面清除。

### 4.5 其他交互问题

**D20 -- 原生 confirm() 弹窗 (line 968)**

```javascript
if(!confirm(`确定要取消「${name}」吗？已完成的进度将丢失。`))return;
```

2026 年的桌面应用中使用浏览器原生 `confirm()` dialog。样式与整个 Apple-inspired 界面格格不入。在 webview 中尤其突兀。

**D21 -- 无键盘导航支持**

- 页面无 skip-to-content 链接
- 7 个卡片区域之间无 `tabindex` 或 `<section>` landmark
- 可折叠的高级设置用 `<button>` 实现（正确），但 aria-expanded 未设置
- Model slider 圆点用 `<div>` + click 实现，无 `role="radio"` 无键盘选择

**D22 -- Modal 无 focus trap (lines 159-168, 368-391)**

Error overlay、dev login overlay、LLM fallback overlay 都没有 focus trap。用户在 modal 打开时按 Tab 会 focus 到 modal 后面的元素。

---

## 5. 暗色模式完整审查

### 已做对的事

- `@media(prefers-color-scheme:dark)` 覆盖 5 个核心颜色 token
- bg (亮) < surface (中) < card (暗) -- Apple 暗色模式层级正确
- Accent 颜色 (blue/red/green/yellow) 不做暗色调整 -- 在暗背景上仍然可读
- `prefers-reduced-motion:reduce` 全局禁用动画 -- 无障碍意识

### 暗色模式问题汇总

| ID | 位置 | 问题 | 严重度 |
|----|------|------|--------|
| D3 | L51 | 上传区虚线边框 `#636366` 对比度不足 | 中 |
| D6 | L127 | 字幕预览框 `#1a1a1a` 与背景 `#1c1c1e` 融为一体 | 中 |
| D17 | L159/L165 | Overlay 遮罩透明度不统一 | 低 |
| Toast | L774 | `#333` 文字和 `#fff` 在暗色背景上可用但不优雅 | 低 |
| 阴影 | L43 | `box-shadow` 仍使用 `rgba(0,0,0,.3)` -- 暗色模式无效果 | 低 |

---

## 6. Bug 与 Triage

| ID | 严重度 | 行号 | 类别 | 描述 |
|----|--------|------|------|------|
| D1 | 🔴 | L28,L158 | 窗口 | 500px 最小高度不够，内容约需 1182px |
| D3 | 🟡 | L51,L18-22 | 视觉 | 暗色模式虚线边框不可见 |
| D6 | 🟡 | L127 | 暗色 | 字幕预览框在暗色模式中消失 |
| D16 | 🟡 | L143,L940 | 交互 | 进度条 pulse 动画仅限 transcribe 阶段 |
| D17 | 🟡 | L159,L165 | 视觉 | 两个 overlay 遮罩透明度不一致 |
| D18 | 🟡 | L219,270,374-375,387-388 | 视觉 | 5 个按钮使用行内样式无类名 |
| D19 | 🟡 | L72 vs L58,L325-328 | 视觉 | 输入框 padding 不一致 (8px vs 6px) |
| D20 | 🟡 | L968 | 交互 | 原生 confirm() 弹窗破坏视觉一致性 |
| D2 | 🟡 | L774 | 交互 | Toast 无退出动画/堆叠/关闭/类型 |
| D8 | 🟢 | L82-84 | 交互 | 模型圆点无 hover 状态 |
| D10 | 🟢 | L97-98 | 交互 | 拖拽排序缺少抬起视觉反馈 |
| D11 | 🟢 | L630-669 | 交互 | API 元素显示隐藏造成布局跳动 |
| D12 | 🟢 | L322-329 | 布局 | API 行小窗口下换行凌乱 |
| D13 | 🟢 | L327 | 交互 | API Key 密码框无显示/隐藏切换 |
| D14 | 🟢 | L522 | 功能 | 免费用户看不到高级设置区域 |
| D15 | 🟢 | L141,L183 | 视觉 | 主进度条 5px vs 卡片 4px 不一致 |
| D21 | 🟢 | 全局 | 无障碍 | 无键盘导航 / landmark / aria |
| D22 | 🟢 | L159-168 | 无障碍 | Modal 无 focus trap |

---

## 7. Quick Wins（高影响，< 30 分钟）

| # | 修改 | 行号 | 时间 |
|---|------|------|------|
| 1 | 暗色模式虚线边框改为 `#8e8e93` | L18 (--border dark 值) | 1 min |
| 2 | 字幕预览框暗色模式加 `border:1px solid #555` | L127 | 1 min |
| 3 | Toast 改用 CSS 变量而非硬编码颜色 | L774 | 5 min |
| 4 | 两个 overlay 遮罩统一为 `rgba(0,0,0,0.5)` | L159, L165 | 1 min |
| 5 | 模型圆点添加 `:hover{border-color:var(--blue)}` | L82 | 1 min |
| 6 | 输入框行内样式统一为基础规则 | L58, L325-328 | 5 min |
| 7 | `min_size` 从 `(800, 500)` 改为 `(800, 650)` | desktop.py L162 | 1 min |
| 8 | 进度条 pulse 动画扩展到所有 running 状态 | L940 | 5 min |

---

## 总结

V2.11 的 UI 仍然是 **有设计意图的界面**，不是 AI 模板。Apple HIG 的影响贯穿始终。总体品质保持在 B 级别。

与 V2.10 设计审查相比，本次发现了很多新的微观问题（输入框 padding 不一致、5 个行内样式按钮、进度条动画阶段限制、Toast 缺少退出动画等）-- 这些不是回归，而是之前没有审查到的角落。

**核心建议：**
1. 修 8 个 Quick Wins（约 20 分钟）将 Design Score 从 B 提升到 B+
2. 将 `min_size` 高度从 500 提高到 650，解决窗口高度不足这个最大的用户体验问题
3. 配合 Steve Jobs Review 中的 C1-C6 修复后，即可进入内测
