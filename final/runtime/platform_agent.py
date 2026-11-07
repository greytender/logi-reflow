"""Platform-side tool workflow with optional server-owned model adapter.

A server-owned Planner implements plan(context, message, tools). It may
propose only the allowlisted workflows below. Confirmation and factual writes
remain direct operator actions. Nothing here uses the Codex runtime.
"""
from copy import deepcopy
from time import perf_counter
from uuid import uuid4
import re

import application as app
import multiwarehouse as mw
import remaining
import agent_actions
import recovery_search
from agent_control import WorkflowCancelled
from store import Conflict

TOOLS = {
    'inspect': {'description': '检查当前批次的仓库归属、字段完整性与执行状态', 'creates_candidate': False},
    'dispatch': {'description': '检查输入、计算各仓排车候选、独立核验并比较费用和利用率', 'creates_candidate': True},
    'compare': {'description': '读取当前候选，解释费用、车次和利用率；基线不是企业人工计划', 'creates_candidate': False},
    'risks': {'description': '按当前输入和本机记录汇总风险、来源缺口及下一步', 'creates_candidate': False},
    'recovery_trial': {'description': '仅对当前选中的模拟仓运行出发前回收压力试算，对比局部修复与情景ALNS，不保存配送候选', 'creates_candidate': False},
    'remaining': {'description': '执行变化后计算有记录仓库的同车剩余顺序候选，保留事实', 'creates_candidate': True},
    'adjust': {'description': '明确车辆与任务编号后，核验调车或调序并生成待人工确认候选', 'creates_candidate': True},
    'rules': {'description': '根据明确的新值生成计费或预计包装回收规则草稿，必须人工应用后重新计算', 'creates_candidate': False},
    'coverage_audit': {'description': '逐仓检查冷链共享箱、回收空间、时效、运力等约束的配置与核验覆盖，区分缺参数和未建模', 'creates_candidate': False},
    'spatial_inspect': {'description': '查看选中车辆和门店的车厢、装卸步骤、沿途回收空间与阻挡证据，返回本地三维查看上下文', 'creates_candidate': False},
    'route_review': {'description': '核对当前选中车辆的站序、时间、往返试算与装卸回收依据', 'creates_candidate': False},
}


def status(provider=None):
    if provider is not None:
        return dict(runtime='deepseek_platform_workflow', model_connected=provider['configured'],
                    model_status=provider['connection_status'], external_requests_enabled=provider['configured'],
                    provider=provider, tools=TOOLS,
                    human_only=['adopt', 'manual_route_edit', 'adopt_remaining', 'receipt', 'observe', 'enterprise_dispatch'])
    return dict(runtime='platform_local_workflow', model_connected=False,
        model_status='awaiting_provider_contract', external_requests_enabled=False,
        tools=TOOLS, human_only=['adopt', 'manual_route_edit', 'adopt_remaining', 'receipt', 'observe', 'enterprise_dispatch'])


def display_aliases(task):
    """Match the UI labels using the full batch order, never warehouse-local order."""
    return {key: [dict(label=f'{prefix}{index}', id=item['id'])
                  for index, item in enumerate(task['data'].get(key, []), 1)]
            for key, prefix in (('orders', '门店'), ('vehicles', '车辆'), ('warehouses', '仓库'))}


def inspect(task):
    mw.canonicalize(task['data'])
    s = mw.view(task)
    return dict(passed=True, warehouses=len(task['data']['warehouses']), orders=len(task['data']['orders']),
                vehicles=len(task['data']['vehicles']), status=s['status'], has_execution_facts=mw.has_facts(task),
                data_sources={w['id']: w['settings']['data_kind'] for w in task['data']['warehouses']},
                operational_policy_warehouses=sum('operational_policy' in w for w in task['data']['warehouses']),
                operational_execution_replanning_supported=False,
                validation_scope='required_input_fields_only_not_all_business_constraints')


def compare(task):
    p = mw.current_plan(task)
    if not p: return dict(status='needs_candidate', next_action='准备数据后计算各仓候选。')
    checked = mw.audit(task['data'], p['warehouses'], reuse_checked=True)
    b, m = p['summary']['baseline'], p['summary']['optimized']
    same_tasks = not b['unserved'] and not m['unserved'] and b['served'] == m['served']
    tradeoffs = comparison_tradeoffs(task,p)
    same_constraints = tradeoffs['same_verified_constraint_set']
    comparable = same_tasks and same_constraints
    return dict(status='review_required', plan_id=p['id'], audit=checked, baseline=deepcopy(b), candidate=deepcopy(m),
        same_complete_task_set=same_tasks, same_verified_constraint_set=same_constraints,
        comparison_limit=None if comparable else '任务集合未完整一致或基线未通过相同专项约束，只描述差异，不计算等约束节省',
        delta_cost_vs_simple_baseline=round(m['cost']-b['cost'], 4) if comparable else None,
        physical_loading=loading_summary(task), constraint_coverage=coverage_audit(task),
        tradeoffs=tradeoffs,
        interpretation='仅重量/容积利用率；基线为简单追加，非企业人工计划，非人力节省证明',
        next_action='先处理未安排任务。' if m['unserved'] else '调度员核对路线与规则后，手动确认整个批次。')


