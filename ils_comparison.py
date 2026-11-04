"""Compare PyVRP ILS and native search on the same synthetic dispatch cases."""
import json
from pathlib import Path
from time import perf_counter

import pyvrp_ils
import recovery_benchmark as bench
import recovery_search

BUDGET = 1.0
SEEDS = (17, 29, 41)
METHODS = ('local', 'pyvrp_ils', 'robust_objective_alns')


def run():
    cases = {}
    for name, n, fleet, seed, geometry, other_port in (
        ('geometry', 8, 3, 623, True, False),
        ('medium', 12, 4, 215, False, False),
        ('alternate_port', 12, 4, 712, False, True),
    ):
        data, profile = bench.fixture(n, fleet, seed, geometry=geometry, second_port=other_port)
        training = bench.scenarios(data, seed+2000)
        holdout = bench.scenarios(data, seed+9000, count=24)
        assert {bench.digest(x) for x in training}.isdisjoint(bench.digest(x) for x in holdout)
        fingerprint = bench.digest([data, profile])
        trials = []
        for search_seed in SEEDS:
            for method in METHODS:
                started = perf_counter()
                if method == 'pyvrp_ils':
                    result = pyvrp_ils.solve(data, seed=search_seed, time_limit_seconds=BUDGET)
                else:
                    result = recovery_search.solve(data, method=method, seed=search_seed, profile=profile,
                        scenarios=training, time_limit_seconds=BUDGET, max_evaluations=100000)
                search_ms = (perf_counter()-started)*1000
                routes = result['routes']
                assert bench.digest([data, profile]) == fingerprint
                started = perf_counter()
                scalar_audit = recovery_search.evaluate_plan(data, routes, scenarios=holdout)
                audit = (recovery_search.evaluate_plan(data, routes, scenarios=holdout, profile=profile)
                         if profile and scalar_audit else scalar_audit)
                eligible = bool(audit and result.get('solver_feasible', True) and not audit['metrics']['unserved']
                                and not audit['metrics']['late_orders'])
                metrics = bench.independent_metrics(data, routes, holdout)
                if scalar_audit:
                    assert abs(scalar_audit['metrics']['cost'] - metrics['freight_estimate']) < .02
                trials.append(dict(method=method, seed=search_seed, search_wall_ms=round(search_ms, 3),
                    common_audit_ms=round((perf_counter()-started)*1000, 3), routes=routes,
                    solver_feasible=result.get('solver_feasible'),
                    nominal_route_verified=bool(scalar_audit), common_model_verified=bool(audit),
                    complete_and_on_time=eligible, metrics=metrics,
                    full_scenarios_verified=audit['metrics']['scenario_full_completions'] if eligible else None))
        cases[name] = dict(input_sha256=fingerprint, stores=n, fleet=fleet, has_geometry=geometry,
                           training_scenarios=training, holdout_scenarios=holdout, trials=trials)
    return dict(data_kind='simulated', pyvrp_version=pyvrp_ils.VERSION, search_budget_seconds=BUDGET,
                seeds=SEEDS, cases=cases)


if __name__ == '__main__':
    target = Path(__file__).resolve().parent/'evidence'/'pyvrp-ils-comparison.json'
    target.write_text(json.dumps(run(), indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    print(target)
