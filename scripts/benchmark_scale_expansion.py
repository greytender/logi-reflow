"""Deterministic capacity expansion checks; no live orders or multi-trip claims.

Basic planning is measured separately from replay of those fixed routes under
declared cuboid/pickup/handling rules. This is not geometric joint-search speed.
"""
from argparse import ArgumentParser
from copy import deepcopy
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import application as app
import multiwarehouse as mw
import operational_constraints as op
import warehouse_loading


SCALES=((1,6,2),(4,12,4),(8,24,8),(16,24,8))


def fixture(warehouse_count, stops_per_warehouse, vehicles_per_warehouse):
    """All stores remain unsplit; each truck fits exactly three store manifests."""
    raw=dict(schema_version=mw.SCHEMA,warehouses=[],orders=[],vehicles=[],legs={})
    template=app.sample()
    for index in range(warehouse_count):
        wid=f'WH_{index+1}'
        settings=deepcopy(template['settings']);settings['depot_id']=wid
        settings['travel_source']='deterministic constructed directed matrix; not GPS/TMS'
        raw['warehouses'].append(dict(id=wid,name=f'构造仓库{index+1}',settings=settings,business=deepcopy(template['business'])))
        vids=[f'{wid}_V{i+1}' for i in range(vehicles_per_warehouse)]
        for vid in vids:
            raw['vehicles'].append(dict(id=vid,warehouse_id=wid,max_weight_kg=70,max_volume_m3=.72,
                fixed_cost=140,cost_per_km=2,shift_start=510,shift_end=1080))
        nodes=[wid]
        for position in range(stops_per_warehouse):
            sid=f'{wid}_S{position+1}';nodes.append(sid)
            raw['orders'].append(dict(id=sid,warehouse_id=wid,lines=[dict(sku='STANDARD_BOX',quantity=3,
                unit_weight_kg=7,unit_volume_m3=.072)],allowed_vehicles=list(vids),open=510,due=1020,
                close=1080,service_minutes=4,pickup=dict(cartons=2,totes=2)))
        for a,source in enumerate(nodes):
            for b,target in enumerate(nodes):
                if source!=target:
                    distance=abs(a-b)+1
                    raw['legs'][source+'>'+target]=dict(distance_km=distance,travel_minutes=distance*2)
    return raw


def replay_policy(data):
    policy=op.sample_policy(data)
    for geometry in policy['vehicle_geometry']:
        geometry.update(length_cm=300,width_cm=80,height_cm=30)
    policy['assumptions']=[a for a in policy['assumptions'] if a['parameter']!='vehicle_geometry']
    policy['assumptions'].append(dict(parameter='vehicle_geometry/vehicle_capacity',evidence='assumed',
        basis='300×80×30厘米、70千克上限为同规则90%压力测试；非实际4.2米车型；常温标准箱和空包装，无冷链商品'))
    return op.validate_policy(data,policy)


