"""Declared operational constraints and replayable spatial instructions.

This is a conservative cuboid/declared-device model, never a temperature
simulation, equipment certification, GPS observation or enterprise dispatch.
No module-global policy, mutable cache, external service or database is used.
"""
from copy import deepcopy
from math import ceil, floor, isfinite

SCHEMA = 'operational_v1'
SCENE_SCHEMA = 'logireflow.operational-scene.v1'
MAX_ENTITIES = 160
MAX_PLACEMENT_CHECKS = 8192
MAX_FRAMES = 512
LAYOUT_VARIANTS = ('legacy', 'wide_ties', 'wide_lanes', 'cold_last', 'large_first', 'heavy_first')
HANDLING_VARIANTS = ('immediate', 'stop_batch')
FLEET_LAYOUT = dict(packing_variant='route_lanes', handling_variant='stop_batch')


class _BudgetExceeded(ValueError):
    """A finite constructor stopped; never proof of physical infeasibility."""

BOUNDARIES = [
    '按声明的箱体、车厢、设备温区和有效时限核验，不推算实际温度或冷链合规',
    '长方体、尾门+x、保持向上；全底面单箱支撑；有限候选放置失败不证明物理无解',
    '回收纸箱/周转箱按明确压平和套叠箱规建模；每店独立套叠',
    '倒货只在声明的临时地面空间中执行，动作工时为输入代理，非现场实测',
    '共享箱内仅核验初始箱内装入几何；开箱交付按声明工时处理，未验证箱内逐件取货路径',
    '无实时温度/GPS/TMS、轮包/轴荷/制动稳定/人员通道、制冷能耗或自动补能排程',
]


def _num(x, label, low=0, high=100000):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not isfinite(x) or not low <= x <= high:
        raise ValueError(label + '需为范围内有限数字')
    return x


def _keys(row, required, optional=()):
    if not isinstance(row, dict) or not set(required) <= set(row) or set(row)-set(required)-set(optional):
        raise ValueError('约束字段不完整或含未映射字段：' + ','.join(required))


def _dims(row):
    for key in ('length_cm', 'width_cm', 'height_cm'):
        _num(row.get(key), key, .01, 2000)


def _temp(value, label):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(label+'需为[min,max]摄氏温度范围')
    for t in value:
        _num(t, label, -100, 100)
    if value[0] > value[1]:
        raise ValueError(label+'范围倒置')


