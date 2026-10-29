import unittest
from types import SimpleNamespace as NS
from sdk_worker import select_gdk_control
class SDKControl(unittest.TestCase):
    def test_requests_sdk_joint_position_source_with_normal_mode(self):
        calls=[]
        g=NS(MotionControlMode=NS,InputSource=NS(INPUT_GDK=6),Target=NS(TARGET_ARMS=0),ControlMode=NS(CTRL_JOINT_POSITION=1),SafeMode=NS(SAFE_NORMAL=0))
        robot=NS(set_control_mode=lambda m:calls.append(m) or 0)
        result=select_gdk_control(robot,g)
        self.assertEqual(vars(calls[0]),dict(input_source=6,target=0,control_mode=1,safe_mode=0))
        self.assertEqual(result['return_code'],0)

    def test_worker_continues_after_sdk_rejection_without_changing_target(self):
        import tempfile,json
        from pathlib import Path
        from unittest.mock import patch
        from sdk_worker import run
        import sdk_worker
        class Traj:
            names=['body','left_grip','right_grip'];joint_count=1;duration=.18;initial=[0.,0.,0.]
            gripper_types={'/hal/left_ee_data':1,'/hal/right_ee_data':1}
            def __init__(self,*args):pass
            def at(self,t):return [t,.025,1.632 if t<.10 else .025]
        class Robot:
            def __init__(self):self.q=[0.,0.,0.];self.sent=[];self.rejected=False;self.stamp=0
            def set_control_mode(self,m):return 0
            def get_joint_states(self):
                self.stamp+=1
                return {'timestamp':self.stamp,'states':[{'name':'body','motor_position':self.q[0]}]}
            def get_end_state(self):
                return {side:{'type':1,'names':[name],'end_states':[{'position':self.q[i]}]} for side,name,i in [('left_end_state','left_grip',1),('right_end_state','right_grip',2)]}
            def joint_servo_control(self,r,low):
                self.sent.append(list(r.joint_positions))
                if r.joint_positions[2]>1:self.rejected=True;raise RuntimeError('SDK range')
                self.q=list(r.joint_positions);return 0
        robot=Robot()
        sdk=NS(MotionControlMode=NS,JointServoControlReq=NS,InputSource=NS(INPUT_GDK=6),Target=NS(TARGET_ARMS=0),ControlMode=NS(CTRL_JOINT_POSITION=1),SafeMode=NS(SAFE_NORMAL=0),GDKRes=NS(kSuccess=0),gdk_init=lambda:0,gdk_release=lambda:0,Robot=lambda:robot)
        class Clock:
            t=0.
            def now(self):self.t+=.01;return self.t
        clock=Clock()
        with tempfile.TemporaryDirectory() as tmp,patch.dict('sys.modules',{'agibot_gdk':sdk}),patch.object(sdk_worker,'Trajectory',Traj),patch.object(sdk_worker,'release_teleop',return_value={'result':True}),patch.object(sdk_worker.time,'monotonic',clock.now),patch.object(sdk_worker.time,'sleep'),patch.object(sdk_worker.signal,'signal'):
            args=NS(run=tmp,file='fake.mcap',start=0,duration=0,speed=1,trace=str(Path(tmp)/'trace.mcap'))
            code=run(args);out=json.loads((Path(tmp)/'result.json').read_text())
            self.assertEqual(code,0);self.assertTrue(out['completed']);self.assertGreater(out['sdk_rejected_count'],0)
            self.assertTrue(any(q[2]==1.632 for q in robot.sent))
            self.assertEqual(robot.sent[-1][2],.025);self.assertGreater(out['sample_count'],0)

    def test_vendor_handoff_requests_pending_and_propagates_failure(self):
        import grpc
        from unittest.mock import patch,MagicMock
        from control_session import release_teleop
        channel=MagicMock();channel.__enter__.return_value=channel
        call=MagicMock(return_value=NS(result=True,result_desception='OK'))
        channel.unary_unary.return_value=call
        with patch.object(grpc,'insecure_channel',return_value=channel):
            self.assertTrue(release_teleop()['result'])
            request=call.call_args.args[0]
            self.assertEqual(request.operator_event,2)
            self.assertEqual(request.operator_event_detail,24)
            self.assertEqual(request.robot_aid,'g2a0004bc02187')
            self.assertEqual(channel.unary_unary.call_args.args[0],'/rh_msgs_v4.OperatorEventService/OperatorEventService')
            call.return_value=NS(result=False,result_desception='not accepted')
            with self.assertRaisesRegex(RuntimeError,'not accepted'):release_teleop()
