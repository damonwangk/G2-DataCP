> 平台 `raw.mcap` 的单条/批量转换入口与命令见 [PLATFORM_MCAP.md](PLATFORM_MCAP.md)。以下保留原厂 bundle 转换说明。

# G2 / R2 采集目录 → π0.5 LeRobot 批量转换

**已部署到机器人 `/data/r2_lerobot/tool`，启动见 [ROBOT_QUICKSTART.md](ROBOT_QUICKSTART.md)。** 本项目只提供格式转换，不依赖机器人 SDK。独立环境为 ARM64 / Python 3.10；不要复制 Mac 的 `.venv`。机器人入口只转换生产端已完结标记的目录。

入口是 `convert_r2.py`。输入为**已经解压的 episode 目录或其父目录**，递归发现采集系统的 `record/-genie-bundle_data.pbdat`，支持连续处理 1000 条及以上目录。一次只规划、解码一个原始 episode，保存后释放图像和数据表；不会把全部视频载入内存。没有硬编码这两份样例的目录名、任务编号或任务描述。

输出遵循 [Physical-Intelligence/openpi 官方转换路径](https://github.com/Physical-Intelligence/openpi#fine-tuning-base-models-on-your-own-data)，使用其固定的 LeRobot 提交 `0cf864870cf29f4738d3ade893e6fd13fbd7cdb5`，格式为 **v2.1**。这里的版本选择用于兼容官方 OpenPI；不要用最新版 v3 替换依赖。完整模型接入示例见 [OPENPI.md](OPENPI.md)。

## 开始使用

机器人部署目录已安装独立 `.venv`。其他机器需先完成下文安装；在已安装环境的目录运行：

```bash
source .venv/bin/activate

# 同一批演示相同任务；任务文本由调用方提供。
python convert_r2.py \
  --input /path/to/recordings \
  --output /path/to/lerobot/local/my_robot_tasks \
  --repo-id local/my_robot_tasks \
  --task '填写这批数据的任务指令'
```

不同 episode 使用不同任务时，用 `--tasks-json /path/to/tasks.json` 替代 `--task`：

```json
{
  "episode_directory_000001": "第一类任务指令",
  "episode_directory_000002": "第二类任务指令"
}
```

JSON 的键是 episode 目录名。名字重复但任务不同的目录应分批执行或先整理目录名。

中断后使用相同命令并加 `--resume`。已提交片段按源文件指纹跳过；未完成片段恢复后重做。可继续在同一个父目录添加新 episode，再续跑追加。源文件及已经转换的任务文字必须保持不变。

```bash
python convert_r2.py --input /path/to/recordings \
  --output /path/to/lerobot/local/my_robot_tasks \
  --repo-id local/my_robot_tasks --tasks-json /path/to/tasks.json \
  --resume --skip-errors
```

`--skip-errors` 在转换阶段记录失败目录并继续其他目录；存在失败时最终退出码仍非零。任务映射必须覆盖全部待转换目录；缺少任务文本会在开始前报错，不能由 `--skip-errors` 跳过。明细在输出的 `provenance/errors.jsonl`。默认遇错即停止。禁止两个进程同时写同一个输出目录。

## 新机器安装

通用环境使用 Python 3.10 / 3.11，支持 Linux / macOS；以下示例使用 3.11。当前机器人使用单独的 ARM64 / 3.10 部署配置。转换本身不需要 GPU。先安装数据处理依赖，再安装固定版本 LeRobot；分开安装避免拉入与转换无关的硬件和仿真依赖。

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-runtime.txt
python -m pip install --no-deps \
  'lerobot @ git+https://github.com/huggingface/lerobot.git@0cf864870cf29f4738d3ade893e6fd13fbd7cdb5'
```

本次实际运行环境另见 `requirements-validated.txt`。训练环境使用 OpenPI 自己的依赖锁，与这个独立转换环境分开。

## 输入与输出字段

本工具适配样例中已观察到的 **G2A / Dahuan 原始 bundle + H.265 + 时间索引**采集格式，不是任意机器人日志的通用解析器。后续同格式目录可直接使用。遇到未知 schema、关节名称或次序会报错。

| LeRobot 字段 | 类型 | 采集字段 |
|---|---|---|
| `observation.state` | float32[16] | `state__joint__position` 与左右 `state__*_effector__position` |
| `action` | float32[16] | 原始 `action__joint__position` 与左右 `action__*_effector__position` |
| `observation.images.head_color` | RGB 视频 | `camera/head_color/` |
| `observation.images.hand_left_color` | RGB 视频 | `camera/hand_left_color/` |
| `observation.images.hand_right_color` | RGB 视频 | `camera/hand_right_color/` |
| task metadata | 文本 | `--task` 或 `--tasks-json` |

16 维顺序：左臂 7 关节、左夹爪、右臂 7 关节、右夹爪。保留绝对目标与原始数值，不二值化夹爪，不把 state 复制成 action。双臂按弧度保留；Dahuan 夹爪保存原生量值，未擅自换算为米或角度。头、腰、底盘、其他相机和深度等原始数据仍保留在输入目录，本训练视图不包含它们。

默认按 30 Hz 均匀网格选择最近 bundle，匹配偏差上限 20 ms；每路图片按自己的源时间戳寻找原始 H.265 帧，腕部缓存帧年龄上限 50 ms。缺动作、缺媒体或时间断点会跳过并分段，因此输入一个录制目录可能产生多个 LeRobot episode；不会把间断两端直接拼起来。只转换格式和字段，不判断演示任务是否有价值。

图像保持比例填充到 256×256，官方 API 写出数据集，CPU H.264 编码器写入 MP4；默认 `--threads 1`，编码方式可显式选择 `--codec libsvtav1`。视频经过有损压缩，像素不会逐字节等于原始 H.265 解码图像。需要无损存储解码后的 RGB 时使用 `--media image`，图片将嵌入 Parquet，磁盘开销较大。

```text
output/
  data/chunk-000/episode_000000.parquet
  videos/chunk-000/observation.images.head_color/episode_000000.mp4
  videos/chunk-000/observation.images.hand_left_color/episode_000000.mp4
  videos/chunk-000/observation.images.hand_right_color/episode_000000.mp4
  meta/info.json
  meta/tasks.jsonl
  meta/episodes.jsonl
  meta/episodes_stats.jsonl
  provenance/frames.jsonl       # 每行的源 bundle、相机索引、原始纳秒时间
  provenance/segments.jsonl     # 断点续跑已提交片段
  provenance/conversion.json   # 转换设置与结果
  provenance/SUCCESS           # 本轮全部输入成功才产生
```

## 验证

```bash
# 仅检查结构并列出转换计划，不产生数据集。
python convert_r2.py --input /path/to/recordings --inspect

# 单元测试：解析、字段映射、时间间断和恢复。
python -m unittest discover -p 'test_*.py' -v

# 用官方 LeRobot 加载器核查数值、图像及 50 步动作序列。
python validate_lerobot.py \
  --root /path/to/lerobot/local/my_robot_tasks \
  --repo-id local/my_robot_tasks
```

验证脚本会读取原始输入目录，请保留原始目录路径。其逐行源值比对适合验收，转换器本身不依赖它。当前测试覆盖两份用户样例、续跑去重和实际进程中断恢复；尚未对真实 1000 条数据完成吞吐量压测。

转换期间输入应是已停止录制的稳定离线目录。恢复针对进程中断，不承诺突然断电或文件系统损坏时的持久化保证。默认视频编码与逐段提交按顺序执行，适合先稳定完成批量处理；耗时取决于视频长度、CPU 和磁盘速度。

部署入口增加 `--ready-only`，读取 `.lerobot-ready.json` 后才解析源数据。标记应由采集程序在关闭所有写入后创建；普通离线格式样例仍可直接使用 `convert_r2.py`。早期版本的视频输出使用 AV1，如需继续追加，明确传入 `--codec libsvtav1 --resume`。
