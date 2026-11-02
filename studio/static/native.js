/*
使用说明
用途：原生采集目录读取、原始指令/NULL查看和显式回放界面。
用法：由 app.py 提供的工作台页面加载，浏览器访问 http://机器人IP:18769/；不作为独立脚本启动。
详细说明：../数据管理与转换调度使用说明.md
*/
/* 原生记录列表、原始指令/空值查看与显式回放；不修改数据。 */
'use strict';
let nativeRoot='',nativeRows=[],nativeBusy=false,nativeRequest=null;
function nativeButtons(){const chosen=!!$('#nativeSource').value,running=!!workflowState.replay?.running;$('#nativeInspect').disabled=!chosen||nativeBusy;$('#nativeExecute').disabled=!chosen||nativeBusy||running;$('#nativeStop').disabled=!running;}
async function refreshNativeFiles(path){
 if(nativeRequest)return nativeRequest;
 nativeBusy=true;nativeButtons();
 nativeRequest=(async()=>{const root=path||nativeRoot||'/data/record',previous=$('#nativeSource').value;
  const data=await api('/api/library/tree?'+new URLSearchParams({path:root,input_format:'factory'}));
  nativeRoot=data.root;nativeRows=data.tree?folderEpisodes(data.tree):[];$('#nativeRoot').value=nativeRoot;
  $('#nativeSource').innerHTML='<option value="">选择一条原生记录</option>'+nativeRows.map(row=>'<option value="'+esc(row.id)+'">'+esc(row.id===nativeRoot?row.file:row.id.slice(nativeRoot.length+1))+'</option>').join('');
  $('#nativeSource').value=nativeRows.some(r=>r.id===previous)?previous:nativeRows.length===1?nativeRows[0].id:'';
  $('#nativeSummary').textContent='共 '+nativeRows.length+' 条原生记录。读取不会连接 SDK 或发送动作。';
 })();try{await nativeRequest;}finally{nativeRequest=null;nativeBusy=false;nativeButtons();}
}
function renderNativeReplay(job){const p=job?.progress||{},r=job?.result||{};$('#nativeRate').textContent=(p.phase||job?.phase||'尚未开始')+' · 目标 100 Hz · 实际 '+(p.send_hz??r.send_hz??'—')+' Hz';$('#nativeJob').textContent=job?.sources?.[0]||r.dataset||'';$('#nativeLog').textContent=workflowLog(job||{})+(job?.result?'\n'+JSON.stringify(job.result,null,2):'');nativeButtons();}
action('#chooseNativeRoot',async()=>{directoryTarget='native-replay';$('#createDir').hidden=true;$('#directoryTitle').textContent='选择原生回放记录目录';$('#directoryDescription').textContent='读取原生 pbdat 中的24维原始控制指令和 PTP 时间。';await browse(nativeRoot||'/data/record');$('#directoryDialog').showModal();});
action('#nativeRefresh',()=>refreshNativeFiles());
action('#nativeInspect',async()=>{const source=$('#nativeSource').value;if(!source)throw Error('请选择原生记录');nativeBusy=true;nativeButtons();try{const data=await api('/api/native/inspect',{source});$('#nativeSummary').textContent=data.frames+' 帧 · '+fmt(data.duration_s,2)+' 秒 · 原始指令 '+fmt(data.fps,2)+' Hz · '+data.names.length+' 个关节 / 夹爪';$('#nativeValues').textContent=data.names.map((name,i)=>name+'  原始指令 '+(data.first_positions[i]===null?'空值':fmt(data.first_positions[i],6))+(data.first_sdk_command?' / SDK 命令 '+(data.first_sdk_command[i]===null?'不发送新目标':fmt(data.first_sdk_command[i],6)):'')).join('\n');}finally{nativeBusy=false;nativeButtons();}});
action('#nativeExecute',async()=>{const source=$('#nativeSource').value;if(!source)throw Error('请选择原生记录');await api('/api/native/replay/start',{source});await refreshWorkflow();});
action('#nativeStop',async()=>{await api('/api/native/replay/stop',{});await refreshWorkflow();});
$('#nativeSource').addEventListener('change',()=>{$('#nativeValues').textContent='';nativeButtons();});
nativeButtons();
