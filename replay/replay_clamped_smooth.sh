#!/usr/bin/env bash
# 使用说明
# 用途：独立限幅平滑回放的Linux入口，保留原回放入口和源数据。
# 使用：项目根目录 replay/replay_clamped_smooth.sh --native-recording /data/record/full_xxx。
# LeRobot：加 --dataset /path/to/dataset --episode 0；MCAP：--mcap /path/to/file.mcap。
# 以上三类来源择一；加 --dry-run 仅计算检查，不导入SDK或发送动作。
# 默认关节3rad/s、夹爪0.1m/s；可用--joint-speed/--gripper-speed指定正速率。
# 多文件使用说明：replay/README_clamped_smooth.md；实际执行会发送变换后目标。
#
# 独立限幅平滑回放入口；仅由操作者手动启动，旧入口不变。
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd)"
suite="$(dirname "$root")"
sdk_app="${R2_SDK_APP:-/home/agi/app}"
python_bin="${R2_REPLAY_PYTHON:-$suite/conversion/.venv/bin/python}"
if [ ! -x "$python_bin" ]; then python_bin=/usr/bin/python3; fi
cd "$root"
export PYTHONPATH="$sdk_app/gdk/lib:$suite/conversion/extra_deps${PYTHONPATH:+:$PYTHONPATH}"
sdk_libs="$(find "$sdk_app/lib" -type f -name '*.so' -printf '%h\n' | sort -u | tr '\n' ':')"
export LD_LIBRARY_PATH="$sdk_app/lib:$sdk_libs${LD_LIBRARY_PATH:+$LD_LIBRARY_PATH}"
export LD_PRELOAD="$sdk_app/lib/libcrypto.so.3:$sdk_app/lib/libssl.so.3${LD_PRELOAD:+:$LD_PRELOAD}"
if [ -f "$sdk_app/gdk/config/app_conf.yml" ]; then
  export APP_CONF_PATH="$sdk_app/gdk/config/app_conf.yml"
else
  unset APP_CONF_PATH
fi
export LOCATOR_IP="${LOCATOR_IP:-10.42.1.101}"
export AORTA_DISCOVERY_URI="${AORTA_DISCOVERY_URI:-http://127.0.0.1:2379}"
export AORTA_DISPATCHER_THREAD_NUM="${AORTA_DISPATCHER_THREAD_NUM:-6}"
exec "$python_bin" "$root/lerobot_replay_clamped_smooth.py" --config "$suite/studio/config/robot_control.json" "$@"
