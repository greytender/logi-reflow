"""Read-only audit of live simulated receipts; no AI, dispatch or DB writes."""
from collections import Counter
from contextlib import closing
from pathlib import Path
import json
import sqlite3
from urllib.request import urlopen


def audit(scene):
    assert scene['progress_mode']=='manual_receipts','需要新建分步回执场景'
    plan=scene['live_plan'];orders={o['id']:o for o in plan['data']['orders']}
    counts=Counter();signed={};vehicle_owners={}
    for vid,q in scene['states'].items():
        for oid in q['cargo']:counts[oid]+=1;vehicle_owners[oid]=vid
        for s in q['completed']:
            if s['id']=='W':
                assert s['finish']-s['arrival']>=8-1e-4
                continue
            oid=s['id'];counts[oid]+=1;signed[oid]=s
            assert s['progress_source']=='manual_simulation'
            assert s['arrival']<=s['unloading_at']<=s['signed_at']==s['finish']<=scene['clock']
            assert s['signed_at']-s['unloading_at']>=5
        for key,unit in [('weight_kg','unit_weight_kg'),('volume_m3','unit_volume_m3')]:
            assert abs(q[key]-sum(orders[x]['quantity']*orders[x][unit] for x in q['cargo']))<1e-6
        n=q.get('pending')
        if n:assert n['id'] in q['cargo'] and n['id']==q['remaining'][0]
    for oid,owner in scene['cargo'].items():
        if owner=='warehouse':assert counts[oid]==0
        elif owner=='delivered':assert oid in signed and counts[oid]==1
        else:assert vehicle_owners[oid]==owner and counts[oid]==1
    assert set(orders)==set(scene['cargo'])
    logs={}
    for r in scene['receipts']:
        assert r['source']=='manual_simulation';logs.setdefault(r['order'],[]).append(r)
    for oid,entries in logs.items():
        kinds=[r['receipt'] for r in entries]
        assert kinds==['arrived','unloading','signed'][:len(kinds)]
        assert len(kinds)<=3
        assert [r['time'] for r in entries]==sorted(r['time'] for r in entries)
        if kinds[-1]=='signed':
            assert oid in signed
            assert [signed[oid][k] for k in ('arrival','unloading_at','signed_at')]==[r['time'] for r in entries]
        else:assert oid not in signed
    assert set(signed)=={oid for oid,e in logs.items() if e[-1]['receipt']=='signed'}
    return dict(scene_id=scene['id'],revision=scene['revision'],clock=scene['clock'],
        signed_count=len(signed),receipts=scene['receipts'],cargo=scene['cargo'],states=scene['states'],
        checks=['回执步骤及时间','签收与回执一致','货物唯一归属及重量体积','未签收原货留车','仓库取货8分钟'],passed=True)


if __name__=='__main__':
    root=Path(__file__).resolve().parent
    with urlopen('http://127.0.0.1:8766/api/dispatch') as response:scene=json.load(response)['scene']
    result=audit(scene)
    backups=sorted((root/'data/backups').glob('before-receipts-*.sqlite3'))
    if backups:
        with closing(sqlite3.connect(backups[-1])) as c:old=c.execute('SELECT id,body FROM dispatch_scenes').fetchall()
        with closing(sqlite3.connect(root/'data/sessions.sqlite3')) as c:
            assert all(c.execute('SELECT body FROM dispatch_scenes WHERE id=?',(i,)).fetchone()==(b,) for i,b in old)
        result['unchanged_prior_scenes']=len(old)
    out=root/'outputs/receipt-http-audit.json';out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('scene_id','revision','signed_count','passed')},ensure_ascii=False))
    print('Read-only audit saved to outputs/receipt-http-audit.json')