def comparison_tradeoffs(task, plan):
    """Compare each warehouse against all of its declared constraint models."""
    import warehouse_loading
    import pilot
    warehouses={w['id']:w for w in task['data']['warehouses']}
    def metrics(mode):
        groups=plan['warehouses']; routes=[r for g in groups.values() for r in g[mode]['routes'].values() if r['sequence']]
        pickup=all('business' in g['data'] for g in groups.values())
        result=dict(estimated_distance_km=round(sum(r['distance'] for r in routes),4),
            planned_trips=len(routes),estimated_cost_yuan=round(sum(r['cost'] for r in routes),4),
            forecast_late_tasks=sum(r['late_orders'] for r in routes),
            planned_return_cartons=sum(s.get('pickup',{}).get('cartons',0) for r in routes for s in r['stops']) if pickup else None,
            planned_return_totes=sum(s.get('pickup',{}).get('totes',0) for r in routes for s in r['stops']) if pickup else None,
            cold_chain_model_passed=None,extra_handling_actions=None,cold_box_openings=None,
            extra_operation_minutes=None,latest_model_return_at=None)
        checks=[];geometry_checks=[];adjusted=[];operation_routes=[];warehouse_checks=[]
        for wid,group in groups.items():
            warehouse=warehouses[wid];candidate=group[mode];geometry_passed=None;operational_passed=None
            try:
                pilot.audit(group['data'],candidate)
                core_passed=not bool(candidate.get('unserved'))
            except (ValueError,KeyError,TypeError):core_passed=False
            if 'loading_profile' in warehouse:
                try:
                    # Baseline may not carry placements: construct only for this
                    # feasibility check, preserving its exact vehicle sequences.
                    geometry_plan=deepcopy(candidate)
                    if mode=='baseline':
                        geometry_plan['routes']=warehouse_loading.manual(group['data'],warehouse['loading_profile'],geometry_plan['routes'])
                    warehouse_loading.check_plan(group['data'],warehouse['loading_profile'],geometry_plan)
                    geometry_passed=not bool(candidate.get('unserved'))
                except (ValueError,KeyError,TypeError):geometry_passed=False
                geometry_checks.append(geometry_passed)
            if 'operational_policy' in warehouse:
                check_name = 'baseline_operational_verification' if mode == 'baseline' else 'operational_verification'
                check=group.get(check_name) or _operational_result(group['data'],warehouse,candidate);checks.append(check)
                operational_passed=check.get('passed') is True and check.get('complete') is True
                for vid,route in candidate['routes'].items():
                    report=check.get('routes',{}).get(vid,{})
                    if route['sequence']:
                        adjusted.append(report.get('adjusted_route',route));operation_routes.append(report)
            else:
                adjusted.extend(r for r in candidate['routes'].values() if r['sequence'])
            warehouse_checks.append(dict(warehouse_id=wid,geometry_model_passed=geometry_passed,
                operational_model_passed=operational_passed,core_model_passed=core_passed))
        result['constraint_checks']=warehouse_checks
        result['declared_constraint_models_passed']=all(c['geometry_model_passed'] is not False and
            c['operational_model_passed'] is not False and c['core_model_passed'] for c in warehouse_checks)
        if geometry_checks:result['geometry_model_passed']=all(geometry_checks)
        if checks:
            result['operational_model_passed']=all(c.get('passed') is True and c.get('complete') is True for c in checks)
            result['operational_rejection_count']=sum(len(c.get('errors',[])) for c in checks)
            result['operational_warehouse_count']=len(checks)
            # These action totals cover configured warehouses only. Other
            # warehouses are not silently assigned zero cold/handling work.
            result['operation_metrics_scope']='configured_operational_warehouses_only'
            if operation_routes:
                result['extra_handling_actions']=sum(r.get('time_adjustments',{}).get('rehandle_moves',0) for r in operation_routes)
                result['cold_box_openings']=sum(b['openings'] for r in operation_routes for b in r.get('cold_boxes',[]))
                result['extra_operation_minutes']=round(sum(r.get('time_adjustments',{}).get('extra_minutes',0) for r in operation_routes),4)
                result['latest_model_return_at']=max(r['return_at'] for r in adjusted) if result['declared_constraint_models_passed'] else None
                result['forecast_late_tasks']=sum(r['late_orders'] for r in adjusted)
                cold=[i for c in checks for i in c.get('coverage',[]) if i.get('key')=='cold_chain']
                result['cold_chain_model_passed']=all(i.get('status')=='VERIFIED_WITHIN_MODEL' for i in cold) if cold else None
        return result
    baseline,candidate=metrics('baseline'),metrics('optimized')
    complete=not plan['summary']['baseline']['unserved'] and not plan['summary']['optimized']['unserved']
    return dict(baseline=baseline,candidate=candidate,same_inputs=True,same_complete_task_set=complete,
        same_verified_constraint_set=baseline['declared_constraint_models_passed'] and candidate['declared_constraint_models_passed'],
        scope='same_declared_rules; simple_append_baseline_not_enterprise_plan',
        unknown_metrics=['实际人力耗时','实际货损','实际温度','正式结算金额'],
        interpretation='缺失指标为null，不按0收益或0风险计算；不以少送、少收或未通过约束换取低费用')


def loading_summary(task):
    """Report only declared/checked departure counts, never coordinates or a field-safety claim."""
    plan=mw.current_plan(task); rows=[]
    def count(value):
        return value if isinstance(value,int) and not isinstance(value,bool) and value>=0 else None
    for warehouse in task['data']['warehouses']:
        result=plan['warehouses'].get(warehouse['id'],{}) if plan else {}
        verification=result.get('loading_verification',{})
        configured='loading_profile' in warehouse or 'operational_policy' in warehouse
        if 'operational_policy' in warehouse and result:
            operational=result.get('operational_verification') or _operational_result(result['data'],warehouse,result['optimized'])
            route_reports=list(operational.get('routes',{}).values())
            total_boxes=sum(len(r.get('boxes',[])) for r in route_reports)
            verification=dict(status='VERIFIED_WITHIN_MODEL' if operational.get('passed') is True and operational.get('complete') is True else 'INCOMPLETE',
                boxes=total_boxes if operational.get('complete') else None,
                checked_boxes=sum(len(r.get('boxes',[])) for r in route_reports if r.get('passed') is True))
        status=verification.get('status','PENDING' if configured else 'NOT_CONFIGURED')
        rows.append(dict(warehouse_id=warehouse['id'],configured=configured,status=status,
            declared_boxes=count(verification.get('boxes')),checked_boxes=count(verification.get('checked_boxes'))))
    total=len(rows);configured=sum(r['configured'] for r in rows)
    verified=sum(r['configured'] and r['status']=='VERIFIED_WITHIN_MODEL' for r in rows)
    incomplete=sum(r['configured'] and r['status']=='INCOMPLETE' for r in rows)
    declared=sum(r['declared_boxes'] or 0 for r in rows)
    checked=sum(r['checked_boxes'] or 0 for r in rows)
    unknown=sum(r['configured'] and r['declared_boxes'] is None for r in rows)
    all_verified=bool(total and verified==total and not unknown and checked==declared)
    boundary=('声明模型含沿途回收几何；冷链隔离的现场验证、真实温度及作业安全仍未核验'
              if any('operational_policy' in w for w in task['data']['warehouses']) else
              '回收箱几何、冷链隔离和现场安全仍未核验')
    if all_verified:
        message=f'全部{total}个仓库所声明的出发配送箱已在有限模型内通过核验（{checked}/{declared}箱）；{boundary}'
    elif not configured:
        message=f'{total}个仓库均未配置出发箱规及车厢几何，当前未做物理装载核验；{boundary}'
    elif not plan or unknown:
        message=f'{total}个仓库中{configured}个已配置装载模型，尚有声明箱数或当前候选核验结果待计算；{boundary}'
    else:
        message=f'{total}个仓库中{configured}个已配置装载模型、{verified}个完整通过，已检查所声明出发配送箱{checked}/{declared}箱'
        if total-configured:message+=f'；另{total-configured}个仓库未配置装载模型'
        if incomplete:message+=f'；{incomplete}个仓库的路线/装载方案尚不完整'
        message+='；'+boundary
    return dict(warehouses=total,configured_warehouses=configured,verified_warehouses=verified,
        incomplete_warehouses=incomplete,not_configured_warehouses=total-configured,
        declared_boxes=declared,checked_boxes=checked,unknown_declared_warehouses=unknown,
        all_declared_departure_boxes_verified=all_verified,scope='declared_departure_delivery_cuboids_only',
        warehouse_counts=rows,message=message,boundary=boundary)


