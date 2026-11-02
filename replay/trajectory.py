# 使用说明
# 用途：内部共用模块，名称别名映射、原目标保持和反馈诊断统计。
# 不独立启动；由原回放、限幅平滑副本和MCAP执行器导入。
# 原目标不改写；夹爪反向换算仅用于期望反馈诊断，不改发送动作。
# 多文件使用说明：replay/README.md；本模块不实现限幅平滑。
#
"""回放共用的名称映射、原指令保持与诊断统计；不插值、不补空值。"""
from bisect import bisect_right
from collections import Counter
import json
import math

PERIOD = .01
GRIPPERS = {'idx31_gripper_l_inner_joint1': 'left_effector',
            'idx71_gripper_r_inner_joint1': 'right_effector'}
ALIASES = {'left_gripper_position': 'idx31_gripper_l_inner_joint1',
           'right_gripper_position': 'idx71_gripper_r_inner_joint1'}
STROKE = .025  # Installed G2 dahuan / GDK 3.1.1 feedback-to-command convention.
COMMAND_TOPICS = {'joint': '/hal/joint_cmd', 'left_effector': '/wbc/left_ee_command',
                  'right_effector': '/wbc/right_ee_command'}
FEEDBACK_TOPICS = {'/hal/joint_state', '/hal/left_ee_data', '/hal/right_ee_data'}


def canonical_name(name):
    return ALIASES.get(name, name)


def gripper_inverse(value):
    """仅用于期望反馈坐标的诊断对照，不作用于发送的原始指令。"""
    return STROKE - value


def source_kind(detail, name):
    if isinstance(detail, str):
        source = detail
    else:
        part = GRIPPERS.get(name, 'joint')
        source = detail.get(name, detail.get(part, detail.get(COMMAND_TOPICS[part], 'command')))
    return ('feedback' if source in FEEDBACK_TOPICS or source in ('feedback', 'state feedback')
            or str(source).startswith('state__') else 'command')


def load_sources(root, episode, frames):
    """Factory and MCAP converters both retain source fields in this sidecar."""
    sources = [{} for _ in range(frames)]
    covered = set()
    path = root / 'provenance/frames.jsonl'
    if path.exists():
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                if not line.strip():
                    continue
                row = json.loads(line)
                index = row.get('frame_index')
                if row.get('episode_index') == episode and isinstance(index, int) and 0 <= index < frames:
                    sources[index] = row.get('action_sources', {})
                    covered.add(index)
    return sources, len(covered)


def prepare_episode(episode):
    if 'command_targets' in episode:
        return episode
    out = dict(episode)
    names = [canonical_name(n) for n in episode['names']]
    if len(set(names)) != len(names):
        raise ValueError('映射后的 GDK 关节名称重复')
    sources = episode.get('action_sources')
    if sources is None:
        default = 'feedback' if episode.get('source_field') == 'state feedback' else 'command'
        sources = [default] * len(episode['actions'])
    kinds = [[source_kind(detail, n) for n in names] for detail in sources]
    if len(kinds) != len(episode['actions']):
        raise ValueError('动作来源行数与轨迹不一致')
    # action 是原始控制指令；不换坐标、不裁剪、不补空值。
    targets = [list(row) for row in episode['actions']]
    out.update(names=names, source_names=list(episode['names']), command_targets=targets, source_kinds=kinds,
               source_counts=dict(Counter(kind for row in kinds for kind in row)))
    return out


def expected_feedback(names, target):
    return [None if q is None else gripper_inverse(q) if n in GRIPPERS else q for n, q in zip(names, target)]




def sample(episode, elapsed):
    """保持上一条原始目标；空值仍为空，不计算任何中间位置。"""
    episode = prepare_episode(episode)
    times = episode['times']
    index = max(0, min(len(times)-1, bisect_right(times, elapsed)-1))
    return index, list(episode['command_targets'][index])




class Tracking:
    """Informational physical-coordinate errors, never a motion gate."""
    def __init__(self, names):
        self.names = names
        self.squared = [0.] * len(names)
        self.maximum = [0.] * len(names)
        self.counts = [0] * len(names)

    def add(self, target, observed):
        if observed is None:
            return
        desired = expected_feedback(self.names, target)
        for i, (q, actual) in enumerate(zip(desired, observed)):
            if q is not None and actual is not None and math.isfinite(actual):
                error = actual-q
                self.squared[i] += error*error
                self.maximum[i] = max(self.maximum[i], abs(error))
                self.counts[i] += 1

    def summary(self):
        return {'names': self.names, 'samples_per_axis': self.counts,
                'rmse': [math.sqrt(s/n) if n else None for s, n in zip(self.squared, self.counts)],
                'max_abs_error': [v if n else None for v, n in zip(self.maximum, self.counts)],
                'coordinates': 'physical joint/gripper feedback; GDK gripper command direction adapted'}
