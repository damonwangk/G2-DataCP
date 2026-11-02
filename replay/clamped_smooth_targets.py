# 使用说明
# 用途：限幅平滑副本的纯Python内部模块，读取按名限位、计算发送目标。
# 不独立启动；入口是replay_clamped_smooth.sh或对应主程序。
# 默认关节3rad/s、夹爪0.1m/s；SDK接受后才提交历史，NULL仍不发送。
# 不导入SDK；离线复验：在replay目录运行 python -m unittest -v test_clamped_smooth。
# 多文件使用说明：replay/README_clamped_smooth.md。
#
"""独立副本的发送目标限幅/限速器；纯Python，不导入SDK，不改写源数据。"""
import hashlib
import json
import math
from pathlib import Path
import struct

from trajectory import PERIOD, GRIPPERS, canonical_name


def float32(value):
    """模拟GDK的double→float校验；极大有限数转为无穷供比较。"""
    try:
        return struct.unpack('f', struct.pack('f', value))[0]
    except OverflowError:
        return math.copysign(math.inf, value)


def load_limits(path, names):
    """读取当前安装配置，以名称选取关节及dahuan双夹爪范围。"""
    path = Path(path).expanduser().resolve()
    content = path.read_bytes()
    config = json.loads(content)
    limits = {n: (v['min'], v['max']) for n, v in config.items()
              if isinstance(v, dict) and 'min' in v and 'max' in v}
    tools = config.get('end_effector', {}).get('dahuan', {})
    for side in ('left_tool', 'right_tool'):
        for item in tools.get(side, []):
            if item['name'] in limits:
                raise ValueError('限位配置名称重复：' + item['name'])
            limits[item['name']] = (item['min'], item['max'])
    selected = {}
    for original in names:
        name = canonical_name(original)
        if name not in limits:
            raise ValueError('当前GDK配置没有关节限位：' + name)
        lo, hi = map(float, limits[name])
        if not all(math.isfinite(v) and math.isfinite(float32(v)) for v in (lo, hi)) or lo > hi:
            raise ValueError('无效GDK限位：' + name)
        selected[name] = (lo, hi)
    return selected, {'path': str(path), 'sha256': hashlib.sha256(content).hexdigest(),
                      'limits_by_name': selected, 'gripper_type': 'dahuan'}


class SmoothTargets:
    """逐轴限速，SDK成功后才提交历史；NULL只省略本次发送，不生成目标。"""
    def __init__(self, names, limits, joint_speed=3., gripper_speed=.1):
        self.names = [canonical_name(n) for n in names]
        if len(set(self.names)) != len(self.names):
            raise ValueError('映射后的关节名称重复')
        if any(not math.isfinite(v) or v <= 0 for v in (joint_speed, gripper_speed)):
            raise ValueError('平滑速度必须是有限正数')
        self.limits = [limits[n] for n in self.names]
        self.speeds = [gripper_speed if n in GRIPPERS else joint_speed for n in self.names]
        self.last = [None] * len(names)
        self.last_tick = None

    def propose(self, raw, now, paused=False):
        if len(raw) != len(self.names):
            raise ValueError('目标维数与名称不一致')
        # 迟到、SDK拒绝、空值或暂停都不能积攒一个大步长的预算。
        dt = 0. if self.last_tick is None or paused else min(PERIOD, max(0., now-self.last_tick))
        self.last_tick = now
        desired, target, clipped, smoothed = [], [], [], []
        for i, (name, q, bounds) in enumerate(zip(self.names, raw, self.limits)):
            if q is None:
                desired.append(None); target.append(None)
                continue
            if not math.isfinite(q):
                raise ValueError('目标不是有限数：' + name)
            lo, hi = bounds
            d = max(lo, min(hi, q))
            desired.append(d)
            if d != q:
                clipped.append(name)
            previous = self.last[i]
            if paused and previous is None:
                # 暂停不能引入一个从未成功发送的新轴。
                target.append(None)
                smoothed.append(name)
                continue
            if previous is None:
                # 首个非空目标仅限幅；不读取反馈、不自动恢复首姿态。
                value = d
            else:
                step = self.speeds[i] * dt
                value = previous + max(-step, min(step, d-previous))
                if abs(d-previous) <= step:
                    value = d
            # 浮点运算后再次硬限幅，同时检查GDK实际使用的float32边界。
            value = max(lo, min(hi, value))
            if not float32(lo) <= float32(value) <= float32(hi):
                raise ValueError('平滑目标未通过float32范围校验：' + name)
            target.append(value)
            if value != d:
                smoothed.append(name)
        return {'raw_target': list(raw), 'clamped_target': desired, 'target': target,
                'clamped_names': clipped, 'smoothed_names': smoothed, 'smoothing_dt_s': dt}

    def commit(self, target):
        # 调用方只在SDK接受整包后提交；NULL不覆盖该轴上次成功值。
        for i, q in enumerate(target):
            if q is not None:
                self.last[i] = q

    def residual(self, raw):
        return [None if q is None or old is None else max(lo, min(hi, q))-old
                for q, old, (lo, hi) in zip(raw, self.last, self.limits)]


def validate_episode(episode):
    """在任何SDK导入前检查所有原目标；非法数不补0、不换成反馈。"""
    for frame, row in enumerate(episode['command_targets']):
        if len(row) != len(episode['names']):
            raise ValueError('目标维数不一致：frame ' + str(frame))
        if any(q is not None and not math.isfinite(q) for q in row):
            raise ValueError('目标含NaN/Inf：frame ' + str(frame))


def offline_steps(episode, limits, joint_speed=3., gripper_speed=.1):
    """理想时钟复现原有逐事件/100Hz保持调度；不初始化SDK、不使用反馈。"""
    from trajectory import sample
    smooth = SmoothTargets(episode['names'], limits, joint_speed, gripper_speed)
    now, next_frame = 0., 0
    while True:
        if next_frame < len(episode['times']) and episode['times'][next_frame] <= now+1e-12:
            frame = next_frame; next_frame += 1
            raw = episode['command_targets'][frame]
        else:
            frame, raw = sample(episode, now)
        change = smooth.propose(raw, now)
        smooth.commit(change['target'])
        yield {'elapsed_s': now, 'frame_index': frame, **change}
        if now >= episode['duration_s'] and next_frame >= len(episode['times']):
            break
        delay = PERIOD
        if next_frame < len(episode['times']):
            delay = min(delay, max(0., episode['times'][next_frame]-now))
        now = min(episode['duration_s'], now+delay)


def offline_summary(episode, limits, joint_speed=3., gripper_speed=.1):
    """遍历全轨迹，统计限幅/平滑及截止残差；源帧与时间完全不写回。"""
    count, clipped, smoothed = 0, 0, 0
    last = None
    for row in offline_steps(episode, limits, joint_speed, gripper_speed):
        count += 1
        clipped += bool(row['clamped_names'])
        smoothed += bool(row['smoothed_names'])
        last = row
    return {'offline_steps': count, 'clamped_steps': clipped, 'smoothed_steps': smoothed,
            'final_target_residual': [None if d is None else d-q
                                      for d, q in zip(last['clamped_target'], last['target'])],
            'physical_tracking_verified': False}
