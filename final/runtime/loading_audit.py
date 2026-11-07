"""Independent coordinate audit; imports no packing/search implementation.

Conservative model: cuboids, rear door at +x, upright planar rotations,
full footprint on one supporting box, recursively accumulated top loads.
"""
from math import isfinite


def geometry_bound(items,vehicle):
    """Exact orthogonal geometry test for <=2 boxes; not physical feasibility."""
    dimensions=[vehicle[k] for k in ('length_cm','width_cm','height_cm')];choices=[]
    for item in items:
        forms=[(item['length_cm'],item['width_cm'],item['height_cm'])]
        if item['rotate_base']:forms.append((item['width_cm'],item['length_cm'],item['height_cm']))
        fit=[shape for shape in forms if all(shape[k]<=dimensions[k] for k in range(3))]
        if not fit:return dict(status='PROVEN_GEOMETRY_INFEASIBLE',reason='至少一箱在所有允许朝向下均超出可用车厢尺寸')
        choices.append(fit)
    if len(items)==2:
        # Two disjoint cuboids must separate on at least one coordinate axis.
        possible=any(any(a[k]+b[k]<=dimensions[k] for k in range(3)) for a in choices[0] for b in choices[1])
        if not possible:return dict(status='PROVEN_GEOMETRY_INFEASIBLE',reason='枚举全部允许朝向，任一坐标轴都不能分离两箱')
    return dict(status='GEOMETRY_ONLY_FEASIBLE' if len(items)<=2 else 'UNKNOWN',reason='只涉及几何，仍需其他规则检查' if len(items)<=2 else '未进行多箱精确几何求解')


def audit(items, vehicle, sequence, placements):
    expected={x['id']:x for x in items}; placed={p['id']:p for p in placements}; errors=[]
    if len(placed)!=len(placements) or set(placed)!=set(expected):
        return dict(passed=False,errors=['箱体标识缺失、重复或多余'])
    rank={oid:i for i,oid in enumerate(sequence)}; parents={}
    if sum(x['weight_kg'] for x in items)>vehicle['max_weight_kg']+1e-8:errors.append('总重量超过车辆载重')
    for pid,p in placed.items():
        item=expected[pid]
        values=[p[k] for k in ('x','y','z','dx','dy','dz')]
        if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not isfinite(x) for x in values):
            return dict(passed=False,errors=['坐标或尺寸不是有限数值'])
        if min(values[:3])<0 or min(values[3:])<=0:errors.append(pid+'：坐标或尺寸范围错误')
        orientations={(item['length_cm'],item['width_cm'],item['height_cm'])}
        if item['rotate_base']:orientations.add((item['width_cm'],item['length_cm'],item['height_cm']))
        if (p['dx'],p['dy'],p['dz']) not in orientations:errors.append(pid+'：禁止的朝向')
        if any(p[a]+p[d]>vehicle[k]+1e-8 for a,d,k in [('x','dx','length_cm'),('y','dy','width_cm'),('z','dz','height_cm')]):errors.append(pid+'：超出车厢')
        if p['z']==0:parents[pid]=None
        else:
            supports=[bid for bid,b in placed.items() if bid!=pid and abs(b['z']+b['dz']-p['z'])<1e-8
                      and b['x']<=p['x'] and b['y']<=p['y'] and b['x']+b['dx']>=p['x']+p['dx']
                      and b['y']+b['dy']>=p['y']+p['dy']]
            if len(supports)!=1:errors.append(pid+'：未被单箱全底面支撑');parents[pid]=None
            else:
                parents[pid]=supports[0]
                if rank[expected[supports[0]]['order']]<rank[item['order']]:errors.append(pid+'：上层货晚于下层货配送，卸货后失去支撑')
    ids=list(placed)
    for index,a_id in enumerate(ids):
        a=placed[a_id]
        for b_id in ids[index+1:]:
            b=placed[b_id]
            overlaps=[min(a[k]+a[d],b[k]+b[d])-max(a[k],b[k])>1e-8 for k,d in [('x','dx'),('y','dy'),('z','dz')]]
            if all(overlaps):errors.append(a_id+'/'+b_id+'：箱体重叠')
            # Rear-door straight extraction: other destinations may not block.
            if overlaps[1] and overlaps[2]:
                if rank[expected[a_id]['order']]<rank[expected[b_id]['order']] and b['x']>=a['x']+a['dx']-1e-8:errors.append(a_id+'：被后送门店挡住卸货通道')
                if rank[expected[b_id]['order']]<rank[expected[a_id]['order']] and a['x']>=b['x']+b['dx']-1e-8:errors.append(b_id+'：被后送门店挡住卸货通道')
    loads={pid:0.0 for pid in placed}
    for pid,item in expected.items():
        parent=parents.get(pid);seen={pid}
        while parent is not None:
            if parent in seen:errors.append('支撑关系成环');break
            seen.add(parent);loads[parent]+=item['weight_kg'];parent=parents.get(parent)
    for pid,load in loads.items():
        if expected[pid]['fragile'] and load>1e-8:errors.append(pid+'：易碎箱上方有货')
        if load>expected[pid]['max_top_load_kg']+1e-8:errors.append(pid+'：累计上方重量超过堆叠承重')
    return dict(passed=not errors,errors=errors,boxes=len(items),top_loads_kg=loads,
        checks=['边界','唯一箱体','不重叠','允许朝向','载重','单箱全底面支撑','累计堆叠承重','易碎不压货','逐店直线取货与支撑顺序'])


def audit_steps(items,vehicle,sequence,placements,load_steps,unload_steps):
    """Replay every instruction independently, including within-store order."""
    boxes={p['id']:p for p in placements};defs={x['id']:x for x in items};errors=[]
    if len(load_steps)!=len(boxes) or set(load_steps)!=set(boxes):return dict(passed=False,errors=['装车步骤缺箱或重复'])
    prefix=[]
    for pid in load_steps:
        prefix.append(boxes[pid]);check=audit([defs[p['id']] for p in prefix],vehicle,sequence,prefix)
        if not check['passed']:errors.append('装车中间状态不可行：'+pid);break
    if [s['order'] for s in unload_steps]!=sequence:return dict(passed=False,errors=['卸货步骤与配送顺序不一致'])
    remaining=dict(boxes);removed=[]
    for step in unload_steps:
        for pid in step['box_ids']:
            if pid not in remaining or defs[pid]['order']!=step['order']:errors.append('卸货箱体重复、未知或错店');continue
            p=remaining[pid]
            for qid,q in remaining.items():
                if qid==pid:continue
                yz=(min(p['y']+p['dy'],q['y']+q['dy'])>max(p['y'],q['y']) and min(p['z']+p['dz'],q['z']+q['dz'])>max(p['z'],q['z']))
                if yz and q['x']>=p['x']+p['dx']:errors.append(pid+'：卸货步骤被箱体挡住')
            del remaining[pid];removed.append(pid)
            check=audit([defs[x] for x in remaining],vehicle,sequence,list(remaining.values()))
            if not check['passed']:errors.append('卸货后剩余货物不可行：'+pid)
    if remaining or len(removed)!=len(boxes):errors.append('卸货步骤未覆盖全部箱体')
    return dict(passed=not errors,errors=errors,load_states=len(load_steps),unload_states=len(removed))
