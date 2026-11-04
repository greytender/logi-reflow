"""Exercise the same local workflow with two synthetic port configurations."""
import argparse
import json
from math import ceil, isclose
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import multiwarehouse as mw
import recovery_search


def port_input(index):
    sample = mw.sample(physical_loading=True)
    warehouse = sample['warehouses'][index]
    wid = warehouse['id']
    data = dict(schema_version=mw.SCHEMA, warehouses=[warehouse],
        orders=[o for o in sample['orders'] if o['warehouse_id'] == wid],
        vehicles=[v for v in sample['vehicles'] if v['warehouse_id'] == wid],
        legs={k: v for k, v in sample['legs'].items() if k.startswith(wid)})
    warehouse['name'] = ('模拟港甲·药房城配', '模拟港乙·快消城配')[index]
    warehouse['settings']['travel_source'] = '跨港演练构造道路'
    if index:
        warehouse['business']['tariff'].update(mode='whole_above', base=98,
            included_km=18, per_km=3, per_stop=9)
        warehouse['business']['packaging'].update(tote_first_m3=.085, tote_nested_m3=.025)
        for vehicle in data['vehicles']:
            vehicle.update(max_weight_kg=180, max_volume_m3=2.1, shift_end=900)
        for spec in warehouse['loading_profile']['vehicles']:
            spec.update(length_cm=220, width_cm=140, height_cm=150)
        for order in data['orders']:
            order.update(open=560, due=860, close=900, service_minutes=12, pickup=dict(cartons=6, totes=5))
            order['lines'][0].update(unit_weight_kg=8, unit_volume_m3=.08)
        for item in warehouse['loading_profile']['items']:
            item.update(length_cm=50, width_cm=40, height_cm=40)
        for leg in data['legs'].values():
            leg['distance_km'] *= 2
            leg['travel_minutes'] *= 2
    return data, wid


def run_port(store, index, route_solver=None):
    data, wid = port_input(index)
    started = perf_counter()
    task = store.create(data['warehouses'][0]['name'], data)
    task = store.solve(task['id'], task['revision'])
    canonical = task['data']
    scenarios = [{o['id']: {**o['pickup'], 'totes': o['pickup']['totes']+2}}
                 for o in canonical['orders']]
    part = mw.split(canonical)[wid]
    profile = canonical['warehouses'][0]['loading_profile']
    if route_solver is None:
        search = recovery_search.solve(part, method='robust_objective_alns', scenarios=scenarios, profile=profile)
    else:
        candidate = route_solver(part)
        search = recovery_search.evaluate_plan(part, candidate['routes'], scenarios=scenarios, profile=profile)
        if not candidate.get('solver_feasible', True) or search is None or search['metrics']['unserved']:
            return dict(port=data['warehouses'][0]['name'], search_method=candidate['method'],
                        search_ms=candidate['elapsed_ms'], status='candidate_rejected',
                        candidate_routes=candidate['routes'],
                        reason='候选未通过完整名义路线及声明三维卸货核验')
        search.update(method=candidate['method'], elapsed_ms=candidate['elapsed_ms'])
    task = store.manual(task['id'], task['revision'], {wid: search['routes']})
    plan = mw.current_plan(task)
    solve_ms = round((perf_counter()-started)*1000, 2)
    task = store.adopt(task['id'], task['revision'], plan['id'], True)
    csv = store.export_csv(task['id'], task['revision'])
    stops = {o['id']: o for o in data['orders']}
    while not mw.view(task)['complete']:
        state = mw.view(task)['execution'][wid]
        vid, route = next((v, r) for v, r in state['routes'].items() if not r['returned'])
        stop = route['remaining'][0] if route['remaining'] else None
        record = dict(vehicle=vid, kind='stop' if stop else 'return',
            stop_id=stop['id'] if stop else None,
            finish=ceil(stop['finish'] if stop else route['estimated_return']),
            pickup=stops[stop['id']]['pickup'] if stop else {},
            source='simulated', note='跨港端到端演练')
        task = store.action(task['id'], task['revision'], wid, 'receipt', record, True)
    package = store.export_package(task['id'], task['revision'])
    result = plan['warehouses'][wid]
    receipts = task['execution_tasks'][wid]['receipts']
    routes = result['optimized']['routes']
    tariff = data['warehouses'][0]['business']['tariff']
    reconstructed_fee = 0
    for route in routes.values():
        if not route['sequence']:
            continue
        nodes = [wid, *route['sequence'], wid]
        km = sum(data['legs'][a+'>'+b]['distance_km'] for a, b in zip(nodes, nodes[1:]))
        fee = tariff['base']
        if km > tariff['included_km']:
            billable = km if tariff['mode'] == 'whole_above' else km-tariff['included_km']
            fee += tariff['per_km']*billable
        reconstructed_fee += fee+tariff['per_stop']*len(route['sequence'])
    metrics = plan['summary']['optimized']
    signed = {r['stop_id']: r for r in receipts if r['kind'] == 'stop'}
    if (set(signed) != set(stops) or any(signed[s]['pickup'] != o['pickup'] for s, o in stops.items())
            or len(csv.splitlines()) != len(stops)+1
            or not isclose(reconstructed_fee, metrics['cost'], abs_tol=.001)
            or (package['dispatch_status'], package['integration_status']) != ('not_sent', 'not_connected')):
        raise RuntimeError('跨港演练签收、回收、清单、费用或交接边界核对失败')
    return dict(port=data['warehouses'][0]['name'], tariff_mode=tariff['mode'], local_candidate_pipeline_ms=solve_ms,
        search_method=search['method'], search_ms=search['elapsed_ms'],
        scenario_count=len(scenarios), scenario_full_completions=search['metrics']['scenario_full_completions'],
        metrics=metrics, signed_on_time=sum(signed[s]['finish'] <= o['due'] for s, o in stops.items()),
        simulated_pickup={k: sum(r['pickup'][k] for r in signed.values()) for k in ('cartons', 'totes')},
        checked_boxes=result['audit']['physical_loading']['checked_boxes'],
        fee_recalculated_yuan=round(reconstructed_fee, 4), receipts=len(receipts),
        dispatch_status=package['dispatch_status'], integration_status=package['integration_status'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pyvrp', action='store_true', help='使用可选 PyVRP 生成候选，再走同一本地核验流程')
    args = parser.parse_args()
    route_solver = None
    if args.pyvrp:
        from pyvrp_ils import solve as route_solver
    with TemporaryDirectory() as directory:
        store = mw.MultiWarehouseStore(Path(directory) / 'ports.sqlite')
        print(json.dumps(dict(data_kind='simulated', ports=[run_port(store, i, route_solver) for i in range(2)]),
            ensure_ascii=False, indent=2))
