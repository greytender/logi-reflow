import sys
from pathlib import Path
import unittest
from math import hypot
from copy import deepcopy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import normal
import dynamic


class DynamicTests(unittest.TestCase):
    def audit(self,result):
        initial=result['initial']; data=initial['data']; orders={o['id']:o for o in data['orders']}
        failed=result['event']['vehicle']; states=result['states']; stranded=set(result['impacted'])
        for name in ('baseline','recovered'):
            plan=result[name]; delivered=[]; cost=0.; lateness=[]
            for v in data['vehicles']:
                vid=v['id']; rr=plan['routes'][vid]; state=states[vid]
                completed=state['completed']; self.assertEqual(rr['stops'][:len(completed)],completed)
                future=rr['stops'][len(completed):]
                if vid==failed:self.assertFalse(future)
                t=state['available']; pos=state['point']; km=state['traveled']
                cargo=set(state['remaining']); w=state['weight_kg']; vol=state['volume_m3']; pickups=0
                picked={s['id'] for s in future if s['id'] in stranded}
                for s in future:
                    d=hypot(pos[0]-s['point'][0],pos[1]-s['point'][1]); km+=d
                    self.assertGreaterEqual(s['arrival']+.001,t+d/.5)
                    if s['id']=='P':
                        pickups+=1; self.assertEqual(s['point'],states[failed]['point'])
                        cargo.update(picked)
                        w+=sum(orders[x]['quantity']*orders[x]['unit_weight_kg'] for x in picked)
                        vol+=sum(orders[x]['quantity']*orders[x]['unit_volume_m3'] for x in picked)
                        self.assertAlmostEqual(s['finish']-s['arrival'],8,places=3)
                    else:
                        oid=s['id']; self.assertIn(oid,cargo); cargo.remove(oid)
                        w-=orders[oid]['quantity']*orders[oid]['unit_weight_kg']
                        vol-=orders[oid]['quantity']*orders[oid]['unit_volume_m3']
                        self.assertGreaterEqual(s['arrival'],orders[oid]['open']-.001)
                        self.assertLessEqual(s['arrival'],orders[oid]['close']+.001)
                        self.assertAlmostEqual(s['finish']-s['arrival'],5,places=3)
                    self.assertGreaterEqual(w,-.001); self.assertGreaterEqual(vol,-.001)
                    self.assertLessEqual(w,v['max_weight_kg']+.001)
                    self.assertLessEqual(vol,v['max_volume_m3']+.001)
                    self.assertAlmostEqual(w,s['weight_kg'],places=3)
                    self.assertAlmostEqual(vol,s['volume_m3'],places=5)
                    pos=s['point']; t=s['finish']
                self.assertLessEqual(pickups,1)
                if vid!=failed:
                    self.assertFalse(cargo); km+=hypot(*pos)
                self.assertAlmostEqual(rr['distance'],km,places=3)
                used=bool(initial['routes'][vid]['sequence']) or bool(future)
                cost+=km*v['cost_per_km']+(v['fixed_cost'] if used else 0)+15*pickups
                for s in rr['stops']:
                    if s['id']=='P':continue
                    delivered.append(s['id']); lateness.append(max(0,s['arrival']-orders[s['id']]['due']))
            self.assertEqual(len(delivered),len(set(delivered)))
            self.assertEqual(set(delivered)|set(plan['unserved_ids']),set(orders))
            self.assertFalse(set(delivered)&set(plan['unserved_ids']))
            self.assertAlmostEqual(cost,plan['metrics']['cost'],places=2)
            self.assertEqual(sum(x>1e-6 for x in lateness),plan['metrics']['late_orders'])
            self.assertAlmostEqual(sum(lateness),plan['metrics']['late_minutes'],places=2)

    def test_default_cargo_time_capacity_cost(self):
        r=dynamic.run(normal.defaults(),'V2 在 08:25 故障'); self.audit(r)
        self.assertEqual(set(r['impacted']),{'S03','S04'})

    def test_changed_quantity_uses_same_input_and_plan(self):
        d=normal.defaults(); d['orders'][6]['quantity']=40; before=deepcopy(d)
        r=dynamic.run(d,'V3 在 08:16 故障'); self.audit(r)
        self.assertEqual(d,before); self.assertEqual(r['initial']['data']['orders'][6]['quantity'],40)
        self.assertEqual(r['initial']['routes'],normal.solve(d)['routes'])

    def test_multiple_fault_times_including_service(self):
        for v,t in [('V1','08:05'),('V2','08:16'),('V3','08:16'),('V1','08:00'),('V2','09:00')]:
            with self.subTest(v=v,t=t): self.audit(dynamic.run(normal.defaults(),f'{v} 在 {t} 故障'))

    def test_pickup_volume_and_precedence(self):
        d=normal.defaults(); orders={o['id']:o for o in d['orders']}; v=d['vehicles'][0].copy(); v['max_volume_m3']=.9
        state=dict(remaining=['S01'],weight_kg=120,volume_m3=.8,point=[0,0],available=480,completed=[])
        self.assertIsNone(dynamic.future_route(['P','S01','S02'],state,v,orders,['S02'],[0,0]))
        self.assertIsNone(dynamic.future_route(['S02','P','S01'],state,v,orders,['S02'],[0,0]))
        self.assertIsNotNone(dynamic.future_route(['S01','P','S02'],state,v,orders,['S02'],[0,0]))

    def test_no_solution_preserves_healthy_work(self):
        d=normal.defaults()
        for v in d['vehicles'][:2]:v['max_volume_m3']=.9
        d['vehicles'][2].update(max_weight_kg=2000,max_volume_m3=10)
        r=dynamic.run(d,'V3 在 08:00 故障'); self.audit(r)
        self.assertGreater(r['recovered']['metrics']['unserved'],0)
        self.assertIn('未找到',r['recovered']['status'])

    def test_clarification_and_incomplete_normal_plan(self):
        self.assertFalse(dynamic.run(normal.defaults(),'V2 故障')['parsed']['ready'])
        d=normal.defaults(); d['orders'][6]['quantity']=100
        with self.assertRaises(ValueError): dynamic.run(d,'V2 在 08:25 故障')

    def test_bounded_search_at_large_stranded_load(self):
        d=normal.defaults(); initial=normal.solve(d)
        # Controlled valid initial plan: all 12 orders on a large truck, even if late.
        v=d['vehicles'][2]; v.update(max_weight_kg=3000,max_volume_m3=20)
        ids=[o['id'] for o in d['orders']]; orders={o['id']:o for o in d['orders']}
        initial['data']=d; initial['routes']={x['id']:normal.route(ids if x['id']=='V3' else [],x,orders) for x in d['vehicles']}
        states=dynamic.snapshot(initial,480)
        r=dynamic.recover(initial,states,dict(vehicle='V3',time=480),'optimized')
        self.assertEqual(r['total_partitions'],4096); self.assertLessEqual(r['searched_partitions'],64)

if __name__=='__main__':unittest.main()
