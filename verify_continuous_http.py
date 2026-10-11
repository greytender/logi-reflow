"""Read the browser-created live scene; rejected writes must not change it."""
import json
from pathlib import Path
import urllib.request
import urllib.error
import sqlite3
from contextlib import closing

ROOT=Path(__file__).resolve().parent
def request(path,body=None):
    req=urllib.request.Request('http://127.0.0.1:8766'+path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req) as r:return r.status,json.load(r)
    except urllib.error.HTTPError as e:return e.code,json.load(e)

if __name__=='__main__':
    _,b=request('/api/dispatch');scene=b['scene'];assert scene and scene['active']==3
    active=next(v['plan'] for v in scene['versions'] if v['id']==3)
    assert len(active['data']['orders'])==13 and scene['cargo']['E01']=='warehouse'
    assert active['metrics']['unserved']==0
    assert scene['versions'][2]['base']==2
    common=dict(id=scene['id'],revision=scene['revision']);checks={}
    for label,path,body,status in [
        ('stale_page','advance',{**common,'revision':0,'time':506},409),
        ('no_candidate','adopt',{**common,'candidate':2},409),
        ('time_backwards','advance',{**common,'time':505},400),
        ('delivered_history','event',{**common,'kind':'window','event':dict(time=506,store='S05',open=555,due=570,close=690)},400),
        ('second_urgent_scope','event',{**common,'kind':'urgent','event':dict(time=506,store='S07',quantity=10,due=540,close=690)},400),
    ]:
        code,result=request('/api/dispatch/'+path,body);assert code==status,(label,code,result)
        checks[label]=dict(status=code,error=result['error'])
    assert request('/api/dispatch')[1]['scene']==scene
    checks['rejected_requests_keep_scene']=True
    assert request('/api/session')[0]==200
    checks['legacy_endpoint_readable']=True
    backup=sorted((ROOT/'data/backups').glob('sessions-before-continuous-*.sqlite3'))[-1]
    with closing(sqlite3.connect(backup)) as old, closing(sqlite3.connect(ROOT/'data/sessions.sqlite3')) as live:
        tables=[r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        unchanged={t:old.execute('SELECT * FROM "'+t+'" ORDER BY rowid').fetchall()==live.execute('SELECT * FROM "'+t+'" ORDER BY rowid').fetchall() for t in tables}
        assert all(unchanged.values());checks['legacy_tables_unchanged']=unchanged
    (ROOT/'evidence/continuous-browser-scene.json').write_text(json.dumps(scene,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'evidence/continuous-http-verification.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(checks,ensure_ascii=False,indent=2))
