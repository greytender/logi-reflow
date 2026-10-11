"""Persistent simulated dispatch. Legacy tables are never edited.

Minimal scope: one urgent warehouse order followed by receiving changes.
Snapshots have an explicit anchor; history is never replayed along a new path.
"""
from contextlib import closing
from copy import deepcopy
from pathlib import Path
import json
import sqlite3
import uuid
from time import perf_counter
import engine
import normal
import dynamic
import urgent
import timewindow
from store import Conflict


def snapshot(plan, now):
    orders={o['id']:o for o in plan['data']['orders']}; result={}
    for vid, route in plan['routes'].items():
        anchor=route['anchor']; p=deepcopy(anchor['point']); depart=anchor['available']
        km=anchor['traveled']; cargo=set(anchor['cargo']); completed=deepcopy(anchor['completed'])
        for stop in route['stops'][len(completed):]:
            d=engine.distance(p,stop['point']); end=depart+d/engine.SPEED
            if now+1e-4<end:
                ratio=max(0,(now-depart)/(d/engine.SPEED)) if d else 0
                pos=[p[i]+ratio*(stop['point'][i]-p[i]) for i in range(2)]
                km+=engine.distance(p,pos); p=pos; break
            km+=d; p=deepcopy(stop['point'])
            if now<stop['arrival']: break
            if stop['id']=='W' and now<stop['finish']:
                # Loading is committed once started, but cargo stays at warehouse
                # until finish. Resume this same node, with no second 8-minute load.
                depart=now; break
            completed.append(deepcopy(stop)); depart=stop['finish']
            if stop['id']=='W': cargo.update(stop['load_ids'])
            else: cargo.remove(stop['id'])
            if now<depart: break
        else:
            d=engine.distance(p,[0,0]); ratio=min(1,max(0,(now-depart)/(d/engine.SPEED))) if d else 1
            pos=[x*(1-ratio) for x in p]; km+=engine.distance(p,pos); p=pos
        remaining=route['sequence'][len(completed):]
        w,vol=normal.totals(cargo,orders)
        result[vid]=dict(point=p,available=max(now,depart),traveled=km,cargo=sorted(cargo),
            completed=completed,remaining=remaining,weight_kg=w,volume_m3=vol)
    return result


def future(seq, state, vehicle, orders, previous):
    cargo=set(state['cargo']); p=state['point']; t=state['available']; km=0; stops=[]
    peak_w,peak_v=normal.totals(cargo,orders)
    if len(seq)!=len(set(seq)): return None
    for oid in seq:
        dest=[0,0] if oid=='W' else orders[oid]['point']
        d=engine.distance(p,dest); km+=d; t+=d/engine.SPEED
        if oid=='W':
            old=next(s for s in previous['stops'] if s['id']=='W')
            load=old['load_ids']; arrival=t
            # A loading operation already started cannot be moved/restarted.
            if old['arrival']<=state['available']<old['finish']:
                if stops or engine.distance(state['point'],[0,0])>1e-7: return None
                arrival=old['arrival']; t=old['finish']
            else: t+=8
            if set(load)&cargo: return None
            cargo.update(load)
        else:
            if oid not in cargo: return None
            o=orders[oid]; t=max(t,o['open']); arrival=t
            if t>o['close']+1e-8: return None
            cargo.remove(oid); t+=5
        w,vol=normal.totals(cargo,orders)
        peak_w=max(peak_w,w); peak_v=max(peak_v,vol)
        if peak_w>vehicle['max_weight_kg']+1e-8 or peak_v>vehicle['max_volume_m3']+1e-8: return None
        stop=dict(id=oid,point=dest,arrival=round(arrival,4),finish=round(t,4),weight_kg=w,volume_m3=vol)
        if oid=='W': stop['load_ids']=load
        stops.append(stop); p=dest
    back=engine.distance(p,[0,0]); km+=back
    return dict(stops=stops,distance=km,return_at=t+back/engine.SPEED,
        peak_weight_kg=peak_w,peak_volume_m3=peak_v,retained_ids=sorted(cargo))


