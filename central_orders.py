"""Confirmed business interpretation of the retained central-warehouse workbook.

Matched by file digest, never by uploaded instructions or a similar filename.
Raw cells remain immutable; calculated cargo volume follows confirmed dimensions.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

SOURCE_SHA256 = '40849d76aceaf3589ac68fdad258c847ed0bbf464e65b76a468258bc36726587'
FIELDS = dict(order_id=None, store_id=None, store_name=3, address=4,
              regular_quantity=5, regular_weight=7, regular_volume=6,
              cold_quantity=8, cold_weight=10, cold_volume=9)
UNITS = dict(regular_weight='kg', regular_volume='m3', cold_weight='kg', cold_volume='m3')


def matches(document, config=None):
    if document.get('sha256', '').lower() != SOURCE_SHA256:
        return False
    if config is None:
        return True
    if config['fields'] != FIELDS or config['units'] != UNITS:
        return False
    # Historical reviews used name+address identities. Do not silently relabel
    # those saved reviews as having adopted the newer same-name contract.
    for row in config['rows']:
        identity=config['identities'].get(str(row),{})
        name=''.join(identity.get('store_name','').split())
        if not name or identity.get('store_id') != 'STORE_'+sha256(name.encode()).hexdigest()[:12]:
            return False
    return True


def configure(document, config):
    if not matches(document):
        return config
    config = deepcopy(config)
    config['units'] = dict(UNITS)
    config['batch_id'] = 'CENTRAL-' + datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d')
    config['delivery_date'] = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    config['note'] = '中央仓直配；同名门店合单；空冷链为零；冷链单件10×10×10cm'
    sheet = document['sheets'][config['sheet_index']]
    records = []
    addresses = defaultdict(list)
    for row in sheet['rows']:
        if row['row'] not in config['rows']:
            continue
        cells = {c['column']: c for c in row['cells']}
        def value(column):
            cell = cells.get(column, {})
            return cell.get('effective_value', cell.get('value'))
        name = ''.join(str(value(3) or '').split())
        address = str(value(4) or '').strip()
        if address:
            addresses[name].append(address)
        records.append((row['row'], name, address))
    for number, name, address in records:
        # Same-name orders use one destination. Preserve every original address
        # in source_refs; choose the most complete supplied spelling for display.
        destination = max(addresses[name], key=len) if addresses[name] else address
        config['identities'][str(number)] = dict(
            order_id=f'ORDER-{number:03d}',
            store_id='STORE_' + sha256(name.encode()).hexdigest()[:12],
            store_name=name, address=destination, batch_id=config['batch_id'], note='')
    return config


def normalize(document, config, item):
    if not matches(document, config):
        return item
    normalized = item['normalized']
    cold_fields = ('cold_quantity', 'cold_weight_kg', 'cold_volume_m3')
    blank = all(normalized[k] is None for k in cold_fields)
    if blank:
        normalized.update(cold_quantity=0, cold_weight_kg=0, cold_volume_m3=0)
    quantity = normalized['cold_quantity']
    if quantity is not None and isinstance(quantity, (int, float)) and quantity >= 0:
        source_volume = normalized['cold_volume_m3']
        normalized['cold_volume_m3'] = round(quantity * .001, 12)
        item['volume_basis'] = dict(kind='confirmed_dimensions', dimensions_cm=[10, 10, 10],
            source_volume_m3=source_volume, planning_volume_m3=normalized['cold_volume_m3'])
    item['missing'] = [k for k in item['missing'] if k not in (
        'cold_quantity', 'cold_weight', 'cold_volume') or not blank]
    item['issues'] = [i for i in item['issues'] if i['code'] not in (
        'formula_cache_unverified', 'cold_unknown')]
    item['cold_zero_confirmed'] = blank
    item['status'] = '已整理' if not item['missing'] and not item['issues'] else '待处理'
    return item


def summary(document, config):
    if not matches(document, config):
        return None
    return dict(id='central-orders-v1', warehouse='中央仓',
                cold_blank_is_zero=True, merge_same_store=True,
                cold_dimensions_cm=[10, 10, 10],
                notes=['一个中央仓出发', '同名门店多笔订单合并配送', '冷链空白为无冷链货物',
                       '冷链商品每件10×10×10cm', '原单与原公式保留'])


def parameters(document, review):
    """Editable demonstration setup, kept separate from retained source facts."""
    if not matches(document, review['config']):
        raise ValueError('这份订单表没有中央仓预设，请填写配送资料')
    root = Path(__file__).resolve().parent
    data = json.loads((root/'docs/reference-materials/仓配试算参数.json').read_text(encoding='utf-8'))
    warehouse = data['warehouse']
    warehouse['name'] = '中央仓'
    warehouse['settings']['travel_source'] = '内置有向距离矩阵；按门店次序构造，可替换实际道路资料'
    policy = warehouse['operational_policy']
    policy['goods'] = []
    policy['cold_boxes'] = []
    vehicle_ids = [v['id'] for v in data['vehicles']]
    stores = {}
    for item in review['assessment']['rows']:
        sid = item['identity']['store_id']
        stores.setdefault(sid, []).append(item)
    group = []
    count = 0
    def flush():
        nonlocal group, count
        if not group:
            return
        box_id = f'BOX_{len(policy["cold_boxes"])+1:03d}'
        policy['cold_boxes'].append(dict(id=box_id, length_cm=58, width_cm=31, height_cm=36,
            inner_length_cm=47.5, inner_width_cm=24.5, inner_height_cm=29.5,
            tare_weight_kg=4.5, inner_volume_m3=47.5*24.5*29.5/1_000_000, max_content_kg=8,
            temperature_range=[2,8], valid_minutes=270, max_openings=3, opening_minutes=2,
            allowed_vehicles=vehicle_ids, requires_cold_zone=False, activated_at_minutes=490))
        for sid, rows in group:
            for item in rows:
                if item['normalized']['cold_quantity']:
                    policy['goods'].append(dict(order=sid, sku=f'COLD_R{item["source_row"]}', kind='cold',
                        length_cm=10,width_cm=10,height_cm=10,fragile=False,max_top_load_kg=5,
                        cold_box_id=box_id,temperature_range=[2,8]))
        group = []; count = 0
    for sid, rows in stores.items():
        for item in rows:
            policy['goods'].append(dict(order=sid,sku=f'REG_R{item["source_row"]}',kind='regular',
                length_cm=60,width_cm=40,height_cm=30,fragile=False,max_top_load_kg=70))
        quantity = sum(item['normalized']['cold_quantity'] for item in rows)
        if not quantity:
            continue
        if quantity > 16:
            raise ValueError('单店冷链超过当前共享箱装载单元，请调整共享箱设置')
        if len(group) >= 3 or count+quantity > 16:
            flush()
        group.append((sid, rows)); count += quantity
    flush()
    policy['assumptions'] = [
        dict(parameter='共享保温箱尺寸与自重', evidence='厂家公开参数 QBLL0830',
             basis='https://www.haierbiomedical.co.uk/transport-cooler/vaccine-transport-cooler-passive-cooling.html'),
        dict(parameter='共享箱装载单元', evidence='标称30L；按公开内尺寸保持朝上摆放10cm方件，最多16件，不按标称升数推算件数',
             basis='几何核验；未模拟蓄冷剂和箱内取货路径'),
        dict(parameter='保温时限与开箱', evidence='本批设置：270分钟、每次2分钟、最多3次；不是设备验证结论',
             basis='调度参数，可按设备验证记录修改'),
        dict(parameter='道路、车辆与回收', evidence='内置批次设置：16辆车、每店2纸箱及1周转箱；道路按门店次序构造',
             basis='用于方案操作展示，可替换实际资料'),
        dict(parameter='商品与门店', evidence='用户确认：同名门店合单、空冷链为零、冷链边长10cm',
             basis='保留原单与逐行来源')]
    ids = ['WH', *stores]
    # Same reproducible distance model as the previous retained setup, rebuilt
    # for the confirmed same-name store identities. It is not a geocoder.
    data['legs'] = {f'{a}>{b}':dict(distance_km=round(2+abs(i-j)*1.5,2),
                       travel_minutes=round(3+abs(i-j)*1.2,2))
                    for i,a in enumerate(ids) for j,b in enumerate(ids) if a != b}
    data['stops'] = {}
    data['zero_cold_rows'] = [r['source_row'] for r in review['assessment']['rows'] if r.get('cold_zero_confirmed')]
    data['accept_cached_totals'] = True
    return data
