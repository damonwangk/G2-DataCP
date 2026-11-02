# 使用说明
# 用途：HEVC封装与无损RGB MP4的像素、尺寸、顺序和帧数回归。
# 从项目根目录运行：./conversion/.venv/bin/python -m unittest discover -s conversion -p "test_video_mp4.py" -v
# 仅使用临时目录、合成数据或Fake依赖，不启动真实SDK、不发送机器人动作。
# 测试分组、依赖环境及历史兼容范围见 README.md。
#
"""离线验证MP4封装与无损回退：原像素、帧顺序、奇数分辨率和帧数。"""
from fractions import Fraction
from pathlib import Path
import tempfile
import unittest

import av
import numpy as np
from video_mp4 import RGBVideoWriter, remux_native, video_info


class MP4Tests(unittest.TestCase):
    def test_lossless_rgb_and_original_dimensions(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'video.mp4'
            images=[np.random.default_rng(i).integers(0,256,(11,15,3),dtype=np.uint8) for i in range(5)]
            writer=RGBVideoWriter(path,images[0].shape,30)
            for image in images:writer.write(image)
            writer.close();writer.close()
            with av.open(str(path)) as c:decoded=list(c.decode(video=0))
            self.assertEqual(len(decoded),5)
            for a,b in zip(images,decoded):np.testing.assert_array_equal(a,b.to_ndarray(format='rgb24'))
            self.assertEqual(video_info(path)['video.fps'],30)
            self.assertEqual(video_info(path)['video.channels'],3)

    def test_hevc_remux_preserves_all_decoded_frames(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);folder=root/'camera/head_color';folder.mkdir(parents=True)
            source=folder/'head_color.h265'
            with av.open(str(source),'w',format='hevc') as c:
                stream=c.add_stream('libx265',rate=30);stream.width=64;stream.height=32;stream.pix_fmt='yuv420p'
                stream.options={'preset':'ultrafast','x265-params':'log-level=error:pools=none:frame-threads=1:bframes=0'}
                for i in range(6):
                    rgb=np.full((32,64,3),i*30,dtype=np.uint8)
                    for packet in stream.encode(av.VideoFrame.from_ndarray(rgb,format='rgb24')):c.mux(packet)
                for packet in stream.encode():c.mux(packet)
            with av.open(str(source)) as c:original=[f.to_ndarray(format='rgb24') for f in c.decode(video=0)]
            output=root/'out.mp4';method=remux_native(root,'head_color',list(range(6)),6,output,30)
            self.assertEqual(method['method'],'original_hevc_remux')
            with av.open(str(output)) as c:
                self.assertEqual(c.streams.video[0].average_rate,Fraction(30,1))
                decoded=list(c.decode(video=0))
            self.assertEqual(len(decoded),6)
            for a,b in zip(original,decoded):np.testing.assert_array_equal(a,b.to_ndarray(format='rgb24'))
            self.assertIsNone(remux_native(root,'head_color',[0,0,2],6,root/'unused.mp4',30))
