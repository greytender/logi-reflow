"""Bounded dispatch experiments for same-trip packaging recovery.

A candidate carries one fixed outbound plan across all recovery scenarios.
Recovery is checked as aggregate onboard weight and volume after each stop.
Joint stress can add directed-leg delays and extra service minutes, replaying
the route clock through store windows and the return shift deadline.
An optional existing geometry profile checks declared outbound cuboids only.
"""
from functools import lru_cache
from math import exp, isfinite
from random import Random
from time import perf_counter

import business
import loading
import normal
import pilot
import warehouse_loading


METHODS = ('sequential', 'local', 'robust_local', 'standard_alns', 'conflict_alns',
           'robust_objective_alns', 'robust_alns')


class _BudgetReached(Exception):
    pass


class _Problem:
    def __init__(self, data, scenarios, profile, joint_scenarios):
        pilot.validate(data)
        if 'business' not in data:
            raise ValueError('回收优化需要明确的包装回收和计费规则')
        if not isinstance(scenarios, (tuple, list)) or len(scenarios) > 32:
            raise ValueError('最多支持32组回收压力情景')
        if not isinstance(joint_scenarios, (tuple, list)) or len(scenarios)+len(joint_scenarios) > 32:
            raise ValueError('回收和联合压力情景合计最多32组')
        self.data = data
        self.profile = profile
        self.orders = {o['id']: o for o in data['orders']}
        self.vehicles = {v['id']: v for v in data['vehicles']}
        self.stops = tuple(sorted(self.orders))
        self.scenarios = []
        overlays = [(scenario, {}, {}) for scenario in scenarios]
        for stress in joint_scenarios:
            if not isinstance(stress, dict) or set(stress)-{'pickup', 'road_delay_minutes', 'extra_service_minutes'}:
                raise ValueError('联合情景仅支持回收、路段延误与门店额外服务时间')
            delays = stress.get('road_delay_minutes', {})
            service = stress.get('extra_service_minutes', {})
            for changes, keys in ((delays, data['legs']), (service, self.orders)):
                if (not isinstance(changes, dict) or not changes.keys() <= keys.keys()
                        or any(type(v) not in (int, float) or not isfinite(v) or not 0 <= v <= 1440
                               for v in changes.values())):
                    raise ValueError('联合情景的路段或门店及附加分钟无效')
            overlays.append((stress.get('pickup', {}), dict(delays), dict(service)))
        for scenario, delays, service in overlays:
            if not isinstance(scenario, dict) or not scenario.keys() <= self.orders.keys():
                raise ValueError('回收情景含未知门店')
            for pickup in scenario.values():
                if (not isinstance(pickup, dict) or set(pickup) != {'cartons', 'totes'}
                        or any(type(n) is not int or not 0 <= n <= 100000 for n in pickup.values())):
                    raise ValueError('回收情景需填写整数纸箱和周转箱数量')
            totals = {
                oid: business.pickup_totals({**order, 'pickup': scenario.get(oid, order['pickup'])},
                                            data['business']['packaging'])
                for oid, order in self.orders.items()
            }
            self.scenarios.append((totals, delays, service))
        self.evaluate_nominal = pilot.evaluator(data)
        self.no_pickup = {oid: {**order, 'pickup': {'cartons': 0, 'totes': 0}}
                          for oid, order in self.orders.items()}
        self.geometry = loading.profile(data, profile) if profile is not None else None
        self.cached_route = lru_cache(maxsize=1024)(self._route)
        self.cached_risk = lru_cache(maxsize=6000)(self._risk)
        self.evaluations = 0
        self.max_evaluations = None
        self.deadline = float('inf')

    def check_budget(self):
        if self.evaluations >= self.max_evaluations or perf_counter() >= self.deadline:
            raise _BudgetReached

    def _route(self, vid, sequence, outbound):
        orders = self.no_pickup if outbound else self.orders
        route = self.evaluate_nominal(sequence, self.vehicles[vid], orders)
        if route is not None and self.geometry is not None and not outbound:
            vehicles, items = self.geometry
            group = [item for item in items if item['order'] in sequence]
            packed = loading.constructive(group, vehicles[vid], route['sequence'],
                                          {'placement_checks': 0, 'budget_exhausted': False})
            if packed is None:
                return None
            route['loading'] = packed
        return route

    def route(self, vid, sequence, outbound=False):
        if self.max_evaluations is not None:
            self.check_budget()
        self.evaluations += 1
        return self.cached_route(vid, tuple(sequence), outbound)

    def _risk(self, vid, sequence):
        vehicle = self.vehicles[vid]
        start_weight, start_volume = normal.totals(sequence, self.orders)
        first_breaches = []
        depot = self.data['settings']['depot_id']
        for scenario, delays, service in self.scenarios:
            weight, volume = start_weight, start_volume
            clock, previous = vehicle['shift_start'], depot
            for oid in sequence:
                order = self.orders[oid]
                if delays or service:
                    edge = previous+'>'+oid
                    arrival = max(clock+self.data['legs'][edge]['travel_minutes']+delays.get(edge, 0), order['open'])
                    clock = arrival+order['service_minutes']+service.get(oid, 0)
                    deadline = clock if self.data['settings']['window_rule'] == 'finish' else arrival
                    if deadline > order['due']+1e-8:
                        first_breaches.append(oid)
                        break
                    previous = oid
                pickup_weight, pickup_volume = scenario[oid]
                weight += pickup_weight - order['unit_weight_kg'] * order['quantity']
                volume += pickup_volume - order['unit_volume_m3'] * order['quantity']
                if (weight > vehicle['max_weight_kg'] + 1e-8
                        or volume > vehicle['max_volume_m3'] + 1e-8):
                    first_breaches.append(oid)
                    break
            else:
                edge = previous+'>'+depot
                late_return = bool(sequence and (delays or service) and
                    clock+self.data['legs'][edge]['travel_minutes']+delays.get(edge, 0) > vehicle['shift_end']+1e-8)
                first_breaches.append(depot if late_return else None)
        return tuple(first_breaches)

    def rank(self, routes, robust):
        served = sum(len(r['sequence']) for r in routes.values())
        late, minutes, fee = normal.score(routes)
        failures = 0
        if robust:
            failures = sum(
                len(r['sequence']) * sum(stop is not None for stop in self.cached_risk(vid, tuple(r['sequence'])))
                for vid, r in routes.items()
            )
        distance = round(sum(r['distance'] for r in routes.values()), 4)
        return (len(self.stops) - served, late, minutes, failures, fee, distance)

    def result(self, routes, started, method, seed=None, budget_reached=False):
        used = {vid: route for vid, route in routes.items() if route['sequence']}
        all_assigned = {oid for r in used.values() for oid in r['sequence']}
        unserved = [
            dict(id=oid, reason='有限搜索未找到通过名义回收及装卸检查的安排，需人工复核')
            for oid in self.stops if oid not in all_assigned
        ]
        objective = self.rank(routes, robust=True)
        count = len(used)
        outcomes = {vid: self.cached_risk(vid, tuple(r['sequence'])) for vid, r in used.items()}
        metrics = dict(
            served=len(all_assigned), unserved=len(unserved), late_orders=objective[1],
            late_minutes=round(objective[2], 2), cost=round(objective[4], 2),
            distance=round(objective[5], 2), used_vehicles=count,
            average_volume_utilization=round(sum(r['volume_m3']/self.vehicles[vid]['max_volume_m3']
                                                for vid, r in used.items())/count, 6) if count else 0,
            average_weight_utilization=round(sum(r['weight_kg']/self.vehicles[vid]['max_weight_kg']
                                                for vid, r in used.items())/count, 6) if count else 0,
            nominal_pickup_units=sum(sum(self.orders[oid]['pickup'].values()) for oid in all_assigned),
            scenario_failed_route_stops=objective[3],
            scenario_total_route_stops=len(all_assigned)*len(self.scenarios),
            scenario_full_completions=(sum(all(row[index] is None for row in outcomes.values())
                                          for index in range(len(self.scenarios)))
                                       if not unserved and not objective[1] else 0),
            scenario_count=len(self.scenarios),
        )
        elapsed_ms = round((perf_counter()-started)*1000, 3)
        plan = dict(routes=routes, unserved=unserved, metrics=metrics, solve_ms=elapsed_ms,
                    simulated=self.data['settings']['data_kind'] == 'simulated',
                    method='回收场景有限搜索 '+method)
        audit = dict(nominal=pilot.audit(self.data, plan), physical_loading=None)
        if self.profile is not None:
            audit['physical_loading'] = warehouse_loading.check_plan(self.data, self.profile, plan)
        return dict(routes={vid: r['sequence'] for vid, r in routes.items()}, plan=plan,
                    metrics=metrics, objective=objective, elapsed_ms=elapsed_ms,
                    method=method, seed=seed, budget_reached=budget_reached,
                    evaluations=self.evaluations, audit=audit,
                    scenario_first_breach_by_vehicle={vid: list(row) for vid, row in outcomes.items()})


