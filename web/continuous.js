// This page's forms use persistent dispatch when a scene has been started.
let dispatchDisplayedToken=null;
function dispatchEnable(){
 for(const id of ['startDispatch','refreshDispatch'])byId(id).disabled=dispatchBusy||calculating||recovering;
 for(const id of ['advanceDispatch','downloadDispatch'])byId(id).disabled=dispatchBusy||!dispatchScene;
 const candidate=dispatchScene?.versions.find(v=>v.id===dispatchScene.candidate);
 byId('adoptDispatch').disabled=dispatchBusy||calculating||recovering||!candidate||candidate.plan.metrics.unserved>0||!dispatchReviewed;
 byId('discardDispatch').disabled=dispatchBusy||calculating||recovering||!candidate;
 if(byId('dispatchReviewAck'))byId('dispatchReviewAck').disabled=dispatchBusy||calculating||recovering||!candidate||candidate.plan.metrics.unserved>0;
 if(dispatchBusy){for(const id of ['simulateUrgent','simulateWindow','interpretAI','applyAI'])byId(id).disabled=true;}
 if(dispatchScene){
  for(const id of ['calculate','increase','oversize','restore','simulateFailure','otherFailure'])byId(id).disabled=true;
  document.querySelectorAll('.number:not(.urgent-input)').forEach(x=>x.disabled=true);
  byId('resultNote').textContent='下方普通计划图是早上参考；连续计划、货物与执行状态以本区为准。修改基础数据需开始新的独立模拟。';
 }
}
const oldEnable=enable; enable=()=>{oldEnable();dispatchEnable()};
function dispatchRender(){
 if(!dispatchScene){byId('dispatchStatus').textContent='尚无连续场景。先保存完整正常计划。';dispatchEnable();return;}
 const s=dispatchScene,active=s.versions.find(v=>v.id===s.active),candidate=s.versions.find(v=>v.id===s.candidate),p=(candidate||active).plan;
 const token=`${s.id}:${s.revision}`;
 if(dispatchDisplayedToken&&dispatchDisplayedToken!==token){
  aiDraft=null;aiContext=[];byId('aiPreview').hidden=true;
  byId('aiStatus').textContent='保存状态已变化；上一次AI事件核对已清除，需要重新理解消息。历史真实调用记录仍保留。';
 }
 dispatchDisplayedToken=token;
 byId('summary').hidden=true;byId('summary').style.display='none';byId('normalReference').open=false;
 byId('failureStatus').textContent='连续场景暂未接入故障，当前按钮已禁用。';
 byId('urgentStatus').textContent=active.plan.data.orders.some(x=>x.id==='E01')?'E01已在保存计划中，本轮仅继续收货变化。':'紧急单将基于已采用P'+s.active+'生成候选。';
 byId('windowStatus').textContent='收货变化将基于已采用P'+s.active+'及模拟执行进度生成候选。';
 byId('dispatchStatus').textContent=`场景 ${s.id.slice(0,8)} · 修订 ${s.revision} · 模拟时间 ${at(s.clock)} · 已采用 P${s.active}${candidate?` · 候选 P${candidate.id}（基于P${candidate.base}，尚未采用）`:''}。刷新会保留，未实际派单。`;
 const m=p.metrics,states=candidate?.states||s.states;
 let html=`<b>${candidate?'候选':'已采用'}计划 P${(candidate||active).id}：${m.served}/${p.data.orders.length}单，迟到${m.late_orders}单 / ${m.late_minutes}分钟，费用${m.cost}元，改动${m.changed_orders||0}单，求解${p.solve_ms}ms</b>`;
 if(candidate)html+=`<p>最近采用P${s.active}费用${active.plan.metrics.cost}元 → 候选${m.cost}元；迟到${active.plan.metrics.late_minutes} → ${m.late_minutes}分钟。新增订单或窗口不同，不能把差额直接称为降本。</p>`;
 if(candidate?.plan.metrics.unserved)html+=`<p class="notice">未找到完整方案，不能采用。未安排：${p.unserved_ids.join('、')}。已采用P${s.active}继续保留，未安排原货仍在原车；请放弃候选或修改事件重算。</p>`;
 let svg='<rect x="316" y="176" width="28" height="28" fill="#254e68"/><text x="348" y="182">仓库 / 装E01</text>';
 for(const[vid,r]of Object.entries(p.routes)){
  const q=states[vid],points=[q.point,...r.stops.slice(q.completed.length).map(x=>x.point),[0,0]];
  svg+=`<polyline points="${points.map(x=>coords(x).join(',')).join(' ')}" fill="none" stroke="${palette[vid]}" stroke-width="3"/>`;
  const[x,y]=coords(q.point);svg+=`<rect x="${x-5}" y="${y-5}" width="10" height="10" fill="${palette[vid]}"/><text x="${x+7}" y="${y-7}">${vid}</text>`;
 }
 for(const o of p.data.orders.filter(x=>x.id!=='E01')){const[x,y]=coords(o.point);svg+=`<circle cx="${x}" cy="${y}" r="12" fill="white" stroke="#254e68"/><text x="${x}" y="${y+4}" text-anchor="middle">${o.id.slice(1)}</text>`;}
 html+=`<svg viewBox="0 0 660 390" role="img" aria-label="连续计划路线" style="width:100%;max-height:300px">${svg}</svg><p class="caption">方块为当前显示计划起点位置，实线为剩余路线；已走轨迹与交付历史保存在下载记录，地图不是实时交通。</p>`;
 html+='<div class="vehicles">'+p.data.vehicles.map(v=>{const r=p.routes[v.id],q=states[v.id];return `<article class="vehicle" style="--color:${palette[v.id]}"><b>${v.id} · 位置 (${q.point.map(x=>x.toFixed(2)).join(', ')})</b><p>已交付：${q.completed.filter(x=>x.id!=='W').map(x=>x.id).join('、')||'无'}<br>当前车上：${q.cargo.join('、')||'无'}<br>后续：${r.sequence.slice(q.completed.length).map(x=>x==='W'?'回仓装E01（8分钟）':x).join(' → ')||'回仓'}<br>最高${r.peak_weight_kg??r.weight_kg} kg / ${r.peak_volume_m3??r.volume_m3} m³</p></article>`}).join('')+'</div>';
 const a=locations(p);
 html+='<div class="table-wrap"><table><thead><tr><th>订单</th><th>已保存货物状态</th><th>当前显示计划：车辆 / ETA</th></tr></thead><tbody>'+p.data.orders.map(o=>`<tr><td>${o.id}${o.store?' → '+o.store:''} · ${o.quantity}箱</td><td>${s.cargo[o.id]==='delivered'?'已交付':s.cargo[o.id]==='warehouse'?'仓库（尚未装车）':s.cargo[o.id]||'新增候选，尚未采用'}</td><td>${a[o.id]?`${a[o.id].v} / ${at(a[o.id].arrival)}${a[o.id].arrival>o.due?' 迟到':''}`:'未安排'}</td></tr>`).join('')+'</tbody></table></div>';
 html+='<p>版本：'+s.versions.map(v=>`P${v.id} ${({adopted:'已采用',candidate:'候选',executed:'已有模拟执行',superseded:'候选已失效',discarded:'候选已放弃'})[v.status]}${v.executed_until!=null?' · 执行到'+at(v.executed_until):''}`).join('；')+'</p>';
 html+='<p>回仓记录：'+Object.entries(p.routes).flatMap(([vid,r])=>r.stops.filter(x=>x.id==='W').map(x=>`${vid} ${at(x.arrival)}到仓 → ${at(x.finish)}装完 ${x.load_ids.join('、')}`)).join('；')+'</p>';
 byId('dispatchView').innerHTML=html;dispatchEnable();
 loadDecisionReview(s.id,s.revision);loadDispatchArchive();
 loadComparison(s.id,s.revision);
}
async function loadComparison(id,revision){
 const host=byId('dispatchComparison');host.hidden=true;
 try{
  const response=await fetch('/api/dispatch/comparison');const result=await response.json();
  if(dispatchScene?.id!==id||dispatchScene?.revision!==revision)return;
  if(!response.ok)throw Error(result.error||'对照读取失败');
  if(!result.available)return;
  if(result.scene_id!==id||result.revision!==revision)throw Error('场景已变化，请刷新保存状态');
  const b=result.baseline,c=result.recovered;
  const fields=[['安排订单','served','单'],['未安排','unserved','单'],['迟到订单','late_orders','单'],['迟到分钟','late_minutes','分钟'],['模拟费用','cost','元'],['改动原有订单','changed_orders','单'],['求解时间','solve_ms','ms']];
  host.innerHTML='<h3>同一次收货变化：等待还是调整？</h3>'+`<p>两边都基于已采用P${result.base_version}，从${at(result.event.time)}的相同位置和车上货出发，使用相同新收货窗口。此次展示为P${result.version}事件时刻的对照，读取不会采用或推进执行。</p>`+
   '<div class="table-wrap"><table><thead><tr><th>指标</th><th>保持原顺序等待</th><th>原车内调整</th></tr></thead><tbody>'+fields.map(([label,key,unit])=>`<tr><td>${label}</td>${[b,c].map(p=>`<td>${key==='solve_ms'?p.solve_ms:p.metrics[key]} ${unit}</td>`).join('')}</tr>`).join('')+'</tbody></table></div>'+
   `<p>本例迟到减少${(b.metrics.late_minutes-c.metrics.late_minutes).toFixed(2)}分钟，费用变化${(c.metrics.cost-b.metrics.cost).toFixed(2)}元；改动${b.metrics.changed_orders} → ${c.metrics.changed_orders}单。E01回仓装货与已交付历史两边均保留。</p>`+
   '<p class="caption">相同事件对照不能证明平均收益。全部模拟数据，有限启发式，无全局最优保证；原窗口计划不是本次对照基准。</p>';
  host.hidden=false;
 }catch(e){host.textContent='未显示对照：'+e.message;host.hidden=false;}
}
async function loadDispatch(){
 if(dispatchBusy)return;
 dispatchBusy=true;dispatchReviewed=false;
 if(byId('dispatchReviewAck'))byId('dispatchReviewAck').checked=false;
 enable();
 try{const r=await fetch('/api/dispatch');const b=await r.json();if(!r.ok)throw Error(b.error);dispatchScene=b.scene;dispatchRender()}
 catch(e){byId('dispatchStatus').textContent='读取失败：'+e.message}
 finally{dispatchBusy=false;enable()}
}
async function dispatchAction(action,extra={}){
 if(dispatchBusy||calculating||recovering)return;
 dispatchReviewed=false;
 if(byId('dispatchReviewAck'))byId('dispatchReviewAck').checked=false;
 dispatchBusy=true;enable();try{
  const r=await fetch('/api/dispatch/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:dispatchScene?.id,revision:dispatchScene?.revision,...extra})});
  const b=await r.json();if(!r.ok)throw Error(b.error+(r.status===409?'；请点击刷新保存状态':''));dispatchScene=b.scene;dispatchRender();
  return true;
 }catch(e){byId('dispatchStatus').textContent='未更改已采用计划：'+e.message;return false}finally{dispatchBusy=false;enable()}
}
async function dispatchEvent(kind){
 const prefix=kind==='urgent'?'urgent':'window';const event={time:minutes(byId(prefix+'Time').value),store:byId(prefix+'Store').value,due:minutes(byId(prefix+'Due').value),close:minutes(byId(prefix+'Close').value)};
 if(kind==='urgent')event.quantity=Number(byId('urgentQuantity').value);else event.open=minutes(byId('windowOpen').value);
 const ok=await dispatchAction('event',{kind,event});byId(prefix+'Status').textContent=ok?'连续场景结果见上方：核对候选后采用并保存。':'未生成新候选，错误原因见连续场景状态；原已采用计划保留。';
}
byId('startDispatch').onclick=()=>dispatchAction('start',{data:normalResult.data});
byId('adoptDispatch').onclick=()=>dispatchAction('adopt',{candidate:dispatchScene.candidate});
byId('discardDispatch').onclick=()=>dispatchAction('discard',{candidate:dispatchScene.candidate});
byId('advanceDispatch').onclick=()=>dispatchAction('advance',{time:minutes(byId('executionTime').value)});
byId('refreshDispatch').onclick=loadDispatch;
byId('downloadDispatch').onclick=()=>{const u=URL.createObjectURL(new Blob([JSON.stringify(dispatchScene,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=u;a.download='continuous-dispatch.json';a.click();URL.revokeObjectURL(u)};

calculate().then(loadDispatch);loadAIStatus();
