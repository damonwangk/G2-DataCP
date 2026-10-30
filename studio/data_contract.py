"""Read-only protobuf field descriptions for the acquisition inspector."""
from google.protobuf.descriptor import FieldDescriptor as F

TYPES={F.TYPE_DOUBLE:'float64',F.TYPE_FLOAT:'float32',F.TYPE_FIXED32:'fixed32 (uint32)',
       F.TYPE_UINT32:'uint32',F.TYPE_INT32:'int32',F.TYPE_UINT64:'uint64',F.TYPE_INT64:'int64',
       F.TYPE_BOOL:'bool',F.TYPE_STRING:'string',F.TYPE_BYTES:'bytes',F.TYPE_ENUM:'enum'}
JOINT_NOTES={
 'name':('—','关节名称；其他等长数组按此顺序对应'),
 'motor_position':('rad','实测电机位置；姿态展示与轨迹使用此字段'),
 'motor_velocity':('rad/s','实测电机速度'),
 'motor_current':('A（SDK 文档）','原始电机电流数值，不额外缩放'),
 'effort':('N·m（SDK 文档）','原始力矩字段'),
 'position':('原生值','SDK 将此字段列为预留；不可统一当成 motor_position'),
 'velocity':('原生值','SDK 将此字段列为预留；速度使用 motor_velocity'),
 'mode':('编码','原生模式编码'),
 'error_code':('编码','原生错误码'),
 'temperature':('原生值','数组长度可能少于 name；单位比例和关节对应尚未核验'),
 'voltage':('原生值','数组长度可能少于 name；不要直接解释成伏特'),
 'driver_state':('编码','数组长度可能少于 name；驱动状态位含义与关节对应待核验'),
}
GRIP_NOTES={
 'id':('编号','末端电机 ID'), 'enable':('布尔','是否启用'),
 'position':('原生行程值','大寰配置范围 0–0.025；连续 float64，非 0/1。不换算 mm 或实际夹指间距'),
 'velocity':('原生值/秒','仅在原报文提供时显示'),
 'effort':('原生值','SDK 通用文档标 N·m，具体末端物理语义未核验'),
 'current':('原生值','SDK 通用文档标 A，但本机原始值可达 256，比例未核验，不直接当成 256 A'),
 'voltage':('原生值','SDK 通用文档标 V；当前报文未必提供，比例未核验'),
 'temperature':('原生值','SDK 通用文档标 ℃；仅显示原报文提供的值'),
 'status':('编码','原生状态码'), 'err_code':('编码','原生错误码；未提供不补零'),
}

def describe(schemas,name,notes):
    result=[]
    for field in schemas.get(name).DESCRIPTOR.fields:
        kind=field.message_type.full_name if field.message_type else TYPES.get(field.type,str(field.type))
        unit,note=notes.get(field.name,('—','原生字段'))
        result.append({'name':field.name,'type':kind,'repeated':(field.is_repeated if hasattr(field,'is_repeated') else field.label==F.LABEL_REPEATED),
                       'optional_presence':field.has_presence,'unit':unit,'note':note})
    return result

def contract(schemas):
    return {'joint_fields':[f for f in describe(schemas,'genie_msgs.msg.pb.JointState',JOINT_NOTES) if f['name'] in {'name','motor_position','motor_velocity','motor_current','effort','mode','error_code'}],
            'gripper_fields':[f for f in describe(schemas,'genie_msgs.msg.pb.MotorState',GRIP_NOTES) if f['name'] not in {'temperature','voltage'}],
            'gripper_envelope':describe(schemas,'genie_msgs.msg.pb.EndState',{}),
            'source':'robot-provided GDK 3.1.1 protobuf descriptors and bundled Python robot documentation',
            'gripper_range':{'model':'dahuan','type':1,'lower':0.,'upper':.025,
                             'documented_lower':'打开','documented_upper':'关闭',
                             'note':'端点方向按厂商文档；不是本轮动作测试结论'}}