def evaluate_plan(data, routes, *, scenarios=(), profile=None, joint_scenarios=()):
    """Check explicit assignment against the same model and robust objective.

    Return None when the nominal plan fails a route check; omitted stops count
    as unserved. Unknown vehicles, unknown stops, and duplicate stops are errors.
    """
    started = perf_counter()
    problem = _Problem(data, scenarios, profile, joint_scenarios)
    if not isinstance(routes, dict) or not routes.keys() <= problem.vehicles.keys():
        raise ValueError('方案含未知车辆')
    checked, assigned = {}, set()
    for vid in problem.vehicles:
        seq = routes.get(vid, ())
        if not isinstance(seq, (tuple, list)) or any(not isinstance(oid, str) or oid not in problem.orders for oid in seq):
            raise ValueError('方案含未知门店')
        if len(set(seq)) != len(seq) or assigned.intersection(seq):
            raise ValueError('同一门店不能重复安排')
        assigned.update(seq)
        checked[vid] = problem.route(vid, seq)
    if any(route is None for route in checked.values()):
        return None
    return problem.result(checked, started, 'evaluation')


def _insert(problem, routes, oid, robust=False, outbound=False):
    best = None
    for vid, old in routes.items():
        if vid not in problem.orders[oid]['allowed_vehicles']:
            continue
        sequence = old['sequence']
        for index in range(len(sequence) + 1):
            route = problem.route(vid, sequence[:index] + [oid] + sequence[index:], outbound)
            if route is None:
                continue
            candidate = {**routes, vid: route}
            key = problem.rank(candidate, robust)
            if best is None or key < best[0]:
                best = key, candidate
    return best[1] if best else routes


