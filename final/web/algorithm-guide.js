'use strict';
document.getElementById('print-guide').addEventListener('click',()=>window.print());
const contents=document.getElementById('contents');
const mobile=window.matchMedia('(max-width:860px)');
function setContents(){contents.open=!mobile.matches;}
setContents();
mobile.addEventListener('change',setContents);
const contentsLinks=[...document.querySelectorAll('.contents a')];
contentsLinks.forEach(link=>link.addEventListener('click',()=>{if(mobile.matches)contents.open=false;}));
if('IntersectionObserver' in window){
  const positions=new Map();
  const observer=new IntersectionObserver(entries=>{
    entries.forEach(entry=>positions.set(entry.target.id,entry.isIntersecting));
    const current=contentsLinks.find(link=>positions.get(link.hash.slice(1)));
    if(!current)return;
    contentsLinks.forEach(link=>{if(link===current)link.setAttribute('aria-current','location');else link.removeAttribute('aria-current');});
  },{rootMargin:'-120px 0px -50% 0px',threshold:0});
  document.querySelectorAll('article section').forEach(section=>observer.observe(section));
}
let openBeforePrint;
window.addEventListener('beforeprint',()=>{openBeforePrint=[...document.querySelectorAll('.source-detail')].map(detail=>[detail,detail.open]);openBeforePrint.forEach(([detail])=>detail.open=true);});
window.addEventListener('afterprint',()=>{(openBeforePrint||[]).forEach(([detail,open])=>detail.open=open);});
