> 历史资料：保留原版转换器/旧部署与验收上下文。当前机器人统一目录为 `/data/r2_datacp`，日常命令请用 [使用说明](../../../使用说明.md)，不要照旧路径启动。

# 格式转换部署验收

工具位于机器人 `/data/r2_lerobot/tool`，独立环境 `.venv`。启动命令见 [ROBOT_QUICKSTART.md](ROBOT_QUICKSTART.md)。本项目现在仅提供数据格式转换；我方编写的回放代码、测试及入口已移除。

- 平台：G2，Ubuntu 22.04，aarch64，Python 3.10.12，OpenPI 固定版本 LeRobot v2.1。
- 34 项格式转换相关软件测试通过；HEVC 解码与 CPU H.264 编码通过。
- 两份用户样例的 16 个转换输入文件，与机器人独立验收副本的大小及 SHA-256 全部一致。
- 机器人实际转换结果：3 个 episode、1360 行；官方加载器逐行核对全部 16 维 state/action，与原始 bundle 的 float32 值完全一致。
- 官方加载器通过 27 张图像和 9 个 50 步动作块及边界填充检查。
- 9 个输出视频全部解码，共 4080 帧，帧数及时间戳一致。
- `--resume` 再跑新增分段为 0，没有重复追加。
- 样例输出：`/data/r2_lerobot/datasets/local/r2_sample_acceptance`；任务文字为格式验收占位文字。
- 原始视频经缩放、有损 H.264 编码，像素不承诺逐位相同；源时间、相机选择、过滤原因保存在 provenance。
- 已实现逐原始 episode 处理、续跑及错误日志，尚未做真实 1000 条 episode 吞吐量压测。

原始采集文件、采集程序和机器人 SDK 均未修改。用户 Ubuntu 中 `/home/robot/Project_codex/r2_lerobot_replay` 属于独立项目，本轮没有改动。

证据见 `validation/deployment/robot/`，结构化状态见 `deployment-status.json`。