def evidence_ref(task):
    """Every read-only explanation is tied to an exact local input and execution snapshot."""
    plan = mw.current_plan(task)
    return dict(task_id=task['id'], task_revision=task['revision'], input_revision=task['input_revision'],
        input_sha256=app.fingerprint(task['data']), plan_id=plan['id'] if plan else None,
        execution_sha256=app.fingerprint(task.get('execution_tasks', {})))


def _operational_result(part, warehouse, plan):
    policy = warehouse.get('operational_policy')
    if policy is None:
        return dict(status='NOT_CONFIGURED', passed=None, missing=['operational_policy'],
            interpretation='未配置专项政策；基础路线通过不等于全部业务约束通过')
    try:
        import operational_constraints as constraints
        return constraints.evaluate_plan(part, plan, policy,
            compact=warehouse.get('planning_mode') == 'fleet',
            layout=constraints.FLEET_LAYOUT if warehouse.get('planning_mode') == 'fleet' else None)
    except ValueError as error:
        return dict(status='INVALID_OR_INCOMPLETE', passed=False, missing=[], errors=[str(error)])


def coverage_audit(task):
    """Coverage is evidence, not a single badge that silently certifies unmodelled rules."""
    plan = mw.current_plan(task); groups = mw.split(task['data']); rows = []
    for warehouse in task['data']['warehouses']:
        wid = warehouse['id']; part = groups[wid]
        result = plan['warehouses'][wid] if plan else None
        core_status = 'CHECKED_WITHIN_MODEL' if result else 'PENDING_CANDIDATE'
        physical = (result or {}).get('loading_verification', {})
        coverage = [dict(key='store_indivisibility', label='同店不拆、完整唯一归属', status=core_status),
            dict(key='weight_volume', label='出发及沿途重量/总体积', status=core_status),
            dict(key='time_windows', label='静态道路、窗口和回仓班次', status=core_status,
                 scope='arrival_or_finish_per_input; service time is declared, not measured'),
            dict(key='tariff', label='配置的运费规则', status=core_status,
                 scope='estimated_road_km_not_tms_settlement'),
            dict(key='departure_geometry', label='出发货物几何及装卸',
                 status=physical.get('status', 'PENDING' if 'loading_profile' in warehouse else 'NOT_CONFIGURED'))]
        operational = (result.get('operational_verification') or _operational_result(part, warehouse, result['optimized'])) if result else dict(
            status='PENDING_CANDIDATE' if 'operational_policy' in warehouse else 'NOT_CONFIGURED', passed=None)
        rows.append(dict(warehouse_id=wid, data_kind=part['settings']['data_kind'], coverage=coverage,
                         operational=operational))
    return dict(status='coverage_review', evidence_ref=evidence_ref(task), warehouses=rows,
        total_warehouses=len(rows), configured_operational_warehouses=sum('operational_policy' in w for w in task['data']['warehouses']),
        all_business_constraints_verified=False,
        unsupported=['真实温度监测与现场冷链合规认证', '企业TMS/GPS实绩与正式结算', '现场装卸安全及实际人工工时'],
        next_action='按仓核对专项政策及其缺项，生成候选后查看逐项结果；未配置不等于没有风险。')


