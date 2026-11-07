"""Explicit optional departure geometry for the multiwarehouse workbench.

Only declared cuboids are checked. Return packaging remains a weight/volume
forecast, and geometry never certifies cold-chain, handling time or field safety.
"""
from copy import deepcopy
from math import ceil, floor

import loading
import loading_audit
import normal
import pilot
import operational_constraints as operational
import fleet_planning

BOUNDARIES = [
    '明确箱规和可用车厢的长方体模型；尾门在+x，仅允许保持向上的底面旋转',
    '逐箱不重叠、全底面支撑、累计承重、易碎不压、按站直线卸货及装卸步骤独立核验',
    '有限构造未找到不代表无解；装载不含轮包、轴荷、人员通道、实际装卸工时及温控合规',
    '空包装回收仍按重量和总体积逐站检查，未验证回收物的几何摆放',
]


def rank(data):
    return (lambda routes: normal.score(routes)+(round(sum(r['distance'] for r in routes.values()), 4),)) if 'business' in data else normal.score


def evaluator(data, profile):
    vehicles, items = loading.profile(data, profile)
    route_eval = pilot.evaluator(data)
    cache = {}
    work = dict(route_checks=0, placement_checks=0, loading_rejections=0, budget_exhausted=False)

    def evaluate(sequence, vehicle, orders):
        key = (vehicle['id'], tuple(sequence))
        if key in cache:
            return deepcopy(cache[key])
        route = route_eval(sequence, vehicle, orders)
        if route is None:
            cache[key] = None
            return None
        group = [i for i in items if i['order'] in sequence]
        work['route_checks'] += 1
        packed = loading.constructive(group, vehicles[vehicle['id']], sequence, work)
        if packed is None:
            work['loading_rejections'] += 1
            cache[key] = None
            return None
        route['loading'] = packed
        cache[key] = route
        return deepcopy(route)

    return evaluate, vehicles, items, work


def check_plan(data, profile, plan):
    """Audit persisted coordinates/steps directly, never rerun the constructor."""
    vehicles, items = loading.profile(data, profile)
    checked = 0
    for vid, route in plan['routes'].items():
        group = [i for i in items if i['order'] in route['sequence']]
        packing = route.get('loading')
        if not isinstance(packing, dict):
            raise ValueError('独立装载核验失败：'+vid+'缺少当前路线的箱体坐标与步骤')
        audit = loading_audit.audit(group, vehicles[vid], route['sequence'], packing.get('placements', []))
        steps = loading_audit.audit_steps(group, vehicles[vid], route['sequence'], packing.get('placements', []),
            packing.get('load_steps', []), packing.get('unload_steps', []))
        if not audit['passed'] or not steps['passed']:
            raise ValueError('独立装载核验失败：'+vid+'：'+'；'.join((audit['errors']+steps['errors'])[:3]))
        checked += len(group)
    return dict(passed=True, checked_boxes=checked, scope='departure_delivery_cuboids_only', boundaries=BOUNDARIES)


