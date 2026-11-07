"""Atomic multi-depot batches over the existing audited single-depot solver.

Warehouse allocation is supplied by the operator. No inventory, transfers,
shared vehicles, multi-trip scheduling, or enterprise dispatch is inferred.
"""
from contextlib import nullcontext
from copy import deepcopy
import csv
import io
import json
from uuid import uuid4

import application as app
import execution
import observation
import pilot
import remaining
import warehouse_loading
import operational_constraints as operational
from store import Conflict

SCHEMA = 'logireflow.multiwarehouse.v1'
MAX_WAREHOUSES = 16
FLEET_LIMITS = (128, 24)
BOUNDARIES = [
    '订单与车辆按明确仓库归属分组；不自动分仓、调拨、跨仓借车或安排多趟',
    '各仓独立启发式排线，统一核验与汇总；不声称多仓全局最优',
    f'标准批次每仓最多{app.MAX_ORDERS}店、{app.MAX_VEHICLES}车，最多{MAX_WAREHOUSES}仓；单仓大批次最多{FLEET_LIMITS[0]}店、{FLEET_LIMITS[1]}车',
    '道路公里为输入估计；可选声明箱规的有限几何核验，非GPS实绩、现场装载证明或正式结算',
    '本机确认及人工回执不等于企业派单或已验证真实履约',
]


def warehouse_limits(warehouse):
    return FLEET_LIMITS if warehouse.get('planning_mode') == 'fleet' else (app.MAX_ORDERS, app.MAX_VEHICLES)


def canonicalize(raw):
    if not isinstance(raw, dict) or raw.get('schema_version') != SCHEMA:
        raise ValueError('请使用多仓任务模板（schema_version不匹配）')
    if set(raw) != {'schema_version', 'warehouses', 'orders', 'vehicles', 'legs'}:
        raise ValueError('多仓输入仅接受schema_version/warehouses/orders/vehicles/legs；额外业务字段需先明确映射，不会静默丢弃')
    warehouses = raw.get('warehouses')
    if not isinstance(warehouses, list) or not 1 <= len(warehouses) <= MAX_WAREHOUSES:
        raise ValueError(f'请填写1—{MAX_WAREHOUSES}个仓库')
    if any(not isinstance(w, dict) for w in warehouses):
        raise ValueError('仓库格式无效')
    if any(set(w)-{'id', 'name', 'settings', 'business', 'loading_profile', 'operational_policy', 'planning_mode'} for w in warehouses):
        raise ValueError('仓库含未映射字段，请先明确业务含义，不会静默丢弃')
    if any('planning_mode' in w and (w['planning_mode'] != 'fleet' or len(warehouses) != 1) for w in warehouses):
        raise ValueError('大批次模式适用于同一仓库的一次配送任务')
    ids = [pilot.identifier(w.get('id'), '仓库编号') for w in warehouses]
    if len(ids) != len(set(ids)):
        raise ValueError('仓库编号重复')
    for field in ('orders', 'vehicles'):
        rows = raw.get(field)
        if not isinstance(rows, list) or not rows or len(rows) > MAX_WAREHOUSES * (app.MAX_ORDERS if field == 'orders' else app.MAX_VEHICLES):
            raise ValueError('订单或车辆为空或超过多仓上限；不会截取数据')
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('订单或车辆格式无效')
            allowed = {'id', 'warehouse_id', 'open', 'due', 'close', 'service_minutes', 'allowed_vehicles', 'lines',
                       'quantity', 'unit_weight_kg', 'unit_volume_m3', 'pickup'} if field == 'orders' else {
                       'id', 'warehouse_id', 'max_weight_kg', 'max_volume_m3', 'fixed_cost', 'cost_per_km', 'shift_start', 'shift_end'}
            if set(row)-allowed:
                raise ValueError('订单或车辆含未映射字段，请先明确字段映射，不会静默丢弃')
            if field == 'orders' and isinstance(row.get('lines'), list):
                if any(isinstance(line, dict) and set(line)-{'sku','quantity','unit_weight_kg','unit_volume_m3'} for line in row['lines']):
                    raise ValueError('货物行含未映射字段，请先核对，不会静默丢弃')
            rid = pilot.identifier(row.get('id'), '配送任务/车辆编号')
            if rid in seen:
                raise ValueError('配送任务/车辆编号跨仓重复，不能重复派车或漏算订单：'+rid)
            if rid in ids:
                raise ValueError('配送任务/车辆编号不能等于仓库编号：'+rid)
            seen.add(rid)
            if row.get('warehouse_id') not in ids:
                raise ValueError(rid+' 缺少有效仓库归属，请明确后重试')
    if not isinstance(raw.get('legs'), dict):
        raise ValueError('缺少有向道路数据')
    result = dict(schema_version=SCHEMA, warehouses=[], orders=[], vehicles=[], legs={})
    for warehouse in warehouses:
        wid = warehouse['id']
        settings = warehouse.get('settings')
        if not isinstance(settings, dict) or settings.get('depot_id') != wid:
            raise ValueError(wid+' 的settings.depot_id必须等于仓库编号')
        orders = [deepcopy(o) for o in raw['orders'] if o['warehouse_id'] == wid]
        vehicles = [deepcopy(v) for v in raw['vehicles'] if v['warehouse_id'] == wid]
        nodes = {wid} | {o['id'] for o in orders}
        keys = {a+'>'+b for a in nodes for b in nodes if a != b}
        data = dict(settings=settings, orders=orders, vehicles=vehicles,
                    legs={k: v for k, v in raw['legs'].items() if k in keys})
        if 'business' in warehouse:
            data['business'] = warehouse['business']
        try:
            clean = app.canonicalize(data, limits=warehouse_limits(warehouse))
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError(wid+'：'+str(error)) from None
        item = dict(id=wid, name=app.task_name(warehouse.get('name')), settings=clean['settings'])
        if 'planning_mode' in warehouse:
            item['planning_mode'] = warehouse['planning_mode']
        if 'business' in clean:
            item['business'] = clean['business']
        if 'loading_profile' in warehouse:
            warehouse_loading.loading.profile(clean, warehouse['loading_profile'])
            item['loading_profile'] = deepcopy(warehouse['loading_profile'])
        if 'operational_policy' in warehouse:
            item['operational_policy'] = operational.validate_policy(clean, warehouse['operational_policy'])
        result['warehouses'].append(item)
        for field in ('orders', 'vehicles'):
            for row in clean[field]:
                row['warehouse_id'] = wid
                result[field].append(row)
        result['legs'].update(clean['legs'])
    extra = set(raw['legs']) - set(result['legs'])
    if extra:
        raise ValueError('道路包含未知、跨仓或自环方向，请核对：'+', '.join(sorted(map(str, extra))[:8]))
    return result


