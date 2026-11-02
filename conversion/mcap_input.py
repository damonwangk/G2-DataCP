# 使用说明
# 用途：读取自采MCAP原始protobuf及相机包，保留时间和值。
# 使用：经 run_platform.sh --input-format mcap 调用，不独立启动。
# 多文件使用说明：PLATFORM_MCAP.md 的MCAP输入契约。
#
"""读取自采 MCAP 的原始话题和头部帧；保留 float64、空值和原始 PTP。

关节使用原 protobuf 字节；图像直接读取 /raw/camera/* 中的 JPEG。
不使用辅助图像信封的本机墙钟，不插帧、不缩放、不重编码 JPEG。
"""
from collections import Counter
import io
from pathlib import Path
import struct

import numpy as np
from mcap.reader import make_reader
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from recording_contract import (CAMERAS, ARM_NAMES as ARMS, GRIPS, STATE, ACTION,
    Sample, positions, source_time, head_plan)

IMAGE = {cam: f'/camera/{cam}/image' for cam in CAMERAS}
RAW_IMAGE = {cam: f'/raw/camera/{cam}' for cam in CAMERAS}


def schema_class(data, name):
    """从文件携带的描述符构造 protobuf 类，不导入运动 SDK。"""
    pool = descriptor_pool.DescriptorPool()
    pending = list(descriptor_pb2.FileDescriptorSet.FromString(data).file)
    while pending:
        remaining = []
        for fd in pending:
            try: pool.Add(fd)
            except (TypeError, KeyError): remaining.append(fd)
        if len(remaining) == len(pending):
            raise ValueError('protobuf 描述符依赖无法解析')
        pending = remaining
    return message_factory.GetMessageClass(pool.FindMessageTypeByName(name))


def messages(path, topics, on_read=None):
    """读取原 protobuf 通道；供离线检查和测试复用。"""
    classes = {}
    with Path(path).open('rb') as stream:
        for schema, channel, message in make_reader(stream).iter_messages(topics=topics):
            if schema is None or channel.message_encoding != 'protobuf':
                raise ValueError(f'{channel.topic}: 缺少原 protobuf 描述符')
            if schema.id not in classes:
                classes[schema.id] = schema_class(schema.data, schema.name)
            yield channel.topic, message, classes[schema.id].FromString(message.data)
            if on_read: on_read(stream.tell())


def available_cameras(path):
    with Path(path).open('rb') as stream:
        reader = make_reader(stream); summary = reader.get_summary()
        if summary and summary.statistics:
            present = {c.topic for i, c in summary.channels.items() if summary.statistics.channel_message_counts.get(i, 0)}
        else:
            present = {c.topic for _, c, _ in reader.iter_messages(topics=list(RAW_IMAGE.values()))}
    return tuple(cam for cam, topic in RAW_IMAGE.items() if topic in present)


def camera_packets(path, camera):
    with Path(path).open('rb') as stream:
        for _, channel, message in make_reader(stream).iter_messages(topics=[RAW_IMAGE[camera]]):
            yield message


def camera_payload(data):
    """只拆出 Aorta 原包里的相机源时间和 JPEG，不改变内容。"""
    if len(data) < 176 or struct.unpack_from('<I', data, 172)[0]+176 != len(data):
        raise ValueError('原始相机包长度不匹配')
    if data[169] != 1 or data[176:178] != b'\xff\xd8':
        raise ValueError('原始 RGB 相机包不是 JPEG')
    return struct.unpack_from('<Q', data, 144)[0], data[176:]


def read_recording(path, progress=None):
    """读取全部原始样本；转换与原文件回放共用，读取时不重采样。"""
    path = Path(path); counts = Counter(); classes = {}
    samples = {t: [] for t in (*STATE, *ACTION)}
    cameras = {cam: [] for cam in available_cameras(path)}
    raw_to_camera = {RAW_IMAGE[cam]: cam for cam in cameras}
    size = path.stat().st_size
    with path.open('rb') as stream:
        topics = [*STATE, *ACTION, *raw_to_camera]
        for schema, channel, message in make_reader(stream).iter_messages(topics=topics):
            topic = channel.topic; index = counts[topic]; counts[topic] += 1
            if topic in raw_to_camera:
                cam = raw_to_camera[topic]; stamp, _jpeg = camera_payload(message.data)
                cameras[cam].append(Sample(index, stamp, message.log_time, message.publish_time))
            else:
                if schema is None or channel.message_encoding != 'protobuf':
                    raise ValueError(f'{topic}: 缺少原 protobuf 描述符')
                if schema.id not in classes: classes[schema.id] = schema_class(schema.data, schema.name)
                value = classes[schema.id].FromString(message.data)
                samples[topic].append(Sample(index, source_time(value), message.log_time,
                                             message.publish_time, positions(topic, value)))
            if progress: progress.update('读取原始 MCAP', fraction=.30*min(stream.tell()/max(size, 1), 1))
    return samples, cameras


def make_plan(path, fps=30, max_offset_ms=None, max_camera_age_ms=None, max_gap_ms=None, progress=None, **_):
    samples, cameras = read_recording(path, progress)
    return head_plan(samples, cameras, path, 'platform_mcap', progress)


class CameraReader:
    """按原帧序号读取 JPEG；压缩字节可直接嵌入 LeRobot image 列。"""
    def __init__(self, path, camera, count, size=None, threads=1):
        self.iterator = iter(camera_packets(path, camera))
        self.index, self.count, self.jpeg = -1, count, None
    def encoded(self, wanted):
        if wanted < self.index: raise ValueError('相机帧号倒序')
        while self.index < wanted:
            try: message = next(self.iterator)
            except StopIteration as exc: raise ValueError('原始相机帧少于索引') from exc
            self.index += 1; _, self.jpeg = camera_payload(message.data)
        return self.jpeg
    def get(self, wanted):
        from PIL import Image
        return np.asarray(Image.open(io.BytesIO(self.encoded(wanted))).convert('RGB'))
    def finish(self):
        for _ in self.iterator: self.index += 1
        if self.index+1 != self.count: raise ValueError('原始相机帧数改变')
    def close(self):
        self.iterator.close()
