# 使用说明
# 用途：采集完成标记的显式确认、文件清单及变更校验。
# 从项目根目录运行：./conversion/.venv/bin/python -m unittest discover -s conversion -p "test_episode_ready.py" -v
# 仅使用临时目录、合成数据或Fake依赖，不启动真实SDK、不发送机器人动作。
# 测试分组、依赖环境及历史兼容范围见 README.md。
#
import json
import os
from pathlib import Path
import tempfile
import unittest

from episode_ready import MARKER, create_marker, required_files, verify_ready


class ReadyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ep = Path(self.tmp.name) / 'episode'
        self.ep.mkdir()
        for path in required_files(self.ep):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'sample')

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_and_requires_explicit_confirmation(self):
        with self.assertRaises(ValueError):
            create_marker(self.ep)
        self.assertFalse(verify_ready(self.ep)[0])
        create_marker(self.ep, confirm_finalized=True)
        self.assertEqual(verify_ready(self.ep), (True, 'ready'))

    def test_malformed_and_path_traversal(self):
        (self.ep / MARKER).write_text('{')
        self.assertFalse(verify_ready(self.ep)[0])
        create_marker(self.ep, confirm_finalized=True)
        marker = self.ep / MARKER
        data = json.loads(marker.read_text())
        data['files'][0]['path'] = '../other'
        marker.write_text(json.dumps(data))
        self.assertFalse(verify_ready(self.ep)[0])

    def test_missing_and_size_change(self):
        create_marker(self.ep, confirm_finalized=True)
        path = required_files(self.ep)[0]
        path.write_bytes(b'changed size')
        self.assertFalse(verify_ready(self.ep)[0])
        path.unlink()
        self.assertFalse(verify_ready(self.ep)[0])

    def test_mtime_change(self):
        create_marker(self.ep, confirm_finalized=True)
        path = required_files(self.ep)[0]
        old = path.stat().st_mtime_ns
        os.utime(path, ns=(old, old + 1_000_000))
        self.assertFalse(verify_ready(self.ep)[0])

    def test_symlink_file_and_parent(self):
        create_marker(self.ep, confirm_finalized=True)
        path = required_files(self.ep)[0]
        outside = Path(self.tmp.name) / 'other'
        outside.write_bytes(b'sample')
        path.unlink()
        path.symlink_to(outside)
        self.assertFalse(verify_ready(self.ep)[0])
        with self.assertRaises(ValueError):
            create_marker(self.ep, confirm_finalized=True)
        path.unlink()
        path.parent.rmdir()
        path.parent.symlink_to(outside.parent, target_is_directory=True)
        self.assertFalse(verify_ready(self.ep)[0])

    def test_marker_symlink(self):
        marker = self.ep / MARKER
        marker.symlink_to(Path(self.tmp.name) / 'fake')
        self.assertFalse(verify_ready(self.ep)[0])
        with self.assertRaises(ValueError):
            create_marker(self.ep, confirm_finalized=True)


if __name__ == '__main__':
    unittest.main()
