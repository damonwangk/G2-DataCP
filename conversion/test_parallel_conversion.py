# 使用说明
# 用途：多源并行转换、合并、索引和断点恢复的离线回归。
# 从项目根目录运行：./conversion/.venv/bin/python -m unittest discover -s conversion -p "test_parallel_conversion.py" -v
# 仅使用临时目录、合成数据或Fake依赖，不启动真实SDK、不发送机器人动作。
# 测试分组、依赖环境及历史兼容范围见 README.md。
#
"""并行原始转换、合并索引与断点恢复的离线回归。"""
import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
import pyarrow.parquet as pq
import numpy as np
from test_mcap_input import recording

class ParallelConversionTests(unittest.TestCase):
    def test_parallel_sources_merge_indices_tasks_videos_and_resume(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);inputs=base/'inputs'
            for i in range(2):recording(inputs/str(i)/'raw.mcap')
            (base/'inputs.json').write_text(json.dumps([str(inputs)]))
            cmd=[sys.executable,str(Path(__file__).with_name('platform_convert.py')),'--inputs-json',str(base/'inputs.json'),'--dataset-path',str(base/'output'),'--repo-id','local/test','--task','移动','--workers','2','--threads','2','--image-size','32','--result',str(base/'result.json'),'--progress',str(base/'progress.json')]
            env=dict(os.environ,HF_HUB_OFFLINE='1',HF_DATASETS_OFFLINE='1',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
            result=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=180)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            outcome=json.loads((base/'result.json').read_text());self.assertTrue(outcome['completed']);self.assertEqual(outcome['total_episodes'],2);self.assertEqual(outcome['total_frames'],12)
            tables=[pq.read_table(p) for p in sorted((base/'output/data').rglob('*.parquet'))]
            self.assertEqual([t.column('index').to_pylist() for t in tables],[list(range(6)),list(range(6,12))])
            self.assertEqual(tables[1].column('episode_index').to_pylist(),[1]*6)
            self.assertEqual(tables[1].column('task_index').to_pylist(),[0]*6)
            self.assertEqual(len(list((base/'output/videos').rglob('*.mp4'))),6)
            self.assertTrue(all('observation.images.head_color' not in t.column_names for t in tables))
            stats=[json.loads(s) for s in (base/'output/meta/episodes_stats.jsonl').read_text().splitlines()]
            self.assertEqual(stats[1]['stats']['index']['min'],[6])
            progress=json.loads((base/'progress.json').read_text());self.assertEqual(progress['completed_sources'],2);self.assertEqual(progress['workers'],2)
            second=subprocess.run(cmd+['--resume'],env=env,capture_output=True,text=True,timeout=180)
            self.assertEqual(second.returncode,0,second.stdout+second.stderr)
            self.assertEqual(json.loads((base/'result.json').read_text())['total_episodes'],2)
            # Append two sources with another task; official indices/statistics must shift.
            extra=base/'more'
            for i in range(2):recording(extra/str(i)/'raw.mcap')
            (base/'inputs.json').write_text(json.dumps([str(extra)]))
            newer=cmd.copy();newer[newer.index('--task')+1]='另一个任务'
            third=subprocess.run(newer+['--resume'],env=env,capture_output=True,text=True,timeout=180)
            self.assertEqual(third.returncode,0,third.stdout+third.stderr)
            table=pq.read_table(base/'output/data/chunk-000/episode_000003.parquet')
            self.assertEqual(table.column('index').to_pylist(),list(range(18,24)))
            self.assertEqual(table.column('task_index').to_pylist(),[1]*6)
            stats=[json.loads(s) for s in (base/'output/meta/episodes_stats.jsonl').read_text().splitlines()]
            self.assertEqual(stats[3]['stats']['task_index']['mean'],[1.0])
            self.assertEqual(stats[3]['stats']['episode_index']['min'],[3])


if __name__=='__main__':unittest.main()
