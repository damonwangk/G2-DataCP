#!/usr/bin/env bash
set -euo pipefail
systemctl --user start r2-mcap-recorder.service r2-mcap-studio.service
printf 'DataCP 已启动：http://10.42.1.101:18769/\n'