def split(data):
    groups = {}
    for w in data['warehouses']:
        wid = w['id']
        orders = [{k: deepcopy(v) for k, v in o.items() if k != 'warehouse_id'}
                  for o in data['orders'] if o['warehouse_id'] == wid]
        vehicles = [{k: deepcopy(v) for k, v in o.items() if k != 'warehouse_id'}
                    for o in data['vehicles'] if o['warehouse_id'] == wid]
        nodes = {wid} | {o['id'] for o in orders}
        part = dict(settings=deepcopy(w['settings']), orders=orders, vehicles=vehicles,
            legs={k: deepcopy(v) for k, v in data['legs'].items() if all(n in nodes for n in k.split('>'))})
        if 'business' in w:
            part['business'] = deepcopy(w['business'])
        groups[wid] = app.canonicalize(part, limits=warehouse_limits(w))
        if 'operational_policy' in w:
            groups[wid]['operational_policy'] = deepcopy(w['operational_policy'])
    return groups


def sample(physical_loading=False):
    result = dict(schema_version=SCHEMA, warehouses=[], orders=[], vehicles=[], legs={})
    for index, name in enumerate(('长沙北仓（模拟）', '长沙南仓（模拟）'), 1):
        part = app.sample(); old = part['settings']['depot_id']; wid = 'WH_'+str(index)
        rename = {old: wid}
        rename.update({o['id']: wid+'_'+o['id'] for o in part['orders']})
        rename.update({v['id']: wid+'_'+v['id'] for v in part['vehicles']})
        part['settings'].update(depot_id=wid, travel_source='多仓闭环构造道路，仅供模拟演练')
        result['warehouses'].append(dict(id=wid, name=name, settings=part['settings'], business=part['business']))
        for o in part['orders']:
            o.update(id=rename[o['id']], warehouse_id=wid,
                     allowed_vehicles=[rename[v] for v in o['allowed_vehicles']])
            result['orders'].append(o)
        for v in part['vehicles']:
            v.update(id=rename[v['id']], warehouse_id=wid)
            result['vehicles'].append(v)
        for key, leg in part['legs'].items():
            a, b = key.split('>'); result['legs'][rename[a]+'>'+rename[b]] = leg
    if physical_loading:
        for warehouse in result['warehouses']:
            wid = warehouse['id']
            orders = [o for o in result['orders'] if o['warehouse_id'] == wid]
            vehicles = [v for v in result['vehicles'] if v['warehouse_id'] == wid]
            # Intentionally constructed small demonstration; never derived from the enterprise attachment.
            for order in orders:
                order['lines'][0]['quantity'] = 2
            warehouse['loading_profile'] = dict(schema='loading_v1', data_kind='simulated',
                vehicles=[dict(id=v['id'], length_cm=240, width_cm=160, height_cm=160) for v in vehicles],
                items=[dict(order=o['id'], sku=line['sku'], length_cm=60, width_cm=40, height_cm=30,
                            rotate_base=True, fragile=False, max_top_load_kg=70)
                       for o in orders for line in o['lines']])
    return canonicalize(result)


