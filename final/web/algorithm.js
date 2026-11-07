'use strict';

const evidence = window.dispatchReplay;
const { source, before, after, affected } = evidence;
const $ = id => document.getElementById(id);
const setText = (id, text) => { $(id).textContent = text; };
const num = (value, digits = 1) => value.toLocaleString('zh-CN', { maximumFractionDigits: digits });

function clock(minutes) {
  const seconds = Math.round(minutes * 60);
  return [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60]
    .map(value => String(value).padStart(2, '0')).join(':');
}

setText('title-trips', before.trips);
setText('source-rows', source.rows);
setText('source-stores', source.stores);
setText('source-trips', before.trips);
setText('row-regular', source.first.regular + ' 件');
setText('row-cold', source.first.cold + ' 件');
setText('row-volume', num(source.first.modelCold / source.first.cold, 3) + ' m³');
setText('replay-title', before.vehicle + ' · 一趟车的箱位');
setText('van-size', before.size.join(' × ') + ' cm');

const fill = { regular: '#caa27a', cold_box: '#76adc4',
  recovery_tote: '#86aa77', recovery_carton: '#a6bc87', goods_return: '#b691a3' };
const nodes = [{ store: '出仓', phase: 'departure' }, ...before.stops,
  { store: '回仓', phase: 'return' }];
let selectedStop = 0;
let selectedPhase = 'arrival';

function boxDrawing(frame) {
  const [length, width] = before.size;
  const margin = 31;
  const roof = frame.boxes.slice().sort((a, b) => a.z - b.z);
  const boxes = roof.map(box => {
    const color = fill[box.kind] || fill.regular;
    return '<rect x="' + (margin + box.x) + '" y="' + (18 + box.y) +
      '" width="' + box.dx + '" height="' + box.dy + '" rx="2" fill="' + color +
      '" stroke="#2f594d" stroke-width=".75"><title>' + boxLabel(box) + '</title></rect>';
  }).join('');
  $('box-map').innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' +
    (length + margin * 2) + ' ' + (width + 54) + '" role="img" aria-label="' +
    (frame.store || nodes[selectedStop].store) + '车厢模型俯视图">' +
    '<rect x="' + margin + '" y="18" width="' + length + '" height="' + width +
    '" rx="5" fill="#fcfdf8" stroke="#577b68" stroke-width="2"/>' + boxes +
    '<path d="M' + (margin + length) + ' 23v' + (width - 10) +
    '" stroke="#245741" stroke-width="3" stroke-dasharray="5 3"/>' +
    '<g fill="#294e3d" font-size="13" font-weight="700">' +
    '<text x="' + margin + '" y="' + (width + 45) + '">车头</text><text x="' +
    (margin + length) + '" text-anchor="end" y="' + (width + 45) +
    '">尾门</text></g></svg>';
}

function boxLabel(box) {
  const type = { regular: '常温货', cold_box: '共享冷链箱', recovery_tote: '周转箱',
    recovery_carton: '压平纸箱', goods_return: '退货' };
  return [type[box.kind] || '货物', box.order, box.count ? box.count + ' 件' : '',
    box.dx + ' × ' + box.dy + ' × ' + box.dz + ' cm'].filter(Boolean).join(' · ');
}

function renderFrame() {
  const node = nodes[selectedStop];
  const frame = before.frames.find(item => item.phase === (node.phase || selectedPhase) &&
    (node.phase || item.store === node.store));
  const cold = frame.boxes.filter(box => box.kind === 'cold_box');
  const content = cold.reduce((sum, box) => sum + box.remaining_content_weight_kg, 0);
  const totes = frame.boxes.reduce((sum, box) =>
    sum + (box.kind === 'recovery_tote' ? box.count : 0), 0);
  const weight = frame.boxes.reduce((sum, box) => sum + box.weight_kg, 0);
  const notes = {
    departure: '按候选箱位装车，开始这一趟的逐站复核。',
    arrival: selectedStop === 1 ? '先从尾门取本站的货，再收这家店的包装。' :
      '前面收来的空包装也在车上。先取本站的货，再把回收物装进去。',
    departure_stop: '本站货物已交接，新增回收物随车。共享箱保留原有外形。',
    return: '冷链商品已交完。' + cold.length + ' 个共享箱和沿途收回的包装一起回仓。'
  };
  setText('stop-name', node.store);
  setText('frame-time', clock(frame.time));
  setText('frame-note', notes[frame.phase]);
  $('phase').hidden = Boolean(node.phase);
  $('stops').querySelectorAll('button').forEach((button, i) =>
    button.setAttribute('aria-pressed', String(i === selectedStop)));
  $('phase').querySelectorAll('button').forEach(button =>
    button.setAttribute('aria-pressed', String(button.dataset.phase === selectedPhase)));
  $('frame-metrics').innerHTML = [
    ['冷链箱／箱内货重', cold.length + ' 个／' + num(content) + ' kg'],
    ['已收周转箱', totes + ' 个'],
    ['在车总重', num(weight) + ' kg']
  ].map(([label, value]) => '<div><dt>' + label + '</dt><dd>' + value + '</dd></div>').join('');
  boxDrawing(frame);
}

