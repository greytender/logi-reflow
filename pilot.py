"""Local, offline planning adapter. No dispatch, AI, database or network writes.

CSV line items -> stop totals -> directed travel matrix -> bounded insertion
solver -> independent numerical audit -> CSV/JSON handoff for human review.
"""
import argparse
import csv
import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from time import perf_counter
import normal
import business

MAX_STOPS = 40
MAX_VEHICLES = 10


def identifier(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,39}', value):
        raise ValueError(label + '必须是1–40位字母、数字、下划线或连字符，以字母或数字开头')
    return value


def minute(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'\d{2}:\d{2}', value):
        raise ValueError(label + '必须是HH:MM')
    h, m = map(int, value.split(':'))
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(label + '超出当天时间范围')
    return 60 * h + m


def number(value, label, low=0, high=100000):
    try:
        n = float(value)
    except (ValueError, TypeError):
        raise ValueError(label + '必须是数字') from None
    return normal.numeric(n, label, low, high)


def rows(path, required):
    with path.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or set(fields) != set(required):
            raise ValueError(path.name + '表头必须为：' + ','.join(required))
        result = list(reader)
    if not result or any(None in row or any(v is None for v in row.values()) for row in result):
        raise ValueError(path.name + '为空或列数不一致')
    return result


ORDER_FIELDS = ('stop_id','sku','quantity','unit_weight_kg','unit_volume_m3',
                'open','due','close','service_minutes','allowed_vehicles')
VEHICLE_FIELDS = ('vehicle_id','max_weight_kg','max_volume_m3','fixed_cost',
                  'cost_per_km','shift_start','shift_end')
TRAVEL_FIELDS = ('from_id','to_id','distance_km','travel_minutes')


def load_bundle(directory):
    directory = Path(directory)
    settings = json.loads((directory / 'settings.json').read_text(encoding='utf-8-sig'))
    if not isinstance(settings, dict) or set(settings) != {'depot_id','data_kind','travel_source','window_rule'}:
        raise ValueError('settings.json字段必须为depot_id/data_kind/travel_source/window_rule')
    identifier(settings['depot_id'], '仓库编号')
    if settings['data_kind'] not in ('simulated', 'enterprise'):
        raise ValueError('data_kind必须为simulated或enterprise；声明不等于企业真实性验证')
    if not isinstance(settings['travel_source'], str) or not settings['travel_source'].strip():
        raise ValueError('必须说明道路时间来源')
    if settings['window_rule'] not in ('arrival', 'finish'):
        raise ValueError('window_rule必须为arrival（到达）或finish（卸货完成）')
    vehicles = []
    for row in rows(directory/'vehicles.csv', VEHICLE_FIELDS):
        v = dict(id=identifier(row['vehicle_id'], '车辆编号'))
        for key in ('max_weight_kg','max_volume_m3','fixed_cost','cost_per_km'):
            v[key] = number(row[key], key, .000001 if key.startswith('max_') else 0)
        v.update(shift_start=minute(row['shift_start'],'出班'), shift_end=minute(row['shift_end'],'收班'))
        if v['shift_start'] >= v['shift_end']: raise ValueError('车辆出班必须早于收班')
        vehicles.append(v)
    vids = {v['id'] for v in vehicles}
    if len(vids) != len(vehicles): raise ValueError('车辆编号重复')
    stops, seen_skus = {}, set()
    for row in rows(directory/'orders.csv', ORDER_FIELDS):
        sid = identifier(row['stop_id'],'门店编号'); sku = identifier(row['sku'],'SKU编号')
        if (sid,sku) in seen_skus: raise ValueError('门店与SKU重复；请先汇总同一SKU的订单行')
        seen_skus.add((sid,sku))
        q = number(row['quantity'],'箱数',1,10000)
        if q != int(q): raise ValueError('箱数必须为整数')
        allowed = row['allowed_vehicles'].split('|') if row['allowed_vehicles'] else sorted(vids)
        if len(allowed) != len(set(allowed)) or not set(allowed) <= vids:
            raise ValueError('门店可用车辆包含未知或重复编号')
        policy = dict(open=minute(row['open'],'开门'),due=minute(row['due'],'承诺'),
                      close=minute(row['close'],'截止'),service_minutes=number(row['service_minutes'],'卸货分钟',0,240),
                      allowed_vehicles=sorted(allowed))
        if not policy['open'] <= policy['due'] <= policy['close']: raise ValueError('开门 ≤ 承诺 ≤ 截止')
        if sid in stops and any(stops[sid][key] != val for key,val in policy.items()):
            raise ValueError('同一门店各SKU的收货规则必须一致；卸货分钟是整次到店用时')
        if sid not in stops: stops[sid] = dict(id=sid,lines=[],**policy)
        stops[sid]['lines'].append(dict(sku=sku,quantity=int(q),
            unit_weight_kg=number(row['unit_weight_kg'],'箱重',.000001,10000),
            unit_volume_m3=number(row['unit_volume_m3'],'箱体积',.000001,1000)))
    orders = list(stops.values())
    for o in orders:
        # A visit is indivisible: totals from its actual SKU lines, never one arbitrary SKU.
        o.update(quantity=1,unit_weight_kg=sum(x['quantity']*x['unit_weight_kg'] for x in o['lines']),
                 unit_volume_m3=sum(x['quantity']*x['unit_volume_m3'] for x in o['lines']))
    legs = {}
    for row in rows(directory/'travel.csv', TRAVEL_FIELDS):
        a,b = identifier(row['from_id'],'起点'), identifier(row['to_id'],'终点')
        key = a+'>'+b
        if key in legs: raise ValueError('道路时间表有重复方向：'+key)
        legs[key] = dict(distance_km=number(row['distance_km'],'道路公里'),
                         travel_minutes=number(row['travel_minutes'],'行驶分钟',0,1440))
    sources = {name:hashlib.sha256((directory/name).read_bytes()).hexdigest()
               for name in ('orders.csv','vehicles.csv','travel.csv','settings.json')}
    data = dict(settings=settings,orders=orders,vehicles=vehicles,legs=legs,source_sha256=sources)
    validate(data)
    return data


