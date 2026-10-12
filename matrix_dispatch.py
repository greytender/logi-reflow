"""CSV dispatch execution using directed legs, never synthetic coordinates.

An in-flight leg is committed to its destination (no mid-edge road network).
Manual receipts alone release store cargo. All input files use pilot's parser.
"""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
import normal
import pilot

MODEL = 'directed_csv_v1'
FILES = ('orders.csv', 'vehicles.csv', 'travel.csv', 'settings.json')


def load_files(files):
    if not isinstance(files, dict) or set(files) != set(FILES):
        raise ValueError('请选择orders.csv、vehicles.csv、travel.csv、settings.json四份文件')
    if any(not isinstance(v, str) for v in files.values()):
        raise ValueError('导入文件必须为UTF-8文本')
    if sum(len(v.encode('utf-8')) for v in files.values()) > 2_000_000:
        raise ValueError('四份文件合计不能超过2MB')
    with TemporaryDirectory() as tmp:
        for name in FILES:
            (Path(tmp)/name).write_text(files[name], encoding='utf-8')
        data = pilot.load_bundle(tmp)
    if any(o['id'] == 'W' for o in data['orders']):
        raise ValueError('连续流程保留W为装货节点，请在脱敏映射中更换该门店编号')
    data['dispatch_model'] = MODEL
    return data


def start(files, loading_profile=None):
    data = load_files(files)
    if loading_profile is None:
        plan = pilot.compare(data)['optimized']
    else:
        import loading
        import fixed_loading
        result = loading.solve(files, loading_profile)
        if not result['complete']:raise ValueError('联合装载方案不完整，不能创建固定装载场景')
        plan = deepcopy(result['optimized'])
        plan['fixed_loading'] = fixed_loading.freeze(result)
    plan['data']=data
    plan['unserved_ids']=[o['id'] for o in plan['unserved']]
    plan['progress_mode'] = 'manual_receipts'
    depot = data['settings']['depot_id']
    orders = {o['id']: o for o in data['orders']}
    for v in data['vehicles']:
        r = plan['routes'][v['id']]; cargo = r['sequence'][:]
        w, vol = normal.totals(cargo, orders)
        r['anchor'] = dict(node=depot, available=v['shift_start'], traveled=0,
            cargo=cargo, completed=[], remaining=cargo[:], weight_kg=w, volume_m3=vol,
            pending=None, loading=None, transit=None, progress_mode='manual_receipts')
    return plan


def is_pickup(oid):
    # ':' is excluded by the CSV identifier contract, avoiding order collisions.
    return oid == 'W' or oid.startswith('warehouse:')


def location(oid, data, orders):
    return data['settings']['depot_id'] if is_pickup(oid) else orders[oid].get('store', oid)


def leg(data, a, b):
    return dict(distance_km=0, travel_minutes=0) if a == b else data['legs'][a+'>'+b]


def pickup_id(stop):
    """Old W records are interpreted in memory, never migrated on read."""
    return stop.get('task_id', 'warehouse:'+','.join(stop['load_ids']))


def loading_matches(loading, stop):
    if not loading:
        return True
    if abs(loading['finish']-loading['arrival']-8) >= 1e-8:
        return False
    if 'task_id' not in loading and 'load_ids' not in loading:
        return 'task_id' not in stop  # Only the old anonymous W may resume.
    return (loading.get('task_id') == pickup_id(stop)
            and loading.get('load_ids') == stop['load_ids'])


