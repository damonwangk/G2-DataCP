
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const nodes=new Map();const node=key=>{if(!nodes.has(key))nodes.set(key,{textContent:'',innerHTML:'',value:'',hidden:false,disabled:false,style:{},classList:{toggle(){}},handlers:{},addEventListener(k,f){this.handlers[k]=f},querySelectorAll(){return []}});return nodes.get(key)};
const ctx={document:{querySelector:node,querySelectorAll:()=>[]},console,Date,JSON,Math,Number,String,Array,Object,Promise,AbortSignal,fetch:()=>new Promise(()=>{}),setTimeout(){},setInterval(){},requestAnimationFrame(){},devicePixelRatio:1};
vm.createContext(ctx);for(const f of ['static/app.js','static/advisory.js'])vm.runInContext(fs.readFileSync(f,'utf8'),ctx);
(async()=>{
 const endpoint={name:'grip',position:.025,lower:0,upper:.025,unit:'native',status:'limit',fresh:true};
 const over={...endpoint,name:'right_grip',position:1.632,status:'over'};
 ctx.renderSDKLimits({fresh:true,items:[endpoint,over],alerts:[endpoint,over],recent_over:[{...over,age_s:.4}]});
 assert.match(node('#sdkLimitTable').innerHTML,/<tr class="near">/);assert.match(node('#sdkLimitTable').innerHTML,/<tr class="over">/);
 assert.equal(node('#limitRecent').hidden,false);assert.match(node('#limitRecent').textContent,/1.632/);
 node('#execute').disabled=false;node('#restorePose').disabled=false;
 let request;
 ctx.fetch=async(path,options)=>{request={path,options};return {ok:true,json:async()=>({status:'occupied',title:'检测到控制占用',routes:{a:true},arbitrator:{floor:100,gdk_priority:40},action_source:{status:'IDLE'},advisory_only:true,note:'只读'})}};
 await node('#checkControl').handlers.click();
 assert.equal(request.path,'/api/control/check');assert.equal(request.options.method,'GET');
 assert.equal(node('#execute').disabled,false);assert.equal(node('#restorePose').disabled,false);assert.equal(node('#checkControl').disabled,false);
 assert.match(node('#controlCheckResult').innerHTML,/100 \/ 40/);
 console.log('PASS yellow endpoint, red overshoot, transient reminder, GET-only diagnostics, no replay/restore gating');
})().catch(e=>{console.error(e);process.exitCode=1});