def validate_policy(data, raw_policy):
    """Strict, optional per-warehouse contract. Missing is not passed."""
    if raw_policy is None:
        return None
    _keys(raw_policy, ('schema', 'data_kind', 'goods', 'cold_boxes', 'vehicle_geometry', 'pickup_geometry', 'handling'),
          ('energy', 'preparation', 'returns', 'assumptions', 'incompatible_pairs'))
    p = deepcopy(raw_policy)
    if 'assumptions' in p and (not isinstance(p['assumptions'],list) or any(not isinstance(a,dict) or not {'parameter','evidence','basis'} <= set(a) for a in p['assumptions'])):
        raise ValueError('假设来源需为含parameter/evidence/basis的列表')
    if p['schema'] != SCHEMA or p['data_kind'] != data['settings']['data_kind']:
        raise ValueError('约束版本或数据来源声明与任务不一致')
    orders = {o['id']: o for o in data['orders']}
    vids = {v['id'] for v in data['vehicles']}
    demand = {(o['id'], l['sku']): l for o in data['orders'] for l in o['lines']}
    seen = set()
    if not isinstance(p['goods'], list) or not isinstance(p['cold_boxes'], list) or not isinstance(p['vehicle_geometry'], list):
        raise ValueError('goods/cold_boxes/vehicle_geometry需为列表')
    for g in p['goods']:
        _keys(g, ('order', 'sku', 'kind', 'length_cm', 'width_cm', 'height_cm', 'fragile', 'max_top_load_kg'),
              ('cold_box_id', 'temperature_range', 'compatibility_group'))
        key = (g['order'], g['sku'])
        if key not in demand or key in seen:
            raise ValueError('约束订单/SKU未知或重复')
        seen.add(key); _dims(g)
        if type(g['fragile']) is not bool:
            raise ValueError('fragile需为布尔值')
        _num(g['max_top_load_kg'], '累计承重')
        if g['kind'] not in ('regular', 'cold'):
            raise ValueError('goods.kind仅支持regular/cold')
        if abs(g['length_cm']*g['width_cm']*g['height_cm']/1e6-demand[key]['unit_volume_m3']) > 1e-7:
            raise ValueError('商品尺寸与每件体积不一致，不自动修正')
        if g['kind'] == 'cold':
            if not g.get('cold_box_id'):
                raise ValueError('冷链商品缺少共享箱归属')
            _temp(g.get('temperature_range'), '商品温区')
        if 'compatibility_group' in g and (not isinstance(g['compatibility_group'],str) or not g['compatibility_group']):
            raise ValueError('货物兼容组需为非空文字')
    if seen != set(demand):
        raise ValueError('约束必须覆盖全部订单/SKU，不能把未标记货物当常温')
    if 'incompatible_pairs' in p:
        groups={g.get('compatibility_group') for g in p['goods']}
        if not isinstance(p['incompatible_pairs'],list) or any(not isinstance(pair,list) or len(pair)!=2 or pair[0]==pair[1] or not set(pair)<=groups for pair in p['incompatible_pairs']):
            raise ValueError('禁混装配对需引用两个已声明不同的兼容组')
    geometry = {}
    for v in p['vehicle_geometry']:
        _keys(v, ('id', 'length_cm', 'width_cm', 'height_cm'), ('cold_zone',))
        _dims(v)
        if v['id'] not in vids or v['id'] in geometry:
            raise ValueError('车厢编号未知或重复')
        if 'cold_zone' in v:
            z = v['cold_zone']; _keys(z, ('x', 'y', 'z', 'length_cm', 'width_cm', 'height_cm', 'temperature_range'))
            _dims(z); _temp(z['temperature_range'], '车厢温区')
            for a in ('x','y','z'): _num(z[a], '温区坐标')
            if any(z[a]+z[k] > v[k]+1e-8 for a,k in [('x','length_cm'),('y','width_cm'),('z','height_cm')]):
                raise ValueError('温区超出车厢')
        geometry[v['id']] = v
    if set(geometry) != vids:
        raise ValueError('约束需要覆盖全部车厢，不猜实际尺寸')
    cb = {}
    for b in p['cold_boxes']:
        _keys(b, ('id','length_cm','width_cm','height_cm','inner_length_cm','inner_width_cm','inner_height_cm','tare_weight_kg','inner_volume_m3','max_content_kg',
                  'temperature_range','valid_minutes','max_openings','opening_minutes','allowed_vehicles','requires_cold_zone','activated_at_minutes'),
              ('max_top_load_kg',))
        _dims(b); _temp(b['temperature_range'], '共享箱能力温区')
        if not isinstance(b['id'], str) or not b['id'] or b['id'] in cb: raise ValueError('共享箱编号为空或重复')
        for key in ('tare_weight_kg','inner_volume_m3','max_content_kg','valid_minutes','opening_minutes'):
            _num(b[key], key, .000001 if key in ('inner_volume_m3','valid_minutes') else 0)
        _num(b['max_openings'], '开启次数', 1, 10000)
        if int(b['max_openings']) != b['max_openings']: raise ValueError('开启次数需为整数')
        if type(b['requires_cold_zone']) is not bool: raise ValueError('requires_cold_zone需为布尔值')
        if not isinstance(b['allowed_vehicles'],list) or not b['allowed_vehicles'] or not set(b['allowed_vehicles']) <= vids:
            raise ValueError('共享箱准入车辆缺失或未知')
        outer=b['length_cm']*b['width_cm']*b['height_cm']/1e6
        if b['inner_volume_m3'] > outer+1e-8: raise ValueError('共享箱内部容量大于外部体积')
        for outer_key,inner_key in [('length_cm','inner_length_cm'),('width_cm','inner_width_cm'),('height_cm','inner_height_cm')]:
            _num(b[inner_key],inner_key,.01,b[outer_key])
        if abs(b['inner_length_cm']*b['inner_width_cm']*b['inner_height_cm']/1e6-b['inner_volume_m3'])>1e-7:
            raise ValueError('共享箱内尺寸与内部体积不一致')
        if 'max_top_load_kg' in b: _num(b['max_top_load_kg'],'共享箱承重')
        if 'activated_at_minutes' in b:_num(b['activated_at_minutes'],'共享箱启用时刻',0,1440)
        cb[b['id']] = b
    used = {g['cold_box_id'] for g in p['goods'] if g['kind']=='cold'}
    if used != set(cb): raise ValueError('冷链共享箱定义与商品归属不一致')
    for bid,b in cb.items():
        members=[g for g in p['goods'] if g.get('cold_box_id')==bid]
        if sum(demand[(g['order'],g['sku'])]['quantity'] for g in members)>MAX_ENTITIES:
            raise ValueError('共享箱内部实体超过有限检查预算，未展开商品')
        volume=sum(demand[(g['order'],g['sku'])]['quantity']*demand[(g['order'],g['sku'])]['unit_volume_m3'] for g in members)
        weight=sum(demand[(g['order'],g['sku'])]['quantity']*demand[(g['order'],g['sku'])]['unit_weight_kg'] for g in members)
        if volume>b['inner_volume_m3']+1e-8 or weight>b['max_content_kg']+1e-8: raise ValueError('共享冷链箱内部容量或承重不足')
        for g in members:
            t=g['temperature_range']; ability=b['temperature_range']
            if ability[0]<t[0] or ability[1]>t[1]: raise ValueError('共享箱能力温区未完全落入商品允许温区')
            if any(g[k]>b['inner_'+k]+1e-7 for k in ('length_cm','width_cm','height_cm')):
                raise ValueError('冷链商品不能按声明向上朝向装入共享箱内尺寸')
        contents=[];inner_work={'placement_checks':0};inner_vehicle=dict(length_cm=b['inner_length_cm'],width_cm=b['inner_width_cm'],height_cm=b['inner_height_cm'],max_volume_m3=b['inner_volume_m3'],max_weight_kg=b['max_content_kg'])
        for g in sorted(members,key=lambda g:(-g['length_cm']*g['width_cm'],g['order'],g['sku'])):
            line=demand[(g['order'],g['sku'])]
            for j in range(line['quantity']):
                if len(contents)>=MAX_ENTITIES:raise ValueError('共享箱内部实体超过有限检查预算')
                entity=_entity(f"{g['order']}/{g['sku']}/{j+1}",g['order'],'cold_content',g,line['unit_weight_kg'])
                placed=_place(entity,contents,inner_vehicle,budget=inner_work)
                if placed is None:raise ValueError('共享箱内部有限装载未找到，不能仅凭总体积宣称装入')
                contents.append(placed)
    _keys(p['handling'], ('allow_rehandling','move_minutes','temporary_slots','pickup_minutes_per_unit'),('temporary_area',))
    h=p['handling']
    if type(h['allow_rehandling']) is not bool: raise ValueError('倒货开关需为布尔值')
    for k in ('move_minutes','temporary_slots','pickup_minutes_per_unit'): _num(h[k], k)
    if int(h['temporary_slots']) != h['temporary_slots']: raise ValueError('临时地面位需为整数')
    if h['allow_rehandling'] and (h['move_minutes'] <= 0 or h['temporary_slots'] < 1):
        raise ValueError('允许倒货必须声明非零动作时间和地面位')
    if h['allow_rehandling']:
        area=h.get('temporary_area');_keys(area,('length_cm','width_cm','height_cm'),('temperature_range',));_dims(area)
        if 'temperature_range' in area:_temp(area['temperature_range'],'临时区温度')
    if not isinstance(p['pickup_geometry'],dict): raise ValueError('pickup_geometry需为对象')
    if 'business' in data:
        for key in ('carton','tote'):
            q=p['pickup_geometry'].get(key)
            _keys(q, ('length_cm','width_cm','height_cm','nested_height_cm') if key=='tote' else ('length_cm','width_cm','height_cm'))
            _dims(q)
            if key=='tote': _num(q['nested_height_cm'],'套叠增高',.01,q['height_cm'])
        cfg=data['business']['packaging']; ca=p['pickup_geometry']['carton'];to=p['pickup_geometry']['tote']
        for actual,expected in [(ca['length_cm']*ca['width_cm']*ca['height_cm']/1e6,cfg['carton_flat_m3']),
                                (to['length_cm']*to['width_cm']*to['height_cm']/1e6,cfg['tote_first_m3']),
                                (to['length_cm']*to['width_cm']*to['nested_height_cm']/1e6,cfg['tote_nested_m3'])]:
            if abs(actual-expected)>1e-7:raise ValueError('回收几何与压平/套叠体积参数不一致')
    if 'energy' in p:
        if not isinstance(p['energy'],list):raise ValueError('energy需为列表')
        seen=set()
        for e in p['energy']:
            _keys(e,('vehicle_id','available_kwh','reserve_kwh','kwh_per_km'))
            if e['vehicle_id'] not in vids or e['vehicle_id'] in seen:raise ValueError('能耗车辆未知或重复')
            seen.add(e['vehicle_id'])
            for k in ('available_kwh','reserve_kwh','kwh_per_km'):_num(e[k],k,.000001 if k=='kwh_per_km' else 0)
            if e['reserve_kwh']>e['available_kwh']:raise ValueError('预留电量超过可用电量')
        if seen!=vids:raise ValueError('启用能耗检查必须覆盖全部车辆')
    if 'preparation' in p:
        if not isinstance(p['preparation'],list):raise ValueError('preparation需为列表')
        seen=set()
        for r in p['preparation']:
            _keys(r,('vehicle_id','at_depot_minutes','load_minutes','ready_deadline_minutes'))
            if r['vehicle_id'] not in vids or r['vehicle_id'] in seen:raise ValueError('装车准备车辆未知或重复')
            seen.add(r['vehicle_id'])
            for k in ('at_depot_minutes','load_minutes','ready_deadline_minutes'):_num(r[k],k,0,1440)
        if seen!=vids:raise ValueError('启用装车准备检查必须覆盖全部车辆')
    if 'returns' in p:
        if not isinstance(p['returns'],list):raise ValueError('returns需为列表')
        ids=set()
        for r in p['returns']:
            _keys(r,('id','order','quantity','length_cm','width_cm','height_cm','unit_weight_kg','kind','fragile','max_top_load_kg','sealed'))
            if r['id'] in ids or r['order'] not in orders:raise ValueError('退货编号重复或门店未知')
            ids.add(r['id']);_dims(r);_num(r['quantity'],'退货件数',1,MAX_ENTITIES)
            if int(r['quantity'])!=r['quantity'] or type(r['fragile']) is not bool:raise ValueError('退货数量或易碎标记无效')
            if r['kind']!='ambient':raise ValueError('当前退货只支持声明常温、独立隔离箱；冷链退货未建模')
            if r['sealed'] is not True:raise ValueError('常温退货须声明封闭隔离包装，开放/泄漏退货未建模')
            _num(r['unit_weight_kg'],'退货重量');_num(r['max_top_load_kg'],'退货承重')
    return p


def _entity(id, order, kind, row, weight, **extra):
    return dict(id=id,order=order,kind=kind,weight_kg=weight,dx=row['length_cm'],dy=row['width_cm'],dz=row['height_cm'],
                fragile=row.get('fragile',False),max_top_load_kg=row.get('max_top_load_kg',0),**extra)


