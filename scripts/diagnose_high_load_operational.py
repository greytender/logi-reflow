"""Four predeclared 90% constructed stress cases, not actual vehicle data.

Reuses complete route-assignment/order enumeration with legacy, portfolio and
failure-triggered layouts. Neither enumeration nor layout failure proves
physical infeasibility. The existing low-load diagnosis is not overwritten.
"""
from argparse import ArgumentParser
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import application
import operational_constraints as op
from scripts.diagnose_operational_search import run_case


def fixtures():
    cases = []
    for shared_cold in (False, True):
        for pickup in (False, True):
            data, _ = op.sample_data()
            for index, order in enumerate(data['orders']):
                if not shared_cold:
                    order['lines'] = [x for x in order['lines'] if x['sku'] != 'COLD_UNIT']
                for line in order['lines']:
                    if line['sku'] == 'STANDARD_BOX':
                        line['quantity'] = (2 if index == 0 else 3) if shared_cold else 3
                order['pickup'] = dict(cartons=2 if pickup else 0, totes=2 if pickup else 0)
            total_weight = 67 if shared_cold else 63
            for vehicle in data['vehicles']:
                vehicle.update(max_volume_m3=.72, max_weight_kg=total_weight/.9)
            data = application.canonicalize(data)
            policy = op.sample_policy(data)
            for geometry in policy['vehicle_geometry']:
                geometry.update(length_cm=300, width_cm=80, height_cm=30)
            if shared_cold:
                policy['cold_boxes'][0].update(
                    length_cm=60, width_cm=40, height_cm=30,
                    inner_length_cm=40, inner_width_cm=25, inner_height_cm=20,
                    inner_volume_m3=.02)
            policy['assumptions'] = [a for a in policy['assumptions'] if a['parameter'] != 'vehicle_geometry']
            policy['assumptions'].append(dict(
                parameter='vehicle_geometry/vehicle_capacity', evidence='assumed',
                basis='专为90%压力诊断构造300×80×30厘米空间及载重上限；不是实际4.2米车厢或企业车辆能力'))
            policy = op.validate_policy(data, policy)
            standard_volume = sum(line['quantity']*line['unit_volume_m3']
                                  for order in data['orders'] for line in order['lines']
                                  if line['sku'] == 'STANDARD_BOX')
            goods_weight = sum(line['quantity']*line['unit_weight_kg']
                               for order in data['orders'] for line in order['lines'])
            outer_volume = sum(b['length_cm']*b['width_cm']*b['height_cm']/1e6
                               for b in policy['cold_boxes'])
            tare_weight = sum(b['tare_weight_kg'] for b in policy['cold_boxes'])
            departure_volume = standard_volume+outer_volume
            departure_weight = goods_weight+tare_weight
            assert abs(departure_volume-.648) < 1e-9
            assert abs(departure_weight-total_weight) < 1e-9
            for vehicle in data['vehicles']:
                assert abs(departure_volume/vehicle['max_volume_m3']-.9) < 1e-9
                assert abs(departure_weight/vehicle['max_weight_kg']-.9) < 1e-9
            for geometry in policy['vehicle_geometry']:
                assert abs(geometry['length_cm']*geometry['width_cm']*geometry['height_cm']/1e6-.72) < 1e-9
                tote = policy['pickup_geometry']['tote']
                assert tote['height_cm']+tote['nested_height_cm'] == geometry['height_cm']
            if shared_cold:
                assert len(policy['cold_boxes']) == 1
                assert policy['cold_boxes'][0]['max_top_load_kg'] == 0
                assert abs(policy['cold_boxes'][0]['inner_volume_m3']-.02) < 1e-9
            else:
                assert not policy['cold_boxes']
            cases.append(dict(
                id=('shared_cold' if shared_cold else 'ambient')+('_pickup' if pickup else '_no_pickup'),
                description=('共享冷链箱' if shared_cold else '常温标准箱')+'出发单车双维90%，'+('每店回收2纸箱2周转箱' if pickup else '无回收'),
                data=data, policy=policy,
                intended_single_vehicle_departure=dict(weight_kg=departure_weight,
                    volume_m3=departure_volume, weight_utilization=.9, volume_utilization=.9)))
    return cases


def actual_high_load(summary):
    if summary is None:
        return dict(complete_candidate_found=False, every_used_vehicle_at_least_90_percent=None,
                    actual_load_by_vehicle=None)
    loads = summary['load_by_vehicle']
    return dict(complete_candidate_found=True,
                every_used_vehicle_at_least_90_percent=bool(loads) and all(
                    value >= .9-1e-7 for load in loads.values() for value in load.values()),
                actual_load_by_vehicle=loads,
                used_vehicles=summary['used_vehicles'], cost=summary['cost'])


def build():
    results = []
    for case in fixtures():
        result = run_case(case, route_modes=('legacy', 'portfolio', 'fallback'))
        result['intended_single_vehicle_departure'] = case['intended_single_vehicle_departure']
        result['high_load_assessment'] = dict(
            current=actual_high_load(result['current']['summary']),
            references={mode: actual_high_load(ref['best']) for mode, ref in result['references'].items()})
        for name, assessment in [('current', result['high_load_assessment']['current'])]+list(result['high_load_assessment']['references'].items()):
            if assessment['every_used_vehicle_at_least_90_percent']:
                assert len(assessment['used_vehicles']) == 1, name
                for load in assessment['actual_load_by_vehicle'].values():
                    assert all(abs(value-.9) < 1e-7 for value in load.values()), (name, load)
        results.append(result)
    return dict(schema='logireflow.high-load-operational-diagnosis.v1',
                scope='four predeclared constructed stress fixtures; not enterprise orders or actual vehicle geometry',
                policy='same complete declared constraints, upright boxes, no relaxed support or cold-shell rules',
                high_load_definition='actual model departure weight and external occupied volume divided by the assigned vehicle limits; each used vehicle checked separately',
                reference_scope='all complete assignments/stop orders under each finite constructor, not globally optimal packing; failures retained',
                cases=results, boundaries=op.BOUNDARIES)


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=ROOT/'outputs/search-diagnosis/high-load-search.json')
    args = parser.parse_args()
    report = build()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(dict(output=str(args.output), cases=[dict(
        id=c['id'], high_load=c['high_load_assessment'],
        references={mode: dict(complete=ref['enumeration_complete'],
                              feasible_complete_plans=ref['feasible_complete_plans'],
                              errors=ref['unique_route_error_counts'])
                    for mode, ref in c['references'].items()}) for c in report['cases']]), ensure_ascii=False))
