/*
使用说明
用途：前端离线回归测试：library_refresh界面交互。
用法：在studio目录执行 node tests/library_refresh_ui_test.cjs；使用虚拟DOM/API，不启动网页或机器人任务。
详细说明：../README.md
*/
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const nodes=new Map(),requests=[],timers=[];let format='mcap';
function node(key){if(!nodes.has(key))nodes.set(key,{textContent:'',innerHTML:'',value:'',disabled:false,handlers:{},addEventListener(event,fn){this.handlers[event]=fn;}});return nodes.get(key);}
const ctx={getExportFormat:()=>format,URLSearchParams,console,Map,Set,Array,Object,String,Math,Date,JSON,Promise,$:node,esc:String,fmt:(n,d)=>Number(n).toFixed(d),notice(){},document:{querySelectorAll(){return [];}},setInterval(fn){timers.push(fn);},action:(selector,fn)=>node(selector).handlers.click=fn,api:path=>new Promise((resolve,reject)=>requests.push({path,resolve,reject}))};
vm.createContext(ctx);vm.runInContext(fs.readFileSync('static/robot_storage.js','utf8'),ctx);
const result={root:'/data/data',tree:{name:'data',path:'/data/data',episodes:[],groups:[],total_bytes:0}};
(async()=>{
 assert.equal(requests.length,1,'startup issues one library request');
 const first=ctx.refreshRobotFiles(),manual=node('#refreshRobotFiles').handlers.click();
 assert.strictEqual(first,manual,'manual refresh shares the in-flight promise');
 for(let i=0;i<20;i++){timers[0]();assert.strictEqual(ctx.refreshRobotFiles(),first);}
 assert.equal(requests.length,1,'slow request must not accumulate timer or manual requests');
 requests[0].resolve(result);await first;assert.equal(node('#libraryRoot').value,'/data/data');
 const failed=ctx.refreshRobotFiles(),same=ctx.refreshRobotFiles();assert.strictEqual(failed,same);assert.equal(requests.length,2);
 requests[1].reject(Error('connection lost'));await assert.rejects(failed,/connection lost/);assert.match(node('#exportProgress').textContent,/目录列表读取失败：connection lost/);
 const retry=node('#refreshRobotFiles').handlers.click();assert.equal(requests.length,3,'failed request releases the slot for retry');requests[2].resolve(result);await retry;assert.match(node('#exportProgress').textContent,/目录列表已恢复/);
 timers[0]();timers[0]();assert.equal(requests.length,4,'periodic refresh continues and merges duplicate polls');const periodic=ctx.refreshRobotFiles();requests[3].resolve(result);await periodic;
 assert(requests.every(request=>request.path==='/api/library/tree'));
 const old=ctx.refreshRobotFiles();format='factory';const changed=ctx.refreshRobotFiles();requests[4].resolve(result);await old;await Promise.resolve();assert.equal(requests.length,6,'switching input format during a refresh issues a follow-up request');assert.match(requests[5].path,/input_format=factory/);requests[5].resolve({root:'/data/record',tree:null});await changed;assert.equal(node('#libraryRoot').value,'/data/record');
 console.log('PASS concurrent manual/timer library refreshes share one request, errors are visible, and retry/periodic refresh recover');
})().catch(error=>{console.error(error);process.exitCode=1;});