def validate_pickups(plan):
    """A warehouse lot has one pickup owner; original goods cannot be loaded again."""
    orders = {o['id']: o for o in plan['data']['orders']}
    tasks = set(); lots = {}; owners = {}
    for vid, route in plan['routes'].items():
        ids=[s['id'] for s in route['stops']]
        if route['sequence'] != ids or len(ids)!=len(set(ids)):
            raise ValueError('路线任务引用重复或与节点不一致')
        if [s['id'] for s in route['anchor']['completed']] != ids[:len(route['anchor']['completed'])]:
            raise ValueError('已完成任务必须保留为路线历史前缀')
        for i, stop in enumerate(route['stops']):
            if not is_pickup(stop['id']):
                if stop['id'] in owners: raise ValueError('订单重复安排')
                owners[stop['id']] = (vid, i)
                continue
            load = stop['load_ids']; task = pickup_id(stop)
            if stop['id'] != 'W' and stop['id'] != task:
                raise ValueError('路线取货引用必须匹配任务标识')
            if not load or len(load) != len(set(load)) or task in tasks:
                raise ValueError('取货任务或货单重复')
            tasks.add(task)
            for oid in load:
                if oid not in orders or not orders[oid].get('store') or oid in lots:
                    raise ValueError('仓库取货只能包含尚未重复取货的新订单')
                lots[oid] = (vid, i)
        q = route['anchor']
        if q.get('loading'):
            remaining = route['stops'][len(q['completed']):]
            if not remaining or not is_pickup(remaining[0]['id']) or not loading_matches(q['loading'], remaining[0]):
                raise ValueError('装货中的取货任务或货单不能更换')
        for oid in q['cargo']:
            if orders[oid].get('store') and not any(is_pickup(s['id']) and oid in s['load_ids'] for s in q['completed']):
                raise ValueError('新货必须完成本车取货后才能归车')
    for oid, delivery in owners.items():
        if orders[oid].get('store'):
            pickup = lots.get(oid)
            if not pickup or pickup[0] != delivery[0] or pickup[1] >= delivery[1]:
                raise ValueError('新货必须由取货车辆在取货后配送')


def snapshot(plan, now):
    validate_pickups(plan)
    data = plan['data']; orders = {o['id']: o for o in data['orders']}; result = {}
    depot = data['settings']['depot_id']
    for vid, route in plan['routes'].items():
        q = deepcopy(route['anchor']); t = q['available']
        # A late-starting vehicle must not arrive along a zero-time leg early.
        if not q.get('pending') and now >= t:
            remaining = route['stops'][len(q['completed']):]
            for stop in remaining + [None]:
                if stop is not None: q.pop('returned_at',None)
                dest = location(stop['id'], data, orders) if stop else depot
                moving = q.get('transit')
                road = moving if moving else leg(data, q['node'], dest)
                # A return leg already in flight must finish before any new trip.
                if moving: dest = moving['to']
                duration = road['travel_minutes']; distance = road['distance_km']
                elapsed = max(0, now-t)
                if elapsed+1e-8 < duration:
                    fraction = elapsed/duration
                    q['traveled'] += distance*fraction
                    q['transit'] = dict(to=dest, from_node=q['node'],
                        travel_minutes=duration-elapsed, distance_km=distance*(1-fraction))
                    t = now
                    break
                q['traveled'] += distance; t += duration; q['node'] = dest; q['transit'] = None
                if stop is None:
                    if not q['completed'] and not q['cargo'] and q['traveled']==0:
                        q['returned_at']=next(v['shift_start'] for v in data['vehicles'] if v['id']==vid)
                    elif 'returned_at' not in q: q['returned_at']=t
                    break
                expected = location(stop['id'], data, orders)
                if dest != expected:
                    # Only a committed return to the depot can precede a new stop.
                    road = leg(data, dest, expected); duration = road['travel_minutes']
                    elapsed = max(0, now-t)
                    if elapsed+1e-8 < duration:
                        fraction = elapsed/duration
                        q['traveled'] += road['distance_km']*fraction
                        q['transit'] = dict(to=expected, from_node=dest,
                            travel_minutes=duration-elapsed, distance_km=road['distance_km']*(1-fraction))
                        t = now; break
                    q['traveled'] += road['distance_km']; t += duration; q['node'] = expected
                if not is_pickup(stop['id']):
                    q['pending'] = dict(id=stop['id'], status='awaiting_arrival', earliest_arrival=t)
                    break
                loading = q.get('loading') or dict(arrival=t, finish=t+8)
                loading.update(task_id=pickup_id(stop), load_ids=stop['load_ids'][:])
                if now+1e-8 < loading['finish']:
                    q['loading'] = loading; break
                done = deepcopy(stop); done.update(loading)
                q['completed'].append(done); q['cargo'] = sorted(set(q['cargo']) | set(stop['load_ids']))
                t = loading['finish']; q['loading'] = None
        q['available'] = max(now, t)
        q['remaining'] = route['sequence'][len(q['completed']):]
        q['weight_kg'], q['volume_m3'] = normal.totals(q['cargo'], orders)
        result[vid] = q
    return result


