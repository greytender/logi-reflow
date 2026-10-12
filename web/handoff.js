// Read-only adopted facts; never borrow the candidate displayed elsewhere.
let handoffToken=null;
async function loadHandoff(id,revision){
 const host=byId('dispatchHandoff');handoffToken=null;
 host.textContent='正在读取已采用计划的交接…';byId('downloadHandoff').disabled=true;
 try{
  const response=await fetch('/api/dispatch/handoff?'+new URLSearchParams({id,revision}));const r=await response.json();
  if(dispatchScene?.id!==id||dispatchScene?.revision!==revision)return;
  if(!response.ok)throw Error(r.error||'交接读取失败');
  if(!r.available){host.textContent=r.reason;return;}
  const esc=dispatchEscape,pt=x=>x==null?'无有效预测':at(x),rt=x=>x==null?'待回执':at(x),owner=x=>x==='warehouse'?'仓库':x==='delivered'?'已交付':x,m=r.metrics;
  host.innerHTML=`<h3>已采用P${r.adopted_version}的模拟交接</h3><p>已模拟签收${m.signed_orders}/${m.orders}单；按收货口径有回执确认迟到${m.receipt_confirmed_late_orders}单；过承诺但缺该口径回执${m.overdue_without_rule_receipt}单。模拟到达至卸货开始累计${m.arrived_to_unloading_minutes}分钟，已签收卸货累计${m.signed_service_minutes}分钟。</p>`+
   (!r.prediction_valid?'<p class="notice">剩余预测已失效，交接隐藏旧ETA；请调度核查后重新试算。</p>':'')+
   (r.candidate_version?`<p>候选P${r.candidate_version}尚未采用，其新货和路线不进入本交接。</p>`:'')+
   '<div class="table-wrap"><table><thead><tr><th>订单 / 货物归属</th><th>预测到达 / 完成</th><th>模拟回执：到达 / 卸货 / 签收</th><th>下一步交接</th></tr></thead><tbody>'+r.rows.map(x=>`<tr><td>${esc(x.order)} → ${esc(x.store)}<br>${esc(owner(x.cargo_owner))} · ${esc(x.status)}</td><td>${pt(x.predicted_arrival)} / ${pt(x.predicted_finish)}</td><td>${rt(x.arrived_at)} / ${rt(x.unloading_at)} / ${rt(x.signed_at)}</td><td>${esc(x.next_actor)}：${esc(x.next_action)}${x.pickup_task?'<br>取货批次 '+esc(x.pickup_task):''}</td></tr>`).join('')+'</tbody></table></div>'+
   '<p class="caption">等待包含尚未开始卸货的累计时间，不能归因为客户责任。装货仍由8分钟模型推进；未接真实仓库、GPS、电子签收或结算。导出的时间为当日分钟数，空值表示未确认或无有效预测。</p>';
  handoffToken={id,revision};byId('downloadHandoff').disabled=false;
 }catch(e){if(dispatchScene?.id===id&&dispatchScene?.revision===revision)host.textContent='交接读取失败：'+e.message;}
}
byId('downloadHandoff').onclick=async()=>{
 if(!handoffToken||dispatchBusy)return;
 const token=handoffToken;
 try{
  const response=await fetch('/api/dispatch/handoff?'+new URLSearchParams({...token,format:'csv'}));
  if(!response.ok){const e=await response.json();throw Error(e.error);}
  if(dispatchScene?.id!==token.id||dispatchScene?.revision!==token.revision)throw Error('场景已变化，请刷新交接');
  const u=URL.createObjectURL(await response.blob()),a=document.createElement('a');a.href=u;a.download='模拟交接-P'+dispatchScene.active+'-r'+token.revision+'.csv';a.click();URL.revokeObjectURL(u);
 }catch(e){byId('dispatchHandoff').textContent='未导出交接：'+e.message;}
};
