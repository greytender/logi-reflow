"""Small counterexample and manual execution replay, constructed data only."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import continuous
import fixed_loading
import loading_benchmark as lb
import matrix_dispatch as md


def three():
    nodes=['WH','A','B','C']
    orders=[dict(stop_id=oid,sku='BOX',quantity=1,unit_weight_kg=5,unit_volume_m3=.125,
                 open='08:00',due=due,close=close,service_minutes=5,allowed_vehicles='V1')
            for oid,due,close in zip(nodes[1:],['08:10','08:20','09:30'],['08:10','08:20','11:30'])]
    return dict(csv_files={'orders.csv':lb.text(orders),
        'vehicles.csv':lb.text([dict(vehicle_id='V1',max_weight_kg=100,max_volume_m3=.375,
            fixed_cost=30,cost_per_km=2,shift_start='08:00',shift_end='12:00')]),
        'travel.csv':lb.text([dict(from_id=a,to_id=b,distance_km=1,travel_minutes=5)
                             for a in nodes for b in nodes if a!=b]),
        'settings.json':json.dumps(dict(depot_id='WH',data_kind='simulated',travel_source='constructed roads',window_rule='finish'))},
        loading_profile=dict(schema='loading_v1',data_kind='simulated',
            vehicles=[dict(id='V1',length_cm=150,width_cm=50,height_cm=50)],
            items=[dict(order=oid,sku='BOX',length_cm=50,width_cm=50,height_cm=50,
                        rotate_base=False,fragile=False,max_top_load_kg=0) for oid in nodes[1:]]))


def run():
    payload=three();example=Path('examples/fixed-loading-three');example.mkdir(exist_ok=True)
    for name,content in payload['csv_files'].items():(example/name).write_text(content,encoding='utf-8')
    (example/'loading.json').write_text(json.dumps(payload['loading_profile'],ensure_ascii=False,indent=2),encoding='utf-8')
    with TemporaryDirectory() as tmp:
        store=continuous.DispatchStore(Path(tmp)/'test.db');scene=store.act('start',payload)
        initial=deepcopy(scene['live_plan']);frozen=deepcopy(initial['fixed_loading'])
        event=dict(time=480,store='B',open=600,due=620,close=690)
        free=deepcopy(initial);free.pop('fixed_loading')
        for r in free['routes'].values():r.pop('fixed_loading')
        free_plan,_=continuous.calculate(free,event,'window',480)
        def act(action,**body):
            nonlocal scene
            scene=store.act(action,dict(id=scene['id'],revision=scene['revision'],**body))
            assert scene['live_plan']['fixed_loading']==frozen
        act('event',kind='window',event=event)
        candidate=deepcopy(scene['versions'][-1]['plan'])
        assert free_plan['routes']['V1']['sequence']==['A','C','B']
        assert candidate['routes']['V1']['sequence']==['A','B','C']
        rejected=fixed_loading.check(frozen,'V1',['A','B','C'],[],['A','C','B'])
        assert not rejected['passed']
        comparison=store.compare()
        act('adopt',candidate=scene['candidate'])
        before=store.read()
        try:act('event',kind='urgent',event={'time':480})
        except ValueError as exc:urgent_error=str(exc)
        else:raise AssertionError('Urgent cargo was added without insertion verification')
        assert store.read()==before
        for oid in ('A','B','C'):
            stop=next(s for s in scene['live_plan']['routes']['V1']['stops'] if s['id']==oid)
            t=max(scene['clock'],stop['arrival'])
            for kind,tm in [('arrived',t),('unloading',t),('signed',t+5)]:
                act('receipt',vehicle='V1',order=oid,receipt=kind,time=tm,source='manual_simulation')
            assert scene['cargo'][oid]=='delivered'
        act('advance',time=800)
        assert scene['states']['V1']['node']=='WH'
        assert scene==continuous.DispatchStore(store.path).read()
        result=dict(data_kind='constructed',initial_sequence=initial['routes']['V1']['sequence'],
                    unconstrained_sequence=free_plan['routes']['V1']['sequence'],
                    constrained_sequence=candidate['routes']['V1']['sequence'],
                    unconstrained_metrics=free_plan['metrics'],constrained_metrics=candidate['metrics'],
                    fixed_check=candidate['routes']['V1']['fixed_loading_check'],rejected_check=rejected,
                    same_state_comparison=comparison,manifest=frozen,urgent_rejected=urgent_error,
                    receipts=len(scene['receipts']),returned=True,restart_equal=True,
                    all_coordinates_unchanged=True,final_scene=scene)
        Path('evidence/fixed-loading-benchmark.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:result[k] for k in ('initial_sequence','unconstrained_sequence','constrained_sequence','unconstrained_metrics','constrained_metrics','receipts','returned','all_coordinates_unchanged')},ensure_ascii=False))

if __name__=='__main__':run()
