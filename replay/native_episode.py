# 使用说明
# 用途：原生full的只读入口，内部复用source_episode.load_source读取指令。
# 使用：在项目根目录运行 conversion/.venv/bin/python replay/native_episode.py --source /data/record/full_xxx。
# 输出：原时间、24轴名称、首帧与NULL统计JSON；不加载SDK、不发送动作。
# 多文件使用说明：replay/README.md；实际动作使用replay.sh --native-recording。
#
"""原生离线目录的直接指令回放入口；不读取 bundle，不生成中间目标。"""
import argparse
import json
from source_episode import load_source


def load_native(source): return load_source(source)


def summary(source):
    ep = load_native(source)
    return {'source': ep['root'], 'frames': len(ep['times']), 'duration_s': ep['duration_s'], 'fps': ep['fps'],
        'names': ep['names'], 'first_positions': ep['actions'][0], 'source_field': ep['source_field'],
        'first_sdk_command': ep['command_targets'][0], 'source_counts': ep['source_counts'],
        'null_action_values': sum(v is None for row in ep['actions'] for v in row),
        'target_hz': 100, 'input_format': 'factory', 'sampling': 'all original command events; 100 Hz hold when idle; no interpolation'}

if __name__ == '__main__':
    p=argparse.ArgumentParser(); p.add_argument('--source',required=True)
    print(json.dumps(summary(p.parse_args().source),ensure_ascii=False))
