#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
export PYTHONPATH="$PWD/_deps:${PYTHONPATH:-}"
exec /usr/bin/python3 -m uvicorn app:app --host 0.0.0.0 --port 18769 --no-access-log
