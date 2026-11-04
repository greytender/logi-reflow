"""Read-only local handoff contract. Never sends or connects to another system."""
from copy import deepcopy
from hashlib import sha256
import json

import execution
import pilot
import remaining

SCHEMA_VERSION = 'logireflow.local-handoff.v1'
UNITS = dict(weight='kg', volume='m3', distance='km', currency='CNY',
             task_time='integer_or_decimal_minutes_since_local_midnight',
             timestamp='ISO8601_UTC', pickup_count='pieces')
BOUNDARIES = [
    '本地只读交接包，未发送、未接入云载，不是派单或接收方回执',
    '企业订单、门店、仓库、车辆、车次及包装映射尚未确认，本机编号不是企业编号',
    '原计划费用与道路估算不是TMS合同结算或实际GPS公里；改序不重新结算',
    '人工或模拟记录不等于真实履约验证，位置观察不是GPS定位或签收',
    '载荷按箱规估算，不证明3D装卸、冷链或现场可执行',
    '确定性指纹用于本地内容核对，不是数字签名、鉴权或外部防重派发保证',
]


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _select(value, keys):
    return {k: deepcopy(value[k]) for k in keys if k in value}


def _record(value, observation=False):
    keys = ('id', 'vehicle', 'kind', 'source', 'recorded_at', 'observation_id')
    result = _select(value, keys)
    result['evidence_level'] = 'synthetic_record' if value.get('source') == 'simulated' else 'manually_reported_unverified'
    if observation:
        result.update(_select(value, ('plan_id', 'remaining_plan_id', 'node', 'observed_at',
            'vehicle_receipt_count', 'warnings_after')))
    else:
        result.update(_select(value, ('plan_id', 'remaining_plan_id', 'stop_id', 'finish', 'pickup', 'warnings_after')))
        if 'pickup' in result:
            result['pickup'] = _select(result['pickup'], ('cartons', 'totes'))
    # Free-form notes, raw files, credentials and unrelated task metadata are not exported.
    return result


def _original(plan):
    if not plan:
        return None
    optimized = plan['result']['optimized']
    routes = {}
    for vid, route in optimized['routes'].items():
        routes[vid] = _select(route, ('sequence', 'distance', 'return_at', 'cost', 'weight_kg', 'volume_m3'))
        if 'fee' in route:
            routes[vid]['fee'] = _select(route['fee'], ('mode', 'base', 'distance', 'stops', 'pickup_extra',
                'estimated_billing_km', 'tms_billing_km', 'actual_gps_km'))
        routes[vid]['stops'] = [_select(s, ('id', 'arrival', 'finish', 'pickup', 'pickup_weight_kg',
            'pickup_volume_m3', 'onboard_weight_kg', 'onboard_volume_m3')) for s in route['stops']]
    return dict(classification='departure_prediction', plan_id=plan['id'],
        input_revision=plan['input_revision'], input_sha256=plan['input_sha256'],
        routes=routes, unserved=[_select(o, ('id', 'reason', 'action')) for o in optimized['unserved']],
        fee_status='estimated_at_departure_not_settlement')


