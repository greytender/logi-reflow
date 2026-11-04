'use strict';
const el = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const copy = value => JSON.parse(JSON.stringify(value));
const fmt = (value, digits = 2) => Number(value).toLocaleString('zh-CN', {maximumFractionDigits:digits});
const time = value => `${String(Math.floor(value / 60)).padStart(2,'0')}:${String(Math.floor(value % 60)).padStart(2,'0')}`;
const date = value => new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
let task = null, tasks = [], selectedPlan = null, dirty = false, busy = false, currentView = 'input';
let executionState = null;
const executionDefaults = new WeakMap();
let discardDecision = null;
const businessDefaults = {tariff:{mode:'incremental',base:140,included_km:100,per_km:2,per_stop:15},packaging:{carton_weight_kg:.4,carton_flat_m3:.003,tote_weight_kg:1.5,tote_first_m3:.06,tote_nested_m3:.012}};
const tariffName = mode => ({linear:'固定出车费＋公里费',incremental:'超起步公里部分计费（待确认）',whole_above:'超过起步公里后全里程计费（待确认）'}[mode] || mode);

async function api(operation, body) {
  const response = await fetch(`/api/application/${operation}`, body === undefined ? {} : {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求未完成，请刷新任务后再试');
  if ('execution' in data) executionState = data.execution;
  return data;
}

function notice(message, kind = '') {
  el('notice').hidden = !message;
  el('notice').textContent = message;
  el('notice').className = `notice ${kind}`;
}

function executionValues(form) {
  return JSON.stringify(Array.from(form.querySelectorAll('input,select,textarea'), input =>
    [input.name, input.type === 'checkbox' ? input.checked : input.value]));
}

function hasExecutionDraft(exceptForm = null) {
  return Array.from(document.querySelectorAll('.receipt-form,.observation-form')).some(form =>
    form !== exceptForm && executionDefaults.has(form) && executionValues(form) !== executionDefaults.get(form));
}

async function confirmDiscardChanges(exceptForm = null, includeInputs = false) {
  if (!(includeInputs && dirty) && !hasExecutionDraft(exceptForm)) return true;
  if (discardDecision) return discardDecision;
  const dialog = el('discardDialog'), previousFocus = document.activeElement;
  discardDecision = new Promise(resolve => {
    dialog.returnValue = 'keep';
    dialog.addEventListener('close', () => {
      const discarded = dialog.returnValue === 'discard';
      discardDecision = null;
      if (!discarded) notice('已保留当前页面的未保存填写。');
      resolve(discarded);
      setTimeout(() => {
        if (previousFocus?.isConnected && !previousFocus.disabled) previousFocus.focus();
      }, 0);
    }, {once:true});
    dialog.showModal();
    el('keepDraft').focus();
  });
  return discardDecision;
}

function controls() {
  document.querySelectorAll('button').forEach(button => {
    if (!button.closest('#discardDialog')) button.disabled = busy;
  });
  if (!task) return;
  el('saveState').textContent = busy ? '正在处理…' : dirty ? '修改尚未保存' : `已保存 · 修订 ${task.revision}`;
  el('saveState').classList.toggle('dirty', dirty);
  document.querySelectorAll('#taskEditor input,#taskEditor select').forEach(input => { input.disabled = busy; });
  document.querySelectorAll('#taskEditor [data-business]').forEach(input => {input.disabled = busy || !el('businessEnabled').checked;});
  const executing = !!task.receipts?.length || !!task.observations?.length;
  if (executing) {
    document.querySelectorAll('#taskEditor input,#taskEditor select').forEach(input => {input.disabled=true;});
    el('solveTask').disabled=true; el('saveTask').disabled=true;
  }
  document.querySelectorAll('#executionPanel input,#executionPanel select').forEach(input=>{input.disabled=busy||dirty;});
  document.querySelectorAll('[data-receipt-submit]').forEach(button=>{button.disabled=busy||dirty;});
  document.querySelectorAll('[data-observation-submit]').forEach(button=>{button.disabled=busy||dirty;});
  const remainingCandidate=task.remaining_plans?.at(-1);
  const fresh=remainingCandidate&&!remainingCandidate.adopted_at&&remainingCandidate.receipt_count===(task.receipts||[]).length&&(remainingCandidate.observation_count||0)===(task.observations||[]).length&&remainingCandidate.base_active_id===(task.active_remaining_plan_id||null);
  if(el('replanRemaining')) el('replanRemaining').disabled=busy||dirty||!executing||executionState?.complete;
  if(el('adoptRemaining')) el('adoptRemaining').disabled=busy||dirty||!fresh||!remainingCandidate.audit.passed||!el('reviewRemaining')?.checked;
  const candidate = task.plans.find(p => p.id === selectedPlan);
  const current = candidate && candidate.input_revision === task.input_revision;
  const adopt = el('adoptPlan');
  if (adopt) adopt.disabled = executing || busy || dirty || !current || candidate.result.optimized.unserved.length > 0 || !el('reviewPlan')?.checked;
  const adopted = task.plans.find(p => p.id === task.adopted_plan_id && p.input_revision === task.input_revision);
  for (const [id, enabled, format] of [['exportJson', !dirty, 'json'], ['exportCsv', !!adopted && !dirty, 'csv'], ['exportReceipts', executing && !dirty, 'receipts'], ['exportRemaining',!!adopted&&!dirty,'remaining'],['exportHandoff',!dirty,'handoff']]) {
    const link = el(id), allowed = enabled && !busy;
    link.setAttribute('aria-disabled', String(!allowed));
    if (allowed) link.href = `/api/application/export?id=${encodeURIComponent(task.id)}&revision=${task.revision}&format=${format}`;
    else link.removeAttribute('href');
  }
}

async function perform(work, message) {
  if (busy) return;
  busy = true; controls(); notice(message, 'busy');
  try { await work(); }
  catch (error) { notice(error.message, 'error'); }
  finally { busy = false; controls(); }
}

function renderList() {
  el('taskList').innerHTML = tasks.length ? tasks.map(t => `<button data-task="${esc(t.id)}" aria-current="${t.id === task?.id}"><strong>${esc(t.name)}</strong><small>${t.orders}个门店 · ${esc(t.status)}</small><small>${date(t.updated_at)} · ${t.data_kind === 'simulated' ? '构造数据' : '企业来源声明'}</small></button>`).join('') : '<p class="list-empty">还没有任务。可用三店样例开始，也可导入自己的CSV。</p>';
}

async function refreshList() {
  tasks = (await api('tasks')).tasks;
  renderList();
}

function displayTask(next, preferredView) {
  task = next; dirty = false;
  if (!task.plans.some(p => p.id === selectedPlan)) selectedPlan = task.plans.at(-1)?.id || null;
  el('welcome').hidden = true; el('taskWorkspace').hidden = false;
  el('taskTitle').textContent = task.name;
  el('taskMeta').textContent = `${task.data.orders.length}个门店 · ${task.data.vehicles.length}辆可用车 · 输入版本 ${task.input_revision} · ${task.data.settings.data_kind === 'simulated' ? '构造数据' : '企业来源声明，真实性需核对'}`;
  history.replaceState(null, '', `#task=${task.id}`);
  renderList(); renderEditor(); renderPlan(); renderExecution();
  setView(preferredView || currentView); controls();
}

function setView(view) {
  currentView = view;
  el('inputPanel').hidden = view !== 'input'; el('resultPanel').hidden = view !== 'result';
  el('inputTab').setAttribute('aria-pressed', String(view === 'input'));
  el('resultTab').setAttribute('aria-pressed', String(view === 'result'));
}

function numberInput(group, index, field, value, options = {}) {
  const line = options.line === undefined ? '' : ` data-line="${options.line}"`;
  return `<input type="number" data-group="${group}" data-index="${index}" data-field="${field}"${line} value="${esc(value)}" aria-label="${esc(options.label)}" min="${options.min ?? 0}" step="${options.step ?? 'any'}" required>`;
}

function clockInput(group, index, field, value, label) {
  return `<input type="time" data-group="${group}" data-index="${index}" data-field="${field}" value="${time(value)}" aria-label="${esc(label)}" required>`;
}

function businessEditor(data) {
  const rules = data.business || businessDefaults;
  const fields = (group, entries) => entries.map(([field,label,unit]) => `<label>${label} ${unit}${numberInput(group,0,field,rules[group][field],{label,min:group==='packaging'?.000001:0}).replace('<input ', '<input data-business ' )}</label>`).join('');
  const pickups = data.orders.map((order,i) => `<tr><td>${esc(order.id)}</td>${[['cartons','预计纸箱数'],['totes','预计周转箱数']].map(([field,label])=>`<td>${numberInput('pickup',i,field,order.pickup?.[field] ?? '',{label:`${order.id} ${label}`,step:1}).replace('<input ', '<input data-business ')}</td>`).join('')}</tr>`).join('');
  return `<section class="editor-section"><div class="section-intro"><h2>运费与空包装回收</h2><p>出发前联合试算：先送货、再收空包装，沿途逐站检查容量。</p></div>
    <label class="inline-check"><input id="businessEnabled" type="checkbox" ${data.business?'checked':''}>启用运费与空包装联合试算</label>
    <p class="section-note">新样例中的数量与箱规均为构造假设。CSV不含回收数据，启用时须逐店填写；没有回收请明确填0。此处不记录到店实绩。</p>
    <div class="form-grid"><label class="wide">计费口径<select id="tariffMode" aria-label="计费口径" data-business>${['incremental','whole_above','linear'].map(mode=>`<option value="${mode}" ${rules.tariff.mode===mode?'selected':''}>${tariffName(mode)}</option>`).join('')}</select></label>
    ${fields('tariff',[['base','起步费','元/车次'],['included_km','包含往返公里','km'],['per_km','公里单价','元/km'],['per_stop','门店服务费','元/店']])}</div>
    <p class="section-note">增量分支：起步费＋max(0,往返公里−包含公里)×公里单价＋店数×门店服务费。全里程分支：超过包含公里后，按全部公里收费。两种分支及参数均待业务方确认。门店费已含带回包装，不另收回收费。线性口径使用下方每辆车的固定费和公里费。</p>
    <div class="table-scroll"><table><thead><tr><th>门店</th><th>预计压平纸箱数</th><th>预计套叠周转箱数</th></tr></thead><tbody>${pickups}</tbody></table></div>
    <details><summary>调整压平、套叠空间与重量假设</summary><div class="form-grid">${fields('packaging',[['carton_weight_kg','每只空纸箱重量','kg'],['carton_flat_m3','每只纸箱压平体积','m³'],['tote_weight_kg','每只周转箱重量','kg'],['tote_first_m3','一组周转箱首只体积','m³'],['tote_nested_m3','套叠每增加一只体积','m³']])}</div><p class="section-note">按每店一组套叠，跨店不合并。回收体积＝纸箱数×压平体积＋首只周转箱体积＋其余只数×套叠增量。到店服务分钟须包含预计回收耗时。总体积可容纳不等于3D摆放或装卸顺序已验证。</p></details>
    <p class="section-note">本轮以道路往返公里估算费用；TMS计费公里和实际GPS公里尚未取得，不生成虚构数值。数量变化可在出发前修改并重算；车辆出发后的回执与剩余任务调整另行接入。</p></section>`;
}

function renderEditor() {
  const data = task.data;
  const orderRows = data.orders.flatMap((order, i) => order.lines.map((line, j) => `<tr><td><b>${esc(order.id)}</b><span class="sku">${esc(line.sku)}</span></td><td>${numberInput('line',i,'quantity',line.quantity,{line:j,label:`${order.id} ${line.sku} 箱数`,min:1,step:1})}</td><td>${numberInput('line',i,'unit_weight_kg',line.unit_weight_kg,{line:j,label:`${order.id} ${line.sku} 每箱重量`,min:.000001})}</td><td>${numberInput('line',i,'unit_volume_m3',line.unit_volume_m3,{line:j,label:`${order.id} ${line.sku} 每箱体积`,min:.000001})}</td><td>${fmt(line.quantity*line.unit_weight_kg)} kg / ${fmt(line.quantity*line.unit_volume_m3,3)} m³</td></tr>`)).join('');
  const policyRows = data.orders.map((order, i) => `<tr><td>${esc(order.id)}</td><td>${clockInput('order',i,'open',order.open,`${order.id} 开始收货`)}</td><td>${clockInput('order',i,'due',order.due,`${order.id} 承诺时刻`)}</td><td>${clockInput('order',i,'close',order.close,`${order.id} 最晚截止`)}</td><td>${numberInput('order',i,'service_minutes',order.service_minutes,{label:`${order.id} 服务分钟`})}</td><td><input data-group="order" data-index="${i}" data-field="allowed_vehicles" value="${esc(order.allowed_vehicles.join('|'))}" aria-label="${esc(order.id)} 允许车辆" required></td></tr>`).join('');
  const vehicleRows = data.vehicles.map((vehicle,i) => `<tr><td><b>${esc(vehicle.id)}</b></td>${[['max_weight_kg','载重上限'],['max_volume_m3','容积上限'],['fixed_cost','固定出车费'],['cost_per_km','每公里费用']].map(([field,label]) => `<td>${numberInput('vehicle',i,field,vehicle[field],{label:`${vehicle.id} ${label}`,min:field.startsWith('max_') ? 0.000001 : 0})}</td>`).join('')}<td>${clockInput('vehicle',i,'shift_start',vehicle.shift_start,`${vehicle.id} 出班时间`)}</td><td>${clockInput('vehicle',i,'shift_end',vehicle.shift_end,`${vehicle.id} 回仓截止`)}</td></tr>`).join('');
  const roads = Object.entries(data.legs).map(([key,leg],i) => `<tr><td>${esc(key.replace('>',' → '))}</td><td>${numberInput('road',i,'distance_km',leg.distance_km,{label:`${key} 道路公里`})}</td><td>${numberInput('road',i,'travel_minutes',leg.travel_minutes,{label:`${key} 行驶分钟`})}</td></tr>`).join('');
  el('taskEditor').innerHTML = `<div class="boundary"><strong>出发前试算：路线、重量体积、逐站空包装回收与分项运费。</strong> 计费和压缩参数可修改，仍待业务确认。冷链、3D与执行回执尚未接入，方案不会实际派发。</div>
    <section class="editor-section"><div class="section-intro"><h2>任务与数据来源</h2><p>先核对当前假设；修改后保存并重新计算。</p></div><div class="form-grid">
    <label>任务名称<input id="taskName" value="${esc(task.name)}" maxlength="80" required></label>
    <label>数据来源声明<select id="dataKind"><option value="simulated" ${data.settings.data_kind === 'simulated'?'selected':''}>构造 / 模拟数据</option><option value="enterprise" ${data.settings.data_kind === 'enterprise'?'selected':''}>企业提供（须自行核对）</option></select></label>
    <label class="wide">道路距离与时间的来源<input id="travelSource" value="${esc(data.settings.travel_source)}" maxlength="500" required></label>
    <label>收货截止核验方式<select id="windowRule"><option value="finish" ${data.settings.window_rule==='finish'?'selected':''}>以预计服务完成核验</option><option value="arrival" ${data.settings.window_rule==='arrival'?'selected':''}>以预计到达核验</option></select></label></div><p class="section-note">仓库编号：${esc(data.settings.depot_id)}。来源声明不自动证明真实性；预计服务完成也不代表实际签收。</p></section>
    <section class="editor-section"><div class="section-intro"><h2>门店与货物</h2><p>同一门店的货物整次配送，不拆车。可直接修改箱数、重量和体积。</p></div><div class="table-scroll"><table><thead><tr><th>门店 / 货物</th><th>箱数</th><th>每箱重量 kg</th><th>每箱体积 m³</th><th>已保存的本行合计</th></tr></thead><tbody>${orderRows}</tbody></table></div>
    <details><summary>收货时间、服务用时与车辆准入</summary><div class="table-scroll"><table><thead><tr><th>门店</th><th>开始收货</th><th>承诺时刻</th><th>最晚截止</th><th>服务分钟</th><th>允许车辆，用 | 分隔</th></tr></thead><tbody>${policyRows}</tbody></table></div></details></section>
    ${businessEditor(data)}
    <section class="editor-section"><div class="section-intro"><h2>可用车辆与线性费用参数</h2><p>固定出车费和每公里费用仅在线性计费口径使用。</p></div><div class="table-scroll"><table><thead><tr><th>车辆</th><th>载重上限 kg</th><th>容积上限 m³</th><th>固定出车费 元</th><th>每公里 元</th><th>出班时间</th><th>回仓截止</th></tr></thead><tbody>${vehicleRows}</tbody></table></div><p class="section-note">这里展示明确的有限车辆池，尚未自动增加车辆。新增门店、车辆或更换编号可通过四份CSV导入新任务。</p></section>
    <section class="editor-section"><details><summary>核对与修改有向道路表（${Object.keys(data.legs).length}个方向）</summary><p class="section-note">距离和行驶分钟分别核对；A到B与B到A可以不同。此表不是实时路况或GPS。</p><div class="table-scroll"><table><thead><tr><th>起点 → 终点</th><th>公里</th><th>行驶分钟</th></tr></thead><tbody>${roads}</tbody></table></div></details></section>`;
}

function collectInput() {
  // Busy UI disables controls; validate the relevant fields before reading them.
  const inputs = Array.from(document.querySelectorAll('#taskEditor input,#taskEditor select'));
  const states = inputs.map(input=>input.disabled);
  let valid;
  try {
    inputs.forEach(input=>{input.disabled = input.hasAttribute('data-business') && !el('businessEnabled').checked;});
    valid = el('taskEditor').reportValidity();
  } finally {inputs.forEach((input,i)=>{input.disabled=states[i];});}
  if (!valid) throw new Error('请先修正表单中标出的输入');
  const data = copy(task.data), roadKeys = Object.keys(data.legs);
  data.settings.data_kind = el('dataKind').value;
  data.settings.travel_source = el('travelSource').value.trim();
  data.settings.window_rule = el('windowRule').value;
  if (el('businessEnabled').checked) {
    data.business = copy(data.business || businessDefaults);
    data.business.tariff.mode = el('tariffMode').value;
    data.orders.forEach(order=>{order.pickup = copy(order.pickup || {cartons:0,totes:0});});
  } else {
    delete data.business; data.orders.forEach(order=>{delete order.pickup;});
  }
  document.querySelectorAll('#taskEditor [data-group]').forEach(input => {
    if(input.hasAttribute('data-business') && !el('businessEnabled').checked) return;
    const {group,index,field,line} = input.dataset;
    const destination = group==='line' ? data.orders[index].lines[line] : group==='order' ? data.orders[index] : group==='vehicle' ? data.vehicles[index] : group==='pickup' ? data.orders[index].pickup : ['tariff','packaging'].includes(group) ? data.business[group] : data.legs[roadKeys[index]];
    if (field==='allowed_vehicles') destination[field] = input.value.split('|').map(x=>x.trim()).filter(Boolean);
    else if (input.type==='time') {
      const [hours,minutes] = input.value.split(':').map(Number); destination[field] = hours*60+minutes;
    } else destination[field] = Number(input.value);
  });
  return {name:el('taskName').value.trim(),data};
}

function renderPlan() {
  const plan = task.plans.find(p => p.id===selectedPlan) || task.plans.at(-1);
  el('resultEmpty').hidden = !!plan;
  if (!plan) { el('planContent').innerHTML = ''; return; }
  selectedPlan = plan.id;
  const result = plan.result, optimized = result.optimized, metrics = optimized.metrics;
  const current = plan.input_revision===task.input_revision, adopted = task.adopted_plan_id===plan.id;
  const used = Object.entries(optimized.routes).filter(([,route])=>route.sequence.length);
  const vehicles = Object.fromEntries(result.data.vehicles.map(v=>[v.id,v]));
  const routes = used.map(([id,route]) => {
    const vehicle=vehicles[id];
    const fee = route.fee;
    const breakdown = fee ? `<div class="fee-breakdown"><b>这车费用怎么来</b><p>${esc(tariffName(fee.mode))}：起步/固定 ${fmt(fee.base)} ＋里程 ${fmt(fee.distance)} ＋门店服务 ${fmt(fee.stops)} ＝ ${fmt(route.cost)} 元；额外回收费 0 元。</p><p>计费公里暂用道路估算 ${fmt(fee.estimated_billing_km)} km · TMS计费公里：未取得 · GPS实绩：未取得</p></div>` : '';
    const capacity = fee ? `<p class="section-note">沿途峰值 ${fmt(route.peak_weight_kg)} kg / ${fmt(route.peak_volume_m3,3)} m³；回仓空包装 ${fmt(route.return_pickup_weight_kg)} kg / ${fmt(route.return_pickup_volume_m3,3)} m³。按每店先卸货再回收核验。</p>` : '';
    return `<article class="route"><div class="route-head"><h3>${esc(id)} · ${route.sequence.length}个门店</h3><p>往返 ${fmt(route.distance)} km · 预计 ${time(route.return_at)} 回仓 · ${fmt(route.cost)} 元</p></div><ol class="route-sequence" aria-label="${esc(id)} 配送顺序"><li class="depot">${esc(result.data.settings.depot_id)} 仓库</li>${route.sequence.map(s=>`<li>${esc(s)}</li>`).join('')}<li class="depot">回仓</li></ol><div class="route-details"><span>出仓重量 <b>${fmt(route.weight_kg)} / ${fmt(vehicle.max_weight_kg)} kg</b>（${fmt(route.weight_kg/vehicle.max_weight_kg*100,1)}%）</span><span>出仓体积 <b>${fmt(route.volume_m3,3)} / ${fmt(vehicle.max_volume_m3,3)} m³</b>（${fmt(route.volume_m3/vehicle.max_volume_m3*100,1)}%）</span></div>${capacity}${breakdown}<div class="table-scroll"><table><thead><tr><th>顺序</th><th>门店</th><th>预计到达</th><th>预计服务结束</th><th>等待分钟</th>${fee?'<th>预计纸箱 / 周转箱</th><th>本店回收 m³</th><th>离店在车 kg / m³</th>':''}</tr></thead><tbody>${route.stops.map((s,i)=>`<tr><td>${i+1}</td><td>${esc(s.id)}</td><td>${time(s.arrival)}</td><td>${time(s.finish)}</td><td>${fmt(s.wait_minutes)}</td>${fee?`<td>${s.pickup.cartons} / ${s.pickup.totes}</td><td>${fmt(s.pickup_volume_m3,3)}</td><td>${fmt(s.onboard_weight_kg)} / ${fmt(s.onboard_volume_m3,3)}</td>`:''}</tr>`).join('')}</tbody></table></div></article>`;
  }).join('');
  const unserved = optimized.unserved.length ? `<section class="unserved"><h3>${optimized.unserved.length}个门店未安排，暂不能确认</h3>${optimized.unserved.map(o=>`<p><b>${esc(o.id)}</b>：${esc(o.reason)}。${esc(o.action)}</p>`).join('')}</section>` : '';
  const reference = result.baseline.metrics;
  const options=task.plans.map((p,i)=>`<option value="${p.id}" ${p.id===plan.id?'selected':''}>方案 ${i+1} · ${date(p.created_at)}${p.input_revision!==task.input_revision?' · 输入已过期':''}${p.id===task.adopted_plan_id?' · 曾确认':''}</option>`).join('');
  el('planContent').innerHTML = `<div class="plan-top"><span class="status-text ${!current||optimized.unserved.length?'warning':''}">${!current?'历史方案：输入已更新':adopted?'当前已确认方案':optimized.unserved.length?'部分任务未安排':'计算完成，等待核对'}</span><label for="planVersion">查看版本<select id="planVersion">${options}</select></label></div>
    ${!current?'<div class="boundary">这份结果属于旧输入，仅供查看。请对当前输入重新计算，再确认方案。</div>':''}
    <dl class="metrics"><div><dt>已安排门店</dt><dd>${metrics.served}<small>/ ${result.data.orders.length}</small></dd></div><div><dt>使用车次</dt><dd>${used.length}<small>车次</small></dd></div><div><dt>试算费用</dt><dd>${fmt(metrics.cost)}<small>元</small></dd></div><div><dt>往返总里程</dt><dd>${fmt(metrics.distance)}<small>km</small></dd></div></dl>
    <div class="boundary">${result.data.settings.data_kind==='simulated'?'构造数据':'企业来源声明，真实性待核对'} · ${result.data.business?'包含预测回收逐站重量体积核验；费用按所选分支估算，参数待业务确认。':'仅路线与配送总量核验；费用为固定出车费＋公里费，未启用回收。'} 不代表3D、冷链或实际装卸可行，未实际派单。</div>
    ${unserved}${routes}
    <div class="comparison"><h3>同批输入，与简单追加方法比较</h3><div class="table-scroll"><table><thead><tr><th>方法</th><th>已安排门店</th><th>费用 元</th><th>往返里程 km</th><th>超过承诺的分钟数</th></tr></thead><tbody><tr><td>按输入顺序依次追加</td><td>${reference.served}</td><td>${fmt(reference.cost)}</td><td>${fmt(reference.distance)}</td><td>${fmt(reference.late_minutes)}</td></tr><tr><td>有限搜索候选</td><td>${metrics.served}</td><td>${fmt(metrics.cost)}</td><td>${fmt(metrics.distance)}</td><td>${fmt(metrics.late_minutes)}</td></tr></tbody></table></div><p>基线是编码规则，未取得企业人工计划。${result.comparison_scope.same_served_stops?'两种方法配送集合相同。':'两种方法配送集合不同，不能计算同任务节省率。'} 本次求解 ${fmt(optimized.solve_ms)} ms，保留无改善或不完整结果。</p></div>
    <p class="audit-line">独立核算已通过：${result.audit.optimized.checks.map(esc).join('、')}。</p>
    <section class="confirm-section">${adopted&&current?'<p class="confirmed-note">方案已确认并保存，尚未派单。</p><p>配送CSV保留出发前预测；下方可记录执行。首次保存回执后锁定输入和原方案。</p>':`<h3>核对后确认，保存这份安排</h3><p>确认只保存本机计划，不发送给司机或企业系统。修改输入不会覆盖这份历史结果。</p><label><input id="reviewPlan" type="checkbox" ${!current||optimized.unserved.length?'disabled':''}>我已核对配送顺序、未安排任务、容量与试算费用</label><button id="adoptPlan" class="primary" disabled>确认并保存此方案</button>`}</section>`;
  el('planVersion').addEventListener('change', event => {selectedPlan=event.target.value;renderPlan();controls();});
  el('reviewPlan')?.addEventListener('change',controls);
  el('adoptPlan')?.addEventListener('click',()=>perform(async()=>{
    if (!(await confirmDiscardChanges())) return;
    const answer=await api('adopt',{id:task.id,revision:task.revision,plan_id:selectedPlan,reviewed:el('reviewPlan').checked});
    await refreshList();displayTask(answer.task,'result');notice('方案已确认并保存。可以导出配送清单；尚未实际派单。');
  },'正在核验并保存确认方案…'));
}

function renderExecution() {
  const panel=el('executionPanel');
  panel.hidden=!executionState;
  if (!executionState) {panel.innerHTML='';return;}
  const simulated=task.data.settings.data_kind==='simulated';
  const receipts=task.receipts||[];
  panel.innerHTML=`<h2>执行记录与剩余预测</h2>
    <p class="execution-intro">按已确认方案记录位置、等待、整单签收与回收。已记录事实保留，原配送CSV仍是出发前预测。${receipts.length||task.observations?.length?'输入和方案已锁定。':'首次保存记录后锁定输入和方案。'}</p>
    <p class="boundary">${simulated?'本任务为构造数据，只能保存模拟演练。':'人工录入需注明单据或信息来源，真实性待核对。'} 时间由你填写；载荷按箱规估算，未接入司机、GPS或电子签收。可计算同车剩余改序候选，核对后采用；不换车、不重新装货，不重新结算运费。${task.active_remaining_plan_id?'当前剩余预测已使用人工采用的改序版本。':''}</p>
    <div id="remainingPlanner" class="remaining-planner"></div>
    ${Object.entries(executionState.routes).map(([vid,r])=>{
      const history=receipts.filter(x=>x.vehicle===vid);
      return `<article class="execution-vehicle"><h3>${esc(vid)} · ${r.returned?'已记录回仓交接':`已签收 ${r.completed} 店，剩余 ${r.remaining.length} 店`}</h3>
        <p>累计记录回收：${r.collected_cartons} 只纸箱、${r.collected_totes} 只周转箱。${r.returned?'回收包装已记录交回仓库。':`当前车载估算 ${fmt(r.onboard_weight_kg)} kg / ${fmt(r.onboard_volume_m3,3)} m³；预计回仓 ${time(r.estimated_return)}。`}</p>
        ${r.anchor?`<p class="anchor-line">预测起点：${esc(r.anchor.node)} · ${time(r.anchor.time)} · ${r.anchor.kind==='observation'?'位置/等待记录':r.anchor.kind==='receipt'?'上一条执行回执':'原计划班次'}。来源：${r.anchor.source==='simulated'?'模拟演练':r.anchor.source==='manual'?'人工录入，待核对':'计划假设'}。</p>`:''}
        ${r.warnings.length?`<div class="unserved" role="status"><h4>剩余路线存在风险，需人工处理</h4>${r.warnings.map(w=>`<p>${esc(w)}</p>`).join('')}<p>记录保留，不会把已发生的超载记录改成预测数量。</p></div>`:''}
        ${r.remaining.length?`<div class="table-scroll"><table><caption>剩余门店预测 · 已按记录更新</caption><thead><tr><th>门店</th><th>预计到达</th><th>预计完成</th><th>离店 kg / m³</th></tr></thead><tbody>${r.remaining.map(s=>`<tr><td>${esc(s.id)}</td><td>${time(s.arrival)}</td><td>${time(s.finish)}</td><td>${fmt(s.onboard_weight_kg)} / ${fmt(s.onboard_volume_m3,3)}</td></tr>`).join('')}</tbody></table></div>`:''}
        ${history.length?`<details class="receipt-history"><summary>查看本车 ${history.length} 条已保存记录</summary>${history.map(x=>`<p>${x.kind==='return'?'回仓交接':esc(x.stop_id)+' 整单签收'} · ${time(x.finish)} · ${x.source==='simulated'?'模拟演练':'人工录入，待核对'}${x.kind==='stop'&&task.data.business?` · 纸箱 ${x.pickup.cartons} / 周转箱 ${x.pickup.totes}`:''}${x.note?' · '+esc(x.note):''}</p>`).join('')}</details>`:''}
        ${observationEditor(vid,r,simulated)}
        ${r.returned?'':`<form data-receipt-vehicle="${esc(vid)}" class="receipt-form">
          <h4>${r.next_stop?`记录下一店：${esc(r.next_stop)}`:'记录回仓与空包装交接'}</h4>
          <div class="receipt-fields"><label>记录来源<select name="source"><option value="simulated">模拟演练</option>${simulated?'':'<option value="manual">人工录入，真实性待核对</option>'}</select></label>
          <label>${r.next_stop?'签收与回收完成时间':'回仓交接时间'}<input name="finish" type="time" required aria-label="${esc(vid)} 完成时间"></label>
          ${r.next_stop&&task.data.business?`<label>本店记录纸箱数<input name="cartons" type="number" min="0" max="100000" step="1" required></label><label>本店记录周转箱数<input name="totes" type="number" min="0" max="100000" step="1" required></label>`:''}
          <label class="receipt-note">来源说明 / 备注<input name="note" maxlength="500" placeholder="人工录入时填写单据或信息来源"></label></div>
          <label class="inline-check"><input name="reviewed" type="checkbox" required>已核对来源、时间和数量；保存后本页不支持撤回</label>
          <button class="primary" data-receipt-submit>${r.next_stop?'保存签收与回收记录':'保存回仓交接记录'}</button>
        </form>`}</article>`;
    }).join('')}
    ${executionState.complete?'<p class="confirmed-note">全部车辆已记录回仓交接，可导出执行记录核对。记录完整不代表真实履约已获验证。</p>':''}`;
  renderRemainingPlanner();
  panel.querySelectorAll('.receipt-form').forEach(form=>form.addEventListener('submit',async event=>{
    event.preventDefault();
    if (!(await confirmDiscardChanges(form))) return;
    const fields=new FormData(form),vid=form.dataset.receiptVehicle,r=executionState.routes[vid];
    perform(async()=>{
      if(dirty) throw new Error('输入有未保存变化，请先保存并重新确认方案');
      const [h,m]=String(fields.get('finish')).split(':').map(Number);
      const receipt={vehicle:vid,kind:r.next_stop?'stop':'return',stop_id:r.next_stop,
        finish:h*60+m,source:fields.get('source'),note:fields.get('note')};
      if(r.next_stop&&task.data.business)receipt.pickup={cartons:Number(fields.get('cartons')),totes:Number(fields.get('totes'))};
      const answer=await api('receipt',{id:task.id,revision:task.revision,receipt,reviewed:fields.get('reviewed')==='on'});
      await refreshList();displayTask(answer.task,'result');notice('记录已保存。请核对剩余路线及风险提示。');
    },'正在保存执行记录并更新剩余预测…');
  }));
  panel.querySelectorAll('.observation-form').forEach(form=>form.addEventListener('submit',async event=>{
    event.preventDefault();
    if (!(await confirmDiscardChanges(form))) return;
    const fields=new FormData(form),vid=form.dataset.vehicle;
    const [h,m]=String(fields.get('observed_at')).split(':').map(Number);
    perform(async()=>{
      if(dirty) throw new Error('请先保存当前输入并重新核对方案');
      const observation={vehicle:vid,kind:fields.get('kind'),node:fields.get('node'),observed_at:h*60+m,source:fields.get('source'),note:fields.get('note')};
      const answer=await api('observe',{id:task.id,revision:task.revision,observation,reviewed:fields.get('reviewed')==='on'});
      await refreshList();displayTask(answer.task,'result');notice('位置/等待记录已保存，剩余预测已更新；未签收货物，旧候选需重新计算。');
    },'正在记录位置/等待并更新剩余预测…');
  }));
  panel.querySelectorAll('.observation-form [name="kind"]').forEach(input=>input.addEventListener('change',()=>{
    if(input.value==='wait')input.form.elements.node.value=executionState.routes[input.form.dataset.vehicle].anchor.node;
  }));
  panel.querySelectorAll('.receipt-form,.observation-form').forEach(form =>
    executionDefaults.set(form, executionValues(form)));
}

function observationEditor(vid,route,simulated) {
  const observations=(task.observations||[]).filter(o=>o.vehicle===vid);
  const history=observations.length?`<details class="receipt-history"><summary>位置与等待历史 · ${observations.length} 条</summary>${observations.map(o=>`<p>${o.kind==='wait'?'原地等待至':'已知节点位置'} ${time(o.observed_at)} · ${esc(o.node)} · ${o.source==='simulated'?'模拟演练':'人工录入，待核对'}${o.note?' · '+esc(o.note):''}</p>`).join('')}</details>`:'';
  if(route.returned||!route.anchor)return history;
  const plan=task.plans.find(p=>p.id===task.adopted_plan_id);
  const nodes=[task.data.settings.depot_id,...plan.result.optimized.routes[vid].sequence];
  return `${history}<details class="observation-editor"><summary>记录已知位置或等待</summary><p>只支持本车原方案门店/仓库节点，非实时GPS。等待保持当前节点；位置记录不会自动签收或卸货，货物和包装仍在原车。记录会使旧候选失效。</p>
    <form class="observation-form" data-vehicle="${esc(vid)}"><div class="receipt-fields">
      <label>记录类型<select name="kind"><option value="wait">在当前节点等待至</option><option value="position">记录已知节点位置</option></select></label>
      <label>已知节点<select name="node" aria-label="${esc(vid)} 观察节点">${nodes.map(n=>`<option value="${esc(n)}" ${n===route.anchor.node?'selected':''}>${esc(n)}</option>`).join('')}</select></label>
      <label>记录时刻<input name="observed_at" type="time" required aria-label="${esc(vid)} 观察时刻"></label>
      <label>位置记录来源<select name="source"><option value="simulated">模拟演练</option>${simulated?'':'<option value="manual">人工录入，真实性待核对</option>'}</select></label>
      <label class="receipt-note">位置/等待来源说明<input name="note" maxlength="500" placeholder="人工录入请填写司机消息或单据来源"></label></div>
      <label class="inline-check"><input name="reviewed" type="checkbox" required>已核对位置、时刻和来源；此操作不代表签收</label>
      <button data-observation-submit>保存位置/等待记录</button></form></details>`;
}

function renderRemainingPlanner() {
  const candidate=task.remaining_plans?.at(-1);
  const fresh=candidate&&!candidate.adopted_at&&candidate.receipt_count===(task.receipts||[]).length&&(candidate.observation_count||0)===(task.observations||[]).length&&candidate.base_active_id===(task.active_remaining_plan_id||null);
  el('remainingPlanner').innerHTML=`<h3>调整剩余配送顺序</h3><p>保留已签收门店、原车货物归属和已收包装。从最新有效位置/等待记录继续试算；没有记录时沿用最近回执或原计划班次。只支持已知节点，不含任意GPS位置。搜索不保证全局最优，未找到可行候选不等于无解。</p>
    <button id="replanRemaining">计算剩余路线候选</button>
    ${candidate?`<div class="remaining-candidate"><h4>${candidate.adopted_at?'该候选已采用':!fresh?'历史候选已失效，需重新计算':candidate.audit.passed?'候选已通过独立核验，等待核对':'候选存在约束风险，不能采用'}</h4>
      <p>计算时剩余道路里程 ${fmt(candidate.distance_before)} → ${fmt(candidate.distance_after)} km。${candidate.changed?'配送顺序有变化。':'未找到更好的改序，保留原顺序。'} 仅比较剩余道路估算，不代表运费或企业收益变化。</p>
      <div class="table-scroll"><table><caption>计算时的前后对比 · 后续回执会使未采用候选失效</caption><thead><tr><th>车辆</th><th>原剩余顺序</th><th>候选顺序</th><th>预计回仓 前 / 后</th></tr></thead><tbody>${Object.entries(candidate.after.routes).map(([vid,r])=>`<tr><td>${esc(vid)}</td><td>${candidate.before.routes[vid].remaining.map(s=>esc(s.id)).join(' → ')||'无'}</td><td>${r.remaining.map(s=>esc(s.id)).join(' → ')||'无'}</td><td>${time(candidate.before.routes[vid].estimated_return)} / ${time(r.estimated_return)}</td></tr>`).join('')}</tbody></table></div>
      ${!candidate.audit.passed?`<p class="status-text warning">${esc(candidate.audit.error)}</p>`:`<p class="audit-line">独立核验：${esc(candidate.audit.scope)}</p>`}
      <label class="inline-check"><input id="reviewRemaining" type="checkbox">已核对改序、已收包装、剩余时效与风险</label><button id="adoptRemaining" class="primary" disabled>采用这份剩余路线</button>
      <details class="receipt-history"><summary>剩余路线历史 · ${task.remaining_plans.length} 份</summary>${task.remaining_plans.map((p,i)=>`<p>候选 ${i+1} · ${date(p.created_at)} · ${p.adopted_at?'已采用':p===candidate&&fresh?'待核对':'未采用 / 历史'} · ${fmt(p.distance_before)} → ${fmt(p.distance_after)} km · ${p.audit.passed?'核验通过':'未通过'}</p>`).join('')}</details></div>`:''}`;
  el('replanRemaining').addEventListener('click',async ()=>{
    if (!(await confirmDiscardChanges())) return;
    perform(async()=>{
    const answer=await api('replan',{id:task.id,revision:task.revision});
    await refreshList();displayTask(answer.task,'result');notice('剩余路线候选已保存，尚未改变当前安排。请核对后采用。');
  },'正在计算并独立核验剩余路线…');
  });
  el('reviewRemaining')?.addEventListener('change',controls);
  el('adoptRemaining')?.addEventListener('click',()=>perform(async()=>{
    if (!(await confirmDiscardChanges())) return;
    const answer=await api('adopt-remaining',{id:task.id,revision:task.revision,candidate_id:candidate.id,reviewed:el('reviewRemaining').checked});
    await refreshList();displayTask(answer.task,'result');notice('剩余路线已采用。原方案与回执保留，下一店及导出已更新。');
  },'正在重新核验并采用剩余路线…'));
}

async function persist() {
  if (!dirty) return;
  const input=collectInput();
  task=(await api('save',{id:task.id,revision:task.revision,...input})).task;
  dirty=false;
}

async function newSample() {
  await perform(async()=>{
    if (!(await confirmDiscardChanges(null, true))) {notice('当前任务仍在，未保存填写已保留。');return;}
    const data=(await api('sample')).data;
    const answer=await api('create',{name:'三店配送任务',data});
    selectedPlan=null;await refreshList();displayTask(answer.task,'input');notice('任务已创建并保存。可修改箱数、车辆和道路，再运行算法。');
  },'正在创建独立保存的小任务…');
}

el('newTask').addEventListener('click',newSample);
el('startSample').addEventListener('click',newSample);
el('inputTab').addEventListener('click',()=>setView('input'));
el('resultTab').addEventListener('click',()=>setView('result'));
el('taskEditor').addEventListener('submit',event=>event.preventDefault());
el('taskEditor').addEventListener('input',()=>{dirty=true;controls();});
el('taskEditor').addEventListener('change',()=>{dirty=true;controls();});
el('saveTask').addEventListener('click',()=>perform(async()=>{
  if (!(await confirmDiscardChanges())) return;
  collectInput();await persist();await refreshList();displayTask(task,'input');notice('输入已保存。参数变化后，请重新计算方案。');
},'正在保存输入…'));
el('solveTask').addEventListener('click',()=>perform(async()=>{
  if (!(await confirmDiscardChanges())) return;
  collectInput();await persist();
  const answer=await api('solve',{id:task.id,revision:task.revision});
  selectedPlan=answer.task.plans.at(-1).id;await refreshList();displayTask(answer.task,'result');notice('计算结果已保存。请核对后确认；计算不会自动采用方案。');
},'正在保存并运行路线算法与独立核算…'));
el('taskList').addEventListener('click',event=>{
  const button=event.target.closest('[data-task]');if (!button) return;
  perform(async()=>{
    if (!(await confirmDiscardChanges(null, true))) {notice('已保留当前页面的填写。');return;}
    const answer=await api(`task?id=${encodeURIComponent(button.dataset.task)}`);
    selectedPlan=null;displayTask(answer.task,answer.task.plans.length?'result':'input');notice('已恢复保存的任务。');
  },'正在读取任务…');
});
el('refreshTasks').addEventListener('click',()=>perform(async()=>{
  if (dirty || hasExecutionDraft()) {await refreshList();notice('任务列表已刷新，当前未保存填写仍保留。');return;}
  await refreshList();if(task){const answer=await api(`task?id=${task.id}`);displayTask(answer.task);}notice('已刷新保存状态。');
},'正在刷新…'));
el('importTask').addEventListener('click',()=>perform(async()=>{
  if (!(await confirmDiscardChanges(null, true))) {notice('当前任务仍在，未保存填写已保留。');return;}
  const files=Array.from(el('importFiles').files);
  if (files.length!==4||files.reduce((sum,f)=>sum+f.size,0)>2000000) throw new Error('请选择同批四份CSV/JSON文件，合计不超过2MB');
  const entries=await Promise.all(files.map(async file=>[file.name,await file.text()]));
  const answer=await api('create',{name:'导入配送任务',csv_files:Object.fromEntries(entries)});
  selectedPlan=null;await refreshList();displayTask(answer.task,'input');notice('导入任务已保存，请核对数据来源、货量和规则。');
},'正在校验并保存CSV输入…'));
window.addEventListener('beforeunload',event=>{if(dirty || hasExecutionDraft()){event.preventDefault();event.returnValue='';}});

perform(async()=>{
  await refreshList();
  const id=new URLSearchParams(location.hash.slice(1)).get('task');
  if(id){const answer=await api(`task?id=${encodeURIComponent(id)}`);displayTask(answer.task,answer.task.plans.length?'result':'input');}
  notice('');
},'正在读取本机任务…');
