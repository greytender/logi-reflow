"""Offline comparison of recovery-aware route search on constructed inputs.

Run: python recovery_benchmark.py
No network, original customer rows, live task database or dispatch.
"""
from hashlib import sha256
from itertools import combinations, permutations, product
import json
from math import hypot
from pathlib import Path
from random import Random
from statistics import median
from time import perf_counter

import application
import business
import recovery_search


ROOT = Path(__file__).resolve().parent
METHODS = ('sequential', 'local', 'standard_alns', 'conflict_alns',
           'robust_objective_alns', 'robust_alns')
SECONDS = 5
SEARCH_SEEDS = (21, 77, 108)


def fixture(store_count, vehicle_count, seed, *, geometry=False, second_port=False):
    rng = Random(seed)
    rules = business.defaults()
    rules['tariff']['mode'] = 'whole_above'
    if second_port:
        rules['tariff'].update(base=210, included_km=75, per_km=2.4, per_stop=11)
        rules['packaging'].update(tote_first_m3=.09, tote_nested_m3=.02)
    dimensions = (50, 40, 30) if second_port else (60, 40, 30)
    unit_volume = dimensions[0] * dimensions[1] * dimensions[2] / 1e6
    box_weight = 8 if second_port else 7
    points = {'DEPOT': (0, 0)}
    raw = dict(settings=dict(depot_id='DEPOT', window_rule='finish',
                             data_kind='simulated', travel_source='constructed Euclidean road matrix'),
               business=rules, vehicles=[], orders=[], legs={})
    profile = dict(schema='loading_v1', data_kind='simulated', vehicles=[], items=[])
    vehicle_ids = [f'T{i+1}' for i in range(vehicle_count)]
    for index, vid in enumerate(vehicle_ids):
        length = 200 if second_port else 180
        length += 20 * (index % 2)
        raw['vehicles'].append(dict(id=vid, max_weight_kg=230 + 20 * (index % 2),
                                    max_volume_m3=length * 80 * 90 / 1e6,
                                    fixed_cost=140, cost_per_km=2, shift_start=510, shift_end=850))
        profile['vehicles'].append(dict(id=vid, length_cm=length, width_cm=80, height_cm=90))
    for index in range(store_count):
        sid = f'S{index+1:02}'
        cluster = index % 3
        points[sid] = (rng.randint(-10, 10) + 14 * (cluster - 1), rng.randint(2, 22))
        early = index % 5 == 0
        raw['orders'].append(dict(
            id=sid, allowed_vehicles=vehicle_ids.copy(), open=510, due=595 if early else 710,
            close=630 if early else 790, service_minutes=8 + index % 3,
            lines=[dict(sku='BOX', quantity=rng.randint(2, 5) if geometry else rng.randint(2, 7),
                        unit_weight_kg=box_weight, unit_volume_m3=unit_volume)],
            pickup=dict(cartons=rng.randint(0, 7), totes=rng.randint(4, 11))))
        length, width, height = dimensions
        profile['items'].append(dict(order=sid, sku='BOX', length_cm=length, width_cm=width,
                                     height_cm=height, rotate_base=True, fragile=False,
                                     max_top_load_kg=50))
    for a, pa in points.items():
        for b, pb in points.items():
            if a != b:
                km = round(2 + hypot(pa[0] - pb[0], pa[1] - pb[1]) * rng.uniform(1.05, 1.2), 3)
                raw['legs'][a+'>'+b] = dict(distance_km=km, travel_minutes=round(km * 1.5, 3))
    return application.canonicalize(raw), profile if geometry else None


