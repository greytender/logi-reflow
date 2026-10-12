const L=id=>document.getElementById(id),escapeLoading=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let loadingPayload=null,loadingResult=null,loadingBusy=false;
function loadingEnable(){L('sampleLoading').disabled=loadingBusy;L('solveLoading').disabled=loadingBusy||!loadingPayload;L('loadingFiles').disabled=loadingBusy;L('fixedLoadingAck').disabled=loadingBusy;L('startFixedLoading').disabled=loadingBusy||!loadingResult?.complete||!L('fixedLoadingAck').checked;}
function clearLoadingResult(){loadingResult=null;L('loadingResult').hidden=true;L('fixedLoadingAck').checked=false;L('fixedLoadingStatus').textContent='创建后原坐标冻结，收货变化不能免费重摆；此模式暂不接受紧急新货。';}
async function calculateLoading(payload){
 clearLoadingResult();loadingBusy=true;loadingEnable();L('loadingStatus').textContent='正在联合试算并独立核算，请稍候…';
 try{const response=await fetch('/api/loading/solve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const r=await response.json();if(!response.ok)throw Error(r.error||'试算失败');
  loadingResult=r;L('loadingResult').hidden=false;L('loadingStatus').textContent=(r.complete?'已生成完整的离线联合候选，尚未采用或派单。':'部分订单未安排，保留失败边界，需人工处理。')+(r.work.budget_exhausted?' 搜索预算已触顶，只保留已核算候选，不保证充分搜索。':'');renderLoading();
 }catch(e){L('loadingStatus').textContent='未生成方案：'+e.message+'。请核对输入后重试。';}finally{loadingBusy=false;loadingEnable();}
}
L('sampleLoading').onclick=async()=>{if(loadingBusy)return;loadingBusy=true;loadingEnable();try{const response=await fetch('/api/loading/sample');if(!response.ok)throw Error('无法读取构造样例');loadingPayload=await response.json();L('loadingInput').textContent=JSON.stringify(loadingPayload.loading_profile,null,2);loadingBusy=false;await calculateLoading(loadingPayload);}catch(e){L('loadingStatus').textContent=e.message;loadingBusy=false;loadingEnable();}};
L('loadingFiles').onchange=async()=>{clearLoadingResult();loadingPayload=null;loadingEnable();try{
 const files=Array.from(L('loadingFiles').files),names=['orders.csv','vehicles.csv','travel.csv','settings.json','loading.json'];
 if(files.length!==5||new Set(files.map(f=>f.name)).size!==5||files.some(f=>!names.includes(f.name)))throw Error('请选择同一批五份文件，文件名必须与输入合同一致');
 if(files.reduce((s,f)=>s+f.size,0)>3000000)throw Error('五份文件合计不得超过3MB');
 const texts=Object.fromEntries(await Promise.all(files.map(async f=>[f.name,await f.text()])));const raw=JSON.parse(texts['loading.json']);delete texts['loading.json'];
 loadingPayload={csv_files:texts,loading_profile:raw};L('loadingInput').textContent=JSON.stringify(raw,null,2);L('loadingStatus').textContent='已读取五份文件，点击联合试算。声明为企业数据不等于已经核实其真实性。';
 }catch(e){L('loadingStatus').textContent='输入未准备：'+e.message;}finally{loadingEnable();}};
L('solveLoading').onclick=()=>{if(loadingPayload&&!loadingBusy)calculateLoading(loadingPayload);};
L('fixedLoadingAck').onchange=loadingEnable;
L('startFixedLoading').onclick=async()=>{
 if(loadingBusy||!loadingResult?.complete||!L('fixedLoadingAck').checked)return;
 loadingBusy=true;loadingEnable();
 try{
  const current=await fetch('/api/dispatch');const state=await current.json();if(!current.ok)throw Error(state.error||'无法读取当前场景');
  const response=await fetch('/api/dispatch/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...loadingPayload,loading_input_sha256:loadingResult.input_sha256,id:state.scene?.id,revision:state.scene?.revision})});
  const result=await response.json();if(!response.ok)throw Error(result.error||'未创建场景');
  L('fixedLoadingStatus').textContent='已创建独立固定装载模拟，原场景保留。去“连续调度”刷新保存状态，继续收货变化和三步人工模拟回执。';
  L('fixedLoadingAck').checked=false;
 }catch(e){L('fixedLoadingStatus').textContent='未创建：'+e.message;}finally{loadingBusy=false;loadingEnable();}
};
function renderLoading(){
 const r=loadingResult,a=r.baseline.metrics,b=r.optimized.metrics,checked=Object.entries(r.baseline_loading).filter(([vid,c])=>c.boxes).map(([vid,c])=>`${vid}：${c.status==='VERIFIED'?'装载检查通过':c.status==='PROVEN_GEOMETRY_INFEASIBLE'?'尺寸检查可证明装不下':'有限搜索未找到装载'}`).join('；');
 L('loadingScope').textContent=`输入声明：${r.data_kind==='simulated'?'构造数据':'企业数据（真实性未核实）'}。同一批订单与道路，配送集合${r.same_served_orders?'相同':'不同，费用不可直接比较为等任务节省'}。`;
 const fields=[['已安排 / 未安排',`${a.served} / ${a.unserved}`,`${b.served} / ${b.unserved}`],['预计迟到分钟',a.late_minutes,b.late_minutes],['模拟运输费用（元）',a.cost,b.cost],['装载检查',checked,r.complete?'已安排车辆均通过独立核算':'仅已安排部分通过核算']];
 L('loadingComparison').innerHTML=fields.map(x=>`<tr>${x.map(v=>`<td>${escapeLoading(v)}</td>`).join('')}</tr>`).join('');
 L('loadingTradeoff').textContent='费用上升可能是可装载方案的代价，不等于算法退步；仅重量/体积方案若装不进去，其低费用不能作为可执行参照。未找到装载不等于一般情况下证明无解。';
 const used=Object.entries(r.optimized.routes).filter(([v,q])=>q.sequence.length);L('loadingVehicle').innerHTML=used.map(([v,q])=>`<option value="${escapeLoading(v)}">${escapeLoading(v)} · ${q.sequence.length}店 / ${q.loading.placements.length}箱</option>`).join('');
 L('loadingUnserved').textContent=r.optimized.unserved.map(o=>o.id+'：'+o.reason).join('；');renderLoadingVehicle();
}
function renderLoadingVehicle(){
 const r=loadingResult,vid=L('loadingVehicle').value;if(!vid){L('loadingDiagram').innerHTML='<text x="24" y="40">没有可展示的已安排车辆</text>';L('loadingBoxes').innerHTML='';L('loadInstructions').innerHTML='';L('unloadInstructions').innerHTML='';L('unloadCaption').textContent='无已安排任务';L('loadingAudit').textContent='';return;}
 const route=r.optimized.routes[vid],v=r.vehicles[vid],pack=route.loading,items=Object.fromEntries(r.items.map(x=>[x.id,x]));L('unloadStep').max=route.sequence.length;L('unloadStep').value=0;
 L('loadInstructions').innerHTML=pack.load_steps.map(id=>`<li>${escapeLoading(id)}；${items[id].fragile?'易碎，不压货':'保持指定朝向与承重'}</li>`).join('');
 L('unloadInstructions').innerHTML=pack.unload_steps.map((step,i)=>`<li>到 ${escapeLoading(step.order)}，依次取 ${step.box_ids.map(escapeLoading).join('、')}。先取本店上层、再取靠尾门箱；其他门店货保持原位。</li>`).join('');
 L('loadingBoxes').innerHTML=pack.placements.map(p=>`<tr><td>${escapeLoading(p.id)}</td><td>${p.x} / ${p.y} / ${p.z}</td><td>${p.dx} / ${p.dy} / ${p.dz}</td><td>${items[p.id].weight_kg}kg / ${pack.audit.top_loads_kg[p.id]}kg</td><td>${items[p.id].fragile?'易碎':'普通'} / ${items[p.id].max_top_load_kg}kg</td></tr>`).join('');
 L('loadingAudit').textContent='独立核算通过：'+pack.audit.checks.join('、')+'。路线窗口与班次也独立核算；有限搜索不保证最优。';drawLoading();
}
function drawLoading(){
 const r=loadingResult,vid=L('loadingVehicle').value;if(!vid)return;const v=r.vehicles[vid],route=r.optimized.routes[vid],step=Number(L('unloadStep').value),removed=new Set(route.sequence.slice(0,step)),defs=Object.fromEntries(r.items.map(x=>[x.id,x]));
 const scale=Math.min(480/(v.length_cm+.55*v.width_cm),250/(v.height_cm+.4*v.width_cm));
 const project=(x,y,z)=>[48+scale*(x+.55*y),330-scale*(z+.4*y)],points=xs=>xs.map(x=>project(...x).map(n=>n.toFixed(2)).join(',')).join(' ');
 const color=['#2f6c91','#aa6529','#39766a','#69558d','#8a454b'];
 let svg='<title id="diagramTitle">'+escapeLoading(vid)+'的计算装载坐标</title><desc id="diagramDesc">尾门位于长轴正方向，图中仅显示尚未卸完的门店箱体。</desc>';
 svg+=`<polygon points="${points([[0,0,0],[v.length_cm,0,0],[v.length_cm,v.width_cm,0],[0,v.width_cm,0]])}" fill="#e9eff2" stroke="#637b8d"/>`;
 const boxes=route.loading.placements.filter(p=>!removed.has(defs[p.id].order)).sort((a,b)=>(b.y+b.z)-(a.y+a.z)||a.x-b.x);
 for(const p of boxes){const{x,y,z,dx,dy,dz}=p,c=color[route.sequence.indexOf(defs[p.id].order)%color.length];
  const faces=[[[x,y,z],[x+dx,y,z],[x+dx,y,z+dz],[x,y,z+dz]],[[x+dx,y,z],[x+dx,y+dy,z],[x+dx,y+dy,z+dz],[x+dx,y,z+dz]],[[x,y,z+dz],[x+dx,y,z+dz],[x+dx,y+dy,z+dz],[x,y+dy,z+dz]]];
  svg+=faces.map((f,i)=>`<polygon points="${points(f)}" fill="${c}" fill-opacity="${[.9,.7,.5][i]}" stroke="#183344" stroke-width="1"><title>${escapeLoading(p.id)}，位置${x},${y},${z}cm</title></polygon>`).join('');
  const[a,b]=project(x+dx*.5,y,z+dz*.5);svg+=`<text x="${a}" y="${b}" fill="white" text-anchor="middle">${escapeLoading(defs[p.id].order)}</text>`;
 }
 const vertices=[[0,0,0],[v.length_cm,0,0],[v.length_cm,v.width_cm,0],[0,v.width_cm,0],[0,0,v.height_cm],[v.length_cm,0,v.height_cm],[v.length_cm,v.width_cm,v.height_cm],[0,v.width_cm,v.height_cm]];
 for(const[a,b]of [[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]]){const[u,w]=project(...vertices[a]),[s,t]=project(...vertices[b]);svg+=`<line x1="${u}" y1="${w}" x2="${s}" y2="${t}" stroke="#45677b" stroke-dasharray="4 4"/>`;}
 const[tx,ty]=project(v.length_cm,0,0);svg+=`<text x="${tx}" y="${ty+28}" fill="#1d2936">尾门 / +x</text>`;L('loadingDiagram').innerHTML=svg;
 L('loadingGeometry').textContent=`可用车厢 ${v.length_cm}×${v.width_cm}×${v.height_cm}cm；平行投影，不改变计算坐标。`;
 L('unloadCaption').textContent=step===route.sequence.length?'本车全部门店已在图示中卸完；只是步骤预览，没有提交执行回执。':`已预览卸完${step}店；下一店 ${route.sequence[step]}，只移除该店货物。`;
}
L('loadingVehicle').onchange=renderLoadingVehicle;L('unloadStep').oninput=drawLoading;
L('downloadLoading').onclick=()=>{if(!loadingResult)return;const u=URL.createObjectURL(new Blob([JSON.stringify(loadingResult,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=u;a.download='departure-loading-review.json';a.click();URL.revokeObjectURL(u);};
