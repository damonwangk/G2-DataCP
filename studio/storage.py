"""Original-value MCAP recording with selected fields and indexed file playback."""
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from mcap.writer import Writer, CompressionType
from mcap.reader import make_reader
from native import ROOT, CAMERAS, decode
from record_filter import EXCLUDED_TOPICS, POLICY, selected_frame
from selection import normalize_topics
from frequency import RateMeter

DATA = ROOT / 'data'
DATA.mkdir(exist_ok=True)


def dumps(data):
    return json.dumps(data, ensure_ascii=False, separators=(',',':'), allow_nan=False).encode()


SETTINGS = ROOT / 'config' / 'workspace.json'


def data_dir():
    if SETTINGS.exists():
        return Path(json.loads(SETTINGS.read_text())['data_dir'])
    return DATA


def episode_number(directory):
    counter = Path(directory) / '.episode_sequence.json'
    recorded = json.loads(counter.read_text()).get('last_episode', 0) if counter.exists() else 0
    numbers = [int(m.group(1)) for p in Path(directory).iterdir() if (m := re.match(r'^episode_(\d+)_', p.name))]
    return max([int(recorded)] + numbers)


def remember_episode_number(directory, number):
    last = max(episode_number(directory), number)
    counter = Path(directory) / '.episode_sequence.json'
    temporary = counter.with_suffix('.tmp')
    temporary.write_bytes(dumps({'last_episode': last}))
    temporary.replace(counter)
    return last


def select_directory(value, create=False):
    p=Path(value).expanduser()
    if not p.is_absolute():raise ValueError('请输入 机器人上的绝对目录路径')
    p=p.resolve()
    if create:p.mkdir(parents=True,exist_ok=True)
    if not p.is_dir():raise ValueError('目录不存在，请先创建或选择已有目录')
    if not os.access(p,os.W_OK|os.X_OK):raise ValueError('当前用户没有该目录的写入权限')
    SETTINGS.parent.mkdir(parents=True,exist_ok=True)
    SETTINGS.write_bytes(dumps({'data_dir':str(p)}))
    return {'data_dir':str(p)}


def browse_directory(value=None):
    p=Path(value).expanduser().resolve() if value else data_dir()
    if not p.is_dir():raise ValueError('目录不存在')
    try:
        folders=[{'name':x.name,'path':str(x)} for x in sorted(p.iterdir()) if not x.name.startswith('.') and x.is_dir()]
    except PermissionError:raise ValueError('没有读取该目录的权限')
    import shutil
    return {'path':str(p),'parent':str(p.parent),'folders':folders,'free_gb':shutil.disk_usage(p).free/1024**3}


def file_path(name):
    root=data_dir().resolve()
    if not name or Path(name).name!=name:raise ValueError('请选择列表中的 episode')
    aliases=root/'.episode_aliases.json'
    if aliases.exists():name=json.loads(aliases.read_text()).get(name,name)
    p=(root/name).resolve()
    if p.parent!=root:raise ValueError('episode 不在当前目录中')
    if p.is_dir():p=(p/'raw.mcap').resolve()
    if not p.is_relative_to(root) or p.suffix!='.mcap' or not p.is_file():
        raise ValueError('当前保存目录中找不到该 episode 的 MCAP')
    return p


