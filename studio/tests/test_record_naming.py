"""Task label must survive the real recorder start/stop path; no live receiver."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import storage


class RecordNamingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / 'chosen'
        self.data.mkdir()
        (self.root / 'schemas.pb').write_bytes(b'offline-schema-attachment')
        self.patches = [patch.object(storage, 'data_dir', return_value=self.data),
                        patch.object(storage, 'ROOT', self.root)]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    def record(self, label):
        recorder = storage.Recorder(SimpleNamespace(data={}))
        started = recorder.start(label, [], topics=['/hal/joint_state'])
        completed = recorder.stop([])
        return started, completed

    def test_custom_task_is_the_directory_prefix_and_metadata_agrees(self):
        started, completed = self.record('122episode')
        self.assertRegex(started['file'], r'^122episode_1_\d{8}_\d{6}$')
        self.assertEqual(started['file'], completed['file'])
        self.assertEqual(Path(completed['path']), self.data / completed['file'] / 'raw.mcap')
        self.assertEqual(completed['label'], '122episode')
        self.assertEqual(json.loads(Path(completed['path']).with_suffix('.json').read_text())['file'], completed['file'])
        self.assertTrue(Path(completed['path']).is_file())

    def test_default_name_stays_compatible_and_numbers_are_per_directory(self):
        a, _ = self.record('122episode')
        b, _ = self.record('episode')
        c, _ = self.record('取放任务')
        self.assertTrue(a['file'].startswith('122episode_1_'))
        self.assertTrue(b['file'].startswith('episode_2_'))
        self.assertTrue(c['file'].startswith('取放任务_3_'))

    def test_scans_existing_custom_and_legacy_names_if_counter_is_missing(self):
        (self.data / 'episode_2_old').mkdir()
        (self.data / 'phone_task_8_20260314_005050').mkdir()
        (self.data / 'unrelated_999_notes').mkdir()
        started, _ = self.record('122episode')
        self.assertTrue(started['file'].startswith('122episode_9_'), started['file'])

    def test_sequence_survives_deleted_episode_and_task_change(self):
        _, a = self.record('task_a')
        directory = Path(a['episode_dir'])
        for p in directory.iterdir(): p.unlink()
        directory.rmdir()
        started, _ = self.record('task_b')
        self.assertTrue(started['file'].startswith('task_b_2_'), started['file'])

    def test_label_is_sanitized_into_one_directory_component(self):
        started, completed = self.record('task / one')
        self.assertTrue(started['file'].startswith('task_one_1_'))
        self.assertEqual(Path(completed['episode_dir']).parent, self.data)

    def test_task_name_containing_episode_number_does_not_steal_sequence(self):
        (self.data/'episode_12_1_20260314_005050').mkdir()
        self.assertEqual(storage.episode_number(self.data),1)
        started,_=self.record('episode_12')
        self.assertTrue(started['file'].startswith('episode_12_2_'),started['file'])


if __name__ == '__main__': unittest.main()
