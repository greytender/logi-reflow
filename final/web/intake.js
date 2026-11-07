'use strict';
const $=id=>document.getElementById(id);
const escapeText=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let intakeDocument=null, sheetIndex=0, rowPage=0, intakeBusy=false;
async function intakeApi(operation,body) {
  const response=await fetch('/api/intake/'+operation,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data=await response.json();if(!response.ok)throw new Error(data.error||'操作未完成，请重试');return data;
}
function intakeMessage(text,error=false) {$('intakeNotice').textContent=text;$('intakeNotice').hidden=!text;$('intakeNotice').className='notice'+(error?' error':'');}
function updateIntakeControls() {
  document.querySelectorAll('.task-sidebar button,.task-sidebar input,.task-sidebar select,#intakeSheet,#previousRows,#nextRows').forEach(e=>{e.disabled=intakeBusy;});
  const rows=intakeDocument?.sheets[sheetIndex]?.rows||[];
  $('previousRows').disabled=intakeBusy||rowPage===0;
  $('nextRows').disabled=intakeBusy||(rowPage+1)*30>=rows.length;
}
async function intakeWork(action,message) {
  if(intakeBusy)return;intakeBusy=true;updateIntakeControls();intakeMessage(message);
  try {await action();}catch(error){intakeMessage(error.message,true);}finally{intakeBusy=false;updateIntakeControls();}
}
async function refreshIntakeList() {
  const records=(await intakeApi('list')).documents;
  $('intakeList').innerHTML=records.length?records.map(d=>`<button data-intake="${escapeText(d.id)}" aria-current="${d.id===intakeDocument?.id}"><strong>${escapeText(d.name)}</strong><small>${d.record_rows==null?'待对应表头':d.record_rows+' 条订单行'}</small></button>`).join(''):'<p class="list-empty">暂无订单表</p>';
}
async function showIntake(document) {
  const reviewOpened=await window.intakeReview.open(document);
  if(!reviewOpened&&window.intakeReview.currentSourceId()!==document.id) {
    intakeMessage('当前原始资料与未保存草稿保持不变；可稍后从列表读取目标资料。');
    return false;
  }
  intakeDocument=document;sheetIndex=0;rowPage=0;
  $('intakeWorkspace').classList.toggle('central-orders',document.sha256==='40849d76aceaf3589ac68fdad258c847ed0bbf464e65b76a468258bc36726587');
  $('intakeWelcome').hidden=true;$('intakeWorkspace').hidden=false;
  $('intakeTitle').textContent=document.name;
  $('intakeMeta').textContent=`${document.filename} · ${document.audit.record_rows??'待识别'} 条订单行`;
  $('exportIntake').href='/api/intake/export?id='+encodeURIComponent(document.id);
  $('downloadOriginal').href='/api/intake/original?id='+encodeURIComponent(document.id);
  $('intakeAuditSummary').textContent=`${document.audit.issues.length} 项`;
  $('intakeIssues').innerHTML=`<p>${escapeText(document.audit.summary||'请核对表头和单位。')}</p>
    <details class="receipt-history"><summary>查看文件内容指纹</summary><p class="intake-hash">SHA256 ${escapeText(document.sha256)}</p></details>
    ${document.audit.issues.map(issue=>`<details class="intake-issue"><summary>${escapeText(issue.message)}</summary><p>工作表：${escapeText(issue.sheet||'全表')} · 原始行：${escapeText((issue.rows||[]).join(', ')||'见表格')} · 单元格：${escapeText((issue.cells||[]).join(', ')||'见原行')}</p><small>${escapeText(issue.code)}</small></details>`).join('')}`;
  $('intakeSheet').innerHTML=document.sheets.map((s,i)=>`<option value="${i}">${escapeText(s.name)} · ${s.rows.length} 个原始行${s.state!=='visible'?' · 隐藏工作表（'+escapeText(s.state)+'）':''}</option>`).join('');
  history.replaceState(null,'','#intake='+document.id);renderIntakeRows();
  return true;
}
function renderIntakeRows() {
  const sheet=intakeDocument?.sheets[sheetIndex];if(!sheet)return;
  const pageRows=sheet.rows.slice(rowPage*30,(rowPage+1)*30);
  const columns=Array.from(new Set(sheet.rows.flatMap(r=>r.cells.map(c=>c.column)))).sort((a,b)=>a-b);
  const colLabel=n=>{let s='';while(n){n--;s=String.fromCharCode(65+n%26)+s;n=Math.floor(n/26);}return s;};
  $('intakeTable').innerHTML=`<table class="intake-table"><caption>${escapeText(sheet.name)} · 共${sheet.rows.length}个原始行，所有行均可翻页查看</caption><thead><tr><th scope="col">原行</th>${columns.map(n=>`<th scope="col">${colLabel(n)}</th>`).join('')}</tr></thead><tbody>${pageRows.map(row=>`<tr><th scope="row">${row.row}${row.hidden?'（隐藏行）':''}</th>${columns.map(column=>{
    const c=row.cells.find(cell=>cell.column===column);if(!c)return '<td class="empty-cell">空白</td>';
    const display=c.merged_from?c.effective_value:c.value;
    return `<td><span class="source-ref">${escapeText(c.ref)}</span><span>${display===null||display===undefined?'<span class="empty-cell">空白</span>':escapeText(display)}</span>${c.merged_from?`<small>合并继承自 ${escapeText(c.merged_from)}；原值${c.value===null?'为空':escapeText(c.value)}</small>`:''}${c.formula?`<small>公式：${escapeText(c.formula.text)} · ${escapeText(c.cache_status)}</small>`:''}<details><summary>原始来源</summary><small>类型 ${escapeText(c.type)}；原值 ${escapeText(JSON.stringify(c.raw_value))}；缓存 ${escapeText(c.cache_status)}</small></details></td>`;
  }).join('')}</tr>`).join('')}</tbody></table>`;
  $('intakePage').textContent=sheet.rows.length?`第 ${rowPage+1} / ${Math.ceil(sheet.rows.length/30)} 页（全部 ${sheet.rows.length} 行）`:'空工作表';
  updateIntakeControls();
}
$('sampleIntake').addEventListener('click',()=>intakeWork(async()=>{
  if(!(await window.intakeReview.canLeave()))return;
  const data=await intakeApi('create',{sample:true,name:'订单核对练习'});const opened=await showIntake(data.document);await refreshIntakeList();if(opened)intakeMessage('练习表格已打开。');
},'正在打开练习表格…'));
$('uploadIntake').addEventListener('click',()=>intakeWork(async()=>{
  if(!(await window.intakeReview.canLeave()))return;
  const file=$('intakeFile').files[0];if(!file||!file.name.toLowerCase().endsWith('.xlsx'))throw new Error('请选择XLSX文件，不支持宏工作簿或旧XLS格式');
  if(file.size>2000000)throw new Error('本轮文件上限2MB，超出时不会截取内容');
  const bytes=new Uint8Array(await file.arrayBuffer());let binary='';
  for(let offset=0;offset<bytes.length;offset+=16384)binary+=String.fromCharCode(...bytes.subarray(offset,offset+16384));
  const name=$('intakeName').value.trim()||file.name;
  const data=await intakeApi('create',{name,filename:file.name,content_base64:btoa(binary)});
  const opened=await showIntake(data.document);await refreshIntakeList();if(opened){intakeMessage('订单表已导入，可用 Agent 整理字段。');$('organizeWithDeepSeek')?.focus();}
},'正在读取订单…'));
$('chooseIntake').addEventListener('click',()=>$('intakeFile').click());
$('openCentralOrders').addEventListener('click',()=>intakeWork(async()=>{
  if(!(await window.intakeReview.canLeave()))return;
  const data=await intakeApi('create',{central:true});
  await showIntake(data.document);await refreshIntakeList();
  intakeMessage('订单已按中央仓规则整理，确认后进入排车。');
},'正在整理中央仓订单…'));
$('intakeList').addEventListener('click',event=>{const button=event.target.closest('[data-intake]');if(!button)return;intakeWork(async()=>{
  if(!(await window.intakeReview.canLeave()))return;
  const opened=await showIntake((await intakeApi('document?id='+encodeURIComponent(button.dataset.intake))).document);await refreshIntakeList();if(opened)intakeMessage('已恢复保存的原始核对资料。');
},'正在读取原始核对资料…');});
$('refreshIntakes').addEventListener('click',()=>intakeWork(async()=>{await refreshIntakeList();intakeMessage('列表已刷新。');},'正在刷新…'));
$('intakeSheet').addEventListener('change',()=>{sheetIndex=Number($('intakeSheet').value);rowPage=0;renderIntakeRows();});
$('previousRows').addEventListener('click',()=>{rowPage--;renderIntakeRows();});
$('nextRows').addEventListener('click',()=>{rowPage++;renderIntakeRows();});
intakeWork(async()=>{await refreshIntakeList();const id=new URLSearchParams(location.hash.slice(1)).get('intake');if(id)await showIntake((await intakeApi('document?id='+encodeURIComponent(id))).document);intakeMessage('');},'正在读取原始核对资料…');

window.addEventListener('beforeunload',event=>{if(window.intakeReview.hasDraft()){event.preventDefault();event.returnValue='';}});
document.querySelectorAll('a[href]').forEach(link=>{if(link.id==='exportIntake')return;link.addEventListener('click',async event=>{if(!window.intakeReview.hasDraft())return;event.preventDefault();if(await window.intakeReview.canLeave())location.href=link.href;});});
