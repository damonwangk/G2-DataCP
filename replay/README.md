# 原始指令回放

本页专门说明原回放入口；独立限幅平滑功能见[README_clamped_smooth.md](README_clamped_smooth.md)。按功能找其他工具见[总索引](../docs/功能使用索引.md)。以下执行命令由操作者手动启动；仅阅读不会执行动作。

机器人代码位置：`/data/r2_datacp/replay`。三种格式都回放保存的**控制指令**，不把实测状态当成动作，不插值、不裁剪、不自动返回首帧。

- LeRobot：直接读 Parquet 的 `action` 和关节名称；新数据使用 `source_timestamp_ns` 原始 PTP，旧外部数据没有此列时使用其保存的 `timestamp`。
- 原生离线目录：直接读 `record/-hal-joint_cmd.pbdat`、`-wbc-left_ee_command.pbdat`、`-wbc-right_ee_command.pbdat`；不使用 bundle/H5。
- 自采 MCAP：直接读相同原始指令话题；后台入口为 `studio/sdk_worker.py`，共用这里的执行器。

原文件回放保留全部原始指令事件，不先生成30 Hz训练表。事件按原 Header PTP 发送；没有新事件时以100 Hz保持上一条已有目标。原始事件多于100 Hz时仍逐条发送，页面和结果显示实际调用频率。若系统调度迟到，按原顺序补发已有事件，不计算中间目标、不丢事件；实际耗时和迟到量另记。

**NULL 不阻止回放。** 全部帧与原时间缺口保留；某轴为 NULL 时，当次 SDK 请求不包含该轴，其余原始值照常发送。全帧 NULL 时不构造位置目标，但继续推进原时间轴。既不填零，也不复制反馈替代指令。暂停只暂停时间推进并保持已有目标；停止来自显式用户操作或原生 SDK 初始化/控制权交接失败，不增加位置处理策略。

24维固定顺序为：腰5、头3、左臂7、左夹爪、右臂7、右夹爪。每个数值与 `meta/info.json` 中的名称逐项对应，禁止依赖未命名数组猜顺序。原始夹爪**指令**不做 `0.025 - value` 换算；该换算只用于反馈误差的坐标对照，不作用于发送值。采用安装的 GDK 3.1.1 `joint_servo_control(req, False)`，使用其原有正常控制模式，不修改厂商库或参数限制。

```bash
cd /data/r2_datacp
./datacp replay --dataset /data/r2_datacp/datasets/原始24维数据 --episode 0
./datacp native-replay --source /data/record/full_20260314022729
./datacp mcap-replay --file episode_2_20261029_134338
./datacp replay-status
./datacp replay-stop
./datacp mcap-replay-stop
```

独立运行：

```bash
./replay/replay.sh --dataset /path/to/dataset --episode 0 --run /path/to/new_run
./replay/replay.sh --native-recording /data/record/full_xxx --run /path/to/new_run
# 仅阅读，不加载运动 SDK：
./replay/replay.sh --dataset /path/to/dataset --episode 0 --dry-run --run /path/to/new_read_run
```

`trace.jsonl` 保留完整原目标（含 NULL）、实际发送名称/位置、源帧号、源时间、实际反馈和 SDK 返回。MCAP 后台任务还输出 MCAP 格式执行日志。`completed` 代表时间轴与事件读取完成；`rejected`、`tracking`、实际发送频率分别报告，不将 SDK 接受请求等同于机械运动完全复现。

文件职责：`source_episode.py` 读取原文件事件；`native_episode.py` 提供原生入口；`lerobot_replay.py` 读取 LeRobot 并执行共用发送循环；`trajectory.py` 只做名称映射、原值保持和诊断；`dataset_info.py` 提供网页只读信息。所有 `test_*.py` 使用模拟 SDK，不发送真机命令。

## 只读元信息检查

在项目根目录执行，输出JSON，不启动SDK：

```bash
conversion/.venv/bin/python replay/dataset_info.py --dataset /path/to/dataset
conversion/.venv/bin/python replay/dataset_info.py --dataset /path/to/dataset --episode 0
conversion/.venv/bin/python replay/native_episode.py --source /data/record/full_xxx
```

`dataset_info.py`只列出完整关闭且已提交的Parquet，保存有来源台账时同时给出源记录名；选`--episode`可进一步读取首帧、NULL及时间信息。`native_episode.py`经`source_episode.py`直接读逐话题指令，原生相机索引参与完整记录时间范围，不从反馈补动作。

## 配置、输入输出与停止

`replay.sh`默认SDK根为`/home/agi/app`，可用`R2_SDK_APP`覆盖；Python默认`conversion/.venv/bin/python`，可用`R2_REPLAY_PYTHON`覆盖。真实执行需要本机SDK、厂商服务及`studio/config/robot_control.json`中的连接配置。Shell不会安装依赖。

LeRobot入口使用`--dataset`与`--episode`，原生full使用`--native-recording`；该参数也能接受MCAP文件，日常MCAP任务通常由工作台管理。原命令行没有新副本的`--mcap`简写、`--gdk-limits`或速度参数。直接命令默认运行记录在本目录`runs/`，也可指定新的`--run`；使用`Ctrl+C`停止继续发送。网页/CLI任务的状态与停止沿用上述`datacp`命令。

## 离线测试

```bash
cd /data/r2_datacp/replay
PYTHONPATH=../conversion/extra_deps ../conversion/.venv/bin/python -m unittest discover -p 'test_*.py' -v
```

包括原目标/NULL、指令事件、名称、已提交episode以及限幅平滑副本的Fake SDK测试。测试依赖转换模块的临时protobuf/MCAP样例，不调用真实SDK。按单文件复验的方法也写在每个`test_*.py`文件最前。
