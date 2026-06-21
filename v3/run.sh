#!/bin/bash
# Video Subber V3 启动脚本
# 确保始终使用项目 venv 里的 Python，不走错门
set -e
cd "$(dirname "$0")"

VENV_PYTHON=".venv/bin/python"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "❌ 找不到虚拟环境，请先运行: python3 -m venv .venv && .venv/bin/pip install -e ."
    exit 1
fi

echo "🚀 Video Subber V3 -> http://localhost:8766"
exec "$VENV_PYTHON" -m uvicorn backend.main:app \
    --host 0.0.0.0 \
    --port 8766 \
    --reload \
    --reload-dir backend \
    --reload-dir frontend