def compare(data, profile=None, policy=None, *, fleet=False):
    if fleet:
        return fleet_compare(data, profile, policy)
    if policy is not None:
        return operational_compare(data, profile, policy)
    result = pilot.compare(data)
    result['diagnostics'] = diagnose(data, result['optimized']['unserved'])
    if profile is None:
        result['loading_verification'] = dict(status='NOT_CONFIGURED', boxes=None,
            reason='尚无经确认的箱规、车厢及堆叠规则；目前只检查重量和总体积', boundaries=BOUNDARIES)
        return result
    ev, vehicles, items, work = evaluator(data, profile)
    plan = normal.solve(data, validator=pilot.validate, evaluator=ev, search_rounds=2, scorer=rank(data))
    plan.pop('data')
    plan.update(simulated=data['settings']['data_kind'] == 'simulated',
        method='多起点路线插入/移位与有限三维装载联合核验，无全局最优保证')
    # A previously found good route is retained only when its actual packing also passes.
    prior = result['optimized']
    prior_routes = {v['id']: ev(prior['routes'][v['id']]['sequence'], v, {o['id']: o for o in data['orders']})
                    for v in data['vehicles']}
    if all(r is not None for r in prior_routes.values()) and (
        len(prior['unserved']), rank(data)(prior_routes)) < (len(plan['unserved']), rank(data)(plan['routes'])):
        plan = deepcopy(prior)
        plan.update(routes=prior_routes, method='保留经三维装载核验的原路线候选')
    for item in plan['unserved']:
        group = [i for i in items if i['order'] == item['id']]
        order = next(o for o in data['orders'] if o['id'] == item['id'])
        eligible = [v for v in data['vehicles'] if v['id'] in order['allowed_vehicles']]
        bounds = [loading_audit.geometry_bound(group, vehicles[v['id']]) for v in eligible]
        proved = bool(bounds) and all(b['status'] == 'PROVEN_GEOMETRY_INFEASIBLE' for b in bounds)
        item.update(code='PROVEN_GEOMETRY_INFEASIBLE' if proved else 'LOADING_OR_ROUTE_SEARCH_NOT_FOUND',
            reason='整店货物在所有准入车辆的允许朝向中无法几何容纳' if proved else '路线/装载有限搜索未找到联合方案，尚未证明无解',
            action='核对箱规与可用车厢，联系适配车辆；不自动拆单或修改货物尺寸')
    result['optimized'] = plan
    result['diagnostics'] = diagnose(data, plan['unserved'])
    result['audit']['optimized'] = pilot.audit(data, plan)
    result['audit']['physical_loading'] = check_plan(data, profile, plan)
    result['loading_verification'] = dict(status='INCOMPLETE' if plan['unserved'] else 'VERIFIED_WITHIN_MODEL',
        boxes=len(items), checked_boxes=result['audit']['physical_loading']['checked_boxes'], work=work,
        boundaries=BOUNDARIES)
    result['comparison_scope']['same_served_stops'] = (
        {s for r in result['baseline']['routes'].values() for s in r['sequence']} ==
        {s for r in plan['routes'].values() for s in r['sequence']})
    result['status'] = '部分任务未安排，需人工处理' if plan['unserved'] else '路线及声明装载模型通过，待人工核对'
    result['boundaries'] = BOUNDARIES + ['道路与费用仍为输入试算，非TMS结算、GPS或现场作业证明']
    return result


def operational_reason(report):
    errors = report.get('errors', [])
    return '；'.join(str(e.get('message', e)) if isinstance(e, dict) else str(e) for e in errors[:3]) or '声明约束未通过'


def operational_evaluator(data, policy, profile=None):
    """Filter every search move through declared cold-chain and spatial rules."""
    basic = evaluator(data, profile)[0] if profile is not None else pilot.evaluator(data)
    cache = {}
    def evaluate(sequence, vehicle, orders):
        key = vehicle['id'], tuple(sequence)
        if key not in cache:
            route = basic(sequence, vehicle, orders)
            if route is not None:
                check = operational.evaluate_route(data, route, vehicle, policy)
                if not check.get('passed'):
                    route = None
                else:
                    route['operational'] = check
            cache[key] = route
        return deepcopy(cache[key])
    return evaluate


def operational_manual(data, routes, policy, *, compact=False):
    checked = {}
    for vehicle in data['vehicles']:
        route = deepcopy(routes[vehicle['id']])
        report = operational.evaluate_route(data, route, vehicle, policy, **(operational.FLEET_LAYOUT if compact else {}))
        if not report.get('passed'):
            raise ValueError(vehicle['id']+'：'+operational_reason(report))
        route['operational'] = operational.compact_report(report) if compact else report
        checked[vehicle['id']] = route
    return checked


def shared_box_groups(data, policy):
    """Stores linked by a shared cold box must use one eligible vehicle."""
    parent = {o['id']: o['id'] for o in data['orders']}
    def root(x):
        while parent[x] != x:
            x = parent[x]
        return x
    cold_groups = {}
    for goods in (policy or {}).get('goods', []):
        if goods['kind'] == 'cold': cold_groups.setdefault(goods['cold_box_id'], []).append(goods['order'])
    for members in cold_groups.values():
        for member in members[1:]: parent[root(member)] = root(members[0])
    components = {}
    for members in cold_groups.values():
        for member in members: components.setdefault(root(member), set()).add(member)
    choices = []
    for members in components.values():
        eligible = {v['id'] for v in data['vehicles']}
        for order in data['orders']:
            if order['id'] in members: eligible &= set(order['allowed_vehicles'])
        for box in policy['cold_boxes']:
            if any(s in members for s in cold_groups[box['id']]): eligible &= set(box['allowed_vehicles'])
        choices.append((members, sorted(eligible)))
    return choices


