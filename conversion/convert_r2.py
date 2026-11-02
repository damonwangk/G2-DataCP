#!/usr/bin/env python3
# 使用说明
# 用途：单/多条原生full或MCAP转换入口，输出24维LeRobot v2.1 MP4。
# 从项目根目录检查：./conversion/.venv/bin/python conversion/convert_r2.py --input /path/to/full_or.mcap --inspect
# 正式转换加：--output /path/to/new_dataset --repo-id local/name --task "明确任务"。
# 批量JSON入口使用 run_platform.sh；完整参数、保真规则与恢复见 README.md。
# 仅进行文件读取/转换，不加载运动SDK；--inspect不写数据集。
#
"""两类原始记录转 LeRobot 的统一入口；保留24维、空值和真实视频帧。

采样逻辑分别在 mcap_input / factory_input，共用 recording_contract；
raw_convert 负责 LeRobot 写入。所有入口都采用同一保真规则。
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from recording_contract import CAMERAS, ARM_NAMES, NAMES

LEROBOT_REV = '0cf864870cf29f4738d3ade893e6fd13fbd7cdb5'


def is_factory(path):
    return (path/'record/-hal-joint_state.pbdat').is_file() or (path/'record/-genie-bundle_data.pbdat').is_file()


def discover(inputs, input_format='auto'):
    found = set()
    for value in inputs:
        path = Path(value).expanduser().resolve()
        if path.is_file() and path.suffix == '.mcap': found.add(path)
        elif path.is_dir():
            if (path/'raw.mcap').is_file(): found.add(path/'raw.mcap')
            elif is_factory(path): found.add(path)
            else:
                found.update(p for p in path.rglob('raw.mcap') if not any(x.startswith('.') for x in p.relative_to(path).parts))
                found.update(p for p in path.glob('*.mcap') if not p.name.startswith('.'))
                found.update(p.parent.parent for name in ('-hal-joint_state.pbdat','-genie-bundle_data.pbdat') for p in path.rglob('record/'+name))
        else: raise ValueError('找不到原始 MCAP / 原生记录：'+str(path))
    if input_format == 'mcap': found = {p for p in found if p.suffix == '.mcap'}
    elif input_format == 'factory': found = {p for p in found if p.is_dir()}
    if not found: raise ValueError('没有找到 '+input_format+' 原始记录')
    return sorted(found)


def episode_name(ep):
    return ep.parent.name if ep.name == 'raw.mcap' else ep.stem if ep.is_file() else ep.name


def source_cameras(ep):
    if ep.suffix == '.mcap':
        from mcap_input import available_cameras
    else:
        from factory_input import available_cameras
    return available_cameras(ep)


def source_files(ep):
    if ep.suffix == '.mcap': return [ep]
    from factory_input import topic_path
    from recording_contract import STATE, ACTION
    files = [topic_path(ep, t) for t in (*STATE, *ACTION) if topic_path(ep, t).exists()]
    files += [ep/f'camera/{cam}/{cam}.{suffix}' for cam in source_cameras(ep) for suffix in ('txt','h265')]
    if (ep/'meta_info.json').exists(): files.append(ep/'meta_info.json')
    return files


def source_ready(ep):
    if ep.suffix == '.mcap': return ep.is_file(), 'completed MCAP filename required'
    from episode_ready import verify_ready
    return verify_ready(ep)


def camera_index(path):
    stamps = [int(line.split()[0]) for line in Path(path).read_text().splitlines() if line.strip()]
    if any(b < a for a,b in zip(stamps, stamps[1:])): raise ValueError('原始相机时间戳倒序：'+str(path))
    return np.asarray(stamps, dtype=np.int64)


def make_plan(episode, fps=30, max_offset_ms=None, max_camera_age_ms=None, max_gap_ms=None, progress=None, raw_mapping=True):
    """所有入口使用真实头部帧；旧的固定网格参数不再改变数据。"""
    if Path(episode).suffix == '.mcap':
        from mcap_input import make_plan as plan
    else:
        from factory_input import make_plan as plan
    return plan(Path(episode), fps=fps, progress=progress)


class CameraReader:
    """顺序解码原 H.265；MP4写入器按原索引读取RGB，不缩放或生成PNG。"""
    def __init__(self, path, count, size=None, threads=1):
        import av
        av.logging.set_level(av.logging.ERROR)
        self.container = av.open(str(path)); self.container.streams.video[0].thread_count = threads
        self.frames = iter(self.container.decode(video=0))
        self.count, self.index, self.image = count, -1, None
    def get(self, wanted):
        if wanted < self.index: raise ValueError('原始相机帧号倒序')
        while self.index < wanted:
            try: frame = next(self.frames)
            except StopIteration as exc: raise ValueError('H.265 解码帧少于原索引') from exc
            self.index += 1; self.image = frame.to_ndarray(format='rgb24')
        return self.image
    def finish(self):
        for _ in self.frames: self.index += 1
        if self.index+1 != self.count: raise ValueError(f'原视频解码帧数 {self.index+1} 与索引 {self.count} 不同')
    def close(self): self.container.close()


def open_camera(ep, cam, count, size=None, threads=1):
    if ep.suffix == '.mcap':
        from mcap_input import CameraReader as McapReader
        return McapReader(ep, cam, count, size, threads)
    return CameraReader(ep/f'camera/{cam}/{cam}.h265', count, size, threads)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8*1024*1024), b''): digest.update(chunk)
    return digest.hexdigest()


def convert(args):
    from raw_convert import convert as raw_convert
    return raw_convert(args)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', nargs='+', required=True); p.add_argument('--output'); p.add_argument('--repo-id')
    p.add_argument('--task'); p.add_argument('--tasks-json'); p.add_argument('--input-format', choices=['auto','mcap','factory'], default='auto')
    p.add_argument('--inspect', action='store_true'); p.add_argument('--resume', action='store_true'); p.add_argument('--skip-errors', action='store_true')
    p.add_argument('--fps', type=int, default=30); p.add_argument('--threads', type=int, default=1)
    # 兼容原部署脚本的参数名；旧参数名不改变MP4输出方式，不触发缩放、过滤或重采样。
    p.add_argument('--ready-only', action='store_true'); p.add_argument('--codec', default='original')
    p.add_argument('--media', default='video'); p.add_argument('--image-size', type=int, default=0)
    p.add_argument('--min-free-gb', type=float, default=0)
    for key in ('offset','camera-age','gap'): p.add_argument('--max-'+key+'-ms', type=float, default=None)
    args = p.parse_args()
    if args.fps != 30: p.error('主视角逻辑索引固定为30；真实帧时间另存，不重采样')
    convert(args)

if __name__ == '__main__': main()
