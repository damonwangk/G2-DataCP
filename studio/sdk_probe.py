import json,time
from pathlib import Path
import agibot_gdk as g
out={'init':str(g.gdk_init())}
r=g.Robot();time.sleep(1)
for name in ['get_joint_states','get_end_state','get_whole_body_status']:
 try:out[name]=getattr(r,name)()
 except Exception as e:out[name]={'error':str(e)}
Path('evidence/sdk_probe.json').write_text(json.dumps(out,indent=2,default=str))
print('SDK_PROBE',json.dumps({k:v for k,v in out.items() if k!='get_joint_states'},default=str))
g.gdk_release()
