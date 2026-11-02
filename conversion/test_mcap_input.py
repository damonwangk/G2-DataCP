# 使用说明
# 用途：MCAP名称映射、原始指令/NULL/PTP和相机包读取回归。
# 从项目根目录运行：./conversion/.venv/bin/python -m unittest discover -s conversion -p "test_mcap_input.py" -v
# 仅使用临时目录、合成数据或Fake依赖，不启动真实SDK、不发送机器人动作。
# 测试分组、依赖环境及历史兼容范围见 README.md。
#
"""真实 protobuf 的24维 MCAP 回归：稀疏指令、空值、时间缺口与图像原字节。"""
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np
from PIL import Image
from mcap.writer import Writer, CompressionType
import mcap_input as adapter
from mcap_input import schema_class
from recording_contract import JOINT_NAMES, NAMES, TYPES
import convert_r2


def recording(path, missing=None, invalid_index=None, cameras=adapter.CAMERAS, sparse=False, delayed=False, head_times=None):
    data=(Path(__file__).resolve().parent.parent/'studio/schemas.pb').read_bytes()
    classes={t:schema_class(data, name) for t,name in TYPES.items()}
    jpeg=io.BytesIO();Image.new('RGB',(8,4),(255,0,0)).save(jpeg,format='JPEG');jpg=jpeg.getvalue()
    stamps=head_times or [1_000_000_000+round(i*1_000_000_000/30) for i in range(6)]
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('wb') as stream:
        w=Writer(stream,compression=CompressionType.NONE);w.start()
        channels={}
        for topic,cls in classes.items():
            sid=w.register_schema(name=cls.DESCRIPTOR.full_name,encoding='protobuf',data=data)
            channels[topic]=w.register_channel(topic=topic,message_encoding='protobuf',schema_id=sid)
        raw={cam:w.register_channel(topic=adapter.RAW_IMAGE[cam],message_encoding='aorta',schema_id=0) for cam in cameras}
        for i,stamp in enumerate(stamps):
            for topic,cls in classes.items():
                if topic==missing or topic in adapter.ACTION and (sparse and i not in (0,3) or delayed and i==0):continue
                value=cls();value.header.stamp.sec=stamp//10**9;value.header.stamp.nanosec=stamp%10**9
                names=JOINT_NAMES if topic in (adapter.STATE[0],adapter.ACTION[0]) else [adapter.GRIPS[0] if 'left' in topic else adapter.GRIPS[1]]
                value.name.extend(names[::-1])
                vals=[i*.001+j*.0001+.000000000001 for j in range(len(names))]
                if topic in (adapter.STATE[0],adapter.ACTION[0]):
                    getattr(value,'motor_position' if topic==adapter.STATE[0] else 'position').extend(vals[::-1])
                    if topic==adapter.STATE[0]:value.position.extend([1e6]*22)
                elif topic in adapter.STATE:value.end_state.add(position=.025-i*.001)
                else:value.position.append(i*.001)
                if topic==adapter.ACTION[0] and i==invalid_index:value.ClearField('position')
                w.add_message(channel_id=channels[topic],log_time=stamp+2*10**9,publish_time=stamp+100,data=value.SerializeToString())
            for cam,cid in raw.items():
                packet=bytearray(176)+jpg
                struct.pack_into('<I',packet,0,len(packet));struct.pack_into('<I',packet,8,len(packet)-24);struct.pack_into('<I',packet,88,len(packet)-128)
                struct.pack_into('<Q',packet,48,stamp+100);struct.pack_into('<Q',packet,144,stamp)
                struct.pack_into('<I',packet,161,8);struct.pack_into('<I',packet,165,4);packet[169]=1
                struct.pack_into('<I',packet,172,len(jpg))
                w.add_message(channel_id=cid,log_time=stamp+2*10**9,publish_time=stamp+100,data=bytes(packet))
        w.finish()
    return jpg


class McapTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'episode/raw.mcap'
    def test_full_24_name_mapping_and_float64_are_exact(self):
        recording(self.path);segments,report=adapter.make_plan(self.path);row=segments[0][0]
        self.assertEqual(len(row['action']),24);self.assertEqual(row['action'][0],.000000000001)
        self.assertEqual(row['state'][8],.000800000001);self.assertEqual(row['state'][15],.025)
        self.assertEqual(row['action'][15],0.);self.assertEqual(row['source_timestamp_ns'],10**9)
        self.assertEqual(report['export_frames'],6);self.assertEqual(row['source_samples'][adapter.ACTION[0]]['publish_time_ns'],10**9+100)
    def test_sparse_commands_hold_previous_original_target(self):
        recording(self.path,sparse=True);rows=adapter.make_plan(self.path)[0][0]
        self.assertEqual(rows[0]['action'],rows[2]['action']);self.assertEqual(rows[3]['action'],rows[5]['action'])
        self.assertNotEqual(rows[2]['action'],rows[3]['action'])
    def test_missing_initial_and_missing_topic_remain_null_without_dropping_frames(self):
        recording(self.path,delayed=True,missing=adapter.ACTION[2]);rows,report=adapter.make_plan(self.path);rows=rows[0]
        self.assertEqual(len(rows),6);self.assertEqual(rows[0]['action'],[None]*24)
        self.assertIsNone(rows[-1]['action'][-1]);self.assertFalse(report['training_ready']);self.assertEqual(report['null_action_frames'],6)
    def test_explicit_empty_position_is_not_replaced_by_state_or_previous_command(self):
        recording(self.path,invalid_index=3);rows=adapter.make_plan(self.path)[0][0]
        self.assertIsNone(rows[3]['action'][0]);self.assertIsNotNone(rows[3]['state'][0]);self.assertIsNotNone(rows[4]['action'][0])
    def test_video_time_gap_is_not_filled(self):
        stamps=[10**9,10**9+33_000_000,10**9+200_000_000]
        recording(self.path,head_times=stamps);rows=adapter.make_plan(self.path)[0][0]
        self.assertEqual([r['source_timestamp_ns'] for r in rows],stamps)
    def test_image_bytes_and_original_dimensions(self):
        jpg=recording(self.path);reader=adapter.CameraReader(self.path,'head_color',6,256);self.addCleanup(reader.close)
        self.assertEqual(reader.encoded(2),jpg);self.assertEqual(reader.get(2).shape,(4,8,3));reader.finish()
    def test_head_is_required_for_training_but_raw_motion_reader_still_works(self):
        recording(self.path,cameras=())
        with self.assertRaisesRegex(ValueError,'头部'):adapter.make_plan(self.path)
        samples,cameras=adapter.read_recording(self.path);self.assertEqual(len(samples[adapter.ACTION[0]]),6)
    def test_discovery_filters_both_formats(self):
        recording(self.path);root=self.path.parent.parent;native=root/'factory/record/-hal-joint_state.pbdat'
        native.parent.mkdir(parents=True);native.write_bytes(b'fixture')
        self.assertEqual(convert_r2.discover([root],'mcap'),[self.path]);self.assertEqual(convert_r2.discover([root],'factory'),[native.parent.parent])

if __name__=='__main__':unittest.main()
