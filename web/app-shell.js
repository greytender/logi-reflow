// One application. Existing solver/session handlers remain authoritative.
const navIcons=[
 '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M3 10h18M10 10v11"/>',
 '<path d="m9 3-6 9 6 9M15 3l6 9-6 9M14 6l-4 12"/>',
 '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
 '<path d="M8 6h13M8 12h13M8 18h13M3 6h1M3 12h1M3 18h1"/>'
];
document.querySelectorAll('.nav-icon').forEach((el,i)=>el.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true">'+navIcons[i]+'</svg>');
const labels={review:'能力演示',workbench:'交互模拟',tools:'专业工作台',technical:'方法与验证'};
const originalProofDraw=proofDraw;
proofDraw=function(r){originalProofDraw(r);
 const m=r.checked.metrics;
 S('proofMetrics').innerHTML=[['安排订单',m.served+' 单'],['未安排',m.unserved+' 单'],['预测迟到',m.late_minutes+' 分钟'],['模拟运输费','¥ '+m.cost]].map(([label,value])=>'<div><span>'+esc(label)+'</span><strong>'+esc(value)+'</strong></div>').join('');
 const checkLabels={PROVEN_GEOMETRY_INFEASIBLE:'尺寸核验：箱体无法装入该车厢',VERIFIED:'装载布局：独立核验通过',SEARCH_NOT_FOUND:'当前搜索未找到可行布局',NOT_CHECKED:'尚未检查装载'};
 S('proofChecks').innerHTML=[...new Set([...r.checks,...(r.unserved||[]).map(o=>'未安排 '+o.id+'：'+o.reason)])].map(x=>{const t=x.replace(/PROVEN_GEOMETRY_INFEASIBLE|SEARCH_NOT_FOUND|NOT_CHECKED|VERIFIED/g,c=>checkLabels[c]);return '<li>'+esc(t)+'</li>';}).join('');
};
let appTool='dispatch';
let demoStage='plan';
function showDemoStage(name){
 demoStage=name;
 for(const [id,stage] of [['decision','plan'],['guide','plan'],['execution','receipts'],['exception','exception']])S(id).hidden=stage!==name;
 for(const b of document.querySelectorAll('[data-demo-stage]'))b.setAttribute('aria-pressed',String(b.dataset.demoStage===name));
 S('demoHint').textContent={plan:'坐标与装卸步骤来自同一次联合计算；接下来查看完整配送如何执行。',receipts:'到达、开始卸货、完成签收分别记录；接下来查看收货变化时的候选。',exception:'这里保留构造异常的代价；你也可以在独立场景里亲自核对和采用。'}[name];
 S('demoNext').textContent={plan:'查看配送回执 →',receipts:'查看异常与交接 →',exception:'进入交互模拟 →'}[name];
}
for(const b of document.querySelectorAll('[data-demo-stage]'))b.onclick=()=>showDemoStage(b.dataset.demoStage);
S('demoNext').onclick=()=>{if(demoStage==='exception'){showWorkMode(true);history.replaceState(null,'','#interactive');}else showDemoStage(demoStage==='plan'?'receipts':'exception');};
const originalRenderScene=renderScene;
renderScene=function(){originalRenderScene();S('demoInput').open=false;showDemoStage('plan');};
showDemoStage('plan');
function openAppTool(name){
 appTool=name==='cargo'?'cargo':'dispatch';
 for(const b of document.querySelectorAll('[data-tool]'))b.setAttribute('aria-pressed',String(b.dataset.tool===appTool));
 const target=appTool==='cargo'?'cargo.html':'workspace.html';
 if(S('toolFrame').dataset.source!==target){S('toolFrame').src=target;S('toolFrame').dataset.source=target;}
 S('toolFrame').hidden=false;
 S('toolStatus').textContent=appTool==='cargo'?'导入五份输入，联合核验路线与装载；核对后可创建保存场景。':'连续调度使用本机保存场景；候选与采用分开，历史只读。';
}
const originalShowView=showReviewView;
showReviewView=function(name){
 for(const v of ['review','workbench','technical','tools'])S(v+'View').hidden=v!==name;
 for(const b of document.querySelectorAll('[data-view]'))b.setAttribute('aria-pressed',String(b.dataset.view===name));
 S('pageLabel').textContent=labels[name]||labels.review;
 if(name==='tools')openAppTool(appTool);
};
for(const b of document.querySelectorAll('[data-tool]'))b.onclick=()=>{openAppTool(b.dataset.tool);history.replaceState(null,'','#tools/'+appTool);};
routeReviewHash=function(){
 const h=location.hash.slice(1);
 if(h.startsWith('tools')){appTool=h.split('/')[1]||'dispatch';showReviewView('tools');}
 else if(['interactive','workbench','keySettings'].includes(h)){showReviewView('workbench');showWorkMode(true);}
 else if(['input','decision','guide','execution','exception','demo'].includes(h)){showReviewView('workbench');showWorkMode(false);}
 else showReviewView(h==='technical'?'technical':'review');
};
// Replace the old listener, so browser Back follows the unified application.
window.removeEventListener('hashchange',window.__originalRouteReviewHash);
window.addEventListener('hashchange',routeReviewHash);
const hints={overview:'先查看当前路线与箱体位置，再处理一条收货变化。',event:'填入示例或自己的消息 → 核对字段 → 生成候选。计算不会自动采用。',candidate:'逐项核对路线、漏单、费用与原摆放；完整候选才允许采用。',execute:'选择车辆，按任务时间推进时钟，再分别记录到达、卸货与签收。'};
function showAppStep(name){
 for(const p of document.querySelectorAll('[data-step-panel]'))p.hidden=p.dataset.stepPanel!==name;
 for(const b of document.querySelectorAll('[data-step]'))b.setAttribute('aria-pressed',String(b.dataset.step===name));
 S('stepHint').textContent=hints[name];
}
for(const b of document.querySelectorAll('[data-step]'))b.onclick=()=>showAppStep(b.dataset.step);
const renderLiveOriginal=liveRender;
liveRender=function(){renderLiveOriginal();
 const p=live.scene.live_plan;
 S('liveStatus').textContent='当前执行采用P'+live.scene.active+'；所有到达、卸货与签收均为人工模拟。';
 S('liveAIStatus').textContent=liveContext.length?S('liveAIStatus').textContent:'可手工填字段；主动点击AI理解才会调用模型。';
 renderNextTask();
};
function nextAppTask(){
 if(!live)return null;
 const s=live.scene,vid=S('liveVehicle').value,q=s.states[vid];if(!q)return null;
 const p=q.pending,orders=s.live_plan.data.orders;
 if(p){const o=orders.find(o=>o.id===p.id),kind={awaiting_arrival:'arrived',arrived:'unloading',unloading:'signed'}[p.status];
  if(!kind||!o)return null;
  const time=Math.ceil(Math.max(s.clock,kind==='arrived'?p.earliest_arrival:kind==='unloading'?o.open:p.unloading_at+(o.service_minutes??5)));
  return {vid,order:p.id,kind,time,label:{arrived:'确认模拟到达',unloading:'开始模拟卸货',signed:'完成模拟签收'}[kind]};
 }
 if(q.loading)return {vid,time:Math.ceil(Math.max(s.clock,q.loading.finish)),label:'推进至本批仓库装货完成'};
 const stop=s.live_plan.routes[vid]?.stops?.find(x=>!q.completed.some(c=>c.id===x.id));
 if(stop&&Number.isFinite(stop.arrival))return {vid,time:Math.ceil(Math.max(s.clock,stop.arrival)),label:'推进至下一任务',order:stop.id};
 return null;
}
function renderNextTask(){
 const t=nextAppTask();
 S('nextTaskTitle').textContent=t?`${tm(t.time)} · ${t.vid}${t.order?' / '+t.order:''}`:'当前车辆没有待执行任务';
 S('nextTaskHint').textContent=t?'每次点击只记录当前一步；时钟按预测或最早允许时间推进。人工模拟，不能替代现场回执。':'可切换到有剩余任务的车辆；回仓及详细时钟操作可在下方展开。';
 S('appNextReceipt').textContent=t?t.label:'无待执行任务';
 S('appNextReceipt').disabled=!t||liveBusy||liveStale||!S('liveReceiptReviewed').checked;
}
const controlsLiveOriginal=liveControls;
liveControls=function(){controlsLiveOriginal();if(S('nextTask'))renderNextTask();};
const drawLiveOriginal=liveDraw;
liveDraw=function(){drawLiveOriginal();renderNextTask();};
S('appNextReceipt').onclick=async()=>{
 const t=nextAppTask();if(!t||!S('liveReceiptReviewed').checked||liveBusy||liveStale)return;
 if(t.time>live.scene.clock||!live.scene.states[t.vid].pending){const oldRevision=live.scene.revision;await liveAction('advance',{time:t.time});if(!live||liveStale||live.scene.revision===oldRevision)return;}
 if(t.kind){const p=live.scene.states[t.vid].pending;
  if(!p||p.id!==t.order){S('liveStatus').textContent='任务已变化，请核对当前计划后重试。';return;}
  await liveAction('receipt',{vehicle:t.vid,order:t.order,receipt:t.kind,time:t.time,source:'manual_simulation'});
 }
};
const validateLiveOriginal=liveValidate;
liveValidate=async function(calculate){await validateLiveOriginal(calculate);if(calculate&&live?.scene.candidate)showAppStep('candidate');};
const beginLiveOriginal=liveBegin;
liveBegin=async function(sample){await beginLiveOriginal(sample);if(live)showAppStep('overview');};
const actionLiveOriginal=liveAction;
liveAction=async function(action,extra={}){await actionLiveOriginal(action,extra);if(action==='adopt'&&live&&!live.scene.candidate)showAppStep('execute');};
S('startGuided').onclick=async()=>{showReviewView('workbench');showWorkMode(false);history.pushState(null,'','#demo');window.scrollTo({top:0,behavior:'auto'});if(!scenario&&!scenarioBusy)await S('runScenario').onclick();};
S('openKeySettings').onclick=()=>{S('keySettings').open=true;S('settingsDialog').showModal();};
S('closeSettings').onclick=()=>S('settingsDialog').close();
S('settingsDialog').addEventListener('click',e=>{if(e.target===S('settingsDialog')){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close();}});
// Module links stay inside this shell. They never open a second product.
window.addEventListener('message',e=>{if(e.origin!==location.origin||e.source!==S('toolFrame').contentWindow)return;if(e.data?.type==='module-height'&&Number.isFinite(e.data.height))S('toolFrame').style.height=Math.min(18000,Math.max(500,e.data.height))+'px';});
routeReviewHash();
if(S('reviewView').hidden===false)proofRun('geometry');
