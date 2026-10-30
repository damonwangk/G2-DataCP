import json,time
from pathlib import Path
import agibot_gdk as g
out=[]
g.gdk_init();r=g.Robot();time.sleep(2)
def snapshot(label):
 s=r.get_motion_control_status();return dict(stage=label,mode=s.mode,control_mode=s.control_mode,error_code=s.error_code,error_msg=s.error_msg)
out.append(snapshot('before'))
try:out.append({'set_position_control_return':r.set_control_mode(1)})
except Exception as e:out.append({'set_position_control_error':str(e)})
for i in range(3):time.sleep(1);out.append(snapshot('after_'+str(i+1)))
Path('evidence/control_mode_probe.json').write_text(json.dumps(out,indent=2));print(json.dumps(out),flush=True);g.gdk_release()