def spatial_inspect(task, selection=None):
    """Return local scene data; provider-facing explain() strips boxes, routes and coordinates."""
    selection = selection or {}
    selected = agent_actions.selected_context(task, selection)
    plan = mw.current_plan(task)
    base = dict(evidence_ref=evidence_ref(task), selected={k: selected[k] for k in
                ('warehouse_id', 'vehicle_id', 'order_id') if k in selected}, read_only=True)
    if plan is None:
        return dict(base, status='needs_candidate', next_action='生成当前候选后查看三维装卸。')
    vehicles = task['data']['vehicles']; orders = task['data']['orders']
    vehicle = next((v for v in vehicles if v['id']==selection.get('vehicle_id')), None)
    order = next((o for o in orders if o['id']==selection.get('order_id')), None)
    wid = selection.get('warehouse_id') or (vehicle or order or {}).get('warehouse_id')
    if any(wid and row and row['warehouse_id'] != wid for row in (vehicle, order)):
        raise ValueError('选中的仓库、车辆和门店不属于同一仓，请重新选择')
    if order:
        assigned = [(w, vid) for w, group in plan['warehouses'].items()
                    for vid, r in group['optimized']['routes'].items() if order['id'] in r['sequence']]
        if not assigned:
            return dict(base, status='unassigned', next_action='该门店尚未安排，不能展示虚构装载。')
        assigned_wid, assigned_vid = assigned[0]
        if vehicle and vehicle['id'] != assigned_vid:
            raise ValueError('选中门店不在该车当前候选中，不会跨车猜测装载')
        wid = assigned_wid
        vehicle = next(v for v in vehicles if v['id']==assigned_vid)
    if vehicle is None:
        vehicle = next((v for v in vehicles if (not wid or v['warehouse_id']==wid)
                        and plan['warehouses'][v['warehouse_id']]['optimized']['routes'][v['id']]['sequence']), None)
    if vehicle is None:
        return dict(base, status='no_active_vehicle', next_action='所选仓库当前没有已安排配送的车辆。')
    wid = vehicle['warehouse_id']; vid=vehicle['id']
    warehouse = next(w for w in task['data']['warehouses'] if w['id']==wid)
    result = plan['warehouses'][wid]; route=result['optimized']['routes'][vid]
    base['selected'].update(warehouse_id=wid, vehicle_id=vid)
    profile=warehouse.get('loading_profile'); geometry=(route.get('loading') or {})
    scene = None
    if warehouse.get('operational_policy') is not None:
        scene=mw.spatial_view(task, wid, vid, plan['id'])['scene']
        if isinstance(scene, dict) and isinstance(scene.get('routes'), list):
            scene = {**scene, 'routes':[r for r in scene['routes'] if r.get('vehicle_id')==vid]}
    scene_route=next(iter((scene or {}).get('routes',[])),{})
    scene_boxes=scene_route.get('boxes') or geometry.get('placements',[])
    scene_actions=scene_route.get('actions') or []
    return dict(base, status='available' if scene or geometry.get('placements') else 'NOT_CONFIGURED',
        visual_context=dict(warehouse_id=wid, vehicle_id=vid, order_id=selection.get('order_id'),
            plan_id=plan['id'], evidence_ref=base['evidence_ref'], data_kind=result['data']['settings']['data_kind']),
        summary=dict(stops=len(route['sequence']), departure_boxes=len(scene_boxes),
            load_steps=sum(a.get('kind')=='load' for a in scene_actions) if scene_actions else len(geometry.get('load_steps', [])),
            unload_stops=len({a.get('stop_id') for a in scene_actions if a.get('kind') in ('unload','cold_open')}) if scene_actions else len(geometry.get('unload_steps', [])),
            timeline_frames=len(scene_route.get('frames',[])),
            physical_status=result.get('loading_verification', {}).get('status','NOT_CONFIGURED'),
            operational_status=(result.get('operational_verification') or
                _operational_result(mw.split(task['data'])[wid],warehouse,result['optimized'])).get('status')),
        local_scene=scene, departure_loading=deepcopy(geometry), loading_profile=deepcopy(profile),
        execution_present=mw.has_facts(task),
        boundary='候选装卸模型，不是现场测量；已有执行记录时不得把出发候选冒充当前车厢实况',
        next_action='在三维查看器核对选中车辆与门店；缺少箱规时补齐参数，不生成装载证明。')


def route_review(task, selection):
    plan = mw.current_plan(task)
    if not plan:
        return dict(status='needs_candidate', next_action='计算配送方案后可核对车次。')
    selection = selection or {}
    selected_vehicle = selection.get('vehicle_id')
    selected_order = selection.get('order_id')
    for warehouse_id, group in plan['warehouses'].items():
        if selection.get('warehouse_id') not in (None, warehouse_id):
            continue
        for vehicle_id, route in group['optimized']['routes'].items():
            if not route['sequence']:
                continue
            if selected_vehicle and selected_vehicle != vehicle_id:
                continue
            if selected_order and selected_order not in route['sequence']:
                continue
            if not (selected_vehicle or selected_order):
                continue
            operations = route.get('operational') or group.get('operational_verification', {}).get('routes', {}).get(vehicle_id) or {}
            timing = operations.get('adjusted_route') or route
            return dict(status='available', plan_id=plan['id'], warehouse_id=warehouse_id,
                vehicle_id=vehicle_id, order_id=selected_order, sequence=list(route['sequence']),
                distance_km=route['distance'], cost=route['cost'], fee=deepcopy(route.get('fee')),
                stops=[{k: s[k] for k in ('id', 'arrival', 'finish', 'pickup', 'onboard_weight_kg', 'onboard_volume_m3') if k in s}
                       for s in timing['stops']],
                return_at=timing['return_at'], late_orders=timing['late_orders'],
                operations_status=operations.get('status'),
                rehandle_moves=operations.get('time_adjustments', {}).get('rehandle_moves'),
                travel_source=group['data']['settings']['travel_source'])
    return dict(status='no_selected_route', next_action='在方案里选中一台出车车辆或已排门店，再核对车次。')


def risks(task):
    s = mw.view(task); p = mw.current_plan(task); items = []
    if p:
        for wid, result in p['warehouses'].items():
            for u in result['optimized']['unserved']:
                items.append(dict(kind='unserved', warehouse_id=wid, task_id=u['id'],
                    evidence='current_candidate', message=u['reason'], next_action=u.get('action')))
    for wid, execution in s['execution'].items():
        for vid, route in execution['routes'].items():
            temperature=route.get('temperature_summary', {})
            if temperature.get('open_excursions'):
                items.append(dict(kind='temperature_excursion',warehouse_id=wid,vehicle=vid,
                    evidence='local_temperature_records',message=f"{temperature['open_excursions']}条温度异常待处理",
                    next_action='核对货物，联系调度并在温度记录中补充处置。'))
            for warning in route['warnings']:
                items.append(dict(kind='execution_forecast', warehouse_id=wid, vehicle=vid,
                    evidence='local_records_and_static_travel', message=warning,
                    next_action='联系调度核对当前箱位和温控设备，记录处置。' if route.get('operational_policy_enabled')
                    else '联系调度核对实况，必要时计算剩余候选。'))
    gaps = [
        dict(kind='physical_loading', message='箱体尺寸、规格、堆叠和装卸通道未核验，不能保证现场装得下', next_action='补充箱规、车厢尺寸与现场装载反馈'),
        dict(kind='gps_billing', message='4.2米车GPS轨迹与实际行驶公里未接入，费用只能按输入道路估算', next_action='确认TMS计费公里来源、费用条款和GPS可用性'),
        dict(kind='live_road', message='未接实时路况或风险路段数据，不能提前声称已避险', next_action='对接可靠路况来源，当前仅能录入位置/等待并更新预测'),
        dict(kind='goods_returns', message='支持空包装预测与回收记录，商品退货未纳入', next_action='明确退货重量、体积、品类、接收仓与兼容规则'),
        dict(kind='labor_savings', message='人工10辆车排车约1—2小时、装载率90%以上来自用户所给材料，现场口径待复核', next_action='用同批订单记录人工分钟、调整次数与系统操作分钟，不预报节省比例'),
    ]
    physical=loading_summary(task)
    if any('operational_policy' in w for w in task['data']['warehouses']):
        gaps[3].update(message='专项模型可检查声明的密封常温退货箱；冷链退货、实际退货数量与隔离回执尚未覆盖',
            next_action='核对常温退货声明与密封隔离条件；冷链退货需独立温区、设备和实际回执模型')
    gaps[0].update(message=physical['message'],
        next_action='核对所声明箱规与装卸步骤，并补充冷链、回收箱形态和现场反馈' if physical['all_declared_departure_boxes_verified']
        else '逐仓补齐或核对装载模型、未安排任务和装卸步骤，再核对现场条件')
    coverage = coverage_audit(task)
    for warehouse in coverage['warehouses']:
        for error in warehouse['operational'].get('errors', []):
            message=error.get('message',str(error)) if isinstance(error,dict) else str(error)
            items.append(dict(kind='operational_constraint',warehouse_id=warehouse['warehouse_id'],
                evidence='declared_operational_model',message=message,
                next_action='核对专项参数和装卸步骤；未满足约束的候选不能确认。'))
    return dict(status='attention_required' if items else 'no_risk_found_within_available_inputs', items=items,
                missing_capabilities=gaps, physical_loading=physical,
                constraint_coverage=coverage,
                interpretation='没有发现输入范围内风险，不等于实际道路安全或履约保证')


