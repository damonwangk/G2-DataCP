"""Dataset selection policy. Keep RGB bytes; exclude depth and temperature values."""
import struct

PROFILE='raw_wire_v3'
EXCLUDED_TOPICS={'/camera/head_depth'}
POLICY={'processing':'none; store received packet bytes directly',
        'profile':PROFILE,'excluded_topics':sorted(EXCLUDED_TOPICS),
        'excluded_fields':[],
        'image_policy':'original RGB JPEG bytes; no transcoding; depth is not subscribed',
        'protobuf_policy':'original protobuf payload bytes; no field removal or reserialization',
        'raw_semantics':'/raw/* is byte-identical to the received Aorta packet, including all original fields'}


def remove_temperatures(message,prefix=''):
    removed=[]
    for field,value in list(message.ListFields()):
        path=prefix+field.name
        if field.name=='temperature' or field.name.endswith('_temperature'):
            message.ClearField(field.name);removed.append(path)
        elif field.message_type:
            if field.is_repeated:
                for i,child in enumerate(value):removed.extend(remove_temperatures(child,f'{path}[{i}].'))
            else:removed.extend(remove_temperatures(value,path+'.'))
    return removed


def selected_frame(event,schemas):
    return event['frame'],[]
