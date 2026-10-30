import io
import tempfile
from pathlib import Path
import unittest

import numpy as np
from PIL import Image
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from mcap.writer import Writer, CompressionType
import mcap_input as adapter
import convert_r2


def recording(path, missing=None, invalid_index=None, cameras=adapter.CAMERAS):
    fd=descriptor_pb2.FileDescriptorProto(name='test.proto',package='test',syntax='proto2')
    for name,fields in {'Motor':[('position',1,1,2,None)],
        'Joint':[('name',1,3,9,None),('motor_position',2,3,2,None),('position',3,3,2,None),('end_state',4,3,11,'.test.Motor')],
        'Image':[('format',1,1,9,None),('data',2,1,12,None)]}.items():
        msg=fd.message_type.add(name=name)
        for name,num,label,typ,type_name in fields:
            f=msg.field.add(name=name,number=num,label=label,type=typ)
            if type_name: f.type_name=type_name
    schema=descriptor_pb2.FileDescriptorSet();schema.file.append(fd)
    pool=descriptor_pool.DescriptorPool();pool.Add(fd)
    Joint=message_factory.GetMessageClass(pool.FindMessageTypeByName('test.Joint'))
    Camera=message_factory.GetMessageClass(pool.FindMessageTypeByName('test.Image'))
    jpeg=io.BytesIO();Image.new('RGB',(8,4),(255,0,0)).save(jpeg,format='JPEG')
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('wb') as f:
        writer=Writer(f,compression=CompressionType.NONE);writer.start()
        js=writer.register_schema(name='test.Joint',encoding='protobuf',data=schema.SerializeToString())
        cs=writer.register_schema(name='test.Image',encoding='protobuf',data=schema.SerializeToString())
        channels={t:writer.register_channel(topic=t,message_encoding='protobuf',schema_id=cs if t in adapter.IMAGE.values() else js) for t in (*adapter.STATE,*adapter.ACTION,*(adapter.IMAGE[c] for c in cameras)) if t!=missing}
        for index in range(6):
            stamp=1_000_000_000+round(index*1_000_000_000/30)
            for topic,channel in channels.items():
                if topic in adapter.IMAGE.values(): value=Camera(format='jpeg',data=jpeg.getvalue())
                else:
                    value=Joint()
                    if topic in (adapter.STATE[0],adapter.ACTION[0]):
                        value.name.extend(adapter.ARMS[::-1])
                        position=np.arange(14,dtype=float)+index+(100 if topic in adapter.ACTION else 0)
                        getattr(value,'motor_position' if topic==adapter.STATE[0] else 'position').extend(position[::-1])
                        if topic==adapter.STATE[0]: value.position.extend(np.full(14,1e6))
                    else:
                        value.name.append(adapter.GRIPS[0] if 'left' in topic else adapter.GRIPS[1])
                        position=index+(200 if topic in adapter.ACTION else 10)
                        if topic in adapter.STATE: value.end_state.add(position=position)
                        else: value.position.append(position)
                    if topic==adapter.ACTION[0] and index==invalid_index: value.ClearField('position')
                writer.add_message(channel_id=channel,log_time=stamp,publish_time=stamp-100,data=value.SerializeToString())
        writer.finish()


class McapTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'episode_1_20261030_100000/raw.mcap'
    def test_source_state_and_action_are_distinct_and_named(self):
        recording(self.path)
        segments,report=adapter.make_plan(self.path)
        self.assertEqual(report['segment_lengths'],[6])
        row=segments[0][0]
        np.testing.assert_array_equal(row['state'],list(range(7))+[10]+list(range(7,14))+[10])
        np.testing.assert_array_equal(row['action'],list(range(100,107))+[200]+list(range(107,114))+[200])
        self.assertEqual(row['source_samples'][adapter.ACTION[0]]['publish_time_ns'],999999900)
    def test_missing_action_uses_state_with_provenance(self):
        recording(self.path,missing=adapter.ACTION[2])
        segments,report=adapter.make_plan(self.path)
        self.assertEqual(segments[0][0]['action'][15],10)
        self.assertEqual(segments[0][0]['action_sources'][adapter.ACTION[2]],adapter.STATE[2])
        self.assertEqual(report['segment_lengths'],[6])
    def test_missing_action_sample_uses_state_without_dropping(self):
        recording(self.path,invalid_index=3)
        segments,report=adapter.make_plan(self.path)
        self.assertEqual(report['segment_lengths'],[6])
        self.assertEqual([r['grid_index'] for s in segments for r in s],[0,1,2,3,4,5])
        self.assertEqual(segments[0][3]['action'][0],3)
    def test_no_images_keeps_one_complete_motion_episode(self):
        recording(self.path,cameras=())
        self.assertEqual(adapter.available_cameras(self.path),())
        segments,report=adapter.make_plan(self.path)
        self.assertEqual(len(segments),1);self.assertEqual(len(segments[0]),6)
        self.assertEqual(report['available_cameras'],[])
        self.assertEqual(segments[0][0]['cameras'],{})
    def test_only_present_camera_is_used(self):
        recording(self.path,cameras=('head_color',))
        segments,report=adapter.make_plan(self.path)
        self.assertEqual(adapter.available_cameras(self.path),('head_color',))
        self.assertEqual(list(segments[0][0]['cameras']),['head_color'])
    def test_camera_and_recursive_discovery(self):
        recording(self.path)
        camera=adapter.CameraReader(self.path,'head_color',6,32)
        self.addCleanup(camera.close)
        self.assertEqual(camera.get(2).shape,(32,32,3));camera.finish()
        for inputs in [[str(self.path)],[str(self.path.parent)],[str(self.path.parent.parent)],[str(self.path),str(self.path.parent)]]:
            self.assertEqual(convert_r2.discover(inputs),[self.path])
        self.assertEqual(convert_r2.source_files(self.path),[self.path])

if __name__=='__main__':unittest.main()