class Recorder:
    def __init__(self, schemas):
        self.schemas = schemas
        self.lock = threading.RLock()
        self.active = False
        self.last = None
        self.error = ''
        self.writer = None
        self.stream = None
        self.counts = Counter()
    def start(self, label, stats, context=None, topics=None):
        with self.lock:
            if self.active:
                raise ValueError('已经在录制')
            self.selected_topics=normalize_topics(topics)
            self.rates={t:RateMeter() for t in self.selected_topics}
            label = re.sub(r'[^\w.-]+','_',label,flags=re.UNICODE).strip('._')[:60] or 'episode'
            stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S_%f')
            self.context=context or {}
            self.record_kind='feedback' if self.context.get('kind') in ('replay','posture') else 'test' if self.context.get('kind')=='self_test' else 'collection'
            if self.record_kind=='collection':
                number=episode_number(data_dir())+1
                self.episode_id=f"episode_{number}_{stamp[:15]}"
            else:
                self.episode_id=f'{stamp}_{label}'
            self.directory=data_dir()/self.episode_id
            self.directory.mkdir()
            if self.record_kind=='collection':remember_episode_number(data_dir(),number)
            self.path=self.directory/'raw.mcap'
            self.context=context or {}
            self.label=label
            self.partial = self.path.with_suffix('.mcap.partial')
            self.stream = self.partial.open('xb')
            self.writer = Writer(self.stream, chunk_size=2*1024*1024, compression=CompressionType.ZSTD, enable_crcs=True, enable_data_crcs=True)
            self.writer.start(profile='', library='r2-mcap-studio/1.0')
            self.channels = {}
            self.schema_ids = {}
            self.counts = Counter()
            self.raw_bytes = 0
            self.error = ''
            self.started_ns = time.time_ns()
            self.started_mono = time.monotonic()
            self.start_stats = stats
            self.writer.add_metadata('r2.recording', {
                'label':label, 'episode_id':self.episode_id, 'robot_host':'10.42.1.101',
                'clock.log_time':'Recording-host realtime ns; playback ordering',
                'clock.publish_time':'Aorta publisher PTP ns; retained independently of recording-host clock',
                'source_time':'Native Header/camera group time retained in raw and metadata. Camera time is not exposure time.',
                'raw':POLICY['raw_semantics'],
                'selection_policy':json.dumps(POLICY),
                'selected_topics':json.dumps(self.selected_topics),
                'positions':'Joint visualization and measured trajectory use motor_position; original position also retained',
                'action_semantics':'/hal/joint_cmd is native command stream. Replay uses explicitly derived measured trajectory.',
            })
            self.writer.add_attachment(create_time=self.started_ns, log_time=self.started_ns, name='schemas.pb', media_type='application/octet-stream',data=(ROOT/'schemas.pb').read_bytes())
            self.writer.add_attachment(create_time=self.started_ns,log_time=self.started_ns,name='start_streams.json',media_type='application/json',data=dumps(stats))
            self.writer.add_attachment(create_time=self.started_ns,log_time=self.started_ns,name='selection_policy.json',media_type='application/json',data=dumps(POLICY))
            self.writer.add_attachment(create_time=self.started_ns,log_time=self.started_ns,name='episode_context.json',media_type='application/json',data=dumps(self.context))
            self.active = True
            return self.status()
    def channel(self, topic, encoding, name=None, publisher=None):
        key = (topic, (publisher or {}).get('uid',''))
        if key not in self.channels:
            sid = 0
            if name:
                if name not in self.schema_ids:
                    data = self.schemas.data[name] if encoding=='protobuf' else dumps({'type':'object','additionalProperties':True})
                    self.schema_ids[name] = self.writer.register_schema(name=name,encoding='protobuf' if encoding=='protobuf' else 'jsonschema',data=data)
                sid = self.schema_ids[name]
            meta = {'publisher':json.dumps(publisher or {}), 'clock.log_time':'recording-host realtime ns','clock.publish_time':'robot Aorta PTP ns'}
            self.channels[key] = self.writer.register_channel(topic=topic,message_encoding=encoding,schema_id=sid,metadata=meta)
        return self.channels[key]
    def ingest(self, event):
        with self.lock:
            if not self.active or event['topic'] not in self.selected_topics or event['topic'] in EXCLUDED_TOPICS:
                return
            try:
                p, meta, topic = event['publisher'],event['meta'],event['topic']
                frame,removed_fields=event['frame'],[]
                log_ns, pub_ns = event['wall_ns'],int(meta['publish_ptp_ns'])
                seq = meta['sequence'] & 0xffffffff
                def write(topic, encoding, data, name=None):
                    cid = self.channel(topic,encoding,name,p)
                    self.writer.add_message(channel_id=cid, log_time=log_ns, publish_time=pub_ns, data=data, sequence=seq)
                write('/raw'+topic, 'aorta', frame)
                write('/metadata'+topic,'json',dumps(dict(meta, receive_unix_ns=str(log_ns), receive_monotonic_ns=str(event['mono_ns']),publisher_uid=p['uid'],removed_fields=removed_fields,source_packet_bytes=len(event['frame']),stored_packet_bytes=len(frame))),'r2.PacketMetadata')
                if p['type'] != 'CosineDataPacket':
                    write(topic,'protobuf',frame[128:],p['type'])
                elif meta['encoding'] == 1:
                    msg = self.schemas.get('foxglove.CompressedImage')()
                    msg.timestamp.seconds = log_ns//1000000000
                    msg.timestamp.nanos = log_ns%1000000000
                    msg.frame_id = topic.rsplit('/',1)[-1]
                    msg.format = 'jpeg'
                    msg.data = event['image']
                    write(topic+'/image','protobuf',msg.SerializeToString(),'foxglove.CompressedImage')
                self.counts[topic] += 1
                self.rates[topic].add(event['mono_ns']/1e9)
                self.raw_bytes += len(frame)
            except Exception as exc:
                self.error = str(exc)
                self.active = False
                # Keep .partial on disk for inspection/recovery; never claim successful recording.
                try:
                    self.stream.flush()
                    self.stream.close()
                except Exception:
                    pass
    def stop(self, stats):
        with self.lock:
            if not self.active:
                if self.error:
                    raise RuntimeError(self.error)
                return self.last or {'active':False}
            self.active = False
            report = dict(file=self.episode_id, path=str(self.path), episode_dir=str(self.directory), label=self.label, record_kind=self.record_kind, context=self.context, duration_s=time.monotonic()-self.started_mono,
                          counts=dict(self.counts),raw_bytes=self.raw_bytes,start_streams=self.start_stats,end_streams=stats,
                          selected_topics=self.selected_topics,recorded_average_hz={t:r.average() for t,r in self.rates.items()},
                          robot_motion_tested=False,selection_policy=POLICY)
            self.writer.add_attachment(create_time=time.time_ns(),log_time=time.time_ns(),name='recording_report.json',media_type='application/json',data=dumps(report))
            self.writer.finish()
            self.stream.flush()
            os.fsync(self.stream.fileno())
            self.stream.close()
            self.partial.rename(self.path)
            report['bytes'] = self.path.stat().st_size
            self.path.with_suffix('.json').write_bytes(dumps(report))
            self.last = report
            return report
    def status(self):
        with self.lock:
            return dict(active=self.active,error=self.error,selected_topics=getattr(self,'selected_topics',[]),
                        hz={t:r.hz(time.monotonic()) if self.active else 0 for t,r in getattr(self,'rates',{}).items()},file=getattr(self,'episode_id',''), path=str(getattr(self,'path','')), 
                        elapsed_s=time.monotonic()-self.started_mono if self.active else 0,
                        counts=dict(self.counts), bytes=self.partial.stat().st_size if self.active else 0,
                        last=self.last)