def operational_sample():
    """Explicitly assumed vehicle/equipment example, unrelated to enterprise rows."""
    part, policy = operational.sample_data()
    wid = part['settings']['depot_id']
    warehouse = dict(id=wid, name='仓库1', settings=part['settings'],
        operational_policy=policy)
    if 'business' in part:
        warehouse['business'] = part['business']
    return canonicalize(dict(schema_version=SCHEMA, warehouses=[warehouse],
        orders=[dict(deepcopy(o), warehouse_id=wid) for o in part['orders']],
        vehicles=[dict(deepcopy(v), warehouse_id=wid) for v in part['vehicles']], legs=part['legs']))


def spatial_view(task, warehouse_id='', vehicle_id='', plan_id=''):
    """Read-only, versioned scene of a candidate; never live cargo observation."""
    plan = current_plan(task)
    if plan is None:
        raise ValueError('当前输入尚无候选，请计算后打开三维操作室')
    if plan_id and plan_id != plan['id']:
        raise Conflict('三维页面引用的候选已失效，请从当前工作台重新打开')
    warehouse = next((w for w in task['data']['warehouses'] if w['id'] == warehouse_id), None)
    if warehouse is None:
        if warehouse_id: raise ValueError('选中的仓库不属于当前任务')
        warehouse = task['data']['warehouses'][0]
    wid = warehouse['id']; group = plan['warehouses'][wid]
    available = {v['id'] for v in group['data']['vehicles']}
    if vehicle_id and vehicle_id not in available:
        raise ValueError('选中的车辆不属于当前仓库')
    if warehouse.get('operational_policy') is not None:
        part = group['data']
        policy = warehouse['operational_policy']
        verification = group['operational_verification']
        if warehouse.get('planning_mode') == 'fleet':
            vehicle_id = vehicle_id or next((vid for vid, route in group['optimized']['routes'].items()
                                             if route['sequence']), '')
            if not vehicle_id:
                raise ValueError('当前还没有已安排路线，请先处理待安排门店')
            vehicle = next(v for v in part['vehicles'] if v['id'] == vehicle_id)
            scene = operational.build_vehicle_scene(part, group['optimized'], policy, vehicle, verification)
        else:
            scene = operational.build_scene(part, group['optimized'], policy)
    else:
        routes = []
        profiles = {v['id']: v for v in warehouse.get('loading_profile', {}).get('vehicles', [])}
        for vehicle in group['data']['vehicles']:
            vid = vehicle['id']; route = group['optimized']['routes'][vid]
            packing = route.get('loading', {})
            if not packing.get('placements'): continue
            items = {i['id']: i for i in warehouse_loading.loading.profile(group['data'], warehouse['loading_profile'])[1]}
            boxes = [dict(deepcopy(p), order=items[p['id']]['order'], sku=items[p['id']]['sku'],
                kind='delivery', weight_kg=items[p['id']]['weight_kg'], fragile=items[p['id']]['fragile']) for p in packing['placements']]
            frames = [dict(index=0, label='出发装载', phase='departure', boxes=deepcopy(boxes),
                time_minutes=vehicle['shift_start'], audit={'passed': True})]
            active = deepcopy(boxes)
            for stop in route['stops']:
                active = [b for b in active if b['order'] != stop['id']]
                frames.append(dict(index=len(frames), label=stop['id']+'卸货后', phase='delivery', stop_id=stop['id'],
                    time_minutes=stop['finish'], boxes=deepcopy(active), audit={'passed': True}))
            routes.append(dict(vehicle_id=vid, vehicle={**vehicle, **profiles[vid]}, boxes=boxes,
                frames=frames, actions=[], warnings=['此旧模型不含回收物三维状态与温控'], checks=['声明出发配送箱几何']))
        scene = dict(schema='logireflow.operational-scene.v1', data_kind=warehouse['settings']['data_kind'],
            routes=routes, status='DEPARTURE_GEOMETRY_ONLY' if routes else 'NOT_CONFIGURED',
            passed=None, coverage=[], errors=[], boundaries=['旧装载模型仅显示出发配送箱；未核验回收箱摆放或冷链'])
    display_names = {key: {item['id']: label+str(i+1) for i,item in enumerate(task['data'][key])}
                     for key,label in [('vehicles','车辆'),('orders','门店'),('warehouses','仓库')]}
    return dict(scene=scene, display_names=display_names, task_ref=dict(id=task['id'], revision=task['revision'], input_sha256=app.fingerprint(task['data'])),
        warehouse_id=wid, vehicle_id=vehicle_id, plan_id=plan['id'],
        execution_present=has_facts(task), classification='candidate_simulation_not_live_cargo',
        title=task['name'], boundaries=scene.get('boundaries', []))


