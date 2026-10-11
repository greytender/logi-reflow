import sys
from pathlib import Path
import unittest
from math import hypot
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import normal


class NormalTests(unittest.TestCase):
    def audit(self,r):
        orders={o['id']:o for o in r['data']['orders']}; allids=[]; totalcost=0
        for v in r['data']['vehicles']:
            rr=r['routes'][v['id']]; ids=rr['sequence']; allids+=ids
            w=sum(orders[x]['quantity']*orders[x]['unit_weight_kg'] for x in ids)
            vol=sum(orders[x]['quantity']*orders[x]['unit_volume_m3'] for x in ids)
            self.assertLessEqual(w,v['max_weight_kg']+1e-8)
            self.assertLessEqual(vol,v['max_volume_m3']+1e-8)
            self.assertAlmostEqual(rr['weight_kg'],w); self.assertAlmostEqual(rr['volume_m3'],vol)
            t=480.; p=[0,0]; km=0
            for s in rr['stops']:
                o=orders[s['id']]; d=hypot(p[0]-o['point'][0],p[1]-o['point'][1]); km+=d
                t=max(t+d/.5,o['open']); self.assertAlmostEqual(s['arrival'],t,places=3)
                self.assertLessEqual(t,o['close']); t+=5; p=o['point']
            km+=hypot(*p)
            self.assertAlmostEqual(rr['distance'],km,places=3)
            totalcost+=(v['fixed_cost']+km*v['cost_per_km']) if ids else 0
        self.assertEqual(len(allids),len(set(allids)))
        self.assertEqual(set(allids)|{x['id'] for x in r['unserved']},set(orders))
        self.assertFalse(set(allids)&{x['id'] for x in r['unserved']})
        self.assertAlmostEqual(r['metrics']['cost'],totalcost,places=2)
    def test_default(self):
        r=normal.solve(normal.defaults()); self.audit(r); self.assertEqual(r['metrics']['served'],12)
    def test_changed_quantity_not_fixed_group(self):
        d=normal.defaults(); d['orders'][6]['quantity']=40
        r=normal.solve(d); self.audit(r); self.assertEqual(r['metrics']['served'],12)
    def test_impossible_volume_explicit(self):
        d=normal.defaults(); d['orders'][6]['quantity']=100
        r=normal.solve(d); self.audit(r)
        self.assertIn('S07',[x['id'] for x in r['unserved']]); self.assertIn('装下',next(x['reason'] for x in r['unserved'] if x['id']=='S07'))
    def test_hard_window(self):
        d=normal.defaults(); d['orders'][0].update(open=480,due=480,close=480)
        r=normal.solve(d); self.audit(r); self.assertIn('S01',[x['id'] for x in r['unserved']])
    def test_validation(self):
        for q in (0,1.5,float('nan'),True):
            d=normal.defaults(); d['orders'][0]['quantity']=q
            with self.subTest(q=q), self.assertRaises(ValueError): normal.solve(d)
    def test_tiny_capacities_never_overload(self):
        d=normal.defaults()
        for v in d['vehicles']:v['max_weight_kg']=1
        r=normal.solve(d); self.audit(r); self.assertEqual(r['metrics']['unserved'],12)

if __name__=='__main__':unittest.main()
