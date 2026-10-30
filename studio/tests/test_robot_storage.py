import json,tempfile,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch,Mock
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse
import robot_storage,robot_recorder_agent,app,storage
class TransferTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.dest=self.root/'ubuntu';self.dest.mkdir()
  self.remote=self.root/'robot'/'episode_1_20261029_150000';self.remote.mkdir(parents=True)
  self.payload=b'raw-file-transfer-fixture'*12000
  (self.remote/'raw.mcap').write_bytes(self.payload)
  (self.remote/'raw.json').write_text(json.dumps({'file':self.remote.name,'record_kind':'collection','duration_s':3,'counts':{}}))
  self.ranges=[]
  ranges=self.ranges
  class Handler(robot_recorder_agent.Handler):
   def do_GET(self):
    if urlparse(self.path).path=='/file':ranges.append(self.headers.get('Range'))
    super().do_GET()
  self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
  self.server.agent=NS(status=lambda:{'active':False})
  self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
  self.patches=[patch.object(robot_storage,'ROOT',self.root),patch.object(robot_storage,'CONFIG',self.root/'none'),patch.object(robot_storage,'data_dir',return_value=self.dest)]
  for p in self.patches:p.start()
  self.client=robot_storage.RobotStorage();self.client.base='http://127.0.0.1:'+str(self.server.server_port)
 def tearDown(self):
  self.client.close();self.server.shutdown();self.server.server_close()
  for p in self.patches:p.stop()
  self.tmp.cleanup()
 def wait(self,key):
  for _ in range(100):
   job=self.client.jobs[key]
   if job['status']!='transferring':return job
   time.sleep(.01)
  self.fail('transfer timeout')
 def test_only_completed_files_are_transferred_without_reencoding(self):
  (self.dest/'episode_1_existing').mkdir()
  queued=self.client.queue_transfer(str(self.remote));done=self.wait(str(self.remote))
  self.assertEqual(done['status'],'complete',done)
  self.assertTrue(done['file'].startswith('episode_2_'))
  local=Path(done['destination'])
  self.assertEqual((local/'raw.mcap').read_bytes(),self.payload)
  metadata=json.loads((local/'raw.json').read_text())
  self.assertEqual(metadata['robot_source']['episode_dir'],str(self.remote))
  self.assertEqual(self.client.queue_transfer(str(self.remote))['file'],queued['file'])
 def test_transfer_can_resume_existing_partial_file(self):
  staging=self.dest/'.robot_import_test';staging.mkdir();(staging/'raw.mcap.download').write_bytes(self.payload[:4096])
  self.client.jobs[str(self.remote)]={'status':'failed','source':str(self.remote),'file':'episode_1_resume','destination':str(self.dest/'episode_1_resume'),'staging':str(staging)}
  self.client.queue_transfer(str(self.remote));done=self.wait(str(self.remote))
  self.assertEqual(done['status'],'complete',done);self.assertIn('bytes=4096-',self.ranges)
 def test_active_partial_cannot_be_taken_as_completed_episode(self):
  incomplete=self.root/'robot'/'episode_2';incomplete.mkdir();(incomplete/'raw.mcap.partial').write_bytes(b'partial')
  with self.assertRaisesRegex(ValueError,'尚未完成'):self.client.queue_transfer(str(incomplete))
 def test_ubuntu_shutdown_does_not_stop_robot_recording(self):
  self.client.cache={'active':True}
  with patch.object(self.client,'request') as request:
   self.client.close();request.assert_not_called()
 def test_ubuntu_live_callback_never_writes_raw_recording(self):
  recorder=Mock()
  with patch.object(app,'recorder',recorder),patch.object(app.limit_monitor,'observe') as observe:
   event={'topic':'preview-only'};app.on_event(event)
   recorder.ingest.assert_not_called();observe.assert_called_once_with(event)
  self.assertIsInstance(app.receiver,robot_storage.RemoteReceiver)
 def test_recorder_commands_have_no_stream_payload(self):
  with patch.object(self.client,'request',return_value={'active':True}) as request:
   self.client.start('task',[],{},topics=['/camera/head_color'])
   request.assert_called_once_with('/start',{'label':'task','topics':['/camera/head_color'],'context':{}})
 def test_hidden_incomplete_imports_are_not_collection_rows(self):
  folder=self.dest/'.robot_import_pending';folder.mkdir();(folder/'raw.mcap').write_bytes(b'partial')
  with patch.object(storage,'data_dir',return_value=self.dest):self.assertEqual(storage.list_files(),[])
