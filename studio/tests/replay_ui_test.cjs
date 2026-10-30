const fs=require('fs'),vm=require('vm'),assert=require('assert');
const nodes=new Map();
const node=key=>{if(!nodes.has(key))nodes.set(key,{textContent:'',innerHTML:'',value:'',hidden:false,disabled:false,dataset:{},style:{},classList:{toggle(){},add(){},remove(){}},handlers:{},addEventListener(k,f){this.handlers[k]=f},querySelectorAll(){return []}});return nodes.get(key)};
const ctx={document:{querySelector:node,querySelectorAll:()=>[]},console,Date,JSON,Math,Number,String,Array,Object,Promise,AbortSignal,
 fetch:()=>new Promise(()=>{}),setTimeout(){},setInterval(){},requestAnimationFrame(){},devicePixelRatio:1};
vm.createContext(ctx);vm.runInContext(fs.readFileSync('static/app.js','utf8'),ctx);
const base={source:'live',samples:{},streams:[],recording:{active:false,bytes:0},playback:{},limits:{fresh:false},robot_replay:{kind:'posture',running:false,phase:'姿态恢复结束',report:{kind:'posture',completed:true}}};
let failures=0;
async function check(name,fn){try{await fn();console.log('PASS',name)}catch(e){failures++;console.error('FAIL',name,e.message)}}
(async()=>{
 await check('posture report does not replace replay preparation text',()=>{node('#replayInfo').textContent='准备检查所选 episode';ctx.render(base);assert.equal(node('#replayInfo').textContent,'准备检查所选 episode');assert.match(node('#poseInfo').textContent,/姿态恢复结束/)});
 await check('preflight failure appears in replay panel',async()=>{vm.runInContext("selected='episode_1_test'",ctx);ctx.fetch=async()=>({ok:false,json:async()=>({detail:'关节/夹爪反馈间隔超过 200 ms'})});await node('#plan').handlers.click();assert.match(node('#replayInfo').textContent,/200 ms/)});
 await check('successful plan stays visible through unchanged failed-job polls',async()=>{
  vm.runInContext("selected='episode_1_test'",ctx);
  const failed={...base,robot_replay:{kind:'replay',file:'episode_1_test',phase:'回放准备失败',error:'old failure',running:false}};
  ctx.render(failed);
  ctx.fetch=async()=>({ok:true,json:async()=>({duration_s:163,ready:true})});
  await node('#plan').handlers.click();ctx.render(failed);
  assert.match(node('#replayInfo').textContent,/163/);assert.doesNotMatch(node('#replayInfo').textContent,/old failure/);
 });
 await check('another episode report cannot replace selected replay text',()=>{
  node('#replayInfo').textContent='selected episode';ctx.render({...base,robot_replay:{kind:'replay',file:'different_episode',phase:'回放结束',running:false}});
  assert.equal(node('#replayInfo').textContent,'selected episode');
 });

 await check('paused replay is visible without selecting its file and explains restore lock',()=>{
  vm.runInContext("selected=null;lastReplaySignature=''",ctx);
  node('#poseSelect').value='pose_1';
  ctx.render({...base,recording:{active:true,bytes:123,file:'test_replay_feedback'},robot_replay:{kind:'replay',file:'episode_1_test',running:true,paused:true,phase:'暂停（保持当前目标）'}});
  assert.equal(node('#restorePose').disabled,true);
  assert.match(node('#restorePoseHint').textContent,/回放已暂停/);
  assert.match(node('#restorePoseHint').textContent,/结束当前执行/);
  assert.match(node('#replayInfo').textContent,/暂停/);
  assert.match(node('#replayInfo').textContent,/episode_1_test/);
  assert.match(node('#recordStatus').textContent,/执行反馈/);
 });
 await check('completed job immediately unlocks restore for selected pose',()=>{
  ctx.render({...base,robot_replay:{kind:'replay',running:false,phase:'已停止',file:'episode_1_test'}});
  assert.equal(node('#restorePose').disabled,false);
  assert.equal(node('#restorePoseHint').textContent,'');
 });
 await check('active recording explains why restore is unavailable',()=>{
  ctx.render({...base,recording:{active:true,bytes:1,file:'episode_2_test'},robot_replay:{running:false}});
  assert.match(node('#restorePoseHint').textContent,/结束并保存/);
 });
 await check('pose selection updates controls without waiting for next poll',()=>{
  ctx.render({...base,robot_replay:{running:false}});
  node('#poseSelect').value='';
  node('#poseSelect').handlers.change();
  assert.equal(node('#restorePose').disabled,true);
  node('#poseSelect').value='pose_1';
  node('#poseSelect').handlers.change();
  assert.equal(node('#restorePose').disabled,false);
 });

 await check('next episode enabled only for regular active capture',()=>{
  ctx.render({...base,recording:{active:false},robot_replay:{running:false}});assert.equal(node('#nextRecord').disabled,true);
  ctx.render({...base,recording:{active:true},robot_replay:{running:false}});assert.equal(node('#nextRecord').disabled,false);
  ctx.render({...base,recording:{active:true},robot_replay:{running:true,kind:'replay'}});assert.equal(node('#nextRecord').disabled,true);
 });
 await check('next episode sends selected data to recorder only',async()=>{
  const calls=[];node('#task').value='take';node('#poseSelect').value='pose';ctx.selectedRecordingTopics=()=>['/hal/joint_state'];
  ctx.fetch=async(path,opts)=>{calls.push({path,body:opts.body?JSON.parse(opts.body):null});return {ok:true,json:async()=>path==='/api/record/next'?{saved:{file:'episode_1_time'},recording:{file:'episode_2_time'}}:{files:[],counts:{}}}};
  await node('#nextRecord').handlers.click();assert.deepEqual(calls[0],{path:'/api/record/next',body:{label:'take',pose_id:'pose',topics:['/hal/joint_state']}});
  assert(calls.every(c=>!c.path.includes('/api/replay/')&&!c.path.includes('/api/poses/')));assert.match(node('#notice').textContent,/episode_2_time/);
 });
 process.exitCode=failures?1:0;
})();
