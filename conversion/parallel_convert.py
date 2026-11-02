# 使用说明
# 用途：按源记录并行转换，主进程统一提交LeRobot元数据。
# 使用：经 run_platform.sh --workers N 调用，不直接运行本模块。
# 多文件使用说明：README.md 的批量并行转换。
#
"""Convert sources in separate processes; commit episodes with one metadata writer."""
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
import copy,json,os,shutil,time,uuid
from pathlib import Path
from types import SimpleNamespace

def read(path,default=None):
    try:return json.loads(Path(path).read_text())
    except (OSError,ValueError):return default

def convert_source(payload):
    from convert_r2 import convert
    from conversion_progress import Progress
    from batch_io import json_write
    options,source,folder=payload;folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    options=dict(options,input=[source],output=str(folder/'dataset'),resume=False,errors_out=[],progress_reporter=Progress(folder/'progress.json'))
    result={'source':source,'completed':False}
    try:
        convert(SimpleNamespace(**options));result['completed']=True
    except Exception as exc:result['error']=str(exc)
    result['errors']=options['errors_out']
    json_write(folder/'result.json',result)
    return result

def metadata(root,repo_id):
    from lerobot.common.datasets.lerobot_dataset import LeRobotDatasetMetadata
    from lerobot.common.datasets.utils import load_info
    meta=LeRobotDatasetMetadata.__new__(LeRobotDatasetMetadata)
    meta.repo_id,meta.root,meta.revision=repo_id,root,'v2.1';meta.info=load_info(root)
    if meta.total_episodes:meta.load_metadata()
    else:meta.tasks,meta.task_to_task_index,meta.episodes,meta.episodes_stats,meta.stats={},{},{},{},{}
    return meta

def merge_source(root,shard,args):
    import numpy as np
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    from lerobot.common.datasets.lerobot_dataset import LeRobotDataset
    from batch_io import begin,commit,recover,append_json,json_write
    source_info=read(shard/'meta/info.json');meta=None
    if not (root/'meta/info.json').exists():
        features={k:v for k,v in source_info['features'].items() if k not in ('index','frame_index','episode_index','task_index','timestamp')}
        meta=LeRobotDataset.create(repo_id=args.repo_id,root=root,fps=args.fps,features=features,robot_type='g2a_bimanual',use_videos=bool(source_info['total_videos']),video_backend='pyav').meta
        (root/'provenance').mkdir()
        json_write(root/'provenance/settings.json',read(shard/'provenance/settings.json'))
    else:
        recover(root);meta=metadata(root,args.repo_id)
    # Both metadata files must describe the same vectors and camera fields.
    def schema(info):return json.loads(json.dumps({k:{p:v.get(p) for p in ('dtype','shape','names')} for k,v in info['features'].items()}))
    if schema(meta.info)!=schema(source_info):raise ValueError('该记录字段与目标数据集不同，请分别转换到不同数据集')
    if args.task not in meta.task_to_task_index:meta.add_task(args.task)
    index,offset=meta.total_episodes,meta.total_frames;task_index=meta.task_to_task_index[args.task]
    begin(SimpleNamespace(root=root,meta=meta))
    try:
        source_data=shard/source_info['data_path'].format(episode_chunk=0,episode_index=0)
        table=pq.read_table(source_data)
        for key,value in [('index',None),('episode_index',index),('task_index',task_index)]:
            column=table.column(key)
            array=pc.add(column,pa.scalar(offset,type=column.type)) if key=='index' else pa.array([value]*len(table),type=column.type)
            table=table.set_column(table.schema.get_field_index(key),key,array)
        output=root/meta.get_data_file_path(index);output.parent.mkdir(parents=True,exist_ok=True);pq.write_table(table,output)
        for key in meta.video_keys:
            src=shard/source_info['video_path'].format(episode_chunk=0,episode_index=0,video_key=key)
            dest=root/meta.get_video_file_path(index,key);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dest)
        stats={k:{stat:np.asarray(value) for stat,value in row.items()} for k,row in read_lines(shard/'meta/episodes_stats.jsonl')[0]['stats'].items()}
        for key,shift in [('index',offset),('episode_index',index),('task_index',task_index)]:
            if key in stats:
                for stat in ('min','max','mean'):stats[key][stat]=stats[key][stat]+shift
        meta.save_episode(index,len(table),[args.task],stats)
        with (root/'provenance/frames.jsonl').open('a') as target:
            for row in read_lines_iter(shard/'provenance/frames.jsonl'):
                row['episode_index']=index;target.write(json.dumps(row)+'\n')
        entry=read_lines(shard/'provenance/segments.jsonl')[0];entry['episode_index']=index
        append_json(root/'provenance/segments.jsonl',entry)
        commit(root)
    except BaseException:
        recover(root);raise
    return meta

def read_lines_iter(path):
    with Path(path).open() as f:
        for line in f:
            if line.strip():yield json.loads(line)

