"""Normal dispatch sandbox: weight/volume constrained insertion + relocation.

Synthetic data and straight-line travel. Deterministic heuristic, not an
optimality proof or a physical packing solver. Independent of legacy sessions.
"""
from copy import deepcopy
from math import isfinite
from time import perf_counter
import engine


def defaults():
    data = engine.scenario()
    data['vehicles'] = [
        dict(id='V1',max_weight_kg=1000,max_volume_m3=4.8,fixed_cost=30,cost_per_km=2),
        dict(id='V2',max_weight_kg=800,max_volume_m3=3.8,fixed_cost=25,cost_per_km=1.8),
        dict(id='V3',max_weight_kg=1200,max_volume_m3=5,fixed_cost=35,cost_per_km=2.2)]
    for o in data['orders']:
        o.update(sku='SKU-'+o['id'],quantity=10,unit_weight_kg=12,unit_volume_m3=.08)
    return data


def numeric(value, label, low=0, high=100000):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not isfinite(value) or not low<=value<=high:
        raise ValueError(label+' 必须是范围内的有限数字')
    return value


def validate(data):
    if not isinstance(data,dict): raise ValueError('数据必须是对象')
    orders,vehicles=data.get('orders'),data.get('vehicles')
    if not isinstance(orders,list) or len(orders)!=12 or not isinstance(vehicles,list) or len(vehicles)!=3:
        raise ValueError('本讲解版固定 12 家门店、3 辆车')
    if any(not isinstance(o,dict) for o in orders+vehicles): raise ValueError('订单和车辆必须是对象')
    if {o.get('id') for o in orders}!={f'S{i:02}' for i in range(1,13)} or {v.get('id') for v in vehicles}!={'V1','V2','V3'}:
        raise ValueError('订单或车辆编号重复或不正确')
    for o in orders:
        q=numeric(o.get('quantity'),o['id']+' 箱数',1,10000)
        if int(q)!=q: raise ValueError('箱数必须是整数')
        numeric(o.get('unit_weight_kg'),'每箱重量',.001,10000)
        numeric(o.get('unit_volume_m3'),'每箱体积',.000001,1000)
        p=o.get('point')
        if not isinstance(p,list) or len(p)!=2: raise ValueError('坐标必须是两个数字')
        for x in p: numeric(x,'模拟坐标',-100,100)
        for k in ('open','due','close'): numeric(o.get(k),'收货时间',480,1440)
        if not o['open']<=o['due']<=o['close']: raise ValueError('需满足开门时间 ≤ 承诺时间 ≤ 最晚收货时间')
    for v in vehicles:
        numeric(v.get('max_weight_kg'),'最大载重',.001,100000)
        numeric(v.get('max_volume_m3'),'最大容积',.000001,1000)
        numeric(v.get('fixed_cost'),'出车费用')
        numeric(v.get('cost_per_km'),'每公里费用')


def totals(seq, orders):
    return (sum(orders[x]['quantity']*orders[x]['unit_weight_kg'] for x in seq),
            sum(orders[x]['quantity']*orders[x]['unit_volume_m3'] for x in seq))


def route(seq,v,orders):
    weight,volume=totals(seq,orders)
    if weight>v['max_weight_kg']+1e-8 or volume>v['max_volume_m3']+1e-8: return None
    t=480.; p=[0,0]; km=0.; stops=[]; late=[]
    for oid in seq:
        o=orders[oid]; d=engine.distance(p,o['point']); km+=d
        t=max(t+d/engine.SPEED,o['open'])
        if t>o['close']+1e-8: return None
        late.append(max(0,t-o['due']))
        stops.append(dict(id=oid,arrival=round(t,4),finish=round(t+engine.SERVICE,4),point=o['point']))
        t+=engine.SERVICE; p=o['point']
    back=engine.distance(p,[0,0]); km+=back
    return dict(sequence=list(seq),stops=stops,weight_kg=round(weight,4),volume_m3=round(volume,6),
                distance=round(km,4),return_at=round(t+back/engine.SPEED,4),
                cost=round(v['fixed_cost']+km*v['cost_per_km'],4) if seq else 0,
                late_orders=sum(x>1e-6 for x in late),late_minutes=round(sum(late),4))


