"""Persistent, local planning tasks with optional tariff and pickup forecasts.

Planning and append-only local receipts; no dispatch or external calls.
"""
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import csv
import io
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from uuid import uuid4

import normal
import pilot
import business
import execution
import remaining
import observation
from store import Conflict

ROOT = Path(__file__).resolve().parent
FILES = ('orders.csv', 'vehicles.csv', 'travel.csv', 'settings.json')
MAX_ORDERS, MAX_VEHICLES = 24, 8


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def fingerprint(data):
    # File serialization hashes are provenance, not a change to the business input.
    payload = {key: value for key, value in data.items() if key != 'source_sha256'}
    return sha256(encode(payload).encode('utf-8')).hexdigest()


def load_files(files, *, limits=(MAX_ORDERS, MAX_VEHICLES)):
    if not isinstance(files, dict) or set(files) != set(FILES):
        raise ValueError('需要orders.csv、vehicles.csv、travel.csv、settings.json四份文件')
    if any(not isinstance(v, str) for v in files.values()):
        raise ValueError('导入文件需要UTF-8文本')
    if sum(len(v.encode('utf-8')) for v in files.values()) > 2_000_000:
        raise ValueError('输入文件合计不能超过2MB')
    with TemporaryDirectory() as directory:
        for name, value in files.items():
            (Path(directory) / name).write_text(value, encoding='utf-8')
        data = pilot.load_bundle(directory, limits=limits)
    return data


