const icons = {
  attach:'<path d="m8 13 6-6a2 2 0 0 1 3 3l-8 8a4 4 0 0 1-6-6l9-9a5 5 0 0 1 7 7l-8 8"/>',
  brand:'<path d="m4 7 8-5 8 5v10l-8 5-8-5V7Zm0 0 8 5 8-5m-8 5v10"/>',
  bell:'<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9m-8 12a2 2 0 0 0 4 0"/>',
  plus:'<path d="M12 5v14M5 12h14"/>', minus:'<path d="M5 12h14"/>',
  user:'<circle cx="12" cy="8" r="3"/><path d="M5 20v-2a7 5 0 0 1 14 0v2"/>',
  settings:'<path d="M10 3h4l1 3 3 1 3 3v4l-3 3-3 1-1 3h-4l-1-3-3-1-3-3v-4l3-3 3-1z"/><circle cx="12" cy="12" r="3"/>',
  focus:'<path d="M4 9V4h5m6 0h5v5m0 6v5h-5m-6 0H4v-5"/><circle cx="12" cy="12" r="2"/>',
  spark:'<path d="M12 2c0 6 4 10 10 10-6 0-10 4-10 10 0-6-4-10-10-10 6 0 10-4 10-10Z"/>',
  close:'<path d="m6 6 12 12M18 6 6 18"/>',
  mic:'<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0m-7 7v3"/>',
  send:'<path d="M12 19V5m-6 6 6-6 6 6"/>',
  task:'<rect x="6" y="5" width="12" height="16" rx="2"/><path d="M9 3h6v4H9zm0 9h6m-6 4h4"/>',
  edit:'<path d="m4 16 12-12 4 4L8 20H4zM14 6l4 4"/>'
};
function icon(name) {
  return '<svg class="icon-art" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+icons[name]+'</svg>';
}

