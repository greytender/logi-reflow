"""Bounded single-depot search for larger batches.

Fast insertion compares the actual round-trip tariff for each vehicle. Only
the highest-ranked schedules and local repairs reach the more expensive
packing/handling check. Stores sharing a transport box move as one group.
"""
from functools import lru_cache
from time import perf_counter

import normal
import pilot

CORE_LIMIT = 40_000
MODEL_LIMIT = 160
SEARCH_SECONDS = 45
REPAIR_SHORTLIST = 6


def route_score(route):
    operations = route.get('operational', {})
    adjusted = operations.get('adjusted_route', route)
    times = operations.get('time_adjustments', {})
    return (adjusted['late_orders'], adjusted['late_minutes'], route['cost'],
            times.get('high_risk_rehandle_moves', 0), times.get('rehandle_moves', 0),
            route['distance'], adjusted['return_at'])


def solve(data, baseline, groups, qualify=None, screen=None):
    started = perf_counter()
    orders = {order['id']: order for order in data['orders']}
    vehicles = {vehicle['id']: vehicle for vehicle in data['vehicles']}
    evaluate = pilot.evaluator(data)
    empty = {vid: evaluate([], vehicle, orders) for vid, vehicle in vehicles.items()}
    deadline = started + SEARCH_SECONDS
    work = dict(core_checks=0, model_checks=0, wall_limit_reached=False)

    def time_available():
        if perf_counter() >= deadline:
            work['wall_limit_reached'] = True
            return False
        return True

    model_cache = {}
    model_rejections = {}

    def remember_issue(sequence, issue):
        for sid in sequence:
            model_rejections.setdefault(sid, issue)

    @lru_cache(maxsize=2048)
    def core(vid, sequence):
        work['core_checks'] += 1
        vehicle = vehicles[vid]
        route = evaluate(sequence, vehicle, orders)
        if route is not None and screen is not None:
            issue = screen(route, vehicle)
            if issue:
                remember_issue(sequence, issue)
                return None
        return route

    def model(vid, route):
        key = vid, tuple(route['sequence'])
        if key not in model_cache:
            work['model_checks'] += 1
            checked, issue = qualify(route, vehicles[vid])
            if (checked is None and issue and issue.get('code')=='cold_duration'
                    and work['model_checks']<MODEL_LIMIT and time_available()):
                reversed_route=core(vid,tuple(reversed(route['sequence'])))
                if reversed_route is not None:
                    work['model_checks']+=1
                    alternative, alternative_issue=qualify(reversed_route,vehicles[vid])
                    if alternative is not None:
                        checked,issue=alternative,None
            model_cache[key] = checked
            if issue:
                remember_issue(route['sequence'], issue)
        return model_cache[key]

    def proposals(routes, members, allowed, balanced=False):
        candidates = []
        orientations = (members, members[::-1]) if len(members) > 1 else (members,)
        for vid in allowed:
            old = routes[vid]
            seq = tuple(old['sequence'])
            score = route_score(old)
            for group in orientations:
                for index in range(len(seq) + 1):
                    if work['core_checks'] >= CORE_LIMIT or not time_available():
                        return sorted(candidates, key=lambda row: row[:3])
                    order = seq[:index] + group + seq[index:]
                    route = core(vid, order)
                    if route is None:
                        continue
                    delta = tuple(round(new - prev, 4) for new, prev in zip(route_score(route), score))
                    if balanced:
                        vehicle = vehicles[vid]
                        pressure = max(route['weight_kg'] / vehicle['max_weight_kg'],
                                       route['volume_m3'] / vehicle['max_volume_m3'])
                        pressure = max(pressure / target_load, len(order) / target_stops)
                        delta = (*delta[:2], round(pressure, 4), *delta[2:])
                    candidates.append((delta, vid, order, route))
        return sorted(candidates, key=lambda row: row[:3])

    def rank(routes):
        active = [route for route in routes.values() if route['sequence']]
        return (len(orders) - sum(len(route['sequence']) for route in active),
                *(round(sum(values), 4) for values in zip(*(route_score(route) for route in routes.values()))))

    def group_order(members):
        return tuple(sorted(members, key=lambda sid: (orders[sid]['close'], orders[sid]['due'], sid)))

    group_map = {group_order(members): tuple(sorted(allowed)) for members, allowed in groups}
    group_for_stop = {sid: set(group) for group in group_map for sid in group}
    by_window = sorted(group_map, key=lambda g: (len(group_map[g]), orders[g[0]]['close'],
                                                 orders[g[0]]['due'], g))
    max_kg = max(v['max_weight_kg'] for v in vehicles.values())
    max_m3 = max(v['max_volume_m3'] for v in vehicles.values())

    def load_share(group):
        kg, m3 = normal.totals(group, orders)
        return max(kg / max_kg, m3 / max_m3)

    by_load = sorted(group_map, key=lambda g: (len(group_map[g]), -load_share(g), g))
    total_kg, total_m3 = normal.totals(orders, orders)
    target_load = max(.4, min(.85, max(total_kg / sum(v['max_weight_kg'] for v in vehicles.values()),
                                       total_m3 / sum(v['max_volume_m3'] for v in vehicles.values())) + .02))
    target_stops = max(1, (len(orders) + len(vehicles) - 1) // len(vehicles))
    seeds = []
    priorities = [(by_window, False), (by_load, False)]
    if qualify is not None:
        priorities.insert(0, (by_load, True))
    for priority, balanced in priorities:
        routes = dict(empty)
        for group in priority:
            options = proposals(routes, group, group_map[group], balanced=balanced)
            if options:
                _, vid, _, route = options[0]
                routes[vid] = route
        seeds.append(routes)

    # Include the rule-based seed only when it respects shared-box grouping.
    owners = {sid: vid for vid, r in baseline['routes'].items() for sid in r['sequence']}
    if all(len({owners.get(sid) for sid in group}) == 1 for group in group_map):
        seeds.append(baseline['routes'])

    seen = set()
    best = dict(empty)
    # Heavy manifests are checked with the more evenly loaded seed first.
    # Other same-input seeds remain available for repair within the budget.
    def seed_rank(routes):
        if qualify is None:
            return rank(routes)
        load = max(max(r['weight_kg']/vehicles[vid]['max_weight_kg'], r['volume_m3']/vehicles[vid]['max_volume_m3'])
                   for vid, r in routes.items())
        return rank(routes)[0], load

    for seed in sorted(seeds, key=seed_rank):
        identity = tuple(tuple(r['sequence']) for r in seed.values())
        if identity in seen:
            continue
        seen.add(identity)
        if qualify is None:
            best = seed
            break
        checked = dict(empty)
        for vid, route in seed.items():
            if not route['sequence']:
                continue
            if work['model_checks'] >= MODEL_LIMIT or not time_available():
                break
            found = model(vid, route)
            # A failed full truck may still contain a useful verified prefix.
            # Remove complete shared-box groups, retaining every removed stop
            # for the same-batch repair below.
            reduced = route
            for _ in range(3):
                if found is not None or len(reduced['sequence'])<=1:
                    break
                if work['model_checks']>=MODEL_LIMIT or not time_available():
                    break
                removed=group_for_stop[reduced['sequence'][-1]]
                sequence=tuple(sid for sid in reduced['sequence'] if sid not in removed)
                if not sequence:
                    break
                reduced=core(vid,sequence)
                if reduced is None:
                    break
                found=model(vid,reduced)
            if found is not None:
                checked[vid] = found
        served = {sid for r in checked.values() for sid in r['sequence']}
        for group in by_window:
            if group[0] in served or work['model_checks'] >= MODEL_LIMIT or not time_available():
                continue
            options = proposals(checked, group, group_map[group])
            shortlisted=options[:REPAIR_SHORTLIST]
            empty_option=next((option for option in options if not checked[option[1]]['sequence']),None)
            if empty_option is not None and empty_option not in shortlisted:
                shortlisted.append(empty_option)
            for _, vid, _, route in shortlisted:
                if work['model_checks'] >= MODEL_LIMIT or not time_available():
                    break
                found = model(vid, route)
                if found is not None:
                    checked[vid] = found
                    served.update(group)
                    break
        if rank(checked) < rank(best):
            best = checked
        # Once all stops have a verified plan, retain the time budget for
        # independent batch verification rather than trying every seed.
        if rank(best)[0] == 0:
            break

    visited = {sid for route in best.values() for sid in route['sequence']}
    budget_exhausted = (work['core_checks'] >= CORE_LIMIT or work['model_checks'] >= MODEL_LIMIT
                        or work['wall_limit_reached'])
    missing = []
    for group in group_map:
        for sid in group:
            if sid in visited:
                continue
            issue = model_rejections.get(sid)
            if not group_map[group]:
                code, reason = 'NO_ELIGIBLE_VEHICLE', '整店或共箱门店组没有共同可用车辆'
            elif issue:
                code, reason = 'DECLARED_MODEL_NOT_PASSED', issue['message']
            elif budget_exhausted:
                code, reason = 'SEARCH_BUDGET_EXHAUSTED', '本次计算预算内尚未找到可核对路线'
            else:
                code, reason = 'ROUTE_SEARCH_NOT_FOUND', '本次计算未找到满足准入、容量和时间的插入位置'
            item = dict(id=sid, code=code, reason=reason,
                        action='核对准入、运力、箱规和收货窗口后再算')
            if issue:
                item['constraint_evidence'] = issue
            missing.append(item)
    work.update(core_limit=CORE_LIMIT, model_limit=MODEL_LIMIT, search_seconds=SEARCH_SECONDS,
                budget_exhausted=budget_exhausted, phases=['insertion', 'declared_model_check', 'repair'] if qualify else ['insertion'])
    score = normal.score(best)
    return dict(routes=best, unserved=missing, metrics=dict(served=len(visited), unserved=len(missing),
        late_orders=score[0], late_minutes=round(score[1], 2), cost=round(score[2], 2),
        distance=round(sum(route['distance'] for route in best.values()), 2)),
        method='整店分组、逐车运费排线与待安排任务补排', solve_ms=round((perf_counter()-started)*1000, 3),
        search=work, simulated=data['settings']['data_kind'] == 'simulated')
