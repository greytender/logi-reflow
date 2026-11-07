'use strict';
let selectedWarehouse='',selectedNode='',selectedVehicle='',agentTaskId='',activeJob=null,jobToken='',jobEvents=[],jobCursor=0,pollTimer=null,jobPolling=false,agentError='',lastPrompt='',oneTimeReply=null,showArchivedChat=false;
let providerAvailable=false;
const spark='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4L12 3Z" fill="currentColor"/></svg>';
const sendIcon='<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M10 16V4m-5 5 5-5 5 5" stroke="currentColor" stroke-width="1.8"/></svg>';
const stopIcon='<svg viewBox="0 0 14 14" aria-hidden="true"><rect x="2" y="2" width="10" height="10" rx="1.5" fill="currentColor"/></svg>';
const terminalJob=s=>['completed','failed','cancelled','interrupted'].includes(s);
// Display aliases never change the identifiers sent to tools or stored in the task.
function displayAgentText(value){
 const text=String(value??'');if(!task?.data)return text;
 const aliases=new Map();
 for(const [items,format] of [[task.data.orders,displayOrderId],[task.data.vehicles,displayVehicleId],[task.data.warehouses,displayWarehouseId]])for(const item of items||[])if(item.id&&!/^\d+$/.test(String(item.id)))aliases.set(String(item.id),format(item.id));
 if(!aliases.size)return text;
 const ids=[...aliases.keys()].sort((a,b)=>b.length-a.length).map(id=>id.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'));
 const tokens=new RegExp('(^|[^A-Za-z0-9_-])('+ids.join('|')+')(?=$|[^A-Za-z0-9_-])','g');
 return text.replace(tokens,(_,prefix,id)=>prefix+aliases.get(id));
}
function jobStorageKey(){return 'logireflow.agent-job.'+task.id;}
function selectedContext(){const selection={warehouse_id:selectedWarehouse||task?.data.warehouses[0]?.id};if(selectedVehicle)selection.vehicle_id=selectedVehicle;if(selectedNode)selection.order_id=selectedNode;return selection;}
async function loadAgentStatus(){
 try{providerAvailable=!!(await api('agent-status')).provider?.configured;}catch{providerAvailable=false;}
 $('agentConnection').textContent=providerAvailable?'本机工具可用 · 已连接模型':'本机工具可用';
 const option=$('agentMode')?.querySelector('[value="model"]');
 if(option){option.disabled=!providerAvailable;if(!providerAvailable)$('agentMode').value='local';}
}
function ensureComposer(){
 if(agentTaskId===task.id&&$('agentChatForm'))return;
 agentTaskId=task.id;agentError='';oneTimeReply=null;showArchivedChat=false;
 $('assistantArea').innerHTML=`<div id="agentConversation" class="agent-conversation" role="log" aria-label="当前任务的协作记录"></div><div class="agent-composer-shell"><div id="selectionContext" class="selection-context"></div><div id="agentActions" class="agent-shortcuts" aria-label="常用调度操作"></div><form id="agentChatForm" class="agent-composer"><label class="sr-only" for="agentRequest">向调度助手发送请求</label><textarea id="agentRequest" maxlength="2000" placeholder="例如：比较两套方案" aria-label="向调度助手发送请求"></textarea><div class="composer-bottom"><label class="sr-only" for="agentMode">对话方式</label><select id="agentMode" title="本机工具在当前设备运行。选用模型时会发送请求和必要的批次摘要。"><option value="local">本机工具</option><option value="model" ${providerAvailable?'':'disabled'}>DeepSeek</option></select><div id="composerActions"><button class="primary composer-send" id="sendAgent" type="submit" aria-label="发送给调度助手">${sendIcon}</button></div></div></form><p id="agentProgress" class="agent-progress" role="status" aria-live="polite"></p></div>`;
 const saved=sessionStorage.getItem(jobStorageKey());if(saved){try{const data=JSON.parse(saved);if(data.task_id===task.id&&data.id&&data.token){activeJob={id:data.id,task_id:task.id,status:'recovering',suppressOneTime:true};jobToken=data.token;jobCursor=0;jobEvents=[];lastPrompt=data.message||'';lockForJob(true);schedulePoll(50);}}catch{sessionStorage.removeItem(jobStorageKey());}}
}
function resultCards(run){
 let html='';const compare=run.result?.compare;
 if(compare?.candidate){const m=compare.candidate;html+=`<section class="result-object"><div class="result-object-head">配送候选<span>${m.unserved?'有未安排任务':'待人工核对'}</span></div><div class="result-object-data"><div>车次<strong>${num(m.trips)}<small>趟</small></strong></div><div>运费试算<strong>${num(m.cost)}<small>元</small></strong></div><div>已安排<strong>${num(m.served)}<small>单</small></strong></div></div><button data-go="plan">打开方案核对 <span aria-hidden="true">→</span></button></section>`;}
 const adj=run.result?.adjustment;if(adj)html+=`<section class="result-object"><div class="result-object-head">路线调整<span>待确认</span></div><div class="result-rule-changes">运费试算 ${num(adj.before.cost)} → ${num(adj.after.cost)} 元</div><button data-go="plan">核对新方案 <span aria-hidden="true">→</span></button></section>`;
 const route=run.result?.route_review;
 if(route?.status==='available')html+=`<section class="result-object"><div class="result-object-head">${esc(displayVehicleId(route.vehicle_id))}<span>预计往返 ${num(route.distance_km)} 公里</span></div><div class="result-rule-changes">${route.sequence.map(id=>esc(displayOrderId(id))).join(' → ')}</div>${route.plan_id===planNow()?.id?`<button data-agent-route="${esc(route.vehicle_id)}" data-agent-warehouse="${esc(route.warehouse_id)}">查看车次 <span aria-hidden="true">→</span></button>`:''}</section>`;
 const spatial=run.result?.spatial_inspect;
 if(spatial?.status==='available'&&spatial.visual_context?.plan_id===planNow()?.id)html+=`<section class="result-object"><div class="result-object-head">装卸作业<span>${num(spatial.summary.timeline_frames||spatial.summary.load_steps)} 步</span></div><button data-agent-route="${esc(spatial.selected.vehicle_id)}" data-agent-warehouse="${esc(spatial.selected.warehouse_id)}" data-agent-destination="spatial">查看三维装卸 <span aria-hidden="true">→</span></button></section>`;
 const risks=run.result?.risks;if(risks)html+=`<section class="result-object"><div class="result-object-head">风险检查<span>${risks.items?.length||0} 项当前提醒</span></div><div class="result-rule-changes">${risks.items?.length?risks.items.slice(0,3).map(i=>`<p>${esc(displayAgentText(i.vehicle||i.task_id||i.warehouse_id||''))} · ${esc(displayAgentText(i.message))}</p>`).join(''):'已提供的信息中未发现异常。'}</div><button data-go="execute">查看执行与回收 <span aria-hidden="true">→</span></button></section>`;
 const rules=run.result?.rule_draft;if(rules){
  const changes=rules.changes||[],labels={base:'起步费',included_km:'起步公里',per_km:'每公里费用',per_stop:'每店服务费',mode:'计费方式',cartons:'纸箱数量',totes:'周转箱数量'};
  const text=changes.map(c=>typeof c==='string'?c:`${c.subject} · ${labels[c.field]||c.field}：${c.before} → ${c.after}`).join('\n');
  const currentFingerprint=planNow()?.input_sha256;
  const valid=rules.input_revision===task.input_revision&&(!currentFingerprint||rules.base_sha256===currentFingerprint)&&!mwFacts();
  html+=`<section class="result-object"><div class="result-object-head">业务规则草稿<span>${valid?'尚未应用':'草稿已失效'}</span></div><div class="result-rule-changes">${esc(displayAgentText(text||'请展开检查新的计费与包装参数。'))}</div>${valid?`<details class="rule-review"><summary>审阅并应用规则</summary><p>应用后原方案失效，需要重新计算。</p><label><input type="checkbox" data-rule-reviewed="${esc(run.id)}">已核对规则变更与来源</label><button data-apply-rules="${esc(run.id)}" disabled>应用这份规则草稿</button></details>`:`<div class="rule-review"><p>${mwFacts()?'已有执行记录，本草稿不可应用。':'输入已变化，这份草稿不能再应用。'}可查看原修改记录。</p></div>`}</section>`;
 }
 return html;
}
function renderAgent(){
 if(!task)return;ensureComposer();
 const log=$('agentConversation'),nearBottom=log.scrollHeight-log.scrollTop-log.clientHeight<100;
 const all=(task.agent_runs||[]).filter(r=>!r.chat_deleted);
 const archived=all.filter(r=>r.chat_archived);
 const history=all.filter(r=>showArchivedChat? r.chat_archived : !r.chat_archived);
 log.innerHTML=history.length?history.map(r=>`${r.user_message?`<div class="chat-user">${esc(displayAgentText(r.user_message))}</div>`:''}<article class="chat-assistant"><div class="assistant-name">${spark}${r.model_connected?'DeepSeek':'本地工具'}</div><div class="answer-text">${esc(displayAgentText(r.message))}</div>${resultCards(r)}${r.requested_workflow==='request'?`<div class="chat-record-actions"><button data-chat-record="${esc(r.id)}" data-chat-action="${r.chat_archived?'restore':'archive'}">${r.chat_archived?'移回记录':'归档'}</button><button data-chat-record="${esc(r.id)}" data-chat-action="delete">删除</button></div>`:''}<details><summary>计算依据 · ${r.traces.length} 项</summary><ol class="process-list">${r.traces.map(t=>`<li>${esc(toolTitle(t.tool))} · ${esc(t.status==='completed'?'完成':t.status==='blocked'?'未执行':t.status)}${t.reason?' · '+esc(displayAgentText(t.reason)):''}</li>`).join('')}</ol><p>依据修订 ${r.input_ref.task_revision}，结果修订 ${r.resulting_revision}。${r.resulting_revision===task.revision?'':'这是历史记录，任务之后已有变化。'}</p>${r.model_calls?.length?`<p>模型调用 ${r.model_calls.length} 次 · ${num(r.model_calls.reduce((n,c)=>n+(c.usage?.total_tokens||0),0))} tokens</p>`:''}</details></article>`).join(''):`<div class="agent-idle">${spark}<h3>调度助手</h3><p>可直接核对订单、算运费或调整回收量。</p></div>`;
 if(archived.length)log.insertAdjacentHTML('afterbegin',`<button class="chat-archive-toggle" data-chat-archive>${showArchivedChat?'返回对话':'已归档 · '+archived.length}</button>`);
 if(oneTimeReply&&!showArchivedChat)log.insertAdjacentHTML('beforeend',`<div class="chat-user">${esc(displayAgentText(oneTimeReply.prompt))}</div><article class="chat-assistant"><div class="assistant-name">${spark}调度助手</div><div class="answer-text">${esc(displayAgentText(oneTimeReply.message))}</div></article>`);
 if(activeJob&&!terminalJob(activeJob.status)){log.insertAdjacentHTML('beforeend',`<div id="liveUser" class="chat-user">${esc(displayAgentText(lastPrompt||'恢复上次请求'))}</div><article id="liveAssistant" class="chat-assistant"><div class="assistant-name">${spark}${activeJob.mode==='model'?'DeepSeek':'本机工具'}</div><div id="liveProcess"></div><div id="liveAnswer" class="answer-text"></div></article>`);renderJobEvents();}
 if(agentError)log.insertAdjacentHTML('beforeend',`<p class="agent-error" role="alert">${esc(displayAgentText(agentError))}</p>`);
 if(nearBottom||!history.length)log.scrollTop=log.scrollHeight;
 if(step==='overview')renderOverview();else updateSelection();
}
function toolTitle(tool){const titles={inspect_current_batch:'检查当前批次',calculate_warehouse_candidates:'计算配车与路线',audit_and_compare:'独立核验与费用比较',inspect_business_risks:'检查业务风险',model_explanation:'生成结果解释',run_recovery_trial:'比较局部修复与情景 ALNS',audit_constraint_coverage:'逐项核对约束覆盖',inspect_vehicle_space:'检查车厢与装卸状态'};return titles[tool]||tool.replace(/^calculate_remaining_/,'计算剩余路线 · ');}
function updateSelection(){
 if(!$('selectionContext')||!task)return;
 const wh=task.data.warehouses.find(w=>w.id===selectedWarehouse)||task.data.warehouses[0];
 $('selectionContext').innerHTML=`<span class="selection-pill">${esc(displayWarehouseId(wh.id))}</span>${selectedVehicle?`<span class="selection-pill">${esc(displayVehicleId(selectedVehicle))}</span>`:''}${selectedNode?`<span class="selection-pill">${esc(displayOrderId(selectedNode))}</span>`:''}`;
 const commands=planNow()?[['比较方案','比较两套方案'],...(selectedVehicle||selectedNode?[['核对车次','核对本车'],['查看装卸','查看本车装卸']]:[]),['待处理','查看待处理']]:[['核对订单','核对这批订单'],['排车','计算配送方案']];
 $('agentActions').innerHTML=commands.map(([label,prompt])=>`<button type="button" data-agent-command="${prompt}">${label}</button>`).join('');
}
let overviewMap=null,overviewMapGeneration=0;
function resetWorkspaceSelection(){
 selectedWarehouse='';selectedVehicle='';selectedNode='';
 overviewMapGeneration++;overviewMap?.destroy();overviewMap=null;
}
function renderOverview(){
 if(!task)return;const wh=task.data.warehouses.find(w=>w.id===selectedWarehouse)||task.data.warehouses[0];selectedWarehouse=wh.id;
 const p=planNow(),result=p?.warehouses[wh.id],orders=task.data.orders.filter(o=>o.warehouse_id===wh.id),vehicles=task.data.vehicles.filter(v=>v.warehouse_id===wh.id),colors=['#6d9454','#8b7baa','#bd9560','#5e9398'];
 if(!vehicles.some(v=>v.id===selectedVehicle))selectedVehicle='';if(!orders.some(o=>o.id===selectedNode))selectedNode='';
 const routes=result?vehicles.map((v,i)=>({vehicle:v,route:result.optimized.routes[v.id],color:colors[i%colors.length]})).filter(r=>r.route.sequence.length):[];
 for(const r of routes)r.journey_url='/journey.html?'+new URLSearchParams({id:task.id,warehouse_id:wh.id,vehicle_id:r.vehicle.id,plan_id:p.id});
 const routeByOrder=new Map();routes.forEach(r=>r.route.sequence.forEach((id,i)=>routeByOrder.set(id,{...r,stop:i+1})));
 const summary=p?.summary.optimized,chosen=orders.find(o=>o.id===selectedNode),v=vehicles.find(v=>v.id===selectedVehicle),route=v&&(result?.optimized.routes[v.id]?.operational?.adjusted_route||result?.optimized.routes[v.id]);
 let detail='';if(chosen){const rr=routeByOrder.get(chosen.id),weight=chosen.lines.reduce((n,l)=>n+l.quantity*l.unit_weight_kg,0),volume=chosen.lines.reduce((n,l)=>n+l.quantity*l.unit_volume_m3,0);detail=`<div class="detail-head"><h3>${esc(orderName(chosen.id))}</h3><div class="detail-tags"><span class="detail-tag">${rr?(state.confirmed?'原计划 · 第':'已安排 · 第')+rr.stop+'站':'待安排'}</span><span class="detail-tag">${esc(rr?displayVehicleId(rr.vehicle.id):displayWarehouseId(wh.id))}</span></div></div><dl class="detail-grid"><div><dt>货品与箱数</dt><dd>${chosen.lines.length} 类 / ${num(chosen.lines.reduce((n,l)=>n+l.quantity,0))} 箱</dd></div><div><dt>货重 / 体积</dt><dd>${num(weight)} kg / ${num(volume)} m³</dd></div><div><dt>预计空包装</dt><dd>${chosen.pickup?chosen.pickup.cartons+' 纸箱 / '+chosen.pickup.totes+' 周转箱':'未启用'}</dd></div></dl><div class="detail-actions"><button data-agent-command="核对本车">检查门店风险</button><button data-go="prepare">查看订单与规则</button></div>`;}
 else if(v&&route){const actual=state.execution?.[wh.id]?.routes?.[v.id],original=state.confirmed;detail=`<div class="detail-head"><h3>${esc(displayVehicleId(v.id))}</h3><span class="detail-tag">${original?'出发原计划':'出发候选路线'} · ${route.sequence.length} 站</span></div><dl class="detail-grid"><div><dt>${original?'原计划运费试算':'运费试算'}</dt><dd>${num(route.cost)} 元</dd></div><div><dt>${original?'原计划道路里程':'道路里程'}</dt><dd>${num(route.distance)} km</dd></div><div><dt>${original?'原预计返仓':'预计返仓'}</dt><dd>${time(route.return_at)}</dd></div></dl>${utilization(original?'原计划出发载重':'出发载重',route.weight_kg,v.max_weight_kg,'kg')}${utilization(original?'原计划出发容积':'出发容积',route.volume_m3,v.max_volume_m3,'m³')}${currentVehicleForecast(actual)}<div class="detail-actions"><button data-agent-command="核对本车">解释车辆方案</button><button data-go="plan">查看出发方案</button></div>`;}
 else detail=`<div class="detail-head"><h3>${result?'路线与任务':'待排车'}</h3><span class="detail-tag">${task.intake_source?.source_sha256==='40849d76aceaf3589ac68fdad258c847ed0bbf464e65b76a468258bc36726587'?'中央仓直配':wh.settings.data_kind==='simulated'?'演示数据':'业务数据，待核对'}</span></div><p>${state.confirmed?'出发原计划；剩余预测见车辆详情。':result?'点选车辆或门店查看详情。':'订单与规则已载入，等待计算。'}</p><div class="detail-actions"><button data-go="prepare">核对订单与规则</button><button data-agent-command="计算配送方案" ${mwFacts()?'disabled':''}>助手排车</button>${result?'<button data-go="plan">打开方案核对</button>':''}</div>`;
 const mapView=overviewMap?.getState?.().view||'top';const generation=++overviewMapGeneration;overviewMap?.destroy();overviewMap=null;
 $('routeOverview').innerHTML=`<div class="overview-heading"><div><h2>${state.confirmed?'出发原计划路线':'配送线路'}</h2><p>选择车辆，查看门店顺序与沿途装卸。</p></div><label>仓库<select id="overviewWarehouse">${task.data.warehouses.map(w=>`<option value="${esc(w.id)}" ${w.id===wh.id?'selected':''}>${esc(displayWarehouseId(w.id))}</option>`).join('')}</select></label></div><div class="overview-kpis"><div>配送任务<strong>${orders.length}<small>单</small></strong></div><div>使用车次<strong>${routes.length||'—'}<small>趟</small></strong></div><div>${state.confirmed?'原计划运费试算':'本仓运费试算'}<strong>${result?num(result.optimized.metrics.cost):'—'}<small>元</small></strong></div><div>未安排<strong>${result?result.optimized.unserved.length:'—'}<small>单</small></strong></div></div><div id="routeMap3d" class="route-map-host"></div><div class="network-detail">${detail}</div>`;
 const names={orders:Object.fromEntries(task.data.orders.map(o=>[o.id,orderName(o.id)])),vehicles:Object.fromEntries(task.data.vehicles.map(v=>[v.id,displayVehicleId(v.id)])),warehouses:Object.fromEntries(task.data.warehouses.map(w=>[w.id,displayWarehouseId(w.id)]))};
 if(window.LogiRouteMap3D)LogiRouteMap3D.mount($('routeMap3d'),{warehouse:wh,orders,routes,selectedVehicle,selectedOrder:selectedNode,names,view:mapView},{onOrder:id=>{selectedNode=id;const rr=routeByOrder.get(id);if(rr)selectedVehicle=rr.vehicle.id;renderOverview();},onVehicle:id=>{selectedVehicle=id;selectedNode='';renderOverview();$('routeOverview').querySelector('[data-map-filter]')?.focus();},onAll:()=>{selectedVehicle='';selectedNode='';renderOverview();$('routeOverview').querySelector('[data-map-filter]')?.focus();},onLoad:id=>{selectedVehicle=id;selectedNode='';showStep('spatial');},onExpand:()=>{if($('toggleAgent').getAttribute('aria-expanded')==='true')$('toggleAgent').click();$('routeOverview').scrollIntoView({block:'start'});}}).then(viewer=>{if(generation!==overviewMapGeneration)viewer.destroy();else overviewMap=viewer;});
 updateSelection();
}
function utilization(label,used,max,unit){const ratio=max?used/max:0;return `<div class="utilization"><div class="utilization-label"><span>${label}</span><span>${num(used)} / ${num(max)} ${unit} · ${pct(ratio)}</span></div><div class="utilization-track"><span style="width:${Math.min(100,Math.max(0,ratio*100))}%"></span></div></div>`;}
function currentVehicleForecast(actual){
 if(!actual)return '';
 if(actual.returned)return `<section id="currentVehicleForecast" class="current-vehicle-forecast"><h4>已记录返仓交接</h4><p>回执已保存；上图为出发原计划。</p>${actual.warnings.length?`<div class="unserved">${actual.warnings.map(w=>`<p>${esc(displayAgentText(w))}</p>`).join('')}</div>`:''}</section>`;
 return `<section id="currentVehicleForecast" class="current-vehicle-forecast"><h4>当前剩余预测</h4><p>按最新执行记录更新</p><dl class="detail-grid"><div><dt>预测起算节点 / 时刻</dt><dd>${esc(displayNodeId(actual.anchor.node))} · ${time(actual.anchor.time)}</dd></div><div><dt>下一步</dt><dd>${esc(actual.next_stop?displayOrderId(actual.next_stop):'返仓交接')}</dd></div><div><dt>当前预计返仓</dt><dd>${time(actual.estimated_return)}</dd></div><div><dt>剩余道路里程</dt><dd>${num(actual.remaining_distance_km)} km</dd></div><div><dt>当前估算载重</dt><dd>${num(actual.onboard_weight_kg)} kg</dd></div><div><dt>当前估算容积</dt><dd>${num(actual.onboard_volume_m3)} m³</dd></div></dl><p class="current-remaining-sequence">剩余顺序：${actual.remaining.length?esc(actual.remaining.map(s=>displayOrderId(s.id)).join(' → '))+' → 返仓':'返仓交接'}</p>${actual.warnings.length?`<div class="unserved"><h3>当前风险</h3>${actual.warnings.map(w=>`<p>${esc(displayAgentText(w))}</p>`).join('')}</div>`:''}<div class="detail-actions"><button data-go="execute">查看执行记录</button></div></section>`;
}
function lockForJob(lock){
 busy=lock;
 document.querySelectorAll('#main button,#main input,#main select,#main textarea,.task-sidebar button,.task-sidebar input').forEach(e=>{
  if(lock){if(e.dataset.agentWasDisabled===undefined)e.dataset.agentWasDisabled=e.dataset.operationWasDisabled??String(e.disabled);e.disabled=true;}
  else if(e.dataset.agentWasDisabled!==undefined){e.disabled=e.dataset.agentWasDisabled==='true';delete e.dataset.agentWasDisabled;}
 });
 if(!lock&&state){const facts=mwFacts();$('saveBatch').disabled=facts;$('solveBatch').disabled=facts;if(facts)$('editor').querySelectorAll('input,select,textarea,button').forEach(e=>e.disabled=true);}
 if($('agentRequest'))$('agentRequest').disabled=lock;renderComposerActions();
}
function renderComposerActions(){if(!$('composerActions'))return;const running=activeJob&&!terminalJob(activeJob.status);$('composerActions').innerHTML=running?`<button class="composer-send stop" id="cancelAgent" type="button" aria-label="停止当前 AI 请求" ${!activeJob.id||['cancelling','committing'].includes(activeJob.status)?'disabled':''}>${stopIcon}</button>`:`<button class="primary composer-send" id="sendAgent" type="submit" aria-label="发送给调度助手">${sendIcon}</button>`;}
function renderJobEvents(){if(!$('liveProcess'))return;const display=jobEvents.filter(e=>e.type!=='answer_delta'),last=display.at(-1);$('liveProcess').innerHTML=`<div class="process-summary running"><i></i>${esc(displayAgentText(last?.title||'正在处理'))}</div><details><summary>执行过程 · ${display.length} 项</summary><ol class="process-list">${display.map(e=>`<li data-type="${esc(e.type)}">${esc(displayAgentText(e.title||e.type))}</li>`).join('')}</ol></details>`;const answer=jobEvents.filter(e=>e.type==='answer_delta').map(e=>e.details?.text||e.details?.delta||'').join('');$('liveAnswer').textContent=displayAgentText(answer);const log=$('agentConversation');if(log.scrollHeight-log.scrollTop-log.clientHeight<180)log.scrollTop=log.scrollHeight;}
async function jobApi(path,body){const r=await fetch('/api/multiwarehouse/agent/'+path,body===undefined?{headers:{'X-Agent-Job-Token':jobToken}}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});let value;try{value=await r.json();}catch{throw Error('无法读取本机服务的回复。');}if(!r.ok){const error=Error(value.error||'请求未完成');error.status=r.status;throw error;}return value;}
function schedulePoll(delay=650){clearTimeout(pollTimer);pollTimer=setTimeout(pollJob,delay);}
async function pollJob(){if(jobPolling||!activeJob||terminalJob(activeJob.status))return;jobPolling=true;try{const value=await jobApi('job?job_id='+encodeURIComponent(activeJob.id)+'&after='+jobCursor);const job=value.job;for(const event of job.events||[]){if(!jobEvents.some(e=>e.seq===event.seq))jobEvents.push(event);}jobCursor=job.cursor??Math.max(0,...jobEvents.map(e=>e.seq));activeJob={...activeJob,...job};renderJobEvents();renderComposerActions();if(terminalJob(job.status)){await finishJob(job);}else schedulePoll();}catch(e){if([403,404].includes(e.status)){await finishJob({status:'interrupted',error:'无法恢复这次请求，请刷新任务后重新发起；没有自动重复调用模型。'});}else{agentError='进度连接中断：'+e.message+'。正在重试，不会重复提交任务。';if($('agentProgress'))$('agentProgress').textContent=agentError;schedulePoll(2500);}}finally{jobPolling=false;}}
async function finishJob(job){
 sessionStorage.removeItem(jobStorageKey());clearTimeout(pollTimer);lockForJob(false);
 if(job.status==='completed'&&job.ephemeral){
  const showReply=!activeJob?.suppressOneTime;
  activeJob=null;agentError='';$('agentRequest').value='';
  oneTimeReply=showReply&&job.ephemeral_message?{prompt:lastPrompt,message:job.ephemeral_message}:null;
  renderAgent();
 }else if(job.status==='completed'&&job.result){
  activeJob=null;agentError='';$('agentRequest').value='';
  if(job.result.agent_run?.workflows?.some(w=>['dispatch','adjust'].includes(w)))step='plan';
  setTask(job.result);await refresh();
 }else{
  const cancelled=job.status==='cancelled';
  agentError=cancelled?'已停止请求。':job.error||'请求未完成，可以检查后重试。';
  activeJob=null;renderAgent();if($('agentRequest'))$('agentRequest').value=lastPrompt;
  notice(agentError,!cancelled);
 }
 renderComposerActions();if($('agentProgress'))$('agentProgress').textContent='';
}
async function sendAgent(message,mode=$('agentMode')?.value||'local'){
 if(busy||!task)return;
 if(dirty){notice('请先保存当前修改。',true);return;}
 message=message.trim();if(!message)return;
 lastPrompt=message;agentError='';oneTimeReply=null;showArchivedChat=false;
 activeJob={status:'starting',mode};lockForJob(true);$('agentProgress').textContent='正在提交…';
 try{
  const value=await jobApi('start',{id:task.id,revision:task.revision,message,mode,selection:selectedContext(),request_id:crypto.randomUUID()});
  activeJob={...value.job,suppressOneTime:activeJob?.suppressOneTime};jobToken=value.job_token;
  jobEvents=activeJob.events||[];jobCursor=activeJob.cursor||0;
  sessionStorage.setItem(jobStorageKey(),JSON.stringify({id:activeJob.id,token:jobToken,task_id:task.id,message}));
  renderAgent();renderComposerActions();$('agentProgress').textContent='';
  if(terminalJob(activeJob.status))await finishJob(activeJob);else schedulePoll(100);
 }catch(e){
  activeJob=null;lockForJob(false);agentError=e.message;renderAgent();
  $('agentRequest').value=message;notice(e.message,true);
 }
}
document.addEventListener('submit',e=>{if(e.target.id!=='agentChatForm')return;e.preventDefault();sendAgent($('agentRequest').value);});
document.addEventListener('keydown',e=>{if(e.target.id==='agentRequest'&&e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();sendAgent(e.target.value);}const node=e.target.closest?.('[data-order-node]');if(node&&(e.key==='Enter'||e.key===' ')){e.preventDefault();selectedNode=node.dataset.orderNode;selectedVehicle='';renderOverview();}});
document.addEventListener('change',e=>{if(e.target.id==='overviewWarehouse'){selectedWarehouse=e.target.value;selectedNode='';selectedVehicle='';renderOverview();}if(e.target.dataset.ruleReviewed){const run=e.target.dataset.ruleReviewed;document.querySelector(`[data-apply-rules="${run}"]`).disabled=!e.target.checked;}});
document.addEventListener('click',async e=>{const node=e.target.closest('[data-order-node]');if(node&&!busy){selectedNode=node.dataset.orderNode;selectedVehicle='';renderOverview();return;}const b=e.target.closest('button');if(!b)return;
 if(b.id==='cancelAgent'&&activeJob){b.disabled=true;try{const r=await jobApi('cancel',{job_id:activeJob.id,job_token:jobToken});activeJob={...activeJob,...r.job};if(terminalJob(activeJob.status))await finishJob(activeJob);else{if(r.cancel_accepted!==false)activeJob.status='cancelling';renderComposerActions();$('agentProgress').textContent=r.cancel_accepted===false?'正在保存结果，完成后停止。':'正在停止当前步骤…';schedulePoll(100);}}catch(err){notice(err.message,true);b.disabled=false;}return;}
 if(b.id==='toggleAgent'){const collapsed=document.querySelector('.app-layout').classList.toggle('agent-collapsed');b.setAttribute('aria-expanded',String(!collapsed));if(collapsed){if(activeJob)activeJob.suppressOneTime=true;if(oneTimeReply){oneTimeReply=null;renderAgent();}}return;}
 if(b.id==='agentSettings'){$('keySettings').open=!$('keySettings').open;return;}
 if(busy)return;
 if(b.dataset.agentCommand){sendAgent(b.dataset.agentCommand,'local');return;}
 if(b.dataset.agentRoute){
  selectedWarehouse=b.dataset.agentWarehouse;selectedVehicle=b.dataset.agentRoute;selectedNode='';
  showStep(b.dataset.agentDestination||'overview');
  updateSelection();return;
 }
 if(b.hasAttribute('data-chat-archive')){showArchivedChat=!showArchivedChat;renderAgent();return;}
 if(b.dataset.chatRecord){
  if(dirty){notice('请先保存当前修改，再整理对话记录。',true);return;}
  const action=b.dataset.chatAction;
  if(action==='delete'&&!confirm('删除这轮对话？对话内容会从记录和后续模型上下文移除，已生成的业务方案和执行记录保留。'))return;
  run(async()=>{
   const updated=await jobApi('chat-record',{id:task.id,revision:task.revision,run_id:b.dataset.chatRecord,action});
   if(showArchivedChat&&!updated.task.agent_runs.some(r=>r.chat_archived&&!r.chat_deleted))showArchivedChat=false;
   setTask(updated);
   notice({archive:'对话已归档',restore:'对话已移回记录',delete:'对话已删除'}[action]);
  });
  return;
 }
 if(b.dataset.route){selectedVehicle=b.dataset.route;selectedNode='';renderOverview();}
 if(b.dataset.prompt){document.querySelector('.app-layout').classList.remove('agent-collapsed');$('toggleAgent').setAttribute('aria-expanded','true');$('agentRequest').value=b.dataset.prompt;$('agentRequest').focus();}
 if(b.id==='welcomeImport'){$('importTask').open=true;$('batchFile').focus();}
 if(b.id==='welcomeSample')$('newBatch').click();
 if(b.id==='newPhysicalBatch')run(async()=>{if(!leaveDraft())return;const sample=await api('sample?physical=1');step='overview';setTask(await api('create',{name:'装载核对练习',data:sample.data}));await refresh();notice('');});
 if(b.dataset.applyRules)run(async()=>{const r=await jobApi('apply-rules',{id:task.id,revision:task.revision,run_id:b.dataset.applyRules,reviewed:true});step='prepare';setTask(r);await refresh();notice('规则草稿已应用，请重新计算与核对方案。');});
});
if(matchMedia('(max-width:1200px)').matches){document.querySelector('.app-layout').classList.add('agent-collapsed');$('toggleAgent').setAttribute('aria-expanded','false');}
loadAgentStatus();
if(new URLSearchParams(location.search).get('sample')==='physical'){let attempts=0;const launch=()=>{if(!busy){$('newPhysicalBatch').click();history.replaceState(null,'',location.pathname);}else if(attempts++<80)setTimeout(launch,100);};setTimeout(launch,100);}
