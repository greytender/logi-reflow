// Run through the Tabbit skill's stable CLI against an isolated QA server.
// Continuous tab video is recorded by MediaRecorder. No screenshots are taken.
const demoPage=globalThis.captureDemoPage||page;
const w=globalThis.walkthrough||{scenes:[],cursor:{x:1090,y:680},originalCoordinates:null};
globalThis.walkthrough=w;
if(!w.initialized){
 await demoPage.setViewportSize({width:1264,height:671});
 await demoPage.goto('http://127.0.0.1:18778/',{waitUntil:'domcontentloaded'});
 await demoPage.waitForFunction(()=>proof&&!proofBusy);w.initialized=true;
}
w.snap=async()=>({at:Date.now()});
w.installCursor=async()=>demoPage.evaluate(()=>{if(document.getElementById('recordCursor'))return;const e=document.createElement('div');e.id='recordCursor';e.style.cssText='position:fixed;left:0;top:0;pointer-events:none;z-index:2147483647;width:28px;height:40px;transition:transform .5s ease';e.innerHTML='<svg width="28" height="40" viewBox="0 0 28 40"><path d="M2 2V30L9 24L15 38L21 35L15 22H27Z" fill="white" stroke="#245440" stroke-width="2"/></svg>';document.body.append(e);});
w.start=async function(id){await w.installCursor();const scene={id,actions:[],frames:[],startedAt:Date.now(),viewport:{width:1264,height:671},startCursor:{...w.cursor}};w.scenes.push(scene);return scene;};
w.click=async function(scene,locator,label){
 await locator.scrollIntoViewIfNeeded();const box=await locator.boundingBox();assert(box);
 const point={x:Math.round(box.x+box.width/2),y:Math.round(box.y+box.height/2)};
 scene.frames.push({kind:'before',cursor:point,image:await w.snap(scene.id,'before-'+scene.actions.length)});
 await demoPage.evaluate(p=>{const e=document.getElementById('recordCursor');e.style.transform=`translate(${p.x}px,${p.y}px)`;},point);await new Promise(r=>setTimeout(r,650));
 await demoPage.mouse.move(point.x,point.y,{steps:24});await locator.click();
 scene.actions.push({label,...point,at:Date.now()});w.cursor=point;await new Promise(r=>setTimeout(r,500));
};
w.focus=async function(id){await demoPage.evaluate(value=>document.getElementById(value)?.scrollIntoView({block:'start',behavior:'smooth'}),id);await new Promise(r=>setTimeout(r,900));};
w.focusLocator=async function(locator){const box=await locator.boundingBox();assert(box);await demoPage.evaluate(y=>window.scrollBy(0,y-45),box.y);};
w.end=async function(scene,id){if(id)await w.focus(id);scene.actionsEndedAt=Date.now();const remaining=(w.minimumSeconds||0)*1000-(Date.now()-scene.startedAt);if(remaining>0)await new Promise(r=>setTimeout(r,remaining));scene.endedAt=Date.now();scene.endCursor={...w.cursor};scene.complete=true;return scene;};
w.liveWait=async()=>demoPage.waitForFunction(()=>live&&!liveBusy);
w.stage=async function(number){
 const id=String(number).padStart(2,'0'),s=await w.start(id);
 switch(number){
 case 0: await demoPage.evaluate(()=>window.scrollTo(0,0));break;
 case 1:
  await w.click(s,demoPage.locator('[data-case=geometry]'),'箱体几何反例');await demoPage.waitForFunction(()=>proof?.case==='geometry'&&!proofBusy);
  assert.equal(await demoPage.evaluate(()=>proof.baseline.metrics.cost),48);assert.equal(await demoPage.evaluate(()=>proof.checked.metrics.cost),117);s.proof=await demoPage.evaluate(()=>({baseline:proof.baseline,checked:proof.checked}));await w.focus('proofResult');break;
 case 2:
  await w.click(s,demoPage.locator('[data-case=fixed_route]'),'原摆放改序反例');await demoPage.waitForFunction(()=>proof?.case==='fixed_route'&&!proofBusy);
  s.proof=await demoPage.evaluate(()=>({baseline:proof.baseline,checked:proof.checked}));assert.equal(s.proof.checked.metrics.late_minutes,45);await w.focus('proofResult');break;
 case 3:
  await w.click(s,demoPage.locator('[data-case=fragile]'),'易碎堆叠反例');await demoPage.waitForFunction(()=>proof?.case==='fragile'&&!proofBusy);await expect(demoPage.locator('#proofUseInput')).toBeDisabled();await w.focus('proofResult');s.proof=await demoPage.evaluate(()=>({checked:proof.checked,unserved:proof.unserved}));break;
 case 4:
  await w.click(s,demoPage.locator('#startGuided'),'开始完整演示');await demoPage.waitForFunction(()=>scenario?.replay&&!scenarioBusy);s.proof=await demoPage.evaluate(()=>({signed:scenario.replay.signed_orders,receipts:scenario.replay.receipts.length}));assert.equal(s.proof.signed,6);assert.equal(s.proof.receipts,18);await w.focus('decision');break;
 case 5:
  await w.click(s,demoPage.locator('#demoInput > summary'),'展开输入');await expect(demoPage.locator('#input')).toBeVisible();await w.focus('demoInput');break;
 case 6:
  await w.click(s,demoPage.locator('#demoInput > summary'),'收起输入');await demoPage.locator('#scenarioVehicle').selectOption('LARGE');await w.focus('guide');
  await w.click(s,demoPage.locator('#scenarioStep'),'调整卸货预览');await demoPage.locator('#scenarioStep').press('Home');await demoPage.locator('#scenarioStep').press('ArrowRight');await demoPage.locator('#scenarioStep').press('ArrowRight');assert.equal(await demoPage.locator('#scenarioStep').inputValue(),'2');break;
 case 7:
  await w.click(s,demoPage.locator('#demoNext'),'查看配送回执');await expect(demoPage.locator('#execution')).toBeVisible();await w.focus('execution');break;
 case 8:
  await w.click(s,demoPage.locator('#demoNext'),'查看异常与交接');await expect(demoPage.locator('#exception')).toBeVisible();await w.focus('exception');break;
 case 9:
  await w.click(s,demoPage.locator('#demoNext'),'进入交互模拟');await w.click(s,demoPage.locator('#liveStart'),'开始固定装载交互');await w.liveWait();w.originalCoordinates=await demoPage.evaluate(()=>JSON.stringify(live.scene.live_plan.fixed_loading.manifest.placements));await w.focus('interactive');break;
 case 10:
  await w.click(s,demoPage.locator('[data-step=event]'),'处理异常');const details=demoPage.locator('.inline-details summary').filter({hasText:'更多操作'});await w.click(s,details,'展开更多操作');await w.click(s,demoPage.locator('#liveUrgentExample'),'尝试固定模式新货');await demoPage.locator('#liveEventReviewed').check();const revision=await demoPage.evaluate(()=>live.scene.revision);await w.click(s,demoPage.locator('#liveCalculate'),'核验固定模式新货');await demoPage.waitForFunction(()=>!liveBusy);assert.equal(await demoPage.evaluate(()=>live.scene.revision),revision);assert.equal(await demoPage.evaluate(()=>live.scene.candidate),null);await w.focus('liveDraftStatus');s.proof={fixed_new_goods_rejected:true,adopted_state_preserved:true};break;
 case 11:
  await w.click(s,demoPage.locator('#liveWindowExample'),'填写收货变化');await expect(demoPage.locator('#liveCalculate')).toBeDisabled();await w.click(s,demoPage.locator('#liveEventReviewed'),'核对事件字段');await w.click(s,demoPage.locator('#liveCalculate'),'生成候选');await demoPage.waitForFunction(()=>live?.scene.candidate&&!liveBusy);await expect(demoPage.locator('#liveAdopt')).toBeDisabled();await w.focus('liveCandidate');break;
 case 12:
  await w.click(s,demoPage.locator('#liveAdoptReviewed'),'核对采用');await w.click(s,demoPage.locator('#liveAdopt'),'采用候选');await demoPage.waitForFunction(()=>live&&!live.scene.candidate&&!liveBusy);assert.equal(await demoPage.evaluate(()=>JSON.stringify(live.scene.live_plan.fixed_loading.manifest.placements)),w.originalCoordinates);await w.focus('nextTask');s.proof={coordinates_unchanged_after_adoption:true};break;
 case 13:
  for(let i=0;i<4;i++){await demoPage.locator('#liveReceiptReviewed').check();await w.click(s,demoPage.locator('#appNextReceipt'),'当前任务回执');await w.liveWait();}
  s.proof=await demoPage.evaluate(()=>({receipts:live.scene.receipts.length,delivered:Object.values(live.scene.cargo).filter(x=>x==='delivered').length}));assert.equal(s.proof.receipts,3);await w.focus('nextTask');break;
 case 14:
  for(let i=0;i<40;i++){
   if(await demoPage.evaluate(()=>live.scene.receipts.length>=18))break;
   await demoPage.locator('#liveReceiptReviewed').check();await w.click(s,demoPage.locator('#appNextReceipt'),'剩余门店分步回执');await w.liveWait();
   if(await demoPage.evaluate(()=>live.scene.receipts.length%3===0))s.frames.push({kind:'progress',image:await w.snap(s.id,'progress-'+i)});
  }
  s.proof=await demoPage.evaluate(()=>({receipts:live.scene.receipts.length,delivered:Object.values(live.scene.cargo).filter(x=>x==='delivered').length}));assert.equal(s.proof.receipts,18);assert.equal(s.proof.delivered,6);
  await w.click(s,demoPage.locator('.manual-execution summary'),'展开回仓时钟');const back=await demoPage.evaluate(()=>Math.ceil(Math.max(live.scene.clock,...Object.values(live.scene.live_plan.routes).map(r=>r.return_at||0))));const time=String(Math.floor(back/60)).padStart(2,'0')+':'+String(back%60).padStart(2,'0');await demoPage.locator('#liveClock').fill(time);await w.click(s,demoPage.locator('#liveAdvance'),'推进至回仓');await w.liveWait();s.proof.returned=await demoPage.evaluate(()=>Object.values(live.scene.states).every(q=>q.returned_at!=null||(!q.cargo.length&&!q.remaining.length)));assert(s.proof.returned);await w.focus('liveHandoff');break;
 case 15:
  await w.click(s,demoPage.locator('.scene-utilities summary'),'场景管理');await w.click(s,demoPage.locator('#liveEnd'),'结束已完成临时场景');await demoPage.waitForFunction(()=>!live&&!liveBusy);await demoPage.locator('#liveMode').selectOption('csv');await w.click(s,demoPage.locator('#liveStart'),'开始普通CSV模式');await w.liveWait();await w.click(s,demoPage.locator('[data-step=event]'),'处理CSV紧急单');const more=demoPage.locator('.inline-details summary').filter({hasText:'更多操作'});if(!(await more.evaluate(e=>e.parentElement.open)))await more.click();await w.click(s,demoPage.locator('#liveUrgentExample'),'填写仓库急单');await demoPage.locator('#liveEventReviewed').check();await w.click(s,demoPage.locator('#liveCalculate'),'核验普通模式候选');await demoPage.waitForFunction(()=>live?.scene.candidate&&!liveBusy);await demoPage.locator('#liveAdoptReviewed').check();await w.click(s,demoPage.locator('#liveAdopt'),'采用仓库急单');await w.liveWait();assert.equal(await demoPage.evaluate(()=>live.scene.cargo.E01),'warehouse');s.proof={new_goods_still_in_warehouse:true,three_dimensional_guarantee:false};await w.focus('liveHandoff');break;
 case 23:
  await w.click(s,demoPage.locator('[data-step=event]'),'第二批异常字段');
  await w.click(s,demoPage.locator('#liveUrgentExample'),'填写第二批仓库急单');await demoPage.locator('#liveEventReviewed').check();await w.click(s,demoPage.locator('#liveCalculate'),'核验第二批候选');await demoPage.waitForFunction(()=>live?.scene.candidate&&!liveBusy);await demoPage.locator('#liveAdoptReviewed').check();await w.click(s,demoPage.locator('#liveAdopt'),'采用第二批仓库急单');await w.liveWait();s.proof=await demoPage.evaluate(()=>({first:live.scene.cargo.E01,second:live.scene.cargo.E02}));assert.equal(s.proof.first,'warehouse');assert.equal(s.proof.second,'warehouse');await w.focus('liveHandoff');break;
 case 22:{
  await w.click(s,demoPage.locator('[data-view=tools]'),'专业工作台');const f=demoPage.frameLocator('#toolFrame');const fp=demoPage.frames().find(f=>f.url().includes('workspace.html'));await fp.waitForFunction(()=>normalResult&&!calculating);await w.click(s,f.getByRole('button',{name:'故障独立试算',exact:true}),'单次故障试算');await w.click(s,f.locator('#simulateFailure'),'模拟单次故障与恢复');await fp.waitForFunction(()=>faultResult&&!recovering);await expect(f.locator('#failureComparison')).toBeVisible();await w.focusLocator(f.locator('#failureComparison'));s.proof={single_failure_comparison_visible:true,continuous_fault_support:false};break;}
 case 16:{
  await w.click(s,demoPage.locator('[data-view=tools]'),'专业工作台');const f=demoPage.frameLocator('#toolFrame');await w.click(s,f.getByRole('button',{name:'导入与计划',exact:true}),'导入与计划');await f.locator('#sampleDispatch').waitFor();await w.click(s,f.locator('#sampleDispatch'),'导入构造CSV样例');const fp=demoPage.frames().find(f=>f.url().includes('workspace.html'));await fp.waitForFunction(()=>dispatchScene&&!dispatchBusy);await w.click(s,f.getByRole('button',{name:'保存历史',exact:true}),'只读保存历史');await f.locator('#dispatchHistoryScene').waitFor();await f.locator('#dispatchHistoryScene').selectOption({index:1});await expect(f.locator('#dispatchHistoryStatus')).not.toContainText('正在');await w.focusLocator(f.locator('#dispatchHistory'));s.proof={persisted_csv_import:true,isolated_qa_database:true};break;}
 case 17:{
  await w.click(s,demoPage.locator('[data-tool=cargo]'),'装载工作台');const f=demoPage.frameLocator('#toolFrame');await f.locator('#sampleLoading').waitFor();await w.click(s,f.locator('#sampleLoading'),'联合装载样例');const fp=demoPage.frames().find(f=>f.url().includes('cargo.html'));await fp.waitForFunction(()=>loadingResult?.complete&&!loadingBusy);await expect(f.locator('#loadingDiagram')).toBeVisible();await w.focusLocator(f.locator('#loadingDiagram'));s.proof={independent_loading_audit_visible:true,complete_loading_sample:true};break;}
 case 18:
  await w.click(s,demoPage.locator('#openKeySettings'),'查看可选AI设置');await expect(demoPage.locator('#settingsDialog')).toBeVisible();s.proof={api_key_entered:false,paid_model_calls:0};break;
 case 19:
  await w.click(s,demoPage.locator('#closeSettings'),'关闭设置');await w.click(s,demoPage.locator('[data-view=technical]'),'方法与验证');await w.focus('technicalView');break;
 case 20:
  await w.click(s,demoPage.getByRole('link',{name:'用大白话读完整思路、最终目标、优点与不足'}),'完整故事讲解');await expect(demoPage.locator('#title-0')).toBeVisible();await demoPage.evaluate(()=>window.scrollTo(0,0));break;
 default:throw Error('Unknown stage');
 }
 if(number===17){
  const f=demoPage.frameLocator('#toolFrame'),fp=demoPage.frames().find(f=>f.url().includes('cargo.html'));
  await w.click(s,f.locator('#fixedLoadingAck'),'核对完整布局');
  await w.click(s,f.locator('#startFixedLoading'),'创建固定坐标保存场景');
  await fp.waitForFunction(()=>!loadingBusy);
  await expect(f.locator('#fixedLoadingStatus')).toContainText('已创建独立固定装载模拟');
  await w.focusLocator(f.locator('#fixedLoadingStatus'));s.proof.fixed_coordinates_scene_saved=true;
 }
 return await w.end(s);
};
// Append a bounded stage list in the same CLI program; refresh page handles per request.
