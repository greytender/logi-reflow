"""Single vehicle failure on the weight/volume normal plan; bounded heuristic.

Cargo stays on its truck until a pickup at the failure point. No real traffic,
driver dispatch, towing or physical packing. Legacy SQLite sessions untouched.
"""
from copy import deepcopy
from time import perf_counter
import engine
import normal

TRANSFER_COST = 15  # synthetic per healthy truck that picks up stranded cargo


def snapshot(plan, time):
    orders={o['id']:o for o in plan['data']['orders']}; states={}
    for v in plan['data']['vehicles']:
        rr=plan['routes'][v['id']]; p=[0,0]; depart=480.; km=0.; completed=[]
        for stop in rr['stops']:
            dest=stop['point']; d=engine.distance(p,dest); travel_end=depart+d/engine.SPEED
            if time<travel_end:
                fraction=max(0,(time-depart)/(d/engine.SPEED)) if d else 0
                pos=[p[j]+fraction*(dest[j]-p[j]) for j in range(2)]
                km+=engine.distance(p,pos); p=pos; break
            km+=d; p=dest
            if time<stop['arrival']: break  # arrived physically, waiting for opening
            # Demo convention: delivery is completed on arrival, service must finish before leaving.
            completed.append(deepcopy(stop)); depart=stop['finish']
            if time<depart: break
        else:
            d=engine.distance(p,[0,0])
            fraction=min(1,max(0,(time-depart)/(d/engine.SPEED))) if d else 1
            pos=[x*(1-fraction) for x in p]; km+=engine.distance(p,pos); p=pos
        done={s['id'] for s in completed}; remaining=[x for x in rr['sequence'] if x not in done]
        w,vol=normal.totals(remaining,orders)
        states[v['id']]=dict(point=p,available=max(time,depart),completed=completed,remaining=remaining,
                             weight_kg=w,volume_m3=vol,traveled=km)
    return states


def future_route(seq,state,v,orders,rescue,pickup,complete=True,*,pickup_minutes=engine.TRANSFER,pickup_cost=TRANSFER_COST):
    expected=set(state['remaining'])|set(rescue)
    delivered=[x for x in seq if x!='P']
    if len(delivered)!=len(set(delivered)) or not set(delivered)<=expected: return None
    if complete and set(delivered)!=expected: return None
    if seq.count('P')!=bool(rescue): return None
    cargo=set(state['remaining']); weight=state['weight_kg']; volume=state['volume_m3']
    t=state['available']; p=state['point']; km=0.; stops=[]; late=[]
    peak_w,peak_v=weight,volume
    for oid in seq:
        dest=pickup if oid=='P' else orders[oid]['point']
        d=engine.distance(p,dest); km+=d; t+=d/engine.SPEED
        if oid=='P':
            w,vol=normal.totals(rescue,orders); weight+=w; volume+=vol; cargo.update(rescue)
            arrival=t; t+=pickup_minutes
        else:
            if oid not in cargo: return None
            o=orders[oid]; t=max(t,o['open']); arrival=t
            if arrival>o['close']+1e-8: return None
            late.append(max(0,arrival-o['due']))
            w,vol=normal.totals([oid],orders); weight-=w; volume-=vol; cargo.remove(oid); t+=engine.SERVICE
        if weight>v['max_weight_kg']+1e-8 or volume>v['max_volume_m3']+1e-8 or min(weight,volume)<-1e-8: return None
        peak_w=max(peak_w,weight); peak_v=max(peak_v,volume)
        stops.append(dict(id=oid,point=dest,arrival=round(arrival,4),finish=round(t,4),
                          weight_kg=round(weight,4),volume_m3=round(volume,6)))
        p=dest
    if complete and cargo: return None
    back=engine.distance(p,[0,0]); km+=back
    return dict(sequence=list(seq),stops=stops,distance=km,return_at=t+back/engine.SPEED,
                peak_weight_kg=peak_w,peak_volume_m3=peak_v,
                late_orders=sum(x>1e-6 for x in late),late_minutes=sum(late),
                extra_cost=km*v['cost_per_km']+(pickup_cost if rescue else 0))


