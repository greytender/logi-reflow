/* One presentation clock over the saved route and its exact cargo frames. */
(function(){
'use strict';
const phaseNames={loading:'装车',load:'装车',departure:'装车完成',arrival:'到店停车',cold_open:'冷链取货',rehandle_out:'移出挡货',rehandle_in:'放回货物',unload:'卸货',unloading:'卸货',pickup:'回收装车',recovery:'回收装车',departure_stop:'离店准备',return:'返仓交接',returned:'返仓交接',delivery:'卸货'};
function buildTimeline(route){
 if(!route||!Array.isArray(route.frames)||!route.frames.length)throw Error('这辆车没有装卸步骤，请返回工作台核对箱规。');
 const sequence=route.sequence||route.route?.sequence||[];
 if(!sequence.length)throw Error('这辆车没有配送门店。');
 if(new Set(sequence).size!==sequence.length)throw Error('配送顺序含重复门店，请重新核对方案。');
 const frames=route.frames,stages=[{kind:'start',leg_index:0,label:'仓库准备',duration:2}],used=new Set();
 const addFrame=(f,index,stop)=>{if(used.has(index))throw Error('装卸步骤重复。');used.add(index);const phase=f.phase||'loading';stages.push({kind:'cargo',frame_index:index,stop_id:stop||null,phase,label:phaseNames[phase]||f.label||'装卸',duration:phase==='cold_open'?3:phase==='load'||phase==='loading'?.85:phase==='arrival'||phase==='departure_stop'?1.4:2.2});};
 let firstStop=frames.findIndex(f=>f.stop_id!=null);if(firstStop<0)firstStop=frames.length;
 for(let i=0;i<firstStop;i++){if(['return','returned'].includes(frames[i].phase))throw Error('装卸步骤缺少门店过程。');addFrame(frames[i],i,null);}
 for(let si=0;si<sequence.length;si++){
  const stop=sequence[si];stages.push({kind:'drive',leg_index:si,stop_id:stop,from_id:si?sequence[si-1]:null,to_id:stop,label:'驶向门店',duration:10});
  stages.push({kind:'park',leg_index:si,stop_id:stop,to_id:stop,label:'到店停车',duration:1.8});
  const group=frames.map((f,i)=>({f,i})).filter(x=>x.f.stop_id===stop);if(!group.length)throw Error('有门店缺少装卸步骤，请重新计算。');
  for(const {f,i} of group)addFrame(f,i,stop);
 }
 stages.push({kind:'drive',leg_index:sequence.length,stop_id:null,from_id:sequence.at(-1),to_id:null,label:'带回包装，返回仓库',duration:10});
 stages.push({kind:'park',leg_index:sequence.length,stop_id:null,to_id:null,label:'驶入仓库',duration:1.8});
 for(let i=firstStop;i<frames.length;i++)if(!used.has(i)){
  if(!['return','returned'].includes(frames[i].phase))throw Error('装卸步骤与门店顺序不一致。');
  addFrame(frames[i],i,null);
 }
 if(used.size!==frames.length)throw Error('装卸步骤不完整。');
 const order=stages.filter(s=>s.kind==='cargo').map(s=>s.frame_index);
 if(order.some((n,i)=>i&&n<=order[i-1]))throw Error('装卸步骤与当前路线顺序不一致。');
 let time=0;for(const stage of stages){stage.start=time;time+=stage.duration;stage.end=time;}return {stages,duration:time,sequence:[...sequence]};
}
function locate(timeline,time){const t=Math.max(0,Math.min(timeline.duration,Number(time)||0));let index=timeline.stages.findIndex(s=>t<s.end);if(index<0)index=timeline.stages.length-1;const stage=timeline.stages[index];return {index,stage,progress:Math.max(0,Math.min(1,(t-stage.start)/stage.duration)),time:t};}
function withLoadingPreparation(spatial,vehicleId){
 const copy=JSON.parse(JSON.stringify(spatial)),r=copy.scene?.routes?.find(r=>r.vehicle_id===vehicleId);
 if(!r?.frames?.length)throw Error('缺少装车步骤。');
 r.frames.unshift({index:-1,label:'准备装车',phase:'loading',time_minutes:r.frames[0].time_minutes,boxes:[],temporary_boxes:[],action:null});
 return copy;
}
window.LogiJourneyFlow={buildTimeline,locate,phaseNames,withLoadingPreparation};
if(typeof document==='undefined'||!document.getElementById('journeyPlayer'))return;
const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let payload,driving,cargo,timeline,route,layout,playing=false,disposed=false,setupFailed=false,time=0,last=0,raf=0,stageIndex=-1,speed=1,view='auto',cargoFrame=null;
const name=(kind,id)=>payload?.map?.names?.[kind]?.[id]||payload?.spatial?.display_names?.[kind]?.[id]||String(id||'仓库');
const setError=message=>{$('journeyError').hidden=false;$('journeyError').innerHTML=`<p>${esc(message)}</p><a href="/workspace.html">返回工作台核对方案</a>`;$('journeyContext').textContent='路线暂时无法播放';};
function playState(value){playing=value;last=0;$('journeyPlay').setAttribute('aria-label',value?'暂停配送过程':'播放配送过程');$('journeyPlay').innerHTML=value?'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14M16 5v14"/></svg>':'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 5 11 7-11 7Z"/></svg>';document.body.dataset.playing=String(value);}
function currentCargo(index){for(let i=index;i>=0;i--)if(timeline.stages[i].kind==='cargo')return timeline.stages[i].frame_index;return -1;}
function frameLeg(stage,index){if(Number.isInteger(stage.leg_index))return {index:stage.leg_index,progress:stage.kind==='start'?0:stage.kind==='drive'?locate(timeline,time).progress:1};for(let i=index-1;i>=0;i--)if(timeline.stages[i].kind==='drive')return {index:timeline.stages[i].leg_index,progress:1};return {index:0,progress:0};}
function showAt(next,force=false){
 time=Math.max(0,Math.min(timeline.duration,Number(next)||0));const state=locate(timeline,time),s=state.stage,changed=state.index!==stageIndex;stageIndex=state.index;
 const autoSurface=s.kind==='cargo'?'cargo':'driving',surface=view==='auto'?autoSurface:view==='cargo'?'cargo':'driving';$('journeyStage').dataset.surface=surface;
 if(changed||force){
  const label=s.kind==='start'?'仓库准备':s.kind==='drive'?(s.to_id?'驶向'+name('orders',s.to_id):'返回'+name('warehouses',payload.warehouse_id)):s.kind==='park'?(s.to_id?name('orders',s.to_id)+' · 到店停车':'驶入仓库'):s.stop_id?name('orders',s.stop_id)+' · '+s.label:['return','returned'].includes(s.phase)?'返仓交接':'仓库 · '+s.label;
  $('journeyStageTitle').textContent=label;
  const leg=Array.isArray(payload.legs)?payload.legs[s.leg_index]:null;
  $('journeyStageDetail').textContent=s.kind==='drive'&&Number.isFinite(leg?.distance_km)?'本段 '+leg.distance_km+' km':s.kind==='cargo'&&route.frames[s.frame_index]?.time_minutes!=null?'计划 '+clock(route.frames[s.frame_index].time_minutes):s.kind==='park'?'停车后查看本店装卸':'按方案中的箱位与动作播放';
  $('journeySwitchNote').textContent=s.kind==='drive'?(s.to_id?'沿路线前往'+name('orders',s.to_id):'带回回收物与共享箱，返回仓库'):s.kind==='park'?(s.to_id?name('orders',s.to_id)+'已到达，即将装卸':'已到仓库，准备查看带回物'):s.stop_id?name('orders',s.stop_id)+'正在'+s.label:['return','returned'].includes(s.phase)?'已返仓，准备交接带回物':'仓库装车，准备出发';
  for(const b of $('journeyStations').querySelectorAll('button'))b.setAttribute('aria-pressed',String((s.stop_id||(['return','returned'].includes(s.phase)||s.kind==='drive'&&!s.to_id?'return':'depot'))===b.dataset.station));
 }
 const leg=frameLeg(s,state.index);driving?.setLeg(leg.index,leg.progress);driving?.setCamera(view==='chase'?'chase':view==='overview'?'overview':'driver');
 const fi=s.kind==='cargo'?s.frame_index:currentCargo(state.index);if(fi!==cargoFrame||force){cargoFrame=fi;cargo?.setFrame(fi+1);}
 $('journeyTimeline').value=time;$('journeyProgressText').textContent='播放进度 '+Math.round(time/timeline.duration*100)+'% · '+(state.index+1)+' / '+timeline.stages.length;
 document.body.dataset.stageIndex=String(state.index);document.body.dataset.stageKind=s.kind;document.body.dataset.frameIndex=String(fi);document.body.dataset.renderFrameIndex=String(fi+1);document.body.dataset.surface=surface;
 $('journeyPrevious').disabled=time<=0;$('journeyNext').disabled=time>=timeline.duration;
 if(time>=timeline.duration)playState(false);
}
function clock(minute){const m=Math.floor(minute);return String(Math.floor(m/60)).padStart(2,'0')+':'+String(m%60).padStart(2,'0');}
function loop(now){if(disposed)return;raf=requestAnimationFrame(loop);if(playing){if(last)showAt(time+Math.min(.12,(now-last)/1000)*speed);last=now;}else last=0;}
async function start(){
 const params=new URLSearchParams(location.search),id=params.get('id');if(!id){setError('从工作台选一辆车，再打开随车演示。');return;}
 try{
  const query=new URLSearchParams({id});for(const key of ['warehouse_id','vehicle_id','plan_id'])if(params.get(key))query.set(key,params.get(key));
  const response=await fetch('/api/multiwarehouse/journey?'+query,{cache:'no-store'});payload=await response.json();if(!response.ok)throw Error(payload.error||'路线读取失败');
  if(!window.LogiDriving3D||!window.LogiSpatial3D||!window.LogiRouteMap3D)throw Error('三维组件未加载，请刷新。');
  const selected=payload.spatial.scene.routes.find(r=>r.vehicle_id===payload.vehicle_id);const solved=payload.map.routes.find(r=>r.vehicle.id===payload.vehicle_id);if(!selected||!solved)throw Error('选中车辆不在当前方案中。');
  route={...selected,sequence:solved.route.sequence};timeline=buildTimeline(route);layout=LogiRouteMap3D.buildLayout(payload.map);
  $('journeyPlayer').hidden=false;$('journeyContext').textContent=[name('warehouses',payload.warehouse_id),name('vehicles',payload.vehicle_id),route.sequence.length+'家门店','路线 '+solved.route.distance+' km'].join(' · ');
  const cargoQuery=new URLSearchParams({id,warehouse_id:payload.warehouse_id,vehicle_id:payload.vehicle_id,plan_id:payload.plan_id});$('journeyCargoLink').href='/spatial.html?'+cargoQuery;$('journeyCargoLink').hidden=false;
  $('journeyStations').innerHTML=`<button data-station="depot" aria-pressed="true">仓库装车</button>${route.sequence.map(s=>`<button data-station="${esc(s)}" aria-pressed="false">${esc(name('orders',s))}</button>`).join('')}<button data-station="return" aria-pressed="false">返仓交接</button>`;
  await Promise.all([LogiDriving3D.mount($('journeyDriving'),{layout,vehicle_id:payload.vehicle_id,names:payload.map.names}).then(viewer=>{driving=viewer;if(disposed||setupFailed)viewer?.destroy();}),LogiSpatial3D.mount($('journeyCargo'),{scene:withLoadingPreparation(payload.spatial,payload.vehicle_id),vehicle_id:payload.vehicle_id}).then(viewer=>{cargo=viewer;if(disposed||setupFailed)viewer?.destroy();})]);
  if(disposed)return;cargo?.setMode('cargo');$('journeyTimeline').max=timeline.duration;showAt(0,true);
  $('journeyPlay').onclick=()=>{if(time>=timeline.duration)showAt(0);playState(!playing);};$('journeyRestart').onclick=()=>{playState(false);showAt(0,true);};
  $('journeyPrevious').onclick=()=>{playState(false);showAt(timeline.stages[Math.max(0,stageIndex-1)].start);};$('journeyNext').onclick=()=>{playState(false);showAt(stageIndex<timeline.stages.length-1?timeline.stages[stageIndex+1].start:timeline.duration);};
  $('journeyTimeline').oninput=e=>{playState(false);showAt(e.target.value);};$('journeySpeed').onchange=e=>{speed=Number(e.target.value)||1;};
  document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.journeyView){view=b.dataset.journeyView;document.querySelectorAll('[data-journey-view]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));showAt(time,true);}if(b.dataset.station){playState(false);const target=b.dataset.station;const index=target==='depot'?0:target==='return'?timeline.stages.findIndex(s=>s.kind==='cargo'&&['return','returned'].includes(s.phase)):timeline.stages.findIndex(s=>s.kind==='park'&&s.stop_id===target);showAt(index<0?timeline.duration:timeline.stages[index].start,true);}});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)playState(false);});document.addEventListener('keydown',e=>{if(e.key===' '&&!/INPUT|SELECT|TEXTAREA|BUTTON/.test(document.activeElement?.tagName||'')){e.preventDefault();playState(!playing);}});
  raf=requestAnimationFrame(loop);document.body.dataset.journeyStatus='ready';
 }catch(error){setupFailed=true;driving?.destroy();cargo?.destroy();$('journeyPlayer').hidden=true;setError(error.message);document.body.dataset.journeyStatus='error';}
}
window.addEventListener('pagehide',()=>{disposed=true;playing=false;cancelAnimationFrame(raf);driving?.destroy();cargo?.destroy();},{once:true});
window.addEventListener('pageshow',event=>{if(event.persisted&&disposed)location.reload();});
window.journeyPresentation={getState:()=>({playing,time,stageIndex,view,duration:timeline?.duration,stage:timeline?.stages[stageIndex],vehicleId:payload?.vehicle_id}),seek:value=>timeline&&showAt(value)};
start();
})();
