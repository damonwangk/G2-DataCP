#!/usr/bin/env python3
"""Read LeRobot v2.1 metadata without loading or connecting the robot SDK."""
import argparse
import json
from pathlib import Path


def describe(dataset, episode=None):
    root = Path(dataset).expanduser().resolve()
    info = json.loads((root / 'meta/info.json').read_text(encoding='utf-8'))
    episodes = []
    records = root / 'meta/episodes.jsonl'
    if records.exists():
        rows = [json.loads(line) for line in records.read_text(encoding='utf-8').splitlines() if line.strip()]
    else:
        rows = [{'episode_index': i} for i in range(int(info.get('total_episodes', 0)))]
    for row in rows:
        index = int(row['episode_index'])
        file = root / info['data_path'].format(episode_chunk=index // int(info['chunks_size']), episode_index=index)
        length = row.get('length')
        episodes.append({'episode': index, 'episode_index': index, 'frames': length,
                         'duration_s': length / float(info['fps']) if length is not None else None,
                         'tasks': row.get('tasks', []), 'file': str(file), 'exists': file.is_file()})
    result = {'dataset': str(root), 'name': root.name, 'format': info.get('codebase_version'),
              'fps': info['fps'], 'total_frames': info.get('total_frames'),
              'total_episodes': info.get('total_episodes'), 'episodes': episodes,
              'action_names': info.get('features', {}).get('action', {}).get('names', []),
              'state_names': info.get('features', {}).get('observation.state', {}).get('names', []),
              'video_features': [key for key, feature in info.get('features', {}).items() if feature.get('dtype') == 'video'],
              'target_control_hz': 100, 'motion_source': 'action',
              'sample_policy': 'LeRobot timestamp; latest original action held; no interpolation',
              'base_drive_replayed': False}
    if episode is not None:
        from lerobot_replay import load_episode
        data = load_episode(root, episode)
        result['selected_episode'] = {'episode': int(episode), 'frames': len(data['times']),
            'duration_s': data['duration_s'], 'last_frame_timestamp_s': data['times'][-1],
            'names': data['names'], 'first_action': data['actions'][0],
            'first_recorded_observation': data['recorded_states'][0]}
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--dataset', required=True)
    p.add_argument('--episode', type=int)
    a = p.parse_args()
    print(json.dumps(describe(a.dataset, a.episode), ensure_ascii=False, allow_nan=False))