def future(seq, state, vehicle, orders, previous, data):
    if len(seq) != len(set(seq)): return None
    if set(seq) & {s['id'] for s in state['completed']}: return None
    pending = state.get('pending'); moving = state.get('transit'); loading = state.get('loading')
    if pending and (not seq or seq[0] != pending['id']): return None
    if loading:
        first = next((s for s in previous['stops'] if seq and s['id']==seq[0]), None)
        if not first or not is_pickup(first['id']) or not loading_matches(loading, first): return None
    depot = data['settings']['depot_id']
    if moving and moving['to'] != depot and (not seq or location(seq[0], data, orders) != moving['to']): return None
    fixed_check = None
    if 'fixed_loading' in previous:
        import fixed_loading
        fixed_check = fixed_loading.check(previous['fixed_loading'], vehicle['id'],
                                          state['cargo'], state['completed'], seq)
        if not fixed_check['passed']:return None
    cargo = set(state['cargo']); node = state['node']; t = state['available']; km = 0; stops = []
    if not seq and node==depot and not moving:
        t=vehicle['shift_start'] if not state['completed'] and not cargo else state.get('returned_at',t)
    peak_w, peak_v = normal.totals(cargo, orders)
    if peak_w > vehicle['max_weight_kg']+1e-8 or peak_v > vehicle['max_volume_m3']+1e-8: return None
    if moving:
        t += moving['travel_minutes']; km += moving['distance_km']; node = moving['to']
    for oid in seq:
        dest = location(oid, data, orders); road = leg(data, node, dest)
        t += road['travel_minutes']; km += road['distance_km']; road_arrival = t
        if is_pickup(oid):
            old = next((s for s in previous['stops'] if s['id'] == oid), None)
            if old is None: return None
            load = old['load_ids']
            if not loading_matches(loading, old): return None
            if set(load) & cargo: return None
            arrival = loading['arrival'] if loading else t
            t = loading['finish'] if loading else t+8
            cargo.update(load)
            loading = None  # A later pickup gets its own eight minutes.
        else:
            if oid not in cargo or vehicle['id'] not in orders[oid]['allowed_vehicles']: return None
            o = orders[oid]; service = o['service_minutes']; arrival = max(t, o['open'])
            if pending and pending['id'] == oid and pending['status'] in ('arrived', 'unloading'):
                arrival = pending['arrived_at']
                t = max(t, pending['unloading_at']+service) if 'unloading_at' in pending else max(t, o['open'])+service
            else: t = arrival+service
            deadline = t if data['settings']['window_rule'] == 'finish' else arrival
            if deadline > o['close']+1e-8: return None
            cargo.remove(oid)
        w, vol = normal.totals(cargo, orders); peak_w = max(peak_w, w); peak_v = max(peak_v, vol)
        if peak_w > vehicle['max_weight_kg']+1e-8 or peak_v > vehicle['max_volume_m3']+1e-8: return None
        stop = dict(id=oid, road_arrival=road_arrival, arrival=arrival, finish=t, weight_kg=w, volume_m3=vol)
        if is_pickup(oid):
            stop['load_ids'] = load[:]
            if 'task_id' in old: stop['task_id'] = old['task_id']
        stops.append(stop); node = dest
    back = leg(data, node, depot); km += back['distance_km']; t += back['travel_minutes']
    if t > vehicle['shift_end']+1e-8: return None
    result = dict(stops=stops, distance=km, return_at=t, peak_weight_kg=peak_w,
        peak_volume_m3=peak_v, retained_ids=sorted(cargo))
    if fixed_check is not None:result['fixed_loading_check']=fixed_check
    return result


def window_event(base, event, now):
    if set(event) != {'time','store','open','due','close'}: raise ValueError('收货变化需要时间、订单编号及开门/承诺/截止')
    normal.numeric(event['time'], '事件时间', now, 1440)
    if event['store'] not in {o['id'] for o in base['data']['orders']}: raise ValueError('订单不存在')
    for key in ('open','due','close'): normal.numeric(event[key], '收货时间', 0, 1440)
    if not event['open'] <= event['due'] <= event['close']: raise ValueError('需满足开门 ≤ 承诺 ≤ 截止')
    states=snapshot(base,event['time'])
    if any(s['id']==event['store'] for q in states.values() for s in q['completed']):
        raise ValueError('该订单已交付，不能改写历史')
    for q in states.values():
        pending=q.get('pending')
        if pending and pending['id']==event['store'] and pending.get('unloading_at') is not None and event['open']>pending['unloading_at']:
            raise ValueError('已开始卸货，不能把开放时间改到已发生的卸货之后；需人工核查')


