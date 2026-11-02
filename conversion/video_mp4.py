# 使用说明
# 用途：按原帧映射封装HEVC或流式保存无损RGB MP4及视频元数据。
# 使用：由 raw_convert.py 调用，不独立启动；保留原分辨率与选帧。
# 多文件使用说明：README.md 的视频保存规则。
#
"""LeRobot v2.1 MP4：原生逐帧对应时复用HEVC码流，其余保存无损RGB视频。"""
from fractions import Fraction
from pathlib import Path

import av


def remux_native(source, camera, indices, count, output, fps):
    """仅在原帧与训练行完整一一对应时封装原码流；不解码重编码、不增删帧。"""
    source, output = Path(source), Path(output)
    if not source.is_dir() or indices != list(range(count)):
        return None
    path = source / "camera" / camera / (camera + ".h265")
    with av.open(str(path)) as incoming:
        stream = incoming.streams.video[0]
        # 带B帧的输入不改写解码/显示顺序，交给下方逐帧无损写入路径。
        if stream.codec_context.has_b_frames:
            return None
        output.parent.mkdir(parents=True, exist_ok=True)
        with av.open(str(output), "w", format="mp4", options={"movflags": "+faststart"}) as outgoing:
            target = outgoing.add_stream_from_template(stream)
            target.time_base = Fraction(1, fps)
            target.codec_context.codec_tag = "hvc1"
            number = 0
            for packet in incoming.demux(stream):
                if not packet.size:
                    continue
                # MP4使用与LeRobot timestamp一致的行号时间；原始PTP另存为int64。
                packet.pts = packet.dts = number
                packet.duration = 1
                packet.time_base = Fraction(1, fps)
                packet.stream = target
                outgoing.mux(packet)
                number += 1
        if number != count:
            raise ValueError(f"{camera}: 原HEVC帧数 {number} 与原索引 {count} 不一致")
    return {"method": "original_hevc_remux", "frames": number, "pixel_policy": "original compressed bitstream"}


class RGBVideoWriter:
    """逐行写入已选原帧；H.264 RGB无损编码避免新增像素损失，不落地PNG。"""
    def __init__(self, output, shape, fps, threads=1):
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        self.container = av.open(str(output), "w", format="mp4", options={"movflags": "+faststart"})
        self.stream = self.container.add_stream("libx264rgb", rate=fps)
        self.stream.height, self.stream.width = shape[:2]
        self.stream.pix_fmt = "rgb24"
        self.stream.options = {"crf": "0", "preset": "veryfast", "threads": str(threads)}
        self.fps, self.count = fps, 0

    def write(self, rgb):
        frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        frame.pts, frame.time_base = self.count, Fraction(1, self.fps)
        for packet in self.stream.encode(frame):
            self.container.mux(packet)
        self.count += 1

    def close(self):
        if self.container is None:
            return
        container, self.container = self.container, None
        try:
            for packet in self.stream.encode():
                container.mux(packet)
        finally:
            container.close()


def video_info(path):
    """按v2.1字段写入信息；其旧辅助函数不识别无损H.264 RGB的gbrp格式。"""
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        return {'video.height': stream.height, 'video.width': stream.width,
                'video.codec': stream.codec.canonical_name, 'video.pix_fmt': stream.pix_fmt,
                'video.fps': int(stream.average_rate), 'video.channels': 3,
                'video.is_depth_map': False, 'has_audio': False}