def read_lines(path):return list(read_lines_iter(path)) if Path(path).exists() else []

def run_parallel(args,workers):
    from convert_r2 import discover,episode_name
    from batch_io import json_write,completed,recover
    from conversion_progress import Progress
    sources=discover(args.input,args.input_format);root=Path(args.output)
    if root.exists() and not args.resume:raise FileExistsError('输出目录已存在；请使用新名称或 --resume')
    if root.exists() and not (root/'provenance/settings.json').is_file():raise ValueError('该目录不是本转换器的数据集')
    if root.exists():recover(root)
    old=read(root/'provenance/settings.json')
    if old and (old.get('profile')!='raw_head_ptp_24_nullable_v1' or old.get('media')!='video' or any(old.get(k)!=getattr(args,k) for k in ('repo_id','fps'))):raise ValueError('继续转换需使用相同格式设置')
    (root/'provenance/SUCCESS').unlink(missing_ok=True)
    done={r['source'] for r in completed(root)}
    pending=[p for p in sources if str(p) not in done]
    progress=args.progress_reporter
    workspace=root.parent/('.'+root.name+'.parallel-'+uuid.uuid4().hex[:8]);workspace.mkdir(parents=True)
    options={k:v for k,v in vars(args).items() if k not in ('progress_reporter','errors_out')}
    options['input_format']=args.input_format
    payloads=[(options,str(source),str(workspace/str(i))) for i,source in enumerate(pending)]
    all_errors=args.errors_out;meta=metadata(root,args.repo_id) if (root/'meta/info.json').exists() else None
    reports=[];contract_template=read(root/'provenance/conversion.json',{})
    with ProcessPoolExecutor(max_workers=workers,mp_context=get_context('spawn')) as pool:
        futures=[pool.submit(convert_source,p) for p in payloads]
        for position,(source,future) in enumerate(zip(pending,futures)):
            while not future.done():
                states=[read(workspace/str(i)/'progress.json',{}) for i in range(len(pending))]
                finished=[read(workspace/str(i)/'result.json',{}) for i in range(len(pending))]
                completed_count=sum(bool(r.get('completed')) for r in finished)+len(done)
                failed=sum(bool(r and not r.get('completed')) for r in finished)
                progress.update('并行转换',force=True,total_sources=len(sources),completed_sources=completed_count,failed_sources=failed,
                    percent=min(99.,(len(done)*100+sum(s.get('percent',0) for s in states))/len(sources)),
                    workers=workers,threads=args.threads,source_name=episode_name(source),source_index=position+1,
                    active_sources=[{**s,'source_name':episode_name(pending[i])} for i,s in enumerate(states) if s and not finished[i]],
                    input_format=args.input_format,output_episodes=meta.total_episodes if meta else 0)
                time.sleep(.25)
            result=future.result();shard=workspace/str(position)/'dataset'
            if result['completed']:
                try:
                    progress.update('合并 episode',force=True,source_name=episode_name(source))
                    meta=merge_source(root,shard,args)
                    contract_template=read(shard/'provenance/conversion.json',{})
                    for report_path in (shard/'provenance').glob('????????????????.json'):
                        if all(c in '0123456789abcdef' for c in report_path.stem):shutil.copy2(report_path,root/'provenance'/report_path.name)
                    reports.extend((read(shard/'provenance/conversion.json',{})).get('source_reports_this_run',[]))
                except Exception as exc:result={'completed':False,'source':str(source),'error':str(exc)}
            if not result['completed']:
                all_errors.extend(result.get('errors') or [{'source':str(source),'error':result.get('error','worker failed')}])
            # Keep result/progress for aggregation, release only our generated shard.
            if shard.exists():shutil.rmtree(shard)
    if meta:
        contract={**contract_template,'schema':'parallel platform conversion -> LeRobot v2.1','workers':workers,'threads':args.threads,
            'total_episodes':meta.total_episodes,'total_frames':meta.total_frames,'source_reports_this_run':reports,
            'failed_inputs_this_run':len(all_errors),'errors':all_errors,'alignment':'actual head frames; original PTP; prior commands held',
            'action_source':'original prior commands held; nulls preserved; no state fallback'}
        entries=completed(root)
        contract.update(training_ready=bool(entries) and all(e.get('training_ready',False) for e in entries),
            null_action_values=sum(e.get('null_action_values',0) for e in entries),
            null_state_values=sum(e.get('null_state_values',0) for e in entries))
        json_write(root/'provenance/conversion.json',contract)
        if not all_errors:(root/'provenance/SUCCESS').write_text('All requested sources converted.\n')
    progress.update(total_sources=len(sources),completed_sources=len(sources)-len(all_errors),failed_sources=len(all_errors),output_episodes=meta.total_episodes if meta else 0,workers=workers,threads=args.threads)
    shutil.rmtree(workspace)
    progress.finish(not all_errors)
    if all_errors:raise RuntimeError('部分记录转换失败，请查看逐条错误')
