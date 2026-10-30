"""Offline adapter for platform MCAP. Reads embedded schemas, never imports a robot SDK."""
from bisect import bisect_left
from collections import Counter
import io
from pathlib import Path
import numpy as np
from mcap.reader import make_reader
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

CAMERAS = ('head_color', 'hand_left_color', 'hand_right_color')
ARMS = [f'idx{21+i}_arm_l_joint{i+1}' for i in range(7)] + [f'idx{61+i}_arm_r_joint{i+1}' for i in range(7)]
GRIPS = ['idx31_gripper_l_inner_joint1', 'idx71_gripper_r_inner_joint1']
STATE = ('/hal/joint_state', '/hal/left_ee_data', '/hal/right_ee_data')
ACTION = ('/hal/joint_cmd', '/wbc/left_ee_command', '/wbc/right_ee_command')
IMAGE = {cam: f'/camera/{cam}/image' for cam in CAMERAS}


class ReadMeter:
    """Measure unique source-file bytes, so summary seeks do not jump to 100%."""
    def __init__(self,stream,callback):
        self.stream=stream;self.callback=callback;self.ranges=[]
    def __getattr__(self,name):return getattr(self.stream,name)
    def read(self,size=-1):
        start=self.stream.tell();data=self.stream.read(size);end=start+len(data)
        if data:
            ranges=[]
            for lo,hi in self.ranges:
                if hi<start or lo>end:ranges.append((lo,hi))
                else:start=min(start,lo);end=max(end,hi)
            ranges.append((start,end));self.ranges=ranges
            self.callback(sum(hi-lo for lo,hi in ranges))
        return data


def available_cameras(path):
    with Path(path).open('rb') as stream:
        reader=make_reader(stream);summary=reader.get_summary()
        if summary and summary.statistics:
            present={c.topic for c in summary.channels.values() if summary.statistics.channel_message_counts.get(c.id,0)}
        else:present={channel.topic for _,channel,_ in reader.iter_messages(topics=list(IMAGE.values()))}
    return tuple(cam for cam,topic in IMAGE.items() if topic in present)


def messages(path, topics, on_read=None):
    classes = {}
    with Path(path).open('rb') as stream:
        for schema, channel, message in make_reader(ReadMeter(stream,on_read) if on_read else stream).iter_messages(topics=topics):
            if schema is None or channel.message_encoding != 'protobuf':
                raise ValueError(f'{channel.topic}: embedded Protobuf schema is required')
            if schema.id not in classes:
                pool = descriptor_pool.DescriptorPool()
                pending = list(descriptor_pb2.FileDescriptorSet.FromString(schema.data).file)
                while pending:
                    remaining = []
                    for fd in pending:
                        try: pool.Add(fd)
                        except (TypeError, KeyError): remaining.append(fd)
                    if len(remaining) == len(pending): raise ValueError('MCAP schema dependency cannot be resolved')
                    pending = remaining
                descriptor = pool.FindMessageTypeByName(schema.name)
                classes[schema.id] = message_factory.GetMessageClass(descriptor)
            yield channel.topic, message, classes[schema.id].FromString(message.data)


def positions(topic, value):
    names = list(value.name)
    expected = ARMS if topic in (STATE[0], ACTION[0]) else [GRIPS[0] if 'left' in topic else GRIPS[1]]
    if topic == STATE[0]: values = list(value.motor_position)
    elif topic in STATE[1:]:
        values = [x.position if x.HasField('position') else None for x in value.end_state]
    else: values = list(value.position)
    if len(names) != len(values) or len(set(names)) != len(names):
        raise ValueError(f'{topic}: missing/ambiguous name-to-position mapping')
    mapping = dict(zip(names, values))
    if any(n not in mapping or mapping[n] is None for n in expected):
        raise ValueError(f'{topic}: required arm/gripper positions absent')
    row = np.asarray([mapping[n] for n in expected], dtype=np.float32)
    if not np.isfinite(row).all(): raise ValueError(f'{topic}: nonfinite positions')
    return row


def nearest(times, target):
    at = bisect_left(times, target)
    choices = [i for i in (at-1, at) if 0 <= i < len(times)]
    return min(choices, key=lambda i: (abs(times[i]-target), i))


