#!/usr/bin/env bash
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$HERE/extra_deps${PYTHONPATH:+:$PYTHONPATH}"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
exec "$HERE/.venv/bin/python" "$HERE/platform_convert.py" "$@"
