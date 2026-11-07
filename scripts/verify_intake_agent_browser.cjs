// Isolated synthetic UI journey. Browser plugin bootstrap is unavailable on this host.
const {chromium}=require('../outputs/browser-tools/node_modules/playwright-core');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const base=process.env.INTAKE_QA_URL||'http://127.0.0.1:18787';
const out=path.resolve('outputs/intake-agent-20261106');fs.mkdirSync(out,{recursive:true});
(async()=>{
  const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  const result={checks:[],console_errors:[],layouts:[]};
  try {
    const context=await browser.newContext({viewport:{width:1440,height:1000}});
    const page=await context.newPage();page.on('pageerror',e=>result.console_errors.push(e.message));
    await page.goto(base+'/intake.html');await page.locator('.intake-example > summary').click();await page.locator('#sampleIntake').click();
    await page.locator('#intakeAgent').waitFor({state:'visible'});
    await page.waitForFunction(()=>document.getElementById('previewAllCount').textContent==='3');
    const id=new URLSearchParams(new URL(page.url()).hash.slice(1)).get('intake');
    const original=(await (await context.request.get(base+'/api/intake/document?id='+id)).json()).document;
    await page.locator('#organizeLocally').click();
    await page.waitForFunction(()=>document.querySelector('#applyIntakeAgent')&&!document.querySelector('#applyIntakeAgent').disabled);
    for(const key of ['regular_weight','cold_weight'])await page.locator(`[data-agent-manual-unit="${key}"]`).selectOption('kg');
    for(const key of ['regular_volume','cold_volume'])await page.locator(`[data-agent-manual-unit="${key}"]`).selectOption('m3');
    await page.locator('#applyIntakeAgent').click();
    await page.waitForFunction(()=>document.getElementById('reviewSaveState').textContent.includes('版本 1'));
    let saved=(await (await context.request.get(base+'/api/intake/review?id='+id)).json()).review;
    assert.equal(saved.revision,1);assert.deepEqual(saved.config.rows,[6,7,8]);
    assert.equal(saved.assessment.rows[1].normalized.cold_quantity,null);
    assert(saved.assessment.rows[0].identity.store_id);
    assert.equal(saved.assessment.rows[0].identity.store_id,saved.assessment.rows[1].identity.store_id);
    assert.deepEqual((await (await context.request.get(base+'/api/intake/document?id='+id)).json()).document,original);
    result.checks.push('local_organize_explicit_units_apply_preserves_source_and_cold_blanks');
    await page.locator('#previewSearch').fill('构造门店乙');
    assert.equal(await page.locator('#standardOrders tbody tr').count(),1);
    await page.locator('#previewSearch').fill('');
    await page.locator('[data-preview-filter="issues"]').click();assert.equal(await page.locator('#standardOrders tbody tr').count(),2);
    await page.locator('[data-preview-filter="all"]').click();
    await page.locator('#standardOrders details').first().locator('summary').click();
    assert((await page.locator('#standardOrders details').first().innerText()).includes('C6'));
    await page.locator('#standardOrders details').first().locator('summary').click();
    await page.locator('#continueDispatch').click();assert(await page.locator('#intakeDispatchSection').evaluate(e=>e.open));
    await page.locator('#intakeDispatchSection > summary').click();result.checks.push('search_issue_filter_cell_sources_and_dispatch_progression');
    await page.locator('#agentInstructions').fill('保留原有字段和已确认单位，只列出还需要人工确认的项目。');
    await page.locator('#organizeWithDeepSeek').click();
    await page.waitForFunction(()=>document.getElementById('organizeWithDeepSeek').textContent.includes('正在'));
    const running=(await (await context.request.get(base+'/api/intake/agent-job?id='+id)).json()).job;
    assert.equal(running.mode,'model');await page.reload();
    await page.waitForFunction(()=>document.querySelector('#applyIntakeAgent')&&!document.querySelector('#applyIntakeAgent').disabled||document.getElementById('agentJob').textContent.includes('本次整理未完成'),null,{timeout:60000});
    assert(!await page.locator('.agent-error').count(),await page.locator('#agentJob').innerText());
    const restored=(await (await context.request.get(base+'/api/intake/agent-job?id='+id)).json()).job;
    assert.equal(restored.id,running.id);assert.equal(restored.calls.length,1);
    result.checks.push('real_deepseek_in_browser_reload_resumes_same_job_without_rebilling');
    await page.locator('#reviewBatch > summary').click();await page.locator('[data-review-global="delivery_date"]').fill('2026-11-06');
    await page.waitForFunction(()=>document.getElementById('applyIntakeAgent').disabled);
    result.checks.push('unsaved_local_edit_disables_old_agent_proposal');
    await page.locator('#reviewSave').click();await page.waitForFunction(()=>document.getElementById('reviewSaveState').textContent.includes('版本 2'));
    await page.locator('#reviewBatch > summary').click();
    const mobileContext=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
    const mobile=await mobileContext.newPage();mobile.on('pageerror',e=>result.console_errors.push(e.message));
    await mobile.goto(base+'/intake.html#intake='+id);await mobile.locator('#standardOrders table').waitFor();
    for(const [device,target] of [['desktop',page],['mobile',mobile]]){
      const layout=await target.evaluate(()=>({width:innerWidth,documentWidth:document.documentElement.scrollWidth,
        smallText:[...document.querySelectorAll('main :is(p,small,span,label,th,td)')].filter(e=>e.getBoundingClientRect().width&&parseFloat(getComputedStyle(e).fontSize)<14).map(e=>e.tagName)}));
      result.layouts.push({device,...layout});assert(layout.documentWidth<=layout.width+1,JSON.stringify(layout));assert.equal(layout.smallText.length,0);
    }
    await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:path.join(out,'desktop.png'),fullPage:true});
    await mobile.locator('#intakeAgent').scrollIntoViewIfNeeded();await mobile.screenshot({path:path.join(out,'mobile.png'),fullPage:true});
    result.checks.push('desktop_mobile_layout_no_page_overflow_minimum_14px_text');
    const fixture=path.join(out,'generic.xlsx');
    if(fs.existsSync(fixture)){
      await page.locator('#intakeFile').setInputFiles(fixture);await page.locator('#uploadIntake').click();
      await page.waitForFunction(()=>document.getElementById('intakeMeta').textContent.includes('generic.xlsx'));
      await page.locator('#organizeLocally').click();await page.waitForFunction(()=>document.querySelector('#applyIntakeAgent')&&!document.querySelector('#applyIntakeAgent').disabled);
      assert((await page.locator('.agent-change').count())>=8);
      await page.locator('#applyIntakeAgent').click();await page.waitForFunction(()=>document.getElementById('reviewSaveState').textContent.includes('版本 1'));
      assert.equal(await page.locator('#previewAllCount').textContent(),'2');
      assert.equal(await page.locator('#standardOrders tbody tr').count(),2);
      result.checks.push('generic_reordered_headers_local_inference_full_adoption');
    }
    assert.equal(result.console_errors.length,0);result.passed=true;
  }finally {
    fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify(result,null,2));await browser.close();
  }
  console.log(JSON.stringify(result));
})().catch(error=>{console.error(error);process.exitCode=1;});