def _overlap(a,b,axes=('x','y','z')):
    for k in axes:
        if not min(a[k]+a['d'+k],b[k]+b['d'+k])-max(a[k],b[k]) > 1e-7:
            return False
    return True


def audit_state(boxes, vehicle):
    """Independent arithmetic checks of a complete intermediate cuboid state."""
    fields = ('x','y','z','dx','dy','dz','weight_kg')
    if any(any(not isinstance(b.get(k),(int,float)) or isinstance(b[k],bool) or not isfinite(b[k])
               for k in fields) for b in boxes):
        return dict(passed=False,errors=['invalid_coordinates'])
    errors=[];parents={};ids={b['id'] for b in boxes}
    if len(ids)!=len(boxes):errors.append('duplicate_box')
    if sum(b['weight_kg'] for b in boxes)>vehicle['max_weight_kg']+1e-7:errors.append('weight_capacity')
    if sum(b['dx']*b['dy']*b['dz']/1e6 for b in boxes)>vehicle['max_volume_m3']+1e-7:errors.append('volume_capacity')
    # Sweep along the vehicle length to avoid comparing distant boxes.
    along_length = sorted(boxes, key=lambda b: b['x'])
    for i,b in enumerate(along_length):
        if min(b['x'],b['y'],b['z'])<0 or min(b['dx'],b['dy'],b['dz'])<=0:errors.append('invalid_dimensions')
        if b['weight_kg'] < 0:errors.append('negative_weight')
        if any(b[a]+b['d'+a]>vehicle[k]+1e-7 for a,k in [('x','length_cm'),('y','width_cm'),('z','height_cm')]):errors.append('outside_vehicle')
        for c in along_length[i+1:]:
            if c['x']>=b['x']+b['dx']-1e-7:break
            if _overlap(b,c):errors.append('overlap')
        parents[b['id']]=None
        if b['z']>1e-7:
            support=[c for c in boxes if c['id']!=b['id'] and abs(c['z']+c['dz']-b['z'])<1e-7 and c['x']<=b['x']+1e-7 and c['y']<=b['y']+1e-7 and c['x']+c['dx']>=b['x']+b['dx']-1e-7 and c['y']+c['dy']>=b['y']+b['dy']-1e-7]
            if len(support)==1:parents[b['id']]=support[0]['id']
            else:errors.append('unsupported')
        if b.get('zone'):
            z=b['zone']
            if any(b[a]<z[a]-1e-7 or b[a]+b['d'+a]>z[a]+z[k]+1e-7 for a,k in [('x','length_cm'),('y','width_cm'),('z','height_cm')]):errors.append('cold_zone_position')
        reserved=vehicle.get('cold_zone')
        if reserved and b.get('kind')!='cold_box':
            region={**reserved,'dx':reserved['length_cm'],'dy':reserved['width_cm'],'dz':reserved['height_cm']}
            if _overlap(b,region):errors.append('ambient_in_cold_zone')
    loads={id:0 for id in ids}
    for b in boxes:
        parent=parents.get(b['id']);seen=set()
        while parent:
            if parent in seen:errors.append('support_cycle');break
            seen.add(parent);loads[parent]+=b['weight_kg'];parent=parents.get(parent)
    for b in boxes:
        if b.get('fragile') and loads[b['id']]>1e-7:errors.append('fragile_topload')
        if loads[b['id']]>b.get('max_top_load_kg',0)+1e-7:errors.append('topload_capacity')
    return dict(passed=not errors,errors=sorted(set(errors)))


def _place(box, boxes, vehicle, require_door_access=False, budget=None, point_order='depth', region=None):
    points={(0,0,0)}
    if region:points.add((0,region[0],0))
    if box.get('zone'):points.add(tuple(box['zone'][k] for k in ('x','y','z')))
    if vehicle.get('cold_zone') and box.get('kind')!='cold_box':
        z=vehicle['cold_zone']
        points.update([(z['x']+z['length_cm'],0,0),(0,z['y']+z['width_cm'],0),(0,0,z['z']+z['height_cm'])])
    for b in boxes:
        points.update([(b['x']+b['dx'],b['y'],b['z']),(b['x'],b['y']+b['dy'],b['z']),(b['x'],b['y'],b['z']+b['dz'])])
    point_keys = dict(width=lambda q:(q[2],q[1],q[0]),
                      column=lambda q:(q[0],q[1],q[2]),
                      depth=lambda q:(q[2],q[0],q[1]))
    point_key = point_keys[point_order]
    if (sum(b['weight_kg'] for b in boxes)+box['weight_kg']>vehicle['max_weight_kg']+1e-7 or
            (sum(b['dx']*b['dy']*b['dz'] for b in boxes)+box['dx']*box['dy']*box['dz'])/1e6>vehicle['max_volume_m3']+1e-7):
        return None
    for x,y,z in sorted(points,key=point_key):
        if region and (y<region[0]-1e-7 or y+box['dy']>region[1]+1e-7):continue
        if budget is not None:
            if budget['placement_checks']>=MAX_PLACEMENT_CHECKS:
                raise _BudgetExceeded('有限空间构造的候选放置检查预算耗尽，未证明无解')
            budget['placement_checks']+=1
        candidate={**box,'x':x,'y':y,'z':z}
        if (x+box['dx']>vehicle['length_cm']+1e-7 or y+box['dy']>vehicle['width_cm']+1e-7 or
                z+box['dz']>vehicle['height_cm']+1e-7 or any(_overlap(candidate,b) for b in boxes)):
            continue
        if require_door_access and _blocking(candidate,boxes):
            continue
        if audit_state(boxes+[candidate],vehicle)['passed']:
            return candidate
    return None


def _blocking(target,boxes):
    return [b for b in boxes if b['id']!=target['id'] and (
        (_overlap(target,b,('y','z')) and b['x']>=target['x']+target['dx']-1e-7) or
        (_overlap(target,b,('x','y')) and b['z']>=target['z']+target['dz']-1e-7))]


def readiness_issue(route, vehicle, preparation, energy):
    """Reject departures that already fail a declared preparation or energy bound."""
    if not route['sequence']:
        return None
    if preparation:
        ready = preparation['at_depot_minutes'] + preparation['load_minutes']
        if ready > min(preparation['ready_deadline_minutes'], vehicle['shift_start']) + 1e-7:
            return dict(code='load_readiness', message='到库与装车时长不满足准备截止/发车时刻',
                        vehicle_id=vehicle['id'], stop_id=None)
    if energy and route['distance']*energy['kwh_per_km'] + energy['reserve_kwh'] > energy['available_kwh'] + 1e-7:
        return dict(code='energy', message='往返能耗与预留超过声明可用电量',
                    vehicle_id=vehicle['id'], stop_id=None)
    return None


