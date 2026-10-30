"""Official writer regression for optional images, real progress, and empty-output resume."""
import io,json,tempfile,unittest
from pathlib import Path
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch
import convert_r2
from conversion_progress import Progress
from test_mcap_input import recording

class OptionalCameraConversionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
 def arguments(self,sources,name='dataset'):
  return SimpleNamespace(input=list(map(str,sources)),inspect=False,output=str(self.root/name),repo_id='local/test',
   task='offline conversion regression',tasks_json=None,fps=30,image_size=32,media='video',codec='h264',threads=1,
   max_offset_ms=20.,max_camera_age_ms=50.,max_gap_ms=50.,resume=False,skip_errors=True,min_free_gb=0,
   progress_reporter=Progress(self.root/(name+'_progress.json')),errors_out=[])
 def run_conversion(self,args):
  with redirect_stdout(io.StringIO()):convert_r2.convert(args)
  return json.loads((Path(args.output)/'meta/info.json').read_text())
 def test_zero_images_is_one_episode_and_has_progress_bytes(self):
  source=self.root/'input/raw.mcap';recording(source,cameras=())
  args=self.arguments([source]);info=self.run_conversion(args)
  self.assertEqual(info['total_episodes'],1);self.assertEqual(info['total_frames'],6)
  self.assertFalse(any(k.startswith('observation.images.') for k in info['features']))
  self.assertEqual(list(Path(args.output).rglob('*.mp4')),[])
  progress=json.loads((self.root/'dataset_progress.json').read_text())
  self.assertEqual(progress['phase'],'完成');self.assertEqual(progress['completed_sources'],1)
  self.assertEqual(progress['percent'],100);self.assertEqual(progress['processed_frames'],6)
  self.assertGreater(progress['bytes_read'],0);self.assertEqual(progress['total_bytes'],source.stat().st_size)
 def test_three_images_and_same_schema_batch_have_all_videos(self):
  sources=[self.root/f'input{i}/raw.mcap' for i in range(2)]
  for source in sources:recording(source)
  args=self.arguments(sources);info=self.run_conversion(args)
  self.assertEqual(info['total_episodes'],2);self.assertEqual(info['total_frames'],12)
  self.assertEqual(len(list(Path(args.output).rglob('*.mp4'))),6)
  progress=json.loads((self.root/'dataset_progress.json').read_text())
  self.assertEqual(progress['completed_sources'],2);self.assertEqual(progress['failed_sources'],0)
  self.assertEqual(progress['source_index'],2);self.assertEqual(progress['total_sources'],2)
 def test_mixed_schema_reports_failure_and_preserves_success(self):
  first=self.root/'input1/raw.mcap';second=self.root/'input2/raw.mcap'
  recording(first,cameras=());recording(second)
  args=self.arguments([first,second])
  with self.assertRaisesRegex(RuntimeError,'相机字段'):self.run_conversion(args)
  info=json.loads((Path(args.output)/'meta/info.json').read_text())
  self.assertEqual(info['total_episodes'],1)
  progress=json.loads((self.root/'dataset_progress.json').read_text())
  self.assertEqual(progress['phase'],'失败');self.assertEqual(progress['failed_sources'],1)
  self.assertEqual(progress['completed_sources'],1);self.assertEqual(progress['percent'],100)
  self.assertEqual(args.errors_out[0]['source'],str(second))
  args.input=[str(first)];args.resume=True;args.errors_out=[]
  args.progress_reporter=Progress(self.root/'retry_progress.json')
  info=self.run_conversion(args)
  self.assertEqual(info['total_episodes'],1)
  self.assertEqual(json.loads((Path(args.output)/'provenance/conversion.json').read_text())['errors'],[])
 def test_resume_empty_old_three_camera_schema_with_no_images(self):
  source=self.root/'input/raw.mcap';recording(source,cameras=())
  args=self.arguments([source]);args.skip_errors=False
  with patch.object(convert_r2,'source_cameras',return_value=convert_r2.CAMERAS),patch.object(convert_r2,'make_plan',side_effect=ValueError('old missing camera failure')):
   with self.assertRaisesRegex(ValueError,'old missing camera failure'):self.run_conversion(args)
  output=Path(args.output);sentinel=output/'user-note.txt';sentinel.write_text('keep')
  args.resume=True;args.errors_out=[];args.progress_reporter=Progress()
  info=self.run_conversion(args)
  self.assertEqual(info['total_episodes'],1);self.assertEqual(info['total_frames'],6)
  self.assertFalse(any(k.startswith('observation.images.') for k in info['features']))
  self.assertEqual(sentinel.read_text(),'keep')
  self.assertEqual(len(list((output/'provenance').glob('empty_schema_before_*.json'))),1)

if __name__=='__main__':unittest.main()
