"""Exhaustive small-route diagnosis under declared operational constraints.

All assignments and stop orders are enumerated, but loading is constructed by
the selected finite constructor. This is never a geometric/global optimum or
an enterprise performance evaluation. No dispatch or database is changed.
"""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from hashlib import sha256
from itertools import permutations, product
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import application
import operational_constraints as op
import pilot
import warehouse_loading


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             allow_nan=False).encode('utf-8')).hexdigest()


def fixtures():
    """Predeclared variants, including failures; no result-based filtering."""
    cases = []
    specifications = (
        ('shared_default', '共享冷链箱、常规回收和宽时间窗'),
        ('cold_50', '仅末店有冷链，声明有效期50分钟'),
        ('cold_35', '仅末店有冷链，声明有效期35分钟'),
        ('tight_windows', '逐店硬收货截止为570/585/600分钟'),
        ('high_pickup', '各店回收30只纸箱、12只周转箱'),
        ('no_rehandling', '禁止临时倒货'),
        ('compact_space', '构造紧凑车厢180×80×90厘米'),
        ('tight_shift', '同一车队收班截止为580分钟'),
    )
    for name, description in specifications:
        data, _ = op.sample_data()
        if name.startswith('cold_'):
            for order in data['orders'][:-1]:
                order['lines'] = [x for x in order['lines'] if x['sku'] != 'COLD_UNIT']
        if name == 'tight_windows':
            for index, order in enumerate(data['orders']):
                order.update(due=570+15*index, close=570+15*index)
        if name == 'high_pickup':
            for order in data['orders']:
                order['pickup'] = dict(cartons=30, totes=12)
        if name == 'tight_shift':
            for vehicle in data['vehicles']:
                vehicle['shift_end'] = 580
        data = application.canonicalize(data)
        policy = op.sample_policy(data)
        if name.startswith('cold_'):
            policy['cold_boxes'][0]['valid_minutes'] = int(name.split('_')[1])
        if name == 'no_rehandling':
            policy['handling']['allow_rehandling'] = False
        if name == 'compact_space':
            for vehicle in policy['vehicle_geometry']:
                vehicle.update(length_cm=180, width_cm=80, height_cm=90)
        policy = op.validate_policy(data, policy)
        cases.append(dict(id=name, description=description, data=data, policy=policy))
    from tests.operational_packing_fixtures import packing_counterexample
    data, policy = packing_counterexample()
    cases.append(dict(id='packing_order_mechanism',
                      description='预构造A/B/C装载顺序反例，仅验证机制，不是独立业务收益样本',
                      data=data, policy=policy))
    return cases


def assignment_sequences(data, policy):
    """Complete labelled-vehicle assignments, with unsplit shared boxes."""
    ids = [o['id'] for o in data['orders']]
    vehicles = [v['id'] for v in data['vehicles']]
    groups = {}
    for goods in policy['goods']:
        if goods['kind'] == 'cold':
            groups.setdefault(goods['cold_box_id'], set()).add(goods['order'])
    for assignment in product(*(o['allowed_vehicles'] for o in data['orders'])):
        owner = dict(zip(ids, assignment))
        if any(len({owner[s] for s in group}) != 1 for group in groups.values()):
            continue
        by_vehicle = {vid: [s for s in ids if owner[s] == vid] for vid in vehicles}
        for orders in product(*(permutations(by_vehicle[vid]) for vid in vehicles)):
            yield dict(zip(vehicles, (list(seq) for seq in orders)))


def nominal_plan(routes):
    all_routes = list(routes.values())
    return dict(routes=routes, unserved=[], metrics=dict(
        served=sum(len(r['sequence']) for r in all_routes), unserved=0,
        late_orders=sum(r['late_orders'] for r in all_routes),
        late_minutes=round(sum(r['late_minutes'] for r in all_routes), 2),
        cost=round(sum(r['cost'] for r in all_routes), 2),
        distance=round(sum(r['distance'] for r in all_routes), 2)))


def rank_reports(routes, reports):
    adjusted = [r['adjusted_route'] for r in reports.values()]
    return (
        sum(r['late_orders'] for r in adjusted),
        round(sum(r['late_minutes'] for r in adjusted), 4),
        round(sum(r['cost'] for r in routes.values()), 4),
        sum(r.get('time_adjustments', {}).get('high_risk_rehandle_moves', 0) for r in reports.values()),
        sum(r.get('time_adjustments', {}).get('rehandle_moves', 0) for r in reports.values()),
        round(sum(r['return_at'] for r in adjusted), 4),
        round(sum(r['distance'] for r in routes.values()), 4))


