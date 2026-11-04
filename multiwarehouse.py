"""Atomic multi-depot batches over the existing audited single-depot solver.

Warehouse allocation is supplied by the operator. No inventory, transfers,
shared vehicles, multi-trip scheduling, or enterprise dispatch is inferred.
"""
from copy import deepcopy
import csv
import io
import json
from uuid import uuid4

import application as app
import execution
import observation
import pilot
import remaining
import warehouse_loading
from store import Conflict

SCHEMA = 'logireflow.multiwarehouse.v1'
MAX_WAREHOUSES = 8
BOUNDARIES = [
    '订单与车辆按明确仓库归属分组；不自动分仓、调拨、跨仓借车或安排多趟',
    '各仓独立启发式排线，统一核验与汇总；不声称多仓全局最优',
    '每仓最多12个配送任务、4辆车，最多8仓；超限拒绝，不截取订单',
    '道路公里为输入估计；可选声明箱规的有限几何核验，非GPS实绩、现场装载证明或正式结算',
    '本机确认及人工回执不等于企业派单或已验证真实履约',
]


def canonicalize(raw):
    if not isinstance(raw, dict) or raw.get('schema_version') != SCHEMA:
        raise ValueError('请使用多仓任务模板（schema_version不匹配）')
    if set(raw) != {'schema_version', 'warehouses', 'orders', 'vehicles', 'legs'}:
        raise ValueError('多仓输入仅接受schema_version/warehouses/orders/vehicles/legs；额外业务字段需先明确映射，不会静默丢弃')
    warehouses = raw.get('warehouses')
    if not isinstance(warehouses, list) or not 1 <= len(warehouses) <= MAX_WAREHOUSES:
        raise ValueError('请填写1—8个仓库')
    if any(not isinstance(w, dict) for w in warehouses):
        raise ValueError('仓库格式无效')
    if any(set(w)-{'id', 'name', 'settings', 'business', 'loading_profile'} for w in warehouses):
        raise ValueError('仓库含未映射字段，请先明确业务含义，不会静默丢弃')
    ids = [pilot.identifier(w.get('id'), '仓库编号') for w in warehouses]
    if len(ids) != len(set(ids)):
        raise ValueError('仓库编号重复')
    for field in ('orders', 'vehicles'):
        rows = raw.get(field)
        if not isinstance(rows, list) or not rows or len(rows) > MAX_WAREHOUSES * (12 if field == 'orders' else 4):
            raise ValueError('订单或车辆为空或超过多仓上限；不会截取数据')
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('订单或车辆格式无效')
            allowed = {'id', 'warehouse_id', 'open', 'due', 'close', 'service_minutes', 'allowed_vehicles', 'lines',
                       'quantity', 'unit_weight_kg', 'unit_volume_m3', 'pickup'} if field == 'orders' else {
                       'id', 'warehouse_id', 'max_weight_kg', 'max_volume_m3', 'fixed_cost', 'cost_per_km', 'shift_start', 'shift_end'}
            if set(row)-allowed:
                raise ValueError('订单或车辆含未映射字段，请先明确字段映射，不会静默丢弃')
            if field == 'orders' and isinstance(row.get('lines'), list):
                if any(isinstance(line, dict) and set(line)-{'sku','quantity','unit_weight_kg','unit_volume_m3'} for line in row['lines']):
                    raise ValueError('货物行含未映射字段，请先核对，不会静默丢弃')
            rid = pilot.identifier(row.get('id'), '配送任务/车辆编号')
            if rid in seen:
                raise ValueError('配送任务/车辆编号跨仓重复，不能重复派车或漏算订单：'+rid)
            if rid in ids:
                raise ValueError('配送任务/车辆编号不能等于仓库编号：'+rid)
            seen.add(rid)
            if row.get('warehouse_id') not in ids:
                raise ValueError(rid+' 缺少有效仓库归属，请明确后重试')
    if not isinstance(raw.get('legs'), dict):
        raise ValueError('缺少有向道路数据')
    result = dict(schema_version=SCHEMA, warehouses=[], orders=[], vehicles=[], legs={})
    for warehouse in warehouses:
        wid = warehouse['id']
        settings = warehouse.get('settings')
        if not isinstance(settings, dict) or settings.get('depot_id') != wid:
            raise ValueError(wid+' 的settings.depot_id必须等于仓库编号')
        orders = [deepcopy(o) for o in raw['orders'] if o['warehouse_id'] == wid]
        vehicles = [deepcopy(v) for v in raw['vehicles'] if v['warehouse_id'] == wid]
        nodes = {wid} | {o['id'] for o in orders}
        keys = {a+'>'+b for a in nodes for b in nodes if a != b}
        data = dict(settings=settings, orders=orders, vehicles=vehicles,
                    legs={k: v for k, v in raw['legs'].items() if k in keys})
        if 'business' in warehouse:
            data['business'] = warehouse['business']
        try:
            clean = app.canonicalize(data)
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError(wid+'：'+str(error)) from None
        item = dict(id=wid, name=app.task_name(warehouse.get('name')), settings=clean['settings'])
        if 'business' in clean:
            item['business'] = clean['business']
        if 'loading_profile' in warehouse:
            warehouse_loading.loading.profile(clean, warehouse['loading_profile'])
            item['loading_profile'] = deepcopy(warehouse['loading_profile'])
        result['warehouses'].append(item)
        for field in ('orders', 'vehicles'):
            for row in clean[field]:
                row['warehouse_id'] = wid
                result[field].append(row)
        result['legs'].update(clean['legs'])
    extra = set(raw['legs']) - set(result['legs'])
    if extra:
        raise ValueError('道路包含未知、跨仓或自环方向，请核对：'+', '.join(sorted(map(str, extra))[:8]))
    return result


