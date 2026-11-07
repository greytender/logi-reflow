"""Predeclared fixed-route layout validation across constructed size families.

This changes neither dispatch nor the prior diagnostic reports. Cases are
related constructed stress fixtures, not independent enterprise observations.
All failures remain in the report. No route or variant is selected on outcomes.
"""
from argparse import ArgumentParser
from copy import deepcopy
import gzip
from hashlib import sha256
import json
from pathlib import Path
from random import Random
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

import application
import operational_constraints as op
import pilot

SEED = 20261105
PROFILES = ((40, 30, 20, 4), (50, 35, 25, 6), (70, 45, 35, 9))
COLD_DISTRIBUTIONS = ('ambient', 'shared_all', 'shared_ends')
PICKUP_PRESSURES = ('low', 'high')
ORDERS = ('forward', 'reverse')
MODES = ('legacy', 'portfolio', 'fallback')


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             allow_nan=False).encode('utf-8')).hexdigest()


def design():
    return dict(seed=SEED, profiles=[list(p) for p in PROFILES],
                cold_distributions=list(COLD_DISTRIBUTIONS),
                pickup_pressures=list(PICKUP_PRESSURES), fixed_orders=list(ORDERS),
                case_count=18, fixed_route_count=36,
                selection='full factorial; retain failures; no outcome-based case or route selection',
                allocation='all stores assigned to the first existing vehicle; no extra vehicle')


def fixtures():
    cases = []
    for profile_index, (length, width, height, unit_weight) in enumerate(PROFILES):
        for cold_index, cold in enumerate(COLD_DISTRIBUTIONS):
            for pickup_index, pickup in enumerate(PICKUP_PRESSURES):
                case_seed=SEED+100*profile_index+10*cold_index+pickup_index
                rng=Random(case_seed)
                data,_=op.sample_data()
                counts=[2,3,4] if cold=='ambient' else [2,3,3]
                rng.shuffle(counts)
                cold_members=set(range(3)) if cold=='shared_all' else ({0,2} if cold=='shared_ends' else set())
                for index,order in enumerate(data['orders']):
                    order['lines']=[dict(sku='BOX',quantity=counts[index],
                        unit_weight_kg=unit_weight,unit_volume_m3=length*width*height/1e6)]
                    if index in cold_members:
                        order['lines'].append(dict(sku='COLD_UNIT',quantity=2,
                            unit_weight_kg=.5,unit_volume_m3=.001))
                    order.update(pickup=dict(cartons=1 if pickup=='low' else 5,
                        totes=1 if pickup=='low' else 3),service_minutes=5,
                        due=660,close=690)
                data=application.canonicalize(data)
                policy=op.sample_policy(data)
                for goods in policy['goods']:
                    if goods['kind']=='regular':
                        goods.update(length_cm=length,width_cm=width,height_cm=height)
                for box in policy['cold_boxes']:
                    inner=(length-6,width-6,height-6)
                    box.update(length_cm=length,width_cm=width,height_cm=height,
                        inner_length_cm=inner[0],inner_width_cm=inner[1],inner_height_cm=inner[2],
                        inner_volume_m3=inner[0]*inner[1]*inner[2]/1e6,
                        valid_minutes=120,max_top_load_kg=0)
                for geom in policy['vehicle_geometry']:
                    geom.update(length_cm=5*length,width_cm=2*width,height_cm=height)
                carton_height=.04*height
                tote_height=.6*height
                nested_height=.15*height
                policy['pickup_geometry']=dict(
                    carton=dict(length_cm=length,width_cm=width,height_cm=carton_height),
                    tote=dict(length_cm=length,width_cm=width,height_cm=tote_height,
                              nested_height_cm=nested_height))
                packaging=data['business']['packaging']
                packaging.update(carton_flat_m3=length*width*carton_height/1e6,
                    tote_first_m3=length*width*tote_height/1e6,
                    tote_nested_m3=length*width*nested_height/1e6)
                regular_weight=sum(counts)*unit_weight
                cold_weight=len(cold_members)+8*bool(cold_members)
                total_weight=regular_weight+cold_weight
                total_volume=(sum(counts)+bool(cold_members))*length*width*height/1e6
                for vehicle in data['vehicles']:
                    vehicle.update(max_weight_kg=total_weight/.9,
                        max_volume_m3=10*length*width*height/1e6)
                data=application.canonicalize(data)
                policy['assumptions']=[dict(parameter='all_fixture_dimensions_and_capacities',
                    evidence='assumed',basis='预设多尺寸高装载压力测试；不是实际车辆、企业箱规或实测工时')]
                policy=op.validate_policy(data,policy)
                assert abs(total_volume/data['vehicles'][0]['max_volume_m3']-.9)<1e-8
                assert abs(total_weight/data['vehicles'][0]['max_weight_kg']-.9)<1e-8
                cases.append(dict(id=f'size{profile_index+1}_{cold}_{pickup}',
                    seed=case_seed,profile=[length,width,height,unit_weight],
                    cold_distribution=cold,pickup_pressure=pickup,
                    intended_departure_weight_kg=total_weight,
                    intended_departure_volume_m3=total_volume,
                    data=data,policy=policy))
    assert len(cases)==18
    return cases


