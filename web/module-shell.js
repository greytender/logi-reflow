// Professional capabilities, grouped into the same application's workflow.
document.addEventListener('DOMContentLoaded',()=>{
 const main=document.querySelector('main');
 if(document.getElementById('csvImport')){
  const titles=['导入与计划','异常处理','执行与交接','保存历史','故障独立试算'];
  const groups=[['csvImport'],['aiControls'],['dispatchControls','handoffControls'],['dispatchHistory'],['faultControls','normalReference']];
  const nav=document.createElement('nav');nav.className='module-tabs';nav.setAttribute('aria-label','连续调度任务');
  const panels=[];
  groups.forEach((ids,i)=>{
   const panel=document.createElement('div');panel.dataset.modulePanel=i;
   ids.forEach(id=>{const el=document.getElementById(id);if(el)panel.append(el);});
   if(i===1){for(const id of ['urgentForm','windowForm']){const el=document.getElementById(id);if(el){const section=el.closest('section');panel.append(section||el);}}}
   panels.push(panel);main.append(panel);
   const b=document.createElement('button');b.textContent=titles[i];b.onclick=()=>select(i);nav.append(b);
  });
  const intro=main.querySelector('h1');intro.textContent='连续调度与保存场景';intro.insertAdjacentElement('beforebegin',nav);
  function select(i){panels.forEach((p,j)=>p.hidden=i!==j);[...nav.children].forEach((b,j)=>b.setAttribute('aria-pressed',String(i===j)));}
  select(0);
  // Keep the normal plan modification tools accessible, grouped in disclosure.
  const normalActions=main.querySelector('.normal-actions');
  if(normalActions&&!normalActions.closest('[data-module-panel]')){const details=document.createElement('details');details.innerHTML='<summary>12店基础数据与独立试算参数</summary>';normalActions.before(details);details.append(normalActions);const reference=document.getElementById('normalReference');panels[4].prepend(details);if(reference)reference.open=true;}
  for(const el of [...main.children])if(el.tagName==='P'&&el.querySelector('a'))el.hidden=true;
  for(const a of document.querySelectorAll('a'))if(a.textContent.includes('旧的抽象容量'))a.closest('section').hidden=true;
 }
 let lastHeight=0;
 const report=()=>{const height=Math.ceil(document.body.getBoundingClientRect().height+25);if(height!==lastHeight){lastHeight=height;parent.postMessage({type:'module-height',height},location.origin);}};
 new ResizeObserver(report).observe(document.body);report();
});
