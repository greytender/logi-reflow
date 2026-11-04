"""Replay an operator-supplied plan and both search methods on one locked input."""
import argparse
import json
from math import isfinite
from pathlib import Path

import application
import recovery_search


def input_stamp(data, profile):
    return application.fingerprint(dict(data=application.canonicalize(data), loading_profile=profile))


def compare(batch, baseline):
    data = application.canonicalize(batch['data'])
    profile = batch.get('loading_profile')
    training = batch.get('search_scenarios', [])
    held_out = batch.get('evaluation_scenarios', [])
    if not held_out:
        raise ValueError('至少提供一个独立评估回收情景')
    nominal = {o['id']: o.get('pickup') for o in data['orders']}
    if any({**nominal, **test} == {**nominal, **fit} for test in held_out for fit in training):
        raise ValueError('评估情景不得与求解情景重复')
    stamp = input_stamp(batch['data'], profile)
    if baseline.get('input_sha256') != stamp:
        raise ValueError('人工方案与当前订单、车辆、路网、规则或箱规的指纹不一致')
    source = baseline.get('source')
    reference = baseline.get('reference')
    if source not in ('simulated', 'operator_record') or not isinstance(reference, str) or not reference.strip():
        raise ValueError('请填写方案来源类别与可核对的本地记录编号')
    elapsed = baseline.get('workflow_seconds')
    if elapsed is not None and (type(elapsed) not in (int, float) or not isfinite(elapsed) or elapsed <= 0):
        raise ValueError('人工全流程耗时需填写正数秒，未知请留空')
    if source == 'operator_record' and data['settings']['data_kind'] == 'simulated':
        raise ValueError('构造批次只能使用构造基线')
    routes = baseline['routes']
    if not isinstance(routes, dict) or set(routes) != {v['id'] for v in data['vehicles']}:
        raise ValueError('请填全本批车辆，未用车辆填写空路线')
    assigned = recovery_search.evaluate_plan(data, routes, profile=profile, scenarios=held_out)
    if assigned is None:
        raise ValueError('基线未通过共同模型的名义约束，先核对输入、站序或实际作业差异')
    if assigned['metrics']['unserved']:
        raise ValueError('基线须覆盖同一批全部门店，每家只出现一次')
    results = {'supplied_plan': dict(metrics=assigned['metrics'], routes=routes)}
    for method in ('local', 'robust_objective_alns'):
        planned = recovery_search.solve(data, method=method, profile=profile, scenarios=training)
        replayed = recovery_search.evaluate_plan(data, planned['routes'], profile=profile, scenarios=held_out)
        metrics = replayed['metrics']
        results[method] = dict(metrics=metrics, routes=planned['routes'], search_ms=planned['elapsed_ms'],
            full_batch_on_time=(metrics['unserved'] == metrics['late_orders'] == 0))
    return dict(input_sha256=stamp, data_kind=data['settings']['data_kind'], baseline_source=source,
        baseline_reference=reference, reported_baseline_workflow_seconds=elapsed, evaluation_scenarios=len(held_out),
        results=results, scope='共同模型复算。回收为压力情景，费用为输入口径试算，搜索毫秒与人工全流程秒数分别记录。')


def example():
    data = application.sample()
    stores = [o['id'] for o in data['orders']]
    batch = dict(data=data, search_scenarios=[{stores[0]: dict(cartons=20, totes=5)}],
        evaluation_scenarios=[{stores[0]: dict(cartons=30, totes=8)}, {stores[1]: dict(cartons=40, totes=4)}])
    baseline = dict(source='simulated', reference='DEMO-CONSTRUCTED-PLAN', workflow_seconds=None,
        input_sha256=input_stamp(data, None),
        routes=recovery_search.solve(data, method='local')['routes'])
    return batch, baseline


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='同批排车计划离线对照')
    parser.add_argument('--batch', type=Path)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--baseline-template', action='store_true')
    args = parser.parse_args()
    if args.baseline_template and args.batch and not (args.demo or args.baseline):
        batch = json.loads(args.batch.read_text(encoding='utf-8-sig'))
        data = application.canonicalize(batch['data'])
        template = dict(source=None, reference='', workflow_seconds=None,
            input_sha256=input_stamp(batch['data'], batch.get('loading_profile')),
            routes={v['id']: [] for v in data['vehicles']})
        print(json.dumps(template, ensure_ascii=False, indent=2))
        raise SystemExit
    if args.demo and not (args.batch or args.baseline or args.baseline_template):
        batch, baseline = example()
    elif not (args.demo or args.baseline_template) and args.batch and args.baseline:
        batch, baseline = (json.loads(path.read_text(encoding='utf-8-sig')) for path in (args.batch, args.baseline))
    else:
        parser.error('请选择 --demo，或同时提供 --batch 和 --baseline')
    try:
        report = compare(batch, baseline)
    except (ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(json.dumps(report, ensure_ascii=False, indent=2))
