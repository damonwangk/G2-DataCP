#!/usr/bin/env python3
"""Play an R2 LeRobot v2.1 episode's action column through the GDK SDK."""
import argparse
from bisect import bisect_right
from datetime import datetime
import importlib
import json
import math
from pathlib import Path
import queue
import signal
import threading
import time


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
    table = pq.read_table(path, columns=['timestamp', 'action', 'observation.state'])
    rows = table.to_pylist()
    if not rows:
        raise ValueError('episode 没有数据')
    timestamps = [float(r['timestamp']) for r in rows]
    origin = timestamps[0]
    times = [t - origin for t in timestamps]
    if any(b < a for a, b in zip(times, times[1:])):
        raise ValueError('episode 时间戳未按时间顺序排列')
    actions = [list(r['action']) for r in rows]
    states = [list(r['observation.state']) for r in rows]
    if any(len(a) != len(names) for a in actions):
        raise ValueError('action 数据维数与 metadata 不一致')
    fps = float(info['fps'])
    if fps <= 0:
        raise ValueError('数据集帧率必须大于零')
    return {'root': str(root), 'episode': int(episode), 'path': str(path),
            'fps': fps, 'duration_s': times[-1] + 1.0 / fps,
            'names': names, 'times': times, 'actions': actions,
            'recorded_states': states}


def sample(episode, elapsed):
    """Return last action at or before elapsed; no interpolation or clipping."""
    index = max(0, min(len(episode['times']) - 1,
                       bisect_right(episode['times'], elapsed) - 1))
    return index, episode['actions'][index]


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


