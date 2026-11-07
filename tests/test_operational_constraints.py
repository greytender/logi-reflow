from copy import deepcopy
import unittest
from unittest.mock import patch

import operational_constraints as op
import pilot


class OperationalConstraintTests(unittest.TestCase):
    def setUp(self):
        self.data,self.policy=op.sample_data()
        self.plan=pilot.compare(self.data)['optimized']

    def report(self,data=None,policy=None,plan=None):
        return op.evaluate_plan(data or self.data,plan or self.plan,policy or self.policy)

    def test_absent_policy_is_not_certification(self):
        r=op.evaluate_plan(self.data,self.plan,None)
        self.assertEqual(r['status'],'NOT_CONFIGURED');self.assertIsNone(r['passed'])
        self.assertFalse(any(c['status']=='VERIFIED_WITHIN_MODEL' for c in r['coverage']))

    def test_shared_shell_survives_delivery_and_returns(self):
        before=deepcopy((self.data,self.policy,self.plan));r=self.report()
        self.assertTrue(r['passed'],r['errors']);self.assertTrue(r['complete'])
        used=next(x for x in r['routes'].values() if x['frames'])
        cold=[b for b in used['boxes'] if b['kind']=='cold_box'][0]
        end=[b for b in used['frames'][-1]['boxes'] if b['kind']=='cold_box'][0]
        self.assertEqual((cold['dx'],cold['dy'],cold['dz']),(end['dx'],end['dy'],end['dz']))
        self.assertGreater(cold['weight_kg'],end['weight_kg'])
        self.assertEqual(end['remaining_content_weight_kg'],0)
        self.assertTrue(any(b['kind']=='recovery_tote' for b in used['frames'][-1]['boxes']))
        self.assertTrue(used['instruction_audit']['passed'])
        self.assertEqual(before,(self.data,self.policy,self.plan))

    def test_invalid_cold_parameters_fail_closed(self):
        for mutation in ('missing_box','wrong_temperature','too_small','missing_vehicle'):
            p=deepcopy(self.policy)
            if mutation=='missing_box':p['cold_boxes']=[]
            if mutation=='wrong_temperature':p['cold_boxes'][0]['temperature_range']=[0,10]
            if mutation=='too_small':p['cold_boxes'][0]['inner_volume_m3']=.001
            if mutation=='missing_vehicle':p['vehicle_geometry'].pop()
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):op.validate_policy(self.data,p)

    def test_time_limit_and_opening_limit_reject(self):
        for field,value,code in [('valid_minutes',15,'cold_duration'),('max_openings',1,'cold_openings')]:
            p=deepcopy(self.policy);p['cold_boxes'][0][field]=value
            r=self.report(policy=p);self.assertFalse(r['passed']);self.assertIn(code,[e['code'] for e in r['errors']])

    def test_active_vehicle_zone_distinct_from_passive_box(self):
        p=deepcopy(self.policy);p['cold_boxes'][0]['requires_cold_zone']=True
        self.assertIn('cold_zone',[e['code'] for e in self.report(policy=p)['errors']])
        for v in p['vehicle_geometry']:
            v['cold_zone']=dict(x=0,y=0,z=0,length_cm=120,width_cm=80,height_cm=100,temperature_range=[2,8])
        self.assertTrue(self.report(policy=p)['passed'])
        p['vehicle_geometry'][0]['cold_zone']['temperature_range']=[-10,10]
        self.assertFalse(self.report(policy=p)['passed'])

    def test_energy_preparation_and_incompatible_groups_are_hard_checks(self):
        p=deepcopy(self.policy)
        for e in p['energy']:e.update(available_kwh=12,reserve_kwh=12)
        self.assertIn('energy',[e['code'] for e in self.report(policy=p)['errors']])
        p=deepcopy(self.policy)
        for prep in p['preparation']:prep['load_minutes']=40
        self.assertIn('load_readiness',[e['code'] for e in self.report(policy=p)['errors']])
        p=deepcopy(self.policy)
        for i,g in enumerate(p['goods']):g['compatibility_group']='A' if i==0 else 'B'
        p['incompatible_pairs']=[['A','B']]
        self.assertIn('incompatible_goods',[e['code'] for e in self.report(policy=p)['errors']])

    def test_shared_box_cannot_be_double_assigned(self):
        plan=deepcopy(self.plan);vehicles=self.data['vehicles'];ids=[o['id'] for o in self.data['orders']];orders={o['id']:o for o in self.data['orders']}
        ev=pilot.evaluator(self.data)
        plan['routes'][vehicles[0]['id']]=ev(ids[:1],vehicles[0],orders)
        plan['routes'][vehicles[1]['id']]=ev(ids[1:],vehicles[1],orders)
        r=self.report(plan=plan)
        self.assertIn('shared_box_split',[e['code'] for e in r['errors']])

    def test_handling_time_reaches_window_checks(self):
        r=self.report();used=next(x for x in r['routes'].values() if x['frames'])
        self.assertGreater(used['adjusted_route']['return_at'],self.plan['routes'][used['vehicle_id']]['return_at'])
        self.assertGreater(used['time_adjustments']['extra_minutes'],0)
        d=deepcopy(self.data)
        for o in d['orders']:o['close']=o['due']=520
        r=self.report(data=d)
        self.assertFalse(r['passed']);self.assertIn('hard_window',[e['code'] for e in r['errors']])

    def test_independent_replay_detects_tampered_states(self):
        r=self.report();used=next(x for x in r['routes'].values() if x['frames']);changed=deepcopy(used)
        frame=next(f for f in changed['frames'] if len(f['boxes'])>=2)
        frame['boxes'][1].update(x=frame['boxes'][0]['x'],y=frame['boxes'][0]['y'],z=frame['boxes'][0]['z'])
        self.assertFalse(op.audit_actions(changed)['passed'])

    def test_recovery_geometry_and_goods_returns(self):
        p=deepcopy(self.policy);p['pickup_geometry']['tote']['height_cm']=26
        with self.assertRaises(ValueError):op.validate_policy(self.data,p)
        p=deepcopy(self.policy);p['returns']=[dict(id='RET_1',order=self.data['orders'][0]['id'],quantity=1,length_cm=20,width_cm=20,height_cm=20,unit_weight_kg=3,kind='ambient',fragile=True,max_top_load_kg=0,sealed=True)]
        r=self.report(policy=p);self.assertTrue(r['passed'],r['errors'])
        self.assertTrue(any(b['kind']=='goods_return' for route in r['routes'].values() for frame in route['frames'] if frame['phase']=='return' for b in frame['boxes']))
        p['returns'][0]['kind']='cold'
        with self.assertRaises(ValueError):op.validate_policy(self.data,p)

    def test_scene_and_unserved_scope(self):
        scene=op.build_scene(self.data,self.plan,self.policy)
        self.assertEqual(scene['schema'],op.SCENE_SCHEMA);self.assertTrue(scene['routes']);self.assertTrue(scene['assumptions'])
        p=deepcopy(self.plan);p['unserved']=[dict(id='not-arranged')]
        r=self.report(plan=p);self.assertTrue(r['passed']);self.assertFalse(r['complete']);self.assertTrue(r['warnings'])

    def test_temporary_space_and_large_entity_budget_are_explicit(self):
        p=deepcopy(self.policy)
        p['handling']['temporary_area']=dict(length_cm=1,width_cm=1,height_cm=1)
        r=self.report(policy=p)
        self.assertFalse(r['passed']);self.assertIn('temporary_spatial',[e['code'] for e in r['errors']])
        d=deepcopy(self.data);d['orders'][0]['lines'][0]['quantity']=200
        p=deepcopy(self.policy)
        r=self.report(data=d,policy=p)
        self.assertFalse(r['passed']);self.assertIn('entity_budget',[e['code'] for e in r['errors']])

    def test_cold_box_activation_and_inner_dimensions_are_not_guessed(self):
        p=deepcopy(self.policy);del p['cold_boxes'][0]['activated_at_minutes']
        with self.assertRaises(ValueError):op.validate_policy(self.data,p)
        p=deepcopy(self.policy);p['cold_boxes'][0]['inner_width_cm']=5;p['cold_boxes'][0]['inner_volume_m3']=.008
        with self.assertRaisesRegex(ValueError,'商品不能'):op.validate_policy(self.data,p)

    def test_independent_replay_rejects_path_through_another_box(self):
        vehicle=dict(length_cm=100,width_cm=10,height_cm=10,max_weight_kg=100,max_volume_m3=.1)
        def box(id,x):
            return dict(id=id,order='STORE',kind='regular',x=x,y=0,z=0,dx=10,dy=10,dz=10,
                        weight_kg=1,fragile=False,max_top_load_kg=10)
        front,rear=box('front',0),box('rear',20)
        # Both endpoints fit, but the second box cannot enter from +x through
        # the already loaded rear box.
        report=dict(vehicle=vehicle,frames=[
            dict(time_minutes=0,boxes=[rear],temporary_boxes=[],action=dict(kind='load',box_ids=['rear'])),
            dict(time_minutes=1,boxes=[rear,front],temporary_boxes=[],action=dict(kind='load',box_ids=['front']))])
        replay=op.audit_actions(report)
        self.assertFalse(replay['passed']);self.assertIn('blocked_action_path',replay['errors'])
        self.assertGreater(op._place(front,[rear],vehicle,require_door_access=True)['x'],rear['x'])

    def test_independent_audit_rejects_negative_weight_and_changed_temporary_entity(self):
        report=next(r for r in self.report()['routes'].values() if r['frames'])
        invalid=deepcopy(report['boxes']);invalid[0]['weight_kg']=-1
        self.assertIn('negative_weight',op.audit_state(invalid,report['vehicle'])['errors'])
        changed=deepcopy(report)
        frame=next(f for f in changed['frames'] if (f.get('action') or {}).get('kind')=='rehandle_out')
        frame['temporary_boxes'][-1]['weight_kg']+=1
        self.assertIn('temporary_entity_changed',op.audit_actions(changed)['errors'])

    def test_independent_replay_rejects_changed_cold_content(self):
        report=next(r for r in self.report()['routes'].values() if r['frames'])
        changed=deepcopy(report)
        frame=next(f for f in changed['frames'] if (f.get('action') or {}).get('kind')=='cold_open')
        cold=next(b for b in frame['boxes'] if b['kind']=='cold_box')
        cold['remaining_content_weight_kg']+=1
        self.assertIn('wrong_cold_remaining_weight',op.audit_actions(changed)['errors'])

    def test_entity_limits_reject_before_expanding_or_placing(self):
        data=deepcopy(self.data)
        data['orders'][0]['lines'][0]['quantity']=10_000_000
        vid=next(v for v in data['vehicles'] if self.plan['routes'][v['id']]['sequence'])
        with patch.object(op,'_entity',side_effect=AssertionError('must not expand')):
            result=op.evaluate_route(data,self.plan['routes'][vid['id']],vid,self.policy)
        self.assertFalse(result['passed']);self.assertEqual(result['errors'][0]['code'],'entity_budget')
        self.assertFalse(result['frames'])
        data=deepcopy(self.data)
        cold=next(line for line in data['orders'][0]['lines'] if line['sku']=='COLD_UNIT')
        cold['quantity']=10_000_000
        with patch.object(op,'_entity',side_effect=AssertionError('must not expand')):
            with self.assertRaisesRegex(ValueError,'未展开商品'):op.validate_policy(data,self.policy)

    def test_placement_and_frame_budgets_stop_with_controlled_unknown(self):
        vehicle=next(v for v in self.data['vehicles'] if self.plan['routes'][v['id']]['sequence'])
        route=self.plan['routes'][vehicle['id']]
        for constant in ('MAX_PLACEMENT_CHECKS','MAX_FRAMES'):
            with self.subTest(constant=constant),patch.object(op,constant,0):
                result=op.evaluate_route(self.data,route,vehicle,self.policy)
                self.assertFalse(result['passed'])
                self.assertEqual(result['status'],'SEARCH_BUDGET_EXHAUSTED')
                self.assertEqual(result['errors'][0]['code'],'search_budget_exhausted')
                self.assertFalse(result['frames'])

    def test_active_cold_zone_excludes_ambient_goods_and_recovery(self):
        p=deepcopy(self.policy);p['cold_boxes'][0]['requires_cold_zone']=True
        for v in p['vehicle_geometry']:
            v['cold_zone']=dict(x=0,y=0,z=0,length_cm=120,width_cm=80,height_cm=100,temperature_range=[2,8])
        result=self.report(policy=p);self.assertTrue(result['passed'],result['errors'])
        report=next(r for r in result['routes'].values() if r['frames'])
        for frame in report['frames']:
            for box in frame['boxes']:
                if box['kind']!='cold_box':
                    self.assertFalse(box['x']<120 and box['y']<80 and box['z']<100)
        changed=deepcopy(report)
        departure=next(f for f in changed['frames'] if f['phase']=='departure')
        regular=next(b for b in departure['boxes'] if b['kind']=='regular');regular.update(x=0,y=0,z=0)
        self.assertIn('ambient_in_cold_zone',op.audit_actions(changed)['errors'])

    def test_cold_open_instructions_preserve_before_and_after_payload(self):
        report=next(r for r in self.report()['routes'].values() if r['frames'])
        action=next(a for a in report['actions'] if a['kind']=='cold_open')
        self.assertGreater(action['from']['remaining_content_weight_kg'],action['to']['remaining_content_weight_kg'])
        changed=deepcopy(report)
        frame=next(f for f in changed['frames'] if (f.get('action') or {}).get('kind')=='cold_open')
        frame['action']['from']['remaining_content_weight_kg']=frame['action']['to']['remaining_content_weight_kg']
        self.assertIn('instruction_snapshot_mismatch',op.audit_actions(changed)['errors'])

    def test_goods_returns_use_pickup_operation_time(self):
        p=deepcopy(self.policy)
        # No geometry impact from these tiny sealed returns, so the only time
        # difference is three explicitly charged pickup operations.
        p['returns']=[dict(id='RET',order=self.data['orders'][0]['id'],quantity=3,
            length_cm=1,width_cm=1,height_cm=1,unit_weight_kg=.1,kind='ambient',fragile=True,max_top_load_kg=0,sealed=True)]
        before=next(r for r in self.report()['routes'].values() if r['frames'])
        after=next(r for r in self.report(policy=p)['routes'].values() if r['frames'])
        self.assertTrue(after['passed'],after['errors'])
        self.assertAlmostEqual(after['time_adjustments']['extra_minutes']-before['time_adjustments']['extra_minutes'],
            3*p['handling']['pickup_minutes_per_unit'],places=5)
        self.assertEqual(sum(a['high_risk'] for a in after['actions'] if a['kind'] in ('rehandle_in','rehandle_out')),
            after['time_adjustments']['high_risk_rehandle_moves'])

    def test_energy_per_km_cannot_be_zero(self):
        p=deepcopy(self.policy);p['energy'][0]['kwh_per_km']=0
        with self.assertRaises(ValueError):op.validate_policy(self.data,p)


if __name__=='__main__':unittest.main()
