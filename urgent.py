"""One warehouse emergency order on an in-flight normal plan. No persistence.

Keep existing cargo ownership and completed deliveries. Search warehouse and
new-order insertions, then bounded same-truck relocation; no optimality claim.
"""
from copy import deepcopy
from time import perf_counter
import dynamic
import normal

LOAD_MINUTES = 8  # synthetic warehouse loading, same duration as demo transfer


def validate(event, data):
    if not isinstance(event, dict): raise ValueError('紧急订单事件必须是对象')
    required = {'time', 'store', 'quantity', 'due', 'close'}
    if set(event) != required: raise ValueError('紧急订单需且仅需发生时间、门店、箱数、承诺和截止时间')
    normal.numeric(event['time'], '事件时间', 480, 540)
    if event['store'] not in [o['id'] for o in data['orders']]: raise ValueError('门店需为S01–S12')
    q = normal.numeric(event['quantity'], '紧急单箱数', 1, 10000)
    if q != int(q): raise ValueError('紧急单箱数必须为整数')
    for key in ('due', 'close'): normal.numeric(event[key], '紧急单收货时间', 480, 1440)
    store = next(o for o in data['orders'] if o['id'] == event['store'])
    if not max(event['time'], store['open']) <= event['due'] <= event['close']:
        raise ValueError('需满足事件时间及门店开门时间 ≤ 承诺时间 ≤ 截止时间')
    return dict(id='E01', store=store['id'], sku=store.get('sku', 'SKU-'+store['id']),
                point=deepcopy(store['point']), quantity=q,
                unit_weight_kg=store['unit_weight_kg'], unit_volume_m3=store['unit_volume_m3'],
                open=max(event['time'], store['open']), due=event['due'], close=event['close'])


def future(seq, state, vehicle, orders):
    if state.get('progress_mode')=='manual_receipts':
        import continuous
        rr=continuous.future(seq,state,vehicle,orders,dict(stops=[dict(id='W',load_ids=['E01'],arrival=-100,finish=-92)]))
        if rr: rr['sequence']=list(seq)
        return rr
    # Reuse the audited cargo simulator, with a warehouse pickup rather than a
    # roadside transfer. Public routes use W; there is no roadside transfer fee.
    rr = dynamic.future_route(['P' if x == 'W' else x for x in seq], state, vehicle,
                              orders, ['E01'] if 'E01' in seq else [], [0, 0],
                              pickup_minutes=LOAD_MINUTES,pickup_cost=0)
    if rr:
        rr['sequence'] = list(seq)
        for stop in rr['stops']:
            if stop['id'] == 'P': stop['id'] = 'W'
    return rr


def assemble(initial, states, futures, order=None):
    orders = {o['id']: o for o in initial['data']['orders']}
    if order is not None: orders['E01'] = order
    original = dynamic.assignments(initial)
    routes = {}; delivered = []; changed = []; late = []; total_cost = 0.
    for vehicle in initial['data']['vehicles']:
        vid = vehicle['id']; state = states[vid]; rr = futures[vid]
        stops = deepcopy(state['completed']) + deepcopy(rr['stops'])
        km = state['traveled'] + rr['distance']
        used = bool(initial['routes'][vid]['sequence']) or bool(rr['sequence'])
        total_cost += km*vehicle['cost_per_km'] + (vehicle['fixed_cost'] if used else 0)
        routes[vid] = dict(sequence=[s['id'] for s in stops], stops=stops,
            distance=round(km, 4), cost=round(km*vehicle['cost_per_km'] + (vehicle['fixed_cost'] if used else 0), 4),
            position=state['point'], peak_weight_kg=rr['peak_weight_kg'], peak_volume_m3=rr['peak_volume_m3'],
            return_at=rr['return_at'])
        previous = None
        for stop in stops:
            oid = stop['id']
            if oid != 'W':
                delivered.append(oid); late.append(max(0, stop['arrival']-orders[oid]['due']))
                if oid in original:
                    old = original[oid]
                    if old[0] != vid or old[2] != previous or abs(old[1]-stop['arrival']) > 1: changed.append(oid)
            previous = oid
    missing = sorted(set(orders)-set(delivered))
    changed.extend(oid for oid in missing if oid in original)
    return dict(routes=routes, unserved_ids=missing, metrics=dict(served=len(delivered),
        unserved=len(missing), late_orders=sum(x>1e-6 for x in late), late_minutes=round(sum(late), 2),
        cost=round(total_cost, 2),
        distance=round(sum(r['distance'] for r in routes.values()), 2),
        changed_orders=len(set(changed)), changed_ids=sorted(set(changed)), new_orders_served=int('E01' in delivered)),
        status='候选试算，未采用或派单' if not missing else '未找到可行插入方案；原任务保留，E01货物留在仓库，需人工处理')