def execute(episode, config, trace_path, g, clock=time, handoff_fn=handoff, sdk_api='struct', stop_event=None, progress_fn=None):
    """Live servo: log each request and observed feedback; no implicit dry run."""
    stop_event = stop_event or threading.Event()
    progress_fn = progress_fn or (lambda state: None)
    trace_path = Path(trace_path)
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    initialized = False
    count = 0
    rejected = 0
    elapsed = 0.
    started = None
    sent_frames = set()
    def progress(phase, **extra):
        wall_s = clock.monotonic() - started if started is not None else 0.
        progress_fn({'phase': phase, 'position_s': elapsed, 'duration_s': episode['duration_s'],
                     'target_hz': 100, 'send_hz': count / wall_s if wall_s > 0 else 0.,
                     'sent': count, 'rejected': rejected, **extra})
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
               'names': episode['names'], 'source_field': 'action', 'fps': episode['fps'],
               'last_frame_timestamp_s': episode['times'][-1],
               'duration_s': episode['duration_s'], 'target_hz': 100,
               'timeline': 'LeRobot timestamp', 'sampling': 'latest action held; no interpolation',
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
            def poll_feedback():
                while not feedback_stop.is_set():
                    try:
                        values = feedback(robot, episode['names'])
                        nonfinite = [name for name, value in zip(episode['names'], values)
                                     if value is not None and not math.isfinite(value)]
                        values = [value if value is None or math.isfinite(value) else None for value in values]
                        current = {'values': values, 'error': '', 'nonfinite_names': nonfinite,
                                   'observed_monotonic_s': time.monotonic()}
                    except Exception as exc:
                        current = {'values': None, 'error': str(exc), 'nonfinite_names': [],
                                   'observed_monotonic_s': time.monotonic()}
                    with feedback_lock:
                        last_feedback.update(current)
                    write({'type': 'feedback', **current})
                    feedback_stop.wait(.05)
            feedback_thread = threading.Thread(target=poll_feedback, daemon=True)
            feedback_thread.start()
            started = clock.monotonic()
            deadline = started
            last_progress = -1.
            while not stop_event.is_set():
                now = clock.monotonic()
                elapsed = max(0, min(now - started, episode['duration_s']))
                frame, target = sample(episode, elapsed)
                req = g.JointServoControlReq()
                req.control_period = .02
                req.joint_names = episode['names']
                req.joint_positions = target
                req.joint_velocities = []
                err = ''
                try:
                    code = robot.joint_servo_control(req, False)
                    if code != 0:
                        err = f'SDK return code {code}'
                except Exception as exc:
                    code = None
                    err = str(exc)
                if err:
                    rejected += 1
                sent_frames.add(frame)
                with feedback_lock:
                    observed = dict(last_feedback)
                write({'type': 'step', 'elapsed_s': elapsed, 'frame_index': frame,
                       'frame_timestamp_s': episode['times'][frame], 'target': target,
                       'recorded_observation': episode['recorded_states'][frame],
                       'robot_observation': observed['values'], 'feedback_error': observed['error'],
                       'feedback_nonfinite_names': observed['nonfinite_names'],
                       'feedback_monotonic_s': observed['observed_monotonic_s'],
                       'sdk_return': int(code) if code is not None else None, 'sdk_error': err})
                count += 1
                if elapsed - last_progress >= .1:
                    progress('正在回放')
                    last_progress = elapsed
                if elapsed >= episode['duration_s']:
                    break
                deadline += .01
                delay = deadline - clock.monotonic()
                if delay > 0:
                    clock.sleep(delay)
                else:
                    deadline = clock.monotonic()
            wall_s = clock.monotonic() - started
            skipped = [i for i in range(len(episode['times'])) if i not in sent_frames]
            result = {'completed': not stop_event.is_set() and elapsed >= episode['duration_s'],
                      'stopped_by_user': stop_event.is_set(), 'position_s': elapsed,
                      'sent': count, 'rejected': rejected,
                      'send_hz': count / wall_s if wall_s > 0 else None,
                      'unsent_frame_indices': skipped, 'unsent_frame_count': len(skipped),
                      'trace': str(trace_path)}
            write({'type': 'result', **result})
            progress('已停止' if result['stopped_by_user'] else '回放结束')
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
    parser = argparse.ArgumentParser(description='R2 LeRobot action 回放（每次一个 episode）')
    parser.add_argument('--dataset', required=True, help='含 meta/info.json 的 LeRobot 数据集根目录')
    parser.add_argument('--episode', required=True, type=int, help='episode 编号')
    parser.add_argument('--dry-run', action='store_true', help='仅检查文件，不加载 SDK、不发送动作')
    parser.add_argument('--config', default=str(Path(__file__).resolve().parent.parent / 'studio/config/robot_control.json'))
    parser.add_argument('--sdk-api', choices=('struct', 'mode-id'), default='struct')
    parser.add_argument('--trace-dir', default=str(Path(__file__).resolve().parent / 'runs'))
    parser.add_argument('--run', help='本次作业目录，保存 progress.json/result.json/trace.jsonl')
    args = parser.parse_args(argv)
    folder = Path(args.run or (Path(args.trace_dir) / datetime.now().strftime('%Y%m%d_%H%M%S_%f')))
    folder.mkdir(parents=True, exist_ok=True)
    stop_event = threading.Event()
    def stop(*_):
        stop_event.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    result = {'dataset': str(Path(args.dataset).resolve()), 'episode': args.episode,
              'completed': False, 'control_commands_sent': False, 'dry_run': args.dry_run}
    try:
        atomic_json(folder / 'progress.json', {'phase': '正在读取 LeRobot 数据', 'running': True})
        episode = load_episode(args.dataset, args.episode)
        summary = {'dataset': episode['root'], 'episode': episode['episode'], 'source': episode['path'],
            'frames': len(episode['times']), 'fps': episode['fps'],
            'last_frame_timestamp_s': episode['times'][-1], 'duration_s': episode['duration_s'],
            'action_names': episode['names'], 'first_action': episode['actions'][0],
            'first_recorded_observation': episode['recorded_states'][0], 'target_hz': 100,
            'sdk_api': args.sdk_api}
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        result.update(summary)
        if args.dry_run:
            result.update(completed=True, validated=True)
        elif stop_event.is_set():
            result.update(stopped_by_user=True)
        else:
            config = json.loads(Path(args.config).expanduser().read_text(encoding='utf-8'))
            # Import is delayed until the explicit replay action; even import can join DDS.
            atomic_json(folder / 'progress.json', {'phase': '正在加载 SDK', 'running': True})
            g = importlib.import_module('agibot_gdk')
            output = execute(episode, config, folder / 'trace.jsonl', g,
                sdk_api=args.sdk_api, stop_event=stop_event,
                progress_fn=lambda state: atomic_json(folder / 'progress.json', dict(state, running=True)))
            result.update(output)
            result['control_commands_sent'] = output.get('sent', 0) > 0
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
                     '已停止' if result.get('stopped_by_user') else '回放结束',
            'position_s': result.get('position_s', 0.), 'duration_s': result.get('duration_s', 0.),
            'send_hz': result.get('send_hz'), 'sent': result.get('sent', 0),
            'rejected': result.get('rejected', 0), 'error': result.get('error', '')})
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return return_code


if __name__ == '__main__':
    raise SystemExit(main())