def run_scale(shape, seconds=60):
    count,stops,vehicles=shape
    raw=fixture(*shape);start=perf_counter();clean=mw.canonicalize(raw)
    canonical_ms=(perf_counter()-start)*1000
    start=perf_counter();groups=mw.split(clean);split_ms=(perf_counter()-start)*1000
    assert len(clean['orders'])==count*stops and len(clean['vehicles'])==count*vehicles
    assert sum(len(p['orders']) for p in groups.values())==count*stops
    assert sum(len(p['vehicles']) for p in groups.values())==count*vehicles
    result=dict(shape=dict(warehouses=count,stops_per_warehouse=stops,available_vehicles_per_warehouse=vehicles),
        input_sha256=app.fingerprint(clean),input_json_bytes=len(app.encode(raw).encode('utf-8')),
        canonicalization=dict(passed=True,orders_preserved=len(clean['orders']),vehicles_preserved=len(clean['vehicles']),
            directed_edges_preserved=len(clean['legs']),elapsed_ms=round(canonical_ms,3)),
        split=dict(passed=True,warehouses_preserved=len(groups),elapsed_ms=round(split_ms,3)),
        soft_budget_seconds=seconds,budget_scope='checked between warehouses/stages; a running solve can exceed the soft deadline',
        basic_planning=[],declared_fixed_route_replay=[])
    deadline=perf_counter()+seconds;results={}
    for wid,data in groups.items():
        if perf_counter()>=deadline:break
        t=perf_counter();comparison=warehouse_loading.compare(data)
        results[wid]=comparison;plan=comparison['optimized']
        result['basic_planning'].append(dict(warehouse=wid,elapsed_ms=round((perf_counter()-t)*1000,3),
            independent_audit=comparison['audit']['optimized'],served=plan['metrics']['served'],
            unserved=plan['metrics']['unserved'],used_vehicle_routes=sum(bool(r['sequence']) for r in plan['routes'].values()),
            sequences={vid:r['sequence'] for vid,r in plan['routes'].items()}))
    basic_complete=len(results)==len(groups)
    result['basic_all_warehouses_solved']=basic_complete
    result['basic_batch_audit']=mw.audit(clean,results) if basic_complete else None
    result['basic_all_orders_served']=basic_complete and all(not r['optimized']['unserved'] for r in results.values())
    # This is replay of an already chosen route, not a claim that every search
    # move was geometrically checked or that large cold-chain cases were solved.
    for wid,comparison in results.items():
        if perf_counter()>=deadline:break
        data=groups[wid];t=perf_counter();policy=replay_policy(data)
        report=op.evaluate_plan(data,comparison['optimized'],policy)
        action_audits={vid:op.audit_actions(r) for vid,r in report['routes'].items()}
        result['declared_fixed_route_replay'].append(dict(warehouse=wid,elapsed_ms=round((perf_counter()-t)*1000,3),
            passed=report['passed'],complete=report['complete'],errors=report['errors'],action_audits=action_audits,
            frame_count=sum(len(r.get('frames',[])) for r in report['routes'].values()),
            placement_checks=sum(r.get('placement_checks',0) for r in report['routes'].values()),
            departure_load_by_used_vehicle={vid:dict(weight_utilization=r['adjusted_route']['weight_kg']/next(v['max_weight_kg'] for v in data['vehicles'] if v['id']==vid),
                volume_utilization=r['adjusted_route']['volume_m3']/next(v['max_volume_m3'] for v in data['vehicles'] if v['id']==vid))
                for vid,r in report['routes'].items() if comparison['optimized']['routes'][vid]['sequence']}))
    replay=result['declared_fixed_route_replay']
    result['replay_all_warehouses_checked']=len(replay)==len(groups)
    result['replay_all_passed']=len(replay)==len(groups) and all(r['passed'] and r['complete'] and all(a['passed'] for a in r['action_audits'].values()) for r in replay)
    result['soft_budget_reached']=perf_counter()>=deadline
    result['basic_planning_ms']=round(sum(x['elapsed_ms'] for x in result['basic_planning']),3)
    result['fixed_route_replay_ms']=round(sum(x['elapsed_ms'] for x in replay),3)
    return result


def build(seconds=60):
    if seconds<0:raise ValueError('seconds must be nonnegative')
    return dict(schema='logireflow.scale-expansion.v1',scope='constructed regular-box and return-packaging fixtures; no enterprise evidence',
        limits=dict(warehouses=mw.MAX_WAREHOUSES,stops_per_warehouse=app.MAX_ORDERS,vehicles_per_warehouse=app.MAX_VEHICLES),
        physical_limits_unchanged=dict(entities=op.MAX_ENTITIES,placement_checks=op.MAX_PLACEMENT_CHECKS,frames=op.MAX_FRAMES),
        exclusions=['no same-vehicle multiple trips','no shared/cross-warehouse fleet or global optimization',
            'no cold-chain scale proof','fixed-route replay is not geometric joint-search performance','no enterprise dispatch'],
        cases=[run_scale(shape,seconds) for shape in SCALES])


if __name__=='__main__':
    parser=ArgumentParser(description=__doc__);parser.add_argument('--seconds',type=float,default=60)
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/algorithm-upgrade/scale.json');args=parser.parse_args()
    report=build(args.seconds);args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(dict(output=str(args.output),cases=[{k:c[k] for k in ('shape','input_json_bytes','basic_all_orders_served',
        'replay_all_passed','basic_planning_ms','fixed_route_replay_ms','soft_budget_reached')} for c in report['cases']]),ensure_ascii=False))
