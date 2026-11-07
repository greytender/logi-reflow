/* Pure route geometry checks; does not replace browser visual inspection. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const context={window:{},document:{currentScript:{src:'http://127.0.0.1/route-map3d.js'}},URL};
vm.runInNewContext(fs.readFileSync('web/route-map3d.js','utf8'),context);
const build=context.window.LogiRouteMap3D.buildLayout;
const input={warehouse:{id:'WH'},orders:[{id:'O1'},{id:'O2'},{id:'O3'}],routes:[{vehicle:{id:'V1'},route:{sequence:['O3','O1','O2']},color:'#315f4c'}]};
const first=build(input),second=build(input);
assert.equal(first.actual,false);
assert.deepEqual(Array.from(first.points),Array.from(second.points));
assert.equal(first.routes[0].legs.length,4);
assert.equal(first.routes[0].legs[0].from,'WH');
assert.equal(first.routes[0].legs[0].to,'O3');
assert.equal(first.routes[0].legs.at(-1).to,'WH');
assert.equal(first.routes[0].legs.at(-1).returning,true);
for(const leg of first.routes[0].legs){
 assert.equal(leg.path[0],first.points.get(leg.from));
 assert.equal(leg.path.at(-1),first.points.get(leg.to));
 for(let i=1;i<leg.path.length;i++)assert(leg.path[i].x===leg.path[i-1].x||leg.path[i].z===leg.path[i-1].z);
}
const geo=build({warehouse:{id:'WH',lat:28.2,lng:112.9},orders:[{id:'O1',lat:28.21,lng:112.92}],routes:[]});
assert.equal(geo.actual,true);
assert.notDeepEqual(geo.points.get('WH'),geo.points.get('O1'));
assert.equal(build({warehouse:{id:'WH'},orders:[],routes:[]}).routes.length,0);
console.log('PASS deterministic layout, exact stop order, depot return, connected road legs, coordinates and empty state');
