"""Bounded same-vehicle reorder after receipts, with independent adoption audit."""
from itertools import permutations

import execution


def stamp(task):
    from application import fingerprint
    payload = dict(data=task['data'], plan=task['adopted_plan_id'],
        receipts=task.get('receipts', []), sequences=task.get('remaining_sequences', {}),
        active_remaining_plan_id=task.get('active_remaining_plan_id'))
    # Empty observations preserve hashes of pending candidates saved before
    # observation support. The first appended observation changes the hash.
    if task.get('observations'):
        payload['observations'] = task['observations']
    return fingerprint(payload)


def sequences(task):
    return {v: [s['id'] for s in r['remaining']] for v, r in execution.view(task)['routes'].items()}


def project(task, proposed):
    # view only reads inputs: do not repeatedly clone all plan/receipt history
    # for every permutation, especially near the history limit.
    return execution.view({**task, 'remaining_sequences': proposed})


def audit(task, proposed):
    """Recompute membership, time and cargo without using execution.view math."""
    plan = execution.confirmed(task)
    data = task['data']; orders = {o['id']:o for o in data['orders']}
    routes = plan['result']['optimized']['routes']
    used = {v for v,r in routes.items() if r['sequence']}
    if not isinstance(proposed, dict) or set(proposed) != used:
        raise ValueError('剩余路线车辆集合不一致')
    checks = []
    for v in data['vehicles']:
        vid = v['id']
        if vid not in used:
            continue
        receipts = [r for r in task.get('receipts', []) if r['vehicle']==vid]
        stops = [r for r in receipts if r['kind']=='stop']
        done = {r['stop_id'] for r in stops}
        expected = set(routes[vid]['sequence'])-done
        seq = proposed[vid]
        if not isinstance(seq,list) or any(not isinstance(s,str) for s in seq) or len(seq)!=len(set(seq)) or set(seq)!=expected:
            raise ValueError('剩余路线必须保留本车全部未签收门店，不能漏单、重复、换车或重送')
        returned = any(r['kind']=='return' for r in receipts)
        if returned:
            if seq: raise ValueError('已回仓车辆不能增加任务')
            continue
        time = receipts[-1]['finish'] if receipts else v['shift_start']
        previous = stops[-1]['stop_id'] if stops else data['settings']['depot_id']
        observations = [o for o in task.get('observations', [])
                        if o['vehicle']==vid and o['vehicle_receipt_count']==len(receipts)]
        if observations:
            previous, time = observations[-1]['node'], observations[-1]['observed_at']
        weight = sum(sum(l['quantity']*l['unit_weight_kg'] for l in orders[s]['lines']) for s in seq)
        volume = sum(sum(l['quantity']*l['unit_volume_m3'] for l in orders[s]['lines']) for s in seq)
        def packaging(pickup):
            p = data['business']['packaging']; c,t = pickup['cartons'],pickup['totes']
            return c*p['carton_weight_kg']+t*p['tote_weight_kg'], c*p['carton_flat_m3']+(p['tote_first_m3']+(t-1)*p['tote_nested_m3'] if t else 0)
        def capacity():
            if weight>v['max_weight_kg']+1e-8 or volume>v['max_volume_m3']+1e-8:
                raise ValueError('当前或后续载荷超容，不能靠改序绕过；需人工处置')
        if 'business' in data:
            for r in stops:
                w,vol = packaging(r['pickup']);weight+=w;volume+=vol
        capacity();km=0
        for sid in seq:
            o=orders[sid]
            if vid not in o['allowed_vehicles']: raise ValueError('车辆准入不满足')
            leg=data['legs'][previous+'>'+sid] if previous!=sid else dict(distance_km=0,travel_minutes=0)
            km+=leg['distance_km']
            arrival=max(time+leg['travel_minutes'],o['open']);time=arrival+o['service_minutes']
            if (time if data['settings']['window_rule']=='finish' else arrival)>o['close']+1e-8:
                raise ValueError('剩余路线超过门店最晚收货时间')
            weight-=sum(l['quantity']*l['unit_weight_kg'] for l in o['lines'])
            volume-=sum(l['quantity']*l['unit_volume_m3'] for l in o['lines'])
            if 'business' in data:
                w,vol=packaging(o['pickup']);weight+=w;volume+=vol
            capacity();previous=sid
        depot=data['settings']['depot_id']
        leg=data['legs'][previous+'>'+depot] if previous!=depot else dict(distance_km=0,travel_minutes=0)
        km+=leg['distance_km'];time+=leg['travel_minutes']
        if time>v['shift_end']+1e-8: raise ValueError('剩余路线回仓超过车辆收班时间')
        checks.append(dict(vehicle=vid, distance_km=round(km,6), return_at=round(time,4)))
    return dict(passed=True, checks=checks, scope='同车货物归属、已收包装、逐站容量、道路时间、窗口及回仓；不含3D与结算')


def solve(task):
    if not task.get('receipts') and not task.get('observations'):
        raise ValueError('先记录至少一条执行回执或位置/等待记录，再计算剩余路线')
    initial=execution.view(task)
    if initial['complete']: raise ValueError('全部车辆已回仓，无需调整剩余路线')
    proposed=sequences(task); evaluations=0
    def rank(state, vid):
        r=state['routes'][vid]
        return (len(r['warnings']),r['remaining_distance_km'],r['estimated_return'])
    for vid, seq in list(proposed.items()):
        if len(seq)<2 or initial['routes'][vid]['returned']:
            continue
        best=list(seq);best_rank=rank(project(task,proposed),vid)
        if len(seq)<=6:
            options=permutations(seq)
            for option in options:
                trial={**proposed,vid:list(option)};evaluations+=1
                key=rank(project(task,trial),vid)
                if key<best_rank:best,best_rank=list(option),key
        else:
            # Six bounded rounds of pair swaps; preserves a valid starting route.
            for _ in range(6):
                previous_best=list(best)
                for i in range(len(seq)):
                    for j in range(i+1,len(seq)):
                        option=list(previous_best);option[i],option[j]=option[j],option[i]
                        evaluations+=1;key=rank(project(task,{**proposed,vid:option}),vid)
                        if key<best_rank:best,best_rank=option,key
                if best==previous_best:break
        proposed[vid]=best
    result=project(task,proposed)
    try:
        audit_result=audit(task,proposed)
    except ValueError as error:
        audit_result=dict(passed=False,error=str(error))
    before=sum(r['remaining_distance_km'] for r in initial['routes'].values())
    after=sum(r['remaining_distance_km'] for r in result['routes'].values())
    return dict(base_sha256=stamp(task),sequences=proposed,before=initial,after=result,
        receipt_count=len(task.get('receipts', [])), base_active_id=task.get('active_remaining_plan_id'),
        observation_count=len(task.get('observations', [])),
        audit=audit_result,changed=proposed!=sequences(task),evaluations=evaluations,
        distance_before=round(before,6),distance_after=round(after,6),
        method='同车剩余门店：6店以内枚举，更多门店最多6轮两店交换；无全局最优保证')
