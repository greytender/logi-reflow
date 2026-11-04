"""Optional PyVRP ILS route generator. Validate candidates with recovery_search."""
from importlib.metadata import version
from math import ceil, floor, isfinite
from time import perf_counter

import business
import pilot

VERSION = '0.13.1'
TIME_SCALE = 100
DISTANCE_SCALE = 1000
LOAD_SCALES = (1000, 1000000)


def up(value, scale):
    return ceil(value*scale-1e-8)


def down(value, scale):
    return floor(value*scale+1e-8)


def solve(data, *, time_limit_seconds=1, seed=0):
    """Solve nominal simultaneous delivery/pickup with a linear cost proxy."""
    import pyvrp
    from pyvrp.stop import MaxRuntime

    if version('pyvrp') != VERSION:
        raise ValueError(f'离线对照要求 pyvrp=={VERSION}')
    if not isfinite(time_limit_seconds) or not 0 < time_limit_seconds <= 60:
        raise ValueError('PyVRP 时间预算需大于0且不超过60秒')
    started = perf_counter()
    pilot.validate(data)
    if 'business' not in data:
        raise ValueError('PyVRP 回收对照需要已确认的包装和费率参数')
    orders = data['orders']
    vehicles = data['vehicles']
    routes = {v['id']: [] for v in vehicles}
    model = pyvrp.Model()
    depot = data['settings']['depot_id']
    nodes = {depot: model.add_depot(0, 0, name=depot)}
    result_base = dict(method='pyvrp_ils', version=VERSION)
    for o in orders:
        oid = o['id']
        duration = up(o['service_minutes'], TIME_SCALE)
        latest = down(o['due'], TIME_SCALE)
        if data['settings']['window_rule'] == 'finish':
            latest -= duration
        earliest = up(o['open'], TIME_SCALE)
        if latest < earliest:
            return dict(result_base, routes=routes, solver_feasible=False,
                elapsed_ms=(perf_counter()-started)*1000, reason='门店名义承诺时间窗与服务时长冲突')
        delivery = (o['quantity']*o['unit_weight_kg'], o['quantity']*o['unit_volume_m3'])
        pickup = business.pickup_totals(o, data['business']['packaging'])
        nodes[oid] = model.add_client(0, 0, name=oid, service_duration=duration,
            tw_early=earliest, tw_late=latest,
            delivery=[up(v, s) for v, s in zip(delivery, LOAD_SCALES)],
            pickup=[up(v, s) for v, s in zip(pickup, LOAD_SCALES)])
    tariff = data['business']['tariff']
    for vehicle in vehicles:
        vid = vehicle['id']
        profile = model.add_profile(name=vid)
        fixed = vehicle['fixed_cost'] if tariff['mode'] == 'linear' else tariff['base']
        rate = vehicle['cost_per_km'] if tariff['mode'] == 'linear' else tariff['per_km']
        model.add_vehicle_type(name=vid, profile=profile,
            capacity=[down(vehicle[k], s) for k, s in zip(('max_weight_kg', 'max_volume_m3'), LOAD_SCALES)],
            tw_early=up(vehicle['shift_start'], TIME_SCALE), tw_late=down(vehicle['shift_end'], TIME_SCALE),
            fixed_cost=round(fixed*DISTANCE_SCALE*100), unit_distance_cost=round(rate*100))
        allowed = [depot, *(o['id'] for o in orders if vid in o['allowed_vehicles'])]
        for a in allowed:
            for b in allowed:
                if a == b:
                    continue
                leg = data['legs'][a+'>'+b]
                model.add_edge(nodes[a], nodes[b], distance=round(leg['distance_km']*DISTANCE_SCALE),
                    duration=up(leg['travel_minutes'], TIME_SCALE), profile=profile)
    remaining = time_limit_seconds-(perf_counter()-started)
    if remaining <= 0:
        return dict(result_base, routes=routes, solver_feasible=False,
            elapsed_ms=(perf_counter()-started)*1000, reason='模型构建耗尽时间预算')
    solved = model.solve(MaxRuntime(remaining), seed=seed, collect_stats=False, display=False)
    ids = list(nodes)
    for route in solved.best.routes():
        routes[vehicles[route.vehicle_type()]['id']] = [ids[i] for i in route.visits()]
    return dict(result_base, routes=routes, solver_feasible=solved.is_feasible(),
                elapsed_ms=(perf_counter()-started)*1000)
