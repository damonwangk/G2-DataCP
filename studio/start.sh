#!/usr/bin/env bash
# 使用说明
# 用途：启动独立网页进程，监听0.0.0.0:18769。
# 用法：日常在项目根目录执行 ./start.sh 启动整套服务；本脚本仅为服务进程入口，不会自动开始录制。
# 详细说明：README.md

set -e
cd "$(dirname "$0")"
export PYTHONPATH="$PWD/_deps:${PYTHONPATH:-}"
exec /usr/bin/python3 -m uvicorn app:app --host 0.0.0.0 --port 18769 --no-access-log
