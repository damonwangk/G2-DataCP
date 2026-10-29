"""Whole-topic recording selection and available motion data; raw bytes are untouched."""
from native import SPECS
LABELS={
 '/camera/head_color':('RGB 图像','头部 RGB'),
 '/camera/hand_left_color':('RGB 图像','左腕 RGB'),
 '/camera/hand_right_color':('RGB 图像','右腕 RGB'),
 '/hal/joint_state':('关节与夹爪','关节反馈（躯干、头部、双臂）'),
 '/hal/left_ee_data':('关节与夹爪','左夹爪反馈'),
 '/hal/right_ee_data':('关节与夹爪','右夹爪反馈'),
 '/hal/joint_cmd':('原始指令','关节指令'),
 '/wbc/left_ee_command':('原始指令','左夹爪指令'),
 '/wbc/right_ee_command':('原始指令','右夹爪指令'),
 '/tf':('姿态与状态','TF 本体 / 末端位姿'),
 '/wbc/motion_control_status':('姿态与状态','运动控制状态'),
 '/hal/whole_body_status':('姿态与状态','整机状态'),
 '/imu/chassis':('其他原生通道','底盘 IMU'),
 '/hal/chassis_power_state':('其他原生通道','底盘电源状态'),
 '/hal/left_ee_force_data':('其他原生通道','左末端力反馈'),
 '/hal/right_ee_force_data':('其他原生通道','右末端力反馈')}
TOPICS=[s[0] for s in SPECS]
MOTION_GROUPS=[
 ('body','躯干 / 腰部',[f'idx{i:02d}_body_joint{i}' for i in range(1,6)]),
 ('head','头部',[f'idx{10+i}_head_joint{i}' for i in range(1,4)]),
 ('left_arm','左臂',[f'idx{20+i}_arm_l_joint{i}' for i in range(1,8)]),
 ('right_arm','右臂',[f'idx{60+i}_arm_r_joint{i}' for i in range(1,8)]),
 ('left_gripper','左夹爪',['idx31_gripper_l_inner_joint1']),
 ('right_gripper','右夹爪',['idx71_gripper_r_inner_joint1'])]
def catalog():
 return {'topics':[{'topic':t,'group':LABELS[t][0],'label':LABELS[t][1]} for t in TOPICS],
         'motion_groups':[{'id':i,'label':l,'names':n} for i,l,n in MOTION_GROUPS],
         'target_control_hz':100,'record_policy':'原始通道逐包保存；不对齐、不降采样、不改写字段'}
def normalize_topics(topics):
 if topics is None:return list(TOPICS)
 if not isinstance(topics,list) or not topics:raise ValueError('请至少选择一种要保存的数据')
 unknown=set(topics)-set(TOPICS)
 if unknown:raise ValueError('未知采集通道：'+', '.join(sorted(unknown)))
 return [t for t in TOPICS if t in topics]
def motion_inventory(path,schemas):
 from mcap.reader import make_reader
 from storage import raw_events
 from robot_replay import JOINT,GRIPS
 with open(path,'rb') as f:
  summary=make_reader(f).get_summary()
  counts={}
  if summary and summary.statistics:
   for cid,ch in summary.channels.items():
    if ch.topic.startswith('/raw/'):
     t=ch.topic[4:];counts[t]=counts.get(t,0)+summary.statistics.channel_message_counts.get(cid,0)
   duration=(summary.statistics.message_end_time-summary.statistics.message_start_time)/1e9
  else:duration=0
 available=[t for t in [JOINT,*GRIPS] if counts.get(t)]
 names=[]
 for topic in available:
  event=next(raw_events(path,schemas,topics=[topic]),None)
  if event:names.extend(event['data'].get('name',[]))
 groups=[{'id':i,'label':l,'names':[n for n in ns if n in names]} for i,l,ns in MOTION_GROUPS]
 known={n for _,_,ns in MOTION_GROUPS for n in ns}
 if any(n not in known for n in names):
  groups.append({'id':'other','label':'其他本体关节','names':[n for n in names if n not in known]})
 return {'topics':list(counts),'counts':counts,'duration_s':duration,
         'recorded_average_hz':{t:n/duration if duration>0 else None for t,n in counts.items()},
         'motion_names':names,'motion_groups':groups}
