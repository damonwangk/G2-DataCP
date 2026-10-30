"use strict";
const limitLabel=x=>({normal:'范围内',near:'接近边界',limit:'到达端点 · SDK 范围内',over:'超出 SDK 范围',stale:'反馈已过期',unknown:'未收到有效位置'}[x?.status]||'未知');
const limitColor=x=>x?.status==='over'?'over':['near','limit'].includes(x?.status)?'near':['stale','unknown'].includes(x?.status)?'stale':'normal';
const limitValue=x=>x===null||x===undefined?'未提供':String(x);
function limitCaption(x){return x.name+'：'+limitValue(x.position)+'（'+x.lower+' ～ '+x.upper+' '+x.unit+'）';}
function renderSDKLimits(limits){
 const items=limits?.items||[],alerts=limits?.alerts||[],recent=limits?.recent_over||[],el=$('#limitNotice');
 const over=alerts.filter(x=>x.status==='over'),near=alerts.filter(x=>x.status!=='over'),missing=items.filter(x=>!x.fresh||x.status==='unknown');
 el.className='limit-notice '+(over.length?'over':near.length?'near':!limits?.fresh?'stale':'normal');
 el.textContent=over.length?'超出 SDK 范围 · '+over.map(limitCaption).join('；'):near.length?'接近 / 到达边界 · '+near.map(limitCaption).join('；'):limits?.fresh?'实时位置均在 SDK 范围内':'等待实时位置 / 部分反馈已过期';
 if(missing.length)el.textContent+=' · '+missing.length+' 项反馈缺失或过期';
 $('#limitRecent').hidden=!recent.length;
 $('#limitRecent').textContent=recent.length?'最近 5 秒超限（原始数据照常保存） · '+recent.map(x=>limitCaption(x)+'，'+fmt(x.age_s,1)+' 秒前').join('；'):'';
 $('#sdkLimitTable').innerHTML='<table class="sdk-limit-table"><thead><tr><th>关节 / 夹爪</th><th>原始位置</th><th>SDK 接受范围</th><th>状态</th></tr></thead><tbody>'+items.map(x=>'<tr class="'+limitColor(x)+'"><td><code>'+esc(x.name)+'</code></td><td>'+esc(limitValue(x.position))+'</td><td>'+x.lower+' ～ '+x.upper+' <small>'+esc(x.unit)+'</small></td><td>'+esc(limitLabel(x))+'</td></tr>').join('')+'</tbody></table>';
}
function renderControlCheck(r){
 const box=$('#controlCheckResult'),stateNames={IDLE:'未显示遥操 / 模型接管',TAKEOVER:'遥操接管',BACKWARD:'回退控制',MODEL:'模型控制'};
 box.hidden=false;box.className='control-check-result '+r.status;
 const route=Object.values(r.routes||{}).every(Boolean)?'两段通道均已注册':'通道不完整或无法读取';
 const a=r.arbitrator||{},source=r.action_source?.status;
 box.innerHTML='<strong>'+esc(r.title)+'</strong><span class="check-time">检测于 '+esc(r.checked_at?.replace('T',' '))+'</span><dl>'+
 '<dt>控制来源</dt><dd>'+esc(stateNames[source]||'未知')+'</dd>'+
 '<dt>仲裁门槛 / GDK 优先级</dt><dd>'+esc(a.floor??'未知')+' / '+esc(a.gdk_priority??'未知')+(r.arbitration_floor_blocks_gdk?' · GDK 指令会被仲裁拦下':'')+'</dd>'+
 '<dt>SDK → 仲裁 → 运控</dt><dd>'+esc(route)+'</dd>'+
 '<dt>实时反馈 / 运控模式</dt><dd>'+(r.feedback_fresh?'新鲜':'未就绪或过期')+' / '+esc(r.motion_control_mode??'未知')+'</dd>'+
 '</dl><p>'+esc(r.note)+'</p>'+
 (r.reported_errors?.length?'<p>反馈错误：'+esc(r.reported_errors.join('；'))+'</p>':'')+
 (r.read_errors?.length?'<p>未读到：'+esc(r.read_errors.join('；'))+'</p>':'');
}
action('#checkControl',async()=>{
 const button=$('#checkControl'),box=$('#controlCheckResult');button.disabled=true;box.hidden=false;box.className='control-check-result';box.textContent='正在只读检测控制来源和通道…';
 try{renderControlCheck(await api('/api/control/check'));}
 catch(e){box.textContent='检测未完成：'+e.message+'。采集、回放和姿态恢复按钮不受影响。';}
 finally{button.disabled=false;}
});
