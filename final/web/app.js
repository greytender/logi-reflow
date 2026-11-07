const $ = selector=>document.querySelector(selector);
const closeButton = '<button class="icon close" type="button" aria-label="关闭">'+icon('close')+'</button>';
document.querySelectorAll('[data-icon]').forEach(element=>element.innerHTML=icon(element.dataset.icon));

const defaults = [
  {id:'cold',name:'长沙医药配送',vehicle:'湘 A·3076',stops:[1,2,3]},
  {id:'north',name:'城北门店补货',vehicle:'湘 A·6218',stops:[1,2]},
  {id:'first',name:'湘雅路补货',vehicle:'湘 A·9073',stops:[1]}
];
const storageKey='logireflow-final-tasks';
let tasks=defaults;
let selected=defaults[0].id;
try {
  const saved=JSON.parse(localStorage.getItem(storageKey));
  if (Array.isArray(saved?.tasks)) ({tasks,selected}=saved);
} catch {}
let zoom=1;
let mapCenter={x:500,y:310};
let mapDrag;
let suppressMapClick=false;
const mapViewport=$('#map');
const conversations=new Map();
const attachments=new Map();
const pendingReads=new Map();
const pendingReplies=new Map();
const current=()=>tasks.find(task=>task.id===selected);
const chatKey=()=>selected || 'general';
const dialog=$('#dialog');
let viewer;
function destroyViewer() {
  viewer?.destroy();
  viewer=null;
}
dialog.addEventListener('close',()=>{
  if (!dialog.open) destroyViewer();
});

