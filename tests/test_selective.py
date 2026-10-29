import json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from tests.test_record_filter import DatasetSelection
import storage,robot_replay,sdk_worker,app
from frequency import RateMeter
from selection import motion_inventory
from postures import JOINT_NAMES,GRIP_NAMES
from fastapi.testclient import TestClient

class Selective(unittest.TestCase):
 def setUp(self):
  self.fixture=DatasetSelection();self.fixture.setUp();self.schemas=self.fixture.schemas
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.directory=patch.object(storage,'data_dir',return_value=self.root);self.directory.start()
 def tearDown(self):self.directory.stop();self.tmp.cleanup()
 def record(self,topics,events):
  recorder=storage.Recorder(self.schemas);recorder.start('test',[],topics=topics)
  for e in events:recorder.ingest(e)
  return recorder,recorder.stop([])
 def test_camera_only_raw_bytes_and_preview_needs_no_joints(self):
  camera=self.fixture.event('/camera/head_color','CosineDataPacket',1_000_000_000)
  _,out=self.record(['/camera/head_color'],[camera,self.fixture.joint(1_000_000_000),self.fixture.grip(0,1_000_000_000)])
  events=list(storage.raw_events(out['path'],self.schemas))
  self.assertEqual(len(events),1);self.assertEqual(events[0]['frame'],camera['frame'])
  self.assertEqual(out['selected_topics'],['/camera/head_color'])
  inventory=motion_inventory(out['path'],self.schemas)
  self.assertEqual(inventory['motion_names'],[])
  preview=storage.Playback(self.schemas);preview.load(out['file'],['/camera/head_color'])
  self.assertEqual(set(preview.latest),{'/camera/head_color'})
 def test_joint_only_file_and_individual_joint_plan(self):
  _,out=self.record(['/hal/joint_state'],[self.fixture.joint(1_000_000_000),self.fixture.joint(1_100_000_000)])
  names=[JOINT_NAMES[0],JOINT_NAMES[8]]
  t=robot_replay.Trajectory(out['path'],self.schemas,controlled_names=names)
  self.assertEqual(t.names,names);self.assertEqual(t.at(.05),[.1,.1]);self.assertEqual(t.joint_count,2)
  latest={'/hal/joint_state':self.fixture.joint(2_000_000_000)}
  p=robot_replay.plan(out['path'],self.schemas,latest=latest,controlled_names=names)
  self.assertTrue(p['ready']);self.assertEqual(p['gripper_types'],{})
 def test_single_gripper_single_sample_needs_no_full_body(self):
  _,out=self.record(['/hal/right_ee_data'],[self.fixture.grip(1,1_000_000_000)])
  t=robot_replay.Trajectory(out['path'],self.schemas,controlled_names=[GRIP_NAMES[1]])
  self.assertEqual(t.names,[GRIP_NAMES[1]]);self.assertEqual(t.joint_count,0);self.assertGreater(t.duration,0)
  p=robot_replay.plan(out['path'],self.schemas,latest={'/hal/right_ee_data':self.fixture.grip(1,2_000_000_000)},controlled_names=[GRIP_NAMES[1]])
  self.assertTrue(p['ready'])
 def test_default_replays_available_parts_and_missing_unselected_data_is_optional(self):
  _,out=self.record(['/hal/joint_state'],[self.fixture.joint(1_000_000_000)])
  t=robot_replay.Trajectory(out['path'],self.schemas)
  self.assertEqual(t.names,JOINT_NAMES)
  t=robot_replay.Trajectory(out['path'],self.schemas,controlled_names=[JOINT_NAMES[0],GRIP_NAMES[1]])
  self.assertEqual(t.names,[JOINT_NAMES[0]]);self.assertEqual(t.missing_names,[GRIP_NAMES[1]])
 def test_preview_can_select_one_topic_from_mixed_file(self):
  _,out=self.record(None,[self.fixture.joint(1_000_000_000),self.fixture.grip(0,1_100_000_000)])
  preview=storage.Playback(self.schemas);preview.load(out['file'],['/hal/left_ee_data'])
  self.assertEqual(set(preview.latest),{'/hal/left_ee_data'})
  self.assertEqual(motion_inventory(out['path'],self.schemas)['motion_names'],JOINT_NAMES+[GRIP_NAMES[0]])
 def test_recorder_rate_counts_saved_topics_only(self):
  events=[self.fixture.joint(1_000_000_000),self.fixture.joint(1_010_000_000)]
  events[0]['mono_ns']=int(time.monotonic()*1e9);events[1]['mono_ns']=events[0]['mono_ns']+10_000_000
  r=storage.Recorder(self.schemas);r.start('test',[],topics=['/hal/joint_state'])
  for e in events:r.ingest(e)
  r.ingest(self.fixture.grip(0,1_010_000_000));status=r.status();r.stop([])
  self.assertEqual(status['counts'],{'/hal/joint_state':2});self.assertAlmostEqual(status['hz']['/hal/joint_state'],100,places=2)
 def test_api_propagates_selection(self):
  client=TestClient(app.app)
  with patch.object(app,'robot_job',None),patch.object(app,'recorder') as rec:
   rec.start.return_value={'active':True}
   response=client.post('/api/record/start',json={'topics':['/camera/head_color']})
   self.assertEqual(response.status_code,200);self.assertEqual(rec.start.call_args.kwargs['topics'],['/camera/head_color'])
  with patch.object(app,'robot_job',None),patch.object(app,'recorder',NS(active=False,refresh=lambda:None)),patch.object(app,'file_path',return_value=Path('fake.mcap')),patch.object(robot_replay,'RobotJob') as job:
   job.return_value.start.return_value={'running':True}
   response=client.post('/api/replay/start',json={'file':'fake','confirm_motion':True,'controlled_names':[JOINT_NAMES[0]]})
   self.assertEqual(response.status_code,200);self.assertEqual(job.return_value.start.call_args.kwargs['controlled_names'],[JOINT_NAMES[0]])
 def test_fake_sdk_receives_only_selected_joint_or_gripper(self):
  for name,topic,event in [(JOINT_NAMES[0],'/hal/joint_state',self.fixture.joint(1_000_000_000)),(GRIP_NAMES[0],'/hal/left_ee_data',self.fixture.grip(0,1_000_000_000))]:
   with self.subTest(name=name):
    _,out=self.record([topic],[event])
    sent=[];q=[0.]
    class Robot:
     def set_control_mode(self,m):return 0
     def get_joint_states(self):
      if topic!='/hal/joint_state':raise AssertionError('Unselected joint feedback requested')
      return {'timestamp':1,'states':[{'name':name,'motor_position':q[0]}]}
     def get_end_state(self):
      if topic=='/hal/joint_state':raise AssertionError('Unselected gripper feedback requested')
      return {'left_end_state':{'names':[name],'end_states':[{'position':q[0]}]}}
     def joint_servo_control(self,req,low):
      sent.append((list(req.joint_names),list(req.joint_positions)));q[:]=req.joint_positions;return 0
    sdk=NS(Robot=Robot,MotionControlMode=NS,JointServoControlReq=NS,InputSource=NS(INPUT_GDK=6),Target=NS(TARGET_ARMS=0),ControlMode=NS(CTRL_JOINT_POSITION=1),SafeMode=NS(SAFE_NORMAL=0),GDKRes=NS(kSuccess=0),gdk_init=lambda:0,gdk_release=lambda:0)
    class Clock:
     t=0.
     def now(self):self.t+=.01;return self.t
    folder=self.root/('run'+name);clock=Clock()
    args=NS(run=str(folder),file=out['path'],start=0,duration=0,speed=1,trace=str(self.root/(name+'.mcap')),controlled_names=[name])
    with patch.dict('sys.modules',{'agibot_gdk':sdk}),patch.object(sdk_worker,'release_teleop',return_value={'result':True}),patch.object(sdk_worker.time,'monotonic',clock.now),patch.object(sdk_worker.time,'sleep'),patch.object(sdk_worker.signal,'signal'):
     self.assertEqual(sdk_worker.run(args),0)
    report=json.loads((folder/'result.json').read_text())
    self.assertTrue(report['completed']);self.assertTrue(sent);self.assertTrue(all(names==[name] for names,_ in sent))
    self.assertGreater(report['send_average_hz'],0);self.assertEqual(report['controlled_names'],[name])

class Rates(unittest.TestCase):
 def test_sdk_send_rate_distinguishes_success_and_observed_window(self):
  sent=RateMeter();accepted=RateMeter()
  for i in range(301):
   sent.add(i*.01)
   if i%2==0:accepted.add(i*.01)
  self.assertAlmostEqual(sent.hz(3),100);self.assertAlmostEqual(accepted.hz(3),50)
  self.assertEqual(sent.hz(7),0)
  sent=RateMeter();accepted=RateMeter()
  for i in range(301):
   sent.add(i*.01)
   if i<2:accepted.add(i*.01)
  self.assertLess(accepted.share_hz(sent,3),1)