def clock(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value or not 0 <= value < 1440:
        raise ValueError('时间必须是当天的整分钟，范围00:00—23:59')
    return f'{int(value)//60:02}:{int(value)%60:02}'


def canonicalize(raw, *, limits=(MAX_ORDERS, MAX_VEHICLES)):
    """Validate all editable fields through the original CSV input contract.

    Derived totals and source hashes are recomputed, never trusted from clients.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get('settings'), dict):
        raise ValueError('缺少任务设置')
    max_orders, max_vehicles = limits
    if not isinstance(raw.get('orders'), list) or not 1 <= len(raw['orders']) <= max_orders:
        raise ValueError(f'请填写1—{max_orders}个门店')
    if not isinstance(raw.get('vehicles'), list) or not 1 <= len(raw['vehicles']) <= max_vehicles:
        raise ValueError(f'请填写1—{max_vehicles}辆车')
    if not isinstance(raw.get('legs'), dict):
        raise ValueError('缺少有向道路数据')
    if not isinstance(raw['settings'].get('travel_source'), str) or len(raw['settings']['travel_source']) > 500:
        raise ValueError('道路来源请填写1—500字')
    files = {'settings.json': encode(raw['settings'])}

    def table(name, fields, rows):
        output = io.StringIO(newline='')
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        files[name] = output.getvalue()

    orders, line_count = [], 0
    seen_orders = set()
    for order in raw['orders']:
        if not isinstance(order, dict) or not isinstance(order.get('lines'), list) or not order['lines']:
            raise ValueError('每个门店至少需要一行货物')
        if order.get('id') in seen_orders:
            raise ValueError('任务中的门店编号重复，请先核对归并')
        seen_orders.add(order.get('id'))
        allowed = order.get('allowed_vehicles')
        if not isinstance(allowed, list) or any(not isinstance(v, str) for v in allowed):
            raise ValueError('门店准入需要车辆编号列表')
        if not allowed:
            raise ValueError('每个门店至少需要一种允许车辆')
        for line in order['lines']:
            if not isinstance(line, dict):
                raise ValueError('货物明细格式无效')
            line_count += 1
            if line_count > 300:
                raise ValueError('本轮最多300行货物明细')
            for field in ('quantity', 'unit_weight_kg', 'unit_volume_m3'):
                normal.numeric(line.get(field), field, .000001, 10000)
            normal.numeric(order.get('service_minutes'), '到店服务分钟', 0, 240)
            orders.append(dict(stop_id=order.get('id'), sku=line.get('sku'),
                quantity=line['quantity'], unit_weight_kg=line['unit_weight_kg'],
                unit_volume_m3=line['unit_volume_m3'],
                open=clock(order.get('open')), due=clock(order.get('due')), close=clock(order.get('close')),
                service_minutes=order['service_minutes'], allowed_vehicles='|'.join(allowed)))
    table('orders.csv', pilot.ORDER_FIELDS, orders)
    vehicles = []
    for vehicle in raw['vehicles']:
        if not isinstance(vehicle, dict):
            raise ValueError('车辆格式无效')
        for field in ('max_weight_kg', 'max_volume_m3', 'fixed_cost', 'cost_per_km'):
            normal.numeric(vehicle.get(field), field, 0, 100000)
        vehicles.append(dict(vehicle_id=vehicle.get('id'),
            **{key: vehicle[key] for key in ('max_weight_kg', 'max_volume_m3', 'fixed_cost', 'cost_per_km')},
            shift_start=clock(vehicle.get('shift_start')), shift_end=clock(vehicle.get('shift_end'))))
    table('vehicles.csv', pilot.VEHICLE_FIELDS, vehicles)
    legs = []
    for key in sorted(raw['legs']):
        leg = raw['legs'][key]
        if not isinstance(key, str) or key.count('>') != 1 or not isinstance(leg, dict):
            raise ValueError('道路方向格式无效')
        a, b = key.split('>')
        normal.numeric(leg.get('distance_km'), '道路公里', 0, 100000)
        normal.numeric(leg.get('travel_minutes'), '行驶分钟', 0, 1440)
        legs.append(dict(from_id=a, to_id=b, **leg))
    table('travel.csv', pilot.TRAVEL_FIELDS, legs)
    result = load_files(files, limits=limits)
    if 'business' in raw:
        result['business'] = business.validate(raw['business'], raw['orders'])
        pickups = {order['id']: order['pickup'] for order in raw['orders']}
        for order in result['orders']:
            order['pickup'] = deepcopy(pickups[order['id']])
    elif any('pickup' in order for order in raw['orders']):
        raise ValueError('填写回收数量前，需先填写空包装业务规则')
    return result


def sample():
    data = pilot.load_bundle(ROOT / 'examples' / 'continuous-small')
    for index, order in enumerate(data['orders']):
        order.update(lines=[dict(sku='STANDARD_BOX', quantity=[12, 18, 10][index],
            unit_weight_kg=7, unit_volume_m3=.072)], open=510, due=1080, close=1080,
            service_minutes=8, allowed_vehicles=['TRUCK_1', 'TRUCK_2'])
    for vehicle, weight, volume, fixed in zip(data['vehicles'], [200, 350], [2, 3.5], [40, 50]):
        vehicle.update(max_weight_kg=weight, max_volume_m3=volume, fixed_cost=fixed,
            cost_per_km=2, shift_start=510, shift_end=1080)
    data['settings']['travel_source'] = '三店小任务的构造有向道路；距离和时间均为模拟，可在道路表修改'
    data['business'] = business.defaults()
    for order in data['orders']:
        order['pickup'] = dict(cartons=20, totes=3)
    return canonicalize(data)


def task_name(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80:
        raise ValueError('任务名称需要1—80字')
    return value.strip()


class ApplicationStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS application_tasks (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, document TEXT NOT NULL)')

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def read(self, task_id):
        with self.connect() as connection:
            row = connection.execute('SELECT document FROM application_tasks WHERE id=?', (task_id,)).fetchone()
        if row is None:
            raise ValueError('任务不存在，请从任务列表重新选择')
        return json.loads(row[0])

    def list(self):
        with self.connect() as connection:
            tasks = [json.loads(row[0]) for row in connection.execute('SELECT document FROM application_tasks')]
        return [dict(id=t['id'], name=t['name'], revision=t['revision'], updated_at=t['updated_at'],
            input_revision=t['input_revision'], orders=len(t['data']['orders']),
            data_kind=t['data']['settings']['data_kind'], plans=len(t['plans']),
            status=self.status(t)) for t in sorted(tasks, key=lambda t: t['updated_at'], reverse=True)]

    @staticmethod
    def status(task):
        if task.get('receipts') or task.get('observations'):
            return '已回仓交接' if execution.view(task)['complete'] else '执行记录中'
        plans = [p for p in task['plans'] if p['input_revision'] == task['input_revision'] and p['input_sha256'] == fingerprint(task['data'])]
        if any(p['id'] == task['adopted_plan_id'] for p in plans):
            return '已确认方案'
        if plans:
            return '待核对方案' if not plans[-1]['result']['optimized']['unserved'] else '部分任务未安排'
        return '待计算' if not task['plans'] else '输入已更新，需重新计算'

    @staticmethod
    def guard(task, revision):
        if type(revision) is not int or revision != task['revision']:
            raise Conflict('任务已经更新，请刷新后核对；当前页面未覆盖保存内容')

    def create(self, name, data):
        data = canonicalize(data)
        timestamp = now()
        task = dict(id=uuid4().hex, name=task_name(name), revision=1, input_revision=1,
            created_at=timestamp, updated_at=timestamp, data=data, plans=[], adopted_plan_id=None)
        with self.connect() as connection:
            connection.execute('INSERT INTO application_tasks VALUES (?,?,?)', (task['id'], 1, encode(task)))
        return task

    def commit(self, task, expected):
        task['revision'] = expected + 1
        task['updated_at'] = now()
        with self.connect() as connection:
            changed = connection.execute('UPDATE application_tasks SET revision=?,document=? WHERE id=? AND revision=?',
                (task['revision'], encode(task), task['id'], expected)).rowcount
            if changed != 1:
                raise Conflict('计算或保存期间任务已改变，请刷新核对；结果没有覆盖新版本')
        return task

    def save(self, task_id, revision, name, data):
        task = self.read(task_id)
        self.guard(task, revision)
        data = canonicalize(data)
        if (task.get('receipts') or task.get('observations')) and fingerprint(data) != fingerprint(task['data']):
            raise Conflict('已有执行记录，不能改写原货物、车辆或规则；请新建任务试算')
        if fingerprint(data) != fingerprint(task['data']):
            task['input_revision'] += 1
        task.update(name=task_name(name), data=data)
        return self.commit(task, revision)

    def solve(self, task_id, revision):
        task = self.read(task_id)
        self.guard(task, revision)
        if task.get('receipts') or task.get('observations'):
            raise Conflict('已有执行记录，不能从仓库重新计算全量配送；请查看剩余路线预测')
        if len(task['plans']) >= 50:
            raise ValueError('本任务已有50个计算版本，请新建任务继续试算；历史没有删除')
        data = canonicalize(task['data'])
        result = pilot.compare(data)
        task['plans'].append(dict(id=uuid4().hex, created_at=now(), input_revision=task['input_revision'],
            input_sha256=fingerprint(data), result=result))
        return self.commit(task, revision)

    def adopt(self, task_id, revision, plan_id, reviewed):
        task = self.read(task_id)
        self.guard(task, revision)
        if task.get('receipts') or task.get('observations'):
            raise Conflict('已有执行记录，不能切换原方案或回滚已签收事实')
        if reviewed is not True:
            raise ValueError('先核对路线、未安排任务和费用，再确认方案')
        plan = next((p for p in task['plans'] if p['id'] == plan_id), None)
        if not plan or plan['input_revision'] != task['input_revision'] or plan['input_sha256'] != fingerprint(task['data']):
            raise Conflict('这个方案不对应当前输入，请重新计算后再确认')
        result = plan['result']
        if result['optimized']['unserved']:
            raise ValueError('还有门店未安排，请修改输入并重新计算；不完整方案保留供核对')
        pilot.audit(task['data'], result['optimized'])
        task['adopted_plan_id'] = plan_id
        return self.commit(task, revision)

    def receipt(self, task_id, revision, receipt, reviewed):
        task = self.read(task_id)
        self.guard(task, revision)
        execution.append(task, receipt, reviewed)
        return self.commit(task, revision)

    def replan(self, task_id, revision):
        task = self.read(task_id)
        self.guard(task, revision)
        if len(task.get('remaining_plans', [])) >= 30:
            raise ValueError('已有30份剩余路线记录，请导出历史后另行处理')
        candidate = remaining.solve(task)
        candidate.update(id=uuid4().hex, created_at=now(), adopted_at=None)
        task.setdefault('remaining_plans', []).append(candidate)
        return self.commit(task, revision)

    def observe(self, task_id, revision, observation_record, reviewed):
        task = self.read(task_id)
        self.guard(task, revision)
        observation.append(task, observation_record, reviewed)
        return self.commit(task, revision)

    def adopt_remaining(self, task_id, revision, candidate_id, reviewed):
        task = self.read(task_id)
        self.guard(task, revision)
        if reviewed is not True:
            raise ValueError('先核对剩余路线、风险与已收包装，再采用')
        candidate = next((p for p in task.get('remaining_plans', []) if p['id']==candidate_id), None)
        if not candidate or candidate['base_sha256'] != remaining.stamp(task) or candidate['adopted_at']:
            raise Conflict('执行或路线已变化，这份候选已失效，请重新计算')
        remaining.audit(task, candidate['sequences'])
        task['remaining_sequences'] = deepcopy(candidate['sequences'])
        task['active_remaining_plan_id'] = candidate['id']
        candidate['adopted_at'] = now()
        return self.commit(task, revision)

    def export_receipts(self, task_id, revision):
        task = self.read(task_id)
        self.guard(task, revision)
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(['任务编号', '方案编号', '回执编号', '车辆', '类型', '门店', '完成时间',
                         '记录纸箱数', '记录周转箱数', '来源', '来源说明', '保存时间UTC', '后续风险', '剩余路线版本'])
        for r in task.get('receipts', []):
            # Spreadsheet formula prefixes in user-provided notes must stay text.
            note = r['note']
            if note.lstrip().startswith(('=', '+', '-', '@')):
                note = "'" + note
            writer.writerow([task['id'], r['plan_id'], r['id'], r['vehicle'],
                '签收并回收' if r['kind'] == 'stop' else '回仓交接', r['stop_id'] or '', clock(r['finish']),
                r['pickup'].get('cartons', ''), r['pickup'].get('totes', ''),
                '模拟演练' if r['source'] == 'simulated' else '人工录入，真实性待核对', note,
                r['recorded_at'], '；'.join(r['warnings_after']), r.get('remaining_plan_id') or ''])
        return '\ufeff' + output.getvalue()

    def export_remaining(self, task_id, revision):
        task = self.read(task_id)
        self.guard(task, revision)
        state = execution.view(task)
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(['任务编号','任务修订','原方案编号','剩余路线版本','车辆','剩余顺序','门店',
            '预计到达分钟','预计完成分钟','预计离店kg','预计离店m3','风险','声明',
            '预测起算节点','预测起算分钟','预测起算类型','预测起算来源'])
        for vid,r in state['routes'].items():
            for i,s in enumerate(r['remaining'],1):
                writer.writerow([task['id'],task['revision'],state['plan_id'],task.get('active_remaining_plan_id') or '',
                    vid,i,s['id'],s['arrival'],s['finish'],s['onboard_weight_kg'],s['onboard_volume_m3'],
                    '；'.join(r['warnings']), '剩余预测，非派单、签收或结算；载荷按箱规估算',
                    r['anchor']['node'],r['anchor']['time'],r['anchor']['kind'],r['anchor']['source']])
        return '\ufeff'+output.getvalue()

    def export_csv(self, task_id, revision):
        task = self.read(task_id)
        self.guard(task, revision)
        plan = next((p for p in task['plans'] if p['id'] == task['adopted_plan_id']), None)
        if not plan or plan['input_revision'] != task['input_revision'] or plan['input_sha256'] != fingerprint(task['data']):
            raise Conflict('请先确认当前输入对应的完整方案，再导出配送清单')
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(['任务编号', '方案编号', '车辆', '配送顺序', '门店编号', '预计到达', '预计服务结束', '数据声明', '核验范围',
                         '预计纸箱数', '预计周转箱数', '本店回收体积m3', '离店在车重量kg', '离店在车体积m3',
                         '计费分支', '车次费用元_同车各行重复勿求和', '车次起步费元', '车次里程费元', '车次门店服务费元',
                         '道路估算计费公里_非TMS结算', 'TMS计费公里', '实际GPS公里'])
        for vehicle, route in plan['result']['optimized']['routes'].items():
            for index, stop in enumerate(route['stops'], 1):
                def display(value):
                    return f'{int(value)//60:02}:{int(value)%60:02}'
                writer.writerow([task['id'], plan['id'], vehicle, index, stop['id'], display(stop['arrival']),
                    display(stop['finish']), task['data']['settings']['data_kind'],
                    '路线与重量体积；含预测回收逐站核验，未派单' if 'business' in task['data'] else '仅路线/总重量/总体积；预测时间，未派单',
                    stop.get('pickup', {}).get('cartons', ''), stop.get('pickup', {}).get('totes', ''),
                    stop.get('pickup_volume_m3', ''), stop.get('onboard_weight_kg', ''), stop.get('onboard_volume_m3', ''),
                    route.get('fee', {}).get('mode', 'linear'), route['cost'],
                    route.get('fee', {}).get('base', ''), route.get('fee', {}).get('distance', ''),
                    route.get('fee', {}).get('stops', ''), route.get('fee', {}).get('estimated_billing_km', ''), '', ''])
        return '\ufeff' + output.getvalue()
