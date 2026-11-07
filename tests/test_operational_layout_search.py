"""Behavioral checks for the opt-in bounded layout portfolio."""
from copy import deepcopy
import unittest
from unittest.mock import patch

import operational_constraints as op
import pilot
from tests.operational_packing_fixtures import packing_counterexample


def route_for(data):
    vehicle=data['vehicles'][0]
    route=pilot.evaluator(data)([o['id'] for o in data['orders']],vehicle,
        {o['id']:o for o in data['orders']})
    return route,vehicle


class OperationalLayoutSearchTests(unittest.TestCase):
    def test_search_recovers_load_order_trap_without_changing_rules(self):
        data,policy=packing_counterexample()
        route,vehicle=route_for(data)
        snapshot=deepcopy((data,policy,route,vehicle))
        legacy=op.evaluate_route(data,route,vehicle,policy)
        self.assertFalse(legacy['passed'])
        self.assertIn('packing_not_found',[e['code'] for e in legacy['errors']])
        result=op.search_route_layouts(data,route,vehicle,policy)
        self.assertTrue(result['passed'],result['errors'])
        self.assertTrue(op.audit_actions(result)['passed'])
        self.assertEqual(result['time_adjustments']['rehandle_moves'],0)
        self.assertEqual([a['box_ids'][0].split('/')[1] for a in result['actions'] if a['kind']=='load'],['A','C','B'])
        self.assertEqual(result['adjusted_route']['sequence'],route['sequence'])
        self.assertEqual(snapshot,(data,policy,route,vehicle))
        # No hidden rotation or fictitious capacity increase.
        sizes={g['sku']:(g['length_cm'],g['width_cm'],g['height_cm']) for g in policy['goods']}
        for box in result['boxes']:
            self.assertEqual((box['dx'],box['dy'],box['dz']),sizes[box['sku']])

    def test_single_variant_preserves_legacy_actions_and_states(self):
        data,policy=op.sample_data();route,vehicle=route_for(data)
        legacy=op.evaluate_route(data,route,vehicle,policy)
        result=op.search_route_layouts(data,route,vehicle,policy,max_variants=1)
        self.assertTrue(result['passed'])
        self.assertEqual(result['frames'],legacy['frames'])
        self.assertEqual(result['actions'],legacy['actions'])
        self.assertEqual(result['adjusted_route'],legacy['adjusted_route'])

    def test_failure_only_search_skips_extra_layouts_after_success(self):
        for fixture,expected_trials in ((op.sample_data,1),(packing_counterexample,2)):
            data,policy=fixture();route,vehicle=route_for(data)
            result=op.search_route_layouts(data,route,vehicle,policy,stop_on_first_feasible=True)
            self.assertTrue(result['passed'])
            self.assertEqual(len(result['layout_search']['trials']),expected_trials)
            self.assertTrue(op.audit_actions(result)['passed'])

    def test_exhaustion_preserves_previously_verified_layout(self):
        data,policy=op.sample_data();route,vehicle=route_for(data)
        legacy=op.evaluate_route(data,route,vehicle,policy)
        cap=legacy['placement_checks']+1
        with patch.object(op,'MAX_PLACEMENT_CHECKS',cap):
            result=op.search_route_layouts(data,route,vehicle,policy)
        self.assertTrue(result['passed'])
        self.assertTrue(result['layout_search']['budget_exhausted'])
        self.assertEqual(result['layout_search']['placement_checks'],cap)
        self.assertEqual(result['frames'],legacy['frames'])
        self.assertTrue(op.audit_actions(result)['passed'])

    def test_zero_budget_is_unknown_and_does_not_claim_feasibility(self):
        data,policy=packing_counterexample();route,vehicle=route_for(data)
        with patch.object(op,'MAX_PLACEMENT_CHECKS',0):
            result=op.search_route_layouts(data,route,vehicle,policy)
        self.assertFalse(result['passed'])
        self.assertEqual(result['status'],'SEARCH_BUDGET_EXHAUSTED')
        self.assertEqual(result['layout_search']['placement_checks'],0)

    def test_cold_limit_cannot_be_learned_or_searched_away(self):
        data,policy=op.sample_data();route,vehicle=route_for(data)
        policy['cold_boxes'][0]['valid_minutes']=15
        result=op.search_route_layouts(data,route,vehicle,policy)
        self.assertFalse(result['passed'])
        self.assertIn('cold_duration',[e['code'] for e in result['errors']])
        self.assertFalse(any(t['passed'] for t in result['layout_search']['trials']))

    def test_later_budget_exhaustion_is_not_hidden_by_first_rejection(self):
        data,policy=packing_counterexample();route,vehicle=route_for(data)
        first=op.evaluate_route(data,route,vehicle,policy)
        self.assertFalse(first['passed'])
        with patch.object(op,'MAX_PLACEMENT_CHECKS',first['placement_checks']+1):
            result=op.search_route_layouts(data,route,vehicle,policy)
        self.assertFalse(result['passed'])
        self.assertEqual(result['status'],'SEARCH_BUDGET_EXHAUSTED')
        self.assertTrue(result['layout_search']['budget_exhausted'])
        self.assertIn('packing_not_found',[e['code'] for e in result['errors']])
        self.assertIn('search_budget_exhausted',[e['code'] for e in result['errors']])

    def test_deterministic_layout_ignoring_runtime_and_invalid_options(self):
        data,policy=packing_counterexample();route,vehicle=route_for(data)
        first=op.search_route_layouts(data,route,vehicle,policy)
        second=op.search_route_layouts(data,route,vehicle,policy)
        first['layout_search'].pop('elapsed_ms');second['layout_search'].pop('elapsed_ms')
        self.assertEqual(first,second)
        for value in (0,7,True,2.5):
            with self.subTest(value=value),self.assertRaises(ValueError):
                op.search_route_layouts(data,route,vehicle,policy,max_variants=value)
        with self.assertRaises(ValueError):op.search_route_layouts(data,route,vehicle,None)
        with self.assertRaises(ValueError):op.evaluate_route(data,route,vehicle,policy,packing_variant='unknown')


if __name__=='__main__':unittest.main()
