/* Saved source mapping drafts; does not create or solve delivery tasks. */
(() => {
  'use strict';
  const clone = value => JSON.parse(JSON.stringify(value));
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fields = {order_id:'订单编号',store_id:'门店编号',store_name:'门店名称',address:'门店地址',regular_quantity:'常规数量',regular_weight:'常规重量',regular_volume:'常规体积',cold_quantity:'冷链数量',cold_weight:'冷链重量',cold_volume:'冷链体积'};
  const units = {regular_weight:['常规重量单位',['kg','g']],regular_volume:['常规体积单位',['m3','L','cm3']],cold_weight:['冷链重量单位',['kg','g']],cold_volume:['冷链体积单位',['m3','L','cm3']]};
  const identityLabels = {order_id:'订单编号',store_id:'门店编号',batch_id:'批次编号',note:'身份核对备注'};
  const fieldPattern = new RegExp(Object.keys(fields).join('|'),'g');
  const fieldText = value => String(value ?? '').replace(fieldPattern, key => fields[key]);
  const state = {source:null,review:null,config:null,baseline:'',page:0,busy:false,generation:0};
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
    const link=byId('reviewExport');
    if(link) {
      const allowed=!state.busy&&!hasDraft()&&state.review.revision>0;link.setAttribute('aria-disabled',String(!allowed));
      if(allowed)link.href=`/api/intake/review-export?id=${encodeURIComponent(state.source.id)}&revision=${state.review.revision}`;
      else link.removeAttribute('href');
    }
  }
  function updateStatus() {
    if(!state.review)return;
    byId('reviewSaveState').textContent=state.busy?'正在处理…':hasDraft()?'本页更改尚未保存':state.review.revision>0?`已保存草稿 · 修订 ${state.review.revision}`:'初始建议 · 尚未保存';
    byId('reviewSaveState').classList.toggle('dirty',hasDraft());
    byId('reviewCount').textContent=`当前工作表全部 ${rows().length} 个原始行 · 本次草稿选中 ${state.config.rows.length} 行；未截取前12行。`;
    byId('reviewAssessmentState').textContent=`${state.review.revision>0?'以下是已保存修订 '+state.review.revision+' 的核对结果。':'以下是初始核对预览，尚未保存或人工确认。'}${hasDraft()?'当前更改尚未保存，结果没有随之重算。':'保存缺项草稿不等于完成业务确认。'}`;
    const factor = unit => ({kg:'kg → kg（×1）',g:'g → kg（÷1000）',m3:'m³ → m³（×1）',L:'L → m³（÷1000）',cm3:'cm³ → m³（÷1000000）'}[unit]||'未确认，不形成换算值');
    byId('reviewUnitBasis').textContent=Object.entries(units).map(([key,[label]])=>label+'：'+factor(state.config.units[key])).join('；');
    controls();
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
    element.innerHTML=`<div class="section-intro"><h2>映射、单位与身份草稿</h2><span id="reviewSaveState" class="save-state"></span></div>
      <p class="boundary">本页保存核对草稿，始终不能直接求解。原始资料、已保存草稿和未保存填写分别保留；不会自动确认门店、订单或单位，不会生成仓库、车辆、道路或冷链规则。</p>
      <div id="reviewNotice" role="status" aria-live="polite" class="notice" hidden></div>
      <form id="reviewEditor" novalidate>
        <div class="form-grid"><label class="wide">草稿对应工作表<select id="reviewSheet" aria-label="草稿对应工作表">${state.source.sheets.map((s,i)=>`<option value="${i}" ${i===config.sheet_index?'selected':''}>${esc(s.name)} · ${s.rows.length} 原行${s.state!=='visible'?' · 隐藏表':''}</option>`).join('')}</select></label>
        ${Object.entries(fields).map(([key,label])=>`<label>${label}来源列<select data-review-field="${key}" aria-label="${esc(label)}来源列">${columnOptions(config.fields[key])}</select></label>`).join('')}
        ${Object.entries(units).map(([key,[label,choices]])=>`<label>${label}<select data-review-unit="${key}" aria-label="${esc(label)}"><option value="">未确认</option>${choices.map(unit=>`<option value="${unit}" ${config.units[key]===unit?'selected':''}>${unit==='m3'?'m³':unit==='cm3'?'cm³':unit}</option>`).join('')}</select></label>`).join('')}
        <label>本批次编号（可留空）<input aria-label="本批次编号（可留空）" data-review-global="batch_id" value="${esc(config.batch_id)}" maxlength="120"></label>
        <label>配送日期（可留空）<input aria-label="配送日期（可留空）" data-review-global="delivery_date" type="date" value="${esc(config.delivery_date)}"></label>
        <label class="wide">整体核对备注<textarea aria-label="整体核对备注" data-review-global="note" maxlength="500">${esc(config.note)}</textarea></label></div>
        <p id="reviewUnitBasis" class="section-note"></p>
        <p class="section-note">默认列映射仅为建议，尚待人工核对。列下拉展示原值示例，不能据此推定表头或单位。手填身份优先于明确映射列；订单/门店ID都未填写且未映射时仍为缺项，不把序号或名称自动当作稳定ID。</p>
        <h3>选择原行并补充身份</h3><p id="reviewCount" class="section-note"></p>
        <p class="section-note">可翻页查看全部原行（含可能的标题、隐藏或空白行）。勾选才纳入本次草稿，取消勾选仍保留本行身份填写；选择全部非空行时也会包含可能的表头，请自行核对。</p>
        <div class="intake-pager"><button id="reviewSelectAll" type="button">选中本表全部非空原行</button><button id="reviewSelectNone" type="button">全部不选</button></div>
        <div id="reviewIdentityRows" class="table-scroll"></div><div class="intake-pager"><button id="reviewPrevious" type="button">上一页身份行</button><span id="reviewPage" aria-live="polite"></span><button id="reviewNext" type="button">下一页身份行</button></div>
        <div class="intake-pager"><button id="reviewSave" class="primary" type="submit">保存映射与身份草稿</button><button id="reviewReload" type="button">重新读取已保存草稿</button><a id="reviewExport" class="button" download>导出已保存核对JSON</a></div>
      </form>
      <section class="comparison"><h3>核对结果与缺项</h3><p id="reviewAssessmentState"></p><div id="reviewAssessment"></div></section>
      <details class="receipt-history"><summary>${state.review.history?.length?'保存历史 · '+state.review.history.length+' 次':'尚无保存历史'}</summary>${(state.review.history||[]).map(h=>`<p>修订 ${h.revision} · ${esc(h.saved_at)} · 来源 ${esc(h.source_sha256)}</p>`).join('')}</details>`;
    element.querySelectorAll('[data-review-field]').forEach(input=>input.addEventListener('change',()=>{
      config.fields[input.dataset.reviewField]=input.value?Number(input.value):null;renderRows();updateStatus();
    }));
    element.querySelectorAll('[data-review-unit]').forEach(input=>input.addEventListener('change',()=>{config.units[input.dataset.reviewUnit]=input.value||null;updateStatus();}));
    element.querySelectorAll('[data-review-global]').forEach(input=>input.addEventListener('input',()=>{config[input.dataset.reviewGlobal]=input.value;updateStatus();}));
    byId('reviewEditor').addEventListener('submit',event=>{event.preventDefault();save();});
    byId('reviewSheet').addEventListener('change',changeSheet);
    byId('reviewSelectAll').addEventListener('click',()=>{config.rows=rows().filter(hasContent).map(r=>r.row);renderRows();updateStatus();});
    byId('reviewSelectNone').addEventListener('click',()=>{config.rows=[];renderRows();updateStatus();});
    byId('reviewPrevious').addEventListener('click',()=>{state.page--;renderRows();updateStatus();});
    byId('reviewNext').addEventListener('click',()=>{state.page++;renderRows();updateStatus();});
    byId('reviewReload').addEventListener('click',async()=>{if(await canLeave())await open(state.source);});
    renderRows();updateStatus();
  }
  function renderRows() {
    const config=state.config,pageRows=rows().slice(state.page*30,(state.page+1)*30),selected=new Set(config.rows);
    const mapped=(row,key)=>cellValue(row.cells.find(c=>c.column===config.fields[key]));
    byId('reviewIdentityRows').innerHTML=`<table><caption>身份填写 · 当前工作表第 ${state.page+1} 页，每页最多30个原行</caption><thead><tr><th>纳入</th><th>原行</th><th>名称/地址映射原值</th>${Object.values(identityLabels).map(label=>`<th>${label}</th>`).join('')}</tr></thead><tbody>${pageRows.map(row=>{
      const identity=config.identities[String(row.row)]||{};
      return `<tr><td><input type="checkbox" data-review-select="${row.row}" ${selected.has(row.row)?'checked':''} aria-label="纳入原行 ${row.row}"></td><td>${row.row}${row.hidden?'（隐藏）':''}</td><td><span>${esc(mapped(row,'store_name')??'未映射/空白')}</span><small class="sku">${esc(mapped(row,'address')??'未映射/空白')}</small></td>${Object.entries(identityLabels).map(([key,label])=>`<td><input data-review-row="${row.row}" data-identity-field="${key}" value="${esc(identity[key]||'')}" maxlength="${key==='note'?500:120}" aria-label="原行 ${row.row} ${label}" placeholder="${key==='order_id'||key==='store_id'?esc(mapped(row,key)==null?'未填写；ID仍待确认':'明确映射值：'+mapped(row,key)):'可留空'}"></td>`).join('')}</tr>`;
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
      state.review=review;state.config=clone(review.config);state.baseline=JSON.stringify(state.config);render();message('草稿已保存，缺项与冲突已更新；尚未创建配送任务。');
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
  window.intakeReview={open,hasDraft,canLeave,currentSourceId:()=>state.source?.id||null};
})();
