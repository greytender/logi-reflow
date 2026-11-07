import unittest
from copy import deepcopy
from unittest.mock import patch

import operational_constraints as op
from scripts import diagnose_operational_search as diagnosis


class OperationalSearchDiagnosisTests(unittest.TestCase):
    def test_enumeration_covers_all_orders_once_without_splitting_shared_box(self):
        data, policy = op.sample_data()
        sequences = list(diagnosis.assignment_sequences(data, policy))
        # Three stores sharing one box: 2 possible vehicles x 3! stop orders.
        self.assertEqual(len(sequences), 12)
        for candidate in sequences:
            served = [s for seq in candidate.values() for s in seq]
            self.assertCountEqual(served, [o['id'] for o in data['orders']])
            self.assertEqual(sum(bool(seq) for seq in candidate.values()), 1)

    def test_budget_stop_cannot_be_reported_as_complete_reference(self):
        data, policy = op.sample_data()
        original = deepcopy((data, policy))
        result = diagnosis.exhaustive(data, policy, max_plans=1)
        self.assertFalse(result['enumeration_complete'])
        self.assertTrue(result['budget_exhausted'])
        self.assertEqual(result['checked_plans'], 1)
        self.assertEqual((data, policy), original)

    def test_partial_baseline_and_extra_vehicle_never_pass_guardrails(self):
        row = dict(used_vehicles=['V1'], late_orders=0, cost=100,
                   load_by_vehicle={'V1': dict(weight_utilization=.9, volume_utilization=.9)})
        self.assertFalse(diagnosis.guardrails(None, row))
        extra = deepcopy(row)
        extra['used_vehicles'].append('V2')
        self.assertFalse(diagnosis.guardrails(row, extra))
        lower = deepcopy(row)
        lower['load_by_vehicle']['V1']['volume_utilization'] = .87
        self.assertFalse(diagnosis.guardrails(row, lower))

    def test_reference_witness_passes_recorded_action_replay(self):
        data, policy = op.sample_data()
        result = diagnosis.exhaustive(data, policy)
        self.assertTrue(result['enumeration_complete'])
        self.assertIsNotNone(result['best'])
        for report in result['best_witness']['reports'].values():
            self.assertTrue(op.audit_actions(report)['passed'])
        self.assertIn('not geometric optimum', result['reference_scope'])

    def test_layout_reference_recovers_packing_trap_without_business_savings_claim(self):
        from tests.operational_packing_fixtures import packing_counterexample
        data, policy = packing_counterexample()
        legacy = diagnosis.exhaustive(data, policy)
        portfolio = diagnosis.exhaustive(data, policy, route_mode='portfolio')
        self.assertTrue(legacy['enumeration_complete'])
        self.assertEqual(legacy['feasible_complete_plans'], 0)
        self.assertGreater(portfolio['feasible_complete_plans'], 0)
        self.assertEqual(portfolio['guarded_plans'], 0)  # No complete baseline.
        self.assertEqual(portfolio['best']['rehandle_moves'], 0)
        for report in portfolio['best_witness']['reports'].values():
            self.assertTrue(op.audit_actions(report)['passed'])

    def test_layout_budget_failures_are_counted_even_when_no_witness_exists(self):
        from tests.operational_packing_fixtures import packing_counterexample
        data, policy = packing_counterexample()
        for mode in ('legacy', 'portfolio'):
            with self.subTest(mode=mode), patch.object(op, 'MAX_PLACEMENT_CHECKS', 0):
                result = diagnosis.exhaustive(data, policy, route_mode=mode)
            self.assertTrue(result['enumeration_complete'])
            self.assertFalse(result['budget_exhausted'])  # Plan budget only.
            self.assertEqual(result['unique_route_layout_budget_exhaustions'], 2)
            self.assertEqual(result['unique_route_error_counts']['search_budget_exhausted'], 2)
            self.assertEqual(result['placement_checks_total'], 0)
            self.assertGreaterEqual(result['total_layout_trials'], 2)
            self.assertIsNone(result['best_witness'])

    def test_entity_limits_and_failed_placement_work_are_not_hidden_by_best_witness(self):
        from tests.operational_packing_fixtures import packing_counterexample
        data, policy = packing_counterexample()
        result = diagnosis.exhaustive(data, policy)
        self.assertGreater(result['placement_checks_total'], 0)
        self.assertEqual(result['unique_route_operational_rejections'], 2)
        self.assertEqual(result['unique_route_layout_budget_exhaustions'], 0)
        self.assertIsNone(result['best_witness'])
        with patch.object(op, 'MAX_ENTITIES', 0):
            limited = diagnosis.exhaustive(data, policy, route_mode='portfolio')
        self.assertEqual(limited['unique_route_entity_budget_limits'], 2)
        self.assertEqual(limited['unique_route_error_counts']['entity_budget'], 2)
        self.assertTrue(limited['enumeration_complete'])
        self.assertIsNone(limited['best_witness'])

    def test_failure_trigger_recovers_trap_and_preserves_first_legacy_success(self):
        from tests.operational_packing_fixtures import packing_counterexample
        data, policy = packing_counterexample()
        trap = diagnosis.exhaustive(data, policy, route_mode='fallback')
        self.assertGreater(trap['feasible_complete_plans'], 0)
        self.assertTrue(all(op.audit_actions(r)['passed']
                            for r in trap['best_witness']['reports'].values()))
        data, policy = op.sample_data()
        normal = diagnosis.exhaustive(data, policy, route_mode='fallback')
        self.assertEqual(normal['total_layout_trials'], normal['unique_route_evaluations'])
        for report in normal['best_witness']['reports'].values():
            self.assertEqual(len(report['layout_search']['trials']), 1)
            self.assertEqual(report['layout_search']['trials'][0]['variant'], 'legacy')


if __name__ == '__main__':
    unittest.main()
