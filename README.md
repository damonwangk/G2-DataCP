# R2 机器人本机数据工作台

程序、前端、MCAP 采集、文件预览和 SDK 回放均部署在机器人：
`/home/agi/Project_codex/r2_mcap_studio`。本机数据默认目录：`/data/data`。

- 网线直连电脑打开： http://10.42.1.101:18769/
- 当前 Mac 转发入口继续使用： http://127.0.0.1:18765/
- Ubuntu 只提供网线连接和现有 SSH 转发；不运行工作台后端，不再自动拉取记录。
- 所有前端资源和 Python 依赖已放在机器人，不需要外网，不使用 8766。

## 日常使用：一个文件夹连续采集
1. 机器人开机后手动启动采集后台和网页服务（见下文）；Ubuntu 连上网线后，双击桌面「机器人采集工作台」。Ubuntu 只显示网页，不运行采集程序。
2. 选择机器人保存文件夹，例如 `/data/data/A`。下一次开始采集仍使用 A，无需重新选目录。
3. 「机器人端开始采集」创建 `A/episode_1_采集时间/raw.mcap`；「结束机器人采集」保存并停止。
4. 连续分段时点击「保存并开始下一条」：先保存当前 episode，随后在同一文件夹创建下一条；选择的原始通道继续使用。也可结束后再点开始。
5. 同目录序号递增，删除记录后也不回退；换到新空目录从 1 开始。不对齐、不重采样、不自动做数据质量核验。

## 按文件夹导出和删除
- 「导出 / 删除范围」只决定管理哪些机器人文件夹，不改变采集目录。可选 `/data/data` 管理 A、B 等多个任务文件夹。
- 勾选文件夹选择其中全部完成记录；展开后可勾选部分 episode。正式记录、执行反馈和测试类别会标注在列表中。
- 导出到当前打开网页的电脑，支持 Windows、Linux、macOS。目录导出和 TAR 下载均保留范围根目录及下级目录，例如 `data/A/episode_1_时间/raw.mcap`；若管理范围就是 A，则导出为 `A/episode_1_时间/raw.mcap`。
- MCAP 与附属文件按原字节导出，不修改机器人原文件。目录导出不覆盖已有同名文件；不支持本机目录接口的浏览器使用 TAR 下载，保存位置由浏览器的下载/另存为设置决定。
- 「删除所选机器人数据」会先显示记录条数和占用空间，再确认永久删除。删除只影响勾选的机器人记录，已导出到电脑的文件保留；采集文件夹本身保留。正在采集或回放使用的记录不能删除。

## 机器人关机后重新使用
正常流程：机器人开机 → 手动启动机器人采集服务和网页服务 → 电脑接上网线 → 打开网页。这两项服务不再设置为开机自动启动。关闭网页不影响正在采集的 episode；关机器人电源前请结束并保存当前采集。

通过现有 Ubuntu 启动：在 Ubuntu 终端执行以下命令，然后双击桌面「机器人采集工作台」打开网页：
```bash
ssh g2 'systemctl --user start r2-mcap-recorder.service r2-mcap-studio.service'
```
上述命令只启动采集和网页服务，不下发机器人动作。如果 Ubuntu 的网页入口连接异常，可运行 `systemctl --user restart r2-robot-recorder-tunnel.service` 后重新打开入口。

通过其他电脑网线直连时，仍需先在机器人端手动启动两项服务；可登录机器人执行：
```bash
systemctl --user start r2-mcap-recorder.service r2-mcap-studio.service
```
然后在连到机器人网线的电脑上打开 `http://10.42.1.101:18769/`。Mac 的现有入口 `http://127.0.0.1:18765/` 由登录后自动启动的 SSH 转发提供；该转发不启动机器人上的采集服务。

## 机器人本机命令
```
cd /home/agi/Project_codex/r2_mcap_studio
python3 recordctl.py status
python3 recordctl.py directory --path /data/data
python3 recordctl.py start --label episode
python3 recordctl.py stop
systemctl --user status r2-mcap-recorder.service r2-mcap-studio.service
```
采集是独立后台进程，关闭网页或断开电脑不停止采集。机器人开机后需手动启动采集和网页服务。
原有通道选择、频率、范围提醒、只读控制检测、姿态保存/恢复和部分关节/夹爪回放保留。姿态不包含底盘。

