"""Turn a reviewed workbook revision into a traceable warehouse task."""
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal
from math import isclose

import application
import business
import intake_review
import multiwarehouse
import pilot
import central_orders
from store import Conflict


NUMERIC_FIELDS = {
    'regular': ('regular_quantity', 'regular_weight', 'regular_volume'),
    'cold': ('cold_quantity', 'cold_weight', 'cold_volume'),
}
UNIT_FACTORS = {'kg': Decimal('1'), 'g': Decimal('.001'), 'm3': Decimal('1'),
                'L': Decimal('.001'), 'cm3': Decimal('.000001')}
STOP_FIELDS = {'open', 'due', 'close', 'service_minutes', 'allowed_vehicles', 'pickup'}
VEHICLE_FIELDS = {'id', 'max_weight_kg', 'max_volume_m3', 'fixed_cost',
                  'cost_per_km', 'shift_start', 'shift_end'}
PARAMETER_FIELDS = {'warehouse', 'vehicles', 'stops', 'stop_defaults', 'legs',
                    'zero_cold_rows', 'accept_cached_totals'}


def snapshot(reviews, document_id, revision):
    if type(revision) is not int or revision < 0:
        raise ValueError('请提供当前核对版本')
    review = reviews.read(document_id)
    if review['revision'] != revision:
        raise Conflict('核对版本已更新，请刷新后再建任务')
    return reviews.source(document_id), review


def _scope(document, review):
    config = review['config']
    sheet = document['sheets'][config['sheet_index']]
    audit = document['audit']
    if audit.get('sheet') == sheet['name'] and 'source_rows' in audit:
        source_rows = audit['source_rows']
    else:
        source_rows = [row['row'] for row in sheet['rows'] if any(
            c['value'] is not None or c['formula'] is not None for c in row['cells'])]
    selected_rows = config['rows']
    omitted_rows = sorted(set(source_rows) - set(selected_rows))
    return dict(sheet=sheet['name'], source_rows=source_rows, selected_rows=selected_rows,
                omitted_rows=omitted_rows, all_selected=set(source_rows) == set(selected_rows),
                batch_id=config['batch_id'], delivery_date=config['delivery_date'], planning_mode='standard')


def _cold_blank(item, columns):
    for field in NUMERIC_FIELDS['cold']:
        cell = item['source_refs'][field]
        if columns[field] is None or (cell and (
                cell.get('effective_value') is not None or cell.get('formula') is not None or cell.get('merged_from'))):
            return False
    return True


def _store_key(item):
    identity = item['identity']
    return identity['store_name'], identity['address']


def _rows(document, review):
    columns = review['config']['fields']
    audit_by_row = defaultdict(list)
    sheet = document['sheets'][review['config']['sheet_index']]['name']
    for issue in document['audit']['issues']:
        if issue['sheet'] == sheet:
            for row in issue['rows']:
                audit_by_row[row].append(issue)
    result = []
    for item in review['assessment']['rows']:
        identity = item['identity']
        number = item['source_row']
        key = _store_key(item)
        result.append(dict(source_row=number, store_name=key[0], address=key[1],
                           store_id=identity['store_id'], order_id=identity['order_id'],
                           batch_id=identity['batch_id'], normalized=item['normalized'],
                           suggested_store_id='STORE_'+application.fingerprint(
                               dict(name=key[0], address=key[1]))[:12] if all(key) else '',
                           cold_blank=_cold_blank(item, columns),
                           skus={'regular': f'REG_R{number}', 'cold': f'COLD_R{number}'},
                           source_refs={k: v['ref'] if v else None for k, v in item['source_refs'].items()},
                           manual_identity_fields=item.get('manual_identity_fields', []),
                           issues=item['issues'] + audit_by_row[number]))
    return result


def template(document, review):
    rows = _rows(document, review)
    return dict(scope=_scope(document, review), rows=rows, units=review['config']['units'],
                business_rules=central_orders.summary(document,review['config']),
                parameters=dict(warehouse=dict(id='', name='', settings=dict(
                    depot_id='', data_kind='', travel_source='', window_rule=''), business=business.defaults()),
                    vehicles=[], stops={}, legs={}, zero_cold_rows=[], accept_cached_totals=False,
                    stop_defaults=dict(open=None, due=None, close=None, service_minutes=None,
                                       allowed_vehicles=[], pickup=dict(cartons=None, totes=None))))


def _add(issues, code, message, rows=(), fields=()):
    found = next((entry for entry in issues if entry['code'] == code and
                  entry['message'] == message and entry['fields'] == list(fields)), None)
    if found is None:
        issues.append(dict(code=code, message=message, rows=list(rows), fields=list(fields)))
    else:
        found['rows'].extend(row for row in rows if row not in found['rows'])


