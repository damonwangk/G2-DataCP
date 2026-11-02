# 使用说明
# 用途：MCAP回放worker，将原指令和NULL交给原GDK执行器并保存JSONL/MCAP过程。
# 用法：由 RobotJob 经 sdk-run.sh 调度，参数 --file MCAP --run 新任务目录 --trace 新MCAP路径，可选 --start/--duration/--speed/--names-json；开始会加载真实SDK并发动作。
# 详细说明：回放与姿态使用说明.md

"""显式 MCAP 回放任务：原始指令交给共用 GDK 执行器，保留空值和原时间。

不返回首帧、不插值、不从状态生成目标。JSONL 和 MCAP 执行记录同时
保留原 target（包含 null）、实际发送的关节名/值以及 SDK 返回结果。
"""
import argparse
import json
from pathlib import Path
import signal
import sys
import threading
import time
from mcap.writer import Writer, CompressionType
from native import ROOT, Schemas
from robot_replay import Trajectory
from storage import dumps
sys.path.insert(0,str(ROOT.parent/'replay'))
from lerobot_replay import execute, select_control, atomic_json
from control_session import release_teleop


def select_gdk_control(robot,g):
    rc=select_control(robot,g)
    return {'input_source':'INPUT_GDK','target':'TARGET_ARMS','control_mode':'CTRL_JOINT_POSITION','safe_mode':'SAFE_NORMAL','return_code':rc}


def run(args):
    folder=Path(args.run);folder.mkdir(parents=True,exist_ok=True)
    stop=threading.Event();paused=threading.Event();finished=threading.Event()
    def interrupted(*_):stop.set()
    signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
    def controls():
        while not finished.wait(.03):
            path=folder/'control.json'
            try: control=json.loads(path.read_text()) if path.exists() else {}
            except (OSError,ValueError):continue
            if control.get('stop'):stop.set()
            if control.get('paused'):paused.set()
            else:paused.clear()
    thread=threading.Thread(target=controls,daemon=True);thread.start()
    result={'completed':False,'control_commands_sent':False,'robot_motion_observed':None}
    trace=Path(args.trace);log=folder/'trace.jsonl'
    try:
        traj=Trajectory(args.file,Schemas(),args.start,args.duration,getattr(args,'controlled_names',None))
        ep=traj.episode;ep['times']=[t/args.speed for t in ep['times']];ep['duration_s']/=args.speed
        import agibot_gdk as g
        result.update(execute(ep,{},log,g,handoff_fn=lambda _:release_teleop(),stop_event=stop,pause_event=paused,
            progress_fn=lambda p:atomic_json(folder/'progress.json',dict(p,send_hz=p.get('send_hz'),command_count=p.get('sent'),sdk_rejected_count=p.get('rejected'),controlled_names=ep['names'],paused=paused.is_set()))))
        result.update(control_commands_sent=result.get('sdk_accepted',0)>0,controlled_names=ep['names'],
            sample_count=result.get('sent',0),send_average_hz=result.get('send_hz'),sdk_rejected_count=result.get('rejected',0),
            accepted_average_hz=(result.get('sdk_accepted',0)/result['wall_duration_s'] if result.get('wall_duration_s') else 0.),
            physical_tracking_verified=False,trace_file=trace.name)
    except BaseException as exc:result['error']=str(exc) or type(exc).__name__
    finally:
        finished.set();thread.join(timeout=1)
        if log.exists():
            with trace.open('xb') as stream:
                writer=Writer(stream,compression=CompressionType.ZSTD,enable_crcs=True,enable_data_crcs=True);writer.start(library='r2-datacp/raw-replay')
                sid=writer.register_schema(name='r2.RawReplay',encoding='jsonschema',data=dumps({'type':'object'}))
                cid=writer.register_channel(topic='/replay/target_and_feedback',message_encoding='json',schema_id=sid)
                with log.open() as source:
                    for index,line in enumerate(source):
                        row=json.loads(line);stamp=time.time_ns()
                        writer.add_message(channel_id=cid,log_time=stamp,publish_time=stamp,sequence=index,data=dumps(row))
                writer.finish()
        atomic_json(folder/'result.json',result)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    return 1 if result.get('error') else 0

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--file',required=True);p.add_argument('--trace',required=True);p.add_argument('--run',required=True)
    p.add_argument('--start',type=float,default=0);p.add_argument('--duration',type=float,default=0);p.add_argument('--speed',type=float,default=1);p.add_argument('--names-json')
    a=p.parse_args();a.controlled_names=json.loads(a.names_json) if a.names_json else None
    raise SystemExit(run(a))
