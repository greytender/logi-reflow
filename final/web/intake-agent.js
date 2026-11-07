/* Model suggestions are source-bound proposals. Only the explicit apply action saves a revision. */
(() => {
  'use strict';
  const host=document.getElementById('intakeAgent');
  const byId=id=>document.getElementById(id);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const unitLabels={regular_weight:'常规重量',regular_volume:'常规体积',cold_weight:'冷链重量',cold_volume:'冷链体积'};
  const unitOptions={regular_weight:['kg','g'],regular_volume:['m3','L','cm3'],cold_weight:['kg','g'],cold_volume:['m3','L','cm3']};
  let source=null,job=null,revision=null,busy=false,generation=0,timer=null,keyState=null,baseDigest=null,currentDigest=null;
  host.innerHTML=`<div class="agent-intro"><div class="agent-title"><svg width="30" height="30" viewBox="0 0 30 30" fill="none" aria-hidden="true"><rect x="4" y="6" width="22" height="20" rx="7" stroke="currentColor" stroke-width="1.6"/><path d="M15 2v4M10 13v3m10-3v3m-9 5h8M1 15h3m22 0h3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg><div><h2 id="intakeAgentTitle">让 Agent 整理这张表</h2><p>识别表头、统一字段和单位，把需要你确认的内容列出来。</p></div></div><span id="agentProviderState" class="agent-provider-state"></span></div>
    <label for="agentInstructions" class="sr-only">补充整理要求</label><textarea id="agentInstructions" maxlength="1000" rows="2" placeholder="可以补充：重量是 kg，体积是 m³；常规货和冷链分开整理。"></textarea>
    <div class="agent-actions"><button id="organizeWithDeepSeek" type="button" class="primary">DeepSeek 整理</button><button id="organizeLocally" type="button">本机整理</button><span class="agent-data-note">仅发送表头与数据类型，原始订单明细留在本机。补充要求会随请求发送。</span></div>
    <details id="intakeAgentSettings" class="agent-settings"><summary>DeepSeek 连接设置 <span id="intakeKeyState"></span></summary><form id="intakeKeyForm" class="agent-key-form"><label>API 密钥<input id="intakeApiKey" type="password" autocomplete="off" spellcheck="false" placeholder="sk-…" aria-describedby="intakeKeyHint"></label><button type="submit" id="intakeSetKey">连接 DeepSeek</button><button id="intakeClearKey" type="button">清除会话密钥</button></form><p id="intakeKeyHint">优先使用本机已配置的密钥。新填写的密钥仅在当前浏览器会话的服务端内存保留一小时。</p></details>
    <div id="agentNotice" class="notice" role="status" aria-live="polite" hidden></div>
    <div id="agentJob" class="agent-job" hidden></div>`;
  async function api(path,body) {
    const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await response.json();if(!response.ok)throw new Error(data.error||'操作未完成，请重试。');return data;
  }
  function notice(text,error=false) {
    const e=byId('agentNotice');e.textContent=text;e.hidden=!text;e.className='notice'+(error?' error':'');
  }
  function keyControls() {
    const configured=keyState?.session_configured||keyState?.local_configured;
    byId('intakeKeyState').textContent=configured?'已配置':'待连接';
    byId('agentProviderState').textContent=configured?'DeepSeek 可用 · 调用时验证':'可用本机整理';
    byId('intakeClearKey').disabled=busy||!keyState?.session_configured;
  }
  async function loadKey() {
    try {keyState=await api('/api/ai/key/status');keyControls();}catch {byId('intakeKeyState').textContent='连接状态读取失败';}
  }
  function controls() {
    const running=busy||job?.status==='running';
    byId('organizeWithDeepSeek').disabled=running;byId('organizeLocally').disabled=running;
    byId('intakeSetKey').disabled=running;byId('agentInstructions').disabled=running;
    byId('organizeWithDeepSeek').textContent=job?.status==='running'?'正在整理…':'DeepSeek 整理';
    keyControls();
    const apply=byId('applyIntakeAgent');
    if(apply)apply.disabled=busy||job?.status!=='succeeded'||revision!==job.revision||baseDigest!==currentDigest||!!job.applied_revision;
    const title=byId('agentResultTitle');
    if(title&&job?.status==='succeeded'&&!job.applied_revision)title.textContent=revision!==job.revision||baseDigest!==currentDigest?'草稿已变化，请重新整理':'整理完成，等你核对';
  }
  function display(change,v) {
    if(v==null)return '未对应';
    if(change.kind==='scope')return v.length+' 行';
    if(change.kind==='units')return {m3:'m³',cm3:'cm³'}[v]||v;
    let s='',n=v;while(n){n--;s=String.fromCharCode(65+n%26)+s;n=Math.floor(n/26);}return '原列 '+s;
  }
  function renderJob() {
    const element=byId('agentJob');element.hidden=!job;if(!job){controls();return;}
    const stale=revision!==job.revision||baseDigest!==currentDigest;
    const title=job.applied_revision?`已应用并保存 · 版本 ${job.applied_revision}`:job.status==='running'?'正在整理订单':job.status==='failed'?'本次整理未完成':stale?'草稿已变化，请重新整理':'整理完成，等你核对';
    const p=job.proposal;
    if(job.applied_revision){
      element.innerHTML=`<div class="agent-result-heading"><h3>${esc(title)}</h3><span>${esc(job.provider)}</span></div><p class="agent-result-summary">已按你的选择保存。下方标准订单显示当前字段和待处理项。</p><details class="agent-trace"><summary>查看本次整理过程</summary><ol>${job.events.map(e=>`<li>${esc(e)}</li>`).join('')}</ol></details>`;
      controls();return;
    }
    element.innerHTML=`<div class="agent-result-heading"><h3 id="agentResultTitle">${esc(title)}</h3><span>${esc(job.provider)}${job.model?' · '+esc(job.model):''}</span></div>
      <details class="agent-trace" ${job.status==='running'?'open':''}><summary>处理过程 · ${job.events.length} 步</summary><ol>${job.events.map(e=>`<li>${esc(e)}</li>`).join('')}</ol></details>
      ${job.status==='failed'?`<p class="agent-error">${esc(job.error)}</p>`:''}
      ${p?`<p class="agent-result-summary">${esc(p.summary)}</p>
      <form id="agentProposalForm"><div class="agent-change-list">${p.changes.length?p.changes.map(c=>`<label class="agent-change"><input type="checkbox" data-agent-change="${esc(c.id)}" checked ${job.applied_revision||stale?'disabled':''}><span><strong>${esc(c.label)}</strong><span class="change-diff">${esc(display(c,c.before))}<span aria-label="改为"> → </span>${esc(display(c,c.after))}</span><small>${esc(c.reason)} · ${c.evidence.map(r=>r==='user'?'补充要求':esc(r)).join('、')}</small></span></label>`).join(''):'<p>现有字段已对应，无需更改来源列。</p>'}</div>
      ${!job.applied_revision&&!stale?`<details class="agent-pending-units" ${Object.values(window.intakeReview.snapshot()?.config.units||{}).some(v=>!v)?'open':''}><summary>确认这张表的计量单位</summary><p>表头没有写明的单位需由你选择；重量、体积均指整行总量。</p><div class="agent-unit-grid">${Object.entries(unitLabels).map(([key,label])=>`<label>${label}<select data-agent-manual-unit="${key}"><option value="">保留当前／待确认</option>${unitOptions[key].map(u=>`<option value="${u}">${u==='m3'?'m³':u==='cm3'?'cm³':u}</option>`).join('')}</select></label>`).join('')}</div></details>
      ${p.store_suggestions.length?`<label class="agent-store-choice"><input type="checkbox" id="agentUseStoreIds" checked><span>为缺少编号的门店生成本机编号<small>${p.store_suggestions.length} 条原行；仅名称和地址完全相同的行共用编号，原始订单逐行保留。此编号用于本机调度。</small></span></label>`:''}`:''}
      <details class="agent-questions" ${p.questions.length&&p.questions.length<=3?'open':''}><summary>待确认事项 · ${p.questions.length} 项</summary>${p.questions.length?`<ul>${p.questions.map(q=>`<li>${esc(q)}</li>`).join('')}</ul>`:'<p>字段整理没有额外问题，配送资料仍需单独补齐。</p>'}</details>
      ${!job.applied_revision?`<div class="agent-apply-actions"><button id="applyIntakeAgent" class="primary" type="submit">应用所选建议并保存</button><span>${stale?'当前填写已变化，旧建议不会覆盖修改。':'仅保存核对草稿，未确认项可以稍后补充。'}</span></div>`:''}</form>`:''}`;
    byId('agentProposalForm')?.addEventListener('submit',apply);controls();
  }
  function canonical(v) {
    if(Array.isArray(v))return v.map(canonical);
    if(v&&typeof v==='object')return Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])]));
    return v;
  }
  async function digest(config) {
    const bytes=new TextEncoder().encode(JSON.stringify(canonical(config)));
    return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
  }
  async function poll(token) {
    if(token!==generation||!job)return;
    try {
      const data=await api(`/api/intake/agent-job?id=${encodeURIComponent(source)}&job_id=${encodeURIComponent(job.id)}`);
      if(token!==generation)return;job=data.job;baseDigest=job.base_digest;renderJob();
      if(job.status==='running')timer=setTimeout(()=>poll(token),1000);
    }catch(error){if(token===generation){notice(error.message+' 可刷新页面恢复查看，不会自动重复请求模型。',true);}}
  }
  async function start(mode) {
    if(busy||job?.status==='running')return;
    if(mode==='model'&&!keyState?.session_configured&&!keyState?.local_configured){byId('intakeAgentSettings').open=true;byId('intakeApiKey').focus();notice('先填写 API 密钥，或使用本机整理。');return;}
    const snapshot=window.intakeReview.snapshot();if(!snapshot)return;
    const token=generation;busy=true;controls();notice('');
    try {
      const data=await api('/api/intake/agent-start',{...snapshot,mode,instructions:byId('agentInstructions').value,request_id:crypto.randomUUID()});
      if(token!==generation)return;job=data.job;baseDigest=job.base_digest;currentDigest=await digest(window.intakeReview.snapshot().config);renderJob();
      if(job.status==='running')timer=setTimeout(()=>poll(token),600);
    }catch(error){if(token===generation)notice(error.message,true);}
    finally {busy=false;controls();}
  }
  async function apply(event) {
    event.preventDefault();if(busy||revision!==job.revision||baseDigest!==currentDigest)return;
    const snapshot=window.intakeReview.snapshot(),baseJSON=JSON.stringify(snapshot.config),token=generation;
    const overrides=Object.fromEntries([...host.querySelectorAll('[data-agent-manual-unit]')].filter(e=>e.value).map(e=>[e.dataset.agentManualUnit,e.value]));
    busy=true;controls();notice('正在保存所选建议…');
    try {
      const data=await api('/api/intake/agent-apply',{id:source,job_id:job.id,config:snapshot.config,reviewed:true,
        change_ids:[...host.querySelectorAll('[data-agent-change]:checked')].map(e=>e.dataset.agentChange),
        unit_overrides:overrides,include_store_ids:!!byId('agentUseStoreIds')?.checked});
      if(token!==generation)return;
      if(!window.intakeReview.acceptAgent(data.review,baseJSON)){notice('整理结果已在服务端保存；当前填写有变化，已保留在页面。可导出后重新读取。',true);return;}
      job.applied_revision=data.review.revision;renderJob();notice('已保存整理结果。可以继续核对标准订单，或补齐配送资料。');
    }catch(error){if(token===generation)notice(error.message,true);}
    finally {busy=false;controls();}
  }
  window.addEventListener('intake:review',async({detail})=>{
    const snapshot=window.intakeReview.snapshot();if(!snapshot)return;
    revision=detail.revision;
    if(source===detail.id){currentDigest=await digest(snapshot.config);controls();return;}
    source=detail.id;generation++;clearTimeout(timer);job=null;baseDigest=null;currentDigest=null;const token=generation;
    host.hidden=false;renderJob();notice('');
    try {
      const [data,hash]=await Promise.all([api('/api/intake/agent-job?id='+encodeURIComponent(source)),digest(snapshot.config)]);
      if(token!==generation)return;job=data.job;currentDigest=hash;baseDigest=job?.base_digest;renderJob();
      if(job?.status==='running')timer=setTimeout(()=>poll(token),600);
    }catch(error){if(token===generation)notice(error.message,true);}
  });
  byId('organizeWithDeepSeek').addEventListener('click',()=>start('model'));
  byId('organizeLocally').addEventListener('click',()=>start('local'));
  byId('intakeKeyForm').addEventListener('submit',async event=>{
    event.preventDefault();if(busy)return;busy=true;controls();
    try{keyState=await api('/api/ai/key/set',{api_key:byId('intakeApiKey').value.trim()});byId('intakeApiKey').value='';notice('密钥已配置，点击 DeepSeek 整理时验证连接。');}
    catch(error){notice(error.message,true);}finally{busy=false;controls();}
  });
  byId('intakeClearKey').addEventListener('click',async()=>{
    try{keyState=await api('/api/ai/key/clear',{});byId('intakeApiKey').value='';notice('会话密钥已清除。'+(keyState.local_configured?'仍可使用本机配置的密钥。':''));keyControls();}catch(error){notice(error.message,true);}
  });
  loadKey();
})();