def assignments(plan):
    return {s['id']:(vid,s['arrival'],rr['sequence'][i-1] if i else None)
            for vid,rr in plan['routes'].items() for i,s in enumerate(rr['stops']) if s['id']!='P'}


def local_key(rr,vid,state,original):
    previous=state['completed'][-1]['id'] if state['completed'] else None; changes=0
    for s in rr['stops']:
        if s['id']!='P':
            old=original[s['id']]
            changes+=old[0]!=vid or old[2]!=previous or abs(old[1]-s['arrival'])>1
        previous=s['id']
    return rr['late_orders'],round(rr['late_minutes'],4),round(rr['extra_cost']+4*changes,4)


def optimize_vehicle(state,v,orders,extra,pickup,original):
    if not extra:
        return future_route(state['remaining'],state,v,orders,[],pickup)
    own=state['remaining']; options=[]
    # Include the append baseline so a feasible baseline is never lost by a greedy start.
    appended=future_route(own+['P']+extra,state,v,orders,extra,pickup)
    if appended: options.append(appended)
    for own_order in (own,sorted(own,key=lambda x:(orders[x]['due'],x))):
        for pos in range(len(own_order)+1):
            for extra_order in (extra,sorted(extra,key=lambda x:(orders[x]['due'],x))):
                seq=own_order[:pos]+['P']+own_order[pos:]
                # Partial search paths carry all rescue goods at P; final paths must deliver them all.
                if not future_route(seq,state,v,orders,extra,pickup,False): continue
                for oid in extra_order:
                    best=None
                    for j in range(seq.index('P')+1,len(seq)+1):
                        trial=seq[:j]+[oid]+seq[j:]
                        rr=future_route(trial,state,v,orders,extra,pickup,False)
                        if rr:
                            key=local_key(rr,v['id'],state,original)
                            if best is None or key<best[0]: best=(key,trial)
                    if best is None: break
                    seq=best[1]
                rr=future_route(seq,state,v,orders,extra,pickup)
                if rr: options.append(rr)
    if not options: return None
    best=min(options,key=lambda rr:local_key(rr,v['id'],state,original))
    # Move one stop at a time, including P; every candidate rechecks cargo precedence/capacity.
    for _ in range(4):
        improved=None; key=local_key(best,v['id'],state,original); seq=best['sequence']
        for i,node in enumerate(seq):
            base=seq[:i]+seq[i+1:]
            for pos in range(len(base)+1):
                rr=future_route(base[:pos]+[node]+base[pos:],state,v,orders,extra,pickup)
                if rr:
                    k=local_key(rr,v['id'],state,original)
                    if k<key: improved,key=rr,k
        if improved is None: break
        best=improved
    return best


