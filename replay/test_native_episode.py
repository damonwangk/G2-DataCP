# 使用说明
# 用途：回放模块离线回归测试，使用临时样例和Fake SDK，不发送真机动作。
# 使用：在replay目录运行 ../conversion/.venv/bin/python -m unittest -v test_native_episode。
# 依赖：conversion/extra_deps及对应测试fixture；支持整目录unittest发现。
# 多文件使用说明：replay/README.md、README_clamped_smooth.md；总测试索引见../tests/README.md。
#
"""同一原始指令事件在 MCAP / pbdat 回放中值与时间完全一致。"""
import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'conversion'))
from test_mcap_input import recording
from test_factory_input import factory_from_mcap
from source_episode import load_source

class NativeTests(unittest.TestCase):
 def test_all_source_events_and_initial_nulls_survive_both_readers(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);mcap=root/'raw.mcap';recording(mcap,delayed=True,sparse=True);factory_from_mcap(mcap,root/'native')
   a=load_source(mcap);b=load_source(root/'native')
   self.assertEqual(a['actions'],b['actions']);self.assertEqual(a['times'],b['times'])
   self.assertEqual(a['actions'][0],[None]*24);self.assertEqual(len(a['actions']),5)
   self.assertEqual(len(a['names']),24)
