# R2 DataCP：采集、转换与回放

所有服务和代码都在机器人 Linux 的 `/data/r2_datacp` 中。Ubuntu、Mac 或其他电脑只通过网线/SSH 转发访问网页和导出文件。系统**手动启动，不设置开机自启**。

| 目录 | 内容 |
|---|---|
| `studio/` | 原始 MCAP 采集、网页、MCAP 预览/实机回放、姿态与范围提醒 |
| `conversion/` | MCAP 到训练用 LeRobot 的转换器、MCAP 适配与批量入口 |
| `replay/` | LeRobot 数据集检查和机器人 ARM SDK 回放 |
| `datasets/` | 转换输出的 LeRobot 数据集 |
| `runtime/` | 转换/LeRobot 回放任务进度、结果与日志 |
| `start.sh`、`stop.sh`、`datacp` | 手动启动、关闭、命令行客户端 |

## 两个数据目录，统一读取

日常只需要设置两个机器人数据目录：

| 数据 | 保存位置示例 | 转换、回放和导出如何读取 |
|---|---|---|
| 原始 MCAP | `/data/data/A/episode_1_时间/raw.mcap` | MCAP 回放和导出读取采集目录 |
| LeRobot | `/data/r2_datacp/datasets/A/` | LeRobot 回放和导出共同读取转换保存目录 |

网页的“LeRobot 保存目录”是根目录，“本次转换文件夹”是根目录下新建的文件夹名称。例如根目录 `/data/r2_datacp/datasets`、名称 `A`，最终就保存为 `/data/r2_datacp/datasets/A`。一个文件夹可以包含批量转换产生的多个 episode。页面会直接显示这个完整路径，不再要求填写 `local/capture_时间`。

设置 LeRobot 保存目录后，转换、回放、导出都使用同一设置，刷新页面后仍保留。回放直接读取已有 LeRobot 文件，不会再复制到新的回放数据目录。`runtime/` 只放后台任务日志和执行结果；此前截图中的 `runtime/deployment_acceptance` 是开发验证样例，不是日常数据目录。

网页按“采集 → 转换 → 回放 → 导出”排列。回放区选择 MCAP 或 LeRobot；导出区也选择格式，共用当前电脑的保存位置和导出按钮。原始采集、相机、姿态、提醒、部分 MCAP 回放、文件预览和删除等功能保留。LeRobot 导出选择整套数据集，与回放选择哪条 episode 无关。

原始 MCAP 默认仍在机器人 `/data/data`，可在网页选择其他目录。旧目录及旧 `local/名称` 数据集仍可读取，本次界面调整不搬动原始数据。

## 开机后使用

在机器人终端（或 SSH 登录机器人后）执行：

```bash
cd /data/r2_datacp
./start.sh
```

电脑网线连接机器人后打开 <http://10.42.1.101:18769/>。如果需要手动配置电脑有线网卡，使用未占用的 `10.42.1.x/24`；代理对机器人地址直连。既有 Ubuntu 桌面入口、Mac 转发地址由各电脑自己的 SSH 转发提供。

启动网页和读取数据不会发送机器人动作。网页“机器人回放”区域选择格式后的执行按钮，以及下文对应 CLI 命令，会通过厂商 SDK 发送动作。

正常关闭可先结束当前采集、转换或回放，然后执行：

```bash
./stop.sh
```

停止服务时采集进程会结束当前 MCAP 写入。以下 CLI 默认连接机器人本机 `http://127.0.0.1:18769`；从其他电脑使用时可指定 `./datacp --url http://10.42.1.101:18769 ...`。命令输出 JSON，便于脚本读取。

## 原始 MCAP 采集

```bash
./datacp status
./datacp record-dir /data/data/A --create
./datacp record-start --label grasp
./datacp record-status
./datacp record-next --label grasp
./datacp record-stop
```

选择 A 后，每条保存在 `A/episode_序号_时间/raw.mcap`；`record-next` 保存当前条并立即开始下一条。也可先停止再开始。可以通过 `--topics` 指定通道；不指定时使用采集程序的默认通道。原始报文直接写 MCAP，不在采集端对齐、重采样或做训练格式转换。

不运行网页服务时，独立采集后台还提供：

