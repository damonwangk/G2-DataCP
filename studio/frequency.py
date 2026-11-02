# 使用说明
# 用途：消息接收、写入和SDK调用的观察频率统计。
# 用法：内部模块，由采集和页面状态调用；只计数，不调度或过滤原始样本。
# 详细说明：采集与预览使用说明.md

"""Observed message / SDK-call rates; never schedules or filters samples."""
from collections import deque
class RateMeter:
 def __init__(self,window=3.):
  self.window=window;self.times=deque();self.first=None;self.last=None;self.count=0
 def add(self,now):
  if self.first is None:self.first=now
  self.last=now;self.count+=1;self.times.append(now);self._trim(now)
 def _trim(self,now):
  while self.times and now-self.times[0]>self.window:self.times.popleft()
 def hz(self,now):
  self._trim(now)
  return (len(self.times)-1)/(self.times[-1]-self.times[0]) if len(self.times)>1 and self.times[-1]>self.times[0] else 0.
 def average(self):
  return (self.count-1)/(self.last-self.first) if self.count>1 and self.last>self.first else 0.

 def share_hz(self,all_calls,now):
  self._trim(now);rate=all_calls.hz(now)
  return rate*len(self.times)/len(all_calls.times) if all_calls.times else 0.
