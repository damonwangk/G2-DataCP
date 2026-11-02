# 使用说明
# 用途：转换/续转、LeRobot和原生回放调度及LeRobot完整数据集导出。
# 用法：内部路由由 app.py 挂载；使用网页或根目录 datacp convert/replay/native-replay/export-lerobot；回放命令会发动作。
# 详细说明：数据管理与转换调度使用说明.md

"""Robot-local conversion and LeRobot workers, independent of MCAP preview."""
import json, os, signal, subprocess, threading, time, uuid, math, tarfile, hashlib, re
from pathlib import Path
from urllib.parse import quote
from fastapi import APIRouter, Body
from fastapi.responses import FileResponse, StreamingResponse
BASE=Path(os.environ.get('R2_DATACP_ROOT',str(Path(__file__).resolve().parent.parent)))
CONVERSION=BASE/'conversion'; REPLAY=BASE/'replay'; RUNTIME=BASE/'runtime'
PYTHON=CONVERSION/'.venv/bin/python'
router=APIRouter()

def read_json(path,default=None):
    try:return json.loads(Path(path).read_text())
    except (OSError,ValueError):return default

def workflow_defaults():
    saved=read_json(BASE/'studio/config/workflow.json',{})
    root=str(saved.get('lerobot_root') or BASE/'datasets')
    return {'conversion_output':root,'lerobot_root':root}

def output_root(value):
    root=Path(value).expanduser()
    if not root.is_absolute():raise ValueError('LeRobot 保存目录必须是机器人上的绝对路径')
    root=root.resolve()
    try:root.mkdir(parents=True,exist_ok=True)
    except OSError as exc:raise ValueError('无法使用 LeRobot 保存目录：'+str(exc)) from exc
    return root

def save_workflow_root(root):
    path=BASE/'studio/config/workflow.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    saved=read_json(path,{})
    saved['lerobot_root']=str(root)
    temporary=path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(saved,ensure_ascii=False,indent=2))
    temporary.replace(path)
    return workflow_defaults()

def dataset_folder_name(value):
    if not isinstance(value,str):raise ValueError('请输入数据集文件夹名称')
    name=value.strip()
    if not name or name=='.' or '..' in name or any(c in name for c in '/\\') or any(ord(c)<32 for c in name):
        raise ValueError('数据集名称应为单个文件夹名，不能包含路径分隔符或 ..')
    return name

def internal_repo_id(name):
    slug=re.sub(r'[^A-Za-z0-9_-]+','_',name).strip('_')[:60] or 'dataset'
    return 'local/'+slug+'_'+hashlib.sha256(name.encode()).hexdigest()[:10]

