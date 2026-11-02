/*
使用说明
用途：转换/续转、独立来源的LeRobot回放和数据集导出界面。
用法：由 app.py 提供的工作台页面加载，浏览器访问 http://机器人IP:18769/；不作为独立脚本启动。
详细说明：../数据管理与转换调度使用说明.md
*/
/* 转换、LeRobot读取/回放及任务进度；NULL只显示，不补值也不禁用回放。 */
'use strict';
// Conversion and LeRobot playback have their own selection and job state.
let workflowState={conversion:{},replay:{}},lerobotDatasets=[],lerobotEpisodes=[],lerobotInspection=null,workflowLoading=false,lerobotBusy=false,conversionBusy=false;
let sharedLerobotRoot='',lerobotRootDirty=false,lerobotCatalogRoot='',lerobotCatalogRequest=null;
let lerobotReplayRoot='',lerobotExportCatalogRoot='',lerobotIncompleteConversion=null,lerobotReplayRootInitialized=false;
try{if(typeof localStorage!=='undefined')lerobotReplayRoot=localStorage.getItem('datacp.lerobotReplayRoot')||'';}catch(_){}
let lerobotExportBusy=false,lerobotExportAbort=null;
let conversionSelection=new Set(),conversionRoot='',conversionTree=null,conversionExpanded=new Map(),conversionRows=[],conversionFilesRequest=null,conversionRequestRoot='';
let conversionRoots={},conversionCaptureActive=false,conversionCapturePath='',conversionRequestFormat='';
let conversionBrowserDataset='',conversionBrowserPath='',conversionBrowserParent=null;
let lastConversionCatalogResult='';
const lerobotDirectoryPickerAvailable=typeof window!=='undefined'&&typeof window.showDirectoryPicker==='function';
function workflowLog(job){
 const log=Array.isArray(job?.log)?job.log.join('\n'):job?.log||'';
 return [job?.error?'错误：'+job.error:'',log].filter(Boolean).join('\n');
}
function jobPhase(job,idle){return (job?.running&&job?.progress?.phase)||job?.phase||(job?.running?'运行中':job?.error?'未完成':job?.result?'已结束':idle);}
function conversionInputFormat(){return $('#conversionFormat').value==='factory'?'factory':'mcap';}
function conversionInputName(){return conversionInputFormat()==='factory'?'原生采集':'MCAP';}
function conversionDefaultRoot(){return conversionInputFormat()==='factory'?'/data/record':captureInputRoot();}
function refreshConversionAfterCapture(){if(conversionInputFormat()==='mcap')return refreshConversionFiles();}
function observeConversionCapture(recording){
 const active=!!recording?.active,path=recording?.path||recording?.file||'';
 if(conversionCaptureActive&&(!active||path!==conversionCapturePath))Promise.resolve(refreshConversionAfterCapture()).catch(()=>{});
 conversionCaptureActive=active;conversionCapturePath=path;
}
async function changeConversionFormat(){
 conversionRoot=conversionRoots[conversionInputFormat()]||conversionDefaultRoot();conversionSelection.clear();conversionExpanded.clear();conversionTree=null;conversionRows=[];
 updateConversionSelection();await refreshConversionFiles(conversionRoot);
}
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
 const format=conversionInputFormat(),requested=path||conversionRoot||conversionDefaultRoot();
 if(conversionFilesRequest){const same=requested===conversionRequestRoot&&format===conversionRequestFormat;await conversionFilesRequest;if(same)return;}
 conversionRequestRoot=requested;conversionRequestFormat=format;$('#conversionChooseRecords').disabled=true;$('#conversionFileStatus').textContent='正在读取目录…';
 conversionFilesRequest=(async()=>{
  const query=new URLSearchParams();if(requested)query.set('path',requested);if(format==='factory')query.set('input_format',format);
  const result=await api('/api/library/tree'+(query.size?'?'+query.toString():''));if(format!==conversionInputFormat())return;
  const root=result.root||requested,changed=root!==conversionRoot;
  if(changed){conversionSelection.clear();conversionExpanded.clear();}
  conversionRoot=root;conversionRoots[format]=root;conversionTree=result.tree;conversionRows=conversionTree?conversionFolderEpisodes(conversionTree):[];
  $('#conversionInputRoot').value=root;$('#conversionSourceDir').value=root;renderConversionFiles(!changed);
  $('#conversionFileStatus').textContent='共 '+conversionRows.length+' 条已完成记录';
 })();
 try{await conversionFilesRequest;}catch(error){$('#conversionFileStatus').textContent='读取失败：'+error.message+'；可刷新重试。';throw error;}
 finally{conversionFilesRequest=null;$('#conversionChooseRecords').disabled=false;}
}
function updateConversionSelection(){
 const count=conversionSelection.size;
 const directory=$('#conversionMode').value==='directory';
 $('#conversionInputLabel').textContent=conversionInputName()+' 输入目录';$('#conversionInputNote').textContent=conversionInputFormat()==='factory'?'生成 LeRobot v2.1：MP4 视频＋Parquet；24维、float64、空值和原分辨率均保留。不插值、不补值。请使用新的 MP4 输出文件夹。':'生成 LeRobot v2.1：MP4 视频＋Parquet；保留原解码像素、24维 float64 和空值。不插值、不补值。请使用新的 MP4 输出文件夹。';$('#conversionUseRoot').textContent=conversionInputFormat()==='factory'?'使用原生采集目录':'使用当前采集目录';
 $('#conversionSelected').textContent='已选择 '+count+' 条 '+conversionInputName()+' 记录';
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
 const payload={output_dir,dataset_name,task,input_format:conversionInputFormat(),threads:Number($('#conversionThreads').value)||2,workers:Number($('#conversionWorkers').value)||2};
 if(directory){payload.source_dir=$('#conversionSourceDir').value.trim();if(!payload.source_dir)throw Error('请输入需要批量转换的原始记录文件夹');}
 else{payload.episode_paths=[...conversionSelection];if(!payload.episode_paths.length)throw Error('请先在转换区勾选原始采集记录');}
 return payload;
}
function updateDatasetPath(){const output=$('#conversionOutput').value.trim().replace(/\/$/,''),name=$('#conversionRepo').value.trim();$('#conversionDatasetPath').textContent=output&&name?output+'/'+name:output?output+'/〔本次转换文件夹〕':'先填写保存位置和文件夹名称';}
function applyWorkflowDefaults(defaults){
 const root=defaults?.lerobot_root||defaults?.conversion_output;if(!root)return;
 const changed=root!==sharedLerobotRoot;sharedLerobotRoot=root;
 if(!lerobotReplayRoot)lerobotReplayRoot=root;
 if(!lerobotReplayRootInitialized){$('#lerobotRoot').value=lerobotReplayRoot;lerobotReplayRootInitialized=true;}
 if(!lerobotRootDirty)$('#conversionOutput').value=root;
 if(changed){lerobotExportCatalogRoot='';$('#lerobotExportDataset').innerHTML='<option value="">正在读取数据集</option>';$('#lerobotExportDataset').value='';}
 updateLerobotLocations();updateDatasetPath();
}
function updateLerobotLocations(){
 const dataset=$('#lerobotDataset').value;
 $('#lerobotReplayRoot').textContent=dataset?'所选数据集：'+dataset:'在上方选择已有数据集目录或父目录；直接读取，不复制文件。';
 $('#lerobotExportRoot').textContent='保存位置：'+(sharedLerobotRoot||'正在读取');
 $('#lerobotExportSelection').textContent=$('#lerobotExportDataset').value?'整套数据集：'+$('#lerobotExportDataset').value:'尚未选择数据集';
}
async function useLerobotReplayRoot(path=$('#lerobotRoot').value){
 const root=path.trim();if(!root.startsWith('/'))throw Error('请输入机器人上的绝对数据目录');
 lerobotReplayRoot=root;$('#lerobotRoot').value=root;
 try{if(typeof localStorage!=='undefined')localStorage.setItem('datacp.lerobotReplayRoot',root);}catch(_){}
 await refreshLerobotDatasets(undefined,true);
}
async function saveLerobotRoot(){
 const root=$('#conversionOutput').value.trim();if(!root.startsWith('/'))throw Error('请输入机器人上的绝对保存路径');
 $('#saveLerobotRoot').disabled=true;
 try{const state=await api('/api/workflow/settings',{lerobot_root:root});lerobotRootDirty=false;applyWorkflowDefaults(state.defaults||{lerobot_root:root});$('#lerobotRootStatus').textContent='位置已保存，供转换和导出使用；回放目录独立选择。';await refreshLerobotDatasets();}
 finally{$('#saveLerobotRoot').disabled=false;}
}
function getExportFormat(){return ['mcap','factory','lerobot'].includes($('#exportFormat').value)?$('#exportFormat').value:'mcap';}
function updateReplayFormat(){const format=$('#replayFormat').value;$('#mcapReplay').hidden=format!=='mcap';$('#lerobotReplay').hidden=format!=='lerobot';$('#nativeReplay').hidden=format!=='factory';if(format==='factory'&&typeof refreshNativeFiles==='function')refreshNativeFiles().catch(e=>notice(e.message));}
function updateExportFormat(){const lr=getExportFormat()==='lerobot';$('#mcapExportInputs').hidden=lr;$('#lerobotExportInputs').hidden=!lr;$('#exportProgress').textContent='导出后机器人中的文件仍保留。';updateUnifiedExportControls();if(typeof refreshRobotFiles==='function'&&getExportFormat()!=='lerobot')refreshRobotFiles().catch(()=>{});}
function updateUnifiedExportControls(){
 const lr=getExportFormat()==='lerobot',busy=lerobotExportBusy||(typeof exportBusy!=='undefined'&&exportBusy);
 const chosen=lr?!!$('#lerobotExportDataset').value:typeof exportSelection!=='undefined'&&exportSelection.size>0;
 $('#exportSelected').disabled=busy||!chosen;$('#exportArchive').disabled=busy||!chosen;
 $('#chooseExportFolder').disabled=busy;$('#exportFormat').disabled=busy;
 $('#cancelExport').disabled=!(lerobotExportAbort||(typeof exportAbort!=='undefined'&&exportAbort));
 $('#exportSelected').textContent=lerobotDirectoryPickerAvailable?(lr?'导出完整数据集':getExportFormat()==='factory'?'导出原生完整目录':'导出所选 MCAP'):(lr?'导出完整数据集 (.tar)':getExportFormat()==='factory'?'导出原生完整目录 (.tar)':'导出所选 MCAP (.tar)');
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
 $('#lerobotResumeConversion').hidden=!lerobotIncompleteConversion?.resume_request||!lerobotIncompleteConversion?.resume_run_dir||!!lerobotIncompleteConversion?.running;
 $('#lerobotResumeConversion').textContent='继续此数据集转换'+(lerobotIncompleteConversion?.source_count?'（'+lerobotIncompleteConversion.source_count+' 条记录）':'');$('#lerobotResumeConversion').disabled=conversionBusy||!!workflowState.conversion?.running;
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
 const p=job.progress||{},result=job.result||{},hasProgress=Object.keys(p).length>0,inputFormat=p.input_format||job.resume_request?.input_format;
 $('#conversionProgress').hidden=!hasProgress&&!job.running;
 const cameraNames={'observation.images.head_color':'头部 RGB','observation.images.hand_left_color':'左腕 RGB','observation.images.hand_right_color':'右腕 RGB'};
 $('#conversionProgressPhase').textContent=conversionPhaseLabel(p.phase||job.phase)+(p.camera?' · '+(cameraNames[p.camera]||p.camera):'');
 const confirmed=!job.running&&result.completed===true&&!job.error;
 const percent=p.percent!==null&&p.percent!==undefined&&Number.isFinite(Number(p.percent))?Math.max(0,Math.min(confirmed?100:99.9,Number(p.percent))):null;
 $('#conversionPercent').textContent=percent===null?(job.running?'处理中':'—'):fmt(percent,1)+'%';
 $('#conversionProgressBar').innerHTML='<progress max="100" '+(percent===null?'':'value="'+percent+'"')+' aria-label="转换总体进度"></progress>';
 const completed=p.completed_sources,failed=p.failed_sources,processed=Number(completed||0)+Number(failed||0);
 $('#conversionSourceProgress').textContent=p.total_sources===undefined?'记录数量：正在统计':'已处理 '+processed+' / '+p.total_sources+' 个'+(inputFormat==='factory'?'原生记录':' MCAP')+(failed?' · 失败 '+failed:'');
 $('#conversionCurrentSource').textContent=p.source_name?'当前：'+p.source_name+(p.source_index?'（'+p.source_index+'/'+(p.total_sources??'—')+'）':''):'等待源记录信息';
 $('#conversionFrameProgress').textContent=p.processed_frames===undefined||p.processed_frames===null?'':'当前源帧数：'+p.processed_frames+(p.total_frames===undefined||p.total_frames===null?'':' / '+p.total_frames);
 $('#conversionByteProgress').textContent=p.bytes_read===undefined||(inputFormat==='factory'&&!p.total_bytes)?'':'已读取 '+fmt(p.bytes_read/1048576,1)+(p.total_bytes===undefined||p.total_bytes===null?'':' / '+fmt(p.total_bytes/1048576,1))+' MB';
 $('#conversionTiming').textContent=p.elapsed_s===undefined?'':'用时 '+clock(p.elapsed_s);
 $('#conversionConcurrency').textContent=p.workers?'并行记录数 '+p.workers+' · 每条 '+(p.threads||1)+' 线程':'';$('#conversionActiveSources').innerHTML=(p.active_sources||[]).map(item=>'<li>'+esc(item.source_name)+' · '+esc(item.phase||'处理中')+' · '+fmt(item.percent,1)+'%</li>').join('');
 $('#conversionProgressRaw').textContent=hasProgress?JSON.stringify(p,null,2):'';
 const count=result.total_episodes??p.output_episodes;
 $('#conversionOutputCount').textContent=count===undefined?'输出 episode 数：等待转换结果':'已输出 '+count+' 个 episode'+(result.total_frames===undefined?'':' · '+result.total_frames+' 帧');
 const sourceErrors=Array.isArray(result.errors)?result.errors:[];
 const error=job.error||result.error||p.error||(sourceErrors.length?sourceErrors.length+' 条源记录转换失败':'');
 $('#conversionOutcome').textContent=error?'转换未完成：'+error:result.completed?'转换完成，原始空值 '+(result.conversion?.null_action_values??0)+' 个动作值已保留。':job.running?'转换进行中。':'';
 $('#conversionOutcome').className=error?'conversion-outcome failed':'conversion-outcome';
 $('#conversionSourceErrors').hidden=!sourceErrors.length;
 $('#conversionSourceErrors').innerHTML=sourceErrors.map(failure=>'<li><strong>'+esc(failure.source||'未命名源记录')+'</strong><span>'+esc(failure.error||'未提供错误详情')+'</span></li>').join('');
}
function conversionOutputPath(){const job=workflowState.conversion||{},result=job.result||{};return result.dataset||result.dataset_path||result.output||job.dataset_path||'';}
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
 if(typeof renderNativeReplay==='function')renderNativeReplay(replay);
 $('#lerobotPhase').textContent=result.completed?'原始指令发送结束':jobPhase(replay,'尚未执行');
 const rate=progress.send_hz??result.send_hz;
 const position=progress.position_s??progress.elapsed_s;
 const duration=progress.duration_s??result.duration_s;
 $('#lerobotRate').textContent='原始指令事件 + 100 Hz 保持 · 实际 '+(rate===null||rate===undefined?'尚无数据':fmt(rate,1)+' Hz')+(position===undefined?'':' · '+clock(position)+' / '+clock(duration))+(result.unsent_frame_count===undefined?'':' · 未读取原始帧 '+result.unsent_frame_count);
 const jobDataset=replay.dataset??progress.dataset??result.dataset,jobEpisode=replay.episode??progress.episode??result.episode;
 const armErrors=(result.tracking?.rmse||[]).filter((v,i)=>v!==null&&result.tracking.names[i]?.includes('_arm_'));
 $('#lerobotJob').textContent=(jobDataset?'执行数据：'+jobDataset+' / episode '+(jobEpisode??'—'):'')+(result.sent===undefined?'':' · SDK 返回成功 '+(result.sdk_accepted??(result.sent-(result.rejected||0)))+' / '+result.sent+' 次')+(armErrors.length?' · 双臂最大反馈 RMSE '+fmt(Math.max(...armErrors),3)+' rad':'');
 $('#lerobotLog').textContent=workflowLog(replay)||'执行日志将在这里显示。';
 $('#lerobotResult').textContent=replay.result?JSON.stringify(replay.result,null,2):'';
 $('#conversionResultHint').textContent=conversionOutputPath()?'输出目录：'+conversionOutputPath():'';
 if(!conversion.running&&conversion.result){const key=JSON.stringify({result:conversion.result,error:conversion.error||''});if(key!==lastConversionCatalogResult){lastConversionCatalogResult=key;const output=conversion.result.dataset_path||conversion.result.dataset,root=(lerobotReplayRoot||$('#lerobotRoot').value.trim()).replace(/\/$/,'');const preferred=conversion.result.completed&&output&&(output===root||output.startsWith(root+'/'))?output:undefined;refreshLerobotDatasets(preferred,true).catch(error=>notice('转换结果已更新，但数据集列表刷新失败：'+error.message));}}
}
async function refreshWorkflow(){
 if(workflowLoading)return;
 workflowLoading=true;
 try{renderWorkflow(await api('/api/workflow/state'));$('#workflowConnection').textContent='机器人本机处理';if(lerobotReplayRoot&&(lerobotCatalogRoot!==lerobotReplayRoot||lerobotExportCatalogRoot!==sharedLerobotRoot))await refreshLerobotDatasets();}
 catch(e){$('#workflowConnection').textContent='任务状态暂不可用：'+e.message;}
 finally{workflowLoading=false;}
}
async function refreshLerobotDatasets(preferred,forceRefresh=false){
 const root=lerobotReplayRoot||$('#lerobotRoot').value.trim(),exportRoot=sharedLerobotRoot||root;if(!root)throw Error('请选择 LeRobot 回放数据目录');
 if(lerobotCatalogRequest){try{await lerobotCatalogRequest;}catch(error){if(!forceRefresh)throw error;}if(lerobotCatalogRoot===root&&lerobotExportCatalogRoot===exportRoot&&!preferred&&!forceRefresh)return;}
 const priorDataset=$('#lerobotDataset').value,priorEpisode=$('#lerobotEpisode').value;
 lerobotBusy=true;updateLerobotButtons();
 lerobotCatalogRequest=(async()=>{
  const previous=preferred||$('#lerobotDataset').value,previousExport=$('#lerobotExportDataset').value;
  const load=async path=>{const result=await api('/api/lerobot/datasets?root='+encodeURIComponent(path));return Array.isArray(result)?result:(result.datasets||[]);};
  const [rows,exportRows]=root===exportRoot?await load(root).then(list=>[list,list]):await Promise.all([load(root),load(exportRoot)]);
  if(lerobotReplayRoot!==root||sharedLerobotRoot!==exportRoot)return;
  lerobotDatasets=rows;lerobotCatalogRoot=root;lerobotExportCatalogRoot=exportRoot;
  const options=list=>list.map(d=>'<option value="'+esc(d.path)+'">'+esc(d.name&&d.name!=='.'?d.name:d.path.split('/').pop())+' · '+(d.total_episodes??'—')+' 条 · '+(d.fps??'—')+' Hz'+(d.conversion_complete===false?' · 转换未完成':'')+'</option>').join('');
  $('#lerobotDataset').innerHTML='<option value="">选择回放数据集</option>'+options(rows);
  $('#lerobotExportDataset').innerHTML='<option value="">选择完整数据集</option>'+options(exportRows);
  const selectPrevious=(list,value)=>list.some(d=>d.path===value)?value:(list.length===1?list[0].path:'');
  $('#lerobotDataset').value=selectPrevious(rows,previous);$('#lerobotExportDataset').value=selectPrevious(exportRows,previousExport);
  $('#lerobotDatasetCount').textContent='回放目录下共 '+rows.length+' 个数据集';
  $('#lerobotEpisode').innerHTML='<option value="">先选择数据集</option>';$('#lerobotEpisode').value='';lerobotIncompleteConversion=null;resetLerobotInspection();updateLerobotLocations();
 })();
 try{await lerobotCatalogRequest;}finally{lerobotCatalogRequest=null;lerobotBusy=false;updateLerobotButtons();}
 if($('#lerobotDataset').value)await refreshLerobotEpisodes($('#lerobotDataset').value===priorDataset?priorEpisode:undefined);
}
async function refreshLerobotEpisodes(preferredEpisode){
 lerobotIncompleteConversion=null;resetLerobotInspection();updateLerobotLocations();const dataset=$('#lerobotDataset').value;
 $('#lerobotEpisode').innerHTML='<option value="">选择 episode</option>';$('#lerobotEpisode').value='';
 if(!dataset){updateLerobotButtons();return;}
 lerobotBusy=true;updateLerobotButtons();
 try{
  const result=await api('/api/lerobot/episodes?dataset='+encodeURIComponent(dataset));
  if($('#lerobotDataset').value!==dataset)return;
  lerobotEpisodes=(Array.isArray(result)?result:(result.episodes||[])).filter(e=>e.exists!==false&&e.committed!==false);
  $('#lerobotEpisode').innerHTML='<option value="">选择 episode</option>'+lerobotEpisodes.map(e=>{
   const index=e.episode_index??e.episode,frames=e.frames??e.length;const duration=e.duration_s??(result.fps&&frames!==undefined&&frames!==null?frames/result.fps:undefined);
   return '<option value="'+esc(index)+'">episode '+esc(index)+' · '+(frames??'—')+' 帧'+(duration===undefined?'':' · '+fmt(duration,2)+' 秒')+(e.source?' · '+esc(e.source.split('/').pop()):'')+'</option>';
  }).join('');
  const hasPrevious=preferredEpisode!==undefined&&preferredEpisode!==''&&lerobotEpisodes.some(e=>String(e.episode_index??e.episode)===String(preferredEpisode));
  $('#lerobotEpisode').value=hasPrevious?String(preferredEpisode):lerobotEpisodes.length?String(lerobotEpisodes[0].episode_index??lerobotEpisodes[0].episode):'';
  if(!lerobotEpisodes.length){
   lerobotIncompleteConversion=result.conversion_status||null;const job=lerobotIncompleteConversion,p=job?.progress||{};
   const frames=p.total_frames?' · '+(p.processed_frames||0)+' / '+p.total_frames+' 帧':'';
   $('#lerobotEpisode').innerHTML='<option value="">此数据集暂无 episode</option>';
   $('#lerobotSummary').textContent=job?'此数据集 0 条 episode。转换'+(job.phase||'未完成')+frames+'；尚未生成可回放文件。'+(job.running?'转换仍在进行，完成后刷新数据集。':job.resume_request?'原任务来源：'+(job.source_paths||job.resume_request.episode_paths||[]).join('、')+'；共 '+(job.source_count||'待统计')+' 条记录。可继续此任务，完成后再刷新。':job.error?job.error:''):'此数据集 0 条 episode，转换尚未完成。请查看转换区的结果与失败原因。';
  }
  else $('#lerobotSummary').textContent=(lerobotEpisodes[0]?.source?'原记录：'+lerobotEpisodes[0].source.split('/').pop()+'。':'')+'此数据集 '+lerobotEpisodes.length+' 条 episode · '+(result.fps??'—')+' Hz。选择片段后可读取动作字段。';
 }catch(error){if($('#lerobotDataset').value===dataset)$('#lerobotSummary').textContent='无法读取 episode，数据集可能未完成转换：'+error.message;throw error;}
 finally{lerobotBusy=false;updateLerobotButtons();}
}
async function inspectLerobotEpisode(){
 const selection=selectedLerobot();lerobotBusy=true;updateLerobotButtons();
 try{
  const info=await api('/api/lerobot/inspect',selection);
  if($('#lerobotDataset').value!==selection.dataset||Number($('#lerobotEpisode').value)!==selection.episode)return;
  lerobotInspection=info;const episode=info.selected_episode||info;
  $('#lerobotSummary').textContent=(info.raw_contract===false?'旧转换结果：可能含旧缩放/补值，请从原始文件重转为24维保真数据。 ':info.training_ready===false?'含原始空值：可回放；标准 OpenPI 训练需另行确定空值规则。 ':'')+(info.source_records?.find(r=>r.episode_index===selection.episode)?.source?'原记录：'+info.source_records.find(r=>r.episode_index===selection.episode).source.split('/').pop()+'；':'')+'episode '+selection.episode+' · '+(episode.frames??episode.length??'—')+' 帧 · '+(info.fps??episode.fps??'—')+' Hz · '+fmt(episode.duration_s,3)+' 秒。发送原始 action；空值保持为空，不删除整帧。';
  const names=episode.names||info.action_names||info.names||[],actions=episode.first_action||[],recorded=episode.first_recorded_observation||[];
  const states=info.state_names?.length?names.map(name=>recorded[info.state_names.indexOf(name)]):recorded;
  $('#lerobotValues').innerHTML=names.length?'<table class="workflow-values"><thead><tr><th>动作字段 / 关节名</th><th>首帧 action</th><th>首帧录制反馈</th></tr></thead><tbody>'+names.map((n,i)=>'<tr><td>'+esc(n)+'</td><td>'+(actions[i]===null?'空值（不发送新目标）':fmt(actions[i],6))+'</td><td>'+(states[i]===null?'空值':fmt(states[i],6))+'</td></tr>').join('')+'</tbody></table>':'<pre>'+esc(JSON.stringify(info,null,2))+'</pre>';
 }finally{lerobotBusy=false;updateLerobotButtons();}
}
action('#conversionStart',async()=>{const body=conversionPayload();conversionBusy=true;updateConversionSelection();try{await api('/api/conversion/start',body);lerobotRootDirty=false;applyWorkflowDefaults({lerobot_root:body.output_dir});workflowState.conversion={running:true,phase:'转换任务已提交'};await refreshWorkflow();}finally{conversionBusy=false;updateConversionSelection();}});
action('#conversionStop',async()=>{await api('/api/conversion/stop',{});await refreshWorkflow();});
action('#conversionUseRoot',()=>refreshConversionFiles(conversionDefaultRoot()||conversionRoot));
action('#conversionShowResult',()=>browseConversionOutput('',conversionOutputPath()));
action('#conversionBrowseParent',()=>{if(conversionBrowserParent!==null)return browseConversionOutput(conversionBrowserParent);});
action('#conversionBrowseRefresh',()=>browseConversionOutput(conversionBrowserPath));
action('#lerobotRefresh',()=>useLerobotReplayRoot());
action('#chooseLerobotReplayRoot',async()=>{directoryTarget='lerobot-replay';$('#createDir').hidden=true;$('#directoryTitle').textContent='选择 LeRobot 回放数据来源';$('#directoryDescription').textContent='可选择一个数据集目录或其父目录；只更改回放来源，不更改采集、转换或导出设置。';await browse(lerobotReplayRoot||sharedLerobotRoot);$('#directoryDialog').showModal();});
// 恢复明确的后台任务编号；不把另一个历史任务或当前全目录选择当作原任务。
action('#lerobotResumeConversion',async()=>{const job=lerobotIncompleteConversion,dataset=$('#lerobotDataset').value;if(!job?.resume_run_dir||job.dataset_path!==dataset)return;conversionBusy=true;updateLerobotButtons();try{await api('/api/conversion/resume',{run_dir:job.resume_run_dir,dataset});await refreshWorkflow();$('#lerobotSummary').textContent='正在继续原任务：'+(job.source_paths||[]).join('、')+'。完成后可选择 episode。';}finally{conversionBusy=false;updateLerobotButtons();}});
action('#lerobotInspect',inspectLerobotEpisode);
action('#lerobotExecute',async()=>{const selection=selectedLerobot();lerobotBusy=true;updateLerobotButtons();try{await api('/api/lerobot/replay/start',selection);await refreshWorkflow();}finally{lerobotBusy=false;updateLerobotButtons();}});
action('#lerobotStop',async()=>{await api('/api/lerobot/replay/stop',{});await refreshWorkflow();});
action('#saveLerobotRoot',saveLerobotRoot);
action('#lerobotExportRefresh',()=>refreshLerobotDatasets());
action('#conversionChooseRecords',()=>refreshConversionFiles());
action('#chooseConversionRoot',async()=>{directoryTarget='conversion';$('#createDir').hidden=true;$('#directoryTitle').textContent='选择转换输入目录';$('#directoryDescription').textContent='在此范围选择需要转换的原始记录；不会更改采集保存目录或导出范围。';await browse(conversionRoot||conversionDefaultRoot()||undefined);$('#directoryDialog').showModal();});
action('#conversionAll',()=>{conversionSelection=new Set(conversionRows.filter(row=>row.category==='collection').map(row=>row.id));renderConversionFiles();});
action('#conversionNone',()=>{conversionSelection.clear();renderConversionFiles();});
action('#convertSelected',async()=>{$('#conversionFormat').value=typeof libraryFormat!=='undefined'?libraryFormat:'mcap';const paths=typeof exportSelection==='undefined'?[]:[...exportSelection],root=typeof libraryRoot==='undefined'?'':libraryRoot;await refreshConversionFiles(root);conversionSelection=new Set(paths);$('#conversionMode').value='selection';renderConversionFiles();$('#dataConversion').scrollIntoView({behavior:'smooth',block:'start'});});
$('#replayFormat').addEventListener('change',updateReplayFormat);
$('#activeMotionLink').addEventListener('click',event=>{event.preventDefault();$('#replayFormat').value='mcap';updateReplayFormat();$('#dataReplay').scrollIntoView({behavior:'smooth',block:'start'});});
$('#exportFormat').addEventListener('change',updateExportFormat);
$('#lerobotExportDataset').addEventListener('change',updateUnifiedExportControls);
$('#conversionFormat').addEventListener('change',()=>changeConversionFormat().catch(error=>notice(error.message)));
$('#conversionMode').addEventListener('change',updateConversionSelection);
$('#conversionRepo').addEventListener('input',updateDatasetPath);
$('#conversionOutput').addEventListener('input',()=>{lerobotRootDirty=true;$('#lerobotRootStatus').textContent='保存位置已修改，点击“保存位置”供转换和导出使用。';updateDatasetPath();});
$('#lerobotDataset').addEventListener('change',()=>refreshLerobotEpisodes().catch(e=>notice(e.message)));
$('#lerobotEpisode').addEventListener('change',resetLerobotInspection);
updateReplayFormat();updateExportFormat();updateConversionSelection();updateLerobotButtons();refreshConversionFiles().catch(()=>{});refreshWorkflow();setInterval(refreshWorkflow,1000);

setInterval(()=>{if(!conversionFilesRequest)refreshConversionFiles().catch(()=>{});},10000);