## 原始数据保存
收到的已订阅通道报文直接写入 MCAP，不做字段删除、重编码、对齐、插值、重采样、归一化或自动质量核验。

- /raw/*：完整 Aorta 原报文字节。
- 原生状态 topic：原始 Protobuf payload 字节，不重新序列化。
- 三路 RGB：原始 JPEG 字节；另写标准 foxglove.CompressedImage 消息便于文件预览，不转码。
- /metadata/*：源时间、发布 PTP 时间、接收时间、序号等附加信息。
- MCAP 数据块使用 ZSTD 无损压缩，不改变消息内容。
- 新记录策略为 raw_wire_v3。原报文存在温度等字段时也完整保留；深度仍不订阅。
- JSON 仅用于前端显示和 sidecar 记录，MCAP 内状态仍是 Protobuf。
- 写入中为 raw.mcap.partial，正常结束后改名为 raw.mcap。
- 旧 episode 内容不修改；新策略无法恢复旧版本录制时已删除的字段。
- 后续数据转换 pipeline 与 LeRobot 转换由用户另外实现，当前采集不调用 pipeline.py。

## 回放
先通过厂商 SDK 请求 INPUT_GDK、CTRL_JOINT_POSITION、SAFE_NORMAL，再返回首个记录姿态，然后按 MCAP log_time（该文件采集主机的接收时间）推进整个 episode 时长。

按照 SDK 要求的 100 Hz，使用该时刻各通道最新的原始实测位置：本体 motor_position，夹爪 end_state[].position。样本间保持上一值，不插值；通道首尾使用首/末样本。多个样本落在一个控制周期内时采用最后一条。

机械臂与夹爪共用正常模式 joint_servo_control，不混用 move_ee_pos。不因采样间隔、控制循环延迟或跟随误差自动中止。SDK 拒绝某个目标时，将原目标和返回错误写入执行反馈，并继续下一帧；不裁剪、不改写源 MCAP。

页面暂停/停止按钮保留。普通文件访问失败、初始化失败等执行故障直接报告。SDK 调用成功和实际动作跟随是两个不同结果。

原始状态回放不是重发原生指令流；缺失样本无法还原。源时间保留在文件内供后续处理。

## 姿态与限位
姿态保存只包含躯干、头部、双臂、双夹爪，不包含底盘。保存是只读操作。恢复通过 SDK 本体位置控制完成后恢复夹爪，不与伺服混用。执行结果显示在初始姿态区域。

关节接近限位和超限提示只用于前端提醒，不裁剪采集值或更改动作。

## 代码与运行

    cd /home/agi/Project_codex/r2_mcap_studio
    systemctl --user start r2-mcap-recorder.service r2-mcap-studio.service
    journalctl --user -u r2-mcap-studio.service -n 30 --no-pager

- native.py：Aorta 接收及原始值解析。
- storage.py：原始 MCAP 写入、文件预览、episode 目录。
- app.py / static/：页面和 API。
- sdk_worker.py / robot_replay.py：实机全过程回放。
- postures.py / pose_worker.py：姿态保存和恢复。
- tests/：采集、导出和页面功能测试。


控制权交接：显式点击动作回放或姿态恢复后，先调用厂商 OperatorEventService 的 takeover_pending，再设置 INPUT_GDK / CTRL_JOINT_POSITION / SAFE_NORMAL。该接口处理退出遥操后遗留的仲裁占用；只采集或打开页面不会调用。机器人地址和 AID 在 config/robot_control.json 中。

## 采集提醒与只读控制检测
SDK 范围提醒覆盖 22 个关节与双夹爪的位置，排除底盘；范围来自本机 GDK 3.1.1 配置。
范围两端各 5% 和端点显示黄色，SDK 接受范围外显示红色。夹爪正常开合端点也为黄色。
按收到的每条位置消息观察超限，短暂红色提醒保留 5 秒；不会阻止录制或改写 MCAP。范围数据见 config/joint_limits_source.json。

「检测能否控制（只读）」读取当前仲裁来源、遗留遥操门槛、SDK 路由和本体反馈。
它不会下发动作、切换模式或释放遥操；空闲也不等于已验证实际运动。检测结果只显示在页面，不参与采集、回放、姿态恢复的启用条件。

上一轮回放已走完整个时间轴，但后半段未持续跟随，且夹爪反馈方向仍需继续核对。当前不将其标记为完整动作验证通过；后续实机测试等待用户恢复控制授权。


## 2026-10-29：可选通道采集、部分回放与频率

- 在“采集内容与实时频率”勾选保存的数据，支持只采某一路 RGB、关节反馈、单侧夹爪等。按整个原始通道选择；勾选通道的报文字节保持不变。选择保存在当前浏览器，当前正在录制时不改变这一条记录的内容。
- 每个通道显示最近 3 秒接收 Hz 和当前保存 Hz；未录制/未选中明确显示，不以网页刷新率代替采集率。实测接收和保存主通道为 RGB 各约 30 Hz、关节/双夹爪各约 100 Hz；各通道独立，采集端没有统一重采样周期。
- 选择已有记录后，“选择执行的关节 / 夹爪”可按躯干、头部、左右臂、左右夹爪整组选取，也可逐关节选取。SDK 仅收到所选的关节名称与原始位置。文件不再必须包含完整关节加双夹爪，单侧夹爪、只有关节、单条位置样本也可建立轨迹。
- 只有图像的文件可使用文件预览；预览通道也能单独选择。“文件平均采集频率”是该通道消息数除以整条 episode 的时长，可能不同于实时窗口频率。
- 实机回放的 SDK 发送目标为 100 Hz，每 10 ms 一次；每个发送时刻取 MCAP 接收时间线上最近的已记录位置，样本之间保持上一值，不做轨迹插值。因此采集保存的是原始到达频率，动作回放有独立的 SDK 发送节拍，并非保证逐包按原始间隔重发。倍速只影响记录时间推进，不改变发送目标频率。
- 动作区显示发送目标、实测发送 Hz、SDK 成功返回 Hz；后两项由执行进程实际计数产生，未执行时显示尚无数据。SDK 成功返回不等于本体已经跟随到位。
- “恢复姿态”置灰原因：暂停回放仍是运行中的任务，并保持当前目标、继续记录执行反馈；录制中也不能另起姿态任务。现在姿态区直接显示原因及“查看当前执行任务”，回放区即使尚未选文件也显示当前任务。“继续回放”与“暂停回放”使用不同按钮文字。任务结束、已选姿态后按钮自动恢复可用。这些原有任务占用条件与范围提醒、只读控制检测无关。
- 本轮没有启动实机动作；部分回放通过临时 MCAP、假 SDK 和浏览器勾选验证，实机跟随仍需后续现场验证。部署前确认 recording.active=false、robot_replay.running=false。

## 本次部署验证边界
本次只验证软件部署、页面、文件传输和离线测试；没有下发机器人动作，没有对现场采集数据进行质量核验。时间字段保留记录时的原始时钟；新 episode 名称使用机器人本机时间。
浏览器目录写入依据 File System Access API；普通 HTTP 不支持时自动使用浏览器下载：
https://developer.chrome.com/docs/capabilities/web-apis/file-system-access

## 仓库内容与依赖

本仓库同步应用源码、前端、测试、消息描述集 schemas.pb 和本机 SDK 的关节范围数据。采集数据、姿态、运行配置、日志、第三方 SDK、Python 安装包均不包含在仓库中。

当前部署依赖机器人本机 `/home/agi/app` 内的厂商 SDK 和 `_deps` 内的 Python 环境；requirements.txt 为参考依赖清单，尚未验证从全新系统重建。实机回放的 `config/robot_control.json` 包含设备专属的 host、operator_port、robot_aid，须按该机器人实际配置建立，不要提交设备标识。没有该文件时请勿尝试动作控制。schemas.pb 和关节范围配置针对现有机器人版本，其他型号或 SDK 版本需要核对。

### 新电脑直连

将电脑网口接到机器人网络，在浏览器打开 http://10.42.1.101:18769/ 。若未自动获得同网段地址，可将有线网卡设置为未被占用的 `10.42.1.x`、子网掩码 `255.255.255.0`，网关和 DNS 留空；代理应对该地址直连。`127.0.0.1:18765` 是原 Mac 的 SSH 转发入口，不能在另一台电脑上直接复用。普通 HTTP 下若浏览器不支持目录写入，可用 TAR 下载。

机器人开机后须手动启动 `r2-mcap-recorder.service` 和 `r2-mcap-studio.service`，命令见「机器人关机后重新使用」。可用 `systemctl --user status r2-mcap-recorder.service r2-mcap-studio.service` 检查状态。
