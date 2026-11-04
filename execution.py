"""Append-only local receipts and forecasts along the confirmed route.

No dispatch, measured cargo dimensions, or settlement; reordering needs adoption.
"""
from copy import deepcopy

import business
import normal
import observation


def confirmed(task):
    from application import fingerprint
    plan = next((p for p in task['plans'] if p['id'] == task['adopted_plan_id']), None)
    if not plan or plan['input_revision'] != task['input_revision'] or plan['input_sha256'] != fingerprint(task['data']):
        raise ValueError('先确认当前输入对应的完整方案，再记录执行')
    return plan


def view(task):
    plan = confirmed(task)
    data = task['data']; orders = {o['id']: o for o in data['orders']}
    receipts = task.get('receipts', [])
    routes = {}
    for vehicle in data['vehicles']:
        vid = vehicle['id']; route = plan['result']['optimized']['routes'][vid]
        if not route['sequence']:
            continue
        actual = [r for r in receipts if r['vehicle'] == vid]
        returned = any(r['kind'] == 'return' for r in actual)
        delivered = [r for r in actual if r['kind'] == 'stop']
        done = {r['stop_id'] for r in delivered}
        sequence = task.get('remaining_sequences', {}).get(vid, route['sequence'])
        remaining = [sid for sid in sequence if sid not in done]
        cargo_w = sum(sum(l['quantity']*l['unit_weight_kg'] for l in orders[s]['lines']) for s in remaining)
        cargo_v = sum(sum(l['quantity']*l['unit_volume_m3'] for l in orders[s]['lines']) for s in remaining)
        collected_w = collected_v = 0
        for receipt in delivered:
            if 'business' in data:
                w, v = business.pickup_totals({'pickup': receipt['pickup']}, data['business']['packaging'])
                collected_w += w; collected_v += v
        current_w, current_v = (0, 0) if returned else (cargo_w+collected_w, cargo_v+collected_v)
        warnings = []
        def capacity(w, v, label):
            if w > vehicle['max_weight_kg']+1e-8 or v > vehicle['max_volume_m3']+1e-8:
                warnings.append(label+'超重或超容，需人工处理，不能按可行路线继续派发')
        capacity(current_w, current_v, '当前估算载荷')
        depot = data['settings']['depot_id']
        origin = observation.anchor(task, vid)
        previous, t = origin['node'], origin['time']
        projected = []; remaining_km = 0
        for sid in remaining:
            order = orders[sid]
            leg = data['legs'][previous+'>'+sid] if previous != sid else dict(distance_km=0, travel_minutes=0)
            remaining_km += leg['distance_km']
            t = max(t+leg['travel_minutes'], order['open'])
            arrival = t; t += order['service_minutes']
            deadline = t if data['settings']['window_rule'] == 'finish' else arrival
            if deadline > order['close']+1e-8:
                warnings.append(sid+'预计超过最晚收货时间')
            cargo_w -= sum(l['quantity']*l['unit_weight_kg'] for l in order['lines'])
            cargo_v -= sum(l['quantity']*l['unit_volume_m3'] for l in order['lines'])
            if 'business' in data:
                w, v = business.pickup_totals(order, data['business']['packaging'])
                collected_w += w; collected_v += v
            capacity(cargo_w+collected_w, cargo_v+collected_v, sid+'预计离店载荷')
            projected.append(dict(id=sid, arrival=round(arrival, 4), finish=round(t, 4),
                onboard_weight_kg=round(cargo_w+collected_w, 6), onboard_volume_m3=round(cargo_v+collected_v, 6)))
            previous = sid
        leg = data['legs'][previous+'>'+depot] if previous != depot else dict(distance_km=0, travel_minutes=0)
        t += leg['travel_minutes']
        remaining_km += leg['distance_km']
        if not returned and t > vehicle['shift_end']+1e-8:
            warnings.append('预计回仓超过车辆收班时间')
        if returned and actual[-1]['finish'] > vehicle['shift_end']:
            warnings.append('记录的回仓交接时间超过车辆收班时间')
        routes[vid] = dict(completed=len(delivered), remaining=projected,
            next_stop=remaining[0] if remaining else None, returned=returned,
            onboard_weight_kg=round(current_w, 6), onboard_volume_m3=round(current_v, 6),
            estimated_return=round(t, 4), warnings=warnings,
            anchor=origin, observation_count=sum(o['vehicle']==vid for o in task.get('observations', [])),
            remaining_distance_km=0 if returned else round(remaining_km, 6),
            collected_cartons=sum(r['pickup'].get('cartons', 0) for r in delivered),
            collected_totes=sum(r['pickup'].get('totes', 0) for r in delivered))
    return dict(plan_id=plan['id'], routes=routes, observation_count=len(task.get('observations', [])),
                complete=bool(routes) and all(r['returned'] for r in routes.values()))


def append(task, raw, reviewed):
    if reviewed is not True:
        raise ValueError('先核对回执来源、数量和时间，再保存记录')
    if not isinstance(raw, dict):
        raise ValueError('回执格式无效')
    state = view(task)
    vehicle = raw.get('vehicle'); kind = raw.get('kind')
    if not isinstance(vehicle, str) or vehicle not in state['routes']:
        raise ValueError('请选择已确认方案中的配送车辆')
    route = state['routes'][vehicle]
    if route['returned']:
        raise ValueError('该车已记录回仓，不能再追加配送记录')
    source = raw.get('source')
    if source not in ('simulated', 'manual'):
        raise ValueError('请选择模拟演练或人工录入来源')
    if task['data']['settings']['data_kind'] == 'simulated' and source != 'simulated':
        raise ValueError('构造任务只能记录模拟演练，不能标为真实履约')
    note = raw.get('note', '')
    if not isinstance(note, str) or len(note) > 500:
        raise ValueError('备注最多500字')
    if source == 'manual' and not note.strip():
        raise ValueError('人工录入请在备注填写信息来源或单据编号，真实性待核对')
    finish = normal.numeric(raw.get('finish'), '完成时间', 0, 1439)
    if int(finish) != finish:
        raise ValueError('完成时间需要当天的整分钟')
    if finish < observation.anchor(task, vehicle)['time']:
        raise ValueError('完成时间不能早于本车上一条回执、观察记录或班次开始')
    pickup = {}; stop_id = None
    if kind == 'stop':
        stop_id = raw.get('stop_id')
        if not route['next_stop'] or stop_id != route['next_stop']:
            raise ValueError('请按本车已确认顺序记录下一门店，不能跳店或重复签收')
        if 'business' in task['data']:
            pickup = raw.get('pickup')
            business.validate(task['data']['business'], [dict(pickup=pickup)])
        elif raw.get('pickup') not in (None, {}):
            raise ValueError('此任务未启用空包装规则，不能填写回收数量')
    elif kind == 'return':
        if route['next_stop']:
            raise ValueError('还有门店未签收，不能记录整车回仓交接')
    else:
        raise ValueError('只支持门店整单签收并回收、回仓交接两类记录')
    from application import now
    from uuid import uuid4
    receipt = dict(id=uuid4().hex, plan_id=state['plan_id'], kind=kind, vehicle=vehicle,
        remaining_plan_id=task.get('active_remaining_plan_id'),
        stop_id=stop_id, finish=int(finish), pickup=deepcopy(pickup), source=source,
        note=note.strip(), recorded_at=now())
    task.setdefault('receipts', []).append(receipt)
    # Facts are retained even when actual pickup quantities expose an overload.
    receipt['warnings_after'] = view(task)['routes'][vehicle]['warnings']
    return receipt
