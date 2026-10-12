// Optional browser evidence: installed Chrome, isolated headless profile.
// npm install playwright-core under outputs/browser-tools before running.
const {chromium}=require('./outputs/browser-tools/node_modules/playwright-core');
const fs=require('fs'),path=require('path'),assert=require('assert');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:18769/loading.html');
  const folder=path.resolve('examples/fixed-loading-three');
  await page.locator('#loadingFiles').setInputFiles(['orders.csv','vehicles.csv','travel.csv','settings.json','loading.json'].map(n=>path.join(folder,n)));
  await page.locator('#solveLoading').click();
  await page.waitForFunction(()=>loadingResult?.complete&&!loadingBusy);
  assert(await page.locator('#startFixedLoading').isDisabled());
  const original=await page.evaluate(()=>loadingResult.optimized.routes.V1.loading.placements);
  await page.locator('#fixedLoadingAck').check();await page.locator('#startFixedLoading').click();
  await page.waitForFunction(()=>document.getElementById('fixedLoadingStatus').textContent.includes('已创建独立')&&!loadingBusy);
  await page.locator('#startFixedLoading').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.resolve('evidence/fixed-loading-created-browser.png')});
  await page.goto('http://127.0.0.1:18769/normal.html');
  await page.waitForFunction(()=>dispatchScene?.live_plan?.fixed_loading&&!dispatchBusy);
  const manifest=await page.evaluate(()=>dispatchScene.live_plan.fixed_loading);
  assert.deepStrictEqual(manifest.manifest.placements.V1,original);
  assert(await page.locator('#simulateUrgent').isDisabled());
  await page.locator('#windowTime').fill('08:00');await page.locator('#windowStore').selectOption('B');
  await page.locator('#windowOpen').fill('10:00');await page.locator('#windowDue').fill('10:20');await page.locator('#windowClose').fill('11:30');
  await page.locator('#simulateWindow').click();
  await page.waitForFunction(()=>dispatchScene?.candidate&&!dispatchBusy);
  const candidate=await page.evaluate(()=>dispatchScene.versions.find(v=>v.id===dispatchScene.candidate).plan);
  assert.deepStrictEqual(candidate.routes.V1.sequence,['A','B','C']);
  assert(candidate.routes.V1.fixed_loading_check.passed);
  await page.locator('#fixedCargoDetails summary').click();
  await page.locator('#fixedCargoDetails').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.resolve('evidence/fixed-loading-candidate-browser.png')});
  await page.locator('#dispatchReviewAck').check();await page.locator('#adoptDispatch').click();
  await page.waitForFunction(()=>dispatchScene?.active===2&&!dispatchBusy);
  await page.reload();await page.waitForFunction(()=>dispatchScene?.active===2&&!dispatchBusy);
  assert.deepStrictEqual(await page.evaluate(()=>dispatchScene.live_plan.fixed_loading),manifest);
  const urgent=await page.evaluate(async()=>{
   const s=dispatchScene;const r=await fetch('/api/dispatch/event',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:s.id,revision:s.revision,kind:'urgent',event:{time:s.clock}})});return {status:r.status,body:await r.json()};
  });assert.strictEqual(urgent.status,400);assert(urgent.body.error.includes('剩余空间'));
  for(const oid of ['A','B','C']){
   await page.evaluate(async oid=>{
    const stop=dispatchScene.live_plan.routes.V1.stops.find(x=>x.id===oid),t=Math.max(dispatchScene.clock,stop.arrival);
    for(const [receipt,time]of [['arrived',t],['unloading',t],['signed',t+5]]){
     const ok=await dispatchAction('receipt',{vehicle:'V1',order:oid,receipt,time,source:'manual_simulation'});if(!ok)throw Error('Receipt failed');
    }
   },oid);
  }
  await page.evaluate(()=>dispatchAction('advance',{time:800}));
  const final=await page.evaluate(()=>({receipts:dispatchScene.receipts.length,cargo:dispatchScene.cargo,node:dispatchScene.states.V1.node,frozen:dispatchScene.live_plan.fixed_loading}));
  assert.strictEqual(final.receipts,9);assert.strictEqual(final.node,'WH');assert.deepStrictEqual(final.frozen,manifest);
  await page.setViewportSize({width:390,height:844});
  await page.locator('#fixedCargoDetails summary').click();await page.locator('#fixedCargoDetails').scrollIntoViewIfNeeded();
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);assert(!overflow);
  await page.screenshot({path:path.resolve('evidence/fixed-loading-mobile-browser.png')});
  assert.deepStrictEqual(errors,[]);
  fs.writeFileSync('evidence/fixed-loading-browser.json',JSON.stringify({browser:'Installed Chrome, headless isolated profile',server:'18769 isolated test database',input:'examples/fixed-loading-three',created_coordinates_match_preview:true,unchecked_creation_disabled:true,candidate_sequence:candidate.routes.V1.sequence,original_coordinates_verified:true,adopt_reload_preserved:true,urgent_rejected:urgent,receipts:final.receipts,returned:final.node==='WH',mobile_overflow:overflow,page_errors:errors},null,2));
  console.log(JSON.stringify({browser:'Chrome',candidate:candidate.routes.V1.sequence,receipts:final.receipts,returned:true,mobile_overflow:overflow,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
