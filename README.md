# Video Subber

本仓库当前公开入口是 `v3/`。

## 当前版本

- 版本：V3.08
- 状态：已发布
- 代码目录：`v3/`
- 后端：FastAPI
- 前端：HTML/CSS/JavaScript
- 功能：视频转录、字幕翻译、字幕生成、字幕封装

## 本地运行

```bash
cd v3
.venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8766
```

打开：

```text
http://127.0.0.1:8766
```

## 测试

```bash
cd v3
.venv/bin/python -m pytest test/ -q
```

V3.08 发布线验证结果：

```text
633 passed, 8 warnings
```

## 说明

旧版本快照、问题库、审查报告、开发流水线记录只保留在本机开发环境，不作为 GitHub 当前公开目录展示。
