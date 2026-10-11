"""Audit browser-created current/archived scenes; only rejected writes are sent."""
from contextlib import closing
from pathlib import Path
import json
import sqlite3
from verify_continuous_http import request
from verify_comparison import audit

ROOT=Path(__file__).resolve().parent

def verify():
    code,result=request('/api/dispatch');scene=result['scene'];assert code==200
    assert scene['active']==3 and scene['candidate'] is None and scene['clock']==506
    assert scene['cargo']['E01']=='warehouse' and len(scene['cargo'])==13
    code,review=request('/api/dispatch/review');assert code==200 and not review['adoptable']
    assert review['frozen_delivery_count']==6
    code,compared=request('/api/dispatch/comparison');assert code==200
    parent=next(v['plan'] for v in scene['versions'] if v['id']==compared['base_version'])
    audits={m:audit(compared[m],compared['states'],parent) for m in ('baseline','recovered')}
    code,history=request('/api/dispatch/history');assert code==200 and history['total']>=2
    assert history['scenes'][0]['id']==scene['id'] and history['scenes'][0]['current']
    archived=[]
    for summary in history['scenes'][1:]:
        code,r=request('/api/dispatch/history/'+summary['id']);assert code==200 and r['read_only']
        archived.append(r['scene'])
    old=next(s for s in archived if sum(v['status']=='discarded' for v in s['versions'])>=2)
    assert old['active']==5 and old['candidate'] is None and old['clock']==507
    discarded=[v['id'] for v in old['versions'] if v['status']=='discarded']
    assert not any(e['version'] in discarded for e in old['execution'])
    assert all(v['executed_until'] is None for v in old['versions'] if v['id'] in discarded)
    checks={}
    for label,path,body,status in [
        ('archived_write','/api/dispatch/advance',dict(id=old['id'],revision=old['revision'],time=508),409),
        ('stale_discard','/api/dispatch/discard',dict(id=scene['id'],revision=0,candidate=2),409),
        ('no_candidate_discard','/api/dispatch/discard',dict(id=scene['id'],revision=scene['revision'],candidate=2),409),
        ('missing_history','/api/dispatch/history/missing',None,404),
    ]:
        actual,response=request(path,body);assert actual==status,(label,actual,response)
        checks[label]=actual
    assert request('/api/dispatch')[1]['scene']==scene
    assert request('/api/dispatch/history/'+old['id'])[1]['scene']==old
    backup=sorted((ROOT/'data/backups').glob('sessions-before-maturity-*.sqlite3'))[-1]
    with closing(sqlite3.connect(backup)) as before, closing(sqlite3.connect(ROOT/'data/sessions.sqlite3')) as after:
        names=[r[0] for r in before.execute("SELECT name FROM sqlite_master WHERE type='table' AND name<>'dispatch_scenes'")]
        legacy={n:before.execute('SELECT * FROM "'+n+'" ORDER BY rowid').fetchall()==after.execute('SELECT * FROM "'+n+'" ORDER BY rowid').fetchall() for n in names}
        assert all(legacy.values())
        for row in before.execute('SELECT body FROM dispatch_scenes'):
            saved=json.loads(row[0]);code,found=request('/api/dispatch/history/'+saved['id']);assert code==200
            assert all(v['plan']==found['scene']['versions'][i]['plan'] for i,v in enumerate(saved['versions']))
    result=dict(simulated=True,current_scene=scene['id'],revision=scene['revision'],active=scene['active'],clock=scene['clock'],
        current_orders=13,archive_count=history['total'],archived_scene=old['id'],discarded_versions=discarded,
        discarded_never_executed=True,reads_and_rejected_writes_unchanged=True,legacy_tables_unchanged=legacy,
        prior_plans_preserved=True,rejected_checks=checks,independent_comparison=audits,
        limits='单个网页流程和构造情境，不能证明生产可靠性或平均收益')
    (ROOT/'evidence/maturity-http-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return result

if __name__=='__main__':verify()
