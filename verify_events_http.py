"""Read-only legacy check and independent HTTP trials; saves simulated evidence.

Run server.py first. No AI key or external network call is required.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import urllib.request
import urllib.error

ROOT=Path(__file__).resolve().parent
BASE='http://127.0.0.1:8766'


def request(path,body=None):
    req=urllib.request.Request(BASE+path,
        json.dumps(body).encode() if body is not None else None,
        {'Content-Type':'application/json'} if body is not None else {})
    try:
        with urllib.request.urlopen(req,timeout=20) as response:return response.status,json.load(response)
    except urllib.error.HTTPError as e:return e.code,json.load(e)


def save(name,result):
    (ROOT/'evidence'/name).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    old_hash=hashlib.sha256((ROOT/'data/sessions.sqlite3').read_bytes()).hexdigest()
    status,initial=request('/api/normal');assert status==200
    data=initial['data'];e=dict(time=505,store='S07',quantity=10,due=540,close=690)
    for name,changes in [('default',{}),('early',dict(time=485,due=530,close=540)),
                         ('oversize',dict(quantity=100)),('deadline',dict(due=506,close=506))]:
        status,r=request('/api/urgent',dict(data=data,event={**e,**changes}));assert status==200
        save('urgent-'+name+'.json',r)
        if name in ('oversize','deadline'):
            assert r['recovered']['unserved_ids']==['E01']
            assert r['recovered']['metrics']['changed_orders']==0
        elif name=='early':assert r['recovered']['metrics']['served']==13 and r['baseline']['metrics']['served']==12
    d=deepcopy(data);d['orders'][6]['quantity']=40
    status,r=request('/api/urgent',dict(data=d,event=e));assert status==200;save('urgent-40.json',r)
    w=dict(time=505,store='S08',open=555,due=570,close=690)
    for name,changes in [('default',{}),('deadline',dict(open=480,due=505,close=505))]:
        status,r=request('/api/window',dict(data=data,event={**w,**changes}));assert status==200;save('window-'+name+'.json',r)
        if name=='default':assert r['recovered']['metrics']['late_orders']==0 and r['baseline']['metrics']['late_orders']==1
        else:assert r['recovered']['routes']['V1']['retained_ids']==['S08']
    assert request('/api/window',dict(data=data,event={**w,'store':'S05'}))[0]==400
    assert request('/api/urgent',dict(data=data,event={**e,'time':True}))[0]==400
    assert request('/api/urgent',dict(data=data,event={**e,'routes':[]}))[0]==400
    d['orders'][6]['quantity']=100
    assert request('/api/urgent',dict(data=d,event=e))[0]==400
    status,r=request('/api/failure',dict(data=data,message='V2 在 08:25 故障'));assert status==200
    assert r['recovered']['metrics']['cost']==268.81
    status,ai=request('/api/ai/status');assert status==200
    save('ai-configuration.json',ai)
    if not ai['configured']:
        status,r=request('/api/interpret',dict(data=data,message='V2故障'))
        assert status==503 and r['ready'] is False;save('ai-no-key.json',r)
    assert request('/api/session')[0]==200
    new_hash=hashlib.sha256((ROOT/'data/sessions.sqlite3').read_bytes()).hexdigest()
    assert new_hash==old_hash
    save('events-http-verification.json',dict(passed=True,legacy_sqlite_unchanged=True,
        legacy_sha256=new_hash,live_ai_called=False,live_ai_verified=False,
        scenarios=['urgent-default','urgent-early','urgent-oversize','urgent-deadline','urgent-40','window-default','window-deadline'],
        invalid_cases=['already-delivered','boolean-time','extra-fields','incomplete-normal-plan']))
    print('HTTP checks passed; legacy SQLite unchanged; no live AI call.')


if __name__=='__main__':main()