def _coverage(policy, errors):
    entries=[('identity_capacity','整店归属、载重容积','VERIFIED_WITHIN_MODEL'),
             ('cold_chain','共享冷链箱容量、温区、有效时间及开箱','VERIFIED_WITHIN_MODEL'),
             ('recovery_spatial','沿途回收几何与有代价倒货','VERIFIED_WITHIN_MODEL'),
             ('time_windows','累计服务、倒货、开箱与硬时间窗','VERIFIED_WITHIN_MODEL'),
             ('energy','声明能耗及回仓预留','VERIFIED_WITHIN_MODEL' if policy and policy.get('energy') else 'NOT_CONFIGURED'),
             ('load_readiness','到库与装车准备截止','VERIFIED_WITHIN_MODEL' if policy and policy.get('preparation') else 'NOT_CONFIGURED'),
             ('goods_returns','声明常温退货隔离箱','VERIFIED_WITHIN_MODEL' if policy and 'returns' in policy else 'NOT_CONFIGURED'),
             ('goods_compatibility','声明异味/排斥货物禁混装','VERIFIED_WITHIN_MODEL' if policy and 'incompatible_pairs' in policy else 'NOT_CONFIGURED'),
             ('live_temperature','实际温度监测与质量责任','NOT_MODELED'),
             ('charging_schedule','补能站与跨批次车辆占用','NOT_MODELED'),
             ('enterprise_settlement','TMS合同里程与正式结算','NOT_MODELED'),
             ('physical_safety','轴荷、制动稳定、人员作业安全','NOT_MODELED')]
    entries.append(('cold_internal_access','共享箱内逐件取货路径','NOT_MODELED'))
    return [dict(key=k,label=l,status=('NOT_CONFIGURED' if not policy and s=='VERIFIED_WITHIN_MODEL' else 'REJECTED' if errors and s=='VERIFIED_WITHIN_MODEL' else s),
                 missing=[] if policy and s=='VERIFIED_WITHIN_MODEL' else [l+'未验证'],risk='仅对声明输入和模型有效' if s=='VERIFIED_WITHIN_MODEL' else '不能据此声称全部业务约束通过') for k,l,s in entries]


def evaluate_route(data, route, vehicle, policy, *, packing_variant='legacy', handling_variant='immediate', _work=None):
    if packing_variant not in (*LAYOUT_VARIANTS, 'stop_access', FLEET_LAYOUT['packing_variant']):
        raise ValueError('未知的有限装载策略')
    if handling_variant not in HANDLING_VARIANTS:
        raise ValueError('未知的有限装卸策略')
    try:
        return _evaluate_route(data,route,vehicle,policy,packing_variant=packing_variant,handling_variant=handling_variant,work=_work)
    except _BudgetExceeded as error:
        errors=[dict(code='search_budget_exhausted',message=str(error),vehicle_id=vehicle['id'],stop_id=None)]
        return dict(status='SEARCH_BUDGET_EXHAUSTED',passed=False,errors=errors,warnings=[],
                    frames=[],actions=[],boxes=[],vehicle_id=vehicle['id'],vehicle=deepcopy(vehicle),
                    coverage=_coverage(policy,errors),adjusted_route=deepcopy(route),
                    time_adjustments=dict(extra_minutes=0,rehandle_moves=0,return_at=route['return_at']),
                    boundaries=BOUNDARIES,budget=dict(max_entities=MAX_ENTITIES,
                        max_placement_checks=MAX_PLACEMENT_CHECKS,max_frames=MAX_FRAMES))


