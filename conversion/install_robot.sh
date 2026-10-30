#!/usr/bin/env bash
# Explicit operator-run installation; never changes the system Python environment.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONVERTER_ENV="${R2_CONVERTER_VENV:-${SCRIPT_DIR}/.venv}"
ROBOT_PYTHON_BIN="${R2_CONVERTER_PYTHON:-/usr/bin/python3.10}"
"${ROBOT_PYTHON_BIN}" - <<'PY'
import platform,sys
if sys.platform != 'linux' or platform.machine() != 'aarch64' or sys.version_info[:2] != (3,10):
    raise SystemExit('This installation profile requires Linux aarch64 and Python 3.10')
PY
if [[ "${SCRIPT_DIR}" != /data/* || "${CONVERTER_ENV}" != /data/* ]]; then
  echo 'Place the tool and its isolated environment under /data; see LINUX_DEPLOYMENT.md' >&2
  exit 1
fi
if [[ ! -f "${SCRIPT_DIR}/vendor/lerobot-0.1.0-py3-none-any.whl" ]]; then
  echo 'Missing bundled pinned LeRobot wheel in vendor/' >&2
  exit 1
fi
if [[ ! -x "${CONVERTER_ENV}/bin/python" ]]; then
  uv venv --python "${ROBOT_PYTHON_BIN}" "${CONVERTER_ENV}"
fi
"${CONVERTER_ENV}/bin/python" -c 'import sys; assert sys.version_info[:2] == (3,10), "expected isolated Python 3.10"'
if [[ -d "${SCRIPT_DIR}/wheelhouse" ]]; then
  uv pip install --python "${CONVERTER_ENV}/bin/python" --only-binary :all: --no-index \
    --find-links "${SCRIPT_DIR}/wheelhouse" --find-links "${SCRIPT_DIR}/vendor" \
    -r "${SCRIPT_DIR}/requirements-robot-linux.lock"
else
  uv pip install --python "${CONVERTER_ENV}/bin/python" --only-binary :all: \
    --find-links "${SCRIPT_DIR}/vendor" \
    -r "${SCRIPT_DIR}/requirements-robot-linux.lock"
fi
uv pip install --python "${CONVERTER_ENV}/bin/python" --no-deps \
  "${SCRIPT_DIR}/vendor/lerobot-0.1.0-py3-none-any.whl"
"${CONVERTER_ENV}/bin/python" -B "${SCRIPT_DIR}/robot_env_check.py"