def _totals(item, kind, units, merged_anchors, approved_zero):
    if approved_zero:
        return 0, Decimal(0), Decimal(0)
    values = []
    for field in NUMERIC_FIELDS[kind]:
        cell = item['source_refs'][field]
        is_quantity = field.endswith('quantity')
        suffix = '' if is_quantity else '_kg' if field.endswith('weight') else '_m3'
        normalized = item['normalized'][field+suffix]
        if normalized is None or cell is None or cell['ref'] in merged_anchors:
            raise ValueError('请核对数量、单位、重量和体积，合并数值需有明确分摊依据')
        if kind == 'cold' and field.endswith('volume') and item.get('volume_basis'):
            values.append(Decimal(str(normalized)))
            continue
        value = Decimal(cell['raw_value'])
        values.append(value if is_quantity else value * UNIT_FACTORS[units[field]])
    quantity, weight, volume = values
    if quantity != int(quantity) or quantity > 10000:
        raise ValueError('件数须为 0–10000 的整数')
    if (quantity == 0 and (weight or volume)) or (quantity > 0 and (weight <= 0 or volume <= 0)):
        raise ValueError('件数与重量、体积不一致')
    return int(quantity), weight, volume


def _time(value):
    return pilot.minute(value, '作业时间') if isinstance(value, str) else value


def _stop_rules(parameters, store_ids, issues):
    defaults = parameters.get('stop_defaults', {})
    overrides = parameters.get('stops', {})
    if not isinstance(defaults, dict) or set(defaults)-STOP_FIELDS or not isinstance(overrides, dict):
        raise ValueError('门店作业设置包含未知字段')
    if set(overrides)-store_ids:
        raise ValueError('作业设置引用了未纳入的门店编号')
    result = {}
    incomplete = []
    for store_id in sorted(store_ids):
        values = overrides.get(store_id, {})
        if not isinstance(values, dict) or set(values)-STOP_FIELDS:
            raise ValueError('门店作业设置包含未知字段')
        config = {**defaults, **values}
        if 'pickup' in config:
            result_pickup = {}
            for raw in (defaults, values):
                if 'pickup' not in raw:
                    continue
                pickup = raw['pickup']
                if not isinstance(pickup, dict) or set(pickup)-{'cartons','totes'}:
                    raise ValueError('回收设置仅支持预计纸箱、周转箱件数')
                result_pickup.update(pickup)
            config['pickup'] = result_pickup
        if (set(config) != STOP_FIELDS or any(config[k] is None for k in STOP_FIELDS) or
                not config['allowed_vehicles'] or set(config['pickup']) != {'cartons', 'totes'} or
                any(value is None for value in config['pickup'].values())):
            incomplete.append(store_id)
            continue
        result[store_id] = {**config, **{k: _time(config[k]) for k in ('open', 'due', 'close')}}
    if incomplete:
        _add(issues, 'stop_rules_required', f'{len(incomplete)} 家门店待确认收货时间、作业时长、可用车辆或回收量',
             fields=incomplete)
    return result