def _evaluate_route(data, route, vehicle, policy, *, packing_variant='legacy', handling_variant='immediate', work=None):
    """Evaluate one ordered route, recalculate all declared action durations.

    Returns adjusted_route: times/loads reflect cold shells, returns and handling.
    Cost/distance never change here. A caller should independently audit times.
    """
    if policy is None:return dict(status='NOT_CONFIGURED',passed=None,errors=[],warnings=[],coverage=_coverage(None,[]),frames=[],actions=[],adjusted_route=deepcopy(route))
    p=policy;errors=[];warnings=[];seq=route['sequence'];orders={o['id']:o for o in data['orders']};vid=vehicle['id']
    geom=next(v for v in p['vehicle_geometry'] if v['id']==vid);v={**vehicle,**geom}
    report=dict(status='VERIFIED_WITHIN_DECLARED_MODEL',passed=True,errors=errors,warnings=warnings,frames=[],actions=[],vehicle=v,vehicle_id=vid,boxes=[])
    def fail(code,message,stop=None):errors.append(dict(code=code,message=message,vehicle_id=vid,stop_id=stop))
    if len(seq)!=len(set(seq)) or any(s not in orders for s in seq):
        fail('identity','路线含未知/重复门店');report.update(passed=False,status='REJECTED',coverage=_coverage(p,errors));return report
    if not seq:
        report.update(adjusted_route=deepcopy(route),coverage=_coverage(p,[]));return report
    preparation=next((item for item in p.get('preparation',[]) if item['vehicle_id']==vid),None)
    energy=next((item for item in p.get('energy',[]) if item['vehicle_id']==vid),None)
    issue=readiness_issue(route,vehicle,preparation,energy)
    if issue:
        errors.append(issue)
        report.update(passed=False,status='REJECTED',coverage=_coverage(p,errors),adjusted_route=deepcopy(route))
        return report
    entity_count=sum(l['quantity'] for o in data['orders'] if o['id'] in seq for l in o['lines']
                     if next(g for g in p['goods'] if g['order']==o['id'] and g['sku']==l['sku'])['kind']=='regular')
    entity_count+=len({g['cold_box_id'] for g in p['goods'] if g['order'] in seq and g['kind']=='cold'})
    entity_count+=sum(r['quantity'] for r in p.get('returns',[]) if r['order'] in seq)
    entity_count+=2*len(seq) if 'business' in data else 0
    if entity_count>MAX_ENTITIES:
        fail('entity_budget','配送、退货与回收实体超过有限空间检查上限')
        report.update(passed=False,status='REJECTED',adjusted_route=deepcopy(route),coverage=_coverage(p,errors),time_adjustments=dict(extra_minutes=0,rehandle_moves=0,return_at=route['return_at']))
        return report
    definitions=[];cold_defs={b['id']:b for b in p['cold_boxes']};cold_members={};line_defs={(o['id'],l['sku']):l for o in data['orders'] for l in o['lines']}
    groups={g.get('compatibility_group') for g in p['goods'] if g['order'] in seq}
    for pair in p.get('incompatible_pairs',[]):
        if set(pair)<=groups:fail('incompatible_goods','声明禁混装的货物组被分到同一车辆')
    for g in p['goods']:
        if g['order'] not in seq:continue
        if vid not in orders[g['order']]['allowed_vehicles']:fail('vehicle_access','门店不允许该车',g['order'])
        line=line_defs[(g['order'],g['sku'])]
        if g['kind']=='cold':cold_members.setdefault(g['cold_box_id'],[]).append(g)
        else:
            for j in range(line['quantity']):definitions.append(_entity(f"{g['order']}/{g['sku']}/{j+1}",g['order'],'regular',g,line['unit_weight_kg'],sku=g['sku']))
    for bid,members in cold_members.items():
        b=cold_defs[bid]
        if vid not in b['allowed_vehicles']:fail('cold_vehicle','共享冷链箱不允许该车辆')
        weights={s:sum(line_defs[(g['order'],g['sku'])]['quantity']*line_defs[(g['order'],g['sku'])]['unit_weight_kg'] for g in members if g['order']==s) for s in seq}
        zone=geom.get('cold_zone')
        if b['requires_cold_zone']:
            if not zone:fail('cold_zone','共享箱要求车厢温区但车辆未配置')
            elif zone['temperature_range'][0]<b['temperature_range'][0] or zone['temperature_range'][1]>b['temperature_range'][1]:fail('cold_zone_temperature','车辆温区超出共享箱声明能力温区')
        definitions.append(_entity('COLD/'+bid,None,'cold_box',b,b['tare_weight_kg']+sum(weights.values()),cold_box_id=bid,orders=[s for s in seq if weights[s]>0],temperature_range=b['temperature_range'],remaining_content_weight_kg=sum(weights.values()),content_weights=weights,zone=zone if b['requires_cold_zone'] else None))
    if len(definitions)>MAX_ENTITIES:fail('entity_budget','明确实体超过有限空间检查上限')
    boxes=[];temporary=[];h=p['handling'];time=vehicle['shift_start'];elapsed_extra=0;rehandles=0;high_risk_rehandles=0
    if work is None:work={'placement_checks':0}
    point_order=('column' if packing_variant in ('stop_access','route_lanes') else
                 'width' if packing_variant=='wide_lanes' else 'depth')
    lane_regions={}
    if packing_variant=='route_lanes':
        regular=[box for box in definitions if box['kind']=='regular']
        cold=[box for box in definitions if box['kind']=='cold_box']
        pickup_specs=[]
        if 'business' in data:
            for sid in seq:
                for key,kind in [('cartons','carton'),('totes','tote')]:
                    if orders[sid]['pickup'][key]:pickup_specs.append(p['pickup_geometry'][kind])
        def lane_width(items,length_key,width_key):
            if not items:return 0
            return ceil(sum(item[length_key] for item in items)/v['length_cm'])*max(item[width_key] for item in items)
        cold_width=lane_width(cold,'dx','dy')
        pickup_width=lane_width(pickup_specs,'length_cm','width_cm')
        regular_width=max((box['dy'] for box in regular),default=0)
        # These lanes choose candidate positions only. Every resulting state
        # still receives the same geometric, capacity and support audit.
        if cold_width+pickup_width+regular_width<=v['width_cm']+1e-7:
            lane_regions=dict(cold_box=(0,cold_width),
                              recovery_carton=(cold_width,cold_width+pickup_width),
                              recovery_tote=(cold_width,cold_width+pickup_width),
                              regular=(cold_width+pickup_width,v['width_cm']))
            shapes={(box['dx'],box['dy'],box['dz'],box['weight_kg'],box['max_top_load_kg']) for box in regular}
            if len(shapes)==1:
                dx,dy,dz,weight,top_load=next(iter(shapes))
                upper=min(floor(v['height_cm']/dz),
                          floor(top_load/weight)+1 if weight>0 else floor(v['height_cm']/dz))
                slots=floor(v['length_cm']/dx)*floor((v['width_cm']-cold_width-pickup_width)/dy)*upper
                if len(regular)>slots:
                    fail('packing_not_found','当前通道构造未找到足够箱位，可调整分车后再核对')
    def place(box, current, container, require_door_access=False):
        region=lane_regions.get(box['kind']) if container is v else None
        return _place(box,current,container,require_door_access=require_door_access,budget=work,point_order=point_order,region=region)
    area=h.get('temporary_area',dict(length_cm=1,width_cm=1,height_cm=1))
    temp_vehicle={**area,'max_weight_kg':100000,'max_volume_m3':area['length_cm']*area['width_cm']*area['height_cm']/1e6}
    report['temporary_area']=temp_vehicle
    def frame(label,phase,stop=None,action=None):
        if len(report['frames'])>=MAX_FRAMES:
            raise _BudgetExceeded('装卸状态帧预算耗尽，停止构造并保留未验证状态')
        check=audit_state(boxes,v)
        if not check['passed']:fail('spatial_state','中间状态未通过：'+','.join(check['errors']),stop)
        temp_check=audit_state(temporary,temp_vehicle)
        if not temp_check['passed'] or len(temporary)>h['temporary_slots']:fail('temporary_spatial','临时地面空间/容量不足',stop)
        report['frames'].append(dict(index=len(report['frames']),label=label,phase=phase,stop_id=stop,time_minutes=round(time,4),boxes=deepcopy(boxes),temporary_boxes=deepcopy(temporary),action=deepcopy(action),audit=check))
    def action(kind,box,stop=None,source=None,target=None):
        a=dict(kind=kind,box_ids=[box['id']],stop_id=stop,high_risk=box.get('kind')=='cold_box' or box.get('fragile',False),**{'from':deepcopy(source),'to':deepcopy(target)})
        report['actions'].append(a);frame(kind,kind,stop,a)
    rank={s:i for i,s in enumerate(seq)}
    def load_key(b):
        station=-rank.get(b['order'],len(seq))
        area=-b['dx']*b['dy']
        if packing_variant in ('stop_access','route_lanes'):
            # Load last visits first. A shared shell must be accessible at
            # its earliest delivery and before that stop's regular cartons.
            if b['kind']=='cold_box':
                station=-min(rank[sid] for sid in b['orders'])
            return (station,b['kind']=='cold_box',area,b['id'])
        if packing_variant in ('wide_ties','wide_lanes'):
            return (station,area,b['dy'],b['id'])
        if packing_variant=='cold_last':
            return (b['kind']=='cold_box',station,area,b['id'])
        if packing_variant=='large_first':
            return (area,station,b['dy'],b['id'])
        if packing_variant=='heavy_first':
            return (-b['weight_kg'],station,area,b['id'])
        return (station,area,b['id'])
    for b in sorted(definitions,key=load_key):
        if errors:break
        placed=place(b,boxes,v,require_door_access=True)
        if placed is None:fail('packing_not_found','有限装载构造未找到位置，不证明无解');break
        boxes.append(placed);action('load',placed,target=placed)
    report['boxes']=deepcopy(boxes);frame('出发装载','departure')
    adjusted=deepcopy(route);adjusted['stops']=[];previous=data['settings']['depot_id'];openings={bid:0 for bid in cold_members};peak_w=sum(b['weight_kg'] for b in boxes);peak_v=sum(b['dx']*b['dy']*b['dz']/1e6 for b in boxes);late=[]
    adjusted.update(weight_kg=round(peak_w,6),volume_m3=round(peak_v,6))
    def restore_one(b,stop):
        nonlocal time,elapsed_extra,rehandles,high_risk_rehandles
        temporary.remove(b);b['zone']=b.pop('_original_zone',None)
        placed=place(b,boxes,v,require_door_access=True)
        if placed is None:fail('rehandle_restore','倒货后无法恢复安全摆放',stop);return False
        boxes.append(placed);time+=h['move_minutes'];elapsed_extra+=h['move_minutes'];rehandles+=1
        high_risk_rehandles+=int(b['kind']=='cold_box' or b.get('fragile',False));action('rehandle_in',placed,stop,source=b,target=placed)
        return True
    def remove_box(target,kind,stop):
        nonlocal time,elapsed_extra,rehandles,high_risk_rehandles
        if handling_variant=='stop_batch':
            staged_target=next((b for b in temporary if b['id']==target['id']),None)
            if staged_target is not None and not restore_one(staged_target,stop):return False
        target=next((b for b in boxes if b['id']==target['id']),None)
        if target is None:
            fail('missing_unload_entity','待卸箱体不在当前车厢',stop);return False
        blockers=_blocking(target,boxes)
        if blockers:
            if not h['allow_rehandling'] or len(blockers)>h['temporary_slots']:
                fail('unloading_blocked','卸货通道受阻且倒货空间/规则不足',stop);return False
            if len(temporary)+len(blockers)>h['temporary_slots']:
                fail('temporary_spatial','同站暂存与新增阻挡箱超过声明临时地面位',stop);return False
            # Select only topmost/door-accessible blockers; cascading support is checked.
            pending=list(blockers)
            while pending:
                available=[b for b in pending if not _blocking(b,boxes)]
                if not available:fail('rehandle_not_found','有限倒货未找到可逐件移出的阻挡箱顺序',stop);return False
                b=sorted(available,key=lambda x:(-x['z'],-x['x'],x['id']))[0]
                temp_box=deepcopy(b);original_zone=temp_box.pop('zone',None)
                if original_zone:
                    tr=area.get('temperature_range');br=b['temperature_range']
                    if not tr or tr[0]<br[0] or tr[1]>br[1]:fail('temporary_cold_zone','需车厢温区的冷链箱不能移出至未声明合适温区的临时区',stop);return False
                staged=place(temp_box,temporary,temp_vehicle)
                if staged is None:fail('temporary_spatial','临时区有限几何构造未找到安全箱位',stop);return False
                staged['_original_zone']=original_zone
                boxes.remove(b);temporary.append(staged);pending.remove(b);time+=h['move_minutes'];elapsed_extra+=h['move_minutes'];rehandles+=1
                high_risk_rehandles+=int(b['kind']=='cold_box' or b.get('fragile',False));action('rehandle_out',b,stop,source=b,target=staged)
        if kind=='cold_open':
            source=deepcopy(target)
            weights=target['content_weights'];removed=weights.get(stop,0)
            target['weight_kg']-=removed;target['remaining_content_weight_kg']-=removed
            action('cold_open',target,stop,source=source,target=target)
        else:
            boxes.remove(target);action(kind,target,stop,source=target)
        if handling_variant=='immediate':
            while temporary:
                if not restore_one(temporary[-1],stop):return False
        return True
    for sid in seq:
        if errors:break
        o=orders[sid];road=time+data['legs'][previous+'>'+sid]['travel_minutes'];time=max(road,o['open']);arrival=time;before_extra=elapsed_extra
        frame('到达 '+sid,'arrival',sid)
        targets=sorted([b for b in boxes if b['order']==sid or b['kind']=='cold_box' and sid in b['orders']],key=lambda b:(b['kind']!='cold_box',not b['fragile'],-b['z'],-b['x']))
        for target in targets:
            if target['kind']=='cold_box':
                bid=target['cold_box_id'];b=cold_defs[bid];openings[bid]+=1
                time+=b['opening_minutes'];elapsed_extra+=b['opening_minutes']
                if openings[bid]>b['max_openings']:fail('cold_openings','共享箱开启次数超过声明限额',sid)
                activated=b.get('activated_at_minutes',vehicle['shift_start'])
                if activated>vehicle['shift_start']:fail('cold_activation','共享箱启用时刻晚于发车时刻',sid)
                if time-activated>b['valid_minutes']+1e-7:fail('cold_duration','到店交付时超过设备声明有效时限',sid)
                if not remove_box(target,'cold_open',sid):break
            elif not remove_box(target,'unload',sid):break
        if handling_variant=='stop_batch':
            while temporary:
                if not restore_one(temporary[-1],sid):break
        time+=o['service_minutes']
        for bid,members in cold_members.items():
            if any(g['order']==sid for g in members):
                b=cold_defs[bid]
                if time-b.get('activated_at_minutes',vehicle['shift_start'])>b['valid_minutes']+1e-7:
                    fail('cold_duration','含搬倒/开箱/门店服务后交付超过声明有效时限',sid)
        pickup=o.get('pickup',{}) if 'business' in data else {};new=[]
        for kind,key in [('recovery_carton','cartons'),('recovery_tote','totes')]:
            count=pickup.get(key,0)
            if not count:continue
            q=deepcopy(p['pickup_geometry']['carton' if key=='cartons' else 'tote'])
            cfg=data['business']['packaging'];weight=count*cfg['carton_weight_kg' if key=='cartons' else 'tote_weight_kg']
            q['height_cm']=count*q['height_cm'] if key=='cartons' else q['height_cm']+(count-1)*q['nested_height_cm']
            new.append(_entity(f'PICKUP/{sid}/{key}',sid,kind,q,weight,count=count))
            extra=count*h['pickup_minutes_per_unit'];time+=extra;elapsed_extra+=extra
        for r in p.get('returns',[]):
            if r['order']==sid:
                for j in range(r['quantity']):new.append(_entity(f"RETURN/{r['id']}/{j+1}",sid,'goods_return',r,r['unit_weight_kg']))
                extra=r['quantity']*h['pickup_minutes_per_unit'];time+=extra;elapsed_extra+=extra
        for b in new:
            placed=place(b,boxes,v,require_door_access=True)
            if placed is None:fail('pickup_spatial_not_found','回收/退货有限空间构造未找到位置',sid);break
            boxes.append(placed);action('pickup',placed,sid,target=placed)
        if len(boxes)>MAX_ENTITIES:fail('entity_budget','回收后实体数超过有限检查预算',sid)
        deadline=time if data['settings']['window_rule']=='finish' else arrival
        if deadline>o['close']+1e-7:fail('hard_window','含开箱、倒货、回收服务后超过最晚收货时间',sid)
        late.append(max(0,deadline-o['due']))
        adjusted['stops'].append(dict(id=sid,road_arrival=round(road,4),arrival=round(arrival,4),finish=round(time,4),wait_minutes=round(arrival-road,4),late_minutes=round(late[-1],4),operational_extra_minutes=round(elapsed_extra-before_extra,4),onboard_weight_kg=round(sum(b['weight_kg'] for b in boxes),6),onboard_volume_m3=round(sum(b['dx']*b['dy']*b['dz']/1e6 for b in boxes),6),pickup=deepcopy(pickup)))
        peak_w=max(peak_w,sum(b['weight_kg'] for b in boxes));peak_v=max(peak_v,sum(b['dx']*b['dy']*b['dz']/1e6 for b in boxes));frame('离店 '+sid,'departure_stop',sid);previous=sid
    if not errors:
        time+=data['legs'][previous+'>'+data['settings']['depot_id']]['travel_minutes']
        if time>vehicle['shift_end']+1e-7:fail('return_shift','含操作工时后返仓超班次')
        frame('回仓交接','return')
    adjusted.update(return_at=round(time,4),late_orders=sum(x>1e-7 for x in late),late_minutes=round(sum(late),4),operational_extra_minutes=round(elapsed_extra,4),peak_weight_kg=round(peak_w,6),peak_volume_m3=round(peak_v,6))
    report.update(status='REJECTED' if errors else 'VERIFIED_WITHIN_DECLARED_MODEL',passed=not errors,coverage=_coverage(p,errors),adjusted_route=adjusted,time_adjustments=dict(extra_minutes=round(elapsed_extra,4),rehandle_moves=rehandles,high_risk_rehandle_moves=high_risk_rehandles,return_at=round(time,4)),cold_boxes=[dict(id=bid,openings=openings[bid],valid_minutes=cold_defs[bid]['valid_minutes']) for bid in cold_members])
    report['checks']=['逐帧几何与容量','共享冷链箱持续占位','设备温区和有效时限','开箱与倒货工时','逐站回收与退货','门店硬窗口与返仓班次']
    replay=audit_actions(report)
    if not replay['passed']:
        fail('instruction_replay','动作独立回放失败：'+','.join(replay['errors']))
        report.update(passed=False,status='REJECTED',coverage=_coverage(p,errors))
    report['instruction_audit']=replay
    report['packing_variant']=packing_variant
    report['handling_variant']=handling_variant
    report['placement_checks']=work['placement_checks']
    return report


