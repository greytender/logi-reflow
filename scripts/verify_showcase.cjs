// Uses an isolated QA server. No paid model calls or enterprise data.
const {chromium}=require('../outputs/browser-tools/node_modules/playwright-core');
const fs=require('fs'),path=require('path'),assert=require('assert');
const base=process.env.APP_QA_URL||'http://127.0.0.1:18805';
const dir=path.resolve('outputs/review-20261104');fs.mkdirSync(dir,{recursive:true});
async function inspect(page,name){
 await page.evaluate(()=>document.fonts.ready);
 const layout=await page.evaluate(()=>{
  const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'&&!e.closest('[hidden]')};
  const text=[...document.querySelectorAll('body *')].filter(e=>visible(e)&&[...e.childNodes].some(n=>n.nodeType===3&&n.textContent.trim())&&!e.closest('script,style,.sr-only'));
  return {width:innerWidth,scrollWidth:document.documentElement.scrollWidth,
   small:text.filter(e=>parseFloat(getComputedStyle(e).fontSize)<13.9).map(e=>({tag:e.tagName,cls:e.className?.baseVal??e.className,text:e.textContent.trim().slice(0,70),px:getComputedStyle(e).fontSize})),
   font:getComputedStyle(document.body).fontFamily,
   loadedFonts:[...document.fonts].map(f=>({family:f.family,status:f.status,weight:f.weight})),
   brokenImages:[...document.images].filter(i=>!i.complete||!i.naturalWidth).map(i=>i.src)};
 });
 await page.screenshot({path:path.join(dir,name+'.png'),fullPage:true});
 assert(layout.scrollWidth<=layout.width+1, name+' horizontal page overflow');
 assert.equal(layout.brokenImages.length,0,name+' broken images');
 assert(layout.loadedFonts.some(f=>f.family==='Resource Han Rounded'&&f.status==='loaded'),name+' rounded font not loaded');
 return {name,...layout};
}
(async()=>{const b=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const result={base,model_calls:0,checks:[],layouts:[],errors:[]};
 try{
  const p=await b.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
  p.on('pageerror',e=>result.errors.push(e.message));
  p.on('request',r=>{if(r.url().includes('/agent/start'))throw Error('Paid model request forbidden in UI QA');});
  await p.goto(base+'/');result.layouts.push(await inspect(p,'home-desktop'));
  await p.emulateMedia({reducedMotion:'no-preference'});
  assert(await p.evaluate(()=>document.getAnimations().length>0),'route animation missing');
  await p.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await p.evaluate(()=>document.getAnimations().length),0,'reduced motion ignored');
  result.checks.push('route motion enabled, system reduced motion respected');
  await p.setViewportSize({width:390,height:844});result.layouts.push(await inspect(p,'home-mobile'));
  const menu=p.locator('.site-menu');if(await menu.isVisible()){await menu.click();assert.equal(await menu.getAttribute('aria-expanded'),'true');await menu.click();}
  result.checks.push('homepage, mobile navigation');
  await p.setViewportSize({width:1440,height:1000});await p.goto(base+'/workspace.html');
  await p.locator('#welcomeSample').click();await p.locator('#agentRequest').waitFor();
  await p.locator('#agentRequest').fill('未发送的调度草稿');
  await p.locator('[data-step="prepare"]').click();await p.locator('#solveBatch').click();
  await p.waitForFunction(()=>!!state?.current_plan_id&&!busy);
  assert.equal(await p.locator('#agentRequest').inputValue(),'未发送的调度草稿');
  await p.locator('[data-step="overview"]').click();
  await p.locator('[data-route]').first().click();
  result.layouts.push(await inspect(p,'workspace-desktop'));
  assert(await p.locator('.network-detail').innerText());result.checks.push('sample, compute, select vehicle, preserve unsent composer');
  await p.locator('[data-step="plan"]').click();assert(await p.locator('#adoptBatch').isDisabled());
  await p.locator('#reviewPlan').check();await p.locator('#adoptBatch').click();
  await p.waitForFunction(()=>state?.confirmed&&!busy);
  await p.locator('[data-step="execute"]').click();
  const form=p.locator('form[data-receipt]').first();
  await form.locator('[name=finish]').fill('09:00');await form.locator('[name=cartons]').fill('2');await form.locator('[name=totes]').fill('1');
  await form.locator('[name=reviewed]').check();await form.locator('button[type=submit]').click();
  await p.waitForFunction(()=>state?.receipts>0&&!busy);
  assert(await p.locator('#saveBatch').isDisabled());
  const id=await p.evaluate(()=>task.id);await p.reload();await p.waitForFunction(()=>state?.receipts>0);
  assert.equal(await p.evaluate(()=>task.id),id);
  result.checks.push('explicit confirmation, simulated receipt, input lock, reload persistence');
  await p.locator('[data-step="overview"]').click();await p.locator('[data-route]').first().click();
  result.layouts.push(await inspect(p,'workspace-execution'));
  await p.evaluate(async()=>{const reply=await api('agent',{id:task.id,revision:task.revision,workflow:'risks'});setTask(reply);});
  assert(await p.locator('.chat-assistant').count());
  await p.locator('#agentConversation').evaluate(e=>e.scrollTop=0);
  result.layouts.push(await inspect(p,'agent-conversation-desktop'));
  result.checks.push('real local risk-tool output renders in agent conversation; no provider call');
  await p.setViewportSize({width:390,height:844});await p.reload();await p.waitForFunction(()=>!!task);
  result.layouts.push(await inspect(p,'workspace-mobile'));
  if(await p.locator('#agentSidebar').isHidden())await p.locator('#toggleAgent').click();
  await p.locator('#agentRequest').waitFor({state:'visible'});result.layouts.push(await inspect(p,'agent-mobile'));
  result.checks.push('mobile workspace and agent toggle');
  for(const url of ['/solutions.html?cap=dispatch','/solutions.html?cap=loading','/solutions.html?cap=cost','/solutions.html?cap=recovery','/solutions.html?cap=assistant','/guide.html','/scenarios.html','/intake.html','/app.html','/index.html']){
   const response=await p.goto(base+url);assert.equal(response.status(),200,url);
  }
  assert.deepStrictEqual(result.errors,[]);result.checks.push('all website, intake and legacy entries load without JavaScript errors');
  fs.writeFileSync(path.join(dir,'browser-verification.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify({checks:result.checks,small_text:result.layouts.map(l=>({name:l.name,count:l.small.length})),errors:result.errors},null,2));
 }finally{await b.close();}
})().catch(e=>{console.error(e);process.exit(1)});
