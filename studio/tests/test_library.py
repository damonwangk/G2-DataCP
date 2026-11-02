# 使用说明
# 用途：平台离线回归测试：library。
# 用法：在studio目录执行 python -m unittest tests.test_library（已配置依赖的Python）；使用模拟SDK/API，不运行真机任务。
# 详细说明：../README.md

import io,json,tempfile,tarfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from fastapi.testclient import TestClient
import app,exports,library,storage,robot_recorder_agent
from native import Schemas

class LibraryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.root=self.base/'data';self.root.mkdir()
  self.patches=[patch.object(library,'SETTINGS',self.base/'library.json'),patch.object(storage,'SETTINGS',self.base/'workspace.json'),patch.object(storage,'DATA',self.root),patch.object(app,'recorder',NS(refresh=lambda:{'active':False})),patch.object(app,'robot_job',None)]
  for p in self.patches:p.start()
  library.select_root(str(self.root));storage.select_directory(str(self.root))
  self.client=TestClient(app.app)
 def tearDown(self):
  app.playback.stop();app.playback.file=None;app.playback.latest={};exports.exports.clear()
  for p in reversed(self.patches):p.stop()
  self.tmp.cleanup()
 def episode(self,group,name='episode_1_time',payload=b'original bytes'):
  p=self.root/group/name;p.mkdir(parents=True)
  (p/'raw.mcap').write_bytes(payload);(p/'raw.json').write_text('{"record_kind":"collection","duration_s":1}')
  return p
 def test_tree_groups_folders_and_excludes_incomplete_and_hidden(self):
  a=self.episode('A');b=self.episode('B/sub');self.episode('.old_tests')
  p=self.root/'A'/'episode_2_writing';p.mkdir();(p/'raw.mcap.partial').write_bytes(b'partial')
  out=self.client.get('/api/library/tree').json();self.assertEqual(out['root'],str(self.root))
  self.assertEqual(out['tree']['count'],2);self.assertEqual([g['name'] for g in out['tree']['groups']],['A','B'])
  self.assertEqual(out['tree']['groups'][0]['episodes'][0]['id'],str(a))
  self.assertEqual(out['tree']['groups'][1]['groups'][0]['episodes'][0]['id'],str(b))
 def test_conversion_tree_scope_does_not_change_export_or_capture_directory(self):
  a=self.episode('A');b=self.episode('B')
  export_before=library.SETTINGS.read_bytes();capture_before=storage.SETTINGS.read_bytes()
  out=self.client.get('/api/library/tree',params={'path':str(a.parent)}).json()
  self.assertEqual(out['root'],str(a.parent));self.assertEqual(out['tree']['count'],1)
  self.assertEqual(out['tree']['episodes'][0]['id'],str(a))
  self.assertEqual(library.SETTINGS.read_bytes(),export_before);self.assertEqual(storage.SETTINGS.read_bytes(),capture_before)
  self.assertEqual(self.client.get('/api/library/tree').json()['tree']['count'],2)
  self.assertTrue((b/'raw.mcap').exists())
 def test_conversion_tree_reports_invalid_directory(self):
  for path in ['relative/path',str(self.base/'missing')]:
   result=self.client.get('/api/library/tree',params={'path':path})
   self.assertEqual(result.status_code,400)
 def test_factory_tree_selects_recordings_recursively_without_changing_roots(self):
  self.episode('mcap')
  for relative in ['factory_a','nested/factory_b']:
   bundle=self.root/relative/'record/-genie-bundle_data.pbdat';bundle.parent.mkdir(parents=True);bundle.write_bytes(b'bundle')
  before=library.SETTINGS.read_bytes()
  out=self.client.get('/api/library/tree',params={'path':str(self.root),'input_format':'factory'}).json()
  self.assertEqual(out['tree']['count'],2)
  self.assertEqual(out['tree']['episodes'][0]['file'],'factory_a')
  self.assertEqual(out['tree']['groups'][0]['episodes'][0]['input_format'],'factory')
  direct=library.tree(str(self.root/'factory_a'),'factory')
  self.assertEqual(direct['tree']['count'],1)
  self.assertEqual(library.SETTINGS.read_bytes(),before)
  self.assertEqual(library.tree(str(self.root))['tree']['count'],1)
 def test_native_export_preserves_all_original_files_and_structure(self):
  source=self.root/'native';files={'record/-genie-bundle_data.pbdat':b'original bundle','camera/head_color/head_color.h265':b'original h265','camera/head_color/head_color.txt':b'1 I\n','meta_info.json':b'{}','record/other_sensor.pbdat':b'other raw data'}
  for relative,data in files.items():
   p=source/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
  response=self.client.post('/api/export/prepare',json={'episode_paths':[str(source)],'input_format':'factory','root':str(self.root)})
  self.assertEqual(response.status_code,200,response.text)
  archive=self.client.get(response.json()['archive_url'])
  with tarfile.open(fileobj=io.BytesIO(archive.content)) as t:
   for relative,data in files.items():self.assertEqual(t.extractfile('data/native/'+relative).read(),data)
  self.assertTrue((source/'meta_info.json').exists())
 def test_export_same_episode_names_in_different_folders(self):
  a=self.episode('A',payload=b'A');b=self.episode('B',payload=b'B')
  m=self.client.post('/api/export/prepare',json={'episode_paths':[str(a),str(b)]}).json()
  r=self.client.get(m['archive_url'])
  with tarfile.open(fileobj=io.BytesIO(r.content)) as t:
   self.assertEqual(t.extractfile('data/A/episode_1_time/raw.mcap').read(),b'A')
   self.assertEqual(t.extractfile('data/B/episode_1_time/raw.mcap').read(),b'B')
  self.assertEqual(self.client.get('/api/library/tree').json()['tree']['count'],2)
 def test_root_and_same_named_child_have_distinct_export_paths(self):
  a=self.episode('',payload=b'root');b=self.episode('data',payload=b'child')
  m=self.client.post('/api/export/prepare',json={'episode_paths':[str(a),str(b)]}).json()
  paths=[f['relative_path'] for f in m['files']]
  self.assertEqual(len(paths),len(set(paths)))
  self.assertIn('data/episode_1_time/raw.mcap',paths);self.assertIn('data/data/episode_1_time/raw.mcap',paths)
 def test_selected_root_folder_name_is_preserved_in_export(self):
  a=self.episode('A');self.client.post('/api/library/root',json={'path':str(a.parent)})
  m=self.client.post('/api/export/prepare',json={'episode_paths':[str(a)]}).json()
  self.assertTrue(all(f['relative_path'].startswith('A/episode_1_time/') for f in m['files']))
 def test_library_root_does_not_change_capture_directory(self):
  a=self.root/'A';a.mkdir();b=self.root/'B';b.mkdir();storage.select_directory(str(a))
  self.client.post('/api/library/root',json={'path':str(b)})
  self.assertEqual(storage.data_dir(),a);self.assertEqual(library.library_root(),b)
 def test_delete_only_selected_episode_and_keep_capture_folder(self):
  a=self.episode('A');b=self.episode('A','episode_2_time');(a/'notes.json').write_text('{}')
  total=library.total_bytes(a)
  r=self.client.post('/api/library/delete',json={'episode_paths':[str(a)],'confirm':True})
  self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['freed_bytes'],total)
  self.assertFalse(a.exists());self.assertTrue(b.exists());self.assertTrue(a.parent.exists())
 def test_delete_custom_record_without_counter_preserves_sequence(self):
  p=self.episode('copied','122episode_8_20260314_005050')
  self.assertFalse((p.parent/'.episode_sequence.json').exists())
  library.delete_episodes([p])
  self.assertEqual(storage.episode_number(p.parent),8)
 def test_deletion_requires_confirmation_and_rejects_parent_outside_symlink_partial(self):
  a=self.episode('A');outside=self.base/'outside';outside.mkdir();(outside/'raw.mcap').write_bytes(b'keep')
  link=self.root/'linked';link.symlink_to(outside,target_is_directory=True)
  partial=self.root/'incomplete';partial.mkdir();(partial/'raw.mcap.partial').write_bytes(b'keep')
  self.assertEqual(self.client.post('/api/library/delete',json={'episode_paths':[str(a)]}).status_code,400)
  for p in [self.root,a.parent,a/'raw.mcap',outside,link,partial]:
   self.assertEqual(self.client.post('/api/library/delete',json={'episode_paths':[str(p)],'confirm':True}).status_code,400)
  self.assertTrue(a.exists());self.assertTrue((outside/'raw.mcap').exists())
 def test_deleting_last_episode_does_not_reuse_sequence(self):
  a=self.episode('A','episode_2_time');storage.select_directory(str(a.parent))
  self.client.post('/api/library/delete',json={'episode_paths':[str(a)],'confirm':True})
  recorder=storage.Recorder(Schemas());recorder.start('test',[],topics=['/hal/joint_state']);out=recorder.stop([])
  self.assertTrue(out['file'].startswith('test_3_'));self.assertEqual(Path(out['path']).parent.parent,a.parent)
 def test_deleting_current_preview_unloads_it_and_invalidates_prepared_export(self):
  a=self.episode('A');app.playback.file=a.name;app.playback.path=a/'raw.mcap'
  m=self.client.post('/api/export/prepare',json={'episode_paths':[str(a)]}).json()
  r=self.client.post('/api/library/delete',json={'episode_paths':[str(a)],'confirm':True})
  self.assertEqual(r.status_code,200);self.assertIsNone(app.playback.file)
  self.assertEqual(self.client.get(m['archive_url']).status_code,404)
 def test_source_used_by_replay_is_not_deleted(self):
  a=self.episode('A');storage.select_directory(str(a.parent))
  with patch.object(app,'robot_job',NS(kind='replay',file=a.name,running=True,preparing=False)):
   r=self.client.post('/api/library/delete',json={'episode_paths':[str(a)],'confirm':True})
  self.assertEqual(r.status_code,400);self.assertTrue(a.exists())
 def test_robot_next_saves_old_then_opens_next_in_same_folder(self):
  a=self.root/'A';a.mkdir();storage.select_directory(str(a))
  agent=robot_recorder_agent.Agent.__new__(robot_recorder_agent.Agent)
  agent.lock=threading.RLock();agent.recorder=storage.Recorder(Schemas());agent.receiver=NS(stats=lambda:[],latest={});agent.last_path=self.base/'last.json';agent.limit_monitor=NS(status=lambda latest:{})
  agent.start({'label':'continuous','topics':['/hal/joint_state']})
  result=agent.next_episode({});self.assertTrue(Path(result['saved']['path']).exists())
  self.assertTrue(result['saved']['file'].startswith('continuous_1_'));self.assertTrue(result['recording']['file'].startswith('continuous_2_'))
  self.assertEqual(result['recording']['selected_topics'],['/hal/joint_state']);agent.stop()
  agent.start({'label':'continuous','topics':['/hal/joint_state']});last=agent.stop()
  self.assertTrue(last['file'].startswith('continuous_3_'));self.assertEqual(storage.data_dir(),a)
