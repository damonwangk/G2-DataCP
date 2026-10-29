"""Raw MCAP -> source-time alignment -> aligned MCAP + quality JSON. No LeRobot export."""
from bisect import bisect_left, bisect_right
from collections import defaultdict, Counter
import hashlib
import json
from pathlib import Path
import statistics
import time
from mcap.reader import make_reader
from mcap.writer import Writer, CompressionType
from native import ROOT, CAMERAS, Schemas
from storage import dumps, raw_events
from record_filter import remove_temperatures, POLICY

JOINT='/hal/joint_state'
GRIPPERS=['/hal/left_ee_data','/hal/right_ee_data']
COMMAND='/hal/joint_cmd'
CAMERA_TOPICS=['/camera/'+c for c in CAMERAS]
CONFIG={'anchor':'/camera/head_color','clock':'robot_native_ptp','camera_tolerance_ms':20,
        'joint_max_bracket_ms':50,'state_max_age_ms':50,
        'camera_method':'nearest native camera-group timestamp',
        'joint_method':'linear interpolation of motor_position between bracketing source samples; no extrapolation',
        'gripper_method':'previous observed feedback; no interpolation',
        'command_method':'previous native command; distinct from measured observation',
        'timebase':'irregular original head-camera frame times; no invented 30 Hz grid',
        'hardware_sync_verified':False,'selection_policy':POLICY}


def ref(e):
    return {'source_ns':str(e['t']),'log_time_ns':str(e['log']),'sequence':e['seq'],'publisher_uid':e['uid']}


def choose(series,t,previous=False,tolerance_ns=50_000_000):
    if not series:
        return None
    ts=[e['t'] for e in series] if not isinstance(series,Series) else series.times
    i=bisect_right(ts,t)-1 if previous else bisect_left(ts,t)
    if previous:
        e=series[i] if i>=0 else None
    else:
        candidates=series[max(0,i-1):i+1]
        e=min(candidates,key=lambda e:abs(e['t']-t)) if candidates else None
    if e is None or abs(e['t']-t)>tolerance_ns:
        return None
    return e

class Series(list):
    def index_times(self):self.times=[e['t'] for e in self]


def joint_at(series,t,max_bracket_ns=50_000_000):
    if not series:return None
    i=bisect_left(series.times,t)
    if i<len(series) and series[i]['t']==t:
        a=b=series[i];alpha=0.
    elif 0<i<len(series):
        a,b=series[i-1],series[i]
        if b['t']-a['t']>max_bracket_ns:return None
        alpha=(t-a['t'])/(b['t']-a['t'])
    else:return None
    names=a['names']
    if set(names)!=set(b['names']) or len(a['q'])!=len(names) or len(b['q'])!=len(names):return None
    right=dict(zip(b['names'],b['q']))
    q=[x+(right[n]-x)*alpha for n,x in zip(names,a['q'])]
    return {'names':names,'motor_position':q,'provenance':{'before':ref(a),'after':ref(b),'alpha':alpha,'method':'linear'},'valid':True}


