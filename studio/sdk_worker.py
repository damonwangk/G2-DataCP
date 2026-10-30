"""Executed only by an explicit robot-replay action. Writes target/feedback trace to MCAP."""
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import math
from pathlib import Path
import signal
import time
import numpy as np
from mcap.writer import Writer,CompressionType
from native import Schemas,ROOT
from robot_replay import Trajectory
from frequency import RateMeter
from control_session import release_teleop
from storage import dumps,DATA


def select_gdk_control(robot,g):
    mode=g.MotionControlMode()
    mode.input_source=g.InputSource.INPUT_GDK
    mode.target=g.Target.TARGET_ARMS
    mode.control_mode=g.ControlMode.CTRL_JOINT_POSITION
    mode.safe_mode=g.SafeMode.SAFE_NORMAL
    rc=robot.set_control_mode(mode)
    if rc!=0:raise RuntimeError(f'SDK control mode returned {rc}')
    return {'input_source':'INPUT_GDK','target':'TARGET_ARMS','control_mode':'CTRL_JOINT_POSITION','safe_mode':'SAFE_NORMAL','return_code':rc}


def run(args):
    folder=Path(args.run);folder.mkdir(parents=True,exist_ok=True)
    traj=Trajectory(args.file,Schemas(),args.start,args.duration,getattr(args,"controlled_names",None))
    import agibot_gdk as g
    call_rate=RateMeter();accepted_rate=RateMeter()
    initialized=False;writer=None;stream=None;stop=False;steps=0;errors=[];command_errors=[];paused=False
    def interrupted(*_):
        nonlocal stop
        stop=True
    signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
    trace=Path(args.trace)
    result={'source_file':str(args.file),'duration_s':traj.duration,'speed':args.speed,'completed':False,'control_commands_sent':False,'robot_motion_observed':False,'trace_file':trace.name,'sample_count':0,'controlled_names':traj.names,'target_control_hz':100}
    def progress(phase,pos):
        tmp=folder/'progress.tmp';tmp.write_bytes(dumps({'phase':phase,'position_s':pos,'duration_s':traj.duration,'paused':paused,'target_control_hz':100,'send_hz':call_rate.hz(time.monotonic()),'accepted_hz':accepted_rate.share_hz(call_rate,time.monotonic()),'command_count':steps,'sdk_rejected_count':len(command_errors),'controlled_names':traj.names}));tmp.replace(folder/'progress.json')
    try:
        if g.gdk_init()!=g.GDKRes.kSuccess:raise RuntimeError('GDK initialization failed')
        initialized=True;robot=g.Robot();time.sleep(1)
        result['control_handoff']=release_teleop()
        result['sdk_control_mode']=select_gdk_control(robot,g)
        def observed():
            values={};stamp=0
            if traj.joint_count:
                joint=robot.get_joint_states();stamp=joint['timestamp']
                values.update({v['name']:v['motor_position'] for v in joint['states']})
            if len(traj.names)>traj.joint_count:
                end=robot.get_end_state()
                for side,topic in [('left_end_state','/hal/left_ee_data'),('right_end_state','/hal/right_ee_data')]:
                    if topic not in traj.gripper_types:continue
                    data=end[side]
                    if not stamp:stamp=data.get('timestamp',end.get('timestamp',0))
                    values.update((n,v['position']) for n,v in zip(data['names'],data['end_states']))
            return stamp,[values[n] for n in traj.names]
        timestamp,current=observed();current=np.asarray(current);first=np.asarray(traj.initial)
        delta=np.abs(current-first);approach=max(1.,float(delta[:traj.joint_count].max(initial=0))/.2,float(delta[traj.joint_count:].max(initial=0))/.01)
        result['return_to_start_seconds']=approach
        stream=trace.with_suffix('.mcap.partial').open('xb');writer=Writer(stream,compression=CompressionType.ZSTD,enable_crcs=True,enable_data_crcs=True);writer.start(library='r2-mcap-studio/replay-1.0')
        sid=writer.register_schema(name='r2.ReplayStep',encoding='jsonschema',data=dumps({'type':'object','additionalProperties':True}));cid=writer.register_channel(topic='/replay/target_and_feedback',message_encoding='json',schema_id=sid)
        writer.add_metadata('trajectory',{'source':str(args.file),'semantics':'raw measured positions; MCAP receive-time timeline; latest sample held at 100 Hz','names':json.dumps(traj.names),'control_hz':'100','low_latency':'false'})
        phase='返回起始姿态';pos=0.;approach_pos=0.;target=current.tolist();deadline=time.monotonic();last_loop=deadline;last_sample_stamp=None;last_fresh=deadline;last_progress=0.
        while not stop:
            now=time.monotonic();dt=now-last_loop;last_loop=now
            control_path=folder/'control.json'
            if control_path.exists():
                control=json.loads(control_path.read_text());stop=control.get('stop',False);paused=control.get('paused',False)
            if stop:break
            if not paused:
                if phase=='返回起始姿态':
                    approach_pos=min(approach,approach_pos+dt);u=approach_pos/approach;u=u*u*(3-2*u);target=(current+(first-current)*u).tolist()
                    if approach_pos>=approach:phase='全过程回放';pos=0.
                else:
                    pos=min(traj.duration,pos+dt*args.speed);target=traj.at(pos)
            req=g.JointServoControlReq();req.control_period=.02;req.joint_names=traj.names;req.joint_positions=target;req.joint_velocities=[]
            sdk_error=''
            try:
                rc=robot.joint_servo_control(req,False)
                if rc!=0:sdk_error=f'SDK return code {rc}'
                else:result['control_commands_sent']=True
            except Exception as exc:
                rc=None;sdk_error=str(exc)
            called_at=time.monotonic();call_rate.add(called_at)
            if not sdk_error:accepted_rate.add(called_at)
            if sdk_error:command_errors.append({'position_s':pos,'error':sdk_error,'names':traj.names,'target':target})

            stamp,feedback=observed()
            if stamp!=last_sample_stamp:last_fresh=now;last_sample_stamp=stamp
            if np.max(np.abs(np.asarray(feedback)-current))>.001:result['robot_motion_observed']=True
            error=np.asarray(feedback)-target
            if phase=='全过程回放' and not paused:errors.append(error.tolist())
            record={'phase':phase,'paused':paused,'position_s':pos,'names':traj.names,'target':target,'observed':feedback,'error':error.tolist(),'observed_source_ns':str(stamp),'receive_monotonic_ns':str(time.monotonic_ns()),'return_code':rc,'sdk_error':sdk_error}
            wall=time.time_ns();writer.add_message(channel_id=cid,log_time=wall,publish_time=wall,sequence=steps,data=dumps(record));steps+=1
            if now-last_progress>.1:progress('暂停（保持当前目标）' if paused else phase,pos);last_progress=now
            if phase=='全过程回放' and pos>=traj.duration and not paused:result['completed']=True;break
            deadline+=.01
            delay=deadline-time.monotonic()
            if delay>0:time.sleep(delay)
            else:deadline=time.monotonic()
        result['stopped_by_user']=stop
        if errors:
            e=np.asarray(errors);joint_errors=e[:,:traj.joint_count];grip_errors=e[:,traj.joint_count:]
            result['joint_tracking_rmse_rad']=float(np.sqrt(np.mean(joint_errors**2))) if traj.joint_count else None
            result['joint_tracking_max_abs_rad']=float(np.abs(joint_errors).max(initial=0)) if traj.joint_count else None
            result['gripper_tracking_max_abs_native']=np.max(np.abs(grip_errors),axis=0).tolist()
        result['tracking_passed']=bool(result['completed'] and errors and
            (result.get('joint_tracking_max_abs_rad') or 0)<.15 and
            max(result.get('gripper_tracking_max_abs_native',[]),default=0)<.005)
    except Exception as exc:
        result['error']=str(exc)
    finally:
        result['sample_count']=steps
        result['send_average_hz']=call_rate.average()
        result['accepted_average_hz']=call_rate.average()*accepted_rate.count/call_rate.count if call_rate.count else 0.
        result['sdk_rejected_count']=len(command_errors)
        result['sdk_rejected_commands']=command_errors
        if writer:
            writer.add_attachment(create_time=time.time_ns(),log_time=time.time_ns(),name='replay_result.json',media_type='application/json',data=dumps(result));writer.finish();stream.close();trace.with_suffix('.mcap.partial').rename(trace)
        if initialized:g.gdk_release()
        (folder/'result.json').write_bytes(dumps(result))
        print(json.dumps(result,ensure_ascii=False),flush=True)
    return 1 if result.get('error') else 0

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--file',required=True);p.add_argument('--trace',required=True);p.add_argument('--run',required=True);p.add_argument('--start',type=float,default=0);p.add_argument('--duration',type=float,default=0);p.add_argument('--speed',type=float,default=1);p.add_argument('--names-json');a=p.parse_args();a.controlled_names=json.loads(a.names_json) if a.names_json is not None else None
    raise SystemExit(run(a))