def split(data):
    groups = {}
    for w in data['warehouses']:
        wid = w['id']
        orders = [{k: deepcopy(v) for k, v in o.items() if k != 'warehouse_id'}
                  for o in data['orders'] if o['warehouse_id'] == wid]
        vehicles = [{k: deepcopy(v) for k, v in o.items() if k != 'warehouse_id'}
                    for o in data['vehicles'] if o['warehouse_id'] == wid]
        nodes = {wid} | {o['id'] for o in orders}
        part = dict(settings=deepcopy(w['settings']), orders=orders, vehicles=vehicles,
            legs={k: deepcopy(v) for k, v in data['legs'].items() if all(n in nodes for n in k.split('>'))})
        if 'business' in w:
            part['business'] = deepcopy(w['business'])
        groups[wid] = app.canonicalize(part)
    return groups


def sample(physical_loading=False):
    result = dict(schema_version=SCHEMA, warehouses=[], orders=[], vehicles=[], legs={})
    for index, name in enumerate(('长沙北仓（模拟）', '长沙南仓（模拟）'), 1):
        part = app.sample(); old = part['settings']['depot_id']; wid = 'WH_'+str(index)
        rename = {old: wid}
        rename.update({o['id']: wid+'_'+o['id'] for o in part['orders']})
        rename.update({v['id']: wid+'_'+v['id'] for v in part['vehicles']})
        part['settings'].update(depot_id=wid, travel_source='多仓闭环构造道路，仅供模拟演练')
        result['warehouses'].append(dict(id=wid, name=name, settings=part['settings'], business=part['business']))
        for o in part['orders']:
            o.update(id=rename[o['id']], warehouse_id=wid,
                     allowed_vehicles=[rename[v] for v in o['allowed_vehicles']])
            result['orders'].append(o)
        for v in part['vehicles']:
            v.update(id=rename[v['id']], warehouse_id=wid)
            result['vehicles'].append(v)
        for key, leg in part['legs'].items():
            a, b = key.split('>'); result['legs'][rename[a]+'>'+rename[b]] = leg
    if physical_loading:
        for warehouse in result['warehouses']:
            wid = warehouse['id']
            orders = [o for o in result['orders'] if o['warehouse_id'] == wid]
            vehicles = [v for v in result['vehicles'] if v['warehouse_id'] == wid]
            # Intentionally constructed small demonstration; never derived from the enterprise attachment.
            for order in orders:
                order['lines'][0]['quantity'] = 2
            warehouse['loading_profile'] = dict(schema='loading_v1', data_kind='simulated',
                vehicles=[dict(id=v['id'], length_cm=240, width_cm=160, height_cm=160) for v in vehicles],
                items=[dict(order=o['id'], sku=line['sku'], length_cm=60, width_cm=40, height_cm=30,
                            rotate_base=True, fragile=False, max_top_load_kg=70)
                       for o in orders for line in o['lines']])
    return canonicalize(result)


