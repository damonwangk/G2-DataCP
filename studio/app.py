"""Robot-local collection console: live view, MCAP recording, playback and trajectory review."""
from contextlib import asynccontextmanager
import io
import json
import os
import shutil
from pathlib import Path
import library
import workflow
import time
from fastapi import FastAPI, Body, HTTPException
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from native import ROOT, CAMERAS, Receiver, Schemas
from storage import Recorder, Playback, data_dir, select_directory, browse_directory, list_files, file_path, validate

from selection import catalog, motion_inventory

schemas= Schemas()
from robot_storage import RobotStorage, RemoteReceiver
from local_storage import LocalStorage
recorder=LocalStorage()
from sdk_limits import LimitMonitor
limit_monitor=LimitMonitor()
def on_event(event):
    limit_monitor.observe(event)
receiver=RemoteReceiver(recorder)
playback= Playback(schemas)
from postures import limit_status, list_poses, save_pose, pose_path, load_pose, pose_plan

startup_error=''
robot_job=None

@asynccontextmanager
async def lifespan(app):
    global startup_error
    if not library.SETTINGS.exists():library.select_root(str(data_dir()))
    recorder.start_monitor()
    try:
        receiver.start()
    except Exception as exc:
        startup_error=str(exc)
    yield
    playback.stop()
    workflow.conversion.stop();workflow.replay.stop()
    if robot_job and robot_job.running:
        robot_job.stop()
        robot_job.thread.join(timeout=10)
    receiver.close()
    recorder.close()

app=FastAPI(title='R2 MCAP Studio',lifespan=lifespan)
from exports import router as export_router
app.include_router(export_router)
app.include_router(workflow.router)

def require_motion_available():
    recorder.refresh()
    if recorder.active:raise ValueError("请先结束当前采集")
    if robot_job and (robot_job.running or robot_job.preparing):raise ValueError("已有 MCAP 回放或姿态恢复正在执行")
workflow.motion_available=require_motion_available

app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')

@app.exception_handler(ValueError)
async def value_error(request,exc):
    return JSONResponse(status_code=400,content={'detail':str(exc)})

@app.get('/')
def index():
    return FileResponse(ROOT/'static/index.html',headers={'Cache-Control':'no-store'})


def snapshot(source):
    if source=='playback':
        with playback.lock:return dict(playback.latest)
    with receiver.lock:return dict(receiver.latest)

@app.get('/api/state')
def state(source:str='live'):
    latest=snapshot(source)
    samples={topic:{'data':e['data'],'meta':e['meta'],'receive_unix_ns':str(e['wall_ns']),
                    'protobuf_type':e['publisher']['type'],'packet_bytes':e.get('packet_bytes',len(e['frame'])),
                    'payload_bytes':e.get('packet_bytes',len(e['frame']))-128,'image_bytes':e.get('image_bytes',len(e['image']) if e['image'] is not None else None),
                    'age_s':(time.monotonic_ns()-e['mono_ns'])/1e9 if source=='live' else max(0,(playback.start_ns+playback.position*1e9-e['wall_ns'])/1e9)}
             for topic,e in latest.items()}
    return {'server_host':'robot','source':source,'samples':samples,'streams':receiver.stats(),'recording':recorder.status(),'robot_transfers':recorder.transfer_status(),
            'playback':playback.status(),'disk_free_gb':shutil.disk_usage(data_dir()).free/1024**3,
            'data_dir':str(data_dir()),'startup_error':startup_error,
            'limits':recorder.status().get('limits') if recorder.status().get('connected') else limit_monitor.status(snapshot('live')), 
            'robot_replay':robot_job.status() if robot_job else {'running':False}}

@app.get('/api/camera/{name}.jpg')
def camera(name:str,source:str='live'):
    if name not in CAMERAS:
        raise HTTPException(404,'Unknown camera')
    if source=='live':
        try:return Response(receiver.camera(name),media_type='image/jpeg',headers={'Cache-Control':'no-store'})
        except Exception as e:raise HTTPException(404,str(e))
    event=snapshot(source).get('/camera/'+name)
    if not event or event['image'] is None:
        raise HTTPException(404,'尚未收到画面')
    data=event['image']
    return Response(data,media_type='image/jpeg',headers={'Cache-Control':'no-store','X-Sequence':str(event['meta']['sequence'])})

