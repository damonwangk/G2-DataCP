# MCAP与厂商原生输入的转换契约

运行、安装、输出、并行和恢复统一见[README.md](README.md)。本页只说明两种输入如何变为同一份训练表，便于查明每个字段来自哪里。

## 输入和调用

| 输入 | 读取模块 | 主要来源 |
|---|---|---|
| 自采`.mcap`文件 | `mcap_input.py` | 原始protobuf关节话题、`/raw/camera/*`中的Aorta相机包 |
| 厂商`full_*`目录 | `factory_input.py` | `record/-hal-joint_cmd.pbdat`等逐话题文件、`camera/<相机>/<相机>.txt/.h265` |

两种输入均经`convert_r2.py` → `raw_convert.py`写入；JSON入口为`run_platform.sh` → `platform_convert.py`。指定`--input-format mcap`或`factory`可限制发现范围，`auto`按路径自动发现。原生主链不读取repaired/aligned H5，也不把bundle聚合值替代原始逐话题动作。

## 24维名称及动作/实测分离

由`recording_contract.py`定义固定顺序：腰5、头3、左臂7、左夹爪、右臂7、右夹爪。protobuf中的值按关节名读取，再组织成这个顺序，不依赖输入数组原始排列。两个夹爪的规范名为`idx31_gripper_l_inner_joint1`、`idx71_gripper_r_inner_joint1`。

`action`读取`/hal/joint_cmd.position`和`/wbc/left_ee_command`、`/wbc/right_ee_command`的指令位置；`observation.state`读取`/hal/joint_state.motor_position`及左右`/hal/*_ee_data`实测位置。HAL指令是原采集链的已有输出，不等同于遥操上游目标，也不代表直接重放完整HAL报文。

24维数值保留float64及原单位，缺失值保存为Arrow NULL。缺少名称对应的位置不补0，不用实测反馈替代动作，不通过夹爪方向变换改写指令。`action_valid`、`observation.state_valid`记录各维有限数有效性；它们不删除或修复原值。

## 时间与原样本关联

每张真实头部RGB帧对应一行训练表：

- 头部相机真实源时间为主轴；protobuf时间取报文Header的原始PTP纳秒。
- 状态及腕部图像选择时间上最近的原始样本；动作保持该时刻之前最近一条原始指令。
- 开头尚无已有指令时保留NULL，不复制未来动作；不会插值、删掉真实头帧或补造缺失视频帧。
- `source_timestamp_ns`保留原始头部PTP；LeRobot的`timestamp=index/30`和MP4显示时间是格式索引，不替代真实源时间。
- 时间倒序会报错，不自动重排、校时或修复。`provenance/frames.jsonl`保存被选原帧/话题/索引/时间及偏移供追溯。

MCAP相机包中使用源相机时间，不使用辅助图像信封的本机墙钟去对齐机器人PTP。训练表只按真实头部帧取样；原文件回放读取全部指令事件，所以高频原始事件不会全部出现在视频主轴训练表中。

## 当前MP4图像契约

MCAP读取器可取得原JPEG字节，但当前MP4输出会把已选原帧解码成RGB再无损编码，保留解码像素，不宣称逐字节保存JPEG。原生H.265在帧映射完整一一对应且无B帧时直接封装HEVC；其余情况同样保存无损RGB MP4。图像保持原分辨率，不缩到256×256，不增加有损转码，也不再把PNG写进Parquet。

至少需要真实头部RGB来构造训练表，其余相机只按源实际可用字段导出。不同记录的相机集合、分辨率或schema不一致时，已有数据集不会通过缩放或填图强行兼容，应使用另一输出目录。

## 完成、训练与回放的边界

转换完成记录在`provenance/SUCCESS`和结果JSON中；是否具备完整有限训练字段另看`provenance/conversion.json`的`training_ready`。标准固定版LeRobot/OpenPI loader可能在含NULL行报错，转换器不替用户补值、删帧或决定训练空值策略。

回放的名称映射、原时间轴和NULL读取由[原回放说明](../replay/README.md)维护；新副本只对发送目标做限幅与平滑，见[独立副本说明](../replay/README_clamped_smooth.md)。转换数据本身不会应用回放限幅、反馈补值或自动姿态恢复。
