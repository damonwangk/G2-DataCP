# 机器人 Linux 数据格式转换部署

转换工具已部署到机器人 `/data/r2_lerobot/tool`，使用独立环境 `/data/r2_lerobot/tool/.venv`，机器人验收环境为 Ubuntu 22.04、aarch64、Python 3.10.12。转换使用 CPU，不需要机器人 SDK。采集流程和转换流程彼此独立；操作入口见 [ROBOT_QUICKSTART.md](ROBOT_QUICKSTART.md)。

## 离线安装

部署包提供 53 个 ARM64 / CPython 3.10 依赖 wheel，另在 `vendor/` 提供固定版本 LeRobot 和 ANTLR wheel。包完整放入 `/data/r2_lerobot/tool` 后，可在机器人上执行：

```bash
cd /data/r2_lerobot/tool
bash install_robot.sh
```

脚本要求 Linux aarch64、Python 3.10、`uv`，在 `/data` 下创建独立虚拟环境。存在 `wheelhouse/` 时安装使用 `--no-index`；完成后运行 `robot_env_check.py`，检查依赖、LeRobot v2.1、HEVC 解码器及 CPU H.264 编码器。不要复制其他机器的 `.venv`。

## 生产数据转换

`run_robot.sh` 设置 CPU 线程数、进程与 I/O 优先级，并强制 `--ready-only`；这些设置不构成实时资源隔离。仅由采集方在所有数据写入、视频收尾与后处理结束后，为每条记录创建完结标记：

```bash
cd /data/r2_lerobot/tool
.venv/bin/python episode_ready.py /data/record/EPISODE_DIR --confirm-finalized

bash run_robot.sh \
  --input /data/record/EPISODE_DIR \
  --output /data/r2_lerobot/datasets/local/my_tasks \
  --repo-id local/my_tasks \
  --task '实际任务指令' --resume
```

标记记录七个必需源文件的大小和修改时间；缺失、变更或标记无效的目录显示 `deferred`，不会进入转换。该标记是采集方声明，不能单凭文件暂时不变推断写入已停止。输出目录和原始 episode 目录不能互相包含。

批量处理父目录时，可用相同任务文字 `--task '实际任务指令'`，或用 `--tasks-json` 指定目录名到任务文字的 JSON 对象：

```json
{
  "episode_directory_000001": "第一类任务指令",
  "episode_directory_000002": "第二类任务指令"
}
```

```bash
bash run_robot.sh \
  --input /data/record \
  --output /data/r2_lerobot/datasets/local/my_tasks \
  --repo-id local/my_tasks \
  --tasks-json /data/r2_lerobot/tasks.json \
  --resume --skip-errors
```

所有已就绪目录的任务映射会在转换前检查；缺少或为空时直接报错，`--skip-errors` 不覆盖该检查。转换阶段的单条失败可由 `--skip-errors` 记录并继续，但最终退出码仍为非零，明细位于输出的 `provenance/errors.jsonl`。`--resume` 会核对设置并跳过已提交片段；源文件和已提交任务文字须保持一致。不得由多个进程同时写同一输出目录。

## 已有验收与限制

机器人原生历史软件测试中，34 项转换相关测试通过。两份历史样例转换得到 3 个 episode、1360 行；数值逐行与原始输入对应，官方加载器抽查了 27 张图像和 9 个动作序列；9 个视频共 4080 帧全部解码通过。续跑没有新增重复片段。

批量入口支持递归发现 episode，但尚未完成真实 1000 条数据的吞吐量和资源压测。正式批量处理前应在适当时段评估磁盘空间及对采集资源的影响。完整验收记录见 [ROBOT_DEPLOYMENT_REPORT.md](ROBOT_DEPLOYMENT_REPORT.md)。