def prepare(document, review, parameters):
    """Return (preview, canonical input, source record). Never write to a store."""
    if not isinstance(parameters, dict) or set(parameters)-PARAMETER_FIELDS:
        raise ValueError('批次参数含未知字段，请按模板填写')
    parameters = deepcopy(parameters)
    if central_orders.matches(document, review['config']):
        parameters['accept_cached_totals'] = True
        parameters['zero_cold_rows'] = [item['source_row'] for item in review['assessment']['rows']
                                       if item.get('cold_zero_confirmed')]
    scope = _scope(document, review)
    config = review['config']
    issues = []
    items = review['assessment']['rows']
    if not review['revision']:
        _add(issues, 'review_required', '请先保存订单字段与门店核对')
    if not items:
        _add(issues, 'empty_scope', '请至少选一条订单')
    batches = {item['identity']['batch_id'] for item in items}
    if len(batches) != 1 or not next(iter(batches), '') or not scope['delivery_date']:
        _add(issues, 'batch_required', '请确认同一配送批次和配送日期')
    else:
        scope['batch_id'] = next(iter(batches))
    for issue in review['assessment']['issues']:
        _add(issues, issue['code'], issue['message'], issue['rows'], issue['fields'])
    sheet = document['sheets'][config['sheet_index']]
    merged_anchors = {merge['anchor'] for merge in sheet['merges']}
    zeros = parameters.get('zero_cold_rows', [])
    if not isinstance(zeros, list) or any(type(row) is not int for row in zeros) or len(zeros) != len(set(zeros)):
        raise ValueError('无冷链确认须为不重复的原行号列表')
    allowed_zeros = {item['source_row'] for item in items if _cold_blank(item, config['fields'])}
    if set(zeros)-allowed_zeros:
        _add(issues, 'zero_cold_scope', '只能确认选中且冷链三项全空的原行为无冷链', sorted(set(zeros)-allowed_zeros))
    if type(parameters.get('accept_cached_totals', False)) is not bool:
        raise ValueError('公式缓存确认须为布尔值')
    cache_rows = {item['source_row'] for item in items if any(
        c and c.get('cache_status') == 'cached' for field, c in item['source_refs'].items()
        if field in NUMERIC_FIELDS['regular'] + NUMERIC_FIELDS['cold'])}
    if cache_rows and not parameters.get('accept_cached_totals'):
        _add(issues, 'formula_confirmation_required', '请核对并确认采用原表的公式缓存值', sorted(cache_rows))

    stores = {}
    lines = defaultdict(list)
    exact_totals = defaultdict(lambda: [0, Decimal(0), Decimal(0)])
    source_rows = []
    kind_by_line = {}
    selected_pairs = {}
    for item in items:
        row = item['source_row']
        identity = item['identity']
        store_id = identity['store_id']
        pair = _store_key(item)
        if not store_id or not all(pair):
            _add(issues, 'store_identity_required', '请核对门店编号、名称和地址', [row], ['store_id', 'store_name', 'address'])
            continue
        try:
            pilot.identifier(store_id, '门店编号')
        except ValueError as error:
            _add(issues, 'invalid_store_id', str(error), [row], ['store_id'])
            continue
        prior = selected_pairs.setdefault(pair, (store_id, row))
        if prior[0] != store_id:
            _add(issues, 'split_store', '同一名称和地址被分到不同门店编号，请合并核对', [prior[1], row], ['store_id'])
        store = stores.setdefault(store_id, dict(id=store_id, name=pair[0], address=pair[1], source_rows=[]))
        store['source_rows'].append(row)
        evidence = dict(source_row=row, row_key=f"ROW-{config['sheet_index']+1}-{row}",
                        order_id=identity['order_id'] or None, store_id=store_id,
                        source_refs={key: cell['ref'] if cell else None for key, cell in item['source_refs'].items()},
                        manual_identity_fields=item.get('manual_identity_fields', []),
                        line_totals={}, cold_zero_confirmed=row in zeros and row in allowed_zeros)
        if item.get('volume_basis'):
            evidence['volume_basis'] = deepcopy(item['volume_basis'])
        for kind in NUMERIC_FIELDS:
            try:
                quantity, weight, volume = _totals(item, kind, config['units'], merged_anchors,
                    kind == 'cold' and evidence['cold_zero_confirmed'])
            except ValueError as error:
                label = '常规货物' if kind == 'regular' else '冷链货物'
                _add(issues, kind+'_totals_required', label+'：'+str(error), [row], NUMERIC_FIELDS[kind])
                continue
            if quantity:
                sku = f'{"REG" if kind == "regular" else "COLD"}_R{row}'
                lines[store_id].append(dict(sku=sku, quantity=quantity,
                    unit_weight_kg=float(weight/quantity), unit_volume_m3=float(volume/quantity)))
                kind_by_line[(store_id, sku)] = kind
            for index, value in enumerate((quantity, weight, volume)):
                exact_totals[store_id][index] += value
            evidence['line_totals'][kind] = dict(quantity=quantity, weight_kg=float(weight), volume_m3=float(volume))
        if len(evidence['line_totals']) == 2 and not any(v['quantity'] for v in evidence['line_totals'].values()):
            _add(issues, 'empty_goods_row', '该原行未确认有任何货物，请先核对', [row])
        source_rows.append(evidence)

    if stores and scope['omitted_rows']:
        omitted_config = {**config, 'rows': scope['omitted_rows']}
        for item in intake_review.assess(document, omitted_config)['rows']:
            identity = item['identity']
            manual_batch = config['identities'].get(str(item['source_row']), {}).get('batch_id')
            if manual_batch and manual_batch != scope['batch_id']:
                continue
            if _store_key(item) in selected_pairs or identity['store_id'] in stores:
                _add(issues, 'partial_store', '本批还漏选了同店原行，请一并选择或确认其所属批次', [item['source_row']])

    warehouse = parameters.get('warehouse')
    vehicles = parameters.get('vehicles')
    legs = parameters.get('legs')
    if not isinstance(warehouse, dict) or not warehouse.get('id') or not isinstance(warehouse.get('settings'), dict):
        _add(issues, 'warehouse_required', '请补充仓库编号、名称、道路来源和收货时点规则')
    elif 'business' not in warehouse:
        _add(issues, 'business_required', '请确认运费和回收包装参数')
    if not isinstance(vehicles, list) or not vehicles:
        _add(issues, 'vehicles_required', '请添加可用车辆、载重、容积和班次')
    elif any(not isinstance(v, dict) or set(v) != VEHICLE_FIELDS for v in vehicles):
        _add(issues, 'vehicles_incomplete', '请补齐每辆车的编号、载重、容积、费用参数和班次')
    if not isinstance(legs, dict) or not legs:
        _add(issues, 'roads_required', '请导入仓库与所选门店之间的有向道路里程和行驶时间')
    try:
        stop_rules = _stop_rules(parameters, set(stores), issues)
    except ValueError as error:
        _add(issues, 'stop_rules_invalid', str(error))
        stop_rules = {}
    if isinstance(warehouse, dict):
        if len(stores) > application.MAX_ORDERS or (isinstance(vehicles, list) and len(vehicles) > application.MAX_VEHICLES):
            warehouse.setdefault('planning_mode', 'fleet')
        scope['planning_mode'] = warehouse.get('planning_mode', 'standard')
        policy = warehouse.get('operational_policy')
        if 'cold' in kind_by_line.values() and not policy:
            _add(issues, 'cold_policy_required', '批次含冷链货，请填写共享箱归属、设备时限及完整装卸约束')
        if isinstance(policy, dict) and isinstance(policy.get('goods'), list):
            for goods in policy['goods']:
                if not isinstance(goods, dict):
                    continue
                key = (goods.get('order'), goods.get('sku'))
                if all(isinstance(part, str) for part in key) and key in kind_by_line and goods.get('kind') != kind_by_line[key]:
                    _add(issues, 'cold_policy_mismatch', '箱规的常规/冷链类型与已核对原表不符', fields=key)

    preview = dict(ready=False, issues=issues, scope=scope, stores=list(stores.values()))
    if issues:
        return preview, None, None
    wid = warehouse['id']
    try:
        raw = dict(schema_version=multiwarehouse.SCHEMA, warehouses=[warehouse], legs=legs,
            vehicles=[{**v, 'warehouse_id': wid, **{k: _time(v[k]) for k in ('shift_start', 'shift_end')}} for v in vehicles],
            orders=[dict(id=sid, warehouse_id=wid, lines=lines[sid], **stop_rules[sid]) for sid in stores])
        data = multiwarehouse.canonicalize(raw)
    except (ValueError, TypeError, KeyError) as error:
        _add(issues, 'invalid_dispatch_inputs', str(error))
        return preview, None, None
    for order in data['orders']:
        count, weight, volume = exact_totals[order['id']]
        if (sum(line['quantity'] for line in order['lines']) != count or
                not isclose(order['unit_weight_kg'], float(weight), rel_tol=1e-12, abs_tol=1e-9) or
                not isclose(order['unit_volume_m3'], float(volume), rel_tol=1e-12, abs_tol=1e-9)):
            _add(issues, 'totals_mismatch', '转换后的门店数量、重量或体积与核对总量不一致',
                 stores[order['id']]['source_rows'])
    if issues:
        return preview, None, None
    source = dict(kind='xlsx', document_id=document['id'], filename=document['filename'],
                  source_sha256=document['sha256'], review_revision=review['revision'],
                  input_revision=1, input_sha256=application.fingerprint(data),
                  scope=scope, rows=source_rows, stores=preview['stores'],
                  accepted_cached_totals=bool(cache_rows), units=config['units'])
    preview['ready'] = True
    return preview, data, source


