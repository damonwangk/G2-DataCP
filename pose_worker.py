"""Explicit posture restore worker. Imported by offline tests without importing GDK."""
import argparse
import json
from pathlib import Path
import time
from mcap.writer import Writer,CompressionType
from postures import load_pose,JOINT_NAMES,GRIP_NAMES,GRIPS,LIMITS
from storage import dumps
from control_session import release_teleop
from sdk_worker import select_gdk_control


def execute(robot,g,pose,emit,stopping):
    target=pose['positions']
    for name,q in zip(JOINT_NAMES,target):
        if not LIMITS[name]['min']-1e-7<=q<=LIMITS[name]['max']+1e-7:raise ValueError('保存姿态超出配置限位：'+name)
    if any(pose['gripper_types'].get(t)!=1 for t in GRIPS):raise ValueError('当前恢复适配大寰 type=1 夹爪')
    def observed():
        j=robot.get_joint_states();e=robot.get_end_state()
        if any(s['error_code'] for s in j['states']):raise RuntimeError('本体关节报告错误')
        values={s['name']:s['motor_position'] for s in j['states']}
        for side,name in zip(['left_end_state','right_end_state'],GRIP_NAMES):
            end=e[side]
            if end['type']!=1 or end['names']!=[name]:raise ValueError('夹爪类型或名称不匹配')
            if end['end_states'][0]['err_code']:raise RuntimeError('夹爪报告错误')
            values[name]=end['end_states'][0]['position']
        return j['timestamp'],[values[n] for n in JOINT_NAMES+GRIP_NAMES]
    def body(q):
        req=g.JointControlReq();req.life_time=1.;req.joint_names=JOINT_NAMES;req.joint_positions=list(q[:22]);req.joint_velocities=[.3]
        if robot.joint_control_request(req)!=0:raise RuntimeError('本体姿态指令发送失败')
    def grips(q):
        for side,v in zip(['left_tool','right_tool'],q[22:]):
            req=g.JointStates();req.group=side;req.target_type='dahuan';item=g.JointState();item.position=float(v);req.states=[item];req.nums=1
            if robot.move_ee_pos(req)!=0:raise RuntimeError('夹爪姿态指令发送失败')
    stamp,current=observed();timeout=max(10.,max(abs(a-b) for a,b in zip(current[:22],target[:22]))/.2+8.)
    body(target)
    try:
        for phase,indices,tolerance in [('恢复本体姿态',range(22),.015),('恢复夹爪姿态',range(22,24),.001)]:
            if phase=='恢复夹爪姿态':grips(target)
            started=time.monotonic();stable=0;last_stamp=None;fresh_at=started
            while True:
                stamp,feedback=observed();now=time.monotonic()
                if stamp!=last_stamp:fresh_at=now;last_stamp=stamp
                elif now-fresh_at>1:raise RuntimeError('关节反馈超过 1 秒未更新')
                emit(phase,stamp,target,feedback)
                if stopping():
                    body(feedback);grips(feedback)
                    return {'completed':False,'stopped_by_user':True,'tracking_passed':False}
                stable=stable+1 if all(abs(feedback[i]-target[i])<=tolerance for i in indices) else 0
                if stable>=3:break
                if now-started>timeout:raise RuntimeError(phase+'超时，实际位置尚未到位')
                time.sleep(.05)
        return {'completed':True,'tracking_passed':True,'stopped_by_user':False,
                'final_error':[a-b for a,b in zip(feedback,target)]}
    except Exception:
        # End a pending non-servo point move by replacing its target with measured posture.
        try:
            _,feedback=observed();body(feedback);grips(feedback)
        except Exception:pass
        raise


def run(args):
    folder=Path(args.run);pose=load_pose(args.pose);trace=Path(args.trace)
    result={'kind':'posture','pose_file':args.pose,'completed':False,'robot_motion_tested':False,
            'trace_file':str(trace),'base_drive_restored':False};initialized=False
    with trace.with_suffix('.mcap.partial').open('xb') as f:
        w=Writer(f,compression=CompressionType.ZSTD,enable_crcs=True,enable_data_crcs=True);w.start(library='r2-mcap-studio/posture-restore-1')
        sid=w.register_schema(name='r2.PostureRestoreStep',encoding='jsonschema',data=dumps({'type':'object','additionalProperties':True}));cid=w.register_channel(topic='/posture_restore/target_and_feedback',message_encoding='json',schema_id=sid)
        def emit(phase,stamp,target,feedback):
            now=time.time_ns();w.add_message(channel_id=cid,log_time=now,publish_time=now,data=dumps({'phase':phase,'source_ns':str(stamp),'names':pose['names'],'target':target,'observed':feedback}))
            tmp=folder/'progress.tmp';tmp.write_bytes(dumps({'phase':phase,'position_s':0,'duration_s':0}));tmp.replace(folder/'progress.json')
            result['robot_motion_tested']=True
        def stopping():return json.loads((folder/'control.json').read_text()).get('stop',False)
        try:
            import agibot_gdk as g
            if g.gdk_init()!=g.GDKRes.kSuccess:raise RuntimeError('GDK initialization failed')
            initialized=True;robot=g.Robot();time.sleep(1)
            result['control_handoff']=release_teleop()
            result['sdk_control_mode']=select_gdk_control(robot,g)
            if stopping():result['stopped_by_user']=True
            else:result.update(execute(robot,g,pose,emit,stopping))
        except Exception as e:result['error']=str(e)
        finally:
            if initialized:g.gdk_release()
            w.add_attachment(create_time=time.time_ns(),log_time=time.time_ns(),name='restore_result.json',media_type='application/json',data=dumps(result));w.finish()
    trace.with_suffix('.mcap.partial').rename(trace);(folder/'result.json').write_bytes(dumps(result))
    return 1 if result.get('error') else 0

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--pose',required=True);p.add_argument('--run',required=True);p.add_argument('--trace',required=True)
    raise SystemExit(run(p.parse_args()))