def score(routes):
    return (sum(r['late_orders'] for r in routes.values()),
            round(sum(r['late_minutes'] for r in routes.values()),4),
            round(sum(r['cost'] for r in routes.values()),4))


def solve(data):
    started=perf_counter(); data=deepcopy(data); validate(data)
    orders={o['id']:o for o in data['orders']}; vehicles=data['vehicles']; candidates=[]
    by_due=sorted(orders,key=lambda x:(orders[x]['due'],x))
    by_size=sorted(orders,key=lambda x:(-orders[x]['quantity']*orders[x]['unit_volume_m3'],x))
    # Different insertion orders reduce dependence on one greedy start.
    priorities=[by_due,by_size]+[by_due[i:]+by_due[:i] for i in (3,6,9)]
    for priority in priorities:
        routes={v['id']:route([],v,orders) for v in vehicles}; unserved=[]
        for oid in priority:
            best=None
            for v in vehicles:
                vid=v['id']; old=routes[vid]['sequence']
                for pos in range(len(old)+1):
                    trial=route(old[:pos]+[oid]+old[pos:],v,orders)
                    if trial is None: continue
                    key=score({**routes,vid:trial})
                    if best is None or key<best[0]: best=(key,vid,trial)
            if best is None: unserved.append(oid)
            else: routes[best[1]]=best[2]
        # Bounded local improvement: move one order between/within vehicles.
        for _ in range(8):
            best=None; oldscore=score(routes)
            for source in vehicles:
                sid=source['id']; original=routes[sid]['sequence']
                for oid in original:
                    removed=route([x for x in original if x!=oid],source,orders)
                    for dest in vehicles:
                        did=dest['id']; base=removed['sequence'] if did==sid else routes[did]['sequence']
                        for pos in range(len(base)+1):
                            trial=route(base[:pos]+[oid]+base[pos:],dest,orders)
                            if trial is None: continue
                            proposal={**routes,sid:removed,did:trial}; key=score(proposal)
                            if key<oldscore and (best is None or key<best[0]): best=(key,proposal)
            if best is None: break
            routes=best[1]
        # Recheck remaining orders after relocation frees capacity/time.
        for oid in list(unserved):
            options=[]
            for v in vehicles:
                vid=v['id']; seq=routes[vid]['sequence']
                for pos in range(len(seq)+1):
                    trial=route(seq[:pos]+[oid]+seq[pos:],v,orders)
                    if trial: options.append((score({**routes,vid:trial}),vid,trial))
            if options:
                _,vid,trial=min(options,key=lambda x:x[0]); routes[vid]=trial; unserved.remove(oid)
        candidates.append((len(unserved),score(routes),routes,unserved))
    _,_,routes,unserved=min(candidates,key=lambda x:x[:2])
    rejected=[]
    for oid in sorted(unserved):
        w,vol=totals([oid],orders)
        if not any(w<=v['max_weight_kg']+1e-8 and vol<=v['max_volume_m3']+1e-8 for v in vehicles):
            reason=f'该单 {w:g} kg / {vol:g} m³，没有车辆能单独装下；本版不拆单。'
        elif not any(route([oid],v,orders) for v in vehicles):
            reason='即使单独从仓库出发，也无法在最晚收货时间前到达。'
        else:
            reason='当前有限搜索未找到插入位置；可能受总容量或时间约束限制，不代表已证明无解。'
        rejected.append(dict(id=oid,reason=reason))
    return dict(data=data,routes=routes,unserved=rejected,simulated=True,
                method='多起点插入 + 单订单移位，启发式求解，无全局最优保证',
                metrics=dict(served=12-len(unserved),unserved=len(unserved),late_orders=score(routes)[0],
                    late_minutes=round(score(routes)[1],2),cost=round(score(routes)[2],2),
                    distance=round(sum(r['distance'] for r in routes.values()),2)),
                solve_ms=round((perf_counter()-started)*1000,3))
