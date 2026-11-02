# 使用说明
# 用途：回放模块离线回归测试，使用临时样例和Fake SDK，不发送真机动作。
# 使用：在replay目录运行 ../conversion/.venv/bin/python -m unittest -v test_clamped_smooth。
# 依赖：conversion/extra_deps及对应测试fixture；支持整目录unittest发现。
# 多文件使用说明：replay/README.md、README_clamped_smooth.md；总测试索引见../tests/README.md。
#
"""限幅平滑独立副本的离线回归：仅纯Python与Fake SDK，不导入真实SDK。"""
import copy
import contextlib
import io
import json
import math
import os
from pathlib import Path
import sys
import threading
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from clamped_smooth_targets import (SmoothTargets, float32, load_limits,
                                    offline_steps, validate_episode)
from trajectory import prepare_episode
import lerobot_replay_clamped_smooth as replay


GRIPPER = 'idx31_gripper_l_inner_joint1'
RIGHT_GRIPPER = 'idx71_gripper_r_inner_joint1'
# Synthetic test values only; never used by replay execution.
CONFIG = Path(os.environ.get('R2_TEST_GDK_LIMITS', str(
    Path(__file__).resolve().parent.parent / 'tests/fixtures/synthetic_gdk_limits.json')))


class Clock:
    def __init__(self): self.t = 0.
    def monotonic(self): return self.t
    def sleep(self, delay): self.t += delay


class FakeRobot:
    """逐条记录请求；可按序注入SDK错误，反馈故意与目标无关。"""
    def __init__(self, returns=None):
        self.requests = []
        self.returns = returns or {}
        self.mode = None

    def set_control_mode(self, mode):
        self.mode = mode
        return 0

    def joint_servo_control(self, request, low_latency):
        self.requests.append({'names': list(request.joint_names),
                              'positions': list(request.joint_positions),
                              'period': request.control_period,
                              'velocities': list(request.joint_velocities),
                              'low_latency': low_latency})
        result = self.returns.get(len(self.requests), 0)
        if isinstance(result, Exception):
            raise result
        return result

    def get_joint_states(self):
        return {'states': [{'name': 'joint', 'motor_position': 999.}]}

    def get_end_state(self):
        return {side: {'names': [name], 'end_states': [{'position': -999.}]}
                for side, name in (('left_end_state', GRIPPER),
                                   ('right_end_state', RIGHT_GRIPPER))}


def fake_sdk(robot):
    return SimpleNamespace(gdk_init=lambda: 0, gdk_release=lambda: None,
        GDKRes=SimpleNamespace(kSuccess=0), Robot=lambda: robot,
        MotionControlMode=SimpleNamespace, JointServoControlReq=SimpleNamespace,
        InputSource=SimpleNamespace(INPUT_GDK=1), Target=SimpleNamespace(TARGET_ARMS=2),
        ControlMode=SimpleNamespace(CTRL_JOINT_POSITION=3),
        SafeMode=SimpleNamespace(SAFE_NORMAL=4))


def episode(actions, times=None, names=None, duration=None):
    names = names or ['joint']
    times = times or [i*.01 for i in range(len(actions))]
    return {'root': 'fake', 'path': 'fake.parquet', 'episode': 0, 'fps': 100,
            'names': names, 'times': times, 'actions': actions,
            'recorded_states': [[999.] * len(names) for _ in actions],
            'duration_s': duration if duration is not None else times[-1] + .01}