def worker_env():
    env=dict(os.environ)
    env['PYTHONPATH']=str(CONVERSION/'extra_deps')
    env.update(OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',HF_HUB_OFFLINE='1',HF_DATASETS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
    return env

def job_pointer(kind,dataset=None):
    if dataset is None:return RUNTIME/(kind+'_latest.json')
    key=hashlib.sha256(str(Path(dataset).resolve()).encode()).hexdigest()
    return RUNTIME/'conversion_by_dataset'/(key+'.json')

def saved_job(pointer,kind='conversion'):
    """只恢复明确记录的提交任务；不使用会受时钟回退影响的文件时间挑选。"""
    value=read_json(pointer,{}) or {};folder=Path(value.get('run_dir','')).resolve()
    return folder if folder.parent==RUNTIME.resolve() and folder.name.startswith(kind+'_') and (folder/'command.json').is_file() else None

def remember_job(kind,folder):
    targets=[job_pointer(kind)]
    if kind=='conversion':
        dataset=conversion_context(folder).get('dataset_path')
        if dataset:targets.append(job_pointer(kind,dataset))
    for target in targets:
        target.parent.mkdir(parents=True,exist_ok=True)
        tmp=target.with_suffix('.tmp');tmp.write_text(json.dumps({'run_dir':str(folder.resolve())}));tmp.replace(target)


def conversion_context(folder):
    """Recover a saved conversion's output and original arguments, without running it."""
    command=read_json(folder/'command.json',[])
    def arg(name,default=None):
        try:return command[command.index(name)+1]
        except (ValueError,IndexError):return default
    repo=arg('--repo-id');output=arg('--dataset-path')
    if not output and repo and arg('--output-dir'):output=str(Path(arg('--output-dir'))/repo)
    if not output:return {}
    result={'dataset_path':str(Path(output).resolve())}
    inputs=read_json(folder/'inputs.json',[]);task=arg('--task')
    if inputs and task and repo:
        body={'episode_paths':inputs,'task':task,'input_format':arg('--input-format','auto'),
              'workers':int(arg('--workers',2)),'threads':int(arg('--threads',2)),'resume':True}
        if arg('--dataset-path') and internal_repo_id(Path(output).name)==repo:
            body.update(output_dir=str(Path(output).parent),dataset_name=Path(output).name)
        elif not arg('--dataset-path'):
            body.update(output_dir=arg('--output-dir'),repo_id=repo)
        else:return result
        result.update(resume_request=body,resume_run_dir=str(folder),source_paths=inputs,source_count=read_json(folder/'progress.json',{}).get('total_sources') or len(inputs))
    return result

class Job:
    def __init__(self,kind,restore_latest=False):
        self.kind=kind; self.process=None; self.folder=None; self.sources=[]; self.lock=threading.RLock(); self.cancelled=False;self.startup_error=""
        self.restore_latest=restore_latest;self.restored=False
    def running(self):return self.process is not None and self.process.poll() is None
    def start(self,command,source_paths=(),prepare=None):
        with self.lock:
            if self.running():raise ValueError('该任务正在运行')
            self.folder=RUNTIME/(self.kind+'_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]); self.folder.mkdir(parents=True)
            self.sources=[Path(p).resolve() for p in source_paths];self.cancelled=False;self.process=None;self.startup_error="";self.restored=False;self.restore_latest=False
            try:
                if prepare:prepare(self.folder)
                cmd=command(self.folder)
                (self.folder/'command.json').write_text(json.dumps(cmd,ensure_ascii=False))
                remember_job(self.kind,self.folder)
                with (self.folder/'worker.log').open('w') as log:
                    self.process=subprocess.Popen(cmd,cwd=BASE,env=worker_env(),stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            except Exception as exc:
                self.startup_error=str(exc)
                raise ValueError('无法启动任务：'+str(exc)) from exc
            return self.status()
    def stop(self):
        with self.lock:
            if self.running():
                self.cancelled=True;proc=self.process
                (self.folder/'stopped.json').write_text(json.dumps({'stopped_by_user':True}))
                try:os.killpg(proc.pid,signal.SIGTERM)
                except ProcessLookupError:return self.status()
                def finish():
                    try:proc.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        try:os.killpg(proc.pid,signal.SIGKILL)
                        except ProcessLookupError:pass
                threading.Thread(target=finish,daemon=True).start()
            return self.status()
    def status(self):
        if not self.folder and self.restore_latest:
            self.restore_latest=False
            candidates=[p for p in RUNTIME.glob(self.kind+'_*') if any((p/name).is_file() for name in ('command.json','progress.json','result.json'))]
            if candidates:
                def latest(folder):return max((folder/name).stat().st_mtime_ns for name in ('command.json','progress.json','result.json','stopped.json') if (folder/name).is_file())
                self.folder=saved_job(job_pointer(self.kind),self.kind) or max(candidates,key=latest);self.restored=True
                self.cancelled=(self.folder/'stopped.json').exists()
        if not self.folder:return {'running':False,'phase':'尚未开始','error':'','log':'','progress':{},'result':None}
        running=self.running();code=self.process.poll() if self.process else None;log=''
        try:
            with (self.folder/'worker.log').open('rb') as f:f.seek(max(0,f.seek(0,2)-12000));log=f.read().decode('utf-8','replace')
        except OSError:pass
        result=read_json(self.folder/'result.json')
        interrupted=self.process is None and self.restored and result is None and not self.cancelled
        if self.process is None and self.restored:
            code=0 if (result or {}).get('completed') else -15 if self.cancelled else None if interrupted else 1
        error=self.startup_error or ('上次任务已中断，尚未生成最终结果，可继续转换。' if interrupted else '' if running or code==0 or self.cancelled else (result or {}).get('error',f'任务退出码 {code}，请查看日志'))
        if self.kind=='conversion' and not running and code==0 and not (result or {}).get('completed'):
            error='转换进程已退出，但缺少最终完成结果；尚未确认生成可用 episode。'
        context=conversion_context(self.folder) if self.kind=='conversion' else {}
        return {'running':running,'phase':'正在停止' if running and self.cancelled else '运行中' if running else '已停止' if self.cancelled else '已中断' if interrupted else '失败' if error else '完成',
                'error':error,'log':log,'progress':read_json(self.folder/'progress.json',{}),'result':result,'run_dir':str(self.folder),'return_code':code,'sources':[str(p) for p in self.sources],**context}

conversion=Job('conversion',restore_latest=True);replay=Job('lerobot')
motion_available=lambda:None

def info(dataset,episode=None):
    root=Path(dataset).expanduser().resolve()
    if not (root/'meta/info.json').is_file():raise ValueError('请选择包含 meta/info.json 的 LeRobot 数据集')
    cmd=[str(PYTHON),str(REPLAY/'dataset_info.py'),'--dataset',str(root)]
    if episode is not None:cmd+=['--episode',str(int(episode))]
    try:p=subprocess.run(cmd,env=worker_env(),capture_output=True,text=True,timeout=60)
    except subprocess.TimeoutExpired as exc:raise ValueError('读取数据集超时，请检查路径和文件') from exc
    if p.returncode:raise ValueError(p.stderr[-3000:] or p.stdout[-3000:])
    return json.loads(p.stdout)

@router.get('/api/workflow/state')
def state():return {'defaults':workflow_defaults(),'conversion':conversion.status(),'replay':replay.status()}

@router.post('/api/workflow/settings')
def settings(body:dict=Body(...)):
    value=body.get('lerobot_root')
    if not isinstance(value,str) or not value.strip():raise ValueError('请选择 LeRobot 保存目录')
    return {'defaults':save_workflow_root(output_root(value))}

@router.post('/api/conversion/start')
def convert(body:dict=Body(...)):
    input_format=body.get('input_format','auto')
    if input_format not in ('auto','mcap','factory'):raise ValueError('未知转换输入格式')
    paths=body.get('episode_paths') or ([body['source_dir']] if body.get('source_dir') else [])
    if not paths:raise ValueError('请选择 MCAP episode 或输入目录')
    paths=[str(Path(p).expanduser().resolve()) for p in paths]
    if any(not Path(p).exists() for p in paths):raise ValueError('输入路径不存在')
    name=dataset_folder_name(body['dataset_name']) if 'dataset_name' in body else None
    if name is not None:
        repo=internal_repo_id(name)
    else:
        repo=body.get('repo_id','').strip();parts=repo.split('/')
        if len(parts)!=2 or any(p in ('','.','..') or not all(c.isalnum() or c in '-_.' for c in p) for p in parts):raise ValueError('repo_id 请使用 local/任务名称 格式')
    task=body.get('task','').strip()
    if not task:raise ValueError('请输入训练任务描述')
    out=output_root(body.get('output_dir') or workflow_defaults()['conversion_output'])
    def prepare(folder):(folder/'inputs.json').write_text(json.dumps(paths,ensure_ascii=False))
    def command(folder):
        cmd=[str(PYTHON),str(CONVERSION/'platform_convert.py'),'--inputs-json',str(folder/'inputs.json'),'--output-dir',str(out),'--repo-id',repo,'--task',task,'--result',str(folder/'result.json'),'--progress',str(folder/'progress.json')]
        cmd+=['--input-format',input_format]
        cmd+=['--threads',str(max(1,int(body.get('threads',2)))),'--workers',str(max(1,int(body.get('workers',2))))]
        if name is not None:cmd+=['--dataset-path',str(out/name)]
        if body.get('resume'):cmd+=['--resume']
        return cmd
    result=conversion.start(command,paths,prepare)
    save_workflow_root(out)
    return result

@router.post('/api/conversion/stop')
def stop_convert():return conversion.stop()

@router.get('/api/conversion/files')
def conversion_files(dataset:str,path:str=''):
    root=Path(dataset).expanduser().resolve()
    folder=(root/path).resolve()
    if not folder.is_relative_to(root):raise ValueError('请选择当前转换结果内的目录')
    relative=folder.relative_to(root).as_posix()
    response={'dataset':str(root),'path':'' if relative=='.' else relative,
              'parent':None if folder==root else ('' if folder.parent==root else folder.parent.relative_to(root).as_posix()),
              'entries':[],'exists':folder.is_dir()}
    if not folder.exists():return response
    if not folder.is_dir():raise ValueError('请选择目录查看文件')
    for item in sorted(folder.iterdir(),key=lambda p:(not p.is_dir(),p.name)):
        if item.name.startswith('.') or not item.resolve().is_relative_to(root):continue
        response['entries'].append({'name':item.name,'path':item.relative_to(root).as_posix(),
                                    'is_dir':item.is_dir(),'size':None if item.is_dir() else item.stat().st_size})
    return response

@router.get('/api/lerobot/datasets')
def datasets(root:str=None):
    base=Path(root or workflow_defaults()['lerobot_root']).expanduser().resolve()
    if not base.is_dir():return {'datasets':[]}
    rows=[]
    for p in sorted(base.rglob('meta/info.json')):
        if any(part=='.r2-transaction' or (part.startswith('.') and ('.r2-initializing-' in part or '.parallel-' in part)) for part in p.relative_to(base).parts):continue
        meta=read_json(p,{})
        rows.append({'path':str(p.parent.parent),'name':base.name if p.parent.parent==base else str(p.parent.parent.relative_to(base)),'total_episodes':meta.get('total_episodes',0),'fps':meta.get('fps'),
                     'conversion_complete':(p.parent.parent/'provenance/SUCCESS').is_file() if (p.parent.parent/'provenance/settings.json').is_file() else None})
    return {'datasets':rows}

def dataset_conversion_status(dataset):
    target=str(Path(dataset).expanduser().resolve())
    active=conversion.status()
    pinned=saved_job(job_pointer('conversion',target))
    if active.get('dataset_path')==target and (active.get('running') or (pinned is not None and str(pinned)==active.get('run_dir'))):return active
    # 每个输出目录记住本次明确提交的任务，避免同名 full 的旧整目录请求串入。
    folder=saved_job(job_pointer('conversion',target))
    if folder is None:
        matches=[p for p in RUNTIME.glob('conversion_*') if p.is_dir() and conversion_context(p).get('dataset_path')==target]
        if not matches:return None
        inputs={tuple(read_json(p/'inputs.json',[])) for p in matches}
        if len(inputs)>1:
            return {'running':False,'phase':'需重新选择原记录','dataset_path':target,
                    'error':'同一输出目录存在多个不同来源的历史任务，无法自动确定要继续哪一条；请在转换区明确选择原记录。'}
        folder=max(matches,key=lambda p:(p/'command.json').stat().st_mtime_ns)
    job=Job('conversion');job.folder=folder;job.restored=True
    job.cancelled=(folder/'stopped.json').exists()
    return job.status()

@router.post('/api/conversion/resume')
def resume_conversion(body:dict=Body(...)):
    """继续明确的任务编号；原输入只从该任务读取，不从当前页面或最新历史猜测。"""
    folder=Path(body.get('run_dir','')).expanduser().resolve()
    if folder.parent!=RUNTIME.resolve() or not folder.name.startswith('conversion_') or not (folder/'command.json').is_file():
        raise ValueError('转换任务编号无效，请刷新数据集')
    context=conversion_context(folder);target=str(Path(body.get('dataset','')).expanduser().resolve())
    if context.get('dataset_path')!=target:raise ValueError('继续任务与所选数据集不一致，请刷新')
    latest=saved_job(job_pointer('conversion',target))
    if latest is not None and latest!=folder:raise ValueError('此数据集已有新的转换任务，请刷新后继续')
    request=context.get('resume_request')
    if not request:raise ValueError('原始转换参数不完整，请在转换区重新选择原记录')
    return convert(request)


@router.get('/api/lerobot/episodes')
def episodes(dataset:str):
    result=info(dataset)
    if not result.get('episodes'):
        job=dataset_conversion_status(dataset)
        if job:
            result['conversion_status']={k:job.get(k) for k in ('phase','running','error','progress','dataset_path','resume_request','resume_run_dir','source_paths','source_count')}
    return result

@router.post('/api/lerobot/inspect')
def inspect(body:dict=Body(...)):return info(body['dataset'],body['episode'])

@router.post('/api/lerobot/replay/start')
def start_replay(body:dict=Body(...)):
    motion_available()
    dataset=str(Path(body['dataset']).expanduser().resolve());episode=int(body['episode'])
    info(dataset,episode)
    return replay.start(lambda f:[str(REPLAY/'replay.sh'),'--dataset',dataset,'--episode',str(episode),'--run',str(f)],[dataset])

@router.post('/api/lerobot/replay/stop')
def stop_replay():return replay.stop()

@router.post('/api/native/inspect')
def inspect_native(body:dict=Body(...)):
    cmd=[str(PYTHON),str(REPLAY/'native_episode.py'),'--source',str(Path(body['source']).expanduser().resolve())]
    proc=subprocess.run(cmd,env=worker_env(),capture_output=True,text=True,timeout=120)
    if proc.returncode:raise ValueError(proc.stderr[-3000:] or '无法读取原生记录')
    return json.loads(proc.stdout)

@router.post('/api/native/replay/start')
def start_native_replay(body:dict=Body(...)):
    motion_available()
    source=Path(body['source']).expanduser().resolve()
    if not (source/'record/-genie-bundle_data.pbdat').is_file():raise ValueError('请选择原生采集记录目录')
    return replay.start(lambda f:[str(REPLAY/'replay.sh'),'--native-recording',str(source),'--run',str(f)],[source])

@router.post('/api/native/replay/stop')
def stop_native_replay():return replay.stop()

def export_root(dataset):
    root=Path(dataset).expanduser().resolve()
    if not (root/'meta/info.json').is_file():raise ValueError('请选择 LeRobot 数据集目录')
    return root

def export_paths(root):
    return [p for p in sorted(root.rglob('*')) if p.is_file()
            and not any(part.startswith('.') for part in p.relative_to(root).parts)
            and p.resolve().is_relative_to(root)]

@router.get('/api/lerobot/export/manifest')
def export_manifest(dataset:str):
    root=export_root(dataset)
    files=[{'path':p.relative_to(root).as_posix(),'size':p.stat().st_size,
            'url':'/api/lerobot/export/file?dataset='+quote(str(root),safe='')+'&path='+quote(p.relative_to(root).as_posix(),safe='')}
           for p in export_paths(root)]
    return {'dataset':str(root),'name':root.name,'files':files,'total_bytes':sum(p['size'] for p in files)}

@router.get('/api/lerobot/export/file')
def export_file(dataset:str,path:str):
    root=export_root(dataset);file=(root/path).resolve()
    if not file.is_relative_to(root) or not file.is_file():raise ValueError('数据集文件不存在')
    return FileResponse(file,media_type='application/octet-stream')

@router.get('/api/lerobot/export/archive')
def export_archive(dataset:str):
    from exports import tar_stream
    root=export_root(dataset);entries=[]
    for p in export_paths(root):
        t=tarfile.TarInfo(root.name+'/'+p.relative_to(root).as_posix())
        t.size=p.stat().st_size;t.mtime=int(p.stat().st_mtime);t.mode=0o644
        entries.append({'path':p,'bytes':t.size,'header':t.tobuf(format=tarfile.PAX_FORMAT)})
    length=1024+sum(len(e['header'])+math.ceil(e['bytes']/512)*512 for e in entries)
    return StreamingResponse(tar_stream(entries),media_type='application/x-tar',
        headers={'Content-Length':str(length),'Content-Disposition':"attachment; filename*=UTF-8''"+quote(root.name+'.tar'),'Cache-Control':'no-store'})
