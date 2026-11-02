# 使用说明
# 用途：内部模块，按原Header PTP逐事件读取原生pbdat或MCAP控制指令。
# 不独立启动；由native_episode、studio/robot_replay及两个回放入口复用。
# 使用入口：replay/replay.sh --native-recording /data/record/full_xxx --dry-run。
# 保持24维名称映射、原目标和NULL；反馈只记录诊断，不替代缺失动作。
# 多文件使用说明：replay/README.md；转换读取模块见conversion/README.md。
#
"""直接读取两类原始文件的指令事件，保留事件原时间、原值与空值。

原文件回放不先变成30 Hz训练表。只在没有新事件的期间保持已下发
目标；每个原始指令事件都保留，缺失维度不从反馈补齐。
"""
from bisect import bisect_left
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent/'conversion'))
from recording_contract import NAMES, STATE, ACTION, nearest
from trajectory import PERIOD, prepare_episode


def load_source(source):
    path = Path(source).expanduser().resolve()
    if path.suffix == '.mcap':
        from mcap_input import read_recording
        kind = 'mcap'
    else:
        from factory_input import read_recording
        kind = 'factory'
    samples, cameras = read_recording(path)
    stamps = [r.time_ns for rows in [*samples.values(), *cameras.values()] for r in rows]
    if not stamps: raise ValueError('原始记录没有带时间戳的数据')
    origin, end = min(stamps), max(stamps)
    events = sorted((r.time_ns, k, r.index, r) for k in ACTION for r in samples[k])
    # 即使一条指令都没有，也保留完整原时间轴，以空目标运行，不生成位置。
    actions, states, times, event_sources = [], [], [], []
    current = {n: None for n in NAMES}
    state_times = {t: [r.time_ns for r in samples[t]] for t in STATE}
    def add(stamp, source_topic=None, source_index=None):
        values = {}
        for topic in STATE:
            if samples[topic]: values.update(samples[topic][nearest(state_times[topic], stamp)].values)
        times.append((stamp-origin)/1e9); actions.append([current[n] for n in NAMES])
        states.append([values.get(n) for n in NAMES])
        event_sources.append({'topic': source_topic, 'index': source_index, 'source_timestamp_ns': stamp})
    if not events or events[0][0] > origin: add(origin)
    for stamp, topic, index, row in events:
        current.update(row.values); add(stamp, topic, index)
    if (not times) or times[-1] < (end-origin)/1e9: add(end)
    return prepare_episode({'root': str(path), 'path': str(path), 'episode': 0, 'fps': len(actions)/max((end-origin)/1e9,PERIOD),
        'duration_s': (end-origin)/1e9+PERIOD, 'names': list(NAMES), 'times': times,
        'actions': actions, 'recorded_states': states, 'action_sources': ['command']*len(actions),
        'event_sources': event_sources,
        'available_names': [n for n in NAMES if any(n in r.values for rows in samples.values() for r in rows)], 'source_field': 'original command topics',
        'timeline': 'original command Header PTP; full original recording duration', 'input_format': kind})
