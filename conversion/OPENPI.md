# π0.5 官方 OpenPI 接入

本适配以 2026-10-29 核对的 [Physical-Intelligence/openpi](https://github.com/Physical-Intelligence/openpi) API 为依据。
其 [pyproject.toml](https://github.com/Physical-Intelligence/openpi/blob/main/pyproject.toml) 锁定
LeRobot `0cf864870cf29f4738d3ade893e6fd13fbd7cdb5`；该提交的存储格式为 **v2.1**。
不要单独升级到 LeRobot v3 后假定现有 OpenPI 数据加载器兼容。
本次只转换、验读数据；没有启动训练、下载模型、上传数据或执行机器人动作。

## 1. 安装到已有 OpenPI checkout

在官方支持的 Linux 环境按照其 README 安装 OpenPI，使用仓库自身的 `uv.lock`。
把本目录 `openpi_r2.py` 复制为 `src/openpi/policies/r2_policy.py`。
在 `src/openpi/training/config.py` 的 import 区域添加：

```python
import openpi.policies.r2_policy as r2_policy
```

在同一文件 `_CONFIGS` 列表中加入以下配置，`repo_id` 与转换时一致：

```python
TrainConfig(
    name="pi05_r2",
    model=pi0_config.Pi0Config(pi05=True, action_dim=32, action_horizon=50),
    data=SimpleDataConfig(
        repo_id="local/r2_test",
        base_config=DataConfig(
            repack_transforms=r2_policy.make_repack(),
            action_sequence_keys=("action",),
            prompt_from_task=True,
        ),
        data_transforms=r2_policy.R2TransformFactory(use_delta_joint_actions=False),
    ),
    weight_loader=weight_loaders.CheckpointWeightLoader(
        "gs://openpi-assets/checkpoints/pi05_base/params"
    ),
    batch_size=8,
    num_train_steps=30_000,
)
```

这里的 batch size 和训练步数是配置示例，需按训练设备和数据规模调整。
50 是官方模型默认 action horizon；以实际数据帧率换算时间长度。
LeRobot 中每一行只存一个 16 维 action，OpenPI 按 `delta_timestamps=[0/fps,...,49/fps]`
读取未来动作序列，不需要在转换时把每行扩成 50×16。
序列末尾由固定版本 LeRobot 在同一 episode 内重复最后动作；不得拼接下一条 episode。

## 2. 本地数据位置与训练前验读

OpenPI 官方 loader 按 `HF_LEROBOT_HOME / repo_id` 找本地数据。
例如输出目录是 `/data/lerobot/local/r2_test`，则在同一个 shell 中设置：

```bash
export HF_LEROBOT_HOME=/data/lerobot
```

以下是用户后续在 GPU 训练环境执行的命令，本次没有执行：

```bash
uv run scripts/compute_norm_stats.py --config-name pi05_r2
uv run scripts/train.py pi05_r2 --exp-name=r2_test_v1
```

必须使用本数据重新计算 normalization statistics，不能套用 LIBERO 或 ALOHA 的统计量。
`SimpleDataConfig` 继承官方 `ModelTransformFactory`，会在标准化之后把图像调整为 224×224，
并把原始 16 维 state/action 补到模型的 32 维；这些 padding 不是额外机器人关节。

## 3. 字段、动作语义和推理接口

转换输出包含：

- `observation.state`: float32[16]，顺序为左臂 7 关节、左夹爪、右臂 7 关节、右夹爪。
- `action`: float32[16]，顺序相同。默认保留转换报告声明的绝对目标和原始单位。
- `observation.images.head_color`、`hand_left_color`、`hand_right_color`: RGB。
- task metadata: 每条 episode 的任务文本，通过 `prompt_from_task=True` 交给模型。

图像分别映射到 `base_0_rgb`、`left_wrist_0_rgb`、`right_wrist_0_rgb`；三个相机均为真实输入，mask 为 True。
R2 自定义 state/action 不能直接套用官方 LIBERO 的 8 维 state、7 维 action。

默认 `use_delta_joint_actions=False`。如果训练方案明确需要相对于当前 state 的关节目标，
可以改成 True 并重新计算 normalization statistics；此时 mask 为
`[True]*7 + [False] + [True]*7 + [False]`，两个夹爪始终保持绝对值。
推理输出通过相反变换还原为绝对目标，最后只返回前 16 维。
这只是数据与模型接口适配，不等于已验证机器人控制器的单位、限位或执行语义。

推理时向模型提交重打包后的字段；RGB 可用 uint8 HWC，state 必须与训练时单位和顺序完全一致：

```python
observation = {
    "state": state_16,
    "images": {
        "head_color": head_rgb,
        "hand_left_color": left_rgb,
        "hand_right_color": right_rgb,
    },
    "prompt": "与采集任务一致的明确指令",
}
```

## 官方依据

- [官方自定义数据转换示例](https://github.com/Physical-Intelligence/openpi/blob/main/examples/libero/convert_libero_data_to_lerobot.py)：`LeRobotDataset.create` → `add_frame` → `save_episode`。
- [官方 DataConfig / SimpleDataConfig / 模型 transforms](https://github.com/Physical-Intelligence/openpi/blob/main/src/openpi/training/config.py)。
- [官方 LeRobot loader](https://github.com/Physical-Intelligence/openpi/blob/main/src/openpi/training/data_loader.py)：action sequence keys、fps 和 task prompt。
- [官方自定义 policy adapter 示例](https://github.com/Physical-Intelligence/openpi/blob/main/src/openpi/policies/libero_policy.py)：图像格式和模型相机键。
- [官方 Pi0Config](https://github.com/Physical-Intelligence/openpi/blob/main/src/openpi/models/pi0_config.py)：π0.5 开关、32 维和默认 horizon。