def record_category(identity,info):
    if info.get('record_kind') in ('collection','feedback','test'):return info['record_kind']
    if info.get('context',{}).get('kind') in ('replay','posture') or identity.removesuffix('.mcap').endswith(('_replay_feedback','_posture_feedback')):
        return 'feedback'
    if 'acceptance' in identity:return 'test'
    return 'collection'


def list_files(category='collection'):
    if category not in ('collection','feedback','test','all'):raise ValueError('未知记录分类')
    root=data_dir();rows=[]
    candidates=list(root.glob('*.mcap'))+list(root.glob('*/raw.mcap'))
    for p in sorted(candidates,key=lambda p:p.stat().st_mtime_ns,reverse=True):
        if p.parent.name.startswith('.'):continue
        if '.aligned' in p.name or p.name.endswith(('_replay_trace.mcap','_posture_trace.mcap')):continue
        legacy=p.parent==root
        identity=p.name if legacy else p.parent.name
        side=p.with_suffix('.json')
        info=json.loads(side.read_text()) if side.exists() else {}
        kind=record_category(identity,info)
        if category!='all' and kind!=category:continue
        rows.append({'file':identity,'path':str(p),'episode_dir':str(p.parent),'legacy':legacy,'category':kind,
                     'bytes':p.stat().st_size,'modified_ns':p.stat().st_mtime_ns,
                     'duration_s':info.get('duration_s'),'counts':info.get('counts'),'label':info.get('label',identity)})
    return rows


def bounds(path):
    with open(path,'rb') as f:
        summary=make_reader(f).get_summary()
        if not summary or not summary.statistics or not summary.statistics.message_count:
            raise ValueError('MCAP 没有消息或缺少完整索引')
        return summary.statistics.message_start_time,summary.statistics.message_end_time


def raw_events(path, schemas, start_ns=None, end_ns=None, topics=None):
    with open(path,'rb') as f:
        reader=make_reader(f)
        summary=reader.get_summary()
        raw_topics=[c.topic for c in summary.channels.values() if c.topic.startswith('/raw/') and (topics is None or c.topic[4:] in topics)]
        for _,channel,msg in reader.iter_messages(topics=raw_topics,start_time=start_ns,end_time=end_ns,log_time_order=True):
            p=json.loads(channel.metadata['publisher'])
            meta,data,image=decode(msg.data,p['type'],schemas)
            yield dict(topic=channel.topic[4:],publisher=p,frame=msg.data,wall_ns=msg.log_time,mono_ns=0,meta=meta,data=data,image=image)


