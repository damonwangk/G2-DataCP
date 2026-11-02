# R2 DataCP 机器人数据平台

## GitHub 代码分工

| 仓库 | 内容 |
|---|---|
| [G2-DataCP](https://github.com/damonwangk/G2-DataCP) | 当前采集平台、网页与 API、数据管理，以及一体运行所需的转换/回放模块 |
| [G2-Replay](https://github.com/damonwangk/G2-Replay) | 独立的 MCAP / 原生离线 / LeRobot 回放与原有独立限幅平滑副本 |
| [G2-Converter](https://github.com/damonwangk/G2-Converter) | 独立的 MCAP / 原生离线 → LeRobot v2.1 MP4，支持批量并行 |

本次源码来自 **2026-11-02 的 G2 部署快照**，包含任务名保存与 MCAP 直接选择文件的修复。
平台保留一体目录结构；另外两个仓库用于单独查看和运行对应功能，不需要修改机器人上的部署。
文件来源与哈希见 [SOURCE_SNAPSHOT.json](SOURCE_SNAPSHOT.json)。不包含现场数据、设备私有配置、SDK、运行日志和 Python 环境。

从 GitHub 新安装时，先手动准备 Python 依赖与机器人本机配置；已经部署的机器人仍沿用下面的手动启动方式。
转换器安装前，执行 `R2_CONVERTER_PYTHON=python3.10 ./tools/prepare_dependencies.sh` 构建固定版本的开源依赖，详见 [依赖准备](docs/DEPENDENCIES.md)。


**当前数据规则：原始MCAP逐包保存；两类输入按真实头部帧关联为24维 float64 LeRobot，NULL、原分辨率与PTP均保留，视频保存为MP4。** 原回放保持原指令；独立限幅平滑副本仅变换发送目标，两者均保留NULL，不自动恢复首姿态。详见 [转换规则](conversion/README.md)、[原回放](replay/README.md) 和 [限幅平滑副本](replay/README_clamped_smooth.md)。

机器人 Linux 的统一项目目录为 **`/data/r2_datacp`**。采集、MCAP / 原生记录转换、三种格式的动作回放和数据导出均部署在这里；Mac、Ubuntu 等电脑用于访问网页和导出数据。

**按功能查找请先读 [功能使用索引](docs/功能使用索引.md)**。每个自有代码文件顶部都有用途和使用入口；多文件功能链接到对应模块说明。日常完整命令见 [使用说明.md](使用说明.md)，开发定位见 [代码结构](docs/代码结构.md)。

```text
/data/r2_datacp/
├── README.md               项目入口和目录索引
├── 使用说明.md             启动、采集、转换、回放、导出、姿态、删除的命令
├── datacp                  统一命令行客户端
├── start.sh / stop.sh       手动启动和关闭平台
├── studio/                 采集后台 + 网页/API + MCAP 回放/导出
│   ├── static/             前端页面、脚本和样式
│   ├── config/             本机配置
│   ├── poses/              已保存姿态
│   └── tests/              网页/采集模块的软件测试
├── conversion/             MCAP / 原生离线采集 → LeRobot
│   ├── platform_convert.py 平台转换入口
│   ├── parallel_convert.py 批量并行处理与 episode 合并
│   └── .venv/、extra_deps/ 机器人转换运行环境
├── replay/                 LeRobot / 原生离线记录的厂商 SDK 回放
├── datasets/               LeRobot 数据集（保存位置可修改）
├── runtime/                转换和 LeRobot / 原生回放的任务日志、进度、结果
├── data/                   历史保留数据目录
├── tests/                  统一 CLI 的软件测试
├── docs/                   代码结构、实现参考和历史文档
└── deployment/             当前功能验收、文件整理清单
```

| 要改什么 | 从哪里看 |
|---|---|
| 机器人原始 MCAP 采集 | [studio/robot_recorder_agent.py](studio/robot_recorder_agent.py)、[studio/README.md](studio/README.md) |
| 前端页面 | [studio/static/index.html](studio/static/index.html) |
| MCAP / 原生数据转换 | [conversion/README.md](conversion/README.md) |
| MCAP 真机回放 | [studio/robot_replay.py](studio/robot_replay.py)、[studio/sdk_worker.py](studio/sdk_worker.py) |
| LeRobot / 原生真机回放 | [replay/README.md](replay/README.md) |
| 独立限幅平滑回放 | [replay/README_clamped_smooth.md](replay/README_clamped_smooth.md) |
| 原始数据导出、删除 | [studio/exports.py](studio/exports.py)、[studio/library.py](studio/library.py) |
| LeRobot 导出、后台任务调度 | [studio/workflow.py](studio/workflow.py) |
| 姿态、关节范围、控制状态检测 | [docs/代码结构.md](docs/代码结构.md) |

MCAP 采集默认保存在 `/data/data`，机器人自带原生离线记录在 `/data/record`，LeRobot 读取网页设置的统一保存目录。源数据与程序代码分开，回放直接读取对应格式的保存位置。

机器人重启后手动执行，不设置开机自启：

```bash
cd /data/r2_datacp
./start.sh
./datacp status
```

网页端口为 **18769**，服务监听局域网。网线入口为 `http://10.42.1.101:18769/`，无线局域网入口以启动输出为准。启动网页不会自动录制或回放动作。

MP4历史部署证据见 [MP4转换实现与验收](docs/MP4转换实现与验收-20261031.md)。`docs/`下带日期的核查/验收文件及`docs/archive/`保留当时结论，当前操作以功能索引指向的模块说明为准。跨主机批转换工具是否部署应以所在主机实际文件为准；本轮说明整合不启动或调整现有批任务。
