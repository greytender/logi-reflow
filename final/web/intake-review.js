/* Saved source mapping drafts; does not create or solve delivery tasks. */
(() => {
  'use strict';
  const clone = value => JSON.parse(JSON.stringify(value));
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fields = {order_id:'订单编号',store_id:'门店编号',store_name:'门店名称',address:'门店地址',regular_quantity:'常规数量',regular_weight:'常规重量',regular_volume:'常规体积',cold_quantity:'冷链数量',cold_weight:'冷链重量',cold_volume:'冷链体积'};
  const units = {regular_weight:['常规重量单位',['kg','g']],regular_volume:['常规体积单位',['m3','L','cm3']],cold_weight:['冷链重量单位',['kg','g']],cold_volume:['冷链体积单位',['m3','L','cm3']]};
  const identityLabels = {store_id:'门店编号',store_name:'补录名称',address:'补录地址',order_id:'订单编号',batch_id:'批次编号',note:'核对备注'};
  const fieldPattern = new RegExp(Object.keys(fields).join('|'),'g');
  const fieldText = value => String(value ?? '').replace(fieldPattern, key => fields[key]);
  const state = {source:null,review:null,config:null,baseline:'',page:0,previewPage:0,filter:'all',search:'',busy:false,generation:0};
  let decision = null;
  const host = () => document.getElementById('intakeReview');
  const byId = id => document.getElementById(id);
  const rows = () => state.source?.sheets[state.config?.sheet_index]?.rows || [];
  const cellValue = cell => cell?.merged_from ? cell.effective_value : cell?.value;
  const hasContent = row => row.cells.some(c => c.formula || ![null,undefined,''].includes(cellValue(c)));
  const colLabel = value => {let result='';for(let n=value;n>0;n=Math.floor((n-1)/26))result=String.fromCharCode(65+(n-1)%26)+result;return result;};
  const hasDraft = () => !!state.config && JSON.stringify(state.config) !== state.baseline;
  function message(text,error=false) {
    const element=byId('reviewNotice');if(!element)return;
    element.hidden=!text;element.textContent=text;element.className='notice'+(error?' error':'');
  }
  async function api(query,body) {
    const response=await fetch('/api/intake/'+query,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const result=await response.json();
    if(!response.ok)throw new Error((response.status===409?'保存冲突：':'')+(result.error||'请求未完成，请重试'));
    return result.review;
  }
  async function confirmDiscard(text) {
    if(decision)return decision;
    let dialog=byId('reviewDiscardDialog');
    if(!dialog) {
      dialog=document.createElement('dialog');dialog.id='reviewDiscardDialog';dialog.dataset.intakeReviewDialog='';
      dialog.className='editor-section';dialog.style.cssText='margin:auto;max-width:min(480px,calc(100vw - 32px))';
      dialog.setAttribute('aria-labelledby','reviewDiscardTitle');dialog.setAttribute('aria-describedby','reviewDiscardText');
      dialog.innerHTML='<form method="dialog"><h2 id="reviewDiscardTitle">保留当前草稿？</h2><p id="reviewDiscardText"></p><div class="export-actions"><button value="keep" autofocus>保留草稿</button><button value="discard">丢弃草稿并继续</button></div></form>';
      document.body.append(dialog);
    }
    const previousFocus=document.activeElement;
    byId('reviewDiscardText').textContent=text;
    decision=new Promise(resolve=>{
      dialog.returnValue='keep';
      dialog.addEventListener('close',()=>{
        const discarded=dialog.returnValue==='discard';decision=null;resolve(discarded);
        setTimeout(()=>{if(previousFocus?.isConnected&&!previousFocus.disabled)previousFocus.focus();},0);
      },{once:true});
      dialog.showModal();dialog.querySelector('[value="keep"]').focus();
    });
    return decision;
  }
  async function canLeave() {
    if(state.busy){message('正在读取或保存草稿，请完成后再切换。');return false;}
    if(!hasDraft())return true;
    if(!await confirmDiscard('映射、单位或身份填写尚未保存。继续会丢弃本页这些更改；原始表格和已保存版本保持不变。'))return false;
    state.config=clone(state.review.config);state.page=0;render();return true;
  }
  function controls() {
    host()?.querySelectorAll('button,input,select,textarea').forEach(element=>{element.disabled=state.busy;});
    const count=rows().length;
    if(byId('reviewPrevious'))byId('reviewPrevious').disabled=state.busy||state.page===0;
    if(byId('reviewNext'))byId('reviewNext').disabled=state.busy||(state.page+1)*30>=count;
    if(byId('reviewSave'))byId('reviewSave').disabled=state.busy||(!hasDraft()&&state.review.revision>0);
    const link=byId('reviewExport');
    if(link) {
      const allowed=!state.busy&&!hasDraft()&&state.review.revision>0;link.setAttribute('aria-disabled',String(!allowed));
      if(allowed)link.href=`/api/intake/review-export?id=${encodeURIComponent(state.source.id)}&revision=${state.review.revision}`;
      else link.removeAttribute('href');
    }
  }
  function updateStatus() {
    if(!state.review)return;
    byId('reviewSaveState').textContent=state.busy?'正在处理…':hasDraft()?'有未保存修改':state.review.revision>0?`已保存 · 版本 ${state.review.revision}`:'待核对';
    byId('reviewSaveState').classList.toggle('dirty',hasDraft());
    byId('reviewCount').textContent=`本批选中 ${state.config.rows.length} 行，可按配送范围调整。`;
    byId('reviewAssessmentState').textContent=`${state.review.revision>0?'以下是已保存修订 '+state.review.revision+' 的核对结果。':'以下是初始核对预览，尚未保存或人工确认。'}${hasDraft()?'当前更改尚未保存，结果没有随之重算。':'保存缺项草稿不等于完成业务确认。'}`;
    const factor = unit => ({kg:'kg → kg（×1）',g:'g → kg（÷1000）',m3:'m³ → m³（×1）',L:'L → m³（÷1000）',cm3:'cm³ → m³（÷1000000）'}[unit]||'未确认，不形成换算值');
    byId('reviewUnitBasis').textContent=Object.entries(units).map(([key,[label]])=>label+'：'+factor(state.config.units[key])).join('；');
    controls();
    window.dispatchEvent(new CustomEvent('intake:review', {detail:{
      id:state.source.id, revision:state.review.revision, dirty:hasDraft(), selected:state.config.rows.length
    }}));
    renderPreview();
  }
  function columnOptions(selected) {
    const sheet=state.source.sheets[state.config.sheet_index];
    const columns=Array.from(new Set(sheet.rows.flatMap(r=>r.cells.map(c=>c.column)))).sort((a,b)=>a-b);
    return '<option value="">未映射</option>'+columns.map(column=>{
      const sample=sheet.rows.flatMap(r=>r.cells).find(c=>c.column===column&&![null,undefined,''].includes(cellValue(c)));
      return `<option value="${column}" ${selected===column?'selected':''}>${colLabel(column)} · 原值示例 ${esc(String(cellValue(sample)??'空白').slice(0,36))}</option>`;
    }).join('');
  }
  function render() {
    const element=host();if(!element||!state.review)return;
    element.hidden=false;
    const config=state.config;
    const central=!!state.review.business_rules;
    element.innerHTML=`<div class="section-intro"><h2>标准订单</h2><span id="reviewSaveState" class="save-state"></span></div>
      <div id="reviewNotice" role="status" aria-live="polite" class="notice" hidden></div>
      <div class="standard-toolbar"><div class="standard-filters" role="group" aria-label="筛选订单"><button type="button" data-preview-filter="all" aria-pressed="${state.filter==='all'}">全部订单 <span id="previewAllCount"></span></button><button type="button" data-preview-filter="issues" aria-pressed="${state.filter==='issues'}">待处理 <span id="previewIssueCount"></span></button></div><label class="standard-search"><span class="sr-only">搜索门店或地址</span><input id="previewSearch" type="search" value="${esc(state.search)}" placeholder="搜索门店或地址" aria-label="搜索门店或地址"></label></div>
      <p id="standardSummary" class="section-note"></p><div id="standardOrders" class="table-scroll"></div>
      <div class="intake-pager standard-pager"><button id="standardPrevious" type="button">上一页</button><span id="standardPage" aria-live="polite"></span><button id="standardNext" type="button">下一页</button></div>
      <form id="reviewEditor" novalidate>
        <details class="review-more" id="reviewBatch"><summary>配送批次与日期 <span id="batchDateHint"></span></summary>
        <div class="form-grid">
        <label>配送批次<input aria-label="本批次编号（可留空）" data-review-global="batch_id" value="${esc(config.batch_id)}" maxlength="120" placeholder="如 CS-1106-A"></label>
        <label>配送日期<input aria-label="配送日期（可留空）" data-review-global="delivery_date" type="date" value="${esc(config.delivery_date)}"></label>
        </div></details>
        <details class="review-more" id="reviewMapping"><summary>手动调整字段与单位</summary>
          <div class="form-grid"><label class="wide">工作表<select id="reviewSheet" aria-label="草稿对应工作表">${state.source.sheets.map((s,i)=>`<option value="${i}" ${i===config.sheet_index?'selected':''}>${esc(s.name)} · ${s.rows.length} 行</option>`).join('')}</select></label>
          ${Object.entries(units).map(([key,[label,choices]])=>`<label>${label}<select data-review-unit="${key}" aria-label="${esc(label)}"><option value="">未确认</option>${choices.map(unit=>`<option value="${unit}" ${config.units[key]===unit?'selected':''}>${unit==='m3'?'m³':unit==='cm3'?'cm³':unit}</option>`).join('')}</select></label>`).join('')}
          ${Object.entries(fields).map(([key,label])=>`<label>${label}来源列<select data-review-field="${key}" aria-label="${esc(label)}来源列">${columnOptions(config.fields[key])}</select></label>`).join('')}
          <label class="wide">核对备注<textarea aria-label="整体核对备注" data-review-global="note" maxlength="500">${esc(config.note)}</textarea></label></div>
          <p id="reviewUnitBasis" class="section-note"></p>
        </details>
        <details class="review-more" id="reviewIdentity"><summary>选择原行与门店编号</summary><p id="reviewCount" class="section-note"></p>
          <div class="review-row-tools"><button id="reviewSelectAll" type="button">选中全部订单行</button><button id="reviewSelectNone" type="button">取消全部</button></div>
          <div id="reviewIdentityRows" class="table-scroll"></div><div class="intake-pager"><button id="reviewPrevious" type="button">上一页</button><span id="reviewPage" aria-live="polite"></span><button id="reviewNext" type="button">下一页</button></div>
        </details>
        <div class="dispatch-actions review-identity-action"><button id="suggestStoreIds" type="button" disabled>核对本机门店编号</button></div>
        <div id="identitySuggestions" class="dispatch-result" hidden></div>
        <div class="review-save-actions"><button id="reviewSave" class="primary" type="submit">保存订单核对</button><a id="continueDispatch" class="button" href="#intakeDispatchSection">继续补齐配送资料</a><details class="review-save-more"><summary>更多操作</summary><div><button id="reviewReload" type="button">恢复已保存版本</button><a id="reviewExport" class="button" download>导出映射记录</a></div></details></div>
      </form>
      <details class="review-more"><summary>核对结果与单元格来源</summary><p id="reviewAssessmentState"></p><div id="reviewAssessment"></div></details>
      <details class="receipt-history"><summary>${state.review.history?.length?'保存历史 · '+state.review.history.length+' 次':'尚无保存历史'}</summary>${(state.review.history||[]).map(h=>`<p>修订 ${h.revision} · ${esc(h.saved_at)} · 来源 ${esc(h.source_sha256)}</p>`).join('')}</details>`;
    element.querySelectorAll('[data-review-field]').forEach(input=>input.addEventListener('change',()=>{
      config.fields[input.dataset.reviewField]=input.value?Number(input.value):null;renderRows();updateStatus();
    }));
    element.querySelectorAll('[data-review-unit]').forEach(input=>input.addEventListener('change',()=>{config.units[input.dataset.reviewUnit]=input.value||null;updateStatus();}));
    element.querySelectorAll('[data-review-global]').forEach(input=>input.addEventListener('input',()=>{config[input.dataset.reviewGlobal]=input.value;updateStatus();}));
    byId('reviewEditor').addEventListener('submit',event=>{event.preventDefault();save();});
    byId('reviewSheet').addEventListener('change',changeSheet);
    byId('reviewSelectAll').addEventListener('click',()=>{
      const audit=state.source.audit;
      const isRecognized=audit.profile==='official_order_headers'&&audit.sheet===state.source.sheets[config.sheet_index].name;
      config.rows=isRecognized?[...audit.source_rows]:rows().filter(hasContent).map(r=>r.row);
      renderRows();updateStatus();
    });
    byId('reviewSelectNone').addEventListener('click',()=>{config.rows=[];renderRows();updateStatus();});
    byId('reviewPrevious').addEventListener('click',()=>{state.page--;renderRows();updateStatus();});
    byId('reviewNext').addEventListener('click',()=>{state.page++;renderRows();updateStatus();});
    byId('reviewReload').addEventListener('click',async()=>{if(await canLeave())await open(state.source);});
    byId('continueDispatch').addEventListener('click',event=>{
      event.preventDefault();
      if(hasDraft()||!state.review.revision){message('先保存订单核对，再补齐配送资料。');return;}
      const section=byId('intakeDispatchSection');section.open=true;section.scrollIntoView({behavior:'smooth',block:'start'});
    });
    element.querySelectorAll('[data-preview-filter]').forEach(button=>button.addEventListener('click',()=>{
      state.filter=button.dataset.previewFilter;state.previewPage=0;renderPreview();
    }));
    byId('previewSearch').addEventListener('input',event=>{state.search=event.target.value;state.previewPage=0;renderPreview();});
    byId('standardPrevious').addEventListener('click',()=>{state.previewPage--;renderPreview();});
    byId('standardNext').addEventListener('click',()=>{state.previewPage++;renderPreview();});
    renderRows();updateStatus();
    if(central){
      byId('reviewSave').textContent='确认订单，继续排车';
      byId('reviewSave').setAttribute('form','reviewEditor');
      element.querySelector('.standard-toolbar').before(byId('reviewSave').closest('.review-save-actions'));
      byId('suggestStoreIds').closest('.review-identity-action').hidden=true;
      byId('continueDispatch').textContent='进入配送设置';
      byId('standardSummary').textContent=`${config.rows.length} 笔订单 · ${new Set(Object.values(config.identities).map(i=>i.store_id)).size} 家门店 · 中央仓直配`;
      const rules=document.createElement('details');rules.className='review-more';
      rules.innerHTML='<summary>订单整理规则</summary><p>同名门店合并配送；冷链空白为无冷链货物。冷链商品按10×10×10cm计算装箱体积，原表值可逐行查看。</p>';
      byId('standardSummary').after(rules);
      const agent=byId('intakeAgent');if(agent)agent.hidden=true;
    }
  }
  function previewProblems(row) {
    const problems=[];
    if(!row.identity.store_name)problems.push('缺门店名称');
    if(!row.identity.address)problems.push('缺地址');
    const missingUnits=Object.keys(units).filter(key=>!state.config.units[key]);
    if(missingUnits.length)problems.push('单位待确认');
    if(row.issues.some(i=>i.code==='cold_unknown'))problems.push('冷链待确认');
    for(const issue of row.issues)if(issue.code!=='cold_unknown')problems.push(issue.message);
    if(row.missing.some(k=>/regular|cold/.test(k)&&!k.endsWith('单位')))problems.push('货量有缺项');
    return [...new Set(problems)];
  }
  function renderPreview() {
    if(!byId('standardOrders')||!state.config)return;
    // Preview is derived locally from the current edits, not the stale saved assessment.
    const config=state.config,sheet=state.source.sheets[config.sheet_index];
    const mapped=(row,key)=>{
      const cell=row.cells.find(c=>c.column===config.fields[key]);
      const saved=config.identities[String(row.row)]?.[key];
      if(saved)return saved;
      if(cell?.formula&&cell.cache_status==='missing')return null;
      if(key.endsWith('weight')||key.endsWith('volume')||key.endsWith('quantity')){
        if(cell?.merged_from)return null;
      }
      return cellValue(cell);
    };
    const selected=new Set(config.rows);
    const items=sheet.rows.filter(r=>selected.has(r.row)).map(source=>{
      const identity={store_name:mapped(source,'store_name'),address:mapped(source,'address'),store_id:mapped(source,'store_id')};
      const raw=Object.fromEntries(Object.keys(fields).slice(4).map(key=>[key,mapped(source,key)]));
      const central=state.review.business_rules&&JSON.stringify(config.fields)===JSON.stringify(state.review.config.fields)&&JSON.stringify(config.units)===JSON.stringify(state.review.config.units);
      if(central){
        if(['cold_quantity','cold_weight','cold_volume'].every(key=>raw[key]==null)){
          raw.cold_quantity=0;raw.cold_weight=0;raw.cold_volume=0;
        }
        if(typeof raw.cold_quantity==='number')raw.cold_volume=Number((raw.cold_quantity*.001).toFixed(12));
      }
      const issues=[],missing=[];
      for(const [key,v] of Object.entries(raw)){
        if(v==null||v==='')missing.push(key);
        else if(typeof v!=='number'||!Number.isFinite(v)||v<0)issues.push({code:'invalid',message:'货量数值需核对'});
      }
      if(['cold_quantity','cold_weight','cold_volume'].every(k=>raw[k]==null))issues.push({code:'cold_unknown'});
      for(const field of Object.keys(fields).slice(4)){
        const cell=source.cells.find(c=>c.column===config.fields[field]);
        if(cell?.formula&&(!central||cell.cache_status==='missing'))issues.push({code:'formula',message:cell.cache_status==='missing'?'公式无缓存':'公式缓存待核对'});
        if(cell?.merged_from)issues.push({code:'merged',message:'合并货量待确认'});
      }
      const problems=previewProblems({identity,issues,missing});
      return {source,identity,raw,problems};
    });
    const issueCount=items.filter(r=>r.problems.length).length;
    const search=state.search.trim().toLocaleLowerCase();
    const filtered=items.filter(r=>(state.filter!=='issues'||r.problems.length)&&(!search||`${r.identity.store_name??''} ${r.identity.address??''}`.toLocaleLowerCase().includes(search)));
    const pages=Math.max(1,Math.ceil(filtered.length/20));state.previewPage=Math.min(Math.max(0,state.previewPage),pages-1);
    const current=filtered.slice(state.previewPage*20,(state.previewPage+1)*20);
    byId('previewAllCount').textContent=items.length;byId('previewIssueCount').textContent=issueCount;
    host().querySelectorAll('[data-preview-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.previewFilter===state.filter)));
    byId('standardSummary').textContent=state.review.business_rules
      ? `${items.length} 笔订单 · ${new Set(items.map(i=>i.identity.store_id)).size} 家门店 · 中央仓直配`
      : `${items.length} 条原始订单完整保留 · 重量、体积按整行总量展示${hasDraft()?' · 当前填写预览，尚未保存':''}`;
    const number=(item,key)=>{
      const raw=item.raw[key],unit=config.units[key];
      if(raw==null||raw==='')return '<span class="pending-value">待补</span>';
      if(typeof raw!=='number'||!Number.isFinite(raw)||raw<0)return `<span class="pending-value">${esc(raw)}</span>`;
      if(key.endsWith('quantity'))return esc(raw);
      if(!unit)return `${esc(raw)} <span class="pending-value">单位?</span>`;
      const factor={kg:1,g:.001,m3:1,L:.001,cm3:.000001}[unit];
      return esc(Number((raw*factor).toPrecision(10)));
    };
    byId('standardOrders').innerHTML=`<table class="standard-order-table"><caption class="sr-only">标准订单，重量单位 kg，体积单位 m³。待补或单位问号表示尚未确认。</caption><thead><tr><th rowspan="2">原行</th><th rowspan="2">门店与地址</th><th colspan="3" scope="colgroup">常规货</th><th colspan="3" scope="colgroup">冷链货</th><th rowspan="2">核对状态</th></tr><tr>${[1,2].map(()=>'<th>数量</th><th>总重 kg</th><th>总体积 m³</th>').join('')}</tr></thead><tbody>${current.length?current.map(item=>`<tr><td>${item.source.row}</td><td><strong>${esc(item.identity.store_name||'名称待补')}</strong><small>${esc(item.identity.address||'地址待补')}</small><details class="standard-source"><summary>查看原格</summary>${Object.entries(fields).map(([key,label])=>{const cell=item.source.cells.find(c=>c.column===config.fields[key]);return `<p>${label} · ${esc(cell?.ref||'未映射')}：${esc(cellValue(cell)??'空白')}${cell?.merged_from?' · 合并自 '+esc(cell.merged_from):''}</p>`;}).join('')}</details></td>${Object.keys(fields).slice(4).map(key=>`<td>${number(item,key)}</td>`).join('')}<td><span class="row-state ${item.problems.length?'pending':''}">${item.problems.length?'待处理':'已对应'}</span><small>${esc(item.problems.slice(0,2).join(' · '))}</small></td></tr>`).join(''):'<tr><td colspan="9" class="standard-empty">没有符合条件的订单。可切换筛选或检查原行选择。</td></tr>'}</tbody></table>`;
    byId('standardPage').textContent=`第 ${state.previewPage+1} / ${pages} 页 · ${filtered.length} 条`;
    byId('standardPrevious').disabled=state.busy||state.previewPage===0;
    byId('standardNext').disabled=state.busy||state.previewPage+1>=pages;
    byId('batchDateHint').textContent=config.delivery_date||'日期待填';
    byId('continueDispatch').hidden=hasDraft()||!state.review.revision;
  }
  function renderRows() {
    const config=state.config,pageRows=rows().slice(state.page*30,(state.page+1)*30),selected=new Set(config.rows);
    const mapped=(row,key)=>cellValue(row.cells.find(c=>c.column===config.fields[key]));
    byId('reviewIdentityRows').innerHTML=`<table><caption>身份填写 · 当前工作表第 ${state.page+1} 页，每页最多30个原行</caption><thead><tr><th>纳入</th><th>原行</th><th>名称/地址映射原值</th>${Object.values(identityLabels).map(label=>`<th>${label}</th>`).join('')}</tr></thead><tbody>${pageRows.map(row=>{
      const identity=config.identities[String(row.row)]||{};
      return `<tr><td><input type="checkbox" data-review-select="${row.row}" ${selected.has(row.row)?'checked':''} aria-label="纳入原行 ${row.row}"></td><td>${row.row}${row.hidden?'（隐藏）':''}</td><td><span>${esc(mapped(row,'store_name')??'名称待补')}</span><small class="sku">${esc(mapped(row,'address')??'地址待补')}</small></td>${Object.entries(identityLabels).map(([key,label])=>`<td><input data-review-row="${row.row}" data-identity-field="${key}" value="${esc(identity[key]||'')}" maxlength="${['address','note'].includes(key)?500:120}" aria-label="原行 ${row.row} ${label}" placeholder="${key==='store_id'?esc(mapped(row,key)||'待确认'):'可留空'}"></td>`).join('')}</tr>`;
    }).join('')}</tbody></table>`;
    byId('reviewPage').textContent=`第 ${state.page+1} / ${Math.max(1,Math.ceil(rows().length/30))} 页 · 全部 ${rows().length} 原行可查看`;
    byId('reviewIdentityRows').querySelectorAll('[data-review-select]').forEach(input=>input.addEventListener('change',()=>{
      const set=new Set(config.rows);input.checked?set.add(Number(input.dataset.reviewSelect)):set.delete(Number(input.dataset.reviewSelect));config.rows=Array.from(set).sort((a,b)=>a-b);updateStatus();
    }));
    byId('reviewIdentityRows').querySelectorAll('[data-review-row]').forEach(input=>input.addEventListener('input',()=>{
      const key=input.dataset.reviewRow,identity={...(config.identities[key]||{}),[input.dataset.identityField]:input.value};
      if(Object.values(identity).some(value=>value!==''))config.identities[key]=identity;else delete config.identities[key];updateStatus();
    }));
    renderAssessment(pageRows);
  }
  function renderAssessment(pageRows) {
    const assessment=state.review.assessment||{},visible=new Set(pageRows.map(row=>row.row));
    const sameSheet=state.config.sheet_index===state.review.config.sheet_index;
    const selected=(assessment.rows||[]).filter(row=>sameSheet&&visible.has(row.source_row));
    const issues=(assessment.issues||[]).filter(issue=>!issue.rows?.length||sameSheet&&issue.rows.some(row=>visible.has(row)));
    const display=value=>value===null||value===undefined?'待补':esc(value);
    byId('reviewAssessment').innerHTML=`<p class="section-note">${state.review.revision>0?'已保存草稿':'初始核对预览'}：选中 ${assessment.selected_rows??0} 行；${assessment.can_solve?'仍须另行确认求解输入':'不可直接求解'}。</p>
      ${(assessment.missing||[]).map(item=>`<p class="status-text warning">${esc(fieldText(item))}</p>`).join('')}
      <details class="receipt-history"><summary>本页相关问题 ${issues.length} 项 / 全草稿 ${(assessment.issues||[]).length} 项</summary>${issues.map(issue=>`<p>${esc(issue.message)}<small> · ${esc(issue.code)} · 原行 ${esc((issue.rows||[]).join(', ')||'全局')} · ${esc((issue.fields||[]).map(key=>fields[key]||key).join('、'))}</small></p>`).join('')}</details>
      ${!sameSheet?'<p class="section-note">当前更改了工作表，现有逐行核对结果属于另一工作表。保存新草稿后再显示本表结果。</p>':`<div class="table-scroll"><table><caption>与上方当前原行页对应的${state.review.revision>0?'已保存核对结果':'初始核对预览'}</caption><thead><tr><th>原行</th><th>${state.review.revision>0?'已保存订单/门店ID':'订单/门店ID待核对'}</th><th>常规 数量 / kg / m³</th><th>冷链 数量 / kg / m³</th><th>缺项与问题</th><th>来源</th></tr></thead><tbody>${selected.map(row=>{
        const n=row.normalized||{},id=row.identity||{};
        return `<tr><td>${row.source_row}</td><td>${display(id.order_id)} / ${display(id.store_id)}<small class="sku">${esc(id.batch_id||'批次待补')} · ${esc(id.delivery_date||'日期待补')}</small></td><td>${display(n.regular_quantity)} / ${display(n.regular_weight_kg)} / ${display(n.regular_volume_m3)}</td><td>${display(n.cold_quantity)} / ${display(n.cold_weight_kg)} / ${display(n.cold_volume_m3)}</td><td>${esc(fieldText([...(row.missing||[]),...(row.issues||[]).map(i=>i.message)].join('；')||row.status||'待核对'))}</td><td><details><summary>原单元格与转换来源</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere;max-width:420px">${esc(JSON.stringify(row.source_refs,null,2))}</pre></details></td></tr>`;
      }).join('')}</tbody></table></div>`}`;
  }
  async function changeSheet(event) {
    const next=Number(event.target.value),previous=state.config.sheet_index;
    if(next===previous)return;
    if(!await confirmDiscard('切换草稿工作表会清空本草稿的身份、列映射和单位选择，并选中该表全部非空原行（可能包含表头）。原始资料和已保存版本不变，仍要继续？')){event.target.value=String(previous);return;}
    state.config.sheet_index=next;state.config.rows=state.source.sheets[next].rows.filter(hasContent).map(r=>r.row);
    state.config.fields=Object.fromEntries(Object.keys(fields).map(key=>[key,null]));
    state.config.units=Object.fromEntries(Object.keys(units).map(key=>[key,null]));state.config.identities={};state.page=0;render();
    message('已切换草稿工作表。列映射、单位及身份须重新核对；尚未保存。');
  }
  async function save() {
    if(state.busy)return;
    const generation=state.generation,id=state.source.id;state.busy=true;updateStatus();message('正在保存草稿及重算缺项…');
    try {
      const review=await api('review',{id,revision:state.review.revision,config:clone(state.config)});
      if(generation!==state.generation||state.source.id!==id)return;
      state.review=review;state.config=clone(review.config);state.baseline=JSON.stringify(state.config);render();message(review.business_rules?'订单已确认，可进入配送设置。':'草稿已保存，缺项与冲突已更新；尚未创建配送任务。');
      if(review.business_rules){const section=byId('intakeDispatchSection');section.open=true;section.scrollIntoView({behavior:'smooth',block:'start'});}
    } catch(error) {if(generation===state.generation)message(error.message+' 当前填写仍保留；重新读取已保存草稿前会询问是否丢弃。',true);}
    finally {if(generation===state.generation){state.busy=false;updateStatus();}}
  }
  async function open(sourceDocument) {
    const element=host();if(!element)return false;
    if(state.busy){message('正在读取或保存草稿，请稍后再切换。');return false;}
    if(state.source?.id===sourceDocument.id&&hasDraft()){message('当前未保存草稿已保留，没有重新读取覆盖。');return true;}
    if(hasDraft()&&!await canLeave())return false;
    const generation=++state.generation;state.busy=true;state.source=sourceDocument;state.review=null;state.config=null;state.baseline='';state.page=0;
    element.hidden=false;element.innerHTML='<h2>映射、单位与身份草稿</h2><div id="reviewNotice" role="status" class="notice">正在读取此原始资料的已保存草稿…</div>';
    try {
      const review=await api('review?id='+encodeURIComponent(sourceDocument.id));
      if(generation!==state.generation||state.source.id!==sourceDocument.id)return false;
      state.review=review;state.config=clone(review.config);state.baseline=JSON.stringify(state.config);render();return true;
    } catch(error) {if(generation===state.generation)message(error.message+' 原始预览仍可使用；可重新选择资料后重试。',true);return false;}
    finally {if(generation===state.generation){state.busy=false;if(state.review)updateStatus();}}
  }
  function applyStoreSuggestions(suggestions,id,revision) {
    if(state.busy||state.source?.id!==id||state.review?.revision!==revision)return false;
    const saved=state.review.config;
    if(state.config.sheet_index!==saved.sheet_index||
      ['store_name','address','store_id'].some(key=>state.config.fields[key]!==saved.fields[key]))return false;
    const selected=new Set(state.config.rows);
    for(const {source_row,store_id} of suggestions) {
      if(!selected.has(source_row)||!store_id)continue;
      const previous=state.config.identities[source_row]||{};
      const original=saved.identities[source_row]||{};
      if(!previous.store_id&&previous.store_name===original.store_name&&previous.address===original.address) {
        state.config.identities[source_row]={...previous,store_id};
      }
    }
    render();
    message('建议编号已填入。核对后保存订单。');
    byId('reviewSave').focus();
    return true;
  }
  function snapshot() {
    return state.review?{id:state.source.id,revision:state.review.revision,config:clone(state.config),source_sha256:state.source.sha256}:null;
  }
  function acceptAgent(review,baseDigest) {
    if(state.busy||review.document_id!==state.source?.id||JSON.stringify(state.config)!==baseDigest)return false;
    state.review=review;state.config=clone(review.config);state.baseline=JSON.stringify(state.config);state.previewPage=0;
    render();message('整理结果已保存，原表保持完整。待确认项可继续补充。');return true;
  }
  window.intakeReview={open,hasDraft,canLeave,applyStoreSuggestions,snapshot,acceptAgent,currentSourceId:()=>state.source?.id||null};
})();
