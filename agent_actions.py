"""Validated AI proposals. No confirmation, receipt or enterprise dispatch writes."""
from copy import deepcopy
from uuid import uuid4

import application as app
import multiwarehouse as mw
import pilot
import warehouse_loading
from store import Conflict


def selected_context(task, selection=None):
    selection = selection or {}
    if not isinstance(selection, dict) or set(selection)-{'warehouse_id', 'vehicle_id', 'order_id'}:
        raise ValueError('助手作用对象需要有效的仓库、车辆或配送任务编号')
    result = {}
    for key, rows in (('warehouse_id', task['data']['warehouses']), ('vehicle_id', task['data']['vehicles']), ('order_id', task['data']['orders'])):
        if selection.get(key):
            row = next((r for r in rows if r['id'] == selection[key]), None)
            if row is None: raise ValueError('选择的业务对象不存在，请重新选择')
            result[key] = row['id']
            if key == 'vehicle_id':
                result['vehicle'] = {k: row[k] for k in ('id', 'warehouse_id', 'max_weight_kg', 'max_volume_m3', 'shift_start', 'shift_end')}
            if key == 'order_id':
                result['order'] = {k: row[k] for k in ('id', 'warehouse_id', 'open', 'due', 'close', 'service_minutes', 'allowed_vehicles')}
                result['order'].update(weight_kg=sum(l['quantity']*l['unit_weight_kg'] for l in row['lines']),
                    volume_m3=sum(l['quantity']*l['unit_volume_m3'] for l in row['lines']), pickup=deepcopy(row.get('pickup')))
    selected_warehouse = result.get('warehouse_id')
    for key in ('vehicle', 'order'):
        if selected_warehouse and result.get(key, {}).get('warehouse_id', selected_warehouse) != selected_warehouse:
            raise ValueError('选中的仓库、车辆和门店不属于同一仓，请重新选择')
    plan = mw.current_plan(task)
    if plan:
        result['candidate_metrics'] = deepcopy(plan['summary']['optimized'])
        selected_vid = selection.get('vehicle_id')
        # Detailed routes are sent only for constructed input or the vehicle explicitly selected by the user.
        result['routes'] = [dict(warehouse_id=wid, vehicle_id=vid, sequence=route['sequence'], cost=route['cost'])
            for wid, group in plan['warehouses'].items() for vid, route in group['optimized']['routes'].items()
            if group['data']['settings']['data_kind']=='simulated' or vid==selected_vid]
    # Rule fields are configuration, not original enterprise order rows.
    result['rules'] = [dict(warehouse_id=w['id'], business=deepcopy(w.get('business')))
        for w in task['data']['warehouses'] if not selection.get('warehouse_id') or selection['warehouse_id']==w['id']]
    return result


def reorder(task, actions):
    if mw.has_facts(task): raise Conflict('已有执行事实，不能跨车移动货物；请使用同车剩余路线工具')
    plan = mw.current_plan(task)
    if not plan: raise ValueError('先生成一个当前候选，再调整车辆或顺序')
    if len(task['plans']) >= 30: raise ValueError('已有30份方案，请另建批次')
    sequences = {wid: {vid: list(r['sequence']) for vid, r in g['optimized']['routes'].items()} for wid, g in plan['warehouses'].items()}
    if not isinstance(actions, list) or not 1<=len(actions)<=8: raise ValueError('调整需要1—8个明确操作')
    for action in actions:
        if not isinstance(action, dict): raise ValueError('调整操作格式无效')
        kind = action.get('kind')
        if kind == 'move_order':
            if set(action)-{'kind','order_id','vehicle_id','position'}: raise ValueError('调车操作有未支持字段')
            oid, vid = action.get('order_id'), action.get('vehicle_id')
            order = next((o for o in task['data']['orders'] if o['id']==oid), None)
            vehicle = next((v for v in task['data']['vehicles'] if v['id']==vid), None)
            if order is None or vehicle is None or order['warehouse_id']!=vehicle['warehouse_id']:
                raise ValueError('订单或车辆未知，或涉及未支持的跨仓调拨')
            group=sequences[order['warehouse_id']]
            for seq in group.values():
                if oid in seq: seq.remove(oid)
            target=group[vid]; position=action.get('position', len(target))
            if not isinstance(position,int) or isinstance(position,bool) or not 0<=position<=len(target):
                raise ValueError('插入位置需要从0开始的有效整数')
            target.insert(position,oid)
        elif kind == 'reorder_route':
            if set(action)!={'kind','vehicle_id','sequence'}: raise ValueError('调序需要车辆和完整配送任务顺序')
            vehicle = next((v for v in task['data']['vehicles'] if v['id']==action['vehicle_id']),None)
            if vehicle is None: raise ValueError('调序车辆不存在')
            seq=action['sequence']; old=sequences[vehicle['warehouse_id']][vehicle['id']]
            if not isinstance(seq,list) or any(not isinstance(x,str) for x in seq) or sorted(seq)!=sorted(old):
                raise ValueError('同车调序须保留当前车辆的全部任务且不能重复')
            sequences[vehicle['warehouse_id']][vehicle['id']]=list(seq)
        else: raise ValueError('不支持的路线调整操作')
    results=deepcopy(plan['warehouses'])
    for wid, part in mw.split(task['data']).items():
        orders={o['id']:o for o in part['orders']}; assigned=[x for seq in sequences[wid].values() for x in seq]
        if set(assigned)!=set(orders) or len(assigned)!=len(set(assigned)): raise ValueError('调整必须保留全部配送任务且只出现一次')
        evaluator=pilot.evaluator(part); routes={}
        for vehicle in part['vehicles']:
            route=evaluator(sequences[wid][vehicle['id']],vehicle,orders)
            if route is None: raise ValueError(vehicle['id']+'：调整违反容量、回收、准入或时间约束，未保存候选')
            routes[vehicle['id']]=route
        warehouse=next(w for w in task['data']['warehouses'] if w['id']==wid)
        if 'loading_profile' in warehouse:
            routes=warehouse_loading.manual(part,warehouse['loading_profile'],routes)
        if 'operational_policy' in warehouse:
            routes=warehouse_loading.operational_manual(part,routes,warehouse['operational_policy'],
                compact=warehouse.get('planning_mode') == 'fleet')
        score=app.normal.score(routes)
        optimized=dict(routes=routes,unserved=[],metrics=dict(served=len(orders),unserved=0,late_orders=score[0],
            late_minutes=round(score[1],2),cost=round(score[2],2),distance=round(sum(r['distance'] for r in routes.values()),2)))
        checked=pilot.audit(part,optimized)
        results[wid].update(optimized=optimized,audit={**results[wid]['audit'],'optimized':checked})
        if 'operational_policy' in warehouse:
            import operational_constraints
            results[wid]['operational_verification']=operational_constraints.evaluate_plan(part,optimized,warehouse['operational_policy'],
                compact=warehouse.get('planning_mode') == 'fleet', reuse_checked=True,
                layout=operational_constraints.FLEET_LAYOUT if warehouse.get('planning_mode') == 'fleet' else None)
        if 'loading_profile' in warehouse:
            previous=results[wid].get('loading_verification',{})
            previous.pop('work',None)
            results[wid]['loading_verification']={**previous,'status':'VERIFIED_WITHIN_MODEL','source':'agent_candidate_revalidation'}
    candidate=dict(id=uuid4().hex,parent_plan_id=plan['id'],method='agent_adjustment',created_at=app.now(),
        input_revision=task['input_revision'],input_sha256=app.fingerprint(task['data']),warehouses=results,
        audit=mw.audit(task['data'],results,reuse_checked=True),**mw.summarize(task['data'],results))
    task['plans'].append(candidate); task.update(adopted_plan_id=None,execution_tasks={})
    return dict(plan_id=candidate['id'],actions=deepcopy(actions),before=deepcopy(plan['summary']['optimized']),
        after=deepcopy(candidate['summary']['optimized']),audit=candidate['audit'],status='candidate_requires_review')


