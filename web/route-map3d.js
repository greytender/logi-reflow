/* Local city scene. Route order comes from the candidate; street geometry is illustrative. */
(function(){
'use strict';
const base=document.currentScript?.src||new URL('/route-map3d.js',location.href).href;
let runtime;
const libs=()=>runtime||=Promise.all([import(new URL('./vendor/three.module.min.js',base)),import(new URL('./vendor/OrbitControls.js',base))]).catch(error=>{runtime=null;throw error;});
const escape=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ico={reset:'<path d="M3 10a9 9 0 1 1 2 8M3 4v6h6"/>',plus:'<path d="M12 4v16M4 12h16"/>',minus:'<path d="M4 12h16"/>',play:'<path d="m8 4 12 8-12 8Z"/>',pause:'<path d="M8 4v16M16 4v16"/>'};
const icon=k=>`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ico[k]}</svg>`;
function geographic(item){const p=item.location||item.coordinates||item;const lat=p.lat??p.latitude,lon=p.lng??p.lon??p.longitude;return Number.isFinite(lat)&&Number.isFinite(lon)&&Math.abs(lat)<=90&&Math.abs(lon)<=180?[lon,lat]:null;}
function buildLayout(data){
 const orders=data.orders||[],warehouse=data.warehouse||{id:'warehouse'},source=[warehouse,...orders],geo=source.map(geographic);
 const actual=geo.every(Boolean)&&new Set(geo.map(p=>p.join(','))).size===geo.length;
 const points=new Map();
 if(actual){const cx=geo.reduce((s,p)=>s+p[0],0)/geo.length,cz=geo.reduce((s,p)=>s+p[1],0)/geo.length;const xy=geo.map(p=>[(p[0]-cx)*Math.cos(cz*Math.PI/180),-(p[1]-cz)]);const scale=140/Math.max(.00001,...xy.flat().map(Math.abs));source.forEach((o,i)=>points.set(o.id,{x:xy[i][0]*scale,z:xy[i][1]*scale}));}
 else{
  points.set(warehouse.id,{x:-80,z:44});
  const slots=[{x:0,z:-44},{x:80,z:0},{x:40,z:44},{x:-40,z:-44},{x:80,z:44},{x:-40,z:0},{x:40,z:-44},{x:0,z:44},{x:80,z:-44},{x:40,z:0},{x:-80,z:-44},{x:0,z:0}];
  if(orders.length>12){
   const columns=Math.ceil(Math.sqrt(orders.length)),rows=Math.ceil(orders.length/columns);
   orders.forEach((o,i)=>points.set(o.id,{x:((i%columns)-(columns-1)/2)*35,z:(Math.floor(i/columns)-(rows-1)/2)*35}));
   points.set(warehouse.id,{x:-(columns+1)*17.5,z:(rows+1)*17.5});
  }else orders.forEach((o,i)=>points.set(o.id,slots[i]));
 }
 const routes=(data.routes||[]).map((r,ri)=>{
  const sequence=[warehouse.id,...r.route.sequence,warehouse.id],legs=[];
  for(let i=1;i<sequence.length;i++){
   const from=points.get(sequence[i-1]),to=points.get(sequence[i]);if(!from||!to)continue;
   // Each delivery leg connects the declared nodes, including the return to depot.
   const bend=actual?{x:to.x,z:from.z}:((ri+i)%2?{x:from.x,z:to.z}:{x:to.x,z:from.z});
   const raw=[from,bend,to],path=raw.filter((p,j)=>!j||p.x!==raw[j-1].x||p.z!==raw[j-1].z);
   legs.push({from:sequence[i-1],to:sequence[i],index:i,returning:i===sequence.length-1,path});
  }
  return {...r,color:({'#6d9454':'#24664b','#8b7baa':'#725095','#bd9560':'#a86b2d','#5e9398':'#276e8a'})[r.color]||r.color||'#24664b',legs};
 });
 return {actual,points,routes,warehouse,orders};
}
async function mount(container,data,callbacks={}){
 let disposed=false,renderer,controls,scene,raf,observer;
 const model=buildLayout(data),chosen=data.selectedVehicle||'',names=data.names||{};
 const name=(kind,id)=>names[kind]?.[id]||id;
 const activeRoute=model.routes.find(r=>r.vehicle.id===chosen);
 const vehicleFilter=model.routes.length?`<label class="rm3-filter">路线<select data-map-filter><option value="">全部车辆</option>${model.routes.map(r=>`<option value="${escape(r.vehicle.id)}" ${r.vehicle.id===chosen?'selected':''}>${escape(name('vehicles',r.vehicle.id))} · ${r.route.sequence.length} 站</option>`).join('')}</select></label>`:'';
 const stopList=activeRoute?`<section class="rm3-route" style="--route-color:${escape(activeRoute.color)}"><div class="rm3-route-head"><h4>到店顺序</h4>${activeRoute.journey_url?`<a class="rm3-journey-link" href="${escape(activeRoute.journey_url)}" target="_blank" rel="noopener">驾驶全程</a>`:''}<button class="rm3-load-link" data-map-load="${escape(chosen)}">查看装卸</button></div><ol>${activeRoute.route.sequence.map((id,i)=>`<li><button data-map-order="${escape(id)}" aria-pressed="${id===data.selectedOrder}"><span>${i+1}</span>${escape(name('orders',id))}</button></li>`).join('')}</ol></section>`:`<p class="rm3-empty">${model.routes.length?'选择一辆车，即可查看到店顺序与装卸。':'计算方案后，这里显示车辆路线与配送顺序。'}</p>`;
 container.classList.add('route-map3d');container.dataset.mapStatus='loading';
 container.innerHTML=`<div class="rm3-head"><div><h3>配送地图</h3><p>${model.actual?'坐标定位 · 道路示意':'城区场景示意'}</p></div><div class="rm3-toolbar">${vehicleFilter}<div class="rm3-views" role="group" aria-label="地图视角"><button data-map-view="top" aria-pressed="true">俯视</button><button data-map-view="city" aria-pressed="false">三维</button><button data-map-expand aria-label="专注查看地图">展开</button></div></div></div><div class="rm3-stage"><div class="rm3-canvas" aria-label="配送道路与门店三维地图"></div><div class="rm3-pins"></div><div class="rm3-map-state" role="status">正在载入地图</div><div class="rm3-controls"><button data-map-control="plus" aria-label="放大地图">${icon('plus')}</button><button data-map-control="minus" aria-label="缩小地图">${icon('minus')}</button><button data-map-control="reset" aria-label="复位地图">${icon('reset')}</button></div><div class="rm3-north">北<span>↑</span></div><p class="rm3-help">拖动平移 · 滚轮缩放</p></div><div class="rm3-legends"><span><i class="rm3-depot-key"></i>仓库</span><span><i class="rm3-store-key"></i>门店</span><span>${activeRoute?'数字为到店顺序 · 虚线回仓':'全车路线分布'}</span></div><div class="rm3-route-list">${stopList}</div>`;
 const filter=container.querySelector('[data-map-filter]');
 filter?.addEventListener('change',()=>{
  if(filter.value)callbacks.onVehicle?.(filter.value);
  else callbacks.onAll?.();
 });
 const event=e=>{
  const b=e.target.closest('button');if(!b)return;
  if(b.hasAttribute('data-map-expand'))setExpanded(!expanded);
  if(b.dataset.mapOrder)callbacks.onOrder?.(b.dataset.mapOrder);
  if(b.dataset.mapLoad)callbacks.onLoad?.(b.dataset.mapLoad);
 };
 container.addEventListener('click',event);
 let expanded=false;
 const setExpanded=value=>{expanded=value;container.dataset.expanded=String(value);const b=container.querySelector('[data-map-expand]');b.textContent=value?'收起':'展开';b.setAttribute('aria-label',value?'收起地图':'专注查看地图');if(container.showPopover){if(value){container.setAttribute('popover','manual');container.showPopover();}else{if(container.matches(':popover-open'))container.hidePopover();container.removeAttribute('popover');}}else{container.classList.toggle('rm3-expanded',value);callbacks.onExpand?.();}if(!value)b.focus();};
 const keydown=e=>{if(e.key==='Escape'&&expanded){setExpanded(false);e.preventDefault();}};
 document.addEventListener('keydown',keydown);
 const destroy=()=>{if(disposed)return;disposed=true;if(expanded)setExpanded(false);container.classList.remove('rm3-expanded');cancelAnimationFrame(raf);observer?.disconnect();controls?.dispose();container.removeEventListener('click',event);document.removeEventListener('keydown',keydown);if(scene)scene.traverse(o=>{o.geometry?.dispose();if(o.material)for(const m of Array.isArray(o.material)?o.material:[o.material]){m.map?.dispose();m.dispose();}});renderer?.dispose();renderer?.forceContextLoss();};
 try{
 const [T,{OrbitControls}]=await libs();if(disposed||!container.isConnected)return {destroy};
 scene=new T.Scene();scene.background=new T.Color('#e3ede5');
 const initialBounds=container.querySelector('.rm3-canvas').getBoundingClientRect();
 const camera=new T.PerspectiveCamera(38,initialBounds.width/Math.max(1,initialBounds.height),.1,5000);
 renderer=new T.WebGLRenderer({antialias:true,alpha:false});renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.7));renderer.outputColorSpace=T.SRGBColorSpace;renderer.toneMapping=T.ACESFilmicToneMapping;renderer.toneMappingExposure=.88;renderer.shadowMap.enabled=true;renderer.shadowMap.type=T.PCFSoftShadowMap;
 container.querySelector('.rm3-canvas').append(renderer.domElement);renderer.domElement.setAttribute('role','img');renderer.domElement.setAttribute('aria-label','城区道路、仓库和门店路线，可切换俯视与三维');
 const hemi=new T.HemisphereLight('#ffffff','#617b66',1.8);scene.add(hemi);const sun=new T.DirectionalLight('#fff4dc',1.9);sun.position.set(-100,180,50);sun.castShadow=true;sun.shadow.mapSize.set(1024,1024);Object.assign(sun.shadow.camera,{left:-180,right:180,top:140,bottom:-140});sun.shadow.bias=-.001;scene.add(sun);
 const mat=(color,extra={})=>new T.MeshStandardMaterial({color,roughness:.87,...extra});
 const mesh=(w,h,d,color,x,y,z,extra={})=>{const o=new T.Mesh(new T.BoxGeometry(w,h,d),mat(color,extra));o.position.set(x,y,z);o.receiveShadow=true;o.castShadow=h>2;scene.add(o);return o;};
 const bound=[...model.points.values()];const extentX=Math.max(95,...bound.map(p=>Math.abs(p.x)+18)),extentZ=Math.max(62,...bound.map(p=>Math.abs(p.z)+18));
 mesh(extentX*2,.7,extentZ*2,'#b8cdb1',0,-.65,0);
 const xs=[...new Set([...model.points.values()].map(p=>p.x))].sort((a,b)=>a-b),zs=[...new Set([...model.points.values()].map(p=>p.z))].sort((a,b)=>a-b);
 const roadWidth=model.actual?7:9;
 // All candidate streets exist in the same scene; buildings stay between streets.
 for(const x of xs){mesh(roadWidth,.15,extentZ*2,'#99ad9f',x,0,0);mesh(roadWidth+.9,.12,extentZ*2,'#d8e4d8',x,-.13,0);}
 for(const z of zs){mesh(extentX*2,.16,roadWidth,'#99ad9f',0,.03,z);mesh(extentX*2,.12,roadWidth+.9,'#d8e4d8',0,-.13,z);}
 if(!model.actual){
  for(const x of xs)for(let z=-extentZ+5;z<extentZ;z+=9)if(!zs.some(v=>Math.abs(v-z)<7))mesh(.35,.05,3,'#eff0e6',x,.17,z);
  for(const z of zs)for(let x=-extentX+5;x<extentX;x+=9)if(!xs.some(v=>Math.abs(v-x)<7))mesh(3,.05,.35,'#eff0e6',x,.18,z);
 }
 for(let xi=0;xi<xs.length-1;xi++)for(let zi=0;zi<zs.length-1;zi++){
  const w=xs[xi+1]-xs[xi]-16,d=zs[zi+1]-zs[zi]-16;if(w<6||d<6)continue;
  const x=(xs[xi]+xs[xi+1])/2,z=(zs[zi]+zs[zi+1])/2;
  mesh(w,.5,d,'#dce6d5',x,.2,z);
  if((xi+zi)%4===1){mesh(w-2,.25,d-2,'#aac39b',x,.65,z);for(let k=0;k<4;k++){const tx=x+(k%2-.5)*w*.5,tz=z+(Math.floor(k/2)-.5)*d*.5;mesh(.5,2,.5,'#8b8a6d',tx,1.8,tz);const tree=new T.Mesh(new T.IcosahedronGeometry(2.4,1),mat('#86a78a'));tree.position.set(tx,4,tz);scene.add(tree);}continue;}
  for(let k=0;k<2;k++){const bw=Math.min(13,w*.38),bd=Math.min(15,d*.68),h=3.6+((xi*7+zi*3+k*5)%9);const bx=x+(k?1:-1)*w*.23;mesh(bw,h,bd,k?'#a6bda8':'#94b0a1',bx,h/2+.5,z);mesh(bw+.7,.35,bd+.7,'#dce5d7',bx,h+.65,z);if(h>7)mesh(bw*.7,.7,bd*.18,'#a1afa6',bx,h+1.1,z);}
 }
 model.routes.forEach((r,ri)=>{
  if(activeRoute&&r!==activeRoute)return;
  const color=new T.Color(r.color),width=activeRoute?1.05:.5;
  r.legs.forEach(leg=>{
   const lane=(leg.index%2?1:-1)*1.25+ri*.7;
   const points=leg.path.map(p=>new T.Vector3(p.x+lane,.5+ri*.12,p.z+lane));
   for(let j=1;j<points.length;j++){
    const a=points[j-1],b=points[j],len=a.distanceTo(b);if(len<.01)continue;
    const direction=b.clone().sub(a).normalize();
    const dashed=leg.returning&&!!activeRoute;
    const segments=dashed?Math.ceil(len/6):1;for(let seg=0;seg<segments;seg++){const size=dashed?Math.min(3.7,len-seg*6):len;if(size<=0)continue;const tube=new T.Mesh(new T.CylinderGeometry(width,width,size,8),new T.MeshBasicMaterial({color,transparent:!activeRoute,opacity:activeRoute?1:.62}));tube.position.copy(a).addScaledVector(direction,(dashed?seg*6:0)+size/2);tube.quaternion.setFromUnitVectors(new T.Vector3(0,1,0),direction);scene.add(tube);}
   }
  });
 });
 // Depot and storefronts give the road scene scale without covering the routes.
 const depot=model.points.get(model.warehouse.id);mesh(18,6,12,'#668b78',depot.x-11,3.2,depot.z-11);mesh(19,.8,13,'#3d6958',depot.x-11,6.6,depot.z-11);for(let k=0;k<3;k++)mesh(3.5,3.5,.15,'#d2e0d4',depot.x-16+k*5,2,depot.z-4.9);
 for(const o of model.orders){const p=model.points.get(o.id);mesh(7,4.2,6,'#d7ded0',p.x+7,2.2,p.z-7);mesh(8,.5,1.8,'#739380',p.x+7,3.5,p.z-3.5);mesh(4,2,.12,'#6b8a86',p.x+7,1.6,p.z-3.9);}
 const pins=[];const host=container.querySelector('.rm3-pins');
 for(const item of [model.warehouse,...model.orders]){
  const isDepot=item.id===model.warehouse.id,p=model.points.get(item.id),assignment=model.routes.find(r=>r.route.sequence.includes(item.id)),stop=assignment?.route.sequence.indexOf(item.id)+1;
  const focused=assignment?.vehicle.id===chosen;
  if(activeRoute&&!isDepot&&!focused&&item.id!==data.selectedOrder)continue;
  const marker=new T.Mesh(new T.CylinderGeometry(isDepot?3:1.8,isDepot?3:1.8,.4,24),new T.MeshBasicMaterial({color:isDepot?'#315f4c':assignment?.color||'#6d8979'}));
  marker.position.set(p.x,.6,p.z);scene.add(marker);
  if(!isDepot&&!focused&&item.id!==data.selectedOrder&&model.routes.length)continue;
  const label=name(isDepot?'warehouses':'orders',item.id);
  const button=document.createElement('button');
  button.className='rm3-pin'+(isDepot?' rm3-pin-depot':stop?' rm3-pin-number':'')+(item.id===data.selectedOrder?' selected':'');
  button.style.setProperty('--pin-color',isDepot?'#315f4c':assignment?.color||'#6d8979');
  if(!isDepot)button.dataset.mapOrder=item.id;
  else button.dataset.mapDepot='';
  button.textContent=stop||label;
  button.title=label;
  button.setAttribute('aria-label',isDepot?label:`查看${label}${stop?'，第'+stop+'站':''}`);
  host.append(button);pins.push({button,p:new T.Vector3(p.x,2,p.z)});
 }
 controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.enableRotate=false;controls.maxPolarAngle=Math.PI*.46;controls.minDistance=85;controls.maxDistance=700;controls.target.set(0,0,0);controls.mouseButtons.LEFT=T.MOUSE.PAN;controls.mouseButtons.RIGHT=T.MOUSE.ROTATE;controls.touches.ONE=T.TOUCH.PAN;controls.touches.TWO=T.TOUCH.DOLLY_ROTATE;
 let view='top';const setView=v=>{view=v;const distance=Math.max(extentZ*2+28,(extentX*2+28)/camera.aspect)/(2*Math.tan(camera.fov*Math.PI/360));const radius=distance*.8;controls.maxDistance=Math.max(700,distance*2);camera.position.set(v==='city'?radius*.62:0,v==='city'?radius*.8:distance,v==='city'?radius*.83:.01);controls.target.set(0,0,0);controls.enableRotate=v==='city';controls.mouseButtons.LEFT=v==='city'?T.MOUSE.ROTATE:T.MOUSE.PAN;controls.update();container.querySelectorAll('[data-map-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mapView===v)));container.querySelector('.rm3-help').textContent=v==='city'?'拖动旋转 · 右键平移 · 滚轮缩放':'拖动平移 · 滚轮缩放';container.dataset.mapView=v;};
 const command=e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.mapView)setView(b.dataset.mapView);if(b.dataset.mapControl==='reset')setView(view);if(['plus','minus'].includes(b.dataset.mapControl)){camera.position.sub(controls.target).multiplyScalar(b.dataset.mapControl==='plus'?.8:1.25).add(controls.target);controls.update();}};
 container.addEventListener('click',command);
 const oldDestroy=destroy;const controller={destroy:()=>{container.removeEventListener('click',command);oldDestroy();},setView,getState:()=>({view,vehicleId:chosen,orders:model.orders.length,routes:model.routes.length,status:container.dataset.mapStatus})};
 const resize=()=>{const box=container.querySelector('.rm3-canvas').getBoundingClientRect();if(!box.width||!box.height)return;renderer.setSize(box.width,box.height,false);camera.aspect=box.width/box.height;camera.updateProjectionMatrix();setView(view);};observer=new ResizeObserver(resize);observer.observe(container.querySelector('.rm3-canvas'));resize();setView(data.view||'top');
 container.querySelector('.rm3-map-state').hidden=true;container.dataset.mapStatus='ready';
 let last=0;
 const loop=now=>{if(disposed||!container.isConnected)return;raf=requestAnimationFrame(loop);controls.update();if(now-last<32||!container.getClientRects().length)return;last=now;renderer.render(scene,camera);const rect=renderer.domElement.getBoundingClientRect(),placed=[];
  // Labels remain in screen space and are separated rather than shrinking with the scene.
  for(const pin of pins){const p=pin.p.clone().project(camera);let x=(p.x*.5+.5)*rect.width,y=(-p.y*.5+.5)*rect.height-24;const bw=pin.button.offsetWidth||75,bh=pin.button.offsetHeight||38;x=Math.min(rect.width-bw/2-5,Math.max(bw/2+5,x));y=Math.max(7,Math.min(rect.height-bh-10,y));for(let attempts=0;attempts<8;attempts++){if(!placed.some(q=>Math.abs(q.x-x)<(q.w+bw)/2+5&&Math.abs(q.y-y)<bh+3))break;y+=bh+7;if(y>rect.height-bh-8)y=8+attempts*7;}placed.push({x,y,w:bw});pin.button.style.transform=`translate(${x}px,${y}px) translateX(-50%)`;pin.button.hidden=p.z>1||p.z< -1;}
 };raf=requestAnimationFrame(loop);return controller;
 }catch(error){if(!disposed){container.dataset.mapStatus='fallback';container.querySelector('.rm3-map-state').textContent='三维地图暂不可用，可选择车辆查看到店顺序。';container.querySelector('.rm3-controls').hidden=true;container.querySelector('.rm3-stage').classList.add('rm3-fallback');}return {destroy};}
}
window.LogiRouteMap3D={mount,buildLayout,version:'20261106-route-focus'};
})();
