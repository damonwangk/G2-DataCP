# 使用说明
# 用途：JSON转换CLI的路径兼容与结果输出回归。
# 从项目根目录运行：./conversion/.venv/bin/python -m unittest discover -s conversion -p "test_platform_paths.py" -v
# 仅使用临时目录、合成数据或Fake依赖，不启动真实SDK、不发送机器人动作。
# 测试分组、依赖环境及历史兼容范围见 README.md。
#
"""CLI path compatibility without loading data or contacting the robot."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import platform_convert as cli

class PlatformPathTests(unittest.TestCase):
 def invoke(self,extra):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);source=root/'raw.mcap';source.touch();inputs=root/'inputs.json';inputs.write_text(json.dumps([str(source)]))
   result=root/'result.json';seen=[]
   arguments=['platform_convert.py','--inputs-json',str(inputs),'--repo-id','local/test','--task','test','--result',str(result),*extra(root)]
   with patch.object(sys,'argv',arguments),patch.object(cli,'convert',side_effect=lambda settings:seen.append(settings)),contextlib.redirect_stdout(io.StringIO()):
    self.assertEqual(cli.main(),0)
   return root,seen[0],json.loads(result.read_text())
 def test_legacy_output_root_adds_repo_id(self):
  root,settings,result=self.invoke(lambda r:['--output-dir',str(r/'datasets')])
  self.assertEqual(settings.output,str(root/'datasets/local/test'))
  self.assertEqual(result['dataset_path'],settings.output)
 def test_exact_path_does_not_add_repo_id(self):
  root,settings,result=self.invoke(lambda r:['--output-dir',str(r/'unused'),'--dataset-path',str(r/'训练集 第一组')])
  self.assertEqual(settings.output,str(root/'训练集 第一组'))
  self.assertEqual(result['dataset'],settings.output)
 def test_exact_path_does_not_require_output_root(self):
  root,settings,result=self.invoke(lambda r:['--dataset-path',str(r/'exact')])
  self.assertEqual(settings.output,str(root/'exact'))
  self.assertTrue(result['completed'])

if __name__=='__main__':unittest.main()
