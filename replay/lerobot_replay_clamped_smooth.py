#!/usr/bin/env python3
# 使用说明
# 用途：独立副本主程序，只对SDK发送目标按名限幅和逐轴限速，源数据不变。
# 入口：项目根目录 replay/replay_clamped_smooth.sh --dataset /path/to/dataset --episode 0。
# 其他输入：--native-recording /data/record/full_xxx 或 --mcap /path/to/file.mcap。
# 检查：加--dry-run；参数：--joint-speed 3 --gripper-speed 0.1；--run指定新结果目录。
# 默认每次步长最多0.03rad/0.001m；失败不推进历史，NULL不补值。
# 原时间轴截止不加收尾；末端残差见result.json，实际发送见trace.jsonl。
# 多文件使用说明：replay/README_clamped_smooth.md；首次姿态与实机跟随需另行确认。
#
"""独立回放副本：只对发送目标按当前GDK配置限幅并逐轴限速，保留源数据与NULL。"""
import argparse
from datetime import datetime
import importlib
import json
import math
import os
from pathlib import Path
import queue
import signal
import threading
import time
from trajectory import (PERIOD, prepare_episode, sample, canonical_name, load_sources,
                        expected_feedback, Tracking)
from clamped_smooth_targets import (SmoothTargets, load_limits, validate_episode,
                                    offline_summary)


def default_limits_path():
    return str(Path(os.environ.get('R2_SDK_APP', '/home/agi/app')) / 'gdk/config/mc_impl_config.json')


def load_episode(dataset, episode):
    import pyarrow.parquet as pq
    root = Path(dataset).expanduser().resolve()
    info = json.loads((root / 'meta/info.json').read_text(encoding='utf-8'))
    feature = info['features']['action']
    names = feature['names']
    if not names or not isinstance(names, list) or len(set(names)) != len(names):
        raise ValueError('action 的关节名称缺失或重复')
    chunk = int(episode) // int(info['chunks_size'])
    path = root / info['data_path'].format(episode_chunk=chunk, episode_index=int(episode))
    available = pq.read_schema(path).names
    cols = ['timestamp', 'action', 'observation.state']
    if 'source_timestamp_ns' in available: cols.append('source_timestamp_ns')
    table = pq.read_table(path, columns=cols)
    rows = table.to_pylist()
    if not rows:
        raise ValueError('episode 没有数据')
    if 'source_timestamp_ns' in available:
        origin = rows[0]['source_timestamp_ns']
        times = [(r['source_timestamp_ns']-origin)/1e9 for r in rows]
    else:
        origin = float(rows[0]['timestamp'])
        times = [float(r['timestamp'])-origin for r in rows]
    if any(b < a for a, b in zip(times, times[1:])):
        raise ValueError('episode 时间戳未按时间顺序排列')
    actions = [list(r['action']) for r in rows]
    states = [list(r['observation.state']) for r in rows]
    state_names = info['features'].get('observation.state', {}).get('names') or names
    state_order = {canonical_name(n): i for i, n in enumerate(state_names)}
    states = [[row[state_order[canonical_name(n)]] for n in names] for row in states]
    if any(len(a) != len(names) for a in actions):
        raise ValueError('action 数据维数与 metadata 不一致')
    fps = float(info['fps'])
    if fps <= 0:
        raise ValueError('数据集帧率必须大于零')
    sources, coverage = load_sources(root, int(episode), len(rows))
    return prepare_episode({'root': str(root), 'episode': int(episode), 'path': str(path),
            'fps': fps, 'duration_s': times[-1] + 1.0 / fps,
            'names': names, 'times': times, 'actions': actions,
            'recorded_states': states, 'action_sources': sources,
            'provenance_frames': coverage,
            'timeline': 'original source_timestamp_ns PTP' if 'source_timestamp_ns' in available else 'saved LeRobot timestamp',
            'source_field': 'action with per-frame provenance' if coverage else 'action (unannotated command)'})


def handoff(config):
    import grpc
    from rh_msgs_v4_pb import operator_event_pb2 as p
    request = p.OperatorEventRequest(timestamp=time.time_ns() // 1000000,
        operator_event=p.takeover, operator_event_detail=p.takeover_pending,
        operator_device='r2_lerobot_replay', robot_aid=config['robot_aid'])
    with grpc.insecure_channel(f"{config['host']}:{config['operator_port']}") as channel:
        call = channel.unary_unary('/rh_msgs_v4.OperatorEventService/OperatorEventService',
            request_serializer=lambda m:m.SerializeToString(),
            response_deserializer=p.OperatorEventReply.FromString)
        reply = call(request, timeout=5)
    if not reply.result:
        raise RuntimeError('厂商控制权交接失败：' + reply.result_desception)
    return {'result': bool(reply.result), 'message': reply.result_desception}


