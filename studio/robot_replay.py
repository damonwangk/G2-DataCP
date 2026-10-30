"""Full-episode measured-trajectory replay through the vendor's normal servo API."""
from bisect import bisect_right
import json
import math
from pathlib import Path
import subprocess
import threading
import time
import uuid
import numpy as np
from native import ROOT, Schemas
from storage import raw_events, bounds, dumps

JOINT='/hal/joint_state'
GRIPS=['/hal/left_ee_data','/hal/right_ee_data']


class Trajectory:
    def __init__(self,path,schemas,start_s=0,duration_s=0,controlled_names=None):
        self.path=Path(path);requested=None if controlled_names is None else set(controlled_names)
        topics=[JOINT,*GRIPS]
        if requested is not None:
            if not requested:raise ValueError('请选择要回放的关节或夹爪')
            topics=[t for t in topics if (t==JOINT and any('gripper' not in n for n in requested)) or
                    (t==GRIPS[0] and 'idx31_gripper_l_inner_joint1' in requested) or
                    (t==GRIPS[1] and 'idx71_gripper_r_inner_joint1' in requested)]
        rows={t:[] for t in topics};names={};types={}
        for e in raw_events(path,schemas,topics=topics):
            topic=e['topic']
            if topic not in rows:continue
            d=e['data'];current_names=d.get('name',[])
            selected=[n for n in current_names if requested is None or n in requested]
            if not selected:continue
            values=d.get('motor_position',[]) if topic==JOINT else [m.get('position') for m in d.get('end_state',[])]
            mapped=dict(zip(current_names,values));names.setdefault(topic,selected)
            if any(n not in mapped or mapped[n] is None for n in names[topic]):raise ValueError('所选关节缺少位置值')
            values=[mapped[n] for n in names[topic]]
            rows[topic].append((e['wall_ns'],values))
            if topic in GRIPS:types[topic]=d.get('type')
        self.topics=[t for t in topics if rows[t]]
        if not self.topics:raise ValueError('文件中没有所选动作数据；仅图像文件可使用文件预览')
        self.names=[n for t in self.topics for n in names[t]]
        self.joint_count=len(names.get(JOINT,[]));self.gripper_types=types
        self.missing_names=[] if requested is None else sorted(requested-set(self.names))
        self.origin_ns,self.end_ns=bounds(path)
        self.full_duration=max(.01,(self.end_ns-self.origin_ns)/1e9)
        self.start_s=float(start_s);self.duration=min(float(duration_s) if duration_s else self.full_duration-self.start_s,self.full_duration-self.start_s)
        if self.start_s<0 or self.duration<=0:raise ValueError('回放时间范围无效')
        self.times={t:np.array([(r[0]-self.origin_ns)/1e9 for r in rows[t]],dtype=float) for t in self.topics}
        self.values={t:np.array([r[1] for r in rows[t]],dtype=float) for t in self.topics}
        self.max_gaps={t:float(np.diff(v).max()) if len(v)>1 else 0. for t,v in self.times.items()}
        self.initial=self.at(0)
    def at(self,t):
        target=min(self.start_s+max(0,float(t)),self.start_s+self.duration);q=[]
        for topic in self.topics:
            ts,values=self.times[topic],self.values[topic]
            q.extend(values[max(0,bisect_right(ts,target)-1)].tolist())
        return q


def live_values(latest,names):
    values={}
    for topic in [JOINT,*GRIPS]:
        e=latest.get(topic)
        if not e:continue
        d=e['data'];v=d.get('motor_position',[]) if topic==JOINT else [m.get('position') for m in d.get('end_state',[])]
        values.update(zip(d.get('name',[]),v))
    if any(n not in values or values[n] is None for n in names):raise ValueError('实时反馈的关节名称与记录不匹配')
    return [values[n] for n in names]


def plan(path,schemas,start_s=0,duration_s=0,speed=1,latest=None,controlled_names=None):
    if speed<=0:raise ValueError('回放速度必须大于 0')
    trajectory=Trajectory(path,schemas,start_s,duration_s,controlled_names)
    out={'file':Path(path).parent.name if Path(path).name=='raw.mcap' else Path(path).name,'duration_s':trajectory.duration,'execution_duration_s':trajectory.duration/speed,
         'speed':speed,'target_control_hz':100,'missing_names':trajectory.missing_names,'joint_count':trajectory.joint_count,'controlled_names':trajectory.names,
         'gripper_types':trajectory.gripper_types,'source':'raw measured motor_position + native gripper position',
         'timeline':'MCAP log_time (recording-host receive time), full recording duration',
         'sample_policy':'latest original sample at each 100 Hz control tick; hold between samples; no interpolation',
         'initial_policy':'return to first recorded positions before episode clock starts; hold first/last at channel edges',
         'control_api':'joint_servo_control, 100 Hz, enable_low_latency=False','max_sample_gap_s':trajectory.max_gaps,
         'base_drive_replayed':False, 'gripper_tracking_verified':False,
         'acceptance_note':'点击执行后由厂商接口挂起遥操并切换到 GDK 关节位控；实际发送结果见执行报告。'}
    if latest is not None:
        if callable(latest): latest=latest()
        current=live_values(latest,trajectory.names);delta=np.abs(np.array(current)-trajectory.initial)
        out['start_max_joint_delta_rad']=float(delta[:trajectory.joint_count].max(initial=0))
        out['start_gripper_delta_native']=delta[trajectory.joint_count:].tolist()
        out['return_to_start_seconds']=max(1.,float(delta[:trajectory.joint_count].max(initial=0))/.2,float(delta[trajectory.joint_count:].max(initial=0))/.01)
        out['ready']=True
    return out


