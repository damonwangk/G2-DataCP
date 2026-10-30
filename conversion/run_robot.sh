#!/usr/bin/env bash
# Run only after installation; this file never installs, starts or stops recording.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONVERTER_ENV="${R2_CONVERTER_VENV:-${SCRIPT_DIR}/.venv}"
if [[ ! -x "${CONVERTER_ENV}/bin/python" ]]; then
  echo "Missing isolated environment: ${CONVERTER_ENV}; see LINUX_DEPLOYMENT.md" >&2
  exit 1
fi
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
export HF_HOME="${R2_CONVERTER_CACHE:-${SCRIPT_DIR}/.cache/huggingface}"
export PYTHONDONTWRITEBYTECODE=1
exec ionice -c 3 nice -n 19 "${CONVERTER_ENV}/bin/python" "${SCRIPT_DIR}/convert_r2.py" \
  --ready-only --threads 1 --codec h264 --min-free-gb 20 "$@"