def dispatch_request(reviews, tasks, body, *, create=False):
    if set(body)-{'id', 'revision', 'name', 'parameters', 'confirmed'}:
        raise ValueError('订单确认请求包含未知字段')
    document_id = body.get('id')
    revision = body.get('revision')
    if not isinstance(document_id, str) or type(revision) is not int or revision < 0:
        raise ValueError('请选择订单文件与当前核对版本')
    if create:
        if body.get('confirmed') is not True:
            raise ValueError('请先确认订单范围、补充参数和计费口径')
        request_hash = application.fingerprint({
            'kind': 'intake_dispatch', 'id': document_id, 'revision': revision,
            'name': body.get('name'), 'parameters': body.get('parameters'),
        })
        task = tasks.find_intake_task(request_hash)
        if task:
            return dict(task=task, state=multiwarehouse.view(task), source=task['intake_source'], reused=True), 200
    document, review = snapshot(reviews, document_id, revision)
    preview, data, source = prepare(document, review, body.get('parameters'))
    if not create:
        return preview, 200
    if not preview['ready']:
        return {**preview, 'error': preview['issues'][0]['message']}, 400
    try:
        with reviews.confirm_current(document_id, revision, source['source_sha256']) as connection:
            shared = connection if reviews.path.resolve() == tasks.path.resolve() else None
            task, reused = tasks.create_intake(body.get('name') or document['name'], data, source,
                                              request_hash, connection=shared)
    except Conflict:
        task = tasks.find_intake_task(request_hash)
        if task is None:
            raise
        reused = True
    return dict(task=task, state=multiwarehouse.view(task), source=task['intake_source'], reused=reused), 200
