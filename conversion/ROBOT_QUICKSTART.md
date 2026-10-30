# 机器人数据格式转换入口

工具已部署在机器人 `/data/r2_lerobot/tool`，使用独立的 Python 3.10.12 环境。它将已完结的原始采集目录转换为 LeRobot v2.1 格式。

## 转换一条采集数据

```bash
cd /data/r2_lerobot/tool

# 采集写入、视频收尾和后处理全部结束后，由采集方声明完结。
.venv/bin/python episode_ready.py /data/record/EPISODE_DIR --confirm-finalized

bash run_robot.sh \
  --input /data/record/EPISODE_DIR \
  --output /data/r2_lerobot/datasets/local/my_tasks \
  --repo-id local/my_tasks \
  --task '实际任务指令' --resume
```

路径和任务文字应替换为实际值。`run_robot.sh` 只处理具有有效完结标记的目录；未标记或标记失效的目录会显示 `deferred`。标记由采集方声明，并不会自动停止采集。

## 批量转换

先为所有已完结的源目录分别创建标记。不同任务可用 JSON 文件按 episode **目录名**映射任务文字，例如 `/data/r2_lerobot/tasks.json`：

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

所有已完结目录必须有非空任务文字；缺键会在批量转换开始前报错，`--skip-errors` 不能跳过这一预检查。所有目录任务相同时，可用 `--task '任务指令'` 代替 `--tasks-json`。`--resume` 跳过已提交片段并恢复未完成片段，源文件、任务文字及转换设置需保持一致；`--skip-errors` 在转换阶段记录失败并继续，存在失败时最终退出码非零。不要让多个进程同时写同一输出目录。

机器人历史验收中，34 项转换相关软件测试通过；两条样例得到 3 个 episode、1360 行，9 个输出视频共 4080 帧均可解码。尚未进行真实 1000 条数据的吞吐量压测。详见 [LINUX_DEPLOYMENT.md](LINUX_DEPLOYMENT.md)。