nodes.forEach((node, index) => {
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = node.store;
  button.addEventListener('click', () => {
    selectedStop = index;
    selectedPhase = 'arrival';
    renderFrame();
  });
  $('stops').append(button);
});
$('phase').addEventListener('click', event => {
  const button = event.target.closest('[data-phase]');
  if (button) {
    selectedPhase = button.dataset.phase;
    renderFrame();
  }
});
renderFrame();

const sameSequence = before.stops.map(stop => stop.store).join() ===
  after.stops.map(stop => stop.store).join();
const sameFleetTotals = before.allKm === after.allKm && before.allCost === after.allCost;
const states = {
  before: ['方案 ' + before.version, '原输入',
    affected.store + ' 预计回收 ' + affected.before.totes + ' 个周转箱，由' + before.vehicle + '负责。'],
  preview: ['先把影响圈出来', '预览 · 原方案仍在',
    affected.store + ' 的周转箱从 ' + affected.before.totes + ' 个改为 ' + affected.after.totes +
    ' 个，原计划涉及' + before.vehicle + '。此时只查看变更。'],
  after: ['方案 ' + after.version, '独立核验通过 · 待调度员核定',
    after.served + ' 家门店，' + after.trips + ' 趟车。原方案留存。' +
    (sameSequence ? before.vehicle + '的站序保持一致，' : '更新后的站序已随方案保存，') +
    affected.store + ' 离店时按 ' + after.focus.toteHeight + ' cm 留回收箱位，' +
    '预计离店 ' + clock(after.focus.finish) + '。']
};

function renderRevision(key) {
  const [title, state, copy] = states[key];
  $('revision-card').innerHTML = '<h3>' + title + '</h3><p class="revision-state">' +
    state + '</p><p>' + copy + '</p>';
  document.querySelectorAll('[data-revision]').forEach(button =>
    button.setAttribute('aria-pressed', String(button.dataset.revision === key)));
}
document.querySelectorAll('[data-revision]').forEach(button =>
  button.addEventListener('click', () => renderRevision(button.dataset.revision)));
renderRevision('before');

function toteDrawing() {
  const ground = 117;
  const boxes = [before.focus, after.focus].map((focus, i) => {
    const x = 54 + i * 174;
    const height = focus.toteHeight * 2.7;
    const y = ground - height;
    const rims = Array.from({ length: focus.toteCount }, (_, j) =>
      '<path d="M' + (x - 3) + ' ' + (y + 2 + j * 13.5) + 'h90" stroke="#476d45" stroke-width="3"/>').join('');
    return '<rect x="' + x + '" y="' + y + '" width="84" height="' + height +
      '" rx="5" fill="#b7cda7" stroke="#5b7f58"/>' + rims +
      '<text x="' + (x + 42) + '" y="' + (y - 8) + '">' + focus.toteHeight +
      ' cm</text><text x="' + (x + 42) + '" y="142">' + focus.toteCount + ' 个</text>';
  }).join('');
  $('tote-map').innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 368 148" ' +
    'role="img" aria-label="一个箱高 ' + before.focus.toteHeight + ' 厘米，两个套叠后高 ' +
    after.focus.toteHeight + ' 厘米"><path d="M26 118h316" stroke="#b8cab9"/>' +
    '<g text-anchor="middle" fill="#31523e" font-size="15" font-weight="700">' + boxes + '</g></svg>';
  setText('change-result', sameFleetTotals ?
    '这次里程和试算费用一样。变化发生在门店 1 离店后的车厢里。' :
    '回收占位、全批里程和试算费用都按新方案重新核对。');
}
toteDrawing();

$('download').addEventListener('click', () => {
  const json = new Blob([JSON.stringify(evidence, null, 2)], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(json);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = '城配续航-中央仓复算依据.json';
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});