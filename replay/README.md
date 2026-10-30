# 机器人本机 LeRobot 回放模块

本模块放在 `/data/r2_datacp/replay`，使用机器人 ARM SDK `/home/agi/app`。Python 默认使用相邻 `conversion/.venv/bin/python`（需 PyArrow、gRPC）；可用 `R2_REPLAY_PYTHON` 覆盖。不依赖 Ubuntu 上的 x86 SDK。

## 命令行

只读查看数据集及某个片段（不导入 SDK）：

```bash
/data/r2_datacp/conversion/.venv/bin/python /data/r2_datacp/replay/dataset_info.py \
  --dataset /data/r2_datacp/data/lerobot/example --episode 0
```

只读检查，生成一次作业结果而不导入 SDK：

```bash
/data/r2_datacp/conversion/.venv/bin/python /data/r2_datacp/replay/lerobot_replay.py \
  --dataset /data/r2_datacp/data/lerobot/example --episode 0 --dry-run \
  --run /data/r2_datacp/runtime/check_example
```

执行实机动作：

```bash
/data/r2_datacp/replay/replay.sh \
  --dataset /data/r2_datacp/data/lerobot/example --episode 0 \
  --run /data/r2_datacp/runtime/replay_example
```

每次使用新的 `--run` 目录。Ctrl+C 或 SIGTERM 停止发送。网页监控进程必须在终止超时后结束整个子进程组，避免原生 SDK 阻塞时进程遗留。不会在启动网页或读数据集时加载 SDK，也不会自动执行任何 episode。

## Studio 子进程接口

- `dataset_info.py --dataset PATH [--episode N]`：stdout 输出一个 JSON；`episodes` 列出编号、帧数、时长和任务，`selected_episode` 含第一帧 action 和对应反馈。
- `replay.sh --dataset PATH --episode N --run RUN_DIR`：执行所选片段。
- `RUN_DIR/progress.json`：原子更新的运行状态，含 `phase`、`position_s`、`duration_s`、`send_hz`、`sent`、`rejected`。
- `RUN_DIR/result.json`：最终结果，区分 `completed`、`stopped_by_user` 和 `error`。
- `RUN_DIR/trace.jsonl`：每次目标、最近反馈、SDK 返回码及独立反馈采样。Studio 自行将 stdout/stderr 保存为 `worker.log`。
- 停止：向整个 worker 进程组发送 SIGTERM，等待数秒；若厂商 native 调用仍阻塞，再对同一进程组发 SIGKILL，并在前端标记强制结束。

回放数据来自 LeRobot v2.1 Parquet 的 `action`，不假定它一定来自原始控制命令：平台转换器有原生 command 时使用 command，缺少时以对应采集 state 轨迹作为 action，具体来源写入转换 provenance。名称取 `meta/info.json` 的 `features.action.names`。按 `timestamp` 推进，以 100 Hz 为目标，保持最近一个原始 action，不插值、不裁剪。第一帧直接发送，末帧保持一个源帧周期。所有选择都明确到单个 episode，不自动拼接片段；不会把录制时的 `observation.state` 当作动作。

当前数据是 16 维双臂和双夹爪；无头、腰、底盘动作。夹爪方向和实机跟随尚需现场核对，SDK 调用成功不代表本体已跟随。`send_hz` 和 `unsent_frame_indices` 显示真实发送状况。反馈读取与文件写入独立于发送循环，最近反馈不保证与目标严格同一时刻；多线程 SDK 行为仍需实机验证。

`struct` 是当前机器人 GDK3.1.1 默认控制模式 API；`--sdk-api mode-id` 仅为有需要时适配其他 SDK，不会通过试发两种模式自动探测。控制权交接使用现有私有 `studio/config/robot_control.json`，不公开该文件。
