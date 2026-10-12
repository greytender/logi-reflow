"""Bounded departure-only routing with constructive 3D loading checks.

Face-corner candidates are inspired by packing literature, not a reproduction
of a published extreme-point or ALNS solver. A failed heuristic is UNKNOWN.
No solver dependencies, AI, database writes or dynamic cargo rearrangement.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter
import normal
import pilot
import matrix_dispatch as md
import loading_audit

ROOT=Path(__file__).resolve().parent
MAX_BOXES=80


def profile(data, raw):
    if not isinstance(raw,dict) or set(raw)!={'schema','data_kind','vehicles','items'} or raw['schema']!='loading_v1':
        raise ValueError('loading.json需要schema=loading_v1、data_kind、vehicles、items')
    if raw['data_kind']!=data['settings']['data_kind']:raise ValueError('装载与CSV数据性质声明必须一致；声明不等于企业真实性验证')
    def number(value,label,low=0,high=100000):return normal.numeric(value,label,low,high)
    def dimension(value):
        number(value,'尺寸cm',1,2000)
        if not isinstance(value,int):raise ValueError('箱体与可用车厢尺寸需为整厘米')
        return value
    vehicle_ids={v['id'] for v in data['vehicles']};vehicles={}
    if not isinstance(raw['vehicles'],list) or not isinstance(raw['items'],list):raise ValueError('vehicles/items需为列表')
    for row in raw['vehicles']:
        if not isinstance(row,dict) or set(row)!={'id','length_cm','width_cm','height_cm'}:raise ValueError('车厢需要id及可用长宽高cm')
        vid=row['id']
        if not isinstance(vid,str) or vid not in vehicle_ids or vid in vehicles:raise ValueError('装载车辆未知或重复')
        v=next(v for v in data['vehicles'] if v['id']==vid);vehicles[vid]={**v,**{k:dimension(row[k]) for k in ('length_cm','width_cm','height_cm')}}
        geom=row['length_cm']*row['width_cm']*row['height_cm']/1e6
        if v['max_volume_m3']>geom+1e-8:raise ValueError('CSV最大容积超过可用车厢几何容积，请核对')
    if set(vehicles)!=vehicle_ids:raise ValueError('装载文件必须覆盖全部CSV车辆，不猜车厢尺寸')
    demand={(o['id'],x['sku']):x for o in data['orders'] for x in o['lines']};seen=set();items=[]
    if sum(x['quantity'] for x in demand.values())>MAX_BOXES:raise ValueError(f'当前装载试算最多{MAX_BOXES}箱，按每箱展开；不是生产规模承诺')
    fields={'order','sku','length_cm','width_cm','height_cm','rotate_base','fragile','max_top_load_kg'}
    for row in raw['items']:
        if not isinstance(row,dict) or set(row)!=fields:raise ValueError('每行需要订单/SKU、长宽高、rotate_base、fragile、max_top_load_kg，不补默认值')
        if not isinstance(row['order'],str) or not isinstance(row['sku'],str):raise ValueError('订单和SKU需为字符串')
        key=(row['order'],row['sku'])
        if key not in demand or key in seen:raise ValueError('装载订单/SKU未知或重复')
        seen.add(key);line=demand[key]
        for k in ('length_cm','width_cm','height_cm'):dimension(row[k])
        if type(row['rotate_base']) is not bool or type(row['fragile']) is not bool:raise ValueError('朝向和易碎字段需为布尔值')
        number(row['max_top_load_kg'],'允许上方累计kg')
        volume=row['length_cm']*row['width_cm']*row['height_cm']/1e6
        if abs(volume-line['unit_volume_m3'])>max(1e-7,volume*.001):raise ValueError('箱体长宽高与CSV每箱体积不一致，不用总体积反推尺寸')
        for i in range(line['quantity']):items.append({**row,'id':f'{row["order"]}/{row["sku"]}/{i+1}', 'weight_kg':line['unit_weight_kg']})
    if seen!=set(demand):raise ValueError('缺少订单/SKU装载规则，不能声称已验证3D可行')
    return vehicles,items


def constructive(items,vehicle,sequence,work):
    rank={o:i for i,o in enumerate(sequence)}
    for strategy in ('area','height','weight'):
        def priority(item):
            size=item['length_cm']*item['width_cm'] if strategy=='area' else item['height_cm'] if strategy=='height' else item['weight_kg']
            return (-rank[item['order']],-size,item['id'])
        placements=[];points={(0,0,0)};parents={};loads={};definitions={x['id']:x for x in items}
        for item in sorted(items,key=priority):
            options=[(item['length_cm'],item['width_cm'],item['height_cm'])]
            if item['rotate_base'] and item['length_cm']!=item['width_cm']:options.append((item['width_cm'],item['length_cm'],item['height_cm']))
            chosen=None
            for x,y,z in sorted(points,key=lambda p:(p[2],p[0],p[1])):
                for dx,dy,dz in options:
                    if work['placement_checks']>=200000:work['budget_exhausted']=True;return None
                    work['placement_checks']+=1
                    if x+dx>vehicle['length_cm'] or y+dy>vehicle['width_cm'] or z+dz>vehicle['height_cm']:continue
                    if any(x<p['x']+p['dx'] and x+dx>p['x'] and y<p['y']+p['dy'] and y+dy>p['y'] and z<p['z']+p['dz'] and z+dz>p['z'] for p in placements):continue
                    support=None
                    if z:
                        support=next((p['id'] for p in placements if p['z']+p['dz']==z and p['x']<=x and p['y']<=y and p['x']+p['dx']>=x+dx and p['y']+p['dy']>=y+dy),None)
                        if support is None or rank[definitions[support]['order']]<rank[item['order']]:continue
                    chain=[];ancestor=support
                    while ancestor is not None:chain.append(ancestor);ancestor=parents[ancestor]
                    if any(definitions[a]['fragile'] or loads[a]+item['weight_kg']>definitions[a]['max_top_load_kg']+1e-8 for a in chain):continue
                    # Earlier stops cannot be behind later stops across the same y/z corridor.
                    blocked=False
                    for p in placements:
                        if y<p['y']+p['dy'] and y+dy>p['y'] and z<p['z']+p['dz'] and z+dz>p['z']:
                            old_rank=rank[definitions[p['id']]['order']];new_rank=rank[item['order']]
                            if new_rank<old_rank and p['x']>=x+dx or old_rank<new_rank and x>=p['x']+p['dx']:blocked=True;break
                    if blocked:continue
                    chosen=dict(id=item['id'],x=x,y=y,z=z,dx=dx,dy=dy,dz=dz);break
                if chosen:break
            if chosen is None:break
            placements.append(chosen);parents[item['id']]=support;loads[item['id']]=0
            for ancestor in chain:loads[ancestor]+=item['weight_kg']
            points.update([(chosen['x']+chosen['dx'],chosen['y'],chosen['z']),
                           (chosen['x'],chosen['y']+chosen['dy'],chosen['z']),
                           (chosen['x'],chosen['y'],chosen['z']+chosen['dz'])])
        if len(placements)==len(items):
            check=loading_audit.audit(items,vehicle,sequence,placements)
            if not check['passed']:raise ValueError('独立装载复核失败：'+str(check['errors'][:2]))
            unload=[];remaining=placements[:]
            for oid in sequence:
                group=[p for p in remaining if definitions[p['id']]['order']==oid]
                # Top first, then closer to the rear door (+x) first.
                group.sort(key=lambda p:(-p['z'],-p['x'],p['y'],p['id']))
                unload.append(dict(order=oid,box_ids=[p['id'] for p in group]));remaining=[p for p in remaining if p not in group]
            load_steps=[p['id'] for p in placements]
            replay=loading_audit.audit_steps(items,vehicle,sequence,placements,load_steps,unload)
            if not replay['passed']:raise ValueError('独立装卸步骤复核失败：'+str(replay['errors'][:2]))
            return dict(placements=placements,audit=check,instruction_audit=replay,
                        unload_steps=unload,load_steps=load_steps,strategy=strategy)
    return None


def solve(files,raw):
    started=perf_counter();data=md.load_files(files);vehicles,items=profile(data,raw)
    ordinary=pilot.compare(data)['optimized'];ev=pilot.evaluator(data);cache={}
    work=dict(route_checks=0,placement_checks=0,loading_rejections=0,budget_exhausted=False)
    def packed(seq,v,orders):
        key=(v['id'],tuple(seq))
        if key in cache:return cache[key]
        route=ev(seq,v,orders)
        if route is None:cache[key]=None;return None
        work['route_checks']+=1
        group=[x for x in items if x['order'] in seq]
        loading=constructive(group,vehicles[v['id']],seq,work)
        if loading is None:work['loading_rejections']+=1;cache[key]=None;return None
        route['loading']=loading;cache[key]=route;return route
    plan=normal.solve(data,validator=pilot.validate,evaluator=packed,search_rounds=2)
    plan.pop('data');plan['method']='多起点路线插入/移位＋逆配送顺序面角点装载；有限构造搜索'
    for o in plan['unserved']:o.update(code='LOADING_OR_ROUTE_SEARCH_NOT_FOUND',reason='路线/装载有限搜索未找到联合方案；不证明无解，需核查尺寸、规则或换求解器')
    route_audit=pilot.audit(data,plan)
    baseline_loading={}
    for vid,r in ordinary['routes'].items():
        group=[x for x in items if x['order'] in r['sequence']]
        before_work=dict(placement_checks=0,budget_exhausted=False)
        check=constructive(group,vehicles[vid],r['sequence'],before_work)
        bound=loading_audit.geometry_bound(group,vehicles[vid])
        baseline_loading[vid]=dict(status='VERIFIED' if check else 'PROVEN_GEOMETRY_INFEASIBLE' if bound['status']=='PROVEN_GEOMETRY_INFEASIBLE' else 'NOT_FOUND',
            boxes=len(group),packing=check,geometry_bound=bound)
    complete=not plan['unserved'];served_a={oid for r in ordinary['routes'].values() for oid in r['sequence']};served_b={oid for r in plan['routes'].values() for oid in r['sequence']}
    return dict(data_kind=data['settings']['data_kind'],departure_only=True,dispatchable=False,
        input_sha256={**data['source_sha256'],'loading.json':sha256(json.dumps(raw,sort_keys=True,ensure_ascii=False).encode()).hexdigest()},
        items=items,vehicles=vehicles,baseline=ordinary,baseline_loading=baseline_loading,optimized=plan,
        independent_route_audit=route_audit,complete=complete,same_served_orders=served_a==served_b,
        work=work,total_ms=round((perf_counter()-started)*1000,2),
        boundaries=['本次仅出发前离线试算；核对后可另建固定装载连续模拟，不自动采用、派单或重摆在途货物',
        '长方体、可用长方体车厢、尾门在+x；只允许保持向上的底面90°旋转',
        '单箱全底面支撑；易碎箱禁止承重，承重按整条支撑链累计',
        '按门店顺序沿+x直线取箱，可先取本店上层货；不搬其他门店货',
        '未建模侧门、轮包、轴荷、制动稳定、叉车/人可达性、温控或真实作业时间',
        '三种有限构造排列；未找到不是几何无解；未复现论文完整EP、ALNS或精确算法',
        '3D检查通过只代表本模型，不能替代现场核验；未证明企业收益'])


def sample():
    directory=ROOT/'examples'/'loading-small'
    return dict(csv_files={name:(directory/name).read_text(encoding='utf-8') for name in md.FILES},
                loading_profile=json.loads((directory/'loading.json').read_text(encoding='utf-8')))


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--directory',type=Path,default=ROOT/'examples/loading-small');parser.add_argument('--output',type=Path,default=ROOT/'outputs/loading-comparison.json');args=parser.parse_args()
    result=solve({n:(args.directory/n).read_text(encoding='utf-8') for n in md.FILES},json.loads((args.directory/'loading.json').read_text(encoding='utf-8')))
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(complete=result['complete'],baseline_loading={k:v['status'] for k,v in result['baseline_loading'].items()},metrics=result['optimized']['metrics'],work=result['work']),ensure_ascii=False))
