'use strict';
let conditionProposal=null;
function conditionEditor(){
 if(!task||!state||!planNow()||mwFacts()||lifecycleLocked())return '';
 return `<section class="condition-editor" aria-label="调整配送条件"><p>修改门店回收或收货时间，查看影响后重新排车。</p><form id="conditionForm"><div class="form-grid"><label class="wide">门店<select id="conditionStore">${task.data.orders.map(o=>`<option value="${esc(o.id)}">${esc(orderName(o.id))}</option>`).join('')}</select></label><label>预计纸箱<input id="conditionCartons" type="number" min="0" max="100000" step="1" required></label><label>预计周转箱<input id="conditionTotes" type="number" min="0" max="100000" step="1" required></label><label>开始收货<input id="conditionOpen" type="time" required></label><label>承诺完成<input id="conditionDue" type="time" required></label><label>最晚完成<input id="conditionClose" type="time" required></label></div><button type="submit">查看变化影响</button></form><div id="conditionImpact" role="status" aria-live="polite" hidden></div></section>`;
}
function fillCondition(){
 const select=$('conditionStore');if(!select)return;
 const order=task.data.orders.find(o=>o.id===select.value);if(!order)return;
 $('conditionCartons').value=order.pickup?.cartons??0;$('conditionTotes').value=order.pickup?.totes??0;
 for(const [key,id] of [['open','conditionOpen'],['due','conditionDue'],['close','conditionClose']])$(id).value=time(order[key]);
 conditionProposal=null;$('conditionImpact').hidden=true;
}
const changesRenderPlan=renderPlan;
renderPlan=function(){
 changesRenderPlan();
 if(!task||!planNow())return;
 const section=$('planCompare');
 if($('planConditions'))$('planConditions').innerHTML=conditionEditor();
 if(!mwFacts()&&!lifecycleLocked())$('plan').querySelector('.plan-actions')?.insertAdjacentHTML('afterbegin','<button id="openConditions">调整条件</button>');
 fillCondition();
 const p=planNow(),change=(task.condition_changes||[]).find(c=>c.id===p.condition_change_id);
 if(change){
  const parent=task.plans.find(old=>old.id===change.before_plan_id),outcome=p.summary.optimized;
  const names={pickup:'预计回收',open:'开始收货',due:'承诺完成',close:'最晚完成',service_minutes:'服务分钟'};
  const value=(c,v)=>c.field==='pickup'?`${v.cartons}纸箱 / ${v.totes}周转箱`:['open','due','close'].includes(c.field)?time(v):num(v);
  const detail=`<section class="plan-comparison"><h4>本次条件调整已重新核验</h4><div class="compare-content">${change.changes.map(c=>`<p>${esc(orderName(c.order_id))} · ${names[c.field]||c.field}：${esc(value(c,c.before))} → ${esc(value(c,c.after))}</p>`).join('')}<p>原受影响车次：${change.affected_routes.map(r=>esc(displayVehicleId(r.vehicle_id))).join('、')||'待分配'}。${outcome.unserved?'仍有'+outcome.unserved+'家待安排。':'全部门店已安排。'}${p.audit?.passed?'独立核验通过。':'请查看未通过的约束。'}</p>${parent?`<p>车次 ${parent.summary.optimized.trips} → ${outcome.trips} · 运费 ${num(parent.summary.optimized.cost)} → ${num(outcome.cost)} 元。${parent.audit?.passed&&p.audit?.passed?'':'两份方案的通过状态不同，不计算节省。'}</p>`:''}</div></section>`;
  section.insertAdjacentHTML('afterbegin',detail);
 }
};
document.addEventListener('change',e=>{if(e.target.id==='conditionStore')fillCondition();});
document.addEventListener('input',e=>{if(e.target.closest('#conditionForm')){conditionProposal=null;if($('conditionImpact'))$('conditionImpact').hidden=true;}});
document.addEventListener('submit',e=>{
 if(e.target.id!=='conditionForm')return;e.preventDefault();
 if(dirty){notice('请先保存当前修改，再调整配送条件。',true);return;}
 run(async()=>{
  const request={id:task.id,revision:task.revision,order_id:$('conditionStore').value,
   pickup:{cartons:Number($('conditionCartons').value),totes:Number($('conditionTotes').value)},
   open:$('conditionOpen').value,due:$('conditionDue').value,close:$('conditionClose').value};
  const result=await api('conditions-preview',request);conditionProposal=request;
  const impact=result.impact,host=$('conditionImpact');host.hidden=false;
  const labels={pickup:'预计回收',open:'开始收货',due:'承诺完成',close:'最晚完成'};
  const value=(c,v)=>c.field==='pickup'?`${v.cartons}纸箱 / ${v.totes}周转箱`:time(v);
  host.innerHTML=`<h4>变化影响</h4><p>${esc(orderName(request.order_id))}</p>${impact.changes.map(c=>`<p>${labels[c.field]}：${esc(value(c,c.before))} → ${esc(value(c,c.after))}</p>`).join('')}<p>原方案涉及${impact.affected_routes.map(r=>esc(displayVehicleId(r.vehicle_id))).join('、')||'待安排门店'}，将重新检查容量、时间、冷链与装卸。当前方案尚未改变。</p><label class="inline-check"><input id="reviewCondition" type="checkbox">已核对变化，按新条件重新排车</label><button id="applyCondition" class="primary" disabled>应用并重新排车</button>`;
 });
});
document.addEventListener('change',e=>{if(e.target.id==='reviewCondition')$('applyCondition').disabled=!e.target.checked;});
document.addEventListener('click',async e=>{
 if(e.target.id==='openConditions'){
  showPlanView('planConditions','plan');$('conditionStore').focus();return;
 }
 if(e.target.id!=='applyCondition'||busy||!conditionProposal||!$('reviewCondition').checked)return;
 let applied=false;
 await run(async()=>{
  const result=await api('conditions-apply',{...conditionProposal,reviewed:true});
  conditionProposal=null;step='plan';setTask(result);await refresh();applied=true;
  notice('条件已更新，正在重新核验配送方案。');
 });
 if(applied)await sendAgent('计算配送方案','local');
});
