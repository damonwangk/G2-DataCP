# 使用说明
# 用途：回放模块离线回归测试，使用临时样例和Fake SDK，不发送真机动作。
# 使用：在replay目录运行 ../conversion/.venv/bin/python -m unittest -v test_replay_adaptation。
# 依赖：conversion/extra_deps及对应测试fixture；支持整目录unittest发现。
# 多文件使用说明：replay/README.md、README_clamped_smooth.md；总测试索引见../tests/README.md。
#
"""原指令回放回归：不取反、不插值、不返回首帧，空值不删整帧。"""
import copy,json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from test_lerobot_replay import Clock, FakeRobot
import lerobot_replay as replay
from trajectory import sample, prepare_episode


def sdk(robot):
 return NS(gdk_init=lambda:0,gdk_release=lambda:None,Robot=lambda:robot,GDKRes=NS(kSuccess=0),MotionControlMode=NS,JointServoControlReq=NS,
  InputSource=NS(INPUT_GDK=6),Target=NS(TARGET_ARMS=0),ControlMode=NS(CTRL_JOINT_POSITION=1),SafeMode=NS(SAFE_NORMAL=0))


def episode():
 return {'path':'fixture','root':'fixture','episode':0,'fps':30,'names':['idx21_arm_l_joint1','idx31_gripper_l_inner_joint1'],
  'times':[0.,.033,.2], 'actions':[[None,None],[.121941149,.025],[.4,None]],
  'recorded_states':[[.7,.025]]*3,'duration_s':.233,'action_sources':['command']*3}


class RawReplayTests(unittest.TestCase):
 def test_prepare_and_sampling_preserve_exact_values_and_nulls(self):
  ep=episode();before=copy.deepcopy(ep);prepared=prepare_episode(ep)
  self.assertEqual(prepared['command_targets'],ep['actions']);self.assertEqual(ep,before)
  self.assertEqual(sample(ep,.02)[1],[None,None]);self.assertEqual(sample(ep,.1)[1],[.121941149,.025])
 def test_null_frame_and_null_axis_complete_without_imputation(self):
  ep=episode();robot=FakeRobot()
  with TemporaryDirectory() as td:
   path=Path(td)/'trace.jsonl';out=replay.execute(ep,{},path,sdk(robot),Clock(),handoff_fn=lambda _: {})
   rows=[json.loads(x) for x in path.read_text().splitlines()]
  steps=[r for r in rows if r['type']=='step']
  self.assertTrue(out['completed']);self.assertEqual(out['unsent_frame_count'],0);self.assertEqual(out['approach_sent'],0)
  self.assertTrue(any(r['target']==[None,None] and r['sent_names']==[] for r in steps))
  self.assertEqual(robot.requests[0][1],[.121941149,.025])
  self.assertEqual(robot.requests[-1][0],['idx21_arm_l_joint1']);self.assertEqual(robot.requests[-1][1],[.4])
  self.assertEqual(ep['actions'],episode()['actions'])
  self.assertFalse(any(r.get('phase')=='返回起始姿态' for r in rows))
 def test_all_null_recording_runs_full_duration_without_sdk_position_requests(self):
  ep=episode();ep['actions']=[[None,None]]*3;robot=FakeRobot()
  with TemporaryDirectory() as td:out=replay.execute(ep,{},Path(td)/'trace.jsonl',sdk(robot),Clock(),handoff_fn=lambda _: {})
  self.assertTrue(out['completed']);self.assertEqual(robot.requests,[]);self.assertAlmostEqual(out['position_s'],.233)
