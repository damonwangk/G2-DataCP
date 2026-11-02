#!/usr/bin/env bash
# 使用说明
# 用途：使用本目录独立Python环境启动JSON清单批量转换，不安装依赖。
# 从项目根目录运行 ./conversion/run_platform.sh --inputs-json /path/to/inputs.json --input-format factory --dataset-path /path/to/new_dataset --repo-id local/name --task "明确任务" --result /path/to/result.json
# 支持 --workers/--threads/--resume/--inspect；输入清单为非空路径JSON数组。
# 设置离线模型环境，不加载运动SDK；多文件使用说明见 README.md / PLATFORM_MCAP.md。
#
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$HERE/extra_deps${PYTHONPATH:+:$PYTHONPATH}"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
exec "$HERE/.venv/bin/python" "$HERE/platform_convert.py" "$@"