def search_route_layouts(data, route, vehicle, policy, *, max_variants=6, stop_on_first_feasible=False, handling_variant='immediate'):
    """Opt-in research portfolio, retaining the legacy candidate under one budget.

    No box rotation, relaxed rule, live-state mutation or default dispatch change.
    The chosen report contains the actual layout/actions and must be preserved;
    evaluating the route again with the default constructor is a different test.
    """
    from time import perf_counter
    if isinstance(max_variants,bool) or not isinstance(max_variants,int) or not 1 <= max_variants <= len(LAYOUT_VARIANTS):
        raise ValueError('布局数量需为1至6的整数')
    if type(stop_on_first_feasible) is not bool:raise ValueError('首个可行停止开关需为布尔值')
    if handling_variant not in HANDLING_VARIANTS:raise ValueError('未知的有限装卸策略')
    started=perf_counter()
    p=validate_policy(data,policy)
    if p is None:raise ValueError('多布局试验需要完整声明约束')
    work={'placement_checks':0};trials=[];feasible=[];first=None
    for variant in LAYOUT_VARIANTS[:max_variants]:
        before=work['placement_checks']
        report=evaluate_route(data,route,vehicle,p,packing_variant=variant,handling_variant=handling_variant,_work=work)
        if first is None:first=report
        replay=audit_actions(report) if report.get('frames') else {'passed':not route['sequence']}
        valid=report.get('passed') is True and replay['passed']
        if valid:feasible.append(report)
        trials.append(dict(variant=variant,passed=valid,status=report['status'],
            error_codes=sorted({e['code'] for e in report['errors']}),
            placement_checks=work['placement_checks']-before))
        if valid and stop_on_first_feasible:break
        if report['status']=='SEARCH_BUDGET_EXHAUSTED':break
    def quality(report):
        adjusted=report['adjusted_route'];times=report.get('time_adjustments',{})
        return (adjusted['late_orders'],adjusted['late_minutes'],
            times.get('high_risk_rehandle_moves',0),times.get('rehandle_moves',0),adjusted['return_at'])
    chosen=deepcopy(min(feasible,key=quality) if feasible else first)
    exhausted=any(t['status']=='SEARCH_BUDGET_EXHAUSTED' for t in trials)
    if not feasible and exhausted:
        chosen['status']='SEARCH_BUDGET_EXHAUSTED'
        if not any(e['code']=='search_budget_exhausted' for e in chosen['errors']):
            chosen['errors'].append(dict(code='search_budget_exhausted',
                message='多布局共享预算耗尽，尚未找到完整方案，不证明无解',
                vehicle_id=vehicle['id'],stop_id=None))
        chosen['coverage']=_coverage(p,chosen['errors'])
    chosen['layout_search']=dict(schema='logireflow.layout-search.v1',experimental=True,
        selected_variant=chosen.get('packing_variant','legacy'),trials=trials,
        handling_variant=handling_variant,
        stop_on_first_feasible=stop_on_first_feasible,
        placement_checks=work['placement_checks'],max_placement_checks=MAX_PLACEMENT_CHECKS,
        budget_exhausted=exhausted,
        placement_budget_scope='路线内装载、临时区、恢复与回收；策略间共享；不含独立约束校验中的冷箱内部构造',
        elapsed_ms=round((perf_counter()-started)*1000,3),
        scope='固定路线、多种有限布局；不是几何全局最优或实际装卸证明')
    return chosen