```bash
python3 studio/recordctl.py status
python3 studio/recordctl.py directory --path /data/data/A --create
python3 studio/recordctl.py start --label grasp
python3 studio/recordctl.py stop
```

## 原始 MCAP 在机器人回放

```bash
./datacp mcap-files
./datacp mcap-replay --file episode_1_采集时间 --speed 1
./datacp mcap-replay-status
./datacp mcap-replay-stop
```

`--file` 使用 `mcap-files` 返回的文件/episode 名称，位于当前采集目录。默认回放整条，不自动缩成几秒测试。MCAP 动作回放使用记录的实测关节与夹爪位置；文件画面预览不会控制机器人。SDK 实际发送频率和返回结果见网页及任务日志。

## 单条、多条、目录批量转换

网页“格式转换”区直接显示 MCAP 目录树，可在本区选择目录范围、勾选单条或多条 episode，也可勾选整个文件夹；不需要跳到导出区。转换选择与导出选择各自独立。也可使用“一个文件夹中的全部 episode”输入模式，然后填写本次转换文件夹名称和任务描述。LeRobot 保存目录同时供回放和导出使用。CLI 对应操作：

```bash
# 设定共同 LeRobot 保存目录（网页、转换、回放、导出共用）
./datacp lerobot-dir /data/r2_datacp/datasets

# 单条
./datacp convert --episode /data/data/A/episode_1_采集时间 \
  --name task_a --task '把物体放进盒子'

# 指定多条（每个 --episode 也可直接指定 raw.mcap）
./datacp convert --episode /data/data/A/episode_1_采集时间 \
  --episode /data/data/A/episode_2_采集时间 \
  --name task_a_selected --task '把物体放进盒子'

# 递归转换目录内的 episode
./datacp convert --source-dir /data/data/A \
  --output-dir /data/r2_datacp/datasets \
  --name task_a_batch --task '把物体放进盒子'

./datacp convert-status
./datacp convert-stop
```

输出路径就是保存根目录加本次文件夹名称，例如 `/data/r2_datacp/datasets/task_a_batch`。`--output-dir` 省略时使用网页保存的目录；`--name` 对应网页的“本次转换文件夹”。旧脚本的 `--repo-id owner/name` 参数保留兼容。批量任务使用同一个训练任务描述；不同任务可以分别转换到不同数据集。平台转换接收本平台保存的完整 `raw.mcap`，也可以发现一个目录中的多条已保存记录。

平台采集的一条 MCAP 转成一条 LeRobot episode，不按时长拆分。单条转换生成 `episode 0`；同一数据集中的多条依次为 `episode 0、1、2…`。转换区显示总体进度、已处理 MCAP 数、当前读写阶段、帧数和最终输出数量。点击“查看转换结果”会在本区打开输出目录，可进入 `meta/`、`data/`、`videos/` 等查看文件，不会跳转或启动机器人回放。

MCAP 未采集图像时，转换保留现有 16 维状态和动作，生成无图像的 episode，仍可用来回放动作；不会补造图像。有图像时保存实际存在的相机。一个 LeRobot 数据集使用固定字段，相机种类不同的记录需分别转换到不同数据集，避免静默丢掉已有图像。需要三路图像训练时，应选含三路图像的原始记录。

转换器按 30 Hz 最近邻取得各通道，直接映射为 LeRobot。原生 command 存在时写入 action，缺少 command 时以对应采集 state 轨迹作为 action；具体来源写入 provenance。转换不会因数值范围、采样缺口或质量阈值主动拒绝/裁剪数据。解析格式仍必须能读取，若缺少构建状态和动作所需的字段，会报告具体原因。原始 MCAP 保留不变。

不启动网页时，可直接使用转换 CLI。`--inputs-json` 接受一个 JSON 文件，其中每项可以是 `raw.mcap`、episode 目录或包含多条 episode 的父目录：

```bash
mkdir -p runtime
# 单条输入
cat > runtime/convert_inputs.json <<'JSON'
["/data/data/A/episode_1_采集时间/raw.mcap"]
JSON
conversion/run_platform.sh --inputs-json runtime/convert_inputs.json \
  --output-dir /data/r2_datacp/datasets --dataset-path /data/r2_datacp/datasets/task_a_direct --repo-id local/task_a_direct \
  --task '把物体放进盒子' --result runtime/convert_result.json

# 批量输入：可以列出多条，也可以只写父目录 /data/data/A
cat > runtime/convert_batch_inputs.json <<'JSON'
["/data/data/A"]
JSON
conversion/run_platform.sh --inputs-json runtime/convert_batch_inputs.json \
  --output-dir /data/r2_datacp/datasets --dataset-path /data/r2_datacp/datasets/task_a_direct_batch --repo-id local/task_a_direct_batch \
  --task '把物体放进盒子' --result runtime/convert_batch_result.json
```

