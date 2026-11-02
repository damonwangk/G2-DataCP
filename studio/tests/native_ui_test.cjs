/*
使用说明
用途：前端离线回归测试：native界面交互。
用法：在studio目录执行 node tests/native_ui_test.cjs；使用虚拟DOM/API，不启动网页或机器人任务。
详细说明：../README.md
*/
/* 原生回放界面回归：指令值及空值显示，空值不阻止显式执行。 */
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const nodes=new Map(),calls=[];let format='mcap';
function node(k){if(!nodes.has(k))nodes.set(k,{textContent:'',innerHTML:'',value:'',disabled:false,hidden:false,handlers:{},addEventListener(k,f){this.handlers[k]=f;},showModal(){},close(){}});return nodes.get(k);}
const native='/data/record/task/A';
const ctx={URLSearchParams,console,Map,Set,Array,Object,String,Math,Date,JSON,Promise,$:node,fmt:(n,d)=>Number(n).toFixed(d),esc:String,notice(){},setInterval(){},getExportFormat:()=>format,workflowState:{replay:{}},workflowLog:()=>'',refreshWorkflow:async()=>{},document:{querySelectorAll:()=>[],body:{appendChild(){}},createElement:()=>({click(){},remove(){}})},action:(s,f)=>node(s).handlers.click=f,api:async(path,body)=>{calls.push({path,body});if(path.startsWith('/api/library/tree'))return {root:format==='factory'||path.includes('factory')?'/data/record':'/data/data',tree:{path:'/data/record',name:'record',groups:[{path:'/data/record/task',name:'task',groups:[],episodes:[{id:native,file:'A',category:'collection',total_bytes:123}]}],episodes:[]}};if(path==='/api/native/inspect')return {frames:3,duration_s:.2,fps:30,names:['joint','gripper'],first_positions:[null,.025],first_sdk_command:[null,.025]};if(path==='/api/export/prepare')return {archive_url:'/archive',filename:'native.tar'};return {};}};
vm.createContext(ctx);for(const f of ['robot_storage.js','native.js'])vm.runInContext(fs.readFileSync('static/'+f,'utf8'),ctx);
(async()=>{
 await ctx.refreshRobotFiles();assert(calls.every(r=>r.body===undefined),'opening page only reads');
 await ctx.refreshNativeFiles();assert.equal(node('#nativeSource').value,native);assert.equal(node('#nativeExecute').disabled,false,'inspect is not a replay prerequisite');
 await node('#nativeInspect').handlers.click();assert.match(node('#nativeSummary').textContent,/3 帧/);assert.match(node('#nativeValues').textContent,/gripper  原始指令 0.025000 \/ SDK 命令 0.025000/);assert.match(node('#nativeValues').textContent,/joint  原始指令 空值 \/ SDK 命令 不发送新目标/);assert.equal(node('#nativeExecute').disabled,false);assert(!calls.some(r=>r.path.includes('/replay/')),'inspection does not start robot control');
 await node('#nativeExecute').handlers.click();assert.equal(calls.filter(r=>r.path==='/api/native/replay/start').length,1);assert.deepEqual(calls.find(r=>r.path==='/api/native/replay/start').body,{source:native});
 format='factory';await ctx.refreshRobotFiles();assert(calls.some(r=>r.path==='/api/library/tree?path=%2Fdata%2Frecord&input_format=factory'));
 ctx.selectLibraryGroup('/data/record',true);await ctx.downloadArchive();assert.deepEqual(JSON.parse(JSON.stringify(calls.find(r=>r.path==='/api/export/prepare').body)),{episode_paths:[native],input_format:'factory',root:'/data/record'});assert.equal(node('#rawDeleteTools').hidden,true);
 console.log('PASS native record inspection is read-only, explicit SDK replay is separate, original native directory export uses shared controls and selection');
})().catch(e=>{console.error(e);process.exitCode=1});