def export_handoff(task):
    """Export a deterministic, reviewable snapshot; never claims dispatch readiness.

    Unconfirmed/stale tasks can still be handed off for review, but have no
    current remaining forecast and explicitly retain their blocking reasons.
    """
    from application import fingerprint
    data = task['data']
    adopted_id = task.get('adopted_plan_id')
    plan = next((p for p in task['plans'] if p['id'] == adopted_id), None)
    local_reasons = []
    state = None
    local_status = 'unconfirmed'
    if plan:
        if plan['input_revision'] != task['input_revision'] or plan['input_sha256'] != fingerprint(data):
            local_status = 'stale'
            local_reasons.append('已确认原方案不对应当前输入，不能作为当前配送安排')
        elif plan['result']['optimized']['unserved']:
            local_status = 'incomplete'
            local_reasons.append('原方案仍有未安排任务')
        else:
            try:
                pilot.audit(data, plan['result']['optimized'])
                state = execution.view(task)
                seq = {vid: [s['id'] for s in r['remaining']] for vid, r in state['routes'].items()}
                remaining.audit(task, seq)
                if task.get('remaining_sequences'):
                    active = next((p for p in task.get('remaining_plans', [])
                        if p['id'] == task.get('active_remaining_plan_id') and p.get('adopted_at')), None)
                    if active is None:
                        raise ValueError('剩余顺序缺少可追溯的人工采用版本')
                local_status = 'current_confirmed'
            except (ValueError, KeyError, TypeError) as error:
                local_status = 'constraint_risk'
                local_reasons.append('本地核验未通过：'+str(error))
    else:
        local_reasons.append('尚未确认当前完整方案')
    if state:
        for vid, route in state['routes'].items():
            local_reasons.extend(vid+'：'+warning for warning in route['warnings'])
        if local_reasons:
            local_status = 'constraint_risk'
    current = None
    if state is not None:
        current = dict(classification='remaining_prediction', original_plan_id=state['plan_id'],
            remaining_plan_id=task.get('active_remaining_plan_id'), complete=state['complete'], routes={})
        for vid, route in state['routes'].items():
            current['routes'][vid] = _select(route, ('completed', 'next_stop', 'returned', 'onboard_weight_kg',
                'onboard_volume_m3', 'estimated_return', 'remaining_distance_km', 'collected_cartons',
                'collected_totes', 'warnings', 'anchor'))
            current['routes'][vid]['remaining'] = [_select(s, ('id', 'arrival', 'finish',
                'onboard_weight_kg', 'onboard_volume_m3')) for s in route['remaining']]
            if 'anchor' in route:
                current['routes'][vid]['anchor'] = _select(route['anchor'], ('kind', 'node', 'time', 'id', 'source'))
    source_kind = data['settings']['data_kind']
    refs = dict(local_task_id=task['id'], local_task_revision=task['revision'],
        local_input_revision=task['input_revision'], input_sha256=fingerprint(data),
        adopted_original_plan_id=adopted_id, active_remaining_plan_id=task.get('active_remaining_plan_id'),
        updated_at=task['updated_at'])
    mappings = dict(status='pending', enterprise_system_instance_id=None, enterprise_task_id=None,
        delivery_date=None, task_timezone=None,
        warehouse=dict(local_id=data['settings']['depot_id'], enterprise_warehouse_id=None),
        orders=[dict(local_stop_id=o['id'], enterprise_store_id=None, enterprise_order_ids=None)
                for o in data['orders']],
        vehicles=[dict(local_vehicle_id=v['id'], enterprise_vehicle_id=None, enterprise_trip_id=None)
                  for v in data['vehicles']],
        packaging=dict(enterprise_carton_type_id=None, enterprise_tote_type_id=None))
    facts = dict(classification='locally_recorded_facts_not_verified_fulfillment',
        receipts=[_record(r) for r in task.get('receipts', [])],
        observations=[_record(o, observation=True) for o in task.get('observations', [])])
    package = dict(schema_version=SCHEMA_VERSION, dispatch_status='not_sent', integration_status='not_connected',
        task_ref=refs, source=dict(data_kind=source_kind,
            evidence_level='synthetic_inputs' if source_kind=='simulated' else 'enterprise_source_claim_unverified',
            travel_source=data['settings']['travel_source'],
            packaging_basis='configured_estimate' if 'business' in data else 'not_enabled'),
        enterprise_mapping=mappings, original_plan=_original(plan), current_remaining_forecast=current,
        recorded_facts=facts, units=deepcopy(UNITS), boundaries=list(BOUNDARIES),
        readiness=dict(status='blocked', local_plan_status=local_status,
            local_checks_passed=local_status=='current_confirmed',
            reasons=local_reasons+['企业字段映射待确认', '配送日期与任务时区待确认',
                '真实目标实例、权限、接口和接收结果未验证']))
    package['handoff_id'] = _digest(package)
    validate_handoff(package)
    return package


