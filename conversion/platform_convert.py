#!/usr/bin/env python3
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
    p.add_argument('--output-dir', help='Output root for legacy output-dir/repo-id layout')
    p.add_argument('--dataset-path', help='Exact dataset directory, without adding repo-id')
    p.add_argument('--repo-id', required=True)
    p.add_argument('--task', required=True)
    p.add_argument('--result', required=True)
    p.add_argument('--progress', help='Atomic progress JSON file')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--skip-errors', action='store_true', default=True)
    p.add_argument('--inspect', action='store_true')
    p.add_argument('--fps', type=int, default=30)
    p.add_argument('--image-size', type=int, default=256)
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
    code=0;errors=[]
    try:
        inputs=json.loads(Path(args.inputs_json).read_text())
        if not isinstance(inputs,list) or not inputs or not all(isinstance(x,str) for x in inputs):
            raise ValueError('inputs-json must contain a nonempty array of paths')
        if not 1<=args.fps<=120 or args.image_size<32 or args.image_size%2:
            raise ValueError('fps must be 1..120; image-size must be even and >=32')
        settings=SimpleNamespace(input=inputs,output=str(dataset),repo_id=args.repo_id,task=args.task,tasks_json=None,
            ready_only=False,inspect=args.inspect,threads=1,codec='h264',media='video',fps=args.fps,image_size=args.image_size,
            max_offset_ms=20.,max_camera_age_ms=50.,max_gap_ms=50.,min_free_gb=args.min_free_gb,resume=args.resume,
            skip_errors=args.skip_errors,progress_reporter=progress,errors_out=errors)
        convert(settings)
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
