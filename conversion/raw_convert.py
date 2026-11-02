# 使用说明
# 用途：将已读取的24维原值、NULL、PTP和视频写入LeRobot v2.1。
# 使用：通过 run_platform.sh 或 convert_r2.py 调用，不直接运行本文件。
# 多文件使用说明：README.md 的输出契约、恢复与完成判据。
#
"""将视频主轴的原始24维样本流式写成 LeRobot v2.1。

数值写入Parquet，图像写入每相机/每episode独立MP4；不再嵌入PNG。
原生一一对应帧复用HEVC码流，其余按原选帧无损写入H.264 RGB。
保留float64、NULL、原分辨率与原始PTP；不插值、不补值。
"""
from contextlib import ExitStack
import fcntl
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from batch_io import append_json, begin, commit, completed, json_write, recover
from conversion_progress import Progress
from recording_contract import NAMES

PROFILE = 'raw_head_ptp_24_nullable_v1'


def numeric_stats(values):
    """只为有完整数值的行生成格式元数据统计，不更改任何保存的行。"""
    a = np.asarray([[np.nan if q is None else q for q in row] if isinstance(row, list) else row
                    for row in values], dtype=np.float64)
    if a.ndim == 1: a = a[:, None]
    a = a[np.isfinite(a).all(axis=1)]
    if not len(a): return None
    return {'min': a.min(axis=0), 'max': a.max(axis=0), 'mean': a.mean(axis=0),
            'std': a.std(axis=0), 'count': np.asarray([len(a)])}


def load_meta(root, repo):
    from lerobot.common.datasets.lerobot_dataset import LeRobotDatasetMetadata
    from lerobot.common.datasets.utils import load_info
    meta = LeRobotDatasetMetadata.__new__(LeRobotDatasetMetadata)
    meta.root, meta.repo_id, meta.revision = root, repo, 'v2.1'
    meta.info = load_info(root)
    if meta.total_episodes: meta.load_metadata()
    else: meta.tasks, meta.task_to_task_index, meta.episodes, meta.episodes_stats, meta.stats = {}, {}, {}, {}, {}
    return meta


