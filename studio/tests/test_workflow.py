import json,sys,tempfile,time,unittest,io,tarfile
from pathlib import Path
from unittest.mock import patch
import workflow as w

class WorkflowTests(unittest.TestCase):
 def test_failed_start_has_readable_status_and_can_retry(self):
  with tempfile.TemporaryDirectory() as td,patch.object(w,'RUNTIME',Path(td)),patch.object(w,'BASE',Path(td)):
   j=w.Job('test')
   with self.assertRaises(ValueError):j.start(lambda f:['/missing/binary'])
   self.assertFalse(j.status()['running']);self.assertTrue(j.status()['error'])
   j.start(lambda f:[sys.executable,'-c','print("ok")']);j.process.wait(timeout=3)
   self.assertEqual(j.status()['phase'],'完成');self.assertIn('ok',j.status()['log'])
 def test_stop_terminates_worker(self):
  with tempfile.TemporaryDirectory() as td,patch.object(w,'RUNTIME',Path(td)),patch.object(w,'BASE',Path(td)):
   j=w.Job('test');j.start(lambda f:[sys.executable,'-c','import time;time.sleep(30)'])
   j.stop();j.process.wait(timeout=3)
   self.assertEqual(j.status()['phase'],'已停止')
 def test_last_conversion_result_and_progress_survive_web_restart(self):
  with tempfile.TemporaryDirectory() as td,patch.object(w,'RUNTIME',Path(td)):
   folder=Path(td)/'conversion_last';folder.mkdir()
   result={'completed':False,'dataset_path':'/data/任务A','total_episodes':0,'error':'missing source field'}
   (folder/'result.json').write_text(json.dumps(result));(folder/'progress.json').write_text('{"percent":60,"phase":"failed"}')
   state=w.Job('conversion',restore_latest=True).status()
   self.assertEqual(state['result'],result);self.assertEqual(state['progress']['percent'],60)
   self.assertFalse(state['running']);self.assertEqual(state['error'],'missing source field')
   self.assertIsNone(w.Job('lerobot').status()['result'])
 def test_directory_catalog_does_not_run_sdk(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'local/test/meta';p.mkdir(parents=True);(p/'info.json').write_text(json.dumps({'fps':30,'total_episodes':2}))
   self.assertEqual(w.datasets(td)['datasets'][0]['total_episodes'],2)
 def test_bad_repo_id_rejected_before_process(self):
  with tempfile.TemporaryDirectory() as td:
   with self.assertRaises(ValueError):w.convert({'source_dir':td,'output_dir':td,'repo_id':'../bad','task':'test'})
 def test_dataset_export_preserves_full_structure_and_bytes(self):
  from fastapi import FastAPI
  from fastapi.testclient import TestClient
  api=FastAPI();api.include_router(w.router);client=TestClient(api)
  with tempfile.TemporaryDirectory() as td:
   root=Path(td)/'训练集';expected={'meta/info.json':b'{}','data/chunk-000/episode_000000.parquet':b'PAR1-data','videos/chunk-000/camera/episode_000000.mp4':b'video-data'}
   for name,content in expected.items():
    p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(content)
   manifest=client.get('/api/lerobot/export/manifest',params={'dataset':str(root)}).json()
   self.assertEqual(manifest['total_bytes'],sum(map(len,expected.values())))
   self.assertEqual({e['path'] for e in manifest['files']},set(expected))
   for entry in manifest['files']:self.assertEqual(client.get(entry['url']).content,expected[entry['path']])
   archive=client.get('/api/lerobot/export/archive',params={'dataset':str(root)})
   self.assertEqual(int(archive.headers['content-length']),len(archive.content))
   with tarfile.open(fileobj=io.BytesIO(archive.content)) as tar:
    self.assertEqual(set(tar.getnames()),{'训练集/'+n for n in expected})
    for name,content in expected.items():self.assertEqual(tar.extractfile('训练集/'+name).read(),content)
class WorkflowDirectoryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.base=Path(self.tmp.name)
  self.patch=patch.object(w,'BASE',self.base);self.patch.start();self.addCleanup(self.patch.stop)
  from fastapi import FastAPI
  from fastapi.testclient import TestClient
  api=FastAPI();api.include_router(w.router);self.client=TestClient(api)
 def test_settings_creates_and_persists_shared_root(self):
  root=self.base/'训练 数据'
  response=self.client.post('/api/workflow/settings',json={'lerobot_root':str(root)})
  self.assertEqual(response.status_code,200)
  expected={'conversion_output':str(root),'lerobot_root':str(root)}
  self.assertEqual(response.json(),{'defaults':expected})
  self.assertTrue(root.is_dir())
  self.assertEqual(json.loads((self.base/'studio/config/workflow.json').read_text())['lerobot_root'],str(root))
  self.assertEqual(self.client.get('/api/workflow/state').json()['defaults'],expected)
  meta=root/'一个数据集/meta';meta.mkdir(parents=True);(meta/'info.json').write_text('{"fps":30,"total_episodes":1}')
  self.assertEqual(self.client.get('/api/lerobot/datasets').json()['datasets'][0]['path'],str(meta.parent))
 def test_default_is_datasets_not_previous_runtime(self):
  self.assertEqual(w.workflow_defaults()['lerobot_root'],str(self.base/'datasets'))
  with patch.object(w.conversion,'status',return_value={'result':{'dataset':str(self.base/'runtime/test')}}):
   self.assertEqual(w.state()['defaults']['conversion_output'],str(self.base/'datasets'))
 def test_settings_rejects_relative_directory_and_file(self):
  with self.assertRaises(ValueError):w.settings({'lerobot_root':'relative'})
  file=self.base/'file';file.write_text('x')
  with self.assertRaises(ValueError):w.settings({'lerobot_root':str(file)})
 def start_conversion(self,body):
  captured={}
  def start(command,paths,prepare):
   folder=self.base/'run';folder.mkdir(exist_ok=True)
   prepare(folder);captured['command']=command(folder)
   return {'running':True,'run_dir':str(folder)}
  with patch.object(w.conversion,'start',side_effect=start):
   captured['response']=self.client.post('/api/conversion/start',json=body)
  return captured
 def test_named_dataset_has_one_level_and_persists_output_root(self):
  root=self.base/'LeRobot 输出'
  result=self.start_conversion({'source_dir':str(self.base),'output_dir':str(root),'dataset_name':'拧瓶盖 第一组','task':'拧瓶盖'})
  self.assertEqual(result['response'].status_code,200)
  cmd=result['command']
  self.assertEqual(cmd[cmd.index('--dataset-path')+1],str(root/'拧瓶盖 第一组'))
  self.assertTrue(cmd[cmd.index('--repo-id')+1].startswith('local/'))
  self.assertEqual(w.workflow_defaults()['conversion_output'],str(root))
  self.assertEqual(w.workflow_defaults()['lerobot_root'],str(root))
  self.assertEqual(cmd[cmd.index('--progress')+1],str(self.base/'run/progress.json'))
 def test_resume_is_forwarded_only_when_requested(self):
  body={'source_dir':str(self.base),'dataset_name':'任务A','task':'test','resume':True}
  self.assertIn('--resume',self.start_conversion(body)['command'])
  body.pop('resume');self.assertNotIn('--resume',self.start_conversion(body)['command'])
 def test_result_folder_browser_stays_within_dataset_and_handles_failed_output(self):
  root=self.base/'empty_result';(root/'provenance').mkdir(parents=True)
  (root/'provenance/errors.jsonl').write_text('failure')
  response=self.client.get('/api/conversion/files',params={'dataset':str(root)}).json()
  self.assertTrue(response['exists']);self.assertIsNone(response['parent'])
  self.assertEqual(response['entries'][0],{'name':'provenance','path':'provenance','is_dir':True,'size':None})
  child=w.conversion_files(str(root),'provenance')
  self.assertEqual(child['parent'],'');self.assertEqual(child['entries'][0]['name'],'errors.jsonl')
  self.assertEqual(child['entries'][0]['size'],7)
  with self.assertRaises(ValueError):w.conversion_files(str(root),'..')
  missing=w.conversion_files(str(self.base/'not_created'))
  self.assertFalse(missing['exists']);self.assertEqual(missing['entries'],[])
 def test_conversion_uses_saved_root_and_legacy_repo_layout(self):
  root=self.base/'saved';w.settings({'lerobot_root':str(root)})
  result=self.start_conversion({'source_dir':str(self.base),'repo_id':'local/legacy','task':'test'})
  cmd=result['command'];self.assertNotIn('--dataset-path',cmd)
  self.assertEqual(cmd[cmd.index('--output-dir')+1],str(root))
  self.assertEqual(cmd[cmd.index('--repo-id')+1],'local/legacy')
 def test_named_hidden_dataset_is_discoverable_but_internal_staging_is_not(self):
  root=self.base/'datasets'
  for name in ('.task','.r2-transaction','.task.r2-initializing-abc/dataset'):
   meta=root/name/'meta';meta.mkdir(parents=True);(meta/'info.json').write_text('{}')
  self.assertEqual(w.dataset_folder_name('.task'),'.task')
  self.assertEqual([d['name'] for d in w.datasets(str(root))['datasets']],['.task'])
 def test_invalid_name_never_starts_process(self):
  with patch.object(w.conversion,'start') as start:
   for name in ('','..','../bad','a/b','a\\b','bad..name','\x00bad'):
    with self.subTest(name=name),self.assertRaises(ValueError):
     w.convert({'source_dir':str(self.base),'output_dir':str(self.base/'out'),'dataset_name':name,'task':'test'})
   start.assert_not_called()
 def test_failed_start_does_not_change_saved_root(self):
  old=self.base/'old';w.settings({'lerobot_root':str(old)})
  with patch.object(w.conversion,'start',side_effect=ValueError('start failed')):
   with self.assertRaises(ValueError):w.convert({'source_dir':str(self.base),'output_dir':str(self.base/'new'),'dataset_name':'new','task':'test'})
  self.assertEqual(w.workflow_defaults()['lerobot_root'],str(old))

if __name__=='__main__':unittest.main()