def operational_screen(data, policy):
    """Check inexpensive necessary bounds before building any cargo frames."""
    lines = {(order['id'], line['sku']): line for order in data['orders'] for line in order['lines']}
    entities = {order['id']: sum(count>0 for count in order.get('pickup',{}).values())
                if 'business' in data else 0 for order in data['orders']}
    orders = {order['id']:order for order in data['orders']}
    regular = {sid:[] for sid in orders}
    cold = {sid: set() for sid in entities}
    boxes = {item['id']: item for item in policy['cold_boxes']}
    energy = {item['vehicle_id']: item for item in policy.get('energy', [])}
    prep = {item['vehicle_id']: item for item in policy.get('preparation', [])}
    geometry = {item['id']: item for item in policy['vehicle_geometry']}
    for item in policy['goods']:
        sid = item['order']
        if item['kind'] == 'cold':
            cold[sid].add(item['cold_box_id'])
        else:
            entities[sid] += lines[sid, item['sku']]['quantity']
            regular[sid].append(item)
    for item in policy.get('returns', []):
        entities[item['order']] += item['quantity']

    def check(route, vehicle):
        vid = vehicle['id']
        issue = operational.readiness_issue(route, vehicle, prep.get(vid), energy.get(vid))
        if issue:
            return issue
        count = sum(entities[sid] for sid in route['sequence'])
        count += len({bid for sid in route['sequence'] for bid in cold[sid]})
        if count > operational.MAX_ENTITIES:
            return dict(code='entity_budget', message='这条路线的装卸实体超过核验上限',
                        vehicle_id=vid, stop_id=None)
        # Match the finite lane constructor's uniform-box grid before paying
        # for the full action replay. This rejects only that construction.
        goods=[item for sid in route['sequence'] for item in regular[sid]]
        shapes={(item['length_cm'],item['width_cm'],item['height_cm'],
                 lines[item['order'],item['sku']]['unit_weight_kg'],item['max_top_load_kg']) for item in goods}
        if len(shapes)==1:
            geom=geometry[vid]
            cold_items=[boxes[bid] for bid in {bid for sid in route['sequence'] for bid in cold[sid]}]
            returns=[policy['pickup_geometry'][kind]
                     for sid in route['sequence'] for key,kind in [('cartons','carton'),('totes','tote')]
                     if orders[sid].get('pickup',{}).get(key,0)]
            def reserve(items):
                return (ceil(sum(item['length_cm'] for item in items)/geom['length_cm'])*
                        max(item['width_cm'] for item in items)) if items else 0
            reserved=reserve(cold_items)+reserve(returns)
            dx,dy,dz,weight,top_load=next(iter(shapes))
            if reserved+dy<=geom['width_cm']+1e-7:
                levels=min(floor(geom['height_cm']/dz),floor(top_load/weight)+1)
                available=floor(geom['length_cm']/dx)*floor((geom['width_cm']-reserved)/dy)*levels
                demand=sum(lines[item['order'],item['sku']]['quantity'] for item in goods)
                if demand>available:
                    return dict(code='layout_capacity',message='当前通道构造未找到足够箱位，可调整分车后再核对',
                                vehicle_id=vid,stop_id=None)
        for stop in route['stops']:
            for bid in cold[stop['id']]:
                box = boxes[bid]
                valid_until = box.get('activated_at_minutes', vehicle['shift_start']) + box['valid_minutes']
                if stop['finish'] > valid_until + .0001:
                    return dict(code='cold_duration', message='预计交付已超出该箱的保温时限',
                                vehicle_id=vid, stop_id=stop['id'])
        return None

    return check


