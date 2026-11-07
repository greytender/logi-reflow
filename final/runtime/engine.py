"""Deterministic synthetic dispatch demo. No external services or dependencies."""
from copy import deepcopy
from itertools import permutations
from math import hypot
from time import perf_counter
import re

DEPOT = [0, 0]
SPEED = 0.5  # km/min, simulated straight-line travel
SERVICE = 5
TRANSFER = 8

def distance(a, b):
    return hypot(a[0]-b[0], a[1]-b[1])

def scenario():
    points = [(2,1),(4,2),(6,1),(8,3),(-2,2),(-4,3),(-6,2),(-8,4),(1,-2),(2,-4),(1,-6),(3,-8)]
    return dict(simulated=True, depot=DEPOT, vehicles=[dict(id=f'V{i+1}',capacity=12) for i in range(3)],
        orders=[dict(id=f'S{i+1:02}',point=list(p),quantity=2,open=480,due=[510,520,530,545][i%4],close=690) for i,p in enumerate(points)])

def simulate(seq, start, available, load, capacity, orders, rescue=(), pickup=None):
    t, p, km, stops = available, start, 0., []
    cargo = set(orders)-set(rescue)
    for oid in seq:
        if oid == 'P':
            q = pickup
        else:
            if oid not in cargo:
                return None
            q = orders[oid]['point']
        d = distance(p,q); km += d; t += d/SPEED
        if oid == 'P':
            load += sum(orders[o]['quantity'] for o in rescue)
            if load > capacity:
                return None
            cargo.update(rescue)
            arrival = t; t += TRANSFER
        else:
            o = orders[oid]; t = max(t,o['open']); arrival=t
            if arrival > o['close']:
                return None
            load -= o['quantity']; cargo.remove(oid); t += SERVICE
        stops.append(dict(id=oid,point=q,arrival=round(arrival,4),finish=round(t,4),load=load))
        p=q
    km += distance(p,DEPOT)
    return dict(sequence=list(seq),stops=stops,distance=round(km,4),return_at=round(t+distance(p,DEPOT)/SPEED,4))

def initial(data):
    begin=perf_counter(); orders={o['id']:o for o in data['orders']}; routes={}
    # Geographic sectors are a transparent initial assignment; exact route ordering within each sector.
    for i,v in enumerate(data['vehicles']):
        ids=[o['id'] for o in data['orders'][i*4:i*4+4]]
        load=sum(orders[o]['quantity'] for o in ids)
        if load>v['capacity']: raise ValueError('初始车辆容量不足')
        candidates=[simulate(s,DEPOT,480,load,v['capacity'],{o:orders[o] for o in ids}) for s in permutations(ids)]
        valid=[r for r in candidates if r]
        if not valid: raise ValueError('初始方案无可行路线')
        routes[v['id']]=min(valid,key=lambda r:score_route(r,orders))
    return dict(routes=routes,solve_ms=round((perf_counter()-begin)*1000,3))

def score_route(route, orders):
    late=[max(0,s['arrival']-orders[s['id']]['due']) for s in route['stops'] if s['id']!='P']
    return (sum(x>1e-6 for x in late),round(sum(late),4),route['distance'])

def snapshot(plan, data, time):
    states={}; orders={o['id']:o for o in data['orders']}
    for v in data['vehicles']:
        r=plan['routes'][v['id']]; p=DEPOT; depart=480; completed=[]; traveled=0.
        for s in r['stops']:
            if s['arrival']<=time:
                traveled+=distance(p,s['point']); p=s['point']; completed.append(s); depart=s['finish']
                if time<depart: break
            else:
                fraction=max(0,min(1,(time-depart)/(distance(p,s['point'])/SPEED)))
                pos=[p[j]+fraction*(s['point'][j]-p[j]) for j in range(2)]
                traveled+=distance(p,pos); p=pos; break
        else:
            d=distance(p,DEPOT)
            fraction=max(0,min(1,(time-depart)/(d/SPEED))) if d else 1
            pos=[p[j]*(1-fraction) for j in range(2)]; traveled+=distance(p,pos); p=pos
        done={s['id'] for s in completed}; remaining=[o for o in r['sequence'] if o not in done]
        states[v['id']]=dict(point=p,available=max(time,depart),capacity=v['capacity'],completed=completed,remaining=remaining,
            load=sum(orders[o]['quantity'] for o in remaining),traveled=traveled)
    return states

