"""Audited opt-in planning with versioned forecasts and bounded handling repair.

This produces reviewable candidates and preserves their actual action witness.
No task adoption, receipt mutation, network request or enterprise dispatch.
"""
from copy import deepcopy
from hashlib import sha256
from time import perf_counter

import application
import forecast_learning as learning
import multiwarehouse as mw
import operational_constraints as op
import pilot
import warehouse_loading


def digest(value):return sha256(application.encode(value).encode()).hexdigest()


def quality(report):
    route=report['adjusted_route'];handling=report.get('time_adjustments',{})
    return (route['late_orders'],route['late_minutes'],handling.get('high_risk_rehandle_moves',0),
            handling.get('rehandle_moves',0),route['return_at'])


def improve_route(data,route,vehicle,policy,*,max_trials=4):
    """Keep immediate legacy; try batch/layout only on failure or rehandling.

    One shared placement budget, no rotation/relaxed rules. Batch may fail when
    staging space is small, so it must never unconditionally replace legacy.
    """
    if isinstance(max_trials,bool) or not isinstance(max_trials,int) or not 1<=max_trials<=4:
        raise ValueError('自适应修复试次需为1至4')
    started=perf_counter();p=op.validate_policy(data,policy)
    if p is None:raise ValueError('自适应装卸需要完整声明约束')
    work={'placement_checks':0};trials=[];feasible=[];first=None;trigger=None
    combinations=(('legacy','immediate'),('legacy','stop_batch'),
                  ('wide_lanes','stop_batch'),('cold_last','stop_batch'))
    for packing,handling in combinations[:max_trials]:
        before=work['placement_checks']
        report=op.evaluate_route(data,route,vehicle,p,packing_variant=packing,
                                 handling_variant=handling,_work=work)
        if first is None:first=report
        replay=op.audit_actions(report)
        valid=report.get('passed') is True and replay['passed']
        trials.append(dict(packing_variant=packing,handling_variant=handling,passed=valid,
                           error_codes=sorted({e['code'] for e in report['errors']}),
                           status=report['status'],placement_checks=work['placement_checks']-before))
        if valid:feasible.append(report)
        if len(trials)==1:
            if valid and not report.get('time_adjustments',{}).get('rehandle_moves',0):
                trigger='no_rehandling_no_extra_search';break
            trigger='legacy_rejected' if not valid else 'rehandling_present'
            # These declared failures are independent of loading/handling order.
            fixed={'identity','incompatible_goods','vehicle_access','cold_vehicle','cold_zone',
                   'cold_zone_temperature','load_readiness','energy','entity_budget'}
            if any(e['code'] in fixed for e in report['errors']):
                trigger='fixed_constraint_or_entity_limit';break
        if report['status']=='SEARCH_BUDGET_EXHAUSTED':break
    chosen=deepcopy(min(feasible,key=quality) if feasible else first)
    exhausted=any(t['status']=='SEARCH_BUDGET_EXHAUSTED' for t in trials)
    if not feasible and exhausted:chosen['status']='SEARCH_BUDGET_EXHAUSTED'
    chosen['adaptive_search']=dict(schema='logireflow.adaptive-handling.v1',trigger=trigger,
        selected_packing=chosen.get('packing_variant','legacy'),
        selected_handling=chosen.get('handling_variant','immediate'),trials=trials,
        placement_checks=work['placement_checks'],max_placement_checks=op.MAX_PLACEMENT_CHECKS,
        budget_exhausted=exhausted,legacy_preserved=bool(first.get('passed')),
        elapsed_ms=round((perf_counter()-started)*1000,3),
        scope='同一站序的布局与同站批量恢复；不代表物理全局最优')
    return chosen


def forecast_input(data,model,decision_at,*,use_risk_upper=True):
    if type(use_risk_upper) is not bool:raise ValueError('风险预测开关需为布尔值')
    result=deepcopy(data);wid=data['settings']['depot_id'];kind=data['settings']['data_kind'];forecasts=[]
    if 'business' not in data:raise ValueError('学习回收需要已声明包装与计费规则')
    for order in result['orders']:
        prediction=learning.predict(model,wid,kind,order['id'],order['pickup'],decision_at)
        order['pickup']=deepcopy(prediction['risk_upper' if use_risk_upper else 'expected'])
        forecasts.append(dict(store_id=order['id'],**prediction))
    result=application.canonicalize(result)
    return result,forecasts


