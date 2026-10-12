// Human review is bound to the currently displayed scene revision.
let dispatchReviewed=false,dispatchReviewToken=null,dispatchArchiveSerial=0,dispatchHistorySerial=0;
let dispatchHistoryScene=null;
const dispatchEscape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dispatchVersionStatus={adopted:'已采用',candidate:'候选',executed:'已有模拟执行',superseded:'候选已失效',discarded:'候选已放弃'};
function dispatchPickup(id){return id==='W'||id.startsWith('warehouse:');}
function dispatchRouteLabel(id){return dispatchPickup(id)?(id==='W'?'回仓装货':'回仓装'+id.slice(10)+'（8分钟）'):id;}
function reviewLocation(value){return value?`${dispatchEscape(value.vehicle)} / ${at(value.eta)}`:'未安排';}
function reviewWindow(value){return value?`${at(value.open)} / ${at(value.due)} / ${at(value.close)}`:'新增';}
function reviewDetails(r){
 const rows=r.rows.filter(x=>x.reasons.length);
 return `<p>生成时刻 ${at(r.time)} · 基于${r.base?'P'+r.base:'早上数据'} · 固定${r.frozen_delivery_count}单已交付历史。收货判断按${r.window_rule==='finish'?'卸货完成':'到达'}；以下ETA与窗口是该版本生成时的记录，窗口依次为开门 / 承诺 / 截止。</p>`+
 (r.event?`<p>事件：${r.event.kind==='urgent'?'新增紧急单':'收货时间变化'} · ${dispatchEscape(r.event.event.store)} · ${at(r.event.event.time)}发生。</p>`:'')+
 (r.blocked_reason?`<p class="notice">${dispatchEscape(r.blocked_reason)}</p>`:'')+
 '<div class="table-wrap"><table><thead><tr><th>订单 / 变化原因</th><th>父计划 → 此版本 ETA</th><th>收货窗口变化</th><th>事件时货物 / 此版本迟到</th></tr></thead><tbody>'+rows.map(x=>`<tr><td>${dispatchEscape(x.id)} · ${dispatchEscape(x.reasons.join('、'))}</td><td>${reviewLocation(x.before)} → ${reviewLocation(x.after)}</td><td>${reviewWindow(x.old_window)} → ${reviewWindow(x.window)}</td><td>${dispatchEscape(x.cargo_owner==='warehouse'?'仓库':x.cargo_owner==='delivered'?'已交付':x.cargo_owner)} / ${x.after?x.after.late_minutes+'分钟':'未安排'}</td></tr>`).join('')+'</tbody></table></div>'+
 (!rows.length?'<p>没有新增、窗口或超过1分钟的ETA变化。</p>':'')+
 '<p>回仓装货：'+(r.pickups.map(x=>`${dispatchEscape(x.vehicle)} ${at(x.arrival)}到仓 → ${at(x.finish)}装完 ${dispatchEscape(x.load_ids.join('、'))}${x.completed?'（事件前已完成）':''}`).join('；')||'本版本无新增装货节点')+'</p>';
}
async function loadDecisionReview(id,revision){
 dispatchReviewed=false;dispatchReviewToken=null;dispatchEnable();
 const host=byId('dispatchReview');host.textContent='正在读取版本核对记录…';
 try{
  const response=await fetch('/api/dispatch/review');const r=await response.json();
  if(dispatchScene?.id!==id||dispatchScene?.revision!==revision)return;
  if(!response.ok)throw Error(r.error||'核对读取失败');
  if(!r.available){host.textContent='尚无版本';return;}
  if(r.scene_id!==id||r.revision!==revision)throw Error('场景已变化，请刷新保存状态');
  const candidate=dispatchScene.candidate===r.version;
  host.innerHTML=`<h3>${candidate?'采用前核对候选':'已采用版本记录'} P${r.version}</h3>`+reviewDetails(r)+
   (candidate?`<label><input id="dispatchReviewAck" type="checkbox" ${r.adoptable?'':'disabled'}> 我已核对候选P${r.version}的事件、货物、迟到和回仓装货</label><p class="caption">${r.adoptable?'勾选后才可采用；刷新、重算或执行进度变化后需重新核对。':'此候选不可采用；请修改事件重算或交由人工处理。'}放弃候选保留计算记录，不改变已采用或已执行配送。</p>`:'<p class="caption">这里只查看采用记录，不会推进执行，也不提供撤回已发生配送。</p>');
  if(candidate&&r.adoptable){
   dispatchReviewToken=`${id}:${revision}:${r.version}`;
   byId('dispatchReviewAck').onchange=()=>{
    dispatchReviewed=byId('dispatchReviewAck').checked&&dispatchReviewToken===`${dispatchScene?.id}:${dispatchScene?.revision}:${dispatchScene?.candidate}`;
    dispatchEnable();
   };
  }
 }catch(e){if(dispatchScene?.id===id&&dispatchScene?.revision===revision){host.textContent='无法核对，采用保持禁用：'+e.message;}}
}
async function loadDispatchArchive(){
 const serial=++dispatchArchiveSerial;
 try{
  const response=await fetch('/api/dispatch/history');const r=await response.json();
  if(serial!==dispatchArchiveSerial)return;
  if(!response.ok)throw Error(r.error||'历史读取失败');
  const select=byId('dispatchHistoryScene'),selected=select.value;
  select.innerHTML='<option value="">选择场景，只读查看</option>'+r.scenes.map(s=>`<option value="${dispatchEscape(s.id)}">${s.current?'当前':'归档'} ${s.id.slice(0,8)} · P${s.active} · ${at(s.clock)} · 已交付${s.delivered}单</option>`).join('');
  select.value=r.scenes.some(s=>s.id===selected)?selected:'';
  byId('dispatchHistoryStatus').textContent=`共${r.total}个场景，显示最近${r.scenes.length}个。查看历史不会切换当前场景或恢复旧计划。`;
  if(select.value)await loadDispatchHistory();
 }catch(e){if(serial===dispatchArchiveSerial)byId('dispatchHistoryStatus').textContent='历史读取失败：'+e.message;}
}
async function loadDispatchHistory(){
 const serial=++dispatchHistorySerial,id=byId('dispatchHistoryScene').value;
 dispatchHistoryScene=null;byId('dispatchHistoryVersions').innerHTML='';byId('dispatchHistoryView').textContent='';
 if(!id)return;
 try{
  const response=await fetch('/api/dispatch/history/'+encodeURIComponent(id));const r=await response.json();
  if(serial!==dispatchHistorySerial)return;
  if(!response.ok||!r.scene)throw Error(r.error||'场景不存在');
  dispatchHistoryScene=r.scene;
  byId('dispatchHistoryVersions').innerHTML=r.scene.versions.map(v=>`<option value="${v.id}">P${v.id} · ${dispatchVersionStatus[v.status]}${v.executed_until!=null?' · 执行到'+at(v.executed_until):''}</option>`).join('');
  byId('dispatchHistoryVersions').value=r.scene.candidate||r.scene.active;
  renderDispatchHistory();
 }catch(e){if(serial===dispatchHistorySerial)byId('dispatchHistoryView').textContent='只读历史未显示：'+e.message;}
}
function renderDispatchHistory(){
 if(!dispatchHistoryScene)return;
 const scene=dispatchHistoryScene,number=Number(byId('dispatchHistoryVersions').value),v=scene.versions.find(x=>x.id===number);
 const event=scene.events.find(x=>x.version===number);
 const routes=Object.entries(v.plan.routes).map(([vid,r])=>`<li>${dispatchEscape(vid)}：${r.sequence.map(x=>dispatchEscape(dispatchRouteLabel(x))).join(' → ')||'无任务'}；回仓 ${at(r.return_at)}${r.retained_ids?.length?'；原车带回未交付货物：'+dispatchEscape(r.retained_ids.join('、')):''}</li>`).join('');
 const m=v.plan.metrics;
 byId('dispatchHistoryView').innerHTML=`<h3>只读历史 ${scene.id.slice(0,8)} / P${number} · ${dispatchVersionStatus[v.status]}</h3><p>${m.served}单已安排 · 未安排${m.unserved}单 · 迟到${m.late_minutes}分钟 · 模拟费用${m.cost}元。</p>`+
  (event?`<p>${event.kind==='urgent'?'紧急单':'收货变化'} · ${dispatchEscape(event.event.store)} · ${at(event.event.time)}发生，基于P${v.base}。</p>`:'<p>早上正常计划。</p>')+
  (v.reason?`<p>${dispatchEscape(v.reason)}</p>`:'')+
  `<ul>${routes}</ul><p class="caption">以上包含历史节点。只是读取保存记录；当前活动场景和模拟时间不改变。</p>`;
}
byId('dispatchHistoryScene').onchange=loadDispatchHistory;
byId('dispatchHistoryVersions').onchange=renderDispatchHistory;
byId('refreshDispatchHistory').onclick=loadDispatchArchive;
