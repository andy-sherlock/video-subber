# Video Subber Build Notes

## 当前阶段

V3.09-B1 只验证 Mac 开发态 Tauri 壳：

```
cd /Users/andy/Andy-agent/video-subtitle/v3
npm run desktop:dev
```

B1 会做这些事：
- Rust 桌面壳启动。
- 从 `8766` 开始找可用本地端口。
- 拉起 `.venv/bin/python -m backend.main --no-reload`。
- 等 `/api/health` 返回 200。
- 打开 WebView 加载现有前端页面。
- 关闭窗口或停止进程时清理这次拉起的 Python 后端。

B1 不是正式安装包，也不是 Windows EXE。它依赖本机源码、`.venv`、Node、Rust 和 Cargo。

## 本机依赖

- Node/npm：用于安装 Tauri CLI。
- Rust/Cargo：用于编译 Tauri 壳。
- Python 虚拟环境：`v3/.venv`。

首次准备：

```
cd /Users/andy/Andy-agent/video-subtitle/v3
npm install
```

Rust 当前通过 Homebrew 安装：

```
brew install rust
```

## 常规 Web 开发态

不需要桌面壳时，仍使用旧入口：

```
cd /Users/andy/Andy-agent/video-subtitle/v3
./run.sh
```

这会在 `8766` 启动 Web 服务并开启 reload。

**测试提醒：同一视频 + 同样设置不会重跑**（2026-10-02 记；D-1 补修删除「重新处理」后生效）

程序按「视频文件 + 处理设置」认任务，不看程序版本（`backend/main.py` `_config_hash`）。改完代码后拿同一段视频、同样设置再拖进来，程序会直接显示上次的结果，不会重新处理——看到的是旧字幕，不能当作新代码的验证结果。要真正重跑：换一段视频、改一项会影响结果的设置，或删除该任务的记录文件 `~/.video-subber/jobs/<任务编号>/meta.json` 后重启服务。

## Windows 真机验收清单

V3.09-B2 后置到 V3.10 打包阶段。只有打出 Windows EXE 或安装包后，才逐条验收：

- [ ] 双击主程序无 CMD 黑窗。
- [ ] 后台 Python sidecar 无黑窗。
- [ ] 关闭窗口后 sidecar 和 worker 进程全部退出。
- [ ] worker 子进程在打包后能正常启动。
- [ ] ffmpeg 在打包目录内可被找到。
- [ ] 模型能下载到用户目录并正常转录。
- [ ] 端口被占时能自动切换并连上。
- [ ] 中文路径、空格路径、本地视频路径正常。
- [ ] Windows 防火墙或杀软没有阻断本地通信。

## 后续打包态

V3.10 再做正式打包链：

```
PyInstaller 双入口 sidecar -> Tauri build -> Windows 真机验收
```

不要把 B1 的 Mac 开发态通过写成 Windows 已通过。
