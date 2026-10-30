import copy
import json
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import patch
from mcap.reader import make_reader
from native import ROOT,Schemas,decode
from storage import Recorder,raw_events
from record_filter import POLICY
from pipeline import align
from postures import JOINT_NAMES,GRIP_NAMES
from data_contract import contract
import storage

class DatasetSelection(unittest.TestCase):
    def setUp(self):
        self.schemas=Schemas();self.camera=(ROOT/'evidence/camera_head_color_cosine_runner.bin').read_bytes()
    def event(self,topic,typ,stamp,seq=0,message=None):
        header=bytearray(self.camera[:128])
        if message is None:
            frame=bytearray(self.camera);struct.pack_into('<Q',frame,144,stamp)
        else:
            message.header.stamp.sec=stamp//10**9;message.header.stamp.nanosec=stamp%10**9
            payload=message.SerializeToString();frame=header+payload
            struct.pack_into('<I',frame,0,len(frame));struct.pack_into('<I',frame,8,len(frame)-24);struct.pack_into('<I',frame,88,len(payload))
        struct.pack_into('<Q',frame,48,stamp);struct.pack_into('<Q',frame,96,stamp);struct.pack_into('<Q',frame,80,seq)
        frame=bytes(frame);meta,data,image=decode(frame,typ,self.schemas)
        return {'topic':topic,'publisher':{'uid':topic,'topic':topic,'type':typ},'frame':frame,'meta':meta,'data':data,'image':image,
                'wall_ns':1_800_000_000_000_000_000+stamp,'mono_ns':time.monotonic_ns()}
    def joint(self,stamp,seq=0):
        m=self.schemas.get('genie_msgs.msg.pb.JointState')();m.name.extend(JOINT_NAMES);m.motor_position.extend([.1]*22);m.motor_velocity.extend([.2]*22);m.temperature.extend([42.5]*14)
        return self.event('/hal/joint_state',m.DESCRIPTOR.full_name,stamp,seq,m)
    def grip(self,side,stamp,seq=0):
        m=self.schemas.get('genie_msgs.msg.pb.EndState')();m.name.append(GRIP_NAMES[side]);m.type=1;m.controlled=True
        motor=m.end_state.add();motor.position=.019375;motor.temperature=33.25;motor.enable=True
        return self.event(['/hal/left_ee_data','/hal/right_ee_data'][side],m.DESCRIPTOR.full_name,stamp,seq,m)
    def test_subscribed_packets_and_temperature_are_stored_without_processing(self):
        stamp=1_000_000_000;joint=self.joint(stamp);grip=self.grip(0,stamp)
        camera=self.event('/camera/head_color','CosineDataPacket',stamp)
        depth=dict(camera,topic='/camera/head_depth')
        with tempfile.TemporaryDirectory() as tmp,patch.object(storage,'data_dir',return_value=Path(tmp)):
            r=Recorder(self.schemas);r.start('selection',[])
            for e in [camera,depth,joint,grip]:r.ingest(e)
            out=r.stop([]);path=Path(out['path'])
            self.assertEqual(out['selection_policy'],POLICY)
            self.assertNotIn('/camera/head_depth',out['counts'])
            with path.open('rb') as f:
                reader=make_reader(f,validate_crcs=True);messages=list(reader.iter_messages())
                self.assertTrue(all('head_depth' not in c.topic for _,c,_ in messages))
                self.assertIn('selection_policy.json',[a.name for a in reader.iter_attachments()])
            for _,c,m in messages:
                if c.topic=='/hal/joint_state':
                    pb=self.schemas.get('genie_msgs.msg.pb.JointState').FromString(m.data)
                    self.assertEqual(list(pb.temperature),[42.5]*14);self.assertEqual(list(pb.motor_position),[.1]*22)
                if c.topic=='/hal/left_ee_data':
                    pb=self.schemas.get('genie_msgs.msg.pb.EndState').FromString(m.data)
                    self.assertEqual(pb.end_state[0].temperature,33.25);self.assertEqual(pb.end_state[0].position,.019375)
                if c.topic=='/raw/camera/head_color':self.assertEqual(m.data,camera['frame'])
            events={e['topic']:e for e in raw_events(path,self.schemas)}
            self.assertEqual(events['/hal/joint_state']['frame'],joint['frame'])
            self.assertEqual(events['/hal/left_ee_data']['frame'],grip['frame'])
            self.assertEqual(events['/hal/joint_state']['meta']['source_ns'],str(stamp))
            # Filtering must not mutate receiver caches or the original incoming bytes.
            self.assertEqual(joint['data']['temperature'],[42.5]*14)
            self.assertEqual(grip['data']['end_state'][0]['temperature'],33.25)
    def test_collection_does_not_require_online_topics(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(storage,'data_dir',return_value=Path(tmp)):
            recorder=Recorder(self.schemas)
            recorder.start('raw',[{'topic':'/hal/joint_state','required':True,'online':False}])
            self.assertTrue(recorder.active);recorder.stop([])
    def test_training_contract_uses_actual_types_and_omits_diagnostics(self):
        c=contract(self.schemas);j={x['name']:x for x in c['joint_fields']};g={x['name']:x for x in c['gripper_fields']}
        self.assertEqual(j['motor_position']['type'],'float64');self.assertTrue(j['motor_position']['repeated'])
        self.assertEqual(g['position']['type'],'float64');self.assertEqual(g['enable']['type'],'bool')
        self.assertNotIn('temperature',g);self.assertNotIn('voltage',g);self.assertNotIn('temperature',j)

if __name__=='__main__':unittest.main()
