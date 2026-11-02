#!/usr/bin/env bash
# 使用说明
# 用途：在当前Linux桌面浏览器打开本机工作台。
# 用法：先启动服务，再执行 ./open-workbench.sh；仅调用 xdg-open http://127.0.0.1:18769/，不启动采集或动作。
# 详细说明：采集与预览使用说明.md

xdg-open http://127.0.0.1:18769/