def rule_draft(task, actions):
    if mw.has_facts(task): raise Conflict('已有执行事实，不能修改原批次规则；请新建试算批次')
    if not isinstance(actions,list) or not 1<=len(actions)<=8: raise ValueError('规则草稿需要1—8项明确修改')
    data=deepcopy(task['data']); changes=[]
    for action in actions:
        if not isinstance(action,dict): raise ValueError('规则操作格式无效')
        if action.get('kind')=='tariff':
            if set(action)!={'kind','warehouse_id','field','value'}: raise ValueError('计费修改字段不完整')
            warehouse=next((w for w in data['warehouses'] if w['id']==action['warehouse_id']),None)
            if not warehouse or not warehouse.get('business'): raise ValueError('目标仓库未启用业务计费规则')
            if action['field'] not in ('base','included_km','per_km','per_stop','mode'): raise ValueError('不支持的计费参数')
            target=warehouse['business']['tariff']; field=action['field']; subject=warehouse['id']
        elif action.get('kind')=='pickup':
            if set(action)!={'kind','order_id','field','value'}: raise ValueError('预计回收修改字段不完整')
            order=next((o for o in data['orders'] if o['id']==action['order_id']),None)
            if not order or not order.get('pickup'): raise ValueError('目标配送任务未启用包装回收')
            if action['field'] not in ('cartons','totes'): raise ValueError('只支持预计纸箱或周转箱数量')
            target=order['pickup']; field=action['field']; subject=order['id']
        else: raise ValueError('当前只支持计费或预计回收规则草稿')
        changes.append(dict(subject=subject,kind=action['kind'],field=field,before=target[field],after=action['value']))
        target[field]=action['value']
    clean=mw.canonicalize(data)
    return dict(status='draft_requires_application',input_revision=task['input_revision'],base_sha256=app.fingerprint(task['data']),
        data=clean,changes=changes,next_action='人工核对后应用草稿，再重新计算方案；当前任务输入尚未改变')


def apply_rule_draft(store, task_id, revision, run_id, reviewed):
    task=store.read(task_id);store.guard(task,revision)
    if reviewed is not True: raise ValueError('请先核对规则差异，再应用草稿')
    run=next((r for r in task.get('agent_runs',[]) if r['id']==run_id and not r.get('chat_deleted')),None)
    draft=run.get('result',{}).get('rule_draft') if run else None
    if not draft: raise ValueError('规则草稿不存在')
    if draft['input_revision']!=task['input_revision'] or draft['base_sha256']!=app.fingerprint(task['data']):
        raise Conflict('输入已经变化，规则草稿已失效，请重新提出修改')
    if mw.has_facts(task): raise Conflict('已有执行事实，不能应用旧批次规则草稿')
    return store.save(task_id,revision,task['name'],draft['data'])


def organize_chat(store, task_id, revision, run_id, action):
    if action not in ('archive', 'restore', 'delete'):
        raise ValueError('请选择有效的对话记录操作')
    task = store.read(task_id)
    store.guard(task, revision)
    run = next((entry for entry in task.get('agent_runs', [])
                if entry['id'] == run_id and entry.get('requested_workflow') == 'request'), None)
    if run is None or run.get('chat_deleted'):
        raise ValueError('这条对话记录已不存在')
    if action == 'delete':
        run.pop('user_message', None)
        run.pop('message', None)
        run['chat_deleted'] = True
    else:
        run['chat_archived'] = action == 'archive'
    return store.commit(task, revision)