def fleet_compare(data, profile, policy):
    if policy is not None:
        policy = operational.validate_policy(data, policy)
    base = pilot.baseline(data)
    groups = shared_box_groups(data, policy)
    grouped = {sid for members, _ in groups for sid in members}
    groups.extend(({o['id']}, o['allowed_vehicles']) for o in data['orders'] if o['id'] not in grouped)
    packed = evaluator(data, profile)[0] if profile is not None else None
    orders = {o['id']: o for o in data['orders']}
    screen = operational_screen(data, policy) if policy is not None else None

    def qualify(route, vehicle):
        if screen is not None:
            issue = screen(route, vehicle)
            if issue:
                return None, issue
        if packed is not None:
            checked = packed(route['sequence'], vehicle, orders)
            if checked is None:
                return None, dict(code='departure_geometry', message='该路线未找到可卸出的箱位',
                                  vehicle_id=vehicle['id'], stop_id=None)
            route = checked
        if policy is not None:
            report = operational.evaluate_route(data, route, vehicle, policy, **operational.FLEET_LAYOUT)
            if not report['passed']:
                return None, report['errors'][0]
            route = {**route, 'operational': operational.compact_report(report)}
        return route, None

    plan = fleet_planning.solve(data, base, groups, qualify if packed or policy else None, screen=screen)
    checks = dict(baseline=pilot.audit(data, base), optimized=pilot.audit(data, plan))
    result = dict(data=data, baseline=base, optimized=plan, audit=checks,
        comparison_scope=dict(same_inputs=True, same_served_stops=served_set(base) == served_set(plan),
                              interpretation='用同一批门店、车辆和计费规则复核'),
        diagnostics=diagnose(data, plan['unserved']),
        status='有门店待安排' if plan['unserved'] else '待核对方案', boundaries=BOUNDARIES,
        planning_mode='fleet')
    if profile is not None:
        # Idle vehicles require an empty, valid packing witness as well.
        for vehicle in data['vehicles']:
            vid = vehicle['id']
            if not plan['routes'][vid]['sequence']:
                plan['routes'][vid] = packed([], vehicle, orders)
        checks['physical_loading'] = check_plan(data, profile, plan)
    if policy is not None:
        result['baseline_operational_verification'] = operational.evaluate_plan(data, base, policy, compact=True,
            layout=operational.FLEET_LAYOUT)
        result['operational_verification'] = operational.evaluate_plan(data, plan, policy, compact=True,
            reuse_checked=True, layout=operational.FLEET_LAYOUT)
        result['loading_verification'] = dict(status='DECLARED_OPERATIONAL_MODEL',
            reason='已按声明箱规检查车厢、冷链和回收步骤')
    elif profile is not None:
        result['loading_verification'] = dict(status='INCOMPLETE' if plan['unserved'] else 'VERIFIED_WITHIN_MODEL',
            checked_boxes=checks['physical_loading']['checked_boxes'], boundaries=BOUNDARIES)
    else:
        result['loading_verification'] = dict(status='NOT_CONFIGURED', boxes=None,
            reason='补齐箱规和车厢资料后可核对装卸', boundaries=BOUNDARIES)
    return result


def served_set(plan):
    return {sid for route in plan['routes'].values() for sid in route['sequence']}


def operational_compare(data, profile, policy):
    policy = operational.validate_policy(data, policy)
    # Keep the exact rule-based route for independent comparison.
    result = pilot.compare(data)
    base = deepcopy(result['baseline'])
    base_check = operational.evaluate_plan(data, base, policy)
    result['baseline_operational_verification'] = base_check
    def operational_rank(routes):
        actual = [r.get('operational', {}).get('adjusted_route', r) for r in routes.values()]
        moves = sum(r.get('operational', {}).get('time_adjustments', {}).get('rehandle_moves', 0) for r in routes.values())
        risk_moves = sum(r.get('operational', {}).get('time_adjustments', {}).get('high_risk_rehandle_moves', 0) for r in routes.values())
        return (sum(r.get('late_orders', 0) for r in actual),
            round(sum(r.get('late_minutes', 0) for r in actual), 4),
            round(sum(r['cost'] for r in routes.values()), 4), risk_moves, moves,
            round(sum(r.get('return_at', 0) for r in actual), 4),
            round(sum(r['distance'] for r in routes.values()), 4))
    # Enumerate a bounded set of vehicle assignments before route insertion.
    from itertools import product, islice
    choices = [(members, allowed or [None]) for members, allowed in shared_box_groups(data, policy)]
    combinations = 1
    for _, candidates in choices: combinations *= len(candidates)
    candidates = []
    for assignment in islice(product(*(c for _, c in choices)), 64):
        search_data = deepcopy(data)
        for (members, _), vid in zip(choices, assignment):
            for order in search_data['orders']:
                if order['id'] in members: order['allowed_vehicles'] = [vid] if vid else []
        ev = operational_evaluator(search_data, policy, profile)
        candidate = normal.solve(search_data, validator=pilot.validate, evaluator=ev,
            search_rounds=2, scorer=operational_rank)
        candidates.append(candidate)
    plan = min(candidates, key=lambda p:(len(p['unserved']), operational_rank(p['routes'])))
    plan.pop('data')
    plan.update(simulated=data['settings']['data_kind'] == 'simulated',
        method='整店路线插入/移位＋共享冷链箱、沿途回收空间与装卸时间硬核验')
    for rejected in plan['unserved']:
        rejected.update(code='DECLARED_POLICY_SEARCH_NOT_FOUND',
            reason='在声明冷链、空间、装卸时间与路线约束下，有限搜索未找到安排；不证明无解',
            action='核对设备时限、共享箱归属、回收空间与可用车辆；不自动放宽温度或门店承诺')
    result['optimized'] = plan
    result['audit']['optimized'] = pilot.audit(data, plan)
    result['operational_verification'] = operational.evaluate_plan(data, plan, policy)
    result['operational_verification']['search'] = dict(shared_group_assignments_checked=len(candidates),
        assignment_budget=64, budget_exhausted=combinations>64,
        interpretation='共享箱整组分车＋有限路线搜索，不证明全局最优或无解')
    result['diagnostics'] = diagnose(data, plan['unserved'])
    result['comparison_scope'].update(same_served_stops=(
        {s for r in base['routes'].values() for s in r['sequence']} ==
        {s for r in plan['routes'].values() for s in r['sequence']}),
        baseline_policy_passed=base_check.get('passed', False),
        interpretation='基线未通过相同冷链及空间约束时，只比较可行性差异，不能称等约束降本')
    if profile is not None:
        result['audit']['physical_loading'] = check_plan(data, profile, plan)
    result['loading_verification'] = dict(status='DECLARED_OPERATIONAL_MODEL',
        reason='共享运输箱与沿途状态以约束核验及三维操作室为准；不是实测温控认证')
    result['status'] = '部分任务未安排，需人工处理' if plan['unserved'] else '声明冷链与沿途空间模型通过，待人工核对'
    return result


