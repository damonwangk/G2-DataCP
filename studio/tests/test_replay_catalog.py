# 使用说明
# 用途：平台离线回归测试：replay_catalog。
# 用法：在studio目录执行 python -m unittest tests.test_replay_catalog（已配置依赖的Python）；使用模拟SDK/API，不运行真机任务。
# 详细说明：../README.md

"""采集序号、回放任务状态及原指令保持的离线回归。"""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import threading
import storage,robot_replay
from native import Schemas
from types import SimpleNamespace

class ReplayCatalog(unittest.TestCase):
    def test_episode_numbers_are_per_directory_and_feedback_does_not_consume_number(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(storage,'data_dir',return_value=Path(tmp)):
            recorder=storage.Recorder(Schemas())
            a=recorder.start('task',[]);recorder.stop([])
            recorder.start('posture_feedback',[],{'kind':'posture'});recorder.stop([])
            b=recorder.start('task',[]);recorder.stop([])
            self.assertTrue(a['file'].startswith('task_1_'),a['file']);self.assertTrue(b['file'].startswith('task_2_'),b['file'])
            self.assertEqual(len(storage.list_files()),2)
            self.assertEqual(len(storage.list_files('feedback')),1)
    def test_prepare_failure_is_retained_as_replay_failure_without_worker(self):
        receiver=SimpleNamespace(lock=threading.RLock(),latest={})
        job=robot_replay.RobotJob(receiver,None,None)
        with patch.object(robot_replay,'plan',side_effect=ValueError('sample gap 0.48 s')),patch('subprocess.Popen') as worker:
            with self.assertRaisesRegex(ValueError,'sample gap'):job.start(Path('/tmp/episode/raw.mcap'))
            self.assertIn('sample gap',job.status()['error']);self.assertEqual(job.status()['phase'],'回放准备失败')
            self.assertFalse(job.running);worker.assert_not_called()


class RawReplay(unittest.TestCase):
    def test_source_commands_are_held_without_feedback_replacement(self):
        ep={'names':['idx01_body_joint1'],'available_names':['idx01_body_joint1'],
            'times':[0.,.1,.6], 'actions':[[None],[1.],[2.]], 'recorded_states':[[9.]]*3,
            'duration_s':1.8,'path':'raw.mcap','episode':0,'fps':100}
        with patch('source_episode.load_source',return_value=ep):
            t=robot_replay.Trajectory(Path('/tmp/raw.mcap'),None)
        self.assertEqual(t.at(.05),[None]);self.assertEqual(t.at(.3),[1.]);self.assertEqual(t.at(1.8),[2.])

if __name__=='__main__':unittest.main()