def validate_handoff(package):
    """Check package identity and conservative semantics, not source authenticity."""
    if not isinstance(package, dict):
        raise ValueError('交接包必须是对象')
    expected = {'schema_version', 'handoff_id', 'dispatch_status', 'integration_status', 'task_ref', 'source',
        'enterprise_mapping', 'original_plan', 'current_remaining_forecast', 'recorded_facts', 'units',
        'boundaries', 'readiness'}
    if set(package) != expected or package.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('交接包版本或字段不匹配')
    if package['dispatch_status'] != 'not_sent' or package['integration_status'] != 'not_connected':
        raise ValueError('本地合同不能声称已发送或已接入')
    if package['readiness'].get('status') != 'blocked' or not package['readiness'].get('reasons'):
        raise ValueError('未完成真实映射及接入，不能标为可派发')
    if package['enterprise_mapping'].get('status') != 'pending':
        raise ValueError('企业编号映射尚待确认')
    def pending_mapping(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.startswith('enterprise_') and item is not None:
                    raise ValueError('本地合同不能把待确认映射填成企业编号')
                pending_mapping(item)
        elif isinstance(value, list):
            for item in value: pending_mapping(item)
    pending_mapping(package['enterprise_mapping'])
    if package['units'] != UNITS:
        raise ValueError('交接包单位口径不匹配')
    if package['source'].get('data_kind') not in ('simulated', 'enterprise'):
        raise ValueError('数据来源无效')
    expected_source = 'synthetic_inputs' if package['source']['data_kind']=='simulated' else 'enterprise_source_claim_unverified'
    if package['source'].get('evidence_level') != expected_source:
        raise ValueError('输入来源证据等级不匹配')
    local_passed = package['readiness'].get('local_checks_passed')
    if type(local_passed) is not bool:
        raise ValueError('本地核验状态必须为布尔值')
    if local_passed and package['readiness'].get('local_plan_status') != 'current_confirmed':
        raise ValueError('未确认或过期方案不能标为本地核验通过')
    if local_passed and (package['original_plan'] is None or
            package['original_plan']['input_sha256'] != package['task_ref']['input_sha256'] or
            package['original_plan']['input_revision'] != package['task_ref']['local_input_revision']):
        raise ValueError('本地核验通过的原方案必须对应当前输入')
    if package['original_plan'] is not None and package['original_plan'].get('classification') != 'departure_prediction':
        raise ValueError('原计划必须明确标为出发前预测')
    forecast = package['current_remaining_forecast']
    if forecast is not None and forecast.get('classification') != 'remaining_prediction':
        raise ValueError('剩余路线必须明确标为预测')
    if local_passed and forecast is None:
        raise ValueError('本地核验通过时必须提供当前剩余预测')
    facts = package['recorded_facts']
    if facts.get('classification') != 'locally_recorded_facts_not_verified_fulfillment':
        raise ValueError('本机记录不能冒充已验证履约')
    for group in ('receipts', 'observations'):
        for record in facts[group]:
            if record.get('kind') not in (('stop', 'return') if group=='receipts' else ('position', 'wait')):
                raise ValueError('记录类型不匹配')
            if record.get('source') not in ('simulated', 'manual'):
                raise ValueError('记录来源不支持')
            if package['source']['data_kind'] == 'simulated' and record['source'] != 'simulated':
                raise ValueError('构造数据不能冒充人工真实记录')
            level = 'synthetic_record' if record['source']=='simulated' else 'manually_reported_unverified'
            if record.get('evidence_level') != level:
                raise ValueError('记录证据等级不匹配')
    if forecast is not None:
        delivered = {r['stop_id'] for r in facts['receipts'] if r.get('kind')=='stop'}
        pending = [s['id'] for route in forecast['routes'].values() for s in route['remaining']]
        if len(pending) != len(set(pending)) or set(pending)&delivered:
            raise ValueError('剩余预测重复或包含已签收门店')
        original = package['original_plan']
        if original is None or forecast.get('original_plan_id') != original['plan_id']:
            raise ValueError('当前剩余预测必须引用已确认原方案')
        if original['plan_id'] != package['task_ref']['adopted_original_plan_id']:
            raise ValueError('原方案引用与任务引用不一致')
        if forecast.get('remaining_plan_id') != package['task_ref']['active_remaining_plan_id']:
            raise ValueError('剩余采用版本引用不一致')
        used = {vid: r for vid, r in original['routes'].items() if r['sequence']}
        if set(forecast['routes']) != set(used):
            raise ValueError('剩余预测的车辆集合不完整')
        receipts_by_vehicle = {vid: [] for vid in used}
        for record in facts['receipts']:
            vid = record['vehicle']
            if vid not in used or record.get('plan_id') != original['plan_id']:
                raise ValueError('回执必须引用本车原已确认方案')
            if record['kind'] == 'stop' and record['stop_id'] not in used[vid]['sequence']:
                raise ValueError('回执门店不属于原车')
            receipts_by_vehicle[vid].append(record)
        for vid, route in forecast['routes'].items():
            records = receipts_by_vehicle[vid]
            signed = [r['stop_id'] for r in records if r['kind'] == 'stop']
            if len(signed) != len(set(signed)):
                raise ValueError('签收记录包含重复门店')
            expected = set(used[vid]['sequence'])-set(signed)
            actual = [s['id'] for s in route['remaining']]
            if set(actual) != expected:
                raise ValueError('剩余预测必须保留本车全部未签收门店，不能漏单或换车')
            if route.get('completed') != len(signed):
                raise ValueError('已签收数量与记录不一致')
            if route.get('next_stop') != (actual[0] if actual else None):
                raise ValueError('下一门店与当前剩余顺序不一致')
            returned = any(r['kind']=='return' for r in records)
            if route.get('returned') is not returned or (returned and actual):
                raise ValueError('回仓状态与回执或剩余任务不一致')
            anchor = route.get('anchor')
            if anchor and anchor.get('kind') not in ('shift', 'receipt', 'observation'):
                raise ValueError('本地预测锚点不能冒充GPS等其他来源')
            active_observations = [o for o in facts['observations'] if o['vehicle']==vid
                and o.get('vehicle_receipt_count')==len(records)]
            if active_observations:
                if not anchor or anchor.get('kind')!='observation' or anchor.get('id')!=active_observations[-1]['id']:
                    raise ValueError('预测锚点未使用本车最新有效观察记录')
            elif records:
                if not anchor or anchor.get('kind')!='receipt' or anchor.get('id')!=records[-1]['id']:
                    raise ValueError('预测锚点未使用本车最新回执')
            elif anchor and (anchor.get('kind')!='shift' or anchor.get('source')!='planned' or
                    anchor.get('id') is not None or
                    anchor.get('node')!=package['enterprise_mapping']['warehouse']['local_id']):
                raise ValueError('尚未开始的车辆锚点必须标为仓库班次预测')
            if anchor and anchor.get('kind') != 'shift':
                candidates = facts['observations'] if anchor.get('kind')=='observation' else records
                record = next((r for r in candidates if r['id']==anchor.get('id') and r['vehicle']==vid), None)
                if record is None:
                    raise ValueError('预测锚点缺少对应的本机记录')
                node = record['node'] if anchor['kind']=='observation' else (
                    package['enterprise_mapping']['warehouse']['local_id'] if record['kind']=='return' else record['stop_id'])
                time = record['observed_at'] if anchor['kind']=='observation' else record['finish']
                if (anchor.get('node'),anchor.get('time'),anchor.get('source')) != (node,time,record['source']):
                    raise ValueError('预测锚点的位置、时刻或来源与记录不一致')
        if forecast.get('complete') is not (bool(used) and all(r['returned'] for r in forecast['routes'].values())):
            raise ValueError('整体回仓状态与各车回执不一致')
    content = {k:v for k,v in package.items() if k != 'handoff_id'}
    if package['handoff_id'] != _digest(content):
        raise ValueError('交接包内容指纹不匹配')
    return dict(passed=True, checks=['版本与单位', '本地未发送/未接入', '来源和证据等级',
        '预测与已记录事实分开', '剩余门店无重复签收', '确定性内容指纹'])
