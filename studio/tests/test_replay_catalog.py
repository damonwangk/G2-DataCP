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
            self.assertTrue(a['file'].startswith('episode_1_'),a['file']);self.assertTrue(b['file'].startswith('episode_2_'),b['file'])
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
    def test_raw_positions_use_recorded_receive_time_and_full_episode(self):
        topics=[robot_replay.JOINT,*robot_replay.GRIPS]
        rows=[]
        for k,topic in enumerate(topics):
            for i,t in enumerate([0.,.1,.6,1.] if k==0 else [0.,.4,1.6]):
                d={'name':[str(k)],'motor_position':[float(i)],'type':1,'end_state':[{'position':float(i)}]}
                rows.append({'topic':topic,'data':d,'wall_ns':1000000000+int(t*1e9),'meta':{'source_ns':str(5000000000+int(t*.8*1e9))}})
        # Source timestamps can repeat; MCAP receive time still defines original recording order.
        rows[-1]['meta']['source_ns']=rows[-2]['meta']['source_ns']
        with patch.object(robot_replay,'raw_events',return_value=iter(sorted(rows,key=lambda x:x['wall_ns']))),patch.object(robot_replay,'bounds',return_value=(1000000000,2800000000)):
            t=robot_replay.Trajectory(Path('/tmp/raw.mcap'),None)
        self.assertAlmostEqual(t.duration,1.8)
        self.assertEqual(t.at(.3),[1.,0.,0.])
        self.assertEqual(t.at(.65),[2.,1.,1.])
        self.assertEqual(t.at(1.8),[3.,2.,2.])

if __name__=='__main__':unittest.main()

