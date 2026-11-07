"""Business boundary tests: arithmetic, route feasibility and persistence."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import application
import business
import pilot
from store import Conflict


class BusinessTests(unittest.TestCase):
    def setUp(self):
        self.data = application.sample()

    def one_stop(self, km):
        data = deepcopy(self.data)
        data['orders'] = data['orders'][:1]
        data['vehicles'] = data['vehicles'][1:]
        data['orders'][0]['allowed_vehicles'] = ['TRUCK_2']
        data['legs'] = {'WH>STORE_001':dict(distance_km=km/2,travel_minutes=30),
                        'STORE_001>WH':dict(distance_km=km/2,travel_minutes=30)}
        return application.canonicalize(data)

    def test_tariff_boundaries_and_no_duplicate_pickup_fee(self):
        for mode, km, expected in [('incremental',99,155),('incremental',100,155),
                                    ('incremental',101,157),('whole_above',100,155),
                                    ('whole_above',101,357)]:
            data=self.one_stop(km); data['business']['tariff']['mode']=mode
            with self.subTest(mode=mode, km=km):
                result=pilot.compare(data)
                route=result['optimized']['routes']['TRUCK_2']
                self.assertEqual(route['cost'], expected)
                charged = 0 if km <= 100 else (km-100 if mode == 'incremental' else km)
                self.assertEqual(route['fee']['chargeable_km'], charged)
                self.assertEqual(route['fee']['included_km'], 100)
                self.assertEqual(route['fee']['stop_count'], 1)
                self.assertEqual(route['fee']['pickup_extra'], 0)
                self.assertIsNone(route['fee']['tms_billing_km'])
                self.assertIsNone(route['fee']['actual_gps_km'])
                self.assertTrue(result['audit']['optimized']['passed'])

    def test_empty_vehicle_never_charged(self):
        result=pilot.compare(self.data)
        empty=[r for r in result['optimized']['routes'].values() if not r['sequence']]
        self.assertEqual(len(empty), 1)
        self.assertEqual(empty[0]['cost'],0)
        self.assertEqual(empty[0]['fee']['stops'],0)

    def test_linear_compatibility_and_compaction_formula(self):
        self.data['business']['tariff']['mode']='linear'
        result=pilot.compare(self.data)
        self.assertEqual(result['optimized']['metrics']['cost'],100)
        for route in result['optimized']['routes'].values():
            for stop in route['stops']:
                self.assertAlmostEqual(stop['pickup_volume_m3'], .144)  # 20*.003+.06+2*.012
        bare=deepcopy(self.data); del bare['business']
        for order in bare['orders']: del order['pickup']
        bare=application.canonicalize(bare)
        self.assertNotIn('business',bare)
        self.assertEqual(pilot.compare(bare)['optimized']['metrics']['cost'],100)

    def test_pickup_increases_vehicle_count_without_omitting_stores(self):
        original=pilot.compare(self.data)
        for order in self.data['orders']: order['pickup']['cartons']=300
        updated=pilot.compare(self.data)
        routes=updated['optimized']['routes']
        self.assertEqual(original['optimized']['metrics']['served'],3)
        self.assertEqual(sum(bool(r['sequence']) for r in routes.values()),2)
        self.assertEqual(updated['optimized']['metrics']['served'],3)
        self.assertGreater(updated['optimized']['metrics']['cost'],original['optimized']['metrics']['cost'])
        self.assertTrue(updated['audit']['optimized']['passed'])

    def test_reordering_needed_to_avoid_intermediate_overload(self):
        data=deepcopy(self.data)
        data['orders']=data['orders'][:2]; data['vehicles']=data['vehicles'][1:]
        for order in data['orders']:
            order['allowed_vehicles']=['TRUCK_2']; order['pickup']=dict(cartons=0,totes=0)
        data['orders'][0]['pickup']['cartons']=600  # collect 240kg, still carrying 126kg if first
        nodes={'WH',*[o['id'] for o in data['orders']]}
        data['legs']={k:v for k,v in data['legs'].items() if set(k.split('>'))<=nodes}
        data=application.canonicalize(data)
        orders={o['id']:o for o in data['orders']}; vehicle=data['vehicles'][0]
        self.assertIsNone(pilot.evaluator(data)(['STORE_001','STORE_002'],vehicle,orders))
        result=pilot.compare(data)
        self.assertEqual(result['optimized']['routes']['TRUCK_2']['sequence'],['STORE_002','STORE_001'])
        self.assertEqual(result['optimized']['metrics']['unserved'],0)

    def test_pickup_alone_impossible_retains_exception(self):
        data=self.one_stop(25); data['orders'][0]['pickup']['cartons']=1000
        result=pilot.compare(data)
        self.assertEqual(result['optimized']['metrics']['unserved'],1)
        self.assertIn('回收',result['optimized']['unserved'][0]['reason'])
        self.assertTrue(result['audit']['optimized']['passed'])

    def test_independent_audit_rejects_tampered_fees_loads_and_counts(self):
        result=pilot.compare(self.data)['optimized']
        vid=next(k for k,r in result['routes'].items() if r['sequence'])
        for kind in ('fee','load','count','gps','nan'):
            changed=deepcopy(result); route=changed['routes'][vid]
            if kind=='fee': route['fee']['distance']+=1
            elif kind=='load': route['stops'][0]['onboard_volume_m3']+=.1
            elif kind=='count': route['stops'][0]['pickup']['cartons']+=1
            elif kind=='gps': route['fee']['actual_gps_km']=route['distance']
            else: route['peak_volume_m3']=float('nan')
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError,'独立复核失败'):
                pilot.audit(self.data,changed)

    def test_missing_or_invalid_pickup_is_not_assumed_zero(self):
        for value in (None,dict(cartons=-1,totes=0),dict(cartons=.5,totes=0),dict(cartons=True,totes=0)):
            data=deepcopy(self.data); data['orders'][0]['pickup']=value
            with self.subTest(value=value), self.assertRaises(ValueError): application.canonicalize(data)
        data=deepcopy(self.data); data['business']['packaging']['tote_nested_m3']=.1
        with self.assertRaises(ValueError): application.canonicalize(data)

    def test_rule_changes_invalidate_confirmation_and_export_persist_rules(self):
        with TemporaryDirectory() as directory:
            store=application.ApplicationStore(Path(directory)/'tasks.sqlite3')
            task=store.create('回收任务',self.data); task=store.solve(task['id'],task['revision'])
            task=store.adopt(task['id'],task['revision'],task['plans'][-1]['id'],True)
            csv=store.export_csv(task['id'],task['revision'])
            self.assertIn('预计纸箱数',csv); self.assertIn('incremental',csv)
            changed=deepcopy(task['data']); changed['business']['tariff']['per_stop']=16
            task=store.save(task['id'],task['revision'],task['name'],changed)
            with self.assertRaises(Conflict): store.export_csv(task['id'],task['revision'])
            task=store.solve(task['id'],task['revision'])
            self.assertEqual(task['plans'][-1]['result']['optimized']['metrics']['cost'],188)
            restored=application.ApplicationStore(Path(directory)/'tasks.sqlite3').read(task['id'])
            self.assertEqual(restored['data']['business'],changed['business'])
            self.assertEqual(len(restored['plans']),2)


if __name__=='__main__': unittest.main()
