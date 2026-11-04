"""Reproduce a coupled road and pickup-service stress case."""
import json

import application
import recovery_search


def run():
    data = application.sample()
    next(order for order in data['orders'] if order['id'] == 'STORE_002')['due'] = 543
    road = dict(road_delay_minutes={'WH>STORE_001': 5})
    pickup = dict(pickup={'STORE_001': {'cartons': 20, 'totes': 5}},
                  extra_service_minutes={'STORE_001': 5})
    scenarios = [road, pickup, {**road, **pickup}]
    rows = {}
    for name, training in (('nominal', []), ('separate', scenarios[:2]), ('joint', scenarios)):
        plan = recovery_search.solve(data, method='robust_objective_alns', joint_scenarios=training)
        replay = recovery_search.evaluate_plan(data, plan['routes'], joint_scenarios=scenarios)
        rows[name] = dict(routes=plan['routes'], metrics=replay['metrics'],
                          first_breach=replay['scenario_first_breach_by_vehicle'])
    return dict(data_kind='simulated', second_store_due_minute=543,
                scenarios=scenarios, comparison=rows)


if __name__ == '__main__':
    print(json.dumps(run(), ensure_ascii=False, indent=2))
