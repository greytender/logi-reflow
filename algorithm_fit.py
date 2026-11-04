"""Factorial algorithm-fit experiment; public geography and simulated operations.

Run candidates on training scenarios, select on independent screening scenarios,
and report once on a final holdout. This never changes application dispatch.
"""
import argparse
from copy import deepcopy
from hashlib import sha256
from itertools import product
import json
from math import ceil
from pathlib import Path
from random import Random
from statistics import median
from time import perf_counter

import application
import china_city_robustness as cities
import recovery_search

ROOT = Path(__file__).resolve().parent
FACTORS = {
    'departure_load': (.60, .92),
    'pickup_volatility': ('low', 'high'),
    'window_slack_minutes': (6, 45),
    'tariff_mode': ('incremental', 'whole_above'),
    'shift_slack_minutes': (8, 90),
}
METHODS = ('local', 'robust_local', 'robust_objective_alns')


def configurations():
    return [dict(zip(FACTORS, values)) for values in product(*FACTORS.values())]


def fixture(source, city, wave, factors):
    data, baseline, source_rows = cities.fixture(source, cities.CITIES.index(city), wave)
    before = recovery_search.evaluate_plan(data, baseline)
    for vehicle in data['vehicles']:
        route = before['plan']['routes'][vehicle['id']]
        vehicle['max_weight_kg'] = route['weight_kg']/factors['departure_load']
        vehicle['max_volume_m3'] = route['volume_m3']/factors['departure_load']
        vehicle['shift_end'] = ceil(route['return_at'])+factors['shift_slack_minutes']
    orders = {order['id']: order for order in data['orders']}
    for route in before['plan']['routes'].values():
        for stop in route['stops']:
            orders[stop['id']]['due'] = ceil(stop['finish'])+factors['window_slack_minutes']
            orders[stop['id']]['close'] = orders[stop['id']]['due']+30
    data['business']['tariff'].update(
        mode=factors['tariff_mode'],
        included_km=median(r['distance'] for r in before['plan']['routes'].values()))
    checked = recovery_search.evaluate_plan(data, baseline)
    if checked is None or checked['metrics']['late_orders'] or checked['metrics']['unserved']:
        raise ValueError('factorial fixture must retain a complete nominal baseline')
    return data, baseline, source_rows