def recovery_trial(task, selection, check_cancel):
    groups = mw.split(task['data'])
    wid = (selection or {}).get('warehouse_id')
    if not wid and len(groups) == 1:
        wid = next(iter(groups))
    if wid not in groups:
        raise ValueError('请先选择要做回收压力试算的仓库')
    data = groups[wid]
    if data['settings']['data_kind'] != 'simulated' or 'business' not in data or mw.has_facts(task):
        return dict(status='unavailable', explanation='回收算法试算需要已填写包装规则、尚未录入执行记录的模拟批次。')
    current = mw.current_plan(task)
    if current is None:
        return dict(status='unavailable', explanation='请计算当前方案，作为同车队、同货量的比较基线。')
    warehouse=next(w for w in task['data']['warehouses'] if w['id']==wid)
    if 'operational_policy' in warehouse:
        return operational_recovery_trial(data,current['warehouses'][wid]['optimized'],
            warehouse['operational_policy'],current['id'],check_cancel)
    profile = next(w.get('loading_profile') for w in task['data']['warehouses'] if w['id'] == wid)
    baseline_routes = {vid: list(route['sequence']) for vid, route in
                       current['warehouses'][wid]['optimized']['routes'].items()}
    baseline = recovery_search.evaluate_plan(data, baseline_routes, profile=profile)
    if baseline is None or baseline['metrics']['unserved'] or baseline['metrics']['late_orders']:
        return dict(status='unavailable', explanation='当前方案有未安排或迟到任务，暂不能作为完整比较基线。')
    scenarios = [{o['id']: {**o['pickup'], 'totes': min(100000, o['pickup']['totes']+2)}}
                 for o in data['orders'] if o['pickup']['totes'] < 100000]
    if not scenarios:
        return dict(status='unavailable', explanation='预计周转箱数量已达到模型上限，无法构造增加回收量的试算。')
    baseline = recovery_search.evaluate_plan(data, baseline_routes, profile=profile, scenarios=scenarios)
    summaries = {}
    for method in ('local', 'robust_objective_alns'):
        check_cancel()
        trial = recovery_search.solve(data, method=method, profile=profile, scenarios=scenarios,
            max_evaluations=2500, time_limit_seconds=2,
            fixed_fleet_baseline=baseline_routes, max_load_drop=.02)
        summaries[method] = dict(trial['metrics'], elapsed_ms=trial['elapsed_ms'],
                                 budget_reached=trial['budget_reached'])
    return dict(status='simulated', scenario_count=len(scenarios), methods=summaries,
                baseline=dict(baseline['metrics']), baseline_plan_id=current['id'],
                guardrails=dict(fixed_active_fleet=True, max_load_drop_percentage_points=2,
                                nominal_fare_increase_allowed=False),
                validation_scope='training_scenarios_only', recommendation='retain_current_plan',
                geometry_scope='outbound_only' if profile else 'none')


def operational_recovery_trial(data, plan, policy, plan_id, check_cancel):
    """Replay declared cold/geometry rules under pickup changes; no old ALNS bypass."""
    import operational_constraints as constraints
    import pilot
    original=constraints.evaluate_plan(data,plan,policy)
    if original.get('passed') is not True or original.get('complete') is not True:
        return dict(status='unavailable',explanation='当前专项方案不完整或声明约束未通过，不能作为压力试算基线。')
    results=[]
    for order in data['orders']:
        check_cancel()
        if order['pickup']['totes']>=100000:continue
        scenario=deepcopy(data);target=next(o for o in scenario['orders'] if o['id']==order['id'])
        target['pickup']['totes']+=2
        routes={};evaluator=pilot.evaluator(scenario);orders={o['id']:o for o in scenario['orders']}
        for vehicle in scenario['vehicles']:
            route=evaluator(plan['routes'][vehicle['id']]['sequence'],vehicle,orders)
            if route is None:break
            routes[vehicle['id']]=route
        if len(routes)!=len(scenario['vehicles']):
            results.append(dict(order_id=order['id'],extra_totes=2,passed=False,
                status='REJECTED',errors=[dict(code='base_capacity_or_time',message='变化后重量、总体积或基础时间约束未通过')]))
            continue
        report=constraints.evaluate_plan(scenario,dict(routes=routes,unserved=[]),policy)
        results.append(dict(order_id=order['id'],extra_totes=2,passed=report['passed'] and report['complete'],
            status=report['status'],errors=deepcopy(report.get('errors',[])),
            extra_operation_minutes=round(sum(r.get('time_adjustments',{}).get('extra_minutes',0) for r in report['routes'].values()),4)))
    return dict(status='operational_sensitivity',scenario_count=len(results),
        passed_scenarios=sum(r['passed'] for r in results),scenarios=results,baseline_plan_id=plan_id,
        fixed_vehicle_sequences=True,creates_candidate=False,recommendation='retain_current_plan',
        validation_scope='declared_cold_chain_and_along_route_spatial_replay',
        boundary='每店分别增加2只周转箱；只做当前方案敏感性回放，不是ALNS搜索、独立留出实验或实际回收预测')