const stops = [
  {name:'中央仓库', x:264, y:458},
  {name:'湘雅路店', x:465, y:158},
  {name:'芙蓉中路店', x:725, y:313},
  {name:'雨花亭店', x:805, y:524}
];
const legs = [
  'M264 458H396Q465 458 465 405V158',
  'M465 158H619Q655 158 655 198V275Q655 313 693 313H725',
  'M725 313H806Q845 313 845 353V486Q845 524 805 524',
  'M805 524H573Q537 524 517 550H375Q337 550 325 506L310 478Q297 458 264 458'
];
const streets = [
  'M-40 213H207Q264 213 282 175L320 95Q334 66 386 66H1040',
  'M312 -20V160Q312 209 354 240L395 273Q428 298 428 341V670',
  'M-40 378H230Q266 378 293 356L332 326Q350 313 394 313H1040',
  'M465 -40V644','M655 -40V644','M845 -40V644',
  'M-40 524H1040','M-40 458H396Q465 458 465 405',
  'M465 158H1040','M557 -20V244Q557 275 586 294L630 322Q655 339 655 378',
  'M-40 285H222Q247 285 257 262L278 214',
  'M164 380V660','M937 -40V670',
  'M465 158H619Q655 158 655 198V275Q655 313 693 313H806Q845 313 845 353V486Q845 524 805 524',
  legs[3]
];
const buildings = [
  [364,105,55,26],[365,194,56,27],[487,83,51,50],[582,86,46,39],[689,90,88,37],[863,90,53,44],
  [492,183,45,29],[494,224,29,31],[574,184,51,42],[681,190,54,37],[755,186,65,29],[751,233,59,45],[871,205,40,58],
  [341,265,34,19],[187,312,60,23],[204,393,38,38],[320,391,69,40],[489,339,63,46],[570,355,51,51],
  [679,351,52,28],[733,391,62,46],[685,459,73,32],[867,359,48,35],[883,433,34,58],[478,469,37,28],
  [193,555,61,39],[459,574,41,42],[586,557,46,34],[695,560,78,30],[864,559,53,43]
];
function tree(x,y,size) {
  return '<g transform="translate('+x+' '+y+') scale('+size+')"><ellipse cy="5" rx="10" ry="5" fill="#afcdb0"/><path d="M0 0v6" stroke="#7f9f80" stroke-width="2"/><circle cx="-3" cy="-4" r="7" fill="#8eb895"/><circle cx="4" cy="-6" r="6" fill="#a7cba4"/></g>';
}
function building(x,y,w,h) {
  return '<g transform="translate('+x+' '+y+')"><rect x="5" y="6" width="'+w+'" height="'+h+'" rx="5" fill="#b4c8bb"/><rect width="'+w+'" height="'+h+'" rx="5" fill="#e8eee5" stroke="#c7d8cb"/><path d="M7 7h'+(w-14)+'" stroke="#f9fcf6" stroke-width="2"/></g>';
}
function marker(stop,index) {
  const warehouse = index === 0;
  return '<g class="map-node" role="button" tabindex="0" aria-label="'+stop.name+'" data-stop="'+index+'" transform="translate('+stop.x+' '+stop.y+')"><circle r="35" fill="transparent"/><circle class="ring" r="'+(warehouse?29:22)+'" fill="#f7fbef" fill-opacity=".8" stroke="#9ebfa7" stroke-width="3"/><g class="marker"><path d="M0 8-7-2H7Z" fill="'+(warehouse?'#235c47':'#4a8066')+'"/><rect class="pin" x="-'+(warehouse?24:19)+'" y="-'+(warehouse?49:42)+'" width="'+(warehouse?48:38)+'" height="'+(warehouse?44:38)+'" rx="12" fill="'+(warehouse?'#235c47':'#4a8066')+'" stroke="#f5fbf1" stroke-width="2"/><g transform="translate(-12 -36)" fill="none" stroke="#fff" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'+(warehouse?'<path d="M3 20V9l9-5 9 5v11H3zm5 0v-8h8v8m-8-4h8"/>':'<path d="M4 11v8h16v-8M3 10l2-5h14l2 5v1H3zm7 9v-5h4v5"/>')+'</g></g></g>';
}
function car(x,y,angle,color) {
  return '<g transform="translate('+x+' '+y+') rotate('+angle+')" pointer-events="none"><rect x="-13" y="-6" width="26" height="14" rx="4" fill="#adc5b8"/><rect x="-13" y="-8" width="26" height="13" rx="4" fill="'+color+'"/><path d="M4-6h4v9H4z" fill="#e6f1e9"/></g>';
}
function drawMap(task) {
  const active = task ? task.stops : [];
  const route = active.length ? legs.slice(0,active.length) : [];
  if (active.length === 3) route.push(legs[3]);
  return '<svg viewBox="0 0 1000 620" preserveAspectRatio="xMidYMid meet" aria-label="配送路网"><defs><pattern id="grid" width="22" height="22" patternUnits="userSpaceOnUse"><path d="M22 0H0v22" fill="none" stroke="#bed6c3" stroke-width=".6" opacity=".55"/></pattern></defs>'+
    '<rect width="1000" height="620" fill="#e1ede1"/><rect width="1000" height="620" fill="url(#grid)"/>'+
    '<path d="M92-40C210 105 97 169 123 257S244 352 137 452 82 599 134 670" fill="none" stroke="#c5dedc" stroke-width="92"/><path d="M92-40C210 105 97 169 123 257S244 352 137 452 82 599 134 670" fill="none" stroke="#b6d7d6" stroke-width="63"/>'+
    '<path d="M13 30Q55 18 84 59T62 181Q24 205 0 160V40ZM213 42Q269 15 296 51L268 115Q252 148 207 126Z M874 16H992V67H867ZM326 556H411V620H306ZM686 217Q725 217 728 260L726 291H681Z" fill="#cbe1c5"/>'+
    Array.from({length:28},(_,i)=>tree(21+(i*131)%960,32+(i*83)%559,.55+(i%4)*.11)).join('')+
    buildings.map(p=>building(...p)).join('')+
    '<g fill="none" stroke-linecap="round" stroke-linejoin="round">'+streets.map(d=>'<path d="'+d+'" stroke="#c8dbcc" stroke-width="17"/>').join('')+streets.map(d=>'<path d="'+d+'" stroke="#f8fbf4" stroke-width="12"/>').join('')+
    route.map((d,i)=>'<path d="'+d+'" stroke="#fff" stroke-width="9"/><path d="'+d+'" stroke="'+(i===0?'#548c78':'#6da080')+'" stroke-width="5" '+(i===3?'stroke-dasharray="8 8"':'')+'/>').join('')+'</g>'+
    car(360,458,0,'#6e9c83')+car(655,243,90,'#6f9ba7')+car(886,524,0,'#d1a17b')+car(428,382,90,'#91ab89')+
    (task ? [0,...active].map(i=>marker(stops[i],i)).join('') : '')+'</svg>';
}