def _plan_for_sequences(data,policy,sequences,max_trials):
    ev=pilot.evaluator(data);orders={o['id']:o for o in data['orders']};routes={};reports={}
    for vehicle in data['vehicles']:
        vid=vehicle['id'];route=ev(sequences[vid],vehicle,orders)
        if route is None:return None
        report=improve_route(data,route,vehicle,policy,max_trials=max_trials)
        if report.get('passed') is not True:return None
        routes[vid]=route;reports[vid]=report
    served=[sid for r in routes.values() for sid in r['sequence']]
    if len(served)!=len(set(served)) or set(served)!=set(orders):return None
    # A shared physical cold box must never be instantiated on two routes.
    cold=[b['id'] for r in reports.values() for b in r.get('cold_boxes',[])]
    if len(cold)!=len(set(cold)):return None
    metrics=dict(served=len(served),unserved=0,
        late_orders=sum(r['late_orders'] for r in routes.values()),
        late_minutes=round(sum(r['late_minutes'] for r in routes.values()),2),
        cost=round(sum(r['cost'] for r in routes.values()),2),
        distance=round(sum(r['distance'] for r in routes.values()),2))
    plan=dict(routes=routes,unserved=[],metrics=metrics)
    pilot.audit(data,plan)
    return dict(plan=plan,reports=reports,complete=True,
                used_vehicles=[vid for vid,r in routes.items() if r['sequence']],
                cost=sum(r['cost'] for r in routes.values()),
                late_orders=sum(r['adjusted_route']['late_orders'] for r in reports.values()),
                load_by_vehicle={v['id']:dict(
                    weight=reports[v['id']]['adjusted_route']['weight_kg']/v['max_weight_kg'],
                    volume=reports[v['id']]['adjusted_route']['volume_m3']/v['max_volume_m3'])
                    for v in data['vehicles'] if routes[v['id']]['sequence']})


def _rank(candidate):
    reports=candidate['reports'].values()
    return (candidate['late_orders'],sum(r['adjusted_route']['late_minutes'] for r in reports),
            candidate['cost'],sum(r.get('time_adjustments',{}).get('high_risk_rehandle_moves',0) for r in reports),
            sum(r.get('time_adjustments',{}).get('rehandle_moves',0) for r in reports),
            sum(r['adjusted_route']['return_at'] for r in reports))


def _guard(reference,candidate):
    if reference is None or candidate['late_orders']:return False
    if set(reference['used_vehicles'])!=set(candidate['used_vehicles']):return False
    if candidate['cost']>reference['cost']+1e-7:return False
    return all(candidate['load_by_vehicle'][vid][k]+.02+1e-7>=n
               for vid,load in reference['load_by_vehicle'].items() for k,n in load.items())


def plan_warehouse(data,policy,*,model=None,decision_at,use_risk_upper=True,max_trials=4):
    """Forecast -> solve -> bounded handling repair -> strict candidate gate.

    Nominal plans are rechecked against updated forecasts. No new forecast is
    silently treated as an execution fact; no unverified model is loaded here.
    """
    started=perf_counter();source=application.canonicalize(data);p=op.validate_policy(source,policy)
    if p is None:raise ValueError('此升级入口需要完整专项约束')
    if model is not None and 'available_after' not in model:raise ValueError('只接受已激活且带可用时间快照的学习模型')
    nominal=warehouse_loading.operational_compare(source,None,p)['optimized']
    nominal_sequences={vid:r['sequence'] for vid,r in nominal['routes'].items()}
    reference=_plan_for_sequences(source,p,nominal_sequences,1)
    learned,forecasts=forecast_input(source,model,decision_at,use_risk_upper=use_risk_upper)
    candidates=[];failures=[]
    # Reuse the known routes as well as search again when the prediction changes.
    inputs=[('nominal_routes_rechecked',nominal_sequences)]
    if digest(source)!=digest(learned):
        rerun=warehouse_loading.operational_compare(learned,None,p)['optimized']
        inputs.append(('replanned_forecast', {vid:r['sequence'] for vid,r in rerun['routes'].items()}))
    seen=set()
    for name,sequences in inputs:
        key=digest(sequences)
        if key in seen:continue
        seen.add(key);candidate=_plan_for_sequences(learned,p,sequences,max_trials)
        if candidate is None:failures.append(dict(source=name,reason='新预测下未找到完整核验通过方案'));continue
        candidate['source']=name;candidate['guarded']=_guard(reference,candidate);candidates.append(candidate)
    forecast_review=any(f.get('requires_review') for f in forecasts)
    guarded=[c for c in candidates if c['guarded']] if not forecast_review else []
    selected=min(guarded,key=_rank) if guarded else None
    # Unguarded complete alternatives remain reviewable, without adoption claims.
    alternative=min(candidates,key=_rank) if candidates else None
    result=dict(schema='logireflow.adaptive-warehouse.v1',data_kind=source['settings']['data_kind'],
        source_input=source,planning_input=learned,operational_policy=p,
        source_sha256=digest(source),planning_sha256=digest(learned),policy_sha256=digest(p),
        forecast_model_id=model['id'] if model else None,forecasts=forecasts,
        forecast_confirmation_required=forecast_review,
        reference=reference,reference_sha256=digest(reference),candidate=selected,alternative=alternative if selected is None else None,
        guarded_candidate_available=selected is not None,status='REVIEWABLE_CANDIDATE' if selected else 'NEEDS_MANUAL_REVIEW',
        failures=failures,elapsed_ms=round((perf_counter()-started)*1000,3),
        boundaries=['仅本地候选，未确认或派单','预测不是回执事实','固定在用车队、逐车双维2个百分点及名义费用护栏',
                    '新预测不可行时明确交回人工；不称原计划在未来仍安全','保存实际布局与动作，不能只保存站序重建'])
    result['witness_verified']=revalidate(result)
    result['elapsed_ms']=round((perf_counter()-started)*1000,3)
    return result


