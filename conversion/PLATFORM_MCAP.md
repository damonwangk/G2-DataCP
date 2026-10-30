# 采集平台 MCAP 转换

`platform_convert.py` 接收平台采集的 `raw.mcap`，也接收原厂 bundle 录制目录。可传单个文件、episode 目录或父目录；父目录递归发现已保存的 MCAP。多个输入路径可同时写入 JSON 数组，重复路径仅处理一次。

```bash
cd /data/r2_datacp/conversion
printf '%s\n' '["/data/data/文件夹A"]' > /tmp/convert-inputs.json
./run_platform.sh --inputs-json /tmp/convert-inputs.json \
  --output-dir /data/lerobot --repo-id local/task_a \
  --task '这里填写本批数据的真实任务描述' \
  --result /tmp/convert-result.json --progress /tmp/convert-progress.json
```

以上实际数据集目录为 `/data/lerobot/local/task_a`。同一输出续转加 `--resume`，已完成来源不会重复追加。结果 JSON 给出 `completed`、`dataset_path`、`total_episodes`、`total_frames`；stdout 输出进度。遇到不能解析的输入会继续其余输入，最终结果列出失败信息。原始 MCAP 不修改。

## MCAP 映射

输出沿用原转换程序固定的 LeRobot v2.1 / π0.5 数据格式：30 Hz（可用 `--fps` 修改）、16 维状态和动作；存在的相机输出为 256×256 RGB H.264 MP4。向量顺序为左臂 7 关节、左夹爪、右臂 7 关节、右夹爪。

- 状态：`/hal/joint_state.motor_position` 的弧度值，加 `/hal/{left,right}_ee_data.end_state[].position` 原生夹爪行程值；根据消息中的关节名取值。
- 动作：优先使用 `/hal/joint_cmd.position` 与 `/wbc/{left,right}_ee_command.position`。对应命令缺失时，用该部分采集到的状态轨迹作为回放动作。
- 图像：`/camera/{head_color,hand_left_color,hand_right_color}/image` 中的原始 JPEG。训练格式将其解码、保持比例补边，再编码为 MP4。
- 每个 MCAP 对应一个输出 episode。按 MCAP `log_time` 建立均匀时间轴，逐通道取最近样本，不因缺口、时间差或关节范围过滤、裁剪或拆分数据。
- `provenance/frames.jsonl` 记录每帧动作实际来自哪个 command/state 通道、原消息序号、接收/发布时间戳。使用状态轨迹时不会宣称它是原始控制命令。

16 维状态是此格式的输入字段；相机为可选字段，没有图像的 MCAP 直接输出状态/动作 episode，不补造图像。完全缺少必要状态字段或损坏的 Protobuf/JPEG 会给出解析错误。单个数据集 schema 固定：首次转换按第一条的实际相机字段建表，同模态数据可以批量转换；相机字段不同的来源会具体报错并保留已成功的 episode，应分别输出到不同数据集。原厂 bundle 输入仍沿用其原有转换逻辑，详见原 README。

## 运行环境

使用同目录 `.venv` 的原官方 LeRobot v2.1 环境，另需 `mcap`、`protobuf`、`zstandard`、`lz4`（见 `requirements-mcap.txt`）。离线机器人部署可以将这些同机 Python 3.10 包放入 `conversion/extra_deps`；`run_platform.sh` 自动加入此路径。此程序只读数据文件，不导入或调用机器人控制 SDK。

## 进度与失败任务继续

`--progress /path/progress.json` 每约 0.25 秒原子更新：`phase`、`source_index`（从 1 开始）、`total_sources`、`completed_sources`、`failed_sources`、`source_name`、`processed_frames`、`total_frames`、`percent`（整个任务 0–100）、`elapsed_s`、`bytes_read` 和 `total_bytes`。读取字节数来自实际 MCAP 文件读取；帧计数来自实际 writer，视频阶段来自编码器。处理完所有来源不代表全成功，必须同时看 `result.completed` 和 `result.errors`，错误列表仅属于本次运行。

原目录恢复使用相同输入、task、repo-id、参数，加 `--resume`。如果原任务因缺少图像而失败且尚无 episode，会保留目录与原信息快照，仅把空数据集的相机 schema 更新为实际字段，然后继续。已有成功 episode 的数据集保持 schema；已提交来源不会重复追加。不会删除 MCAP 或用户目录。
