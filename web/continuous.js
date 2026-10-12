// This page's forms use persistent dispatch when a scene has been started.
let dispatchDisplayedToken=null;
function dispatchEnable(){
 document.querySelectorAll('#dispatchReceipts button').forEach(b=>b.disabled=dispatchBusy||calculating||recovering);
 for(const id of ['startDispatch','refreshDispatch','importDispatch','sampleDispatch'])byId(id).disabled=dispatchBusy||calculating||recovering;
 for(const id of ['advanceDispatch','downloadDispatch'])byId(id).disabled=dispatchBusy||!dispatchScene;
 const candidate=dispatchScene?.versions.find(v=>v.id===dispatchScene.candidate);
 byId('adoptDispatch').disabled=dispatchBusy||calculating||recovering||!candidate||candidate.plan.metrics.unserved>0||!dispatchReviewed;
 byId('discardDispatch').disabled=dispatchBusy||calculating||recovering||!candidate;
 if(byId('dispatchReviewAck'))byId('dispatchReviewAck').disabled=dispatchBusy||calculating||recovering||!candidate||candidate.plan.metrics.unserved>0;
 if(dispatchBusy){for(const id of ['simulateUrgent','simulateWindow','interpretAI','applyAI'])byId(id).disabled=true;}
 if(dispatchScene?.live_plan?.data.dispatch_model==='directed_csv_v1'){
  byId('interpretAI').disabled=dispatchBusy||calculating||recovering;
  byId('applyAI').disabled=dispatchBusy||calculating||recovering||!aiDraft||!byId('aiReviewAck').checked;
  byId('aiReviewAck').disabled=dispatchBusy||calculating||recovering||!aiDraft;
 }
 if(dispatchScene?.live_plan?.data.dispatch_model==='directed_csv_v1'&&dispatchScene.live_plan.data.orders.filter(o=>o.store).length>=2)byId('simulateUrgent').disabled=true;
 if(dispatchScene?.live_plan?.fixed_loading)byId('simulateUrgent').disabled=true;
 if(dispatchScene){
  for(const id of ['calculate','increase','oversize','restore','simulateFailure','otherFailure'])byId(id).disabled=true;
  document.querySelectorAll('.number:not(.urgent-input)').forEach(x=>x.disabled=true);
  byId('resultNote').textContent='下方普通计划图是早上参考；连续计划、货物与执行状态以本区为准。修改基础数据需开始新的独立模拟。';
 }
}
const oldEnable=enable; enable=()=>{oldEnable();dispatchEnable()};
function dispatchRender(){
 if(!dispatchScene){byId('dispatchStatus').textContent='尚无连续场景。先保存完整正常计划。';dispatchEnable();return;}
 const s=dispatchScene,active=s.versions.find(v=>v.id===s.active),candidate=s.versions.find(v=>v.id===s.candidate),p=candidate?.plan||s.live_plan||active.plan;
 const matrix=p.data.dispatch_model==='directed_csv_v1';
 const token=`${s.id}:${s.revision}`;
 const selected=byId('windowStore').value;
 byId('windowStore').innerHTML=p.data.orders.map(o=>`<option value="${dispatchEscape(o.id)}">${dispatchEscape(o.id)}</option>`).join('');
 if(p.data.orders.some(o=>o.id===selected))byId('windowStore').value=selected;
 byId('windowTime').min=matrix?'00:00':'08:00';byId('windowTime').max=matrix?'23:59':'09:00';
 if(dispatchDisplayedToken&&dispatchDisplayedToken!==token){
  aiDraft=null;aiContext=[];byId('aiPreview').hidden=true;
  byId('aiReviewAck').checked=false;
  byId('aiStatus').textContent='保存状态已变化；上一次AI事件核对已清除，需要重新理解消息。历史真实调用记录仍保留。';
 }
 dispatchDisplayedToken=token;
 byId('aiReviewLabel').hidden=!matrix;
 byId('aiContract').textContent=matrix?'CSV场景支持仓库紧急单和订单收货变化。请注明准确编号、发生时间；新增货逐SKU注明箱数、每箱kg和每箱m³，缺项会补问。核对后才生成候选，不自动采用。':'AI只理解事件，不直接改路线。先核对结构化结果，再点击试算；缺时间、箱数或收货窗口会补问。';
 byId('aiDisclosure').textContent=matrix?'请求DeepSeek时发送当前消息、最多4条补问原话，以及原始门店/紧急订单对应编号和已签收编号。不会发送SKU原表、道路、车辆、路线或凭据到网页；当前为模拟数据。':'只发送当前业务消息及最多4条补问原话到DeepSeek；不发送订单全表、路线或密钥到网页。当前均为模拟数据。';
 byId('summary').hidden=true;byId('summary').style.display='none';byId('normalReference').open=false;
 byId('failureStatus').textContent='连续场景暂未接入故障，当前按钮已禁用。';
 byId('urgentStatus').textContent=active.plan.data.orders.some(x=>x.id==='E01')?'E01已在保存计划中，本轮仅继续收货变化。':'紧急单将基于已采用P'+s.active+'生成候选。';
 if(p.fixed_loading){
  byId('urgentStatus').textContent='固定装载场景：仓库新货的剩余空间插入尚未实现，紧急单拒绝；原坐标保持。';
  byId('aiContract').textContent='固定装载场景目前仅支持收货变化。新顺序必须按已保存坐标卸货；未实现仓库新货空间插入，紧急单会被服务端拒绝。';
 }
 byId('windowStatus').textContent='收货变化将基于已采用P'+s.active+'及模拟执行进度生成候选。';
 byId('dispatchStatus').textContent=`场景 ${s.id.slice(0,8)} · 修订 ${s.revision} · 模拟时间 ${at(s.clock)} · 已采用 P${s.active}${candidate?` · 候选 P${candidate.id}（基于P${candidate.base}，尚未采用）`:''}。刷新会保留，未实际派单。`;
 byId('urgentLinesLabel').hidden=!matrix;byId('urgentQuantity').disabled=matrix||dispatchBusy;
 if(matrix){
  const selectedUrgent=byId('urgentStore').value;
  byId('urgentStore').innerHTML=active.plan.data.orders.filter(o=>!o.store).map(o=>`<option value="${dispatchEscape(o.id)}">${dispatchEscape(o.id)}</option>`).join('');
  if(active.plan.data.orders.some(o=>o.id===selectedUrgent))byId('urgentStore').value=selectedUrgent;
  byId('urgentTime').min='00:00';byId('urgentTime').max='23:59';
  byId('urgentStatus').textContent=`已采用${active.plan.data.orders.filter(o=>o.store).length}/2个仓库紧急单。新货需明确SKU数量、箱重和箱体积；卸货时长和准入沿用目的店。编号自动分配，每批分别装货8分钟；达到上限后仍可继续收货变化。`;
 }

 renderReceipts(s);
 const m=p.metrics,states=candidate?.states||s.states;
 let html=`<b>${candidate?'候选':'已采用'}计划 P${(candidate||active).id}：已安排${m.served}/${p.data.orders.length}单（不是签收数），预计迟到${m.late_orders}单 / ${m.late_minutes}分钟，模拟费用${m.cost}元，改动${m.changed_orders||0}单，求解${p.solve_ms}ms</b>`;
 if(p.needs_replan)html+='<p class="notice">回执已保存，但延误使剩余路线未通过约束。下方旧ETA和费用不能作为可执行承诺，需人工核查并重新试算。</p>';
 if(candidate)html+=`<p>最近采用P${s.active}费用${active.plan.metrics.cost}元 → 候选${m.cost}元；迟到${active.plan.metrics.late_minutes} → ${m.late_minutes}分钟。新增订单或窗口不同，不能把差额直接称为降本。</p>`;
 if(candidate?.plan.metrics.unserved)html+=`<p class="notice">未找到完整方案，不能采用。未安排：${p.unserved_ids.join('、')}。已采用P${s.active}继续保留，未安排原货仍在原车；请放弃候选或修改事件重算。</p>`;
 if(matrix){html+=matrixView(p,states);}else{
 let svg='<rect x="316" y="176" width="28" height="28" fill="#254e68"/><text x="348" y="182">仓库 / 装E01</text>';
 for(const[vid,r]of Object.entries(p.routes)){
  const q=states[vid],points=[q.point,...r.stops.slice(q.completed.length).map(x=>x.point),[0,0]];
  svg+=`<polyline points="${points.map(x=>coords(x).join(',')).join(' ')}" fill="none" stroke="${palette[vid]}" stroke-width="3"/>`;
  const[x,y]=coords(q.point);svg+=`<rect x="${x-5}" y="${y-5}" width="10" height="10" fill="${palette[vid]}"/><text x="${x+7}" y="${y-7}">${vid}</text>`;
 }
 for(const o of p.data.orders.filter(x=>x.id!=='E01')){const[x,y]=coords(o.point);svg+=`<circle cx="${x}" cy="${y}" r="12" fill="white" stroke="#254e68"/><text x="${x}" y="${y+4}" text-anchor="middle">${o.id.slice(1)}</text>`;}
 html+=`<svg viewBox="0 0 660 390" role="img" aria-label="连续计划路线" style="width:100%;max-height:300px">${svg}</svg><p class="caption">方块为当前显示计划起点位置，实线为剩余路线；已走轨迹与交付历史保存在下载记录，地图不是实时交通。</p>`;
 html+='<div class="vehicles">'+p.data.vehicles.map(v=>{const r=p.routes[v.id],q=states[v.id];return `<article class="vehicle" style="--color:${palette[v.id]}"><b>${v.id} · 位置 (${q.point.map(x=>x.toFixed(2)).join(', ')})</b><p>已交付：${q.completed.filter(x=>!dispatchPickup(x.id)).map(x=>x.id).join('、')||'无'}<br>当前车上：${q.cargo.join('、')||'无'}<br>后续：${r.sequence.slice(q.completed.length).map(x=>x==='W'?'回仓装E01（8分钟）':x).join(' → ')||'回仓'}<br>最高${r.peak_weight_kg??r.weight_kg} kg / ${r.peak_volume_m3??r.volume_m3} m³</p></article>`}).join('')+'</div>';
 }
 const a=locations(p);
 html+='<div class="table-wrap"><table><thead><tr><th>订单</th><th>已保存货物状态</th><th>当前显示计划：车辆 / ETA</th></tr></thead><tbody>'+p.data.orders.map(o=>`<tr><td>${o.id}${o.store?' → '+o.store:''} · ${o.lines?o.lines.reduce((n,x)=>n+x.quantity,0):o.quantity}箱${o.lines?' / '+o.lines.length+'行SKU':''}</td><td>${s.cargo[o.id]==='delivered'?'已交付':s.cargo[o.id]==='warehouse'?'仓库（尚未装车）':s.cargo[o.id]||'新增候选，尚未采用'}</td><td>${a[o.id]?`${a[o.id].v} / ${at(a[o.id].arrival)}${(p.data.settings?.window_rule==='finish'?p.routes[a[o.id].v].stops.find(x=>x.id===o.id).finish:a[o.id].arrival)>o.due?' 迟到':''}`:'未安排'}</td></tr>`).join('')+'</tbody></table></div>';
 html+='<p>版本：'+s.versions.map(v=>`P${v.id} ${({adopted:'已采用',candidate:'候选',executed:'已有模拟执行',superseded:'候选已失效',discarded:'候选已放弃'})[v.status]}${v.executed_until!=null?' · 执行到'+at(v.executed_until):''}`).join('；')+'</p>';
 html+='<p>回仓记录：'+Object.entries(p.routes).flatMap(([vid,r])=>r.stops.filter(x=>dispatchPickup(x.id)).map(x=>`${vid} ${at(x.arrival)}到仓 → ${at(x.finish)}装完 ${x.load_ids.join('、')}`)).join('；')+'</p>';
 if(p.fixed_loading){
  const frozen=p.fixed_loading,defs=Object.fromEntries(frozen.manifest.items.map(x=>[x.id,x]));
  html+='<details id="fixedCargoDetails"><summary>固定装载坐标与候选卸货核验</summary><p>原坐标冻结；只在人工模拟签收后释放整单箱体。到达、卸货中和候选调整均不重摆其他货物。新货空间插入尚未实现。</p>';
  for(const [vid,r]of Object.entries(p.routes)){
   const current=frozen.manifest.placements[vid].filter(x=>s.cargo[defs[x.id].order]!=='delivered'),check=r.fixed_loading_check;
   html+=`<h4>${dispatchEscape(vid)} · ${current.length}箱仍在车上</h4><p>${check?.passed?'本版本剩余顺序已通过原坐标卸货核验':'出发布局已核验；异常候选另按原坐标核验'}${p.needs_replan?'；时间预测已失效，需重新核查':''}</p>`;
   html+='<div class="table-wrap"><table><thead><tr><th>箱体</th><th>x / y / z cm</th><th>摆放长 / 宽 / 高 cm</th></tr></thead><tbody>'+current.map(x=>`<tr><td>${dispatchEscape(x.id)}</td><td>${x.x} / ${x.y} / ${x.z}</td><td>${x.dx} / ${x.dy} / ${x.dz}</td></tr>`).join('')+'</tbody></table></div>';
   if(check)html+='<ol>'+check.unload_steps.map(x=>`<li>${dispatchEscape(x.order)}：依次取 ${x.box_ids.map(dispatchEscape).join('、')}</li>`).join('')+'</ol>';
  }
  html+='</details>';
 }
 byId('dispatchView').innerHTML=html;dispatchEnable();
 loadDecisionReview(s.id,s.revision);loadDispatchArchive();
 loadComparison(s.id,s.revision);
 loadHandoff(s.id,s.revision);
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
   `<p>本例迟到减少${(b.metrics.late_minutes-c.metrics.late_minutes).toFixed(2)}分钟，费用变化${(c.metrics.cost-b.metrics.cost).toFixed(2)}元；改动${b.metrics.changed_orders} → ${c.metrics.changed_orders}单。已有回仓装货任务与已交付历史两边均保留。</p>`+
   '<p class="caption">相同事件对照不能证明平均收益。执行为模拟，输入来源见场景声明；有限启发式，无全局最优保证；原窗口计划不是本次对照基准。</p>';
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
 if(kind==='urgent'&&dispatchScene?.live_plan?.data.dispatch_model==='directed_csv_v1'){
  try{event.lines=byId('urgentLines').value.trim().split(/\r?\n/).map(line=>{const fields=line.split(',').map(x=>x.trim());if(fields.length!==4||fields.some(x=>!x))throw Error('每行需填SKU、箱数、箱重kg、箱体积m³，共4列');const [sku,q,w,v]=fields;return {sku,quantity:Number(q),unit_weight_kg:Number(w),unit_volume_m3:Number(v)};});}
  catch(e){byId('urgentStatus').textContent=e.message;return;}
 }else if(kind==='urgent')event.quantity=Number(byId('urgentQuantity').value);else event.open=minutes(byId('windowOpen').value);
 const ok=await dispatchAction('event',{kind,event});byId(prefix+'Status').textContent=ok?'连续场景结果见上方：核对候选后采用并保存。':'未生成新候选，错误原因见连续场景状态；原已采用计划保留。';
}
byId('startDispatch').onclick=()=>dispatchAction('start',{data:normalResult.data});
byId('adoptDispatch').onclick=()=>dispatchAction('adopt',{candidate:dispatchScene.candidate});
byId('discardDispatch').onclick=()=>dispatchAction('discard',{candidate:dispatchScene.candidate});
byId('advanceDispatch').onclick=()=>dispatchAction('advance',{time:minutes(byId('executionTime').value)});

