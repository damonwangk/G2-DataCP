"""Ubuntu control-plane client and completed-file transfer; never records live packets."""
import json,re,shutil,threading,time,uuid,urllib.request,urllib.error
from pathlib import Path
from urllib.parse import urlencode
from native import ROOT
from storage import data_dir,dumps
CONFIG=ROOT/'config/robot_storage.json'
DEFAULT_URL='http://127.0.0.1:18767'
class RobotStorage:
 def __init__(self):
  self.base=json.loads(CONFIG.read_text()).get('url',DEFAULT_URL) if CONFIG.exists() else DEFAULT_URL
  self.lock=threading.RLock();self.stop_event=threading.Event();self.cache={'active':False,'storage_host':'robot','error':'正在连接机器人本机采集服务'}
  self.transfer_file=ROOT/'config/robot_transfers.json'
  self.jobs=json.loads(self.transfer_file.read_text()) if self.transfer_file.exists() else {}
  for job in self.jobs.values():
   if job.get('status')=='transferring':job['status']='pending'
  self.directory=None;self.latest={}
 def request(self,path,body=None,timeout=20):
  req=urllib.request.Request(self.base+path,data=dumps(body) if body is not None else None,headers={'Content-Type':'application/json'})
  try:
   with urllib.request.urlopen(req,timeout=timeout) as r:return json.load(r)
  except urllib.error.HTTPError as e:
   try:message=json.load(e).get('detail',str(e))
   except Exception:message=str(e)
   raise ValueError(message) from e
  except OSError as e:raise ValueError('机器人本机采集服务暂时不可达：'+str(e)) from e
 @property
 def active(self):return bool(self.cache.get('active'))
 def status(self):
  with self.lock:return dict(self.cache)
 def refresh(self):
  try:
   state=self.request('/status',timeout=4);state['connected']=True
   snapshot=self.request('/snapshot',timeout=4);now=time.monotonic_ns()
   latest={t:dict(e,wall_ns=int(e['wall_ns']),mono_ns=now-int(e['age_s']*1e9),frame=b'',image=None) for t,e in snapshot.items()}
   with self.lock:self.cache=state;self.latest=latest
  except Exception as e:
   with self.lock:self.cache=dict(self.cache,connected=False,error=str(e))
  return self.status()
 def start_monitor(self):
  self.thread=threading.Thread(target=self._monitor,daemon=True);self.thread.start()
 def close(self):
  self.stop_event.set()
  if hasattr(self,'thread'):self.thread.join(timeout=5)
 def _monitor(self):
  tick=0
  while not self.stop_event.is_set():
   self.refresh()
   if tick%25==0 and self.cache.get('connected'):
    try:
     for source,job in list(self.jobs.items()):
      if job.get('status')=='pending':self.queue_transfer(source)
     for episode in self.request('/episodes')['files']:
      if episode.get('category') in ('collection','feedback'):
       key=episode['episode_dir'];job=self.jobs.get(key,{})
       if not job or job.get('status')=='pending':self.queue_transfer(key)
    except Exception:pass
   tick+=1;self.stop_event.wait(.2)
 def start(self,label,stats=None,context=None,topics=None):
  state=self.request('/start',{'label':label,'topics':topics,'context':context or {}})
  with self.lock:self.cache=dict(state,connected=True)
  if context and context.get('kind') in ('replay','posture'):
   self.directory=ROOT/'runtime'/('robot_trace_'+uuid.uuid4().hex[:10]);self.directory.mkdir(parents=True)
  else:self.directory=None
  return state
 def stop(self,stats=None):
  result=self.request('/stop',{})
  self.refresh()
  if not result.get('episode_dir'):return result
  job=self.queue_transfer(result['episode_dir'],self.directory)
  return dict(result,file=job['file'],robot_episode_dir=result['episode_dir'],path=str(Path(job['destination'])/'raw.mcap'),transferring=job['status']!='complete',transfer=job)
 def _save_jobs(self):
  self.transfer_file.parent.mkdir(parents=True,exist_ok=True)
  tmp=self.transfer_file.with_suffix('.tmp');tmp.write_bytes(dumps(self.jobs));tmp.replace(self.transfer_file)
 def transfer_status(self):
  with self.lock:return [dict(j) for j in sorted(self.jobs.values(),key=lambda j:j.get('started',0),reverse=True)]
 def queue_transfer(self,path,extras=None):
  with self.lock:
   existing=self.jobs.get(path)
   if existing and existing.get('status') in ('transferring','complete'):
    if extras:
     existing['extras']=str(extras)
     if existing['status']=='complete':self._copy_extras(existing)
    return dict(existing)
  info=self.request('/episode?'+urlencode({'path':path}))
  with self.lock:
   if existing:
    job=existing
   else:
    identity=Path(path).name;root=data_dir()
    if info['report'].get('record_kind')=='collection':
     numbers=[int(m.group(1)) for p in root.iterdir() if (m:=re.match(r'^episode_(\d+)_',p.name))]
     numbers += [int(m.group(1)) for j in self.jobs.values() if Path(j['destination']).parent==root and (m:=re.match(r'^episode_(\d+)_',j['file']))]
     stamp=re.sub(r'^episode_\d+_','',identity)
     identity='episode_'+str(max(numbers,default=0)+1)+'_'+stamp
    if (root/identity).exists():identity+='_'+uuid.uuid4().hex[:6]
    job={'source':path,'file':identity,'destination':str(root/identity),'staging':str(root/('.robot_import_'+uuid.uuid4().hex[:10])),'started':time.time()}
   job.update(status='transferring',error='',total_bytes=sum(f['bytes'] for f in info['files']),received_bytes=0)
   if extras:job['extras']=str(extras)
   self.jobs[path]=job;self._save_jobs()
   threading.Thread(target=self._transfer,args=(path,info),daemon=True).start()
   return dict(job)
 def _copy_extras(self,job):
  folder=Path(job.get('extras','/nonexistent'))
  if folder.is_dir():
   for p in folder.glob('*.mcap'):shutil.copy2(p,Path(job['destination'])/p.name)
 def _transfer(self,key,info):
  job=self.jobs[key];stage=Path(job['staging']);stage.mkdir(parents=True,exist_ok=True)
  try:
   transferred=0
   for entry in info['files']:
    name=entry['name'];partial=stage/(name+'.download');offset=partial.stat().st_size if partial.exists() else 0
    if offset>entry['bytes']:partial.unlink();offset=0
    if offset<entry['bytes']:
     req=urllib.request.Request(self.base+'/file?'+urlencode({'path':key,'name':name}),headers={'Range':'bytes='+str(offset)+'-'} if offset else {})
     with urllib.request.urlopen(req,timeout=30) as response,partial.open('ab' if offset else 'wb') as target:
      while chunk:=response.read(1024*1024):
       target.write(chunk);offset+=len(chunk)
       with self.lock:job['received_bytes']=transferred+offset
    if offset!=entry['bytes']:raise OSError('文件传输未完成，可点击重试继续取回')
    transferred+=offset
   for entry in info['files']:(stage/(entry['name']+'.download')).replace(stage/entry['name'])
   report=json.loads((stage/'raw.json').read_text())
   report.update(original_episode_id=Path(key).name,file=job['file'],path=str(Path(job['destination'])/'raw.mcap'),episode_dir=job['destination'],robot_source={'host':'g2','episode_dir':key},bytes=(stage/'raw.mcap').stat().st_size)
   (stage/'raw.json').write_bytes(dumps(report))
   stage.rename(job['destination']);self._copy_extras(job)
   with self.lock:
    job.update(status='complete',received_bytes=transferred,finished=time.time());self._save_jobs()
  except Exception as e:
   with self.lock:job.update(status='failed',error=str(e));self._save_jobs()
 def remote_files(self):
  result=self.request('/episodes')
  for row in result['files']:row['transfer']=self.jobs.get(row['episode_dir'])
  return result
 def browse(self,path=None):return self.request('/directories'+('?' +urlencode({'path':path}) if path else ''))
 def select_directory(self,path,create=False):
  result=self.request('/directory',{'path':path,'create':create});self.refresh();return result


class RemoteReceiver:
 def __init__(self,client):
  self.client=client;self.lock=client.lock;self.host='10.42.1.101'
 @property
 def latest(self):return self.client.latest
 def start(self):pass
 def close(self):pass
 def stats(self):return self.client.status().get('streams',[])
 def camera(self,name):
  with urllib.request.urlopen(self.client.base+'/camera?'+urlencode({'name':name}),timeout=5) as r:return r.read()
