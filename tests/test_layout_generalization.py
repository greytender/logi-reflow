from copy import deepcopy
import unittest

import operational_constraints as op
from scripts import validate_layout_generalization as validation


class LayoutGeneralizationTests(unittest.TestCase):
    def test_fixed_design_has_no_outcome_filter_and_is_deterministic(self):
        first=validation.fixtures();second=validation.fixtures()
        self.assertEqual(len(first),18)
        self.assertEqual(validation.digest(first),validation.digest(second))
        self.assertEqual(len({case['id'] for case in first}),18)
        self.assertEqual({tuple(case['profile'][:3]) for case in first},
                         {(40,30,20),(50,35,25),(70,45,35)})

    def test_declared_departure_is_actual_external_volume_and_weight_90_percent(self):
        for case in validation.fixtures():
            vehicle=case['data']['vehicles'][0]
            self.assertAlmostEqual(case['intended_departure_weight_kg']/vehicle['max_weight_kg'],.9)
            self.assertAlmostEqual(case['intended_departure_volume_m3']/vehicle['max_volume_m3'],.9)
            policy=case['policy'];tote=policy['pickup_geometry']['tote']
            self.assertLessEqual(tote['height_cm']+2*tote['nested_height_cm'],case['profile'][2]+1e-8)
            self.assertEqual(policy,op.validate_policy(case['data'],policy))

    def test_fixed_route_does_not_mutate_input_and_success_has_actual_audit(self):
        case=validation.fixtures()[0];before=deepcopy(case)
        row,reports=validation.evaluate_fixed_route(case,'reverse',modes=('legacy',))
        self.assertEqual(case,before)
        self.assertEqual(row['sequence'],list(reversed([o['id'] for o in case['data']['orders']])))
        self.assertEqual(row['results']['legacy']['passed'],reports['legacy']['passed'])
        if reports['legacy']['passed']:
            self.assertTrue(row['results']['legacy']['independent_action_audit']['passed'])
            self.assertAlmostEqual(row['results']['legacy']['volume_utilization'],.9)

    def test_failure_is_not_counted_as_completed_route_savings(self):
        old=dict(passed=False,completed_rehandle_moves=None,placement_checks=10,
                 layout_trials=1,elapsed_ms=1,layout_budget_exhausted=False)
        new={**old,'passed':True,'completed_rehandle_moves':100}
        result=validation.summary([dict(results={'legacy':old,'portfolio':new})],modes=('portfolio',))
        self.assertEqual(result['portfolio']['recovered_routes'],1)
        self.assertEqual(result['portfolio']['both_complete_routes'],0)
        self.assertEqual(result['portfolio']['paired_completed_move_delta_sum'],0)


if __name__=='__main__':unittest.main()
