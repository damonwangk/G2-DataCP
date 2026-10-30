import copy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from mcap.reader import make_reader
import storage
import postures as p
from native import Schemas
from pose_worker import execute


def latest():
    positions=[(p.LIMITS[n]['min']+p.LIMITS[n]['max'])/2 for n in p.JOINT_NAMES]
    rows={p.JOINT:{'name':p.JOINT_NAMES+['wheel_joint'],'motor_position':positions+[999.]},
          p.GRIPS[0]:{'name':[p.GRIP_NAMES[0]],'type':1,'end_state':[{'position':.012}]},
          p.GRIPS[1]:{'name':[p.GRIP_NAMES[1]],'type':1,'end_state':[{'position':.023}]},
          '/imu/chassis':{'orientation':{'x':777}},'/tf':{'chassis_pose':'do not save'}}
    return {t:{'data':d,'mono_ns':time.monotonic_ns(),'wall_ns':time.time_ns(),'meta':{'source_ns':123456}} for t,d in rows.items()}

class Workspace(unittest.TestCase):
    def test_each_collection_has_own_episode_directory(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(storage,'data_dir',return_value=Path(tmp)):
            r=storage.Recorder(Schemas());r.start('same task',[]);a=r.stop([]);r.start('same task',[]);b=r.stop([])
            self.assertNotEqual(a['file'],b['file']);self.assertEqual(Path(a['path']).name,'raw.mcap')
            self.assertEqual(storage.file_path(a['file']),Path(a['path']))
            self.assertEqual(len(storage.list_files()),2)
            legacy=Path(tmp)/'legacy.mcap';legacy.write_bytes(Path(a['path']).read_bytes())
            self.assertEqual(storage.file_path('legacy.mcap'),legacy)
            self.assertEqual(len(storage.list_files()),3)
    def test_directory_selection_persists_and_rejects_nonabsolute(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(storage,'SETTINGS',Path(tmp)/'config.json'):
            dest=Path(tmp)/'chosen';storage.select_directory(str(dest),create=True)
            self.assertEqual(storage.data_dir(),dest)
            with self.assertRaises(ValueError):storage.select_directory('relative')
            with self.assertRaises(ValueError):storage.file_path('../x.mcap')
    def test_pose_only_whitelisted_joints_and_grippers_mcap(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(p,'POSES',Path(tmp)):
            saved=p.save_pose(latest(),'桌前准备位');loaded=p.load_pose(p.pose_path(saved['id']))
            self.assertEqual(loaded['names'],p.NAMES);self.assertEqual(len(loaded['positions']),24)
            text=json.dumps(loaded);self.assertNotIn('wheel_joint',text);self.assertNotIn('chassis',text);self.assertNotIn('/tf',text)
            self.assertEqual(loaded['positions'][-2:],[.012,.023]);self.assertEqual(len(p.list_poses()),1)
    def test_pose_rejects_stale_or_missing_feedback(self):
        s=latest();s[p.JOINT]['mono_ns']-=1_000_000_000
        with self.assertRaises(ValueError):p.capture(s,'stale')
        s=latest();s[p.JOINT]['data']['motor_position'][1]=float('nan')
        with self.assertRaises(ValueError):p.capture(s,'nan')
        s=latest();del s[p.GRIPS[1]]
        with self.assertRaises(ValueError):p.capture(s,'missing')
    def test_limits_near_at_over_and_stale(self):
        s=latest();name=p.JOINT_NAMES[8];lim=p.LIMITS[name]
        for position,status in [(lim['max']-.001,'near'),(lim['max'],'limit'),(lim['max']+.01,'over')]:
            s[p.JOINT]['data']['motor_position'][8]=position
            self.assertEqual(p.limit_status(s)['joints'][name]['status'],status)
        s[p.JOINT]['mono_ns']-=1_000_000_000;self.assertFalse(p.limit_status(s)['fresh'])
    def test_restore_with_fake_sdk_uses_no_chassis_and_no_servo(self):
        pose=p.capture(latest(),'fake');commands=[]
        class FakeRobot:
            def __init__(self):self.q=[0.]*24;self.stamp=0
            def get_joint_states(self):
                self.stamp+=1
                return {'timestamp':self.stamp,'states':[{'name':n,'motor_position':q,'error_code':0} for n,q in zip(p.JOINT_NAMES,self.q)]}
            def get_end_state(self):
                return {side:{'type':1,'names':[p.GRIP_NAMES[i]],'end_states':[{'position':self.q[22+i],'err_code':0}]} for i,side in enumerate(['left_end_state','right_end_state'])}
            def joint_control_request(self,r):
                commands.append(('body',r.joint_names));self.q[:22]=r.joint_positions;return 0
            def move_ee_pos(self,r):
                commands.append(('gripper',r.group,r.target_type));self.q[22+(r.group=='right_tool')]=r.states[0].position;return 0
        sdk=SimpleNamespace(JointControlReq=SimpleNamespace,JointStates=SimpleNamespace,JointState=SimpleNamespace)
        with patch('pose_worker.time.sleep'):
            out=execute(FakeRobot(),sdk,pose,lambda *args:None,lambda:False)
        self.assertTrue(out['tracking_passed']);self.assertEqual(len(commands),3);self.assertEqual(commands[0][1],p.JOINT_NAMES)
        self.assertEqual(commands[1:], [('gripper','left_tool','dahuan'),('gripper','right_tool','dahuan')])
    def test_readonly_api_and_pose_save_do_not_start_hardware(self):
        import app as server
        with tempfile.TemporaryDirectory() as tmp,patch.object(p,'POSES',Path(tmp)/'poses'),patch.object(storage,'SETTINGS',Path(tmp)/'workspace.json'),patch.object(server,'snapshot',side_effect=lambda _:latest()),patch.object(server.recorder,'select_directory',side_effect=storage.select_directory),patch.object(server.library,'SETTINGS',Path(tmp)/'library.json'),patch.object(server.receiver,'start') as start,patch('subprocess.Popen') as proc:
            # No context manager: FastAPI lifespan and receiver threads intentionally stay off.
            client=TestClient(server.app)
            dest=Path(tmp)/'episodes';r=client.post('/api/directory',json={'path':str(dest),'create':True});self.assertEqual(r.status_code,200)
            r=client.post('/api/poses',json={'label':'offline pose'});self.assertEqual(r.status_code,200);identity=r.json()['id']
            r=client.post('/api/poses/'+identity+'/plan',json={});self.assertEqual(r.status_code,200);self.assertFalse(r.json()['base_drive_restored'])
            self.assertEqual(client.get('/api/poses').status_code,200)
            self.assertEqual(client.get('/api/directories',params={'path':str(dest)}).status_code,200)
            start.assert_not_called();proc.assert_not_called()

if __name__=='__main__':unittest.main()
