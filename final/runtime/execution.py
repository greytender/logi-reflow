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
    if data.get('operational_policy') is not None:
        return operational_view(task, plan)
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


def operational_view(task, plan):
    """Receipt-derived loads with persistent cold shells; never a live packing certificate.

    The original declared handling durations remain a conservative *reference*,
    not an observed duration or an upper bound after packaging deviations.
    """
    import operational_constraints as operational
    data=task['data'];policy=data['operational_policy'];orders={o['id']:o for o in data['orders']}
    receipts=task.get('receipts',[]);routes={};goods={(g['order'],g['sku']):g for g in policy['goods']}
    cold_defs={b['id']:b for b in policy['cold_boxes']}
    def box_volume(row):
        return row['length_cm']*row['width_cm']*row['height_cm']/1e6
    def packaging_load(pickup):
        if 'business' not in data:return 0.,0.
        cartons,totes=pickup.get('cartons',0),pickup.get('totes',0)
        p=data['business']['packaging'];g=policy['pickup_geometry'];t=g['tote']
        volume=cartons*box_volume(g['carton'])
        if totes:volume+=t['length_cm']*t['width_cm']*(t['height_cm']+(totes-1)*t['nested_height_cm'])/1e6
        return cartons*p['carton_weight_kg']+totes*p['tote_weight_kg'],volume
    for vehicle in data['vehicles']:
        vid=vehicle['id'];route=plan['result']['optimized']['routes'][vid];sequence=route['sequence']
        if not sequence:continue
        actual=[r for r in receipts if r['vehicle']==vid];delivered=[r for r in actual if r['kind']=='stop']
        returned=any(r['kind']=='return' for r in actual);done={r['stop_id'] for r in delivered}
        remaining=[s for s in sequence if s not in done];observations=[o for o in task.get('observations',[]) if o['vehicle']==vid]
        received={r['stop_id']:deepcopy(r.get('pickup',{})) for r in delivered}
        cold_ids={goods[(sid,line['sku'])]['cold_box_id'] for sid in sequence for line in orders[sid]['lines']
                  if goods[(sid,line['sku'])]['kind']=='cold'}
        warnings=[];deviated=any(r.get('pickup',{})!=orders[r['stop_id']].get('pickup',{}) for r in delivered)
        geometry_status='RETURNED_NOT_ONBOARD' if returned else 'NEEDS_OBSERVATION' if actual or observations else 'DECLARED_PREDICTION'
        def load(remaining_ids, pickups):
            weight=volume=0.
            for sid in remaining_ids:
                for line in orders[sid]['lines']:
                    definition=goods[(sid,line['sku'])]
                    weight+=line['quantity']*line['unit_weight_kg']
                    if definition['kind']!='cold':volume+=line['quantity']*box_volume(definition)
            for bid in cold_ids:
                weight+=cold_defs[bid]['tare_weight_kg'];volume+=box_volume(cold_defs[bid])
            for values in pickups.values():
                pw,pv=packaging_load(values);weight+=pw;volume+=pv
            # Only declared return manifests exist; receipt UI does not certify their actual quantities.
            for item in policy.get('returns',[]):
                if item['order'] in pickups:
                    weight+=item['quantity']*item['unit_weight_kg'];volume+=item['quantity']*box_volume(item)
            return weight,volume
        def capacity(w,v,label):
            if w>vehicle['max_weight_kg']+1e-8 or v>vehicle['max_volume_m3']+1e-8:
                warnings.append(label+'超重或超容，需人工处理，不能按可行路线继续派发')
        current_w,current_v=(0.,0.) if returned else load(remaining,received)
        capacity(current_w,current_v,'当前载荷')
        if deviated and not returned:
            warnings.append('回收数量偏离预计，载荷已更新，请重核箱位和装卸用时')
        if policy.get('returns') and not returned:
            warnings.append('退货按计划数量计算，请核对实际件数和封装状态')
        report=route.get('operational') or operational.evaluate_route(data,route,vehicle,policy)
        original_stops={s['id']:s for s in report.get('adjusted_route',{}).get('stops',[])}
        origin=observation.anchor(task,vid);previous=origin['node'];t=origin['time'];km=0.;projected=[];pending=list(remaining)
        forecast_pickups=deepcopy(received)
        for sid in remaining:
            order=orders[sid];leg=data['legs'][previous+'>'+sid] if previous!=sid else dict(distance_km=0,travel_minutes=0)
            km+=leg['distance_km'];road=t+leg['travel_minutes'];t=max(road,order['open']);arrival=t
            extra=original_stops.get(sid,{}).get('operational_extra_minutes',0)
            t+=order['service_minutes']+extra
            deadline=t if data['settings']['window_rule']=='finish' else arrival
            if deadline>order['close']+1e-8:warnings.append(sid+'含声明开箱/倒货/回收工时后预计超过最晚收货时间')
            for bid in {goods[(sid,line['sku'])].get('cold_box_id') for line in order['lines'] if goods[(sid,line['sku'])]['kind']=='cold'}:
                definition=cold_defs[bid];activated=definition.get('activated_at_minutes',vehicle['shift_start'])
                # Finish is conservative for this reference forecast; opening sequence is not observed.
                if t-activated>definition['valid_minutes']+1e-8:
                    warnings.append(sid+'预计超过保温箱声明有效时限，请核对冷货交付安排')
            pending.remove(sid)
            if 'business' in data:forecast_pickups[sid]=deepcopy(order['pickup'])
            # Returns are attached to stop completion even when packaging is not enabled.
            elif any(r['order']==sid for r in policy.get('returns',[])):forecast_pickups[sid]={}
            weight,volume=load(pending,forecast_pickups);capacity(weight,volume,sid+'预计离店载荷')
            projected.append(dict(id=sid,arrival=round(arrival,4),finish=round(t,4),
                onboard_weight_kg=round(weight,6),onboard_volume_m3=round(volume,6),
                operational_extra_minutes=round(extra,4),geometry_status='NEEDS_OBSERVATION' if actual or observations else 'DECLARED_PREDICTION'))
            previous=sid
        depot=data['settings']['depot_id'];back=data['legs'][previous+'>'+depot] if previous!=depot else dict(distance_km=0,travel_minutes=0)
        if not returned:km+=back['distance_km'];t+=back['travel_minutes']
        if t>vehicle['shift_end']+1e-8:warnings.append('含声明操作工时的预计或已记录回仓超过车辆收班时间')
        cold_shells=[] if returned else [dict(id=bid,tare_weight_kg=cold_defs[bid]['tare_weight_kg'],
            outer_volume_m3=round(box_volume(cold_defs[bid]),6),activated_at_minutes=cold_defs[bid].get('activated_at_minutes',vehicle['shift_start']),
            remaining_delivery_stores=[s for s in remaining if any(goods[(s,l['sku'])].get('cold_box_id')==bid for l in orders[s]['lines'])]) for bid in sorted(cold_ids)]
        temperature_records=[deepcopy(r) for r in task.get('temperature_records',[]) if r['vehicle']==vid]
        temperature_summary=summarize_temperature(temperature_records,sorted(cold_ids))
        if temperature_summary['open_excursions']:
            warnings.append('有温度异常尚未记录处置，请检查货品并补充司机处理记录')
        routes[vid]=dict(completed=len(delivered),remaining=projected,next_stop=remaining[0] if remaining else None,
            returned=returned,onboard_weight_kg=round(current_w,6),onboard_volume_m3=round(current_v,6),
            estimated_return=round(t,4),warnings=warnings,anchor=origin,observation_count=len(observations),
            remaining_distance_km=round(km,6),collected_cartons=sum(r['pickup'].get('cartons',0) for r in delivered),
            collected_totes=sum(r['pickup'].get('totes',0) for r in delivered),
            current_geometry_status=geometry_status,current_geometry_verified=False,current_cold_boxes=cold_shells,
            actual_pickup_deviation=deviated,forecast_scope='按记录起点和计划装卸用时推算',
            load_scope='剩余商品、冷链箱、回收包装与计划退货',
            temperature_status=temperature_summary['status'],temperature_records=temperature_records,
            temperature_summary=temperature_summary,operational_policy_enabled=True)
    return dict(plan_id=plan['id'],routes=routes,observation_count=len(task.get('observations',[])),
        complete=bool(routes) and all(r['returned'] for r in routes.values()),
        operational_policy_enabled=True,current_geometry_scope='prediction_not_live_geometry')