def select_control(robot, g, sdk_api='struct'):
    if sdk_api == 'struct':
        mode = g.MotionControlMode()
        mode.input_source = g.InputSource.INPUT_GDK
        mode.target = g.Target.TARGET_ARMS
        mode.control_mode = g.ControlMode.CTRL_JOINT_POSITION
        mode.safe_mode = g.SafeMode.SAFE_NORMAL
    elif sdk_api == 'mode-id':
        # GDK 3.3.8 Python API takes a numeric high-level mode (1 = joint position).
        mode = 1
    else:
        raise ValueError(f'未知的 SDK 控制模式接口: {sdk_api}')
    code = robot.set_control_mode(mode)
    if code != 0:
        raise RuntimeError(f'SDK set_control_mode 返回 {code}')
    return int(code)


def feedback(robot, names):
    values = {}
    joint = robot.get_joint_states()
    for item in joint['states']:
        values[item['name']] = item['motor_position']
    end = robot.get_end_state()
    for side in ('left_end_state', 'right_end_state'):
        data = end[side]
        values.update((name, item['position']) for name, item in zip(data['names'], data['end_states']))
    return [float(values[name]) if name in values else None for name in names]


def execute(episode, config, trace_path, g, clock=time, handoff_fn=handoff, sdk_api='struct', stop_event=None, progress_fn=None, pause_event=None,
            limits=None, joint_speed=3., gripper_speed=.1):
    """独立发送器：范围/有限数预检查，逐轴限速，保留普通GDK通道。"""
    episode = prepare_episode(episode)
    validate_episode(episode)
    if limits is None:
        limits, _ = load_limits(default_limits_path(), episode['names'])
    smooth = SmoothTargets(episode['names'], limits, joint_speed, gripper_speed)
    stop_event = stop_event or threading.Event()
    pause_event = pause_event or threading.Event()
    progress_fn = progress_fn or (lambda state: None)
    trace_path = Path(trace_path)
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    initialized = False
    count = 0
    rejected = 0
    elapsed = 0.
    maximum_lateness = 0.
    started = None
    sent_frames = set()
    clamped_steps = 0
    smoothed_steps = 0
    approach_sent = 0
    phase = '正在初始化 SDK'
    tracking = Tracking(episode['names'])
    def progress(phase, **extra):
        wall_s = clock.monotonic() - started if started is not None else 0.
        progress_fn({'phase': phase, 'position_s': elapsed, 'duration_s': episode['duration_s'],
                     'target_hz': 100, 'send_hz': count / wall_s if wall_s > 0 else 0.,
                     'sent': count, 'rejected': rejected, 'accepted_hz': (count-rejected)/wall_s if wall_s>0 else 0., **extra})
    with trace_path.open('x', encoding='utf-8') as stream:
        pending = queue.SimpleQueue()
        writer_error = []
        def drain():
            while True:
                row = pending.get()
                if row is None:
                    break
                try:
                    stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
                except Exception as exc:
                    writer_error.append(str(exc))
        writer = threading.Thread(target=drain, daemon=True)
        writer.start()
        def write(row):
            pending.put(row)
        write({'type': 'metadata', 'source': episode['path'], 'episode': episode['episode'],
               'names': episode['names'], 'source_field': episode.get('source_field','action'), 'fps': episode['fps'],
               'source_names': episode['source_names'],
               'last_frame_timestamp_s': episode['times'][-1],
               'duration_s': episode['duration_s'], 'target_hz': 100,
               'timeline': episode.get('timeline','LeRobot timestamp'),
               'null_policy': 'retain full timeline and nulls; send only existing numeric targets',
               'sampling': 'original event timeline; clamp then slew limit each send; nulls retained; no initial approach',
               'smoothing': {'joint_speed_rad_s': joint_speed, 'gripper_speed_m_s': gripper_speed,
                             'max_budget_period_s': PERIOD, 'limits_by_name': limits,
                             'history': 'only SDK accepted targets; no feedback substitution',
                             'end_policy': 'original source end; residual reported; no automatic tail'},
               'source_counts': episode['source_counts'], 'control_period_s': PERIOD,
               'sdk_api': sdk_api})
        feedback_stop = threading.Event()
        feedback_thread = None
        last_feedback = {'values': None, 'error': '', 'nonfinite_names': [], 'observed_monotonic_s': None}
        feedback_lock = threading.Lock()
        try:
            progress('正在初始化 SDK')
            if stop_event.is_set():
                return {'completed': False, 'stopped_by_user': True, 'sent': 0, 'rejected': 0}
            if g.gdk_init() != g.GDKRes.kSuccess:
                raise RuntimeError('GDK initialization failed')
            initialized = True
            robot = g.Robot()
            clock.sleep(1)
            if stop_event.is_set():
                return {'completed': False, 'stopped_by_user': True, 'sent': 0, 'rejected': 0}
            progress('正在交接控制权')
            handoff_result = handoff_fn(config)
            if stop_event.is_set():
                return {'completed': False, 'stopped_by_user': True, 'sent': 0, 'rejected': 0}
            mode_result = select_control(robot, g, sdk_api)
            write({'type': 'setup', 'control_handoff': handoff_result, 'sdk_api': sdk_api,
                   'control_mode_return': mode_result})
            approach_duration = 0.
            def poll_feedback():
                while not feedback_stop.is_set():
                    try:
                        values = feedback(robot, episode['names'])
                        nonfinite = [name for name, value in zip(episode['names'], values)
                                     if value is not None and not math.isfinite(value)]
                        values = [value if value is None or math.isfinite(value) else None for value in values]
                        current = {'values': values, 'error': '', 'nonfinite_names': nonfinite,
                                   'observed_monotonic_s': clock.monotonic()}
                    except Exception as exc:
                        current = {'values': None, 'error': str(exc), 'nonfinite_names': [],
                                   'observed_monotonic_s': clock.monotonic()}
                    with feedback_lock:
                        last_feedback.update(current)
                    write({'type': 'feedback', **current})
                    feedback_stop.wait(.05)
            feedback_thread = threading.Thread(target=poll_feedback, daemon=True)
            feedback_thread.start()
            started = clock.monotonic()
            replay_started = started
            next_frame = 0
            deadline = started
            last_progress = -1.
            last_loop = started
            while not stop_event.is_set():
                now = clock.monotonic()
                approaching = False
                paused = pause_event.is_set()
                phase = '暂停（保持原目标）' if paused else '正在回放'
                if not paused: elapsed = max(0., min(elapsed+now-last_loop, episode['duration_s']))
                last_loop = now
                # 每条到期原帧都至少读取一次；调度迟到也不跳过中间原帧。
                if not paused and next_frame < len(episode['times']) and episode['times'][next_frame] <= elapsed+1e-12:
                    frame = next_frame; next_frame += 1
                    target = list(episode['command_targets'][frame])
                else:
                    frame, target = sample(episode, elapsed)
                lateness = max(0.,now-replay_started-episode['times'][frame])
                maximum_lateness=max(maximum_lateness,lateness)
                # 每次保持/原始事件都重新计算，失败不推进已发送历史。
                change = smooth.propose(target, now, paused=paused)
                target = change['target']
                clamped_steps += bool(change['clamped_names'])
                smoothed_steps += bool(change['smoothed_names'])
                active = [(n,q) for n,q in zip(episode['names'], target) if q is not None]
                req = g.JointServoControlReq()
                req.control_period = PERIOD
                req.joint_names = [n for n,q in active]
                req.joint_positions = [q for n,q in active]
                req.joint_velocities = []
                err = ''
                try:
                    code = robot.joint_servo_control(req, False) if active else None
                    if code is not None and code != 0:
                        err = f'SDK return code {code}'
                except Exception as exc:
                    code = None
                    err = str(exc)
                if err:
                    rejected += 1
                elif active:
                    smooth.commit(target)
                if not approaching:
                    sent_frames.add(frame)
                with feedback_lock:
                    observed = dict(last_feedback)
                if not approaching:
                    tracking.add(target, observed['values'])
                write({'type': 'step', 'phase': phase, 'elapsed_s': elapsed, 'frame_index': frame,
                       'frame_timestamp_s': episode['times'][frame], **change,
                       'sent_names': req.joint_names, 'sent_positions': req.joint_positions,
                       'send_monotonic_s': now, 'source_lateness_s': lateness,
                       'null_names': [n for n,q in zip(episode['names'],target) if q is None],
                       'recorded_observation': episode['recorded_states'][frame],
                       'recorded_action': episode['actions'][frame],
                       'action_sources': episode['source_kinds'][frame],
                       'expected_feedback': expected_feedback(episode['names'], target),
                       'robot_observation': observed['values'], 'feedback_error': observed['error'],
                       'feedback_nonfinite_names': observed['nonfinite_names'],
                       'feedback_monotonic_s': observed['observed_monotonic_s'],
                       'sdk_return': int(code) if code is not None else None, 'sdk_error': err})
                count += int(bool(active))
                if now - last_progress >= .1:
                    progress(phase, approach_duration_s=0.)
                    last_progress = now
                if elapsed >= episode['duration_s'] and next_frame >= len(episode['times']):
                    break
                # 原始事件按原时刻发送；事件间隔内按100 Hz保持已有目标。
                delay = PERIOD
                if not paused and next_frame < len(episode['times']):
                    delay = min(delay, max(0., episode['times'][next_frame]-elapsed))
                delay -= clock.monotonic()-now
                if delay > 0: clock.sleep(delay)
            wall_s = clock.monotonic() - started
            skipped = [i for i in range(len(episode['times'])) if i not in sent_frames]
            result = {'completed': not stop_event.is_set() and elapsed >= episode['duration_s'] and next_frame >= len(episode['times']),
                      'stopped_by_user': stop_event.is_set(), 'position_s': elapsed,
                      'sent': count, 'rejected': rejected,
                      'sdk_accepted': count-rejected,
                      'approach_sent': approach_sent, 'replay_sent': count-approach_sent,
                      'approach_duration_s': approach_duration,
                      'tracking': tracking.summary(), 'physical_tracking_verified': False,
                      'send_hz': count / wall_s if wall_s > 0 else None,
                      'unsent_frame_indices': skipped, 'unsent_frame_count': len(skipped),
                      'trace': str(trace_path), 'wall_duration_s': wall_s, 'source_duration_s': episode['duration_s'],
                      'max_schedule_lateness_s': maximum_lateness}
            result.update(clamped_steps=clamped_steps, smoothed_steps=smoothed_steps,
                          final_target_residual=smooth.residual(episode['command_targets'][-1]))
            write({'type': 'result', **result})
            progress('已停止' if result['stopped_by_user'] else '发送完成（跟随见反馈）')
            return result
        except BaseException as exc:
            write({'type': 'result', 'completed': False, 'sent': count, 'rejected': rejected,
                   'error': str(exc) or type(exc).__name__})
            raise
        finally:
            feedback_stop.set()
            if feedback_thread:
                feedback_thread.join(timeout=2)
            try:
                if initialized:
                    if feedback_thread and feedback_thread.is_alive():
                        write({'type': 'warning', 'message': '反馈读取仍在等待，跳过 SDK 释放；并发行为未经实机验证'})
                    else:
                        g.gdk_release()
            finally:
                pending.put(None)
                writer.join()
                if writer_error:
                    raise OSError('回放日志写入失败：' + writer_error[0])


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description='独立限幅平滑回放：按当前GDK限位截断，再逐轴限速（原数据不变）')
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--dataset', help='含 meta/info.json 的 LeRobot 数据集根目录')
    source.add_argument('--native-recording',help='原生离线记录目录，直接读取控制指令与原时间戳')
    source.add_argument('--mcap', dest='native_recording', help='自采MCAP文件，复用原始事件读取器')
    parser.add_argument('--episode', default=0, type=int, help='episode 编号')
    parser.add_argument('--dry-run', action='store_true', help='仅检查文件，不加载 SDK、不发送动作')
    parser.add_argument('--config', default=str(Path(__file__).resolve().parent.parent / 'studio/config/robot_control.json'))
    parser.add_argument('--sdk-api', choices=('struct', 'mode-id'), default='struct')
    parser.add_argument('--trace-dir', default=str(Path(__file__).resolve().parent / 'runs_clamped_smooth'))
    parser.add_argument('--gdk-limits', default=default_limits_path(), help='当前安装GDK的mc_impl_config.json')
    parser.add_argument('--joint-speed', default=3., type=float, help='新副本平滑最大位置变化率，默认3 rad/s（非厂商限速）')
    parser.add_argument('--gripper-speed', default=.1, type=float, help='夹爪平滑最大变化率，默认0.1 m/s')
    parser.add_argument('--run', help='本次作业目录，保存 progress.json/result.json/trace.jsonl')
    args = parser.parse_args(argv)
    folder = Path(args.run or (Path(args.trace_dir) / datetime.now().strftime('%Y%m%d_%H%M%S_%f')))
    folder.mkdir(parents=True, exist_ok=True)
    stop_event = threading.Event()
    def stop(*_):
        stop_event.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    result = {'dataset': str(Path(args.dataset or args.native_recording).resolve()), 'episode': args.episode,
              'completed': False, 'control_commands_sent': False, 'dry_run': args.dry_run}
    try:
        atomic_json(folder / 'progress.json', {'phase': '正在读取 LeRobot 数据', 'running': True})
        if args.native_recording:
            from native_episode import load_native
            episode=load_native(args.native_recording)
        else:episode = load_episode(args.dataset, args.episode)
        episode = prepare_episode(episode)
        # 先读配置并验证全部目标，再允许导入真实SDK；dry-run始终纯读取。
        limits, limits_info = load_limits(args.gdk_limits, episode['names'])
        validate_episode(episode)
        SmoothTargets(episode['names'], limits, args.joint_speed, args.gripper_speed)
        summary = {'dataset': episode['root'], 'episode': episode['episode'], 'source': episode['path'],
            'frames': len(episode['times']), 'fps': episode['fps'],
            'last_frame_timestamp_s': episode['times'][-1], 'duration_s': episode['duration_s'],
            'action_names': episode['names'], 'first_action': episode['actions'][0],
            'first_sdk_command': SmoothTargets(episode['names'], limits, args.joint_speed,
                                              args.gripper_speed).propose(episode['command_targets'][0], 0.)['target'],
            'gdk_limits': limits_info,
            'smoothing': {'joint_speed_rad_s': args.joint_speed, 'gripper_speed_m_s': args.gripper_speed,
                          'max_budget_period_s': PERIOD, 'end_policy': 'original source end; no automatic tail'},
            'source_counts': episode['source_counts'], 'provenance_frames': episode.get('provenance_frames'),
            'first_recorded_observation': episode['recorded_states'][0], 'target_hz': 100,
            'sdk_api': args.sdk_api,'input_format':episode.get('input_format','lerobot'),
            'source_field':episode.get('source_field','action'),'timeline':episode.get('timeline','LeRobot timestamp')}
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        result.update(summary)
        if args.dry_run:
            result.update(offline_summary(episode, limits, args.joint_speed, args.gripper_speed),
                          completed=True, validated=True)
        elif stop_event.is_set():
            result.update(stopped_by_user=True)
        else:
            config = json.loads(Path(args.config).expanduser().read_text(encoding='utf-8'))
            # Import is delayed until the explicit replay action; even import can join DDS.
            atomic_json(folder / 'progress.json', {'phase': '正在加载 SDK', 'running': True})
            g = importlib.import_module('agibot_gdk')
            output = execute(episode, config, folder / 'trace.jsonl', g,
                sdk_api=args.sdk_api, stop_event=stop_event,
                limits=limits, joint_speed=args.joint_speed, gripper_speed=args.gripper_speed,
                progress_fn=lambda state: atomic_json(folder / 'progress.json', dict(state, running=True)))
            result.update(output)
            result['control_commands_sent'] = output.get('sdk_accepted', output.get('sent', 0)) > 0
        return_code = 0
    except BaseException as exc:
        result.update(error=str(exc) or type(exc).__name__)
        try:
            last = json.loads((folder / 'progress.json').read_text())
            result.update({k: last[k] for k in ('sent', 'rejected', 'position_s', 'duration_s', 'send_hz') if k in last})
            result['control_commands_sent'] = result.get('sent', 0) > 0
        except (OSError, ValueError):
            pass
        return_code = 1
    finally:
        atomic_json(folder / 'result.json', result)
        atomic_json(folder / 'progress.json', {'running': False,
            'phase': '检查完成' if args.dry_run and result.get('completed') else
                     '执行失败' if result.get('error') else
                     '已停止' if result.get('stopped_by_user') else '发送完成（跟随见反馈）',
            'position_s': result.get('position_s', 0.), 'duration_s': result.get('duration_s', 0.),
            'send_hz': result.get('send_hz'), 'sent': result.get('sent', 0),
            'rejected': result.get('rejected', 0), 'error': result.get('error', '')})
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return return_code


if __name__ == '__main__':
    raise SystemExit(main())
