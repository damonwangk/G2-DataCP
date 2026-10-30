
'use strict';
let dataCatalog=null, fileContents=null, selectionFile=null;
function checkedValues(selector){return Array.from(document.querySelectorAll(selector)).filter(n=>n.checked).map(n=>n.value);}
function selectedRecordingTopics(){
 if(!dataCatalog)throw Error('采集通道列表正在加载');
 return checkedValues('#captureSelection input[data-topic]');
}
function selectedMotionNames(){
 if(selectionFile!==selected||!fileContents)throw Error('请先选择记录，等待动作列表加载');
 const names=checkedValues('#replayTargets input[data-joint]');
 if(!names.length)throw Error('请选择要执行的关节或夹爪；只有图像的记录可使用文件预览');
 return names;
}
function capturePreferences(){
 try{const value=JSON.parse(localStorage.getItem('r2.capture_topics'));return Array.isArray(value)?value:null;}catch{return null;}
}
function saveCapturePreferences(){localStorage.setItem('r2.capture_topics',JSON.stringify(selectedRecordingTopics()));}
async function loadDataCatalog(){
 dataCatalog=await api('/api/data/catalog');
 const remembered=capturePreferences();
 $('#captureSelection').innerHTML=dataCatalog.topics.map(t=>'<tr><td><label><input type="checkbox" data-topic value="'+esc(t.topic)+'" '+(remembered===null||remembered.includes(t.topic)?'checked':'')+'> '+esc(t.label)+'</label><small>'+esc(t.topic)+'</small></td><td data-receive-rate="'+esc(t.topic)+'">—</td><td data-record-rate="'+esc(t.topic)+'">未录制</td></tr>').join('');
 document.querySelectorAll('#captureSelection input').forEach(n=>n.addEventListener('change',()=>{saveCapturePreferences();if(current)renderDataRates(current);}));
 if(current)renderDataRates(current);
}
function renderDataRates(s){
 const rec=s.recording,rr=s.robot_replay,streams=new Map((rec.storage_host==='robot'?(rec.streams||[]):s.streams).map(r=>[r.topic,r]));
 document.querySelectorAll('[data-receive-rate]').forEach(n=>{const r=streams.get(n.dataset.receiveRate);n.textContent=rec.storage_host==='robot'&&!rec.active?'未采集':r?.online?fmt(r.hz,1)+' Hz':'暂无消息';});
 document.querySelectorAll('[data-record-rate]').forEach(n=>{
  const t=n.dataset.recordRate;
  n.textContent=!rec.active?'未录制':rec.selected_topics?.includes(t)?fmt(rec.hz?.[t],1)+' Hz':'未保存';
 });
 document.querySelectorAll('#captureSelection input').forEach(n=>n.disabled=rec.active||rr.running||rr.preparing);
 $('#captureAll').disabled=$('#captureNone').disabled=rec.active||rr.running||rr.preparing;
 $('#captureCount').textContent=dataCatalog?checkedValues('#captureSelection input[data-topic]').length+' / '+dataCatalog.topics.length+' 类已选':'正在加载';
 const report=rr.report||{},send=rr.running?rr.send_hz:report.send_average_hz,accepted=rr.running?rr.accepted_hz:report.accepted_average_hz;
 $('#replayRate').textContent=rr.kind==='posture'&&rr.running?'姿态恢复使用 SDK 点位指令，不是连续轨迹发送。':
  '动作发送目标 100 Hz（每 10 ms） · '+(rr.running?'当前实测 ':report.sample_count?'上次平均 ':'实测 ')+
  (Number.isFinite(send)?fmt(send,1)+' Hz':'尚无数据')+' · SDK 成功返回 '+(Number.isFinite(accepted)?fmt(accepted,1)+' Hz':'尚无数据')+
  (rr.paused?' · 已暂停时间推进，仍发送保持目标':'');
}
async function loadFileSelections(file){
 selectionFile=null;fileContents=null;$('#replayTargets').textContent='正在读取该记录包含的动作数据…';
 const contents=await api('/api/files/'+encodeURIComponent(file)+'/contents');
 if(selected!==file)return;
 fileContents=contents;selectionFile=file;
 $('#replayTargets').innerHTML=contents.motion_groups.map(g=>'<fieldset class="motion-group"><legend><label><input type="checkbox" data-motion-group="'+esc(g.id)+'" '+(g.names.length?'checked':'disabled')+'> '+esc(g.label)+(g.names.length?'':'（无数据）')+'</label></legend>'+g.names.map(n=>'<label class="joint-choice"><input type="checkbox" data-joint data-group="'+esc(g.id)+'" value="'+esc(n)+'" checked> '+esc(n)+'</label>').join('')+'</fieldset>').join('');
 if(!contents.motion_names.length)$('#replayTargets').textContent='此记录没有关节或夹爪轨迹，可以预览已保存的图像及其他数据。';
 document.querySelectorAll('[data-motion-group]').forEach(n=>n.addEventListener('change',()=>{document.querySelectorAll('#replayTargets input[data-joint]').forEach(j=>{if(j.dataset.group===n.dataset.motionGroup)j.checked=n.checked;});}));
 document.querySelectorAll('#replayTargets input[data-joint]').forEach(j=>j.addEventListener('change',()=>{
  const children=Array.from(document.querySelectorAll('#replayTargets input[data-joint]')).filter(n=>n.dataset.group===j.dataset.group);
  const group=Array.from(document.querySelectorAll('[data-motion-group]')).find(n=>n.dataset.motionGroup===j.dataset.group);
  group.checked=children.every(n=>n.checked);group.indeterminate=children.some(n=>n.checked)&&!group.checked;
 }));
 const labels=new Map((dataCatalog?.topics||[]).map(t=>[t.topic,t.label]));
 $('#previewTopics').innerHTML=contents.topics.map(t=>'<label class="preview-choice"><input type="checkbox" data-preview-topic value="'+esc(t)+'" checked> '+esc(labels.get(t)||t)+' <small>文件平均 '+fmt(contents.recorded_average_hz[t],1)+' Hz</small></label>').join('');
 $('#fileSelectionNote').textContent='已保存 '+contents.topics.length+' 类通道，可执行 '+contents.motion_names.length+' 个关节 / 夹爪。只下发勾选的名称与原始位置；未选部分不发送位置目标。';
}
action('#captureAll',()=>{document.querySelectorAll('#captureSelection input').forEach(n=>n.checked=true);saveCapturePreferences();if(current)renderDataRates(current);});
action('#captureNone',()=>{document.querySelectorAll('#captureSelection input').forEach(n=>n.checked=false);saveCapturePreferences();if(current)renderDataRates(current);});
action('#applyPreview',async()=>{
 if(selectionFile!==selected)throw Error('请先选择记录');
 const topics=checkedValues('#previewTopics input');
 if(!topics.length)throw Error('请选择要预览的数据');
 await api('/api/playback/load',{file:selected,topics});setSource('playback');notice('预览通道已更新；点击预览画面播放。');
});
loadDataCatalog().catch(e=>notice(e.message));
