"""Append-only operator observations at known nodes; never GPS evidence."""
import normal


def anchor(task, vehicle):
    """Select latest observation after this vehicle's most recent receipt."""
    receipts = [r for r in task.get('receipts', []) if r['vehicle'] == vehicle]
    observations = [o for o in task.get('observations', [])
                    if o['vehicle'] == vehicle and o['vehicle_receipt_count'] == len(receipts)]
    if observations:
        o = observations[-1]
        return dict(kind='observation', node=o['node'], time=o['observed_at'],
                    id=o['id'], source=o['source'])
    if receipts:
        r = receipts[-1]
        return dict(kind='receipt', node=(task['data']['settings']['depot_id']
                    if r['kind'] == 'return' else r['stop_id']), time=r['finish'],
                    id=r['id'], source=r['source'])
    v = next(v for v in task['data']['vehicles'] if v['id'] == vehicle)
    return dict(kind='shift', node=task['data']['settings']['depot_id'],
                time=v['shift_start'], id=None, source='planned')


def append(task, raw, reviewed):
    import execution
    from application import now
    from uuid import uuid4
    if reviewed is not True:
        raise ValueError('先核对位置、时刻和来源，再保存观察记录')
    if not isinstance(raw, dict):
        raise ValueError('位置或等待记录格式无效')
    if len(task.get('observations', [])) >= 500:
        raise ValueError('本任务已有500条位置/等待记录，请先导出历史再另行处理')
    plan = execution.confirmed(task)
    vehicle = raw.get('vehicle')
    if not isinstance(vehicle, str) or vehicle not in plan['result']['optimized']['routes']:
        raise ValueError('请选择已确认方案中的配送车辆')
    route = plan['result']['optimized']['routes'][vehicle]
    if not route['sequence']:
        raise ValueError('没有配送任务的车辆不能记录执行位置')
    receipts = [r for r in task.get('receipts', []) if r['vehicle'] == vehicle]
    if any(r['kind'] == 'return' for r in receipts):
        raise ValueError('该车已记录回仓，不能再追加位置或等待记录')
    kind = raw.get('kind')
    if kind not in ('position', 'wait'):
        raise ValueError('请选择已知节点位置或原地等待记录')
    node = raw.get('node')
    nodes = set(route['sequence']) | {task['data']['settings']['depot_id']}
    if not isinstance(node, str) or node not in nodes:
        raise ValueError('位置仅支持本车原方案门店或仓库等已知道路节点，不能填任意位置或GPS坐标')
    previous = anchor(task, vehicle)
    if kind == 'wait' and node != previous['node']:
        raise ValueError('等待记录必须保持当前已知节点；位置变化请选择位置记录')
    time = normal.numeric(raw.get('observed_at'), '观察时刻', 0, 1439)
    if int(time) != time:
        raise ValueError('观察时刻必须是当天的整分钟')
    if time < previous['time']:
        raise ValueError('观察时刻不能早于本车上一条回执、观察记录或班次开始')
    source = raw.get('source')
    if source not in ('simulated', 'manual'):
        raise ValueError('请选择模拟演练或人工录入来源')
    if task['data']['settings']['data_kind'] == 'simulated' and source != 'simulated':
        raise ValueError('构造任务只能记录模拟演练，不能标为真实定位')
    note = raw.get('note', '')
    if not isinstance(note, str) or len(note) > 500:
        raise ValueError('备注最多500字')
    if source == 'manual' and not note.strip():
        raise ValueError('人工观察请填写单据、司机消息等信息来源，真实性待核对')
    item = dict(id=uuid4().hex, plan_id=plan['id'], vehicle=vehicle, kind=kind,
        node=node, observed_at=int(time), source=source, note=note.strip(),
        vehicle_receipt_count=len(receipts), recorded_at=now(),
        remaining_plan_id=task.get('active_remaining_plan_id'))
    task.setdefault('observations', []).append(item)
    item['warnings_after'] = execution.view(task)['routes'][vehicle]['warnings']
    return item
