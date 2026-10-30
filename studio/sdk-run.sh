#!/usr/bin/env bash
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
