from pathlib import Path
from copy import deepcopy
from tempfile import TemporaryDirectory
import unittest

import multiwarehouse as mw
import application
import fleet_planning
import operational_constraints as op
import pilot
import warehouse_loading
from scripts.benchmark_scale_expansion import fixture


class FleetPlanningTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = mw.MultiWarehouseStore(Path(temp.name) / 'task.sqlite3')

    def solve(self, raw):
        task = self.store.create('同仓核对', raw)
        task = self.store.solve(task['id'], task['revision'])
        return task, mw.current_plan(task)

    def test_single_warehouse_large_batch_stays_intact_through_confirmation(self):
        raw = fixture(1, 116, 16)
        for vehicle in raw['vehicles']:
            vehicle.update(max_weight_kg=180, max_volume_m3=1.85)
        with self.assertRaises(ValueError):
            mw.canonicalize(raw)
        raw['warehouses'][0]['planning_mode'] = 'fleet'
        task, plan = self.solve(raw)
        self.assertEqual(plan['audit'], dict(passed=True, warehouses=1, served=116, unserved=0))
        group = plan['warehouses']['WH_1']
        visits = [sid for route in group['optimized']['routes'].values() for sid in route['sequence']]
        self.assertCountEqual(visits, [order['id'] for order in raw['orders']])
        self.assertGreater(plan['summary']['optimized']['trips'], 8)
        self.assertTrue(any(r['fee']['chargeable_km'] > 0 for r in group['optimized']['routes'].values()))
        task = self.store.adopt(task['id'], task['revision'], plan['id'], True)
        self.assertTrue(mw.view(task)['confirmed'])
        self.assertEqual(len(self.store.export_csv(task['id'], task['revision']).splitlines()), 117)

    def test_fee_comparison_is_per_vehicle_and_requires_complete_coverage(self):
        raw = fixture(1, 4, 2)
        raw['warehouses'][0]['planning_mode'] = 'fleet'
        for vehicle in raw['vehicles']:
            vehicle.update(max_weight_kg=50, max_volume_m3=.48)
        a, b, c, d = raw['orders']
        raw['orders'] = [c, a, d, b]
        locations = dict(zip(('WH_1', *(order['id'] for order in (a,b,c,d))), (0, 5, 6, 60, 62)))
        for key, leg in raw['legs'].items():
            left, right = key.split('>')
            distance = abs(locations[left] - locations[right])
            leg.update(distance_km=distance, travel_minutes=distance)
        _, plan = self.solve(raw)
        self.assertEqual(plan['summary']['baseline']['cost'], 428)
        self.assertEqual(plan['summary']['optimized']['cost'], 388)
        self.assertTrue(plan['comparison']['comparable'])
        self.assertEqual(plan['comparison']['delta']['cost'], -40)

        raw['vehicles'][1]['max_weight_kg'] = 1
        task, plan = self.solve(raw)
        self.assertEqual(plan['comparison']['status'], 'coverage_mismatch')
        self.assertIsNone(plan['comparison']['delta'])
        self.assertEqual(plan['audit']['served'] + plan['audit']['unserved'], 4)
        with self.assertRaises(ValueError):
            self.store.adopt(task['id'], task['revision'], plan['id'], True)

    def test_shared_box_baseline_conflict_has_evidence_and_candidate_replays(self):
        raw = mw.operational_sample()
        warehouse = raw['warehouses'][0]
        warehouse['planning_mode'] = 'fleet'
        warehouse['operational_policy']['cold_boxes'][0]['allowed_vehicles'] = ['TRUCK_2']
        task, plan = self.solve(raw)
        self.assertEqual(plan['audit']['unserved'], 0)
        self.assertEqual(plan['comparison']['status'], 'constraint_mismatch')
        self.assertIsNone(plan['comparison']['delta'])
        self.assertTrue(any(reason['code'] == 'cold_vehicle' and reason['plan'] == 'baseline'
                            for reason in plan['comparison']['reasons']))
        group = plan['warehouses'][warehouse['id']]
        witness = group['operational_verification']['routes']['TRUCK_2']
        self.assertTrue(witness['instruction_audit']['passed'])
        self.assertNotIn('frames', witness)
        view = mw.spatial_view(task, warehouse['id'], 'TRUCK_2', plan['id'])
        self.assertEqual(len(view['scene']['routes']), 1)
        self.assertEqual(len(view['scene']['routes'][0]['frames']), witness['frame_count'])
        self.assertTrue(view['scene']['passed'])
        task = self.store.adopt(task['id'], task['revision'], plan['id'], True)
        self.assertTrue(mw.view(task)['confirmed'])

    def test_persistent_cold_shells_and_returns_have_replayable_lanes(self):
        raw=fixture(1,7,1)
        raw['warehouses'][0]['planning_mode']='fleet'
        raw['vehicles'][0].update(max_weight_kg=900,max_volume_m3=18)
        for order in raw['orders']:
            order['lines'][0]['quantity']=9
            order['lines'].append(dict(sku='COLD_UNIT',quantity=2,unit_weight_kg=.5,unit_volume_m3=.001))
        clean=mw.canonicalize(raw)
        part=mw.split(clean)['WH_1']
        policy=op.sample_policy(part)
        original=policy['cold_boxes'][0]
        policy['cold_boxes']=[]
        for index,order in enumerate(part['orders']):
            box=deepcopy(original)
            box.update(id=f'BOX_{index}',height_cm=85,inner_height_cm=70,inner_volume_m3=.07)
            policy['cold_boxes'].append(box)
            for goods in policy['goods']:
                if goods['order']==order['id'] and goods['kind']=='cold':goods['cold_box_id']=box['id']
        raw['warehouses'][0]['operational_policy']=policy
        task,plan=self.solve(raw)
        self.assertEqual(plan['summary']['optimized']['unserved'],0)
        report=plan['warehouses']['WH_1']['operational_verification']['routes']['WH_1_V1']
        self.assertEqual(report['packing_variant'],op.FLEET_LAYOUT['packing_variant'])
        self.assertTrue(report['instruction_audit']['passed'])
        vehicle={**part['vehicles'][0],'warehouse_id':'WH_1'}
        scene=op.build_vehicle_scene(part,plan['warehouses']['WH_1']['optimized'],policy,vehicle,
                                    plan['warehouses']['WH_1']['operational_verification'])
        self.assertTrue(scene['passed'])
        last=scene['routes'][0]['frames'][-1]
        self.assertEqual(sum(box['kind']=='cold_box' for box in last['boxes']),7)
        self.assertEqual(sum(box['kind'].startswith('recovery_') for box in last['boxes']),14)
        self.assertTrue(all(frame['audit']['passed'] for frame in scene['routes'][0]['frames']))

    def test_same_stop_group_can_reverse_to_meet_cold_duration(self):
        data,_=op.sample_data()
        for order in data['orders'][:-1]:
            order['lines']=[line for line in order['lines'] if line['sku']!='COLD_UNIT']
        data=application.canonicalize(data)
        policy=op.sample_policy(data)
        policy['cold_boxes'][0]['valid_minutes']=50
        base=pilot.baseline(data)
        sequence=[order['id'] for order in data['orders']]
        original=pilot.evaluator(data)(sequence,data['vehicles'][0],{order['id']:order for order in data['orders']})
        self.assertFalse(op.evaluate_route(data,original,data['vehicles'][0],policy,**op.FLEET_LAYOUT)['passed'])
        def qualify(route,vehicle):
            report=op.evaluate_route(data,route,vehicle,policy,**op.FLEET_LAYOUT)
            return ({**route,'operational':op.compact_report(report)},None) if report['passed'] else (None,report['errors'][0])
        result=fleet_planning.solve(data,base,[(sequence,[data['vehicles'][0]['id']])],qualify)
        self.assertFalse(result['unserved'])
        report=op.evaluate_plan(data,result,policy,layout=op.FLEET_LAYOUT)
        self.assertTrue(report['passed'])
        self.assertTrue(report['complete'])
        self.assertEqual(result['routes'][data['vehicles'][0]['id']]['cost'],185)

    def test_lane_bound_does_not_assume_equal_weights_from_equal_dimensions(self):
        raw=fixture(1,2,1)
        raw['vehicles'][0].update(max_weight_kg=200,max_volume_m3=.048)
        for order,weight in zip(raw['orders'],[1,100]):
            order['lines'][0].update(quantity=1,unit_weight_kg=weight,unit_volume_m3=.024)
            order['pickup']=dict(cartons=0,totes=0)
        clean=mw.canonicalize(raw)
        part=mw.split(clean)['WH_1']
        policy=op.sample_policy(part)
        for goods,load in zip(policy['goods'],[0,30]):
            goods.update(length_cm=60,width_cm=40,height_cm=10,max_top_load_kg=load)
        policy['vehicle_geometry'][0].update(length_cm=60,width_cm=40,height_cm=20)
        policy=op.validate_policy(part,policy)
        orders={order['id']:order for order in part['orders']}
        vehicle=part['vehicles'][0]
        route=pilot.evaluator(part)(list(orders),vehicle,orders)
        self.assertIsNone(warehouse_loading.operational_screen(part,policy)(route,vehicle))
        report=op.evaluate_route(part,route,vehicle,policy,**op.FLEET_LAYOUT)
        self.assertTrue(report['passed'],report['errors'])