def validate(data):
    if 'business' in data:
        business.validate(data['business'], data['orders'])
    if not 1 <= len(data['orders']) <= MAX_STOPS or not 1 <= len(data['vehicles']) <= MAX_VEHICLES:
        raise ValueError('离线试算支持1–40个门店、1–10辆车；这不是生产规模承诺')
    nodes = {o['id'] for o in data['orders']}; depot = data['settings']['depot_id']
    if depot in nodes: raise ValueError('门店编号不能等于仓库编号')
    nodes.add(depot)
    required = {a+'>'+b for a in nodes for b in nodes if a != b}
    missing, extra = required-set(data['legs']), set(data['legs'])-required
    if missing: raise ValueError('道路表缺方向，不用直线猜测：'+', '.join(sorted(missing)[:8]))
    if extra: raise ValueError('道路表含未知或自环方向：'+', '.join(sorted(extra)[:8]))


def evaluator(data):
    depot = data['settings']['depot_id']; legs=data['legs']; rule=data['settings']['window_rule']
    def route(seq, v, orders):
        w, vol = normal.totals(seq, orders)
        if w > v['max_weight_kg']+1e-8 or vol > v['max_volume_m3']+1e-8: return None
        t=v['shift_start']; prev=depot; km=0; stops=[]; late=[]; wait=0
        for sid in seq:
            o=orders[sid]
            if v['id'] not in o['allowed_vehicles']: return None
            leg=legs[prev+'>'+sid]; road_arrival=t+leg['travel_minutes']; km+=leg['distance_km']
            arrival=max(road_arrival,o['open']); finish=arrival+o['service_minutes']
            deadline_time=finish if rule=='finish' else arrival
            if deadline_time > o['close']+1e-8: return None
            delay=max(0,deadline_time-o['due']); late.append(delay); wait+=arrival-road_arrival
            stops.append(dict(id=sid,road_arrival=round(road_arrival,4),arrival=round(arrival,4),
                finish=round(finish,4),wait_minutes=round(arrival-road_arrival,4),late_minutes=round(delay,4)))
            t=finish; prev=sid
        if seq:
            leg=legs[prev+'>'+depot]; km+=leg['distance_km']; t+=leg['travel_minutes']
            if t > v['shift_end']+1e-8: return None
        result = dict(sequence=list(seq),stops=stops,weight_kg=round(w,4),volume_m3=round(vol,6),
            distance=round(km,4),return_at=round(t,4),wait_minutes=round(wait,4),
            cost=round(v['fixed_cost']+km*v['cost_per_km'],4) if seq else 0,
            late_orders=sum(x>1e-6 for x in late),late_minutes=round(sum(late),4))
        return business.apply(data, result, v, orders, km)
    return route


