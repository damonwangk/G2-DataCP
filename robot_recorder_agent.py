"""Robot-local raw MCAP writer. No SDK control, no dependency on Ubuntu connection."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'_deps'))
import json,os,signal,threading,time,shutil
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
from native import Receiver,Schemas
from storage import Recorder,data_dir,select_directory,browse_directory,list_files
from selection import normalize_topics
from sdk_limits import LimitMonitor
class Agent:
 def __init__(self):
  self.schemas=Schemas();self.recorder=Recorder(self.schemas);self.limit_monitor=LimitMonitor();self.receiver=None;self.lock=threading.RLock()
  self.last_path=ROOT/'last_recording.json'
  if self.last_path.exists():self.recorder.last=json.loads(self.last_path.read_text())
  self.receiver=Receiver('10.42.1.101',self.schemas,self.on_event,connect_host='127.0.0.1')
  self.receiver.start()
 def on_event(self,event):
  self.recorder.ingest(event);self.limit_monitor.observe(event)
 def status(self):
  return dict(self.recorder.status(),data_dir=str(data_dir()),free_gb=shutil.disk_usage(data_dir()).free/1024**3,
              storage_host='robot',hostname=os.uname().nodename,limits=self.limit_monitor.status(self.receiver.latest),
              streams=self.receiver.stats() if self.receiver else [])
 def start(self,body):
  with self.lock:
   if self.recorder.active:raise ValueError('机器人本机已经在采集')
   topics=normalize_topics(body.get('topics'))
   self.recorder.start(body.get('label','episode'),self.receiver.stats(),body.get('context'),topics=topics)
   return self.status()
 def stop(self):
  with self.lock:
   result=self.recorder.stop(self.receiver.stats() if self.receiver else [])
   if result.get('path'):self.last_path.write_text(json.dumps(result,ensure_ascii=False))
   return result
 def next_episode(self,body):
  with self.lock:
   if not self.recorder.active:raise ValueError('当前没有正在采集的 episode')
   defaults={'label':self.recorder.label,'topics':self.recorder.selected_topics,'context':self.recorder.context}
   defaults.update(body)
   saved=self.stop()
   return {'saved':saved,'recording':self.start(defaults)}
 def snapshot(self):
  now=time.monotonic_ns()
  with self.receiver.lock:
   return {topic:{'data':e['data'],'meta':e['meta'],'publisher':e['publisher'],'wall_ns':str(e['wall_ns']),
      'age_s':(now-e['mono_ns'])/1e9,'packet_bytes':len(e['frame']),'image_bytes':len(e['image']) if e['image'] is not None else None}
      for topic,e in self.receiver.latest.items()}
 def directory(self,body):
  with self.lock:
   if self.recorder.active:raise ValueError('请先结束机器人本机采集，再修改保存位置')
   return select_directory(body['path'],body.get('create',False))
def episode(path):
 p=Path(path).expanduser().resolve()
 if not p.is_dir() or not (p/'raw.mcap').is_file():raise ValueError('该记录尚未完成或不存在')
 report=json.loads((p/'raw.json').read_text())
 files=[{'name':f.name,'bytes':f.stat().st_size} for f in [p/'raw.mcap',p/'raw.json']]
 return {'episode_path':str(p),'report':report,'files':files}
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def json(self,value,status=200):
  b=json.dumps(value,ensure_ascii=False).encode()
  self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def do_GET(self):
  try:
   u=urlparse(self.path);q={k:v[0] for k,v in parse_qs(u.query).items()};agent=self.server.agent
   if u.path=='/status':return self.json(agent.status())
   if u.path=='/snapshot':return self.json(agent.snapshot())
   if u.path=='/camera':
    with agent.receiver.lock:e=agent.receiver.latest.get('/camera/'+q['name'])
    if not e or e['image'] is None:return self.json({'detail':'尚无画面'},404)
    image=e['image'];self.send_response(200);self.send_header('Content-Type','image/jpeg');self.send_header('Content-Length',str(len(image)));self.end_headers();self.wfile.write(image);return
   if u.path=='/directories':return self.json(browse_directory(q.get('path')))
   if u.path=='/episodes':return self.json({'data_dir':str(data_dir()),'files':list_files('all')})
   if u.path=='/episode':return self.json(episode(q['path']))
   if u.path=='/file':
    info=episode(q['path']);name=q['name']
    if name not in [f['name'] for f in info['files']]:raise ValueError('未知记录文件')
    path=Path(info['episode_path'])/name;size=path.stat().st_size;offset=0
    if self.headers.get('Range'):offset=int(self.headers['Range'].removeprefix('bytes=').split('-')[0])
    if not 0<=offset<=size:raise ValueError('传输位置无效')
    self.send_response(206 if offset else 200);self.send_header('Content-Length',str(size-offset));self.send_header('Accept-Ranges','bytes')
    if offset:self.send_header('Content-Range',f'bytes {offset}-{size-1}/{size}')
    self.end_headers()
    with path.open('rb') as f:
     f.seek(offset)
     while chunk:=f.read(1024*1024):self.wfile.write(chunk)
    return
   self.json({'detail':'Not found'},404)
  except (BrokenPipeError,ConnectionResetError):pass
  except Exception as e:self.json({'detail':str(e)},400)
 def do_POST(self):
  try:
   body=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or b'{}');a=self.server.agent
   if self.path=='/start':return self.json(a.start(body))
   if self.path=='/stop':return self.json(a.stop())
   if self.path=='/next':return self.json(a.next_episode(body))
   if self.path=='/directory':return self.json(a.directory(body))
   self.json({'detail':'Not found'},404)
  except Exception as e:self.json({'detail':str(e)},400)
def main():
 agent=Agent();server=ThreadingHTTPServer(('127.0.0.1',18767),Handler);server.agent=agent
 for sig in [signal.SIGTERM,signal.SIGINT]:signal.signal(sig,lambda *_:threading.Thread(target=server.shutdown).start())
 try:server.serve_forever()
 finally:agent.stop();agent.receiver.close();server.server_close()
if __name__=='__main__':main()