def key(plan):
    m = plan['metrics']
    return m['unserved'], m['late_orders'], m['late_minutes'], round(m['cost']+4*m['changed_orders'], 4)


def insert(initial, states, order, method):
    start = perf_counter(); orders = {o['id']: o for o in initial['data']['orders']}; orders['E01'] = order
    vehicles = initial['data']['vehicles']
    unchanged = {v['id']: future(states[v['id']]['remaining'], states[v['id']], v, orders) for v in vehicles}
    if any(rr is None for rr in unchanged.values()): raise ValueError('原计划续行未通过约束检查')
    best = None; checked = 0
    def candidate(seq, v):
        nonlocal checked
        checked += 1
        rr = future(seq, states[v['id']], v, orders)
        if rr is None: return None
        return assemble(initial, states, {**unchanged, v['id']: rr}, order)
    for v in vehicles:
        own = states[v['id']]['remaining']
        sequences = [own+['W', 'E01']]
        if method != 'baseline':
            sequences = []
            for p in range(len(own)+1):
                with_pickup = own[:p]+['W']+own[p:]
                for e in range(p+1, len(with_pickup)+1):
                    sequences.append(with_pickup[:e]+['E01']+with_pickup[e:])
        local = None
        for seq in sequences:
            plan = candidate(seq, v)
            if plan and (local is None or key(plan)<key(local)): local = plan
        if local and method != 'baseline':
            for _ in range(4):
                improved = None
                n = len(states[v['id']]['completed'])
                seq = local['routes'][v['id']]['sequence'][n:]
                for i, node in enumerate(seq):
                    base = seq[:i]+seq[i+1:]
                    for p in range(len(base)+1):
                        plan = candidate(base[:p]+[node]+base[p:], v)
                        if plan and key(plan)<key(local) and (improved is None or key(plan)<key(improved)): improved = plan
                if improved is None: break
                local = improved
        if local and (best is None or key(local)<key(best)): best = local
    if best is None: best = assemble(initial, states, unchanged, order)
    best.update(solve_ms=round((perf_counter()-start)*1000, 3), checked_routes=checked,
        method='原任务送完后回仓取货' if method=='baseline' else '回仓及新单插入 + 原车内有限单节点移位')
    return best


def run(data, event):
    normal.validate(data)
    order = validate(event, data)
    initial = normal.solve(data)
    if initial['metrics']['unserved']: raise ValueError('正常计划仍有未安排订单，请先调整货量或车辆容量')
    states = dynamic.snapshot(initial, event['time'])
    return dict(simulated=True, initial=initial, event={**deepcopy(event), 'type':'urgent_order'},
        order=order, states=states, baseline=insert(initial, states, order, 'baseline'),
        recovered=insert(initial, states, order, 'optimized'),
        assumptions=dict(load_minutes=LOAD_MINUTES, load_fee=0,
            cargo='E01在仓库，接手车必须回仓装货；原货不换车；已交付单固定',
            delivery='到店即视为交付；已开始服务需完成后离开',
            scope='现有12家门店中的一家追加一整单，单次事件，08:00–09:00；无全局最优保证',
            costs='全程往返里程费与原已出车固定费；装货暂无独立费用；不含真实人工或货损',
            disturbance='仅统计原12单的车辆、前驱、ETA超过1分钟变化；新增E01另计'))
