/*
使用说明
用途：前端离线回归测试：replay_source界面交互。
用法：在studio目录执行 node tests/replay_source_ui_test.cjs；使用虚拟DOM/API，不启动网页或机器人任务。
详细说明：../README.md
*/
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const nodes=new Map(),calls=[],saved=new Map([['datacp.lerobotReplayRoot','/independent/replay']]);let output='/conversion/output';
const node=k=>{if(!nodes.has(k))nodes.set(k,{value:'',textContent:'',innerHTML:'',hidden:false,disabled:false,handlers:{},addEventListener(t,f){this.handlers[t]=f},showModal(){}});return nodes.get(k);};
const ctx={URLSearchParams,console,JSON,Date,Math,Map,Set,Array,Object,Number,String,setInterval(){},$:node,esc:String,fmt:(x,n)=>Number(x).toFixed(n),clock:String,notice(){},document:{querySelectorAll:()=>[]},localStorage:{getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v)},current:{data_dir:'/capture'},directoryTarget:'',browse:async()=>{},action:(k,f)=>node(k).handlers.click=f,api:async(path,body)=>{
 calls.push({path,body});
 if(path==='/api/workflow/state')return {defaults:{lerobot_root:output},conversion:{},replay:{}};
 if(path.startsWith('/api/library/tree'))return {root:'/capture',tree:null};
 if(path.startsWith('/api/lerobot/datasets')){const root=new URL(path,'http://test').searchParams.get('root');return {datasets:root==='/none'?[]:[{path:root+'/task',name:'task',total_episodes:root==='/empty'?0:1,fps:30}]};}
 if(path.startsWith('/api/lerobot/episodes'))return {fps:30,episodes:path.includes('%2Fempty')?[]:[{episode_index:0,frames:30,duration_s:1}],conversion_status:{dataset_path:'/empty/task',resume_run_dir:'/runtime/conversion_single',source_paths:['/raw/original'],source_count:1,phase:'已中断',progress:{processed_frames:7812,total_frames:16650},resume_request:{episode_paths:['/raw/original'],output_dir:'/empty',dataset_name:'task',task:'move',resume:true,workers:2,threads:2}}};
 return {};
}};
node('#conversionMode').value='selection';vm.createContext(ctx);vm.runInContext(fs.readFileSync('static/workflow.js','utf8'),ctx);
(async()=>{
 await ctx.refreshWorkflow();await ctx.refreshLerobotDatasets();
 assert.equal(node('#lerobotRoot').value,'/independent/replay');assert.equal(node('#lerobotDataset').value,'/independent/replay/task');assert.equal(node('#lerobotExportDataset').value,'/conversion/output/task');
 output='/conversion/other';ctx.applyWorkflowDefaults({lerobot_root:output});await ctx.refreshLerobotDatasets();
 assert.equal(node('#lerobotDataset').value,'/independent/replay/task');assert.equal(node('#lerobotEpisode').value,'0');assert.equal(node('#lerobotExportDataset').value,'/conversion/other/task');
 node('#lerobotRoot').value='';ctx.applyWorkflowDefaults({lerobot_root:output});assert.equal(node('#lerobotRoot').value,'','polls must not overwrite a directory being edited');
 await ctx.useLerobotReplayRoot('/external/dataset');assert.equal(node('#lerobotDataset').value,'/external/dataset/task');assert.equal(node('#conversionOutput').value,output);assert.equal(node('#lerobotExportDataset').value,'/conversion/other/task');assert.equal(saved.get('datacp.lerobotReplayRoot'),'/external/dataset');
 await ctx.useLerobotReplayRoot('/empty');assert.match(node('#lerobotSummary').textContent,/已中断.*7812 \/ 16650/);assert.equal(node('#lerobotResumeConversion').hidden,false);assert.equal(node('#lerobotExecute').disabled,true);
 await ctx.useLerobotReplayRoot('/none');assert.equal(node('#lerobotResumeConversion').hidden,true,'empty new source must clear the previous resume action');assert.equal(node('#lerobotExecute').disabled,true);await ctx.useLerobotReplayRoot('/empty');
 assert(calls.every(c=>c.body===undefined),'changing source and reading metadata cannot start any worker or control robot');
 await node('#lerobotResumeConversion').handlers.click();const resume=calls.find(c=>c.path==='/api/conversion/resume');assert.deepEqual(JSON.parse(JSON.stringify(resume.body)),{run_dir:'/runtime/conversion_single',dataset:'/empty/task'});assert(!calls.some(c=>c.path.includes('/replay/start')));
 ctx.renderWorkflow({conversion:{running:true,progress:{phase:'完成',percent:100}},replay:{}});assert.equal(node('#conversionPercent').textContent,'99.9%');assert(!node('#conversionOutcome').textContent.includes('转换完成'),'progress alone must not confirm a usable episode');
 console.log('PASS replay uses its own persisted root, preserves edits, leaves conversion/export roots unchanged, and explains/resumes interrupted conversion only on explicit click');
})().catch(e=>{console.error(e);process.exitCode=1});
