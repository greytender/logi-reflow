"""Whole-scene rough pass in a temporary scene; no AI or live DB writes."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
import continuous
import dispatch_handoff
import loading
import loading_benchmark as lb


def sample():
    nodes=['WH']+[f'S{i:02d}' for i in range(1,7)];orders=[];items=[]
    for oid in nodes[1:]:
        for sku in ('STANDARD','FRAGILE'):
            orders.append(dict(stop_id=oid,sku=sku,quantity=1,unit_weight_kg=4,
                unit_volume_m3=.024,open='08:00',due='10:00',close='12:00',
                service_minutes=5,allowed_vehicles='SMALL|LARGE'))
            items.append(dict(order=oid,sku=sku,length_cm=40,width_cm=30,height_cm=20,
                rotate_base=True,fragile=sku=='FRAGILE',max_top_load_kg=0 if sku=='FRAGILE' else 12))
    vehicles=[dict(vehicle_id=vid,max_weight_kg=w,max_volume_m3=vol,fixed_cost=cost,
                   cost_per_km=2,shift_start='08:00',shift_end='14:00')
              for vid,w,vol,cost in [('SMALL',20,.32,30),('LARGE',50,.96,70)]]
    return dict(csv_files={'orders.csv':lb.text(orders),'vehicles.csv':lb.text(vehicles),
        'travel.csv':lb.text([dict(from_id=a,to_id=b,distance_km=abs(i-j)+1,
             travel_minutes=(abs(i-j)+1)*3+(1 if i>j else 0))
             for i,a in enumerate(nodes) for j,b in enumerate(nodes) if a!=b]),
        'settings.json':json.dumps(dict(depot_id='WH',data_kind='simulated',
             travel_source='constructed directed travel table; not live map',window_rule='finish'))},
        loading_profile=dict(schema='loading_v1',data_kind='simulated',items=items,
            vehicles=[dict(id='SMALL',length_cm=100,width_cm=80,height_cm=40),
                      dict(id='LARGE',length_cm=150,width_cm=80,height_cm=80)]))


def overview(packed):
    rows=[];defs={x['id']:x for x in packed['items']}
    for vid,r in packed['optimized']['routes'].items():
        v=packed['vehicles'][vid];coords=r['loading']['placements']
        weight=sum(defs[p['id']]['weight_kg'] for p in coords)
        volume=sum(p['dx']*p['dy']*p['dz'] for p in coords)/1e6
        rows.append(dict(vehicle=vid,sequence=r['sequence'],boxes=len(coords),weight_kg=weight,
            weight_use_percent=round(100*weight/v['max_weight_kg'],1),cargo_volume_m3=round(volume,6),
            cuboid_space_use_percent=round(100*volume/(v['length_cm']*v['width_cm']*v['height_cm']/1e6),1),
            predicted_cost=r['cost'],predicted_return=r['return_at']))
    return rows


def run(payload):
    if not isinstance(payload,dict) or set(payload)!={'csv_files','loading_profile'}:
        raise ValueError('完整场景需要四份CSV与loading_profile，不读取或覆盖运行场景')
    started=perf_counter();packed=loading.solve(payload['csv_files'],payload['loading_profile'])
    result=dict(schema='scenario_overview_v1',local_isolated=True,ai_called=False,
        source='offline sample or user-declared data; authenticity unverified',
        loading=packed,vehicles=overview(packed),replay=None,exception=None,
        limits=['复用有限路线/装载算法，不保证全局最优',
                '空间利用率按长方体容积计算，不代表真实车辆或收益',
                '回执由固定脚本生成，非司机/门店事实，不是AI执行',
                '临时数据库，不采用或覆盖当前保存场景',
                '固定装载新货插入、温控、轴荷、侧门与真实接口未实现'])
    if not packed['complete']:
        result['blocked']='联合方案不完整，只展示已核算部分，不运行配送回放'
        result['total_ms']=round((perf_counter()-started)*1000,2);return result
    with TemporaryDirectory() as tmp:
        store=continuous.DispatchStore(Path(tmp)/'isolated.sqlite3')
        scene=store.act('start',payload);departure=deepcopy(scene)
        if scene['live_plan']['fixed_loading']['source_sha256']!=packed['input_sha256']:
            raise ValueError('场景输入与装载试算不一致')
        for vid in scene['live_plan']['routes']:
            if scene['live_plan']['fixed_loading']['manifest']['placements'][vid]!=packed['optimized']['routes'][vid]['loading']['placements']:
                raise ValueError('重复计算布局不同，不能回放为同一场景')
        route=next((r for r in scene['live_plan']['routes'].values() if len(r['sequence'])>=2),None)
        if route:
            oid=route['sequence'][1];o=next(o for o in scene['live_plan']['data']['orders'] if o['id']==oid)
            opening=max(o['open'],scene['clock']+75)
            if opening<=o['close']:
                event=dict(time=scene['clock'],store=oid,open=opening,due=max(opening,o['due']),close=o['close'])
                try:
                    candidate,_=continuous.calculate(scene['live_plan'],event,'window',scene['clock'])
                    result['exception']=dict(status='candidate_only',event=event,plan=candidate,
                        adoptable=not candidate['metrics']['unserved'],
                        statement='脚本构造收货变化，只试算；配送回放仍采用初始计划')
                except ValueError as exc:result['exception']=dict(status='rejected',event=event,reason=str(exc))
        receipts=[]
        while any(q['remaining'] for q in scene['states'].values()):
            jobs=[]
            for vid,q in scene['states'].items():
                if not q['remaining']:continue
                oid=q['remaining'][0];stop=next(s for s in scene['live_plan']['routes'][vid]['stops'] if s['id']==oid)
                jobs.append((max(scene['clock'],stop['arrival']),vid,oid))
            t,vid,oid=min(jobs);o=next(o for o in scene['live_plan']['data']['orders'] if o['id']==oid)
            for kind,tm in [('arrived',t),('unloading',max(t,o['open'])),('signed',max(t,o['open'])+o['service_minutes'])]:
                scene=store.act('receipt',dict(id=scene['id'],revision=scene['revision'],vehicle=vid,
                    order=oid,receipt=kind,time=tm,source='manual_simulation'))
                receipts.append(dict(vehicle=vid,order=oid,receipt=kind,time=tm,provenance='fixed_script_simulation'))
        return_time=max(scene['clock'],max(r['return_at'] for r in scene['live_plan']['routes'].values()))
        scene=store.act('advance',dict(id=scene['id'],revision=scene['revision'],time=return_time))
        if any(q['cargo'] or q['node']!=scene['live_plan']['data']['settings']['depot_id'] for q in scene['states'].values()):
            raise ValueError('回放未完成签收或回仓')
        if scene['live_plan']['fixed_loading']!=departure['live_plan']['fixed_loading']:
            raise ValueError('回放改写了原装载快照')
        result['replay']=dict(departure_handoff=dispatch_handoff.report(departure),
            final_handoff=dispatch_handoff.report(scene),receipts=receipts,
            signed_orders=sum(x=='delivered' for x in scene['cargo'].values()),returned=True,
            coordinates_unchanged=True,restart_equal=scene==continuous.DispatchStore(store.path).read(),
            final_metrics=scene['live_plan']['metrics'],finished_at=scene['clock'])
    result['total_ms']=round((perf_counter()-started)*1000,2);return result


if __name__=='__main__':
    payload=sample();folder=Path('examples/scenario-six');folder.mkdir(exist_ok=True)
    for name,content in payload['csv_files'].items():(folder/name).write_text(content,encoding='utf-8')
    (folder/'loading.json').write_text(json.dumps(payload['loading_profile'],ensure_ascii=False,indent=2),encoding='utf-8')
    result=run(payload);Path('evidence/scenario-overview.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(complete=result['loading']['complete'],vehicles=result['vehicles'],
        signed=result['replay']['signed_orders'] if result['replay'] else 0,
        receipts=len(result['replay']['receipts']) if result['replay'] else 0,total_ms=result['total_ms']),ensure_ascii=False))
