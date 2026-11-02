# 原始记录转换、并行处理与环境工具

本目录把厂商原生 `full_*` 目录或自采 MCAP 转成 **LeRobot v2.1 MP4 视频数据集**。两类输入共用24维名称、真实头部帧主轴和原值保留规则。下列命令均从项目根目录执行，例如 G2 的 `/data/r2_datacp`；由操作者手动启动。转换不加载运动 SDK，也不启动或停止采集。

输入话题、名称、NULL和时间关联细节集中在 [PLATFORM_MCAP.md](PLATFORM_MCAP.md)；本页负责安装、运行、输出、恢复和文件职责。[项目使用说明](../使用说明.md)负责整体工作台入口，批处理说明（见部署系统原位置）负责每条full独立的数据集作业及后续传输，二者不要混用参数。

## 功能与入口

| 功能 | 推荐入口 | 配合的主要模块 |
|---|---|---|
| 单条或多条原始记录检查/转换 | `convert_r2.py` | `mcap_input.py`、`factory_input.py`、`recording_contract.py`、`raw_convert.py` |
| JSON清单转换、进度及结果JSON | `run_platform.sh` → `platform_convert.py` | 上述模块、`conversion_progress.py` |
| 多源并行后合并为同一个数据集 | `run_platform.sh --workers N` | `parallel_convert.py`、`batch_io.py` |
| MP4封装/无损RGB写入 | 由转换入口调用 | `video_mp4.py` |
| G2已完成记录的保守资源入口 | `run_robot.sh` | `convert_r2.py`、`episode_ready.py` |
| 独立环境安装与依赖自查 | `install_robot.sh` / `install_linux_x86.sh`、`robot_env_check.py` | `requirements-*`、`vendor/`固定依赖 |
| OpenPI 24维数据接口 | `openpi_r2.py`，作为库接入已有OpenPI | [OpenPI接入说明](../docs/reference/OPENPI.md) |
| 历史格式解析和兼容验读 | `r2_raw.py`、`robot_media.py`、`validate_lerobot.py` | 下文“验证与历史兼容范围” |

内部辅助模块没有独立启动命令。各自有Python/Shell文件开头的“使用说明”会给出所属功能和对应入口；测试文件头给出单项离线复验命令。`vendor/`、依赖环境及锁文件不作为功能入口编辑。

## 环境安装与自查

G2采用 Linux aarch64 / Python 3.10，代码与独立环境须放在 `/data` 下；需要 `uv`、基础Python和本目录的固定LeRobot wheel。下面的安装脚本会创建或更新独立虚拟环境，不应当作普通转换命令反复运行：

```bash
cd /data/r2_datacp
./conversion/install_robot.sh
./conversion/.venv/bin/python conversion/robot_env_check.py
```

G2脚本支持 `R2_CONVERTER_PYTHON`、`R2_CONVERTER_VENV` 指定基础解释器和环境。没有本地 `wheelhouse/` 时会安装所需依赖，需要相应网络访问。

robot等 Linux x86_64 / Python 3.10 主机使用另一安装脚本，默认基础解释器为 `/home/robot/anaconda3/envs/g2/bin/python`，可通过 `R2_CONVERTER_PYTHON` 指定：

```bash
R2_CONVERTER_PYTHON=/usr/bin/python3.10 ./conversion/install_linux_x86.sh
```

它在 `conversion/.venv` 安装CPU版依赖。`robot_env_check.py`输出平台、包版本和HEVC/H.264编解码能力JSON，不读取录制数据。以上安装和自查都不表示实机回放已准备完成。

## 单条/多条检查与转换

纯读取检查，保留输入，不写数据集：

```bash
./conversion/.venv/bin/python conversion/convert_r2.py \
  --input /data/record/full_xxx --input-format factory --inspect

./conversion/.venv/bin/python conversion/convert_r2.py \
  --input /path/to/raw.mcap --input-format mcap --inspect
```

