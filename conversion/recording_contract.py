# 使用说明
# 用途：定义24维名称、原始PTP提取及真实头帧主轴的样本关联规则。
# 使用：由两类输入读取器与回放读取器导入，不独立启动。
# 多文件使用说明：PLATFORM_MCAP.md 的名称、NULL和时间规则。
#
"""两种原始记录共用的24维名称、原始时间提取和视频主轴取样逻辑。

这里只选择已有样本：状态/腕部图像取最近样本，动作保持最近一条
已经下发的原始指令。既不计算中间位置，也不以反馈补动作。
"""
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass

import numpy as np

BODY_NAMES = [f'idx{i:02d}_body_joint{i}' for i in range(1, 6)]
HEAD_NAMES = [f'idx{10+i}_head_joint{i}' for i in range(1, 4)]
ARM_NAMES = [f'idx{20+i}_arm_l_joint{i}' for i in range(1, 8)] + [f'idx{60+i}_arm_r_joint{i}' for i in range(1, 8)]
JOINT_NAMES = BODY_NAMES + HEAD_NAMES + ARM_NAMES
GRIPS = ['idx31_gripper_l_inner_joint1', 'idx71_gripper_r_inner_joint1']
NAMES = BODY_NAMES + HEAD_NAMES + ARM_NAMES[:7] + GRIPS[:1] + ARM_NAMES[7:] + GRIPS[1:]
CAMERAS = ('head_color', 'hand_left_color', 'hand_right_color')
STATE = ('/hal/joint_state', '/hal/left_ee_data', '/hal/right_ee_data')
ACTION = ('/hal/joint_cmd', '/wbc/left_ee_command', '/wbc/right_ee_command')
TYPES = {STATE[0]: 'genie_msgs.msg.pb.JointState', ACTION[0]: 'genie_msgs.msg.pb.JointCommand',
         **{t: 'genie_msgs.msg.pb.EndState' for t in STATE[1:]},
         **{t: 'genie_msgs.msg.pb.JointCommand' for t in ACTION[1:]}}


@dataclass
class Sample:
    index: int
    time_ns: int
    record_ns: int
    publish_ns: int
    values: dict | None = None


def source_time(value):
    """使用原报文 Header 的 PTP 时间；不校时，不拿本机墙钟替代。"""
    stamp = value.header.stamp
    result = int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)
    if result <= 0:
        raise ValueError('原报文没有有效的 Header PTP 时间戳')
    return result


def positions(topic, value):
    """按名字读取原始 float64；motor_position 才是关节实测位置。"""
    names = list(value.name)
    if topic == STATE[0]:
        values = list(value.motor_position)
    elif topic in STATE[1:]:
        values = [v.position if v.HasField('position') else None for v in value.end_state]
    else:
        values = list(value.position)
    if len(values) > len(names) or len(set(names)) != len(names):
        raise ValueError(f'{topic}: 关节名与位置数组缺失、重复或长度不同')
    result = dict(zip(names, values))
    expected = JOINT_NAMES if topic in (STATE[0], ACTION[0]) else [GRIPS[0] if 'left' in topic else GRIPS[1]]
    # 缺少的位置保留 None；不补零、不从其他话题推导。
    return {n: result.get(n) for n in expected}


def nearest(times, stamp):
    at = bisect_left(times, stamp)
    return min((i for i in (at-1, at) if 0 <= i < len(times)),
               key=lambda i: (abs(times[i]-stamp), i))


def head_plan(samples, cameras, source, input_format, progress=None):
    """一张真实头部图像对应一行；停住时保持已知指令，不补视频帧。

只有开头尚无历史指令时才产生起始缺失。trim 必须由调用者明确选用；
它只排除未知前缀并逐帧列出，不改源文件、不把状态当成指令。
"""
    if not cameras.get('head_color'):
        raise ValueError('没有头部 RGB 原始视频帧，不能生成视频主轴训练表')
    samples = {t: samples.get(t, []) for t in (*STATE, *ACTION)}
    all_rows = {**samples, **cameras}
    times = {key: [r.time_ns for r in rows] for key, rows in all_rows.items()}
    for key, stamps in times.items():
        if any(b < a for a, b in zip(stamps, stamps[1:])):
            raise ValueError(f'{key}: 原始时间戳倒序；请检查源文件，不自动重排/修复')
    rows = []
    for index, head in enumerate(cameras['head_color']):
        stamp = head.time_ns
        if progress:
            progress.update('按真实头部帧关联原始样本', fraction=.30+.05*index/len(cameras['head_color']))
        command_indices = {t: bisect_right(times[t], stamp)-1 for t in ACTION}
        chosen = {t: samples[t][nearest(times[t], stamp)] if samples[t] else None for t in STATE}
        chosen.update({t: samples[t][i] if i >= 0 else None for t, i in command_indices.items()})
        def vector(topics):
            mapping = {n: q for t in topics if chosen[t] is not None for n, q in chosen[t].values.items()}
            return [mapping.get(n) for n in NAMES]
        camera_rows = {}
        for cam, recorded in cameras.items():
            row = head if cam == 'head_color' else recorded[nearest(times[cam], stamp)]
            camera_rows[cam] = {'index': row.index, 'timestamp_ns': row.time_ns, 'offset_ns': row.time_ns-stamp}
        state, action = vector(STATE), vector(ACTION)
        rows.append({'head_frame_index': head.index, 'source_timestamp_ns': stamp,
                     'record_timestamp_ns': head.record_ns, 'state': state, 'action': action,
                     'state_valid': [v is not None and bool(np.isfinite(v)) for v in state],
                     'action_valid': [v is not None and bool(np.isfinite(v)) for v in action],
                     'action_sources': {t: t for t in ACTION}, 'cameras': camera_rows,
                     'source_samples': {t: None if r is None else {'source_topic': t, 'index': r.index,
                         'source_timestamp_ns': r.time_ns, 'record_timestamp_ns': r.record_ns,
                         'publish_time_ns': r.publish_ns, 'offset_ns': r.time_ns-stamp} for t, r in chosen.items()}})
    missing_action = sum(v is None for row in rows for v in row['action'])
    missing_state = sum(v is None for row in rows for v in row['state'])
    finite = all(all(row['action_valid']) and all(row['state_valid']) for row in rows)
    return [rows], {'source': str(source), 'input_format': input_format, 'names': NAMES, 'numeric_dtype': 'float64',
        'clock': 'original message Header / camera source PTP nanoseconds',
        'mapping': 'one actual head RGB frame per row; nearest original states/wrist images; latest prior original commands held; no interpolation',
        'source_head_frames': len(cameras['head_color']), 'export_frames': len(rows), 'segment_lengths': [len(rows)],
        'null_action_values': missing_action, 'null_state_values': missing_state,
        'null_action_frames': sum(any(v is None for v in row['action']) for row in rows),
        'training_ready': finite,
        'available_cameras': list(cameras), 'camera_index_counts': {cam: len(v) for cam, v in cameras.items()},
        'messages': {t: len(v) for t, v in samples.items()},
        'action_sources': dict(Counter(t for _ in rows for t in ACTION))}
