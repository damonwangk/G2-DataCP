'use strict';
// Conversion and LeRobot playback have their own selection and job state.
let workflowState={conversion:{},replay:{}},lerobotDatasets=[],lerobotEpisodes=[],lerobotInspection=null,workflowLoading=false,lerobotBusy=false,conversionBusy=false;
let sharedLerobotRoot='',lerobotRootDirty=false,lerobotCatalogRoot='',lerobotCatalogRequest=null;
let lerobotExportBusy=false,lerobotExportAbort=null;
let conversionSelection=new Set(),conversionRoot='',conversionTree=null,conversionExpanded=new Map(),conversionRows=[],conversionFilesRequest=null,conversionRequestRoot='';
let conversionBrowserDataset='',conversionBrowserPath='',conversionBrowserParent=null;
let lastConversionCatalogResult='';
const lerobotDirectoryPickerAvailable=typeof window!=='undefined'&&typeof window.showDirectoryPicker==='function';
function workflowLog(job){
 const log=Array.isArray(job?.log)?job.log.join('\n'):job?.log||'';
 return [job?.error?'错误：'+job.error:'',log].filter(Boolean).join('\n');
}
function jobPhase(job,idle){return (job?.running&&job?.progress?.phase)||job?.phase||(job?.running?'运行中':job?.error?'未完成':job?.result?'已结束':idle);}
function conversionFolderEpisodes(group){return [...(group.episodes||[]),...(group.groups||[]).flatMap(conversionFolderEpisodes)];}
function rememberConversionExpansion(){document.querySelectorAll('[data-conversion-folder]').forEach(el=>conversionExpanded.set(el.dataset.conversionFolder,el.open));}
function selectConversionGroup(path,checked){
 function find(group){if(group.path===path)return group;for(const child of group.groups||[]){const match=find(child);if(match)return match;}}
 const group=conversionTree&&find(conversionTree);if(!group)return;
 for(const row of conversionFolderEpisodes(group))checked?conversionSelection.add(row.id):conversionSelection.delete(row.id);
 renderConversionFiles();
}
function renderConversionFiles(remember=true){
 if(remember)rememberConversionExpansion();
 const available=new Set(conversionRows.map(row=>row.id));conversionSelection=new Set([...conversionSelection].filter(path=>available.has(path)));
 const groups=new Map();
 function html(group,isRoot=false){
  const rows=conversionFolderEpisodes(group),chosen=rows.filter(row=>conversionSelection.has(row.id)).length;
  groups.set(group.path,{chosen,count:rows.length});
  const open=conversionExpanded.has(group.path)?conversionExpanded.get(group.path):isRoot;
  return '<details class="library-folder" data-conversion-folder="'+esc(group.path)+'" '+(open?'open':'')+'><summary><label class="library-folder-label"><input type="checkbox" data-conversion-group="'+esc(group.path)+'" '+(!rows.length?'disabled ':'')+(chosen&&chosen===rows.length?'checked':'')+'><strong>'+esc(group.name)+'</strong></label><span class="muted">'+rows.length+' 条</span></summary><div class="library-folder-content">'+
   (group.episodes||[]).map(row=>'<label class="robot-file-row"><input type="checkbox" data-conversion-file="'+esc(row.id)+'" '+(conversionSelection.has(row.id)?'checked':'')+'><div><strong>'+esc(row.file)+'</strong><small>'+({collection:'正式采集',feedback:'执行反馈',test:'测试'}[row.category]||'记录')+' · '+fmt((row.total_bytes??row.bytes??0)/1048576,1)+' MB</small></div></label>').join('')+
   (group.groups||[]).map(child=>html(child)).join('')+(!rows.length?'<p class="muted library-empty">此目录暂无已完成的 episode。</p>':'')+'</div></details>';
 }
 $('#conversionFiles').innerHTML=conversionTree?html(conversionTree,true):'<p class="muted library-empty">正在读取 MCAP 记录…</p>';
 document.querySelectorAll('[data-conversion-folder]').forEach(el=>el.addEventListener('toggle',()=>conversionExpanded.set(el.dataset.conversionFolder,el.open)));
 document.querySelectorAll('[data-conversion-group]').forEach(el=>{const group=groups.get(el.dataset.conversionGroup);el.indeterminate=!!group&&group.chosen>0&&group.chosen<group.count;el.addEventListener('click',event=>event.stopPropagation());el.addEventListener('change',()=>selectConversionGroup(el.dataset.conversionGroup,el.checked));});
 document.querySelectorAll('[data-conversion-file]').forEach(el=>el.addEventListener('change',()=>{el.checked?conversionSelection.add(el.dataset.conversionFile):conversionSelection.delete(el.dataset.conversionFile);renderConversionFiles();}));
 updateConversionSelection();
}
function captureInputRoot(){return typeof current!=='undefined'?(current?.recording?.data_dir||current?.data_dir||''):'';}
async function refreshConversionFiles(path){
 const requested=path||conversionRoot||captureInputRoot();
 if(conversionFilesRequest){const same=requested===conversionRequestRoot;await conversionFilesRequest;if(same)return;}
 conversionRequestRoot=requested;$('#conversionChooseRecords').disabled=true;$('#conversionFileStatus').textContent='正在读取目录…';
 conversionFilesRequest=(async()=>{
  const result=await api('/api/library/tree'+(requested?'?path='+encodeURIComponent(requested):''));
  const root=result.root||requested,changed=root!==conversionRoot;
  if(changed){conversionSelection.clear();conversionExpanded.clear();}
  conversionRoot=root;conversionTree=result.tree;conversionRows=conversionTree?conversionFolderEpisodes(conversionTree):[];
  $('#conversionInputRoot').value=root;$('#conversionSourceDir').value=root;renderConversionFiles(!changed);
  $('#conversionFileStatus').textContent='共 '+conversionRows.length+' 条已完成记录';
 })();
 try{await conversionFilesRequest;}catch(error){$('#conversionFileStatus').textContent='读取失败：'+error.message+'；可刷新重试。';throw error;}
 finally{conversionFilesRequest=null;$('#conversionChooseRecords').disabled=false;}
}
function updateConversionSelection(){
 const count=conversionSelection.size;
 const directory=$('#conversionMode').value==='directory';
 $('#conversionSelected').textContent='已选择 '+count+' 条 MCAP 记录';
 const selectedPaths=[...conversionSelection];
 $('#conversionSelectedList').textContent=selectedPaths.slice(0,3).map(path=>path.split('/').pop()).join(' · ')+(count>3?' · 另 '+(count-3)+' 条':'');
 $('#conversionDirectoryRow').hidden=!directory;$('#conversionSelectionRow').hidden=directory;
 $('#conversionStart').disabled=conversionBusy||!!workflowState.conversion?.running||(!directory&&!count);
 $('#conversionStop').disabled=!workflowState.conversion?.running;
}
function conversionPayload(){
 const directory=$('#conversionMode').value==='directory';
 const output_dir=$('#conversionOutput').value.trim(),dataset_name=$('#conversionRepo').value.trim(),task=$('#conversionTask').value.trim();
 if(!output_dir)throw Error('请输入 LeRobot 输出根目录');
 if(!dataset_name||dataset_name==='.'||dataset_name==='..'||/[\/\\]/.test(dataset_name))throw Error('请输入本次转换文件夹名称，不含路径分隔符');
 if(!task)throw Error('请输入本次数据的任务描述');
 const payload={output_dir,dataset_name,task};
 if(directory){payload.source_dir=$('#conversionSourceDir').value.trim();if(!payload.source_dir)throw Error('请输入需要批量转换的 MCAP 文件夹');}
 else{payload.episode_paths=[...conversionSelection];if(!payload.episode_paths.length)throw Error('请先在转换区勾选 MCAP episode');}
 return payload;
}
function updateDatasetPath(){const output=$('#conversionOutput').value.trim().replace(/\/$/,''),name=$('#conversionRepo').value.trim();$('#conversionDatasetPath').textContent=output&&name?output+'/'+name:output?output+'/〔本次转换文件夹〕':'先填写保存位置和文件夹名称';}
function applyWorkflowDefaults(defaults){
 const root=defaults?.lerobot_root||defaults?.conversion_output;if(!root)return;
 const changed=root!==sharedLerobotRoot;sharedLerobotRoot=root;$('#lerobotRoot').value=root;
 if(!lerobotRootDirty)$('#conversionOutput').value=root;
 if(changed){lerobotCatalogRoot='';$('#lerobotDataset').innerHTML='<option value="">正在读取数据集</option>';$('#lerobotDataset').value='';$('#lerobotEpisode').value='';$('#lerobotExportDataset').innerHTML='<option value="">正在读取数据集</option>';$('#lerobotExportDataset').value='';resetLerobotInspection();}
 updateLerobotLocations();updateDatasetPath();
}
function updateLerobotLocations(){
 const dataset=$('#lerobotDataset').value;
 $('#lerobotReplayRoot').textContent=dataset?'数据来源：'+dataset:'保存位置：'+(sharedLerobotRoot||'正在读取');
 $('#lerobotExportRoot').textContent='保存位置：'+(sharedLerobotRoot||'正在读取');
 $('#lerobotExportSelection').textContent=$('#lerobotExportDataset').value?'整套数据集：'+$('#lerobotExportDataset').value:'尚未选择数据集';
}
async function saveLerobotRoot(){
 const root=$('#conversionOutput').value.trim();if(!root.startsWith('/'))throw Error('请输入机器人上的绝对保存路径');
 $('#saveLerobotRoot').disabled=true;
 try{const state=await api('/api/workflow/settings',{lerobot_root:root});lerobotRootDirty=false;applyWorkflowDefaults(state.defaults||{lerobot_root:root});$('#lerobotRootStatus').textContent='位置已保存，转换、回放和导出共同使用。';await refreshLerobotDatasets();}
 finally{$('#saveLerobotRoot').disabled=false;}
}
function getExportFormat(){return $('#exportFormat').value==='lerobot'?'lerobot':'mcap';}
function updateReplayFormat(){const lr=$('#replayFormat').value==='lerobot';$('#mcapReplay').hidden=lr;$('#lerobotReplay').hidden=!lr;}
function updateExportFormat(){const lr=getExportFormat()==='lerobot';$('#mcapExportInputs').hidden=lr;$('#lerobotExportInputs').hidden=!lr;$('#exportProgress').textContent='导出后机器人中的文件仍保留。';updateUnifiedExportControls();}
function updateUnifiedExportControls(){
 const lr=getExportFormat()==='lerobot',busy=lerobotExportBusy||(typeof exportBusy!=='undefined'&&exportBusy);
 const chosen=lr?!!$('#lerobotExportDataset').value:typeof exportSelection!=='undefined'&&exportSelection.size>0;
 $('#exportSelected').disabled=busy||!chosen;$('#exportArchive').disabled=busy||!chosen;
 $('#chooseExportFolder').disabled=busy;$('#exportFormat').disabled=busy;
 $('#cancelExport').disabled=!(lerobotExportAbort||(typeof exportAbort!=='undefined'&&exportAbort));
 $('#exportSelected').textContent=lerobotDirectoryPickerAvailable?(lr?'导出完整数据集':'导出所选 MCAP'):(lr?'导出完整数据集 (.tar)':'导出所选 MCAP (.tar)');
 $('#exportMethodNote').textContent=lerobotDirectoryPickerAvailable?'选择当前电脑的保存位置，按原目录结构写入。':'通过 TAR 下载保留目录结构；保存位置与取消下载由浏览器管理。';
 updateLerobotLocations();
}
function runSelectedExport(){return getExportFormat()==='lerobot'?exportLerobotToDirectory():exportToDirectory();}
function runSelectedArchive(){return getExportFormat()==='lerobot'?downloadLerobotArchive():downloadArchive();}
function cancelSelectedExport(){if(lerobotExportAbort)lerobotExportAbort.abort();if(typeof exportAbort!=='undefined')exportAbort?.abort();}

