"""Constructed loading regressions, not paper benchmarks or enterprise data."""
from copy import deepcopy
import csv,io,json
from pathlib import Path
from time import perf_counter
import loading


def text(rows):
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows);return stream.getvalue()


def stack(fragile_base=False):
    orders=[dict(stop_id='FIRST',sku='TOP',quantity=1,unit_weight_kg=5,unit_volume_m3=.075,
        open='08:00',due='09:00',close='11:00',service_minutes=5,allowed_vehicles='V1'),
        dict(stop_id='LAST',sku='BASE',quantity=1,unit_weight_kg=15,unit_volume_m3=.075,
        open='08:00',due='09:00',close='11:00',service_minutes=5,allowed_vehicles='V1'),
        dict(stop_id='LAST',sku='MID',quantity=1,unit_weight_kg=10,unit_volume_m3=.075,
        open='08:00',due='09:00',close='11:00',service_minutes=5,allowed_vehicles='V1')]
    nodes=['WH','FIRST','LAST'];roads=[dict(from_id=a,to_id=b,distance_km=1,travel_minutes=5) for a in nodes for b in nodes if a!=b]
    files={'orders.csv':text(orders),'vehicles.csv':text([dict(vehicle_id='V1',max_weight_kg=100,max_volume_m3=.225,fixed_cost=30,cost_per_km=2,shift_start='08:00',shift_end='12:00')]),
        'travel.csv':text(roads),'settings.json':json.dumps(dict(depot_id='WH',data_kind='simulated',travel_source='constructed roads',window_rule='finish'))}
    raw=dict(schema='loading_v1',data_kind='simulated',vehicles=[dict(id='V1',length_cm=50,width_cm=50,height_cm=90)],
        items=[dict(order=x['stop_id'],sku=x['sku'],length_cm=50,width_cm=50,height_cm=30,rotate_base=False,
                    fragile=x['sku']=='TOP' or fragile_base,max_top_load_kg=0 if x['sku']=='TOP' or fragile_base else 30) for x in orders])
    return dict(csv_files=files,loading_profile=raw)


def thirty():
    base=stack();nodes=['WH']+[f'S{i:02d}' for i in range(30)];orders=[]
    for oid in nodes[1:]:orders.append(dict(stop_id=oid,sku='ONE',quantity=1,unit_weight_kg=1,unit_volume_m3=.001,open='08:00',due='15:00',close='16:00',service_minutes=1,allowed_vehicles='V1'))
    base['csv_files']['orders.csv']=text(orders)
    base['csv_files']['vehicles.csv']=text([dict(vehicle_id='V1',max_weight_kg=100,max_volume_m3=.5,fixed_cost=30,cost_per_km=2,shift_start='08:00',shift_end='17:00')])
    base['csv_files']['travel.csv']=text([dict(from_id=a,to_id=b,distance_km=abs(i-j),travel_minutes=abs(i-j)*2) for i,a in enumerate(nodes) for j,b in enumerate(nodes) if a!=b])
    base['loading_profile']['vehicles']=[dict(id='V1',length_cm=100,width_cm=100,height_cm=100)]
    base['loading_profile']['items']=[dict(order=o['stop_id'],sku='ONE',length_cm=10,width_cm=10,height_cm=10,rotate_base=False,fragile=False,max_top_load_kg=20) for o in orders]
    return base


def run():
    records=[];cases=[('volume_vs_geometry',loading.sample(),2),('stack_supported',stack(),2),('fragile_no_stack',stack(True),1),('thirty_one_box_each',thirty(),30)]
    for name,payload,expected in cases:
        start=perf_counter();r=loading.solve(payload['csv_files'],payload['loading_profile'])
        assert r['optimized']['metrics']['served']==expected,(name,r['optimized']['metrics'])
        assert r['independent_route_audit']['passed']
        assert all(x['loading']['audit']['passed'] and x['loading']['instruction_audit']['passed'] for x in r['optimized']['routes'].values())
        records.append(dict(case=name,data_kind='constructed',expected_served=expected,complete=r['complete'],
            metrics=r['optimized']['metrics'],baseline_metrics=r['baseline']['metrics'],baseline_loading={v:x['status'] for v,x in r['baseline_loading'].items()},
            same_served_orders=r['same_served_orders'],elapsed_ms=round((perf_counter()-start)*1000,2),work=r['work'],independent_audits=True))
        if name=='stack_supported':Path('outputs/loading-stack-result.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
    result=dict(scope='4 constructed cases; not published instances, stochastic performance evidence, or enterprise effect',cases=records)
    Path('evidence/loading-benchmark.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(records,ensure_ascii=False))


if __name__=='__main__':run()