def report(outputs):
    lines = []
    labels={'NOT_CONFIGURED':'未配置','NOT_MODELED':'未建模','REJECTED':'未通过',
        'VERIFIED_WITHIN_DECLARED_MODEL':'已通过所设规则','VERIFIED_WITHIN_MODEL':'已通过所设规则',
        'PENDING_CANDIDATE':'待计算','INVALID_OR_INCOMPLETE':'参数不完整'}
    for name, result in outputs:
        if name == 'inspect':
            if len(outputs) == 1:
                lines.append(f"已核对 {result['orders']} 个配送任务、{result['vehicles']} 辆车的必填信息与归属。")
        elif name == 'compare':
            if result['status'] == 'needs_candidate': lines.append(result['next_action']); continue
            m = result['candidate']
            lines.append(f"当前候选安排{m['served']}个任务，{m['unserved']}个未安排；使用{m['trips']}趟，费用试算{m['cost']:g}元。")
            lines.append(f"出发载重利用率{m['weight_utilization']:.1%}，容积利用率{m['volume_utilization']:.1%}。装卸结果见三维页面。")
            delta = result['delta_cost_vs_simple_baseline']
            if delta == 0:
                lines.append('两份方案试算同价，可继续比较时效和装卸。')
            elif delta is not None:
                lines.append(f"比规则方案{'少' if delta<0 else '多'} {abs(delta):g} 元。")
            else: lines.append(result.get('comparison_limit') or '基线与候选未全部完成同一任务集合，不给出节省率。')
            lines.append(result['next_action'])
        elif name == 'risks':
            lines.append(f"发现{len(result['items'])}项需要处理的提醒。" if result['items'] else '已提供的信息中未发现异常。')
            lines.extend(i.get('vehicle',i.get('task_id',i.get('warehouse_id','')))+'：'+i['message'] for i in result['items'][:3])
            if len(result['items'])>3:lines.append('其余提醒见执行记录和约束覆盖。')
            lines.append('待复核项目与未接入信息见约束覆盖。')
        elif name == 'coverage_audit':
            lines.append(f"已检查{result['total_warehouses']}个仓库，其中{result['configured_operational_warehouses']}个已配置冷链与沿途空间规则。")
            for warehouse in result['warehouses']:
                operational=warehouse['operational']
                lines.append(warehouse['warehouse_id']+'：'+labels.get(operational['status'],operational['status'])+'。')
                for item in operational.get('coverage',[]):
                    if item.get('status') != 'VERIFIED_WITHIN_MODEL':
                        lines.append(item.get('label',item.get('key','约束'))+'：'+labels.get(item['status'],item['status']))
            lines.append('此处检查所设规则，现场温度与作业安全尚未验证。')
        elif name == 'spatial_inspect':
            if result['status'] not in ('available','NOT_CONFIGURED'):
                lines.append(result['next_action']); continue
            s=result['summary']; selected=result['selected']
            lines.append(f"{selected['vehicle_id']}安排{s['stops']}站，出发时有{s['departure_boxes']}个箱体。")
            lines.append('打开三维装卸可查看每站变化。画面是计划推演，不是车内实况。' if result['status']=='available' else '缺少箱规或车厢尺寸，暂时无法展示布局。')
        elif name == 'route_review':
            if result['status'] != 'available':
                lines.append(result['next_action']); continue
            lines.append(f"{result['vehicle_id']} 安排 {len(result['sequence'])} 站，含返仓 {result['distance_km']:g} 公里，运费试算 {result['cost']:g} 元。")
            if result['fee']:
                fee = result['fee']
                lines.append(f"其中起步 {fee['base']:g} 元、里程加价 {fee['distance']:g} 元、门店服务 {fee['stops']:g} 元。")
            if result['rehandle_moves'] is not None:
                lines.append(f"沿途空间已逐站核对，装卸计划含 {result['rehandle_moves']} 次搬倒动作。")
        elif name == 'remaining':
            lines.extend(w+'：'+r for w,r in result.items())
        elif name == 'recovery_trial':
            if result['status'] == 'unavailable':
                lines.append(result['explanation'])
                continue
            if result['status'] == 'operational_sensitivity':
                lines.append(f"保持当前车辆和路线，分别给每店增加2只周转箱；{result['scenario_count']}次试算中，{result['passed_scenarios']}次通过冷链、空间和时间检查。")
                lines.extend(r['order_id']+'：'+('声明模型内通过' if r['passed'] else '未通过：'+'；'.join(str(e.get('message',e)) if isinstance(e,dict) else str(e) for e in r['errors'][:2])) for r in result['scenarios'])
                lines.append('这是回收量变化的试算，未修改当前方案。')
                continue
            lines.append(f"已完成{result['scenario_count']}次增加周转箱的压力试算。")
            base = result['baseline']
            lines.append(f"当前方案通过 {base['scenario_full_completions']}/{result['scenario_count']} 个情景。固定原车队，逐车装载降幅不超过2个百分点，名义运费不增。")
            for key, label in (('local', '局部修复'), ('robust_objective_alns', '情景评分 ALNS')):
                m = result['methods'][key]
                lines.append(f"{label}安排 {m['served']} 个任务，预测迟到 {m['late_orders']} 个，试算运费 {m['cost']:g} 元。全批通过 {m['scenario_full_completions']}/{result['scenario_count']} 个压力情景，搜索耗时 {m['elapsed_ms']:g} 毫秒。")
                lines.append(f"使用 {m['used_vehicles']} 趟，已用车辆平均出发载重利用率 {m['average_weight_utilization']:.1%}，容积利用率 {m['average_volume_utilization']:.1%}。")
            lines.append('保留当前方案。此项按重量和总体积试算，不含回收物的三维摆放。')
        elif name == 'adjustment':
            lines.append(f"调整候选已核验：费用试算{result['before']['cost']:g} → {result['after']['cost']:g}元，须人工确认。")
        elif name == 'rule_draft':
            lines.append(f"已生成{len(result['changes'])}项规则草稿，当前输入尚未改变。请核对并应用，再重新计算。")
    return '\n\n'.join(lines)


