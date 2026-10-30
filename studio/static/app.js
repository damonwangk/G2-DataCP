'use strict';
const $=s=>document.querySelector(s), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(x,n=3)=>Number.isFinite(Number(x))&&x!==null&&x!==undefined?Number(x).toFixed(n):'—';
const clock=t=>`${Math.floor((t||0)/60).toString().padStart(2,'0')}:${Math.floor((t||0)%60).toString().padStart(2,'0')}`;
const cameras=[['head_color','头部 RGB','CAM 01'],['hand_left_color','左腕 RGB','CAM 02'],['hand_right_color','右腕 RGB','CAM 03']];
let source='live',group='arms',selected=null,current=null,dragging=false,sourceVersion=0,lastFileRefresh=0,replayPending=false,lastReplaySignature='',lastPoseSignature='';
function notice(text){$('#notice').textContent=text;$('#notice').hidden=!text;}
async function api(path,data){const r=await fetch(path,{method:data===undefined?'GET':'POST',headers:data===undefined?{}:{'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});let out=await r.json();if(!r.ok)throw Error(typeof out.detail==='string'?out.detail:JSON.stringify(out));return out;}
function action(selector,fn){$(selector).addEventListener('click',async()=>{try{notice('');await fn();}catch(e){notice(e.message);}});}
function setSource(s){source=s;sourceVersion++;$('#liveTab').classList.toggle('active',s==='live');$('#playbackTab').classList.toggle('active',s==='playback');$('#modeLabel').textContent=s==='live'?'LIVE':'PLAYBACK';document.querySelectorAll('.camera-placeholder').forEach(x=>{x.hidden=false;x.textContent=s==='live'?'等待实时画面':'等待文件画面';});}
$('#cameras').innerHTML=cameras.map(([key,label,code])=>`<article class="camera"><div class="camera-top"><strong>${label}<span class="camera-code">${code}</span></strong><span id="hz-${key}">— Hz</span></div><div class="camera-view"><span class="camera-placeholder" id="ph-${key}">等待实时画面</span><canvas id="cam-${key}" aria-label="${label}"></canvas></div><div class="camera-info"><span id="shape-${key}">原始 JPEG</span><span id="seq-${key}">SEQ —</span></div></article>`).join('');
async function cameraLoop(name){for(;;){const version=sourceVersion;try{const r=await fetch(`/api/camera/${name}.jpg?source=${source}&t=${Date.now()}`,{signal:AbortSignal.timeout(4000)});if(r.ok){const bitmap=await createImageBitmap(await r.blob());if(version===sourceVersion){const c=$(`#cam-${name}`);if(c.width!==bitmap.width||c.height!==bitmap.height){c.width=bitmap.width;c.height=bitmap.height;}c.getContext('2d').drawImage(bitmap,0,0);$(`#ph-${name}`).hidden=true;}bitmap.close();}}catch(e){}await new Promise(r=>setTimeout(r,130));}}
cameras.forEach(([name])=>cameraLoop(name));
function renderJoints(sample){
  const d=sample?.data||{},names=d.name||[];$('#jointCount').textContent=names.length;
  const cols=group==='arms'?[['左臂','_arm_l_','var(--left)'],['右臂','_arm_r_','var(--right)']]:[['躯干','_body_','#859365'],['头部','_head_','#859365']];
  $('#joints').innerHTML=cols.map(([label,match,color])=>`<div class="joint-column"><div class="thead"><span>${label}</span><span>位置</span><span>速度</span><span>力矩</span></div>${names.map((name,i)=>{
    if(!name.includes(match))return '';
    const lim=source==='live'?current?.limits?.joints?.[name]:null,q=d.motor_position?.[i];
    const pct=lim?Math.max(0,Math.min(100,(q-lim.lower)/(lim.upper-lim.lower)*100)):0;
    const tip=lim?`范围 ${fmt(lim.lower)} ~ ${fmt(lim.upper)} rad；最近边界距离 ${fmt(lim.distance)} rad`:name;
    const badge=lim&&lim.status!=='normal'?` · ${lim.status==='near'?'接近限位':lim.status==='over'?'超限':'到限位'}`:'';
    return `<div class="joint-row ${lim?.status||''}" title="${esc(tip)}"><span class="joint-name" style="--pct:${pct}%;--bar:${color}">${esc(name.split('_')[0])}<small> · J${name.match(/joint(\d+)/)?.[1]||''}${badge}</small></span><span class="joint-value">${fmt(q)}</span><span class="joint-value">${fmt(d.motor_velocity?.[i],2)}</span><span class="joint-value">${fmt(d.effort?.[i],2)}</span></div>`;
  }).join('')||'<p class="muted">等待关节数据</p>'}</div>`).join('');
  const errors=(d.error_code||[]).filter(x=>x!==0);$('#errors').textContent=errors.length?`${errors.length} 个关节报告错误`:names.length?'采样错误码 0':'等待反馈';if(sample?.age_s>2)$('#errors').textContent='数据已超过 2 秒未更新';
}
function renderLimits(limits){if(typeof renderSDKLimits==='function')renderSDKLimits(limits);}
function renderGrippers(samples){$('#grippers').innerHTML=['left','right'].map((side,i)=>{const s=samples[`/hal/${side}_ee_data`],d=s?.data||{},motor=d.end_state?.[0]||{};return `<div class="gripper"><svg viewBox="0 0 50 50" fill="none" stroke="${i?'#d88853':'#29a889'}" stroke-width="4"><path d="M10 5h30v10H10zM13 15v24h9M37 15v24h-9"/></svg><div><strong>${i?'右':'左'}夹爪 · TYPE ${esc(d.type??'—')}</strong><b>${fmt(motor.position,5)}</b><small>${s?.age_s>2?'反馈已过期':'原生位置'} · 电流 ${fmt(motor.current,0)}</small></div></div>`;}).join('');}
let yaw=.6,pitch=.12,zoom=1,poseDown=null;
const pose=$('#pose');pose.addEventListener('pointerdown',e=>{poseDown=[e.clientX,e.clientY,yaw,pitch];pose.setPointerCapture(e.pointerId);});pose.addEventListener('pointermove',e=>{if(poseDown){yaw=poseDown[2]+(e.clientX-poseDown[0])*.008;pitch=Math.max(-.6,Math.min(.6,poseDown[3]+(e.clientY-poseDown[1])*.004));}});pose.addEventListener('pointerup',()=>poseDown=null);pose.addEventListener('wheel',e=>{e.preventDefault();zoom=Math.max(.5,Math.min(2,zoom-e.deltaY*.001));},{passive:false});
function drawPose(){const rect=pose.getBoundingClientRect(),dpr=devicePixelRatio||1,w=rect.width,h=rect.height;if(pose.width!==Math.round(w*dpr)||pose.height!==Math.round(h*dpr)){pose.width=Math.round(w*dpr);pose.height=Math.round(h*dpr);}const c=pose.getContext('2d');c.setTransform(dpr,0,0,dpr,0,0);c.clearRect(0,0,w,h);const scale=h*.48*zoom,cy=Math.cos(yaw),sy=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch);const project=p=>{let x=p[0]*cy-p[1]*sy,y=p[0]*sy+p[1]*cy,z=p[2];return [w*.5+x*scale,h*.83-(z*cp-y*sp)*scale];};
function line(a,b,color,width=1){const p=project(a),q=project(b);c.strokeStyle=color;c.lineWidth=width;c.lineCap='round';c.beginPath();c.moveTo(...p);c.lineTo(...q);c.stroke();}
for(let i=-1;i<=1.01;i+=.2){line([i,-1,0],[i,1,0],'#d8dfd1');line([-1,i,0],[1,i,0],'#d8dfd1');}line([0,0,0],[.22,0,0],'#bf8b75',2);line([0,0,0],[0,.22,0],'#8aa184',2);line([0,0,0],[0,0,.22],'#819ba8',2);
const transforms=current?.samples?.['/tf']?.data?.transforms||[],points={'base_link':[0,0,0]};for(const t of transforms){if(t.header?.frame_id!=='base_link')continue;const p=t.transform?.translation;if(p)points[t.child_frame_id]=[p.x??0,p.y??0,p.z??0];}
function chain(names,color,width){for(let i=1;i<names.length;i++){if(points[names[i-1]]&&points[names[i]])line(points[names[i-1]],points[names[i]],color,width);}for(const name of names){if(!points[name])continue;const p=project(points[name]);c.fillStyle='#fafbf6';c.strokeStyle=color;c.lineWidth=2;c.beginPath();c.arc(...p,3.2,0,Math.PI*2);c.fill();c.stroke();}}
chain(['base_link',...Array.from({length:5},(_,i)=>`body_link${i+1}`),'arm_base_link'],'#8b9987',9);chain(['arm_base_link','head_base_link','head_link1','head_link2','head_link3'],'#8b9987',6);for(const side of ['l','r'])chain(['arm_base_link',...Array.from({length:7},(_,i)=>`arm_${side}_link${i+1}`),`arm_${side}_end_link`],side==='l'?'#29a889':'#d88853',7);
if(!transforms.length){c.fillStyle='#8a9386';c.font='12px sans-serif';c.textAlign='center';c.fillText('等待机器人姿态',w/2,h/2);}requestAnimationFrame(drawPose);}
requestAnimationFrame(drawPose);

function renderControlAvailability(s){
 const rr=s.robot_replay,rec=s.recording,busy=rr.running||rr.preparing,chosen=!!$('#poseSelect').value;
 $('#pauseRobot').disabled=!rr.running||rr.kind==='posture';
 $('#pauseRobot').textContent=rr.paused?'继续回放':'暂停回放';
 $('#execute').disabled=!selected||busy||rec.active||replayPending;
 $('#plan').disabled=!selected||busy||replayPending;
 $('#record').disabled=rec.active||busy;
 $('#nextRecord').disabled=!rec.active||!!busy;
 $('#restorePose').disabled=!chosen||busy||rec.active;
 $('#checkPose').disabled=!chosen;
 $('#chooseDir').disabled=rec.active||busy;$('#chooseRobotDir').disabled=rec.active||busy;
 $('#stopRobot').disabled=!rr.running;
 let reason='';
 if(busy){
  const task=rr.kind==='posture'?'姿态恢复':rr.paused?'回放已暂停（仍保持当前目标）':rr.preparing?'回放正在准备':'回放正在执行';
  reason=task+(rr.file?' · '+rr.file:'')+'。执行任务尚未结束，暂不能恢复另一姿态或开始新采集；请先结束当前执行。';
 }else if(rec.active)reason='正在采集数据，请先结束并保存，再恢复姿态。';
 else if(!chosen)reason='请选择已保存的初始姿态。';
 $('#restorePoseHint').textContent=reason;$('#restorePoseHint').hidden=!reason;
 $('#restorePose').title=reason;$('#record').title=busy?reason:rec.active?'正在采集数据':'';
 $('#activeMotionLink').hidden=!busy;
}

function render(s){current=s;if(typeof renderRobotStorage==='function')renderRobotStorage(s);if(typeof renderDataRates==='function')renderDataRates(s);if(typeof renderInspector==='function')renderInspector(s);$('#disk').textContent=`${fmt(s.disk_free_gb,1)} GB`;$('#clock').textContent=new Date().toLocaleTimeString('zh-CN',{hour12:false});$('#dataDir').textContent=s.data_dir;$('#saveDir').value=s.data_dir;renderLimits(s.limits);
const online=s.streams.filter(x=>x.online),required=s.streams.filter(x=>x.required),okay=required.every(x=>x.online);$('#connDot').style.background=okay?'#37845d':'#d88853';$('#connection').textContent=source==='playback'?'文件回放 · 机器人':okay?'机器人 · 数据已连接':'部分数据未就绪';$('#streamSummary').textContent=`${online.length} / ${s.streams.length} 通道有数据`;
const rec=s.recording;$('#record').disabled=rec.active;$('#stopRecord').disabled=!rec.active||s.robot_replay.running;$('#recordStatus').textContent=rec.active?`${s.robot_replay.running?'机器人本机记录执行反馈':'机器人本机采集原始数据'} · ${(rec.bytes/1048576).toFixed(1)} MB`:rec.error?`录制错误：${rec.error}`:'机器人本机采集 · 原始 MCAP';$('#recordStatus').classList.toggle('recording',rec.active);$('#elapsed').textContent=clock(rec.elapsed_s);
for(const [name] of cameras){const stream=s.streams.find(x=>x.topic==='/camera/'+name),sample=s.samples['/camera/'+name],meta=sample?.meta;$(`#hz-${name}`).textContent=source==='live'?`${fmt(stream?.hz,1)} Hz`:'FILE';$(`#hz-${name}`).classList.toggle('stale',source==='live'&&!stream?.online);if(meta){$(`#shape-${name}`).textContent=`${meta.width} × ${meta.height} · JPEG`;$(`#seq-${name}`).textContent=`SEQ ${meta.sequence}`;}if(source==='live'&&!stream?.online){$(`#ph-${name}`).hidden=false;$(`#ph-${name}`).textContent='等待数据';}}
$('#jointHz').textContent=source==='live'?`${fmt(s.streams.find(x=>x.topic==='/hal/joint_state')?.hz,1)} Hz`:'FILE';renderJoints(s.samples['/hal/joint_state']);renderGrippers(s.samples);
const pb=s.playback;if(!dragging){$('#seek').max=pb.duration_s||1;$('#seek').value=pb.position_s||0;}$('#playTime').textContent=`${clock(pb.position_s)} / ${clock(pb.duration_s)}`;$('#play').textContent=pb.playing?'正在播放':'▶ 预览画面';
$('#streams').innerHTML=s.streams.map(x=>`<div class="stream-row"><span>${esc(x.topic)}</span><span>${fmt(x.hz,1)} Hz</span><span>${x.online?'在线':x.count?'已过期':'尚无消息'}</span><span>序号间断 ${x.gaps}</span><span>${x.required?'必需':'可选'}${x.error?' · '+esc(x.error):''}</span></div>`).join('');
const rr=s.robot_replay;renderControlAvailability(s);
const signature=JSON.stringify(rr);
if(rr.kind==='replay'&&(rr.running||rr.preparing||rr.phase)&&(rr.running||rr.preparing||!selected||!rr.file||rr.file===selected)&&(signature!==lastReplaySignature||rr.running||rr.preparing)&&!replayPending){$('#replayInfo').textContent=(rr.file&&rr.file!==selected?'当前执行：'+rr.file+'\n':'')+(rr.phase||'')+' · '+clock(rr.position_s)+' / '+clock(rr.duration_s)+(rr.error?'\n'+rr.error:'')+(rr.report?'\n'+JSON.stringify(rr.report,null,2):'');}
lastReplaySignature=signature;
if(rr.kind==='posture'&&(rr.running||rr.phase)&&signature!==lastPoseSignature){$('#poseInfo').hidden=false;$('#poseInfo').textContent=rr.phase+(rr.error?'\n'+rr.error:'')+(rr.report?'\n'+(rr.report.tracking_passed?'反馈到位':'尚未确认到位'):'');}
lastPoseSignature=signature;if(rec.error)notice(rec.error);if(s.startup_error)notice(s.startup_error);}
async function poll(){try{render(await api(`/api/state?source=${source}`));}catch(e){$('#connection').textContent='连接中断';$('#connDot').style.background='#d88853';}setTimeout(poll,200);}poll();
async function refreshFiles(){const category=$('#fileCategory').value||'collection';const {files,counts={}}=await api('/api/files?category='+category);$('#fileCounts').textContent='正式 '+(counts.collection??0)+' · 反馈 '+(counts.feedback??0)+' · 测试 '+(counts.test??0);$('#files').innerHTML=files.length?files.map(f=>`<div class="file-item ${f.file===selected?'selected':''}" role="button" tabindex="0" data-file="${esc(f.file)}"><strong>${esc(f.file)}</strong><small>${clock(f.duration_s)} · ${(f.bytes/1048576).toFixed(1)} MB · ${{collection:'正式采集',feedback:'执行反馈',test:'历史测试'}[f.category]||'MCAP'}</small></div>`).join(''):'<p class="muted">当前分类暂无记录；开始采集后会出现在「正式采集」。</p>';document.querySelectorAll('.file-item').forEach(el=>{const load=async()=>{try{await api('/api/playback/load',{file:el.dataset.file});selected=el.dataset.file;if(typeof loadFileSelections==='function')await loadFileSelections(selected);$('#replayInfo').textContent='尚未开始 · '+selected;lastReplaySignature=JSON.stringify(current?.robot_replay||{});$('#selectedFile').textContent=selected;['play','pause','validate','plan','execute'].forEach(id=>$('#'+id).disabled=false);setSource('playback');await refreshFiles();}catch(e){notice(e.message);}};el.onclick=load;el.onkeydown=e=>{if(e.key==='Enter')load();};});}
action('#record',async()=>{setSource('live');await api('/api/record/start',{label:$('#task').value,pose_id:$('#poseSelect').value,...(typeof selectedRecordingTopics==='function'?{topics:selectedRecordingTopics()}:{})});});action('#nextRecord',async()=>{const r=await api('/api/record/next',{label:$('#task').value,pose_id:$('#poseSelect').value,...(typeof selectedRecordingTopics==='function'?{topics:selectedRecordingTopics()}:{})});notice('已保存 '+(r.saved?.file||'上一条')+'，正在采集 '+(r.recording?.file||'下一条')+'。');await refreshFiles();if(typeof refreshRobotFiles==='function')await refreshRobotFiles();});action('#stopRecord',async()=>{const r=await api('/api/record/stop',{});notice('机器人已保存：'+(r.robot_episode_dir||r.episode_dir||r.file)+'；可在数据导出面板导出到当前电脑。');await refreshFiles();if(typeof refreshRobotFiles==='function')await refreshRobotFiles();});action('#liveTab',()=>setSource('live'));action('#playbackTab',()=>setSource('playback'));action('#refresh',refreshFiles);$('#fileCategory').addEventListener('change',()=>refreshFiles().catch(e=>notice(e.message)));action('#play',async()=>{setSource('playback');await api('/api/playback/play',{speed:Number($('#speed').value)});$('#cameras').scrollIntoView?.({behavior:'smooth',block:'start'});});action('#pause',()=>api('/api/playback/pause',{}));
$('#seek').addEventListener('pointerdown',()=>dragging=true);$('#seek').addEventListener('change',async()=>{try{await api('/api/playback/seek',{seconds:Number($('#seek').value)});}catch(e){notice(e.message);}dragging=false;});
document.querySelectorAll('[data-group]').forEach(b=>b.onclick=()=>{group=b.dataset.group;document.querySelectorAll('[data-group]').forEach(x=>x.classList.toggle('active',x===b));if(current)renderJoints(current.samples['/hal/joint_state']);});
async function resultAction(path,message){$('#result').hidden=false;$('#result').textContent=message;const result=await api(path,{});$('#result').textContent=JSON.stringify(result,null,2);return result;}
action('#validate',()=>resultAction(`/api/files/${encodeURIComponent(selected)}/validate`,'正在检查 MCAP 索引、CRC、图像和通道…'));
action('#plan',async()=>{const file=selected;replayPending=true;$('#plan').disabled=true;$('#replayInfo').textContent='正在读取原始 MCAP 并检查起始姿态…';
try{const p=await api('/api/replay/plan',{file,speed:Number($('#robotSpeed').value),...(typeof selectedMotionNames==='function'?{controlled_names:selectedMotionNames()}:{})});if(selected===file)$('#replayInfo').textContent='准备检查完成 · 尚未执行机器人动作\n'+JSON.stringify(p,null,2);}
catch(e){if(selected===file)$('#replayInfo').textContent='回放准备失败：'+e.message;throw e;}
finally{replayPending=false;$('#plan').disabled=!selected;}});
action('#execute',()=>{pendingMotion='replay';const names=typeof selectedMotionNames==='function'?selectedMotionNames():[];$('#motionSummary').textContent='仅执行勾选的 '+names.length+' 个关节 / 夹爪，先返回这些关节的起始位置，再按该记录时间线回放。未选关节不发送位置目标。';$('#motionFile').textContent=selected;$('#confirmMotion').textContent='开始全过程回放';$('#motionDialog').showModal();});action('#cancelMotion',()=>$('#motionDialog').close());action('#confirmMotion',async()=>{$('#motionDialog').close();setSource('live');const posture=pendingMotion==='posture',panel=$(posture?'#poseInfo':'#replayInfo');panel.hidden=false;panel.textContent=posture?'正在准备恢复姿态…':'正在读取原始 MCAP，准备全过程回放…';replayPending=!posture;
try{if(posture)await api('/api/poses/'+encodeURIComponent($('#poseSelect').value)+'/restore',{confirm_motion:true});else await api('/api/replay/start',{file:selected,speed:Number($('#robotSpeed').value),confirm_motion:true,...(typeof selectedMotionNames==='function'?{controlled_names:selectedMotionNames()}:{})});}
catch(e){panel.textContent=(posture?'姿态恢复准备失败：':'回放准备失败：')+e.message;throw e;}
finally{replayPending=false;lastReplaySignature='';}});
action('#stopRobot',()=>api('/api/replay/stop',{}));action('#pauseRobot',()=>api('/api/replay/pause',{}));
refreshFiles().catch(e=>notice(e.message));setInterval(()=>{if(!current?.recording.active)refreshFiles().catch(()=>{});},10000);

let pendingMotion='replay',folderState=null,poseCatalog=[],directoryTarget='robot';
async function refreshPoses(choose){
  const selectedPose=choose||$('#poseSelect').value,{poses}=await api('/api/poses');poseCatalog=poses;
  $('#poseSelect').innerHTML='<option value="">选择已保存的初始姿态</option>'+poses.map(p=>`<option value="${esc(p.id)}">${esc(p.label)} · ${esc(p.saved_at.slice(5,16).replace('T',' '))}</option>`).join('');
  if(poses.some(p=>p.id===selectedPose))$('#poseSelect').value=selectedPose;
  if(current)renderControlAvailability(current);
}
action('#savePose',async()=>{const p=await api('/api/poses',{label:$('#poseName').value||'初始姿态'});await refreshPoses(p.id);notice(`姿态已保存为 MCAP：${p.label}。仅保存 22 个本体关节和双夹爪。`);});
action('#checkPose',async()=>{const p=await api(`/api/poses/${encodeURIComponent($('#poseSelect').value)}/plan`,{});$('#poseInfo').hidden=false;$('#poseInfo').textContent=`${p.label}\n当前与保存姿态最大关节差：${fmt(p.max_joint_delta_rad)} rad；夹爪差：${p.gripper_delta_native.map(x=>fmt(x,5)).join(' / ')}\n恢复范围：躯干、头部、双臂、双夹爪。尚未执行恢复。`;});
action('#restorePose',()=>{pendingMotion='posture';const p=poseCatalog.find(p=>p.id===$('#poseSelect').value);$('#motionSummary').textContent='将机器人恢复到已保存的躯干、头部、双臂和双夹爪姿态，不移动底盘。本体关节先恢复，再恢复双夹爪；执行结果将在初始姿态区域显示。';$('#motionFile').textContent=p?.label||'';$('#confirmMotion').textContent='确认恢复姿态';$('#motionDialog').showModal();});
async function browse(path){folderState=await api(((directoryTarget==='library'||directoryTarget==='conversion')?'/api/library/directories':directoryTarget==='robot'?'/api/robot-storage/directories':'/api/directories')+(path?'?path='+encodeURIComponent(path):''));$('#folderPath').value=folderState.path;$('#folderMeta').textContent=`可用空间 ${fmt(folderState.free_gb,1)} GB · ${folderState.folders.length} 个文件夹`;$('#folderList').innerHTML=folderState.folders.length?folderState.folders.map(f=>`<button data-folder="${esc(f.path)}">▸ ${esc(f.name)}</button>`).join(''):'<p class="muted">此目录下没有子文件夹</p>';$('#folderList').querySelectorAll('button').forEach(b=>b.onclick=()=>browse(b.dataset.folder).catch(e=>notice(e.message)));}
action('#chooseDir',async()=>{directoryTarget='robot';$('#createDir').hidden=false;$('#directoryTitle').textContent='选择机器人保存目录';$('#directoryDescription').textContent='采集、文件查看和回放均使用此机器人本机目录。';await browse(current?.data_dir);$('#directoryDialog').showModal();});
action('#browsePath',()=>browse($('#folderPath').value));action('#parentFolder',()=>browse(folderState?.parent));action('#cancelDir',()=>$('#directoryDialog').close());
async function useDirectory(create){const path=create?$('#folderPath').value:folderState?.path;if(!path)throw Error('请先选择目录');if(directoryTarget==='conversion'){await refreshConversionFiles(path);$('#directoryDialog').close();return;}if(directoryTarget==='library'){await api('/api/library/root',{path});$('#directoryDialog').close();await refreshRobotFiles();notice('导出 / 删除范围已更新：'+path);return;}const r=await api(directoryTarget==='robot'?'/api/robot-storage/directory':'/api/directory',{path,create});selected=null;$('#replayInfo').textContent='尚未开始';$('#fileCategory').value='collection';$('#selectedFile').textContent='选择一条 episode';['play','pause','validate','plan','execute'].forEach(id=>$('#'+id).disabled=true);setSource('live');$('#saveDir').value=r.data_dir;$('#directoryDialog').close();await refreshFiles();if(typeof refreshRobotFiles==='function')await refreshRobotFiles();notice(`后续 episode 将保存到 ${r.data_dir}`);}
action('#useDir',()=>useDirectory(false));action('#createDir',()=>useDirectory(true));refreshPoses().catch(e=>notice(e.message));

$('#poseSelect').addEventListener('change',()=>{if(current)renderControlAvailability(current);});
