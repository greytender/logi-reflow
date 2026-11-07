(() => {
  'use strict';

  const host = document.getElementById('intakeDispatch');
  const byId = id => document.getElementById(id);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
  })[c]);
  let review = null;
  let template = null;
  let ready = false;
  let busy = false;
  let generation = 0;

  host.innerHTML = `<div class="section-intro"><h2>批次资料</h2><span id="dispatchScope" class="save-state"></span></div>
    <div class="dispatch-form">
      <p id="dispatchNext">保存订单核对后，可检查仓库、车辆和道路资料。</p>
      <div class="dispatch-actions"><button id="downloadDispatchTemplate" type="button">下载批次模板</button><button id="downloadStoreReferences" type="button">下载门店与箱规编号</button></div>
      <div class="dispatch-fields"><label>导入批次参数 <input id="dispatchParameterFile" type="file" accept=".json,application/json"></label></div>
      <details id="dispatchSetup"><summary>录入仓库、运力与道路</summary>
        <form id="dispatchSetupForm" class="dispatch-setup-form">
          <div class="form-grid">
            <label>仓库编号<input name="depot" required maxlength="40" placeholder="如 CS-WH"></label>
            <label>仓库名称<input name="warehouse" required maxlength="80" placeholder="如 长沙中心仓"></label>
            <label>本批条件<select name="dataKind" required><option value="">请选择</option><option value="enterprise">业务资料</option><option value="simulated">试算条件</option></select></label>
            <label>道路来源<input name="roadSource" required maxlength="500" placeholder="如 调度员提供的线路表"></label>
            <label>同规格车辆数量<input name="vehicleCount" type="number" min="1" max="24" required></label>
            <label>单车载重 kg<input name="weight" type="number" min="0.01" step="any" required></label>
            <label>单车容积 m³<input name="volume" type="number" min="0.01" step="any" required></label>
            <label>发车时刻<input name="shiftStart" type="time" required></label>
            <label>最晚返仓<input name="shiftEnd" type="time" required></label>
            <label>门店开始收货<input name="open" type="time" required></label>
            <label>承诺时刻<input name="due" type="time" required></label>
            <label>截止时刻<input name="close" type="time" required></label>
            <label>时间窗依据<select name="windowRule"><option value="finish">交付完成</option><option value="arrival">到达门店</option></select></label>
            <label>每站服务分钟<input name="service" type="number" min="0" max="240" step="any" required></label>
            <label>每店预计回收纸箱<input name="cartons" type="number" min="0" max="100000" required></label>
            <label>每店预计回收周转箱<input name="totes" type="number" min="0" max="100000" required></label>
            <label class="wide">有向道路 CSV<input name="roads" type="file" accept=".csv,text/csv" required></label>
            <label class="wide">装卸与冷链规则 JSON（有冷链时必填）<input name="operations" type="file" accept=".json,application/json"></label>
          </div>
          <p>道路表列为 from_id, to_id, distance_km, travel_minutes。车辆按 V01 起编号。异型车辆、单店规则和费率可在批次参数中调整。</p>
          <button id="stageDispatchSetup" type="submit">整理批次参数</button>
        </form>
      </details>
      <details id="dispatchEdit"><summary>填写或编辑批次参数</summary>
        <p>包含仓库、车辆、往返道路、收货时段和预计回收。冷链任务需一并提供箱规与保温规则。</p>
        <label for="dispatchParameters" class="sr-only">批次参数 JSON</label><textarea id="dispatchParameters" spellcheck="false" placeholder="载入已维护的批次参数，或在此填写模板"></textarea>
      </details>
      <div class="dispatch-confirmations" id="dispatchConfirmations" hidden>
        <label id="confirmColdLabel"><input type="checkbox" id="confirmBlankCold"><span id="blankColdText"></span></label>
        <label><input type="checkbox" id="confirmFormulaCache">已核对公式缓存的重量、体积与单位</label>
      </div>
      <div class="dispatch-actions"><button id="previewDispatch" type="button" class="primary">核验批次</button><button id="createDispatch" type="button" hidden>建立调度任务</button></div>
      <div id="dispatchResult" class="dispatch-result" role="status" aria-live="polite" hidden></div>
    </div>`;
  const parameters = byId('dispatchParameters');
  const result = byId('dispatchResult');
  const cold = byId('confirmBlankCold');
  const cache = byId('confirmFormulaCache');

  async function request(operation, body) {
    const response = await fetch('/api/intake/' + operation, body === undefined ? {} : {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)
    });
    const data = await response.json();
    if (!response.ok && !(response.status === 400 && (data.preview || Array.isArray(data.issues)))) {
      throw new Error(data.error || '批次操作未完成。');
    }
    return data;
  }

  function controls() {
    const usable = !!template && !!review?.revision && !review.dirty && !busy;
    const suggestion = byId('suggestStoreIds');
    if (suggestion) suggestion.disabled = busy || !template?.rows.some(row => !row.store_id && row.suggested_store_id);
    byId('downloadDispatchTemplate').disabled = !usable;
    byId('downloadStoreReferences').disabled = !usable;
    byId('previewDispatch').disabled = !usable || !parameters.value.trim();
    byId('createDispatch').hidden = !ready;
    byId('createDispatch').disabled = !usable || !ready;
    byId('dispatchParameterFile').disabled = busy;
    byId('stageDispatchSetup').disabled = !usable;
    parameters.disabled = busy;
    cold.disabled = busy;
    cache.disabled = busy;
    byId('dispatchScope').textContent = review ? `${review.selected} 行纳入本批` : '';
    byId('dispatchNext').textContent = review?.dirty ? '先保存上方订单核对，再核验批次。'
      : !review?.revision ? '保存订单核对后可生成批次模板。'
      : !template ? '正在读取已保存的核对资料。'
      : template.business_rules ? '中央仓 · 16辆可用车 · 共享保温箱，配送设置可展开调整。' : '导入车辆、道路和作业规则，一并核对。';
    if(byId('dispatchStageHint'))byId('dispatchStageHint').textContent=usable?'订单已保存，可补齐车辆与道路':'先整理并保存订单';
    byId('dispatchConfirmations').hidden = !template;
    byId('intakeReviewStep').removeAttribute('aria-current');
    byId('intakeDispatchStep').removeAttribute('aria-current');
    byId(usable && byId('intakeDispatchSection')?.open ? 'intakeDispatchStep' : 'intakeReviewStep').setAttribute('aria-current','step');
  }
  byId('intakeDispatchSection')?.addEventListener('toggle',controls);

  function resetPreview() {
    ready = false;
    result.hidden = true;
    result.replaceChildren();
    controls();
  }

  function showMessage(message) {
    result.hidden = false;
    result.textContent = message;
  }

  function showPreview(preview) {
    ready = preview.ready === true;
    const {scope, stores = [], issues = []} = preview;
    const omitted = scope?.omitted_rows?.length || 0;
    const counts = scope ? `${scope.selected_rows.length} 行 · ${stores.length} 个门店任务${omitted?' · '+omitted+' 行未纳入本批':''}` : '';
    result.hidden = false;
    result.innerHTML = `<strong>${ready?'资料齐全，可排车':'以下资料需要核对'}</strong><p>${escape(counts)}</p>
      ${issues.length ? `<ul>${issues.map(issue => {
        const rows = issue.rows || [];
        const rowText = rows.length ? ` · 原行 ${rows.slice(0,12).join('、')}${rows.length>12?' 等 '+rows.length+' 行':''}` : '';
        return `<li>${escape(issue.message + rowText)}</li>`;
      }).join('')}</ul>` : ''}`;
    controls();
  }

  function readParameters() {
    let value;
    try { value = JSON.parse(parameters.value); }
    catch { throw new Error('批次参数格式有误，请使用 JSON 模板。'); }
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      throw new Error('请导入一份批次参数对象。');
    }
    if (cold.checked) value.zero_cold_rows = template.rows.filter(row => row.cold_blank).map(row => row.source_row);
    if (cache.checked) value.accept_cached_totals = true;
    return value;
  }

  async function checkBatch(create) {
    if (busy || !template || review?.dirty || (create && !ready)) return;
    busy = true;
    const current = generation;
    controls();
    showMessage(create?'正在建立任务…':'正在核对批次…');
    try {
      const body = {id:review.id, revision:review.revision, parameters:readParameters()};
      if (create) body.confirmed = true;
      const response = await request(create?'dispatch':'dispatch-preview',body);
      if (current !== generation) return;
      if (response.task?.id) {
        location.assign('/workspace.html?task=' + encodeURIComponent(response.task.id));
      } else {
        showPreview(response.preview || response);
      }
    } catch (error) {
      if (current === generation) { ready=false; showMessage(error.message); }
    } finally {
      busy = false;
      controls();
      if (ready && !review.dirty) byId('createDispatch').focus();
    }
  }

  window.addEventListener('intake:review', async ({detail}) => {
    const fileChanged = review?.id !== detail.id;
    const versionChanged = fileChanged || review?.revision !== detail.revision;
    review = detail;
    host.hidden = false;
    if (!versionChanged) {
      if (detail.dirty) resetPreview(); else controls();
      return;
    }
    const current = ++generation;
    template = null;
    if (fileChanged) {
      parameters.value = '';
      byId('dispatchParameterFile').value = '';
    }
    cold.checked = false;
    cache.checked = false;
    byId('identitySuggestions').hidden = true;
    resetPreview();
    try {
      const response = await request(`dispatch-template?id=${encodeURIComponent(detail.id)}&revision=${detail.revision}`);
      if (current !== generation) return;
      template = response.template || response;
      if(template.business_rules&&review.revision&&!parameters.value.trim()){
        const preset=await request(`central-parameters?id=${encodeURIComponent(detail.id)}&revision=${detail.revision}`);
        if(current!==generation)return;
        parameters.value=JSON.stringify(preset.parameters,null,2);
        byId('dispatchNext').textContent='中央仓配送设置已载入，可直接核验并建立批次。';
      }
      const blanks = template.rows.filter(row => row.cold_blank).length;
      byId('confirmColdLabel').hidden = !blanks;
      byId('blankColdText').textContent = `已确认 ${blanks} 行冷链全空的订单无冷链货`;
      controls();
      if(template.business_rules){
        byId('dispatchConfirmations').hidden=true;
        byId('dispatchNext').textContent=review.revision?'中央仓 · 16辆可用车 · 共享保温箱。可在参数详情调整车辆、回收和作业时间。':'确认上方订单后，载入中央仓配送设置。';
        byId('previewDispatch').textContent='核验配送设置';
        if(review.revision&&parameters.value.trim())await checkBatch(false);
      }
    } catch (error) {
      if (current === generation) {
        showMessage(error.message);
        byId('dispatchNext').textContent='无法读取核对资料。可保存一次或刷新后重试。';
      }
    }
  });

  document.addEventListener('click', event => {
    if (event.target.id!=='suggestStoreIds' || !template) return;
    const suggestions = template.rows.filter(row => !row.store_id && row.suggested_store_id);
    const panel = byId('identitySuggestions');
    panel.hidden = false;
    panel.innerHTML = `<strong>${suggestions.length} 行的编号建议</strong>
      <p>对照门店名和地址核对，已有编号保留。可先采纳，再在上方逐行修订。</p>
      <div class="table-scroll"><table><thead><tr><th>原行</th><th>门店</th><th>地址</th><th>建议编号</th></tr></thead><tbody>
      ${suggestions.map(row=>`<tr><td>${row.source_row}</td><td>${escape(row.store_name)}</td><td>${escape(row.address)}</td><td>${escape(row.suggested_store_id)}</td></tr>`).join('')}
      </tbody></table></div><button type="button" id="applyStoreSuggestions">填入建议编号</button>`;
    panel.querySelector('button').addEventListener('click', () => {
      const adopted = window.intakeReview.applyStoreSuggestions(
        suggestions.map(row => ({source_row:row.source_row,store_id:row.suggested_store_id})),
        review.id,review.revision
      );
      if (adopted) panel.hidden = true;
      else panel.insertAdjacentHTML('beforeend','<p>表头对应已变化。请保存后重新查看建议。</p>');
    });
  });

  function download(value,name) {
    const file = new Blob([JSON.stringify(value,null,2)], {type:'application/json'});
    const url = URL.createObjectURL(file);
    const link = document.createElement('a');
    link.href = url;
    link.download = name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url),1000);
  }
  byId('downloadDispatchTemplate').addEventListener('click', () => download(template.parameters,'批次参数模板.json'));
  byId('downloadStoreReferences').addEventListener('click', () => download(
    template.rows.map(row => ({
      source_row:row.source_row, store_id:row.store_id || null,
      suggested_store_id:row.store_id ? null : row.suggested_store_id,
      store_name:row.store_name, address:row.address, skus:row.skus, totals:row.normalized
    })), '门店与箱规编号.json'
  ));
  function roadTable(content) {
    const rows = content.replace(/^\uFEFF/,'').split(/\r?\n/).filter(line=>line.trim());
    const cells = line => line.split(',').map(cell=>cell.trim().replace(/^"(.*)"$/,'$1'));
    const columns = rows.length ? cells(rows.shift()) : [];
    const required = ['from_id','to_id','distance_km','travel_minutes'];
    const positions = required.map(column=>columns.indexOf(column));
    if (positions.some(index=>index<0) || !rows.length) {
      throw new Error('道路表需要起点、终点、公里和行驶分钟四列，列名见表单。');
    }
    const legs = {};
    for (const [index,line] of rows.entries()) {
      const values = cells(line);
      const [from,to,km,minutes] = positions.map(position=>values[position]);
      if (!from || !to || !km || !minutes || from === to ||
          ![km,minutes].every(value=>Number.isFinite(Number(value)) && Number(value)>=0)) {
        throw new Error(`道路表第 ${index+2} 行请核对编号、里程和时间。`);
      }
      const key = `${from}>${to}`;
      if (Object.hasOwn(legs,key)) throw new Error(`道路表重复记录 ${key}。`);
      legs[key] = {distance_km:Number(km),travel_minutes:Number(minutes)};
    }
    return legs;
  }
  byId('dispatchSetupForm').addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || !template || review?.dirty) return;
    const current = generation;
    const input = new FormData(event.target);
    const text = key => String(input.get(key) || '').trim();
    const number = key => Number(input.get(key));
    const roads = input.get('roads');
    const operations = input.get('operations');
    resetPreview();
    busy = true; controls();
    try {
      if (roads.size > 2900000 || operations.size > 2900000) throw new Error('批次资料文件请控制在 2.9 MB 内。');
      const legs = roadTable(await roads.text());
      const policy = operations.size ? JSON.parse((await operations.text()).replace(/^\uFEFF/,'')) : null;
      if (current !== generation) return;
      const business = structuredClone(template.parameters.warehouse.business);
      const vehicles = Array.from({length:number('vehicleCount')}, (_,index)=>({
        id:'V'+String(index+1).padStart(2,'0'),max_weight_kg:number('weight'),max_volume_m3:number('volume'),
        shift_start:text('shiftStart'),shift_end:text('shiftEnd'),
        fixed_cost:business.tariff.base,cost_per_km:business.tariff.per_km
      }));
      const next = {
        ...template.parameters, vehicles, legs, warehouse:{
          id:text('depot'),name:text('warehouse'),business,
          settings:{depot_id:text('depot'),data_kind:text('dataKind'),
            travel_source:text('roadSource'),window_rule:text('windowRule')},
          ...(policy?{operational_policy:policy}:{})
        },
        stop_defaults:{open:text('open'),due:text('due'),close:text('close'),service_minutes:number('service'),
          allowed_vehicles:vehicles.map(vehicle=>vehicle.id),pickup:{cartons:number('cartons'),totes:number('totes')}}
      };
      parameters.value = JSON.stringify(next,null,2);
      byId('dispatchSetup').open = false;
      showMessage(`已整理 ${vehicles.length} 辆车和 ${Object.keys(legs).length} 条道路，继续核验批次。`);
    } catch (error) {
      if (current === generation) showMessage(error.message);
    } finally {
      busy = false; controls();
    }
  });
  byId('dispatchParameterFile').addEventListener('change', async event => {
    const file = event.target.files[0];
    const current = generation;
    if (!file) return;
    resetPreview();
    if (file.size > 2900000) { showMessage('批次参数请控制在 2.9 MB 内。'); return; }
    try {
      const content = await file.text();
      if (generation !== current) return;
      const value = JSON.parse(content.replace(/^\uFEFF/,''));
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error();
      parameters.value = JSON.stringify(value,null,2);
      const warehouse = value.warehouse?.name || '仓库待补';
      const vehicleCount = Array.isArray(value.vehicles) ? value.vehicles.length : 0;
      const roadCount = Object.keys(value.legs || {}).length;
      showMessage(`${warehouse} · ${vehicleCount} 辆车 · ${roadCount} 条道路 · ${value.warehouse?.operational_policy?'已提供装卸规则':'未提供装卸规则'}`);
    } catch { showMessage('无法读取这份 JSON，请检查文件格式。'); }
    controls();
  });
  parameters.addEventListener('input',resetPreview);
  cold.addEventListener('change',resetPreview);
  cache.addEventListener('change',resetPreview);
  byId('previewDispatch').addEventListener('click',()=>checkBatch(false));
  byId('createDispatch').addEventListener('click',()=>checkBatch(true));
})();