class TestSmoothTargets(unittest.TestCase):
    def test_configuration_all_24_names_order_and_gripper_alias(self):
        from source_episode import NAMES
        names = list(reversed(NAMES))
        names[names.index(GRIPPER)] = 'left_gripper_position'
        limits, metadata = load_limits(CONFIG, names)
        self.assertEqual(len(limits), 24)
        self.assertEqual(limits[GRIPPER], (0., .025))
        self.assertEqual(limits[RIGHT_GRIPPER], (0., .025))
        self.assertEqual(limits['idx66_arm_r_joint6'], (-1.0123, 1.0123))
        self.assertEqual(len(metadata['sha256']), 64)
        smooth = SmoothTargets(names, limits)
        values = [limits[n][1] + .5 for n in smooth.names]
        result = smooth.propose(values, 0)
        for name, value in zip(smooth.names, result['target']):
            self.assertEqual(value, limits[name][1])
        self.assertEqual(values, [limits[n][1] + .5 for n in smooth.names])

    def test_configuration_unknown_invalid_and_duplicate(self):
        with self.assertRaisesRegex(ValueError, '没有关节限位'):
            load_limits(CONFIG, ['unknown'])
        with self.assertRaisesRegex(ValueError, '名称重复'):
            SmoothTargets([GRIPPER, 'left_gripper_position'], {GRIPPER: (0, .025)})
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/'limits.json'
            for bounds in ({'min': 2, 'max': 1}, {'min': 0, 'max': math.inf}):
                path.write_text(json.dumps({'joint': bounds}))
                with self.assertRaisesRegex(ValueError, '无效GDK限位'):
                    load_limits(path, ['joint'])

    def test_float32_and_double_limits_after_smoothing(self):
        limits, _ = load_limits(CONFIG, ['idx66_arm_r_joint6', GRIPPER])
        names = list(limits)
        for direction in (-1, 1):
            smooth = SmoothTargets(names, limits)
            raw = [bounds[0] - 100 if direction < 0 else bounds[1] + 100
                   for bounds in limits.values()]
            for tick in range(30):
                result = smooth.propose(raw, tick*.01)
                smooth.commit(result['target'])
                for q, (lo, hi) in zip(result['target'], limits.values()):
                    self.assertLessEqual(lo, q)
                    self.assertLessEqual(q, hi)
                    self.assertLessEqual(float32(lo), float32(q))
                    self.assertLessEqual(float32(q), float32(hi))
            for name, (lo, hi) in limits.items():
                for value in (lo, hi, math.nextafter(lo, -math.inf),
                              math.nextafter(hi, math.inf), float32(lo), float32(hi)):
                    result = SmoothTargets([name], limits).propose([value], 0)
                    q = result['target'][0]
                    self.assertTrue(lo <= q <= hi)
                    self.assertTrue(float32(lo) <= float32(q) <= float32(hi))

    def test_enter_hold_exit_and_reverse_limit(self):
        smooth = SmoothTargets(['joint'], {'joint': (-1., 1.)}, joint_speed=1., gripper_speed=.025)
        previous = None
        for tick, raw in enumerate([.95] + [1.2]*20 + [-.7]*20 + [-1.2]*200 + [.5]*200):
            row = smooth.propose([raw], tick*.01)
            value = row['target'][0]
            self.assertTrue(-1. <= value <= 1.)
            if previous is not None:
                self.assertLessEqual(abs(value-previous), .01+1e-12)
            smooth.commit(row['target'])
            previous = value
            if tick == 20:
                self.assertEqual(value, 1.)
            if tick == 21:
                self.assertAlmostEqual(value, .99)
            if tick == 240:
                self.assertEqual(value, -1.)
        self.assertEqual(previous, .5)

    def test_hold_runs_between_source_frames_and_keeps_original(self):
        raw = episode([[0.], [.5]], times=[0., .02], duration=.08)
        prepared = prepare_episode(raw)
        original = copy.deepcopy(prepared)
        rows = list(offline_steps(prepared, {'joint': (-1., 1.)}))
        held = [row for row in rows if row['frame_index'] == 1]
        self.assertGreater(len(held), 3)
        self.assertGreater(held[-1]['target'][0], held[0]['target'][0])
        self.assertEqual(prepared, original)
        self.assertAlmostEqual(rows[-1]['elapsed_s'], .08)

    def test_null_return_preserves_last_success_without_budget(self):
        smooth = SmoothTargets(['joint', GRIPPER], {'joint': (-1., 1.), GRIPPER: (0., .025)},
                               joint_speed=1., gripper_speed=.025)
        start = smooth.propose([0., .01], 0)
        smooth.commit(start['target'])
        empty = smooth.propose([None, None], 4.)
        smooth.commit(empty['target'])
        self.assertEqual(empty['target'], [None, None])
        self.assertEqual(smooth.last, [0., .01])
        back = smooth.propose([1., .025], 8.)
        self.assertAlmostEqual(back['target'][0], .01)
        self.assertAlmostEqual(back['target'][1], .01025)
        self.assertEqual(smooth.residual([None, .025]), [None, .015000000000000001])

    def test_failed_request_same_time_late_schedule_and_pause(self):
        smooth = SmoothTargets(['joint'], {'joint': (-1., 1.)}, joint_speed=1., gripper_speed=.025)
        smooth.commit(smooth.propose([0.], 0)['target'])
        failed = smooth.propose([1.], .01)
        self.assertAlmostEqual(failed['target'][0], .01)
        # 不commit模拟SDK失败；下一次仍从0开始，不累积那次失败的步长。
        retried = smooth.propose([1.], .02)
        self.assertAlmostEqual(retried['target'][0], .01)
        smooth.commit(retried['target'])
        same_time = smooth.propose([-1.], .02)
        self.assertEqual(same_time['target'], retried['target'])
        late = smooth.propose([1.], 5.)
        self.assertAlmostEqual(late['target'][0], .02)
        smooth.commit(late['target'])
        paused = smooth.propose([-1.], 10., paused=True)
        self.assertEqual(paused['target'], late['target'])
        smooth.commit(paused['target'])
        resumed = smooth.propose([-1.], 10.01)
        self.assertAlmostEqual(resumed['target'][0], .01)

    def test_default_rates_allow_point03_rad_and_point001_m_per_10ms(self):
        smooth = SmoothTargets(['joint', GRIPPER], {'joint': (-1., 1.), GRIPPER: (0., .025)})
        smooth.commit(smooth.propose([0., 0.], 0)['target'])
        moved = smooth.propose([1., .025], .01)
        self.assertAlmostEqual(moved['target'][0], .03)
        self.assertAlmostEqual(moved['target'][1], .001)

    def test_pause_does_not_introduce_axis_without_successful_history(self):
        smooth = SmoothTargets(['joint'], {'joint': (-1., 1.)})
        paused = smooth.propose([.7], 0, paused=True)
        self.assertEqual(paused['target'], [None])
        smooth.commit(paused['target'])
        self.assertEqual(smooth.last, [None])
        resumed = smooth.propose([.7], .01)
        self.assertEqual(resumed['target'], [.7])

    def test_reject_nonfinite_dimension_and_rates(self):
        for value in (math.nan, math.inf, -math.inf):
            smooth = SmoothTargets(['joint'], {'joint': (-1., 1.)})
            with self.assertRaisesRegex(ValueError, '有限数'):
                smooth.propose([value], 0)
            with self.assertRaisesRegex(ValueError, 'NaN/Inf'):
                validate_episode(prepare_episode(episode([[0.], [value]])))
        for speed in (0, -1, math.nan, math.inf):
            with self.assertRaisesRegex(ValueError, '有限正数'):
                SmoothTargets(['joint'], {'joint': (-1., 1.)}, joint_speed=speed)
        with self.assertRaisesRegex(ValueError, '维数'):
            SmoothTargets(['joint'], {'joint': (-1., 1.)}).propose([], 0)


