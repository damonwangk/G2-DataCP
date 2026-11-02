"""Synthetic packet and handoff-message fixtures; no robot bytes or vendor modules.

Only tests import this module. The protobuf messages model the handoff wrapper's
attribute/serialization interface; they are not vendor wire-format verification.
"""
from functools import lru_cache
import io
import struct
from types import ModuleType, SimpleNamespace


@lru_cache(maxsize=1)
def synthetic_camera_frame():
    """Build a 640x400 JPEG with documented Aorta/camera header offsets."""
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', (640, 400), (12, 94, 173)).save(buffer, format='JPEG', quality=80)
    jpeg = buffer.getvalue()
    frame = bytearray(176) + jpeg
    stamp, sequence = 1_000_000_000, 7
    struct.pack_into('<I', frame, 0, len(frame))
    frame[4], frame[5] = 0, 1
    struct.pack_into('<I', frame, 8, len(frame)-24)
    struct.pack_into('<I', frame, 24, sequence)
    for offset in (48, 56, 96, 128, 136, 144):
        struct.pack_into('<Q', frame, offset, stamp)
    for offset in (80, 152):
        struct.pack_into('<Q', frame, offset, sequence)
    struct.pack_into('<I', frame, 88, len(frame)-128)
    struct.pack_into('<I', frame, 161, 640)
    struct.pack_into('<I', frame, 165, 400)
    frame[169], frame[171] = 1, 8
    struct.pack_into('<I', frame, 172, len(jpeg))
    return bytes(frame)


def synthetic_operator_package():
    """Return test-only dynamic protobufs for the mocked gRPC handoff call."""
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
    fd = descriptor_pb2.FileDescriptorProto(
        name='tests/synthetic_operator.proto', package='datacp_test', syntax='proto3')
    request = fd.message_type.add(name='OperatorEventRequest')
    for number, (name, kind) in enumerate((('timestamp', 3), ('operator_event', 5),
            ('operator_event_detail', 5), ('operator_device', 9), ('robot_aid', 9)), 1):
        request.field.add(name=name, number=number, type=kind, label=1)
    reply = fd.message_type.add(name='OperatorEventReply')
    reply.field.add(name='result', number=1, type=8, label=1)
    reply.field.add(name='result_desception', number=2, type=9, label=1)
    pool = descriptor_pool.DescriptorPool()
    pool.Add(fd)
    pb = SimpleNamespace(
        takeover=2, takeover_pending=24,
        OperatorEventRequest=message_factory.GetMessageClass(
            pool.FindMessageTypeByName('datacp_test.OperatorEventRequest')),
        OperatorEventReply=message_factory.GetMessageClass(
            pool.FindMessageTypeByName('datacp_test.OperatorEventReply')))
    package = ModuleType('rh_msgs_v4_pb')
    package.operator_event_pb2 = pb
    return package, pb
