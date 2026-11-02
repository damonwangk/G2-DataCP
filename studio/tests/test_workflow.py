# 使用说明
# 用途：平台离线回归测试：workflow。
# 用法：在studio目录执行 python -m unittest tests.test_workflow（已配置依赖的Python）；使用模拟SDK/API，不运行真机任务。
# 详细说明：../README.md

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
 def test_restart_restores_latest_interrupted_job_instead_of_old_success(self):
  import os
  with tempfile.TemporaryDirectory() as td,patch.object(w,'RUNTIME',Path(td)):
   older=Path(td)/'conversion_old';older.mkdir();old=older/'result.json';old.write_text('{"completed":true}');os.utime(old,(100,100))
   newer=Path(td)/'conversion_new';newer.mkdir()
   (newer/'command.json').write_text(json.dumps(['python','platform_convert.py','--dataset-path','/data/test1','--repo-id','local/test1','--task','move']))
   progress=newer/'progress.json';progress.write_text('{"percent":56.11,"processed_frames":7812,"total_frames":16650}');os.utime(progress,(200,200));os.utime(newer/'command.json',(200,200))
   state=w.Job('conversion',restore_latest=True).status()
   self.assertEqual(state['run_dir'],str(newer));self.assertEqual(state['phase'],'已中断')
   self.assertEqual(state['dataset_path'],'/data/test1');self.assertEqual(state['progress']['processed_frames'],7812)
   self.assertIsNone(state['result']);self.assertIn('中断',state['error'])
 def test_stopped_job_is_restored_as_stopped_after_restart(self):
  with tempfile.TemporaryDirectory() as td,patch.object(w,'RUNTIME',Path(td)),patch.object(w,'BASE',Path(td)):
   first=w.Job('test');first.start(lambda f:[sys.executable,'-c','import time;time.sleep(30)'])
   first.stop();first.process.wait(timeout=3)
   later=w.Job('test',restore_latest=True).status()
   self.assertEqual(later['phase'],'已停止');self.assertFalse(later['running']);self.assertEqual(later['error'],'')
 def test_empty_dataset_explains_interrupted_source_and_has_resume_arguments(self):
  from fastapi import FastAPI
  from fastapi.testclient import TestClient
  api=FastAPI();api.include_router(w.router);client=TestClient(api)
  with tempfile.TemporaryDirectory() as td,patch.object(w,'RUNTIME',Path(td)),patch.object(w.conversion,'status',return_value={'running':False}):
   folder=Path(td)/'conversion_old';folder.mkdir()
   repo=w.internal_repo_id('test1')
   (folder/'command.json').write_text(json.dumps(['python','platform_convert.py','--dataset-path','/data/train/test1','--repo-id',repo,'--task','11','--input-format','factory']))
   (folder/'inputs.json').write_text('["/data/record/original"]')
   (folder/'progress.json').write_text('{"processed_frames":7812,"total_frames":16650,"percent":56.11}')
   with patch.object(w,'info',return_value={'episodes':[],'total_episodes':0}):result=client.get('/api/lerobot/episodes',params={'dataset':'/data/train/test1'}).json()
   status=result['conversion_status'];self.assertEqual(status['phase'],'已中断');self.assertEqual(status['progress']['processed_frames'],7812)
   self.assertEqual(status['resume_request'],{'episode_paths':['/data/record/original'],'task':'11','input_format':'factory','workers':2,'threads':2,'resume':True,'output_dir':'/data/train','dataset_name':'test1'})
 def test_directory_catalog_does_not_run_sdk(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'local/test/meta';p.mkdir(parents=True);(p/'info.json').write_text(json.dumps({'fps':30,'total_episodes':2}))
   self.assertEqual(w.datasets(td)['datasets'][0]['total_episodes'],2)
 def test_catalog_ignores_hidden_parallel_workspaces(self):
  with tempfile.TemporaryDirectory() as td:
   for name in ['finished','.unfinished.parallel-temp/0/dataset']:
    p=Path(td)/name/'meta';p.mkdir(parents=True);(p/'info.json').write_text('{"total_episodes":1,"fps":30}')
   self.assertEqual([row['name'] for row in w.datasets(td)['datasets']],['finished'])
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
 def test_factory_format_passes_to_batch_worker(self):
  command=self.start_conversion({'source_dir':str(self.base),'dataset_name':'native','task':'移动','input_format':'factory'})['command']
  self.assertEqual(command[command.index('--input-format')+1],'factory')
  self.assertEqual(command[command.index('--workers')+1],'2')
  self.assertEqual(command[command.index('--threads')+1],'2')
 def test_native_replay_only_starts_on_explicit_endpoint(self):
  folder=self.base/'native/record';folder.mkdir(parents=True);(folder/'-genie-bundle_data.pbdat').write_bytes(b'raw')
  captured={}
  def start(command,paths):captured['command']=command(self.base);return {'running':True}
  with patch.object(w.replay,'start',side_effect=start),patch.object(w,'motion_available'):
   response=self.client.post('/api/native/replay/start',json={'source':str(folder.parent)})
  self.assertEqual(response.status_code,200);self.assertIn('--native-recording',captured['command'])
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