def summarize(data, routes, reports):
    vehicles = {v['id']: v for v in data['vehicles']}
    return dict(
        sequences={vid: r['sequence'] for vid, r in routes.items()},
        rank=list(rank_reports(routes, reports)),
        used_vehicles=[vid for vid, r in routes.items() if r['sequence']],
        load_by_vehicle={vid: dict(
            weight_utilization=reports[vid]['adjusted_route']['weight_kg']/vehicles[vid]['max_weight_kg'],
            volume_utilization=reports[vid]['adjusted_route']['volume_m3']/vehicles[vid]['max_volume_m3'])
            for vid, r in routes.items() if r['sequence']},
        cost=round(sum(r['cost'] for r in routes.values()), 4),
        distance_km=round(sum(r['distance'] for r in routes.values()), 4),
        late_orders=sum(r['adjusted_route']['late_orders'] for r in reports.values()),
        latest_return_minutes=max(r['adjusted_route']['return_at'] for r in reports.values()),
        handling_minutes=round(sum(r.get('time_adjustments', {}).get('extra_minutes', 0) for r in reports.values()), 4),
        high_risk_rehandle_moves=sum(r.get('time_adjustments', {}).get('high_risk_rehandle_moves', 0) for r in reports.values()),
        rehandle_moves=sum(r.get('time_adjustments', {}).get('rehandle_moves', 0) for r in reports.values()),
        independent_action_audits={vid: op.audit_actions(r) for vid, r in reports.items()})


def guardrails(reference, candidate, max_load_drop=.02):
    if reference is None or candidate['late_orders']:
        return False
    if set(reference['used_vehicles']) != set(candidate['used_vehicles']):
        return False
    if candidate['cost'] > reference['cost']+1e-7:
        return False
    return all(candidate['load_by_vehicle'][vid][key]+max_load_drop+1e-7 >= value
               for vid, load in reference['load_by_vehicle'].items()
               for key, value in load.items())


def exhaustive(data, policy, reference=None, max_plans=100000, route_mode='legacy'):
    """Enumerate stop assignments/orders; layout search remains finite."""
    if max_plans < 1:
        raise ValueError('max_plans must be positive')
    if route_mode not in ('legacy', 'portfolio', 'fallback'):
        raise ValueError('unknown route mode')
    policy = op.validate_policy(data, policy)
    basic = pilot.evaluator(data)
    vehicles = {v['id']: v for v in data['vehicles']}
    orders = {o['id']: o for o in data['orders']}
    cache = {}
    failures = Counter()
    unique_errors = Counter()
    route_status_counts = Counter()
    layout_budget_limits = entity_budget_limits = 0
    placement_checks_total = total_layout_trials = 0
    base_route_rejections = operational_route_rejections = 0
    best = guarded_best = None
    best_payload = guarded_payload = None
    checked = feasible = guarded = 0
    exhausted = False
    start = perf_counter()
    for sequences in assignment_sequences(data, policy):
        if checked >= max_plans:
            exhausted = True
            break
        checked += 1
        routes, reports = {}, {}
        valid = True
        for vid, sequence in sequences.items():
            key = vid, tuple(sequence)
            if key not in cache:
                route = basic(sequence, vehicles[vid], orders)
                report = None
                if route is not None:
                    work = {'placement_checks': 0}
                    report = (op.evaluate_route(data, route, vehicles[vid], policy, _work=work)
                              if route_mode == 'legacy' else
                              op.search_route_layouts(data, route, vehicles[vid], policy, max_variants=6,
                                                      stop_on_first_feasible=route_mode == 'fallback'))
                    search = report.get('layout_search', {})
                    trials = search.get('trials', [])
                    codes = {e['code'] for e in report['errors']}
                    codes.update(code for trial in trials for code in trial['error_codes'])
                    unique_errors.update(codes)
                    route_status_counts[report['status']] += 1
                    layout_budget_limits += int(search.get('budget_exhausted', False) or
                                                report['status'] == 'SEARCH_BUDGET_EXHAUSTED')
                    entity_budget_limits += int('entity_budget' in codes)
                    placement_checks_total += search.get('placement_checks', work['placement_checks'])
                    total_layout_trials += len(trials) if search else 1
                    operational_route_rejections += int(not report['passed'])
                else:
                    base_route_rejections += 1
                cache[key] = route, report
            route, report = cache[key]
            if route is None:
                failures['base_route_rejected'] += 1
                valid = False
                break
            if not report['passed']:
                failures.update({e['code'] for e in report['errors']})
                valid = False
                break
            routes[vid], reports[vid] = route, report
        if not valid:
            continue
        pilot.audit(data, nominal_plan(routes))
        row = summarize(data, routes, reports)
        if not all(audit['passed'] for audit in row['independent_action_audits'].values()):
            raise AssertionError('candidate failed independent recorded-action audit')
        feasible += 1
        if best is None or row['rank'] < best['rank']:
            best, best_payload = row, deepcopy(dict(routes=routes, reports=reports))
        if guardrails(reference, row):
            guarded += 1
            if guarded_best is None or row['rank'] < guarded_best['rank']:
                guarded_best, guarded_payload = row, deepcopy(dict(routes=routes, reports=reports))
    return dict(route_mode=route_mode, checked_plans=checked, feasible_complete_plans=feasible,
                guarded_plans=guarded, unique_route_evaluations=len(cache),
                enumeration_complete=not exhausted, budget_exhausted=exhausted,
                enumeration_scope='all labelled-vehicle assignments and stop orders visited; no claim that layout search is exhaustive',
                budget_exhausted_scope='full-plan enumeration max_plans only; layout/entity limits are counted separately',
                unique_route_layout_budget_exhaustions=layout_budget_limits,
                unique_route_entity_budget_limits=entity_budget_limits,
                unique_route_base_rejections=base_route_rejections,
                unique_route_operational_rejections=operational_route_rejections,
                unique_route_error_counts=dict(sorted(unique_errors.items())),
                unique_route_error_count_scope='union of error codes across all layout trials per unique route, including rejected trials of an eventually feasible route',
                unique_route_status_counts=dict(sorted(route_status_counts.items())),
                placement_checks_total=placement_checks_total,
                total_layout_trials=total_layout_trials,
                search_work_scope='actual unique cached route evaluations, including rejected routes and empty-route trials; placement checks exclude policy validation and independent audits',
                max_plans=max_plans, elapsed_ms=round((perf_counter()-start)*1000, 3),
                rejection_counts=dict(sorted(failures.items())),
                rejection_count_scope='per rejected full plan; first failed vehicle; codes may overlap',
                best=best, guarded_best=guarded_best, best_witness=best_payload,
                guarded_best_witness=guarded_payload,
                reference_scope='all complete assignments and stop orders under selected finite loading constructor; not geometric optimum')


