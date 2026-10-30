'use strict';
let robotFileRows=[],libraryTree=null,libraryRoot='',libraryOpen=new Map(),exportSelection=new Set(),exportDirectory=null,exportBusy=false,exportAbort=null,pendingDelete=[],robotFilesRefresh=null;
const directoryPickerAvailable=typeof window!=='undefined'&&typeof window.showDirectoryPicker==='function';
function clientComputer(){
 const platform=typeof navigator!=='undefined'?(navigator.userAgentData?.platform||navigator.platform||'当前电脑'):'当前电脑';
 return /mac/i.test(platform)?'macOS':/win/i.test(platform)?'Windows':/linux/i.test(platform)?'Linux':platform;
}
function renderRobotStorage(s){
 const r=s.recording;
 $('#robotSaveDir').value=r.data_dir||s.data_dir||'';
 $('#robotStorageStatus').textContent=r.connected?'机器人本机运行':'采集服务连接中';
 $('#robotStorageInfo').textContent='机器人可用空间 '+fmt(r.free_gb,1)+' GB · '+(r.active?'正在写入 '+r.path:'当前采集目录持续保留；每次开始 / 结束生成一条独立 episode。');
}
function folderEpisodes(group){return [...(group.episodes||[]),...(group.groups||[]).flatMap(folderEpisodes)];}
function selectedBytes(){return robotFileRows.filter(r=>exportSelection.has(r.id)).reduce((n,r)=>n+(r.total_bytes??r.bytes??0),0);}
function updateExportControls(){
 $('#exportCount').textContent='已选 '+exportSelection.size+' 条 · '+fmt(selectedBytes()/1048576,1)+' MB';
 ['exportSelected','exportArchive','deleteSelected'].forEach(id=>$('#'+id).disabled=exportBusy||!exportSelection.size);
 ['chooseExportFolder','chooseLibraryRoot','useCaptureRoot','exportAll','exportNone'].forEach(id=>$('#'+id).disabled=exportBusy);
 $('#cancelExport').disabled=!exportAbort;
 if(typeof updateConversionSelection==='function')updateConversionSelection();
 if(typeof updateUnifiedExportControls==='function')updateUnifiedExportControls();
 if($('#convertSelected'))$('#convertSelected').disabled=exportBusy||!exportSelection.size;
 document.querySelectorAll('[data-export-file],[data-export-group]').forEach(el=>el.disabled=exportBusy||el.dataset.empty==='true');
}
function rememberFolderExpansion(){
 document.querySelectorAll('[data-library-folder]').forEach(el=>libraryOpen.set(el.dataset.libraryFolder,el.open));
}
function selectLibraryGroup(path,checked){
 function find(group){if(group.path===path)return group;for(const child of group.groups||[]){const found=find(child);if(found)return found;}}
 const group=libraryTree&&find(libraryTree);if(!group)return;
 for(const row of folderEpisodes(group))checked?exportSelection.add(row.id):exportSelection.delete(row.id);
 renderRobotFiles();
}
function renderRobotFiles(){
 rememberFolderExpansion();
 const available=new Set(robotFileRows.map(r=>r.id));
 exportSelection=new Set([...exportSelection].filter(n=>available.has(n)));
 const groups=new Map();
 function html(group,isRoot=false){
  const rows=folderEpisodes(group),chosen=rows.filter(r=>exportSelection.has(r.id)).length;
  groups.set(group.path,{chosen,count:rows.length});
  const open=libraryOpen.has(group.path)?libraryOpen.get(group.path):isRoot;
  return '<details class="library-folder" data-library-folder="'+esc(group.path)+'" '+(open?'open':'')+'><summary><label class="library-folder-label"><input type="checkbox" data-export-group="'+esc(group.path)+'" data-empty="'+(!rows.length)+'" '+(chosen&&chosen===rows.length?'checked':'')+'><strong>'+esc(group.name)+'</strong></label><span class="muted">'+rows.length+' 条 · '+fmt((group.total_bytes||0)/1048576,1)+' MB</span></summary><div class="library-folder-content">'+
   (group.episodes||[]).map(row=>'<label class="robot-file-row"><input type="checkbox" data-export-file="'+esc(row.id)+'" '+(exportSelection.has(row.id)?'checked':'')+'><div><strong>'+esc(row.file)+'</strong><small>'+({collection:'正式采集',feedback:'执行反馈',test:'测试'}[row.category]||'记录')+' · '+fmt((row.total_bytes??row.bytes??0)/1048576,1)+' MB</small></div></label>').join('')+
   (group.groups||[]).map(child=>html(child)).join('')+(!rows.length?'<p class="muted library-empty">此文件夹暂无已完成的 episode。</p>':'')+'</div></details>';
 }
 $('#robotFiles').innerHTML=libraryTree?html(libraryTree,true):'<p class="muted">正在读取机器人数据目录…</p>';
 document.querySelectorAll('[data-library-folder]').forEach(el=>el.addEventListener('toggle',()=>libraryOpen.set(el.dataset.libraryFolder,el.open)));
 document.querySelectorAll('[data-export-group]').forEach(el=>{
  const g=groups.get(el.dataset.exportGroup);el.indeterminate=!!g&&g.chosen>0&&g.chosen<g.count;
  el.addEventListener('click',e=>e.stopPropagation());
  el.addEventListener('change',()=>selectLibraryGroup(el.dataset.exportGroup,el.checked));
 });
 document.querySelectorAll('[data-export-file]').forEach(el=>el.addEventListener('change',()=>{
  el.checked?exportSelection.add(el.dataset.exportFile):exportSelection.delete(el.dataset.exportFile);renderRobotFiles();
 }));
 updateExportControls();
}
function refreshRobotFiles(){
 if(robotFilesRefresh)return robotFilesRefresh;
 robotFilesRefresh=(async()=>{
  const result=await api('/api/library/tree');
  if(result.root!==libraryRoot){libraryOpen.clear();exportSelection.clear();}
  libraryRoot=result.root;libraryTree=result.tree;robotFileRows=libraryTree?folderEpisodes(libraryTree):[];
  $('#libraryRoot').value=libraryRoot||'';renderRobotFiles();
  if($('#exportProgress').textContent.startsWith('目录列表读取失败：'))$('#exportProgress').textContent='目录列表已恢复，可选择记录进行导出。';
 })().catch(error=>{$('#exportProgress').textContent='目录列表读取失败：'+error.message+'。可点击“刷新记录”重试。';throw error;}).finally(()=>{robotFilesRefresh=null;});
 return robotFilesRefresh;
}
async function chooseExportDirectory(){
 if(!directoryPickerAvailable){
  $('#exportProgress').textContent='此浏览器使用下载保存：点击“下载打包文件”，在浏览器下载/另存为窗口选择位置；也可在浏览器设置中启用“下载前询问保存位置”。';return;
 }
 try{exportDirectory=await window.showDirectoryPicker({id:'r2-data-export',mode:'readwrite'});$('#exportFolder').textContent=exportDirectory.name+' / 保留原目录结构';}
 catch(e){if(e.name!=='AbortError')throw e;}
}
async function streamToFile(url,fileHandle,onBytes,signal){
 const response=await fetch(url,{signal});if(!response.ok)throw Error('下载失败：HTTP '+response.status);
 const writable=await fileHandle.createWritable(),reader=response.body.getReader();
 try{while(true){const {done,value}=await reader.read();if(done)break;await writable.write(value);onBytes(value.length);}await writable.close();}
 catch(e){await writable.abort().catch(()=>{});await reader.cancel().catch(()=>{});throw e;}
}
async function existingExportFile(relativePath){
 const parts=relativePath.split('/');let folder=exportDirectory;
 try{for(const part of parts.slice(0,-1))folder=await folder.getDirectoryHandle(part);await folder.getFileHandle(parts[parts.length-1]);return true;}
 catch(e){if(e.name==='NotFoundError')return false;throw e;}
}
async function exportToDirectory(){
 if(!directoryPickerAvailable)return downloadArchive();
 if(!exportDirectory)await chooseExportDirectory();if(!exportDirectory)return;
 exportBusy=true;exportAbort=new AbortController();updateExportControls();
 const episodePaths=[...exportSelection];
 try{
  const manifest=await api('/api/export/prepare',{episode_paths:episodePaths});
  for(const entry of manifest.files)if(await existingExportFile(entry.relative_path))throw Error('目标文件已存在：'+entry.relative_path+'。请选择其他目录，避免覆盖已导出数据。');
  const folders=new Map([['',exportDirectory]]);let bytes=0;const began=performance.now();
  for(const entry of manifest.files){
   if(exportAbort.signal.aborted)throw new DOMException('导出已取消','AbortError');
   const parts=entry.relative_path.split('/');let folder=exportDirectory,key='';
   for(const part of parts.slice(0,-1)){key=key?key+'/'+part:part;if(!folders.has(key))folders.set(key,await folder.getDirectoryHandle(part,{create:true}));folder=folders.get(key);}
   const handle=await folder.getFileHandle(parts[parts.length-1],{create:true});
   await streamToFile(entry.url,handle,n=>{bytes+=n;const seconds=Math.max(.1,(performance.now()-began)/1000);$('#exportProgress').textContent='正在导出 '+entry.relative_path+' · '+fmt(bytes/1048576,1)+' / '+fmt(manifest.total_bytes/1048576,1)+' MB · '+fmt(bytes/1048576/seconds,1)+' MB/s';},exportAbort.signal);
  }
  $('#exportProgress').textContent='导出完成：'+episodePaths.length+' 条记录，保存到 '+exportDirectory.name+'，保留采集文件夹层级。机器人原始文件已保留。';
 }catch(e){$('#exportProgress').textContent=e.name==='AbortError'?'导出已取消；已保存的文件保留在所选目录。':'导出失败：'+e.message;}
 finally{exportBusy=false;exportAbort=null;updateExportControls();}
}
async function downloadArchive(){
 exportBusy=true;updateExportControls();
 try{const manifest=await api('/api/export/prepare',{episode_paths:[...exportSelection]});const link=document.createElement('a');link.href=manifest.archive_url;link.download=manifest.filename;document.body.appendChild(link);link.click();link.remove();$('#exportProgress').textContent='已交给浏览器下载：'+manifest.filename+'。在浏览器中查看进度、选择保存位置或取消；解包后保留采集文件夹与 episode 层级。';}
 finally{exportBusy=false;updateExportControls();}
}
function requestEpisodeDelete(){
 pendingDelete=[...exportSelection];if(!pendingDelete.length)return;
 $('#deleteSummary').textContent='将永久删除机器人中的 '+pendingDelete.length+' 条记录，释放约 '+fmt(selectedBytes()/1048576,1)+' MB。已导出到电脑的文件不受影响。';
 $('#deleteList').textContent=pendingDelete.join('\n');$('#deleteEpisodeDialog').showModal();
}
async function deleteEpisodes(){
 if(!pendingDelete.length)return;
 const paths=[...pendingDelete];exportBusy=true;$('#confirmDeleteEpisodes').disabled=true;updateExportControls();
 try{
  const result=await api('/api/library/delete',{episode_paths:paths,confirm:true});
  const deleted=result.deleted||paths;
  if(typeof selected!=='undefined'&&selected&&deleted.includes((current?.data_dir||'').replace(/\/$/,'')+'/'+selected)){
   selected=null;$('#selectedFile').textContent='选择一条 episode';$('#replayInfo').textContent='所选记录已删除';
   ['play','pause','validate','plan','execute'].forEach(id=>$('#'+id).disabled=true);
   $('#previewTopics').textContent='请选择一条记录';$('#replayTargets').textContent='请选择一条记录';setSource('live');
  }
  exportSelection.clear();pendingDelete=[];$('#deleteEpisodeDialog').close();await refreshRobotFiles();if(typeof refreshFiles==='function')await refreshFiles();
  $('#exportProgress').textContent='已删除 '+result.deleted_count+' 条机器人记录，释放 '+fmt(result.freed_bytes/1048576,1)+' MB。';
 }catch(e){$('#deleteEpisodeDialog').close();$('#exportProgress').textContent='删除失败：'+e.message;}
 finally{exportBusy=false;$('#confirmDeleteEpisodes').disabled=false;updateExportControls();}
}
action('#chooseRobotDir',async()=>{directoryTarget='robot';$('#createDir').hidden=false;$('#directoryTitle').textContent='选择机器人本机保存目录';$('#directoryDescription').textContent='选定文件夹后可连续采集，每次结束保留目录，下次开始递增 episode 序号。';await browse(current?.recording.data_dir);$('#directoryDialog').showModal();});
action('#chooseLibraryRoot',async()=>{directoryTarget='library';$('#createDir').hidden=true;$('#directoryTitle').textContent='选择导出 / 删除的机器人目录';$('#directoryDescription').textContent='展开此目录中的采集文件夹，批量选择全部或部分 episode；不会改变正在使用的采集目录。';await browse(libraryRoot||current?.data_dir);$('#directoryDialog').showModal();});
action('#useCaptureRoot',async()=>{const path=current?.recording.data_dir||current?.data_dir;if(!path)throw Error('尚未取得采集目录');await api('/api/library/root',{path});await refreshRobotFiles();});
action('#refreshRobotFiles',refreshRobotFiles);action('#chooseExportFolder',chooseExportDirectory);
action('#exportAll',()=>{exportSelection=new Set(robotFileRows.filter(r=>r.category==='collection').map(r=>r.id));renderRobotFiles();});
action('#exportNone',()=>{exportSelection.clear();renderRobotFiles();});action('#exportSelected',()=>typeof runSelectedExport==='function'?runSelectedExport():exportToDirectory());action('#exportArchive',()=>typeof runSelectedArchive==='function'?runSelectedArchive():downloadArchive());action('#cancelExport',()=>typeof cancelSelectedExport==='function'?cancelSelectedExport():exportAbort?.abort());
action('#deleteSelected',requestEpisodeDelete);action('#cancelDeleteEpisodes',()=>{pendingDelete=[];$('#deleteEpisodeDialog').close();});action('#confirmDeleteEpisodes',deleteEpisodes);
$('#exportComputer').textContent='导出目标：当前访问电脑（'+clientComputer()+'）';
$('#exportMethodNote').textContent=directoryPickerAvailable?'勾选文件夹可批量导出，展开后可选具体 episode；导出保留原来的文件夹层级和原始 MCAP。':'勾选文件夹或具体 episode 后下载 TAR 包。请在浏览器下载设置中启用“每次询问保存位置”；解包后保留原目录层级。';
if(!directoryPickerAvailable){$('#chooseExportFolder').textContent='保存位置说明';$('#exportSelected').textContent='导出所选记录 (.tar)';$('#exportFolder').textContent='由浏览器下载/另存为窗口选择';}
refreshRobotFiles().catch(()=>{});setInterval(()=>{if(!exportBusy)refreshRobotFiles().catch(()=>{});},10000);
