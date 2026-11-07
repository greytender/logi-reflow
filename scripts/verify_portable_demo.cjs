const {chromium}=require('../outputs/browser-tools/node_modules/playwright-core');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const out=path.resolve('outputs/central-loop'),base='http://127.0.0.1:18807';
 const report={posts:[],errors:[]};
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000}});const page=await context.newPage();
  page.on('request',r=>{if(r.method()==='POST')report.posts.push(new URL(r.url()).pathname);});
  page.on('pageerror',e=>report.errors.push(e.message));
  await page.goto(base+'/');await page.getByRole('link',{name:'打开中央仓方案',exact:false}).first().click();
  await page.waitForFunction(()=>typeof task!=='undefined'&&task?.data?.orders?.length===103&&step==='plan');
  report.status=await page.evaluate(()=>({orders:task.data.orders.length,rows:task.intake_source.rows.length,
   confirmed:state.confirmed,plans:task.plans.length,step,documentId:task.intake_source.document_id}));
  assert.equal(report.status.rows,116);assert(report.status.confirmed);assert.equal(report.status.plans,2);
  assert.equal(report.posts.length,0,'Opening the bundled plan must not import, calculate or mutate data');
  const original=await context.request.get(base+'/api/intake/original?id='+report.status.documentId);
  assert.equal(original.status(),200);
  const digest=crypto.createHash('sha256').update(await original.body()).digest('hex');
  assert.equal(digest,'40849d76aceaf3589ac68fdad258c847ed0bbf464e65b76a468258bc36726587');
  report.xlsx_sha256=digest;
  await page.screenshot({path:path.join(out,'portable-plan.png'),fullPage:false});
  await page.goto(base+'/intake.html#intake='+report.status.documentId);
  await page.locator('#downloadOriginal').waitFor();assert(await page.locator('#downloadOriginal').isVisible());
  await page.waitForFunction(()=>document.querySelector('#previewAllCount')?.textContent==='116');
  await page.screenshot({path:path.join(out,'portable-orders.png'),fullPage:false});
  assert.equal(report.errors.length,0);report.passed=true;
 }finally{fs.writeFileSync(path.join(out,'portable-browser.json'),JSON.stringify(report,null,2));await browser.close();}
 console.log(JSON.stringify(report));
})().catch(e=>{console.error(e);process.exitCode=1;});