def scenarios(data, seed, count=6):
    rng = Random(seed)
    orders = data['orders']
    result = []
    for index in range(count):
        affected_count = min(len(orders), 1 + (index // 2) % 3)
        result.append({order['id']: dict(
            cartons=order['pickup']['cartons'] + rng.randint(1, 7),
            totes=order['pickup']['totes'] + rng.randint(9, 26),
        ) for order in rng.sample(orders, affected_count)})
    return result


def digest(payload):
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode('utf-8')
    return sha256(serialized).hexdigest()


def conflict_fixture():
    """Construct a two-stop case where nearest route fails only after pickup."""
    data, _ = fixture(2, 1, 196)
    for order in data['orders']:
        order['lines'][0]['quantity'] = 7
        order.update(due=650, close=720, service_minutes=10)
    data['orders'][0]['pickup'] = dict(cartons=4, totes=68)
    data['orders'][1]['pickup'] = dict(cartons=0, totes=1)
    for key, km in {'DEPOT>S01': 2, 'S01>S02': 3, 'S02>DEPOT': 3,
                    'DEPOT>S02': 4, 'S02>S01': 3, 'S01>DEPOT': 3}.items():
        data['legs'][key] = dict(distance_km=km, travel_minutes=2*km)
    return application.canonicalize(data), None


def independent_metrics(data, routes, held_out):
    """Recalculate departure load, billable fees and capacity-constrained pickup."""
    vehicles = {v['id']: v for v in data['vehicles']}
    orders = {o['id']: o for o in data['orders']}
    legs = data['legs']
    depot = data['settings']['depot_id']
    tariff, packaging = (data['business'][key] for key in ('tariff', 'packaging'))
    assigned = [sid for route in routes.values() for sid in route]
    assert not (set(routes) - set(vehicles))
    assert len(assigned) == len(set(assigned)), 'Duplicate stop assignment'
    assert set(assigned) <= set(orders)
    volume_rates, weight_rates = [], []
    totals = {sid: (sum(x['quantity'] * x['unit_weight_kg'] for x in order['lines']),
                    sum(x['quantity'] * x['unit_volume_m3'] for x in order['lines']))
              for sid, order in orders.items()}
    fee, km, on_time, hard_violations = 0, 0, 0, 0
    for vid, route in routes.items():
        if not route:
            continue
        v = vehicles[vid]
        weight = sum(totals[sid][0] for sid in route)
        volume = sum(totals[sid][1] for sid in route)
        weight_rates.append(weight / v['max_weight_kg'])
        volume_rates.append(volume / v['max_volume_m3'])
        hard_violations += int(weight_rates[-1] > 1 + 1e-8 or volume_rates[-1] > 1 + 1e-8)
        nodes = [depot, *route, depot]
        distance = sum(legs[a+'>'+b]['distance_km'] for a, b in zip(nodes, nodes[1:]))
        km += distance
        if tariff['mode'] == 'whole_above':
            billed = distance if distance > tariff['included_km'] else 0
        elif tariff['mode'] == 'incremental':
            billed = max(0, distance - tariff['included_km'])
        else:
            raise ValueError('Unsupported benchmark tariff')
        fee += tariff['base'] + billed * tariff['per_km'] + len(route) * tariff['per_stop']
        elapsed = v['shift_start']
        for prev, sid in zip(nodes, route):
            o = orders[sid]
            elapsed = max(elapsed + legs[prev+'>'+sid]['travel_minutes'], o['open']) + o['service_minutes']
            on_time += int(elapsed <= o['due'] + 1e-8)
            hard_violations += int(elapsed > o['close'] + 1e-8 or vid not in o['allowed_vehicles'])
        hard_violations += int(elapsed + legs[route[-1]+'>'+depot]['travel_minutes'] > v['shift_end'] + 1e-8)

    # At each station recover the entire lot if it fits; a refused lot stays at the
    # station. Pickup and delivery times are held fixed across comparisons.
    held_metrics = []
    for overlay in held_out:
        completed = requested = complete_routes = 0
        for sid, o in orders.items():
            pickup = overlay.get(sid, o['pickup'])
            requested += int(any(pickup.values()))
        for vid, route in routes.items():
            if not route:
                continue
            remaining_weight = sum(totals[sid][0] for sid in route)
            remaining_volume = sum(totals[sid][1] for sid in route)
            route_complete = True
            v = vehicles[vid]
            for sid in route:
                remaining_weight -= totals[sid][0]
                remaining_volume -= totals[sid][1]
                counts = overlay.get(sid, orders[sid]['pickup'])
                cartons, totes = counts['cartons'], counts['totes']
                if cartons + totes == 0:
                    continue
                pw = cartons * packaging['carton_weight_kg'] + totes * packaging['tote_weight_kg']
                pv = cartons * packaging['carton_flat_m3']
                if totes:
                    pv += packaging['tote_first_m3'] + (totes - 1) * packaging['tote_nested_m3']
                if (remaining_weight + pw <= v['max_weight_kg'] + 1e-8 and
                    remaining_volume + pv <= v['max_volume_m3'] + 1e-8):
                    completed += 1
                    remaining_weight += pw
                    remaining_volume += pv
                else:
                    route_complete = False
            complete_routes += int(route_complete)
        held_metrics.append(dict(recovered_stops=completed, requested_stops=requested,
                                 complete_recovery_routes=complete_routes,
                                 complete_all_stops=completed == requested and on_time == len(orders)))
    return dict(assigned=len(assigned), total_stores=len(orders), on_time_signoffs=on_time,
                used_vehicles=len(volume_rates), structural_violations=hard_violations,
                freight_estimate=round(fee, 4), kilometers=round(km, 4),
                mean_departure_volume_pct=round(100 * sum(volume_rates) / len(volume_rates), 3) if volume_rates else None,
                mean_departure_weight_pct=round(100 * sum(weight_rates) / len(weight_rates), 3) if weight_rates else None,
                held_out=held_metrics)


def exact_small(data, training):
    """Exhaustive route-assignment oracle for the declared aggregate fixture."""
    ids = tuple(o['id'] for o in data['orders'])
    vehicles = tuple(v['id'] for v in data['vehicles'])
    best = None
    candidates = 0
    start = perf_counter()
    for size in range(len(ids)+1):
        for served in combinations(ids, size):
            for assigned in product(range(len(vehicles)), repeat=size):
                groups = [[sid for sid, idx in zip(served, assigned) if idx == i]
                          for i in range(len(vehicles))]
                for sequences in product(*(permutations(group) for group in groups)):
                    routes = dict(zip(vehicles, sequences))
                    evaluation = recovery_search.evaluate_plan(data, routes, scenarios=training)
                    candidates += 1
                    if evaluation is None:
                        continue
                    objective = tuple(evaluation['objective'])
                    if best is None or objective < best['objective']:
                        best = dict(routes=routes, objective=objective)
    return {**best, 'candidates': candidates, 'elapsed_ms': round((perf_counter()-start)*1000, 3)}


def run():
    configurations = (
        ('forced-pickup-conflict', 2, 1, 196, False, False),
        ('small-exact', 4, 2, 104, False, False),
        ('geometry-check', 8, 3, 623, True, False),
        ('medium', 12, 4, 215, False, False),
        ('different-port-rules', 12, 4, 712, False, True),
    )
    cases = []
    for name, count, fleet, data_seed, geometry, second_port in configurations:
        data, profile = (conflict_fixture() if name == 'forced-pickup-conflict' else
                         fixture(count, fleet, data_seed, geometry=geometry, second_port=second_port))
        evaluation_cap = 650 if geometry else 1800
        training = scenarios(data, data_seed + 2000)
        held_out = scenarios(data, data_seed + 9000, count=24)
        assert {digest(x) for x in training}.isdisjoint(digest(x) for x in held_out)
        input_digest = digest([data, profile])
        trials = []
        for seed in SEARCH_SEEDS:
            for method in METHODS:
                started = perf_counter()
                plan = recovery_search.solve(data, method=method, scenarios=training, profile=profile,
                                             seed=seed, max_evaluations=evaluation_cap, time_limit_seconds=SECONDS)
                wall_ms = round(1000*(perf_counter()-started), 3)
                assert digest([data, profile]) == input_digest, 'Search changed source input'
                routes = plan['routes']
                metrics = independent_metrics(data, routes, held_out)
                assert metrics['structural_violations'] == 0, (name, method, metrics)
                assert metrics['assigned'] == plan['metrics']['served']
                assert metrics['on_time_signoffs'] == metrics['assigned'] - plan['metrics']['late_orders']
                assert abs(metrics['freight_estimate'] - plan['metrics']['cost']) < .02
                shared_objective = recovery_search.evaluate_plan(data, routes, scenarios=training,
                                                                  profile=profile)['objective']
                trials.append(dict(method=method, seed=seed, wall_ms=wall_ms,
                                   elapsed_ms=plan['elapsed_ms'], evaluations=plan['evaluations'],
                                   budget_reached=plan['budget_reached'], audit=plan['audit'],
                                   operator_trials=plan['operator_trials'],
                                   routes=routes, optimized_objective=plan['objective'],
                                   common_training_objective=shared_objective, metrics=metrics))
        exact = exact_small(data, training) if name == 'small-exact' else None
        if exact:
            assert all(tuple(row['common_training_objective']) >= exact['objective'] for row in trials)
        if name == 'forced-pickup-conflict':
            assert all(row['metrics']['assigned'] == (1 if row['method'] == 'sequential' else 2)
                       for row in trials)
        cases.append(dict(name=name, fixture_sha256=input_digest, data_seed=data_seed,
                          vehicles=fleet, stores=count, departure_geometry=geometry,
                          evaluation_cap=evaluation_cap,
                          training=training, held_out=held_out, trials=trials, exact=exact))
    return dict(data_kind='simulated', same_input_per_case=True,
                time_cap_seconds=SECONDS, search_seeds=SEARCH_SEEDS, cases=cases)


def summarize(result):
    for case in result['cases']:
        print(case['name'])
        for method in METHODS:
            rows = [r for r in case['trials'] if r['method'] == method]
            med = lambda key: median(r['metrics'][key] for r in rows)
            recovered = median(sum(h['recovered_stops'] for h in r['metrics']['held_out']) for r in rows)
            possible = sum(h['requested_stops'] for h in rows[0]['metrics']['held_out'])
            print(method, f"ms={median(r['wall_ms'] for r in rows):.2f}",
                  f"eval={median(r['evaluations'] for r in rows)}",
                  f"served={med('assigned')} signoffs={med('on_time_signoffs')}",
                  f"volume/weight={med('mean_departure_volume_pct')}/{med('mean_departure_weight_pct')}",
                  f"freight={med('freight_estimate')} held-out={recovered}/{possible}",
                  f"exact_matches={sum(tuple(r['common_training_objective']) == case['exact']['objective'] for r in rows) if case['exact'] else '-'}")


if __name__ == '__main__':
    results = run()
    target = ROOT / 'evidence' / 'recovery-search-benchmark.json'
    target.write_text(json.dumps(results, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    summarize(results)
