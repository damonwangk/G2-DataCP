"""Display-only SDK bounds. Does not change or reject recorder packets."""
import hashlib
import json
import math
import struct
import threading
import time
from native import ROOT

SOURCE=ROOT/'config/joint_limits_source.json'
CONFIG=json.loads(SOURCE.read_text())
SHA256=hashlib.sha256(SOURCE.read_bytes()).hexdigest()
JOINT='/hal/joint_state'
GRIPS=['/hal/left_ee_data','/hal/right_ee_data']
JOINT_NAMES=([f'idx{i:02d}_body_joint{i}' for i in range(1,6)]+
 [f'idx{10+i}_head_joint{i}' for i in range(1,4)]+
 [f'idx{20+i}_arm_l_joint{i}' for i in range(1,8)]+
 [f'idx{60+i}_arm_r_joint{i}' for i in range(1,8)])
BOUNDS={n:dict(CONFIG[n],topic=JOINT,field='motor_position',unit='rad') for n in JOINT_NAMES}
for side,topic in zip(['left_tool','right_tool'],GRIPS):
 for bound in CONFIG['end_effector']['dahuan'][side]:
  BOUNDS[bound['name']]=dict(bound,topic=topic,field='end_state[].position',unit='原生行程值')
WARNING_RATIO=.05

def float32(value):
    try:return struct.unpack('<f',struct.pack('<f',value))[0]
    except OverflowError:return math.copysign(math.inf,value)

def classify(name,q,fresh=True):
    b=BOUNDS[name];low,high=b['min'],b['max']
    item=dict(name=name,position=q,lower=low,upper=high,margin=WARNING_RATIO*(high-low),
              unit=b['unit'],field=b['field'],topic=b['topic'],fresh=fresh,sdk_rejected=None)
    if q is None or not isinstance(q,(int,float)) or not math.isfinite(q):
        item.update(position=None,status='unknown',side='',distance=None);return item
    distance=min(q-low,high-q);x,lo,hi=map(float32,[q,low,high])
    rejected=x<lo or x>hi
    state='over' if rejected else 'limit' if x in (lo,hi) else 'near' if distance<=item['margin'] else 'normal'
    item.update(status=state if fresh else 'stale',sdk_rejected=rejected if fresh else None,
                side='lower' if q-low<=high-q else 'upper',distance=distance)
    return item

def event_values(event):
    topic=event.get('topic');d=event.get('data') or {}
    if topic==JOINT:values=d.get('motor_position',[])
    elif topic in GRIPS:values=[m.get('position') for m in d.get('end_state',[])]
    else:return {}
    return {n:v for n,v in zip(d.get('name',[]),values) if n in BOUNDS and BOUNDS[n]['topic']==topic}

def limit_status(latest):
    now=time.monotonic_ns();items=[]
    for name,b in BOUNDS.items():
        event=latest.get(b['topic'])
        fresh=bool(event and now-event['mono_ns']<=500_000_000)
        values=event_values(dict(event,topic=b['topic'])) if event else {}
        item=classify(name,values.get(name),fresh)
        if b['topic'] in GRIPS and event and event['data'].get('type')!=1:
            item.update(status='unknown',sdk_rejected=None)
        items.append(item)
    alerts=[i for i in items if i['status'] in ('near','limit','over')]
    return {'fresh':all(i['fresh'] for i in items),'joints':{i['name']:i for i in items if i['topic']==JOINT},
            'grippers':{i['name']:i for i in items if i['topic'] in GRIPS},
            'items':items,'alerts':alerts,'warning_ratio':WARNING_RATIO,'advisory_only':True,
            'source':'GDK 3.1.1 mc_impl_config.json / ValidateParam(float)','sha256':SHA256,
            'boundary_policy':'inclusive float32 bounds; equality is yellow, outside is red',
            'note':'仅提示原始位置按当前 SDK 直接发送时的范围风险；不修改、丢弃或阻止采集。速度、力矩和电流未作为此伺服接口的位置限位判定。'}

class LimitMonitor:
    def __init__(self):
        self.lock=threading.Lock();self.recent={}
    def observe(self,event):
        if event.get('topic') in GRIPS and event['data'].get('type')!=1:return
        for name,q in event_values(event).items():
            item=classify(name,q)
            if item['status']=='over':
                with self.lock:self.recent[name]=dict(item,mono_ns=event['mono_ns'],wall_ns=str(event['wall_ns']))
    def status(self,latest):
        out=limit_status(latest);now=time.monotonic_ns()
        with self.lock:
            out['recent_over']=[dict((k,v) for k,v in item.items() if k!='mono_ns')|{'age_s':(now-item['mono_ns'])/1e9}
                               for item in self.recent.values() if now-item['mono_ns']<5_000_000_000]
        return out
