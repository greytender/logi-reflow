"""Platform-side tool workflow. No external model or credentials configured.

A future server-owned Planner implements plan(context, message, tools). It may
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


def inspect(task):
    mw.canonicalize(task['data'])
    s = mw.view(task)
    return dict(passed=True, warehouses=len(task['data']['warehouses']), orders=len(task['data']['orders']),
                vehicles=len(task['data']['vehicles']), status=s['status'], has_execution_facts=mw.has_facts(task),
                data_sources={w['id']: w['settings']['data_kind'] for w in task['data']['warehouses']})


def compare(task):
    p = mw.current_plan(task)
    if not p: return dict(status='needs_candidate', next_action='准备数据后计算各仓候选。')
    checked = mw.audit(task['data'], p['warehouses'])
    b, m = p['summary']['baseline'], p['summary']['optimized']
    comparable = not b['unserved'] and not m['unserved'] and b['served'] == m['served']
    return dict(status='review_required', plan_id=p['id'], audit=checked, baseline=deepcopy(b), candidate=deepcopy(m),
        same_complete_task_set=comparable, delta_cost_vs_simple_baseline=round(m['cost']-b['cost'], 4) if comparable else None,
        physical_loading=loading_summary(task),
        interpretation='仅重量/容积利用率；基线为简单追加，非企业人工计划，非人力节省证明',
        next_action='先处理未安排任务。' if m['unserved'] else '调度员核对路线与规则后，手动确认整个批次。')


def loading_summary(task):
    """Report only declared/checked departure counts, never coordinates or a field-safety claim."""
    plan=mw.current_plan(task); rows=[]
    def count(value):
        return value if isinstance(value,int) and not isinstance(value,bool) and value>=0 else None
    for warehouse in task['data']['warehouses']:
        result=plan['warehouses'].get(warehouse['id'],{}) if plan else {}
        verification=result.get('loading_verification',{})
        configured='loading_profile' in warehouse
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
    boundary='回收箱几何、冷链隔离和现场安全仍未核验'
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


def risks(task):
    s = mw.view(task); p = mw.current_plan(task); items = []
    if p:
        for wid, result in p['warehouses'].items():
            for u in result['optimized']['unserved']:
                items.append(dict(kind='unserved', warehouse_id=wid, task_id=u['id'],
                    evidence='current_candidate', message=u['reason'], next_action=u.get('action')))
    for wid, execution in s['execution'].items():
        for vid, route in execution['routes'].items():
            for warning in route['warnings']:
                items.append(dict(kind='execution_forecast', warehouse_id=wid, vehicle=vid,
                    evidence='local_records_and_static_travel', message=warning,
                    next_action='联系调度核对实况；按确认后的业务规则处理，必要时计算剩余候选。'))
    gaps = [
        dict(kind='physical_loading', message='箱体尺寸、规格、堆叠和装卸通道未核验，不能保证现场装得下', next_action='补充箱规、车厢尺寸与现场装载反馈'),
        dict(kind='gps_billing', message='4.2米车GPS轨迹与实际行驶公里未接入，费用只能按输入道路估算', next_action='确认TMS计费公里来源、费用条款和GPS可用性'),
        dict(kind='live_road', message='未接实时路况或风险路段数据，不能提前声称已避险', next_action='对接可靠路况来源，当前仅能录入位置/等待并更新预测'),
        dict(kind='goods_returns', message='支持空包装预测与回收记录，商品退货未纳入', next_action='明确退货重量、体积、品类、接收仓与兼容规则'),
        dict(kind='labor_savings', message='人工10辆车排车约1—2小时、装载率90%以上来自用户所给材料，现场口径待复核', next_action='用同批订单记录人工分钟、调整次数与系统操作分钟，不预报节省比例'),
    ]
    physical=loading_summary(task)
    gaps[0].update(message=physical['message'],
        next_action='核对所声明箱规与装卸步骤，并补充冷链、回收箱形态和现场反馈' if physical['all_declared_departure_boxes_verified']
        else '逐仓补齐或核对装载模型、未安排任务和装卸步骤，再核对现场条件')
    return dict(status='attention_required' if items else 'no_risk_found_within_available_inputs', items=items,
                missing_capabilities=gaps, physical_loading=physical,
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


def report(outputs):
    lines = []
    for name, result in outputs:
        if name == 'inspect':
            lines.append(f"已检查 {result['warehouses']}仓、{result['orders']}个配送任务、{result['vehicles']}辆车。归属与必需字段校验通过；真实性仍需业务核对。")
        elif name == 'compare':
            if result['status'] == 'needs_candidate': lines.append(result['next_action']); continue
            m = result['candidate']
            lines.append(f"当前候选安排{m['served']}个任务，{m['unserved']}个未安排；使用{m['trips']}趟，费用试算{m['cost']:g}元。")
            lines.append(f"出发载重利用率{m['weight_utilization']:.1%}、容积利用率{m['volume_utilization']:.1%}，按已用车辆合计容量计算。总体积不证明物理装载。")
            delta = result['delta_cost_vs_simple_baseline']
            if delta is not None:
                lines.append(f"同一任务集合下，比简单追加基线{'少' if delta<=0 else '多'}{abs(delta):g}元。此基线不是企业人工计划。")
            else: lines.append('基线与候选未全部完成同一任务集合，不给出节省率。')
            lines.append(result['next_action'])
        elif name == 'risks':
            lines.append(f"输入与本机记录范围内发现{len(result['items'])}项风险。")
            lines.extend(i['warehouse_id']+'：'+i['message'] for i in result['items'])
            lines.append('仍需补齐的业务能力：')
            lines.extend('• '+i['message']+'；下一步：'+i['next_action']+'。' for i in result['missing_capabilities'])
        elif name == 'remaining':
            lines.extend(w+'：'+r for w,r in result.items())
        elif name == 'recovery_trial':
            if result['status'] == 'unavailable':
                lines.append(result['explanation'])
                continue
            lines.append(f"已完成 {result['scenario_count']} 个单店周转箱增加最多 2 只的构造情景。")
            base = result['baseline']
            lines.append(f"当前方案通过 {base['scenario_full_completions']}/{result['scenario_count']} 个情景。固定原车队，逐车装载降幅不超过2个百分点，名义运费不增。")
            for key, label in (('local', '局部修复'), ('robust_objective_alns', '情景评分 ALNS')):
                m = result['methods'][key]
                lines.append(f"{label}安排 {m['served']} 个任务，预测迟到 {m['late_orders']} 个，试算运费 {m['cost']:g} 元。全批通过 {m['scenario_full_completions']}/{result['scenario_count']} 个压力情景，搜索耗时 {m['elapsed_ms']:g} 毫秒。")
                lines.append(f"使用 {m['used_vehicles']} 趟，已用车辆平均出发载重利用率 {m['average_weight_utilization']:.1%}，容积利用率 {m['average_volume_utilization']:.1%}。")
            lines.append('以上是求解所用情景内的表现，未做独立验收，保留当前方案。回收按重量和体积检查；耗时仅计本机搜索。')
        elif name == 'adjustment':
            lines.append(f"调整候选已核验：费用试算{result['before']['cost']:g} → {result['after']['cost']:g}元，须人工确认。")
        elif name == 'rule_draft':
            lines.append(f"已生成{len(result['changes'])}项规则草稿，当前输入尚未改变。请核对并应用，再重新计算。")
    return '\n\n'.join(lines)


def grounding_issue(answer,task):
    """Finite protection against promoting a plan into recorded execution; not a general NLP fact checker."""
    signatures=[r for child in task.get('execution_tasks',{}).values() for r in child.get('receipts',[]) if r.get('kind')=='stop']
    confirmed=mw.view(task)['confirmed']
    phrases=['已派单','已经派单','已下发派车','已完成企业派发']
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
            self.emit('model_started',title='理解这次配送请求')
            proposed = self.planner.plan(context=dict(task_id=task_id, revision=revision, summary=inspect(task),
                                         confirmed=mw.view(task)['confirmed'], has_candidate=bool(mw.current_plan(task)),
                                         selected=agent_actions.selected_context(task,self.selection)),
                                         message=message, tools=deepcopy(TOOLS))
            self.check_cancel()
            self.emit('model_completed',title='已理解请求',details=dict(workflows=proposed))
            if not isinstance(proposed, list) or len(proposed) > 4 or any(not isinstance(w,str) or w not in TOOLS for w in proposed) or len(set(proposed)) != len(proposed):
                raise ValueError('模型返回的工作流不在工具白名单内，未执行')
            workflows = proposed
            if getattr(self.planner, 'disposition', None) == 'unsupported':
                if workflows: raise ValueError('能力范围外的请求不能执行工作流')
                answer = getattr(self.planner, 'last_answer', '')
                if secret: answer = answer.replace(secret, '[密钥已隐藏]')
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
        if workflow == 'request':
            # The explanation must describe the state after tool execution, not the pre-dispatch snapshot.
            outputs = [(name, inspect(task) if name == 'inspect' else value) for name, value in outputs]
            answer = getattr(self.planner, 'last_answer', '') if not workflows else answer
            if workflows and 'recovery_trial' not in workflows and hasattr(self.planner, 'explain'):
                try:
                    self.check_cancel(); self.emit('model_started',title='整理核验结果')
                    answer = self.planner.explain(message=message, outputs=outputs, fallback=answer)
                    self.emit('model_completed',title='模型解释草稿已生成')
                except ValueError:
                    traces.append(dict(tool='model_explanation', status='unavailable', reason='工具已完成；模型解释暂时不可用，显示本机核算结果'))
            if grounding_issue(answer,task):
                answer=report(outputs)
                traces.append(dict(tool='model_explanation',status='grounding_rejected',
                    reason='模型解释把计划提升为未记录的履约事实，已改为本机核算说明'))
                self.emit('grounding_rejected',title='解释已按业务事实纠正',
                    details=dict(reason='模型草稿存在未记录的履约声明，最终显示本机核算结果'))
            # Never persist or echo the active credential, even if it appears in user/model text.
            secret = getattr(self.planner, 'key', None)
            if secret:
                answer = answer.replace(secret, '[密钥已隐藏]')
                message = message.replace(secret, '[密钥已隐藏]')
        result = dict(id=uuid4().hex, created_at=app.now(), runtime='platform_local_workflow' if workflow != 'request' else 'platform_planner',
            model_connected=workflow == 'request', requested_workflow=workflow, workflows=workflows,
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
    run_recovery_trial='比较局部修复与情景 ALNS')
