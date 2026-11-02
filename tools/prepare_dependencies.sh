#!/usr/bin/env bash
# Build the pinned public third-party dependencies required by the existing installers.
# Run manually on a networked machine; does not install or invoke robot SDKs.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
WHEEL_DIR="${PROJECT_DIR}/conversion/vendor"
BUILD_PYTHON="${R2_CONVERTER_PYTHON:-python3.10}"
LEROBOT_COMMIT="0cf864870cf29f4738d3ade893e6fd13fbd7cdb5"

command -v git >/dev/null
"${BUILD_PYTHON}" -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ required"'
mkdir -p "${WHEEL_DIR}"
"${BUILD_PYTHON}" -m pip wheel --no-deps --wheel-dir "${WHEEL_DIR}" \
  "lerobot @ git+https://github.com/huggingface/lerobot.git@${LEROBOT_COMMIT}"
"${BUILD_PYTHON}" -m pip wheel --no-deps --wheel-dir "${WHEEL_DIR}" \
  'antlr4-python3-runtime==4.9.3'

"${BUILD_PYTHON}" - "${WHEEL_DIR}" "${LEROBOT_COMMIT}" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
expected = ('lerobot-0.1.0-py3-none-any.whl', 'antlr4_python3_runtime-4.9.3-py3-none-any.whl')
rows = []
for name in expected:
    path = root / name
    if not path.is_file():
        raise SystemExit(f'Expected installer wheel was not produced: {path}')
    rows.append({'file': name, 'bytes': path.stat().st_size,
                 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
value = {'lerobot_source_repository': 'https://github.com/huggingface/lerobot',
         'lerobot_source_commit': sys.argv[2],
         'antlr4_python3_runtime_version': '4.9.3',
         'note': 'Hashes describe this local build, not the historical deployment wheels.',
         'wheel_files': rows}
(root / 'source_manifest.json').write_text(json.dumps(value, indent=2) + '\n')
print(f'Prepared installer dependencies: {root}')
PY