def scenarios(data, seed, count, volatility):
    rng = Random(seed)
    ids = [o['id'] for o in data['orders']]
    order_map = {o['id']: o for o in data['orders']}
    edges = list(data['legs'])
    output = []
    for index in range(count):
        family = index%4
        hot = rng.sample(ids, max(4, len(ids)//2) if family == 3 else 1+index%3)
        pickups = {}
        for oid in hot:
            nominal = order_map[oid]['pickup']
            if volatility == 'high':
                pickups[oid] = dict(cartons=rng.randint(8, 30), totes=rng.randint(25, 85))
            else:
                pickups[oid] = {k: max(0, round(v*rng.uniform(.8, 1.3))) for k, v in nominal.items()}
        output.append(dict(
            pickup=pickups if family != 1 else {},
            extra_service_minutes=({oid: round(rng.uniform(1, 12 if volatility == 'high' else 3), 5)
                                   for oid in hot} if family != 1 else {}),
            road_delay_minutes=({edge: round(rng.uniform(1, 20), 5)
                                for edge in rng.sample(edges, len(ids)*(2 if family == 3 else 1))}
                               if family != 0 else {})))
    return output


def independent_replay(data, routes, stress):
    """Arithmetic replay, independent of search risk cache/evaluator/business.apply.

    All constraints are checked even after a first breach, so cause counts can
    overlap. Each route uses one unchanged assignment across all scenarios.
    """
    orders = {o['id']: o for o in data['orders']}
    p = data['business']['packaging']
    depot = data['settings']['depot_id']
    failures = set()
    fulfilled_pickup_units = 0
    for vehicle in data['vehicles']:
        seq = routes.get(vehicle['id'], [])
        if not seq:
            continue
        weight = sum(orders[oid]['quantity']*orders[oid]['unit_weight_kg'] for oid in seq)
        volume = sum(orders[oid]['quantity']*orders[oid]['unit_volume_m3'] for oid in seq)
        clock, previous = vehicle['shift_start'], depot
        route_failures = set()
        units = 0
        if weight > vehicle['max_weight_kg']+1e-8 or volume > vehicle['max_volume_m3']+1e-8:
            route_failures.add('capacity')
        for oid in seq:
            order = orders[oid]
            edge = previous+'>'+oid
            clock = max(clock+data['legs'][edge]['travel_minutes']+
                        stress.get('road_delay_minutes', {}).get(edge, 0), order['open'])
            arrival = clock
            clock += order['service_minutes']+stress.get('extra_service_minutes', {}).get(oid, 0)
            deadline = clock if data['settings']['window_rule'] == 'finish' else arrival
            if deadline > order['due']+1e-8:
                route_failures.add('window')
            pickup = stress.get('pickup', {}).get(oid, order['pickup'])
            cartons, totes = pickup['cartons'], pickup['totes']
            weight += cartons*p['carton_weight_kg']+totes*p['tote_weight_kg']-order['quantity']*order['unit_weight_kg']
            volume += cartons*p['carton_flat_m3']+(p['tote_first_m3']+(totes-1)*p['tote_nested_m3'] if totes else 0)-order['quantity']*order['unit_volume_m3']
            if weight > vehicle['max_weight_kg']+1e-8 or volume > vehicle['max_volume_m3']+1e-8:
                route_failures.add('capacity')
            units += cartons+totes
            previous = oid
        edge = previous+'>'+depot
        clock += data['legs'][edge]['travel_minutes']+stress.get('road_delay_minutes', {}).get(edge, 0)
        if clock > vehicle['shift_end']+1e-8:
            route_failures.add('shift')
        if not route_failures:
            fulfilled_pickup_units += units
        failures.update(route_failures)
    return dict(passed=not failures, causes=sorted(failures),
                pickup_units_on_completed_routes=fulfilled_pickup_units)


def measure(data, routes, stress, elapsed_ms=0):
    measured = cities.measure(data, routes, stress, elapsed_ms)
    replay = [independent_replay(data, routes, s) for s in stress]
    oracle_passes = sum(r['passed'] for r in replay)
    if oracle_passes != measured['metrics']['scenario_full_completions']:
        raise AssertionError('independent stress replay disagrees with search evaluator')
    measured['independent_replay'] = dict(
        full_completions=oracle_passes,
        failures_by_cause={cause: sum(cause in r['causes'] for r in replay)
                           for cause in ('capacity', 'window', 'shift')},
        pickup_units_on_completed_routes=sum(r['pickup_units_on_completed_routes'] for r in replay))
    return measured


def guardrails(base, candidate, max_drop=.02):
    if set(base['load_by_vehicle']) != set(candidate['load_by_vehicle']):
        return False
    if candidate['metrics']['unserved'] or candidate['metrics']['late_orders']:
        return False
    if candidate['metrics']['cost'] > base['metrics']['cost']+1e-6:
        return False
    return all(value+max_drop+1e-6 >= reference
               for vid, values in candidate['load_by_vehicle'].items()
               for value, reference in zip(values, base['load_by_vehicle'][vid]))


def select_candidate(base, candidates, min_improvement=2):
    """Only screening values may enter this decision; final results are absent."""
    allowed = []
    for candidate in candidates:
        fee_ok = all(candidate['fee_threshold_sensitivity'][key] <= value+1e-6
                     for key, value in base['fee_threshold_sensitivity'].items())
        gain = candidate['metrics']['scenario_full_completions']-base['metrics']['scenario_full_completions']
        if guardrails(base, candidate) and fee_ok and gain >= min_improvement:
            allowed.append(candidate)
    if not allowed:
        return base
    return min(allowed, key=lambda row: (-row['metrics']['scenario_full_completions'],
                                      row['metrics']['cost'], METHODS.index(row['method']), row.get('seed', 0)))


def run(sources, waves=2, evaluations=2500, seconds=2, limit=None):
    started = perf_counter()
    results = []
    configs = configurations()
    if limit is not None:
        configs = configs[:limit]
    for city, source in sources.items():
        for wave in range(waves):
            for index, factors in enumerate(configs):
                data, baseline, source_rows = fixture(source, city, wave, factors)
                groups = [scenarios(data, seed+wave*101, count, factors['pickup_volatility'])
                          for seed, count in ((51000, 9), (61000, 24), (71000, 32))]
                hashes = [{application.fingerprint(scene) for scene in group} for group in groups]
                if any(left & right for i, left in enumerate(hashes) for right in hashes[i+1:]):
                    raise AssertionError('training, screening and final scenarios overlap')
                fit, screen, final = groups
                base = measure(data, baseline, screen)
                base.update(method='rule_baseline', seed=None)
                candidate_screens = []
                for method in METHODS:
                    for seed in ((17, 41) if method.endswith('_alns') else (17,)):
                        tick = perf_counter()
                        solved = recovery_search.solve(data, method=method, seed=seed,
                            joint_scenarios=fit, fixed_fleet_baseline=baseline,
                            max_load_drop=.02, max_evaluations=evaluations, time_limit_seconds=seconds)
                        elapsed = (perf_counter()-tick)*1000
                        row = measure(data, solved['routes'], screen, elapsed)
                        if not guardrails(base, row):
                            raise AssertionError('candidate violated fixed fleet/load/fare guardrails')
                        row.update(method=method, seed=seed, evaluations=solved['evaluations'],
                                   budget_reached=solved['budget_reached'])
                        candidate_screens.append(row)
                chosen = select_candidate(base, candidate_screens)
                # Freeze selection before reading any final metric.
                frozen = (chosen['method'], chosen['seed'], deepcopy(chosen['routes']))
                final_base = measure(data, baseline, final)
                final_rows = []
                for row in candidate_screens:
                    final_row = measure(data, row['routes'], final, row['search_ms'])
                    final_row.update(method=row['method'], seed=row['seed'],
                                     evaluations=row['evaluations'], budget_reached=row['budget_reached'],
                                     screen_completions=row['metrics']['scenario_full_completions'])
                    final_rows.append(final_row)
                selected_final = (final_base if frozen[0] == 'rule_baseline' else
                    next(row for row in final_rows if (row['method'], row['seed']) == frozen[:2]))
                results.append(dict(city=city, wave=wave, configuration=index,
                    factors=factors, input_sha256=application.fingerprint(data), source_rows=source_rows,
                    scenario_sha256=[sorted(items) for items in hashes],
                    baseline=final_base, candidates=final_rows,
                    selection=dict(method=frozen[0], seed=frozen[1],
                        screen_baseline=base['metrics']['scenario_full_completions'],
                        screen_selected=chosen['metrics']['scenario_full_completions'],
                        final_baseline=final_base['metrics']['scenario_full_completions'],
                        final_selected=selected_final['metrics']['scenario_full_completions'],
                        routes=frozen[2]),
                    candidate_search_total_ms=round(sum(r['search_ms'] for r in candidate_screens), 3)))
            print(f'{city} wave {wave}: {len(configs)} configurations completed', flush=True)
    return dict(scope='public spatial structures, fully simulated operations; no enterprise dispatch',
        factors=FACTORS, configuration_count=len(configs), waves=waves,
        budget=dict(max_evaluations=evaluations, time_limit_seconds=seconds,
                    note='evaluation cap is primary; wall cap can change seeded output if reached first'),
        scenario_counts=dict(training=9, screening=24, final=32),
        reconstruction=dict(fixture='algorithm_fit.fixture(source, city, wave, factors)',
            scenario_generator='algorithm_fit.scenarios(data, seed, count, factors["pickup_volatility"])',
            scenario_seed_base=dict(training=51000, screening=61000, final=71000),
            scenario_seed_rule='base + 101 * zero_based_wave',
            geometry_seed_rule='china_city_robustness.fixture: 8137 + zero_based_wave',
            candidate_seeds=dict(local=[17], robust_local=[17], robust_objective_alns=[17, 41]),
            selection_tie_break='completion desc, nominal fee asc, METHODS order, seed asc; never elapsed time'),
        selection_rule='screen gain >=2/24, unchanged active fleet, <=2pp per-vehicle load drop, nominal and +/-10% tariff thresholds no dearer',
        elapsed_seconds=round(perf_counter()-started, 3), results=results)


def summarize(rows):
    output = {}
    for method in ('rule_baseline',)+METHODS+('screened_policy',):
        gains, passes, times, fees, pickups = [], [], [], [], []
        for row in rows:
            base = row['baseline']
            if method == 'rule_baseline':
                measures = [base]
            elif method == 'screened_policy':
                selected = row['selection']
                measures = [base] if selected['method'] == 'rule_baseline' else [
                    next(r for r in row['candidates'] if (r['method'], r['seed']) == (selected['method'], selected['seed']))]
            else:
                measures = [r for r in row['candidates'] if r['method'] == method]
            count = median(r['metrics']['scenario_full_completions'] for r in measures)
            passes.append(count)
            gains.append(count-base['metrics']['scenario_full_completions'])
            times.append(row['candidate_search_total_ms'] if method == 'screened_policy' else median(r['search_ms'] for r in measures))
            fees.append(median(r['metrics']['cost'] for r in measures)-base['metrics']['cost'])
            pickups.append(median(r['independent_replay']['pickup_units_on_completed_routes'] for r in measures))
        output[method] = dict(final_completions=sum(passes), improved_batches=sum(x>0 for x in gains),
            degraded_batches=sum(x<0 for x in gains), unchanged_batches=sum(x==0 for x in gains),
            mean_completion_percent=round(100*sum(passes)/(32*len(rows)), 3),
            mean_gain_per_batch=round(sum(gains)/len(rows), 3),
            search_ms_median=round(median(times), 3), fee_delta_sum=round(sum(fees), 3),
            pickup_units_on_completed_routes=sum(pickups))
    return output


def report_markdown(report):
    rows = report['results']
    names = dict(rule_baseline='规则基线', local='名义局部修复', robust_local='情景局部修复',
                 robust_objective_alns='情景 ALNS（两种子中位数）', screened_policy='独立筛选策略')
    city_names = dict(shenzhen='深圳福田', jilin='吉林', yantai='烟台', wuhan='武汉研究矩阵')
    factor_names = dict(departure_load='出发装载率', pickup_volatility='回收波动',
                        window_slack_minutes='门店余量（分钟）', tariff_mode='计费模式',
                        shift_slack_minutes='班次余量（分钟）')
    summary = summarize(rows)
    lines = ['# 约束组合与算法适配边界', '',
        '本报告只用于模拟条件下的算法选择。公开数据提供空间结构，车辆、货量、回收、时窗、费率和行驶时间均为构造值；不是传化公路港的运营效果。', '',
        '## 实验范围', '',
        f"共 {len(rows)} 个批次，{report['configuration_count']} 种约束组合，每地 {report['waves']} 个波次。每批 8 或 12 店、2 或3辆车。五因素采用两水平全组合：", '',
        '- 出发装载率：逐车重量和体积均为 60% / 92%。低装载用于检验空间余量，不代表推荐企业降低装载率。',
        '- 回收波动：低波动为原预计量的 0.8–1.3 倍；高波动为热点门店 8–30 纸箱、25–85 周转箱，并增加 1–12 分钟作业。',
        '- 门店余量：基线卸货完成后 6 / 45 分钟；班次余量：基线返仓后 8 / 90 分钟。班次余量代表同一车队剩余作业时间，不包含增派车或多趟。',
        '- 计费：超出包干里程的增量计费 / 超阈值后全程计费；阈值设在基线路线距离中位数，另对阈值上下浮动 10% 复核。', '',
        '所有候选锁定原在用车队、逐车重量及体积装载降幅不超过2个百分点、名义全覆盖且准时、名义运费不增。车辆数持平不等于已验证真实运力成本。', '',
        '每批9组训练情景、24组筛选情景、32组最终留出情景，指纹逐批检查无重叠。情景轮换回收及作业时间、仅道路延误、两者叠加、多店同时高压四类；最终集每类8组。使用相同波次种子进行配对比较；同波次不同组合不是独立业务样本，不能据此计算企业总体显著性。', '',
        '每个ALNS有两个随机种子；总计每批4次候选搜索。以评价次数为主预算，同时保留墙钟上限；运行时间仅为本机算法调用时间，基线时间记0表示未测量，不是人工调度耗时。', '',
        '## 最终留出结果', '',
        f"每个方法的整批通过分母为 {len(rows)*32}。通过要求全店按承诺时间完成、逐站容量不超、按班次返仓。ALNS先对每批两个种子取中位数后汇总，因此允许半次计数。", '',
        '| 方法 | 整批通过 | 平均通过率 | 改进/退化/持平批次 | 搜索中位毫秒 | 名义运费差合计 |',
        '|---|---:|---:|---:|---:|---:|']
    for method, item in summary.items():
        lines.append(f"| {names[method]} | {item['final_completions']:g} | {item['mean_completion_percent']}% | {item['improved_batches']}/{item['degraded_batches']}/{item['unchanged_batches']} | {item['search_ms_median']} | {item['fee_delta_sum']} |")
    selected = [r for r in rows if r['selection']['method'] != 'rule_baseline']
    lines += ['', '独立筛选只看筛选集：较基线至少多通过2/24情景，且阈值上下10%时运费均不增，才替换。最终集不参与选择。策略耗时列为生成所有候选的总搜索时间，另有筛选回放开销，不能仅报被选算法的时间。', '',
        f"共替换 {len(selected)}/{len(rows)} 批。最终集退化 {summary['screened_policy']['degraded_batches']} 批；即使筛选通过，也不能保证新情景不退化。", '',
        '## 按地检查', '', '| 地理输入 | 基线通过 | 筛选后通过 | 退化批次 |', '|---|---:|---:|---:|']
    for city in cities.CITIES:
        group = [r for r in rows if r['city'] == city]
        if group:
            item = summarize(group)
            lines.append(f"| {city_names[city]} | {item['rule_baseline']['final_completions']:g}/{len(group)*32} | {item['screened_policy']['final_completions']:g}/{len(group)*32} | {item['screened_policy']['degraded_batches']} |")
    lines += ['', '## 按约束观察', '',
        '下表每行只按一个因素分组，其他因素仍混合；用于观察适配线索，不是单因素因果结论。', '',
        '| 因素 | 水平 | 批次数 | 局部修复通过差 | 情景局部通过差 | ALNS通过差 | 筛选后退化批次 |',
        '|---|---|---:|---:|---:|---:|---:|']
    for factor, values in FACTORS.items():
        for value in values:
            group = [r for r in rows if r['factors'][factor] == value]
            if group:
                stats = summarize(group)
                base = stats['rule_baseline']['final_completions']
                deltas = [stats[m]['final_completions']-base for m in METHODS]
                value_label = {'low': '低', 'high': '高', 'incremental': '增量', 'whole_above': '超阈全程'}.get(value, value)
                lines.append(f"| {factor_names[factor]} | {value_label} | {len(group)} | {deltas[0]:g} | {deltas[1]:g} | {deltas[2]:g} | {stats['screened_policy']['degraded_batches']} |")
    lines += ['', '## 完整组合', '',
        '每行汇总同一组合在四地两波次的最终结果。各方法列为相对基线的通过情景数变化；ALNS为两种子中位数。正值仅表明这一组最终样本更好，不用于反向选择已公布结果。', '',
        '| 编号 | 装载 | 回收 | 门店余量 | 计费 | 班次余量 | 基线通过/情景数 | 局部差 | 情景局部差 | ALNS差 | 策略退化批次 |',
        '|---:|---:|---|---:|---|---:|---:|---:|---:|---:|---:|']
    for index, factors in enumerate(configurations()):
        group = [r for r in rows if r['factors'] == factors]
        if not group:
            continue
        stats = summarize(group)
        base = stats['rule_baseline']['final_completions']
        deltas = [stats[m]['final_completions']-base for m in METHODS]
        lines.append(f"| {index} | {factors['departure_load']:.0%} | {'高' if factors['pickup_volatility']=='high' else '低'} | {factors['window_slack_minutes']}分钟 | {'增量' if factors['tariff_mode']=='incremental' else '超阈全程'} | {factors['shift_slack_minutes']}分钟 | {base:g}/{len(group)*32} | {deltas[0]:g} | {deltas[1]:g} | {deltas[2]:g} | {stats['screened_policy']['degraded_batches']} |")
    candidates = [item for row in rows for item in row['candidates']]
    utilization = [values[0] for row in rows for values in row['baseline']['load_by_vehicle'].values()]
    count_final = len(rows)*32
    lines += ['', '## 运力与回收核对', '',
        f"基线逐车体积装载率范围 {min(utilization):.0%}–{max(utilization):.0%}。全部 {len(candidates)} 个候选通过原车队、全覆盖、逐车2个百分点装载护栏与名义运费上限检查；这不表示全部候选的阈值敏感性检查也通过。", '',
        '| 方法 | 可完整完成车次的预测回收件数合计 |', '|---|---:|']
    for method, item in summary.items():
        lines.append(f"| {names[method]} | {item['pickup_units_on_completed_routes']} |")
    lines += ['',
        f"回收合计覆盖 {count_final} 组批次情景；同一构造波次被重复施加压力，合计不是实际企业处理量，ALNS列为两种子中位数汇总。", '',
        f"实际评价次数范围 {min(r['evaluations'] for r in candidates)}–{max(r['evaluations'] for r in candidates)}；预算耗尽 {sum(r['budget_reached'] for r in candidates)}/{len(candidates)} 次。预算耗尽表示返回当前最好方案，不代表搜索证明最优或无解。"]
    lines += ['', '| 方法 | 逐车体积装载变化范围（百分点） | 逐车重量装载变化范围（百分点） |',
              '|---|---:|---:|']
    for method in METHODS+('screened_policy',):
        changes = [[], []]
        for row in rows:
            if method == 'screened_policy':
                selected = row['selection']
                measures = [row['baseline']] if selected['method'] == 'rule_baseline' else [
                    next(r for r in row['candidates'] if (r['method'], r['seed']) == (selected['method'], selected['seed']))]
            else:
                measures = [r for r in row['candidates'] if r['method'] == method]
            for candidate in measures:
                for vid, utilization_pair in candidate['load_by_vehicle'].items():
                    for dimension, value in enumerate(utilization_pair):
                        changes[dimension].append(100*(value-row['baseline']['load_by_vehicle'][vid][dimension]))
        lines.append(f"| {names[method]} | {min(changes[0]):+.3f} 至 {max(changes[0]):+.3f} | {min(changes[1]):+.3f} 至 {max(changes[1]):+.3f} |")
    lines += ['', '## 使用边界', '',
        '- 默认继续保留原方案。局部修复适合低成本生成备选，是否采用仍要同时看装载、费用和独立情景回放；耗时更长不代表更稳。',
        '- 高回收波动、紧门店时间和紧班次可能同时压缩可行域。即使已有名义可行基线，也不保证任何算法能消除构造压力；失败需要增加业务余量或改变条件，不能只换算法名称。',
        '- 本次并未训练一个跨城市自动选算法的分类器。32种组合的结果是适配证据，尚不足以建立对新企业保证有效的规则。原方案始终可回退，试验不接管主排车默认。',
        '- 回收按重量和总体积核验；未覆盖实际回收物三维摆放、冷链隔离、门店身份、GPS/TMS、真实道路或结算。出发箱体几何另有既有专项测试，不应把本报告的容量通过当作三维通过。',
        '- 不把未完成路线的回收记成已完成。JSON保留“完整完成车次可回收件数”；该指标仍为预测，不是实际回执，失败车次可能完成的部分回收未计入。', '',
        '## 复现与审计', '',
        '```powershell', 'python -X utf8 algorithm_fit.py',
        'python -m unittest discover -s tests -p "test_algorithm_fit.py" -v', '```', '',
        f"本次总耗时 {report['elapsed_seconds']} 秒；每次搜索最多 {report['budget']['max_evaluations']} 次评价、{report['budget']['time_limit_seconds']} 秒。输入与三个情景集合的指纹、空间源行、组合参数、重建种子配方、路线、逐车装载、费用敏感性、筛选与最终结果留在 [algorithm-fit.json](algorithm-fit.json)；JSON未嵌入整份道路矩阵和情景正文，需配合本脚本及空间输入重建。独立算术回放逐候选核对最终整批通过数，不复用搜索风险缓存。", '',
        '重建情景种子：训练51000、筛选61000、最终71000，各加101×从0开始的wave；数量分别9、24、32。候选种子local/robust_local为17，ALNS为17和41。并列按通过数、运费、固定方法顺序、种子选择，不用运行耗时打破并列。', '',
        '后续需要以全新空间样本和真实同批人工计划继续复验；不能反复调整本次筛选阈值后继续把这一组最终情景称为未见数据。', '']
    return '\n'.join(lines)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--waves', type=int, default=2)
    parser.add_argument('--evaluations', type=int, default=2500)
    parser.add_argument('--seconds', type=float, default=2)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--output', type=Path, default=ROOT/'evidence/algorithm-fit.json')
    args = parser.parse_args()
    if not 1 <= args.waves <= 6 or args.limit is not None and not 1 <= args.limit <= 32:
        parser.error('waves must be 1..6 and limit must be 1..32')
    sources = {}
    source_hashes = {}
    for name in ('china-city-spatial-input.json', 'wuhan-city-distance-input.json'):
        raw = (ROOT/'examples'/name).read_bytes()
        source_hashes[name] = sha256(raw).hexdigest()
        sources.update(json.loads(raw))
    report = run(sources, args.waves, args.evaluations, args.seconds, args.limit)
    report['spatial_input_sha256'] = source_hashes
    report['summary'] = summarize(report['results'])
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    args.output.with_suffix('.md').write_text(report_markdown(report), encoding='utf-8')
    print(json.dumps(summarize(report['results']), ensure_ascii=False, indent=2))