def make_plan(path, fps=30, max_offset_ms=None, max_camera_age_ms=None, max_gap_ms=None, progress=None):
    """Map each source to one episode, without filtering gaps or clipping values.

    MCAP clock domains differ, so the training grid uses recorder log_time. Original
    command topics are preferred; an absent/unparseable command uses the recorded
    corresponding state trajectory and is identified in per-frame provenance.
    """
    path = Path(path)
    topics = (*STATE, *ACTION, *IMAGE.values())
    samples = {t: [] for t in topics}; counts = Counter(); parse_notes = Counter()
    size=path.stat().st_size
    def on_read(count):
        if progress:progress.update('读取源文件',fraction=.30*min(count/max(size,1),1),bytes_read=count,total_bytes=size)
    for topic, message, value in messages(path, topics, on_read):
        index = counts[topic]; counts[topic] += 1
        if topic in IMAGE.values():
            if value.format.lower() not in ('jpeg', 'jpg') or not value.data:
                raise ValueError(f'{topic}: expected source JPEG')
            data = None
        else:
            try: data = positions(topic, value)
            except ValueError as exc:
                if topic not in ACTION: raise
                data = None; parse_notes[str(exc)] += 1
        samples[topic].append((message.log_time, data, index, message.publish_time))
    cameras={cam:topic for cam,topic in IMAGE.items() if samples[topic]}
    required = (*STATE, *cameras.values())
    missing = [t for t in required if not samples[t]]
    if missing: raise ValueError('MCAP缺少16维状态的必要格式字段: '+', '.join(missing))
    times = {t: [r[0] for r in rows] for t, rows in samples.items()}
    start = min(times[t][0] for t in required); end = max(times[t][-1] for t in required)
    frames = (end-start)*fps//1_000_000_000+1
    if progress:progress.update('映射状态与动作',fraction=.30,force=True,total_frames=frames)
    segment = []; action_sources = Counter()
    for grid_index in range(frames):
        if progress:progress.update('映射状态与动作',fraction=.30+.05*grid_index/max(frames,1))
        stamp = start+round(grid_index*1_000_000_000/fps)
        chosen = {t: samples[t][nearest(times[t], stamp)] for t in required}
        sources = {}
        for command, state in zip(ACTION, STATE):
            row = samples[command][nearest(times[command], stamp)] if samples[command] else None
            source = command if row is not None and row[1] is not None else state
            chosen[command] = row if source == command else chosen[state]
            sources[command] = source
            action_sources[source] += 1
        def vector(keys):
            arm, left, right = [chosen[t][1] for t in keys]
            return np.concatenate((arm[:7], left, arm[7:], right)).astype(np.float32)
        segment.append({'grid_index':grid_index, 'grid_timestamp_ns':stamp, 'record_timestamp_ns':stamp,
                        'state':vector(STATE), 'action':vector(ACTION), 'action_sources':sources,
                        'source_samples':{t:{'source_topic':sources.get(t,t),'index':r[2],'log_time_ns':r[0],'publish_time_ns':r[3],'offset_ns':r[0]-stamp} for t,r in chosen.items() if t not in IMAGE.values()},
                        'cameras':{cam:{'index':chosen[t][2],'timestamp_ns':chosen[t][0],'publish_time_ns':chosen[t][3],'offset_ns':chosen[t][0]-stamp} for cam,t in cameras.items()}})
    return [segment], {'source':str(path),'input_format':'platform_mcap','clock':'MCAP log_time recording-host receive time',
        'messages':dict(counts),'command_parse_notes':dict(parse_notes),'grid_frames':frames,
        'mapping':'nearest recorded sample; no gap, offset or range filtering; one source MCAP per episode',
        'action_sources':dict(action_sources),'segment_lengths':[frames],
        'export_frames':frames, 'available_cameras':list(cameras), 'camera_index_counts':{cam:counts[t] for cam,t in cameras.items()}}


class CameraReader:
    def __init__(self, path, camera, count, size, threads=1):
        self.iterator = iter(messages(path, [IMAGE[camera]]))
        self.index, self.count, self.size, self.image = -1, count, size, None
    def get(self, wanted):
        from PIL import Image, ImageOps
        if wanted < self.index: raise ValueError('MCAP camera index moved backwards')
        while self.index < wanted:
            try: _, _, msg = next(self.iterator)
            except StopIteration as exc: raise ValueError('MCAP camera stream shorter than planned') from exc
            self.index += 1
            self.image = Image.open(io.BytesIO(msg.data)).convert('RGB')
        return np.asarray(ImageOps.pad(self.image, (self.size,self.size), method=Image.Resampling.BILINEAR,color=(0,0,0)),dtype=np.uint8)
    def finish(self):
        for _ in self.iterator: self.index += 1
        if self.index+1 != self.count: raise ValueError('MCAP image message count changed')
    def close(self):
        close = getattr(self.iterator, 'close', None)
        if close: close()
