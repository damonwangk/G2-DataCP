"""R2 native Aorta TCP receiver. No SDK initialization and no control publication."""
import base64
from collections import deque
import importlib
import json
import socket
import struct
import sys
import threading
import time
import urllib.request
from pathlib import Path
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory, json_format, timestamp_pb2

ROOT = Path(__file__).resolve().parent
CAMERAS = ('head_color', 'hand_left_color', 'hand_right_color')
SPECS = [
    ('/camera/' + c, 'cosine_runner', True) for c in CAMERAS
] + [
    ('/hal/joint_state', 'hal', True),
    ('/hal/left_ee_data', 'hal', True),
    ('/hal/right_ee_data', 'hal', True),
    ('/hal/joint_cmd', 'genie_motion_control', False),
    ('/wbc/left_ee_command', 'genie_motion_control', False),
    ('/wbc/right_ee_command', 'genie_motion_control', False),
    ('/wbc/motion_control_status', 'genie_motion_control', False),
    ('/hal/whole_body_status', 'hal', False),
    ('/tf', 'genie_motion_control', True),
    ('/imu/chassis', 'hal_lowerlimb', False),
    ('/hal/chassis_power_state', 'hal_lowerlimb', False),
    ('/hal/left_ee_force_data', 'hal', False),
    ('/hal/right_ee_force_data', 'hal', False),
]
U32 = lambda b, o=0: struct.unpack_from('<I', b, o)[0]
U64 = lambda b, o: struct.unpack_from('<Q', b, o)[0]


def discover(host):
    enc = lambda s: base64.b64encode(s.encode()).decode()
    req = urllib.request.Request('http://' + host + ':2379/v3/kv/range',
        data=json.dumps({'key': enc('/publishers/'), 'range_end': enc('/publishers0'), 'limit': '10000'}).encode(),
        headers={'Content-Type': 'application/json'})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(req, timeout=5) as f:
        result = json.load(f)
    if result.get('more'):
        raise RuntimeError('Publisher list truncated')
    return [json.loads(base64.b64decode(k['value'])) for k in result.get('kvs', [])]


def descriptor_set(cls):
    ds = descriptor_pb2.FileDescriptorSet()
    seen = set()
    def visit(fd):
        if fd.name in seen:
            return
        for dep in fd.dependencies:
            visit(dep)
        seen.add(fd.name)
        ds.file.add().ParseFromString(fd.serialized_pb)
    visit(cls.DESCRIPTOR.file)
    return ds.SerializeToString()


class Schemas:
    def __init__(self):
        self.classes = {}
        self.data = {}
        self.pool = descriptor_pool.DescriptorPool()
        saved = ROOT / 'schemas.pb'
        if saved.exists():
            ds = descriptor_pb2.FileDescriptorSet.FromString(saved.read_bytes())
            for fd in ds.file:
                self.pool.Add(fd)
                for msg in fd.message_type:
                    name = fd.package + '.' + msg.name
                    cls = message_factory.GetMessageClass(self.pool.FindMessageTypeByName(name))
                    self.classes[name] = cls
                    self.data[name] = descriptor_set(cls)
        # Official foxglove.CompressedImage protobuf layout.
        if 'foxglove.CompressedImage' not in self.classes:
            self.pool.AddSerializedFile(timestamp_pb2.DESCRIPTOR.serialized_pb)
            fd = descriptor_pb2.FileDescriptorProto(name='foxglove/CompressedImage.proto', package='foxglove', syntax='proto3')
            fd.dependency.append('google/protobuf/timestamp.proto')
            msg = fd.message_type.add(name='CompressedImage')
            for name, number, typ in [('timestamp',1,11),('frame_id',2,9),('data',3,12),('format',4,9)]:
                field = msg.field.add(name=name, number=number, type=typ, label=1)
                if typ == 11:
                    field.type_name = '.google.protobuf.Timestamp'
            self.pool.Add(fd)
            cls = message_factory.GetMessageClass(self.pool.FindMessageTypeByName('foxglove.CompressedImage'))
            self.classes['foxglove.CompressedImage'] = cls
            self.data['foxglove.CompressedImage'] = descriptor_set(cls)

    def get(self, name):
        if name not in self.classes:
            # Import protobuf definitions only, never agibot_gdk.
            sys.path.append(str(Path('/home/agi/app/gdk/lib') if Path('/home/agi/app/gdk/lib').is_dir() else ROOT/'vendor/robot-sdk-3.1.1/app/gdk/lib'))
            package, group, pb, message = name.split('.')
            cls = getattr(importlib.import_module(f'{package}_pb.{group}.{message}_pb2'), message)
            self.classes[name] = cls
            self.data[name] = descriptor_set(cls)
        return self.classes[name]

    def save(self):
        ds = descriptor_pb2.FileDescriptorSet()
        seen = set()
        for data in self.data.values():
            for fd in descriptor_pb2.FileDescriptorSet.FromString(data).file:
                if fd.name not in seen:
                    seen.add(fd.name)
                    ds.file.add().CopyFrom(fd)
        (ROOT / 'schemas.pb').write_bytes(ds.SerializeToString())


class Framer:
    def __init__(self):
        self.buffer = bytearray()
    def feed(self, chunk):
        self.buffer.extend(chunk)
        while len(self.buffer) >= 4:
            n = U32(self.buffer)
            if not 128 <= n <= 32 * 1024 * 1024:
                raise ValueError(f'Invalid native frame size: {n}')
            if len(self.buffer) < n:
                return
            frame = bytes(self.buffer[:n])
            del self.buffer[:n]
            yield frame


