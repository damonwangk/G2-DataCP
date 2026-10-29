const fs=require('fs'),vm=require('vm'),assert=require('assert');
const elements=new Map(),links=[],requests=[],writes=[],mkdirs=[];
function node(k){if(!elements.has(k))elements.set(k,{textContent:'',innerHTML:'',value:'',disabled:false,hidden:false,handlers:{},addEventListener(t,f){this.handlers[t]=f},showModal(){this.open=true},close(){this.open=false}});return elements.get(k)}
const notFound=()=>Object.assign(Error('missing'),{name:'NotFoundError'});
const raw=Uint8Array.from([1,2,0,255,6]);
const a1={id:'/data/data/A/episode_1_time',file:'episode_1_time',category:'collection',bytes:5,total_bytes:7};
const a2={id:'/data/data/A/episode_2_time',file:'episode_2_time',category:'collection',bytes:5,total_bytes:7};
const feedback={id:'/data/data/B/feedback',file:'feedback',category:'feedback',bytes:2,total_bytes:2};
let tree={name:'data',path:'/data/data',episodes:[],groups:[{name:'A',path:'/data/data/A',episodes:[a1,a2],groups:[],total_bytes:14},{name:'B',path:'/data/data/B',episodes:[feedback],groups:[],total_bytes:2}],total_bytes:16};
const manifest={files:[{relative_path:'A/episode_1_time/raw.mcap',url:'/api/export/t/files/0',bytes:5},{relative_path:'A/episode_1_time/raw.json',url:'/api/export/t/files/1',bytes:2}],total_bytes:7,archive_url:'/api/export/t/archive',filename:'A.tar'};
const fileHandle=name=>({createWritable:async()=>({write:async b=>writes.push({name,bytes:[...b]}),close:async()=>writes.push({name,closed:true}),abort:async()=>{}})});
const leaf={getFileHandle:async(name,opts)=>{if(!opts)throw notFound();return fileHandle(name)}};
const existingGroup={getDirectoryHandle:async(name,opts)=>{if(!opts)throw notFound();mkdirs.push('A/'+name);return leaf}};
const destination={name:'Chosen-folder',getDirectoryHandle:async(name,opts)=>{assert.equal(name,'A');return existingGroup}};
const groupInputs=['/data/data','/data/data/A','/data/data/B'].map(path=>({dataset:{exportGroup:path,empty:'false'},handlers:{},addEventListener(k,f){this.handlers[k]=f}}));
const openFolders=[{dataset:{libraryFolder:'/data/data'},open:true,addEventListener(){}},{dataset:{libraryFolder:'/data/data/A'},open:false,addEventListener(){}}];
const ctx={console,Map,Set,Array,Object,String,Math,Date,JSON,Promise,Uint8Array,AbortController,DOMException,performance,
 navigator:{platform:'MacIntel'},window:{showDirectoryPicker:async()=>destination},current:{data_dir:'/data/data/A'},selected:'episode_1_time',
 document:{querySelectorAll:s=>s==='[data-library-folder]'?openFolders:s==='[data-export-group]'||s==='[data-export-file],[data-export-group]'?groupInputs:[],body:{appendChild(){}},createElement:()=>({click(){links.push({href:this.href,download:this.download})},remove(){}})},
 $:node,fmt:(n,d)=>Number(n).toFixed(d),esc:String,notice(){},setInterval(){},setSource:s=>requests.push({source:s}),refreshFiles:async()=>requests.push({refresh:true}),
 action:(s,f)=>node(s).handlers.click=f,
 api:async(p,b)=>{requests.push({p,b});if(p.includes('/prepare'))return manifest;if(p==='/api/library/delete'){tree.groups[0].episodes=tree.groups[0].episodes.filter(r=>!b.episode_paths.includes(r.id));return {deleted:b.episode_paths,deleted_count:b.episode_paths.length,freed_bytes:7};}return {root:'/data/data',tree}},
 fetch:async()=>({ok:true,body:new ReadableStream({start(c){c.enqueue(raw);c.close()}})})};
vm.createContext(ctx);vm.runInContext(fs.readFileSync('static/robot_storage.js','utf8'),ctx);
(async()=>{
 await ctx.refreshRobotFiles();assert.match(node('#exportComputer').textContent,/macOS/);
 ctx.selectLibraryGroup('/data/data/A',true);assert.match(node('#exportCount').textContent,/已选 2 条/);assert.equal(groupInputs[0].indeterminate,true);assert.equal(groupInputs[1].indeterminate,false);
 vm.runInContext("exportSelection.delete('/data/data/A/episode_2_time');renderRobotFiles()",ctx);assert.equal(groupInputs[1].indeterminate,true);
 openFolders[1].open=true;await ctx.refreshRobotFiles();assert.match(node('#robotFiles').innerHTML,/data-library-folder="\/data\/data\/A" open/);assert.match(node('#exportCount').textContent,/已选 1 条/);
 await node('#chooseExportFolder').handlers.click();await node('#exportSelected').handlers.click();
 assert.deepEqual(mkdirs,['A/episode_1_time']);assert.deepEqual(writes.filter(x=>x.bytes).map(x=>x.bytes),[[1,2,0,255,6],[1,2,0,255,6]]);assert.match(node('#exportProgress').textContent,/导出完成：1 条/);
 assert.deepEqual(Array.from(requests.find(r=>r.p==='/api/export/prepare').b.episode_paths),[a1.id]);
 await node('#exportArchive').handlers.click();assert.deepEqual(links,[{href:'/api/export/t/archive',download:'A.tar'}]);
 const before=requests.filter(r=>r.p==='/api/library/delete').length;await node('#deleteSelected').handlers.click();assert(node('#deleteEpisodeDialog').open);assert.match(node('#deleteSummary').textContent,/永久删除机器人中的 1 条/);assert.equal(requests.filter(r=>r.p==='/api/library/delete').length,before);
 await node('#cancelDeleteEpisodes').handlers.click();assert.equal(requests.filter(r=>r.p==='/api/library/delete').length,before);
 await node('#deleteSelected').handlers.click();await node('#confirmDeleteEpisodes').handlers.click();assert.equal(requests.filter(r=>r.p==='/api/library/delete').length,before+1);assert.equal(ctx.selected,null);assert.match(node('#exportProgress').textContent,/已删除 1 条/);
 assert(requests.every(r=>!r.p||!/(record|replay|poses)\//.test(r.p)));
 leaf.getFileHandle=async()=>fileHandle('existing');existingGroup.getDirectoryHandle=async()=>leaf;await ctx.exportToDirectory();assert.match(node('#exportProgress').textContent,/目标文件已存在/);assert.equal(writes.filter(x=>x.bytes).length,2);
 console.log('PASS nested folder selection, partial state, expansion persistence, recursive original-file export, confirmed deletion and selected preview reset; no robot control');
})().catch(e=>{console.error(e);process.exitCode=1});
