#!/usr/bin/env bash
# 使用说明
# 用途：G2保守资源入口，仅转换已由采集方标记完成的记录。
# 从项目根目录运行 ./conversion/run_robot.sh --input /path/to/full --output /path/to/new_dataset --repo-id local/name --task "明确任务"
# 默认 --ready-only、单线程及低IO/CPU优先级；传入 --min-free-gb 20 但主写入器未执行该门槛。
# R2_CONVERTER_VENV可指定独立环境；--codec h264为保留兼容参数，当前仍按MP4保真规则输出。
# 如需离线检查可加 --inspect；ready标记的历史文件清单及适用限制见 README.md。
#
# Run only after installation; this file never installs, starts or stops recording.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONVERTER_ENV="${R2_CONVERTER_VENV:-${SCRIPT_DIR}/.venv}"
if [[ ! -x "${CONVERTER_ENV}/bin/python" ]]; then
  echo "Missing isolated environment: ${CONVERTER_ENV}; see ../使用说明.md" >&2
  exit 1
fi
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
export HF_HOME="${R2_CONVERTER_CACHE:-${SCRIPT_DIR}/.cache/huggingface}"
export PYTHONDONTWRITEBYTECODE=1
exec ionice -c 3 nice -n 19 "${CONVERTER_ENV}/bin/python" "${SCRIPT_DIR}/convert_r2.py" \
  --ready-only --threads 1 --codec h264 --min-free-gb 20 "$@"