正式转换到新目录：

```bash
./conversion/.venv/bin/python conversion/convert_r2.py \
  --input /data/record/full_xxx --input-format factory \
  --output /path/to/new_dataset --repo-id local/new_dataset \
  --task 'pack the phone boxes' --threads 1
```

`--input`接受一个或多个MCAP文件、原生记录目录或待发现的父目录。`--input-format`可选 `factory`、`mcap`、`auto`。`--task`提供统一任务；直接Python入口也支持 `--tasks-json` 的“记录名→任务文本”映射。输出不能等于、包含或位于输入内部。

`--fps`只接受30，它是LeRobot行号与MP4显示时间的索引频率，不会按固定时间网格补图或重采样原PTP。`--codec`、`--media`、`--image-size`、`--max-offset-ms`、`--max-camera-age-ms`、`--max-gap-ms`是保留的兼容参数，当前主写入器仍使用下述保真规则，不据它们缩放、过滤、插值或切换有损输出。`--min-free-gb`当前只解析/传递，主写入器未实现这个空间门槛，不能将参数值当作已执行的磁盘检查。

## JSON清单、并行转换、进度与结果

`inputs.json`必须是非空路径字符串数组，例如：

```json
["/data/record/full_001", "/data/record/full_002"]
```

将多条源记录合并为一个数据集，每条源记录对应一个episode：

```bash
./conversion/run_platform.sh \
  --inputs-json /path/to/inputs.json --input-format factory \
  --dataset-path /path/to/new_mp4_dataset --repo-id local/new_mp4_dataset \
  --task 'pack the phone boxes' --workers 4 --threads 1 \
  --result /path/to/result.json --progress /path/to/progress.json
```

`--dataset-path`就是数据集准确目录，不追加repo-id；兼容旧接口时可用 `--output-dir /path/to/root`，此时实际路径为`output-dir/repo-id`。`--workers`是同时处理不同源记录的进程数，`--threads`是每条记录的图像编码线程数；单条源记录不会再被拆成多个worker。各worker先产生临时分片，由主进程统一合并索引和元数据。

加 `--inspect`只检查源，但仍会写指定的结果/进度JSON。`progress.json`是原子更新的阶段/帧数/完成记录数；百分比不等于最终数据集已经提交完成。`result.json`提供完成标记、错误、实际数据集路径和episode/帧数，另须核查`provenance`及最终文件。

这里的`--workers`不等于`batch_upload/start.sh --jobs`：后者管理每条full独立输出的数据集及其作业状态。当前任务的输入、目标目录、是否上传以批处理说明（见部署系统原位置）和实际命令为准；本转换器本身没有上传动作，不以历史云服务器路径作为默认操作指引。

## 恢复与完成判据

同一输出继续时加`--resume`。已有输出目录不加该参数会拒绝覆盖；已有PNG图像数据集不能混入当前MP4输出，请为MP4使用新目录。恢复会核对当前保存设置，并由事务日志恢复未完整提交的episode；串行写入器还核对已完成来源文件指纹和任务文本。原始采集文件不写回。

完成后至少查看：`meta/info.json`、相应Parquet/MP4、`provenance/segments.jsonl`、`provenance/conversion.json`和`provenance/SUCCESS`，以及JSON入口的`result.json`与错误列表。`SUCCESS`表示本次要求的来源已转换，不等于`training_ready=true`；含NULL的数据仍保留，训练阶段的空值策略需要另行明确。

`run_robot.sh`默认启用`--ready-only`，使用单线程和低CPU/IO优先级，不启停录制。它同时传入`--min-free-gb 20`，但该参数当前没有在主写入器中执行空间门槛判断。原生记录的ready标记只能由采集方确认所有写入者已关闭后创建：

