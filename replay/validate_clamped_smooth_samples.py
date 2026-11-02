#!/usr/bin/env python3
# 使用说明
# 用途：单文件离线验收入口，完整读取两条指定full及对应LeRobot验证限幅平滑。
# 使用：在replay目录运行 ../conversion/.venv/bin/python validate_clamped_smooth_samples.py --output /tmp/clamped_samples.json。
# 可选：--record-root、--dataset-root、--gdk-limits、--joint-speed、--gripper-speed。
# 默认数据在/data/record与/data/r2_datacp/datasets/robot_full_spotcheck_20261102。
# 只生成验收JSON，不导入或初始化真实SDK，不修改数据或执行动作。
# 关联说明：README_clamped_smooth.md；统计分别保留double/float32边界语义。
#
"""纯读取真实full/LeRobot的离线验收；只模拟发送目标，不导入或初始化真实SDK。"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from clamped_smooth_targets import float32, load_limits, offline_steps, validate_episode
from lerobot_replay_clamped_smooth import load_episode
from source_episode import load_source
from recording_contract import NAMES
from trajectory import GRIPPERS, PERIOD, canonical_name


SAMPLES = ('full_20260314010540', 'full_20260314012619')


def episode_hash(episode):
    """对原始位置/时间和共用loader生成的原目标作可复核摘要。"""
    value = {key: episode[key] for key in ('actions', 'times', 'command_targets')}
    return hashlib.sha256(json.dumps(value, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def raw_ranges(episode, limits, sdk_float32=False):
    """按连续源帧列出任一轴超限区间；末帧不向原时间轴之后外推。"""
    intervals, current, bad_frames = [], None, 0
    for index, (stamp, row) in enumerate(zip(episode['times'], episode['command_targets'])):
        bad = []
        for name, value in zip(episode['names'], row):
            if value is None:
                continue
            lo, hi = limits[name]
            compared = (float32(lo), float32(value), float32(hi)) if sdk_float32 else (lo, value, hi)
            if not compared[0] <= compared[1] <= compared[2]:
                bad.append(name)
        if bad:
            bad_frames += 1
            if current is None:
                current = {'first_frame': index, 'start_s': stamp, 'frames': 0, 'names': set()}
            current.update(last_frame=index, last_source_s=stamp)
            current['frames'] += 1
            current['names'].update(bad)
        elif current is not None:
            current['end_s'] = stamp
            current['names'] = sorted(current['names'])
            intervals.append(current)
            current = None
    if current is not None:
        current['end_s'] = episode['times'][-1]
        current['names'] = sorted(current['names'])
        intervals.append(current)
    return {'frames': bad_frames, 'intervals': intervals,
            'sample_hold_duration_s': sum(row['end_s']-row['start_s'] for row in intervals)}


def validate_one(episode, limits, joint_speed, gripper_speed):
    validate_episode(episode)
    names = [canonical_name(name) for name in episode['names']]
    assert episode['names'] == names, 'loader应先完成GDK名称映射'
    assert len(names) == 24 and len(set(names)) == 24 and set(names) == set(NAMES), '24维名称集合不一致'
    assert len(episode['times']) == len(episode['command_targets']) == len(episode['actions'])
    assert all(b >= a for a, b in zip(episode['times'], episode['times'][1:])), '时间倒序'
    original_hash = episode_hash(episode)
    seen, last, previous_raw = set(), [None]*len(names), [None]*len(names)
    max_steps, max_offsets = [0.]*len(names), [0.]*len(names)
    steps = clamped = smoothed = null_values = 0
    windows, current, last_row, previous_time = [], None, None, None
    for row in offline_steps(episode, limits, joint_speed, gripper_speed):
        steps += 1
        frame = row['frame_index']
        seen.add(frame)
        assert row['raw_target'] == episode['command_targets'][frame], '离线调度改变源目标'
        stamp, dt = row['elapsed_s'], row['smoothing_dt_s']
        expected_dt = 0. if previous_time is None else min(PERIOD, max(0., stamp-previous_time))
        assert abs(dt-expected_dt) < 1e-12, '平滑dt不符合逐事件/100Hz调度'
        changes, raw_changes = [], []
        for axis, (name, raw, desired, target) in enumerate(zip(names, row['raw_target'], row['clamped_target'], row['target'])):
            assert (raw is None) == (desired is None) == (target is None), 'NULL被填入或丢失'
            if raw is None:
                null_values += 1
                continue
            lo, hi = limits[name]
            assert desired == max(lo, min(hi, raw)), '按名称限幅结果不一致'
            assert lo <= target <= hi, '发送double越界'
            assert float32(lo) <= float32(target) <= float32(hi), '发送float32越界'
            max_offsets[axis] = max(max_offsets[axis], abs(target-raw))
            if last[axis] is not None:
                change = abs(target-last[axis])
                speed = gripper_speed if name in GRIPPERS else joint_speed
                assert change <= speed*dt+1e-12, '发送目标变化超过逐轴限速预算'
                max_steps[axis] = max(max_steps[axis], change)
                changes.append(change)
            if previous_raw[axis] is not None:
                raw_changes.append(abs(raw-previous_raw[axis]))
            # NULL不清空历史；再次出现的数值仍相对该轴上次接受值检查。
            last[axis] = target
            previous_raw[axis] = raw
        clamped += bool(row['clamped_names'])
        smoothed += bool(row['smoothed_names'])
        if row['clamped_names']:
            if current is None:
                current = {'entry_s': stamp, 'entry_frame': frame, 'clamped_steps': 0,
                           'entry_max_output_step': max(changes, default=0.),
                           'max_output_step_while_clamped': 0., 'names': set()}
            current['clamped_steps'] += 1
            current['names'].update(row['clamped_names'])
            current['last_clamped_s'] = stamp
            current['max_output_step_while_clamped'] = max(current['max_output_step_while_clamped'], max(changes, default=0.))
        elif current is not None:
            current.update(exit_s=stamp, exit_frame=frame,
                           exit_max_output_step=max(changes, default=0.),
                           exit_max_raw_step=max(raw_changes, default=0.),
                           exit_smoothed_names=row['smoothed_names'],
                           exit_max_residual=max((abs(d-q) for d, q in zip(row['clamped_target'], row['target']) if d is not None), default=0.))
            current['names'] = sorted(current['names'])
            windows.append(current)
            current = None
        previous_time, last_row = stamp, row
    if current is not None:
        current['exit_s'] = None
        current['names'] = sorted(current['names'])
        windows.append(current)
    assert seen == set(range(len(episode['times']))), '存在未读取的源事件帧'
    assert last_row['elapsed_s'] == episode['duration_s'], '原始回放截止时间改变'
    after_hash = episode_hash(episode)
    assert after_hash == original_hash, '读取/平滑改变了原始episode'
    assert 'agibot_gdk' not in sys.modules, '纯读取验收不应导入真实SDK'
    residual = [None if value is None else value-target
                for value, target in zip(last_row['clamped_target'], last_row['target'])]
    return {'source': episode['path'], 'input_format': episode.get('input_format', 'lerobot'),
            'frames': len(episode['times']), 'duration_s': episode['duration_s'],
            'names': names, 'raw_nominal_outside': raw_ranges(episode, limits),
            'raw_sdk_float32_outside': raw_ranges(episode, limits, True),
            'offline_steps': steps, 'clamped_steps': clamped, 'smoothed_steps': smoothed,
            'null_values_preserved': null_values, 'all_source_frames_seen': True,
            'numeric_double_and_float32_in_range': True, 'per_axis_step_budget_passed': True,
            'max_output_step_by_name': dict(zip(names, max_steps)),
            'max_abs_raw_output_difference_by_name': dict(zip(names, max_offsets)),
            'final_clamped_target_residual_by_name': dict(zip(names, residual)),
            'clamp_entry_sustained_exit_windows': windows,
            'episode_hash_before': original_hash, 'episode_hash_after': after_hash,
            'source_arrays_unchanged': True, 'physical_tracking_verified': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record-root', default='/data/record')
    parser.add_argument('--dataset-root', default='/data/r2_datacp/datasets/robot_full_spotcheck_20261102')
    parser.add_argument('--gdk-limits', default='/home/agi/app/gdk/config/mc_impl_config.json')
    parser.add_argument('--output', required=True)
    parser.add_argument('--joint-speed', type=float, default=3.)
    parser.add_argument('--gripper-speed', type=float, default=.1)
    args = parser.parse_args(argv)
    assert 'agibot_gdk' not in sys.modules
    limits, metadata = load_limits(args.gdk_limits, NAMES)
    results = []
    for sample in SAMPLES:
        # 原生full与LeRobot分别完整走原有loader；不生成转换文件或修改原始数据。
        for kind in ('full', 'lerobot'):
            episode = (load_source(Path(args.record_root)/sample) if kind == 'full'
                       else load_episode(Path(args.dataset_root)/sample, 0))
            result = validate_one(episode, limits, args.joint_speed, args.gripper_speed)
            result.update(sample=sample, input=kind)
            results.append(result)
            print(json.dumps({'sample': sample, 'input': kind, 'frames': result['frames'],
                              'offline_steps': result['offline_steps'], 'passed': True}), flush=True)
    report = {'purpose': 'offline pure-read target validation; no real SDK or motion',
              'gdk_limits': metadata, 'joint_speed_rad_s': args.joint_speed,
              'gripper_speed_m_s': args.gripper_speed, 'results': results,
              'passed': True, 'real_sdk_imported': 'agibot_gdk' in sys.modules,
              'control_commands_sent': 0, 'physical_tracking_verified': False}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps({'passed': True, 'output': str(output), 'cases': len(results)}), flush=True)


if __name__ == '__main__':
    main()