def journey_view(task, warehouse_id='', vehicle_id='', plan_id=''):
    """One immutable candidate joins the driving route and its cargo frames."""
    spatial=spatial_view(task,warehouse_id,vehicle_id,plan_id)
    plan=current_plan(task);wid=spatial['warehouse_id'];group=plan['warehouses'][wid]
    routes=group['optimized']['routes']
    vid=vehicle_id or next((v for v,r in routes.items() if r['sequence']),None)
    if not vid or not routes[vid]['sequence']:
        raise ValueError('该车没有配送路线，请选择已安排的车辆')
    cargo=next((r for r in spatial['scene']['routes'] if r['vehicle_id']==vid),None)
    if cargo is None or not cargo.get('frames'):
        raise ValueError('该车缺少装卸箱位，请补齐箱规后计算方案')
    phases={f.get('phase') for f in cargo['frames']}
    if not phases.intersection({'load','loading'}) or not phases.intersection({'return','returned'}):
        raise ValueError('完整流程需要装车和返仓步骤，请补齐装卸参数，或打开冷链与回收示例')
    if spatial['scene'].get('passed') is False:
        raise ValueError('该车装卸约束未通过，请核对后重新计算')
    spatial['vehicle_id']=vid
    spatial['scene']['routes']=[cargo]
    warehouse=next(w for w in task['data']['warehouses'] if w['id']==wid)
    vehicle=next(v for v in group['data']['vehicles'] if v['id']==vid)
    raw=routes[vid];adjusted=raw.get('operational',{}).get('adjusted_route') or raw
    display_route={k:deepcopy(adjusted[k]) for k in ('sequence','distance','cost','return_at','stops')}
    nodes=[wid,*raw['sequence'],wid]
    legs=[dict(deepcopy(task['data']['legs'][a+'>'+b]),from_id=a,to_id=b)
          for a,b in zip(nodes,nodes[1:])]
    return dict(schema='logireflow.journey.v1',spatial=spatial,
        map=dict(warehouse=dict(id=wid,name=warehouse['name']),orders=deepcopy(group['data']['orders']),
            routes=[dict(vehicle=deepcopy(vehicle),route=display_route,color='#24664b')],names=spatial['display_names']),
        vehicle_id=vid,warehouse_id=wid,plan_id=plan['id'],task_ref=deepcopy(spatial['task_ref']),
        legs=legs,execution_present=spatial['execution_present'],classification='candidate_presentation_not_execution',
        title=task['name'])


def metrics(results, mode):
    routes = []
    operation_totals = []
    check_name = 'operational_verification' if mode == 'optimized' else 'baseline_operational_verification'
    weight_capacity = volume_capacity = 0
    for p in results.values():
        check = p.get(check_name, {})
        for v in p['data']['vehicles']:
            route = p[mode]['routes'][v['id']]
            if not route['sequence']:
                continue
            report = check.get('routes', {}).get(v['id'], {}) if check.get('passed') is True else {}
            routes.append(report.get('adjusted_route', route))
            operation_totals.append(report.get('time_adjustments'))
            weight_capacity += v['max_weight_kg']; volume_capacity += v['max_volume_m3']
    handling_known = bool(operation_totals) and all(row is not None for row in operation_totals)
    return dict(served=sum(len(r['sequence']) for r in routes),
        unserved=sum(len(p[mode]['unserved']) for p in results.values()),
        trips=len(routes),
        cost=round(sum(r['cost'] for r in routes), 4),
        distance_km=round(sum(r['distance'] for r in routes), 4),
        late_orders=sum(r['late_orders'] for r in routes),
        latest_return_at=max((r['return_at'] for r in routes), default=None),
        rehandle_moves=sum(row['rehandle_moves'] for row in operation_totals) if handling_known else None,
        high_risk_rehandle_moves=sum(row.get('high_risk_rehandle_moves', 0) for row in operation_totals) if handling_known else None,
        weight_utilization=round(sum(r['weight_kg'] for r in routes)/weight_capacity, 6) if weight_capacity else 0,
        volume_utilization=round(sum(r['volume_m3'] for r in routes)/volume_capacity, 6) if volume_capacity else 0)


