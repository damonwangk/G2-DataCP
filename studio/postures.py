"""Named posture snapshots: 22 body/head/arm joints + two grippers; no chassis state."""
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import json
import math
from pathlib import Path
import time
import uuid
from mcap.writer import Writer, CompressionType
from mcap.reader import make_reader
from native import ROOT
from storage import dumps

JOINT='/hal/joint_state'
GRIPS=['/hal/left_ee_data','/hal/right_ee_data']
JOINT_NAMES=([f'idx{i:02d}_body_joint{i}' for i in range(1,6)]+
             [f'idx{10+i}_head_joint{i}' for i in range(1,4)]+
             [f'idx{20+i}_arm_l_joint{i}' for i in range(1,8)]+
             [f'idx{60+i}_arm_r_joint{i}' for i in range(1,8)])
GRIP_NAMES=['idx31_gripper_l_inner_joint1','idx71_gripper_r_inner_joint1']
NAMES=JOINT_NAMES+GRIP_NAMES
POSES=ROOT/'poses'
LIMIT_SOURCE=ROOT/'config/joint_limits_source.json'
LIMITS=json.loads(LIMIT_SOURCE.read_text())
LIMIT_HASH=hashlib.sha256(LIMIT_SOURCE.read_bytes()).hexdigest()


def capture(latest,label):
    values={};stamps={};types={}
    for topic in [JOINT,*GRIPS]:
        e=latest.get(topic)
        if not e or time.monotonic_ns()-e['mono_ns']>500_000_000:raise ValueError('实时关节/夹爪数据未就绪，无法保存姿态')
        d=e['data'];names=d.get('name',[])
        q=d.get('motor_position',[]) if topic==JOINT else [x.get('position') for x in d.get('end_state',[])]
        if len(names)!=len(q) or len(names)!=len(set(names)):raise ValueError('姿态反馈的关节名称或数组长度无效')
        values.update(zip(names,q));stamps[topic]=str(e['meta']['source_ns'])
        if topic in GRIPS:types[topic]=d.get('type')
    if any(n not in values or values[n] is None or not math.isfinite(values[n]) for n in NAMES):raise ValueError('姿态缺少关节或包含无效位置')
    return {'version':1,'label':str(label).strip()[:80] or '初始姿态',
            'saved_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
            'names':NAMES,'positions':[values[n] for n in NAMES],
            'joint_count':22,'gripper_types':types,'joint_units':'rad','gripper_units':'native',
            'source_ns':stamps,'source_clock':'robot native','position_field':'motor_position',
            'scope':['body','head','left_arm','right_arm','left_gripper','right_gripper']}


def validate_pose(pose):
    if pose.get('names')!=NAMES or pose.get('joint_count')!=22:raise ValueError('姿态必须仅含指定的 22 个本体关节和双夹爪')
    q=pose.get('positions',[])
    if len(q)!=24 or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in q):raise ValueError('姿态位置无效')
    return pose


def pose_path(identity):
    if not identity or Path(identity).name!=identity:raise ValueError('姿态编号无效')
    p=POSES/(identity+'.mcap')
    if not p.is_file():raise ValueError('未找到保存的姿态')
    return p


def save_pose(latest,label):
    pose=capture(latest,label);POSES.mkdir(exist_ok=True)
    identity=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]
    path=POSES/(identity+'.mcap');stamp=time.time_ns()
    with path.open('xb') as f:
        w=Writer(f,compression=CompressionType.ZSTD,enable_crcs=True,enable_data_crcs=True);w.start(library='r2-mcap-studio/posture-1')
        sid=w.register_schema(name='r2.Posture',encoding='jsonschema',data=dumps({'type':'object','additionalProperties':True}))
        cid=w.register_channel(topic='/posture',message_encoding='json',schema_id=sid)
        w.add_message(channel_id=cid,log_time=stamp,publish_time=stamp,data=dumps(pose));w.finish()
    return dict(pose,id=identity,path=str(path))


def load_pose(path):
    with Path(path).open('rb') as f:
        for _,_,m in make_reader(f,validate_crcs=True).iter_messages(topics=['/posture']):
            return validate_pose(json.loads(m.data))
    raise ValueError('姿态 MCAP 中没有 /posture 消息')


def list_poses():
    result=[]
    for p in sorted(POSES.glob('*.mcap'),reverse=True):
        pose=load_pose(p);result.append(dict(pose,id=p.stem,path=str(p)))
    return result


def pose_plan(path,latest):
    target=load_pose(path);current=capture(latest,'current')
    if current['gripper_types']!=target['gripper_types']:raise ValueError('当前夹爪型号与保存姿态不一致')
    delta=[abs(a-b) for a,b in zip(target['positions'],current['positions'])]
    return {'label':target['label'],'names':NAMES,'positions':target['positions'],
            'max_joint_delta_rad':max(delta[:22]),'gripper_delta_native':delta[22:],
            'scope':target['scope'],'base_drive_restored':False,'robot_motion_tested':False,
            'control_api':'joint_control_request followed by move_ee_pos; no servo mixing'}



from sdk_limits import limit_status
