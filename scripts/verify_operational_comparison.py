"""Three constructed counterfactuals; no enterprise inputs or benefit claims."""
from copy import deepcopy
from hashlib import sha256
from itertools import permutations
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import application
import operational_constraints as op
import pilot


def digest(value):
    return sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def check(data,policy,sequence):
    vehicle=data['vehicles'][0]
    route=pilot.evaluator(data)(list(sequence),vehicle,{o['id']:o for o in data['orders']})
    if route is None:return dict(base_route_passed=False,operational_passed=False,errors=['base_route_infeasible'])
    raw=op.evaluate_route(data,route,vehicle,op.validate_policy(data,policy))
    return dict(sequence=list(sequence),base_route_passed=True,operational_passed=raw['passed'],
                distance_km=route['distance'],fare=route['cost'],base_return_minutes=route['return_at'],
                operational_return_minutes=raw['adjusted_route']['return_at'] if raw['passed'] else None,
                extra_handling_minutes=raw['time_adjustments']['extra_minutes'],
                rehandle_moves=raw['time_adjustments']['rehandle_moves'],
                errors=raw['errors'],frame_count=len(raw['frames']),
                departure_weight_kg=raw['adjusted_route']['weight_kg'],
                departure_volume_m3=raw['adjusted_route']['volume_m3'])


def build():
    cases=[]
    # No regular cartons: legacy arithmetic releases product volume; the
    # declared shared cold shell remains on the vehicle until return.
    data,_=op.sample_data()
    for o in data['orders']:
        o['lines']=[l for l in o['lines'] if l['sku']=='COLD_UNIT']
        o['pickup']=dict(cartons=10,totes=0)
    for v in data['vehicles']:v['max_volume_m3']=.12
    data=application.canonicalize(data);policy=op.sample_policy(data)
    for v in policy['vehicle_geometry']:v.update(length_cm=60,width_cm=40,height_cm=50)
    seq=[o['id'] for o in data['orders']]
    outcome=check(data,policy,seq)
    assert outcome['base_route_passed'] and not outcome['operational_passed']
    cases.append(dict(id='shared_shell_persists',data_kind='simulated',input_sha256=digest([data,policy]),
        question='只按商品体积卸货后释放空间，是否漏掉仍在车上的共享冷链箱？',
        baseline_method='现有商品重量/总体积逐站检查，不含共享箱外形和自重',
        baseline_claim='该路线通过原容量规则',same_rule_recheck=outcome,
        interpretation='同一路线按完整实体重新检查被拒绝；这证明简单体积结论不足，不是企业收益或新方案更省。',
        inputs=dict(data=data,policy=policy)))
    # Intentionally small geometric boundary, not a claim about a real van.
    data,_=op.sample_data()
    for o in data['orders']:
        o['lines']=[dict(sku='LOW_BOX',quantity=1,unit_weight_kg=7,unit_volume_m3=.024)]
        o['pickup']=dict(cartons=0,totes=1)
    for v in data['vehicles']:v['max_volume_m3']=.5
    data=application.canonicalize(data);policy=op.sample_policy(data)
    for g in policy['goods']:g.update(length_cm=60,width_cm=40,height_cm=10)
    for v in policy['vehicle_geometry']:v.update(length_cm=240,width_cm=160,height_cm=20)
    outcome=check(data,policy,[o['id'] for o in data['orders']])
    assert outcome['base_route_passed'] and not outcome['operational_passed']
    assert any(e['code']=='pickup_spatial_not_found' for e in outcome['errors'])
    cases.append(dict(id='pickup_shape_not_just_volume',data_kind='simulated',input_sha256=digest([data,policy]),
        question='出发能装下，沿途总体积也够，是否代表回收包装能进入车厢？',
        baseline_method='出发及沿途总重量/总体积检查',baseline_claim='通过容量检查',same_rule_recheck=outcome,
        interpretation='构造低矮空间20cm、保持向上的周转箱25cm，回收无法按声明朝向进入；低矮空间为边界反例，非实际4.2米车。',
        inputs=dict(data=data,policy=policy)))
    # The shortest route visits the only cold store last; a longer route can
    # satisfy declared equipment duration. Both are replayed under same policy.
    data,_=op.sample_data()
    for i,o in enumerate(data['orders']):
        if i!=2:o['lines']=[l for l in o['lines'] if l['sku']!='COLD_UNIT']
    data=application.canonicalize(data);policy=op.sample_policy(data)
    policy['cold_boxes'][0]['valid_minutes']=50
    all_results=[check(data,policy,seq) for seq in permutations(o['id'] for o in data['orders'])]
    shortest=min(all_results,key=lambda r:r['distance_km'])
    feasible=[r for r in all_results if r['operational_passed']]
    best=min(feasible,key=lambda r:(r['distance_km'],r['extra_handling_minutes']))
    assert not shortest['operational_passed'] and best['operational_passed'] and best['distance_km']>shortest['distance_km']
    sensitivity=[]
    for duration in (35,45,50,60,90,180):
        altered=deepcopy(policy);altered['cold_boxes'][0]['valid_minutes']=duration
        checks=[check(data,altered,seq) for seq in permutations(o['id'] for o in data['orders'])]
        ok=[r for r in checks if r['operational_passed']]
        sensitivity.append(dict(valid_minutes=duration,feasible_sequences=len(ok),
            min_feasible_distance_km=min((r['distance_km'] for r in ok),default=None)))
    cases.append(dict(id='cold_time_vs_distance',data_kind='simulated',input_sha256=digest([data,policy]),
        question='更短的路线，是否仍能在共享箱声明有效时限内交付冷链商品？',
        baseline_method='枚举同一三店的6种顺序，按距离选最短',
        baseline_same_rule_recheck=shortest,candidate_same_rule_recheck=best,
        all_six_sequences=all_results,sensitivity=sensitivity,
        tradeoff=dict(extra_km=best['distance_km']-shortest['distance_km'],fare_change=best['fare']-shortest['fare']),
        interpretation='更长路线满足所声明冷链时限；无温度实测，不证明真实冷链合规，也不宣传距离或费用全面改善。',
        inputs=dict(data=data,policy=policy)))
    return dict(schema='logireflow.operational-comparison.v1',data_kind='simulated',enterprise_data_used=False,
        cases=cases,boundaries=op.BOUNDARIES,
        conclusion='三个差异均来自明确构造反例；用于验证缺少约束会改变结论，不是统计总体优势或现场履约证据。')


if __name__=='__main__':
    result=build()
    output=ROOT/'evidence'/'operational-comparison-20261105.json'
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(output=str(output),cases=[dict(id=c['id'],tradeoff=c.get('tradeoff'),sensitivity=c.get('sensitivity')) for c in result['cases']]),ensure_ascii=False))
