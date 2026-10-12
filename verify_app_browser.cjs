// Real background Chrome verification; mutations use an isolated QA server.
const {chromium}=require('./outputs/browser-tools/node_modules/playwright-core');
const fs=require('fs'),path=require('path'),assert=require('assert');
const base=process.env.APP_QA_URL||'http://127.0.0.1:18777';
const evidence=path.resolve('evidence');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const results={base,checks:[],viewport:[],console_errors:[]};
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>results.console_errors.push(e.message));
  page.on('dialog',d=>d.dismiss());
  const waitLive=()=>page.waitForFunction(()=>live&&!liveBusy);
  await page.goto(base+'/');await page.waitForFunction(()=>proof&&!proofBusy);
  assert.strictEqual(await page.locator('#proofMetrics strong').count(),4);
  await page.screenshot({path:path.join(evidence,'app-home-desktop.png'),fullPage:true});
  for(const name of ['geometry','fixed_route','fragile']){
   await page.locator('[data-case='+name+']').click();await page.waitForFunction(n=>proof?.case===n&&!proofBusy,name);
   const result=await page.evaluate(()=>({case:proof.case,baseline:proof.baseline,checked:proof.checked,coordinates:proof.geometry?.placements}));
   if(name==='fixed_route'){assert.deepStrictEqual(result.baseline.sequence,['A','C','B']);assert.deepStrictEqual(result.checked.sequence,['A','B','C']);assert.strictEqual(result.checked.metrics.late_minutes,45);}
   if(name==='fragile')assert(await page.locator('#proofUseInput').isDisabled());
   results.checks.push({evidence_case:name,result});
  }
  const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#proofExport').click()]);
  const file=path.join(evidence,'app-proof-export.json');await download.saveAs(file);assert.strictEqual(JSON.parse(fs.readFileSync(file)).case,'fragile');
  await page.locator('#openKeySettings').click();await page.locator('#deepseekKey').waitFor({state:'visible'});
  assert(await page.locator('#settingsDialog').isVisible());await page.keyboard.press('Escape');assert(!await page.locator('#settingsDialog').isVisible());
  results.checks.push({settings_dialog:true,api_key_not_changed:true});
  await page.locator('#startGuided').click();await page.waitForFunction(()=>scenario?.replay&&!scenarioBusy);
  const replay=await page.evaluate(()=>({stores:scenario.replay.signed_orders,receipts:scenario.replay.receipts.length,finished:scenario.replay.finished_at}));
  assert.strictEqual(replay.stores,6);assert.strictEqual(replay.receipts,18);results.checks.push({full_demo:replay});
  assert(await page.locator('#guide').isVisible());assert(!await page.locator('#execution').isVisible());
  await page.locator('#demoNext').click();assert(await page.locator('#execution').isVisible());
  await page.locator('#demoNext').click();assert(await page.locator('#exception').isVisible());
  await page.locator('[data-demo-stage=plan]').click();
  await page.evaluate(()=>{document.activeElement?.blur();window.scrollTo(0,0);});
  await page.screenshot({path:path.join(evidence,'app-complete-demo.png'),fullPage:true});
  await page.locator('#showManual').click();await page.locator('#liveStart').click();await waitLive();
  assert.strictEqual(await page.locator('[data-step-panel]:visible').count(),1);
  await page.locator('[data-step=event]').click();await page.locator('#liveWindowExample').click();
  assert(await page.locator('#liveCalculate').isDisabled());
  await page.locator('#liveEventReviewed').check();await page.locator('#liveCalculate').click();await page.waitForFunction(()=>live?.scene.candidate&&!liveBusy);
  assert(await page.locator('[data-step-panel=candidate]').isVisible());
  assert(await page.locator('#liveAdopt').isDisabled());
  const original=await page.evaluate(()=>live.scene.live_plan.fixed_loading.manifest.placements);
  await page.evaluate(()=>{document.activeElement?.blur();window.scrollTo(0,0);});await page.screenshot({path:path.join(evidence,'app-candidate-review.png'),fullPage:true});
  await page.locator('#liveAdoptReviewed').check();await page.locator('#liveAdopt').click();await page.waitForFunction(()=>live&&!live.scene.candidate&&!liveBusy);
  assert.deepStrictEqual(await page.evaluate(()=>live.scene.live_plan.fixed_loading.manifest.placements),original);
  assert(await page.locator('[data-step-panel=execute]').isVisible());
  // Advance exactly to the selected vehicle's next task and record three receipts.
  for(let step=0;step<4;step++){
   await page.locator('#liveReceiptReviewed').check();await page.locator('#appNextReceipt').click();await page.waitForFunction(()=>!liveBusy);
  }
  const execution=await page.evaluate(()=>({receipts:live.scene.receipts,cargo:live.scene.cargo,status:document.getElementById('liveStatus').textContent}));
  assert.strictEqual(execution.receipts.length,3);assert(Object.values(execution.cargo).includes('delivered'));results.checks.push({fixed_live_coordinates_preserved:true,execution});
  await page.locator('.scene-utilities summary').click();await page.locator('#liveEnd').click();await page.waitForFunction(()=>!live&&!liveBusy);
  await page.locator('#liveMode').selectOption('csv');await page.locator('#liveStart').click();await waitLive();
  await page.locator('[data-step=event]').click();await page.locator('.inline-details summary').filter({hasText:'更多操作'}).click();await page.locator('#liveUrgentExample').click();
  await page.locator('#liveEventReviewed').check();await page.locator('#liveCalculate').click();await page.waitForFunction(()=>live?.scene.candidate&&!liveBusy);
  assert.strictEqual(await page.evaluate(()=>live.scene.versions.find(v=>v.id===live.scene.candidate).plan.metrics.unserved),0);
  await page.locator('#liveAdoptReviewed').check();await page.locator('#liveAdopt').click();await page.waitForFunction(()=>!liveBusy&&!live.scene.candidate);
  results.checks.push({csv_urgent_generated_and_adopted:true});
  await page.locator('[data-view=tools]').click();const frame=page.frameLocator('#toolFrame');
  await frame.locator('.module-tabs').waitFor();await frame.locator('.module-tabs button').filter({hasText:'保存历史'}).click();await frame.locator('#dispatchHistory').waitFor({state:'visible'});
  await frame.locator('.module-tabs button').filter({hasText:'故障独立试算'}).click();assert(await frame.locator('#faultControls').isVisible());
  await page.locator('[data-tool=cargo]').click();const cargo=page.frameLocator('#toolFrame');await cargo.locator('#sampleLoading').click();
  await page.frames().find(f=>f.url().includes('cargo.html')).waitForFunction(()=>loadingResult?.complete===true);
  assert(await cargo.locator('#loadingDiagram').isVisible());results.checks.push({professional_modules:true});
  for(const entry of ['normal.html','loading.html','scenario.html']){await page.goto(base+'/'+entry);await page.waitForFunction(()=>location.pathname==='/');results.checks.push({old_entry_redirect:entry,destination:page.url()});}
  for(const width of [375,768,1024,1440]){
   await page.setViewportSize({width,height:900});await page.goto(base+'/');await page.waitForFunction(()=>proof&&!proofBusy);
   const overflow=await page.evaluate(()=>({width:innerWidth,document:document.documentElement.scrollWidth}));assert(overflow.document<=width);results.viewport.push(overflow);
   if(width===375)await page.screenshot({path:path.join(evidence,'app-home-mobile.png'),fullPage:true});
  }
  await page.emulateMedia({reducedMotion:'reduce'});assert.strictEqual(await page.locator('#startGuided').evaluate(e=>getComputedStyle(e).transitionDuration),'0s');
  await page.locator('[data-view=technical]').click();assert(await page.locator('#technicalView').isVisible());results.checks.push({reduced_motion:true,technical_boundaries:true});
  await page.setViewportSize({width:375,height:812});await page.goto(base+'/#workbench');
  await page.locator('#liveStart').click();await waitLive();await page.locator('[data-step=event]').click();
  assert(await page.locator('#liveWindowExample').isVisible());assert(await page.locator('#liveCalculate').isDisabled());
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.locator('[data-view=tools]').click();await page.frameLocator('#toolFrame').locator('.module-tabs').waitFor();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  results.checks.push({mobile_interactive_and_module:true});
  assert.deepStrictEqual(results.console_errors,[]);
  fs.writeFileSync(path.join(evidence,'app-browser-verification.json'),JSON.stringify(results,null,2));console.log(JSON.stringify({passed:true,checks:results.checks.length,viewport:results.viewport,console_errors:results.console_errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