def result_row(report,vehicle,elapsed_ms,checks,mode):
    search=report.get('layout_search',{})
    valid=report.get('passed') is True
    replay=op.audit_actions(report) if report.get('frames') else dict(passed=False,errors=['no_recorded_states'])
    if valid and not replay['passed']:raise AssertionError('successful layout failed recorded-state audit')
    route=report.get('adjusted_route',{})
    times=report.get('time_adjustments',{})
    return dict(mode=mode,passed=valid,status=report['status'],
        error_codes=sorted({error['code'] for error in report['errors']}),
        independent_action_audit=replay,elapsed_ms=round(elapsed_ms,3),
        placement_checks=search.get('placement_checks',checks),
        layout_trials=len(search['trials']) if search else 1,
        layout_budget_exhausted=search.get('budget_exhausted',False) or report['status']=='SEARCH_BUDGET_EXHAUSTED',
        selected_variant=search.get('selected_variant',report.get('packing_variant','legacy')),
        completed_rehandle_moves=times.get('rehandle_moves') if valid else None,
        completed_high_risk_rehandle_moves=times.get('high_risk_rehandle_moves') if valid else None,
        completed_extra_minutes=times.get('extra_minutes') if valid else None,
        completed_return_at=route.get('return_at') if valid else None,
        completed_late_orders=route.get('late_orders') if valid else None,
        recorded_partial_action_count=len(report.get('actions',[])),
        weight_utilization=route.get('weight_kg',0)/vehicle['max_weight_kg'] if valid else None,
        volume_utilization=route.get('volume_m3',0)/vehicle['max_volume_m3'] if valid else None,
        witness_sha256=digest({k:report.get(k) for k in ('boxes','frames','actions')}))


def evaluate_fixed_route(case,order_name,modes=MODES):
    data,policy=case['data'],case['policy']
    original=digest([data,policy])
    vehicle=data['vehicles'][0]
    sequence=[order['id'] for order in data['orders']]
    if order_name=='reverse':sequence.reverse()
    elif order_name!='forward':raise ValueError('unknown fixed stop order')
    route=pilot.evaluator(data)(sequence,vehicle,{o['id']:o for o in data['orders']})
    rows={};witnesses={}
    if route is not None:
        for mode in modes:
            started=perf_counter();work={'placement_checks':0}
            if mode=='legacy':report=op.evaluate_route(data,route,vehicle,policy,_work=work)
            elif mode=='batch':
                report=op.evaluate_route(data,route,vehicle,policy,_work=work,
                                        handling_variant='stop_batch')
            elif mode in ('portfolio','fallback','portfolio_batch'):
                report=op.search_route_layouts(data,route,vehicle,policy,
                    max_variants=len(op.LAYOUT_VARIANTS),stop_on_first_feasible=mode=='fallback',
                    **({'handling_variant':'stop_batch'} if mode=='portfolio_batch' else {}))
            else:raise ValueError('unknown layout mode')
            rows[mode]=result_row(report,vehicle,(perf_counter()-started)*1000,
                                  work['placement_checks'],mode)
            witnesses[mode]=report
    assert original==digest([data,policy])
    return dict(case_id=case['id'],order=order_name,sequence=sequence,
                fixed_vehicle=vehicle['id'],base_route_passed=route is not None,
                fare=route['cost'] if route else None,
                distance_km=route['distance'] if route else None,
                input_sha256=original,results=rows),witnesses


