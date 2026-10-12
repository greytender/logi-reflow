"""Small, rerunnable evidence cases. Constructed data, no DB or model calls."""
from copy import deepcopy
import continuous
import fixed_loading
import fixed_loading_benchmark
import loading
import loading_benchmark
import matrix_dispatch


def run(name):
    if name not in ('geometry', 'fixed_route', 'fragile'):
        raise ValueError('请选择箱体尺寸、原摆放改序或易碎堆叠案例')
    if name == 'fixed_route':
        inputs = fixed_loading_benchmark.three()
        original = matrix_dispatch.start(inputs['csv_files'], inputs['loading_profile'])
        for route in original['routes'].values():
            route['fixed_loading'] = deepcopy(original['fixed_loading'])
        free = deepcopy(original); free.pop('fixed_loading')
        for route in free['routes'].values(): route.pop('fixed_loading')
        event = dict(time=480, store='B', open=600, due=620, close=690)
        ignored, _ = continuous.calculate(free, event, 'window', 480)
        checked, _ = continuous.calculate(original, event, 'window', 480)
        probe = fixed_loading.check(original['fixed_loading'], 'V1', ['A', 'B', 'C'], [],
                                    ignored['routes']['V1']['sequence'])
        assert not probe['passed'] and checked['routes']['V1']['fixed_loading_check']['passed']
        f = original['fixed_loading']['manifest']
        return dict(case=name, data_kind='constructed', title='改了配送顺序，原摆放却挡住卸货',
            baseline=dict(metrics=ignored['metrics'], sequence=ignored['routes']['V1']['sequence'],
                          executable=False, reason='路线试算忽略原摆放，不能沿此顺序直线卸货'),
            checked=dict(metrics=checked['metrics'], sequence=checked['routes']['V1']['sequence'],
                         executable=True, reason='核验出发坐标，保留45分钟预测迟到的可执行候选'),
            geometry=dict(vehicle=f['vehicles']['V1'], items=f['items'],
                          placements=f['placements']['V1'], sequence=f['sequences']['V1']),
            checks=probe['errors'], inputs=inputs, event=event,
            verification=dict(rejected_original_layout=probe,
                              accepted_original_layout=checked['routes']['V1']['fixed_loading_check']),
            source_sha256=original['fixed_loading']['source_sha256'],
            limits='三店三箱构造反例；不重新摆货，不代表企业迟到或收益')
    inputs = loading.sample() if name == 'geometry' else loading_benchmark.stack(True)
    result = loading.solve(inputs['csv_files'], inputs['loading_profile'])
    route = next((r for r in result['optimized']['routes'].values() if r['sequence']),None)
    vid = next((v for v,r in result['optimized']['routes'].items() if r['sequence']),None)
    return dict(case=name, data_kind='constructed',
        title='重量与体积够，箱子仍装不进小车' if name=='geometry' else '易碎箱不能靠叠放把订单塞进车里',
        baseline=dict(metrics=result['baseline']['metrics'],
                      sequence=[o for r in result['baseline']['routes'].values() for o in r['sequence']],
                      executable=all(v['status']=='VERIFIED' for v in result['baseline_loading'].values()),
                      reason='仅按重量和总体积排程；基线装载状态见独立检查'),
        checked=dict(metrics=result['optimized']['metrics'],
                     sequence=[o for r in result['optimized']['routes'].values() for o in r['sequence']],
                     executable=result['complete'],
                     reason='改用可容纳箱体的大车，费用增加是约束代价' if name=='geometry' else '保留未安排订单；部分方案低费用不作降本比较'),
        geometry=dict(vehicle=result['vehicles'][vid],items=result['items'],
                      placements=route['loading']['placements'],sequence=route['sequence']) if route else None,
        checks=[f'{vid}: {check["status"]}' for vid,check in result['baseline_loading'].items()],
        verification=dict(independent_route_audit=result['independent_route_audit'],
            loading_audits={v:dict(layout=r['loading']['audit'],instructions=r['loading']['instruction_audit'])
                            for v,r in result['optimized']['routes'].items()}),
        source_sha256=result['input_sha256'],
        unserved=result['optimized']['unserved'], work=result['work'], inputs=inputs,
        limits='有限启发式；缺规则拒绝推测。一般未找到布局不等于已证明无解')
