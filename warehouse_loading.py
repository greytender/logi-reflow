"""Explicit optional departure geometry for the multiwarehouse workbench.

Only declared cuboids are checked. Return packaging remains a weight/volume
forecast, and geometry never certifies cold-chain, handling time or field safety.
"""
from copy import deepcopy

import loading
import loading_audit
import normal
import pilot

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


def compare(data, profile=None):
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