def metrics(results, mode):
    routes = [r for p in results.values() for r in p[mode]['routes'].values()]
    weight_capacity = volume_capacity = 0
    for p in results.values():
        for v in p['data']['vehicles']:
            if p[mode]['routes'][v['id']]['sequence']:
                weight_capacity += v['max_weight_kg']; volume_capacity += v['max_volume_m3']
    return dict(served=sum(len(r['sequence']) for r in routes),
        unserved=sum(len(p[mode]['unserved']) for p in results.values()),
        trips=sum(bool(r['sequence']) for r in routes),
        cost=round(sum(r['cost'] for r in routes), 4),
        distance_km=round(sum(r['distance'] for r in routes), 4),
        late_orders=sum(r['late_orders'] for r in routes),
        weight_utilization=round(sum(r['weight_kg'] for r in routes)/weight_capacity, 6) if weight_capacity else 0,
        volume_utilization=round(sum(r['volume_m3'] for r in routes)/volume_capacity, 6) if volume_capacity else 0)


def audit(data, results):
    groups = split(data)
    if set(results) != set(groups):
        raise ValueError('多仓核验失败：仓库覆盖不完整')
    served, unserved, used_vehicles = [], [], []
    for wid, part in groups.items():
        plan = results[wid]['optimized']
        pilot.audit(part, plan)
        warehouse = next(w for w in data['warehouses'] if w['id'] == wid)
        if 'loading_profile' in warehouse:
            warehouse_loading.check_plan(part, warehouse['loading_profile'], plan)
        for vid, route in plan['routes'].items():
            if route['sequence']:
                used_vehicles.append(vid)
            served.extend(route['sequence'])
        unserved.extend(o['id'] for o in plan['unserved'])
    expected = {o['id'] for o in data['orders']}
    if len(served) != len(set(served)) or len(used_vehicles) != len(set(used_vehicles)):
        raise ValueError('多仓核验失败：重复配送或重复使用车辆')
    if set(served) & set(unserved) or set(served) | set(unserved) != expected or len(unserved) != len(set(unserved)):
        raise ValueError('多仓核验失败：任务遗漏或重复')
    return dict(passed=True, warehouses=len(groups), served=len(served), unserved=len(unserved))


def current_plan(task):
    return next((p for p in reversed(task['plans']) if p['input_revision'] == task['input_revision']
        and p['input_sha256'] == app.fingerprint(task['data'])), None)


def has_facts(task):
    return any(t.get('receipts') or t.get('observations') for t in task.get('execution_tasks', {}).values())


def view(task):
    plan = current_plan(task)
    confirmed = bool(plan and plan['id'] == task.get('adopted_plan_id'))
    states = {wid: execution.view(t) for wid, t in task.get('execution_tasks', {}).items()} if confirmed else {}
    complete = bool(states) and all(s['complete'] for s in states.values())
    if complete: status = '已回仓交接'
    elif has_facts(task): status = '执行记录中'
    elif confirmed: status = '已确认，待执行'
    elif plan: status = '部分未安排' if plan['summary']['optimized']['unserved'] else '待核对方案'
    else: status = '待准备与计算'
    return dict(status=status, confirmed=confirmed, complete=complete, execution=states,
        current_plan_id=plan['id'] if plan else None,
        receipts=sum(len(t.get('receipts', [])) for t in task.get('execution_tasks', {}).values()),
        boundaries=BOUNDARIES)


