"""No-key CSV checkpoint: 3/30 stores, two urgent lots, window change, full receipts.

Fixed constructed broad-window input, temporary SQLite only. No AI/network.
The input messages/drafts are fixtures; does not establish AI scale accuracy.
"""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import json
from time import perf_counter
from contribution_benchmark import prepare, compare_case
from continuous import DispatchStore
import csv_ai_events
import matrix_dispatch as md
from verify_matrix_dispatch import audit

ROOT=Path(__file__).resolve().parent


def stamp(t):return f'{int(t)//60:02d}:{int(t)%60:02d}'


def run(stores):
    started=perf_counter();log=[]
    with TemporaryDirectory() as tmp:
        store,scene=prepare(tmp,stores,20261011)
        initial_orders=len(scene['live_plan']['data']['orders'])
        def act(action,**body):
            nonlocal scene
            before=deepcopy(scene['receipts'])
            scene=store.act(action,dict(id=scene['id'],revision=scene['revision'],**body))
            check=audit(scene)
            assert scene['receipts'][:len(before)]==before
            assert scene==DispatchStore(store.path).read()
            log.append(dict(action=action,body=body,clock=scene['clock'],revision=scene['revision'],audit=check))
        for i in (1,2):
            # Round up, explicitly advance the simulation clock to that minute.
            minute=int(scene['clock'])+(scene['clock']%1>0)
            act('advance',time=minute)
            draft=dict(type='urgent_order',time=stamp(minute),store=f'STORE_{i+1:03}',
                       pickup_location='warehouse',due='15:00',close='18:00',
                       lines=[dict(sku=f'LOT_{i}',quantity=2+i,unit_weight_kg=7,unit_volume_m3=.03)])
            validated=csv_ai_events.validate_draft(draft,scene);assert validated['ready']
            event={k:v for k,v in validated['event'].items() if k!='type'}
            act('event',kind='urgent',event=event);assert scene['cargo'].get(f'E{i:02}') is None
            act('adopt',candidate=scene['candidate']);assert scene['cargo'][f'E{i:02}']=='warehouse'
        # Change the second independent urgent order, with a fair comparator.
        minute=int(scene['clock'])
        event=dict(time=minute,store='E02',open=minute+60,due=900,close=1080)
        comparison=compare_case(f'{stores}店_双紧急单后E02改窗',scene,'window',event)
        act('event',kind='window',event=event);act('adopt',candidate=scene['candidate'])
        completed_before=deepcopy(scene['receipts'])
        for _ in range((stores+4)*4):
            choices=[]
            for vid,q in scene['states'].items():
                if q['remaining']:
                    stop=scene['live_plan']['routes'][vid]['stops'][len(q['completed'])]
                    choices.append((max(scene['clock'],stop['arrival']),vid,stop))
            if not choices:break
            time,vid,stop=min(choices,key=lambda x:(x[0],x[1]));oid=stop['id']
            if md.is_pickup(oid):act('advance',time=max(time,stop['finish']))
            else:
                order=next(o for o in scene['live_plan']['data']['orders'] if o['id']==oid)
                for receipt,t in [('arrived',time),('unloading',time),('signed',time+order['service_minutes'])]:
                    act('receipt',vehicle=vid,order=oid,receipt=receipt,time=t,source='manual_simulation')
        assert set(scene['cargo'].values())=={'delivered'}
        assert len(scene['receipts'])==(stores+2)*3
        assert scene['receipts'][:len(completed_before)]==completed_before
        act('advance',time=max(scene['clock'],max(r['return_at'] for r in scene['live_plan']['routes'].values())))
        assert all(q['node']=='WH' for q in scene['states'].values())
        assert not scene['live_plan'].get('needs_replan')
        return dict(stores=stores,sku_rows=stores*10,orders=initial_orders+2,receipts=len(scene['receipts']),
                    elapsed_ms=round((perf_counter()-started)*1000,2),final_audit=audit(scene),all_returned=True,
                    initial_plan=scene['versions'][0]['plan'],comparison=comparison,
                    final_scene=scene,steps=log)


if __name__=='__main__':
    results=[run(n) for n in (3,30)]
    report=dict(synthetic=True,ai_transport='not called; fixed draft validation only',
                limits='两个定向宽窗口构造流程，不代表普遍规模能力、真实道路、人工耗时或企业价值；耗时是本次整段复现，不是单次求解延迟。',cases=results)
    output=ROOT/'evidence/csv-checkpoint-replay.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print([(r['stores'],r['orders'],r['receipts'],r['all_returned'],r['elapsed_ms']) for r in results])
