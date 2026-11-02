# 使用说明
# 用途：平台离线回归测试：conversion_resume。
# 用法：在studio目录执行 python -m unittest tests.test_conversion_resume（已配置依赖的Python）；使用模拟SDK/API，不运行真机任务。
# 详细说明：../README.md

"""复现系统时钟倒退、同名数据集不同来源及没有最终结果时的转换恢复错误。"""
import hashlib,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import workflow as w

class ResumeIdentityTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.patch=patch.object(w,'RUNTIME',self.root);self.patch.start();self.addCleanup(self.patch.stop)
  self.target=str(self.root/'datasets/full')
 def job(self,name,inputs,mtime):
  folder=self.root/name;folder.mkdir()
  command=['python','platform_convert.py','--dataset-path',self.target,'--repo-id',w.internal_repo_id('full'),'--task','pack the phone boxes','--input-format','factory']
  (folder/'command.json').write_text(json.dumps(command));(folder/'inputs.json').write_text(json.dumps(inputs));(folder/'progress.json').write_text(json.dumps({'total_sources':146 if inputs==['/data/record'] else 1,'percent':93.83}))
  for p in folder.iterdir():os.utime(p,(mtime,mtime))
  return folder
 def pointer(self,folder,dataset=False):
  p=self.root/'conversion_latest.json'
  if dataset:p=self.root/'conversion_by_dataset'/(hashlib.sha256(self.target.encode()).hexdigest()+'.json')
  p.parent.mkdir(exist_ok=True);p.write_text(json.dumps({'run_dir':str(folder)}))
 def test_latest_submission_wins_over_future_file_times(self):
  self.job('conversion_old',['/data/record'],2000000000)
  single=self.job('conversion_single',['/data/record/full_20260314070413'],1000000000);self.pointer(single)
  s=w.Job('conversion',restore_latest=True).status()
  self.assertEqual(s['run_dir'],str(single));self.assertEqual(s['resume_request']['episode_paths'],['/data/record/full_20260314070413'])
 def test_dataset_pointer_wins_over_other_request_with_same_output(self):
  self.job('conversion_old',['/data/record'],2000000000)
  single=self.job('conversion_single',['/data/record/full_20260314070413'],1000000000);self.pointer(single,True)
  with patch.object(w.conversion,'status',return_value={'running':False}):s=w.dataset_conversion_status(self.target)
  self.assertEqual(s['resume_request']['episode_paths'],['/data/record/full_20260314070413'])
 def test_ambiguous_legacy_history_does_not_offer_wrong_resume(self):
  self.job('conversion_old',['/data/record'],2000000000);self.job('conversion_single',['/data/record/full_20260314070413'],1000000000)
  with patch.object(w.conversion,'status',return_value={'running':False}):s=w.dataset_conversion_status(self.target)
  self.assertFalse(s.get('resume_request'));self.assertIn('多个',s['error'])
 def test_cached_legacy_job_cannot_override_ambiguous_sources(self):
  old=self.job('conversion_old',['/data/record'],2000000000);self.job('conversion_single',['/data/record/full_20260314070413'],1000000000)
  cached={'dataset_path':self.target,'running':False,'run_dir':str(old),'resume_request':{'episode_paths':['/data/record']}}
  with patch.object(w.conversion,'status',return_value=cached):s=w.dataset_conversion_status(self.target)
  self.assertFalse(s.get('resume_request'));self.assertIn('多个',s['error'])
 def test_partial_catalog_marks_conversion_incomplete_without_guessing_external_datasets(self):
  root=self.root/'dataset';(root/'meta').mkdir(parents=True);(root/'meta/info.json').write_text('{"fps":30,"total_episodes":1}')
  self.assertIsNone(w.datasets(str(root))['datasets'][0]['conversion_complete'])
  (root/'provenance').mkdir();(root/'provenance/settings.json').write_text('{}')
  self.assertFalse(w.datasets(str(root))['datasets'][0]['conversion_complete'])
  (root/'provenance/SUCCESS').write_text('done');self.assertTrue(w.datasets(str(root))['datasets'][0]['conversion_complete'])
 def test_exit_zero_without_final_result_is_not_success(self):
  folder=self.job('conversion_no_result',['/raw/one'],1000)
  j=w.Job('conversion');j.folder=folder;j.process=SimpleNamespace(poll=lambda:0)
  s=j.status();self.assertNotEqual(s['phase'],'完成');self.assertIn('最终',s['error'])
 def test_explicit_resume_keeps_the_saved_single_source(self):
  from fastapi import FastAPI
  from fastapi.testclient import TestClient
  single=self.job('conversion_single',['/data/record/full_20260314070413'],1000)
  api=FastAPI();api.include_router(w.router)
  with patch.object(w,'convert',return_value={'running':True}) as start:
   r=TestClient(api).post('/api/conversion/resume',json={'run_dir':str(single),'dataset':self.target})
  self.assertEqual(r.status_code,200);self.assertEqual(start.call_args.args[0]['episode_paths'],['/data/record/full_20260314070413'])
  self.assertTrue(start.call_args.args[0]['resume'])

if __name__=='__main__':unittest.main()
