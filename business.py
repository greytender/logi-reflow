"""Departure-only tariff and packaging pickup rules; no execution receipts."""
from copy import deepcopy
from math import isfinite

import normal


def defaults():
    return dict(tariff=dict(mode='incremental', base=140, included_km=100,
                           per_km=2, per_stop=15),
                packaging=dict(carton_weight_kg=.4, carton_flat_m3=.003,
                               tote_weight_kg=1.5, tote_first_m3=.06, tote_nested_m3=.012))


def validate(raw, orders):
    if not isinstance(raw, dict) or set(raw) != {'tariff', 'packaging'}:
        raise ValueError('业务规则需要计费及空包装参数')
    result = deepcopy(raw)
    tariff, packaging = result['tariff'], result['packaging']
    if not isinstance(tariff, dict) or set(tariff) != {'mode', 'base', 'included_km', 'per_km', 'per_stop'}:
        raise ValueError('计费参数不完整')
    if tariff['mode'] not in ('linear', 'incremental', 'whole_above'):
        raise ValueError('请选择支持的计费分支')
    for field in ('base', 'included_km', 'per_km', 'per_stop'):
        normal.numeric(tariff[field], '计费 '+field, 0, 100000)
    if not isinstance(packaging, dict) or set(packaging) != set(defaults()['packaging']):
        raise ValueError('空包装参数不完整')
    for field, value in packaging.items():
        normal.numeric(value, '空包装 '+field, .000001, 1000)
    if packaging['tote_nested_m3'] > packaging['tote_first_m3']:
        raise ValueError('套叠后每增加一只的体积不能大于首只体积')
    for order in orders:
        pickup = order.get('pickup')
        if not isinstance(pickup, dict) or set(pickup) != {'cartons', 'totes'}:
            raise ValueError('每个门店需填写预计回收的纸箱和周转箱数量；未知不能当作零')
        for value in pickup.values():
            normal.numeric(value, '预计回收数量', 0, 100000)
            if int(value) != value:
                raise ValueError('预计回收数量必须为整数')
    return result


def pickup_totals(order, packaging):
    cartons, totes = order['pickup']['cartons'], order['pickup']['totes']
    return (cartons*packaging['carton_weight_kg'] + totes*packaging['tote_weight_kg'],
            cartons*packaging['carton_flat_m3'] +
            (packaging['tote_first_m3'] + (totes-1)*packaging['tote_nested_m3'] if totes else 0))


def apply(data, route, vehicle, orders, exact_km):
    """Decorate a time-feasible route; reject capacity breaches after every pickup."""
    if 'business' not in data:
        return route
    rules = data['business']; tariff = rules['tariff']
    cargo_w, cargo_v = normal.totals(route['sequence'], orders)
    collected_w = collected_v = 0
    peak_w, peak_v = cargo_w, cargo_v
    for stop in route['stops']:
        order = orders[stop['id']]
        cargo_w -= order['unit_weight_kg']; cargo_v -= order['unit_volume_m3']
        pw, pv = pickup_totals(order, rules['packaging'])
        collected_w += pw; collected_v += pv
        loaded_w, loaded_v = cargo_w + collected_w, cargo_v + collected_v
        if loaded_w > vehicle['max_weight_kg']+1e-8 or loaded_v > vehicle['max_volume_m3']+1e-8:
            return None
        peak_w, peak_v = max(peak_w, loaded_w), max(peak_v, loaded_v)
        stop.update(pickup=deepcopy(order['pickup']), pickup_weight_kg=round(pw, 6),
                    pickup_volume_m3=round(pv, 6), onboard_weight_kg=round(loaded_w, 6),
                    onboard_volume_m3=round(loaded_v, 6))
    km = exact_km
    base = distance_fee = stop_fee = 0
    if route['sequence']:
        if tariff['mode'] == 'linear':
            base = vehicle['fixed_cost']; distance_fee = km*vehicle['cost_per_km']
        else:
            base = tariff['base']; stop_fee = len(route['sequence'])*tariff['per_stop']
            charged = max(0, km-tariff['included_km']) if tariff['mode']=='incremental' else (km if km>tariff['included_km'] else 0)
            distance_fee = charged*tariff['per_km']
    route.update(cost=round(base+distance_fee+stop_fee, 4),
                 fee=dict(mode=tariff['mode'], base=round(base, 4), distance=round(distance_fee, 4),
                          stops=round(stop_fee, 4), pickup_extra=0,
                          estimated_billing_km=round(km, 4), tms_billing_km=None, actual_gps_km=None),
                 peak_weight_kg=round(peak_w, 6), peak_volume_m3=round(peak_v, 6),
                 return_pickup_weight_kg=round(collected_w, 6), return_pickup_volume_m3=round(collected_v, 6))
    return route