function persist() {
  try { localStorage.setItem(storageKey,JSON.stringify({tasks,selected})); } catch {}
}
function renderTasks() {
  const list=$('#tasks');
  list.replaceChildren();
  if (!tasks.length) {
    const empty=document.createElement('p');
    empty.className='empty';
    empty.textContent='暂无任务';
    list.append(empty);
  }
  for (const task of tasks) {
    const row=document.createElement('div');
    row.className='task'+(task.id===selected?' active':'');
    row.innerHTML='<button class="task-select" aria-pressed="'+(task.id===selected)+'">'+icon('task')+'<div><strong></strong><small></small></div></button><button class="icon task-edit" title="编辑任务">'+icon('edit')+'</button>';
    row.querySelector('strong').textContent=task.name;
    row.querySelector('small').textContent=task.stops.length ? task.stops.length+' 家门店' : '待安排';
    row.querySelector('.task-select').onclick=()=>{selected=task.id;resetMap();render();};
    const edit=row.querySelector('.task-edit');
    edit.setAttribute('aria-label','编辑'+task.name);
    edit.onclick=()=>editTask(task);
    list.append(row);
  }
}
function updateZoom() {
  const width=1000/zoom,height=620/zoom;
  const limitX=(1000-width)/2+125,limitY=(620-height)/2+90;
  mapCenter.x=Math.max(500-limitX,Math.min(500+limitX,mapCenter.x));
  mapCenter.y=Math.max(310-limitY,Math.min(310+limitY,mapCenter.y));
  mapViewport.querySelector('svg').setAttribute('viewBox',[mapCenter.x-width/2,mapCenter.y-height/2,width,height].join(' '));
  $('#zoom-in').disabled=zoom>=1.8;
  $('#zoom-out').disabled=zoom<=1;
  $('#reset').disabled=zoom===1 && mapCenter.x===500 && mapCenter.y===310;
}
function resetMap() {
  zoom=1;
  mapCenter={x:500,y:310};
}
function render() {
  const task=current();
  persist();
  renderTasks();
  $('#batch').textContent=task?.name || '配送工作台';
  $('#summary').textContent=task?.stops.length ? task.stops.length+' 家门店 · 路线示意' : '';
  $('#map').innerHTML=drawMap(task?.stops.length ? task : null);
  updateZoom();
  $('#bell').classList.toggle('has-notices',Boolean(task?.stops.length));
  renderNotices();
  renderChat();
}
function openDialog(html) {
  destroyViewer();
  dialog.className='';
  dialog.innerHTML=html;
  dialog.querySelector('.close').onclick=()=>dialog.close();
  if (!dialog.open) dialog.showModal();
}
function editTask(task) {
  openDialog('<div class="panel-head"><h2>'+(task?'编辑任务':'添加任务')+'</h2>'+closeButton+'</div><form><label for="task-name">任务名称</label><input id="task-name" maxlength="32" required autofocus><div class="actions">'+(task?'<button type="button" class="danger">删除任务</button>':'')+'<button type="button" class="cancel">取消</button><button class="primary">保存</button></div></form>');
  const name=dialog.querySelector('input');
  name.value=task?.name || '';
  name.focus();
  dialog.querySelector('.cancel').onclick=()=>dialog.close();
  dialog.querySelector('form').onsubmit=event=>{
    event.preventDefault();
    if (!name.value.trim()) {name.setCustomValidity('请输入任务名称');name.reportValidity();return;}
    if (task) task.name=name.value.trim();
    else {
      selected='task-'+Date.now();
      tasks=[...tasks,{id:selected,name:name.value.trim(),stops:[]}];
      resetMap();
    }
    dialog.close();
    render();
  };
  name.oninput=()=>name.setCustomValidity('');
  if (task) dialog.querySelector('.danger').onclick=()=>{
    openDialog('<div class="panel-head"><h2>删除任务</h2>'+closeButton+'</div><p></p><div class="actions"><button class="cancel">取消</button><button class="danger">删除</button></div>');
    dialog.querySelector('p').textContent='删除「'+task.name+'」？';
    dialog.querySelector('.cancel').onclick=()=>editTask(task);
    dialog.querySelector('.danger').onclick=()=>{
      tasks=tasks.filter(item=>item.id!==task.id);
      if (selected===task.id) { selected=tasks[0]?.id;resetMap(); }
      dialog.close();
      render();
    };
  };
}
function renderNotices() {
  const panel=$('#notices');
  panel.innerHTML='<div class="panel-head"><h2>三方同步预览</h2>'+closeButton+'</div>';
  panel.querySelector('.close').onclick=()=>toggleNotices(false);
  const task=current();
  const rows=task?.stops.length ? [
    ['调度与仓内','查看箱位和装车顺序，核对本次配送计划。'],
    ['司机 · '+task.vehicle,'待同步：配送线路、装卸和回收要求。'],
    ...task.stops.map((i,order)=>['门店 · '+stops[i].name,'待同步：预计 '+['09:20','09:45','10:15'][order]+' 到店，交接货品与回收包装。'])
  ] : [];
  if (rows.length) panel.insertAdjacentHTML('beforeend','<p class="notice-scope">本机预览 · 方案未发送</p>');
  else panel.insertAdjacentHTML('beforeend','<p class="empty">暂无通知</p>');
  for (const [recipient,detail] of rows) {
    const row=document.createElement('article');
    row.className='notice';
    row.innerHTML='<strong></strong><p></p>';
    row.querySelector('strong').textContent=recipient;
    row.querySelector('p').textContent=detail;
    panel.append(row);
  }
}
function toggleNotices(open) {
  $('#notices').hidden=!open;
  $('#bell').setAttribute('aria-expanded',String(open));
}
async function showStop(index) {
  const stop=stops[index];
  const task=current();
  const stage=index ? task.stops.indexOf(index)+1 : 0;
  const before=index?'到店时':'装车前';
  const after=index?'卸货后':'装车后';
  const note=index===1?'冷链药品交接后，共用冷链箱继续随车。':
    index?'卸下本站货物后，把回收包装放在尾门一侧。':'先装后送的货，首站货物靠近尾门。';
  openDialog('<div class="panel-head"><div><h2>'+stop.name+'</h2><small>'+task.vehicle+' · '+(index?'第 '+stage+' 站卸货':'出发装车')+'</small></div>'+closeButton+'</div><div class="load-figure"></div><div class="load-viewer" hidden></div><div class="legend"><span style="--swatch:#c4a074">常温货物</span><span style="--swatch:#78aabd">共用冷链箱</span><span style="--swatch:#8db184">回收包装</span></div><p>'+note+' <a class="inline-evidence" href="algorithm.html#route">查看复算箱位 ↗</a></p><div class="actions"><button id="load-before">'+before+'</button><button id="load-after">'+after+'</button></div>');
  dialog.className='load-dialog';
  const buttons=[dialog.querySelector('#load-before'),dialog.querySelector('#load-after')];
  let isAfter=!index;
  function update(next) {
    isAfter=next;
    dialog.querySelector('.load-figure').innerHTML=loadingScene(stage,isAfter,task.stops.length);
    viewer?.setFrame(Number(isAfter));
    buttons.forEach((button,i)=>{
      button.classList.toggle('primary',Boolean(i)===isAfter);
      button.setAttribute('aria-pressed',String(Boolean(i)===isAfter));
    });
  }
  buttons[0].onclick=()=>update(false);
  buttons[1].onclick=()=>update(true);
  update(isAfter);
  if (location.protocol==='file:') return;
  const host=dialog.querySelector('.load-viewer');
  host.hidden=false;
  dialog.classList.add('live-load');
  const instance=await LogiSpatial3D.mount(host,{scene:loadingPayload(task,index,before,after)});
  if (!dialog.open || !host.isConnected || !instance.getState().webgl) {
    instance.destroy();
    host.hidden=true;
    if (host.isConnected) dialog.classList.remove('live-load');
    return;
  }
  viewer=instance;
  const meta=host.querySelector('.s3-scale').textContent;
  const dimensions=document.createElement('p');
  dimensions.className='load-dimensions';
  dimensions.textContent='车厢内尺寸 '+meta;
  host.before(dimensions);
  viewer.setFrame(Number(isAfter));
}
function mapSelection(event) {
  const marker=event.target.closest('[data-stop]');
  if (marker) showStop(Number(marker.dataset.stop));
}
mapViewport.addEventListener('pointerdown',event=>{
  if (!event.isPrimary || event.button!==0) return;
  suppressMapClick=false;
  const scale=mapViewport.querySelector('svg').getScreenCTM().a;
  mapDrag={id:event.pointerId,x:event.clientX,y:event.clientY,center:{...mapCenter},scale,active:false};
});
mapViewport.addEventListener('pointermove',event=>{
  if (mapDrag?.id!==event.pointerId) return;
  const dx=event.clientX-mapDrag.x,dy=event.clientY-mapDrag.y;
  if (!mapDrag.active) {
    if (Math.hypot(dx,dy)<6) return;
    mapDrag.active=true;
    suppressMapClick=true;
    mapViewport.setPointerCapture(event.pointerId);
    mapViewport.classList.add('is-dragging');
  }
  mapCenter={x:mapDrag.center.x-dx/mapDrag.scale,y:mapDrag.center.y-dy/mapDrag.scale};
  updateZoom();
});
function endMapDrag(event) {
  if (mapDrag?.id!==event.pointerId) return;
  mapDrag=null;
  mapViewport.classList.remove('is-dragging');
}
for (const type of ['pointerup','pointercancel','lostpointercapture']) {
  mapViewport.addEventListener(type,endMapDrag);
}
mapViewport.addEventListener('pointerleave',event=>{
  if (!mapDrag?.active) endMapDrag(event);
});
mapViewport.onclick=event=>{
  if (suppressMapClick && event.detail>0) return;
  mapSelection(event);
};
mapViewport.onkeydown=event=>{
  if (event.target.matches('[data-stop]') && ['Enter',' '].includes(event.key)) {
    event.preventDefault();
    mapSelection(event);
  }
};
$('#zoom-in').onclick=()=>{zoom=Math.min(1.8,zoom+.2);updateZoom();};
$('#zoom-out').onclick=()=>{zoom=Math.max(1,zoom-.2);updateZoom();};
$('#reset').onclick=()=>{resetMap();updateZoom();};
$('#add').onclick=()=>editTask();
$('#bell').onclick=()=>toggleNotices($('#notices').hidden);
$('#settings').onclick=()=>openDialog('<div class="panel-head"><h2>设置</h2>'+closeButton+'</div><p>账号身份 · 中央仓调度员</p>');

