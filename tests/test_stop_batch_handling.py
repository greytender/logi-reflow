"""Same-stop staging retains physical actions and declared hard limits."""
from copy import deepcopy
from itertools import permutations
import unittest

import application
import operational_constraints as op
import pilot
from scripts.diagnose_high_load_operational import fixtures


def route_for(data, sequence=None):
    vehicle=data['vehicles'][0]
    route=pilot.evaluator(data)(sequence or [o['id'] for o in data['orders']],vehicle,
                               {o['id']:o for o in data['orders']})
    return route,vehicle


class StopBatchHandlingTests(unittest.TestCase):
    def test_default_and_explicit_immediate_are_identical(self):
        data,policy=op.sample_data();route,vehicle=route_for(data)
        self.assertEqual(op.evaluate_route(data,route,vehicle,policy),
                         op.evaluate_route(data,route,vehicle,policy,handling_variant='immediate'))

    def test_four_fixed_high_load_fixtures_keep_routes_and_hard_audits(self):
        expected={'ambient_no_pickup':0,'ambient_pickup':-6,
                  'shared_cold_no_pickup':0,'shared_cold_pickup':None}
        for case in fixtures():
            data,policy=case['data'],case['policy'];original=deepcopy((data,policy))
            for seq in permutations(o['id'] for o in data['orders']):
                with self.subTest(case=case['id'],sequence=seq):
                    route,vehicle=route_for(data,list(seq))
                    before=op.evaluate_route(data,route,vehicle,policy)
                    after=op.evaluate_route(data,route,vehicle,policy,handling_variant='stop_batch')
                    self.assertTrue(before['passed'],before['errors'])
                    self.assertTrue(after['passed'],after['errors'])
                    self.assertTrue(op.audit_actions(after)['passed'])
                    self.assertEqual(after['adjusted_route']['sequence'],before['adjusted_route']['sequence'])
                    for field in ('weight_kg','volume_m3','distance','cost'):
                        self.assertEqual(after['adjusted_route'][field],before['adjusted_route'][field])
                    delta=after['time_adjustments']['rehandle_moves']-before['time_adjustments']['rehandle_moves']
                    if expected[case['id']] is not None:self.assertEqual(delta,expected[case['id']])
                    else:self.assertIn(delta,(-10,-12))
                    self.assertEqual(after['time_adjustments']['high_risk_rehandle_moves'],0)
            self.assertEqual((data,policy),original)

    def test_staged_delivery_target_returns_to_vehicle_before_unload(self):
        case=next(c for c in fixtures() if c['id']=='shared_cold_pickup')
        data,policy=case['data'],case['policy'];route,vehicle=route_for(data)
        report=op.evaluate_route(data,route,vehicle,policy,handling_variant='stop_batch')
        self.assertTrue(report['passed'])
        staged=set();restored=set();delivered_restored=set()
        for frame in report['frames']:
            action=frame.get('action') or {};kind=action.get('kind');ids=set(action.get('box_ids',[]))
            if kind=='rehandle_out':staged.update(ids)
            if kind=='rehandle_in':restored.update(ids&staged)
            if kind=='unload':delivered_restored.update(ids&restored)
            if frame['phase'] in ('departure_stop','return'):
                self.assertFalse(frame['temporary_boxes'])
        self.assertTrue(delivered_restored)
        self.assertTrue(op.audit_actions(report)['passed'])

    def test_accumulated_staging_does_not_bypass_temporary_slots(self):
        case=next(c for c in fixtures() if c['id']=='ambient_pickup')
        data,policy=case['data'],deepcopy(case['policy']);policy['handling']['temporary_slots']=2
        route,vehicle=route_for(data)
        before=op.evaluate_route(data,route,vehicle,policy)
        after=op.evaluate_route(data,route,vehicle,policy,handling_variant='stop_batch')
        self.assertTrue(before['passed'])
        self.assertFalse(after['passed'])
        self.assertIn('temporary_spatial',[e['code'] for e in after['errors']])
        self.assertTrue(all(len(f['temporary_boxes'])<=2 for f in after['frames']))

    def test_active_cold_box_requires_suitable_temporary_temperature(self):
        data,_=op.sample_data();data['orders']=data['orders'][:1]
        keep={data['settings']['depot_id'],data['orders'][0]['id']}
        data['legs']={k:v for k,v in data['legs'].items() if set(k.split('>'))<=keep}
        data['orders'][0]['lines'][0]['quantity']=1
        data['orders'][0]['pickup']={'cartons':0,'totes':0}
        data=application.canonicalize(data);policy=op.sample_policy(data)
        policy['cold_boxes'][0]['requires_cold_zone']=True
        for geom in policy['vehicle_geometry']:
            geom.update(length_cm=120,width_cm=40,height_cm=45,
                cold_zone=dict(x=60,y=0,z=0,length_cm=60,width_cm=40,height_cm=45,temperature_range=[2,8]))
        policy=op.validate_policy(data,policy);route,vehicle=route_for(data)
        for suitable in (False,True):
            candidate=deepcopy(policy)
            if suitable:candidate['handling']['temporary_area']['temperature_range']=[2,8]
            report=op.evaluate_route(data,route,vehicle,candidate,
                packing_variant='cold_last',handling_variant='stop_batch')
            if suitable:
                self.assertTrue(report['passed'],report['errors'])
                self.assertTrue(op.audit_actions(report)['passed'])
            else:
                self.assertFalse(report['passed'])
                self.assertIn('temporary_cold_zone',[e['code'] for e in report['errors']])

    def test_hard_cold_limit_and_invalid_strategy_remain_rejected(self):
        data,policy=op.sample_data();route,vehicle=route_for(data)
        policy['cold_boxes'][0]['valid_minutes']=15
        report=op.evaluate_route(data,route,vehicle,policy,handling_variant='stop_batch')
        self.assertFalse(report['passed'])
        self.assertIn('cold_duration',[e['code'] for e in report['errors']])
        with self.assertRaises(ValueError):op.evaluate_route(data,route,vehicle,policy,handling_variant='unknown')
        with self.assertRaises(ValueError):op.search_route_layouts(data,route,vehicle,policy,handling_variant='unknown')

    def test_layout_search_forwards_and_records_handling_strategy(self):
        case=next(c for c in fixtures() if c['id']=='ambient_pickup')
        data,policy=case['data'],case['policy'];route,vehicle=route_for(data)
        report=op.search_route_layouts(data,route,vehicle,policy,max_variants=1,handling_variant='stop_batch')
        self.assertTrue(report['passed'])
        self.assertEqual(report['handling_variant'],'stop_batch')
        self.assertEqual(report['layout_search']['handling_variant'],'stop_batch')
        self.assertEqual(report['time_adjustments']['rehandle_moves'],12)


if __name__=='__main__':unittest.main()
