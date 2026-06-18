#!/bin/bash
# Video Subber V3 真实长视频测试入口
# 固定使用 v2/.venv，且不启用 uvicorn reload。
set -e
cd "$(dirname "$0")"

VENV_PYTHON=".venv/bin/python"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "找不到虚拟环境，请先运行: python3 -m venv .venv && .venv/bin/pip install -e ."
    exit 1
fi

echo "Video Subber V3 test server -> http://localhost:8766"
exec "$VENV_PYTHON" -m uvicorn backend.main:app \
    --host 0.0.0.0 \
    --port 8766