def assemble(initial,states,futures,failed):
    orders={o['id']:o for o in initial['data']['orders']}; routes={}
    for v in initial['data']['vehicles']:
        vid=v['id']; state=states[vid]; future=futures.get(vid)
        stops=deepcopy(state['completed'])+(deepcopy(future['stops']) if future else [])
        km=state['traveled']+(future['distance'] if future else 0)
        used=bool(initial['routes'][vid]['sequence']) or bool(future and future['sequence'])
        transferred=bool(future and 'P' in future['sequence'])
        routes[vid]=dict(sequence=[s['id'] for s in stops],stops=stops,distance=round(km,4),
            cost=round(km*v['cost_per_km']+(v['fixed_cost'] if used else 0)+(TRANSFER_COST if transferred else 0),4),
            failed=vid==failed,position=state['point'],rescue_ids=[s['id'] for s in (future or {}).get('stops',[]) if s['id'] in states[failed]['remaining']],
            peak_weight_kg=round(future['peak_weight_kg'] if future else state['weight_kg'],4),
            peak_volume_m3=round(future['peak_volume_m3'] if future else state['volume_m3'],6))
    original=assignments(initial); delivered={}; changes=[]; late=[]
    for vid,rr in routes.items():
        prev=None
        for s in rr['stops']:
            oid=s['id']
            if oid!='P':
                delivered[oid]=vid
                old=original[oid]
                if old[0]!=vid or old[2]!=prev or abs(old[1]-s['arrival'])>1: changes.append(oid)
                late.append(max(0,s['arrival']-orders[oid]['due']))
            prev=oid
    missing=sorted(set(orders)-set(delivered)); changed=sorted(set(changes+missing))
    return dict(routes=routes,unserved_ids=missing,metrics=dict(served=len(delivered),unserved=len(missing),
        late_orders=sum(x>1e-6 for x in late),late_minutes=round(sum(late),2),
        cost=round(sum(rr['cost'] for rr in routes.values()),2),distance=round(sum(rr['distance'] for rr in routes.values()),2),
        changed_orders=len(changed),changed_ids=changed),
        status='候选方案，仅供模拟核查，未实际派单' if not missing else '有限搜索未找到完整恢复方案，未安排货物仍在故障车上，需人工处理')


def recover(initial,states,event,method):
    start=perf_counter(); failed=event['vehicle']; rescue=states[failed]['remaining']
    orders={o['id']:o for o in initial['data']['orders']}; original=assignments(initial)
    healthy=[v for v in initial['data']['vehicles'] if v['id']!=failed]; pickup=states[failed]['point']
    total=1<<len(rescue)
    masks=list(range(total)) if total<=64 else sorted({round(i*(total-1)/63) for i in range(64)})
    best=None; key=None
    for mask in masks:
        subsets=[[o for j,o in enumerate(rescue) if ((mask>>j)&1)==k] for k in (0,1)]
        futures={}
        for v,extra in zip(healthy,subsets):
            state=states[v['id']]
            rr=(future_route(state['remaining']+(['P']+extra if extra else []),state,v,orders,extra,pickup)
                if method=='baseline' else optimize_vehicle(state,v,orders,extra,pickup,original))
            if rr is None: break
            futures[v['id']]=rr
        if len(futures)!=2: continue
        result=assemble(initial,states,futures,failed); m=result['metrics']
        k=(m['unserved'],m['late_orders'],m['late_minutes'],m['cost']+4*m['changed_orders'])
        if key is None or k<key: best,key=result,k
    if best is None:
        futures={v['id']:future_route(states[v['id']]['remaining'],states[v['id']],v,orders,[],pickup) for v in healthy}
        best=assemble(initial,states,futures,failed)
    best.update(solve_ms=round((perf_counter()-start)*1000,3),searched_partitions=len(masks),
                total_partitions=total,method='原任务送完后追加接货' if method=='baseline' else '有限货物分配 + 接货插入 + 单节点移位')
    return best


def run(data,message):
    if not isinstance(message,str) or len(message)>200: raise ValueError('异常消息必须是最多200字的字符串')
    parsed=engine.parse_message(message)
    if not parsed['ready']: return dict(parsed=parsed)
    initial=normal.solve(data)
    if initial['metrics']['unserved']: raise ValueError('正常计划还有未安排订单，请先调整货量或车辆容量；本版仅恢复完整初始计划')
    event=parsed['event']; states=snapshot(initial,event['time'])
    return dict(simulated=True,initial=initial,parsed=parsed,event=event,states=states,
                impacted=states[event['vehicle']]['remaining'],baseline=recover(initial,states,event,'baseline'),
                recovered=recover(initial,states,event,'optimized'),
                assumptions=dict(transfer_minutes=engine.TRANSFER,transfer_cost_per_truck=TRANSFER_COST,
                    delivery='到店即视为交付；正常车需完成5分钟服务后离开',
                    transfer='故障现场可安全接货；两辆接货车可独立并行，无排队',
                    costs='出车固定费、实际行驶距离费和接货费；不含拖车、货损、真实人工费用'))
