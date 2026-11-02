#!/usr/bin/env bash
# 使用说明
# 用途：手动启动机器人本机采集后台与网页/API两个systemd用户服务。
# 使用：cd /data/r2_datacp && ./start.sh；随后 ./datacp status 查询。
# 前提：服务单元已安装；网页端口18769，采集代理端口18767。
# 启动服务不会自动开始录制或执行动作；脚本不设置开机自启。
# 多文件使用说明：docs/平台启动与命令行.md（配合stop.sh、studio/start.sh等）。
#
set -euo pipefail
systemctl --user start r2-mcap-recorder.service r2-mcap-studio.service
printf 'DataCP 已启动（端口 18769，局域网可访问）\n'
lan_ip=$(ip -4 -o addr show dev wlan0 scope global 2>/dev/null | awk '{split($4,a,"/"); print a[1]; exit}' || true)
if [ -n "$lan_ip" ]; then
    printf '当前无线局域网：http://%s:18769/\n' "$lan_ip"
fi
printf '网线直连入口：http://10.42.1.101:18769/\n'