class MultiWarehouseStore(app.ApplicationStore):
    def __init__(self, path):
        super().__init__(path)
        with self.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS multiwarehouse_tasks (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, document TEXT NOT NULL)')

    def read(self, task_id):
        with self.connect() as c:
            row = c.execute('SELECT document FROM multiwarehouse_tasks WHERE id=?', (task_id,)).fetchone()
        if row is None: raise ValueError('多仓任务不存在，请刷新任务列表')
        return json.loads(row[0])

    def list(self):
        with self.connect() as c:
            tasks = [json.loads(r[0]) for r in c.execute('SELECT document FROM multiwarehouse_tasks')]
        return [dict(id=t['id'], name=t['name'], revision=t['revision'], updated_at=t['updated_at'],
            warehouses=len(t['data']['warehouses']), orders=len(t['data']['orders']), status=view(t)['status'])
            for t in sorted(tasks, key=lambda t: t['updated_at'], reverse=True)]

    def create(self, name, data):
        clean = canonicalize(data); timestamp = app.now()
        task = dict(id=uuid4().hex, name=app.task_name(name), revision=1, input_revision=1,
            created_at=timestamp, updated_at=timestamp, data=clean, plans=[], adopted_plan_id=None,
            execution_tasks={}, agent_runs=[])
        with self.connect() as c:
            c.execute('INSERT INTO multiwarehouse_tasks VALUES (?,?,?)', (task['id'], 1, app.encode(task)))
        return task

    def commit(self, task, expected):
        task.update(revision=expected+1, updated_at=app.now())
        with self.connect() as c:
            changed = c.execute('UPDATE multiwarehouse_tasks SET revision=?,document=? WHERE id=? AND revision=?',
                (task['revision'], app.encode(task), task['id'], expected)).rowcount
            if changed != 1: raise Conflict('任务已变化，请刷新后核对；本次没有覆盖新内容')
        return task

    def save(self, task_id, revision, name, data):
        task = self.read(task_id); self.guard(task, revision)
        clean = canonicalize(data)
        if app.fingerprint(clean) != app.fingerprint(task['data']):
            if has_facts(task): raise Conflict('已有执行事实，不能改写仓库归属或原输入；请新建批次')
            task.update(input_revision=task['input_revision']+1, adopted_plan_id=None, execution_tasks={})
        task.update(name=app.task_name(name), data=clean)
        return self.commit(task, revision)

    def calculate(self, task):
        if has_facts(task): raise Conflict('已有执行记录，请计算剩余路线，不能重算原始批次')
        if len(task['plans']) >= 30: raise ValueError('已有30个计算版本，请新建批次；历史保留')
        data = canonicalize(task['data'])
        warehouses = {w['id']: w for w in data['warehouses']}
        results = {wid: warehouse_loading.compare(part, warehouses[wid].get('loading_profile'))
                   for wid, part in split(data).items()}
        checked = audit(data, results)
        plan = dict(id=uuid4().hex, created_at=app.now(), input_revision=task['input_revision'],
            input_sha256=app.fingerprint(data), warehouses=results, audit=checked,
            summary={mode: metrics(results, mode) for mode in ('baseline', 'optimized')})
        task['plans'].append(plan)
        # A new calculation needs a fresh human confirmation, even with identical inputs.
        task.update(adopted_plan_id=None, execution_tasks={})
        return plan

    def solve(self, task_id, revision):
        task = self.read(task_id); self.guard(task, revision); self.calculate(task)
        return self.commit(task, revision)

    def manual(self, task_id, revision, sequences):
        task = self.read(task_id); self.guard(task, revision)
        if has_facts(task): raise Conflict('已有执行事实，请使用剩余路线工具，不能重新分配原货物')
        plan = current_plan(task)
        if not plan: raise ValueError('先计算一个候选，再人工调整')
        if len(task['plans']) >= 30: raise ValueError('已有30份方案，请另建批次')
        groups = split(task['data'])
        if not isinstance(sequences, dict) or set(sequences) != set(groups): raise ValueError('人工调整必须包含全部仓库')
        results = deepcopy(plan['warehouses'])
        for wid, part in groups.items():
            proposed = sequences[wid]
            if not isinstance(proposed, dict) or set(proposed) != {v['id'] for v in part['vehicles']}:
                raise ValueError(wid+'：人工调整必须包含全部车辆（闲置车填空列表）')
            orders = {o['id']: o for o in part['orders']}
            assigned = []
            for seq in proposed.values():
                if not isinstance(seq, list) or any(not isinstance(s, str) or s not in orders for s in seq):
                    raise ValueError(wid+'：包含未知或跨仓任务，不能隐式调拨')
                assigned.extend(seq)
            if len(assigned) != len(set(assigned)) or set(assigned) != set(orders):
                raise ValueError(wid+'：请保留全部任务且各出现一次，不能漏单或重复')
            ev = pilot.evaluator(part); routes = {}
            for v in part['vehicles']:
                route = ev(proposed[v['id']], v, orders)
                if route is None: raise ValueError(wid+'/'+v['id']+'：调整后违反重量、体积、回收、收货时间或收班约束')
                routes[v['id']] = route
            warehouse = next(w for w in task['data']['warehouses'] if w['id'] == wid)
            if 'loading_profile' in warehouse:
                routes = warehouse_loading.manual(part, warehouse['loading_profile'], routes)
            score = app.normal.score(routes)
            result = dict(routes=routes, unserved=[], metrics=dict(served=len(orders), unserved=0,
                late_orders=score[0], late_minutes=round(score[1], 2), cost=round(score[2], 2),
                distance=round(sum(r['distance'] for r in routes.values()), 2)))
            checked = pilot.audit(part, result)
            results[wid].update(optimized=result, audit={**results[wid]['audit'], 'optimized': checked})
            if 'loading_profile' in warehouse:
                results[wid]['audit']['physical_loading'] = warehouse_loading.check_plan(part, warehouse['loading_profile'], result)
                results[wid]['loading_verification'].update(status='VERIFIED_WITHIN_MODEL',
                    checked_boxes=results[wid]['audit']['physical_loading']['checked_boxes'])
                results[wid]['loading_verification'].pop('work', None)
                results[wid]['loading_verification']['source'] = 'manual_candidate_revalidation'
            results[wid]['diagnostics'] = []
        candidate = dict(id=uuid4().hex, parent_plan_id=plan['id'], method='manual_reorder',
            created_at=app.now(), input_revision=task['input_revision'], input_sha256=app.fingerprint(task['data']),
            warehouses=results, audit=audit(task['data'], results),
            summary={mode: metrics(results, mode) for mode in ('baseline', 'optimized')})
        task['plans'].append(candidate)
        task.update(adopted_plan_id=None, execution_tasks={})
        return self.commit(task, revision)

    def adopt(self, task_id, revision, plan_id, reviewed):
        task = self.read(task_id); self.guard(task, revision)
        if reviewed is not True: raise ValueError('先核对各仓归属、费用、未安排任务与规则，再确认')
        if has_facts(task): raise Conflict('已有执行事实，不能切换原方案')
        plan = current_plan(task)
        if not plan or plan['id'] != plan_id: raise Conflict('候选不是当前输入的最新方案，请刷新核对')
        checked = audit(task['data'], plan['warehouses'])
        if checked['unserved']: raise ValueError('还有配送任务未安排，不能确认整个多仓批次')
        task['execution_tasks'] = {}
        for wid, part in split(task['data']).items():
            child_plan = dict(id=plan_id+'_'+wid, input_revision=task['input_revision'],
                input_sha256=app.fingerprint(part), result=deepcopy(plan['warehouses'][wid]))
            task['execution_tasks'][wid] = dict(id=task_id+'_'+wid, revision=1,
                input_revision=task['input_revision'], data=part, plans=[child_plan],
                adopted_plan_id=child_plan['id'], receipts=[], observations=[])
            warehouse = next(w for w in task['data']['warehouses'] if w['id'] == wid)
            if 'loading_profile' in warehouse:
                task['execution_tasks'][wid]['physical_loading_profile'] = deepcopy(warehouse['loading_profile'])
        task['adopted_plan_id'] = plan_id
        return self.commit(task, revision)

    def action(self, task_id, revision, warehouse_id, operation, payload, reviewed=False):
        task = self.read(task_id); self.guard(task, revision)
        if not view(task)['confirmed']: raise Conflict('请先确认当前多仓方案')
        child = task['execution_tasks'].get(warehouse_id)
        if child is None: raise ValueError('仓库不存在')
        if operation == 'receipt': execution.append(child, payload, reviewed)
        elif operation == 'observe': observation.append(child, payload, reviewed)
        elif operation == 'replan':
            self.calculate_remaining(child)
        elif operation == 'adopt-remaining':
            if reviewed is not True: raise ValueError('先核对剩余顺序与风险，再采用')
            candidate = next((p for p in child.get('remaining_plans', []) if p['id'] == payload), None)
            if not candidate or candidate['base_sha256'] != remaining.stamp(child) or candidate['adopted_at']:
                raise Conflict('执行或顺序已经变化，候选已失效，请重新计算')
            remaining.audit(child, candidate['sequences'])
            warehouse_loading.remaining_check(child, candidate['sequences'])
            child.update(remaining_sequences=deepcopy(candidate['sequences']), active_remaining_plan_id=candidate['id'])
            candidate['adopted_at'] = app.now()
        else: raise ValueError('不支持的执行操作')
        return self.commit(task, revision)

    @staticmethod
    def calculate_remaining(child):
        if len(child.get('remaining_plans', [])) >= 30: raise ValueError('剩余方案达到30份上限')
        candidate = remaining.solve(child)
        candidate['physical_loading'] = warehouse_loading.remaining_check(child, candidate['sequences'])
        candidate.update(id=uuid4().hex, created_at=app.now(), adopted_at=None)
        child.setdefault('remaining_plans', []).append(candidate)
        return candidate

    def export_csv(self, task_id, revision):
        task = self.read(task_id); self.guard(task, revision)
        if not view(task)['confirmed']: raise Conflict('请先确认当前完整多仓方案再导出配送清单')
        output = io.StringIO(newline=''); writer = csv.writer(output)
        writer.writerow(['批次', '仓库', '方案', '车辆', '顺序', '配送任务', '预计到达分钟', '预计完成分钟',
            '预计纸箱数', '预计周转箱数', '估算离店kg', '估算离店m3', '车次费用元_每车仅首行填写', '数据来源', '声明'])
        plan = current_plan(task)
        for wid, result in plan['warehouses'].items():
            kind = next(w['settings']['data_kind'] for w in task['data']['warehouses'] if w['id'] == wid)
            for vid, route in result['optimized']['routes'].items():
                for i, stop in enumerate(route['stops'], 1):
                    writer.writerow([task_id, wid, plan['id'], vid, i, stop['id'], stop['arrival'], stop['finish'],
                        stop.get('pickup', {}).get('cartons', ''), stop.get('pickup', {}).get('totes', ''),
                        stop.get('onboard_weight_kg', ''), stop.get('onboard_volume_m3', ''),
                        route['cost'] if i == 1 else '', kind, '本机确认的出发前预测；未向企业派单，非正式结算'])
        return '\ufeff'+output.getvalue()

    def export_package(self, task_id, revision):
        task = self.read(task_id); self.guard(task, revision)
        states = view(task)
        return dict(schema_version=SCHEMA, dispatch_status='not_sent', integration_status='not_connected',
            task=task, state=states, boundaries=BOUNDARIES,
            facts_classification='locally_recorded_unverified', forecast_classification='estimated_not_fulfillment',
            pending=['企业仓库/订单/门店/车辆编号映射', '配送日期与时区', '库存与可调拨范围', '接口、鉴权与接收结果'])
