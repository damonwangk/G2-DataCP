# 使用说明
# 用途：未标记的原生记录不会进入转换的离线回归。
# 从项目根目录运行：./conversion/.venv/bin/python -m unittest discover -s conversion -p "test_ready_filter.py" -v
# 仅使用临时目录、合成数据或Fake依赖，不启动真实SDK、不发送机器人动作。
# 测试分组、依赖环境及历史兼容范围见 README.md。
#
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import convert_r2


class ReadyFilterTests(unittest.TestCase):
    def test_unmarked_recording_never_reaches_bundle_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            episode = Path(directory)
            args = SimpleNamespace(input=[str(episode)], ready_only=True, inspect=False)
            with patch.object(convert_r2, "discover", return_value=[episode]), \
                 patch.object(convert_r2, "make_plan", side_effect=AssertionError("active source parsed")) as plan:
                convert_r2.convert(args)
            plan.assert_not_called()


if __name__ == "__main__":
    unittest.main()
