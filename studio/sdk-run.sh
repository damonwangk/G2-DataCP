#!/usr/bin/env bash
# 使用说明
# 用途：MCAP回放/姿态worker的Linux厂商SDK环境包装。
# 用法：由 RobotJob 调度；格式 ./sdk-run.sh worker.py [worker参数]，实际行为由worker决定，运行SDK worker会加载真实SDK。
# 详细说明：回放与姿态使用说明.md

set -e
cd "$(dirname "$0")"
SDK_APP=/home/agi/app
export LD_LIBRARY_PATH="$SDK_APP/lib:$(find "$SDK_APP/lib" -type f -name '*.so' -printf '%h\n' | sort -u | tr '\n' ':')"
export PYTHONPATH="$PWD/_deps:$SDK_APP/gdk/lib"
export APP_CONF_PATH="$SDK_APP/gdk/config/app_conf.yml"
export LOCATOR_IP=10.42.1.101
export AORTA_DISCOVERY_URI=http://127.0.0.1:2379
export AORTA_DISPATCHER_THREAD_NUM=6
export LD_PRELOAD="$SDK_APP/lib/libcrypto.so.3:$SDK_APP/lib/libssl.so.3"
exec /usr/bin/python3 "$@"
