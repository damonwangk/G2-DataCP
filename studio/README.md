# 采集与网页模块使用索引

机器人目录为`/data/r2_datacp/studio`。本目录提供网页/API、机器人本机MCAP采集、文件预览、数据管理，以及回放/姿态任务的后台编排。各代码文件开头的“使用说明”指出用途、入口和对应功能文档；内部模块通过入口调用，不逐个启动。

| 要做的事 | 功能说明 | 组成文件 |
|---|---|---|
| 启动工作台、选择采集通道、开始/下一条/结束、预览MCAP | [采集与预览](采集与预览使用说明.md) | `app.py`、`robot_recorder_agent.py`、`recordctl.py`、`native.py`、`storage.py`、`local_storage.py`、`robot_storage.py`、`record_filter.py`、`selection.py`、`static/` |
| MCAP实机回放、姿态保存/检查/恢复/删除、控制诊断 | [回放与姿态](回放与姿态使用说明.md) | `robot_replay.py`、`sdk_worker.py`、`postures.py`、`pose_worker.py`、`control_session.py`、`sdk_limits.py`、`control_diagnostics.py` |
| 目录树、转换/续转、LeRobot/原生回放调度、导出和MCAP删除 | [数据管理与转换调度](数据管理与转换调度使用说明.md) | `library.py`、`exports.py`、`workflow.py`、`static/robot_storage.js`、`static/workflow.js`、`static/native.js` |
| 查看字段与频率 | [采集与预览](采集与预览使用说明.md#查看原始数据与状态) | `data_contract.py`、`frequency.py`、`static/inspector.js`、`static/advisory.js` |
| 旧版MCAP对齐分析工具 | 本文下方及`pipeline.py`开头注释 | `pipeline.py`；当前采集/保真转换不自动调用 |

整套平台手动启动：

```bash
cd /data/r2_datacp
./start.sh
./datacp status
```

然后在浏览器访问`http://机器人IP:18769/`。根目录`start.sh`启动已配置的网页和采集服务；不会自动录制或发送机器人动作。本目录`start.sh`只启动网页进程，`open-workbench.sh`只在Linux桌面打开`http://127.0.0.1:18769/`，均不代替整套平台入口。完整CLI速查见[根使用说明](../使用说明.md)。

文件与输出位置：

| 位置 | 内容 |
|---|---|
| `config/` | 采集/数据管理目录设置、机器人控制配置、范围提示配置；本次说明整理不改配置值 |
| `poses/` | 保存的22个本体关节及双夹爪实测姿态，不含底盘；删除姿态移到`poses/.deleted/` |
| 项目`runtime/` | 转换、LeRobot/原生回放的命令、进度、结果和日志 |
| `studio/runtime/` | MCAP回放、姿态恢复的控制、进度、结果和worker日志 |
| 当前采集目录 | 每条已完成记录的`raw.mcap`、`raw.json`及执行反馈；用`./datacp record-status`查询 |
| `_deps/`、`vendor/` | 运行依赖与离线重建材料，不作为功能源码编辑 |
| `tests/`、`tests/fixtures/` | 离线软件测试和本机相机样本，不属于采集数据 |

`pipeline.py`是独立的旧版分析工具：在studio目录执行`python3 pipeline.py /绝对路径/raw.mcap`，在源文件旁写`raw.aligned-v2.mcap`及`raw.aligned-v2.quality.json`。它会生成对齐分析结果，包含对关节观测的插值；不替代当前`conversion/`中的24维保真转换，不被原始采集自动调用，也不会覆盖原MCAP。

离线测试示例（不会调用真实SDK动作）：

```bash
cd /data/r2_datacp/studio
PYTHONPATH="$PWD/_deps:$PWD/../conversion/extra_deps" \
  /data/r2_datacp/conversion/.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
node tests/workflow_ui_test.cjs
```

Python测试使用Fake SDK或模拟HTTP/文件；前端CJS测试使用Node的虚拟DOM/API，不需要启动网页。其余`*_ui_test.cjs`可同样逐个运行。[采集实现参考](../docs/reference/采集实现.md)、[姿态修复验收](../docs/姿态功能修复与Mac同步-20261031.md)和[历史资料](../docs/archive/README.md)保留实现背景；当前日常用法以上述功能说明为入口。
