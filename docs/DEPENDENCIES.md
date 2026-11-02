# 发布依赖与部署差异

核心采集、转换和回放源文件取自本次 G2 只读快照，未重新部署或改变机器人运行任务。
公共仓库仅额外提供独立 README、配置模板、开源依赖准备脚本，以及不依赖私有 SDK 的合成测试夹具。
`conversion/install_linux_x86.sh` 与 `requirements-linux-x86.txt` 来自 Ubuntu 上对应的现有工具；它们是公开安装辅助，不属于 G2 aarch64 的运行入口。

## 开源转换依赖

```bash
R2_CONVERTER_PYTHON=python3.10 ./tools/prepare_dependencies.sh
# 在 G2 aarch64 上；代码与环境应位于 /data 下
./conversion/install_robot.sh
# 或在 Linux x86_64 上
R2_CONVERTER_PYTHON=/usr/bin/python3.10 ./conversion/install_linux_x86.sh
```

准备脚本仅从官方 LeRobot 固定提交 `0cf864870cf29f4738d3ade893e6fd13fbd7cdb5` 与 antlr4 4.9.3 构建 wheel，产物位于忽略的 `conversion/vendor/`。安装仍需要对应架构的 Python 依赖下载或自行准备 wheelhouse。厂商 SDK、SDK 动态库与设备配置由机器人本机提供，仓库不复制这些文件。

## 网页与采集后台

部署好的 G2 手动执行 `./start.sh` 即可；该脚本使用已经安装的两个 systemd 用户服务，不创建服务或开启自启动。
新机器需先安装 `studio/requirements.txt` 到本地环境（部署布局为 `studio/_deps`），按模块说明启动 `studio/robot_recorder_agent.py` 与网页。机器人连接和 SDK 环境需由使用者按实际机器准备。网页默认监听 `0.0.0.0:18769`，不会因为启动网页而开始录制。

## 发布测试

`replay/test_clamped_smooth.py` 默认读取 `tests/fixtures/synthetic_gdk_limits.json`。该文件明确是合成单元测试值，不是厂商限位配置，不会被真实回放入口使用；`R2_TEST_GDK_LIMITS` 可显式覆盖测试输入。
公开安装器额外安装 `requirements-mcap.txt`，补齐干净环境中的 MCAP/Protobuf 读取依赖；所有 Shell 入口补齐可执行权限。回放执行器、原始采集与转换数值逻辑未因本次发布而调整。

公开平台测试已改用 `studio/tests/synthetic_fixtures.py` 中生成的合成 JPEG/Aorta 包和测试专用 protobuf；不需要复制现场相机记录或厂商模块。测试只核对本平台包装层，不把模拟 protobuf 当成厂商线协议一致性的证据。
