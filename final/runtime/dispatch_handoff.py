"""Read-only handoff from adopted CSV plans and explicit simulated receipts.

Candidate routes never become dispatch instructions. Predictions are suppressed
when reforecast fails; source/revision and missing facts remain visible.
"""
import csv
import io
from copy import deepcopy
import matrix_dispatch


def report(scene):
    if not scene:
        return dict(available=False, reason='尚无保存场景')
    plan = scene['live_plan']; data = plan['data']
    if data.get('dispatch_model') != matrix_dispatch.MODEL or scene.get('progress_mode') != 'manual_receipts':
        return dict(available=False, reason='交接仅适用于CSV分步回执场景；旧记录不转换')
    valid = not plan.get('needs_replan') and not plan['metrics']['unserved']
    states = scene['states']; rule = data['settings']['window_rule']; clock = scene['clock']
    stops = {s['id']: (vid, s) for vid, route in plan['routes'].items()
             for s in route['stops'] if not matrix_dispatch.is_pickup(s['id'])}
    pickups = []
    for vid, route in plan['routes'].items():
        completed = {s['id']: s for s in states[vid]['completed']}
        for stop in route['stops']:
            if not matrix_dispatch.is_pickup(stop['id']): continue
            done = completed.get(stop['id']); loading = states[vid].get('loading')
            busy = loading and loading.get('task_id') == matrix_dispatch.pickup_id(stop)
            pickups.append(dict(task_id=matrix_dispatch.pickup_id(stop), vehicle=vid,
                load_ids=stop['load_ids'][:], status='模拟已装完' if done else '模拟装货中' if busy else '待回仓装货',
                predicted_arrival=stop['arrival'] if valid and not done else None,
                predicted_finish=stop['finish'] if valid and not done else None,
                simulated_finish=done['finish'] if done else None,
                evidence_source='模型按8分钟推进，非仓库回执'))
    records = {}
    for receipt in scene['receipts']:
        if receipt['source'] != 'manual_simulation': raise ValueError('交接发现不支持的回执来源')
        records.setdefault(receipt['order'], {})[receipt['receipt']] = receipt['time']
    rows = []
    for order in data['orders']:
        oid = order['id']; assigned = stops.get(oid); facts = records.get(oid, {})
        arrived = facts.get('arrived'); unloading = facts.get('unloading'); signed = facts.get('signed')
        owner = scene['cargo'][oid]
        lot = next((p for p in pickups if oid in p['load_ids']), None)
        if owner == 'delivered' and signed is None:
            raise ValueError('已交付订单缺少签收回执，不能生成交接')
        if signed is not None:
            status, actor, action = '已模拟签收', '调度', '核对模拟回执；真实单据及结算未接入'
        elif unloading is not None:
            status, actor, action = '已模拟开始卸货', '司机与门店', '卸货完成后确认模拟签收，保持货物归原车'
        elif arrived is not None:
            status, actor, action = '已模拟到达', '司机与门店', '核对收货开放时间并确认模拟开始卸货'
        elif owner == 'warehouse':
            status, actor, action = '新货仍在仓库', '仓库与司机', '核对批次及车辆，先回仓装本批，再送目的店'
        else:
            status, actor, action = '原车持货，未确认到达', '司机', '按已采用路线继续；预计到店不等于到达回执'
        if signed is None and not valid:
            actor = '调度'; action = '剩余预测已失效，核查实际进度与收货约束后重新试算；保留当前货物归属'
        prediction = assigned[1] if valid and signed is None and assigned else None
        actual_deadline = signed if rule == 'finish' else arrived
        rows.append(dict(order=oid,store=order.get('store',oid),quantity=sum(x['quantity'] for x in order['lines']),
            sku_lines=len(order['lines']),sku_summary='; '.join(f"{x['sku']}:{x['quantity']}箱,{x['unit_weight_kg']:g}kg/箱,{x['unit_volume_m3']:g}m3/箱" for x in order['lines']),
            required_service_minutes=order['service_minutes'],allowed_vehicles='|'.join(order['allowed_vehicles']),
            cargo_owner=owner,assigned_vehicle=assigned[0] if assigned else None,
            status=status,next_actor=actor,next_action=action,pickup_task=lot['task_id'] if lot else None,
            pickup_status=lot['status'] if lot else None,
            pickup_predicted_finish=lot['predicted_finish'] if lot else None,
            pickup_simulated_finish=lot['simulated_finish'] if lot else None,
            pickup_evidence_source=lot['evidence_source'] if lot else '初始原货已装车假设，非仓库回执',
            predicted_arrival=prediction['arrival'] if prediction else None,
            predicted_finish=prediction['finish'] if prediction else None,
            arrived_at=arrived,unloading_at=unloading,signed_at=signed,
            due=order['due'],close=order['close'],window_rule=rule,
            actual_late_minutes=round(max(0,actual_deadline-order['due']),2) if actual_deadline is not None else None,
            overdue_without_rule_receipt=actual_deadline is None and clock>order['due'],
            waiting_minutes=round((unloading if unloading is not None else clock)-arrived,2) if arrived is not None else None,
            completed_service_minutes=round(signed-unloading,2) if signed is not None else None,
            receipt_source='manual_simulation' if facts else 'none'))
    signed_rows = [r for r in rows if r['signed_at'] is not None]
    return dict(available=True,scene_id=scene['id'],revision=scene['revision'],clock=clock,
        adopted_version=scene['active'],candidate_version=scene['candidate'],prediction_valid=valid,
        input_kind=data['settings']['data_kind'],input_source_sha256=deepcopy(data.get('source_sha256',{})),
        rows=rows,pickups=pickups,metrics=dict(orders=len(rows),signed_orders=len(signed_rows),
            receipt_confirmed_late_orders=sum(r['actual_late_minutes']>0 for r in rows if r['actual_late_minutes'] is not None),
            overdue_without_rule_receipt=sum(r['overdue_without_rule_receipt'] for r in rows),
            arrived_to_unloading_minutes=round(sum(r['waiting_minutes'] for r in rows if r['waiting_minutes'] is not None),2),
            signed_service_minutes=round(sum(r['completed_service_minutes'] for r in signed_rows),2)),
        boundaries=['仅已采用版本；候选新增货与路线不进入交接',
                    '全部执行事实是人工模拟，非GPS、真实签收或结算凭证',
                    '等待为模拟到达至卸货开始；未开始卸货按当前模拟时钟累计，不能归因为客户责任',
                    '装货完成由8分钟模型推演；未接仓库实绩、排队、备货或三维装载',
                    '预计到达沿用求解器口径，可能包含开门等待；到达回执单独列示'])


FIELDS = ('scene_id','revision','adopted_version','clock','prediction_valid','input_kind',
          'order','store','quantity','sku_lines','sku_summary','required_service_minutes','allowed_vehicles',
          'cargo_owner','assigned_vehicle','status','next_actor','next_action',
          'pickup_task','pickup_status','pickup_predicted_finish','pickup_simulated_finish','pickup_evidence_source',
          'predicted_arrival','predicted_finish','arrived_at','unloading_at','signed_at','due','close',
          'window_rule','actual_late_minutes','overdue_without_rule_receipt','waiting_minutes',
          'completed_service_minutes','receipt_source')


def csv_text(result):
    if not result.get('available'): raise ValueError(result['reason'])
    output = io.StringIO(newline=''); writer = csv.DictWriter(output,fieldnames=FIELDS)
    writer.writeheader()
    for row in result['rows']:
        value = {key:result.get(key) for key in FIELDS[:6]}; value.update(row)
        # Do not let externally supplied text become a spreadsheet formula.
        value = {k: "'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r','\n')) else v for k,v in value.items()}
        writer.writerow(value)
    return '\ufeff'+output.getvalue()