def assemble(base, states, futures):
    plan=deepcopy(base); original=dynamic.assignments(base); done=[]; changes=[]; late=[]; cost=0
    orders={o['id']:o for o in plan['data']['orders']}
    for v in plan['data']['vehicles']:
        vid=v['id']; state=states[vid]; rr=futures[vid]; stops=deepcopy(state['completed'])+rr['stops']
        km=state['traveled']+rr['distance']; amount=v['fixed_cost']+km*v['cost_per_km']; cost+=amount
        plan['routes'][vid]=dict(sequence=[s['id'] for s in stops],stops=stops,anchor=deepcopy(state),
            distance=km,cost=amount,return_at=rr['return_at'],peak_weight_kg=rr['peak_weight_kg'],
            peak_volume_m3=rr['peak_volume_m3'],retained_ids=rr['retained_ids'])
        prev=None; completed={s['id'] for s in state['completed']}
        for s in stops:
            oid=s['id']
            if oid!='W':
                done.append(oid); late.append(max(0,s['arrival']-orders[oid]['due']))
                if oid not in completed and oid in original:
                    old=original[oid]
                    if old[0]!=vid or old[2]!=prev or abs(old[1]-s['arrival'])>1: changes.append(oid)
            prev=oid
    missing=sorted(set(orders)-set(done)); changes+=missing
    plan['unserved_ids']=missing
    plan['metrics']=dict(served=len(done),unserved=len(missing),late_orders=sum(x>1e-6 for x in late),
        late_minutes=round(sum(late),2),cost=round(cost,2),changed_orders=len(set(changes)),changed_ids=sorted(set(changes)))
    return plan


def calculate(base, event, kind, now, method='optimized'):
    if method not in ('optimized','baseline'): raise ValueError('不支持的对照方法')
    started=perf_counter(); normal.numeric(event.get('time'),'发生时间',now,540)
    states=snapshot(base,event['time'])
    if kind=='urgent':
        if any(o['id']=='E01' for o in base['data']['orders']): raise ValueError('本轮最小闭环只支持一个新增E01；请继续调整收货时间')
        order=urgent.validate(event,base['data'])
        # First urgent event uses the previously verified search. The scene has
        # no prior exceptions, so its persisted normal route is the exact base.
        result=urgent.insert(base,states,order,method)
        plan=deepcopy(result); plan['data']=deepcopy(base['data']); plan['data']['orders'].append(order)
        for vid,r in plan['routes'].items():
            r['anchor']=deepcopy(states[vid])
            for s in r['stops']:
                if s['id']=='W': s['load_ids']=['E01']
        return plan,states
    if kind!='window': raise ValueError('连续模式当前支持紧急单与收货变化，故障仍为独立试算')
    timewindow.validate(event,base['data'])
    if any(s['id']==event['store'] for state in states.values() for s in state['completed']):
        raise ValueError('该订单已交付，不能改写历史')
    updated=deepcopy(base); target=event['store']
    next(o for o in updated['data']['orders'] if o['id']==target).update({k:event[k] for k in ('open','due','close')})
    orders={o['id']:o for o in updated['data']['orders']}; owner=dynamic.assignments(base)[target][0]
    futures={v['id']:future(states[v['id']]['remaining'],states[v['id']],v,orders,base['routes'][v['id']])
        for v in base['data']['vehicles'] if v['id']!=owner}
    if any(r is None for r in futures.values()): raise ValueError('其他车辆续行未通过约束，需人工核查')
    v=next(v for v in base['data']['vehicles'] if v['id']==owner); own=states[owner]['remaining']
    best=None; checked=0
    def trial(seq):
        nonlocal checked
        checked+=1; rr=future(seq,states[owner],v,orders,base['routes'][owner])
        return assemble(updated,states,{**futures,owner:rr}) if rr else None
    sequences=[own]; rest=[x for x in own if x!=target]
    if method=='optimized': sequences += [rest[:i]+[target]+rest[i:] for i in range(len(rest)+1)]
    for seq in sequences:
        p=trial(seq)
        if p and (best is None or urgent.key(p)<urgent.key(best)): best=p
    if best is None: best=trial(rest)
    if best is None: raise ValueError('保留其他工作也无法续行，需人工核查')
    best.update(solve_ms=round((perf_counter()-started)*1000,3),checked_routes=checked,
        method='保持原顺序，按新窗口等待' if method=='baseline' else '受影响原车内插入调整')
    return best,states


