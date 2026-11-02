# 使用说明
# 用途：LeRobot MP4写入、NULL/float64/PTP及恢复的离线回归。
# 从项目根目录运行：./conversion/.venv/bin/python -m unittest discover -s conversion -p "test_optional_camera_conversion.py" -v
# 仅使用临时目录、合成数据或Fake依赖，不启动真实SDK、不发送机器人动作。
# 测试分组、依赖环境及历史兼容范围见 README.md。
#
"""LeRobot 原始列回归：保留NULL、float64、MP4原像素、原始时间与批量恢复。"""
import io,json,tempfile,unittest
import numpy as np
import av
from PIL import Image
from pathlib import Path
from types import SimpleNamespace
import pyarrow.parquet as pq
from conversion_progress import Progress
import convert_r2
from test_mcap_input import recording

class RawConversionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
 def args(self,sources):
  return SimpleNamespace(input=list(map(str,sources)),inspect=False,output=str(self.root/'dataset'),repo_id='local/test',
   task='offline test',tasks_json=None,fps=30,threads=1,resume=False,skip_errors=False,progress_reporter=Progress(),errors_out=[])
 def test_nulls_and_images_survive_arrow_and_pinned_loader(self):
  source=self.root/'input/raw.mcap';jpg=recording(source,delayed=True)
  args=self.args([source]);convert_r2.convert(args)
  table=pq.read_table(self.root/'dataset/data/chunk-000/episode_000000.parquet');rows=table.to_pylist()
  self.assertEqual(len(rows),6);self.assertEqual(rows[0]['action'],[None]*24)
  self.assertEqual(rows[1]['action'][0],.001000000001);self.assertEqual(table.schema.field('action').type.value_type.__str__(),'double')
  self.assertEqual(table.schema.field('action').type.list_size,24)
  self.assertNotIn('observation.images.head_color',table.column_names)
  info=json.loads((self.root/'dataset/meta/info.json').read_text())
  self.assertEqual(info['codebase_version'],'v2.1');self.assertEqual(info['total_videos'],3)
  self.assertEqual(info['features']['observation.images.head_color']['dtype'],'video')
  video=self.root/'dataset'/info['video_path'].format(episode_chunk=0,episode_index=0,video_key='observation.images.head_color')
  with av.open(str(video)) as container:
   frames=list(container.decode(video=0))
  self.assertEqual(len(frames),6)
  np.testing.assert_array_equal(frames[0].to_ndarray(format='rgb24'),np.asarray(Image.open(io.BytesIO(jpg)).convert('RGB')))
  self.assertFalse(list((self.root/'dataset').rglob('*.png')))
  self.assertEqual(rows[0]['source_timestamp_ns'],10**9)
  report=json.loads((self.root/'dataset/provenance/conversion.json').read_text())
  self.assertFalse(report['training_ready']);self.assertEqual(report['null_action_values'],24)
  from lerobot.common.datasets.lerobot_dataset import LeRobotDataset
  ds=LeRobotDataset('local/test',root=self.root/'dataset',video_backend='pyav',delta_timestamps={'action':[i/30 for i in range(50)]})
  valid=ds[1];self.assertEqual(tuple(valid['action'].shape),(50,24));self.assertEqual(tuple(valid['observation.images.head_color'].shape),(3,4,8))
  self.assertEqual(valid['action'].dtype.__str__(),'torch.float32')  # 固定版本训练 loader 的内存格式；磁盘仍为 double。
 def test_resume_keeps_all_original_rows_once(self):
  source=self.root/'input/raw.mcap';recording(source);args=self.args([source]);convert_r2.convert(args)
  before=(self.root/'dataset/data/chunk-000/episode_000000.parquet').read_bytes();args.resume=True;convert_r2.convert(args)
  self.assertEqual((self.root/'dataset/data/chunk-000/episode_000000.parquet').read_bytes(),before)
  self.assertEqual(json.loads((self.root/'dataset/meta/info.json').read_text())['total_frames'],6)
 def test_native_shape_mismatch_is_reported_without_resizing(self):
  a=self.root/'a/raw.mcap';b=self.root/'b/raw.mcap';recording(a,cameras=('head_color',));recording(b)
  args=self.args([a,b])
  with self.assertRaisesRegex(ValueError,'原始分辨率'):convert_r2.convert(args)
  self.assertEqual(json.loads((self.root/'dataset/meta/info.json').read_text())['total_episodes'],1)

 def test_video_gap_keeps_original_ptp_and_exact_head_row_count(self):
  source=self.root/'input/raw.mcap';stamps=[10**9,10**9+33_000_000,10**9+200_000_000]
  recording(source,head_times=stamps);convert_r2.convert(self.args([source]))
  table=pq.read_table(self.root/'dataset/data/chunk-000/episode_000000.parquet')
  self.assertEqual(table.column('source_timestamp_ns').to_pylist(),stamps)
  with av.open(str(self.root/'dataset/videos/chunk-000/observation.images.head_color/episode_000000.mp4')) as c:
   frames=list(c.decode(video=0))
  self.assertEqual(len(frames),3)
  self.assertEqual([float(f.pts*f.time_base) for f in frames],[0.,1/30,2/30])
 def test_partial_mp4_is_rolled_back_then_resumes(self):
  from unittest.mock import patch
  from video_mp4 import RGBVideoWriter
  source=self.root/'input/raw.mcap';recording(source);args=self.args([source])
  real=RGBVideoWriter.write
  def fail(writer,rgb):
   if writer.count==2:raise RuntimeError('injected video failure')
   return real(writer,rgb)
  with patch.object(RGBVideoWriter,'write',fail):
   with self.assertRaisesRegex(RuntimeError,'injected'):convert_r2.convert(args)
  self.assertFalse(list((self.root/'dataset/videos').rglob('*.mp4')))
  self.assertFalse(list((self.root/'dataset/data').rglob('*.parquet')))
  args.resume=True;args.errors_out=[];convert_r2.convert(args)
  self.assertEqual(json.loads((self.root/'dataset/meta/info.json').read_text())['total_frames'],6)
 def test_png_settings_are_not_silently_resumed_as_mp4(self):
  source=self.root/'input/raw.mcap';recording(source);args=self.args([source]);convert_r2.convert(args)
  path=self.root/'dataset/provenance/settings.json';settings=json.loads(path.read_text());settings['media']='image';path.write_text(json.dumps(settings))
  args.resume=True
  with self.assertRaisesRegex(ValueError,'PNG'):convert_r2.convert(args)
