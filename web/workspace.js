'use strict';
let task=null, state=null, draft=null, dirty=false, busy=false, step='overview';
let orderPage=0, orderQuery='', roadOrigin='', executionVehicle='';
let reviewedRoute='', reviewedPlan='';
const ordersPerPage=12;
const $=id=>document.getElementById(id), esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const copy=v=>JSON.parse(JSON.stringify(v)), num=v=>Number(v??0).toLocaleString('zh-CN',{maximumFractionDigits:2}), pct=v=>num(v*100)+'%';
const time=v=>v==null?'':String(Math.floor(v/60)).padStart(2,'0')+':'+String(Math.floor(v%60)).padStart(2,'0');
function displayEntityId(id,collection,label){const value=String(id??'');const index=(task?.data?.[collection]||[]).findIndex(item=>String(item.id)===value);return index<0?value:label+(index+1);}
function displayOrderId(id){return displayEntityId(id,'orders','门店');}
function displayVehicleId(id){return displayEntityId(id,'vehicles','车辆');}
function displayWarehouseId(id){return displayEntityId(id,'warehouses','仓库');}
function displayNodeId(id){const value=String(id??'');return (task?.data?.warehouses||[]).some(w=>String(w.id)===value)?displayWarehouseId(value):displayOrderId(value);}
function sourceStore(id){return task?.intake_source?.stores?.find(store=>store.id===id);}
function orderName(id){return sourceStore(id)?.name||displayOrderId(id);}
function warehouseName(w){return w.name||displayWarehouseId(w.id);}
function nodeName(id){const w=task?.data.warehouses.find(item=>item.id===id);return w?warehouseName(w):orderName(id);}
function sourceLink(){return task?.intake_source?'/intake.html#intake='+encodeURIComponent(task.intake_source.document_id):'';}
function resolveDisplayId(value,collection,label){const text=String(value??'').trim();const items=task?.data?.[collection]||draft?.[collection]||[];if(items.some(item=>String(item.id)===text))return text;const match=text.match(new RegExp('^'+label+'([1-9]\\d*)$'));return match&&items[Number(match[1])-1]?items[Number(match[1])-1].id:text;}
function resolveOrderId(value){return resolveDisplayId(value,'orders','门店');}
function resolveVehicleId(value){return resolveDisplayId(value,'vehicles','车辆');}
function displayInputLabel(label){let text=String(label??'');const entries=[...(task?.data?.orders||[]).map(x=>[x.id,displayOrderId(x.id)]),...(task?.data?.vehicles||[]).map(x=>[x.id,displayVehicleId(x.id)]),...(task?.data?.warehouses||[]).map(x=>[x.id,displayWarehouseId(x.id)])].sort((a,b)=>String(b[0]).length-String(a[0]).length);for(const [id,name] of entries){if(text===id||text.startsWith(id+' ' )||text.startsWith(id)&&/[\u3400-\u9fff]/.test(text.charAt(String(id).length))){text=name+text.slice(String(id).length);break;}}return text;}
function displayRoadId(id){return String(id).split('>').map(displayNodeId).join(' → ');}
function notice(text,error=false){$('notice').hidden=!text;$('notice').textContent=text;$('notice').classList.toggle('error',error);$('notice').setAttribute('role',error?'alert':'status');}
async function api(path,body){const r=await fetch('/api/multiwarehouse/'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});let value;try{value=await r.json();}catch{throw Error('服务响应异常，请检查本机服务。');}if(!r.ok)throw Error(value.error||'请求失败，请刷新。');return value;}
async function run(work){
 if(busy)return;busy=true;const previous=new WeakMap();
 document.querySelectorAll('button,input,select,textarea').forEach(e=>{previous.set(e,e.disabled);e.dataset.operationWasDisabled=String(e.disabled);e.disabled=true;});
 $('main').setAttribute('aria-busy','true');
 try{await work();}catch(e){notice(e.message,true);}
 finally{
  busy=false;$('main').removeAttribute('aria-busy');
  document.querySelectorAll('button,input,select,textarea').forEach(e=>{if(previous.has(e))e.disabled=previous.get(e);delete e.dataset.operationWasDisabled;});
  if(state){const locked=state.receipts>0||Object.values(task.execution_tasks||{}).some(t=>(t.observations?.length||t.temperature_records?.length));$('saveBatch').disabled=locked;$('solveBatch').disabled=locked;syncLifecycleControls();}
  if(typeof lockForJob==='function'&&activeJob&&!terminalJob(activeJob.status))lockForJob(true);
 }
}
function setTask(result){
 if(task?.id!==result.task.id){orderPage=0;orderQuery='';roadOrigin='';executionVehicle='';if(typeof resetWorkspaceSelection==='function')resetWorkspaceSelection();}
 task=result.task;state=result.state;draft=copy(task.data);dirty=false;
 localStorage.setItem('logireflow.multiwarehouse.active',task.id);
 const url=new URL(location.href);
 if(url.searchParams.get('task')!==task.id){url.searchParams.set('task',task.id);history.replaceState(history.state,'',url);}
 render();
}
function resumeStep(result){return result.state.confirmed?'execute':result.state.current_plan_id?'plan':'prepare';}
async function openBatch(id){
 const result=await api('task?id='+encodeURIComponent(id));step=resumeStep(result);setTask(result);
}
async function refresh(){
 const data=await api('tasks');
 $('batches').innerHTML=data.tasks.length?data.tasks.map(t=>`<button data-open="${esc(t.id)}" title="${esc(t.name)} · ${esc(t.status)}" ${t.id===task?.id?'aria-current="true"':''}><strong>${esc(t.name)}</strong><span>${t.orders} 个配送任务${t.warehouses>1?' · '+t.warehouses+' 仓':''}</span><small>${esc(t.status)}</small></button>`).join(''):'<p class="muted">还没有批次</p>';
 for(const details of document.querySelectorAll('[data-lifecycle-list][open]'))await refreshOrganized(details.dataset.lifecycleList);
}
async function refreshOrganized(lifecycle){
 const host=$(lifecycle==='archived'?'archivedBatches':'trashBatches');
 const data=await api('tasks?lifecycle='+lifecycle);
 host.innerHTML=data.tasks.length?data.tasks.map(row=>`<div><button class="organized-batch-name" data-open="${esc(row.id)}" title="${esc(row.name)}">${esc(row.name)}</button><button class="quiet" data-restore-batch="${esc(row.id)}" data-batch-revision="${row.revision}">恢复</button></div>`).join(''):'<p class="muted">暂无批次</p>';
}
function lifecycleLocked(){return !!state&&state.lifecycle&&state.lifecycle!=='active';}
function syncLifecycleControls(){
 const locked=lifecycleLocked();
 document.querySelectorAll('#editor input,#editor select,#editor textarea,#editor button,#saveBatch,#solveBatch,#adoptBatch,#reviewPlan,#manualRoutes input,#manualPreview,#execute input,#execute select,#execute textarea,#execute button,#agentRequest,#agentMode,#sendAgent,#agentActions button').forEach(el=>{
  if(locked){
   if(el.dataset.lifecycleWasDisabled===undefined)el.dataset.lifecycleWasDisabled=el.dataset.operationWasDisabled??String(el.disabled);
   el.disabled=true;
  }else if(el.dataset.lifecycleWasDisabled!==undefined){
   el.disabled=el.dataset.lifecycleWasDisabled==='true';
   if(!busy)delete el.dataset.lifecycleWasDisabled;
  }
 });
}
function renderBatchActions(){
 const locked=lifecycleLocked();
 $('batchActionItems').innerHTML=locked?'<button data-organize="restore" type="button">恢复批次</button>':'<button data-organize="archive" type="button">归档批次</button><button data-organize="trash" type="button">移至回收站</button>';
 if(locked)$('saveState').textContent=state.lifecycle==='archived'?'已归档 · 可恢复':'回收站 · 可恢复';
 syncLifecycleControls();
}
function leaveDraft(){return !dirty||confirm('当前修改尚未保存。离开后会丢失这些修改，是否继续？');}
function pathValue(path){return path.reduce((o,k)=>o?.[k],draft);}
function input(path,label,type='number',extra=''){const v=pathValue(path);return `<input aria-label="${esc(displayInputLabel(label))}" data-path="${esc(JSON.stringify(path))}" data-kind="${type}" type="${type==='clock'?'time':type}" value="${esc(type==='clock'?time(v):v)}" ${type==='number'?'step="any" min="0"':''} ${extra}>`;}
function select(path,label,options){return `<select aria-label="${esc(displayInputLabel(label))}" data-path="${esc(JSON.stringify(path))}" data-kind="select">${options.map(([v,name])=>`<option value="${esc(v)}" ${pathValue(path)===v?'selected':''}>${esc(name)}</option>`).join('')}</select>`;}
function table(headers,rows){return `<div class="table-scroll"><table><thead><tr>${headers.map(h=>`<th scope="col">${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;}
function markDirty(){
 dirty=true;$('saveState').textContent='修改尚未保存';$('stageHint').textContent='输入已修改 · 需重新排车';$('stageHint').hidden=false;
 for(const link of document.querySelectorAll('a[data-plan-export]')){
  link.removeAttribute('href');link.setAttribute('aria-disabled','true');
 }
 if($('adoptBatch'))$('adoptBatch').disabled=true;
}
function planNow(){return task?.plans.find(p=>p.id===state.current_plan_id);}
function showStep(next){
 const changed=!document.querySelector(`[data-panel="${next}"]:not([hidden])`);
 step=next;document.querySelectorAll('[data-panel]').forEach(e=>e.hidden=e.dataset.panel!==next);
 document.querySelectorAll('[data-step]').forEach(e=>{if(e.dataset.step===next)e.setAttribute('aria-current','step');else e.removeAttribute('aria-current');});
 $('stageHint').hidden=!dirty&&!(next==='prepare'&&!planNow());
 if(changed){$('main').scrollTop=0;if(innerWidth<=720)$('main').scrollIntoView({block:'start',behavior:'instant'});}
 if(changed&&next==='overview'&&typeof renderOverview==='function')renderOverview();
}
function render(){
 document.body.classList.add('has-task');$('welcome').hidden=true;$('workspace').hidden=false;$('batchTitle').textContent=task.name;
 const kinds=task.data.warehouses.map(w=>w.settings.data_kind);const source=kinds.every(k=>k==='simulated')?'示例数据':kinds.every(k=>k==='enterprise')?'业务数据待核对':'混合来源';
 const wh=task.data.warehouses.length===1?warehouseName(task.data.warehouses[0]):task.data.warehouses.length+' 个仓库';
 $('batchMeta').textContent=`${wh} · ${task.data.orders.length} 个配送任务 · ${task.data.vehicles.length} 辆可用车`;
 $('saveState').textContent='本机已保存';
 $('stageHint').textContent=state.complete?'本批次已返仓':state.confirmed?'方案已核定':planNow()?'方案待核定':'核对订单、运力与业务规则后，生成配送方案。';
 renderEditor();renderPlan();renderExecution();
 const imported=task.intake_source;
 $('boundaries').innerHTML=`<li>${esc(imported?'订单源于已核对的 Excel · '+imported.filename:source)} · 修订 ${task.revision}</li>`+(state.boundaries||[]).map(b=>`<li>${esc(b)}</li>`).join('');
 renderHistory();
 const ref=`id=${task.id}&revision=${task.revision}`;
 if(state.confirmed)$('exportCsv').href='/api/multiwarehouse/export?'+ref+'&format=csv';
 else $('exportCsv').removeAttribute('href');
 $('exportCsv').setAttribute('aria-disabled',String(!state.confirmed));
 $('exportJson').href='/api/multiwarehouse/export?'+ref;$('exportJson').download='multiwarehouse.json';
 showStep(step);
 if(typeof renderAgent==='function')renderAgent();
 renderBatchActions();
}
function renderHistory(){
 const plans=task.plans||[];
 $('planHistory').innerHTML=plans.length?`<details class="batch-history"><summary>方案记录 · ${plans.length} 份</summary><ul>${plans.slice().reverse().map(p=>{
  const m=p.summary?.optimized||{};
  return `<li><b>${p.id===state.current_plan_id?'当前方案':'历史方案'}</b><span>${esc(p.id.slice(0,8))}</span><span>${m.served??'—'} 个任务 · ${m.trips??'—'} 趟 · ${m.cost==null?'—':num(m.cost)+' 元'}</span></li>`;
 }).join('')}</ul></details>`:'';
}
function renderEditor(){
 const whOptions=draft.warehouses.map(w=>[w.id,warehouseName(w)]);
 const imported=task.intake_source;
 const source=imported?`<div class="order-source"><a href="${esc(sourceLink())}">查看订单原表</a><small class="order-source-lines">${esc(imported.scope?.sheet||imported.filename)} · ${imported.scope?.selected_rows?.length??imported.rows?.length??0} 行纳入${task.input_revision===imported.input_revision?'':' · 当前输入已更新'}</small></div>`:'';
 let html=`<section class="editor-section batch-identity"><label for="batchName">批次名称<input id="batchName" type="text" maxlength="80" value="${esc(task.name)}"></label>${source}</section>`;
 html+=`<section class="editor-section">
  <div class="editor-order-heading"><h3>配送订单</h3><label class="editor-search"><span class="sr-only">筛选门店</span><input id="orderSearch" type="search" value="${esc(orderQuery)}" placeholder="查找门店"></label></div>
  <div id="orderTable"></div>
  <details><summary>收货时段、服务用时与车辆准入</summary>${table(['配送任务','开门','承诺','截止','服务分钟','允许车辆（逗号分隔）'],draft.orders.map((o,i)=>`<tr><td>${esc(orderName(o.id))}</td><td>${input(['orders',i,'open'],o.id+'开门','clock')}</td><td>${input(['orders',i,'due'],o.id+'承诺','clock')}</td><td>${input(['orders',i,'close'],o.id+'截止','clock')}</td><td>${input(['orders',i,'service_minutes'],o.id+'服务分钟')}</td><td><input class="wide-input" aria-label="${esc(displayOrderId(o.id))}允许车辆" data-path="${esc(JSON.stringify(['orders',i,'allowed_vehicles']))}" data-kind="list" value="${esc(o.allowed_vehicles.map(displayVehicleId).join(','))}"></td></tr>`))}</details>
 </section>`;
 html+=`<section class="editor-section"><h3>运力与费用</h3>
  <details><summary>车辆载重、容积与班次 · ${draft.vehicles.length} 辆</summary>
  ${table(['车辆','所属仓库','载重 kg','容积 m³','出班','收班'],draft.vehicles.map((v,i)=>`<tr><td>${esc(displayVehicleId(v.id))}</td><td>${select(['vehicles',i,'warehouse_id'],v.id+'所属仓库',whOptions)}</td><td>${input(['vehicles',i,'max_weight_kg'],v.id+'载重')}</td><td>${input(['vehicles',i,'max_volume_m3'],v.id+'容积')}</td><td>${input(['vehicles',i,'shift_start'],v.id+'出班','clock')}</td><td>${input(['vehicles',i,'shift_end'],v.id+'收班','clock')}</td></tr>`))}
  </details>${draft.warehouses.map((w,i)=>`<details><summary>${esc(warehouseName(w))} · 道路、计费与回收规则</summary>
  <div class="form-grid"><label>仓库名称${input(['warehouses',i,'name'],w.id+'名称','text')}</label><label>数据来源${select(['warehouses',i,'settings','data_kind'],w.id+'来源',[['simulated','练习数据'],['enterprise','业务资料']])}</label><label>道路来源${input(['warehouses',i,'settings','travel_source'],w.id+'道路来源','text')}</label><label>收货截止口径${select(['warehouses',i,'settings','window_rule'],w.id+'收货截止口径',[['arrival','到达时间'],['finish','卸货完成时间']])}</label></div>
  ${w.business?`<h4>费用和空包装</h4><div class="form-grid">
    <label>里程计费${select(['warehouses',i,'business','tariff','mode'],w.id+'计费分支',[['incremental','只计超出起步里程的部分'],['whole_above','超出起步里程后按全程计费'],['linear','车辆固定费加全程里程费']])}</label>
    ${[['base','起步费（元）'],['included_km','起步公里'],['per_km','每公里费用（元）'],['per_stop','每店服务费（元）']].map(([k,l])=>`<label>${l}${input(['warehouses',i,'business','tariff',k],w.id+l)}</label>`).join('')}
    ${[['carton_weight_kg','纸箱 kg/个'],['carton_flat_m3','纸箱压平 m³/个'],['tote_weight_kg','周转箱 kg/个'],['tote_first_m3','每店首只周转箱 m³'],['tote_nested_m3','套叠每加一只 m³']].map(([k,l])=>`<label>${l}${input(['warehouses',i,'business','packaging',k],w.id+l)}</label>`).join('')}
  </div>`:'<p>费用按车辆固定费与全程里程计。未配置空包装。</p>'}</details>`).join('')}
  <details><summary>车辆线性费率</summary>${table(['车辆','固定费（元）','每公里费用（元）'],draft.vehicles.map((v,i)=>`<tr><td>${esc(displayVehicleId(v.id))}</td><td>${input(['vehicles',i,'fixed_cost'],v.id+'固定费')}</td><td>${input(['vehicles',i,'cost_per_km'],v.id+'每公里费用')}</td></tr>`))}</details>
 </section>`;
 const origins=[...draft.warehouses,...draft.orders];
 if(!origins.some(o=>o.id===roadOrigin))roadOrigin=origins[0]?.id||'';
 html+=`<section class="editor-section">
  <details id="roadDetails"><summary>道路里程 · ${Object.keys(draft.legs).length} 个方向</summary>
  <label class="road-select" for="roadOrigin">起点<select id="roadOrigin">${origins.map(o=>`<option value="${esc(o.id)}" ${o.id===roadOrigin?'selected':''}>${esc(o.name||orderName(o.id))}</option>`).join('')}</select></label><div id="roadTable"></div>
  </details>
  <details><summary>完整批次文件</summary><p>新增订单或车辆时，请补齐与其他节点的道路信息。</p><label for="inputJson">批次 JSON</label><textarea id="inputJson" class="input-json" spellcheck="false"></textarea><div class="toolbar-actions"><button id="loadJsonEditor">载入当前批次</button><button id="applyJsonEditor">核对并载入</button></div></details>
 </section>`;
 $('editor').innerHTML=html;
 renderOrderPage();
 $('roadDetails').addEventListener('toggle',()=>{
  if($('roadDetails').open&&!$('roadTable').firstChild)renderRoads();
 });
 const locked=mwFacts();
 if(locked)$('editor').querySelectorAll('[data-path], #batchName, #inputJson, #loadJsonEditor, #applyJsonEditor').forEach(e=>e.disabled=true);
 $('saveBatch').disabled=locked;$('solveBatch').disabled=locked;
}
function renderOrderPage(focusDirection){
 const filtered=draft.orders.map((order,index)=>({order,index})).filter(({order:o})=>[orderName(o.id),displayOrderId(o.id),o.id].some(v=>String(v).toLowerCase().includes(orderQuery)));
 const pageCount=Math.max(1,Math.ceil(filtered.length/ordersPerPage));
 orderPage=Math.max(0,Math.min(orderPage,pageCount-1));
 const from=orderPage*ordersPerPage,to=Math.min(filtered.length,from+ordersPerPage);
 const rows=filtered.slice(from,to).flatMap(({order:o,index:i})=>o.lines.map((l,j)=>{
  const raw=sourceStore(o.id);
  const source=j===0&&raw?.source_rows?.length?`<small class="order-source-lines">原表第 ${raw.source_rows.map(esc).join('、')} 行</small>`:'';
  const whOptions=draft.warehouses.map(w=>[w.id,warehouseName(w)]);
  return `<tr><td>${esc(orderName(o.id))}<small class="sku">${esc(l.sku)}</small>${source}</td><td>${j===0?select(['orders',i,'warehouse_id'],o.id+'所属仓库',whOptions):esc(displayWarehouseId(o.warehouse_id))}</td><td>${input(['orders',i,'lines',j,'quantity'],o.id+' '+l.sku+'箱数')}</td><td>${input(['orders',i,'lines',j,'unit_weight_kg'],o.id+' '+l.sku+'每箱kg')}</td><td>${input(['orders',i,'lines',j,'unit_volume_m3'],o.id+' '+l.sku+'每箱m3')}</td><td>${j===0&&o.pickup?input(['orders',i,'pickup','cartons'],o.id+'预计纸箱数'):'—'}</td><td>${j===0&&o.pickup?input(['orders',i,'pickup','totes'],o.id+'预计周转箱数'):'—'}</td></tr>`;
 }));
 const host=$('orderTable');
 host.innerHTML=rows.length?table(['门店与货品','仓库','箱数','每箱 kg','每箱 m³','预计纸箱','预计周转箱'],rows)+`<div class="editor-pager"><span role="status">第 ${from+1}–${to} 家 · 共 ${filtered.length} 家</span>${pageCount>1?`<div><button data-order-page="-1" ${orderPage===0?'disabled':''}>上一页</button><button data-order-page="1" ${orderPage+1===pageCount?'disabled':''}>下一页</button></div>`:''}</div>`:'<p class="form-note" role="status">未找到匹配的门店。</p>';
 if(mwFacts())host.querySelectorAll('[data-path]').forEach(e=>e.disabled=true);
 if(focusDirection) (host.querySelector(`[data-order-page="${focusDirection}"]:not(:disabled)`)||host.querySelector('[data-order-page]:not(:disabled)')||$('orderSearch')).focus();
}
function renderRoads(){
 const host=$('roadTable');
 const rows=Object.keys(draft.legs).filter(key=>key.startsWith(roadOrigin+'>')).map(key=>`<tr><td>${esc(nodeName(key.split('>')[1]))}</td><td>${input(['legs',key,'distance_km'],displayRoadId(key)+'道路公里')}</td><td>${input(['legs',key,'travel_minutes'],displayRoadId(key)+'行驶分钟')}</td></tr>`);
 host.innerHTML=rows.length?table(['终点','道路公里','行驶分钟'],rows):'<p class="form-note">该节点暂无出发道路。</p>';
 if(mwFacts())host.querySelectorAll('[data-path]').forEach(e=>e.disabled=true);
}
function comparisonMarkup(plan){
 const c=plan.comparison,b=plan.summary.baseline,m=plan.summary.optimized;
 if(!c?.comparable){
  const reasons=c?.reasons||[];
  const summarized=new Map();
  for(const r of reasons){
   const key=[r.plan,r.warehouse_id,r.vehicle_id,r.stop_id,r.code,r.message].join('/');
   summarized.set(key,r);
  }
  return `<section class="plan-comparison"><h4>${c?'暂不可比':'等待同口径核验'}</h4><div class="compare-content">${c?`<p>参考方案覆盖 ${b.served} 家，当前方案覆盖 ${m.served} 家。两份完整方案须通过同一组规则才能比较费用与效率。</p>`:'<p>重新生成方案后可查看同口径对照。</p>'}${summarized.size?`<ul>${[...summarized.values()].map(r=>`<li><strong>${r.plan==='baseline'?'参考方案':'当前方案'}</strong>${r.vehicle_id?' · '+esc(displayVehicleId(r.vehicle_id)):''}${r.stop_id?' · '+esc(orderName(r.stop_id)):''} · ${esc(r.message)}</li>`).join('')}</ul>`:''}</div></section>`;
 }
 const delta=c.delta||{};
 const fields=[
  ['cost','运费试算','元',1],['trips','车次','趟',1],['distance_km','往返道路','km',1],
  ['late_orders','预计逾期','家',1],['weight_utilization','出发载重','%',100],
  ['volume_utilization','出发容积','%',100],['rehandle_moves','搬倒','次',1]
 ];
 const rows=fields.filter(([key])=>Number.isFinite(b[key])&&Number.isFinite(m[key])).map(([key,label,unit,factor])=>{
  const diff=delta[key];
  const signed=Number.isFinite(diff)?(diff===0?'持平':(diff>0?'+':'−')+num(Math.abs(diff)*factor)+(factor===100?' 个百分点':' '+unit)):'—';
  return `<tr><td>${label}</td><td>${num(b[key]*factor)} ${unit}</td><td>${num(m[key]*factor)} ${unit}</td><td>${signed}</td></tr>`;
 });
 const fare=delta.cost===0?'费用持平':Number.isFinite(delta.cost)?'运费'+(delta.cost<0?'少':'多')+' '+num(Math.abs(delta.cost))+' 元':'';
 const handling=Number.isFinite(delta.rehandle_moves)&&delta.rehandle_moves!==0?(delta.rehandle_moves<0?'少':'多')+'搬倒 '+num(Math.abs(delta.rehandle_moves))+' 次':'';
 const summary=[fare,handling].filter(Boolean).join(' · ')||'查看差异';
 return `<section class="plan-comparison"><h4>${summary}</h4><div class="compare-content"><p>相同任务、可用运力与规则。两份方案都已通过核验。</p>${table(['指标','按序参考','当前方案','变化'],rows)}</div></section>`;
}
function routeFeeMarkup(route,part){
 const fee=route.fee;if(!fee)return '';
 const tariff=part.business?.tariff;
 const estimated=fee.estimated_billing_km??route.distance;
 const threshold=fee.included_km??tariff?.included_km;
 const chargeable=fee.chargeable_km??(fee.mode==='incremental'&&threshold!=null?Math.max(0,estimated-threshold):null);
 const perKm=fee.per_km??tariff?.per_km, perStop=fee.per_stop??tariff?.per_stop;
 const stopCount=fee.stop_count??route.sequence.length;
 const mileage=fee.mode==='incremental'&&threshold!=null&&chargeable!=null&&perKm!=null
  ?` · 超 ${num(threshold)} km 部分 ${num(chargeable)} km × ${num(perKm)} 元`
  :chargeable!=null&&perKm!=null?` · ${num(chargeable)} km × ${num(perKm)} 元`:'';
 const storeRule=fee.mode!=='linear'&&perStop!=null?` · ${stopCount} 家 × ${num(perStop)} 元`:'';
 const official=fee.tms_billing_km==null?'待核对':num(fee.tms_billing_km)+' km';
 return `<section class="route-fee" id="tripFees" hidden aria-label="计费明细"><dl>
  <div><dt>起步费</dt><dd>${num(fee.base)} 元</dd></div>
  <div><dt>里程费${mileage}</dt><dd>${num(fee.distance)} 元</dd></div>
  <div><dt>到店费${storeRule}</dt><dd>${num(fee.stops)} 元</dd></div>
  <div class="fee-sum"><dt>运费试算</dt><dd>${num(route.cost)} 元</dd></div>
 </dl><p>试算往返 ${num(estimated)} km · TMS 计费里程 ${official}</p>${part.settings.travel_source?`<p>道路来源 · ${esc(part.settings.travel_source)}</p>`:''}</section>`;
}
function routePlanMarkup(warehouse,vehicle,result){
 const raw=result.optimized.routes[vehicle.id];
 const route=raw?.operational?.adjusted_route||raw;
 if(!route?.sequence.length)return '';
 const op=raw.operational, loading=!!op?.passed||!!raw.loading?.placements?.length;
 const moves=op?.time_adjustments?.rehandle_moves;
 const risk=op?.time_adjustments?.high_risk_rehandle_moves;
 const checks=loading?'箱位已核验':'箱位待核对';
 const attrs=`data-route-warehouse="${esc(warehouse.id)}" data-route-vehicle="${esc(vehicle.id)}"`;
 const items=route.stops.map((s,index)=>`<tr><td>${index+1}</td><th scope="row">${esc(orderName(s.id))}</th><td>${time(s.arrival)}</td><td>${time(s.finish)}</td><td>${s.pickup?`${s.pickup.cartons} / ${s.pickup.totes}`:'—'}</td><td>${s.onboard_weight_kg==null?'—':num(s.onboard_weight_kg)+' / '+num(s.onboard_volume_m3)}</td></tr>`);
 return `<article class="route plan-route" aria-labelledby="tripTitle">
  <header class="trip-heading"><h4 id="tripTitle">${esc(displayVehicleId(vehicle.id))}</h4><p>${esc(warehouseName(warehouse))}出发 · ${route.sequence.length} 站 · 预计 ${time(route.return_at)} 返仓</p><p>往返 ${num(route.distance)} km · 运费试算 ${num(route.cost)} 元</p></header>
  <div class="plan-route-body">
   <div class="route-details"><span>出发载重 ${num(route.weight_kg)} kg · ${pct(route.weight_kg/vehicle.max_weight_kg)}</span><span>出发容积 ${num(route.volume_m3)} m³ · ${pct(route.volume_m3/vehicle.max_volume_m3)}</span><span>沿途最高载荷 ${num(route.peak_weight_kg??route.weight_kg)} kg / ${num(route.peak_volume_m3??route.volume_m3)} m³</span></div>
   <div class="route-tools"><span class="detail-tag">${checks}</span>${Number.isFinite(moves)?`<span class="detail-tag">搬倒 ${num(moves)} 次${risk?' · 高风险 '+num(risk)+' 次':''}</span>`:''}
    ${loading?`<button data-route-panel="spatial" ${attrs}>查看箱位</button>`:''}
    <button data-route-panel="overview" ${attrs}>查看路线</button>
   </div>
   ${route.fee?`<div class="plan-choices" role="group" aria-label="单车详情"><button data-trip-view="tripStops" aria-controls="tripStops" aria-pressed="true">到店顺序</button><button data-trip-view="tripFees" aria-controls="tripFees" aria-pressed="false">计费明细</button></div>`:''}
   <section id="tripStops" aria-label="到店顺序">
   <p class="trip-scroll-hint">横滑查看完成时间、回收量和离店载荷。</p>
   ${table(['站序','门店','预计到达','预计完成','预计回收（纸箱 / 周转箱）','离店 kg / m³'],items)}
   </section>
   ${routeFeeMarkup(route,result.data)}
  </div>
 </article>`;
}
function renderReviewedRoute(){
 const selected=$('plan').querySelector('[data-review-route][aria-pressed="true"]');
 if(!selected)return;
 const warehouse=task.data.warehouses.find(w=>w.id===selected.dataset.routeWarehouse);
 const result=planNow().warehouses[warehouse.id];
 const vehicle=result.data.vehicles.find(v=>v.id===selected.dataset.routeVehicle);
 $('reviewedRoute').innerHTML=routePlanMarkup(warehouse,vehicle,result);
}
function renderPlan(){
 const p=planNow();
 if(!p){$('plan').innerHTML='<div class="result-empty"><h2>准备排车</h2><p>核对订单、车辆和配送条件后，生成方案。</p><button data-go="prepare" class="primary">去核对订单</button></div>';return;}
 const m=p.summary.optimized;
 const method=p.method==='manual_reorder'?'调整后的方案':'配送方案';
 const latest=m.latest_return_at!=null?` · 末车预计 ${time(m.latest_return_at)} 返仓`:'';
 const canAdjust=!mwFacts()&&!lifecycleLocked();
 const handoff=state.confirmed?`<a class="button" data-plan-export href="/api/multiwarehouse/export?${new URLSearchParams({id:task.id,revision:task.revision,format:'csv'})}">导出作业清单</a>`:'';
 let html=`<div class="plan-top"><h2>${method}</h2><div class="plan-actions"><span class="status-text ${m.unserved?'warning':''}">${m.unserved?'有 '+m.unserved+' 家待处理':state.confirmed?'已核定':'已核验 · 待核定'}</span>${handoff}</div></div>
 <section class="plan-summary" aria-label="方案结果"><dl class="metrics">
  <div><dt>已安排</dt><dd>${m.served}<small>/ ${m.served+m.unserved} 家</small></dd></div>
  <div><dt>使用车次</dt><dd>${m.trips}<small>趟</small></dd></div>
  <div><dt>运费试算</dt><dd>${num(m.cost)}<small>元</small></dd></div>
  <div><dt>往返道路</dt><dd>${num(m.distance_km)}<small>km</small></dd></div>
 </dl><p class="metric-detail">出发载重 ${pct(m.weight_utilization)} · 容积 ${pct(m.volume_utilization)}${latest}</p></section>`;
 const groups=task.data.warehouses.map(w=>({warehouse:w,result:p.warehouses[w.id]}));
 const pending=groups.flatMap(({warehouse,result})=>result.optimized.unserved.map(order=>({warehouse,order})));
 if(pending.length)html+=`<section class="unserved plan-pending"><h3>${pending.length} 家待安排</h3><p>请核对以下原因，调整配送条件后重新生成方案。</p><ul aria-label="门店与处理建议">${pending.map(({warehouse,order})=>`<li><strong>${esc(orderName(order.id))}</strong>${groups.length>1?' · '+esc(warehouseName(warehouse)):''} · ${esc(order.reason)}${order.action?`<br>${esc(order.action)}`:''}</li>`).join('')}</ul><button data-go="prepare">核对订单和运力</button></section>`;
 if(reviewedPlan!==p.id){reviewedPlan=p.id;reviewedRoute='';}
 const trips=groups.flatMap(({warehouse,result})=>result.data.vehicles.flatMap(vehicle=>{
  const raw=result.optimized.routes[vehicle.id];
  const route=raw?.operational?.adjusted_route||raw;
  return route?.sequence.length?[{warehouse,vehicle,route,key:JSON.stringify([warehouse.id,vehicle.id])}]:[];
 }));
 if(!trips.some(trip=>trip.key===reviewedRoute))reviewedRoute=trips[0]?.key||'';
 html+=`<section class="plan-trips" aria-labelledby="tripListTitle"><h3 id="tripListTitle">车次清单 <small>${trips.length} 趟</small></h3>${trips.length?`
  <div class="trip-review">
   <ul class="trip-list">${trips.map(({warehouse,vehicle,route,key})=>`<li><button data-review-route="${esc(key)}" data-route-warehouse="${esc(warehouse.id)}" data-route-vehicle="${esc(vehicle.id)}" aria-controls="reviewedRoute" aria-pressed="${key===reviewedRoute}"><strong>${esc(displayVehicleId(vehicle.id))}${groups.length>1?' · '+esc(warehouseName(warehouse)):''}</strong><span>${route.sequence.length} 站 · ${num(route.distance)} km · ${time(route.return_at)} 返仓</span></button></li>`).join('')}</ul>
   <div id="reviewedRoute" class="trip-detail"></div>
  </div>`:'<p>当前方案没有可查看的车次。</p>'}</section>`;
 html+=`<section class="plan-review" aria-labelledby="planReviewTitle"><h3 id="planReviewTitle">复核与调整</h3><div class="plan-choices" role="group" aria-label="复核与调整">
  <button data-plan-view="planChecks" aria-controls="planChecks" aria-pressed="true">约束核验</button>
  <button data-plan-view="planCompare" aria-controls="planCompare" aria-pressed="false">方案对比</button>
  ${canAdjust?`<button data-plan-view="planConditions" aria-controls="planConditions" aria-pressed="false">配送条件</button><button data-plan-view="planManual" aria-controls="planManual" aria-pressed="false">车辆与站序</button>`:''}
 </div><div id="planChecks" class="plan-review-panel"><p id="planCheckStatus">${m.unserved?'部分门店仍待安排。':p.audit?.passed?'本方案通过独立核验。':'请核对本批方案与作业要求。'}</p></div><div id="planCompare" class="plan-review-panel" hidden>${comparisonMarkup(p)}`;
 const parent=task.plans.find(old=>old.id===p.parent_plan_id);
 if(parent){
  const before=parent.summary.optimized;
  html+=`<section class="plan-comparison"><h4>调整前后</h4><div class="compare-content"><p>车次 ${before.trips} → ${m.trips} · 运费试算 ${num(before.cost)} → ${num(m.cost)} 元 · 往返道路 ${num(before.distance_km)} → ${num(m.distance_km)} km</p></div></section>`;
 }
 html+='</div>';
 if(canAdjust){
  const vehicles=groups.flatMap(({warehouse,result})=>result.data.vehicles.map(vehicle=>({warehouse,vehicle,result})));
  html+=`<div id="planConditions" class="plan-review-panel" hidden></div><section id="planManual" class="plan-review-panel" hidden aria-label="调整车辆与站序">
   <p>可依次修改多辆车，每家门店只保留一次。核验时会一起检查所有车次。</p>
   <label class="manual-vehicle-picker">选择车辆<select id="manualVehicle">${vehicles.map(({warehouse,vehicle},index)=>`<option value="${index}">${esc(warehouseName(warehouse))} · ${esc(displayVehicleId(vehicle.id))}</option>`).join('')}</select></label>
   <div id="manualRoutes">${vehicles.map(({warehouse,vehicle,result},index)=>`<label class="manual-route" ${index?'hidden':''}>门店顺序（用逗号分隔，闲置车留空）<input data-manual-warehouse="${esc(warehouse.id)}" data-manual-vehicle="${esc(vehicle.id)}" value="${esc(result.optimized.routes[vehicle.id].sequence.map(displayOrderId).join(','))}"></label>`).join('')}</div><button id="manualPreview">核验调整</button>
  </section>`;
 }
 html+='</section>';
 if(!state.confirmed)html+=`<section class="confirm-section"><h3>核定批次</h3>${m.unserved?'<p>请先处理未安排门店，再重新排车。</p>':''}<label class="inline-check"><input id="reviewPlan" type="checkbox" ${m.unserved?'disabled':''}>已核对路线与作业要求</label><button id="adoptBatch" class="primary" disabled>核定方案</button></section>`;
 else html+=`<div class="confirm-section"><p class="confirmed-note">方案已核定 · 作业清单可交接</p><div class="detail-actions"><button data-go="spatial">查看装卸</button><button data-go="execute" class="primary">登记回收</button></div></div>`;
 $('plan').innerHTML=html;
 renderReviewedRoute();
}
function sourceOptions(isPractice){
 return isPractice?'<option value="simulated">本地登记</option>':'<option value="manual">人工登记</option><option value="simulated">本地登记</option>';
}
function receiptForm(w,v,r,child){
 const isStop=!!r.next_stop,business=!!child.data.business,practice=child.data.settings.data_kind==='simulated';
 return `<form class="receipt-form" data-receipt="${esc(w)}" data-vehicle="${esc(v)}">
  <h5>${isStop?'登记 '+esc(orderName(r.next_stop))+' 签收与回收':'登记返仓交接'}</h5>
  <div class="receipt-fields">
   <label>完成时间<input name="finish" type="time" value="${time(isStop?r.remaining[0]?.finish:r.estimated_return)}" required></label>
   <label>记录来源<select name="source">${sourceOptions(practice)}</select></label>
   ${isStop&&business?`<label>回收纸箱<input name="cartons" type="number" min="0" max="100000" step="1" value="${child.data.orders.find(o=>o.id===r.next_stop)?.pickup?.cartons??0}" required></label><label>回收周转箱<input name="totes" type="number" min="0" max="100000" step="1" value="${child.data.orders.find(o=>o.id===r.next_stop)?.pickup?.totes??0}" required></label>`:''}
   <label class="receipt-note">单据编号或来源<input name="note" type="text" maxlength="500" ${practice?'':'required'}></label>
  </div>
  <label class="inline-check"><input name="reviewed" type="checkbox" required>已核对本次记录</label>
  <button class="primary" type="submit">${isStop?'保存签收与回收':'保存返仓交接'}</button>
 </form>`;
}
function executionHistory(child,vid){
 const rows=(child.receipts||[]).filter(x=>x.vehicle===vid);
 return `<details class="receipt-history"><summary>本车记录 · ${rows.length} 条</summary>${rows.map(x=>{
  const pickup=x.kind==='stop'&&child.data.business?' · 纸箱 '+esc(x.pickup.cartons)+' / 周转箱 '+esc(x.pickup.totes):'';
  return `<p>${time(x.finish)} · ${esc(x.stop_id?orderName(x.stop_id):'返仓交接')}${pickup} · ${x.source==='simulated'?'本地登记':'人工登记'}${x.note?' · '+esc(x.note):''}</p>`;
 }).join('')||'<p>还没有回执。</p>'}</details>`;
}
function observationForm(w,vid,r,child){
 const points=[w.id,...child.plans[0].result.optimized.routes[vid].sequence];
 return `<details class="observation-editor"><summary>登记位置与等待</summary>
  <form data-observe="${esc(w.id)}" data-vehicle="${esc(vid)}">
   <div class="receipt-fields">
    <label>类型<select name="kind"><option value="wait">当前节点等待</option><option value="position">已到已知节点</option></select></label>
    <label>节点<select name="node">${points.map(n=>`<option value="${esc(n)}" ${n===r.anchor.node?'selected':''}>${esc(nodeName(n))}</option>`).join('')}</select></label>
    <label>观察时刻<input name="observed_at" type="time" required></label>
    <label>来源<select name="source">${sourceOptions(child.data.settings.data_kind==='simulated')}</select></label>
    <label>来源说明<input name="note" maxlength="500"></label>
   </div><label class="inline-check"><input name="reviewed" type="checkbox" required>已核对节点、时刻与来源</label>
   <button type="submit">保存观察</button>
  </form>
 </details>`;
}
function renderExecution(){
 if(!state.confirmed){
  $('execute').innerHTML='<div class="result-empty"><h2>回收与返仓</h2><p>方案核定后，这里按车登记签收、回收和返仓。</p><button data-go="plan" class="primary">去核定方案</button></div>';return;
 }
 const vehicles=Object.entries(state.execution).flatMap(([wid,g])=>Object.entries(g.routes).map(([vid,r])=>({wid,vid,r})));
 const returned=vehicles.filter(v=>v.r.returned).length;
 if(!vehicles.some(v=>v.vid===executionVehicle))executionVehicle='';
 let html=`<h2>回收与返仓</h2><div class="execution-controls"><p>已返仓 ${returned} / ${vehicles.length} 辆 · ${state.receipts} 条回执</p>
 ${vehicles.length>1?`<label>车辆<select id="executionVehicle"><option value="">全部</option>${vehicles.map(v=>`<option value="${esc(v.vid)}" ${v.vid===executionVehicle?'selected':''}>${esc(displayVehicleId(v.vid))}${v.r.returned?' · 已返仓':''}</option>`).join('')}</select></label>`:''}
 </div>`;
 for(const w of task.data.warehouses){
  const group=state.execution[w.id],child=task.execution_tasks[w.id];
  html+=`<section class="execution-warehouse"><div class="warehouse-heading"><h3>${esc(warehouseName(w))}</h3><p>${group.complete?'已返仓':'作业记录'}</p></div>`;
  for(const [vid,r] of Object.entries(group.routes)){
   html+=`<article class="execution-vehicle" data-execution-warehouse="${esc(w.id)}" data-execution-vehicle="${esc(vid)}">
    <h4>${esc(displayVehicleId(vid))} · ${r.returned?'已返仓':r.next_stop?'下一站 '+esc(orderName(r.next_stop)):'待返仓'}</h4>
    <p>已签收 ${r.completed} 站 · 回收 ${r.collected_cartons} 个纸箱 / ${r.collected_totes} 个周转箱</p>
    ${r.warnings.length?`<div class="unserved"><h3>待处理</h3>${r.warnings.map(x=>`<p>${esc(x)}</p>`).join('')}</div>`:''}
    ${!r.returned?`<p class="anchor-line">预计 ${time(r.estimated_return)} 回仓 · 当前估算载荷 ${num(r.onboard_weight_kg)} kg / ${num(r.onboard_volume_m3)} m³</p>
     ${receiptForm(w.id,vid,r,child)}
     ${r.remaining.length?`<details class="receipt-history"><summary>剩余站点 · ${r.remaining.length} 站</summary>${table(['门店','预计到达','预计完成'],r.remaining.map(x=>`<tr><td>${esc(orderName(x.id))}</td><td>${time(x.arrival)}</td><td>${time(x.finish)}</td></tr>`))}</details>`:''}
     ${observationForm(w,vid,r,child)}`:''}
    ${executionHistory(child,vid)}
   </article>`;
  }
  if(!group.complete&&!w.operational_policy)html+=`<details class="remaining-planner"><summary>调整本仓剩余路线</summary><p>在原车上调整未完成门店，保留已登记的签收和回收。</p><button data-replan="${esc(w.id)}">计算剩余路线</button>${remainingCandidate(w.id,child)}</details>`;
  html+='</section>';
 }
 $('execute').innerHTML=html;
 applyExecutionFilter();
}
function applyExecutionFilter(){
 for(const warehouse of document.querySelectorAll('.execution-warehouse')){
  const vehicles=[...warehouse.querySelectorAll('[data-execution-vehicle]')];
  for(const v of vehicles)v.hidden=!!executionVehicle&&v.dataset.executionVehicle!==executionVehicle;
  warehouse.hidden=!!executionVehicle&&vehicles.every(v=>v.hidden);
 }
}
function remainingCandidate(w,child){const p=child.remaining_plans?.at(-1);if(!p||p.adopted_at)return '';return `<div class="remaining-candidate"><p>剩余候选 ${esc(p.id.slice(0,8))} · ${esc(p.status||'待核对')}；新记录可能使其失效，采用前会重新检查。</p>${Object.entries(p.sequences||{}).map(([v,s])=>`<p>${esc(displayVehicleId(v))}：${esc(s.map(displayOrderId).join(' → ')||'返仓')}</p>`).join('')}<label class="inline-check"><input type="checkbox" data-review-remaining="${esc(w)}">我已核对剩余顺序和风险</label><button data-adopt-remaining="${esc(w)}" data-candidate="${esc(p.id)}" disabled>采用这份剩余候选</button></div>`;}
async function mutate(op,extra={},next=step){if(dirty&&op!=='save')throw Error('有未保存的输入修改，请先保存并重新计算。');const result=await api(op,{id:task.id,revision:task.revision,...extra});step=next;setTask(result);await refresh();notice('');}
async function saveInput(){const name=$('batchName').value;const result=await api('save',{id:task.id,revision:task.revision,name,data:draft});setTask(result);await refresh();}
function mwFacts(){return state.receipts>0||Object.values(task.execution_tasks||{}).some(t=>(t.observations?.length||t.temperature_records?.length));}
$('newBatch').onclick=()=>run(async()=>{if(!leaveDraft())return;const sample=await api('sample');step='prepare';setTask(await api('create',{name:'配送入门批次',data:sample.data}));$('newBatch').closest('.sample-section').open=false;await refresh();notice('');});
$('refresh').onclick=()=>run(async()=>{if(!leaveDraft())return;if(task)setTask(await api('task?id='+task.id));await refresh();notice('已读取本机保存的最新内容。');});
$('importBatch').onclick=()=>run(async()=>{if(!leaveDraft())return;const file=$('batchFile').files[0];if(!file)throw Error('先选择多仓 JSON 文件。');if(file.size>3000000)throw Error('文件不能超过3MB。');let data;try{data=JSON.parse(await file.text());}catch{throw Error('文件不是有效 JSON，请使用多仓模板。');}data=data.data?.schema_version?data.data:data;step='prepare';setTask(await api('create',{name:file.name.replace(/\.json$/i,'').slice(0,80),data}));await refresh();notice('任务已校验并导入。');});
$('saveBatch').onclick=()=>run(async()=>{await saveInput();notice('已保存，请重新计算。');});
$('solveBatch').onclick=async()=>{
 if(busy||lifecycleLocked())return;
 if(dirty)await run(saveInput);
 if(dirty||busy)return;
 if(typeof sendAgent==='function'){
  document.querySelector('.app-layout').classList.remove('agent-collapsed');
  $('toggleAgent').setAttribute('aria-expanded','true');
  notice('');
  await sendAgent('计算配送方案','local');
 }else run(()=>mutate('solve',{},'plan'));
};
function showPlanView(target,group){
 const buttons=$('plan').querySelectorAll(`[data-${group}-view]`);
 buttons.forEach(button=>{
  const panel=$(button.getAttribute('aria-controls'));
  const selected=panel.id===target;
  button.setAttribute('aria-pressed',String(selected));
  panel.hidden=!selected;
 });
}
document.addEventListener('input',e=>{
 const el=e.target;
 if(el.id==='orderSearch'){orderQuery=el.value.trim().toLowerCase();orderPage=0;renderOrderPage();return;}
 if(el.id==='batchName'){markDirty();return;}
 if(!el.dataset.path)return;
 const path=JSON.parse(el.dataset.path);let value=el.value;
 if(el.dataset.kind==='number')value=value===''?null:Number(value);
 if(el.dataset.kind==='clock')value=value===''?null:Number(value.slice(0,2))*60+Number(value.slice(3));
 if(el.dataset.kind==='list')value=value.split(/[,，|]/).map(x=>x.trim()).filter(Boolean).map(resolveVehicleId);
 let node=draft;for(const key of path.slice(0,-1))node=node[key];
 node[path.at(-1)]=value;markDirty();
});
document.addEventListener('change',e=>{
 const el=e.target;
 if(el.id==='reviewPlan')$('adoptBatch').disabled=!el.checked||dirty;
 if(el.id==='manualVehicle')$('manualRoutes').querySelectorAll('label').forEach((label,index)=>label.hidden=index!==Number(el.value));
 if(el.id==='roadOrigin'){roadOrigin=el.value;renderRoads();}
 if(el.id==='executionVehicle'){executionVehicle=el.value;applyExecutionFilter();}
 if(el.dataset.reviewRemaining){
  const button=document.querySelector(`[data-adopt-remaining="${CSS.escape(el.dataset.reviewRemaining)}"]`);
  if(button)button.disabled=!el.checked;
 }
});
document.addEventListener('click',e=>{const el=e.target.closest('button');if(!el||busy)return;
 if(el.dataset.organize){
  if(!leaveDraft())return;
  const action=el.dataset.organize;
  if(action==='trash'&&!confirm('将这个批次移至回收站？以后可从左侧恢复。'))return;
  if(action==='archive'&&!confirm('归档这个批次？需要继续作业时可恢复。'))return;
  run(async()=>{const result=await api('organize',{id:task.id,revision:task.revision,action});setTask(result);await refresh();$('batchActions').open=false;notice(action==='restore'?'批次已恢复。':action==='archive'?'批次已归档，可从左侧恢复。':'批次已移至回收站，可从左侧恢复。');});
  return;
 }
 if(el.dataset.restoreBatch){
  if(!leaveDraft())return;
  run(async()=>{const result=await api('organize',{id:el.dataset.restoreBatch,revision:Number(el.dataset.batchRevision),action:'restore'});step=resumeStep(result);setTask(result);await refresh();notice('批次已恢复。');});
  return;
 }
 if(el.dataset.workflow)run(async()=>{await mutate('agent',{workflow:el.dataset.workflow},el.dataset.workflow==='dispatch'?'plan':step);const details=$('assistantArea').querySelector('details');if(details)details.open=true;notice('检查完成，结果已保存。');});
 if(el.dataset.step||el.dataset.go){showStep(el.dataset.step||el.dataset.go);return;}
 if(el.dataset.orderPage){orderPage+=Number(el.dataset.orderPage);renderOrderPage(el.dataset.orderPage);return;}
 if(el.dataset.planView){showPlanView(el.dataset.planView,'plan');return;}
 if(el.dataset.tripView){showPlanView(el.dataset.tripView,'trip');return;}
 if(el.dataset.reviewRoute){
  reviewedRoute=el.dataset.reviewRoute;
  $('plan').querySelectorAll('[data-review-route]').forEach(button=>button.setAttribute('aria-pressed',String(button===el)));
  renderReviewedRoute();
  if(matchMedia('(max-width: 960px)').matches)$('reviewedRoute').scrollIntoView({block:'nearest'});
  return;
 }
 if(el.dataset.routePanel){
  selectedWarehouse=el.dataset.routeWarehouse;selectedVehicle=el.dataset.routeVehicle;selectedNode='';
  updateSelection();showStep(el.dataset.routePanel);
  return;
 }
 if(el.dataset.open)run(async()=>{if(!leaveDraft())return;await openBatch(el.dataset.open);await refresh();notice('');});
 if(el.id==='adoptBatch')run(async()=>{await mutate('adopt',{plan_id:planNow().id,reviewed:$('reviewPlan').checked},'plan');$('main').scrollTop=0;if(innerWidth<=720)$('main').scrollIntoView({block:'start',behavior:'instant'});});
 if(el.id==='manualPreview')run(async()=>{const sequences={};document.querySelectorAll('[data-manual-warehouse]').forEach(i=>{(sequences[i.dataset.manualWarehouse]??={})[i.dataset.manualVehicle]=i.value.split(/[,，]/).map(x=>x.trim()).filter(Boolean).map(resolveOrderId);});await mutate('manual',{sequences},'plan');notice('已生成调整方案，请核对后确认。');});
 if(el.dataset.replan)run(()=>mutate('replan',{warehouse_id:el.dataset.replan},'execute'));
 if(el.dataset.adoptRemaining)run(()=>mutate('adopt-remaining',{warehouse_id:el.dataset.adoptRemaining,candidate_id:el.dataset.candidate,reviewed:true},'execute'));
 if(el.id==='loadJsonEditor')$('inputJson').value=JSON.stringify(draft,null,2);
 if(el.id==='applyJsonEditor')run(async()=>{let data;try{data=JSON.parse($('inputJson').value);}catch{throw Error('批次文件不是有效的 JSON。');}const valid=await api('validate',{data});const name=$('batchName').value;draft=valid.data;renderEditor();if(typeof opRenderRules==='function')opRenderRules();$('batchName').value=name;markDirty();notice('资料已载入表单，请核对并保存。');});
});
document.querySelectorAll('[data-lifecycle-list]').forEach(details=>details.addEventListener('toggle',()=>{
 if(details.open)refreshOrganized(details.dataset.lifecycleList).catch(error=>notice(error.message,true));
}));
document.addEventListener('submit',e=>{const form=e.target;if(!form.matches('[data-receipt],[data-observe]'))return;e.preventDefault();const f=new FormData(form),minute=name=>{const t=f.get(name);return Number(t.slice(0,2))*60+Number(t.slice(3));};if(form.dataset.receipt){const wid=form.dataset.receipt,vid=form.dataset.vehicle,r=state.execution[wid].routes[vid];const record={vehicle:vid,kind:r.next_stop?'stop':'return',stop_id:r.next_stop,finish:minute('finish'),source:f.get('source'),note:f.get('note'),pickup:r.next_stop&&task.execution_tasks[wid].data.business?{cartons:Number(f.get('cartons')),totes:Number(f.get('totes'))}:{}};run(()=>mutate('receipt',{warehouse_id:wid,receipt:record,reviewed:f.get('reviewed')==='on'},'execute'));}else{const record={vehicle:form.dataset.vehicle,kind:f.get('kind'),node:f.get('node'),observed_at:minute('observed_at'),source:f.get('source'),note:f.get('note')};run(()=>mutate('observe',{warehouse_id:form.dataset.observe,observation:record,reviewed:f.get('reviewed')==='on'},'execute'));}});
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
run(async()=>{
 await refresh();
 const requested=new URLSearchParams(location.search).get('task');
 const stored=localStorage.getItem('logireflow.multiwarehouse.active');
 const id=requested||stored;
 if(!id)return;
 try{
  await openBatch(id);await refresh();
  const requestedStep=new URLSearchParams(location.search).get('step');
  if(['overview','prepare','plan','spatial','execute'].includes(requestedStep))showStep(requestedStep);
 }
 catch(e){
  if(id===stored)localStorage.removeItem('logireflow.multiwarehouse.active');
  notice('无法打开批次。'+e.message+' 可从批次列表重新选择。',true);
 }
});
