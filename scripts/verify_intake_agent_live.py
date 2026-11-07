"""One explicitly invoked DeepSeek request against an isolated local QA service, synthetic data only."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import time
from urllib.request import Request,urlopen
from uuid import uuid4


def verify(base,output):
    def request(path,body=None):
        req=Request(base+path,data=None if body is None else json.dumps(body).encode(),
            headers={'Content-Type':'application/json'})
        with urlopen(req,timeout=15) as response:return json.load(response)
    document=request('/api/intake/create',dict(sample=True,name='DeepSeek整理验收 · 构造订单'))['document']
    id=document['id'];review=request('/api/intake/review?id='+id)['review']
    job=request('/api/intake/agent-start',dict(id=id,revision=0,config=review['config'],mode='model',
        request_id=uuid4().hex,instructions='全部重量单位 kg；全部体积单位 m3。原表为整行总重量和整行总体积。'))['job']
    deadline=time.monotonic()+70
    while job['status']=='running' and time.monotonic()<deadline:
        time.sleep(.5);job=request('/api/intake/agent-job?id='+id+'&job_id='+job['id'])['job']
    if job['status']!='succeeded':raise RuntimeError(job.get('error') or 'Live request did not finish')
    saved=request('/api/intake/agent-apply',dict(id=id,job_id=job['id'],config=review['config'],reviewed=True,
        change_ids=[c['id'] for c in job['proposal']['changes']],include_store_ids=True))['review']
    assert saved['config']['rows']==[6,7,8]
    assert all(v is not None for v in saved['config']['units'].values()),saved['config']['units']
    assert saved['assessment']['rows'][1]['normalized']['cold_quantity'] is None
    assert request('/api/intake/document?id='+id)['document']==document
    record=dict(passed=True,synthetic_only=True,document_id=id,job_id=job['id'],model=job['model'],calls=job.get('calls'),
        changes=job['proposal']['changes'],questions=job['proposal']['questions'],review_revision=saved['revision'],
        original_sha256=document['sha256'],source_unchanged=True,all_rows_preserved=True,cold_blank_preserved=True,
        no_dispatch_created=True,base=base)
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:record[k] for k in ('passed','model','calls','review_revision','source_unchanged','all_rows_preserved')},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--base',default='http://127.0.0.1:18786');p.add_argument('--output',type=Path,default=Path('outputs/intake-agent-20261106/live.json'))
    args=p.parse_args();verify(args.base,args.output)
