"""Independent arithmetic audit of the live, read-only comparison endpoint."""
from math import hypot
from copy import deepcopy
from pathlib import Path
import json
import urllib.request

ROOT=Path(__file__).resolve().parent
def distance(a,b): return hypot(a[0]-b[0],a[1]-b[1])

def audit(plan,states,parent):
    orders={o['id']:o for o in plan['data']['orders']}; delivered=[];late=[];total=0;changes=[]
    original={}
    for vid,r in parent['routes'].items():
        prev=None
        for s in r['stops']:
            if s['id']!='W':original[s['id']]=(vid,s['arrival'],prev)
            prev=s['id']
    for v in plan['data']['vehicles']:
        vid=v['id'];state=states[vid];r=plan['routes'][vid];n=len(state['completed'])
        assert r['stops'][:n]==state['completed'],'已完成节点被修改'
        assert r['anchor']==state,'起点被修改'
        cargo=set(state['cargo']);pos=state['point'];t=state['available'];km=state['traveled'];loaded=set()
        def capacity():
            w=sum(orders[x]['quantity']*orders[x]['unit_weight_kg'] for x in cargo)
            vol=sum(orders[x]['quantity']*orders[x]['unit_volume_m3'] for x in cargo)
            assert w<=v['max_weight_kg']+1e-7 and vol<=v['max_volume_m3']+1e-7,'容量违法'
        capacity()
        for stop in r['stops'][n:]:
            oid=stop['id'];dest=[0,0] if oid=='W' else orders[oid]['point']
            assert stop['point']==dest
            d=distance(pos,dest);km+=d;arrival=t+d/.5
            if oid=='W':
                assert stop['load_ids']==['E01'] and 'E01' not in cargo and 'E01' not in loaded
                old=next((s for s in parent['routes'][vid]['stops'] if s['id']=='W'),None)
                if old and old['arrival']<=state['available']<old['finish']:
                    assert stop is r['stops'][n] and distance(state['point'],[0,0])<1e-7,'已开始装货被移动'
                    arrival=old['arrival'];finish=old['finish']
                else: finish=arrival+8
                cargo.update(stop['load_ids']);loaded.update(stop['load_ids'])
            else:
                assert oid in cargo,'先送后取或换车货物'
                cargo.remove(oid);arrival=max(arrival,orders[oid]['open']);finish=arrival+5
                assert arrival<=orders[oid]['close']+1e-3,'超过硬截止'
            assert abs(stop['arrival']-arrival)<1e-3 and abs(stop['finish']-finish)<1e-3,'行驶或服务时间不一致'
            capacity();pos=dest;t=finish
        assert cargo==set(r.get('retained_ids',[])),'遗漏未交付货物'
        km+=distance(pos,[0,0]);total+=v['fixed_cost']+km*v['cost_per_km']
        assert abs(r['return_at']-(t+distance(pos,[0,0])/.5))<1e-3
        assert abs(r['distance']-km)<1e-3
        prev=None;done={s['id'] for s in state['completed']}
        for s in r['stops']:
            oid=s['id']
            if oid!='W':
                delivered.append(oid);late.append(max(0,s['arrival']-orders[oid]['due']))
                old=original.get(oid)
                if old and oid not in done and (old[0]!=vid or old[2]!=prev or abs(old[1]-s['arrival'])>1):changes.append(oid)
            prev=oid
    assert len(delivered)==len(set(delivered)),'重复交付'
    missing=sorted(set(orders)-set(delivered));changes+=missing
    m=plan['metrics'];assert m['served']==len(delivered) and m['unserved']==len(missing)
    assert plan['unserved_ids']==missing
    assert m['late_orders']==sum(x>1e-6 for x in late) and m['late_minutes']==round(sum(late),2)
    assert m['cost']==round(total,2),'费用汇总错误'
    assert m['changed_ids']==sorted(set(changes)) and m['changed_orders']==len(set(changes))
    return dict(history_fixed=True,cargo_conserved=True,capacity_checked=True,
        pickup_before_delivery=True,windows_checked=True,time_checked=True,cost_checked=True,
        changed_orders_checked=True,metrics=deepcopy(m))

def get(path):
    with urllib.request.urlopen('http://127.0.0.1:8766'+path,timeout=20) as r:return json.load(r)

def verify():
    before=get('/api/dispatch')['scene'];r=get('/api/dispatch/comparison');assert r['available']
    parent=next(v['plan'] for v in before['versions'] if v['id']==r['base_version'])
    checks={k:audit(r[k],r['states'],parent) for k in ('baseline','recovered')}
    for vid,state in r['states'].items():
        expected=state['remaining'];rr=r['baseline']['routes'][vid]
        assert rr['sequence'][len(state['completed']):]==expected,'对照改变了原顺序'
    assert get('/api/dispatch')['scene']==before,'只读对照修改了场景'
    checks['read_only_scene_unchanged']=True
    r['audit']=checks
    (ROOT/'evidence/continuous-comparison.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
    b=r['baseline']['metrics'];c=r['recovered']['metrics']
    delta=dict(late_minutes_reduced=round(b['late_minutes']-c['late_minutes'],2),
        extra_cost=round(c['cost']-b['cost'],2),changed_orders_difference=c['changed_orders']-b['changed_orders'])
    (ROOT/'evidence/continuous-comparison-audit.json').write_text(json.dumps(dict(checks=checks,delta=delta),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(baseline=b,recovered=c,delta=delta,read_only=True),ensure_ascii=False))

if __name__=='__main__':verify()
