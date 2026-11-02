# 独立限幅平滑回放副本

用途：读取原有三类输入，只在本副本发送GDK请求前按关节名限幅、逐轴限速。机器人位置：`/data/r2_datacp/replay`。Mac对应文件也保存在本项目的`r2_datacp/replay`。

入口为`replay_clamped_smooth.sh` / `lerobot_replay_clamped_smooth.py`，辅助逻辑为`clamped_smooth_targets.py`。原回放入口、共用读取模块、转换器的执行逻辑保持原样，原始full、LeRobot和厂商配置不改。后续统一补充使用注释会改变源码字节哈希，原部署记录仍保留当时版本，维护记录另行核对正文/AST。以下实际回放命令由操作者手动执行；开发、文档和离线验收不代替实机跟随验证。

原回放规则见[README.md](README.md)，其他功能见[总索引](../docs/功能使用索引.md)。本功能由多个文件配合，文件顶部注释给出快速入口，本MD集中参数、输入输出、平滑规则与停止方法。

## 处理规则

1. 每次启动读取`/home/agi/app/gdk/config/mc_impl_config.json`，以名称匹配腰5、头3、左臂7、左夹爪、右臂7、右夹爪。夹爪使用当前G2的`end_effector.dahuan`范围。LeRobot夹爪别名沿用原名称映射，不改变夹爪指令方向。配置路径、SHA-256及24轴范围写入结果。
2. 每轴`desired = max(min, min(max, raw))`，例如上限1.1、原值1.2，先得到1.1。平滑后再次硬限幅，并按GDK实际float32转换语义检查范围；原本合法的数值不会被全面量化成float32。
3. 首个非NULL目标只有限幅，没有可用的历史目标，因此直接发送。之后从该轴上一次**SDK接受**的发送位置向desired靠近。每次最大步长为`速度 × min(距前次发送尝试的真实间隔, 0.01秒)`。默认腰/头/手臂为3 rad/s，双夹爪为0.1 m/s，即每次最多0.03 rad和0.001 m。这些是本副本的平滑参数，不是厂商速度上限。
4. 进入、持续超限和恢复正常时都使用同一个逐轴规则；所有已有数值轴都限速。事件之间的100Hz保持也继续逼近desired。调度迟到、失败或NULL期间不积攒大步长预算；SDK返回非0或异常时，不提交新位置历史。相同时间的多个原始事件预算可以为0。暂停保持变换后的位置，恢复不累积暂停预算。
5. NULL仍为NULL，当次不向SDK发送该轴，不补0、不复制反馈。该轴先前成功发送的位置仅作为以后重新出现时的平滑起点。NaN/Inf或缺失限位会在SDK导入前报错。
6. 保留原始事件、原PTP时间及原结束规则，事件之间仍按100Hz保持。**不延长原时间轴，也不自动追加收尾动作**。正常的大步同样可能被平滑，所以发送曲线可能落后于源目标；末尾尚未追上的距离记录在`final_target_residual`，正负号为最终限幅目标减最后SDK接受值，NULL或从未接受的轴为NULL。

本规则限制的是发送目标相对上次SDK接受目标的变化，不是相对当前实测反馈的闭环误差。不从反馈重规划、恢复姿态或重置厂商停止状态。首姿态不匹配、外部干预或厂商内部滤波/参考误差仍可能导致无法跟随。普通通道`joint_servo_control(req, False)`、周期0.01秒、空速度数组保持原设置；厂商PVT/LPF、限位与0.1rad监控阈值不修改。

两条LeRobot样例在默认参数下，发送目标相对源曲线最大关节偏离约0.090842/0.048358 rad，夹爪最大偏离约14.691/15.365 mm，末端残差均为0。这些是纯读取目标计算的结果，不是实测跟随误差。默认值是在1/2/3/4/5 rad/s及多组夹爪速率对照后选择的折中：1 rad/s会使第一条样例的正常快速动作滞后约0.523 rad，而3提高到4/5的最大偏离改善已较小。每次日志仍保存全部源目标与实际发送目标，以便核查平滑对具体动作的影响。

## 手动使用

从Mac进入G2：

```bash
ssh -t robot 'ssh -t g2'
cd /data/r2_datacp/replay
```

纯读取检查（遍历完整目标序列，不导入GDK、不发动作）：

```bash
./replay_clamped_smooth.sh --native-recording /data/record/full_20260314010540 --dry-run
./replay_clamped_smooth.sh --dataset /data/r2_datacp/datasets/robot_full_spotcheck_20261102/full_20260314010540 --episode 0 --dry-run
```

操作者手动开始实际回放，选择其中一条：

```bash
./replay_clamped_smooth.sh --native-recording /data/record/full_20260314010540
./replay_clamped_smooth.sh --dataset /data/r2_datacp/datasets/robot_full_spotcheck_20261102/full_20260314010540 --episode 0
```

第二条样例将末尾名称换为`full_20260314012619`。自采MCAP使用`--mcap /path/to/record.mcap`，也支持原有`--native-recording /path/to/record.mcap`读取路径。只需调整本副本平滑参数时可加`--joint-speed 3.0 --gripper-speed 0.1`；必须为有限正数。它们不会修改厂商配置。

终端`Ctrl+C`或向本进程发送`SIGTERM`可停止继续发目标；不附加恢复姿态。默认日志独立写入`replay/runs_clamped_smooth/<时间>/`，也可用`--run /path/to/new_run`指定本次目录。原`datacp`网页及其停止按钮仍指向原任务；本副本通过上述独立终端入口管理。

`trace.jsonl`逐次保留`raw_target`、`clamped_target`、实际尝试的`target/sent_positions`、受影响轴、平滑预算、原帧号/时间、原始action、反馈及SDK结果。`result.json`记录限幅/平滑次数、未发送帧、末端残差。`completed`表示源时间轴读取完成；`physical_tracking_verified`始终为false，SDK返回0不等于实机跟随已验证。

## 离线复验

以下命令只用Fake SDK或纯读取：

```bash
cd /data/r2_datacp/replay
/data/r2_datacp/conversion/.venv/bin/python -m unittest -v test_clamped_smooth
PYTHONPATH=/data/r2_datacp/conversion/extra_deps /data/r2_datacp/conversion/.venv/bin/python validate_clamped_smooth_samples.py --output /tmp/clamped_smooth_samples.json
```

验收覆盖两条真实full及对应LeRobot的全轨迹、范围、NULL、24轴名称、连续限幅/返回、100Hz保持和平滑步长；另用Fake SDK验证错误、暂停和停止。部署记录及原文件/厂商配置/样例的部署前后SHA-256核对保存在本项目`整理清单`和G2本副本的验收目录。
