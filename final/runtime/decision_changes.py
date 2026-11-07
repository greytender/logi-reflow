"""Versioned pre-dispatch changes and their affected original routes."""
from copy import deepcopy
from uuid import uuid4

import application as app
from store import Conflict


def differences(task, data):
    old = {o['id']: o for o in task['data']['orders']}
    changes = []
    for order in data['orders']:
        before = old.get(order['id'])
        if not before:
            continue
        for field in ('open', 'due', 'close', 'service_minutes', 'pickup'):
            if before.get(field) != order.get(field):
                changes.append(dict(order_id=order['id'], field=field,
                                    before=deepcopy(before.get(field)), after=deepcopy(order.get(field))))
    return changes


def history(task, data):
    import multiwarehouse as mw
    plan = mw.current_plan(task)
    changes = differences(task, data)
    if not changes:
        return None
    affected = {c['order_id'] for c in changes}
    routes = [dict(warehouse_id=wid, vehicle_id=vid, affected_orders=[o for o in route['sequence'] if o in affected])
              for wid, group in (plan or {}).get('warehouses', {}).items()
              for vid, route in group['optimized']['routes'].items() if affected.intersection(route['sequence'])]
    return dict(id=uuid4().hex, created_at=app.now(), input_revision=task['input_revision']+1,
                before_plan_id=plan['id'] if plan else None,
                changes=changes, affected_routes=routes)


def preview(task, body):
    import multiwarehouse as mw
    import pilot
    if mw.has_facts(task):
        raise Conflict('已有签收或作业事实，不能改写出发前条件')
    if set(body)-{'id','revision','order_id','pickup','open','due','close','reviewed'}:
        raise ValueError('配送条件包含不支持的字段')
    data = deepcopy(task['data'])
    order = next((o for o in data['orders'] if o['id'] == body.get('order_id')), None)
    if order is None:
        raise ValueError('请选择当前批次的门店')
    if 'pickup' in body:
        value = body['pickup']
        if not isinstance(value, dict) or set(value) != {'cartons','totes'}:
            raise ValueError('请填写预计纸箱和周转箱数量')
        order['pickup'] = deepcopy(value)
    for key in ('open','due','close'):
        if key in body:
            order[key] = pilot.minute(body[key], '收货时间') if isinstance(body[key],str) else body[key]
    data = mw.canonicalize(data)
    change = history(task, data)
    if change is None:
        raise ValueError('条件没有变化，请修改回收数量或收货时间')
    return dict(data=data, impact=change)
