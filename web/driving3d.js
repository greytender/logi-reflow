/* Road-level presentation. Candidate order is retained; street geometry is illustrative. */
(function(){
'use strict';
const base=document.currentScript?.src||new URL('/driving3d.js',location.href).href;
let runtime;
const libraries=()=>runtime||=import(new URL('./vendor/three.module.min.js',base)).catch(e=>{runtime=null;throw e;});
const clamp=(v,a=0,b=1)=>Math.min(b,Math.max(a,Number(v)||0));
const distance=(a,b)=>Math.hypot(b.x-a.x,b.z-a.z);
const mix=(a,b,t)=>({x:a.x+(b.x-a.x)*t,z:a.z+(b.z-a.z)*t});
function roadPath(points){
 const source=(points||[]).filter((p,i,a)=>p&&Number.isFinite(p.x)&&Number.isFinite(p.z)&&(!i||distance(a[i-1],p)>1e-7));
 if(source.length<2)return source.map(p=>({...p}));
 const out=[{...source[0]}];
 for(let i=1;i<source.length-1;i++){
  const a=source[i-1],b=source[i],c=source[i+1],r=Math.min(5.5,distance(a,b)*.22,distance(b,c)*.22);
  const entry=mix(b,a,r/distance(a,b)),exit=mix(b,c,r/distance(b,c));out.push(entry);
  for(let k=1;k<=16;k++){const t=k/16,u=1-t;out.push({x:u*u*entry.x+2*u*t*b.x+t*t*exit.x,z:u*u*entry.z+2*u*t*b.z+t*t*exit.z});}
 }
 out.push({...source.at(-1)});return out;
}
function pathAt(points,progress){
 const p=roadPath(points);if(!p.length)return {x:0,z:0,dx:0,dz:1,length:0};
 const lengths=p.slice(1).map((v,i)=>distance(p[i],v)),length=lengths.reduce((a,b)=>a+b,0);
 let rest=length*clamp(progress),index=0;
 while(index<lengths.length-1&&rest>lengths[index]){rest-=lengths[index];index++;}
 const a=p[index],b=p[index+1]||a,len=lengths[index]||1,position=clamp(progress)>=1?{...p.at(-1)}:mix(a,b,clamp(rest/len));
 return {...position,dx:(b.x-a.x)/len,dz:(b.z-a.z)/len,length};
}
async function mount(container,options={}){
 const layout=options.layout?.points?options.layout:window.LogiRouteMap3D?.buildLayout(options.layout||{});
 const route=layout?.routes?.find(r=>r.vehicle.id===options.vehicle_id)||layout?.routes?.[0],names=options.names||{};
 let disposed=false,renderer,world,camera,truck,raf,observer,mode='driver',legIndex=0,progress=0,lastDraw=0;
 let pose={x:0,z:0,dx:0,dz:1,length:0},status='loading';
 const cleanups=[];const name=id=>names.orders?.[id]||names.warehouses?.[id]||id||'仓库';
 container.classList.add('driving3d');container.dataset.drivingStatus=status;container.dataset.cameraMode=mode;
 container.innerHTML='<div class="d3-canvas"></div><div class="d3-loading" role="status">正在载入道路</div><div class="d3-road-label"><span>驾驶视角</span><strong class="d3-destination"></strong></div><div class="d3-windshield" aria-hidden="true"><i></i><i></i></div><div class="d3-dashboard" aria-hidden="true"><div class="d3-wheel"><i></i></div><div class="d3-panel"><span class="d3-instrument">出发准备</span><strong class="d3-progress">0%</strong><div class="d3-gauge"><i></i></div></div><div class="d3-vent"></div></div><p class="d3-hint">道路驾驶</p>';
 const $=s=>container.querySelector(s);
 function updateText(){const leg=route?.legs?.[legIndex];$('.d3-destination').textContent=leg?(leg.returning?'返回':'前往')+name(leg.to):'暂无车辆路线';$('.d3-instrument').textContent=progress>=1?'已停靠':progress<=0?'准备出发':'沿道路行驶';$('.d3-progress').textContent=Math.round(progress*100)+'%';$('.d3-gauge i').style.width=progress*100+'%';container.dataset.legIndex=legIndex;container.dataset.progress=progress.toFixed(4);container.dataset.destination=leg?.to||'';}
 function setLeg(index,value){legIndex=Math.max(0,Math.min((route?.legs?.length||1)-1,Math.floor(Number(index)||0)));progress=clamp(value);pose=pathAt(route?.legs?.[legIndex]?.path||[],progress);updateText();renderPose();}
 function setCamera(value){if(!['driver','chase','overview'].includes(value))return;mode=value;container.dataset.cameraMode=mode;$('.d3-road-label>span').textContent={driver:'驾驶视角',chase:'跟车视角',overview:'路线全景'}[mode];renderPose();}
 function renderPose(){
  if(!camera||!world)return;const T=world.userData.THREE,dir=new T.Vector3(pose.dx,0,pose.dz),p=new T.Vector3(pose.x-pose.dz*1.5,0,pose.z+pose.dx*1.5);
  truck.position.copy(p);truck.rotation.y=Math.atan2(pose.dx,pose.dz);truck.visible=mode!=='driver';
  const target=p.clone().addScaledVector(dir,18);target.y=2.1;
  if(mode==='driver'){
   camera.fov=65;camera.position.copy(p).addScaledVector(dir,1.1);camera.position.y=2.35;
   if(progress>.96){const leg=route.legs[legIndex],endpoint=layout.points.get(leg.to);if(endpoint){const storefront=new T.Vector3(endpoint.x+(leg.returning?-9:7),2.6,endpoint.z-(leg.returning?5:5));target.lerp(storefront,(progress-.96)/.04*.58);}}
  }else if(mode==='chase'){camera.fov=51;camera.position.copy(p).addScaledVector(dir,-12);camera.position.y=7;target.copy(p).addScaledVector(dir,7);target.y=1.7;}
  else{camera.fov=43;const span=Math.max(85,...[...layout.points.values()].map(v=>Math.abs(v.x)+Math.abs(v.z)));camera.position.set(p.x+span*.55,span*.9,p.z+span*.65);target.copy(p);}
  camera.lookAt(target);camera.updateProjectionMatrix();
 }
 const getState=()=>({status,legIndex,progress,cameraMode:mode,vehicleId:route?.vehicle.id,from:route?.legs?.[legIndex]?.from,to:route?.legs?.[legIndex]?.to,position:{x:pose.x,z:pose.z},renderPosition:{x:pose.x-pose.dz*1.5,z:pose.z+pose.dx*1.5},laneOffset:1.5,heading:Math.atan2(pose.dx,pose.dz)});
 function destroy(){if(disposed)return;disposed=true;cancelAnimationFrame(raf);observer?.disconnect();cleanups.forEach(f=>f());world?.traverse(o=>{o.geometry?.dispose();for(const m of o.material?(Array.isArray(o.material)?o.material:[o.material]):[]){m.map?.dispose();m.dispose();}});renderer?.dispose();renderer?.forceContextLoss();container.replaceChildren();container.classList.remove('driving3d');for(const k of ['drivingStatus','cameraMode','legIndex','progress','destination'])delete container.dataset[k];}
 if(!route?.legs?.length){status='empty';container.dataset.drivingStatus=status;$('.d3-loading').textContent='这辆车没有配送路线。';return {setLeg,setCamera,getState,destroy};}
 try{
  const T=await libraries();if(disposed)return {setLeg,setCamera,getState,destroy};
  renderer=new T.WebGLRenderer({antialias:true,powerPreference:'high-performance'});renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.7));renderer.outputColorSpace=T.SRGBColorSpace;renderer.toneMapping=T.ACESFilmicToneMapping;renderer.toneMappingExposure=.96;renderer.shadowMap.enabled=true;renderer.shadowMap.type=T.PCFSoftShadowMap;
  $('.d3-canvas').append(renderer.domElement);renderer.domElement.setAttribute('role','img');renderer.domElement.setAttribute('aria-label','车内道路驾驶视角，沿当前配送路线驶向门店');
  world=new T.Scene();world.background=new T.Color('#dceaf0');world.fog=new T.Fog('#dceaf0',105,260);world.userData.THREE=T;
  camera=new T.PerspectiveCamera(65,1,.08,500);
  world.add(new T.HemisphereLight('#fff8e9','#819d86',2));const sun=new T.DirectionalLight('#fff2dc',2.3);sun.position.set(-65,110,30);sun.castShadow=true;sun.shadow.mapSize.set(2048,2048);Object.assign(sun.shadow.camera,{left:-150,right:150,top:125,bottom:-125,near:1,far:240});sun.shadow.normalBias=.06;world.add(sun);
  const material=(color,opts={})=>new T.MeshStandardMaterial({color,roughness:.88,...opts});
  const box=(parent,w,h,d,color,x,y,z,opts={})=>{const obj=new T.Mesh(new T.BoxGeometry(w,h,d),material(color,opts));obj.position.set(x,y,z);obj.castShadow=h>.8;obj.receiveShadow=true;parent.add(obj);return obj;};
  const points=[...layout.points.values()],ex=Math.max(105,...points.map(p=>Math.abs(p.x)+32)),ez=Math.max(90,...points.map(p=>Math.abs(p.z)+32));
  box(world,ex*2,.8,ez*2,'#a8b69a',0,-.6,0);
  const xs=[...new Set(points.map(p=>p.x))].sort((a,b)=>a-b),zs=[...new Set(points.map(p=>p.z))].sort((a,b)=>a-b),road=9;
  for(const x of xs){box(world,11,.18,ez*2,'#cbd0c7',x,-.01,0);box(world,road,.12,ez*2,'#5d6968',x,.13,0);for(let z=-ez+3;z<ez;z+=8)if(!zs.some(s=>Math.abs(s-z)<7)){box(world,.13,.02,3,'#ece9cf',x,.205,z);for(const side of [-1,1])box(world,.9,.28,5,'#cbd0c7',x+side*5,.12,z);}}
  for(const z of zs){box(world,ex*2,.18,11,'#cbd0c7',0,0,z);box(world,ex*2,.12,road,'#5d6968',0,.145,z);for(let x=-ex+3;x<ex;x+=8)if(!xs.some(s=>Math.abs(s-x)<7)){box(world,3,.02,.13,'#ece9cf',x,.215,z);for(const side of [-1,1])box(world,5,.28,.9,'#cbd0c7',x,.12,z+side*5);}}
  // A zebra crossing is kept off the node itself so loading remains clear.
  for(const x of xs)for(const z of zs)for(let k=0;k<7;k++){box(world,.55,.025,2.5,'#e8e8db',x-3+k,.22,z+6.8);box(world,2.5,.025,.55,'#e8e8db',x+6.8,.23,z-3+k);}
  function tree(x,z,size=1){const trunk=new T.Mesh(new T.CylinderGeometry(.22,.3,3,6),material('#7a7660'));trunk.position.set(x,1.5,z);world.add(trunk);const crown=new T.Mesh(new T.IcosahedronGeometry(2.2*size,1),material('#64876a'));crown.position.set(x,4.3,z);world.add(crown);crown.castShadow=true;}
  function light(x,z,angle){const pole=new T.Group();pole.position.set(x,0,z);pole.rotation.y=angle;world.add(pole);box(pole,.16,6.5,.16,'#687570',0,3.25,0);box(pole,1.9,.14,.16,'#687570',.9,6.4,0);box(pole,.6,.12,.36,'#e9e7cd',1.6,6.3,0);}
  for(const x of xs)for(let z=-ez+15;z<ez;z+=28)if(!zs.some(s=>Math.abs(s-z)<12)){light(x+6.1,z,Math.PI);tree(x-7.5,z+5,.8);}
  for(const z of zs)for(let x=-ex+15;x<ex;x+=28)if(!xs.some(s=>Math.abs(s-x)<12))light(x,z-6.1,Math.PI/2);
  function sign(text,x,y,z,width=6,color='#315f4c',rotation=0){const c=document.createElement('canvas');c.width=768;c.height=160;const ctx=c.getContext('2d');ctx.fillStyle=color;ctx.fillRect(0,0,c.width,c.height);ctx.fillStyle='#fffbed';ctx.font='600 72px "Resource Han Rounded CN", "Microsoft YaHei", sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(text,384,82,710);const texture=new T.CanvasTexture(c);texture.colorSpace=T.SRGBColorSpace;const mesh=new T.Mesh(new T.PlaneGeometry(width,width*160/768),new T.MeshBasicMaterial({map:texture,side:T.DoubleSide,toneMapped:false}));mesh.position.set(x,y,z);mesh.rotation.y=rotation;world.add(mesh);return mesh;}
  for(let xi=0;xi<xs.length-1;xi++)for(let zi=0;zi<zs.length-1;zi++){
   const w=xs[xi+1]-xs[xi]-16,d=zs[zi+1]-zs[zi]-16;if(w<8||d<8)continue;const x=(xs[xi]+xs[xi+1])/2,z=(zs[zi]+zs[zi+1])/2;
   box(world,w,.25,d,'#c8d0ba',x,.1,z);
   if((xi+zi)%3===1){for(let a=0;a<3;a++)for(let b=0;b<2;b++)tree(x+(a-1)*w*.23,z+(b-.5)*d*.5,1.2);box(world,w*.75,.05,1.5,'#dfd8bd',x,.3,z);continue;}
   for(let k=0;k<2;k++){const bw=Math.min(17,w*.38),bd=Math.min(18,d*.65),h=9+(xi*5+zi*3+k*4)%12,bx=x+(k?1:-1)*w*.23;box(world,bw,h,bd,['#c9c6b7','#b9c5bd','#d1c9b4'][(xi+zi+k)%3],bx,h/2+.3,z);box(world,bw+.5,.4,bd+.5,'#e5dfce',bx,h+.5,z);for(let floor=0;floor<Math.floor(h/3);floor++)for(let col=0;col<3;col++){box(world,1.4,1.45,.09,'#607b80',bx+(col-1)*bw*.24,2+floor*3,z+bd/2+.06);box(world,.09,1.45,1.4,'#607b80',bx+bw/2+.06,2+floor*3,z+(col-1)*bd*.25);}}
  }
  // Actual route nodes receive distinct storefronts and a warehouse dock.
  const depot=layout.points.get(layout.warehouse.id);box(world,19,7,14,'#a6b9ac',depot.x-12,3.6,depot.z-12);box(world,20,.5,15,'#446d5b',depot.x-12,7.3,depot.z-12);for(let k=0;k<3;k++){box(world,3.8,4,.12,'#6a7c76',depot.x-18+k*5.2,2.4,depot.z-4.92);box(world,4.3,.3,2.6,'#a4aaa1',depot.x-18+k*5.2,.3,depot.z-3.7);}sign(name(layout.warehouse.id),depot.x-12,5.8,depot.z-4.85,11);
  for(const order of layout.orders){const p=layout.points.get(order.id),x=p.x+9,z=p.z-10;box(world,9,5,9,'#d9d6c8',x,2.6,z);box(world,10,.4,2,'#477b65',x,3.6,z+4.8);box(world,5.4,2.5,.1,'#6c9191',x,1.7,z+4.56);box(world,.15,2.5,.18,'#d8dfd3',x,1.7,z+4.64);box(world,10,.4,10,'#e7e3d4',x,5.3,z);sign(name(order.id),x,4.45,z+4.68,7);box(world,7,.06,4,'#cbd1bf',p.x+3,.25,p.z-3);}
  // The route is visible at road level without replacing the road surface.
  for(const leg of route.legs){const path=roadPath(leg.path);for(let i=1;i<path.length;i++){const a=path[i-1],b=path[i],len=distance(a,b);if(len<.05)continue;const ribbon=box(world,.16,.025,len,'#acd1ac',(a.x+b.x)/2,.25,(a.z+b.z)/2);ribbon.rotation.y=Math.atan2(b.x-a.x,b.z-a.z);}}
  truck=new T.Group();world.add(truck);box(truck,2.1,.3,6.4,'#334c42',0,.65,-.2);box(truck,2.12,2.25,4.2,'#e4e8dc',0,2.05,-1);box(truck,2,1.65,1.9,'#c5d5c7',0,1.55,2.1);box(truck,1.7,.68,.04,'#41686b',0,2.03,3.07);box(truck,2.05,.22,.2,'#587867',0,.92,3.12);for(const x of [-1.06,1.06])for(const z of [-2.15,2.1]){const wheel=new T.Mesh(new T.CylinderGeometry(.49,.49,.24,16),material('#283831'));wheel.rotation.z=Math.PI/2;wheel.position.set(x,.53,z);truck.add(wheel);}
  status='ready';container.dataset.drivingStatus=status;$('.d3-loading').hidden=true;
  const resize=()=>{if(disposed)return;const rect=$('.d3-canvas').getBoundingClientRect();if(!rect.width||!rect.height)return;renderer.setSize(rect.width,rect.height,false);camera.aspect=rect.width/rect.height;camera.updateProjectionMatrix();renderPose();};observer=new ResizeObserver(resize);observer.observe($('.d3-canvas'));
  const lost=e=>{e.preventDefault();status='unavailable';container.dataset.drivingStatus=status;$('.d3-loading').hidden=false;$('.d3-loading').textContent='驾驶画面已中断，请重新载入。';};renderer.domElement.addEventListener('webglcontextlost',lost);cleanups.push(()=>renderer.domElement.removeEventListener('webglcontextlost',lost));
  setLeg(0,0);resize();const render=now=>{if(disposed)return;raf=requestAnimationFrame(render);if(now-lastDraw>40&&container.getClientRects().length){renderer.render(world,camera);lastDraw=now;}};raf=requestAnimationFrame(render);
 }catch(error){status='unavailable';container.dataset.drivingStatus=status;$('.d3-loading').hidden=false;$('.d3-loading').textContent='道路画面暂不可用，可继续查看配送步骤。';options.onError?.(error);}
 return {setLeg,setCamera,getState,destroy};
}
window.LogiDriving3D={mount,pathAt,roadPath,version:'20261105-road-level-v1'};
})();
