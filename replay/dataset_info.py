#!/usr/bin/env python3
# 使用说明
# 用途：单文件只读查看LeRobot元数据、已提交episode及可选首帧，JSON输出。
# 使用：在项目根目录运行 conversion/.venv/bin/python replay/dataset_info.py --dataset /path/to/dataset。
# 选定episode：加 --episode 0；依赖PyArrow及本目录只读loader。
# 不加载SDK，不启动回放；未关闭Parquet/未提交事务不会列为可回放数据。
# 关联说明：replay/README.md；网页会通过studio/workflow.py调用describe。
#
"""Read LeRobot v2.1 metadata without loading or connecting the robot SDK."""
import argparse
import json
from pathlib import Path


def describe(dataset, episode=None):
    root = Path(dataset).expanduser().resolve()
    info = json.loads((root / 'meta/info.json').read_text(encoding='utf-8'))
    episodes = [];unavailable=[]
    ledger=root/'provenance/segments.jsonl'
    source_records=[json.loads(line) for line in ledger.read_text().splitlines() if line.strip()] if ledger.exists() else []
    origins={int(row['episode_index']):row.get('source') for row in source_records}
    pending=root/".r2-transaction/pending.json"
    pending_files=set()
    if pending.exists():
        pending_files={str((root/p).resolve()) for p in json.loads(pending.read_text()).get("generated_files",[])}
    records = root / 'meta/episodes.jsonl'
    if records.exists():
        rows = [json.loads(line) for line in records.read_text(encoding='utf-8').splitlines() if line.strip()]
    else:
        rows = [{'episode_index': i} for i in range(int(info.get('total_episodes', 0)))]
    for row in rows:
        index = int(row['episode_index'])
        file = root / info['data_path'].format(episode_chunk=index // int(info['chunks_size']), episode_index=index)
        length = row.get('length')
        # Parquet 写入中没有完整 footer；metadata 存在不等于 episode 已提交。
        reason='';duration=length/float(info['fps']) if length is not None else None
        if str(file.resolve()) in pending_files:reason='episode 尚在写入事务中'
        elif not file.is_file():reason='episode 文件尚未生成'
        else:
            try:
                import pyarrow.parquet as pq
                file_meta=pq.read_metadata(file);rows=file_meta.num_rows
                if length is not None and rows!=length:reason='Parquet 行数与 episode 元数据不一致'
                else:
                    length=rows
                    if 'source_timestamp_ns' in file_meta.schema.to_arrow_schema().names and rows:
                        times=pq.read_table(file,columns=['source_timestamp_ns'])['source_timestamp_ns']
                        duration=(times[-1].as_py()-times[0].as_py())/1e9+1/float(info['fps'])
            except (OSError,ValueError) as exc:reason='Parquet 尚未完整关闭：'+str(exc)
        if reason:
            unavailable.append({'episode_index':index,'file':str(file),'reason':reason});continue
        episodes.append({'episode': index, 'episode_index': index, 'frames': length,
                         'duration_s': duration, 'source':origins.get(index),
                         'tasks': row.get('tasks', []), 'file': str(file), 'exists': file.is_file()})
    result = {'dataset': str(root), 'name': root.name, 'format': info.get('codebase_version'),
              'unavailable_episodes':unavailable, 'source_records':source_records, 'fps': info['fps'], 'total_frames': info.get('total_frames'),
              'total_episodes': info.get('total_episodes'), 'episodes': episodes,
              'action_names': info.get('features', {}).get('action', {}).get('names', []),
              'state_names': info.get('features', {}).get('observation.state', {}).get('names', []),
              'video_features': [key for key, feature in info.get('features', {}).items() if feature.get('dtype') == 'video'],
              'target_control_hz': 100, 'motion_source': 'action',
              'sample_policy': 'original targets only; no interpolation/approach; nulls retained, null axes send no target',
              'base_drive_replayed': False}
    contract=root/'provenance/conversion.json'
    result['conversion_contract']=json.loads(contract.read_text()) if contract.exists() else {}
    result['training_ready']=result['conversion_contract'].get('training_ready')
    result['raw_contract']=result['conversion_contract'].get('profile')=='raw_head_ptp_24_nullable_v1'
    if episode is not None:
        from lerobot_replay import load_episode
        data = load_episode(root, episode)
        result['selected_episode'] = {'episode': int(episode), 'frames': len(data['times']),
            'duration_s': data['duration_s'], 'last_frame_timestamp_s': data['times'][-1],
            'names': data['names'], 'first_action': data['actions'][0],
            'first_sdk_command': data['command_targets'][0],
            'source_counts': data['source_counts'], 'provenance_frames': data.get('provenance_frames'),
            'first_recorded_observation': data['recorded_states'][0],
            'null_action_values':sum(v is None for row in data['actions'] for v in row),
            'timeline':data.get('timeline')}
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--dataset', required=True)
    p.add_argument('--episode', type=int)
    a = p.parse_args()
    print(json.dumps(describe(a.dataset, a.episode), ensure_ascii=False, allow_nan=False))
