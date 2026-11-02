#!/usr/bin/env bash
# 在 Linux x86_64 / Python 3.10 的独立环境部署同一份24维保真转换器，不启动机器人SDK。
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${R2_CONVERTER_PYTHON:-/home/robot/anaconda3/envs/g2/bin/python}"
"$PYTHON_BIN" -c 'import platform,sys; assert platform.machine()=="x86_64" and sys.version_info[:2]==(3,10), "requires Linux x86_64 Python 3.10"'
if [[ ! -x "$SCRIPT_DIR/.venv/bin/python" ]]; then "$PYTHON_BIN" -m venv "$SCRIPT_DIR/.venv"; fi
"$SCRIPT_DIR/.venv/bin/python" -m pip install torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cpu
"$SCRIPT_DIR/.venv/bin/python" -m pip install --only-binary :all: --find-links "$SCRIPT_DIR/vendor" -r "$SCRIPT_DIR/requirements-linux-x86.txt"
"$SCRIPT_DIR/.venv/bin/python" -m pip install --no-deps "$SCRIPT_DIR/vendor/lerobot-0.1.0-py3-none-any.whl"
"$SCRIPT_DIR/.venv/bin/python" -B "$SCRIPT_DIR/robot_env_check.py"