def decode(frame, typ, schemas):
    if len(frame) < 128 or U32(frame) != len(frame) or frame[4] != 0 or frame[5] != 1:
        raise ValueError('Unsupported Aorta framing')
    if U32(frame,8) + 24 != len(frame) or U32(frame,88) + 128 != len(frame):
        raise ValueError('Aorta length mismatch')
    meta = {'sequence': U64(frame,80), 'aorta_sequence': U32(frame,24),
            'publish_ptp_ns': str(U64(frame,48)), 'publish_utc_label_ns': str(U64(frame,56)),
            'envelope_source_ns': str(U64(frame,96))}
    if typ == 'CosineDataPacket':
        if len(frame) < 176 or U32(frame,172) + 176 != len(frame):
            raise ValueError('Camera payload length mismatch')
        meta.update(width=U32(frame,161), height=U32(frame,165), encoding=frame[169],
                    bit_depth=frame[171], source_ns=str(U64(frame,144)),
                    camera_sequence=U64(frame,152), packet_send_ns=str(U64(frame,128)),
                    packet_receive_ns=str(U64(frame,136)))
        if frame[169] == 1 and (frame[176:178] != b'\xff\xd8' or frame[-2:] != b'\xff\xd9'):
            raise ValueError('Invalid JPEG boundaries')
        return meta, None, frame[176:]
    msg = schemas.get(typ)()
    msg.ParseFromString(frame[128:])
    data = json_format.MessageToDict(msg, preserving_proto_field_name=True)
    stamp = data.get('header', {}).get('stamp')
    if stamp:
        meta['source_ns'] = str(int(stamp.get('sec',0))*1000000000 + int(stamp.get('nanosec',0)))
    elif data.get('transforms'):
        stamp = data['transforms'][0].get('header',{}).get('stamp',{})
        meta['source_ns'] = str(int(stamp.get('sec',0))*1000000000 + int(stamp.get('nanosec',0)))
    else:
        meta['source_ns'] = meta['envelope_source_ns']
    return meta, data, None


class Receiver:
    def __init__(self, host, schemas, callback, topics=None, connect_host=None):
        self.host, self.schemas, self.callback = host, schemas, callback
        self.selected_topics=topics
        self.connect_host=connect_host or host
        self.stopping = threading.Event()
        self.lock = threading.RLock()
        self.latest = {}
        self.streams = {}
        self.threads = []
        self.registry = []
    def start(self):
        self.registry = discover(self.connect_host)
        for topic, exe, required in SPECS:
            if self.selected_topics is not None and topic not in self.selected_topics:continue
            matches = [e for e in self.registry if e.get('topic') == topic and e.get('exe_name') == exe and e.get('role_type') == 1 and self.host in e.get('ip','').replace(';',',').split(',')]
            state = dict(topic=topic, required=required, count=0, bytes=0, gaps=0, resets=0, errors=0,
                         error='', last_mono=0, times=deque(maxlen=500), publisher=None)
            self.streams[topic] = state
            if len(matches) != 1:
                state['error'] = f'发现 {len(matches)} 个发布者'
                continue
            state['publisher'] = matches[0]
            if matches[0]['type'] != 'CosineDataPacket':
                self.schemas.get(matches[0]['type'])
            thread = threading.Thread(target=self._read, args=(state,), daemon=True)
            self.threads.append(thread)
        self.schemas.save()
        for thread in self.threads:
            thread.start()
    def _read(self, state):
        publisher = state['publisher']
        while not self.stopping.is_set():
            previous = None
            try:
                with socket.create_connection((self.connect_host, publisher['port']),timeout=3) as sock:
                    sock.settimeout(1)
                    framer = Framer()
                    while not self.stopping.is_set():
                        try:
                            chunk = sock.recv(1048576)
                        except socket.timeout:
                            continue
                        if not chunk:
                            raise ConnectionError('Publisher disconnected')
                        for frame in framer.feed(chunk):
                            wall, mono = time.time_ns(), time.monotonic_ns()
                            meta, data, image = decode(frame, publisher['type'], self.schemas)
                            event = dict(topic=publisher['topic'], publisher=publisher, frame=frame,
                                wall_ns=wall, mono_ns=mono, meta=meta, data=data, image=image)
                            with self.lock:
                                state['count'] += 1
                                state['bytes'] += len(frame)
                                state['last_mono'] = mono
                                state['times'].append(mono)
                                state['error'] = ''
                                seq = meta['sequence']
                                if previous is not None:
                                    if seq > previous + 1:
                                        state['gaps'] += seq - previous - 1
                                    elif seq <= previous:
                                        state['resets'] += 1
                                previous = seq
                                self.latest[state['topic']] = event
                            self.callback(event)
            except Exception as exc:
                with self.lock:
                    state['errors'] += 1
                    state['error'] = str(exc)
                self.stopping.wait(2)
                try:
                    matches = [e for e in discover(self.connect_host) if e.get('topic') == state['topic'] and e.get('exe_name') == publisher['exe_name'] and e.get('role_type') == 1 and self.host in e.get('ip','').replace(';',',').split(',')]
                    if len(matches) == 1:
                        publisher = matches[0]
                        state['publisher'] = publisher
                except Exception:
                    pass
    def stats(self):
        now = time.monotonic_ns()
        with self.lock:
            rows = []
            for state in self.streams.values():
                row = {k:v for k,v in state.items() if k not in ('times','last_mono')}
                row['age_s'] = (now-state['last_mono'])/1e9 if state['last_mono'] else None
                ts = [t for t in state['times'] if now-t < 3e9]
                row['hz'] = (len(ts)-1)/((ts[-1]-ts[0])/1e9) if len(ts)>1 else 0
                row['online'] = row['age_s'] is not None and row['age_s']<2
                rows.append(row)
            return rows
    def close(self):
        self.stopping.set()
        for thread in self.threads:
            thread.join(timeout=4)
