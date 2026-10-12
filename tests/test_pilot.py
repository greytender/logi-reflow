import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from copy import deepcopy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import normal
import pilot
from pilot_benchmark import sample,write_bundle


class PilotTests(unittest.TestCase):
    def load(self,bundle):
        with TemporaryDirectory() as tmp:
            write_bundle(bundle,tmp);return pilot.load_bundle(tmp)

    def test_multiple_skus_and_non_demo_ids(self):
        d=self.load(sample(4));r=pilot.compare(d)
        self.assertEqual(len(d['orders']),4)
        self.assertEqual(sum(len(o['lines']) for o in d['orders']),40)
        self.assertEqual(r['optimized']['metrics']['served'],4)
        self.assertTrue(r['audit']['optimized']['passed'])
        self.assertNotEqual(d['legs']['WH>STORE_001'],d['legs']['STORE_001>WH'])

    def test_no_straight_line_fallback(self):
        b=sample(3);b['travel'].pop()
        with self.assertRaisesRegex(ValueError,'缺方向'):self.load(b)

    def test_duplicate_and_inconsistent_stop_rows(self):
        for kind in ('duplicate','conflict'):
            b=sample(3)
            if kind=='duplicate': b['orders'].append(deepcopy(b['orders'][0]))
            else:b['orders'][1]['service_minutes']=100
            with self.subTest(kind=kind),self.assertRaises(ValueError):self.load(b)

    def test_finish_deadline_and_shift_return(self):
        d=self.load(sample(1));o=d['orders'][0];vid=d['vehicles'][0]['id'];v=d['vehicles'][0]
        o.update(open=480,due=495,close=500,service_minutes=15,allowed_vehicles=[vid])
        d['legs']['WH>'+o['id']]=dict(distance_km=2,travel_minutes=10)
        d['legs'][o['id']+'>WH']=dict(distance_km=3,travel_minutes=20)
        ev=pilot.evaluator(d)
        self.assertIsNone(ev([o['id']],v,{o['id']:o}))
        o['close']=510;v['shift_end']=524
        self.assertIsNone(ev([o['id']],v,{o['id']:o}))
        v['shift_end']=530;r=ev([o['id']],v,{o['id']:o})
        self.assertEqual(r['return_at'],525);self.assertEqual(r['late_minutes'],10)
        self.assertEqual(r['distance'],5)

    def test_restrictions_and_actionable_unserved(self):
        d=self.load(sample(3))
        for o in d['orders']:o['allowed_vehicles']=[d['vehicles'][0]['id']]
        d['vehicles'][0]['max_weight_kg']=1
        r=pilot.compare(d)
        self.assertEqual(r['optimized']['metrics']['served'],0)
        self.assertTrue(all(o['code']=='NO_ELIGIBLE_CAPACITY' and o['action'] for o in r['optimized']['unserved']))

    def test_audit_detects_fabricated_eta_and_cost(self):
        d=self.load(sample(3));r=pilot.compare(d)['optimized']
        for kind in ('eta','cost'):
            bad=deepcopy(r)
            if kind=='cost':bad['metrics']['cost']-=1
            else:next(rr for rr in bad['routes'].values() if rr['stops'])['stops'][0]['arrival']-=1
            with self.subTest(kind=kind),self.assertRaisesRegex(ValueError,'独立复核失败'):pilot.audit(d,bad)

    def test_bad_data_rejected(self):
        for field,value in [('quantity','nan'),('quantity','2.5'),('allowed_vehicles','BAD'),('open','25:00')]:
            b=sample(2);b['orders'][0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):self.load(b)

    def test_old_continuous_boundary_not_silently_expanded(self):
        with self.assertRaises(ValueError):normal.solve(self.load(sample(4)))

    def test_non_metric_shortcut_rejected_without_crashing(self):
        d=self.load(sample(3))
        for o in d['orders']:o.update(open=480,due=850,close=900,allowed_vehicles=['TRUCK_1'])
        for leg in d['legs'].values():leg.update(distance_km=1,travel_minutes=1)
        # The feasible A->B->C journey does not imply A->C is feasible.
        d['legs']['STORE_001>STORE_003']['travel_minutes']=1000
        r=pilot.compare(d)
        self.assertEqual(r['optimized']['metrics']['served'],3)
        self.assertTrue(r['audit']['optimized']['passed'])


if __name__=='__main__':unittest.main()
