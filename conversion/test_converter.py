# 使用说明
# 用途：原生视频读取的分辨率与像素离线回归。
# 从项目根目录运行：./conversion/.venv/bin/python -m unittest discover -s conversion -p "test_converter.py" -v
# 仅使用临时目录、合成数据或Fake依赖，不启动真实SDK、不发送机器人动作。
# 测试分组、依赖环境及历史兼容范围见 README.md。
#
"""原视频读取回归：不缩放、不补边，原解码像素不变。"""
import unittest
import numpy as np
from convert_r2 import CameraReader

class FakeFrame:
    def __init__(self,value):self.value=value
    def to_ndarray(self,format):return np.full((4,8,3),self.value,dtype=np.uint8)

class CameraReaderTests(unittest.TestCase):
    def reader(self,actual=4,declared=4):
        r=CameraReader.__new__(CameraReader);r.frames=iter(FakeFrame(i) for i in range(actual))
        r.count,r.index,r.image=declared,-1,None;r.encoded_index,r.png=-1,None
        return r
    def test_native_pixels_and_original_dimensions(self):
        r=self.reader();a=r.get(2);self.assertEqual(a.shape,(4,8,3));self.assertEqual(int(a[0,0,0]),2)
        np.testing.assert_array_equal(r.get(2),a)
        r.finish()
    def test_count_mismatch_is_reported(self):
        r=self.reader(actual=3)
        with self.assertRaises(ValueError):r.get(3)
        r=self.reader(actual=5);r.get(0)
        with self.assertRaises(ValueError):r.finish()