def run_case(case, max_plans=100000, route_modes=('legacy',)):
    data, policy = case['data'], case['policy']
    initial = digest([data, policy])
    start = perf_counter()
    current = warehouse_loading.operational_compare(data, None, policy)
    current_ms = (perf_counter()-start)*1000
    plan = current['optimized']
    check = current['operational_verification']
    full = check['passed'] and check['complete']
    current_summary = summarize(data, plan['routes'], check['routes']) if full else None
    result = dict(id=case['id'], description=case['description'], input_sha256=initial,
                  inputs=dict(data=data, policy=policy),
                  current=dict(complete=check['complete'], passed=check['passed'],
                               elapsed_ms=round(current_ms, 3), summary=current_summary,
                               unserved=plan['unserved'], errors=check['errors'],
                               search=check.get('search')),
                  references={mode: exhaustive(data, policy, current_summary,
                                               max_plans, mode) for mode in route_modes})
    for mode, reference in result['references'].items():
        best = reference['guarded_best']
        result['references'][mode]['guarded_improves_current'] = (
            current_summary is not None and best is not None and best['rank'] < current_summary['rank'])
    if digest([data, policy]) != initial:
        raise AssertionError('diagnosis mutated source inputs')
    return result


def build(max_plans=100000, route_modes=('legacy',)):
    return dict(schema='logireflow.operational-search-diagnosis.v1',
                scope='predeclared constructed small instances; no enterprise data or dispatch',
                interpretation='route enumeration reference only, never proof of globally optimal packing or physical infeasibility',
                guardrails=dict(same_available_fleet=True, same_used_vehicle_ids=True,
                                max_weight_volume_load_drop_percentage_points=2,
                                nominal_cost_increase_allowed=False, complete_and_on_time=True),
                comparison_rule='partial/rejected current plans have no cost-saving or guarded improvement comparison',
                rank_fields=['late_orders', 'late_minutes', 'cost', 'high_risk_rehandle_moves',
                             'rehandle_moves', 'sum_return_minutes', 'distance_km'],
                cases=[run_case(case, max_plans, route_modes) for case in fixtures()],
                boundaries=op.BOUNDARIES)


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--max-plans', type=int, default=100000)
    parser.add_argument('--portfolio', action='store_true')
    parser.add_argument('--output', type=Path,
                        default=ROOT/'outputs/search-diagnosis/operational-search.json')
    args = parser.parse_args()
    report = build(args.max_plans, ('legacy', 'portfolio', 'fallback') if args.portfolio else ('legacy',))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(dict(output=str(args.output), cases=[dict(
        id=c['id'], current_complete=c['current']['complete'], current_passed=c['current']['passed'],
        references={mode: dict(complete=r['enumeration_complete'], checked=r['checked_plans'],
                              feasible=r['feasible_complete_plans'], best_rank=r['best']['rank'] if r['best'] else None,
                              guarded_improves=r['guarded_improves_current'])
                    for mode, r in c['references'].items()}) for c in report['cases']]), ensure_ascii=False))