def comparison(scene):
    """Reproduce the latest displayed window event without saving or executing.

    Both alternatives share the adopted parent, orders, event-time state and
    changed window. The parent metrics are context only, not a fair baseline.
    """
    if not scene: return dict(available=False,reason='尚未开始连续场景')
    number=scene['candidate'] or scene['active']
    version=next(v for v in scene['versions'] if v['id']==number)
    record=next((e for e in scene['events'] if e['version']==number),None)
    if not record or record['kind']!='window':
        return dict(available=False,reason='当前版本没有收货变化事件，先完成紧急单后收货变化的例子')
    parent=next(v['plan'] for v in scene['versions'] if v['id']==version['base'])
    event=record['event'];baseline,states=calculate(parent,event,'window',event['time'],'baseline')
    recovered,check_states=calculate(parent,event,'window',event['time'],'optimized')
    if states!=check_states: raise ValueError('两种方法起始状态不同，不能比较')
    # Strip machine-dependent timing before checking replay consistency.
    def behavior(p):
        return dict(data=p['data'],routes=p['routes'],metrics=p['metrics'],unserved_ids=p['unserved_ids'])
    if behavior(recovered)!=behavior(version['plan']): raise ValueError('当前代码重放与已保存计划不一致，不能冒充同一结果')
    return dict(available=True,simulated=True,scene_id=scene['id'],revision=scene['revision'],
        base_version=version['base'],version=number,event=deepcopy(event),states=states,
        baseline=baseline,recovered=recovered,adopted_reference_metrics=deepcopy(parent['metrics']),
        scope='相同已采用父计划、事件时刻位置、车上货、历史交付与新收货窗口；不采用、不推进、不改存储',
        limits='同一模拟案例，有限启发式，无全局最优保证；不代表企业收益或平均效果')


