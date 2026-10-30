import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import time
import unittest
import threading
import signal
from unittest.mock import patch

import lerobot_replay as replay


class Clock:
    def __init__(self): self.t = 0
    def monotonic(self): return self.t
    def sleep(self, dt): self.t += dt


class FakeRobot:
    def __init__(self): self.requests = []; self.mode = None
    def set_control_mode(self, mode): self.mode = mode; return 0
    def joint_servo_control(self, request, low):
        self.requests.append((request.joint_names[:], request.joint_positions[:], request.control_period, low))
        return 9 if len(self.requests) == 2 else 0
    def get_joint_states(self): return {'states': [{'name':'left', 'motor_position':1.5}]}
    def get_end_state(self):
        return {'left_end_state': {'names':['grip'], 'end_states':[{'position':.025}]},
                'right_end_state': {'names':[], 'end_states':[]}}


class TestReplay(unittest.TestCase):
    def test_main_sigterm_finishes_with_result_and_no_more_commands(self):
        episode = {'path':'example.parquet', 'root':'example', 'episode':0, 'fps':30, 'names':['left'],
                   'times':[0., 1.], 'actions':[[1.], [2.]], 'duration_s':1.1,
                   'recorded_states':[[1.], [2.]]}
        clock = Clock()
        class StoppedRobot(FakeRobot):
            def joint_servo_control(self, request, low):
                code = super().joint_servo_control(request, low)
                if len(self.requests) == 2:
                    signal.raise_signal(signal.SIGTERM)
                return code
        robot = StoppedRobot()
        sdk = SimpleNamespace(gdk_init=lambda:0, gdk_release=lambda:None, Robot=lambda:robot,
            GDKRes=SimpleNamespace(kSuccess=0), MotionControlMode=SimpleNamespace,
            JointServoControlReq=SimpleNamespace, InputSource=SimpleNamespace(INPUT_GDK=1),
            Target=SimpleNamespace(TARGET_ARMS=2), ControlMode=SimpleNamespace(CTRL_JOINT_POSITION=3),
            SafeMode=SimpleNamespace(SAFE_NORMAL=4))
        original_execute = replay.execute
        def execute(*args, **kwargs):
            return original_execute(*args, **kwargs, clock=clock, handoff_fn=lambda _: {})
        old_handlers = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
        try:
            with TemporaryDirectory() as tmp:
                root = Path(tmp); (root/'config.json').write_text('{}')
                with patch.object(replay, 'load_episode', return_value=episode), \
                     patch.object(replay.importlib, 'import_module', return_value=sdk), \
                     patch.object(replay, 'execute', side_effect=execute):
                    code = replay.main(['--dataset', 'example', '--episode', '0', '--run', str(root/'run'), '--config', str(root/'config.json')])
                result = json.loads((root/'run/result.json').read_text())
                progress = json.loads((root/'run/progress.json').read_text())
            self.assertEqual(code, 0)
            self.assertEqual(len(robot.requests), 2)
            self.assertTrue(result['stopped_by_user'])
            self.assertFalse(result['completed'])
            self.assertTrue(result['control_commands_sent'])
            self.assertFalse(progress['running'])
            self.assertEqual(progress['phase'], '已停止')
        finally:
            for sig, handler in old_handlers.items(): signal.signal(sig, handler)

    def test_sdk_control_mode_signatures(self):
        class Robot:
            def __init__(self): self.calls = []
            def set_control_mode(self, value):
                self.calls.append(value)
                return 0
        sdk = SimpleNamespace(MotionControlMode=SimpleNamespace,
            InputSource=SimpleNamespace(INPUT_GDK=6), Target=SimpleNamespace(TARGET_ARMS=0),
            ControlMode=SimpleNamespace(CTRL_JOINT_POSITION=1), SafeMode=SimpleNamespace(SAFE_NORMAL=0))
        old = Robot()
        self.assertEqual(replay.select_control(old, sdk, 'struct'), 0)
        self.assertEqual(len(old.calls), 1)
        self.assertEqual((old.calls[0].input_source, old.calls[0].target,
                          old.calls[0].control_mode, old.calls[0].safe_mode), (6, 0, 1, 0))
        modern = Robot()
        self.assertEqual(replay.select_control(modern, SimpleNamespace(), 'mode-id'), 0)
        self.assertEqual(modern.calls, [1])

    def test_mode_id_is_used_through_execute(self):
        episode = {'path':'example.parquet', 'episode':0, 'fps':30, 'names':['left'],
                   'times':[0.], 'actions':[[1.]], 'duration_s':1/30,
                   'recorded_states':[[1.]]}
        robot = FakeRobot()
        sdk = SimpleNamespace(gdk_init=lambda:0, gdk_release=lambda:None, Robot=lambda:robot,
                              GDKRes=SimpleNamespace(kSuccess=0), JointServoControlReq=SimpleNamespace)
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/'trace.jsonl'
            result = replay.execute(episode, {}, path, sdk, Clock(), handoff_fn=lambda _: {},
                                    sdk_api='mode-id')
            lines = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertTrue(result['completed'])
        self.assertEqual(robot.mode, 1)
        self.assertEqual(lines[0]['sdk_api'], 'mode-id')
        self.assertEqual(next(x for x in lines if x['type'] == 'setup')['sdk_api'], 'mode-id')

    def test_held_sampling_sends_original_action_and_continues_sdk_error(self):
        episode = {'path':'example.parquet', 'episode':0, 'fps':30, 'names':['left','grip'],
                   'times':[0., .03, .06], 'actions':[[1., 0.],[2., .01],[3., .02]],
                   'duration_s': .06 + 1/30,
                   'recorded_states':[[101., .025], [102., .025], [103., .025]]}
        robot = FakeRobot()
        sdk = SimpleNamespace(gdk_init=lambda:0, gdk_release=lambda:None, Robot=lambda:robot,
            GDKRes=SimpleNamespace(kSuccess=0), MotionControlMode=SimpleNamespace,
            JointServoControlReq=SimpleNamespace, InputSource=SimpleNamespace(INPUT_GDK=1),
            Target=SimpleNamespace(TARGET_ARMS=2), ControlMode=SimpleNamespace(CTRL_JOINT_POSITION=3),
            SafeMode=SimpleNamespace(SAFE_NORMAL=4))
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/'trace.jsonl'
            result = replay.execute(episode, {}, path, sdk, Clock(), handoff_fn=lambda _: {'result':True})
            lines = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertTrue(result['completed'])
        self.assertEqual(result['rejected'], 1)
        self.assertEqual((robot.mode.input_source, robot.mode.target,
                          robot.mode.control_mode, robot.mode.safe_mode), (1, 2, 3, 4))
        self.assertGreater(len(robot.requests), 2)
        self.assertEqual(robot.requests[0], (['left','grip'], [1.,0.], .02, False))
        self.assertEqual(robot.requests[-1][1], [3.,.02])
        self.assertTrue(all(row[1] in episode['actions'] for row in robot.requests))
        steps = [line for line in lines if line['type']=='step']
        samples = [line for line in lines if line['type'] == 'feedback']
        self.assertTrue(any(line['values'] == [1.5, .025] for line in samples))
        self.assertEqual(steps[0]['recorded_observation'], [101.,.025])
        self.assertEqual(steps[0]['target'], [1.,0.])
        final_steps = [step for step in steps if step['frame_index'] == 2]
        self.assertGreater(len(final_steps), 1)
        self.assertAlmostEqual(final_steps[-1]['elapsed_s'], .06 + 1/30)
        self.assertTrue(all(step['target'] == [3.,.02] for step in final_steps))
        self.assertEqual(lines[0]['last_frame_timestamp_s'], .06)
        self.assertAlmostEqual(lines[0]['duration_s'], .06 + 1/30)
        self.assertEqual(replay.sample(episode, .029)[0], 0)
        self.assertEqual(replay.sample(episode, .03)[0], 1)

    def test_slow_nonfinite_feedback_does_not_delay_commands(self):
        clock = Clock()
        episode = {'path':'example.parquet', 'episode':0, 'fps':30, 'names':['left','grip'],
                   'times':[i/30 for i in range(6)], 'actions':[[float(i), .01] for i in range(6)],
                   'duration_s': .2, 'recorded_states':[[0.,0.]]*6}
        class SlowRobot(FakeRobot):
            def get_joint_states(self):
                time.sleep(.08)
                return {'states':[{'name':'left', 'motor_position':float('nan')}]}
        robot = SlowRobot()
        sdk = SimpleNamespace(gdk_init=lambda:0, gdk_release=lambda:None, Robot=lambda:robot,
            GDKRes=SimpleNamespace(kSuccess=0), MotionControlMode=SimpleNamespace,
            JointServoControlReq=SimpleNamespace, InputSource=SimpleNamespace(INPUT_GDK=1),
            Target=SimpleNamespace(TARGET_ARMS=2), ControlMode=SimpleNamespace(CTRL_JOINT_POSITION=3),
            SafeMode=SimpleNamespace(SAFE_NORMAL=4))
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/'trace.jsonl'
            result = replay.execute(episode, {}, path, sdk, clock, handoff_fn=lambda _: {})
            lines = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(result['unsent_frame_count'], 0)
        self.assertGreaterEqual(result['send_hz'], 95)
        self.assertEqual({x[1][0] for x in robot.requests}, set(float(i) for i in range(6)))
        samples = [line for line in lines if line['type'] == 'feedback']
        self.assertTrue(any(s['values'] == [None, .025] and s['nonfinite_names'] == ['left'] for s in samples))

    def test_slow_servo_reports_skipped_frames(self):
        clock = Clock()
        episode = {'path':'example.parquet', 'episode':0, 'fps':30, 'names':['left'],
                   'times':[i/30 for i in range(30)], 'actions':[[float(i)] for i in range(30)],
                   'duration_s': 1., 'recorded_states':[[0.]]*30}
        class SlowRobot(FakeRobot):
            def joint_servo_control(self, request, low):
                self.requests.append((request.joint_names[:], request.joint_positions[:], request.control_period, low))
                clock.sleep(.08)
                return 0
        robot = SlowRobot()
        sdk = SimpleNamespace(gdk_init=lambda:0, gdk_release=lambda:None, Robot=lambda:robot,
            GDKRes=SimpleNamespace(kSuccess=0), MotionControlMode=SimpleNamespace,
            JointServoControlReq=SimpleNamespace, InputSource=SimpleNamespace(INPUT_GDK=1),
            Target=SimpleNamespace(TARGET_ARMS=2), ControlMode=SimpleNamespace(CTRL_JOINT_POSITION=3),
            SafeMode=SimpleNamespace(SAFE_NORMAL=4))
        with TemporaryDirectory() as tmp:
            result = replay.execute(episode, {}, Path(tmp)/'trace.jsonl', sdk, clock, handoff_fn=lambda _: {})
        sent = {int(r[1][0]) for r in robot.requests}
        self.assertEqual(result['unsent_frame_indices'], [i for i in range(30) if i not in sent])
        self.assertGreater(result['unsent_frame_count'], 0)
        self.assertLess(result['send_hz'], 20)


if __name__ == '__main__': unittest.main()
