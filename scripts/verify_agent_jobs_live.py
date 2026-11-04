"""Paid DeepSeek integration checks over constructed tasks; no enterprise data or keys in evidence."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import monotonic,sleep

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import agent_jobs
import deepseek_agent
import multiwarehouse as mw


def wait(registry,start):
    deadline=monotonic()+100
    while monotonic()<deadline:
        job=registry.get(start['job']['id'],start['job_token'])['job']
        if job['status'] not in agent_jobs.ACTIVE:
            if job['status']!='completed':raise RuntimeError(job.get('error',job['status']))
            return job
        sleep(.1)
    raise RuntimeError('本轮联调超时')


def main():
    if not deepseek_agent.credential():raise SystemExit('未配置密钥，未发起调用。')
    evidence=[]
    with TemporaryDirectory() as temp:
        store=mw.MultiWarehouseStore(Path(temp)/'jobs.db');registry=agent_jobs.JobRegistry(store)
        task=store.create('DeepSeek工作流模拟联调',mw.sample())
        cases=[('给这批模拟订单排车，再检查费用和回收风险。',None,'dispatch'),
               ('把当前选中车辆的全部配送任务顺序倒过来，生成待我确认的调整候选。',{},'adjust'),
               ('把 WH_1 的起步费改为160元，只给我规则草稿。',{'warehouse_id':'WH_1'},'rules')]
        for message,selection,required in cases:
            if required=='adjust':
                plan=mw.current_plan(task)
                vid=next(v for v,r in plan['warehouses']['WH_1']['optimized']['routes'].items() if r['sequence'])
                selection={'warehouse_id':'WH_1','vehicle_id':vid}
            start=registry.start(task['id'],task['revision'],message,planner=deepseek_agent.planner(),selection=selection)
            job=wait(registry,start);task=job['result']['task'];run=job['result']['agent_run']
            assert required in run['workflows'],run
            assert task['adopted_plan_id'] is None
            evidence.append(dict(request=message,selection=selection,workflows=run['workflows'],message=run['message'],
                events=[dict(type=e['type'],title=e['title']) for e in job['events'] if e['type']!='answer_delta'],
                streamed_chunks=sum(e['type']=='answer_delta' for e in job['events']),model_calls=run['model_calls'],
                result_keys=list(run['result']),confirmed=False))
        assert task['data']['warehouses'][0]['business']['tariff']['base']==140
    output=Path(__file__).resolve().parents[1]/'outputs/deepseek-agent-qa';output.mkdir(parents=True,exist_ok=True)
    (output/'jobs-live-verification.json').write_text(json.dumps(dict(data_kind='simulated',enterprise_data_sent=False,cases=evidence),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(passed=True,cases=[dict(workflows=e['workflows'],streamed_chunks=e['streamed_chunks'],
        tokens=sum(c['usage'].get('total_tokens',0) for c in e['model_calls'])) for e in evidence]),ensure_ascii=False))


if __name__=='__main__':main()