def review_version(scene, number=None):
    """Read-only event-time explanation; never move the live simulation clock."""
    if not scene: return dict(available=False)
    number = number if number is not None else scene['candidate'] or scene['active']
    version=next((v for v in scene['versions'] if v['id']==number),None)
    if version is None: raise ValueError('计划版本不存在')
    plan=version['plan'];parent=next((v['plan'] for v in scene['versions'] if v['id']==version.get('base')),plan)
    before=dynamic.assignments(parent);after=dynamic.assignments(plan)
    old_orders={o['id']:o for o in parent['data']['orders']}
    states=version.get('states') or {vid:r['anchor'] for vid,r in plan['routes'].items()}
    completed={s['id'] for q in states.values() for s in q['completed'] if s['id']!='W'}
    cargo_owner={oid:vid for vid,q in states.items() for oid in q['cargo']}
    rows=[]
    for o in plan['data']['orders']:
        oid=o['id'];old=before.get(oid);new=after.get(oid);reasons=[]
        if oid not in old_orders: reasons.append('新增订单')
        if new is None: reasons.append('未安排，需人工处理')
        if old and new and oid not in completed:
            if old[0]!=new[0]: reasons.append('车辆变化')
            if old[2]!=new[2]: reasons.append('前一站变化')
            if abs(old[1]-new[1])>1: reasons.append('ETA变化超过1分钟')
        old_window={k:old_orders[oid][k] for k in ('open','due','close')} if oid in old_orders else None
        window={k:o[k] for k in ('open','due','close')}
        if old_window and old_window!=window: reasons.append('收货窗口变化')
        rows.append(dict(id=oid,store=o.get('store',oid),quantity=o['quantity'],
            delivered=oid in completed,cargo_owner='delivered' if oid in completed else cargo_owner.get(oid,'warehouse'),
            before=dict(vehicle=old[0],eta=old[1]) if old else None,
            after=dict(vehicle=new[0],eta=new[1],late_minutes=round(max(0,new[1]-o['due']),2)) if new else None,
            old_window=old_window,window=window,reasons=reasons))
    event=next((e for e in scene['events'] if e['version']==number),None)
    return dict(available=True,scene_id=scene['id'],revision=scene['revision'],version=number,
        base=version.get('base'),status=version['status'],reason=version.get('reason'),event=deepcopy(event),
        time=version.get('time',480),executed_until=version['executed_until'],rows=rows,
        frozen_delivery_count=len(completed),metrics=deepcopy(plan['metrics']),
        adoptable=scene['candidate']==number and not plan['metrics']['unserved'],
        blocked_reason='未找到完整方案；已采用计划保留，未安排原货仍留原车' if plan['metrics']['unserved'] else None,
        pickups=[dict(vehicle=vid,arrival=s['arrival'],finish=s['finish'],load_ids=s['load_ids'],
            completed=s in states[vid]['completed']) for vid,r in plan['routes'].items() for s in r['stops'] if s['id']=='W'])