def recover(data,plan,event,method='optimized'):
    begin=perf_counter(); states=snapshot(plan,data,event['time']); failed=event['vehicle']; rescue=states[failed]['remaining']
    orders={o['id']:o for o in data['orders']}; healthy=[v for v in data['vehicles'] if v['id']!=failed]
    candidates=[]
    # Enumerate all splits of stranded cargo; optimize each truck's ordering, respecting pickup precedence.
    for mask in range(1<<len(rescue)):
        subsets=[[o for j,o in enumerate(rescue) if ((mask>>j)&1)==k] for k in (0,1)]
        future={}; valid=True
        for v,extra in zip(healthy,subsets):
            state=states[v['id']]; own=state['remaining']; ids=own+extra
            local={o:orders[o] for o in ids}
            if method=='baseline':
                sequences=[own+(['P']+extra if extra else [])]
            else:
                sequences=permutations(ids+(['P'] if extra else []))
            best=None; key=None
            for seq in sequences:
                if extra and any(seq.index(o)<seq.index('P') for o in extra): continue
                r=simulate(seq,state['point'],state['available'],state['load'],v['capacity'],local,extra,states[failed]['point'])
                if r is None: continue
                newkey=score_route(r,orders)
                # Orders whose vehicle, predecessor or ETA changes count as disturbed.
                old=plan['routes'][v['id']]['sequence']; changes=sum(o not in old or (seq[seq.index(o)-1] if seq.index(o)>0 else None)!=(own[own.index(o)-1] if o in own and own.index(o)>0 else None) for o in ids)
                newkey=(newkey[0],newkey[1],newkey[2]+changes*2)
                if key is None or newkey<key: best,key=r,newkey
            if best is None: valid=False; break
            future[v['id']]=best
        if not valid: continue
        result=assemble(plan,states,future,failed,rescue,orders)
        m=result['metrics']; candidates.append(result)
        if method=='baseline': break  # first feasible split, append rescue after existing work
    if not candidates:
        result=assemble(plan,states,{},failed,rescue,orders)
        result['status']='无可行恢复方案：保留待人工处理订单'
    else:
        result=min(candidates,key=lambda r:(r['metrics']['unserved'],r['metrics']['late_orders'],r['metrics']['late_minutes'],r['metrics']['cost']+r['metrics']['changed_orders']*4))
        result['status']='候选方案，需调度员确认执行'
    result['solve_ms']=round((perf_counter()-begin)*1000,3)
    return result

def assemble(plan,states,future,failed,rescue,orders):
    routes={}; total=0.
    for vid,state in states.items():
        f=future.get(vid)
        if f is None and vid!=failed:
            # No rescue solution: preserve healthy trucks' existing remaining routes.
            local={o:orders[o] for o in state['remaining']}
            f=simulate(state['remaining'],state['point'],state['available'],state['load'],state['capacity'],local)
        stops=deepcopy(state['completed'])+(f['stops'] if f else [])
        km=state['traveled']+(f['distance'] if f else 0)
        routes[vid]=dict(sequence=[s['id'] for s in stops],stops=stops,distance=round(km,4),position=state['point'],failed=vid==failed)
        total+=km
    result=dict(routes=routes,simulated=True)
    result['metrics']=metrics(result,orders,plan)
    return result

def metrics(plan,orders,original=None):
    delivered={}; predecessor={}; km=sum(r['distance'] for r in plan['routes'].values())
    for vid,r in plan['routes'].items():
        prev=None
        for s in r['stops']:
            if s['id']=='P': prev='P'; continue
            delivered[s['id']]=(vid,s['arrival']); predecessor[s['id']]=prev; prev=s['id']
    original_delivered={s['id']:(vid,s['arrival']) for vid,r in (original or plan)['routes'].items() for s in r['stops'] if s['id']!='P'}
    original_prev={o:(r['sequence'][j-1] if j else None) for r in (original or plan)['routes'].values() for j,o in enumerate(r['sequence'])}
    late=[max(0,t-orders[o]['due']) for o,(_,t) in delivered.items()]
    changes=[o for o,v in original_delivered.items() if o not in delivered or delivered[o][0]!=v[0] or predecessor[o]!=original_prev[o] or abs(delivered[o][1]-v[1])>1]
    return dict(late_orders=sum(x>1e-6 for x in late),late_minutes=round(sum(late),2),distance=round(km,2),cost=round(km*2,2),changed_orders=len(changes),changed_ids=changes,unserved=len(orders)-len(delivered))

def parse_message(text):
    """Local rule parser, explicitly NOT an LLM. Missing fields lead to clarification."""
    if not re.search(r'故障|抛锚|坏了',text):
        return dict(ready=False,questions=['首版只支持车辆故障。请说明故障车辆和发生时间。'],parser='规则解析，非大模型')
    vehicles=re.findall(r'(?:V|v|车\s*)(\d+)',text)
    vehicle=re.search(r'(?:V|v|车\s*)([123])(?!\d)',text)
    when=re.search(r'(?<!\d)(\d{1,2})[:：](\d{2})(?!\d)',text)
    questions=[]
    if len(set(vehicles))>1: questions.append('首版每次处理一辆故障车，请明确唯一车辆。')
    if not vehicle: questions.append('是哪辆车？请填写 V1、V2 或 V3。')
    if not when: questions.append('几点发生故障？请填写如 08:25。')
    if when and (int(when[1])>23 or int(when[2])>59): questions.append('时间格式无效，请使用 00:00–23:59。')
    t=int(when[1])*60+int(when[2]) if when else None
    if t is not None and not 480<=t<=540: questions.append('首版演示支持 08:00–09:00 的故障事件。')
    return dict(ready=not questions,questions=questions,parser='规则解析，非大模型',event=dict(type='vehicle_failure',vehicle=f'V{vehicle[1]}' if vehicle else None,time=t))

def run(text='V2 在 08:25 故障'):
    parsed=parse_message(text)
    if not parsed['ready']: return dict(parsed=parsed)
    data=scenario(); plan=initial(data); orders={o['id']:o for o in data['orders']}; plan['metrics']=metrics(plan,orders)
    event=parsed['event']; states=snapshot(plan,data,event['time'])
    baseline=recover(data,plan,event,'baseline'); optimized=recover(data,plan,event)
    impacted=states[event['vehicle']]['remaining']
    return dict(data=data,parsed=parsed,initial=plan,states=states,impacted=impacted,baseline=baseline,recovered=optimized,
        explanation=f"{event['vehicle']} 剩余 {len(impacted)} 单需要接货。已完成订单固定；正常车辆保留车上货物。候选方案安排到故障点接货，每次接货耗时 {TRANSFER} 分钟，并检查沿途载重和最晚收货时间。")
