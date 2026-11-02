#!/usr/bin/env python3
# 使用说明
# 用途：历史内嵌图像/PNG数据集的逐行原值、NULL、PTP及图像校验。
# 从项目根目录运行：./conversion/.venv/bin/python conversion/validate_lerobot.py --root /path/to/legacy_image_dataset --repo-id local/name --horizon 50
# 实现读取Parquet图像列的bytes，不能直接用于当前仅含MP4视频的输出。
# 输入provenance引用的原记录须仍可访问；仅验读，不改数据、不发机器人动作。
# 当前MP4验证入口与相关离线测试见 README.md 的“验证与历史兼容范围”。
#
"""逐行对照两类原始输入验证 LeRobot：数值、NULL、PTP、图像字节/像素。

验证不处理数据、不执行动作。固定版本官方 loader 的图像和未来50帧
动作读取单独验读；含NULL时如实报告，不把 loader 可读当成可以训练。
"""
import argparse
from contextlib import ExitStack
import io
import json
from pathlib import Path
import numpy as np
import pyarrow.parquet as pq
from PIL import Image
from convert_r2 import make_plan, open_camera, source_cameras, NAMES


def validate(root,repo_id,horizon=50):
    root=Path(root);info=json.loads((root/'meta/info.json').read_text());entries=[json.loads(x) for x in (root/'provenance/segments.jsonl').read_text().splitlines()]
    rows_checked=images_checked=0;nulls=0
    for entry in entries:
        source=Path(entry['source']);plan,report=make_plan(source);expected=plan[0];episode=entry['episode_index']
        path=root/info['data_path'].format(episode_chunk=episode//info['chunks_size'],episode_index=episode)
        with ExitStack() as stack:
            readers={cam:open_camera(source,cam,report['camera_index_counts'][cam],None,1) for cam in source_cameras(source)}
            for r in readers.values():stack.callback(r.close)
            index=0
            for batch in pq.ParquetFile(path).iter_batches(batch_size=8):
                for actual in batch.to_pylist():
                    row=expected[index]
                    for key,original in [('action','action'),('observation.state','state')]:
                        if actual[key]!=row[original]:raise ValueError(f'{episode}/{index}: {key} 原值或NULL不同')
                    if actual['source_timestamp_ns']!=row['source_timestamp_ns']:raise ValueError('原始 PTP 不同')
                    if len(actual['action'])!=24:raise ValueError('不是24维')
                    for cam,reader in readers.items():
                        data=actual['observation.images.'+cam]['bytes'];wanted=row['cameras'][cam]['index']
                        if source.suffix=='.mcap':
                            if data!=reader.encoded(wanted):raise ValueError('原 JPEG 字节不同')
                        elif not np.array_equal(np.asarray(Image.open(io.BytesIO(data)).convert('RGB')),reader.get(wanted)):
                            raise ValueError('原 H.265 解码 RGB 像素不同')
                        images_checked+=1
                    nulls+=sum(v is None for v in actual['action']);rows_checked+=1;index+=1
            if index!=len(expected):raise ValueError('真实头部帧数不同，出现丢帧或补帧')
    from lerobot.common.datasets.lerobot_dataset import LeRobotDataset
    ds=LeRobotDataset(repo_id,root=root,delta_timestamps={'action':[i/info['fps'] for i in range(horizon)]})
    loader_samples=0;null_loader_errors=[]
    for episode in range(ds.num_episodes):
        start=int(ds.episode_data_index['from'][episode]);end=int(ds.episode_data_index['to'][episode])
        for index in sorted({start,(start+end-1)//2,end-1}):
            try:
                item=ds[index]
                if tuple(item['action'].shape)!=(horizon,24):raise ValueError('官方loader动作序列shape不同')
                loader_samples+=1
            except RuntimeError as exc:
                if nulls and 'NoneType' in str(exc):
                    null_loader_errors.append({'index':index,'error':str(exc)})
                else:raise
    result={'all_source_values_nulls_and_timestamps_identical':True,'images_bytes_or_decoded_pixels_identical':True,
        'episodes':len(entries),'frames':rows_checked,'images_checked':images_checked,'null_action_values':nulls,
        'official_loader_samples':loader_samples,'official_loader_null_errors':null_loader_errors,'horizon':horizon,'robot_commands_sent':False,
        'training_ready':json.loads((root/'provenance/conversion.json').read_text())['training_ready']}
    print(json.dumps(result,ensure_ascii=False,indent=2));return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--repo-id',required=True);p.add_argument('--horizon',type=int,default=50)
    a=p.parse_args();validate(a.root,a.repo_id,a.horizon)