def audit_actions(report):
    """Recheck recorded state transitions without invoking placement/search."""
    errors=[];previous={};previous_temp={};time=-1;dimensions={}
    for frame in report.get('frames',[]):
        boxes=frame['boxes'];current={b['id']:b for b in boxes};action=frame.get('action');temp={b['id']:b for b in frame.get('temporary_boxes',[])}
        if set(current)&set(temp):errors.append('entity_in_vehicle_and_temporary_area')
        if frame['time_minutes']<time-1e-7:errors.append('time_reversed')
        time=frame['time_minutes']
        check=audit_state(boxes,report['vehicle'])
        errors.extend(check['errors'])
        errors.extend(audit_state(frame.get('temporary_boxes',[]),report.get('temporary_area',report['vehicle']))['errors'])
        for b in boxes:
            size=tuple(b[k] for k in ('dx','dy','dz'))
            if b['id'] in dimensions and size!=dimensions[b['id']]:errors.append('box_dimensions_changed')
            dimensions[b['id']]=size
        added=set(current)-set(previous);removed=set(previous)-set(current)
        for bid in set(current)&set(previous):
            if any(current[bid][k]!=previous[bid][k] for k in ('x','y','z','dx','dy','dz')):errors.append('unexplained_repacking')
            if current[bid]['weight_kg']!=previous[bid]['weight_kg']:
                if not action or action['kind']!='cold_open' or bid not in action['box_ids']:
                    errors.append('unexplained_weight_change')
                else:
                    expected=previous[bid].get('content_weights',{}).get(frame['stop_id'],0)
                    if abs(previous[bid]['weight_kg']-current[bid]['weight_kg']-expected)>1e-7:errors.append('wrong_cold_delivery_weight')
        if action:
            ids=set(action['box_ids']);kind=action['kind']
            # Instructions must describe the same before/after payloads as the
            # independently stored states, especially a shared box opened in place.
            source_map=previous_temp if kind=='rehandle_in' else previous
            target_map=temp if kind=='rehandle_out' else current
            for key,reference in [('from',source_map),('to',target_map)]:
                if key not in action or action[key] is None:continue
                snapshot=action[key];actual=reference.get(snapshot.get('id'))
                fields=('id','x','y','z','dx','dy','dz','weight_kg','kind','remaining_content_weight_kg')
                if actual is None or snapshot.get('id') not in ids or any(snapshot.get(k)!=actual.get(k) for k in fields):
                    errors.append('instruction_snapshot_mismatch')
            if kind in ('load','pickup','rehandle_in') and (added!=ids or removed):errors.append('invalid_add_transition')
            elif kind in ('unload','rehandle_out') and (removed!=ids or added):errors.append('invalid_remove_transition')
            elif kind=='cold_open' and (added or removed):errors.append('cold_shell_disappeared')
            if kind not in ('load','pickup','rehandle_in','unload','rehandle_out','cold_open'):errors.append('unknown_action')
            if kind=='rehandle_out' and (set(temp)-set(previous_temp)!=ids or set(previous_temp)-set(temp)):errors.append('temporary_transfer_missing')
            elif kind=='rehandle_in' and (set(previous_temp)-set(temp)!=ids or set(temp)-set(previous_temp)):errors.append('temporary_restore_missing')
            elif kind not in ('rehandle_out','rehandle_in') and set(temp)!=set(previous_temp):errors.append('unexplained_temporary_change')
            if kind=='unload' and any(previous[id]['order']!=frame['stop_id'] for id in ids if id in previous):errors.append('wrong_store_unload')
            # Audit door access from the state before the action, independently
            # of the placement constructor; endpoint geometry alone can hide
            # an impossible move straight through another box.
            if kind in ('unload','rehandle_out','cold_open'):
                for bid in ids:
                    if bid not in previous:errors.append('missing_action_entity');continue
                    if _blocking(previous[bid],list(previous.values())):errors.append('blocked_action_path')
            if kind in ('load','pickup','rehandle_in'):
                for bid in ids:
                    if bid not in current:errors.append('missing_action_entity');continue
                    if _blocking(current[bid],list(previous.values())):errors.append('blocked_action_path')
            # A box moved to a temporary ground area keeps its identity,
            # dimensions, payload weight and temperature requirement.
            if kind in ('rehandle_out','rehandle_in'):
                source,destination=(previous,temp) if kind=='rehandle_out' else (previous_temp,current)
                for bid in ids:
                    if bid not in source or bid not in destination:continue
                    keys=('dx','dy','dz','weight_kg','kind','cold_box_id','temperature_range','remaining_content_weight_kg')
                    if any(source[bid].get(k)!=destination[bid].get(k) for k in keys):errors.append('temporary_entity_changed')
            if kind=='cold_open':
                for bid in ids:
                    if bid not in current or bid not in previous:continue
                    before,after=previous[bid],current[bid]
                    if before.get('kind')!='cold_box' or frame.get('stop_id') not in before.get('orders',[]):errors.append('invalid_cold_delivery')
                    expected=before.get('content_weights',{}).get(frame.get('stop_id'),0)
                    if abs(before.get('remaining_content_weight_kg',0)-after.get('remaining_content_weight_kg',0)-expected)>1e-7:errors.append('wrong_cold_remaining_weight')
        elif added or removed:errors.append('unexplained_box_change')
        previous=current;previous_temp=temp
    if previous_temp:errors.append('temporary_cargo_not_restored')
    return dict(passed=not errors,errors=sorted(set(errors)),states=len(report.get('frames',[])))


def compact_report(report):
    """Keep checked route evidence without shipping every cargo snapshot."""
    return {**{k: v for k, v in report.items() if k not in ('frames', 'actions')},
            'frame_count': report.get('frame_count', len(report.get('frames', []))),
            'action_count': report.get('action_count', len(report.get('actions', [])))}