class DispatchStore:
    def __init__(self,path):
        self.path=Path(path)
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute('CREATE TABLE IF NOT EXISTS dispatch_scenes (id TEXT PRIMARY KEY, body TEXT NOT NULL)')

    def read(self, scene_id=None):
        with closing(sqlite3.connect(self.path)) as c, c:
            row=c.execute('SELECT body FROM dispatch_scenes WHERE id=?',(scene_id,)).fetchone() if scene_id is not None else c.execute('SELECT body FROM dispatch_scenes ORDER BY rowid DESC LIMIT 1').fetchone()
        return json.loads(row[0]) if row else None

    def archive(self):
        with closing(sqlite3.connect(self.path)) as c:
            rows=c.execute('SELECT body FROM dispatch_scenes ORDER BY rowid DESC LIMIT 20').fetchall()
            total=c.execute('SELECT COUNT(*) FROM dispatch_scenes').fetchone()[0]
        summaries=[]
        for index,(body,) in enumerate(rows):
            scene=json.loads(body);plan=next(v['plan'] for v in scene['versions'] if v['id']==scene['active'])
            summaries.append(dict(id=scene['id'],revision=scene['revision'],clock=scene['clock'],active=scene['active'],
                candidate=scene['candidate'],versions=len(scene['versions']),orders=len(plan['data']['orders']),
                delivered=sum(value=='delivered' for value in scene['cargo'].values()),current=index==0))
        return dict(scenes=summaries,total=total,limit=20)

    def compare(self):
        return comparison(self.read())

    def act(self,action,body):
        with closing(sqlite3.connect(self.path,timeout=15)) as c, c:
            c.execute('BEGIN IMMEDIATE')
            if action=='start':
                latest=c.execute('SELECT body FROM dispatch_scenes ORDER BY rowid DESC LIMIT 1').fetchone()
                if latest:
                    old=json.loads(latest[0]); self.guard(old,body)
                plan=normal.solve(body.get('data'))
                if plan['metrics']['unserved']: raise ValueError('需先生成完整正常计划')
                for vid,r in plan['routes'].items():
                    cargo=r['sequence']; w,vol=normal.totals(cargo,{o['id']:o for o in plan['data']['orders']})
                    r['anchor']=dict(point=[0,0],available=480,traveled=0,cargo=cargo[:],completed=[],remaining=cargo[:],weight_kg=w,volume_m3=vol)
                state=dict(id=uuid.uuid4().hex,revision=1,clock=480,active=1,candidate=None,
                    versions=[dict(id=1,status='adopted',plan=plan,executed_until=480)],events=[],execution=[])
            else:
                row=c.execute('SELECT body FROM dispatch_scenes WHERE id=?',(body.get('id'),)).fetchone()
                if not row: raise Conflict('场景不存在，请刷新')
                state=json.loads(row[0]); self.guard(state,body)
                # An archived tab cannot resurrect an older scene as current.
                latest=c.execute('SELECT id FROM dispatch_scenes ORDER BY rowid DESC LIMIT 1').fetchone()[0]
                if latest!=state['id']: raise Conflict('场景已归档，请刷新')
                base=next(v['plan'] for v in state['versions'] if v['id']==state['active'])
                if action=='event':
                    event=body.get('event'); kind=body.get('kind')
                    if not isinstance(event,dict): raise ValueError('事件必须是对象')
                    plan,states=calculate(base,event,kind,state['clock'])
                    if state['candidate']:
                        previous=next(v for v in state['versions'] if v['id']==state['candidate'])
                        previous.update(status='superseded',reason='新的事件试算替代了候选；未采用、未执行')
                    number=len(state['versions'])+1
                    state['versions'].append(dict(id=number,status='candidate',base=state['active'],time=event['time'],plan=plan,states=states,executed_until=None))
                    state['events'].append(dict(kind=kind,event=event,version=number))
                    state['candidate']=number
                elif action=='adopt':
                    if body.get('candidate')!=state['candidate'] or not state['candidate']: raise Conflict('候选已变化，请刷新核对')
                    version=next(v for v in state['versions'] if v['id']==state['candidate'])
                    if version['plan']['metrics']['unserved']: raise ValueError('候选不完整，保留已采用计划，需人工处理')
                    self.advance(state,base,version['time'])
                    version['status']='adopted'; version['executed_until']=version['time']
                    state['active']=version['id']; state['candidate']=None
                elif action=='discard':
                    if not state['candidate'] or body.get('candidate')!=state['candidate']: raise Conflict('候选已变化，请刷新核对')
                    version=next(v for v in state['versions'] if v['id']==state['candidate'])
                    version.update(status='discarded',reason='用户放弃候选；未采用、未执行')
                    state['candidate']=None
                elif action=='advance':
                    normal.numeric(body.get('time'),'模拟执行时间',state['clock'],1440)
                    self.advance(state,base,body['time'])
                    if state['candidate']:
                        previous=next(v for v in state['versions'] if v['id']==state['candidate'])
                        previous.update(status='superseded',reason='模拟执行进度已变化，原候选失效；未采用、未执行')
                        state['candidate']=None
                else: raise ValueError('未知操作')
                state['revision']+=1
            active=next(v['plan'] for v in state['versions'] if v['id']==state['active'])
            state['states']=snapshot(active,state['clock'])
            state['cargo']={o['id']:'warehouse' for o in active['data']['orders']}
            for vid,s in state['states'].items():
                for oid in s['cargo']: state['cargo'][oid]=vid
                for stop in s['completed']:
                    if stop['id']!='W': state['cargo'][stop['id']]='delivered'
            c.execute('INSERT OR REPLACE INTO dispatch_scenes(id,body) VALUES (?,?)',(state['id'],json.dumps(state,ensure_ascii=False)))
            return state

    @staticmethod
    def guard(state,body):
        if body.get('id')!=state['id'] or body.get('revision')!=state['revision']: raise Conflict('页面版本已过期，请刷新，不能覆盖新计划')

    @staticmethod
    def advance(state,base,time):
        states=snapshot(base,time)
        state['execution'].append(dict(version=state['active'],time=time,states=states))
        old=next(v for v in state['versions'] if v['id']==state['active'])
        old['executed_until']=time
        if time>state['clock']: old['status']='executed'
        state['clock']=time