底层 `--dataset-path` 是实际保存位置，`--repo-id` 是格式内部标识，不会为指定的实际路径再添加 `local/` 层。以上直接转换命令不修改网页保存目录；如果换到其他根目录，使用 `./datacp lerobot-dir /新的根目录` 更新页面。

这些命令读取原文件并写入新数据集，不需要采集或网页服务运行；进度在终端输出，结果写入指定的 `--result` 文件。

## LeRobot 数据检查与实机回放

```bash
./datacp datasets
./datacp inspect --dataset /data/r2_datacp/datasets/task_a_batch
./datacp inspect --dataset /data/r2_datacp/datasets/task_a_batch --episode 0

# 以下命令会发送动作，直接从所选 episode 第一帧开始
./datacp replay --dataset /data/r2_datacp/datasets/task_a_batch --episode 0
./datacp replay-status
./datacp replay-stop
```

`replay` 默认回放所选 episode 的全过程；不同 episode 不自动串接。读取的是转换后的 `action`，不再重新从 MCAP 取动作，也不把 `observation.state` 当作 action。其原始来源请看转换 provenance。

回放使用机器人本机 GDK SDK，按 LeRobot 时间戳推进，目标发送频率 100 Hz，在源帧之间保持上一值，不插值或裁剪；末帧保持一个源帧周期。SDK 返回拒绝会记录并继续，实际发送 Hz、未发送帧及反馈写入 `runtime/lerobot_*/`。SDK 接受请求不等于机器人已正确跟随。当前动作维度包含双臂和双夹爪，不控制头、腰、底盘。

也可以完全绕过网页服务直接运行回放 CLI：

```bash
replay/replay.sh --dataset /data/r2_datacp/datasets/task_a_batch --episode 0 \
  --run /data/r2_datacp/runtime/manual_replay_01
```

附加 `--dry-run` 只读取文件，不加载 SDK、不发送动作；直接执行时 Ctrl+C 可停止。每次选择新的 `--run` 目录。更多接口和日志字段见 `replay/README.md`。

## 两种数据导出

**原始 MCAP**：网页“数据导出”中选择 MCAP，支持文件夹批量选择和展开选择 episode，可直接写入访问电脑选择的目录，也可下载 TAR。CLI：

```bash
./datacp library --root /data/data
./datacp export-mcap --episode /data/data/A/episode_1_采集时间 \
  --episode /data/data/A/episode_2_采集时间 --output /目标目录/raw_episodes.tar
```

**转换后的 LeRobot**：同一“数据导出”区域选择 LeRobot 和数据集后，可以选择访问电脑的目录，按原有目录结构直接导出；也可以下载完整数据集 TAR（metadata、Parquet、视频与 provenance）。目录写入需使用支持目录选择的 Chrome/Edge 等浏览器；不可用时使用 TAR 下载。CLI 下载 TAR：

```bash
./datacp export-lerobot --dataset /data/r2_datacp/datasets/task_a_batch \
  --output /目标目录/task_a_batch.tar
```

导出不删除机器人原文件。CLI 保存到运行 `datacp` 的电脑；如果在机器人终端运行，输出也在机器人本机。要直接导出到访问电脑，可在电脑浏览器下载，或复制 `datacp` 客户端到该电脑并使用 `--url` 连接机器人。已有输出文件不会被覆盖。

## 日志与维护

```bash
systemctl --user status r2-mcap-recorder.service r2-mcap-studio.service
journalctl --user -u r2-mcap-studio.service -n 80 --no-pager
./datacp convert-status
./datacp replay-status
```

转换和回放的 `run_dir` 下保留 `worker.log`、进度和最终结果。私有机器人标识放在 `studio/config/robot_control.json`，不要公开上传。旧路径如保留兼容符号链接，其实际代码仍以 `/data/r2_datacp` 为准。