def audit_route(data, route, vehicle, orders, exact_km):
    """Independent arithmetic, never calls apply/pickup_totals."""
    rules = data['business']; p = rules['packaging']; t = rules['tariff']
    def equal(actual, expected, label):
        if not isinstance(actual, (float, int)) or isinstance(actual, bool) or not isfinite(actual) or abs(actual-expected) > .0002:
            raise ValueError('独立复核失败：'+label)
    seq = route['sequence']
    weight = sum(sum(x['quantity']*x['unit_weight_kg'] for x in orders[s]['lines']) for s in seq)
    volume = sum(sum(x['quantity']*x['unit_volume_m3'] for x in orders[s]['lines']) for s in seq)
    peak_w, peak_v = weight, volume
    pickups_w = pickups_v = 0
    for stop in route['stops']:
        order = orders[stop['id']]; counts = order['pickup']
        n, m = counts['cartons'], counts['totes']
        pw = n*p['carton_weight_kg'] + m*p['tote_weight_kg']
        pv = n*p['carton_flat_m3'] + (p['tote_first_m3']+(m-1)*p['tote_nested_m3'] if m>0 else 0)
        weight -= sum(x['quantity']*x['unit_weight_kg'] for x in order['lines'])
        volume -= sum(x['quantity']*x['unit_volume_m3'] for x in order['lines'])
        pickups_w += pw; pickups_v += pv
        loaded_w, loaded_v = weight+pickups_w, volume+pickups_v
        if loaded_w>vehicle['max_weight_kg']+1e-8 or loaded_v>vehicle['max_volume_m3']+1e-8:
            raise ValueError('独立复核失败：回收后超载')
        if stop.get('pickup') != counts:
            raise ValueError('独立复核失败：预计回收数量')
        for field, value in [('pickup_weight_kg', pw), ('pickup_volume_m3', pv),
                             ('onboard_weight_kg', loaded_w), ('onboard_volume_m3', loaded_v)]:
            equal(stop.get(field), value, field)
        peak_w, peak_v = max(peak_w, loaded_w), max(peak_v, loaded_v)
    base = mileage = stores = 0
    if seq:
        if t['mode'] == 'linear':
            base, mileage = vehicle['fixed_cost'], exact_km*vehicle['cost_per_km']
        else:
            base, stores = t['base'], len(seq)*t['per_stop']
            if exact_km > t['included_km']:
                mileage = (exact_km-t['included_km'] if t['mode']=='incremental' else exact_km)*t['per_km']
    fee = route.get('fee', {})
    if fee.get('mode') != t['mode'] or fee.get('tms_billing_km') is not None or fee.get('actual_gps_km') is not None:
        raise ValueError('独立复核失败：计费口径或里程来源')
    for field, value in [('base', base), ('distance', mileage), ('stops', stores),
                         ('pickup_extra', 0), ('estimated_billing_km', exact_km)]:
        equal(fee.get(field), value, '费用明细 '+field)
    for field, value in [('peak_weight_kg', peak_w), ('peak_volume_m3', peak_v),
                         ('return_pickup_weight_kg', pickups_w), ('return_pickup_volume_m3', pickups_v)]:
        equal(route.get(field), value, field)
    return base+mileage+stores
