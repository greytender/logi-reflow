"""Constructed integration checks: forecasts, repair, guardrails and witnesses.

Learning changes planning inputs only. A warning is not fulfilled recovery,
and model calibration scores are never reported as dispatch savings.
"""
from argparse import ArgumentParser
from copy import deepcopy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import adaptive_planning as ap
import application
import forecast_learning as learning
import multiwarehouse as mw
import operational_constraints as op
import pilot
from scripts.diagnose_high_load_operational import fixtures

DECISION_AT='2026-10-08T08:00:00+00:00'


def instant(day,hour=23):return f'2026-10-{day:02}T{hour:02}:00:00+00:00'


def record_days(store,data,days,bias):
    wid=data['settings']['depot_id']
    for day in days:
        for order in data['orders']:
            nominal=deepcopy(order['pickup'])
            actual={key:nominal[key]+bias[index] for index,key in enumerate(('cartons','totes'))}
            store.record(dict(id=f'{wid}-{order["id"]}-{day}',warehouse_id=wid,store_id=order['id'],
                data_kind='simulated',source='simulated',verified=True,reference='constructed integration validation',
                decision_at=instant(day,8),observed_at=instant(day,16),available_at=instant(day,17),
                nominal=nominal,actual=actual))


def activate_model(store,data,bias=(4,2)):
    record_days(store,data,range(1,5),bias)
    model=store.train(data['settings']['depot_id'],'simulated',instant(4))
    record_days(store,data,range(5,8),bias)
    validation=store.validate(model['id'],instant(5,0),instant(7))
    if not validation['passed']:raise AssertionError('constructed model failed chronological promotion gate')
    return store.activate(model['id']),validation


def summary(result):
    candidate=result.get('candidate') or result.get('alternative')
    return dict(status=result['status'],guarded_candidate_available=result['guarded_candidate_available'],
        witness_verified=result['witness_verified'],forecast_confirmation_required=result['forecast_confirmation_required'],
        source_sha256=result['source_sha256'],planning_sha256=result['planning_sha256'],
        forecast_model_id=result['forecast_model_id'],forecasts=result['forecasts'],
        candidate_cost=candidate['cost'] if candidate else None,
        selected_handling={vid:r.get('handling_variant','immediate') for vid,r in candidate['reports'].items()} if candidate else {},
        rehandle_moves=sum(r.get('time_adjustments',{}).get('rehandle_moves',0) for r in candidate['reports'].values()) if candidate else None)


def restricted_staging():
    case=next(c for c in fixtures() if c['id']=='ambient_pickup')
    data,policy=case['data'],deepcopy(case['policy']);policy['handling']['temporary_slots']=2
    vehicle=data['vehicles'][0];sequence=[o['id'] for o in data['orders']]
    route=pilot.evaluator(data)(sequence,vehicle,{o['id']:o for o in data['orders']})
    legacy=op.evaluate_route(data,route,vehicle,policy)
    batch=op.evaluate_route(data,route,vehicle,policy,handling_variant='stop_batch')
    adaptive=ap.improve_route(data,route,vehicle,policy,max_trials=2)
    return dict(legacy=legacy,batch=batch,adaptive=adaptive)


def learning_preemption(store):
    case=next(c for c in fixtures() if c['id']=='ambient_no_pickup')
    data,policy=case['data'],case['policy'];source=deepcopy(data)
    nominal=ap.plan_warehouse(data,policy,decision_at=DECISION_AT)
    model,validation=activate_model(store,data,bias=(0,10))
    learned=ap.plan_warehouse(data,policy,model=model,decision_at=DECISION_AT)
    actual=deepcopy(data)
    for order in actual['orders']:order['pickup']=dict(cartons=0,totes=10)
    actual=application.canonicalize(actual)
    assert nominal['candidate'] is not None
    old_actual=op.evaluate_plan(actual,nominal['candidate']['plan'],policy)
    assert source==data
    return dict(inputs=dict(nominal=data,actual=actual,policy=policy),model=model,validation=validation,
        nominal_result=summary(nominal),learned_result=summary(learned),
        nominal_plan_on_same_actual=dict(passed=old_actual['passed'],complete=old_actual['complete'],errors=old_actual['errors']),
        interpretation='名义方案在同一构造实际回收量下失败；学习预测提前交回人工，不等于已完成回收、节省费用或避免真实故障')


