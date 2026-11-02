# 使用说明
# 用途：厂商pbdat与同源MCAP的原值、NULL和时间一致性回归。
# 从项目根目录运行：./conversion/.venv/bin/python -m unittest discover -s conversion -p "test_factory_input.py" -v
# 仅使用临时目录、合成数据或Fake依赖，不启动真实SDK、不发送机器人动作。
# 测试分组、依赖环境及历史兼容范围见 README.md。
#
"""将同一批原 protobuf 放入 pbdat，验证两类输入的采样值与空值一致。"""
import struct
import tempfile
from pathlib import Path
import unittest
from mcap_input import messages, STATE, ACTION, CAMERAS
from test_mcap_input import recording
from factory_input import make_plan, topic_path
import mcap_input


def factory_from_mcap(source,root):
    for topic in (*STATE,*ACTION):
        rows=list(messages(source,[topic]));path=topic_path(root,topic);path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('wb') as f:
            f.write(struct.pack('<q',len(rows)))
            for _,msg,value in rows:
                f.write(struct.pack('<5q',msg.publish_time,0,0,0,len(msg.data)));f.write(msg.data)
    for cam in CAMERAS:
        frames=list(mcap_input.camera_packets(source,cam));folder=root/f'camera/{cam}';folder.mkdir(parents=True)
        (folder/f'{cam}.h265').write_bytes(b'plan-only fixture; video is not decoded')
        (folder/f'{cam}.txt').write_text(''.join(str(mcap_input.camera_payload(m.data)[0])+' I\n' for m in frames))


class FactoryMappingTests(unittest.TestCase):
    def test_raw_topics_match_mcap_and_do_not_depend_on_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);source=base/'raw.mcap';recording(source,sparse=True,delayed=True)
            factory_from_mcap(source,base/'native')
            a=mcap_input.make_plan(source)[0][0];b,report=make_plan(base/'native');b=b[0]
            self.assertEqual([r['action'] for r in a],[r['action'] for r in b])
            self.assertEqual([r['state'] for r in a],[r['state'] for r in b])
            self.assertEqual([r['source_timestamp_ns'] for r in a],[r['source_timestamp_ns'] for r in b])
            self.assertEqual(report['export_frames'],6);self.assertEqual(b[0]['action'],[None]*24)

if __name__=='__main__':unittest.main()
