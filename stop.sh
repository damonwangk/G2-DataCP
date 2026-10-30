#!/usr/bin/env bash
set -euo pipefail
# 工作台停止时停止其转换/回放子进程；采集服务退出时完成当前 MCAP 写入。
systemctl --user stop r2-mcap-studio.service
systemctl --user stop r2-mcap-recorder.service
printf 'DataCP 已停止。\n'