@app.post('/api/record/start')
def record_start(body:dict=Body(default={})):
    if workflow.replay.running():raise ValueError("LeRobot 动作回放正在执行，请先停止")
    recorder.refresh()
    if robot_job and (robot_job.running or robot_job.preparing):
        raise ValueError('实机回放已在记录反馈，结束后再开始新采集')
    context={}
    if body.get('pose_id'):
        context={'selected_posture':load_pose(pose_path(body['pose_id'])),'posture_id':body['pose_id']}
    return recorder.start(str(body.get('label','episode')),receiver.stats(),context,topics=body.get('topics'))

@app.post('/api/record/next')
def record_next(body:dict=Body(default={})):
    if workflow.replay.running():raise ValueError("LeRobot 动作回放正在执行，请先停止")
    if robot_job and (robot_job.running or robot_job.preparing):
        raise ValueError('动作任务的执行反馈由任务保存')
    context={}
    if body.get('pose_id'):
        context={'selected_posture':load_pose(pose_path(body['pose_id'])),'posture_id':body['pose_id']}
    command={'label':str(body.get('label','episode')),'context':context}
    if body.get('topics') is not None:command['topics']=body['topics']
    return recorder.next_episode(command)

@app.post('/api/record/stop')
def record_stop():
    if robot_job and robot_job.running:raise ValueError('执行反馈由动作任务自动保存，请使用停止执行按钮')
    return recorder.stop(receiver.stats())

@app.get('/api/files')
def files(category:str='collection'):
    rows=list_files('all')
    if category not in ('collection','feedback','test','all'):raise ValueError('未知记录分类')
    return {'files':[r for r in rows if category=='all' or r['category']==category],
            'counts':{k:sum(r['category']==k for r in rows) for k in ('collection','feedback','test')},
            'data_dir':str(data_dir())}

@app.get('/api/files/{name}/download')
def download(name:str):
    return FileResponse(file_path(name),filename=name if name.endswith('.mcap') else name+'.mcap',media_type='application/octet-stream')

@app.post('/api/files/{name}/validate')
def validate_file(name:str):
    return validate(file_path(name),schemas)

@app.post('/api/playback/load')
def playback_load(body:dict=Body(...)):
    return playback.load(body['file'],body.get('topics'))

@app.post('/api/playback/play')
def playback_play(body:dict=Body(default={})):
    return playback.play(body.get('speed',1))

@app.post('/api/playback/pause')
def playback_pause():
    playback.stop();return playback.status()

@app.post('/api/playback/seek')
def playback_seek(body:dict=Body(...)):
    return playback.seek(body.get('seconds',0))

@app.post('/api/replay/plan')
def replay_plan(body:dict=Body(...)):
    from robot_replay import plan
    return plan(file_path(body['file']),schemas,float(body.get('start_s',0)),float(body.get('duration_s',0)),float(body.get('speed',1)),lambda:snapshot('live'),controlled_names=body.get('controlled_names'))

@app.post('/api/replay/start')
def replay_start(body:dict=Body(...)):
    if workflow.replay.running():raise ValueError("LeRobot 动作回放正在执行，请先停止")
    recorder.refresh()
    global robot_job
    from robot_replay import RobotJob
    if body.get('confirm_motion') is not True:
        raise ValueError('需要在页面确认现场已准备好执行实机动作')
    if recorder.active:
        raise ValueError('请先结束当前采集')
    if robot_job and (robot_job.running or robot_job.preparing):
        raise ValueError('机器人回放正在运行')
    robot_job=RobotJob(receiver,recorder,schemas)
    return robot_job.start(file_path(body['file']),float(body.get('start_s',0)),float(body.get('duration_s',0)),float(body.get('speed',1)),controlled_names=body.get('controlled_names'))

@app.post('/api/replay/stop')
def replay_stop():
    if robot_job:
        robot_job.stop()
        return robot_job.status()
    return {'running':False}

@app.post('/api/replay/pause')
def replay_pause():
    if robot_job:
        robot_job.pause()
        return robot_job.status()
    return {'running':False}

@app.get('/guide')
def guide():
    return FileResponse(workflow.BASE/'README.md',media_type='text/plain; charset=utf-8')

@app.get('/guide/studio')
def studio_guide():
    return FileResponse(ROOT/'README.md',media_type='text/plain; charset=utf-8')


@app.exception_handler(OSError)
async def filesystem_error(request,exc):
    return JSONResponse(status_code=400,content={'detail':'机器人文件访问失败：'+str(exc)})

