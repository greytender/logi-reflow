import unittest
from copy import deepcopy

import application
import multiwarehouse
import pilot
import recovery_search as search


class RecoverySearchTests(unittest.TestCase):
    def test_scenarios_have_one_fixed_nominal_plan_and_auditable_breaches(self):
        data = application.sample()
        scenes = [
            {order['id']: {'cartons': cartons, 'totes': totes}}
            for order in data['orders']
            for cartons, totes in [(100, 80), (140, 100), (180, 110)]
        ]
        original = deepcopy(data)
        nominal = search.solve(data, method='conflict_alns', scenarios=scenes, max_evaluations=500)
        robust = search.solve(data, method='robust_alns', scenarios=scenes, max_evaluations=500)
        self.assertEqual(data, original)
        self.assertEqual(nominal['metrics']['served'], robust['metrics']['served'])
        self.assertLess(robust['metrics']['scenario_failed_route_stops'],
                        nominal['metrics']['scenario_failed_route_stops'])
        for index, scene in enumerate(scenes):
            perturbed = {o['id']: {**o, 'pickup': scene.get(o['id'], o['pickup'])} for o in data['orders']}
            for vehicle in data['vehicles']:
                vid = vehicle['id']
                route = robust['routes'][vid]
                if route:
                    independently_checked = pilot.evaluator(data)(route, vehicle, perturbed)
                    self.assertEqual(independently_checked is None,
                                     robust['scenario_first_breach_by_vehicle'][vid][index] is not None)
        checked = search.evaluate_plan(data, robust['routes'], scenarios=scenes)
        self.assertEqual(checked['objective'], robust['objective'])
        self.assertTrue(checked['audit']['nominal']['passed'])

    def test_geometry_is_independently_audited_and_search_is_seeded(self):
        data = multiwarehouse.sample(physical_loading=True)
        warehouse = data['warehouses'][0]
        child = multiwarehouse.split(data)[warehouse['id']]
        options = dict(profile=warehouse['loading_profile'], seed=3, max_evaluations=100)
        first = search.solve(child, **options)
        second = search.solve(child, **options)
        self.assertEqual(first['routes'], second['routes'])
        self.assertEqual(first['evaluations'], second['evaluations'])
        self.assertLessEqual(first['evaluations'], 100)
        self.assertTrue(first['audit']['physical_loading']['passed'])
        self.assertEqual(first['audit']['physical_loading']['scope'], 'departure_delivery_cuboids_only')

    def test_rejects_unknown_scenario_counts_and_duplicate_orders(self):
        data = application.sample()
        oid = data['orders'][0]['id']
        with self.assertRaises(ValueError):
            search.solve(data, scenarios=[{oid: {'cartons': True, 'totes': 0}}])
        with self.assertRaises(ValueError):
            search.evaluate_plan(data, {v['id']: [oid] for v in data['vehicles']})

    def test_tight_budget_retains_auditable_partial_plan(self):
        result = search.solve(application.sample(), max_evaluations=1)
        self.assertTrue(result['budget_reached'])
        self.assertTrue(result['audit']['nominal']['passed'])
        self.assertEqual(result['metrics']['served'] + result['metrics']['unserved'], 3)

    def test_fixed_fleet_keeps_full_baseline_under_tight_budget(self):
        data = application.sample()
        baseline = search.solve(data, method='local')['routes']
        result = search.solve(data, method='robust_objective_alns', fixed_fleet_baseline=baseline,
                              max_load_drop=0, max_evaluations=1)
        self.assertTrue(result['budget_reached'])
        self.assertEqual(result['routes'], baseline)
        self.assertEqual(result['metrics']['unserved'], 0)
        self.assertEqual(result['metrics']['late_orders'], 0)
        with self.assertRaisesRegex(ValueError, '覆盖全部门店'):
            search.solve(data, fixed_fleet_baseline={})


if __name__ == '__main__':
    unittest.main()
