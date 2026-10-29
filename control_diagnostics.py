"""Passive diagnostics only: registry reads, status subscription and log GETs."""
import base64
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import re
import socket
import sys
import time
import urllib.request
from native import ROOT,Framer,discover

def subscribers(host):
    enc=lambda s:base64.b64encode(s.encode()).decode()
    request=urllib.request.Request(f'http://{host}:2379/v3/kv/range',
        data=json.dumps({'key':enc('/subscribers/'),'range_end':enc('/subscribers0')}).encode(),
        headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=3) as f:result=json.load(f)
    return [json.loads(base64.b64decode(k['value'])) for k in result.get('kvs',[])]

def read_action_source(host,publishers):
    pub=next(p for p in publishers if p['topic']=='/arbitrator/action_src' and p['exe_name']=='arbitrator_runner')
    sys.path.append(str(Path('/home/agi/app/gdk/lib') if Path('/home/agi/app/gdk/lib').is_dir() else ROOT/'vendor/robot-sdk-3.1.1/app/gdk/lib'))
    from fsm_msgs_pb.action_src_pb2 import ActionSrcState,ActionSrcStatus
    deadline=time.monotonic()+2
    with socket.create_connection((host,pub['port']),timeout=2) as sock:
        framer=Framer()
        while time.monotonic()<deadline:
            sock.settimeout(max(.01,deadline-time.monotonic()))
            data=sock.recv(65536)
            if not data:break
            for frame in framer.feed(data):
                msg=ActionSrcState.FromString(frame[128:])
                return {'status':ActionSrcStatus.Name(msg.status),'source_timestamp':str(msg.timestamp),'publisher_pid':pub['pid']}
    raise TimeoutError('仲裁状态消息未收到')

def parse_arbitrator(text):
    floors=re.findall(r"group 'control': floor (\d+) -> (\d+)",text)
    accepted=re.findall(r'ACCEPTED: /gdk/joint_control -> /wbc/joint_control \(pri=(\d+)',text)
    evidence=[l for l in text.splitlines() if "group 'control': floor" in l or 'ACCEPTED: /gdk/joint_control -> /wbc/joint_control ' in l]
    return {'floor':int(floors[-1][1]) if floors else None,
            'gdk_priority':int(accepted[-1]) if accepted else None,'evidence':evidence[-3:]}

def read_arbitrator(host,publishers):
    pub=next(p for p in publishers if p['exe_name']=='arbitrator_runner')
    base=f'http://{host}:8849'
    with urllib.request.urlopen(base+'/latest/',timeout=3) as f:listing=f.read().decode()
    pattern=r"/latest/arbitrator_runner\.log\.INFO\.\d{8}-\d{6}\."+str(pub['pid'])+r"\.\d+"
    paths=sorted(set(re.findall(pattern,listing)),reverse=True)
    if not paths:raise ValueError('未找到当前仲裁进程日志')
    url=base+paths[0]
    with urllib.request.urlopen(urllib.request.Request(url,method='HEAD'),timeout=3) as f:size=int(f.headers.get('Content-Length',0))
    request=urllib.request.Request(url,headers={'Range':f'bytes={max(0,size-4*1024*1024)}-'})
    with urllib.request.urlopen(request,timeout=3) as f:text=f.read().decode(errors='replace')
    result=parse_arbitrator(text);result['log_path']=paths[0];result['publisher_pid']=pub['pid']
    return result

def assess(latest,routes,action,arbitrator):
    now=time.monotonic_ns();joint=latest.get('/hal/joint_state');mc=latest.get('/wbc/motion_control_status')
    fresh=bool(joint and mc and now-joint['mono_ns']<1e9 and now-mc['mono_ns']<1e9)
    md=(mc or {}).get('data',{});jd=(joint or {}).get('data',{})
    body=latest.get('/hal/whole_body_status',{}).get('data',{})
    errors=[f"{n}: {c}" for n,c in zip(jd.get('name',[]),jd.get('error_code',[])) if c]
    if md.get('error_code'):errors.append('运控错误 '+str(md['error_code']))
    for key in ['left_arm_estop','right_arm_estop']:
        if body.get(key):errors.append(key+'=true')
    floor=arbitrator.get('floor');priority=arbitrator.get('gdk_priority')
    locked=floor is not None and priority is not None and floor>priority
    taken=action.get('status') in ('TAKEOVER','BACKWARD','MODEL')
    if locked or taken:
        state='occupied';title='检测到控制占用'
    elif errors:
        state='attention';title='机器人反馈存在错误'
    elif not fresh or not all(routes.values()) or floor is None or priority is None or not action:
        state='unknown';title='检测信息不足'
    else:
        state='clear';title='未发现遥操 / 模型占用'
    return {'status':state,'title':title,'feedback_fresh':fresh,'motion_control_mode':md.get('mode'),
            'reported_errors':errors,'arbitration_floor_blocks_gdk':locked,'can_control':None,
            'motion_verified':False,'advisory_only':True,
            'note':'这是检测时刻的只读状态，不发送试动作、不切换模式或释放遥操；实际运动能力尚未验证。检测结果不限制采集、回放或姿态恢复。'}

def diagnose_control(host,latest):
    failures=[];pubs=[];subs=[];action={};arbitrator={}
    try:pubs=discover(host);subs=subscribers(host)
    except Exception as exc:failures.append('读取通道：'+str(exc))
    routes={'sdk_to_arbitrator':any(p.get('exe_name')=='arbitrator_runner' and p.get('topic')=='/gdk/joint_control' for p in subs),
            'arbitrator_to_motion_control':any(p.get('exe_name')=='arbitrator_runner' and p.get('topic')=='/wbc/joint_control' for p in pubs) and
                any(p.get('exe_name')=='genie_motion_control' and p.get('topic')=='/wbc/joint_control' for p in subs)}
    if pubs:
        try:action=read_action_source(host,pubs)
        except Exception as exc:failures.append('读取控制来源：'+str(exc))
        try:arbitrator=read_arbitrator(host,pubs)
        except Exception as exc:failures.append('读取仲裁门槛：'+str(exc))
    result=assess(latest() if callable(latest) else latest,routes,action,arbitrator)
    if failures and result['status']=='clear':result.update(status='unknown',title='检测信息不足')
    return dict(result,checked_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'),
                routes=routes,action_source=action,arbitrator=arbitrator,read_errors=failures)