function loadingPayload(task,index,before,after) {
  const cold={id:'cold',kind:'cold_box',order:1,x:25,y:110,z:0,dx:90,dy:75,dz:80,weight_kg:24,remaining_content_weight_kg:12,temperature_range:[2,8]};
  const emptyCold={...cold,weight_kg:12,remaining_content_weight_kg:0};
  const cargo=task.stops.flatMap((stop,i)=>Array.from({length:3},(_,j)=>({
    id:'cargo-'+stop+'-'+j,kind:'regular',order:stop,x:317-i*79,y:8+j*65,z:0,dx:73,dy:59,dz:54,weight_kg:12
  })));
  const returns=task.stops.map((stop,i)=>({
    id:'return-'+stop,kind:'recovery_tote',order:stop,x:330-i*79,y:8,z:0,dx:55,dy:49,dz:36,weight_kg:4
  }));
  const all=[...cargo,cold];
  const stage=task.stops.indexOf(index);
  const arrived=[...cargo.filter(box=>task.stops.indexOf(box.order)>=stage),stage>0?emptyCold:cold,...returns.slice(0,stage)];
  const departed=[...arrived.filter(box=>box.kind!=='cold_box' && (box.kind==='recovery_tote' || box.order!==index)),emptyCold,returns[stage]];
  const frames=index ? [
    {phase:'arrival',stop_id:index,label:before,boxes:arrived},
    {phase:'unload',stop_id:index,label:after,boxes:departed,action:{kind:'unload',box_ids:cargo.filter(box=>box.order===index).map(box=>box.id)}}
  ] : [
    {phase:'load',label:before,boxes:[]},
    {phase:'departure',label:after,boxes:all,action:{kind:'load',box_ids:all.map(box=>box.id)}}
  ];
  return {
    display_names:{vehicles:{truck:task.vehicle},orders:Object.fromEntries(task.stops.map(i=>[i,stops[i].name]))},
    routes:[{vehicle_id:'truck',vehicle:{length_cm:420,width_cm:205,height_cm:200,max_weight_kg:1500},
      boxes:all,frames:frames.map((frame,i)=>({index:i,...frame}))}]
  };
}

function loadingScene(stage, after, total) {
  const delivered = stage ? stage-Number(!after) : 0;
  const loaded = stage || after;
  const point = (x,y,z=0) => [98+x*1.02+y*.59, 157+x*.23-y*.61-z];
  const face = (points,color) => '<polygon points="'+points.map(p=>point(...p).join(',')).join(' ')+'" fill="'+color+'" stroke="#254b40" stroke-opacity=".23" stroke-width=".8"/>';
  function box(x,y,w,d,h,colors) {
    return face([[x,y],[x+w,y],[x+w,y,h],[x,y,h]],colors[1])+
      face([[x+w,y],[x+w,y+d],[x+w,y+d,h],[x+w,y,h]],colors[2])+
      face([[x,y,h],[x+w,y,h],[x+w,y+d,h],[x,y+d,h]],colors[0]);
  }
  const body = ['#f9fbf7','#c1d8cc','#a3c0b3'];
  const freight = ['#e8cb9f','#c4a074','#ad875d'];
  const cold = ['#a7d0dd','#78aabd','#5e93a7'];
  const returns = ['#bbd9a8','#8db184','#73976d'];
  const wheel = (x,y) => {const [cx,cy]=point(x,y);return '<ellipse cx="'+cx+'" cy="'+cy+'" rx="11" ry="14" fill="#38554f"/><ellipse cx="'+cx+'" cy="'+cy+'" rx="5" ry="7" fill="#b5c9bd"/>';};
  return '<svg viewBox="0 0 490 245" role="img" aria-label="货车车厢及箱位的等轴测示意"><ellipse cx="254" cy="181" rx="165" ry="36" fill="#d7e6d9"/>'+
    wheel(52,-2)+wheel(238,-2)+wheel(211,-2)+
    box(0,0,62,96,66,['#83ac95','#4f7d69','#6b967e'])+
    face([[7,0,39],[38,0,39],[38,0,59],[7,0,59]],'#d0e6e5')+
    box(62,0,221,96,9,body)+
    face([[62,94,9],[283,94,9],[283,94,75],[62,94,75]],'#cfdfd4')+
    face([[62,0,9],[62,94,9],[62,94,75],[62,0,75]],'#e1ede5')+
    (loaded ? box(83,58,46,30,35,cold):'')+
    (loaded && total>=3 && delivered<3 ? box(140,56,37,32,31,freight):'')+
    (loaded && total>=2 && delivered<2 ? box(179,51,44,34,33,freight):'')+
    (loaded && delivered<1 ? box(228,53,42,36,34,freight)+box(235,12,38,30,29,freight):'')+
    (delivered>0 ? box(226,12,47,30,20+5*(delivered-1),returns):'')+
    face([[283,0,9],[311,0,9],[311,72,9],[283,72,9]],'#d5e6dc')+'</svg>';
}
