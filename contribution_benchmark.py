"""Same-state CSV exception ablation, in temporary databases only.

Run: python contribution_benchmark.py
No AI requests, network, live SQLite writes or external publication.
The baseline is a coded rule, not a measured human dispatcher.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from continuous import DispatchStore, calculate
import matrix_dispatch as md
import pilot_benchmark as pb
from verify_matrix_dispatch import audit

ROOT = Path(__file__).resolve().parent


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(',', ':')).encode('utf-8')).hexdigest()


def audit_alternative(scene, plan, states):
    """Use the independent saved-route auditor, including real simulated receipts."""
    view = deepcopy(scene)
    view.update(live_plan=plan, states=states)
    view['cargo'] = {o['id']: 'warehouse' for o in plan['data']['orders']}
    for vid, state in states.items():
        assert plan['routes'][vid]['anchor'] == state
        assert plan['routes'][vid]['stops'][:len(state['completed'])] == state['completed']
        for oid in state['cargo']:
            view['cargo'][oid] = vid
        for stop in state['completed']:
            if not md.is_pickup(stop['id']):
                view['cargo'][stop['id']] = 'delivered'
    result = audit(view)
    served = {s['id'] for r in plan['routes'].values() for s in r['stops']
              if not md.is_pickup(s['id'])}
    missing = set(view['cargo']) - served
    assert missing == set(plan['unserved_ids'])
    assert len(served) == plan['metrics']['served']
    assert len(missing) == plan['metrics']['unserved']
    result['service_set_checked'] = True
    return result, sorted(served)


def compare_case(name, scene, kind, event):
    parent = scene['live_plan']; parent_hash = digest(parent)
    alternatives = {}; shared_states = None; sets = {}
    for method in ('baseline', 'optimized'):
        plan, states = calculate(parent, deepcopy(event), kind, scene['clock'], method)
        assert digest(parent) == parent_hash, 'Evaluation mutated the parent plan'
        if shared_states is None:
            shared_states = states
        else:
            assert shared_states == states, 'Unequal event-time execution states'
        checked, sets[method] = audit_alternative(scene, plan, states)
        replay, replay_states = calculate(parent, deepcopy(event), kind, scene['clock'], method)
        assert replay_states == states
        assert all(replay[key] == plan[key] for key in ('data', 'routes', 'metrics', 'unserved_ids'))
        assert digest(parent) == parent_hash
        checked['deterministic_replay_checked'] = True
        alternatives[method] = dict(plan=plan, audit=checked)
    a, b = (alternatives[m]['plan']['metrics'] for m in ('baseline', 'optimized'))
    same_set = sets['baseline'] == sets['optimized']
    delta = dict(served=b['served']-a['served'], unserved=b['unserved']-a['unserved'])
    if same_set:
        delta.update(late_minutes=round(b['late_minutes']-a['late_minutes'], 2),
                     cost=round(b['cost']-a['cost'], 2),
                     changed_orders=b['changed_orders']-a['changed_orders'])
    return dict(case=name, kind=kind, event=event, parent_sha256=parent_hash,
                source_sha256=parent['data']['source_sha256'],
                initial_plan=scene['versions'][0]['plan'], parent_plan=parent,
                receipts=scene['receipts'], states=shared_states,
                alternatives=alternatives, served_ids=sets, same_service_set=same_set,
                delta_optimized_minus_baseline=delta,
                savings_rate_allowed=same_set,
                comparable_scope='相同父计划、事件、时刻、货物、签收历史、道路、费用与约束；仅改变候选搜索规则')


def prepare(directory, stores, seed):
    bundle = pb.sample(stores, seed)
    # Broad windows guarantee a complete common starting plan. These choices
    # are part of the constructed experiment, not empirical business settings.
    for row in bundle['orders']:
        row.update(open='08:00', due='11:00', close='18:00', allowed_vehicles='')
    for vehicle in bundle['vehicles']:
        vehicle.update(shift_end='20:00')
    pb.write_bundle(bundle, directory)
    files = {name: (Path(directory)/name).read_text(encoding='utf-8') for name in md.FILES}
    store = DispatchStore(Path(directory)/'benchmark.sqlite3')
    scene = store.act('start', dict(csv_files=files))
    # One actual manual-simulation signature precedes every comparison.
    vid, route = max(scene['live_plan']['routes'].items(), key=lambda item: len(item[1]['stops']))
    stop = route['stops'][0]; oid = stop['id']
    order = next(o for o in scene['live_plan']['data']['orders'] if o['id'] == oid)
    arrival = stop['arrival']
    for kind, time in [('arrived', arrival), ('unloading', arrival),
                       ('signed', arrival+order['service_minutes'])]:
        scene = store.act('receipt', dict(id=scene['id'], revision=scene['revision'],
                          vehicle=vid, order=oid, receipt=kind, time=time,
                          source='manual_simulation'))
        audit(scene)
    return store, scene


def urgent_event(scene, quantity=2):
    return dict(time=scene['clock'], store='STORE_002', due=660, close=1080,
                lines=[dict(sku='ADDED_A', quantity=quantity,
                            unit_weight_kg=7, unit_volume_m3=.03)])


def run():
    results = []; refusals = []
    for stores in (3, 12):
        for seed in (20261011, 20261012):
            with TemporaryDirectory() as tmp:
                store, scene = prepare(tmp, stores, seed)
                remaining = [(vid, q) for vid, q in scene['states'].items() if q['remaining']]
                vid, state = max(remaining, key=lambda item: len(item[1]['remaining']))
                # Prefer a task whose current road leg is not already committed.
                target = state['remaining'][1] if state.get('transit') and len(state['remaining']) > 1 else state['remaining'][0]
                parent_stop = next(s for s in scene['live_plan']['routes'][vid]['stops'] if s['id'] == target)
                for delay in (0, 60, 180):
                    opening = 480 if delay == 0 else parent_stop['arrival']+delay
                    event = dict(time=scene['clock'], store=target, open=opening,
                                 due=max(660, opening), close=1080)
                    results.append(compare_case(f'{stores}店_seed{seed}_开门延后{delay}', scene, 'window', event))
                # Tight deadline retains incomplete alternatives rather than
                # quietly discarding a difficult event from the experiment.
                results.append(compare_case(f'{stores}店_seed{seed}_硬截止不可达', scene, 'window',
                    dict(time=scene['clock'], store=target, open=scene['clock'],
                         due=scene['clock'], close=scene['clock'])))
                for quantity in (2, 1000):
                    results.append(compare_case(f'{stores}店_seed{seed}_紧急{quantity}箱', scene,
                                                'urgent', urgent_event(scene, quantity)))
                # Adopt a common optimized first order before comparing the
                # second; this measures a local decision, not entire policies.
                event = urgent_event(scene)
                scene = store.act('event', dict(id=scene['id'], revision=scene['revision'], kind='urgent', event=event))
                scene = store.act('adopt', dict(id=scene['id'], revision=scene['revision'], candidate=scene['candidate']))
                audit(scene)
                results.append(compare_case(f'{stores}店_seed{seed}_第二个紧急单', scene, 'urgent', urgent_event(scene)))
                scene = store.act('event', dict(id=scene['id'], revision=scene['revision'], kind='urgent', event=urgent_event(scene)))
                scene = store.act('adopt', dict(id=scene['id'], revision=scene['revision'], candidate=scene['candidate']))
                before = store.read()
                try:
                    store.act('event', dict(id=scene['id'], revision=scene['revision'], kind='urgent', event=urgent_event(scene)))
                except ValueError as error:
                    assert '两个' in str(error), str(error)
                    assert store.read() == before
                    refusals.append(dict(stores=stores, seed=seed, case='第三紧急单',
                                         rejected=True, transaction_unchanged=True, reason=str(error)))
                else:
                    raise AssertionError('Third order should be rejected')
    counts = dict(cases=len(results), independently_audited_alternatives=2*len(results),
                  same_service_set=sum(c['same_service_set'] for c in results),
                  different_service_set=sum(not c['same_service_set'] for c in results),
                  incomplete_alternatives=sum(c['alternatives'][m]['plan']['metrics']['unserved'] > 0
                                              for c in results for m in ('baseline', 'optimized')),
                  no_metric_change=sum(c['alternatives']['baseline']['plan']['metrics'] ==
                                       c['alternatives']['optimized']['plan']['metrics'] for c in results),
                  third_order_refusals=len(refusals))
    return dict(simulated=True, generator='pilot_benchmark.sample', seeds=[20261011, 20261012],
                scenario_design='3/12店；宽初始窗口；先人工模拟签收一单；窗口延后/硬截止/紧急小单/超载/第二紧急单',
                baseline='收货变化保持原序等待；紧急单在原序末尾回仓取货再送，枚举准入车辆',
                optimized='收货变化原车内受影响单插入；紧急单枚举回仓及送货位置',
                limits='定向构造的小样本消融，不是企业人工基线、随机抽样、AI准确率、全局最优或生产性能；签收是人工模拟。第二紧急单共享先前采用方案。',
                summary=counts, cases=results, refusals=refusals)


def markdown(report):
    lines = ['# 新通知调整：同状态算法贡献对照', '',
             '2026-10-11。运行 `python contribution_benchmark.py`，只使用临时数据库。', '',
             report['limits'], '', '基线：'+report['baseline']+'。',
             '调整：'+report['optimized']+'。', '',
             '两边独立核算货物、签收历史、取货、容量、有向道路、窗口、班次、费用及服务集合。',
             '差额为“调整−基线”；负迟到/费用差额表示减少。配送集合不同时不报告迟到和费用差额。', '',
             '| 构造情境 | 已安排 基线→调整 | 迟到分钟 基线→调整 | 模拟费用 基线→调整 | 同配送集合 |',
             '|---|---:|---:|---:|---|']
    for case in report['cases']:
        a, b = (case['alternatives'][m]['plan']['metrics'] for m in ('baseline', 'optimized'))
        lines.append(f"| {case['case']} | {a['served']}→{b['served']} | {a['late_minutes']}→{b['late_minutes']} | {a['cost']}→{b['cost']} | {'是' if case['same_service_set'] else '否'} |")
    lines += ['', '汇总计数：`'+json.dumps(report['summary'], ensure_ascii=False)+'`。', '',
              '完整输入父计划、已签收回执、共享状态、两个输出和核算结果见同目录 contribution-benchmark.json。',
              '保留无变化、不完整方案和费用上升；不完整候选在业务入口禁止采用。',
              '第三紧急单拒绝与事务不变是范围保护证据，不是算法增益。求解毫秒在JSON保留，仅反映本次机器运行。',
              '本轮没有调用AI，也没有验证AI带来的人工耗时改善。']
    return '\n'.join(lines)+'\n'


if __name__ == '__main__':
    report = run()
    (ROOT/'evidence/contribution-benchmark.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (ROOT/'evidence/新通知算法对照验证.md').write_text(markdown(report), encoding='utf-8')
    print(json.dumps(report['summary'], ensure_ascii=False))
