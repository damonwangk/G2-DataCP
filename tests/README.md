# 软件测试

本目录放统一 CLI 的测试；采集/网页测试在 `studio/tests/`，转换和回放测试在各自目录的 `test_*.py`。测试与采集 episode 分开。

各测试代码最前写有单项运行命令。功能入口与配套说明见[功能使用索引](../docs/功能使用索引.md)；本页统一收拢离线复验命令。先使用本机已安装且满足依赖的Python，G2通常为`conversion/.venv/bin/python`；不要为了读说明运行环境安装脚本。

在机器人项目根目录运行：

```bash
# CLI 参数和 HTTP 契约，不连接机器人 SDK
python3 -m unittest discover -s tests -p 'test_*.py'

# 网页/API 和采集解析测试；使用已安装的本机依赖
cd /data/r2_datacp/studio
PYTHONPATH="$PWD/_deps" python3 -m unittest discover -s tests -p 'test_*.py'

# 转换软件测试：会生成临时样例和转换输出
cd /data/r2_datacp/conversion
PYTHONPATH="$PWD/extra_deps" .venv/bin/python -m unittest discover -p 'test_*.py'

# LeRobot / 原生回放测试，使用模拟 SDK
cd /data/r2_datacp/replay
PYTHONPATH=../conversion/extra_deps ../conversion/.venv/bin/python -m unittest discover -p 'test_*.py'

# 前端逻辑测试；需要 Node.js
cd /data/r2_datacp/studio
for test in tests/*_ui_test.cjs; do node "$test"; done

# 底盘软件测试，全部使用Fake，不加--execute
cd /data/r2_datacp/底盘控制
python3 -m unittest discover -s tests -p 'test_*.py'

# robot主机批任务调度测试，Fake服务器/转换器，不运行真实批任务
cd /home/robot/Project_codex/r2_datacp/batch_upload
python3 -B -m unittest -v test_runner
```

`studio/tests/fixtures/` 中的本机相机样本仅供消息解析回归测试使用，不随公开源码发布。日常使用不需要运行这些测试。

`replay/validate_clamped_smooth_samples.py`是需要两条已存在真实输入的纯读取验收工具，不属于自动发现的单元测试；路径与运行命令见[副本说明](../replay/README_clamped_smooth.md#离线复验)。测试输出、SDK返回码或时间轴完成标记均不能证明实机持续跟随。