def align(path,schemas=None):
    schemas=schemas or Schemas();path=Path(path)
    if '.aligned' in path.name:raise ValueError('请选择原始 MCAP')
    output=path.with_name(path.stem+'.aligned-v2.mcap')
    if output.exists():
        return json.loads(output.with_suffix('.quality.json').read_text())
    series=defaultdict(Series)
    # Lightweight references for images; do not load a whole episode of JPEGs into RAM.
    with path.open('rb') as f:
        reader=make_reader(f,validate_crcs=True)
        for _,c,m in reader.iter_messages(topics=['/metadata'+t for t in CAMERA_TOPICS]):
            d=json.loads(m.data);topic=c.topic[len('/metadata'):]
            series[topic].append(dict(t=int(d['source_ns']),log=m.log_time,seq=d['sequence'],uid=d['publisher_uid']))
    for e in raw_events(path,schemas,topics=[JOINT,*GRIPPERS,COMMAND]):
        d=e['data'];row=dict(t=int(e['meta']['source_ns']),log=e['wall_ns'],seq=e['meta']['sequence'],uid=e['publisher']['uid'])
        if e['topic']==JOINT:row.update(names=d.get('name',[]),q=d.get('motor_position',[]))
        elif e['topic']==COMMAND:row.update(names=d.get('name',[]),position=d.get('position',[]),mode=d.get('mode',[]))
        else:
            motors=[{k:v for k,v in m.items() if k!='temperature' and not k.endswith('_temperature')} for m in d.get('end_state',[])]
            row.update(names=d.get('name',[]),type=d.get('type'),motors=motors)
        series[e['topic']].append(row)
    discontinuities={}
    for topic,rows in series.items():
        bad=sum(b['t']<=a['t'] for a,b in zip(rows,rows[1:]))
        if bad:discontinuities[topic]=bad
        rows.index_times()
    if discontinuities:
        raise ValueError('源时间重复或回退，请先分段再对齐：'+json.dumps(discontinuities))
    anchors=series[CONFIG['anchor']]
    if not anchors:raise ValueError('原始 MCAP 缺少头部相机时间锚点')
    digest=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(8*1024*1024),b''):digest.update(chunk)
    source_sha=digest.hexdigest()
    image_targets=defaultdict(list);valid=Counter();reuse=Counter();delta=defaultdict(list);selected_counts=Counter();num_valid=0
    partial=output.with_suffix('.mcap.partial')
    with partial.open('xb') as f:
        w=Writer(f,compression=CompressionType.ZSTD,enable_crcs=True,enable_data_crcs=True)
        w.start(library='r2-mcap-studio/alignment-1.0')
        sid=w.register_schema(name='r2.AlignedObservation',encoding='jsonschema',data=dumps({'type':'object','additionalProperties':True}))
        cid=w.register_channel(topic='/aligned/observation',message_encoding='json',schema_id=sid)
        w.add_metadata('alignment',{'config':json.dumps(CONFIG),'raw_file':path.name,'raw_sha256':source_sha})
        for index,anchor in enumerate(anchors):
            t=anchor['t'];mask={};provenance={};cams={}
            for topic in CAMERA_TOPICS:
                e=choose(series[topic],t,tolerance_ns=20_000_000)
                mask[topic]=e is not None
                if e:
                    reference=ref(e);reference['offset_ns']=str(e['t']-t);cams[topic]=reference
                    delta[topic].append((e['t']-t)/1e6)
                    key=(topic,e['log'],e['uid'],e['seq']);selected_counts[key]+=1
                    image_targets[key].append((index,anchor['log']))
            joint=joint_at(series[JOINT],t);mask[JOINT]=joint is not None
            grips={}
            for topic in GRIPPERS:
                e=choose(series[topic],t,previous=True)
                has=e is not None and bool(e.get('motors')) and all('position' in m for m in e['motors'])
                mask[topic]=has
                grips[topic]={'names':e['names'],'type':e['type'],'motors':e['motors'],'provenance':ref(e),'age_ns':str(t-e['t'])} if has else None
                if has:delta[topic].append((e['t']-t)/1e6)
            command=choose(series[COMMAND],t,previous=True);mask[COMMAND]=command is not None
            command_data={'names':command['names'],'position':command['position'],'mode':command['mode'],'provenance':ref(command),'age_ns':str(t-command['t'])} if command else None
            required=['/camera/'+c for c in CAMERAS]+[JOINT,*GRIPPERS]
            complete=all(mask[k] for k in required);num_valid+=int(complete)
            for topic,okay in mask.items():valid[topic]+=int(okay)
            row={'frame_index':index,'anchor_source_ns':str(t),'relative_source_seconds':(t-anchors[0]['t'])/1e9,
                 'anchor':ref(anchor),'cameras':cams,'joints':joint,'grippers':grips,
                 'native_joint_command':command_data,'valid':mask,'observation_valid':complete,
                 'state_source':'measured','command_source':'native /hal/joint_cmd','clock_domain':'robot_native_ptp'}
            w.add_message(channel_id=cid,log_time=anchor['log'],publish_time=t,sequence=index,data=dumps(row))
        # Copy selected full native camera frames and original JPEG bytes into aligned MCAP.
        image_schema=w.register_schema(name='foxglove.CompressedImage',encoding='protobuf',data=schemas.data['foxglove.CompressedImage'])
        output_channels={};written=Counter()
        for e in raw_events(path,schemas,topics=CAMERA_TOPICS):
            key=(e['topic'],e['wall_ns'],e['publisher']['uid'],e['meta']['sequence'])
            targets=image_targets.get(key,[])
            if not targets:continue
            topic=e['topic'];meta=e['meta']
            if topic not in output_channels:
                raw_id=w.register_channel(topic='/aligned/raw'+topic,message_encoding='aorta',schema_id=0,metadata={'publisher':json.dumps(e['publisher'])})
                image_id=w.register_channel(topic='/aligned'+topic+'/image',message_encoding='protobuf',schema_id=image_schema) if meta['encoding']==1 else None
                output_channels[topic]=(raw_id,image_id)
            raw_id,image_id=output_channels[topic]
            for index,log_ns in targets:
                w.add_message(channel_id=raw_id,log_time=log_ns,publish_time=int(meta['publish_ptp_ns']),sequence=index,data=e['frame'])
                if image_id:
                    msg=schemas.get('foxglove.CompressedImage')();msg.timestamp.seconds=log_ns//1000000000;msg.timestamp.nanos=log_ns%1000000000
                    msg.frame_id=topic.rsplit('/',1)[-1];msg.format='jpeg';msg.data=e['image']
                    w.add_message(channel_id=image_id,log_time=log_ns,publish_time=int(meta['publish_ptp_ns']),sequence=index,data=msg.SerializeToString())
                written[topic]+=1
        intervals=[(b['t']-a['t'])/1e9 for a,b in zip(anchors,anchors[1:])]
        quality={'raw_file':path.name,'raw_sha256':source_sha,'aligned_file':output.name,'frames':len(anchors),
            'valid_observation_frames':num_valid,'valid_observation_fraction':num_valid/len(anchors),
            'valid_counts':dict(valid),'written_camera_frames':dict(written),
            'missing_counts':{k:len(anchors)-v for k,v in valid.items()},
            'camera_reuse_count':sum(n-1 for n in selected_counts.values()),
            'source_interval_s':{'min':min(intervals),'median':statistics.median(intervals),'max':max(intervals)} if intervals else {},
            'offset_ms':{k:{'min':min(v),'max':max(v),'mean':statistics.mean(v)} for k,v in delta.items() if v},
            'config':CONFIG,'format':'MCAP only; no LeRobot conversion','hardware_sync_verified':False,
            'alignment_passed':num_valid==len(anchors) and all(written[t]==valid[t] for t in CAMERA_TOPICS),
            'camera_gaps_over_200ms':sum(dt>.2 for dt in intervals),
            'sampling_regular':bool(intervals) and max(intervals)<=.2 and min(intervals)>=.005,
            'warnings':['Camera source timestamps are software frame-group times, not verified exposure timestamps'] + (['Camera source cadence is irregular; do not assume fixed 30 fps'] if intervals and (max(intervals)>.2 or min(intervals)<.005) else [])}
        w.add_attachment(create_time=time.time_ns(),log_time=anchors[-1]['log'],name='quality.json',media_type='application/json',data=dumps(quality))
        w.finish()
    partial.rename(output)
    output.with_suffix('.quality.json').write_bytes(dumps(quality))
    return quality

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('file');args=p.parse_args()
    print(json.dumps(align(args.file),ensure_ascii=False,indent=2))
