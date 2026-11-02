#!/usr/bin/env python3
# 使用说明
# 用途：JSON输入清单的单条/批量转换，并输出结果和进度JSON。
# 推荐从项目根目录运行 ./conversion/run_platform.sh --inputs-json /path/to/inputs.json --input-format factory --dataset-path /path/to/new_dataset --repo-id local/name --task "明确任务" --result /path/to/result.json
# 可加 --workers 4 --threads 1 --progress /path/to/progress.json；继续同一输出加 --resume。
# --inspect只检查源，但仍写指定的结果/进度JSON；不加载运动SDK。
# 多文件实现及完整说明见 README.md，输入契约见 PLATFORM_MCAP.md。
#
"""Single/batch MCAP or factory recording conversion through the existing v2.1 writer."""
import argparse
import json
from pathlib import Path
import traceback
from types import SimpleNamespace
from convert_r2 import convert


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inputs-json', required=True, help='JSON array of MCAP/episode/parent paths')
    p.add_argument('--input-format', choices=['auto','mcap','factory'], default='auto')
    p.add_argument('--output-dir', help='Output root for legacy output-dir/repo-id layout')
    p.add_argument('--dataset-path', help='Exact dataset directory, without adding repo-id')
    p.add_argument('--repo-id', required=True)
    p.add_argument('--task', required=True)
    p.add_argument('--result', required=True)
    p.add_argument('--progress', help='Atomic progress JSON file')
    p.add_argument('--threads',type=int,default=2,help='每条记录的图像写入/编码线程数')
    p.add_argument('--workers',type=int,default=2,help='批量并行处理记录数')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--skip-errors', action='store_true', default=True)
    p.add_argument('--inspect', action='store_true')
    p.add_argument('--fps', type=int, default=30)
    p.add_argument('--image-size', type=int, default=0)
    p.add_argument('--min-free-gb', type=float, default=0)
    args=p.parse_args()
    if len(Path(args.repo_id).parts)!=2 or any(x in ('..','.') for x in Path(args.repo_id).parts) or Path(args.repo_id).is_absolute():
        p.error('repo-id must be namespace/name')
    if not args.dataset_path and not args.output_dir:
        p.error('provide --dataset-path or --output-dir')
    dataset=(Path(args.dataset_path).expanduser().resolve() if args.dataset_path else
             Path(args.output_dir).expanduser().resolve()/args.repo_id)
    result={'completed':False,'dataset_path':str(dataset),'dataset':str(dataset),'robot_control_commands_sent':False}
    from conversion_progress import Progress
    progress=Progress(args.progress)
    progress.update(workers=1,threads=max(1,args.threads))
    code=0;errors=[]
    try:
        inputs=json.loads(Path(args.inputs_json).read_text())
        if not isinstance(inputs,list) or not inputs or not all(isinstance(x,str) for x in inputs):
            raise ValueError('inputs-json must contain a nonempty array of paths')
        if args.fps != 30:
            raise ValueError('fps=30 是 LeRobot 行索引频率；不生成或删除真实视频帧')
        settings=SimpleNamespace(input=inputs,output=str(dataset),repo_id=args.repo_id,task=args.task,tasks_json=None,
            ready_only=False,inspect=args.inspect,threads=max(1,args.threads),codec='original',media='video',fps=args.fps,image_size=args.image_size,
            max_offset_ms=20.,max_camera_age_ms=50.,max_gap_ms=50.,min_free_gb=args.min_free_gb,resume=args.resume,
            skip_errors=args.skip_errors,progress_reporter=progress,errors_out=errors,input_format=args.input_format,raw_mapping=True)
        from convert_r2 import discover
        source_count=len(discover(inputs,args.input_format))
        if args.workers>1 and source_count>1 and not args.inspect:
            from parallel_convert import run_parallel
            run_parallel(settings,min(args.workers,source_count))
        else:convert(settings)
        result['completed']=True
        progress.finish(True)
    except Exception as exc:
        code=1;result['error']=f'{type(exc).__name__}: {exc}'
        progress.finish(False,result['error'])
        print(json.dumps({'failed':result['error']},ensure_ascii=False),flush=True)
    finally:
        meta=dataset/'meta/info.json'
        if meta.exists():
            info=json.loads(meta.read_text())
            result.update(total_episodes=info.get('total_episodes'),total_frames=info.get('total_frames'))
        provenance=dataset/'provenance/conversion.json'
        if provenance.exists():
            result['conversion']=json.loads(provenance.read_text())
        result['errors']=errors
        result['progress']=dict(progress.value)
        out=Path(args.result);out.parent.mkdir(parents=True,exist_ok=True)
        tmp=out.with_suffix('.tmp');tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2));tmp.replace(out)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    return code

if __name__=='__main__': raise SystemExit(main())