def build():
    high_load=[]
    for case in fixtures():
        before=deepcopy((case['data'],case['policy']))
        result=ap.plan_warehouse(case['data'],case['policy'],decision_at=DECISION_AT)
        assert before==(case['data'],case['policy'])
        assert result['guarded_candidate_available'] and result['witness_verified']
        current=result['reference'];chosen=result['candidate']
        high_load.append(dict(id=case['id'],summary=summary(result),
            legacy_moves=sum(r.get('time_adjustments',{}).get('rehandle_moves',0) for r in current['reports'].values()),
            selected_moves=sum(r.get('time_adjustments',{}).get('rehandle_moves',0) for r in chosen['reports'].values()),
            actual_load_by_vehicle=chosen['load_by_vehicle'],witness=result))
    slots=restricted_staging()
    assert slots['legacy']['passed'] and not slots['batch']['passed'] and slots['adaptive']['passed']
    assert slots['adaptive']['handling_variant']=='immediate'
    with TemporaryDirectory() as directory:
        store=learning.ForecastStore(Path(directory)/'wide.sqlite3');data,policy=op.sample_data();source=deepcopy(data)
        model,validation=activate_model(store,data)
        learned=ap.plan_warehouse(data,policy,model=model,decision_at=DECISION_AT)
        assert source==data and learned['source_sha256']!=learned['planning_sha256']
        serialized=json.loads(application.encode(learned));assert ap.revalidate(serialized)
        temporal_rejected=False
        try:ap.plan_warehouse(data,policy,model=model,decision_at=model['available_after'])
        except ValueError:temporal_rejected=True
        assert temporal_rejected
        record_days(store,data,range(8,10),bias=(8,4))
        monitor=store.monitor(data['settings']['depot_id'],'simulated',instant(8,0),instant(9))
        reviewed=ap.plan_warehouse(data,policy,model=store.active_model(data['settings']['depot_id'],'simulated'),decision_at=instant(10,8))
        assert monitor['coverage_warning'] and reviewed['forecast_confirmation_required']
        assert reviewed['candidate'] is None
        warning_store=learning.ForecastStore(Path(directory)/'overload.sqlite3')
        preemption=learning_preemption(warning_store)
    # Two independent warehouse candidates, each with an explicit policy.
    batch=mw.sample()
    for warehouse in batch['warehouses']:
        part=mw.split(batch)[warehouse['id']]
        warehouse['operational_policy']=op.sample_policy(part)
    multi=ap.plan_batch(batch,decision_at=DECISION_AT)
    assert len(multi['warehouses'])==2 and multi['all_guarded_candidates']
    return dict(schema='logireflow.adaptive-integration.v1',scope='constructed integration mechanisms; no enterprise data or dispatch',
        high_load=high_load,restricted_staging=slots,
        learning_wide_space=dict(model=model,validation=validation,summary=summary(learned),witness=learned,
            serialized_witness_revalidated=True,future_model_time_rejected=temporal_rejected),
        coverage_warning=dict(monitor=monitor,summary=summary(reviewed)),
        forecast_preemption=preemption,multiwarehouse=multi,
        boundaries=['仅构造数据和声明工时','模型数量校准不直接表示调度收益','无可行方案时提前告警仍需人工处置','不支持跨仓借车或同车多趟'])


if __name__=='__main__':
    parser=ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=ROOT/'outputs/algorithm-upgrade/integration.json')
    args=parser.parse_args();report=build();args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(dict(output=str(args.output),high_load=[dict(id=c['id'],legacy=c['legacy_moves'],selected=c['selected_moves']) for c in report['high_load']],
        learning=report['learning_wide_space']['summary'],preemption=report['forecast_preemption']['learned_result'],
        actual_nominal=report['forecast_preemption']['nominal_plan_on_same_actual'],multiwarehouse_guarded=report['multiwarehouse']['all_guarded_candidates']),ensure_ascii=False))
