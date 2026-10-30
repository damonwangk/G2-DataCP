"""A real Parquet state/action-only dataset must work without any image files."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import signal
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from dataset_info import describe
import lerobot_replay as replay
from test_lerobot_replay import Clock, FakeRobot


def write_dataset(root):
    names = ([f'idx{20 + i}_arm_l_joint{i}' for i in range(1, 8)] +
             ['idx31_gripper_l_inner_joint1'] +
             [f'idx{60 + i}_arm_r_joint{i}' for i in range(1, 8)] +
             ['idx71_gripper_r_inner_joint1'])
    root = Path(root)
    (root / 'meta').mkdir()
    (root / 'data/chunk-000').mkdir(parents=True)
    info = {'codebase_version': 'v2.1', 'fps': 30, 'chunks_size': 1000,
            'total_episodes': 1, 'total_frames': 3,
            'data_path': 'data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet',
            'features': {'timestamp': {'dtype': 'float32', 'shape': [1]},
                         'action': {'dtype': 'float32', 'shape': [16], 'names': names},
                         'observation.state': {'dtype': 'float32', 'shape': [16], 'names': names}}}
    (root / 'meta/info.json').write_text(json.dumps(info))
    (root / 'meta/episodes.jsonl').write_text(json.dumps({'episode_index': 0, 'length': 3, 'tasks': ['记录的动作']}) + '\n')
    actions = [[.01 * (frame + index) if index not in (7, 15) else .025
                for index in range(16)] for frame in range(3)]
    table = pa.table({'timestamp': pa.array([0, 1 / 30, 2 / 30], type=pa.float32()),
                      'action': pa.array(actions, type=pa.list_(pa.float32(), 16)),
                      'observation.state': pa.array([[.5] * 16] * 3, type=pa.list_(pa.float32(), 16))})
    pq.write_table(table, root / 'data/chunk-000/episode_000000.parquet')
    return names, table.column('action').to_pylist()


class ImageFreeDatasetTests(unittest.TestCase):
    def test_list_inspect_load_and_fake_replay_without_images(self):
        robot = FakeRobot()
        sdk = SimpleNamespace(gdk_init=lambda: 0, gdk_release=lambda: None, Robot=lambda: robot,
            GDKRes=SimpleNamespace(kSuccess=0), MotionControlMode=SimpleNamespace,
            JointServoControlReq=SimpleNamespace, InputSource=SimpleNamespace(INPUT_GDK=1),
            Target=SimpleNamespace(TARGET_ARMS=2), ControlMode=SimpleNamespace(CTRL_JOINT_POSITION=3),
            SafeMode=SimpleNamespace(SAFE_NORMAL=4))
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            names, stored_actions = write_dataset(root)
            self.assertFalse((root / 'videos').exists())
            summary = describe(root)
            self.assertEqual(summary['video_features'], [])
            self.assertEqual(summary['total_episodes'], 1)
            self.assertEqual(summary['episodes'][0]['frames'], 3)
            self.assertTrue(summary['episodes'][0]['exists'])
            inspected = describe(root, 0)['selected_episode']
            self.assertEqual(inspected['names'], names)
            self.assertEqual(inspected['first_action'], stored_actions[0])
            episode = replay.load_episode(root, 0)
            self.assertEqual(episode['actions'], stored_actions)
            result = replay.execute(episode, {}, root / 'trace.jsonl', sdk, Clock(), handoff_fn=lambda _: {})
            self.assertTrue(result['completed'])
            self.assertEqual(result['unsent_frame_count'], 0)
            self.assertEqual(robot.requests[0][0], names)
            self.assertEqual(robot.requests[0][1], stored_actions[0])
            self.assertEqual(robot.requests[-1][1], stored_actions[-1])
            self.assertTrue(all(request[1] in stored_actions for request in robot.requests))

    def test_cli_dry_run_without_images_or_sdk(self):
        old_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
        original_import = replay.importlib.import_module
        def no_sdk(name, *args, **kwargs):
            if name == 'agibot_gdk':
                raise AssertionError('dry-run must not import the SDK')
            return original_import(name, *args, **kwargs)
        try:
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                write_dataset(root)
                with patch.object(replay.importlib, 'import_module', side_effect=no_sdk), \
                     patch.object(replay, 'execute', side_effect=AssertionError('dry-run must not execute')), \
                     redirect_stdout(io.StringIO()):
                    code = replay.main(['--dataset', str(root), '--episode', '0', '--dry-run', '--run', str(root / 'run')])
                result = json.loads((root / 'run/result.json').read_text())
                self.assertEqual(code, 0)
                self.assertTrue(result['completed'])
                self.assertFalse(result['control_commands_sent'])
                self.assertEqual(result['frames'], 3)
        finally:
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)


if __name__ == '__main__':
    unittest.main()
