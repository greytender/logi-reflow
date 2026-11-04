from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import multiwarehouse as mw
import warehouse_loading as wl


class WorkbenchLoadingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = mw.MultiWarehouseStore(Path(self.temp.name)/'tasks.db')

    def tearDown(self):
        self.temp.cleanup()

    def create(self, data=None):
        return self.store.create('几何核验模拟', data or mw.sample(physical_loading=True))

    def solve(self, data=None):
        task = self.create(data)
        return self.store.solve(task['id'], task['revision'])

    def test_explicit_geometry_is_in_main_plan_and_independently_audited(self):
        task = self.solve()
        plan = mw.current_plan(task)
        self.assertEqual(plan['summary']['optimized']['served'], 6)
        self.assertEqual(sum(r['loading_verification']['boxes'] for r in plan['warehouses'].values()), 12)
        for result in plan['warehouses'].values():
            self.assertEqual(result['loading_verification']['status'], 'VERIFIED_WITHIN_MODEL')
            self.assertTrue(result['audit']['physical_loading']['passed'])
            for route in result['optimized']['routes'].values():
                self.assertTrue(route['loading']['audit']['passed'])
                self.assertTrue(route['loading']['instruction_audit']['passed'])
        adopted = self.store.adopt(task['id'], task['revision'], plan['id'], True)
        self.assertTrue(mw.view(adopted)['confirmed'])
        self.assertIn('physical_loading_profile', adopted['execution_tasks']['WH_1'])

    def test_missing_profile_never_claims_geometry(self):
        task = self.solve(mw.sample())
        for result in mw.current_plan(task)['warehouses'].values():
            self.assertEqual(result['loading_verification']['status'], 'NOT_CONFIGURED')
            self.assertIsNone(result['loading_verification']['boxes'])
            self.assertNotIn('physical_loading', result['audit'])

    def test_profile_missing_box_or_bad_volume_is_rejected_without_guessing(self):
        for variant in ('missing', 'volume', 'too_many'):
            data = mw.sample(physical_loading=True)
            if variant == 'missing':
                data['warehouses'][0]['loading_profile']['items'].pop()
            elif variant == 'volume':
                data['warehouses'][0]['loading_profile']['items'][0]['length_cm'] = 61
            else:
                data['orders'][0]['lines'][0]['quantity'] = 81
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                self.create(data)

    def test_physical_counterexample_changes_fleet_without_faking_savings(self):
        data = mw.sample(physical_loading=True)
        # A narrow first vehicle has adequate aggregate volume but cannot take even one 60x40 box upright.
        first = data['warehouses'][0]['loading_profile']['vehicles'][0]
        first.update(length_cm=2000, width_cm=20, height_cm=100)
        task = self.solve(data)
        result = mw.current_plan(task)['warehouses']['WH_1']
        self.assertEqual(result['optimized']['routes'][first['id']]['sequence'], [])
        self.assertFalse(result['optimized']['unserved'])
        self.assertEqual(result['loading_verification']['status'], 'VERIFIED_WITHIN_MODEL')

    def test_tampered_coordinates_cannot_be_confirmed(self):
        task = self.solve()
        plan = mw.current_plan(task)
        route = next(r for r in plan['warehouses']['WH_1']['optimized']['routes'].values() if r['sequence'])
        route['loading']['placements'][0]['x'] = 99999
        task = self.store.commit(task, task['revision'])
        with self.assertRaisesRegex(ValueError, '装载核验失败'):
            self.store.adopt(task['id'], task['revision'], plan['id'], True)

    def test_proven_single_box_geometry_failure_remains_unserved(self):
        data = mw.sample(physical_loading=True)
        for shape in data['warehouses'][0]['loading_profile']['vehicles']:
            shape.update(length_cm=2000, width_cm=20, height_cm=100)
        task = self.solve(data)
        result = mw.current_plan(task)['warehouses']['WH_1']
        self.assertEqual(result['loading_verification']['status'], 'INCOMPLETE')
        self.assertEqual(len(result['optimized']['unserved']), 3)
        self.assertTrue(all(o['code'] == 'PROVEN_GEOMETRY_INFEASIBLE' for o in result['optimized']['unserved']))
        with self.assertRaisesRegex(ValueError, '未安排'):
            self.store.adopt(task['id'], task['revision'], mw.current_plan(task)['id'], True)

    def test_manual_reorder_rechecks_coordinates_and_preserves_history(self):
        task = self.solve()
        parent = mw.current_plan(task)
        sequences = {wid: {vid: list(reversed(route['sequence'])) for vid, route in result['optimized']['routes'].items()}
            for wid, result in parent['warehouses'].items()}
        task = self.store.manual(task['id'], task['revision'], sequences)
        candidate = mw.current_plan(task)
        self.assertEqual(candidate['parent_plan_id'], parent['id'])
        self.assertEqual(len(task['plans']), 2)
        self.assertTrue(mw.audit(task['data'], candidate['warehouses'])['passed'])
        self.assertEqual(candidate['warehouses']['WH_1']['loading_verification']['source'], 'manual_candidate_revalidation')

    def test_in_transit_reorder_does_not_repack_original_boxes(self):
        task = self.solve()
        plan = mw.current_plan(task)
        task = self.store.adopt(task['id'], task['revision'], plan['id'], True)
        child = task['execution_tasks']['WH_1']
        original = child['plans'][0]['result']['optimized']['routes']
        sequences = {vid: list(route['sequence']) for vid, route in original.items()}
        before = deepcopy(child)
        check = wl.remaining_check(child, sequences)
        self.assertTrue(check['passed'])
        self.assertEqual(before, child)
        active = next(vid for vid, route in original.items() if route['sequence'])
        sequences[active] = list(reversed(sequences[active]))
        with self.assertRaisesRegex(ValueError, '原摆放'):
            wl.remaining_check(child, sequences)
        self.assertEqual(before, child)

    def test_failure_diagnostics_distinguish_access_capacity_time_pickup(self):
        part = next(iter(mw.split(mw.sample()).values()))
        o = part['orders'][0]
        reports = wl.diagnose(part, [dict(id=o['id'], reason='未安排')])
        self.assertTrue(any(v['code'] == 'INDIVIDUALLY_FEASIBLE' for v in reports[0]['vehicles']))
        o['pickup'] = dict(cartons=100000, totes=100000)
        reports = wl.diagnose(part, [dict(id=o['id'], reason='未安排')])
        self.assertTrue(any(v['code'] == 'PICKUP_CAPACITY' for v in reports[0]['vehicles']))
        o['allowed_vehicles'] = []
        reports = wl.diagnose(part, [dict(id=o['id'], reason='未安排')])
        self.assertTrue(all(v['code'] == 'VEHICLE_NOT_ALLOWED' for v in reports[0]['vehicles']))


if __name__ == '__main__':
    unittest.main()