class TestClampedSmoothExecute(unittest.TestCase):
    def run_fake(self, source, returns=None):
        robot = FakeRobot(returns)
        limits = {name: ((0., .025) if name in (GRIPPER, RIGHT_GRIPPER) else (-1., 1.))
                  for name in source['names']}
        with TemporaryDirectory() as tmp:
            trace = Path(tmp)/'trace.jsonl'
            result = replay.execute(source, {}, trace, fake_sdk(robot), clock=Clock(),
                                    handoff_fn=lambda _: {}, limits=limits,
                                    joint_speed=1., gripper_speed=.025)
            rows = [json.loads(line) for line in trace.read_text().splitlines()]
        return robot, result, rows

    def test_fake_normal_channel_and_complete_raw_null_sequence(self):
        source = episode([[0., .01], [1.2, .03], [None, None], [-.5, .005]],
                         names=['joint', GRIPPER], duration=.10)
        original = copy.deepcopy(source)
        robot, result, rows = self.run_fake(source)
        self.assertTrue(result['completed'])
        self.assertEqual(result['rejected'], 0)
        self.assertEqual(result['unsent_frame_count'], 0)
        self.assertFalse(result['physical_tracking_verified'])
        self.assertEqual(source, original)
        self.assertTrue(all(request['period'] == .01 and not request['low_latency']
                            and request['velocities'] == [] for request in robot.requests))
        steps = [row for row in rows if row['type'] == 'step']
        null_steps = [row for row in steps if row['frame_index'] == 2]
        self.assertTrue(null_steps)
        self.assertTrue(all(row['sent_names'] == [] and row['target'] == [None, None]
                            for row in null_steps))
        previous = {}
        for row in steps:
            for name, q in zip(row['sent_names'], row['sent_positions']):
                if name in previous:
                    bound = .00025 if name == GRIPPER else .01
                    self.assertLessEqual(abs(q-previous[name]), bound+1e-12)
                previous[name] = q
                self.assertTrue((0. <= q <= .025) if name == GRIPPER else (-1. <= q <= 1.))
        self.assertEqual(steps[0]['sent_positions'], [0., .01])
        self.assertTrue(any(row['recorded_action'] == [1.2, .03] for row in steps))

    def test_sdk_return_and_exception_do_not_advance_accepted_history(self):
        source = episode([[0.], [1.2]], times=[0., .01], duration=.06)
        robot, result, rows = self.run_fake(source, {2: 9, 3: RuntimeError('fake reject')})
        self.assertEqual(result['rejected'], 2)
        self.assertEqual(result['sdk_accepted'], len(robot.requests)-2)
        positions = [request['positions'][0] for request in robot.requests]
        self.assertAlmostEqual(positions[1], .01)
        self.assertAlmostEqual(positions[2], .01)
        self.assertAlmostEqual(positions[3], .01)
        self.assertGreater(positions[-1], positions[3])

    def test_same_timestamp_events_are_all_read_without_jump(self):
        source = episode([[0.], [.8], [-.8], [.5]],
                         times=[0., .01, .01, .01], duration=.05)
        robot, result, rows = self.run_fake(source)
        steps = [row for row in rows if row['type'] == 'step']
        self.assertEqual(result['unsent_frame_count'], 0)
        self.assertEqual([row['frame_index'] for row in steps[:4]], [0, 1, 2, 3])
        values = [row['target'][0] for row in steps[:4]]
        self.assertAlmostEqual(values[1], .01)
        self.assertEqual(values[1], values[2])
        self.assertEqual(values[2], values[3])

    def test_invalid_late_frame_fails_before_fake_initialization(self):
        source = episode([[0.], [math.nan]])
        sdk = fake_sdk(FakeRobot())
        with TemporaryDirectory() as tmp, patch.object(sdk, 'gdk_init') as initialize:
            with self.assertRaises(ValueError):
                replay.execute(source, {}, Path(tmp)/'trace', sdk,
                               limits={'joint': (-1., 1.)})
            initialize.assert_not_called()

    def test_all_null_never_submits_feedback_or_zero(self):
        robot, result, rows = self.run_fake(episode([[None], [None]]))
        self.assertEqual(robot.requests, [])
        self.assertEqual(result['sent'], 0)
        self.assertTrue(result['completed'])
        self.assertEqual(result['final_target_residual'], [None])

    def test_execute_initial_pause_sends_no_new_axis(self):
        paused = threading.Event()
        paused.set()
        class ResumeClock(Clock):
            def sleep(self, delay):
                super().sleep(delay)
                if self.t > 1.:
                    paused.clear()
        source = episode([[.7]], duration=.04)
        robot = FakeRobot()
        with TemporaryDirectory() as tmp:
            trace = Path(tmp)/'trace'
            result = replay.execute(source, {}, trace, fake_sdk(robot), clock=ResumeClock(),
                                    handoff_fn=lambda _: {}, limits={'joint': (-1., 1.)},
                                    pause_event=paused)
            rows = [json.loads(line) for line in trace.read_text().splitlines()]
        steps = [row for row in rows if row['type'] == 'step']
        self.assertTrue(result['completed'])
        self.assertEqual(steps[0]['sent_names'], [])
        self.assertEqual(steps[0]['target'], [None])
        self.assertEqual(robot.requests[0]['positions'], [.7])

    def test_end_residual_uses_last_success_and_does_not_append_tail(self):
        source = episode([[0.], [1.2]], times=[0., .01], duration=.03)
        robot, result, rows = self.run_fake(source, {i: 9 for i in range(2, 30)})
        self.assertTrue(result['completed'])
        self.assertEqual(result['final_target_residual'], [1.])
        self.assertLess(result['wall_duration_s'], .03+.01+1e-12)
        self.assertAlmostEqual(result['position_s'], .03)

    def test_dry_run_does_not_import_sdk(self):
        source = episode([[0.], [1.2]])
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            limits = root/'limits.json'
            limits.write_text(json.dumps({'joint': {'min': -1., 'max': 1.}}))
            with patch.object(replay, 'load_episode', return_value=source), \
                 patch.object(replay.signal, 'signal'), \
                 patch.object(replay.importlib, 'import_module',
                              side_effect=AssertionError('dry-run must not import SDK')) as sdk_import, \
                 contextlib.redirect_stdout(io.StringIO()):
                status = replay.main(['--dataset', 'fake', '--dry-run',
                                      '--gdk-limits', str(limits), '--run', str(root/'run')])
            result = json.loads((root/'run/result.json').read_text())
            sdk_import.assert_not_called()
        self.assertEqual(status, 0)
        self.assertTrue(result['validated'])
        self.assertFalse(result['control_commands_sent'])

    def test_native_and_mcap_cli_dry_run_preserve_source_timeline_and_nulls(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent/'conversion'))
        from test_mcap_input import recording
        from test_factory_input import factory_from_mcap
        original_import = replay.importlib.import_module
        def block_sdk(name, *args, **kwargs):
            self.assertFalse(name == 'agibot_gdk' or name.startswith('agibot_gdk.'))
            return original_import(name, *args, **kwargs)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mcap = root/'raw.mcap'
            native = root/'native'
            recording(mcap, delayed=True, sparse=True)
            factory_from_mcap(mcap, native)
            results = []
            for flag, source in (('--native-recording', native), ('--mcap', mcap)):
                with self.subTest(source=flag), \
                     patch.object(replay.signal, 'signal'), \
                     patch.object(replay.importlib, 'import_module', side_effect=block_sdk), \
                     contextlib.redirect_stdout(io.StringIO()):
                    run = root/('run_native' if flag == '--native-recording' else 'run_mcap')
                    status = replay.main([flag, str(source), '--dry-run',
                                          '--gdk-limits', str(CONFIG), '--run', str(run)])
                    result = json.loads((run/'result.json').read_text())
                    self.assertEqual(status, 0)
                    self.assertTrue(result['validated'])
                    self.assertFalse(result['control_commands_sent'])
                    self.assertEqual(result['source'], str(source.resolve()))
                    self.assertEqual(result['frames'], 5)
                    self.assertEqual(result['first_action'], [None]*24)
                    self.assertEqual(result['first_sdk_command'], [None]*24)
                    self.assertIn('original command Header PTP', result['timeline'])
                    results.append(result)
            self.assertEqual(results[0]['duration_s'], results[1]['duration_s'])
            self.assertEqual(results[0]['last_frame_timestamp_s'], results[1]['last_frame_timestamp_s'])
            self.assertEqual(results[0]['action_names'], results[1]['action_names'])


if __name__ == '__main__':
    unittest.main()
