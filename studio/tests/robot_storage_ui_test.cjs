
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const nodes=new Map(),lists=new Map();
const node=key=>{if(!nodes.has(key))nodes.set(key,{textContent:'',innerHTML:'',value:'',hidden:false,disabled:false,dataset:{},style:{},classList:{toggle(){},add(){},remove(){}},handlers:{},addEventListener(k,f){this.handlers[k]=f},querySelectorAll(){return []},showModal(){},close(){}});return nodes.get(key)};
const ctx={document:{querySelector:node,querySelectorAll:s=>lists.get(s)||[]},console,Date,JSON,Math,Number,String,Array,Object,Map,Promise,AbortSignal,localStorage:{getItem:()=>null,setItem(){}},fetch:path=>path==='/api/library/tree'?Promise.resolve({ok:true,json:async()=>({root:'/data/data',tree:null})}):new Promise(()=>{}),setTimeout(){},setInterval(){},requestAnimationFrame(){},devicePixelRatio:1};
vm.createContext(ctx);
for(const f of ['static/app.js','static/selection.js','static/robot_storage.js'])vm.runInContext(fs.readFileSync(f,'utf8'),ctx);
(async()=>{
 await ctx.refreshRobotFiles();
 const s={data_dir:'/ubuntu/data',streams:[{topic:'/camera/head_color',online:true,hz:999}],recording:{storage_host:'robot',connected:true,data_dir:'/data/data',free_gb:100,active:false,streams:[{topic:'/camera/head_color',online:true,hz:30}]},robot_replay:{running:false},robot_transfers:[]};
 ctx.stateFixture=s;vm.runInContext("current=stateFixture;selected='keep-episode'",ctx);
 ctx.renderRobotStorage(s);assert.equal(node('#robotSaveDir').value,'/data/data');
 const requests=[];
 ctx.fetch=async(path,opts)=>{
  requests.push({path,body:opts.body?JSON.parse(opts.body):null});
  const result=(path.startsWith('/api/robot-storage/directories')||path.startsWith('/api/library/directories'))?{path:'/data/data',parent:'/data',folders:[],free_gb:100}:path==='/api/robot-storage/directory'?{data_dir:'/data/chosen'}:{files:[]};
  return {ok:true,json:async()=>result};
 };
 await node('#chooseRobotDir').handlers.click();
 assert.match(requests[0].path,/^\/api\/robot-storage\/directories/);
 node('#folderPath').value='/data/chosen';await node('#createDir').handlers.click();
 assert.deepEqual(requests.find(r=>r.path==='/api/robot-storage/directory').body,{path:'/data/chosen',create:true});
 assert.equal(vm.runInContext('selected',ctx),null);
 assert.equal(node('#saveDir').value,'/data/chosen');
 vm.runInContext("selected='retain-current'",ctx);await node('#chooseLibraryRoot').handlers.click();assert.equal(node('#createDir').hidden,true);await node('#useDir').handlers.click();assert(requests.some(r=>r.path.startsWith('/api/library/directories')));assert.deepEqual(requests.find(r=>r.path==='/api/library/root').body,{path:'/data/data'});assert.equal(vm.runInContext('selected',ctx),'retain-current');
 const conversionScopes=[];ctx.refreshConversionFiles=async path=>conversionScopes.push(path);
 const beforeConversion=requests.length,priorSaveDir=node('#saveDir').value;
 vm.runInContext("directoryTarget='conversion'",ctx);await ctx.browse('/data/conversion-input');await node('#useDir').handlers.click();
 assert.deepEqual(conversionScopes,['/data/data']);assert(requests.slice(beforeConversion).every(r=>r.path.startsWith('/api/library/directories')&&r.body===null),'conversion picker only browses, never writes capture/export settings');
 assert.equal(node('#saveDir').value,priorSaveDir);assert.equal(vm.runInContext('selected',ctx),'retain-current');
 const rate={dataset:{receiveRate:'/camera/head_color'}};
 lists.set('[data-receive-rate]',[rate]);s.recording.active=true;
 ctx.renderDataRates(s);assert.equal(rate.textContent,'30.0 Hz');
 console.log('PASS robot directory routing, independent read-only conversion directory, library scope does not change collection or preview, displayed acquisition rate comes from robot');
})().catch(e=>{console.error(e);process.exitCode=1});