function renderReceipts(s){
 const host=byId('dispatchReceipts');
 if(s.progress_mode!=='manual_receipts'){
  host.innerHTML='<p class="notice">旧场景：到达即交付的模拟规则。旧历史原样保留；请开始新模拟使用分步回执。</p>';return;
 }
 host.innerHTML='<h3>配送进度 · 人工模拟回执</h3><p>预计位置由模拟行驶推算，不能证明真实到店。到达和卸货中货物仍在原车；点击签收才记录完成。卸货时长按当前订单规则执行，原案例为5分钟。回执使用上方“模拟执行到”的时间。</p>'+Object.entries(s.states).map(([vid,q])=>{
  const n=q.pending, labels={awaiting_arrival:'预计已到店，待到达回执',arrived:'已到达，待卸货',unloading:'正在卸货，待签收'},actions={awaiting_arrival:['arrived','确认模拟到达'],arrived:['unloading','开始模拟卸货'],unloading:['signed','完成模拟签收']};
  return `<p><b>${vid}</b> · ${n?`${n.id} · ${labels[n.status]}${n.arrived_at!=null?' · 到达'+at(n.arrived_at):''}${n.unloading_at!=null?' · 卸货开始'+at(n.unloading_at):''}`:(q.loading?`仓库装货中 · ${at(q.loading.arrival)}开始 / ${at(q.loading.finish)}预计装完；货仍归仓库`:q.available>s.clock?'尚未出班 / 等待可出发时刻':!q.remaining.length?'无剩余配送任务，回仓 / 仓库等待':'模拟行驶中 / 尚无可回执到店任务')} ${n?`<button data-receipt="${actions[n.status][0]}" data-vehicle="${vid}" data-order="${n.id}" ${dispatchBusy?'disabled':''}>${actions[n.status][1]}</button>`:''}</p>`;
 }).join('')+`<p>人工模拟签收 ${Object.values(s.cargo).filter(x=>x==='delivered').length} 单；回执 ${s.receipts.length} 条。未签收前车辆等待，推进时间不会跳过本站。</p>`+
 '<p>最近回执：'+(s.receipts.slice(-9).map(r=>`${at(r.time)} ${r.vehicle}/${r.order} ${({arrived:'到达',unloading:'开始卸货',signed:'完成签收'})[r.receipt]}（人工模拟）`).join('；')||'暂无')+'</p>';
 host.querySelectorAll('button').forEach(b=>b.onclick=()=>dispatchAction('receipt',{vehicle:b.dataset.vehicle,order:b.dataset.order,receipt:b.dataset.receipt,time:minutes(byId('executionTime').value),source:'manual_simulation'}));
}
byId('refreshDispatch').onclick=loadDispatch;
byId('downloadDispatch').onclick=()=>{const u=URL.createObjectURL(new Blob([JSON.stringify(dispatchScene,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=u;a.download='continuous-dispatch.json';a.click();URL.revokeObjectURL(u)};

calculate().then(loadDispatch);loadAIStatus();

function matrixView(p,states){
 const esc=dispatchEscape,rule=p.data.settings.window_rule==='finish'?'卸货完成':'到达';
 return `<p class="notice">导入数据 · ${p.data.orders.length}单 / ${p.data.vehicles.length}车 · 收货承诺及截止按${rule}判断。静态有向道路表；车在路上时先走完当前路段，不推测中途改道。没有GPS或地图坐标。</p><p>来源声明：${esc(p.data.settings.data_kind)}；${esc(p.data.settings.travel_source)}。声明不等于企业真实性验证。</p>`+
 p.data.vehicles.map(v=>{const q=states[v.id],r=p.routes[v.id];return `<article class="vehicle"><b>${esc(v.id)} · ${q.transit?`${esc(q.transit.from_node)} → ${esc(q.transit.to)}（模拟途中，剩余${q.transit.travel_minutes.toFixed(2)}分钟）`:esc(q.node)}</b><p>班次 ${at(v.shift_start)}–${at(v.shift_end)}；预计回仓 ${at(r.return_at)}。当前货物 ${q.weight_kg.toFixed(2)} kg / ${q.volume_m3.toFixed(3)} m³。</p><p>车上：${esc(q.cargo.join('、')||'无')}；已签收：${esc(q.completed.filter(x=>!dispatchPickup(x.id)).map(x=>x.id).join('、')||'无')}。</p><p>剩余：${esc(r.sequence.slice(q.completed.length).map(dispatchRouteLabel).join(' → ')||'回仓 / 等待')}。</p><p>${r.stops.filter(x=>!dispatchPickup(x.id)).map(x=>`${esc(x.id)} ${x.signed_at!=null?'回执到达':'预计到达'} ${at(x.arrival)} / ${x.signed_at!=null?'签收':'预计完成'} ${at(x.finish)}`).join('；')}</p></article>`}).join('')+
 '<div class="table-wrap"><table><thead><tr><th>订单</th><th>箱数 / SKU行</th><th>重量 / 体积</th><th>卸货分钟 / 准入</th></tr></thead><tbody>'+p.data.orders.map(o=>`<tr><td>${esc(o.id)}</td><td>${o.lines.reduce((n,x)=>n+x.quantity,0)} / ${o.lines.length}</td><td>${o.unit_weight_kg.toFixed(2)} kg / ${o.unit_volume_m3.toFixed(3)} m³</td><td>${o.service_minutes} / ${esc(o.allowed_vehicles.join('、'))}</td></tr>`).join('')+'</tbody></table></div>';
}
async function startImported(sample=false){
 if(dispatchBusy)return;
 const status=byId('importStatus');status.textContent='正在读取并校验输入…';
 try{
  let files={};
  if(sample){const r=await fetch('/api/dispatch/sample-files');if(!r.ok)throw Error('样例读取失败');files=await r.json();}
  else {const chosen=[...byId('csvFiles').files];if(chosen.length!==4||new Set(chosen.map(f=>f.name)).size!==4)throw Error('请一次选择四份不同文件');if(chosen.reduce((n,f)=>n+f.size,0)>2000000)throw Error('文件合计不能超过2MB');for(const f of chosen)files[f.name]=await f.text();}
  const ok=await dispatchAction('start',{csv_files:files});
  status.textContent=ok?'已保存导入正常计划；请核对路线，再推进模拟及提交分步回执。':'导入未保存，原因见连续场景状态。旧场景保留。';
  if(ok){byId('executionTime').value=at(dispatchScene.clock);byId('windowTime').value=at(dispatchScene.clock);byId('urgentTime').value=at(dispatchScene.clock);}
 }catch(e){status.textContent='未导入：'+e.message;}
}
byId('importDispatch').onclick=()=>startImported(false);
byId('sampleDispatch').onclick=()=>startImported(true);

async function interpretCSVAI(){
 if(dispatchBusy||calculating||recovering||!dispatchScene)return;
 const token=`${dispatchScene.id}:${dispatchScene.revision}`;
 aiDraft=null;byId('aiReviewAck').checked=false;recovering=true;enable();
 byId('aiPreview').hidden=true;byId('aiStatus').textContent='正在按保存的CSV场景理解消息；不保存候选或改变路线…';
 try{
  const response=await fetch('/api/dispatch/interpret',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:dispatchScene.id,revision:dispatchScene.revision,message:byId('aiMessage').value,context:aiContext})});
  const result=await response.json();if(!response.ok)throw Error(result.error+(response.status===409?'；请刷新保存状态':''));
  if(token!==`${dispatchScene.id}:${dispatchScene.revision}`||token!==`${result.scene_id}:${result.scene_revision}`)throw Error('保存状态已变化，需重新理解');
  aiContext=result.context;byId('aiPreview').hidden=false;
  byId('aiPreview').textContent=csvAIReadable(result.draft);
  if(result.ready){aiDraft=result;byId('aiStatus').textContent='字段已通过当前CSV场景校验。请核对并勾选后试算；字段有效不代表路线可行。';}
  else byId('aiStatus').textContent='需要补问：'+result.questions.join(' ')+' 请补充后再次理解；路线未改变。';
 }catch(e){byId('aiStatus').textContent='未生成可执行事件：'+e.message+'。已采用计划保留。';}
 finally{recovering=false;enable();}
}
async function applyCSVAI(){
 if(dispatchBusy||calculating||recovering||!aiDraft||!byId('aiReviewAck').checked)return;
 const result=aiDraft;aiDraft=null;byId('aiReviewAck').checked=false;enable();
 if(result.scene_id!==dispatchScene.id||result.scene_revision!==dispatchScene.revision){byId('aiStatus').textContent='AI事件对应旧状态，请刷新并重新理解。';return;}
 const {type,...event}=result.event;const kind=type==='urgent_order'?'urgent':'window';
 const prefix=kind==='urgent'?'urgent':'window';
 for(const key of ['time','store','due','close'])byId(prefix+key[0].toUpperCase()+key.slice(1)).value=key==='store'?event[key]:at(event[key]);
 if(kind==='urgent')byId('urgentLines').value=event.lines.map(x=>[x.sku,x.quantity,x.unit_weight_kg,x.unit_volume_m3].join(',')).join('\n');
 else byId('windowOpen').value=at(event.open);
 const ok=await dispatchAction('event',{kind,event});
 byId('aiStatus').textContent=ok?'核对后的AI事件已生成候选，请在连续调度区核对路线、费用及迟到，再决定是否采用。':'候选未生成，原因见连续调度状态；已采用计划保留。';
}
byId('aiReviewAck').onchange=enable;
function csvAIReadable(d){
 const value=x=>x==null?'待补充':String(x);
 if(d.type==='unknown')return '事件尚不明确，请按补问澄清。';
 const lines=[`事件：${d.type==='urgent_order'?'仓库新增紧急单':'订单收货变化'}`,`发生时间：${value(d.time)}`,`${d.type==='urgent_order'?'目的门店':'订单编号'}：${value(d.store)}`];
 if(d.type==='urgent_order'){
  lines.push(`取货地点：${d.pickup_location==='warehouse'?'仓库':'待确认'}`);
  if(!d.lines)lines.push('SKU明细：待补充');
  else for(const row of d.lines)lines.push(`${value(row.sku)}：${value(row.quantity)}箱，每箱 ${value(row.unit_weight_kg)} kg / ${value(row.unit_volume_m3)} m³`);
 }else lines.push(`新开门：${value(d.open)}`);
 lines.push(`承诺时间：${value(d.due)}`,`最晚截止：${value(d.close)}`);
 return lines.join('\n');
}