def grounding_issue(answer,task):
    """Finite protection against promoting a plan into recorded execution; not a general NLP fact checker."""
    signatures=[r for child in task.get('execution_tasks',{}).values() for r in child.get('receipts',[]) if r.get('kind')=='stop']
    confirmed=mw.view(task)['confirmed']
    phrases=['已派单','已经派单','已下发派车','已完成企业派发',
             '冷链已合规','冷链已经合规','温控已达标','温度实测合格','全部业务约束已通过','全部业务约束均已通过']
    if not confirmed:phrases+=['已确认方案','方案已确认']
    if not signatures:phrases+=['已签收','已经签收','已送达','已经送达','已完成配送']
    if len(signatures)<len(task['data']['orders']):phrases+=['全部送达','全部已送达','均已送达','全部完成交付','全部签收','全部已签收','均已签收']
    for phrase in phrases:
        for match in re.finditer(re.escape(phrase),answer):
            prefix=answer[max(0,match.start()-22):match.start()]
            # Negation is recognized only within this punctuation-delimited local clause.
            if re.search(r'(?:未|没有|尚无|尚未|并非|不是|不等于|不代表|不能|不应|无需|无)[^，。；！？\n]{0,14}$',prefix):continue
            return phrase
    return None


def numerical_issue(answer, outputs):
    """Reject quantitative claims absent from tool evidence, including unit conversions."""
    from math import isfinite
    values = set()
    def add(number):
        if not isfinite(number):
            return
        for digits in (0, 1, 2, 4, 6):
            values.add(round(number, digits))
    def visit(value, key=''):
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            add(value)
            if 'utilization' in key:
                add(value * 100)
        elif isinstance(value, dict):
            for name, item in value.items():
                visit(item, name)
        elif isinstance(value, (list, tuple)):
            add(len(value))
            for item in value:
                visit(item, key)
    for _, output in outputs:
        visit(output)
    # Counts in the deterministic report are derived from tool arrays.
    for match in re.finditer(r'(?<![\d.])(-?\d+(?:\.\d+)?)\s*(?:元|公里|千米|分钟|次|趟|站|辆|%|％|kg|m³|km)', answer):
        number = float(match[1])
        if not any(abs(number - candidate) <= .0001 for candidate in values):
            return match[0]
    return None


