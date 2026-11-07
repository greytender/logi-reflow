(async function(){
'use strict';
const room=document.getElementById('spatialRoom'),status=document.getElementById('spatialPageStatus');
let viewer=null,loadGeneration=0;
async function show(scene,message){const generation=++loadGeneration;const selected=new URLSearchParams(location.search).get('vehicle_id')||'';if(viewer)viewer.update(scene,{vehicle_id:selected});else{const next=await LogiSpatial3D.mount(room,{scene,vehicle_id:selected});if(generation!==loadGeneration){next?.destroy();return;}viewer=next;}window.spatialViewer=viewer;status.textContent=message;}
document.getElementById('openSpatialDemo').onclick=()=>show(LogiSpatial3D.demo(),'装卸示例');
try{
 const params=new URLSearchParams(location.search),id=params.get('id');
 if(!id){await show(LogiSpatial3D.demo(),'装卸示例');return;}
 const query=new URLSearchParams({id});for(const key of ['warehouse_id','vehicle_id','plan_id'])if(params.get(key))query.set(key,params.get(key));
 const response=await fetch('/api/multiwarehouse/spatial?'+query,{cache:'no-store'}),payload=await response.json();if(!response.ok)throw Error(payload.error||'场景读取失败');
 const names=LogiSpatial3D.displayNames(payload);
 const journey=document.createElement('a');journey.href='/journey.html?'+query;journey.textContent='驾驶与装卸全程';document.querySelector('.spatial-page-header nav').append(journey);
 await show(payload,[payload.execution_present?'出发计划':'当前候选',names.warehouses[payload.warehouse_id],names.vehicles[payload.vehicle_id]].filter(Boolean).join(' · '));
}catch(error){status.textContent='方案读取失败：'+error.message;await show({routes:[],boundaries:['返回工作台检查数据，或查看功能示例。']},status.textContent);}
window.addEventListener('pagehide',()=>viewer?.destroy(),{once:true});
})();