class Playback:
    def __init__(self,schemas):
        self.schemas=schemas
        self.lock=threading.RLock()
        self.stop_event=threading.Event()
        self.thread=None
        self.latest={}
        self.file=None
        self.selected_topics=None
        self.playing=False
        self.position=0.
        self.duration=0.
        self.speed=1.
        self.error=''
    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
        self.playing=False
    def load(self,name,topics=None):
        self.stop()
        path=file_path(name)
        self.path=path
        self.selected_topics=None if topics is None else normalize_topics(topics)
        self.start_ns,self.end_ns=bounds(path)
        self.file=name
        self.duration=(self.end_ns-self.start_ns)/1e9
        self.seek(0)
        return self.status()
    def seek(self,seconds):
        self.stop()
        if not self.file:
            raise ValueError('先选择 MCAP')
        self.position=max(0.,min(float(seconds),self.duration))
        at=self.start_ns+int(self.position*1e9)
        latest={}
        # For each channel fetch the last observation near the cursor; startup uses its first sample.
        for event in raw_events(self.path,self.schemas,max(self.start_ns,at-2_000_000_000),at+1,topics=self.selected_topics):
            latest[event['topic']]=event
        if self.position==0:
            for event in raw_events(self.path,self.schemas,self.start_ns,self.start_ns+500_000_000,topics=self.selected_topics):
                latest.setdefault(event['topic'],event)
        with self.lock:
            self.latest=latest
        return self.status()
    def play(self,speed=1):
        self.stop()
        speed=float(speed)
        if not .1<=speed<=4:
            raise ValueError('播放速度应为 0.1–4')
        if not self.file:
            raise ValueError('先选择 MCAP')
        if self.position>=self.duration:
            self.seek(0)
        self.speed=speed
        self.stop_event=threading.Event()
        self.playing=True
        self.error=''
        self.thread=threading.Thread(target=self._run,daemon=True)
        self.thread.start()
        return self.status()
    def _run(self):
        start_at=self.start_ns+int(self.position*1e9)
        anchor=time.monotonic()
        try:
            for event in raw_events(self.path,self.schemas,start_at,topics=self.selected_topics):
                due=anchor+(event['wall_ns']-start_at)/1e9/self.speed
                if self.stop_event.wait(max(0,due-time.monotonic())):
                    break
                with self.lock:
                    self.latest[event['topic']]=event
                    self.position=(event['wall_ns']-self.start_ns)/1e9
        except Exception as exc:
            self.error=str(exc)
        finally:
            self.playing=False
    def status(self):
        return dict(file=self.file,selected_topics=self.selected_topics,playing=self.playing,position_s=self.position,duration_s=self.duration,speed=self.speed,error=self.error)


def validate(path,schemas):
    counts=Counter(); first={};last={};errors=[];gaps=Counter(); previous={};images={};raw_hash=hashlib.sha256()
    from PIL import Image
    import io
    for event in raw_events(path,schemas):
        topic=event['topic'];meta=event['meta'];counts[topic]+=1
        first.setdefault(topic,event['wall_ns']);last[topic]=event['wall_ns']
        raw_hash.update(event['frame'])
        seq=meta['sequence'];key=(topic,event['publisher']['uid'])
        if key in previous and seq>previous[key]+1:gaps[topic]+=seq-previous[key]-1
        previous[key]=seq
        if event['image'] is not None and meta['encoding']==1:
            try:
                with Image.open(io.BytesIO(event['image'])) as im:
                    im.load()
                images[topic]=[meta['width'],meta['height']]
            except Exception as exc:errors.append({'topic':topic,'error':str(exc)})
    required=['/camera/'+c for c in CAMERAS]+['/hal/joint_state','/hal/left_ee_data','/hal/right_ee_data','/tf']
    missing=[t for t in required if not counts[t]]
    hz={t:(counts[t]-1)/((last[t]-first[t])/1e9) if counts[t]>1 else 0 for t in counts}
    digest=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(8*1024*1024),b''):digest.update(chunk)
    report=dict(file=Path(path).name,passed=not missing and not errors,counts=dict(counts),hz=hz,
                missing=missing,image_errors=errors,images=images,sequence_gaps=dict(gaps),
                sha256=digest.hexdigest(),raw_frames_sha256=raw_hash.hexdigest(),crc_checked=True,
                robot_motion_tested=False,selection_policy=POLICY)
    Path(path).with_suffix('.validation.json').write_bytes(dumps(report))
    return report

if __name__=='__main__':
    import argparse
    from native import Schemas
    p=argparse.ArgumentParser();p.add_argument('file');a=p.parse_args()
    print(json.dumps(validate(Path(a.file),Schemas()),ensure_ascii=False,indent=2))
