# 使用说明
# 用途：回放模块离线回归测试，使用临时样例和Fake SDK，不发送真机动作。
# 使用：在replay目录运行 ../conversion/.venv/bin/python -m unittest -v test_dataset_info_commit。
# 依赖：conversion/extra_deps及对应测试fixture；支持整目录unittest发现。
# 多文件使用说明：replay/README.md、README_clamped_smooth.md；总测试索引见../tests/README.md。
#
"""未落盘、未关闭或仍在事务中的 Parquet 不应成为可选回放 episode。"""
import json,tempfile,unittest
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
from dataset_info import describe

class CommittedEpisodeTests(unittest.TestCase):
 def root(self,folder):
  root=Path(folder);(root/'meta').mkdir();(root/'meta/info.json').write_text(json.dumps({'codebase_version':'v2.1','fps':30,'total_episodes':1,'total_frames':2,'chunks_size':1000,'data_path':'data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet','features':{}}));(root/'meta/episodes.jsonl').write_text('{"episode_index":0,"length":2}\n');return root
 def test_only_closed_committed_parquet_is_selectable(self):
  with tempfile.TemporaryDirectory() as td:
   root=self.root(td);file=root/'data/chunk-000/episode_000000.parquet';file.parent.mkdir(parents=True)
   self.assertEqual(describe(root)['episodes'],[])
   file.write_bytes(b'PAR1unfinished');self.assertEqual(describe(root)['episodes'],[])
   pq.write_table(pa.table({'action':[[1.],[2.]]}),file)
   self.assertEqual(len(describe(root)['episodes']),1)
   tx=root/'.r2-transaction';tx.mkdir();(tx/'pending.json').write_text(json.dumps({'generated_files':[str(file.relative_to(root))]}))
   self.assertEqual(describe(root)['episodes'],[])
   (tx/'pending.json').unlink();self.assertEqual(describe(root)['episodes'][0]['frames'],2)

 def test_source_and_ptp_duration_are_shown_instead_of_nominal_row_fps(self):
  with tempfile.TemporaryDirectory() as td:
   root=self.root(td);file=root/'data/chunk-000/episode_000000.parquet';file.parent.mkdir(parents=True)
   pq.write_table(pa.table({'action':[[1.],[2.]],'source_timestamp_ns':[1000000000000000000,1000000025000000000]}),file)
   (root/'provenance').mkdir();(root/'provenance/segments.jsonl').write_text('{"episode_index":0,"source":"/data/record/full_original"}\n')
   ep=describe(root)['episodes'][0];self.assertEqual(ep['source'],'/data/record/full_original');self.assertAlmostEqual(ep['duration_s'],25+1/30)

if __name__=='__main__':unittest.main()