function selectedLerobot(){
 const dataset=$('#lerobotDataset').value;
 const raw=$('#lerobotEpisode').value;
 if(!dataset||raw==='')throw Error('请先选择 LeRobot 数据集和 episode');
 const episode=Number(raw);if(!Number.isInteger(episode)||episode<0)throw Error('episode 编号无效');
 return {dataset,episode};
}
function updateLerobotButtons(){
 const chosen=!!$('#lerobotDataset').value&&$('#lerobotEpisode').value!=='';
 const running=!!workflowState.replay?.running;
 $('#lerobotInspect').disabled=!chosen||lerobotBusy;
 $('#lerobotExecute').disabled=!chosen||running||lerobotBusy;
 $('#lerobotStop').disabled=!running;
 updateLerobotExportControls();
}
function updateLerobotExportControls(){updateUnifiedExportControls();}
function selectedLerobotDataset(){const dataset=$('#lerobotExportDataset').value;if(!dataset)throw Error('请先选择需要导出的完整 LeRobot 数据集');return dataset;}
function lerobotExportPath(name,path){
 const parts=[name,...path.split('/')];
 if(parts.some(part=>!part||part==='.'||part==='..'||/[\\/]/.test(part)))throw Error('导出文件路径无效：'+path);
 return parts;
}
async function lerobotExportFileExists(parts){
 let folder=exportDirectory;
 try{for(const part of parts.slice(0,-1))folder=await folder.getDirectoryHandle(part);await folder.getFileHandle(parts[parts.length-1]);return true;}
 catch(e){if(e.name==='NotFoundError')return false;throw e;}
}
async function exportLerobotToDirectory(){
 const dataset=selectedLerobotDataset();
 if(!lerobotDirectoryPickerAvailable)return downloadLerobotArchive();
 if(!exportDirectory)await chooseExportDirectory();if(!exportDirectory)return;
 lerobotExportBusy=true;lerobotExportAbort=new AbortController();updateLerobotExportControls();
 const signal=lerobotExportAbort.signal;
 try{
  $('#exportProgress').textContent='正在读取完整数据集文件清单：'+dataset;
  const response=await fetch('/api/lerobot/export/manifest?dataset='+encodeURIComponent(dataset),{signal});
  const manifest=await response.json();if(!response.ok)throw Error(manifest.detail||'读取导出清单失败：HTTP '+response.status);
  const name=manifest.name||dataset.split('/').filter(Boolean).pop(),files=manifest.files||[];
  if(!files.length)throw Error('数据集中没有可导出的文件');
  const entries=files.map(file=>({...file,parts:lerobotExportPath(name,file.path)}));
  for(const entry of entries){if(signal.aborted)throw new DOMException('导出已取消','AbortError');if(await lerobotExportFileExists(entry.parts))throw Error('目标文件已存在：'+entry.parts.join('/')+'。请选择其他目录，避免覆盖已导出数据。');}
  const folders=new Map([['',exportDirectory]]);let bytes=0,completed=0;const began=performance.now();
  for(const entry of entries){
   if(signal.aborted)throw new DOMException('导出已取消','AbortError');
   let folder=exportDirectory,key='';
   for(const part of entry.parts.slice(0,-1)){key=key?key+'/'+part:part;if(!folders.has(key))folders.set(key,await folder.getDirectoryHandle(part,{create:true}));folder=folders.get(key);}
   const handle=await folder.getFileHandle(entry.parts[entry.parts.length-1],{create:true});
   await streamToFile(entry.url,handle,n=>{bytes+=n;const seconds=Math.max(.1,(performance.now()-began)/1000);$('#exportProgress').textContent='正在导出 '+name+'/'+entry.path+' · '+completed+' / '+entries.length+' 个文件 · '+fmt(bytes/1048576,1)+' / '+fmt(manifest.total_bytes/1048576,1)+' MB · '+fmt(bytes/1048576/seconds,1)+' MB/s';},signal);
   completed++;
  }
  $('#exportProgress').textContent='导出完成：'+exportDirectory.name+'/'+name+'，共 '+completed+' 个文件。meta、data、videos 等目录保持完整；机器人数据仍保留。';
 }catch(e){$('#exportProgress').textContent=e.name==='AbortError'?'导出已取消；已经保存的部分文件保留在所选目录。':'导出失败：'+e.message;}
 finally{lerobotExportBusy=false;lerobotExportAbort=null;updateLerobotExportControls();}
}
function downloadLerobotArchive(){
 const dataset=selectedLerobotDataset(),name=dataset.split('/').filter(Boolean).pop();
 const link=document.createElement('a');link.href='/api/lerobot/export/archive?dataset='+encodeURIComponent(dataset);link.download=name+'.tar';document.body.appendChild(link);link.click();link.remove();
 $('#exportProgress').textContent='已交给浏览器下载完整数据集 '+name+'.tar。在浏览器中查看进度、选择保存位置或取消；解包后保留整个数据集目录，机器人数据仍保留。';
}
function resetLerobotInspection(){lerobotInspection=null;$('#lerobotSummary').textContent='选择 episode 后可读取动作字段；读取不会连接 SDK 或发送动作。';$('#lerobotValues').innerHTML='';updateLerobotButtons();}
function conversionPhaseLabel(phase){return ({discovering:'查找 MCAP',reading:'读取原始数据',writing:'写入帧',saving:'保存数据集',complete:'转换完成',completed:'转换完成',failed:'转换失败',stopped:'已停止'})[phase]||phase||'等待任务';}
function renderConversionProgress(job){
 const p=job.progress||{},result=job.result||{},hasProgress=Object.keys(p).length>0;
 $('#conversionProgress').hidden=!hasProgress&&!job.running;
 $('#conversionProgressPhase').textContent=conversionPhaseLabel(p.phase||job.phase);
 const percent=p.percent!==null&&p.percent!==undefined&&Number.isFinite(Number(p.percent))?Math.max(0,Math.min(100,Number(p.percent))):null;
 $('#conversionPercent').textContent=percent===null?(job.running?'处理中':'—'):fmt(percent,1)+'%';
 $('#conversionProgressBar').innerHTML='<progress max="100" '+(percent===null?'':'value="'+percent+'"')+' aria-label="转换总体进度"></progress>';
 const completed=p.completed_sources,failed=p.failed_sources,processed=Number(completed||0)+Number(failed||0);
 $('#conversionSourceProgress').textContent=p.total_sources===undefined?'MCAP 数量：正在统计':'已处理 '+processed+' / '+p.total_sources+' 个 MCAP'+(failed?' · 失败 '+failed:'');
 $('#conversionCurrentSource').textContent=p.source_name?'当前：'+p.source_name+(p.source_index?'（'+p.source_index+'/'+(p.total_sources??'—')+'）':''):'等待源记录信息';
 $('#conversionFrameProgress').textContent=p.processed_frames===undefined||p.processed_frames===null?'':'当前源帧数：'+p.processed_frames+(p.total_frames===undefined||p.total_frames===null?'':' / '+p.total_frames);
 $('#conversionByteProgress').textContent=p.bytes_read===undefined?'':'已读取 '+fmt(p.bytes_read/1048576,1)+(p.total_bytes===undefined||p.total_bytes===null?'':' / '+fmt(p.total_bytes/1048576,1))+' MB';
 $('#conversionTiming').textContent=p.elapsed_s===undefined?'':'用时 '+clock(p.elapsed_s);
 $('#conversionProgressRaw').textContent=hasProgress?JSON.stringify(p,null,2):'';
 const count=result.total_episodes??p.output_episodes;
 $('#conversionOutputCount').textContent=count===undefined?'输出 episode 数：等待转换结果':'已输出 '+count+' 个 episode'+(result.total_frames===undefined?'':' · '+result.total_frames+' 帧');
 const sourceErrors=Array.isArray(result.errors)?result.errors:[];
 const error=job.error||result.error||p.error||(sourceErrors.length?sourceErrors.length+' 条源记录转换失败':'');
 $('#conversionOutcome').textContent=error?'转换未完成：'+error:result.completed?'转换完成。':job.running?'转换进行中。':'';
 $('#conversionOutcome').className=error?'conversion-outcome failed':'conversion-outcome';
 $('#conversionSourceErrors').hidden=!sourceErrors.length;
 $('#conversionSourceErrors').innerHTML=sourceErrors.map(failure=>'<li><strong>'+esc(failure.source||'未命名源记录')+'</strong><span>'+esc(failure.error||'未提供错误详情')+'</span></li>').join('');
}
function conversionOutputPath(){const result=workflowState.conversion?.result||{};return result.dataset||result.dataset_path||result.output||'';}
async function browseConversionOutput(path='',dataset=conversionBrowserDataset){
 if(!dataset)throw Error('本次任务尚未提供输出目录');
 $('#conversionFileBrowser').hidden=false;$('#conversionBrowserStatus').textContent='正在读取输出目录…';
 try{
  const result=await api('/api/conversion/files?dataset='+encodeURIComponent(dataset)+'&path='+encodeURIComponent(path));
  conversionBrowserDataset=result.dataset||dataset;conversionBrowserPath=result.path??path;conversionBrowserParent=result.parent??null;
  $('#conversionBrowsePath').textContent=conversionBrowserDataset.replace(/\/$/,'')+(conversionBrowserPath?'/'+conversionBrowserPath:'');
  $('#conversionBrowseParent').disabled=conversionBrowserParent===null;
  const entries=result.entries||[];
  $('#conversionBrowserFiles').innerHTML=entries.map(entry=>entry.is_dir?'<button type="button" class="conversion-output-entry folder" data-conversion-entry="'+esc(entry.path)+'"><span>▸ '+esc(entry.name)+'</span><small>文件夹</small></button>':'<div class="conversion-output-entry"><span>'+esc(entry.name)+'</span><small>'+fmt((entry.size||0)/1024,1)+' KB</small></div>').join('');
  $('#conversionBrowserStatus').textContent=result.exists===false?'输出目录尚未创建。可查看上方失败原因，或稍后刷新。':entries.length?entries.length+' 个项目':'此目录为空。';
  document.querySelectorAll('[data-conversion-entry]').forEach(el=>el.addEventListener('click',()=>browseConversionOutput(el.dataset.conversionEntry).catch(e=>notice(e.message))));
 }catch(error){$('#conversionBrowserStatus').textContent='读取输出目录失败：'+error.message;throw error;}
}
function renderWorkflow(s){
 workflowState=s||{};applyWorkflowDefaults(s?.defaults||{});
 updateDatasetPath();updateConversionSelection();updateLerobotButtons();
 const conversion=s?.conversion||{},replay=s?.replay||{},progress=replay.progress||{},result=replay.result||{};
 $('#conversionPhase').textContent=jobPhase(conversion,'尚未转换');
 renderConversionProgress(conversion);
 $('#conversionLog').textContent=workflowLog(conversion)||'转换日志将在这里显示。';
 $('#conversionResult').textContent=conversion.result?JSON.stringify(conversion.result,null,2):'';
 $('#conversionShowResult').disabled=!conversionOutputPath();
 $('#lerobotPhase').textContent=jobPhase(replay,'尚未执行');
 const rate=progress.send_hz??result.send_hz;
 const position=progress.position_s??progress.elapsed_s;
 const duration=progress.duration_s??result.duration_s;
 $('#lerobotRate').textContent='动作发送目标 100 Hz · 实际 '+(rate===null||rate===undefined?'尚无数据':fmt(rate,1)+' Hz')+(position===undefined?'':' · '+clock(position)+' / '+clock(duration))+(result.unsent_frame_count===undefined?'':' · 未发送帧 '+result.unsent_frame_count);
 const jobDataset=replay.dataset??progress.dataset??result.dataset,jobEpisode=replay.episode??progress.episode??result.episode;
 $('#lerobotJob').textContent=jobDataset?'执行数据：'+jobDataset+' / episode '+(jobEpisode??'—'):'';
 $('#lerobotLog').textContent=workflowLog(replay)||'执行日志将在这里显示。';
 $('#lerobotResult').textContent=replay.result?JSON.stringify(replay.result,null,2):'';
 $('#conversionResultHint').textContent=conversionOutputPath()?'输出目录：'+conversionOutputPath():'';
 if(!conversion.running&&conversion.result){const key=JSON.stringify({result:conversion.result,error:conversion.error||''});if(key!==lastConversionCatalogResult){lastConversionCatalogResult=key;refreshLerobotDatasets(undefined,true).catch(error=>notice('转换结果已更新，但数据集列表刷新失败：'+error.message));}}
}
async function refreshWorkflow(){
 if(workflowLoading)return;
 workflowLoading=true;
 try{renderWorkflow(await api('/api/workflow/state'));$('#workflowConnection').textContent='机器人本机处理';if(sharedLerobotRoot&&lerobotCatalogRoot!==sharedLerobotRoot)await refreshLerobotDatasets();}
 catch(e){$('#workflowConnection').textContent='任务状态暂不可用：'+e.message;}
 finally{workflowLoading=false;}
}
async function refreshLerobotDatasets(preferred,forceRefresh=false){
 const root=sharedLerobotRoot||$('#lerobotRoot').value.trim();if(!root)throw Error('请先保存 LeRobot 保存位置');
 if(lerobotCatalogRequest){try{await lerobotCatalogRequest;}catch(error){if(!forceRefresh)throw error;}if(lerobotCatalogRoot===root&&!preferred&&!forceRefresh)return;}
 const priorDataset=$('#lerobotDataset').value,priorEpisode=$('#lerobotEpisode').value;
 lerobotBusy=true;updateLerobotButtons();
 lerobotCatalogRequest=(async()=>{
  const previous=preferred||$('#lerobotDataset').value,previousExport=preferred||$('#lerobotExportDataset').value;
  const result=await api('/api/lerobot/datasets?root='+encodeURIComponent(root));
  if(sharedLerobotRoot&&sharedLerobotRoot!==root)return;
  lerobotDatasets=Array.isArray(result)?result:(result.datasets||[]);lerobotCatalogRoot=root;
  const options=lerobotDatasets.map(d=>'<option value="'+esc(d.path)+'">'+esc(d.name&&d.name!=='.'?d.name:d.path.split('/').pop())+' · '+(d.total_episodes??'—')+' 条 · '+(d.fps??'—')+' Hz</option>').join('');
  $('#lerobotDataset').innerHTML='<option value="">选择回放数据集</option>'+options;
  $('#lerobotExportDataset').innerHTML='<option value="">选择完整数据集</option>'+options;
  const selectPrevious=value=>lerobotDatasets.some(d=>d.path===value)?value:(lerobotDatasets.length===1?lerobotDatasets[0].path:'');
  $('#lerobotDataset').value=selectPrevious(previous);$('#lerobotExportDataset').value=selectPrevious(previousExport);
  $('#lerobotDatasetCount').textContent='共 '+lerobotDatasets.length+' 个数据集';
  $('#lerobotEpisode').innerHTML='<option value="">先选择数据集</option>';$('#lerobotEpisode').value='';resetLerobotInspection();updateLerobotLocations();
 })();
 try{await lerobotCatalogRequest;}finally{lerobotCatalogRequest=null;lerobotBusy=false;updateLerobotButtons();}
 if($('#lerobotDataset').value)await refreshLerobotEpisodes($('#lerobotDataset').value===priorDataset?priorEpisode:undefined);
}
async function refreshLerobotEpisodes(preferredEpisode){
 resetLerobotInspection();updateLerobotLocations();const dataset=$('#lerobotDataset').value;
 $('#lerobotEpisode').innerHTML='<option value="">选择 episode</option>';$('#lerobotEpisode').value='';
 if(!dataset){updateLerobotButtons();return;}
 lerobotBusy=true;updateLerobotButtons();
 try{
  const result=await api('/api/lerobot/episodes?dataset='+encodeURIComponent(dataset));
  if($('#lerobotDataset').value!==dataset)return;
  lerobotEpisodes=Array.isArray(result)?result:(result.episodes||[]);
  $('#lerobotEpisode').innerHTML='<option value="">选择 episode</option>'+lerobotEpisodes.map(e=>{
   const index=e.episode_index??e.episode,frames=e.frames??e.length;const duration=e.duration_s??(result.fps&&frames!==undefined&&frames!==null?frames/result.fps:undefined);
   return '<option value="'+esc(index)+'">episode '+esc(index)+' · '+(frames??'—')+' 帧'+(duration===undefined?'':' · '+fmt(duration,2)+' 秒')+'</option>';
  }).join('');
  const hasPrevious=preferredEpisode!==undefined&&preferredEpisode!==''&&lerobotEpisodes.some(e=>String(e.episode_index??e.episode)===String(preferredEpisode));
  $('#lerobotEpisode').value=hasPrevious?String(preferredEpisode):lerobotEpisodes.length?String(lerobotEpisodes[0].episode_index??lerobotEpisodes[0].episode):'';
  if(!lerobotEpisodes.length){$('#lerobotEpisode').innerHTML='<option value="">此数据集暂无 episode</option>';$('#lerobotSummary').textContent='此数据集 0 条 episode，转换可能尚未完成。请查看转换区的结果与失败原因；也可完成后刷新数据集。';}
  else $('#lerobotSummary').textContent='此数据集 '+lerobotEpisodes.length+' 条 episode · '+(result.fps??'—')+' Hz。选择片段后可读取动作字段。';
 }catch(error){if($('#lerobotDataset').value===dataset)$('#lerobotSummary').textContent='无法读取 episode，数据集可能未完成转换：'+error.message;throw error;}
 finally{lerobotBusy=false;updateLerobotButtons();}
}
async function inspectLerobotEpisode(){
 const selection=selectedLerobot();lerobotBusy=true;updateLerobotButtons();
 try{
  const info=await api('/api/lerobot/inspect',selection);
  if($('#lerobotDataset').value!==selection.dataset||Number($('#lerobotEpisode').value)!==selection.episode)return;
  lerobotInspection=info;const episode=info.selected_episode||info;
  $('#lerobotSummary').textContent='episode '+selection.episode+' · '+(episode.frames??episode.length??'—')+' 帧 · '+(info.fps??episode.fps??'—')+' Hz · '+fmt(episode.duration_s,3)+' 秒。发送字段为 action；observation.state 仅用于对照。';
  const names=episode.names||info.action_names||info.names||[],actions=episode.first_action||[],recorded=episode.first_recorded_observation||[];
  const states=info.state_names?.length?names.map(name=>recorded[info.state_names.indexOf(name)]):recorded;
  $('#lerobotValues').innerHTML=names.length?'<table class="workflow-values"><thead><tr><th>动作字段 / 关节名</th><th>首帧 action</th><th>首帧录制反馈</th></tr></thead><tbody>'+names.map((n,i)=>'<tr><td>'+esc(n)+'</td><td>'+fmt(actions[i],6)+'</td><td>'+fmt(states[i],6)+'</td></tr>').join('')+'</tbody></table>':'<pre>'+esc(JSON.stringify(info,null,2))+'</pre>';
 }finally{lerobotBusy=false;updateLerobotButtons();}
}
action('#conversionStart',async()=>{const body=conversionPayload();conversionBusy=true;updateConversionSelection();try{await api('/api/conversion/start',body);lerobotRootDirty=false;applyWorkflowDefaults({lerobot_root:body.output_dir});workflowState.conversion={running:true,phase:'转换任务已提交'};await refreshWorkflow();}finally{conversionBusy=false;updateConversionSelection();}});
action('#conversionStop',async()=>{await api('/api/conversion/stop',{});await refreshWorkflow();});
action('#conversionUseRoot',()=>refreshConversionFiles(captureInputRoot()||conversionRoot));
action('#conversionShowResult',()=>browseConversionOutput('',conversionOutputPath()));
action('#conversionBrowseParent',()=>{if(conversionBrowserParent!==null)return browseConversionOutput(conversionBrowserParent);});
action('#conversionBrowseRefresh',()=>browseConversionOutput(conversionBrowserPath));
action('#lerobotRefresh',()=>refreshLerobotDatasets());
action('#lerobotInspect',inspectLerobotEpisode);
action('#lerobotExecute',async()=>{const selection=selectedLerobot();lerobotBusy=true;updateLerobotButtons();try{await api('/api/lerobot/replay/start',selection);await refreshWorkflow();}finally{lerobotBusy=false;updateLerobotButtons();}});
action('#lerobotStop',async()=>{await api('/api/lerobot/replay/stop',{});await refreshWorkflow();});
action('#saveLerobotRoot',saveLerobotRoot);
action('#lerobotExportRefresh',()=>refreshLerobotDatasets());
action('#conversionChooseRecords',()=>refreshConversionFiles());
action('#chooseConversionRoot',async()=>{directoryTarget='conversion';$('#createDir').hidden=true;$('#directoryTitle').textContent='选择转换输入目录';$('#directoryDescription').textContent='在此范围选择需要转换的 MCAP；不会更改采集保存目录或导出范围。';await browse(conversionRoot||captureInputRoot()||undefined);$('#directoryDialog').showModal();});
action('#conversionAll',()=>{conversionSelection=new Set(conversionRows.filter(row=>row.category==='collection').map(row=>row.id));renderConversionFiles();});
action('#conversionNone',()=>{conversionSelection.clear();renderConversionFiles();});
action('#convertSelected',async()=>{const paths=typeof exportSelection==='undefined'?[]:[...exportSelection],root=typeof libraryRoot==='undefined'?'':libraryRoot;await refreshConversionFiles(root);conversionSelection=new Set(paths);$('#conversionMode').value='selection';renderConversionFiles();$('#dataConversion').scrollIntoView({behavior:'smooth',block:'start'});});
$('#replayFormat').addEventListener('change',updateReplayFormat);
$('#activeMotionLink').addEventListener('click',event=>{event.preventDefault();$('#replayFormat').value='mcap';updateReplayFormat();$('#dataReplay').scrollIntoView({behavior:'smooth',block:'start'});});
$('#exportFormat').addEventListener('change',updateExportFormat);
$('#lerobotExportDataset').addEventListener('change',updateUnifiedExportControls);
$('#conversionMode').addEventListener('change',updateConversionSelection);
$('#conversionRepo').addEventListener('input',updateDatasetPath);
$('#conversionOutput').addEventListener('input',()=>{lerobotRootDirty=true;$('#lerobotRootStatus').textContent='保存位置已修改，点击“保存位置”供回放和导出使用。';updateDatasetPath();});
$('#lerobotDataset').addEventListener('change',()=>refreshLerobotEpisodes().catch(e=>notice(e.message)));
$('#lerobotEpisode').addEventListener('change',resetLerobotInspection);
updateReplayFormat();updateExportFormat();updateConversionSelection();updateLerobotButtons();refreshConversionFiles().catch(()=>{});refreshWorkflow();setInterval(refreshWorkflow,1000);
