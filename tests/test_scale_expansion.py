from copy import deepcopy
import unittest

import application as app
import multiwarehouse as mw
from scripts.benchmark_scale_expansion import fixture,run_scale


class ScaleExpansionTests(unittest.TestCase):
    def test_full_new_input_boundary_is_preserved_in_each_warehouse(self):
        raw=fixture(16,24,8);saved=deepcopy(raw);data=mw.canonicalize(raw);groups=mw.split(data)
        self.assertEqual(len(data['orders']),384);self.assertEqual(len(data['vehicles']),128)
        self.assertEqual(len(groups),16)
        self.assertTrue(all(len(p['orders'])==24 and len(p['vehicles'])==8 for p in groups.values()))
        self.assertEqual(raw,saved)

    def test_per_warehouse_and_batch_overflow_reject_without_truncation(self):
        for shape in ((17,6,2),(1,25,8),(1,24,9)):
            with self.subTest(shape=shape),self.assertRaises(ValueError):mw.canonicalize(fixture(*shape))
        raw=fixture(1,24,8);part=dict(settings=raw['warehouses'][0]['settings'],orders=raw['orders'],vehicles=raw['vehicles'],legs=raw['legs'],business=raw['warehouses'][0]['business'])
        clean=app.canonicalize(part)
        self.assertEqual((len(clean['orders']),len(clean['vehicles'])),(24,8))

    def test_zero_solve_budget_does_not_mislabel_input_validation_as_solve(self):
        result=run_scale((1,6,2),seconds=0)
        self.assertTrue(result['canonicalization']['passed'])
        self.assertFalse(result['basic_all_warehouses_solved']);self.assertFalse(result['basic_all_orders_served'])
        self.assertFalse(result['replay_all_passed']);self.assertEqual(result['basic_planning'],[])


if __name__=='__main__':unittest.main()