function history(key) {
  if (!conversations.has(key)) conversations.set(key,[]);
  return conversations.get(key);
}
function renderChat() {
  const key=chatKey();
  const log=$('#messages');
  log.replaceChildren();
  const items=history(key);
  if (!items.length) {
    const greeting=document.createElement('p');
    greeting.className='message';
    greeting.textContent='你好，我是小航。可以问我这趟怎么送，也可以上传订单表让我看看。';
    log.append(greeting);
    const suggestions=document.createElement('div');
    suggestions.className='suggestions';
    for (const question of ['为什么先去第一站？','冷链箱怎么回收？']) {
      const button=document.createElement('button');
      button.textContent=question;
      button.onclick=()=>submitQuestion(question);
      suggestions.append(button);
    }
    log.append(suggestions);
  }
  for (const item of items) {
    const message=document.createElement('p');
    message.className='message '+item.role;
    message.textContent=item.text || '…';
    log.append(message);
    if (item.complete && item.stop!==undefined) {
      const action=document.createElement('button');
      action.className='chat-action';
      action.textContent='查看'+stops[item.stop].name+'的'+(item.stop?'卸货':'装车');
      action.onclick=()=>showStop(item.stop);
      log.append(action);
    }
  }
  log.scrollTop=log.scrollHeight;
  const attachment=$('#attachment');
  attachment.hidden=!attachments.has(key);
  if (!attachment.hidden) {
    attachment.replaceChildren();
    const status=document.createElement('span');
    status.textContent=attachments.get(key).name;
    const remove=document.createElement('button');
    remove.className='icon';
    remove.setAttribute('aria-label','移除附件');
    remove.innerHTML=icon('close');
    remove.onclick=()=>{pendingReads.delete(key);attachments.delete(key);renderChat();};
    attachment.append(status,remove);
    if (attachments.get(key).central) {
      const proof=document.createElement('a');
      proof.className='inline-evidence';
      proof.href='algorithm.html#orders';
      proof.textContent='核对原表 ↗';
      attachment.append(proof);
    }
  }
  $('.send').disabled=pendingReplies.has(key);
}
function toggleAssistant(open) {
  $('#assistant').hidden=!open;
  $('#assistant-toggle').setAttribute('aria-expanded',String(open));
  if (open) {renderChat();$('#question').focus();}
  else $('#assistant-toggle').focus();
}
$('#assistant-toggle').onclick=()=>toggleAssistant($('#assistant').hidden);
$('#assistant .close').onclick=()=>toggleAssistant(false);
$('#attach').onclick=()=>$('#file').click();
$('#file').onchange=async event=>{
  const file=event.target.files[0];
  event.target.value='';
  if (!file) return;
  const key=chatKey();
  const token=Symbol();
  pendingReads.set(key,token);
  attachments.set(key,{name:file.name+' · 读取中'});
  renderChat();
  try {
    const result=await readAttachment(file);
    if (pendingReads.get(key)!==token) return;
    attachments.set(key,result);
    history(key).push({role:'assistant',text:summarizeAttachment(result)+'\n可以继续问我这份文件。'});
  } catch (error) {
    if (pendingReads.get(key)!==token) return;
    attachments.delete(key);
    history(key).push({role:'assistant',text:'读不到《'+file.name+'》。'+(error instanceof SyntaxError?'请检查文件格式。':error.message)});
  } finally {
    if (pendingReads.get(key)===token) pendingReads.delete(key);
    if (key===chatKey()) renderChat();
  }
};
function reply(question, task, attachment) {
  if (attachment && /文件|附件|表|订单|多少|门店/.test(question)) return [summarizeAttachment(attachment,question)];
  if (!task?.stops.length) return ['这项任务还没有配送路线。可以先上传订单表，我帮你查看表头和门店。'];
  const first=task.stops[0];
  const mentioned=task.stops.find(i=>question.includes(stops[i].name));
  if (/冷链|回收|空箱/.test(question)) return ['首站交接冷链药品后，共用冷链箱还要随车，所以它的位置不能腾给回收包装。点开'+stops[first].name+'，切到「卸货后」就能看到。',first];
  if (/先|路线|顺序|怎么送|哪家/.test(question)) return ['这趟从中央仓发车，按 '+task.stops.map(i=>stops[i].name).join(' → ')+' 的顺序配送。先去'+stops[first].name+'，优先满足冷链交接时间。',first];
  if (mentioned) return [stops[mentioned].name+'是第 '+(task.stops.indexOf(mentioned)+1)+' 站。点开地图上的门店，可以对照到店时和卸货后的箱位。',mentioned];
  if (/装|箱|车/.test(question)) return ['这趟由 '+task.vehicle+' 执行。中央仓的箱位把先卸的货放在尾门附近，方便到店交接。',0];
  return [attachment ? summarizeAttachment(attachment,question) : '我可以帮你查看这趟配送的顺序、装卸位置和回收安排。你想先看哪一段？'];
}
function submitQuestion(question) {
  question=question.trim();
  const key=chatKey();
  if (!question || pendingReplies.has(key)) return;
  $('#question').value='';
  const attachment=pendingReads.has(key)?null:attachments.get(key);
  const [text,stop]=reply(question,current(),attachment);
  const item={role:'assistant',text:'',stop};
  history(key).push({role:'user',text:question},item);
  pendingReplies.set(key,item);
  renderChat();
  const chars=Array.from(text);
  let index=0;
  const timer=setInterval(()=>{
    index+=5;
    item.text=chars.slice(0,index).join('');
    if (index>=chars.length) {
      clearInterval(timer);
      item.complete=true;
      pendingReplies.delete(key);
      if (key===chatKey()) {
        document.querySelectorAll('.map-node').forEach(node=>node.classList.toggle('highlight',Number(node.dataset.stop)===stop));
      }
    }
    if (key===chatKey()) renderChat();
  },32);
}
$('#chat').onsubmit=event=>{event.preventDefault();submitQuestion($('#question').value);};
document.addEventListener('click',event=>{
  if (!$('#notices').hidden && !event.target.closest('#bell,#notices')) toggleNotices(false);
});
document.addEventListener('keydown',event=>{
  if (event.key!=='Escape' || dialog.open) return;
  if (!$('#notices').hidden) {toggleNotices(false);$('#bell').focus();}
  else if (!$('#assistant').hidden) toggleAssistant(false);
});
function openLinkedPanel() {
  if (['#upload','#assistant'].includes(location.hash)) toggleAssistant(true);
  if (location.hash==='#notices') toggleNotices(true);
  if (current()?.stops.length && location.hash==='#warehouse') showStop(0);
  if (current()?.stops.length && location.hash==='#recovery') showStop(current().stops[0]);
}
render();
openLinkedPanel();
window.addEventListener('hashchange',openLinkedPanel);