def _local(problem, routes, robust, admissible):
    old_rank = problem.rank(routes, robust)
    for oid in problem.stops:
        if all(oid not in r['sequence'] for r in routes.values()):
            candidate = _insert(problem, routes, oid, robust)
            if admissible(candidate) and problem.rank(candidate, robust) < old_rank:
                return candidate
    for vid, route in routes.items():
        for oid in route['sequence']:
            reduced = problem.route(vid, [stop for stop in route['sequence'] if stop != oid])
            if reduced is None:
                continue
            candidate = _insert(problem, {**routes, vid: reduced}, oid, robust)
            if admissible(candidate) and problem.rank(candidate, robust) < old_rank:
                return candidate
    return routes


def _conflict_order(problem, routes, robust):
    scores = {}
    for vid, route in routes.items():
        vehicle = problem.vehicles[vid]
        seq = route['sequence']
        breaches = problem.cached_risk(vid, tuple(seq)) if robust else ()
        for index, stop in enumerate(route['stops']):
            # The peak binds after collecting at this stop. Later deliveries
            # still on board and earlier pickups can contribute to the peak.
            weight = stop['onboard_weight_kg']/vehicle['max_weight_kg']
            volume = stop['onboard_volume_m3']/vehicle['max_volume_m3']
            pressure = max(weight, volume)
            pressure += breaches.count(stop['id'])/max(1, len(breaches))
            for offset, oid in enumerate(seq):
                score = pressure/(1 + abs(index-offset)*.2)
                scores[oid] = max(score, scores.get(oid, 0))
    return sorted(scores, key=lambda oid: (-scores[oid], oid))


