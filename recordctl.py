#!/usr/bin/env python3
"""Run on the robot; the daemon owns acquisition after this command exits."""
import argparse,json,urllib.request
p=argparse.ArgumentParser();p.add_argument('command',choices=['start','stop','status','directory','episodes'])
p.add_argument('--label',default='episode');p.add_argument('--topics',nargs='+');p.add_argument('--path');p.add_argument('--create',action='store_true')
a=p.parse_args();body=None
if a.command=='start':body={'label':a.label,'topics':a.topics}
elif a.command=='stop':body={}
elif a.command=='directory':body={'path':a.path,'create':a.create}
req=urllib.request.Request('http://127.0.0.1:18767/'+a.command,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
with urllib.request.urlopen(req,timeout=30) as response:print(json.dumps(json.load(response),ensure_ascii=False,indent=2))
