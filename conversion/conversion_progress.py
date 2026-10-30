"""Atomic, throttled job progress. No timing simulation or robot dependencies."""
import json
from pathlib import Path
import time

class Progress:
    def __init__(self,path=None):
        self.path=Path(path) if path else None;self.started=time.monotonic();self.last_write=0.
        self.value=dict(phase='准备',source_index=0,total_sources=0,completed_sources=0,failed_sources=0,
            source_name='',processed_frames=0,total_frames=None,percent=0.,elapsed_s=0.,bytes_read=0,total_bytes=None)
        self.update(force=True)
    def update(self,phase=None,fraction=None,force=False,**fields):
        if phase is not None:self.value['phase']=phase
        self.value.update(fields)
        if fraction is not None and self.value['total_sources']:
            count=self.value['total_sources'];index=self.value['source_index']
            self.value['percent']=max(self.value['percent'],round(100*(max(0,index-1)+min(1.,max(0.,fraction)))/count,2))
        now=time.monotonic();self.value['elapsed_s']=round(now-self.started,1)
        if self.path and (force or now-self.last_write>=.25):
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temp=self.path.with_suffix(self.path.suffix+'.tmp')
            temp.write_text(json.dumps(self.value,ensure_ascii=False));temp.replace(self.path)
            self.last_write=now
    def source(self,index,name,size=None):
        self.update('读取源文件',fraction=0,force=True,source_index=index,source_name=name,
            processed_frames=0,total_frames=None,bytes_read=0,total_bytes=size,error='')
    def finish_source(self,success,error=''):
        key='completed_sources' if success else 'failed_sources'
        self.update('该条完成' if success else '该条失败',fraction=1,force=True,error=error,
            **{key:self.value[key]+1})
    def finish(self,success,error=''):
        self.update('完成' if success else '失败',force=True,percent=100. if success else self.value['percent'],error=error)
