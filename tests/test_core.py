import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from mcap.reader import make_reader
from native import Framer, Schemas, decode, ROOT
from pipeline import Series, choose, joint_at
from storage import Recorder, Playback, validate

class Contracts(unittest.TestCase):
    def setUp(self):self.schemas=Schemas()
    def test_fragmented_camera_frame(self):
        frame=(ROOT/'evidence/camera_head_color_cosine_runner.bin').read_bytes();f=Framer()
        self.assertEqual(list(f.feed(frame[:3])),[]);self.assertEqual(list(f.feed(frame[3:129])),[])
        self.assertEqual(list(f.feed(frame[129:]+frame)),[frame,frame])
    def test_wrong_length_rejected(self):
        with self.assertRaises(ValueError):list(Framer().feed(b'\x01\x00\x00\x00'))
    def test_original_jpeg_payload(self):
        frame=(ROOT/'evidence/camera_head_color_cosine_runner.bin').read_bytes();meta,_,jpg=decode(frame,'CosineDataPacket',self.schemas)
        self.assertEqual(jpg,frame[176:]);self.assertEqual((meta['width'],meta['height']),(640,400))
    def samples(self):
        s=Series([{'t':100,'log':1010,'seq':1,'uid':'x','names':['a','b'],'q':[0.,10.]},
                  {'t':200,'log':9000,'seq':2,'uid':'x','names':['b','a'],'q':[20.,2.]}]);s.index_times();return s
    def test_joint_name_mapping_and_source_clock(self):
        x=joint_at(self.samples(),150);self.assertEqual(x['motor_position'],[1.,15.]);self.assertEqual(x['provenance']['alpha'],.5)
    def test_no_extrapolation(self):
        self.assertIsNone(joint_at(self.samples(),99));self.assertIsNone(joint_at(self.samples(),201))
    def test_no_interpolation_over_gap(self):self.assertIsNone(joint_at(self.samples(),150,max_bracket_ns=50))
    def test_causal_gripper_selection(self):
        s=self.samples();self.assertEqual(choose(s,180,previous=True)['seq'],1);self.assertEqual(choose(s,180,previous=False)['seq'],2)
        self.assertIsNone(choose(s,250,previous=True,tolerance_ns=20))
    def test_lossless_mcap_roundtrip(self):
        import storage,json,time
        publishers=json.loads((ROOT/'evidence/registry.json').read_text());p=next(p for p in publishers if p['topic']=='/camera/head_color' and p['exe_name']=='cosine_runner')
        frame=(ROOT/'evidence/camera_head_color_cosine_runner.bin').read_bytes();meta,data,image=decode(frame,p['type'],self.schemas)
        with tempfile.TemporaryDirectory() as tmp,patch.object(storage,'data_dir',return_value=Path(tmp)):
            recorder=Recorder(self.schemas);recorder.start('roundtrip',[])
            event=dict(topic=p['topic'],publisher=p,frame=frame,wall_ns=time.time_ns(),mono_ns=time.monotonic_ns(),meta=meta,data=data,image=image)
            recorder.ingest(event);out=recorder.stop([])
            with Path(out['path']).open('rb') as f:
                messages=list(make_reader(f,validate_crcs=True).iter_messages())
            raw=next(m.data for _,c,m in messages if c.topic=='/raw/camera/head_color');self.assertEqual(raw,frame)
            image_data=next(m.data for _,c,m in messages if c.topic=='/camera/head_color/image');decoded=self.schemas.get('foxglove.CompressedImage').FromString(image_data)
            self.assertEqual(decoded.data,image)
            self.assertEqual(len(messages),3)
    def test_schema_bundle_self_contained(self):
        from google.protobuf import descriptor_pb2,descriptor_pool
        ds=descriptor_pb2.FileDescriptorSet.FromString((ROOT/'schemas.pb').read_bytes());pool=descriptor_pool.DescriptorPool()
        for f in ds.file:pool.Add(f)
        self.assertEqual(pool.FindMessageTypeByName('genie_msgs.msg.pb.EndState').name,'EndState')

if __name__=='__main__':unittest.main()