def revalidate(result):
    if (digest(result['source_input'])!=result['source_sha256'] or
        digest(result['planning_input'])!=result['planning_sha256'] or
        digest(result['operational_policy'])!=result['policy_sha256'] or
        digest(result['reference'])!=result['reference_sha256']):raise ValueError('输入、参照或约束已变化，候选失效')
    candidate=result.get('candidate') or result.get('alternative')
    if candidate is None:return False
    data=result['planning_input'];p=op.validate_policy(data,result['operational_policy'])
    pilot.audit(data,candidate['plan'])
    served=[s for r in candidate['plan']['routes'].values() for s in r['sequence']]
    if candidate['plan']['unserved'] or len(served)!=len(set(served)) or set(served)!={o['id'] for o in data['orders']}:
        raise ValueError('候选不是完整唯一任务集合')
    cold=[];actual_loads={}
    for vehicle in data['vehicles']:
        vid=vehicle['id'];saved=candidate['reports'][vid]
        replay=op.evaluate_route(data,candidate['plan']['routes'][vid],vehicle,p,
                                packing_variant=saved.get('packing_variant','legacy'),
                                handling_variant=saved.get('handling_variant','immediate'))
        if replay.get('passed') is not True or not op.audit_actions(saved)['passed']:raise ValueError('候选动作或约束重放未通过')
        if digest(replay['frames'])!=digest(saved['frames']) or digest(replay['actions'])!=digest(saved['actions']):
            raise ValueError('候选布局/动作与声明策略不一致')
        for field in ('adjusted_route','time_adjustments','cold_boxes'):
            if digest(replay.get(field))!=digest(saved.get(field)):raise ValueError('候选时间、载荷或冷链指标与重放不一致')
        cold.extend(b['id'] for b in replay.get('cold_boxes',[]))
        if candidate['plan']['routes'][vid]['sequence']:
            actual_loads[vid]=dict(weight=replay['adjusted_route']['weight_kg']/vehicle['max_weight_kg'],
                                   volume=replay['adjusted_route']['volume_m3']/vehicle['max_volume_m3'])
    if len(cold)!=len(set(cold)):raise ValueError('共享实体被重复分车')
    if result.get('candidate') is not None:
        actual=deepcopy(candidate)
        actual.update(load_by_vehicle=actual_loads,used_vehicles=list(actual_loads),
                      cost=sum(r['cost'] for r in candidate['plan']['routes'].values()),
                      late_orders=sum(r['adjusted_route']['late_orders'] for r in candidate['reports'].values()))
        if not _guard(result['reference'],actual):raise ValueError('候选不满足固定车队、装载或费用护栏')
    return True


def plan_batch(raw,*,decision_at,learning_store=None,max_trials=4):
    data=mw.canonicalize(raw);results={};parts=mw.split(data)
    for warehouse in data['warehouses']:
        wid=warehouse['id'];part=parts[wid]
        model=learning_store.active_model(wid,part['settings']['data_kind']) if learning_store else None
        results[wid]=plan_warehouse(part,warehouse.get('operational_policy'),model=model,
                                   decision_at=decision_at,max_trials=max_trials)
    return dict(schema='logireflow.adaptive-batch.v1',input_sha256=digest(data),warehouses=results,
                all_guarded_candidates=all(r['guarded_candidate_available'] for r in results.values()),
                scope='各仓独立求解与学习，未跨仓借车/调拨/安排同车多趟；候选未采用')
