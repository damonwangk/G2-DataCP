> 历史资料：保留原版转换器/旧部署与验收上下文。当前机器人统一目录为 `/data/r2_datacp`，日常命令请用 [使用说明](../../../使用说明.md)，不要照旧路径启动。

# 2026-10-29 全程序迁移到机器人

- 程序 /home/agi/Project_codex/r2_mcap_studio；数据 /data/data。
- 机器人网页 http://10.42.1.101:18769/；原 Mac 转发 127.0.0.1:18765 保留。
- 机器人 r2-mcap-recorder.service、r2-mcap-studio.service 均 active/enabled。采集后台独立运行。
- Ubuntu 旧工作台服务 inactive；仅 SSH 转发运行。8766 未使用、未修改。
- 使用机器人自带 ARM GDK 3.1.1；只做 import 依赖检查，没有 gdk_init 或机器人动作调用。
- ARM 本机 47 项离线测试通过（SDK 用替身）；5 份前端测试通过。
- 三路相机预览显示：头部 640x400、双腕 1280x1056。原始字段面板已适配机器人 Protobuf 5.28。
- 用户已有 2 条正式 episode 和已保存姿态已复制至机器人，Ubuntu 原始文件保留。旧执行反馈和历史测试保留在 Ubuntu 原目录。
- 网页已实际导出 episode_2 至 Mac Downloads/episode_2_20261029_134338.tar，4270080 bytes。
- 通过 Ubuntu 到机器人 10.42.1.101 的直连地址导出到 Ubuntu evidence/robot_full_export，包含 raw.mcap 4242404 bytes、原始 raw.json 25245 bytes。
- 导出不绑定 Ubuntu：浏览器识别当前访问电脑，支持目录选择；普通 HTTP 或不支持目录接口的浏览器提供原样 TAR 打包下载，保存位置由浏览器选择。
- 未自动检查用户 MCAP 内容/质量，未做对齐、插值或数据转换。本轮未下发机器人控制命令，现场遥操继续。

证据：evidence/robot_full_tests.txt、evidence/robot_full_deployment.json、evidence/ui_tests_final.json。