```bash
# 会在原记录目录写标记；仅由确认采集已经结束的操作者执行。
./conversion/.venv/bin/python conversion/episode_ready.py \
  /data/record/full_xxx --confirm-finalized

./conversion/run_robot.sh --input /data/record/full_xxx \
  --output /path/to/new_dataset --repo-id local/new_dataset \
  --task 'pack the phone boxes'
```

`episode_ready.py`沿用历史的bundle及三相机文件清单，不自动确认逐话题pbdat完整性，也不侦测采集进程是否结束。MCAP的ready检查只要求已完成文件名存在，文件闭合仍由采集方负责。普通`run_platform.sh`入口不自动要求ready标记。

## 输出与视频保存规则

```text
dataset/
  meta/info.json
  meta/episodes.jsonl
  meta/episodes_stats.jsonl
  meta/tasks.jsonl
  data/chunk-000/episode_000000.parquet
  videos/chunk-000/observation.images.head_color/episode_000000.mp4
  videos/chunk-000/observation.images.hand_left_color/episode_000000.mp4
  videos/chunk-000/observation.images.hand_right_color/episode_000000.mp4
  provenance/settings.json
  provenance/conversion.json
  provenance/frames.jsonl
  provenance/segments.jsonl
  provenance/SUCCESS
```

相机字段只包含源文件实际存在的相机；头部RGB必须存在。每相机/每episode独立MP4，`meta/info.json`中`codebase_version=v2.1`、相机`dtype=video`、`video_path`指向MP4。Parquet保存24维float64状态/动作、Arrow NULL、有效性和时间列，不内嵌PNG。

原生H.265在原相机帧与训练表完整一一对应且无B帧时，直接把原HEVC码流封装MP4。MCAP JPEG、帧映射差异或带B帧原生输入，按训练表已选择的原帧解码后流式保存为H.264 RGB无损MP4；保留解码后的RGB像素，当前输出不再保存逐帧原JPEG字节或落地PNG。原分辨率、原选帧及24维数值不变，不插值、不补值、不缩图。

MP4显示时间和LeRobot`timestamp`按行号/30匹配；原头部PTP原样保存为`source_timestamp_ns`。这些转换规则不改变原回放入口；独立限幅平滑副本的处理仅作用于其发送目标，见[回放说明](../replay/README_clamped_smooth.md)。

## 验证与历史兼容范围

离线测试使用合成源数据和临时目录，不初始化真实SDK：

```bash
./conversion/.venv/bin/python -m unittest discover -s conversion -p 'test_*.py' -v
```

| 测试组 | 覆盖范围 |
|---|---|
| `test_mcap_input`、`test_factory_input`、`test_converter` | 原话题读取、同源输入一致性、NULL/PTP、图像尺寸与像素 |
| `test_video_mp4`、`test_optional_camera_conversion` | 当前MP4封装/无损像素、原值、可选相机及固定版LeRobot验读 |
| `test_parallel_conversion`、`test_batch_io`、`test_platform_paths` | 并行合并、断点恢复、事务提交与输出路径 |
| `test_episode_ready`、`test_ready_filter` | ready标记及过滤边界 |
| `test_r2_raw`、`test_robot_media` | 历史bundle/图像工具兼容行为 |

`validate_lerobot.py`仍逐行读取Parquet中内嵌图像`bytes`，适用于历史图像/PNG数据集，不是当前MP4数据集的通用验收入口。它需要provenance中的原始路径仍可访问。`r2_raw.py`提供历史bundle解析；当前原生转换读取逐话题pbdat。`robot_media.py`保留历史图像写入辅助，当前MP4主链由`video_mp4.py`实现。这些文件保留兼容用途，未在本次说明整合中改变执行逻辑。

历史验收记录见[MP4转换实现与验收](../docs/MP4转换实现与验收-20261031.md)和[归档说明](../docs/archive/README.md)。2026-10-31的49项robot回归及2750帧真实样例结论属于当时验收，当前环境状态需重新执行对应检查；本页不把历史测试当作当前机器人运动验证。
