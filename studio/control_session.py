# 使用说明
# 用途：厂商控制权交接封装。
# 用法：内部模块，仅显式回放/姿态恢复调用 release_teleop；只读状态检测不调用它。
# 详细说明：回放与姿态使用说明.md

"""Vendor control handoff, called only by explicit replay or posture restore."""
import json
import os
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def release_teleop():
    import grpc
    sys.path.insert(0,str(Path('/home/agi/app/gdk/lib') if Path('/home/agi/app/gdk/lib').is_dir() else ROOT/'vendor/robot-sdk-3.1.1/app/gdk/lib'))
    from rh_msgs_v4_pb import operator_event_pb2 as p
    config=json.loads((ROOT/'config/robot_control.json').read_text())
    host=os.environ.get('R2_HOST',config['host'])
    request=p.OperatorEventRequest(timestamp=time.time_ns()//1000000,
        operator_event=p.takeover,operator_event_detail=p.takeover_pending,
        operator_device='r2_mcap_studio',robot_aid=config['robot_aid'])
    method='/rh_msgs_v4.OperatorEventService/OperatorEventService'
    with grpc.insecure_channel(f"{host}:{config['operator_port']}") as channel:
        call=channel.unary_unary(method,request_serializer=lambda m:m.SerializeToString(),
            response_deserializer=p.OperatorEventReply.FromString)
        reply=call(request,timeout=5)
    if not reply.result:raise RuntimeError('厂商遥操挂起失败：'+reply.result_desception)
    return {'event':'takeover_pending','result':reply.result,'message':reply.result_desception}