def summarize_temperature(records, cold_ids):
    """An append-only observation log; a handling note is never proof of compliance."""
    readings=[r for r in records if r['kind']=='reading']
    handled={r['target_id'] for r in records if r['kind']=='action'}
    excursions=[r for r in readings if r['excursion']]
    open_records=[r for r in excursions if not r.get('action') and r['id'] not in handled]
    status=('NOT_OBSERVED' if not readings else 'EXCURSION_OPEN' if open_records else
            'EXCURSION_HANDLED' if excursions else 'RECORDED')
    phases=('loading','transit','unloading')
    boxes=[]
    for bid in cold_ids:
        own=[r for r in readings if r['cold_box']==bid]
        own_open=[r['id'] for r in open_records if r['cold_box']==bid]
        boxes.append(dict(cold_box=bid,reading_count=len(own),open_excursion_ids=own_open,
            covered_phases=[p for p in phases if any(r['phase']==p for r in own)],
            missing_phases=[p for p in phases if not any(r['phase']==p for r in own)],
            latest_reading=deepcopy(max(own,key=lambda r:(r['time'],r['recorded_at']))) if own else None))
    return dict(status=status,record_count=len(records),reading_count=len(readings),
        open_excursions=len(open_records),handled_excursions=len(excursions)-len(open_records),
        open_excursion_ids=[r['id'] for r in open_records],
        covered_phases=[p for p in phases if any(r['phase']==p for r in readings)],
        required_phases=list(phases),boxes=boxes,
        latest_reading=deepcopy(max(readings,key=lambda r:(r['time'],r['recorded_at']))) if readings else None,
        coverage_scope='分环节温度记录，不代表连续监测')