def audit(data, result):
    """Independent arithmetic: do not call evaluator/normal.route to verify them."""
    orders={o['id']:o for o in data['orders']}; assigned=[]; total_cost=0; total_km=0; all_late=[]
    def check(condition, message):
        if not condition: raise ValueError('独立复核失败：'+message)
    def equal(a,b,label,tolerance=.0002): check(abs(a-b)<=tolerance,label)
    for v in data['vehicles']:
        r=result['routes'][v['id']]; seq=r['sequence']; assigned+=seq
        check(seq==[s['id'] for s in r['stops']], '路线与节点不一致')
        w=sum(sum(x['quantity']*x['unit_weight_kg'] for x in orders[s]['lines']) for s in seq)
        vol=sum(sum(x['quantity']*x['unit_volume_m3'] for x in orders[s]['lines']) for s in seq)
        check(w<=v['max_weight_kg']+1e-8 and vol<=v['max_volume_m3']+1e-8,'超载')
        equal(w,r['weight_kg'],'重量'); equal(vol,r['volume_m3'],'体积')
        time=v['shift_start']; previous=data['settings']['depot_id']; km=0; waiting=0; delays=[]
        for stop in r['stops']:
            o=orders[stop['id']]; check(v['id'] in o['allowed_vehicles'],'车辆不允许到该门店')
            leg=data['legs'][previous+'>'+o['id']]; km+=leg['distance_km']; time+=leg['travel_minutes']
            equal(time,stop['road_arrival'],'道路到达时间')
            wait=max(0,o['open']-time); waiting+=wait; time+=wait
            equal(time,stop['arrival'],'开始卸货'); equal(wait,stop['wait_minutes'],'等待')
            deadline_time=time+o['service_minutes'] if data['settings']['window_rule']=='finish' else time
            check(deadline_time<=o['close']+1e-8,'超过硬收货截止')
            delay=max(0,deadline_time-o['due']); delays.append(delay); equal(delay,stop['late_minutes'],'迟到')
            time+=o['service_minutes']; equal(time,stop['finish'],'卸货完成'); previous=o['id']
        if seq:
            leg=data['legs'][previous+'>'+data['settings']['depot_id']]
            km+=leg['distance_km']; time+=leg['travel_minutes']; check(time<=v['shift_end']+1e-8,'超过收班')
        cost=(v['fixed_cost']+km*v['cost_per_km']) if seq else 0
        if 'business' in data:
            cost=business.audit_route(data, r, v, orders, km)
        equal(km,r['distance'],'道路里程'); equal(cost,r['cost'],'费用'); equal(time,r['return_at'],'回仓时间')
        equal(waiting,r['wait_minutes'],'总等待'); equal(sum(delays),r['late_minutes'],'路线迟到')
        check(sum(x>1e-6 for x in delays)==r['late_orders'],'路线迟到单数')
        total_cost+=cost; total_km+=km; all_late+=delays
    rejected=[o['id'] for o in result['unserved']]
    check(len(assigned)==len(set(assigned)) and len(rejected)==len(set(rejected)),'重复配送或重复未安排')
    check(set(assigned).isdisjoint(rejected) and set(assigned)|set(rejected)==set(orders),'漏单或重复归属')
    m=result['metrics']; check(m['served']==len(assigned) and m['unserved']==len(rejected),'数量')
    equal(total_cost,m['cost'],'总费用',.011); equal(total_km,m['distance'],'总公里',.011)
    equal(sum(all_late),m['late_minutes'],'总迟到',.011)
    check(m['late_orders']==sum(x>1e-6 for x in all_late),'总迟到单数')
    return dict(passed=True,checked_stops=len(assigned),unserved=len(rejected),
        checks=['SKU汇总载重与体积','车辆准入','有向道路时间','卸货与窗口','班次回仓','费用','迟到','完整且唯一归属']+
            (['逐站先卸后收容量','纸箱压平与周转箱套叠估算','分项运费与回收不重复收费'] if 'business' in data else []))


def baseline(data):
    started=perf_counter(); orders={o['id']:o for o in data['orders']}; evaluate=evaluator(data)
    routes={v['id']:evaluate([],v,orders) for v in data['vehicles']}; rejected=[]
    for o in data['orders']:
        for v in data['vehicles']:
            r=evaluate(routes[v['id']]['sequence']+[o['id']],v,orders)
            if r is not None:
                routes[v['id']]=r; break
        else: rejected.append(dict(id=o['id'],reason='按输入顺序依次追加未找到位置；不是企业人工计划，也不是无解证明'))
    sc=normal.score(routes)
    return dict(routes=routes,unserved=rejected,metrics=dict(served=len(orders)-len(rejected),unserved=len(rejected),
        late_orders=sc[0],late_minutes=round(sc[1],2),cost=round(sc[2],2),
        distance=round(sum(r['distance'] for r in routes.values()),2)),solve_ms=round((perf_counter()-started)*1000,3))