def validate_urgent_event(base, event, now):
    """Validate fields without searching routes; shared by forms and CSV AI."""
    if set(event) != {'time','store','due','close','lines'}:
        raise ValueError('导入紧急单需要发生时间、门店、承诺、截止及明确SKU明细')
    normal.numeric(event['time'], '事件时间', now, 1440)
    data=deepcopy(base['data'])
    if sum(bool(o.get('store')) for o in data['orders']) >= 2:
        raise ValueError('当前CSV最小闭环最多两个仓库紧急单')
    occupied={o['id'] for o in data['orders']}
    oid=next(f'E{i:02d}' for i in range(1,len(occupied)+2) if f'E{i:02d}' not in occupied)
    target=next((o for o in data['orders'] if o['id']==event['store'] and not o.get('store')),None)
    if target is None: raise ValueError('目的地必须为已导入门店')
    for key in ('due','close'): normal.numeric(event[key], '紧急单时间', 0, 1440)
    opening=max(event['time'],target['open'])
    if not opening<=event['due']<=event['close']: raise ValueError('需满足事件及开门 ≤ 承诺 ≤ 截止')
    lines=event['lines']
    if not isinstance(lines,list) or not 1<=len(lines)<=100: raise ValueError('紧急单需要1–100行SKU')
    seen=set()
    for row in lines:
        if not isinstance(row,dict) or set(row)!={'sku','quantity','unit_weight_kg','unit_volume_m3'}: raise ValueError('SKU明细字段不完整')
        sku=pilot.identifier(row['sku'],'SKU')
        if sku in seen: raise ValueError('SKU重复，请先汇总')
        seen.add(sku)
        q=normal.numeric(row['quantity'],'箱数',1,10000)
        if int(q)!=q: raise ValueError('箱数必须为整数')
        normal.numeric(row['unit_weight_kg'],'箱重',.000001,10000)
        normal.numeric(row['unit_volume_m3'],'箱体积',.000001,1000)
    order=dict(id=oid,store=target['id'],lines=deepcopy(lines),quantity=1,
        unit_weight_kg=sum(x['quantity']*x['unit_weight_kg'] for x in lines),
        unit_volume_m3=sum(x['quantity']*x['unit_volume_m3'] for x in lines),
        open=opening,due=event['due'],close=event['close'],
        service_minutes=target['service_minutes'],allowed_vehicles=target['allowed_vehicles'][:])
    return order


def urgent_event(base, event, now, method):
    import continuous
    import urgent
    order=validate_urgent_event(base,event,now)
    oid=order['id'];data=deepcopy(base['data'])
    states=snapshot(base,event['time']); updated=deepcopy(base);data['orders'].append(order);updated['data']=data
    orders={o['id']:o for o in data['orders']}; vehicles=data['vehicles']; started=perf_counter()
    unchanged={v['id']:future(states[v['id']]['remaining'],states[v['id']],v,orders,base['routes'][v['id']],data) for v in vehicles}
    if any(r is None for r in unchanged.values()): raise ValueError('原任务续行未通过约束，请先核查延误和班次')
    best=None;checked=0
    for v in vehicles:
        if v['id'] not in order['allowed_vehicles']: continue
        own=states[v['id']]['remaining'];previous=deepcopy(base['routes'][v['id']])
        task='warehouse:'+oid
        previous['stops'].append(dict(id=task,task_id=task,load_ids=[oid]))
        sequences=[own+[task,oid]]
        if method!='baseline':
            sequences=[]
            for i in range(len(own)+1):
                picked=own[:i]+[task]+own[i:]
                for j in range(i+1,len(picked)+1): sequences.append(picked[:j]+[oid]+picked[j:])
        for seq in sequences:
            checked+=1;r=future(seq,states[v['id']],v,orders,previous,data)
            if r is None: continue
            plan=continuous.assemble(updated,states,{**unchanged,v['id']:r})
            if best is None or urgent.key(plan)<urgent.key(best): best=plan
    if best is None: best=continuous.assemble(updated,states,unchanged)
    best.update(solve_ms=round((perf_counter()-started)*1000,3),checked_routes=checked,
        method=f'有向道路回仓及{oid}有限插入；沿用目的店卸货时长与准入')
    return best,states