class RobotJob:
    def __init__(self,receiver,recorder,schemas):
        self.receiver,self.recorder,self.schemas=receiver,recorder,schemas
        self.kind='replay';self.running=False;self.preparing=False;self.phase='';self.error='';self.report=None;self.process=None;self.file=''
        self.job_id=uuid.uuid4().hex[:10]
    def start(self,path,start_s=0,duration_s=0,speed=1,controlled_names=None):
        self.file=Path(path).parent.name if Path(path).name=='raw.mcap' else Path(path).name
        self.controlled_names=controlled_names
        self.kind='replay';self.preparing=True;self.phase='正在读取原始 MCAP';self.error=''
        def latest():
            with self.receiver.lock:return dict(self.receiver.latest)
        try:
            info=plan(path,self.schemas,start_s,duration_s,speed,controlled_names=controlled_names)
            self.duration=info['duration_s'];self.position=0.;self.phase='准备回放'
            self.run=ROOT/'runtime'/('replay_'+self.job_id);self.run.mkdir(parents=True)
            self.control={'stop':False,'paused':False};self.write_control()
            self.thread=threading.Thread(target=self._run,args=(path,start_s,duration_s,speed),daemon=True)
            self.running=True;self.thread.start()
        except Exception as exc:
            self.running=False;self.phase='回放准备失败';self.error=str(exc)
            raise
        finally:self.preparing=False
        return self.status()
    def restore(self,path):
        from postures import pose_plan
        with self.receiver.lock:latest=dict(self.receiver.latest)
        info=pose_plan(path,latest)
        self.kind='posture';self.duration=0.;self.position=0.;self.running=True;self.phase='准备恢复姿态'
        self.run=ROOT/'runtime'/('posture_'+uuid.uuid4().hex[:10]);self.run.mkdir(parents=True)
        self.control={'stop':False,'paused':False};self.write_control()
        self.thread=threading.Thread(target=self._run,args=(path,0,0,1),daemon=True);self.thread.start()
        return self.status()
    def write_control(self):
        p=self.run/'control.json';tmp=p.with_suffix('.tmp');tmp.write_bytes(dumps(self.control));tmp.replace(p)
    def pause(self):
        if self.running:self.control['paused']=not self.control['paused'];self.write_control()
    def stop(self):
        if self.running:self.control['stop']=True;self.write_control()
    def _run(self,path,start_s,duration_s,speed):
        started_recording=False
        try:
            self.recorder.start(self.kind+'_feedback',self.receiver.stats(),{'kind':self.kind,'source_file':str(path)})
            started_recording=True
            trace=self.recorder.directory/(self.kind+'_trace.mcap')
            cmd=([str(ROOT/'sdk-run.sh'),str(ROOT/'pose_worker.py'),'--pose',str(path),'--run',str(self.run),'--trace',str(trace)] if self.kind=='posture' else
                 [str(ROOT/'sdk-run.sh'),str(ROOT/'sdk_worker.py'),'--file',str(path),'--run',str(self.run),'--start',str(start_s),'--duration',str(duration_s),'--speed',str(speed),'--trace',str(trace)])
            if self.kind=='replay' and self.controlled_names is not None:cmd+=['--names-json',json.dumps(self.controlled_names)]
            with (self.run/'worker.log').open('w') as log:
                self.process=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
                code=self.process.wait()
            result_path=self.run/'result.json'
            self.report=json.loads(result_path.read_text()) if result_path.exists() else {'error':(self.run/'worker.log').read_text()[-2500:]}
            if code or self.report.get('error'):self.error=self.report.get('error',f'Worker exited {code}')
            progress_path=self.run/'progress.json'
            if progress_path.exists():self.position=json.loads(progress_path.read_text()).get('position_s',0.)
            if self.report.get('completed'):self.position=self.duration
            if self.error:self.phase='执行未完成'
            elif self.report.get('stopped_by_user'):self.phase='已停止 · 未回放完'
            elif self.report.get('completed') and self.report.get('sdk_rejected_count'):self.phase=f"回放结束 · SDK 拒绝 {self.report['sdk_rejected_count']} 帧"
            elif self.report.get('completed'):self.phase='姿态恢复结束' if self.kind=='posture' else '回放完成'
            else:self.phase='执行未完成'
        except Exception as exc:
            self.error=str(exc);self.phase='回放未完成'
        finally:
            if started_recording and self.recorder.active:
                recorded=self.recorder.stop(self.receiver.stats())
                if self.report is not None:self.report['feedback_file']=recorded['file']
            self.running=False
            if self.report is not None:(self.run/'result.json').write_bytes(dumps(self.report))
    def status(self):
        out={'target_control_hz':100 if self.kind=='replay' else None,'kind':self.kind,'file':self.file,'job_id':self.job_id,'preparing':self.preparing,'running':self.running,'phase':self.phase,'error':self.error,'position_s':getattr(self,'position',0.),'duration_s':getattr(self,'duration',0.),'report':self.report}
        if self.running and (self.run/'progress.json').exists():
            try:out.update(json.loads((self.run/'progress.json').read_text()))
            except (ValueError,OSError):pass
        return out
