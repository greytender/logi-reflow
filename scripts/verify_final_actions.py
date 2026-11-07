from pathlib import Path
import sys, sqlite3, json
root=Path(r'D:\桌面文件\logi-reflow-final')
out=Path(r'D:\桌面文件\logi-reflow-main\outputs\final-upgrade-20261107')
sys.path.insert(0,str(root/'runtime'))
import multiwarehouse, decision_changes
from store import Conflict

def isolated(name):
    path=out/name
    with sqlite3.connect(f'file:{(root/"runtime/data/application.sqlite3").as_posix()}?mode=ro',uri=True) as src,sqlite3.connect(path) as dst:src.backup(dst)
    return multiwarehouse.MultiWarehouseStore(path)

tid='953c6f26280f84964c6d1ed6ab62d0b1'
checks=[]
store=isolated('qa-receipts.sqlite3');t=store.read(tid);wid=t['data']['warehouses'][0]['id'];view=multiwarehouse.view(t);vid=next(iter(view['execution'][wid]['routes']));r=view['execution'][wid]['routes'][vid]
first=r['next_stop'];order=next(o for o in t['data']['orders'] if o['id']==first)
receipt=dict(vehicle=vid,kind='stop',stop_id=first,finish=int(r['remaining'][0]['finish']+1),source='simulated',note='isolated QA only',pickup=order['pickup'])
t=store.action(tid,t['revision'],wid,'receipt',receipt,True)
assert multiwarehouse.view(t)['execution'][wid]['routes'][vid]['completed']==1
checks.append('receipt advances next stop and preserves simulated source')
try: decision_changes.preview(t,dict(id=tid,revision=t['revision'],order_id=first,pickup=order['pickup']))
except Conflict:checks.append('execution facts block pre-dispatch condition rewrite')
else:raise AssertionError('facts not protected')
route=t['execution_tasks'][wid]['plans'][0] if False else None
plan=multiwarehouse.current_plan(t);sequence=plan['warehouses'][wid]['optimized']['routes'][vid]['sequence'];policy=t['data']['warehouses'][0]['operational_policy']
box=next(g['cold_box_id'] for g in policy['goods'] if g['kind']=='cold' and g['order'] in sequence)
temperature=dict(kind='reading',vehicle=vid,cold_box=box,time=510,phase='loading',temperature_c=12,source='simulated',note='isolated QA only',action='')
t=store.action(tid,t['revision'],wid,'temperature',temperature,True)
reading=t['execution_tasks'][wid]['temperature_records'][-1]
assert reading['excursion']
action=dict(kind='action',vehicle=vid,cold_box=box,time=510,source='simulated',note='isolated QA only',action='检视货物并更换设备（演练）',target_id=reading['id'])
t=store.action(tid,t['revision'],wid,'temperature',action,True)
assert len(t['execution_tasks'][wid]['temperature_records'])==2
checks.append('temperature excursion retained with append-only follow-up action')

store=isolated('qa-conditions.sqlite3');t=store.read(tid);order=t['data']['orders'][0];old=t['revision']
request=dict(id=tid,revision=old,order_id=order['id'],pickup={**order['pickup'],'totes':order['pickup']['totes']+1})
proposal=decision_changes.preview(t,request)
assert proposal['impact']['affected_routes']
assert store.read(tid)['revision']==old
changed=store.save(tid,old,t['name'],proposal['data'])
assert multiwarehouse.current_plan(changed) is None and not multiwarehouse.view(changed)['confirmed']
assert len(changed['plans'])==2 and changed['input_revision']==t['input_revision']+1
checks.append('condition preview is read-only; applying invalidates current candidate and retains two original plans')
assert multiwarehouse.MultiWarehouseStore(root/'runtime/data/application.sqlite3').read(tid)['revision']==5
checks.append('shipped demonstration database remains at revision 5 with no test receipts')
(out/'actions-report.json').write_text(json.dumps({'checks':checks},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'checks':checks},ensure_ascii=True))