def _neighbor(problem, routes, method, rng, robust, scenario_guidance):
    assigned = [oid for route in routes.values() for oid in route['sequence']]
    if not assigned:
        return routes
    size = min(len(assigned), rng.randint(2, max(2, min(7, len(assigned)//2))))
    if method == 'conflict':
        ordered = _conflict_order(problem, routes, scenario_guidance)
        # Sample from the highest pressure stops to vary repeated proposals.
        removed = set(rng.sample(ordered[:size+2], size))
    elif method == 'related':
        root = rng.choice(assigned)
        removed = set(sorted(assigned, key=lambda oid: (
            0 if oid == root else problem.data['legs'][root+'>'+oid]['travel_minutes'], oid))[:size])
    elif method == 'route':
        sequence = rng.choice([r['sequence'] for r in routes.values() if r['sequence']])
        start = rng.randrange(len(sequence))
        removed = set((sequence[start:]+sequence[:start])[:size])
    else:
        removed = set(rng.sample(assigned, size))
    candidate = {}
    for vid, route in routes.items():
        seq = route['sequence']
        if removed.isdisjoint(seq):
            candidate[vid] = route
            continue
        candidate[vid] = problem.route(vid, [oid for oid in seq if oid not in removed])
        if candidate[vid] is None:
            return routes  # A directed shortcut can violate a window.
    outstanding = sorted((set(problem.stops)-set(assigned)) | removed)
    rng.shuffle(outstanding)
    for oid in outstanding:
        candidate = _insert(problem, candidate, oid, robust)
    return candidate


def solve(data, *, method='conflict_alns', scenarios=(), profile=None, seed=0, joint_scenarios=(),
          max_evaluations=4000, time_limit_seconds=3, fixed_fleet_baseline=None, max_load_drop=0.02):
    """Run a reproducible seeded search unless the wall-clock limit binds."""
    started = perf_counter()
    if method not in METHODS:
        raise ValueError('未知的回收搜索方法')
    if type(seed) is not int:
        raise ValueError('随机种子需为整数')
    if (type(max_evaluations) is not int or not 1 <= max_evaluations <= 100000
            or isinstance(time_limit_seconds, bool) or not isinstance(time_limit_seconds, (int, float))
            or not isfinite(time_limit_seconds) or not 0 < time_limit_seconds <= 60):
        raise ValueError('回收搜索预算无效')
    problem = _Problem(data, scenarios, profile, joint_scenarios)
    empty = {vid: problem.cached_route(vid, (), False) for vid in problem.vehicles}
    best = empty.copy()
    def admissible(routes):
        return True
    if fixed_fleet_baseline is not None:
        if (type(max_load_drop) not in (int, float) or not isfinite(max_load_drop)
                or not 0 <= max_load_drop <= 1):
            raise ValueError('装载率允许降幅需为0至1之间的小数')
        baseline = evaluate_plan(data, fixed_fleet_baseline, profile=profile)
        if baseline is None or baseline['metrics']['unserved'] or baseline['metrics']['late_orders']:
            raise ValueError('固定车队基线须通过名义核验并按时覆盖全部门店')
        best = baseline['plan']['routes']
        used = {vid for vid, route in best.items() if route['sequence']}
        baseline_cost = sum(route['cost'] for route in best.values())
        volume_floors = {vid: best[vid]['volume_m3']-max_load_drop*problem.vehicles[vid]['max_volume_m3'] for vid in used}
        weight_floors = {vid: best[vid]['weight_kg']-max_load_drop*problem.vehicles[vid]['max_weight_kg'] for vid in used}

        def admissible(routes):
            return ({vid for vid, route in routes.items() if route['sequence']} == used
                and sum(len(r['sequence']) for r in routes.values()) == len(problem.stops)
                and not any(r['late_orders'] for r in routes.values())
                and sum(r['cost'] for r in routes.values()) <= baseline_cost+1e-7
                and all(routes[vid]['volume_m3'] >= volume_floors[vid]-1e-7
                        and routes[vid]['weight_kg'] >= weight_floors[vid]-1e-7 for vid in used))
    problem.max_evaluations = max_evaluations
    problem.deadline = started + time_limit_seconds
    robust = method in ('robust_alns', 'robust_objective_alns', 'robust_local')
    interrupted = False
    operator_trials = {}
    try:
        if fixed_fleet_baseline is None:
            proposed = {vid: problem.cached_route(vid, (), True) for vid in problem.vehicles}
            priority = sorted(problem.stops, key=lambda oid: (problem.orders[oid]['due'], oid))
            for oid in priority:
                proposed = _insert(problem, proposed, oid, outbound=True)
            # Keep the proposed assignment and remove failing suffixes.
            for vid, route in proposed.items():
                seq = route['sequence']
                while seq:
                    checked = problem.route(vid, seq)
                    if checked is not None:
                        best[vid] = checked
                        break
                    seq = seq[:-1]
        if method != 'sequential':
            while True:
                improved = _local(problem, best, robust, admissible)
                if improved is best:
                    break
                best = improved
        if method.endswith('_alns'):
            rng = Random(seed)
            operators = ['random', 'related', 'route']
            if method != 'standard_alns':
                operators.append('conflict')
            weights = [1.0] * len(operators)
            operator_trials = dict.fromkeys(operators, 0)
            current = best
            for iteration in range(max_evaluations):
                problem.check_budget()
                index = rng.choices(range(len(operators)), weights=weights)[0]
                candidate = _neighbor(problem, current, operators[index], rng, robust,
                                      scenario_guidance=method == 'robust_alns')
                operator_trials[operators[index]] += 1
                if not admissible(candidate):
                    weights[index] = .85*weights[index] + .015
                    continue
                key = problem.rank(candidate, robust)
                previous = problem.rank(current, robust)
                record = problem.rank(best, robust)
                reward = 0.1
                if key < record:
                    best = candidate
                    reward = 6.0
                elif key < previous:
                    reward = 3.0
                if key <= previous:
                    current = candidate
                elif key[:4] == previous[:4]:
                    temperature = max(0.1, 5*(1-iteration/max_evaluations))
                    if rng.random() < exp(-(key[4]-previous[4])/temperature):
                        current = candidate
                        reward = max(reward, 0.5)
                weights[index] = .85*weights[index] + .15*reward
    except _BudgetReached:
        interrupted = True
    result = problem.result(best, started, method, seed, interrupted)
    result['operator_trials'] = operator_trials
    return result
