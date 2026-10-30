'use strict';
let inspectTab='grippers',inspectFrozen=false,inspectCache=null,inspectContract=null,inspectPaintAt=0,inspectJoint=null;
const inspectViews={grippers:'#inspectGrippers',joints:'#inspectJoints',images:'#inspectImages',other:'#inspectOther'};
const inspectTopics=[
 ['/hal/joint_cmd','关节动作目标','原生指令 position[] 是控制目标；motor_position[] 是实测状态。两者分别保存，不把实测位置冒充操作者动作。'],
 ['/wbc/left_ee_command','左夹爪动作目标','只读显示已接收的夹爪指令；有操作时可能更新，长时间不更新会显示最近一条指令的年龄。'],
 ['/wbc/right_ee_command','右夹爪动作目标','只读显示已接收的夹爪指令；未收到或过期不补为零，不推断当前开合量。'],
 ['/wbc/motion_control_status','末端位姿 / 速度 / 力','用于观察遥操结果：位置 xyz 与姿态四元数 xyzw；保留源参考系。不是下发控制的接口。'],
 ['/tf','本体姿态 TF','本体、头部、双臂及末端变换，用于姿态可视化；base_link 是参考系名，不是房间坐标中的底盘位置。']
];
const rawText=v=>v===undefined||v===null?'未提供':typeof v==='object'?JSON.stringify(v):String(v);
const typeText=f=>f.type+(f.repeated?'[]':'');
const sampleAge=e=>!e?'未收到':`${fmt(e.age_s,2)} s ${e.age_s>2?'· 最近样本已过期':''}`;
function fieldTable(fields,valueOf){return `<div class="data-scroll"><table class="field-table"><thead><tr><th>字段 / 类型</th><th>当前值</th><th>单位与用途</th></tr></thead><tbody>${fields.map(f=>`<tr><td><code>${esc(f.name)}</code><small>${esc(typeText(f))}</small></td><td class="data-num">${esc(rawText(valueOf(f)))}</td><td>${esc(f.unit)}<small>${esc(f.note)}</small></td></tr>`).join('')}</tbody></table></div>`;}
function stampLine(e){return e?`<div class="sample-stamp"><span>源时间 ns <code>${esc(e.meta?.source_ns)}</code></span><span>接收时间 ns <code>${esc(e.receive_unix_ns)}</code></span><span>SEQ ${esc(e.meta?.sequence)} · 样本年龄 ${esc(sampleAge(e))}</span></div>`:'<p class="muted">尚未收到消息</p>';}
function renderInspector(state,force=false){
 if(!state)return;if(!force&&inspectFrozen)return;if(!force&&Date.now()-inspectPaintAt<500)return;
 if(!inspectFrozen)inspectCache=state;inspectPaintAt=Date.now();const s=inspectCache||state;
 $('#inspectSource').textContent=`${s.source==='live'?'实时观测':'文件样本'} · ${inspectFrozen?'读数已冻结':'只读更新'}`;
 if(!inspectContract)return;
 if(inspectTab==='grippers')paintGripperData(s);
 if(inspectTab==='joints')paintJointData(s);
 if(inspectTab==='images')paintCameraData(s);
 if(inspectTab==='other')paintActionData(s);
}
function paintGripperData(s){
 const range=inspectContract.gripper_range;
 $('#gripperData').innerHTML=['left','right'].map((side,i)=>{
  const topic=`/hal/${side}_ee_data`,e=s.samples[topic],d=e?.data||{},motors=d.end_state||[],m=motors[0]||{},pos=m.position;
  const known=d.type===range.type;
  const example={name:d.name,type:d.type,end_state:motors.map(m=>Object.fromEntries(['position','velocity','effort','current','enable','status','err_code'].filter(k=>k in m).map(k=>[k,m[k]])))};
  return `<article class="grip-data-card"><h3>${i?'右':'左'}夹爪 <small>${esc(d.name?.join(', ')||'等待数据')}</small></h3><div class="grip-reading"><strong>${esc(rawText(pos))}</strong><span>原生开合值 · float64${known?' · dahuan':''}</span></div><p class="data-footnote">controlled=${esc(rawText(d.controlled))} · type=${esc(rawText(d.type))} · 消息 ${esc(sampleAge(e))}</p>${fieldTable(inspectContract.gripper_fields,f=>m[f.name])}<h4>采集字段样式（JSON 展示，文件内是 Protobuf）</h4><pre>${esc(JSON.stringify(example,null,2))}</pre>${stampLine(e)}</article>`;
 }).join('');
}
function paintJointData(s){
 const e=s.samples['/hal/joint_state'],d=e?.data||{},names=d.name||[],filter=$('#inspectJointGroup').value;
 const visible=names.map((name,i)=>({name,i})).filter(x=>!/(chassis|wheel|steer|traction)/i.test(x.name)&&(filter==='all'||x.name.includes('_'+filter+'_')));
 if(!visible.some(x=>x.name===inspectJoint))inspectJoint=visible[0]?.name;
 const val=(field,i)=>Array.isArray(d[field])&&d[field].length===names.length?d[field][i]:undefined;
 $('#jointDataStamp').textContent=`${visible.length} / ${names.length} 个关节 · ${sampleAge(e)}`;
 $('#jointDataTable').innerHTML=`<table class="all-joints"><thead><tr><th>关节名称</th><th>实测位置 rad</th><th>实测速度 rad/s</th><th>力矩</th><th>错误码</th></tr></thead><tbody>${visible.map(({name,i})=>`<tr class="${name===inspectJoint?'selected':''}"><td><button data-joint-name="${esc(name)}">${esc(name)}</button></td><td>${esc(rawText(val('motor_position',i)))}</td><td>${esc(rawText(val('motor_velocity',i)))}</td><td>${esc(rawText(val('effort',i)))}</td><td>${esc(rawText(val('error_code',i)))}</td></tr>`).join('')}</tbody></table>`;
 const i=names.indexOf(inspectJoint),fields=inspectContract.joint_fields.filter(f=>f.name!=='header'&&f.name!=='name');
 const example={name:inspectJoint,motor_position:val('motor_position',i),motor_velocity:val('motor_velocity',i),effort:val('effort',i)};
 $('#jointFieldDetail').innerHTML=i>=0?`<h3>${esc(inspectJoint)} <small>原始观测字段</small></h3>${fieldTable(fields,f=>val(f.name,i))}<pre>${esc(JSON.stringify(example,null,2))}</pre>${stampLine(e)}`:'<p class="muted">等待关节数据</p>';
 const unmapped=fields.filter(f=>Array.isArray(d[f.name])&&d[f.name].length!==names.length);
 $('#unmappedJointData').innerHTML=unmapped.length?`<p class="data-callout">以下字段长度与 name[] 不一致，未映射到关节：${unmapped.map(f=>esc(f.name)+' '+d[f.name].length+' 项').join('；')}</p>`:'';
}
function paintCameraData(s){
 $('#cameraFormatData').innerHTML=cameras.map(([name,label])=>{
  const e=s.samples['/camera/'+name],m=e?.meta||{},encoding=m.encoding===1?'JPEG（encoding=1）':m.encoding===undefined?'等待图像':`encoding=${m.encoding}，格式待核对`;
  return `<article class="camera-format-card"><h3>${esc(label)} <code>/camera/${esc(name)}</code></h3><dl><dt>单帧格式</dt><dd>${esc(encoding)} · ${esc(m.bit_depth??'—')} bit</dd><dt>分辨率</dt><dd>${esc(m.width??'—')} × ${esc(m.height??'—')}</dd><dt>当前图像字节</dt><dd>${e?.image_bytes!==null&&e?.image_bytes!==undefined?e.image_bytes.toLocaleString()+' B':'—'}（压缩后，每帧大小会变化）</dd><dt>数据样式</dt><dd>JPEG bytes → Protobuf bytes 字段 → MCAP 消息</dd></dl>${stampLine(e)}</article>`;
 }).join('');
}
function nonBaseTransforms(data){
 return {...data,transforms:(data.transforms||[]).filter(t=>/^(body_|arm_|head_|gripper_|hand_|left_tool|right_tool)/.test(t.child_frame_id||''))};
}
function paintActionData(s){
 const topic=$('#inspectTopic').value,entry=inspectTopics.find(x=>x[0]===topic),e=s.samples[topic];
 $('#otherDataNote').textContent=entry?.[2]||'';$('#otherDataStatus').textContent=sampleAge(e);$('#otherDataMeta').innerHTML=stampLine(e);
 let d=e?.data;
 if(d&&topic==='/tf')d=nonBaseTransforms(d);
 if(d&&topic==='/wbc/motion_control_status')d=Object.fromEntries(['header','frame_names','frame_poses','frame_twists','frame_wrenchs','mode','error_code','error_msg'].filter(k=>k in d).map(k=>[k,d[k]]));
 $('#otherDataJson').textContent=d?JSON.stringify(d,null,2):'当前没有接收到该通道，未填入虚构的 0 值。';
}
document.querySelectorAll('[data-inspect-tab]').forEach(button=>button.onclick=()=>{
 inspectTab=button.dataset.inspectTab;document.querySelectorAll('[data-inspect-tab]').forEach(x=>{x.classList.toggle('active',x===button);x.setAttribute('aria-selected',String(x===button));});
 Object.entries(inspectViews).forEach(([key,selector])=>$(selector).hidden=key!==inspectTab);renderInspector(current,true);
});
$('#freezeInspector').onclick=()=>{inspectFrozen=!inspectFrozen;$('#freezeInspector').textContent=inspectFrozen?'继续更新':'冻结读数';renderInspector(current,true);};
$('#inspectJointGroup').onchange=()=>renderInspector(current,true);
$('#jointDataTable').onclick=e=>{const button=e.target.closest('[data-joint-name]');if(button){inspectJoint=button.dataset.jointName;renderInspector(current,true);}};
$('#inspectTopic').innerHTML=inspectTopics.map(([topic,label])=>`<option value="${esc(topic)}">${esc(label)} · ${esc(topic)}</option>`).join('');
$('#inspectTopic').onchange=()=>renderInspector(current,true);
api('/api/data-contract').then(c=>{inspectContract=c;renderInspector(current,true);}).catch(e=>notice('数据字段说明加载失败：'+e.message));
