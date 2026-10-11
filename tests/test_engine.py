"""Independent operational checks: cargo conservation, travel time, completed work, windows."""
import sys
from pathlib import Path
import unittest
from math import hypot
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import engine

class RecoveryTests(unittest.TestCase):
    def audit(self,r):
        orders={o['id']:o for o in r['data']['orders']}; failed=r['parsed']['event']['vehicle']
        for key in ('baseline','recovered'):
            plan=r[key]; delivered=[]
            for vid,route in plan['routes'].items():
                state=r['states'][vid]; completed=state['completed']
                self.assertEqual(route['stops'][:len(completed)],completed)
                if vid==failed:
                    self.assertEqual(route['stops'],completed)
                future=route['stops'][len(completed):]; cargo=set(state['remaining']); load=state['load']
                t=state['available']; p=state['point']
                rescue=[s['id'] for s in future if s['id'] in r['impacted']]
                for s in future:
                    d=hypot(p[0]-s['point'][0],p[1]-s['point'][1]); earliest=t+d/0.5
                    self.assertGreaterEqual(s['arrival']+0.001,earliest)
                    if s['id']=='P':
                        self.assertEqual(s['point'],r['states'][failed]['point'])
                        cargo.update(rescue); load+=sum(orders[o]['quantity'] for o in rescue)
                        self.assertAlmostEqual(s['finish']-s['arrival'],8,places=3)
                    else:
                        self.assertIn(s['id'],cargo); cargo.remove(s['id']); load-=orders[s['id']]['quantity']
                        self.assertLessEqual(s['arrival'],orders[s['id']]['close'])
                        self.assertGreaterEqual(s['arrival'],orders[s['id']]['open'])
                        self.assertAlmostEqual(s['finish']-s['arrival'],5,places=3)
                    self.assertEqual(load,s['load']); self.assertGreaterEqual(load,0); self.assertLessEqual(load,state['capacity'])
                    t=s['finish']; p=s['point']
                if vid!=failed:self.assertFalse(cargo)
                delivered += [s['id'] for s in route['stops'] if s['id']!='P']
            self.assertEqual(len(delivered),len(set(delivered)))
            self.assertEqual(set(delivered),set(orders))
            lateness=[max(0,s['arrival']-orders[s['id']]['due']) for route in plan['routes'].values() for s in route['stops'] if s['id']!='P']
            self.assertEqual(plan['metrics']['late_orders'],sum(x>1e-6 for x in lateness))
            self.assertAlmostEqual(plan['metrics']['late_minutes'],sum(lateness),places=1)
    def test_default_operational_invariants(self):self.audit(engine.run())
    def test_different_truck_and_service_in_progress(self):self.audit(engine.run('V1 在 08:16 故障'))
    def test_failure_after_deliveries(self):
        r=engine.run('V3 在 09:00 故障'); self.audit(r); self.assertEqual(r['impacted'],[])
    def test_clarification_and_bad_input(self):
        for text in ('车坏了','V2 故障','V12 在 08:25 故障','V1 和 V2 在 08:25 故障','V2 在 08:99 故障','V2 在 07:30 故障','插入紧急订单'):
            with self.subTest(text=text):self.assertFalse(engine.parse_message(text)['ready'])
    def test_over_capacity_rejected(self):
        orders={'A':dict(quantity=4,point=[1,0],open=480,due=510,close=690)}
        self.assertIsNone(engine.simulate(['P','A'],[0,0],480,10,12,orders,['A'],[0,0]))
    def test_delivery_before_pickup_rejected(self):
        orders={'A':dict(quantity=2,point=[1,0],open=480,due=510,close=690)}
        self.assertIsNone(engine.simulate(['A','P'],[0,0],480,0,12,orders,['A'],[0,0]))
    def test_hard_window_rejected(self):
        orders={'A':dict(quantity=2,point=[100,0],open=480,due=510,close=500)}
        self.assertIsNone(engine.simulate(['A'],[0,0],480,2,12,orders))

if __name__=='__main__':unittest.main()