def summary(rows,modes=MODES):
    result={}
    for mode in modes:
        available=[r['results'][mode] for r in rows if mode in r['results']]
        paired=[(r['results']['legacy'],r['results'][mode]) for r in rows
                if 'legacy' in r['results'] and mode in r['results']]
        both=[(old,new) for old,new in paired if old['passed'] and new['passed']]
        deltas=[new['completed_rehandle_moves']-old['completed_rehandle_moves'] for old,new in both]
        result[mode]=dict(evaluated_routes=len(available),completed_routes=sum(r['passed'] for r in available),
            recovered_routes=sum(not old['passed'] and new['passed'] for old,new in paired),
            lost_routes=sum(old['passed'] and not new['passed'] for old,new in paired),
            both_complete_routes=len(both),moves_improved_routes=sum(d<0 for d in deltas),
            moves_degraded_routes=sum(d>0 for d in deltas),
            paired_completed_move_delta_sum=sum(deltas),
            placement_checks_total=sum(r['placement_checks'] for r in available),
            layout_trials_total=sum(r['layout_trials'] for r in available),
            elapsed_ms_total=round(sum(r['elapsed_ms'] for r in available),3),
            budget_exhausted_routes=sum(r['layout_budget_exhausted'] for r in available))
    return result


def build(output_dir,modes=MODES):
    output_dir=Path(output_dir);output_dir.mkdir(parents=True,exist_ok=True)
    cases=fixtures()
    manifest=dict(design=design(),design_sha256=digest(design()),
        fixture_sha256={c['id']:digest([c['data'],c['policy']]) for c in cases},
        ordering='manifest written before any layout outcome is evaluated')
    (output_dir/'layout-generalization-manifest.json').write_text(
        json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    rows=[];witnesses={}
    variants=list(op.LAYOUT_VARIANTS)
    for case in cases:
        for order in ORDERS:
            row,reports=evaluate_fixed_route(case,order,modes=modes)
            rows.append(row);witnesses[case['id']+'/'+order]=reports
    witness_path=output_dir/'layout-generalization-witnesses.json.gz'
    with gzip.open(witness_path,'wt',encoding='utf-8') as out:
        json.dump(witnesses,out,ensure_ascii=False,allow_nan=False)
    report=dict(schema='logireflow.layout-generalization.v1',design=manifest,
        layout_variants=variants,fixture_count=len(cases),fixed_route_count=len(rows),
        scope='predeclared related constructed size/load/cold/recovery families; not independent enterprise data',
        comparison='fixed vehicle, identical stop order, capacities, fare, distance and policy per paired comparison',
        timing_scope='whole layout evaluation including policy validation and independent audit; not full human scheduling time',
        workload_scope='route placement checks only; cold-box internal policy validation excluded',
        limitations=['No route optimization or geometry optimum proof.',
            'Failed partial actions are not reported as completed-route savings.',
            'Neither move counts nor assumed move time proves real field productivity or damage reduction.',
            'Cases share geography and a generator; aggregate counts are descriptive, not statistical evidence of enterprise generalization.'],
        inputs={c['id']:{k:v for k,v in c.items()} for c in cases},
        modes=list(modes),results=rows,summary=summary(rows,modes=modes),witness_file=str(witness_path))
    (output_dir/'layout-generalization.json').write_text(
        json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    return report


if __name__=='__main__':
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'outputs/search-diagnosis')
    parser.add_argument('--batch',action='store_true',help='also compare stop-batched handling under identical layouts')
    args=parser.parse_args();report=build(args.output_dir,MODES+('batch','portfolio_batch') if args.batch else MODES)
    print(json.dumps(dict(design_sha256=report['design']['design_sha256'],
        fixtures=report['fixture_count'],routes=report['fixed_route_count'],
        variants=report['layout_variants'],summary=report['summary']),ensure_ascii=False))
