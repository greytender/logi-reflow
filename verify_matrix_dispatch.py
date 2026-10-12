"""Read-only independent arithmetic audit for CSV dispatch, no AI calls.

Does not call snapshot/future/assemble or route evaluation. Validates facts,
ownership and the saved anchor-to-return prediction, including committed legs.
"""
import argparse
from collections import defaultdict
from pathlib import Path
import json
import urllib.request


def audit(scene):
    plan=scene['live_plan'];data=plan['data'];orders={o['id']:o for o in data['orders']}
    assert data['dispatch_model']=='directed_csv_v1'
    def pickup(oid): return oid=='W' or oid.startswith('warehouse:')
    def close(a,b): assert abs(a-b)<.001,(a,b)
    def totals(ids):
        return (sum(sum(x['quantity']*x['unit_weight_kg'] for x in orders[oid]['lines']) for oid in ids),
                sum(sum(x['quantity']*x['unit_volume_m3'] for x in orders[oid]['lines']) for oid in ids))
    for oid,o in orders.items():
        w,vol=totals([oid]);close(w,o['unit_weight_kg']);close(vol,o['unit_volume_m3'])
    records=defaultdict(list)
    for r in scene['receipts']:
        assert r['source']=='manual_simulation';records[r['order']].append(r)
    signed={};owners={};depot=data['settings']['depot_id']
    tasks=set();lots={};deliveries={}
    for vid,route in plan['routes'].items():
        ids=[s['id'] for s in route['stops']]
        assert route['sequence']==ids and len(ids)==len(set(ids))
        assert [s['id'] for s in route['anchor']['completed']]==ids[:len(route['anchor']['completed'])]
        for index,stop in enumerate(route['stops']):
            if not pickup(stop['id']):
                assert stop['id'] not in deliveries
                deliveries[stop['id']]=(vid,index);continue
            load=stop['load_ids'];task=stop.get('task_id','warehouse:'+','.join(load))
            assert stop['id']=='W' or stop['id']==task
            assert load and len(load)==len(set(load)) and task not in tasks
            tasks.add(task)
            for oid in load:
                assert oid in orders and orders[oid].get('store') and oid not in lots
                lots[oid]=(vid,index)
        q=scene['states'][vid];loading=q.get('loading')
        if loading:
            stop=route['stops'][len(q['completed'])];assert pickup(stop['id'])
            if 'task_id' in stop or 'task_id' in loading or 'load_ids' in loading:
                assert loading.get('task_id')==stop.get('task_id','warehouse:'+','.join(stop['load_ids']))
                assert loading.get('load_ids')==stop['load_ids']
            close(loading['finish']-loading['arrival'],8)
    for oid,(vid,index) in deliveries.items():
        if orders[oid].get('store'):
            assert oid in lots and lots[oid][0]==vid and lots[oid][1]<index
    for v in data['vehicles']:
        vid=v['id'];q=scene['states'][vid]
        for oid in q['cargo']:
            assert oid not in owners;owners[oid]=vid
        w,vol=totals(q['cargo']);close(w,q['weight_kg']);close(vol,q['volume_m3'])
        assert w<=v['max_weight_kg']+.001 and vol<=v['max_volume_m3']+.001
        for s in q['completed']:
            if pickup(s['id']):
                assert s['finish']>=s['arrival']+8-.001;continue
            oid=s['id'];assert oid not in signed;signed[oid]=s
            rr=records[oid];assert [x['receipt'] for x in rr]==['arrived','unloading','signed']
            assert all(x['vehicle']==vid for x in rr)
            close(rr[0]['time'],s['arrival']);close(rr[1]['time'],s['unloading_at']);close(rr[2]['time'],s['signed_at'])
            assert rr[0]['time']<=rr[1]['time']<=rr[2]['time']
            assert rr[2]['time']>=rr[1]['time']+orders[oid]['service_minutes']-.001
            assert oid not in owners
    for oid in orders:
        expected='delivered' if oid in signed else owners.get(oid,'warehouse')
        assert scene['cargo'][oid]==expected
        if oid not in signed:
            rr=records[oid];assert [x['receipt'] for x in rr] in ([],['arrived'],['arrived','unloading'])
        # Original goods remain with their initially assigned truck until signed.
        first=scene['versions'][0]['plan']
        initial=next((vid for vid,r in first['routes'].items() if oid in r['sequence']),None)
        if initial and oid not in signed: assert owners.get(oid)==initial
        if not initial and (oid in owners or oid in signed):
            truck=owners.get(oid) or records[oid][-1]['vehicle']
            assert any(pickup(s['id']) and oid in s['load_ids'] for s in scene['states'][truck]['completed'])
    # Actual signatures must survive every later adopted anchor unchanged.
    seen={}
    for version in scene['versions']:
        if version['status'] not in ('adopted','executed'):continue
        for route in version['plan']['routes'].values():
            for stop in route['anchor']['completed']:
                if pickup(stop['id']):continue
                if stop['id'] in seen: assert seen[stop['id']]==stop
                seen[stop['id']]=stop;assert signed[stop['id']]==stop
    total_cost=0;late=[]
    if not plan.get('needs_replan'):
        for v in data['vehicles']:
            r=plan['routes'][v['id']];q=r['anchor'];t=q['available'];km=q['traveled'];node=q['node'];cargo=set(q['cargo'])
            if len(q['completed'])==len(r['stops']) and node==depot and not q.get('transit'):
                t=v['shift_start'] if not q['completed'] and not cargo else q.get('returned_at',t)
            if q.get('transit'):
                moving=q['transit'];t+=moving['travel_minutes'];km+=moving['distance_km'];node=moving['to']
            def move(dest):
                nonlocal t,km,node
                if node!=dest:
                    road=data['legs'][node+'>'+dest];t+=road['travel_minutes'];km+=road['distance_km'];node=dest
            for s in r['stops'][len(q['completed']):]:
                oid=s['id'];move(depot if pickup(oid) else orders[oid].get('store',oid))
                if pickup(oid):
                    loading=q.get('loading');arrival=loading['arrival'] if loading else t
                    t=loading['finish'] if loading else t+8
                    assert not (cargo&set(s['load_ids']));cargo.update(s['load_ids'])
                    q=dict(q,loading=None)
                else:
                    o=orders[oid];assert oid in cargo and v['id'] in o['allowed_vehicles']
                    pending=q.get('pending');arrival=max(t,o['open'])
                    if pending and pending['id']==oid and 'arrived_at' in pending:
                        arrival=pending['arrived_at']
                        t=max(t,pending['unloading_at']+o['service_minutes']) if 'unloading_at' in pending else max(t,o['open'])+o['service_minutes']
                    else:t=arrival+o['service_minutes']
                    deadline=t if data['settings']['window_rule']=='finish' else arrival
                    assert deadline<=o['close']+.001;cargo.remove(oid)
                close(arrival,s['arrival']);close(t,s['finish'])
                w,vol=totals(cargo);assert w<=v['max_weight_kg']+.001 and vol<=v['max_volume_m3']+.001
            move(depot);assert t<=v['shift_end']+.001;close(t,r['return_at']);close(km,r['distance'])
            used=bool(r['stops'] or q['cargo'] or km)
            amount=(v['fixed_cost'] if used else 0)+km*v['cost_per_km'];close(amount,r['cost']);total_cost+=amount
            for s in r['stops']:
                if pickup(s['id']):continue
                deadline=s['finish'] if data['settings']['window_rule']=='finish' else s['arrival']
                late.append(max(0,deadline-orders[s['id']]['due']))
        close(round(total_cost,2),plan['metrics']['cost']);close(round(sum(late),2),plan['metrics']['late_minutes'])
    return dict(passed=True,scene_id=scene['id'],revision=scene['revision'],orders=len(orders),
        signatures=len(signed),receipts=len(scene['receipts']),prediction_checked=not plan.get('needs_replan',False),
        scope='SKU算术、唯一货物归属、签收历史、回仓装货、保存锚点后有向路线/窗口/班次/费用；不验证真实位置或企业效果')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='outputs/matrix-http-audit.json');args=parser.parse_args()
    with urllib.request.urlopen('http://127.0.0.1:8766/api/dispatch') as response: scene=json.load(response)['scene']
    result=audit(scene);out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
