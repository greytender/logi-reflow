import sys
from pathlib import Path
import unittest
from copy import deepcopy
from math import hypot
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import normal
import urgent


def event(**kw):
    return dict(time=505, store='S07', quantity=10, due=540, close=690, **kw)


class UrgentTests(unittest.TestCase):
    def audit(self, result, include_new=True, allow_retained=False):
        initial=result['initial']; orders={o['id']:o for o in initial['data']['orders']}
        if include_new:orders['E01']=result['order']
        original={s['id']:(v,s['arrival'],r['sequence'][i-1] if i else None)
                  for v,r in initial['routes'].items() for i,s in enumerate(r['stops'])}
        for name in ('baseline','recovered'):
            plan=result[name]; all_ids=[]; late=[]; changes=[]; total_cost=0
            for v in initial['data']['vehicles']:
                vid=v['id']; r=plan['routes'][vid]; state=result['states'][vid]; n=len(state['completed'])
                self.assertEqual(r['stops'][:n],state['completed'])
                cargo=set(state['remaining']); w=state['weight_kg']; vol=state['volume_m3']
                t=state['available']; p=state['point']; km=state['traveled']; pickups=0
                peak_w=w; peak_v=vol
                for s in r['stops'][n:]:
                    d=hypot(p[0]-s['point'][0],p[1]-s['point'][1]); km+=d
                    self.assertGreaterEqual(s['arrival']+.001,t+d/.5)
                    if s['id']=='W':
                        self.assertEqual(s['point'],[0,0]); pickups+=1; cargo.add('E01')
                        w+=orders['E01']['quantity']*orders['E01']['unit_weight_kg']
                        vol+=orders['E01']['quantity']*orders['E01']['unit_volume_m3']
                        self.assertAlmostEqual(s['finish']-s['arrival'],8,places=3)
                    else:
                        o=orders[s['id']]; self.assertIn(s['id'],cargo); cargo.remove(s['id'])
                        if s['id']!='E01':self.assertEqual(original[s['id']][0],vid)
                        self.assertLessEqual(s['arrival'],o['close']+.001)
                        self.assertGreaterEqual(s['arrival'],o['open']-.001)
                        w-=o['quantity']*o['unit_weight_kg']; vol-=o['quantity']*o['unit_volume_m3']
                        self.assertAlmostEqual(s['finish']-s['arrival'],5,places=3)
                    peak_w=max(peak_w,w); peak_v=max(peak_v,vol)
                    self.assertLessEqual(w,v['max_weight_kg']+.001); self.assertLessEqual(vol,v['max_volume_m3']+.001)
                    self.assertGreaterEqual(w,-.001); self.assertGreaterEqual(vol,-.001)
                    self.assertAlmostEqual(w,s['weight_kg'],places=3); self.assertAlmostEqual(vol,s['volume_m3'],places=5)
                    t=s['finish']; p=s['point']
                self.assertEqual(cargo,set(r.get('retained_ids',[])) if allow_retained else set()); self.assertLessEqual(pickups,1)
                self.assertAlmostEqual(r['peak_weight_kg'],peak_w,places=3); self.assertAlmostEqual(r['peak_volume_m3'],peak_v,places=5)
                km+=hypot(*p); self.assertAlmostEqual(r['distance'],km,places=3)
                self.assertAlmostEqual(r['return_at'],t+hypot(*p)/.5,places=3)
                used=bool(initial['routes'][vid]['sequence']) or bool(r['sequence'])
                total_cost+=km*v['cost_per_km']+(v['fixed_cost'] if used else 0)
                prev=None
                for s in r['stops']:
                    oid=s['id']
                    if oid!='W':
                        all_ids.append(oid); late.append(max(0,s['arrival']-orders[oid]['due']))
                        if oid in original:
                            old=original[oid]
                            if old[0]!=vid or old[2]!=prev or abs(old[1]-s['arrival'])>1:changes.append(oid)
                    prev=oid
            self.assertEqual(len(all_ids),len(set(all_ids)))
            self.assertEqual(set(all_ids)|set(plan['unserved_ids']),set(orders))
            self.assertFalse(set(all_ids)&set(plan['unserved_ids']))
            self.assertAlmostEqual(total_cost,plan['metrics']['cost'],places=2)
            self.assertEqual(sum(x>1e-6 for x in late),plan['metrics']['late_orders'])
            self.assertAlmostEqual(sum(late),plan['metrics']['late_minutes'],places=2)
            changes.extend(oid for oid in plan['unserved_ids'] if oid in original)
            self.assertEqual(sorted(set(changes)),plan['metrics']['changed_ids'])
            self.assertEqual(len(set(changes)),plan['metrics']['changed_orders'])

    def test_default_conservation_ownership_capacity_time_cost_disturbance(self):
        d=normal.defaults(); before=deepcopy(d); r=urgent.run(d,event()); self.audit(r)
        self.assertEqual(d,before); self.assertEqual(r['initial']['routes'],normal.solve(d)['routes'])
        self.assertEqual(r['recovered']['metrics']['served'],13)
        self.assertNotIn('E01',r['recovered']['metrics']['changed_ids'])

    def test_in_service_waiting_enroute_and_returned(self):
        for time in (480,486,505,540):
            with self.subTest(time=time):
                e=event(); e['time']=time; self.audit(urgent.run(normal.defaults(),e))
        d=normal.defaults()
        for o in d['orders']:o['open']=510
        e=event(); e['due']=550
        self.audit(urgent.run(d,e))

    def test_oversized_and_unreachable_keep_original_work(self):
        for changes in (dict(quantity=100),dict(due=506,close=506)):
            e=event(); e.update(changes); r=urgent.run(normal.defaults(),e); self.audit(r)
            for name in ('baseline','recovered'):
                p=r[name]; self.assertEqual(p['unserved_ids'],['E01']); self.assertEqual(p['metrics']['changed_orders'],0)
                self.assertFalse(any('W' in rr['sequence'] for rr in p['routes'].values()))
                self.assertIn('仓库',p['status'])

    def test_pickup_capacity_and_precedence(self):
        d=normal.defaults(); orders={o['id']:o for o in d['orders']}; orders['E01']=urgent.validate(event(),d)
        state=dict(remaining=['S01'],weight_kg=120,volume_m3=.8,point=[0,0],available=505,completed=[])
        v=d['vehicles'][0].copy(); v['max_volume_m3']=.9
        self.assertIsNone(urgent.future(['W','E01','S01'],state,v,orders))
        self.assertIsNone(urgent.future(['S01','E01','W'],state,v,orders))
        self.assertIsNotNone(urgent.future(['S01','W','E01'],state,v,orders))
        v['max_volume_m3']=10; v['max_weight_kg']=200
        self.assertIsNone(urgent.future(['W','E01','S01'],state,v,orders))

    def test_modified_quantity_and_bounded_search(self):
        d=normal.defaults(); d['orders'][6]['quantity']=40; r=urgent.run(d,event()); self.audit(r)
        self.assertEqual(r['initial']['data']['orders'][6]['quantity'],40)
        self.assertLess(r['recovered']['checked_routes'],5000)

    def test_event_validation_and_incomplete_initial(self):
        for change in (dict(time=True),dict(time=float('nan')),dict(time=550),dict(store='bad'),dict(quantity=1.2),dict(quantity=0),dict(due=500),dict(close=530),dict(routes=[])):
            e=event(); e.update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):urgent.run(normal.defaults(),e)
        for e in (None,[],{},dict(time=505)):
            with self.assertRaises(ValueError):urgent.run(normal.defaults(),e)
        d=normal.defaults(); d['orders'][6]['quantity']=100
        with self.assertRaises(ValueError):urgent.run(d,event())


if __name__=='__main__':unittest.main()