def append_temperature(task, raw, reviewed):
    """Record explicit readings or follow-up actions, including out-of-range facts.

    Readings never update location, delivery status, or the cold activation clock.
    Retrospective readings can be added after return but cannot rewrite history.
    """
    if reviewed is not True:raise ValueError('请核对温度记录来源、时刻与箱号')
    if not isinstance(raw,dict):raise ValueError('温度记录格式无效')
    kind=raw.get('kind','reading')
    required={'vehicle','cold_box','time','source','note'}
    allowed=required|{'kind','action'}|({'phase','temperature_c'} if kind=='reading' else {'target_id'})
    if kind not in ('reading','action') or not required<=set(raw) or set(raw)-allowed:
        raise ValueError('温度记录字段不完整或含未知字段')
    policy=task['data'].get('operational_policy')
    if not policy:raise ValueError('本批次尚未配置冷链箱规则')
    state=view(task);vid=raw.get('vehicle');bid=raw.get('cold_box')
    if not isinstance(vid,str) or vid not in state['routes']:raise ValueError('请选择已确认路线中的车辆')
    route=confirmed(task)['result']['optimized']['routes'][vid]
    goods=policy['goods'];assigned={g.get('cold_box_id') for g in goods if g['kind']=='cold' and g['order'] in route['sequence']}
    if not isinstance(bid,str) or bid not in assigned:raise ValueError('冷链箱不属于该车的已确认路线')
    source=raw.get('source');note=raw.get('note');action=raw.get('action','')
    if source not in ('simulated','manual'):raise ValueError('请选择示例演练或人工记录来源')
    if task['data']['settings']['data_kind']=='simulated' and source!='simulated':
        raise ValueError('示例任务不能标为真实温度记录')
    if not isinstance(note,str) or len(note)>500 or (source=='manual' and not note.strip()):
        raise ValueError('请填写500字内来源说明，人工记录须注明单据或设备来源')
    if not isinstance(action,str) or len(action)>500:raise ValueError('处置记录最多500字')
    minute=normal.numeric(raw.get('time'),'记录时刻',0,1439)
    if int(minute)!=minute:raise ValueError('记录时刻需要当天的整分钟')
    from application import now
    from uuid import uuid4
    record=dict(id=uuid4().hex,plan_id=state['plan_id'],kind=kind,vehicle=vid,cold_box=bid,
        time=int(minute),source=source,note=note.strip(),action=action.strip(),recorded_at=now())
    if kind=='reading':
        phase=raw.get('phase');temperature=normal.numeric(raw.get('temperature_c'),'温度',-100,100)
        if phase not in ('loading','transit','unloading'):raise ValueError('请选择装车、在途或卸货环节')
        vehicle=next(v for v in task['data']['vehicles'] if v['id']==vid)
        if phase=='loading' and minute>vehicle['shift_start']:raise ValueError('装车温度时刻不能晚于计划发车，发车后请选择在途或卸货')
        if phase!='loading' and minute<vehicle['shift_start']:raise ValueError('在途或卸货时刻不能早于计划发车')
        returns=[r['finish'] for r in task.get('receipts',[]) if r['vehicle']==vid and r['kind']=='return']
        if returns and minute>returns[-1]:raise ValueError('该车已返仓，只能补录返仓前的温度读数')
        box=next(b for b in policy['cold_boxes'] if b['id']==bid);limits=box['temperature_range']
        record.update(phase=phase,temperature_c=temperature,temperature_range=deepcopy(limits),
            excursion=temperature<limits[0] or temperature>limits[1])
    else:
        target=next((r for r in task.get('temperature_records',[]) if r['id']==raw.get('target_id')),None)
        if not target or target['kind']!='reading' or not target['excursion'] or target['vehicle']!=vid or target['cold_box']!=bid:
            raise ValueError('请选择该车该箱的一条温度异常记录')
        if minute<target['time']:raise ValueError('处置时刻不能早于异常读数')
        if not action.strip():raise ValueError('请填写司机处置、货品检查或交接结果')
        record['target_id']=target['id']
    task.setdefault('temperature_records',[]).append(record)
    return deepcopy(record)


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
        raise ValueError('请选择示例演练或人工录入来源')
    if task['data']['settings']['data_kind'] == 'simulated' and source != 'simulated':
        raise ValueError('示例任务只能记录演练结果')
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
