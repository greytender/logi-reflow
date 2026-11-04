"""Compare fixed-fleet routing on public Chinese point layouts and synthetic operations."""
import argparse
import json
from hashlib import sha256
from itertools import permutations, product
from math import atan2, cos, hypot, radians
from pathlib import Path
from random import Random
from time import perf_counter

import application
import business
import recovery_search

ROOT = Path(__file__).resolve().parent
CITIES = ('shenzhen', 'jilin', 'yantai', 'wuhan')


def fixture(source, city, wave):
    rng = Random(8137+wave)
    fleet_size = 2+wave%2
    count = fleet_size*4
    matrix = source.get('distance_matrix')
    if matrix:
        selected = rng.sample(source['node_indices'][1:], count)
        ordered = []
        current = 0
        while selected:
            if len(ordered)%4 == 0:
                current = 0
            current = min(selected, key=lambda node: (matrix[str(current)][str(node)], node))
            ordered.append(current)
            selected.remove(current)
        layout = [(str(node), 0, 0) for node in ordered]
    else:
        selected = rng.sample(source['points'], count)
        lat0 = sum(p[1] for p in selected)/count
        lon0 = sum(p[2] for p in selected)/count
        layout = [(key, (lon-lon0)*111.32*cos(radians(lat0)), (lat-lat0)*111.32)
                  for key, lat, lon in selected]
        layout.sort(key=lambda p: atan2(p[2], p[1]))
    points = {'DEPOT': (-2.0, 0.0)}
    rules = business.defaults()
    tariff = rules['tariff']
    tariff.update(mode=('incremental', 'whole_above', 'whole_above', 'incremental')[city],
                  included_km=(18, 22, 25, 34)[city], base=(140, 150, 160, 170)[city],
                  per_km=(2.0, 2.3, 2.6, 2.8)[city], per_stop=(15, 12, 10, 13)[city])
    vids = [f'T{i+1}' for i in range(fleet_size)]
    data = dict(settings=dict(depot_id='DEPOT', window_rule='finish', data_kind='simulated',
                             travel_source='公开站点投影距离×构造绕行系数，非实际道路矩阵'),
                business=rules, orders=[], vehicles=[], legs={})
    assignment = {vid: [] for vid in vids}
    source_rows = {'DEPOT': '0'} if matrix else {}
    if matrix:
        data['settings']['travel_source'] = '公开匿名距离矩阵，试验暂按公里解释，时间为构造值'
    for index, (key, x, y) in enumerate(layout):
        sid = f'S{index+1:02}'
        points[sid] = (x, y)
        source_rows[sid] = key
        vid = vids[index//4]
        assignment[vid].append(sid)
        qty = ((6, 8, 8, 10), (7, 7, 9, 10), (6, 8, 9, 8))[wave%3][index%4]
        data['orders'].append(dict(id=sid, allowed_vehicles=vids, open=510, due=900, close=920,
            service_minutes=6+index%4, lines=[dict(sku='BOX', quantity=qty, unit_weight_kg=7, unit_volume_m3=.072)],
            pickup=dict(cartons=rng.randint(2, 6), totes=rng.randint(3, 7))))
    for vid in vids:
        data['vehicles'].append(dict(id=vid, max_weight_kg=280, max_volume_m3=2.592,
            fixed_cost=140, cost_per_km=2, shift_start=510, shift_end=960))
    for a, (x, y) in points.items():
        for b, (xx, yy) in points.items():
            if a == b:
                continue
            km = (matrix[source_rows[a]][source_rows[b]] if matrix else
                  .15+hypot(x-xx, y-yy)*rng.uniform(1.16, 1.42))
            data['legs'][a+'>'+b] = dict(distance_km=round(km, 4),
                travel_minutes=round(km*rng.uniform(1.9, 2.7)+.5, 4))
    for vid, stops in assignment.items():
        seq, previous = [], 'DEPOT'
        while stops:
            nearest = min(stops, key=lambda s: (data['legs'][previous+'>'+s]['travel_minutes'], s))
            stops.remove(nearest)
            seq.append(nearest)
            previous = nearest
        assignment[vid] = seq
    data = application.canonicalize(data)
    initial = recovery_search.evaluate_plan(data, assignment)
    if initial is None:
        raise RuntimeError('构造规则基线不符合名义业务约束')
    order_map = {o['id']: o for o in data['orders']}
    for route in initial['plan']['routes'].values():
        for i, stop in enumerate(route['stops']):
            order_map[stop['id']]['due'] = int(stop['finish'])+14+((i+wave)%3)*6
    return data, assignment, source_rows


def stresses(data, seed, count):
    rng = Random(seed)
    ids = [o['id'] for o in data['orders']]
    edges = list(data['legs'])
    output = []
    for i in range(count):
        hot = rng.sample(ids, 1+i%3)
        result = {}
        if i%3 != 1:
            result['pickup'] = {s: dict(cartons=rng.randint(6, 22), totes=rng.randint(28, 65)) for s in hot}
            result['extra_service_minutes'] = {s: rng.randint(3, 12) for s in hot}
        if i%3 != 0:
            result['road_delay_minutes'] = {leg: rng.randint(5, 22) for leg in rng.sample(edges, len(ids))}
        output.append(result)
    return output


def measure(data, routes, scenarios, elapsed_ms):
    checked = recovery_search.evaluate_plan(data, routes, joint_scenarios=scenarios)
    if checked is None:
        raise RuntimeError('独立核验未通过')
    metrics = checked['metrics']
    utilization = {v['id']: (checked['plan']['routes'][v['id']]['volume_m3']/v['max_volume_m3'],
                             checked['plan']['routes'][v['id']]['weight_kg']/v['max_weight_kg'])
                   for v in data['vehicles'] if routes[v['id']]}
    sensitivity_fees = {}
    for factor in (.9, 1.1):
        rules = {**data['business'], 'tariff': {**data['business']['tariff'],
                 'included_km': data['business']['tariff']['included_km']*factor}}
        stressed = recovery_search.evaluate_plan({**data, 'business': rules}, routes)
        sensitivity_fees[str(factor)] = stressed['metrics']['cost']
    return dict(metrics=metrics, routes=routes, search_ms=round(elapsed_ms, 3),
                load_by_vehicle=utilization, fee_threshold_sensitivity=sensitivity_fees)


def run(sources, waves, seconds):
    results = []
    for name, source in sources.items():
        city = CITIES.index(name)
        for wave in range(waves):
            started = perf_counter()
            data, baseline, source_rows = fixture(source, city, wave)
            baseline_ms = (perf_counter()-started)*1000
            training = stresses(data, 9000+wave, 9)
            holdout = stresses(data, 19000+wave, 24)
            assert not any(s in training for s in holdout)
            base = measure(data, baseline, holdout, baseline_ms)
            assert base['metrics']['unserved'] == base['metrics']['late_orders'] == 0
            assert .85 <= base['metrics']['average_volume_utilization'] <= .92
            trials = []
            for max_drop in (0, .02, .05):
                for method in ('local', 'robust_local', 'robust_objective_alns'):
                    for seed in ((17, 29, 41) if method.endswith('_alns') else (17,)):
                        started = perf_counter()
                        plan = recovery_search.solve(data, method=method, joint_scenarios=training,
                            fixed_fleet_baseline=baseline, max_load_drop=max_drop, seed=seed,
                            max_evaluations=100000, time_limit_seconds=seconds)
                        row = measure(data, plan['routes'], holdout, (perf_counter()-started)*1000)
                        assert set(row['load_by_vehicle']) == set(base['load_by_vehicle'])
                        assert row['metrics']['unserved'] == row['metrics']['late_orders'] == 0
                        assert row['metrics']['cost'] <= base['metrics']['cost']+1e-6
                        assert all(x+max_drop+1e-7 >= y
                                   for vid, values in row['load_by_vehicle'].items()
                                   for x, y in zip(values, base['load_by_vehicle'][vid]))
                        trials.append(dict(row, method=method, seed=seed, max_load_drop=max_drop))
            results.append(dict(city=name, wave=wave+1, source_rows=source_rows,
                input_sha256=application.fingerprint(data), baseline=base, trials=trials))
            print(f'{name} wave {wave+1}: {len(trials)} trials', flush=True)
    return dict(scope='public geography, simulated operations', waves_per_city=waves,
                time_cap_seconds=seconds, evaluation_scenarios_per_wave=24,
                sources={k: {f: v for f, v in item.items() if f not in ('points', 'distance_matrix')}
                         for k, item in sources.items()},
                results=results)


def fixed_assignment_reorder(sources, waves):
    """Exact comparison over orderings of the already assigned four stops per vehicle."""
    rows = []
    for name, source in sources.items():
        city = CITIES.index(name)
        for wave in range(waves):
            started = perf_counter()
            data, baseline, _ = fixture(source, city, wave)
            fit = stresses(data, 9000+wave, 9)
            holdout = stresses(data, 19000+wave, 24)
            cap = recovery_search.evaluate_plan(data, baseline)['metrics']['cost']
            choices = {}
            for vid, seq in baseline.items():
                candidates = []
                for order in permutations(seq):
                    trial = recovery_search.evaluate_plan(data, {vid: list(order)}, joint_scenarios=fit)
                    if trial and not trial['metrics']['late_orders']:
                        m = trial['metrics']
                        candidates.append((m['scenario_failed_route_stops'], m['cost'], m['distance'], order))
                choices[vid] = candidates
            best = None
            for combo in product(*choices.values()):
                key = tuple(sum(r[i] for r in combo) for i in range(3))
                if key[1] <= cap+1e-7 and (best is None or key < best[0]):
                    best = (key, combo)
            if best is None:
                raise RuntimeError('固定分车枚举缺少基线候选')
            routes = {vid: list(row[3]) for vid, row in zip(choices, best[1])}
            measured = measure(data, routes, holdout, (perf_counter()-started)*1000)
            rows.append(dict(measured, city=name, wave=wave+1, input_sha256=application.fingerprint(data)))
    return dict(scope='exact ordering within each fixed assignment; public geography, simulated operations',
                results=rows)


def acceptance_replay(sources, prior):
    """Choose on separate screening scenarios and report on fresh final scenarios."""
    results = []
    for record in prior['results']:
        name = record['city']
        wave = record['wave']-1
        data, baseline, _ = fixture(sources[name], CITIES.index(name), wave)
        if application.fingerprint(data) != record['input_sha256']:
            raise ValueError('复验批次指纹不同')
        fit = stresses(data, 9000+wave, 9)
        first_holdout = stresses(data, 19000+wave, 24)
        screen = stresses(data, 29000+wave, 24)
        final = stresses(data, 39000+wave, 24)
        hashes = [{application.fingerprint(s) for s in group} for group in (fit, first_holdout, screen, final)]
        if any(a & b for i, a in enumerate(hashes) for b in hashes[i+1:]):
            raise ValueError('求解、筛选和最终情景应互不重复')
        started = perf_counter()
        initial = measure(data, baseline, screen, 0)
        initial_final = measure(data, baseline, final, 0)
        chosen = initial
        method = 'rule_baseline'
        count_key = 'scenario_full_completions'
        def rank(row):
            return (-row['metrics'][count_key], row['metrics']['cost'])
        for trial in record['trials']:
            if trial['max_load_drop'] != .02 or trial['method'] == 'local':
                continue
            tested = measure(data, trial['routes'], screen, 0)
            fee_ok = all(tested['fee_threshold_sensitivity'][k] <= v+1e-7
                         for k, v in initial['fee_threshold_sensitivity'].items())
            if fee_ok and tested['metrics'][count_key] > initial['metrics'][count_key] and rank(tested) < rank(chosen):
                chosen = tested
                method = trial['method']
        checked = measure(data, chosen['routes'], final, 0)
        results.append(dict(city=name, wave=wave+1, selected_method=method,
            screen_baseline=initial['metrics'][count_key], screen_selected=chosen['metrics'][count_key],
            final_baseline=initial_final['metrics'][count_key], final_selected=checked['metrics'][count_key],
            baseline_fee=initial_final['metrics']['cost'], selected_fee=checked['metrics']['cost'],
            replay_overhead_ms=round((perf_counter()-started)*1000, 3), selected_routes=checked['routes']))
    return dict(scope='exploratory independent screening and fresh simulation holdout', results=results)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--waves', type=int, default=6)
    parser.add_argument('--seconds', type=float, default=1.5)
    parser.add_argument('--reorder-only', action='store_true')
    parser.add_argument('--acceptance-replay', type=Path)
    parser.add_argument('--source', type=Path, default=ROOT/'examples/china-city-spatial-input.json')
    parser.add_argument('--city', choices=CITIES)
    parser.add_argument('--output', type=Path, default=ROOT/'evidence/china-city-robustness.json')
    args = parser.parse_args()
    source_path = args.source
    source_text = source_path.read_bytes()
    sources = json.loads(source_text)
    if args.city:
        sources = {args.city: sources[args.city]}
    if args.acceptance_replay:
        report = acceptance_replay(sources, json.loads(args.acceptance_replay.read_bytes()))
    elif args.reorder_only:
        report = fixed_assignment_reorder(sources, args.waves)
    else:
        report = run(sources, args.waves, args.seconds)
    report['spatial_input_sha256'] = sha256(source_text).hexdigest()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