def summarize(data, results):
    """Publish deltas only when both plans cover the same complete input and pass its rules."""
    summary = {mode: metrics(results, mode) for mode in ('baseline', 'optimized')}
    reasons, models = [], []
    complete = True
    for warehouse in data['warehouses']:
        wid = warehouse['id']
        result = results[wid]
        part = result['data']
        expected = {o['id'] for o in part['orders']}
        scope = dict(warehouse_id=wid, departure_geometry='loading_profile' in warehouse,
                     cold_chain_and_recovery='operational_policy' in warehouse)
        models.append(scope)
        for mode in ('baseline', 'optimized'):
            plan = result[mode]
            if plan['unserved'] or warehouse_loading.served_set(plan) != expected:
                complete = False
                first = plan['unserved'][0]['id'] if plan['unserved'] else None
                reasons.append(dict(warehouse_id=wid, plan=mode, code='unserved', stop_id=first,
                    message=f"还有 {len(plan['unserved'])} 家门店待安排，先核对任务覆盖"))
            try:
                pilot.audit(part, plan)
            except (ValueError, KeyError, TypeError) as error:
                reasons.append(dict(warehouse_id=wid, plan=mode, code='core_audit', message=str(error)))
            if scope['departure_geometry']:
                profile = warehouse['loading_profile']
                try:
                    checked = ({**plan, 'routes': warehouse_loading.manual(part, profile, plan['routes'])}
                               if mode == 'baseline' else plan)
                    warehouse_loading.check_plan(part, profile, checked)
                except (ValueError, KeyError, TypeError) as error:
                    reasons.append(dict(warehouse_id=wid, plan=mode, code='departure_geometry', message=str(error)))
            if scope['cold_chain_and_recovery']:
                check_name = 'operational_verification' if mode == 'optimized' else 'baseline_operational_verification'
                layout = operational.FLEET_LAYOUT if warehouse.get('planning_mode') == 'fleet' else None
                checked = result.get(check_name) or operational.evaluate_plan(part, plan, warehouse['operational_policy'],
                    compact=True, layout=layout)
                if checked.get('passed') is not True:
                    reasons.extend(dict(warehouse_id=wid, plan=mode, **issue) for issue in checked['errors'])
    comparable = complete and not reasons
    status = 'comparable' if comparable else ('constraint_mismatch' if complete else 'coverage_mismatch')
    delta = None
    if comparable:
        before, after = summary['baseline'], summary['optimized']
        keys = ('cost', 'trips', 'distance_km', 'late_orders', 'weight_utilization',
                'volume_utilization', 'rehandle_moves', 'high_risk_rehandle_moves', 'latest_return_at')
        delta = {key: round(after[key] - before[key], 6) if before[key] is not None and after[key] is not None else None
                 for key in keys}
    return dict(summary=summary, comparison=dict(comparable=comparable, status=status, reasons=reasons,
        delta=delta, models=models, baseline_method='input_order_append', mileage_basis='input_road_distance'))


def audit(data, results, *, reuse_checked=False):
    groups = split(data)
    if set(results) != set(groups):
        raise ValueError('多仓核验失败：仓库覆盖不完整')
    served, unserved, used_vehicles = [], [], []
    for wid, part in groups.items():
        plan = results[wid]['optimized']
        pilot.audit(part, plan)
        warehouse = next(w for w in data['warehouses'] if w['id'] == wid)
        if 'loading_profile' in warehouse:
            warehouse_loading.check_plan(part, warehouse['loading_profile'], plan)
        if 'operational_policy' in warehouse:
            checked = (results[wid].get('operational_verification') if reuse_checked else None)
            if checked is None:
                layout = operational.FLEET_LAYOUT if warehouse.get('planning_mode') == 'fleet' else None
                checked = operational.evaluate_plan(part, plan, warehouse['operational_policy'], compact=True, layout=layout)
            if not checked.get('passed'):
                raise ValueError(wid+'：冷链与沿途空间核验失败：'+warehouse_loading.operational_reason(checked))
        for vid, route in plan['routes'].items():
            if route['sequence']:
                used_vehicles.append(vid)
            served.extend(route['sequence'])
        unserved.extend(o['id'] for o in plan['unserved'])
    expected = {o['id'] for o in data['orders']}
    if len(served) != len(set(served)) or len(used_vehicles) != len(set(used_vehicles)):
        raise ValueError('多仓核验失败：重复配送或重复使用车辆')
    if set(served) & set(unserved) or set(served) | set(unserved) != expected or len(unserved) != len(set(unserved)):
        raise ValueError('多仓核验失败：任务遗漏或重复')
    return dict(passed=True, warehouses=len(groups), served=len(served), unserved=len(unserved))


def current_plan(task):
    return next((p for p in reversed(task['plans']) if p['input_revision'] == task['input_revision']
        and p['input_sha256'] == app.fingerprint(task['data'])), None)


def has_facts(task):
    return any(t.get('receipts') or t.get('observations') or t.get('temperature_records') for t in task.get('execution_tasks', {}).values())


def view(task):
    plan = current_plan(task)
    confirmed = bool(plan and plan['id'] == task.get('adopted_plan_id'))
    states = {wid: execution.view(t) for wid, t in task.get('execution_tasks', {}).items()} if confirmed else {}
    complete = bool(states) and all(s['complete'] for s in states.values())
    if complete: status = '已回仓交接'
    elif has_facts(task): status = '执行记录中'
    elif confirmed: status = '已确认，待执行'
    elif plan: status = '部分未安排' if plan['summary']['optimized']['unserved'] else '待核对方案'
    else: status = '待准备与计算'
    return dict(status=status, confirmed=confirmed, complete=complete, execution=states,
        lifecycle=task.get('lifecycle', 'active'),
        current_plan_id=plan['id'] if plan else None,
        receipts=sum(len(t.get('receipts', [])) for t in task.get('execution_tasks', {}).values()),
        temperature_records=sum(len(t.get('temperature_records', [])) for t in task.get('execution_tasks', {}).values()),
        boundaries=BOUNDARIES)


