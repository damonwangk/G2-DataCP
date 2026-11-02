#!/usr/bin/env bash
# 使用说明
# 用途：手动关闭网页/API和采集后台；会停止平台管理的转换/回放任务并完成MCAP收尾。
# 使用：cd /data/r2_datacp && ./stop.sh；只关浏览器不会停止后台。
# 范围：systemd用户服务r2-mcap-studio.service和r2-mcap-recorder.service。
# 多文件使用说明：docs/平台启动与命令行.md。
#
set -euo pipefail
# 工作台停止时停止其转换/回放子进程；采集服务退出时完成当前 MCAP 写入。
systemctl --user stop r2-mcap-studio.service
systemctl --user stop r2-mcap-recorder.service
printf 'DataCP 已停止。\n'