def evaluate_plan(data, plan, policy, *, compact=False, reuse_checked=False, layout=None):
    p=validate_policy(data,policy)
    if p is None:return dict(status='NOT_CONFIGURED',passed=None,errors=[],warnings=[],routes={},coverage=_coverage(None,[]),boundaries=BOUNDARIES)
    reports={};errors=[];served=[];used_cold={}
    for vehicle in data['vehicles']:
        vid=vehicle['id'];route=plan['routes'][vid]
        r={k: v for k, v in route.items() if k != 'operational'}
        saved=route.get('operational') if reuse_checked else None
        report=saved if saved and saved.get('instruction_audit', {}).get('passed') is True else evaluate_route(data,r,vehicle,p,**(layout or {}))
        reports[vid]=compact_report(report) if compact else report
        errors.extend(report['errors']);served+=r['sequence']
        for c in report.get('cold_boxes',[]):
            if c['id'] in used_cold:errors.append(dict(code='shared_box_split',message='同一实体共享冷链箱不能同时分配多辆车',vehicle_id=vid,stop_id=None))
            used_cold[c['id']]=vid
    if len(served)!=len(set(served)):errors.append(dict(code='duplicate_store',message='整店配送重复分车',vehicle_id=None,stop_id=None))
    complete=not bool(plan.get('unserved')) and set(served)=={o['id'] for o in data['orders']}
    return dict(status='REJECTED' if errors else 'VERIFIED_WITHIN_DECLARED_MODEL',passed=not errors,complete=complete,errors=errors,warnings=[] if complete else ['存在未安排任务，核验仅覆盖已安排路线'],routes=reports,coverage=_coverage(p,errors),boundaries=BOUNDARIES,assumptions=deepcopy(p.get('assumptions',[])))


def build_scene(data, plan, policy):
    report=evaluate_plan(data,plan,policy)
    routes=[{k:deepcopy(r.get(k)) for k in ('vehicle_id','vehicle','boxes','frames','actions','checks','warnings','temporary_area')} for r in report['routes'].values() if r.get('frames')]
    return dict(schema=SCENE_SCHEMA,data_kind=data['settings']['data_kind'],status=report['status'],passed=report['passed'],complete=report.get('complete',False),routes=routes,coverage=report['coverage'],errors=report['errors'],boundaries=BOUNDARIES,assumptions=deepcopy(report.get('assumptions',[])))


def build_vehicle_scene(data, plan, policy, vehicle, verification):
    """Restore one vehicle's deterministic frames from its checked route."""
    p = validate_policy(data, policy)
    vehicle = next(item for item in data['vehicles'] if item['id']==vehicle['id'])
    route = {k: v for k, v in plan['routes'][vehicle['id']].items() if k != 'operational'}
    saved = verification['routes'][vehicle['id']]
    layout = {key: saved[key] for key in ('packing_variant', 'handling_variant') if key in saved}
    report = evaluate_route(data, route, vehicle, p, **layout)
    if compact_report(report) != saved:
        raise ValueError('该车装卸结果与已保存的核验记录不一致，请重新排车')
    frames = [{k: deepcopy(report.get(k)) for k in
               ('vehicle_id', 'vehicle', 'boxes', 'frames', 'actions', 'checks', 'warnings', 'temporary_area')}]
    return dict(schema=SCENE_SCHEMA, data_kind=data['settings']['data_kind'],
                status=verification['status'], passed=report['passed'], complete=verification['complete'],
                routes=frames, coverage=report['coverage'], errors=report['errors'],
                boundaries=BOUNDARIES, assumptions=deepcopy(verification.get('assumptions', [])))


def sample_policy(data):
    """Complete, adjustable constructed policy, never inferred enterprise rules."""
    goods=[]
    for o in data['orders']:
        for line in o['lines']:
            # Intended sample uses standard 60x40x30 cartons and 10cm cold units.
            cold=line['sku']=='COLD_UNIT'
            volume=line['unit_volume_m3']
            if cold:dims=(10,10,10)
            elif abs(volume-.072)<1e-8:dims=(60,40,30)
            else:
                side=(volume*1e6)**(1/3);dims=(side,side,side)
            g=dict(order=o['id'],sku=line['sku'],kind='cold' if cold else 'regular',length_cm=dims[0],width_cm=dims[1],height_cm=dims[2],fragile=False,max_top_load_kg=70)
            if cold:g.update(cold_box_id='SHARED_COLD_1',temperature_range=[2,8])
            goods.append(g)
    cold_boxes=[]
    if any(g['kind']=='cold' for g in goods):
        cold_boxes=[dict(id='SHARED_COLD_1',length_cm=60,width_cm=40,height_cm=45,inner_length_cm=40,inner_width_cm=25,inner_height_cm=40,tare_weight_kg=8,inner_volume_m3=.04,max_content_kg=25,temperature_range=[2,8],valid_minutes=180,max_openings=6,opening_minutes=2,allowed_vehicles=[v['id'] for v in data['vehicles']],requires_cold_zone=False,max_top_load_kg=0,activated_at_minutes=min(v['shift_start'] for v in data['vehicles'])-20)]
    return validate_policy(data,dict(schema=SCHEMA,data_kind=data['settings']['data_kind'],goods=goods,cold_boxes=cold_boxes,
        vehicle_geometry=[dict(id=v['id'],length_cm=420,width_cm=210,height_cm=210) for v in data['vehicles']],
        pickup_geometry=dict(carton=dict(length_cm=60,width_cm=40,height_cm=1.25),tote=dict(length_cm=60,width_cm=40,height_cm=25,nested_height_cm=5)),
        handling=dict(allow_rehandling=True,move_minutes=.35,temporary_slots=8,pickup_minutes_per_unit=.03,temporary_area=dict(length_cm=300,width_cm=200,height_cm=210)),
        energy=[dict(vehicle_id=v['id'],available_kwh=70,reserve_kwh=12,kwh_per_km=.32) for v in data['vehicles']],
        preparation=[dict(vehicle_id=v['id'],at_depot_minutes=v['shift_start']-25,load_minutes=20,ready_deadline_minutes=v['shift_start']) for v in data['vehicles']],
        returns=[],incompatible_pairs=[],assumptions=[
            dict(parameter='vehicle_geometry',evidence='assumed',basis='4.2米城配厢车量级；可用内部尺寸420×210×210cm为构造值，不代表特定车型实测',source='https://aumark.foton.com.cn/profile/upload/2025/08/08/%E6%AC%A7%E9%A9%AC%E5%8F%AF%E6%99%BA%E8%93%9DES1%E5%8D%95%E9%A1%B5-NEW_20250808124334A003.pdf'),
            dict(parameter='cold_boxes',evidence='assumed',basis='普通新能源厢车+独立共享保温箱；2—8°C是声明设备能力区间，180分钟和开启限制为压力试算参数，非实测保温认证',source='https://www.va-q-tec.com/wp-content/uploads/va-Q-one_EN_V1.0.pdf'),
            dict(parameter='handling/energy/preparation/pickup_geometry',evidence='assumed',basis='倒货0.35分钟/次、能耗0.32kWh/km、准备20分钟和包装尺寸均可调；不代表企业SOP或实测')]))


def sample_data():
    """One depot, three stores, shared cold payload and actual recovery geometry."""
    import application
    data=application.sample()
    for i,o in enumerate(data['orders']):
        o['lines']=[dict(sku='STANDARD_BOX',quantity=2+i,unit_weight_kg=7,unit_volume_m3=.072),
                    dict(sku='COLD_UNIT',quantity=2,unit_weight_kg=.5,unit_volume_m3=.001)]
        o.update(pickup=dict(cartons=6+i*2,totes=2),service_minutes=8,due=660,close=690)
    for v in data['vehicles']:
        v.update(max_weight_kg=900,max_volume_m3=18,shift_start=510,shift_end=780)
    data=application.canonicalize(data)
    return data,sample_policy(data)
