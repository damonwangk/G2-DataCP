import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import sdk_limits as limits
import control_diagnostics as diagnostic
from tests.test_workspace import latest
from tests.test_record_filter import DatasetSelection
import storage
from storage import raw_events

class Advisory(unittest.TestCase):
    def test_24_sdk_ranges_exclude_chassis_and_accept_endpoints(self):
        self.assertEqual(len(limits.BOUNDS),24)
        for name,b in limits.BOUNDS.items():
            self.assertFalse(any(s in name for s in ['wheel','chassis']))
            for x in [b['min'],b['max']]:
                item=limits.classify(name,x);self.assertEqual(item['status'],'limit');self.assertFalse(item['sdk_rejected'])
            self.assertEqual(limits.classify(name,b['max']+(b['max']-b['min'])*.001)['status'],'over')
            self.assertEqual(limits.classify(name,b['max']-(b['max']-b['min'])*.01)['status'],'near')
            self.assertEqual(limits.classify(name,(b['min']+b['max'])/2)['status'],'normal')
    def test_float32_sdk_rounding_does_not_falsely_reject_endpoint(self):
        n='idx66_arm_r_joint6'
        self.assertFalse(limits.classify(n,1.012300001)['sdk_rejected'])
        self.assertTrue(limits.classify(n,1.013)['sdk_rejected'])
    def test_transient_gripper_overshoot_remains_visible_without_mutation(self):
        s=latest();event=dict(s[limits.GRIPS[1]],topic=limits.GRIPS[1])
        event['data']['end_state'][0]['position']=1.632
        original=copy.deepcopy(event);m=limits.LimitMonitor();m.observe(event)
        self.assertEqual(event,original)
        event['data']['end_state'][0]['position']=.012
        m.observe(event);out=m.status(s)
        self.assertEqual(out['recent_over'][0]['position'],1.632)
        self.assertEqual(out['grippers']['idx71_gripper_r_inner_joint1']['status'],'normal')
        with patch.object(limits.time,'monotonic_ns',return_value=event['mono_ns']+6_000_000_000):
            self.assertEqual(m.status(s)['recent_over'],[])
    def test_out_of_sdk_range_is_still_written_byte_identically(self):
        fixture=DatasetSelection();fixture.setUp()
        pb=fixture.schemas.get('genie_msgs.msg.pb.EndState')();pb.name.append('idx71_gripper_r_inner_joint1');pb.type=1;pb.end_state.add().position=1.632
        event=fixture.event(limits.GRIPS[1],pb.DESCRIPTOR.full_name,1_000_000_000,message=pb)
        before=copy.deepcopy(event);monitor=limits.LimitMonitor()
        with tempfile.TemporaryDirectory() as tmp,patch.object(storage,'data_dir',return_value=Path(tmp)):
            recorder=storage.Recorder(fixture.schemas);recorder.start('overshoot',[])
            recorder.ingest(event);monitor.observe(event);saved=recorder.stop([])
            result=list(raw_events(saved['path'],fixture.schemas))[0]
            self.assertEqual(result['frame'],event['frame'])
            self.assertEqual(result['data']['end_state'][0]['position'],1.632)
        self.assertEqual(event,before)

class PassiveControl(unittest.TestCase):
    def snapshot(self):
        d=latest();d['/wbc/motion_control_status']={'mono_ns':time.monotonic_ns(),'data':{'mode':1,'error_code':0}}
        return d
    def test_idle_does_not_hide_sticky_teleop_floor(self):
        r=diagnostic.assess(self.snapshot(),{'a':True},{'status':'IDLE'},{'floor':100,'gdk_priority':40})
        self.assertEqual(r['status'],'occupied');self.assertTrue(r['advisory_only']);self.assertIsNone(r['can_control'])
    def test_free_snapshot_does_not_claim_motion_verified(self):
        r=diagnostic.assess(self.snapshot(),{'a':True},{'status':'IDLE'},{'floor':0,'gdk_priority':40})
        self.assertEqual(r['status'],'clear');self.assertFalse(r['motion_verified'])
    def test_active_teleop_and_unknown_floor_are_not_clear(self):
        self.assertEqual(diagnostic.assess(self.snapshot(),{'a':True},{'status':'TAKEOVER'},{'floor':0,'gdk_priority':40})['status'],'occupied')
        self.assertEqual(diagnostic.assess(self.snapshot(),{'a':True},{'status':'IDLE'},{})['status'],'unknown')
    def test_log_uses_last_floor_and_actual_gdk_priority(self):
        out=diagnostic.parse_arbitrator("group 'control': floor 0 -> 100\nACCEPTED: /gdk/joint_control -> /wbc/joint_control (pri=40\ngroup 'control': floor 100 -> 0")
        self.assertEqual((out['floor'],out['gdk_priority']),(0,40))
    def test_check_endpoint_is_passive_and_separate_from_replay(self):
        from fastapi.testclient import TestClient
        import app
        with patch.object(diagnostic,'diagnose_control',return_value={'advisory_only':True}),patch('control_session.release_teleop',side_effect=AssertionError('control called')),patch('sdk_worker.select_gdk_control',side_effect=AssertionError('mode called')):
            response=TestClient(app.app).get('/api/control/check')
            self.assertEqual(response.status_code,200);self.assertTrue(response.json()['advisory_only'])
        self.assertNotIn('diagnose_control',__import__('inspect').getsource(app.replay_start))