@app.get('/api/directories')
def directories(path:str|None=None):
    return browse_directory(path)

@app.post('/api/directory')
def choose_directory(body:dict=Body(...)):
    if recorder.active or (robot_job and (robot_job.running or robot_job.preparing)):raise ValueError('请先结束当前采集或执行任务，再切换目录')
    playback.stop()
    result=recorder.select_directory(str(body.get('path','')),bool(body.get('create',False)))
    library.include_capture_directory(result['data_dir'])
    playback.file=None;playback.latest={};playback.position=0.;playback.duration=0.
    return result

@app.get('/api/poses')
def poses():
    return {'poses':list_poses(),'scope':'躯干、头部、双臂、双夹爪；不保存底盘'}

@app.post('/api/poses')
def pose_save(body:dict=Body(default={})):
    return save_pose(snapshot('live'),str(body.get('label','初始姿态')))

@app.post('/api/poses/{identity}/plan')
def pose_check(identity:str):
    return pose_plan(pose_path(identity),snapshot('live'))

@app.post('/api/poses/{identity}/restore')
def pose_restore(identity:str,body:dict=Body(...)):
    if workflow.replay.running():raise ValueError("LeRobot 动作回放正在执行，请先停止")
    global robot_job
    from robot_replay import RobotJob
    if body.get('confirm_motion') is not True:raise ValueError('需要点击确认恢复姿态')
    if recorder.active:raise ValueError('请先结束当前采集')
    if robot_job and (robot_job.running or robot_job.preparing):raise ValueError('已有机器人动作正在执行')
    robot_job=RobotJob(receiver,recorder,schemas)
    return robot_job.restore(pose_path(identity))


@app.get('/api/data-contract')
def data_contract():
    from data_contract import contract
    return contract(schemas)


@app.get('/api/control/check')
def check_control():
    from control_diagnostics import diagnose_control
    return diagnose_control(receiver.host,lambda:snapshot('live'))


@app.get('/api/data/catalog')
def data_catalog():
    return catalog()

@app.get('/api/files/{name}/contents')
def file_contents(name:str):
    return motion_inventory(file_path(name),schemas)


@app.get('/api/robot-storage')
def robot_storage_status():
    return dict(recorder.refresh(),transfers=recorder.transfer_status())

@app.get('/api/robot-storage/files')
def robot_storage_files():
    return recorder.remote_files()

@app.get('/api/robot-storage/directories')
def robot_storage_directories(path:str|None=None):
    return recorder.browse(path)

@app.post('/api/robot-storage/directory')
def robot_storage_directory(body:dict=Body(...)):
    return choose_directory(body)

@app.post('/api/robot-storage/import')
def robot_storage_import(body:dict=Body(...)):
    return recorder.queue_transfer(body['episode_path'])


@app.get('/api/library/tree')
def library_tree(path:str|None=None):
    return library.tree(path)

@app.get('/api/library/directories')
def library_directories(path:str|None=None):
    return browse_directory(path or str(library.library_root()))

@app.post('/api/library/root')
def library_select_root(body:dict=Body(...)):
    return library.select_root(body['path'])

@app.post('/api/library/delete')
def library_delete(body:dict=Body(...)):
    if body.get('confirm') is not True:raise ValueError('请确认永久删除所选记录')
    paths=library.resolve_episodes(body.get('episode_paths'))
    if workflow.conversion.running() and any(p==q or p.is_relative_to(q) or q.is_relative_to(p) for p in paths for q in workflow.conversion.sources):raise ValueError('所选记录正在转换，请先停止转换')
    state=recorder.refresh()
    active=Path(state['path']).parent.resolve() if state.get('active') and state.get('path') else None
    source=(data_dir()/robot_job.file).resolve() if robot_job and robot_job.kind=='replay' and (robot_job.running or robot_job.preparing) else None
    if any(protected and (p==protected or p.is_relative_to(protected)) for p in paths for protected in [active,source]):raise ValueError('正在采集或回放使用的记录暂不能删除')
    if playback.file:
        viewed=getattr(playback,'path',None)
        if viewed and any(viewed==p or viewed.is_relative_to(p) for p in paths):
            playback.stop();playback.file=None;playback.latest={};playback.position=0.;playback.duration=0.
    result=library.delete_episodes(paths)
    from exports import exports
    for key,job in list(exports.items()):
        if any(e['path']==p or e['path'].is_relative_to(p) for p in paths for e in job['entries']):exports.pop(key,None)
    return result