def convert(args):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from lerobot.common.datasets.lerobot_dataset import CODEBASE_VERSION, LeRobotDatasetMetadata
    from lerobot.common.datasets.utils import get_hf_features_from_features
    from convert_r2 import discover, make_plan, source_files, source_cameras, source_ready, open_camera, sha256, episode_name
    from video_mp4 import remux_native, RGBVideoWriter, video_info
    if CODEBASE_VERSION != 'v2.1': raise ValueError('必须使用 OpenPI 固定版本的 LeRobot v2.1')
    progress = getattr(args, 'progress_reporter', None) or Progress()
    sources = discover(args.input, getattr(args, 'input_format', 'auto'))
    if getattr(args, 'ready_only', False):
        sources = [p for p in sources if source_ready(p)[0]]
        if not sources: return
    progress.update(total_sources=len(sources), force=True)
    if args.inspect:
        for source in sources:
            _, report = make_plan(source, args.fps, progress=progress)
            print(json.dumps(report, ensure_ascii=False), flush=True)
        return
    if not args.output or not args.repo_id or not (args.task or args.tasks_json):
        raise ValueError('需要 output、repo-id、task 或 tasks-json')
    tasks = json.loads(Path(args.tasks_json).read_text()) if args.tasks_json else {}
    root = Path(args.output).expanduser().resolve()
    if any(root == p or root.is_relative_to(p) or p.is_relative_to(root) for p in sources):
        raise ValueError('输出目录不能覆盖或包含原始输入')
    root.parent.mkdir(parents=True, exist_ok=True)
    settings = {'profile': PROFILE, 'repo_id': args.repo_id, 'fps': args.fps, 'names': NAMES,
                'numeric_dtype': 'float64', 'media': 'video', 'video_encoding': 'source_hevc_or_lossless_h264rgb',
                'image_resize': False, 'null_policy': 'preserve'}
    errors = getattr(args, 'errors_out', [])
    lock = (root.parent/('.'+root.name+'.r2.lock')).open('a')
    with lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if root.exists():
            if not args.resume: raise FileExistsError('输出目录已存在；使用新目录或 --resume')
            if not (root/'provenance/settings.json').is_file() or json.loads((root/'provenance/settings.json').read_text()) != settings:
                raise ValueError('输出采用PNG图像模式或不同设置；请为MP4结果使用新目录')
            recover(root)
        meta = load_meta(root, args.repo_id) if (root/'meta/info.json').exists() else None
        done = {r['source']: r for r in completed(root)}
        for number, source in enumerate(sources, 1):
            task = args.task or tasks.get(episode_name(source))
            if not isinstance(task, str) or not task.strip(): raise ValueError('缺少明确任务描述：'+str(source))
            progress.source(number, episode_name(source), source.stat().st_size if source.is_file() else None)
            try:
                files = source_files(source)
                manifest = [{'path': str(p), 'bytes': p.stat().st_size, 'sha256': sha256(p)} for p in files]
                fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
                if str(source) in done:
                    if done[str(source)]['fingerprint'] != fingerprint or done[str(source)]['task'] != task:
                        raise ValueError('已转换的原始文件或任务描述发生改变')
                    progress.finish_source(True); continue
                segments, report = make_plan(source, args.fps, progress=progress)
                rows = segments[0]; cameras = source_cameras(source)
                progress.update(total_frames=len(rows), force=True)
                with ExitStack() as stack:
                    readers = {cam: open_camera(source, cam, report['camera_index_counts'][cam], None, getattr(args, 'threads', 1)) for cam in cameras}
                    for reader in readers.values(): stack.callback(reader.close)
                    shapes = {cam: tuple(readers[cam].get(0).shape) for cam in cameras}
                    features = {'observation.state': {'dtype': 'float64', 'shape': (24,), 'names': NAMES},
                        'action': {'dtype': 'float64', 'shape': (24,), 'names': NAMES},
                        'observation.state_valid': {'dtype': 'bool', 'shape': (24,), 'names': NAMES},
                        'action_valid': {'dtype': 'bool', 'shape': (24,), 'names': NAMES},
                        'source_timestamp_ns': {'dtype': 'int64', 'shape': (1,), 'names': None}}
                    features.update({f'observation.images.{cam}': {'dtype': 'video', 'shape': shape, 'names': ['height','width','channels']} for cam, shape in shapes.items()})
                    if meta is None:
                        meta = LeRobotDatasetMetadata.create(args.repo_id, args.fps, root=root, features=features, robot_type='g2_24axis', use_videos=True)
                        (root/'provenance').mkdir(); json_write(root/'provenance/settings.json', settings)
                    elif any(any(meta.features.get(k, {}).get(field) != v.get(field) for field in ('dtype','shape','names')) for k, v in features.items()) or set(meta.camera_keys) != {f'observation.images.{cam}' for cam in cameras}:
                        raise ValueError('原始分辨率/相机/字段与已有数据集不同，请使用另一输出目录；不会缩放迁就旧 schema')
                    begin(SimpleNamespace(root=root, meta=meta))
                    if task not in meta.task_to_task_index: meta.add_task(task)
                    episode = meta.total_episodes; offset = meta.total_frames; task_index = meta.task_to_task_index[task]
                    # MP4由同一事务记录，必须关闭后再提交官方视频元数据。
                    video_methods, video_writers = {}, {}
                    for cam in cameras:
                        path = root/meta.get_video_file_path(episode, 'observation.images.'+cam)
                        indices = [row['cameras'][cam]['index'] for row in rows]
                        method = remux_native(source, cam, indices, report['camera_index_counts'][cam], path, args.fps)
                        if method is None:
                            writer = RGBVideoWriter(path, shapes[cam], args.fps, getattr(args, 'threads', 1))
                            video_writers[cam] = writer
                            stack.callback(writer.close)
                            method = {'method': 'lossless_h264rgb', 'frames': len(rows), 'pixel_policy': 'exact decoded RGB'}
                        video_methods[cam] = method
                    schema = get_hf_features_from_features(meta.features).arrow_schema
                    output = root/meta.get_data_file_path(episode); output.parent.mkdir(parents=True, exist_ok=True)
                    buffer = []; columns = {k: [] for k in meta.features if not k.startswith('observation.images.')}
                    image_stats = {}
                    with pq.ParquetWriter(output, schema, compression='zstd') as writer, (root/'provenance/frames.jsonl').open('a') as trace:
                        for index, row in enumerate(rows):
                            frame = {'observation.state': row['state'], 'action': row['action'],
                                'observation.state_valid': row['state_valid'], 'action_valid': row['action_valid'],
                                'source_timestamp_ns': row['source_timestamp_ns'], 'timestamp': index/args.fps,
                                'frame_index': index, 'episode_index': episode, 'index': offset+index, 'task_index': task_index}
                            for cam, reader in readers.items():
                                wanted = row['cameras'][cam]['index']
                                image = reader.get(wanted) if cam in video_writers or index == 0 else None
                                if cam in video_writers: video_writers[cam].write(image)
                                # 图像统计仅为 LeRobot 元数据；不写回图像和动作。
                                if index == 0:
                                    pixels = np.asarray(image, dtype=np.float64)/255
                                    a = pixels.reshape(-1, 3)
                                    image_stats['observation.images.'+cam] = {k: v.reshape(3,1,1) for k,v in {'min': a.min(0), 'max': a.max(0), 'mean': a.mean(0), 'std': a.std(0)}.items()}
                                    image_stats['observation.images.'+cam]['count'] = np.asarray([1])
                            for key in columns: columns[key].append(frame[key])
                            buffer.append(frame)
                            if len(buffer) >= 8:
                                writer.write_table(pa.Table.from_pylist(buffer, schema=schema)); buffer.clear()
                            detail = {k:v for k,v in row.items() if k not in ('state','action','state_valid','action_valid')}
                            detail.update(source_episode=str(source), episode_index=episode, frame_index=index)
                            trace.write(json.dumps(detail, ensure_ascii=False)+'\n')
                            progress.update('写入MP4、原始数值与空值', fraction=.35+.6*(index+1)/len(rows), processed_frames=index+1)
                        if buffer: writer.write_table(pa.Table.from_pylist(buffer, schema=schema))
                    for writer in video_writers.values(): writer.close()
                    for reader in readers.values(): reader.finish()
                    for cam in cameras:
                        key = 'observation.images.'+cam
                        if not meta.info['features'][key].get('info'):
                            meta.info['features'][key]['info'] = video_info(root/meta.get_video_file_path(episode, key))
                    stats = {k: value for k, data in columns.items() if (value := numeric_stats(data)) is not None}
                    stats.update(image_stats)
                    # 原始纳秒值在 Parquet 中为 int64；元数据统计不替代原值。
                    meta.save_episode(episode, len(rows), [task], stats)
                    entry = {'source': str(source), 'segment': 0, 'episode_index': episode, 'fingerprint': fingerprint,
                        'frames': len(rows), 'task': task, 'training_ready': report['training_ready'],
                        'null_action_values': report['null_action_values'], 'null_state_values': report['null_state_values']}
                    append_json(root/'provenance/segments.jsonl', entry); commit(root)
                    report['image_shapes'] = shapes
                    report['video_storage'] = video_methods
                    json_write(root/'provenance'/(hashlib.sha256(str(source).encode()).hexdigest()[:16]+'.json'), {'report': report, 'source_manifest': manifest})
                    done[str(source)] = entry
                progress.finish_source(True)
            except Exception as exc:
                detail = {'source': str(source), 'error': f'{type(exc).__name__}: {exc}'}; errors.append(detail)
                if root.exists():
                    if (root/'.r2-transaction').exists(): recover(root); meta = load_meta(root, args.repo_id)
                    (root/'provenance').mkdir(exist_ok=True); append_json(root/'provenance/errors.jsonl', detail)
                progress.finish_source(False, detail['error'])
                if not args.skip_errors: raise
        if meta:
            entries = completed(root)
            contract = {'profile': PROFILE, 'schema': 'LeRobot v2.1 / OpenPI-pinned',
                'total_episodes': meta.total_episodes, 'total_frames': meta.total_frames, 'names': NAMES,
                'alignment': 'actual head frames; nearest recorded states/wrist images; prior original commands held',
                'timestamp': 'timestamp=index/30 is format index; source_timestamp_ns is unchanged PTP and used for replay',
                'numeric_dtype': 'float64', 'null_policy': 'Arrow null preserved; no dropped head frames or state fallback',
                'image_policy': 'MP4: original HEVC bitstream when frame mapping is identity; otherwise pixel-lossless H264 RGB; original dimensions',
                'media': 'video',
                'null_action_values': sum(e.get('null_action_values', 0) for e in entries),
                'null_state_values': sum(e.get('null_state_values', 0) for e in entries),
                'training_ready': bool(entries) and all(e.get('training_ready', False) for e in entries),
                'training_note': '含空值时标准 OpenPI 不能直接训练；本转换不替用户补值/删帧。图像首帧统计不是训练归一化参数，训练前由 OpenPI 重算。',
                'failed_inputs_this_run': len(errors), 'errors': errors}
            json_write(root/'provenance/conversion.json', contract)
            if not errors: (root/'provenance/SUCCESS').write_text('All requested source rows converted; see training_ready separately.\n')
        progress.finish(not errors)
        if errors: raise RuntimeError('部分输入转换失败，请查看逐条错误')
