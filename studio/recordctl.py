#!/usr/bin/env python3
# 使用说明
# 用途：直接操作已启动的本机采集后台；命令返回后后台可继续采集。
# 用法：在项目根目录执行 python3 studio/recordctl.py status；start --label episode [--topics 话题...]、stop、episodes、directory --path /绝对路径 [--create]。
# 详细说明：采集与预览使用说明.md

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