class AgentWorkflow:
    def __init__(self, store, planner=None, *, emit=None, check_cancel=None, before_commit=None, selection=None, operation_id=None):
        self.store = store
        # Server-owned injection only; never a client URL, executable, or key.
        self.planner = planner
        self.emit = emit or (lambda kind, **kw: None)
        self.check_cancel = check_cancel or (lambda: None)
        self.before_commit = before_commit or self.check_cancel
        self.selection = selection
        self.operation_id = operation_id

    def run(self, task_id, revision, workflow, message=None):
        self.check_cancel()
        task = self.store.read(task_id); self.store.guard(task, revision)
        local_request = bool(getattr(self.planner, 'is_local', False))
        if workflow == 'request':
            if self.planner is None: raise ValueError('平台Agent模型尚未连接；请先使用下方本地工作流。待提供接口协议后再启用自主编排。')
            if not isinstance(message, str) or not 1 <= len(message.strip()) <= 2000: raise ValueError('请求需要1—2000字')
            secret = getattr(self.planner,'key',None)
            if secret: message=message.replace(secret,'[密钥已隐藏]')
            if hasattr(self.planner, 'history'):
                recent = [r for r in task.get('agent_runs', [])
                          if r.get('user_message') and not (r.get('chat_deleted') or r.get('chat_archived'))]
                self.planner.history = [entry for r in recent[-3:]
                    for entry in (dict(role='user', content=r['user_message']), dict(role='assistant', content=r['message'][:3000]))]
            self.emit('intent_started' if local_request else 'model_started',title='读取当前批次')
            proposed = self.planner.plan(context=dict(task_id=task_id, revision=revision, summary=inspect(task),
                                         display_aliases=display_aliases(task),
                                         confirmed=mw.view(task)['confirmed'], has_candidate=bool(mw.current_plan(task)),
                                         selected=agent_actions.selected_context(task,self.selection)),
                                         message=message, tools=deepcopy(TOOLS))
            self.check_cancel()
            self.emit('intent_completed' if local_request else 'model_completed',title='已找到对应操作',details=dict(workflows=proposed))
            if not isinstance(proposed, list) or len(proposed) > 4 or any(not isinstance(w,str) or w not in TOOLS for w in proposed) or len(set(proposed)) != len(proposed):
                raise ValueError('模型返回的工作流不在工具白名单内，未执行')
            workflows = proposed
            if getattr(self.planner, 'disposition', None) == 'unsupported':
                if workflows: raise ValueError('能力范围外的请求不能执行工作流')
                answer = getattr(self.planner, 'last_answer', '')
                if secret: answer = answer.replace(secret, '[密钥已隐藏]')
                if not local_request and (grounding_issue(answer,task) or numerical_issue(answer,[])):
                    answer = '可以先核对当前批次的方案、费用或回收量。选中车辆后可查看具体依据。'
                return task, dict(ephemeral=True, message=answer)
        else:
            if workflow not in TOOLS: raise ValueError('请选择受支持的本地工作流')
            workflows = [workflow]
        if sum(not r.get('chat_deleted') for r in task.get('agent_runs', [])) >= 100:
            raise ValueError('本批次已有100次工作流记录，请删除旧对话或另建批次')
        if sum(w in ('dispatch','remaining','adjust','rules') for w in workflows) > 1:
            raise ValueError('单次只允许生成一类候选，请分步核对')
        if 'recovery_trial' in workflows and len(workflows) != 1:
            raise ValueError('回收算法压力试算须单独运行')
        started = perf_counter(); traces = []; outputs = []
        def call(name, function):
            self.check_cancel(); self.emit('tool_started',title=TOOL_LABELS.get(name,name),details=dict(tool=name))
            begin = perf_counter()
            try: result = function()
            except (ValueError,Conflict) as error:
                self.emit('tool_failed',title=TOOL_LABELS.get(name,name),details=dict(tool=name,error=str(error)))
                raise
            self.check_cancel()
            traces.append(dict(tool=name, status='completed', duration_ms=round((perf_counter()-begin)*1000, 3)))
            self.emit('tool_completed',title=TOOL_LABELS.get(name,name),details=dict(tool=name,duration_ms=traces[-1]['duration_ms']))
            return result
        checked = call('inspect_current_batch', lambda: inspect(task)); outputs.append(('inspect', checked))
        base = dict(task_revision=revision, input_revision=task['input_revision'], input_sha256=app.fingerprint(task['data']),
                    plan_id=task.get('adopted_plan_id'), execution_sha256=app.fingerprint(task.get('execution_tasks', {})))
        for w in workflows:
            if w == 'dispatch':
                call('calculate_warehouse_candidates', lambda: self.store.calculate(task))
                outputs.append(('compare', call('audit_and_compare', lambda: compare(task))))
            elif w == 'compare': outputs.append(('compare', call('audit_and_compare', lambda: compare(task))))
            elif w == 'risks': outputs.append(('risks', call('inspect_business_risks', lambda: risks(task))))
            elif w == 'coverage_audit':
                outputs.append(('coverage_audit', call('audit_constraint_coverage', lambda: coverage_audit(task))))
            elif w == 'spatial_inspect':
                outputs.append(('spatial_inspect', call('inspect_vehicle_space', lambda: spatial_inspect(task, self.selection))))
            elif w == 'route_review':
                outputs.append(('route_review', call('review_vehicle_route', lambda: route_review(task, self.selection))))
            elif w == 'recovery_trial':
                outputs.append(('recovery_trial', call('run_recovery_trial',
                    lambda: recovery_trial(task, self.selection, self.check_cancel))))
            elif w == 'adjust':
                outputs.append(('adjustment',call('validate_route_adjustment',lambda:agent_actions.reorder(task,getattr(self.planner,'actions',[])))))
                outputs.append(('compare',call('audit_and_compare',lambda:compare(task))))
            elif w == 'rules':
                outputs.append(('rule_draft',call('prepare_rule_draft',lambda:agent_actions.rule_draft(task,getattr(self.planner,'actions',[])))))
            elif w == 'remaining':
                if not mw.view(task)['confirmed']: raise Conflict('先确认当前方案，再处理执行中的剩余路线')
                updates = {}
                for wid, child in task['execution_tasks'].items():
                    if not (child.get('receipts') or child.get('observations')):
                        updates[wid] = '尚无执行变化，保留原路线。'; continue
                    if mw.execution.view(child)['complete']:
                        updates[wid] = '已记录返仓交接，无剩余任务。'; continue
                    try:
                        candidate = call('calculate_remaining_'+wid, lambda c=child: self.store.calculate_remaining(c))
                        updates[wid] = '已生成同车剩余顺序候选，须人工核对后采用。候选 '+candidate['id'][:8]
                    except ValueError as error:
                        traces.append(dict(tool='calculate_remaining_'+wid, status='blocked', reason=str(error)))
                        updates[wid] = '未生成可行候选：'+str(error)
                outputs.append(('remaining', updates))
        answer = report(outputs)
        model_explanation = False
        if workflow == 'request':
            # The explanation must describe the state after tool execution, not the pre-dispatch snapshot.
            outputs = [(name, inspect(task) if name == 'inspect' else value) for name, value in outputs]
            answer = getattr(self.planner, 'last_answer', '') if not workflows else answer
            model_explanation = not workflows and not local_request
            if workflows and 'recovery_trial' not in workflows and hasattr(self.planner, 'explain'):
                try:
                    self.check_cancel(); self.emit('model_started',title='整理核验结果')
                    answer = self.planner.explain(message=message, outputs=outputs, fallback=answer)
                    model_explanation = True
                    self.emit('model_completed',title='模型解释草稿已生成')
                except ValueError:
                    traces.append(dict(tool='model_explanation', status='unavailable', reason='工具已完成；模型解释暂时不可用，显示本机核算结果'))
            if model_explanation and (grounding_issue(answer,task) or numerical_issue(answer,outputs)):
                answer=report(outputs)
                traces.append(dict(tool='model_explanation',status='grounding_rejected',
                    reason='模型解释包含未记录的事实或缺少工具依据的数字，已改为本机核算说明'))
                self.emit('grounding_rejected',title='解释已按业务事实纠正',
                    details=dict(reason='模型草稿存在未记录的履约声明，最终显示本机核算结果'))
            # Never persist or echo the active credential, even if it appears in user/model text.
            secret = getattr(self.planner, 'key', None)
            if secret:
                answer = answer.replace(secret, '[密钥已隐藏]')
                message = message.replace(secret, '[密钥已隐藏]')
        model_used = workflow == 'request' and not local_request
        result = dict(id=uuid4().hex, created_at=app.now(), runtime='platform_planner' if model_used else 'platform_local_workflow',
            model_connected=model_used, requested_workflow=workflow, workflows=workflows,
            operation_id=self.operation_id,
            input_ref=base, resulting_revision=revision+1, traces=traces,
            result={k:v for k,v in outputs}, message=answer, user_message=message if workflow == 'request' else None,
            model_calls=deepcopy(getattr(self.planner, 'calls', [])) if workflow == 'request' else [],
            duration_ms=round((perf_counter()-started)*1000, 3),
            boundary='未自动确认、写入履约事实或向企业派单；本地运行耗时不是人工节省时间')
        task.setdefault('agent_runs', []).append(result)
        self.before_commit()
        return self.store.commit(task, revision), result


TOOL_LABELS = dict(inspect_current_batch='检查订单与车辆',calculate_warehouse_candidates='计算各仓配送方案',
    audit_and_compare='核验方案并比较费用',inspect_business_risks='检查容量与履约风险',
    validate_route_adjustment='核验车辆与顺序调整',prepare_rule_draft='整理待确认规则草稿',
    run_recovery_trial='比较局部修复与情景 ALNS',audit_constraint_coverage='逐项检查约束覆盖',
    inspect_vehicle_space='查看车厢与装卸空间',review_vehicle_route='复核所选车次的路线与费用')
