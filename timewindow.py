"""Single receiving-window change; existing cargo never switches trucks."""
from copy import deepcopy
from time import perf_counter
import normal
import dynamic
import urgent


def validate(event, data):
    if not isinstance(event,dict) or set(event)!={'time','store','open','due','close'}:
        raise ValueError('收货变化需且仅需发生时间、门店、开门、承诺和截止时间')
    normal.numeric(event['time'],'事件时间',480,540)
    if event['store'] not in [o['id'] for o in data['orders']]: raise ValueError('门店需为S01–S12')
    for k in ('open','due','close'):normal.numeric(event[k],'收货时间',480,1440)
    if not event['open']<=event['due']<=event['close']:raise ValueError('需满足开门时间 ≤ 承诺时间 ≤ 截止时间')


def reschedule(initial, updated, states, event, method):
    start=perf_counter(); orders={o['id']:o for o in updated['data']['orders']}; target=event['store']
    owner=dynamic.assignments(initial)[target][0]; vehicles=initial['data']['vehicles']
    vehicle=next(v for v in vehicles if v['id']==owner); state=states[owner]; own=state['remaining']; checked=0
    futures={v['id']:dynamic.future_route(states[v['id']]['remaining'],states[v['id']],v,orders,[],[0,0])
             for v in vehicles if v['id']!=owner}
    best=None
    def candidate(seq):
        nonlocal checked
        checked+=1
        rr=dynamic.future_route(seq,state,vehicle,orders,[],[0,0])
        return urgent.assemble(updated,states,{**futures,owner:rr}) if rr else None
    sequences=[own]
    if method!='baseline':
        base=[x for x in own if x!=target]
        sequences += [base[:i]+[target]+base[i:] for i in range(len(base)+1)]
        sequences += [sorted(own,key=lambda x:(orders[x]['due'],x))]
    for seq in sequences:
        plan=candidate(seq)
        if plan and (best is None or urgent.key(plan)<urgent.key(best)):best=plan
    if best and method!='baseline':
        for _ in range(4):
            seq=best['routes'][owner]['sequence'][len(state['completed']):]; improved=None
            for i,node in enumerate(seq):
                base=seq[:i]+seq[i+1:]
                for j in range(len(base)+1):
                    plan=candidate(base[:j]+[node]+base[j:])
                    if plan and urgent.key(plan)<urgent.key(best) and (improved is None or urgent.key(plan)<urgent.key(improved)):improved=plan
            if improved is None:break
            best=improved
    if best is None:
        # Do not deliver outside a hard window. Carry the rejected original
        # order back to warehouse on its original truck; preserve other work.
        rr=dynamic.future_route([x for x in own if x!=target],state,vehicle,orders,[],[0,0],False)
        if rr is None:raise ValueError('保留其他订单的续行也未通过约束检查，需要人工核查')
        best=urgent.assemble(updated,states,{**futures,owner:rr})
    for vid,rr in best['routes'].items():
        rr['retained_ids']=[x for x in states[vid]['remaining'] if x not in rr['sequence']]
    best.update(solve_ms=round((perf_counter()-start)*1000,3),checked_routes=checked,
        method='原顺序等到开门再送' if method=='baseline' else '原车内时间变化插入 + 有限单节点移位',
        status='候选试算，未采用或派单' if not best['unserved_ids'] else '未找到满足新窗口的完整方案；未安排原货留在原车并带回仓库，需人工处理')
    return best


def run(data,event):
    normal.validate(data);validate(event,data);initial=normal.solve(data)
    if initial['metrics']['unserved']:raise ValueError('正常计划还有未安排订单，请先调整数据')
    states=dynamic.snapshot(initial,event['time'])
    if any(s['id']==event['store'] for state in states.values() for s in state['completed']):
        raise ValueError('该门店原订单已交付，不能改写历史收货时间；如需追加货物请使用紧急订单')
    updated=deepcopy(initial)
    order=next(o for o in updated['data']['orders'] if o['id']==event['store'])
    old={k:order[k] for k in ('open','due','close')}
    order.update({k:event[k] for k in ('open','due','close')})
    return dict(simulated=True,initial=initial,updated_data=updated['data'],states=states,
        event={**deepcopy(event),'type':'time_window_change'},old_window=old,
        baseline=reschedule(initial,updated,states,event,'baseline'),
        recovered=reschedule(initial,updated,states,event,'optimized'),
        assumptions=dict(cargo='原车货物归属固定，已交付单固定，仅重排受影响原车剩余订单',
            windows='开门和截止为硬约束，承诺为迟到软目标；已交付订单不可修改',
            failure='无法满足新窗口则保留其他工作，未安排货物由原车带回仓库，等待人工处理',
            scope='08:00–09:00单次变化，独立试算，有限启发式，无全局最优保证'))