def manual(data, profile, routes):
    ev, _, _, _ = evaluator(data, profile)
    orders = {o['id']: o for o in data['orders']}
    checked = {}
    for vehicle in data['vehicles']:
        vid = vehicle['id']
        checked[vid] = ev(routes[vid]['sequence'], vehicle, orders)
        if checked[vid] is None:
            raise ValueError(vid+'：调整后的路线/三维装载有限核验未通过，不能确认为可装卸方案')
    return checked


def diagnose(data, unserved):
    """Explain individual hard-rule failures without claiming a global infeasibility proof."""
    orders = {o['id']: o for o in data['orders']}
    ev = pilot.evaluator(data)
    without_pickup = deepcopy(data)
    without_pickup.pop('business', None)
    forward = pilot.evaluator(without_pickup)
    reports = []
    for rejected in unserved:
        order = orders[rejected['id']]
        vehicles = []
        for vehicle in data['vehicles']:
            if vehicle['id'] not in order['allowed_vehicles']:
                code, reason = 'VEHICLE_NOT_ALLOWED', '该车辆不在门店准入列表'
            elif order['unit_weight_kg'] > vehicle['max_weight_kg']+1e-8 or order['unit_volume_m3'] > vehicle['max_volume_m3']+1e-8:
                code, reason = 'DELIVERY_CAPACITY', '整店正向货重或总体积超过车辆可用容量'
            elif forward([order['id']], vehicle, orders) is None:
                code, reason = 'WINDOW_OR_SHIFT', '单独执行也不能满足静态道路、收货截止或回仓收班'
            elif ev([order['id']], vehicle, orders) is None:
                code, reason = 'PICKUP_CAPACITY', '单独正向配送可行，但先卸后收的包装载荷超过容量'
            else:
                code, reason = 'INDIVIDUALLY_FEASIBLE', '单独路线通过已提供约束；组合搜索或可选几何仍需核对'
            vehicles.append(dict(vehicle_id=vehicle['id'], code=code, reason=reason))
        reports.append(dict(task_id=order['id'], code=rejected.get('code', 'SEARCH_NOT_FOUND'),
            reason=rejected['reason'], vehicles=vehicles,
            interpretation='逐车单任务检查；不证明整批路线或未提供规则的全局可行性'))
    return reports


def remaining_check(child, sequences):
    """Preserve original delivery placements when reviewing an in-transit reorder."""
    profile = child.get('physical_loading_profile')
    if profile is None:
        return dict(status='NOT_CONFIGURED', passed=True)
    vehicles, items = loading.profile(child['data'], profile)
    original = next(p for p in child['plans'] if p['id'] == child['adopted_plan_id'])['result']['optimized']
    checked = 0
    for vid, sequence in sequences.items():
        group = [i for i in items if i['order'] in sequence]
        ids = {i['id'] for i in group}
        placements = [p for p in original['routes'][vid]['loading']['placements'] if p['id'] in ids]
        audit = loading_audit.audit(group, vehicles[vid], sequence, placements)
        if not audit['passed']:
            raise ValueError(vid+'：原摆放下剩余改序未通过装卸核验；需要人工确认倒货步骤，不能自动重摆在途货物')
        checked += len(group)
    return dict(status='VERIFIED_DELIVERY_CARGO_ONLY', passed=True, checked_boxes=checked,
        boundary='保留原配送箱坐标；回收包装仅有重量体积核验，未证明沿途几何装载或现场可执行')
