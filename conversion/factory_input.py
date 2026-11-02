# 使用说明
# 用途：读取厂商full目录的原始逐话题pbdat及相机索引。
# 使用：经 run_platform.sh --input-format factory 调用，不独立启动。
# 多文件使用说明：PLATFORM_MCAP.md 的原生输入契约。
#
"""从厂商 full_* 的逐话题 pbdat 和相机索引读取原始样本。

不使用已经打包/对齐过的 bundle_data，也不读取 repaired/aligned H5。
pbdat 容器按机器人随附 pb_to_mcap.py 的 8字节总数+40字节帧头读取。
"""
from pathlib import Path
import struct
from google.protobuf import descriptor_pb2
from recording_contract import CAMERAS, STATE, ACTION, TYPES, Sample, positions, source_time, head_plan
from mcap_input import schema_class


def protobuf_classes():
    data = (Path(__file__).resolve().parent.parent/'studio/schemas.pb').read_bytes()
    return {topic: schema_class(data, name) for topic, name in TYPES.items()}


def pbdat_messages(path, cls):
    """保留原 protobuf、帧号和 PTP 帧头；不重排、不修复源记录。"""
    path = Path(path)
    with path.open('rb') as stream:
        head = stream.read(8)
        if len(head) != 8: raise ValueError(f'{path}: 缺少 pbdat 总帧数')
        count, = struct.unpack('<q', head)
        for index in range(count):
            header = stream.read(40)
            if len(header) != 40: raise ValueError(f'{path}: 帧头截断 {index}')
            stamp, _a, _b, _c, length = struct.unpack('<5q', header)
            if length < 0 or length > path.stat().st_size-stream.tell():
                raise ValueError(f'{path}: 负载长度错误 {index}')
            payload = stream.read(length)
            yield index, stamp, cls.FromString(payload)
        if stream.read(1): raise ValueError(f'{path}: 总帧数后还有额外数据')


def topic_path(root, topic):
    return Path(root)/'record'/(topic.replace('/', '-')+'.pbdat')


def available_cameras(path):
    path = Path(path)
    return tuple(cam for cam in CAMERAS if (path/f'camera/{cam}/{cam}.txt').is_file()
                 and (path/f'camera/{cam}/{cam}.h265').is_file())


def read_recording(path, progress=None):
    from convert_r2 import camera_index
    path = Path(path); classes = protobuf_classes(); samples = {t: [] for t in (*STATE, *ACTION)}
    for topic, cls in classes.items():
        file = topic_path(path, topic)
        if not file.exists(): continue
        for index, record_ns, value in pbdat_messages(file, cls):
            samples[topic].append(Sample(index, source_time(value), record_ns, record_ns, positions(topic, value)))
        if progress: progress.update('读取原始 pbdat '+topic, fraction=.30*len([v for v in samples.values() if v])/6)
    cameras = {cam: [Sample(i, int(t), int(t), int(t))
                    for i, t in enumerate(camera_index(path/f'camera/{cam}/{cam}.txt'))]
               for cam in available_cameras(path)}
    return samples, cameras


def make_plan(path, fps=30, progress=None, **_):
    samples, cameras = read_recording(path, progress)
    return head_plan(samples, cameras, path, 'factory', progress)
