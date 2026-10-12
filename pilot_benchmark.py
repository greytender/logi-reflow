"""Reproducible constructed cases, including failures, never enterprise evidence."""
import csv
import json
import random
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import pilot

ROOT=Path(__file__).resolve().parent


def sample(stops=30,seed=20261011):
    rng=random.Random(seed); order_rows=[]; nodes={'WH':[0,0]}
    fleet=[dict(vehicle_id=f'TRUCK_{i+1}',max_weight_kg=800+200*i,max_volume_m3=5+i,
        fixed_cost=50+10*i,cost_per_km=2+.2*i,shift_start='08:00',shift_end='16:00') for i in range(5)]
    for i in range(stops):
        sid=f'STORE_{i+1:03}'; nodes[sid]=[rng.uniform(-15,15),rng.uniform(-15,15)]
        opening='08:00' if i%3 else '09:00'; due='11:00' if i%2 else '10:30'
        allowed='TRUCK_1|TRUCK_2' if i%7==0 else ''
        service=6+i%5
        for j in range(10):
            order_rows.append(dict(stop_id=sid,sku=f'SKU_{j+1:03}',quantity=rng.randint(1,3),
                unit_weight_kg=round(rng.uniform(2,9),2),unit_volume_m3=round(rng.uniform(.01,.05),3),
                open=opening,due=due,close='14:00',service_minutes=service,allowed_vehicles=allowed))
    travel=[]
    for a,pa in nodes.items():
        for b,pb in nodes.items():
            if a==b: continue
            # Explicitly constructed road-like matrix, not a map-provider result.
            km=round((abs(pa[0]-pb[0])+abs(pa[1]-pb[1]))*rng.uniform(1.05,1.25),3)
            minutes=round(km/rng.uniform(.35,.65)+rng.uniform(1,4),3)
            travel.append(dict(from_id=a,to_id=b,distance_km=km,travel_minutes=minutes))
    return dict(orders=order_rows,vehicles=fleet,travel=travel,settings=dict(depot_id='WH',data_kind='simulated',
        travel_source='固定随机种子的构造有向路程与行驶时间；不是实际地图或企业数据',window_rule='finish'))


def write_bundle(bundle,directory):
    directory=Path(directory); directory.mkdir(parents=True,exist_ok=True)
    for name,fields in [('orders',pilot.ORDER_FIELDS),('vehicles',pilot.VEHICLE_FIELDS),('travel',pilot.TRAVEL_FIELDS)]:
        with (directory/(name+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(bundle[name])
    (directory/'settings.json').write_text(json.dumps(bundle['settings'],ensure_ascii=False,indent=2),encoding='utf-8')


def cases():
    base=sample()
    yield '30店_300SKU行',base
    yield '12店_120SKU行',sample(12)
    yield '40店_400SKU行',sample(40)
    traffic=deepcopy(base)
    for leg in traffic['travel']: leg['travel_minutes']=round(leg['travel_minutes']*1.7,3)
    yield '静态行驶时间增加70%',traffic
    capacity=deepcopy(base)
    for v in capacity['vehicles']:v['max_weight_kg']=200;v['max_volume_m3']=1.2
    yield '运力不足_应报告未安排',capacity
    service=deepcopy(base)
    for row in service['orders']:row['service_minutes']=25
    yield '卸货耗时25分钟',service
    restricted=deepcopy(base)
    for row in restricted['orders']: row['allowed_vehicles']='TRUCK_1'
    yield '仅一辆车允许到店',restricted
    late=deepcopy(base)
    for v in late['vehicles']: v.update(shift_start='13:30',shift_end='14:00')
    yield '短班次_保留失败结果',late


def run():
    results=[]
    with TemporaryDirectory() as tmp:
        for name,bundle in cases():
            write_bundle(bundle,tmp);data=pilot.load_bundle(tmp);r=pilot.compare(data)
            results.append(dict(case=name,stops=len(data['orders']),sku_rows=len(bundle['orders']),
                source_sha256=data['source_sha256'],baseline=r['baseline']['metrics'],optimized=r['optimized']['metrics'],
                baseline_ms=r['baseline']['solve_ms'],optimized_ms=r['optimized']['solve_ms'],audit=r['audit'],
                comparison_scope=r['comparison_scope'],
                rejection_codes=sorted({o['code'] for o in r['optimized']['unserved']})))
        # Import failures must not silently become a different valid problem.
        refusals=[]
        for name,mutate in [('缺失道路方向',lambda b:b['travel'].pop()),
                            ('重复SKU行',lambda b:b['orders'].append(deepcopy(b['orders'][0]))),
                            ('未知可用车辆',lambda b:b['orders'][0].update(allowed_vehicles='UNKNOWN'))]:
            b=deepcopy(sample(3));mutate(b);write_bundle(b,tmp)
            try:pilot.load_bundle(tmp)
            except ValueError as e:refusals.append(dict(case=name,rejected=True,reason=str(e)))
            else:raise AssertionError(name+'没有拒绝')
    return dict(simulated=True,generator='pilot_benchmark.sample；固定种子20261011',
        purpose='迁移入口与约束核验；不是随机业务抽样、真实收益或动态调度评测',
        cases=results,refusals=refusals)


if __name__=='__main__':
    write_bundle(sample(),ROOT/'examples/pilot-30')
    result=run();out=ROOT/'outputs/pilot-benchmark';out.mkdir(parents=True,exist_ok=True)
    (out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 离线迁移入口：构造场景验证','',result['purpose'],'',
        '| 场景 | 已安排：追加→搜索 | 迟到分钟：追加→搜索 | 费用：追加→搜索 | 搜索毫秒 |',
        '|---|---:|---:|---:|---:|']
    for c in result['cases']:
        a,b=c['baseline'],c['optimized']
        lines.append(f"| {c['case']} | {a['served']}→{b['served']} | {a['late_minutes']}→{b['late_minutes']} | {a['cost']}→{b['cost']} | {c['optimized_ms']} |")
    lines+=['','全部输出通过独立算术复核；3种坏输入均拒绝。',
        '费用可能上升，运力不足、短班次可能保留未安排。简单追加不代表企业人工排程。',
        '配送集合不同的案例不得将费用或迟到差额表述成等任务节省率。',
        '旧连续异常模块未接通；本轮为出发前的离线排程入口。']
    (out/'结果.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('\n'.join(lines))