def compare(data):
    validate(data); base=baseline(data)
    # When tariff kilometres are included in a base price, break fee ties by
    # road distance rather than letting insertion order choose longer routes.
    rank = (lambda routes: normal.score(routes)+(round(sum(r['distance'] for r in routes.values()),4),)) if 'business' in data else normal.score
    plan=normal.solve(data,validator=validate,evaluator=evaluator(data),search_rounds=2,scorer=rank)
    plan.pop('data'); plan['simulated']=data['settings']['data_kind']=='simulated'
    search_key=(plan['metrics']['unserved'],rank(plan['routes']))
    base_key=(base['metrics']['unserved'],rank(base['routes']))
    if base_key < search_key:
        search_ms=plan['solve_ms']; plan=deepcopy(base)
        plan.update(solve_ms=search_ms,method='有限搜索不如简单追加，保留简单追加结果',
                    fallback_to_baseline=True,simulated=data['settings']['data_kind']=='simulated')
    orders={o['id']:o for o in data['orders']}; ev=evaluator(data)
    for item in plan['unserved']:
        o=orders[item['id']]
        fit=[v for v in data['vehicles'] if v['id'] in o['allowed_vehicles'] and
             o['unit_weight_kg']<=v['max_weight_kg']+1e-8 and o['unit_volume_m3']<=v['max_volume_m3']+1e-8]
        if not fit:
            item.update(code='NO_ELIGIBLE_CAPACITY',reason='可到店车辆无法装下整次门店配送；不拆单',
                        action='调度员核对车辆准入和SKU数量，联系合适车辆或确认分批规则')
        elif not any(ev([o['id']],v,orders) is not None for v in fit):
            item.update(code='SINGLE_STOP_RULE_INFEASIBLE' if 'business' in data else 'SINGLE_STOP_TIME_INFEASIBLE',
                        reason='可用车辆单独执行也不满足回收容量、窗口或回仓班次' if 'business' in data else '可用车辆单独配送也无法满足窗口及回仓班次',
                        action='核对回收数量、压缩参数、运力及窗口；系统不丢弃回收，也不自动修改承诺')
        else:
            item.update(code='SEARCH_NOT_FOUND',reason='有限搜索没有安排；尚未证明无解',
                        action='人工复核剩余运力、尝试不同分单或求解方法')
    return dict(data=data,baseline=base,optimized=plan,
        audit=dict(baseline=audit(data,base),optimized=audit(data,plan)),
        comparison_scope=dict(same_inputs=True,same_served_stops=
            {s for r in base['routes'].values() for s in r['sequence']}==
            {s for r in plan['routes'].values() for s in r['sequence']},
            interpretation='配送集合不同则费用/迟到只描述各自输出，不能计算等任务节省率'),
        status='可供人工核对的完整离线候选' if not plan['unserved'] else '部分任务未安排，需人工处理',
        boundaries=['单仓、出发前整店配送、不拆单；SKU只参与重量体积汇总',
            '静态有向道路表；不是实时交通、GPS或车辆通行规则引擎',
            '3D装载、易碎堆叠、冷链、司机执行回执未实现',
            'CSV已接通连续紧急单和收货变化；连续故障尚未接入',
            '简单追加基线不是企业人工排程；无企业收益或全局最优证明',
            '费用为选定计费分支的道路公里试算；不是TMS结算或GPS实绩' if 'business' in data else
            '费用仅出车固定费与道路公里费，不含等待工资、加班、货损、装卸人工费']+
            (['回收为出发前预测，逐站先卸后收；每店独立套叠，不跨店合并套叠',
              '仅核验重量和体积，不证明箱体摆放、倒货步骤或到店实际可行'] if 'business' in data else []))


def export(result, directory):
    directory=Path(directory); directory.mkdir(parents=True,exist_ok=True)
    (directory/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    with (directory/'route-stops.csv').open('w',encoding='utf-8-sig',newline='') as f:
        fields=['vehicle_id','stop_id','sequence','arrival_minutes','finish_minutes','late_minutes','sku_lines','weight_kg','volume_m3']
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); orders={o['id']:o for o in result['data']['orders']}
        for vid,r in result['optimized']['routes'].items():
            for i,s in enumerate(r['stops'],1):
                o=orders[s['id']]
                w.writerow(dict(vehicle_id=vid,stop_id=s['id'],sequence=i,arrival_minutes=s['arrival'],finish_minutes=s['finish'],
                    late_minutes=s['late_minutes'],sku_lines=len(o['lines']),weight_kg=o['unit_weight_kg'],volume_m3=o['unit_volume_m3']))
    with (directory/'unserved.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['id','code','reason','action']); w.writeheader(); w.writerows(result['optimized']['unserved'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='离线CSV试算；不调用AI、不派单、不修改连续场景')
    parser.add_argument('directory'); parser.add_argument('--output',default='outputs/pilot')
    args=parser.parse_args()
    try:
        result=compare(load_bundle(args.directory)); export(result,args.output)
    except (ValueError,OSError) as e:
        parser.exit(2,'数据拒绝：'+str(e)+'\n')
    print(json.dumps(dict(status=result['status'],baseline=result['baseline']['metrics'],optimized=result['optimized']['metrics'],
        audit=result['audit'],output=str(Path(args.output).resolve())),ensure_ascii=False,indent=2))
