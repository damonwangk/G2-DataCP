const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('static/index.html','utf8'),app=fs.readFileSync('static/app.js','utf8');
assert(!html.includes('id="fileCategory"'));
for(const id of ['mcapReplayPath','chooseMcapFile','readMcapSource','captureFilePath','openCapturedMcap'])assert(html.includes('id="'+id+'"'));
const nodes=new Map(),calls=[],actions=new Map(),saved=new Map();
function node(id){if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',innerHTML:'',disabled:false,hidden:false,scrollIntoView(){},showModal(){},close(){}});return nodes.get(id);}
const file='/data/other task/122episode_2_20260314_005050/raw.mcap';
const ctx={console,JSON,String,Number,Map,Promise,encodeURIComponent,
 $:node,esc:String,clock:String,selected:null,current:{data_dir:'/data/capture'},mcapReplayRoot:'',mcapReplayRequest:null,capturedMcapPath:file,lastReplaySignature:'',directoryTarget:'',
 localStorage:{setItem:(k,v)=>saved.set(k,v)},document:{querySelectorAll:()=>[]},notice(){},setSource(){},browse:async p=>calls.push({browse:p}),
 action:(id,fn)=>actions.set(id,fn),loadFileSelections:async p=>calls.push({inspect:p}),
 api:async(path,body)=>{calls.push({path,body});if(path.startsWith('/api/mcap/files')){const input=new URL(path,'http://local').searchParams.get('path');return {root:'/data/other task',path:input||'/data/other task',files:[{file:'122episode_2_20260314_005050',path:file,bytes:1024,label:'122episode',duration_s:2}]};}return {};}
};vm.createContext(ctx);vm.runInContext(app.slice(app.indexOf('async function loadMcapFile('),app.indexOf("action('#record'")),ctx);
(async()=>{
 await ctx.refreshFiles('/data/other task');assert.equal(node('#mcapReplayPath').value,'/data/other task');assert(node('#files').innerHTML.includes(file));
 node('#mcapReplayPath').value=file;await ctx.readMcapSource();assert.equal(ctx.selected,file);assert(node('#selectedFile').textContent.includes(file));
 assert.equal(calls.find(x=>x.path==='/api/playback/load').body.file,file);assert.equal(saved.get('datacp.mcapReplayPath'),file);
 assert(!calls.some(x=>x.path?.includes('/replay/start')||x.path?.includes('/directory')||x.path?.includes('/record/')),'file selection cannot move hardware, change capture settings or start recording');
 assert.equal(ctx.current.data_dir,'/data/capture');assert.equal(ctx.directoryTarget,'');
 await actions.get('#chooseMcapFile')();assert.equal(ctx.directoryTarget,'mcap-replay');assert.equal(node('#createDir').hidden,true);
 console.log('PASS explicit MCAP path selection, full capture/replay locations, independent source and read-only file loading');
})().catch(e=>{console.error(e);process.exitCode=1;});
