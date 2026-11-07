const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const context={window:{},document:{getElementById:()=>null}};
vm.runInNewContext(fs.readFileSync('web/journey.js','utf8'),context);
const {buildTimeline,locate,withLoadingPreparation}=context.window.LogiJourneyFlow;
const route={sequence:['B','A'],frames:[{phase:'load'},{phase:'departure'},{phase:'arrival',stop_id:'B'},{phase:'rehandle_out',stop_id:'B'},{phase:'cold_open',stop_id:'B'},{phase:'rehandle_in',stop_id:'B'},{phase:'unload',stop_id:'B'},{phase:'pickup',stop_id:'B'},{phase:'departure_stop',stop_id:'B'},{phase:'arrival',stop_id:'A'},{phase:'unload',stop_id:'A'},{phase:'pickup',stop_id:'A'},{phase:'departure_stop',stop_id:'A'},{phase:'return',boxes:[{id:'shared',kind:'cold_box'},{id:'recovered'}]}]};
const before=JSON.stringify(route),t=buildTimeline(route);
assert.deepEqual(Array.from(t.stages.filter(s=>s.kind==='cargo'),s=>s.frame_index),route.frames.map((_,i)=>i));
assert.deepEqual(Array.from(t.stages.filter(s=>s.kind==='drive'),s=>s.leg_index),[0,1,2]);
assert.deepEqual(Array.from(t.stages.filter(s=>s.kind==='park'),s=>s.stop_id),['B','A',null]);
assert.equal(locate(t,0).index,0);assert.equal(locate(t,t.duration).stage.phase,'return');
assert.equal(locate(t,-3).time,0);assert.equal(locate(t,1e6).time,t.duration);
assert.equal(JSON.stringify(route),before);assert.equal(route.frames.at(-1).boxes.length,2);
for(const s of t.stages)assert.equal(locate(t,s.start).stage,s);
assert.throws(()=>buildTimeline({sequence:['B'],frames:[{phase:'departure'},{phase:'return'}]}));
assert.throws(()=>buildTimeline({...route,sequence:['A','B']}));
assert.throws(()=>buildTimeline({...route,sequence:['B','B']}));
assert.throws(()=>buildTimeline({sequence:[],frames:[]}));
const originalSpatial={scene:{routes:[{...route,vehicle_id:'V'}]}};
const prepared=withLoadingPreparation(originalSpatial,'V');
assert.equal(prepared.scene.routes[0].frames.length,route.frames.length+1);
assert.equal(prepared.scene.routes[0].frames[0].boxes.length,0);
assert.deepEqual(JSON.parse(JSON.stringify(prepared.scene.routes[0].frames.slice(1))),route.frames);
assert.equal(originalSpatial.scene.routes[0].frames.length,route.frames.length);
async function controllerChecks(){
 const elements=new Map(),documentEvents={},windowEvents={},callbacks=new Map();let rafId=0,reloads=0,driverDestroyed=0,cargoDestroyed=0;
 const stations=['depot','B','A','return'].map(id=>({dataset:{station:id},setAttribute(name,value){this[name]=value;}}));
 function element(id){if(!elements.has(id))elements.set(id,{id,dataset:{},hidden:false,innerHTML:'',textContent:'',value:'',attributes:{},setAttribute(k,v){this.attributes[k]=v;},querySelectorAll(){return id==='journeyStations'?stations:[];}});return elements.get(id);}
 const driver={setLeg(i,p){this.leg=i;this.progress=p;},setCamera(v){this.camera=v;},destroy(){driverDestroyed++;}};
 const cargo={setFrame(i){this.frame=i;},setMode(v){this.mode=v;},destroy(){cargoDestroyed++;}};
 const payload={vehicle_id:'V',warehouse_id:'WH',plan_id:'P',legs:[{distance_km:2},{distance_km:3},{distance_km:4}],map:{names:{orders:{B:'门店1',A:'门店2'},warehouses:{WH:'仓库1'},vehicles:{V:'车辆1'}},routes:[{vehicle:{id:'V'},route:{sequence:route.sequence,distance:9}}]},spatial:{scene:{routes:[{...route,vehicle_id:'V'}]}}};
 const doc={getElementById:element,querySelectorAll:()=>[],activeElement:null,body:{dataset:{}},addEventListener:(type,fn)=>documentEvents[type]=fn};
 const win={addEventListener:(type,fn)=>windowEvents[type]=fn};
 const runtime={window:win,document:doc,location:{search:'?id=T',reload:()=>reloads++},URLSearchParams,fetch:async()=>({ok:true,json:async()=>payload}),requestAnimationFrame:fn=>{callbacks.set(++rafId,fn);return rafId;},cancelAnimationFrame:id=>callbacks.delete(id),LogiDriving3D:{mount:async()=>driver},LogiSpatial3D:{mount:async()=>cargo},LogiRouteMap3D:{buildLayout:()=>({})}};
 win.LogiDriving3D=runtime.LogiDriving3D;win.LogiSpatial3D=runtime.LogiSpatial3D;win.LogiRouteMap3D=runtime.LogiRouteMap3D;
 vm.runInNewContext(fs.readFileSync('web/journey.js','utf8'),runtime);
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(doc.body.dataset.journeyStatus,'ready');assert.equal(win.journeyPresentation.getState().playing,false);assert.equal(doc.body.dataset.surface,'driving');assert.equal(driver.leg,0);assert.equal(driver.progress,0);
 function tick(now){const batch=[...callbacks];callbacks.clear();for(const [,fn]of batch)fn(now);}
 element('journeyPlay').onclick();tick(100);tick(200);const running=win.journeyPresentation.getState().time;assert.ok(running>0);
 element('journeyPlay').onclick();tick(300);tick(400);assert.equal(win.journeyPresentation.getState().time,running);assert.equal(win.journeyPresentation.getState().playing,false);
 // Each stage can be reached through the real range-input handler; this pauses playback.
 const model=buildTimeline(route);let priorFrame=-1;
 for(const stage of model.stages){element('journeyTimeline').oninput({target:{value:stage.start}});const state=win.journeyPresentation.getState();assert.equal(state.playing,false);assert.equal(doc.body.dataset.stageKind,stage.kind);assert.equal(doc.body.dataset.surface,stage.kind==='cargo'?'cargo':'driving');if(stage.kind==='cargo')priorFrame=stage.frame_index;assert.equal(cargo.frame,priorFrame+1);if(stage.kind==='drive'){assert.equal(driver.leg,stage.leg_index);assert.equal(driver.progress,0);}}
 element('journeyTimeline').oninput({target:{value:model.duration}});assert.equal(win.journeyPresentation.getState().playing,false);assert.equal(cargo.frame,route.frames.length);assert.equal(payload.spatial.scene.routes[0].frames.at(-1).boxes.length,2);
 element('journeyPrevious').onclick();assert.equal(win.journeyPresentation.getState().playing,false);element('journeyNext').onclick();assert.equal(cargo.frame,route.frames.length);
 // Replaying requires the explicit play button; paused frames never advance on their own.
 element('journeyTimeline').oninput({target:{value:model.duration}});element('journeyPlay').onclick();assert.equal(win.journeyPresentation.getState().playing,true);assert.equal(win.journeyPresentation.getState().time,0);assert.equal(cargo.frame,0);
 doc.hidden=true;documentEvents.visibilitychange();assert.equal(win.journeyPresentation.getState().playing,false);
 windowEvents.pagehide();assert.equal(driverDestroyed,1);assert.equal(cargoDestroyed,1);assert.equal(callbacks.size,0);
 windowEvents.pageshow({persisted:true});assert.equal(reloads,1);
 // If one renderer fails while the other is still loading, its late context is released.
 let resolveLate,lateDestroyed=0;
 const slowDriving={mount:()=>new Promise(resolve=>resolveLate=resolve)},failedCargo={mount:async()=>{throw Error('WebGL setup failure');}};
 const failureWindow={addEventListener:()=>{},LogiDriving3D:slowDriving,LogiSpatial3D:failedCargo,LogiRouteMap3D:runtime.LogiRouteMap3D};
 vm.runInNewContext(fs.readFileSync('web/journey.js','utf8'),{...runtime,window:failureWindow,LogiDriving3D:slowDriving,LogiSpatial3D:failedCargo});
 await new Promise(resolve=>setImmediate(resolve));assert.equal(doc.body.dataset.journeyStatus,'error');assert.equal(element('journeyPlayer').hidden,true);
 resolveLate({destroy:()=>lateDestroyed++});await new Promise(resolve=>setImmediate(resolve));assert.equal(lateDestroyed,1);
 console.log('PASS journey: exact cargo frames, empty-car preparation, ordered drive/parking, all-stage auto-view seek, pause/replay, return cargo retained, renderer-failure cleanup, pagehide cleanup and cached-page recovery.');
}
controllerChecks().catch(error=>{console.error(error);process.exitCode=1;});
