'use strict';
// One source of truth: a versioned candidate, never a fabricated live truck.
let operationalViewer=null, operationalSceneKey='', operationalRequest=0;
const opLabels={NOT_CONFIGURED:'未配置',NOT_MODELED:'未建模',VERIFIED_WITHIN_MODEL:'已通过',VERIFIED_WITHIN_DECLARED_MODEL:'已通过',REJECTED:'未通过',CHECKED_WITHIN_MODEL:'已检查',PENDING_CANDIDATE:'待计算'};
function opLabel(status){return opLabels[status]||status||'待核对';}
function opDispose(){if(operationalViewer)operationalViewer.destroy();operationalViewer=null;operationalSceneKey='';}
function opPolicies(){return task?.data.warehouses.filter(w=>w.operational_policy)||[];}
function opCoverageMarkup(check){
 const rows=Array.isArray(check?.coverage)?check.coverage:[];
 return rows.length?`<div class="op-coverage">${rows.map(c=>`<div data-status="${esc(c.status)}"><span>${esc(c.label)}</span><strong>${esc(opLabel(c.status))}</strong></div>`).join('')}</div>`:'<p>缺少冷链、回收箱规或装卸参数。</p>';
}
function opParameterLabel(parameter){const names={vehicle_geometry:'车厢尺寸',cold_boxes:'共享保温箱',handling:'装卸工时',energy:'车辆能耗',preparation:'出车准备',pickup_geometry:'回收箱规',returns:'退货',incompatible_pairs:'货物隔离'};return String(parameter??'').split('/').map(key=>names[key]||key).join('、');}
function opAssumptions(policy){return (policy.assumptions||[]).map(a=>`<li><strong>${esc(opParameterLabel(a.parameter))}</strong> · ${esc(a.basis)} <span>· ${a.evidence==='assumed'?'估计参数':esc(a.evidence)}</span>${/^https?:\/\//i.test(a.source||'')?` · <a href="${esc(a.source)}" target="_blank" rel="noopener">参考资料</a>`:''}</li>`).join('');}
function opRenderRules(){
 const editor=$('editor');if(!editor||!draft)return;
 for(const [wi,w] of draft.warehouses.entries()){
  const p=w.operational_policy;if(!p)continue;
  const base=['warehouses',wi,'operational_policy'];
  const value=(path,label)=>input([...base,...path],w.id+' '+label);
  const section=document.createElement('section');section.className='editor-section operational-rules';
  section.innerHTML=`<h3>${esc(displayWarehouseId(w.id))} · 冷链与沿途空间</h3><p>修改本仓参数后，请重新计算并核对方案。</p><details><summary>参数依据</summary><ul>${opAssumptions(p)||'<li>参数来源待补充。</li>'}</ul></details><details><summary>车厢与温控设备</summary>${table(['车辆','可用长 cm','宽 cm','高 cm'],p.vehicle_geometry.map((v,i)=>`<tr><td>${esc(displayVehicleId(v.id))}</td><td>${value(['vehicle_geometry',i,'length_cm'],'车厢长')}</td><td>${value(['vehicle_geometry',i,'width_cm'],'车厢宽')}</td><td>${value(['vehicle_geometry',i,'height_cm'],'车厢高')}</td></tr>`))}${table(['共享箱','外长 cm','外宽 cm','外高 cm','内体积 m³','箱体自重 kg','有效时长 min','开启次数','开箱用时 min'],p.cold_boxes.map((b,i)=>`<tr><td>${esc(b.id)}<small>${b.requires_cold_zone?'需车厢制冷':'共享保温箱'} · ${esc(b.temperature_range.join('—'))}°C</small></td>${['length_cm','width_cm','height_cm','inner_volume_m3','tare_weight_kg','valid_minutes','max_openings','opening_minutes'].map(k=>`<td>${value(['cold_boxes',i,k],b.id+' '+k)}</td>`).join('')}</tr>`))}</details><details><summary>装卸、回收和倒货工时</summary><div class="form-grid"><label>每次搬运 min${value(['handling','move_minutes'],'倒货动作分钟')}</label><label>临时箱位${value(['handling','temporary_slots'],'临时地面箱位')}</label><label>每件回收用时 min${value(['handling','pickup_minutes_per_unit'],'每件回收工时')}</label></div><p>允许倒货：${p.handling.allow_rehandling?'是':'否'}。共享箱留在车上，回收物按箱规计入每站载荷。</p></details>`;
  editor.append(section);
 }
 if(mwFacts())editor.querySelectorAll('.operational-rules input').forEach(e=>e.disabled=true);
}
function opRenderPlan(){
 const p=planNow();if(!p)return;
 const target=$('planChecks');
 for(const w of task.data.warehouses){
  const result=p.warehouses[w.id],check=result.operational_verification;
  if(!check)continue;
  const section=document.createElement('section');section.className='op-review';
  const routes=Object.values(check.routes||{}).filter(r=>r.frame_count||r.frames?.length);
  const minutes=routes.reduce((s,r)=>s+(r.time_adjustments?.extra_minutes||0),0);
  const moves=routes.reduce((s,r)=>s+(r.time_adjustments?.rehandle_moves||0),0);
  section.innerHTML=`<h4>${esc(warehouseName(w))} · 冷链、装卸与回收</h4>${routes.length?`<p>预计开箱、回收与搬倒用时 ${num(minutes)} 分钟 · 搬倒 ${num(moves)} 次</p>`:''}${opCoverageMarkup(check)}`;
  target.append(section);
 }
}
function opRenderExecution(){
 if(!state?.confirmed)return;
 for(const [wid,group] of Object.entries(state.execution||{})){
  for(const [vid,r] of Object.entries(group.routes||{})){
   if(!r.operational_policy_enabled)continue;
   const form=document.querySelector(`form[data-receipt="${CSS.escape(wid)}"][data-vehicle="${CSS.escape(vid)}"]`);
   const host=form?.closest('.execution-vehicle')||document.querySelector(`[data-execution-warehouse="${CSS.escape(wid)}"][data-execution-vehicle="${CSS.escape(vid)}"]`);if(!host)continue;
   const note=document.createElement('p');note.className='boundary op-execution-boundary';
   note.textContent=r.returned?'已记录回仓，车内载荷已清空。':
    `${r.current_geometry_status==='NEEDS_OBSERVATION'?'当前箱位需现场核对':'车内空间按计划估算'}，共享箱保留至回仓。`;
   host.insertBefore(note,form||null);
   opRenderTemperature(host,wid,vid,r);
  }
 }
}
function opRouteColdBoxes(wid,vid){
 const child=task.execution_tasks?.[wid],policy=child?.data.operational_policy;if(!policy)return [];
 const sequence=child.plans[0].result.optimized.routes[vid]?.sequence||[];
 const used=new Set(policy.goods.filter(g=>sequence.includes(g.order)&&g.kind==='cold').map(g=>g.cold_box_id));
 return policy.cold_boxes.filter(b=>used.has(b.id)).map(b=>({...b,label:'冷链箱'+(policy.cold_boxes.indexOf(b)+1)}));
}
function opRenderTemperature(host,wid,vid,route){
 const boxes=opRouteColdBoxes(wid,vid);if(!boxes.length)return;
 const records=(task.execution_tasks[wid].temperature_records||[]).filter(r=>r.vehicle===vid);
 const readings=records.filter(r=>r.kind==='reading'),handled=new Set(records.filter(r=>r.kind==='action').map(r=>r.target_id));
 const abnormal=r=>{const box=boxes.find(b=>b.id===r.cold_box);return box&&(r.temperature_c<box.temperature_range[0]||r.temperature_c>box.temperature_range[1]);};
 const pending=readings.filter(r=>abnormal(r)&&!r.action&&!handled.has(r.id));
 const phases={loading:'装车',transit:'在途',unloading:'卸货'};
 const summary=route.temperature_summary||{};
 const panel=document.createElement('details');panel.className='op-temperature';panel.open=pending.length>0;
 const boxOptions=boxes.map(b=>`<option value="${esc(b.id)}">${esc(b.label)} · ${esc(b.temperature_range.join('—'))}℃</option>`).join('');
 const source=`<label>来源<select name="source">${sourceOptions(task.execution_tasks[wid].data.settings.data_kind==='simulated')}</select></label>`;
 panel.innerHTML=`<summary>温度记录 · ${readings.length}条${pending.length?' · '+pending.length+'条异常待处理':''}</summary><p>装车、在途、卸货分别留温度记录。${summary.reading_count?'':'尚无读数。'}</p><form data-temperature="${esc(wid)}" data-vehicle="${esc(vid)}"><div class="receipt-fields"><label>冷链箱<select name="cold_box">${boxOptions}</select></label><label>环节<select name="phase"><option value="loading">装车</option><option value="transit">在途</option><option value="unloading">卸货</option></select></label><label>时刻<input name="time" type="time" required></label><label>温度 ℃<input name="temperature_c" type="number" min="-100" max="100" step="0.1" required></label>${source}<label>记录依据<input name="note" maxlength="500" placeholder="温度计、设备记录或单据" required></label><label>即时处置<input name="action" maxlength="500" placeholder="异常可先保存，随后补充处置"></label></div><label class="inline-check"><input name="reviewed" type="checkbox" required>已核对箱号、时刻和读数</label><button type="submit">保存温度</button></form>${pending.length?`<form data-temperature-action="${esc(wid)}" data-vehicle="${esc(vid)}"><h5>记录异常处置</h5><div class="receipt-fields"><label>异常读数<select name="target_id">${pending.map(r=>`<option value="${esc(r.id)}" data-cold-box="${esc(r.cold_box)}">${time(r.time)} · ${esc(boxes.find(b=>b.id===r.cold_box)?.label||'冷链箱')} · ${num(r.temperature_c)}℃</option>`).join('')}</select></label><label>处置时刻<input name="time" type="time" required></label>${source}<label>处置说明<input name="action" maxlength="500" placeholder="如隔离货物、联系调度、更换保温设备" required></label><label>记录依据<input name="note" maxlength="500" required></label></div><label class="inline-check"><input name="reviewed" type="checkbox" required>已核对异常与处置内容</label><button type="submit">保存处置</button></form>`:''}${records.length?`<ol class="op-temperature-history">${records.map(r=>`<li><strong>${time(r.time)} · ${esc(boxes.find(b=>b.id===r.cold_box)?.label||'冷链箱')}</strong> ${r.kind==='reading'?`${esc(phases[r.phase]||r.phase)} · ${num(r.temperature_c)}℃${abnormal(r)?' · 超出温区':''}`:'追加处置'}${r.action?' · '+esc(r.action):''}<span>${esc(r.note)}</span></li>`).join('')}</ol>`:''}`;
 const missing=[...new Set((summary.boxes||[]).flatMap(b=>b.missing_phases||[]))];
 panel.querySelector('p').textContent=readings.length?(missing.length?'还缺'+missing.map(k=>phases[k]||k).join('、')+'温度记录。':'三个环节已有记录，可在下方核对读数与处置。'):'装车、在途、卸货分别留温度记录。';
 host.append(panel);
}
document.addEventListener('submit',e=>{
 const form=e.target;if(!form.matches('[data-temperature],[data-temperature-action]'))return;e.preventDefault();
 const f=new FormData(form),clock=String(f.get('time')),minute=Number(clock.slice(0,2))*60+Number(clock.slice(3));
 const isAction=!!form.dataset.temperatureAction,wid=form.dataset.temperatureAction||form.dataset.temperature;
 const temperature={kind:isAction?'action':'reading',vehicle:form.dataset.vehicle,time:minute,source:f.get('source'),note:f.get('note'),action:f.get('action')||''};
 if(isAction){temperature.target_id=f.get('target_id');temperature.cold_box=(task.execution_tasks[wid].temperature_records||[]).find(r=>r.id===temperature.target_id)?.cold_box;}
 else{temperature.cold_box=f.get('cold_box');temperature.phase=f.get('phase');temperature.temperature_c=Number(f.get('temperature_c'));}
 run(()=>mutate('temperature',{warehouse_id:wid,temperature,reviewed:f.get('reviewed')==='on'},'execute'));
});
async function opRenderSpatial(force=false){
 if(!task||step!=='spatial')return;
 const panel=$('spatialPanel'),p=planNow();
 if(!p){opDispose();panel.innerHTML='<div class="result-empty"><h2>仓内作业</h2><p>有配送方案后，可按车查看箱位、装卸和回收步骤。</p><button data-go="prepare" class="primary">核对订单</button></div>';return;}
 const requestedWarehouse=typeof selectedWarehouse==='string'?selectedWarehouse:'';
 const wid=task.data.warehouses.some(w=>w.id===requestedWarehouse)?requestedWarehouse:task.data.warehouses[0].id;
 const group=p.warehouses[wid];
 const vids=Object.keys(group.optimized.routes).filter(v=>group.optimized.routes[v].sequence.length);
 const requestedVehicle=typeof selectedVehicle==='string'?selectedVehicle:'';
 const vid=vids.includes(requestedVehicle)?requestedVehicle:vids[0]||'';
 if(!vid){opDispose();panel.innerHTML='<div class="result-empty"><h2>仓内作业</h2><p>当前仓库还没有可查看的车次。</p><button data-go="plan" class="primary">查看配车方案</button></div>';return;}
 selectedWarehouse=wid;selectedVehicle=vid;updateSelection();
 const key=[task.id,task.revision,p.id,wid,vid].join('/');
 if(!force&&operationalSceneKey===key)return;
 opDispose();operationalSceneKey=key;const request=++operationalRequest;
 const ref=new URLSearchParams({id:task.id,warehouse_id:wid,vehicle_id:vid,plan_id:p.id});
 const route=group.optimized.routes[vid];
 const hasLayout=!!route.operational?.passed||!!route.loading?.placements?.length;
 panel.innerHTML=`<div class="op-heading"><div><h2>仓内作业</h2><p>${state.confirmed?'已核定的装车计划':'待核定的装车计划'}</p></div><div class="op-actions">${hasLayout?`<a class="button" id="openSpatialFull" href="/spatial.html?${ref}" target="_blank" rel="noopener">全屏箱位</a>`:''}${hasLayout&&route.operational?`<a class="button" href="/journey.html?${ref}" target="_blank" rel="noopener">查看作业全程</a>`:''}${state.confirmed?'<button data-go="execute" class="primary">登记回收</button>':''}</div></div>
 <div class="op-selection"><label ${task.data.warehouses.length===1?'hidden':''}>仓库<select id="spatialWarehouse">${task.data.warehouses.map(w=>`<option value="${esc(w.id)}" ${w.id===wid?'selected':''}>${esc(warehouseName(w))}</option>`).join('')}</select></label><label>车辆<select id="spatialVehicle">${vids.map(v=>`<option value="${esc(v)}" ${v===vid?'selected':''}>${esc(displayVehicleId(v))}</option>`).join('')}</select></label></div>
 ${hasLayout?'<div id="operationalCanvas" class="op-canvas" aria-label="车厢三维与装卸时间轴"><p>正在载入车厢…</p></div><div id="operationalEvidence"></div>':'<div class="result-empty"><h3>箱位待核对</h3><p>这趟车缺少可用的车厢尺寸或货物箱规。</p><button data-go="prepare">查看配送条件</button></div>'}`;
 if(!hasLayout)return;
 try{
  const reply=await api('spatial?'+new URLSearchParams({id:task.id,warehouse_id:wid,vehicle_id:vid,plan_id:p.id}));
  if(request!==operationalRequest||step!=='spatial')return;
  if(!window.LogiSpatial3D)throw Error('三维组件未加载，请刷新。');
  const viewer=await window.LogiSpatial3D.mount($('operationalCanvas'),{scene:reply,vehicle_id:vid,onSelect:box=>{if(box?.order)selectedNode=box.order;updateSelection();}});
  if(request!==operationalRequest){viewer?.destroy();return;}
  operationalViewer=viewer;
  $('operationalEvidence').innerHTML=`<details class="op-evidence"><summary>约束检查与参数依据</summary>${opCoverageMarkup(reply.scene)}<ul>${opAssumptions(reply.scene)}</ul><p>${esc((reply.scene.boundaries||[]).join('；'))}</p><small>方案 ${esc(reply.plan_id.slice(0,12))} · 修订 ${reply.task_ref.revision}</small></details>`;
 }catch(e){if(request===operationalRequest){opDispose();const host=$('operationalCanvas');if(host)host.innerHTML=`<div class="result-empty"><h3>车厢暂时无法显示</h3><p>${esc(e.message)}</p><button id="retrySpatial">重新读取</button></div>`;}}
}
const opOriginalRender=render;
render=function(){opOriginalRender();opRenderRules();opRenderPlan();opRenderExecution();syncLifecycleControls();if(step==='spatial')opRenderSpatial();};
const opOriginalShowStep=showStep;
showStep=function(next){if(next!=='spatial'){operationalRequest++;opDispose();}opOriginalShowStep(next);if(next==='spatial')opRenderSpatial();};
document.addEventListener('change',e=>{
 if(e.target.id==='spatialWarehouse'){selectedWarehouse=e.target.value;selectedVehicle='';selectedNode='';updateSelection();opRenderSpatial(true);}
 if(e.target.id==='spatialVehicle'){selectedVehicle=e.target.value;selectedNode='';updateSelection();opRenderSpatial(true);}
});
document.addEventListener('click',e=>{
 const b=e.target.closest('button');if(!b)return;
 if(b.id==='retrySpatial')opRenderSpatial(true);
 if(b.id==='newOperationalBatch')run(async()=>{if(!leaveDraft())return;const source=await api('sample?operational=1');step='prepare';setTask(await api('create',{name:'冷链与回收练习批次',data:source.data}));b.closest('.sample-section').open=false;await refresh();notice('');});
 if(b.dataset.spatialContext){const c=JSON.parse(b.dataset.spatialContext);if(c.plan_id!==planNow()?.id){notice('这份三维记录已过期，请打开当前方案。',true);return;}selectedWarehouse=c.warehouse_id;selectedVehicle=c.vehicle_id;selectedNode=c.order_id||'';showStep('spatial');}
});
const opOriginalResultCards=resultCards;
resultCards=function(run){
 let html=opOriginalResultCards(run);
 const coverage=run.result?.coverage_audit;
 if(coverage)html+=`<section class="result-object"><div class="result-object-head">约束检查<span>逐项核验</span></div><div class="result-rule-changes">${(coverage.warehouses||[]).map(w=>`<strong>${esc(displayWarehouseId(w.warehouse_id))}</strong>${opCoverageMarkup(w.operational)}`).join('')}</div></section>`;
 const spatial=run.result?.spatial_inspect;
 if(spatial?.visual_context)html+=`<section class="result-object"><div class="result-object-head">车厢与装卸<span>${esc(displayVehicleId(spatial.selected?.vehicle_id))}</span></div><div class="result-rule-changes">${esc(spatial.boundary)}</div><button data-spatial-context="${esc(JSON.stringify(spatial.visual_context))}">查看三维装卸</button></section>`;
 return html;
};
if(new URLSearchParams(location.search).get('sample')==='operational'){
 let checks=0;const launch=()=>{if(!busy){$('newOperationalBatch').click();history.replaceState(null,'',location.pathname);}else if(checks++<80)setTimeout(launch,100);};setTimeout(launch,100);
}
