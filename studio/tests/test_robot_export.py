import io,json,tempfile,tarfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from fastapi.testclient import TestClient
import app,exports,storage,local_storage

class RobotExportTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.patches=[patch.object(storage,'data_dir',return_value=self.root),patch.object(exports,'data_dir',return_value=self.root)]
  for p in self.patches:p.start()
  self.client=TestClient(app.app);self.name='episode_1_20261029_143000';self.folder=self.root/self.name;self.folder.mkdir()
  self.payload=b'original-mcap-fixture\x00\xff'*10000
  (self.folder/'raw.mcap').write_bytes(self.payload)
  (self.folder/'raw.json').write_text('{"source":"untouched"}')
  (self.folder/'replay_trace.mcap').write_bytes(b'trace')
  (self.folder/'unfinished.mcap.partial').write_bytes(b'partial')
 def tearDown(self):
  for p in self.patches:p.stop()
  exports.exports.clear();self.tmp.cleanup()
 def prepare(self):
  r=self.client.post('/api/export/prepare',json={'files':[self.name]});self.assertEqual(r.status_code,200,r.text);return r.json()
 def test_folder_download_preserves_original_file_names_and_bytes(self):
  m=self.prepare();entries={e['relative_path']:e for e in m['files']}
  self.assertEqual(len(entries),3)
  r=self.client.get(entries[self.name+'/raw.mcap']['url']);self.assertEqual(r.content,self.payload)
  self.assertEqual(self.client.get(entries[self.name+'/raw.json']['url']).content,b'{"source":"untouched"}')
 def test_tar_stream_contains_same_bytes_and_folder_structure(self):
  m=self.prepare();r=self.client.get(m['archive_url']);self.assertEqual(r.status_code,200)
  self.assertEqual(int(r.headers['content-length']),len(r.content))
  with tarfile.open(fileobj=io.BytesIO(r.content)) as t:
   self.assertEqual(t.extractfile(self.name+'/raw.mcap').read(),self.payload)
   self.assertNotIn(self.name+'/unfinished.mcap.partial',t.getnames())
  self.assertEqual((self.folder/'raw.mcap').read_bytes(),self.payload)
 def test_multiple_selected_episodes_and_unicode(self):
  n='episode_2_采集时间';p=self.root/n;p.mkdir();(p/'raw.mcap').write_bytes(b'two')
  m=self.client.post('/api/export/prepare',json={'files':[self.name,n,self.name]}).json()
  with tarfile.open(fileobj=io.BytesIO(self.client.get(m['archive_url']).content)) as t:
   self.assertEqual(len(t.getnames()),4);self.assertEqual(t.extractfile(n+'/raw.mcap').read(),b'two')
 def test_unfinished_collection_is_not_exported(self):
  (self.folder/'raw.mcap').rename(self.folder/'raw.mcap.partial')
  r=self.client.post('/api/export/prepare',json={'files':[self.name]});self.assertEqual(r.status_code,400)
 def test_empty_and_outside_path_not_exported(self):
  for names in [[],['../outside.mcap']]:
   self.assertEqual(self.client.post('/api/export/prepare',json={'files':names}).status_code,400)
 def test_server_and_client_not_bound_to_ubuntu(self):
  r=self.client.get('/api/export/client').json();self.assertEqual(r['server'],'robot');self.assertEqual(r['destination'],'current_browser_computer')
 def test_local_stop_never_transfers_data(self):
  client=local_storage.LocalStorage()
  with patch.object(client,'request',return_value={'file':self.name,'episode_dir':str(self.folder)}),patch.object(client,'refresh'),patch.object(client,'queue_transfer') as transfer:
   r=client.stop();transfer.assert_not_called();self.assertFalse(r['transferring'])
 def test_motion_trace_uses_robot_episode_folder(self):
  client=local_storage.LocalStorage()
  with patch.object(client,'request',return_value={'active':True,'path':str(self.folder/'raw.mcap')}):
   client.start('feedback',context={'kind':'replay'})
  self.assertEqual(client.directory,self.folder)
 def test_closing_web_keeps_recorder_independent(self):
  client=local_storage.LocalStorage()
  with patch.object(client,'request') as request:client.close();request.assert_not_called()
 def test_robot_directory_selection_updates_current_view(self):
  with patch.object(app,'recorder') as rec,patch.object(app,'robot_job',None),patch.object(app.library,'include_capture_directory'):
   rec.active=False;rec.select_directory.return_value={'data_dir':'/data/chosen'}
   r=self.client.post('/api/robot-storage/directory',json={'path':'/data/chosen'})
   self.assertEqual(r.status_code,200);rec.select_directory.assert_called_once_with('/data/chosen',False)