class MultiWarehouseStore(app.ApplicationStore):
    def __init__(self, path):
        super().__init__(path)
        with self.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS multiwarehouse_tasks (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, document TEXT NOT NULL)')

    def read(self, task_id):
        with self.connect() as c:
            row = c.execute('SELECT document FROM multiwarehouse_tasks WHERE id=?', (task_id,)).fetchone()
        if row is None: raise ValueError('多仓任务不存在，请刷新任务列表')
        return json.loads(row[0])

    def list(self, lifecycle='active'):
        if lifecycle not in ('active', 'archived', 'trash'):
            raise ValueError('请选择有效的批次列表')
        with self.connect() as c:
            tasks = [json.loads(r[0]) for r in c.execute('SELECT document FROM multiwarehouse_tasks')]
        return [dict(id=t['id'], name=t['name'], revision=t['revision'], updated_at=t['updated_at'],
            warehouses=len(t['data']['warehouses']), orders=len(t['data']['orders']), status=view(t)['status'])
            for t in sorted(tasks, key=lambda t: t['updated_at'], reverse=True)
            if t.get('lifecycle', 'active') == lifecycle]

    def guard(self, task, revision):
        super().guard(task, revision)
        if task.get('lifecycle', 'active') != 'active':
            raise Conflict('请先恢复这个批次，再继续操作')

    def organize(self, task_id, revision, action):
        destinations = {'archive': 'archived', 'trash': 'trash', 'restore': 'active'}
        if action not in destinations:
            raise ValueError('请选择归档、移至回收站或恢复')
        task = self.read(task_id)
        super().guard(task, revision)
        task['lifecycle'] = destinations[action]
        return self.commit(task, revision)

    def _new_task(self, name, data, task_id):
        clean = canonicalize(data); timestamp = app.now()
        return dict(id=task_id, name=app.task_name(name), revision=1, input_revision=1,
            created_at=timestamp, updated_at=timestamp, data=clean, plans=[], adopted_plan_id=None,
            execution_tasks={}, agent_runs=[])

    def create(self, name, data):
        task = self._new_task(name, data, uuid4().hex)
        with self.connect() as c:
            c.execute('INSERT INTO multiwarehouse_tasks VALUES (?,?,?)', (task['id'], 1, app.encode(task)))
        return task

    def find_intake_task(self, request_hash, *, connection=None):
        with self.connect() if connection is None else nullcontext(connection) as c:
            row = c.execute('SELECT document FROM multiwarehouse_tasks WHERE id=?', (request_hash[:32],)).fetchone()
        if row is None:
            return None
        task = json.loads(row[0])
        if task.get('intake_source', {}).get('request_sha256') != request_hash:
            raise Conflict('任务编号冲突，请刷新订单后重试')
        return task

    def create_intake(self, name, data, source, request_hash, *, connection=None):
        task = self._new_task(name, data, request_hash[:32])
        task['intake_source'] = {**deepcopy(source), 'request_sha256': request_hash}
        if app.fingerprint(task['data']) != source['input_sha256']:
            raise ValueError('订单确认数据与任务输入指纹不符')
        with self.connect() if connection is None else nullcontext(connection) as c:
            inserted = c.execute(
                'INSERT INTO multiwarehouse_tasks VALUES (?,?,?) ON CONFLICT(id) DO NOTHING',
                (task['id'], 1, app.encode(task))).rowcount
            if inserted:
                return task, False
            existing = self.find_intake_task(request_hash, connection=c)
            if existing is None:
                raise Conflict('任务已变化，请刷新订单后重试')
            return existing, True

    def commit(self, task, expected):
        task.update(revision=expected+1, updated_at=app.now())
        with self.connect() as c:
            changed = c.execute('UPDATE multiwarehouse_tasks SET revision=?,document=? WHERE id=? AND revision=?',
                (task['revision'], app.encode(task), task['id'], expected)).rowcount
            if changed != 1: raise Conflict('任务已变化，请刷新后核对；本次没有覆盖新内容')
        return task

    def save(self, task_id, revision, name, data):
        task = self.read(task_id); self.guard(task, revision)
        clean = canonicalize(data)
        if app.fingerprint(clean) != app.fingerprint(task['data']):
            if has_facts(task): raise Conflict('已有执行事实，不能改写仓库归属或原输入；请新建批次')
            import decision_changes
            change=decision_changes.history(task,clean)
            if change:
                task.setdefault('condition_changes',[]).append(change)
            task.update(input_revision=task['input_revision']+1, adopted_plan_id=None, execution_tasks={})
        task.update(name=app.task_name(name), data=clean)
        return self.commit(task, revision)

    def calculate(self, task):
        if has_facts(task): raise Conflict('已有执行记录，请计算剩余路线，不能重算原始批次')
        if len(task['plans']) >= 30: raise ValueError('已有30个计算版本，请新建批次；历史保留')
        data = canonicalize(task['data'])
        warehouses = {w['id']: w for w in data['warehouses']}
        results = {wid: warehouse_loading.compare(part, warehouses[wid].get('loading_profile'),
                                                  warehouses[wid].get('operational_policy'),
                                                  fleet=warehouses[wid].get('planning_mode') == 'fleet')
                   for wid, part in split(data).items()}
        checked = audit(data, results, reuse_checked=True)
        plan = dict(id=uuid4().hex, created_at=app.now(), input_revision=task['input_revision'],
            input_sha256=app.fingerprint(data), warehouses=results, audit=checked,
            **summarize(data, results))
        task['plans'].append(plan)
        change=next((c for c in reversed(task.get('condition_changes',[]))
                     if c['input_revision']==task['input_revision']),None)
        if change:
            plan['condition_change_id']=change['id']
            if change['before_plan_id']:
                plan['parent_plan_id']=change['before_plan_id']
        # A new calculation needs a fresh human confirmation, even with identical inputs.
        task.update(adopted_plan_id=None, execution_tasks={})
        return plan

    def solve(self, task_id, revision):
        task = self.read(task_id); self.guard(task, revision); self.calculate(task)
        return self.commit(task, revision)

    def manual(self, task_id, revision, sequences):
        task = self.read(task_id); self.guard(task, revision)
        if has_facts(task): raise Conflict('已有执行事实，请使用剩余路线工具，不能重新分配原货物')
        plan = current_plan(task)
        if not plan: raise ValueError('先计算一个候选，再人工调整')
        if len(task['plans']) >= 30: raise ValueError('已有30份方案，请另建批次')
        groups = split(task['data'])
        if not isinstance(sequences, dict) or set(sequences) != set(groups): raise ValueError('人工调整必须包含全部仓库')
        results = deepcopy(plan['warehouses'])
        for wid, part in groups.items():
            proposed = sequences[wid]
            if not isinstance(proposed, dict) or set(proposed) != {v['id'] for v in part['vehicles']}:
                raise ValueError(wid+'：人工调整必须包含全部车辆（闲置车填空列表）')
            orders = {o['id']: o for o in part['orders']}
            assigned = []
            for seq in proposed.values():
                if not isinstance(seq, list) or any(not isinstance(s, str) or s not in orders for s in seq):
                    raise ValueError(wid+'：包含未知或跨仓任务，不能隐式调拨')
                assigned.extend(seq)
            if len(assigned) != len(set(assigned)) or set(assigned) != set(orders):
                raise ValueError(wid+'：请保留全部任务且各出现一次，不能漏单或重复')
            ev = pilot.evaluator(part); routes = {}
            for v in part['vehicles']:
                route = ev(proposed[v['id']], v, orders)
                if route is None: raise ValueError(wid+'/'+v['id']+'：调整后违反重量、体积、回收、收货时间或收班约束')
                routes[v['id']] = route
            warehouse = next(w for w in task['data']['warehouses'] if w['id'] == wid)
            if 'loading_profile' in warehouse:
                routes = warehouse_loading.manual(part, warehouse['loading_profile'], routes)
            if 'operational_policy' in warehouse:
                routes = warehouse_loading.operational_manual(part, routes, warehouse['operational_policy'],
                                                                compact=warehouse.get('planning_mode') == 'fleet')
            score = app.normal.score(routes)
            result = dict(routes=routes, unserved=[], metrics=dict(served=len(orders), unserved=0,
                late_orders=score[0], late_minutes=round(score[1], 2), cost=round(score[2], 2),
                distance=round(sum(r['distance'] for r in routes.values()), 2)))
            checked = pilot.audit(part, result)
            results[wid].update(optimized=result, audit={**results[wid]['audit'], 'optimized': checked})
            if 'loading_profile' in warehouse:
                results[wid]['audit']['physical_loading'] = warehouse_loading.check_plan(part, warehouse['loading_profile'], result)
                results[wid]['loading_verification'].update(status='VERIFIED_WITHIN_MODEL',
                    checked_boxes=results[wid]['audit']['physical_loading']['checked_boxes'])
                results[wid]['loading_verification'].pop('work', None)
                results[wid]['loading_verification']['source'] = 'manual_candidate_revalidation'
            results[wid]['diagnostics'] = []
            if 'operational_policy' in warehouse:
                results[wid]['operational_verification'] = operational.evaluate_plan(part, result, warehouse['operational_policy'],
                    compact=warehouse.get('planning_mode') == 'fleet', reuse_checked=True,
                    layout=operational.FLEET_LAYOUT if warehouse.get('planning_mode') == 'fleet' else None)
        candidate = dict(id=uuid4().hex, parent_plan_id=plan['id'], method='manual_reorder',
            created_at=app.now(), input_revision=task['input_revision'], input_sha256=app.fingerprint(task['data']),
            warehouses=results, audit=audit(task['data'], results, reuse_checked=True),
            **summarize(task['data'], results))
        task['plans'].append(candidate)
        task.update(adopted_plan_id=None, execution_tasks={})
        return self.commit(task, revision)

    def adopt(self, task_id, revision, plan_id, reviewed):
        task = self.read(task_id); self.guard(task, revision)
        if reviewed is not True: raise ValueError('先核对各仓归属、费用、未安排任务与规则，再确认')
        if has_facts(task): raise Conflict('已有执行事实，不能切换原方案')
        plan = current_plan(task)
        if not plan or plan['id'] != plan_id: raise Conflict('候选不是当前输入的最新方案，请刷新核对')
        checked = audit(task['data'], plan['warehouses'])
        if checked['unserved']: raise ValueError('还有配送任务未安排，不能确认整个多仓批次')
        task['execution_tasks'] = {}
        for wid, part in split(task['data']).items():
            child_plan = dict(id=plan_id+'_'+wid, input_revision=task['input_revision'],
                input_sha256=app.fingerprint(part), result=deepcopy(plan['warehouses'][wid]))
            task['execution_tasks'][wid] = dict(id=task_id+'_'+wid, revision=1,
                input_revision=task['input_revision'], data=part, plans=[child_plan],
                adopted_plan_id=child_plan['id'], receipts=[], observations=[], temperature_records=[])
            warehouse = next(w for w in task['data']['warehouses'] if w['id'] == wid)
            if 'loading_profile' in warehouse:
                task['execution_tasks'][wid]['physical_loading_profile'] = deepcopy(warehouse['loading_profile'])
        task['adopted_plan_id'] = plan_id
        return self.commit(task, revision)

    def action(self, task_id, revision, warehouse_id, operation, payload, reviewed=False):
        task = self.read(task_id); self.guard(task, revision)
        if not view(task)['confirmed']: raise Conflict('请先确认当前多仓方案')
        child = task['execution_tasks'].get(warehouse_id)
        if child is None: raise ValueError('仓库不存在')
        if operation == 'receipt': execution.append(child, payload, reviewed)
        elif operation == 'observe': observation.append(child, payload, reviewed)
        elif operation == 'temperature': execution.append_temperature(child, payload, reviewed)
        elif operation == 'replan':
            self.calculate_remaining(child)
        elif operation == 'adopt-remaining':
            if reviewed is not True: raise ValueError('先核对剩余顺序与风险，再采用')
            candidate = next((p for p in child.get('remaining_plans', []) if p['id'] == payload), None)
            if not candidate or candidate['base_sha256'] != remaining.stamp(child) or candidate['adopted_at']:
                raise Conflict('执行或顺序已经变化，候选已失效，请重新计算')
            remaining.audit(child, candidate['sequences'])
            warehouse_loading.remaining_check(child, candidate['sequences'])
            child.update(remaining_sequences=deepcopy(candidate['sequences']), active_remaining_plan_id=candidate['id'])
            candidate['adopted_at'] = app.now()
        else: raise ValueError('不支持的执行操作')
        return self.commit(task, revision)

    @staticmethod
    def calculate_remaining(child):
        if child.get('data', {}).get('operational_policy'):
            raise ValueError('本批次含共享冷链箱与沿途空间模型；当前只验证出发前候选，执行变化后须人工复核设备时限与实际箱位，不能沿用旧温控结果自动改序')
        if len(child.get('remaining_plans', [])) >= 30: raise ValueError('剩余方案达到30份上限')
        candidate = remaining.solve(child)
        candidate['physical_loading'] = warehouse_loading.remaining_check(child, candidate['sequences'])
        candidate.update(id=uuid4().hex, created_at=app.now(), adopted_at=None)
        child.setdefault('remaining_plans', []).append(candidate)
        return candidate

    def export_csv(self, task_id, revision):
        task = self.read(task_id); self.guard(task, revision)
        if not view(task)['confirmed']: raise Conflict('请先确认当前完整多仓方案再导出配送清单')
        output = io.StringIO(newline=''); writer = csv.writer(output)
        writer.writerow(['批次', '仓库', '方案', '车辆', '顺序', '配送任务', '预计到达分钟', '预计完成分钟',
            '预计纸箱数', '预计周转箱数', '估算离店kg', '估算离店m3', '车次费用元_每车仅首行填写', '数据来源', '声明'])
        plan = current_plan(task)
        for wid, result in plan['warehouses'].items():
            kind = next(w['settings']['data_kind'] for w in task['data']['warehouses'] if w['id'] == wid)
            for vid, route in result['optimized']['routes'].items():
                route = route.get('operational', {}).get('adjusted_route') or route
                for i, stop in enumerate(route['stops'], 1):
                    writer.writerow([task_id, wid, plan['id'], vid, i, stop['id'], stop['arrival'], stop['finish'],
                        stop.get('pickup', {}).get('cartons', ''), stop.get('pickup', {}).get('totes', ''),
                        stop.get('onboard_weight_kg', ''), stop.get('onboard_volume_m3', ''),
                        route['cost'] if i == 1 else '', kind, '本机确认的出发前预测；未向企业派单，非正式结算'])
        return '\ufeff'+output.getvalue()

    def export_package(self, task_id, revision):
        task = self.read(task_id); self.guard(task, revision)
        states = view(task)
        return dict(schema_version=SCHEMA, dispatch_status='not_sent', integration_status='not_connected',
            task=task, state=states, boundaries=BOUNDARIES,
            facts_classification='locally_recorded_unverified', forecast_classification='estimated_not_fulfillment',
            pending=['企业仓库/订单/门店/车辆编号映射', '配送日期与时区', '库存与可调拨范围', '接口、鉴权与接收结果'])
