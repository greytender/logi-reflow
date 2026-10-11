from contextlib import closing
import unittest
import tempfile
import sqlite3
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import normal
import continuous
from verify_comparison import audit
from store import Conflict


class ContinuousTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'scene.db';self.store=continuous.DispatchStore(self.path)
        self.state=self.store.act('start',dict(data=normal.defaults()))

    def act(self,action,**extra):
        self.state=self.store.act(action,dict(id=self.state['id'],revision=self.state['revision'],**extra));return self.state

    def urgent(self):
        self.act('event',kind='urgent',event=dict(time=505,store='S07',quantity=10,due=540,close=690))
        self.act('adopt',candidate=self.state['candidate'])

    def window(self):
        return self.act('event',kind='window',event=dict(time=506,store='S08',open=555,due=570,close=690))

    def plan(self):
        return next(v['plan'] for v in self.state['versions'] if v['id']==self.state['active'])

    def test_complete_chain_restart_and_loading(self):
        self.urgent();self.act('advance',time=506);before=self.state['states'];self.window()
        candidate=self.state['versions'][-1];self.assertEqual(candidate['base'],2)
        self.assertEqual(len(candidate['plan']['data']['orders']),13)
        for vid,s in before.items():
            self.assertEqual(candidate['states'][vid]['point'],s['point'])
            self.assertEqual(candidate['states'][vid]['cargo'],s['cargo'])
            self.assertEqual(candidate['plan']['routes'][vid]['stops'][:len(s['completed'])],s['completed'])
        self.act('adopt',candidate=3);self.assertEqual(self.state['active'],3)
        self.assertEqual(continuous.DispatchStore(self.path).read(),self.state)
        plan=self.plan();owner=next(vid for vid,r in plan['routes'].items() if 'W' in r['sequence'])
        rr=plan['routes'][owner];w=next(s for s in rr['stops'] if s['id']=='W');e=next(s for s in rr['stops'] if s['id']=='E01')
        self.assertAlmostEqual(w['finish']-w['arrival'],8)
        self.assertGreater(e['arrival'],w['finish'])
        self.act('advance',time=w['arrival']+2);self.assertEqual(self.state['cargo']['E01'],'warehouse')
        self.assertNotIn('W',[x['id'] for x in self.state['states'][owner]['completed']])
        self.act('advance',time=w['finish']);self.assertEqual(self.state['cargo']['E01'],owner)
        self.act('advance',time=e['arrival']);self.assertEqual(self.state['cargo']['E01'],'delivered')

    def test_candidate_does_not_execute_or_change_active(self):
        self.urgent();old=self.plan();self.window()
        self.assertEqual(self.state['active'],2);self.assertEqual(self.state['clock'],505)
        self.assertEqual(self.plan(),old);self.assertEqual(self.state['versions'][-1]['status'],'candidate')

    def test_delivered_cannot_be_changed(self):
        self.urgent();old=self.store.read()
        with self.assertRaisesRegex(ValueError,'已交付'):
            self.act('event',kind='window',event=dict(time=506,store='S05',open=555,due=570,close=690))
        self.assertEqual(self.store.read(),old)

    def test_time_never_moves_back_and_failed_write_rolls_back(self):
        self.urgent();self.act('advance',time=506);old=self.store.read()
        with self.assertRaises(ValueError): self.act('advance',time=505)
        with self.assertRaises(ValueError):self.act('event',kind='window',event=dict(time=505,store='S08',open=555,due=570,close=690))
        self.assertEqual(self.store.read(),old)

    def test_infeasible_candidate_cannot_adopt(self):
        self.urgent();self.act('event',kind='window',event=dict(time=506,store='S08',open=506,due=506,close=506))
        p=self.state['versions'][-1]['plan'];self.assertIn('S08',p['unserved_ids'])
        self.assertIn('S08',p['routes']['V1']['retained_ids'])
        with self.assertRaises(ValueError):self.act('adopt',candidate=self.state['candidate'])
        self.assertEqual(self.store.read()['active'],2)

    def test_advance_invalidates_pending_candidate(self):
        self.urgent();self.window();number=self.state['candidate'];self.act('advance',time=506)
        self.assertIsNone(self.state['candidate']);self.assertEqual(self.state['versions'][-1]['status'],'superseded')
        with self.assertRaises(Conflict):self.act('adopt',candidate=number)

    def test_stale_and_concurrent_adoption(self):
        self.urgent();self.window();body=dict(id=self.state['id'],revision=self.state['revision'],candidate=self.state['candidate'])
        def adopt(_):
            try:self.store.act('adopt',body);return True
            except Conflict:return False
        with ThreadPoolExecutor(2) as pool:self.assertEqual(sorted(pool.map(adopt,range(2))),[False,True])

    def test_new_scene_archives_and_preserves_legacy(self):
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute('CREATE TABLE legacy(value TEXT)');c.execute("INSERT INTO legacy VALUES ('keep')")
        old=self.state;self.act('start',data=normal.defaults());self.assertNotEqual(old['id'],self.state['id'])
        with self.assertRaises(Conflict):self.store.act('advance',dict(id=old['id'],revision=old['revision'],time=506))
        with closing(sqlite3.connect(self.path)) as c, c:
            self.assertEqual(c.execute('SELECT value FROM legacy').fetchone()[0],'keep')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM dispatch_scenes').fetchone()[0],2)

    def test_constraints_and_cost_independent(self):
        self.urgent();self.window();p=self.state['versions'][-1]['plan'];orders={o['id']:o for o in p['data']['orders']};total=0
        for v in p['data']['vehicles']:
            r=p['routes'][v['id']];a=r['anchor'];cargo=set(a['cargo']);pos=a['point'];t=a['available'];km=a['traveled']
            for stop in r['stops'][len(a['completed']):]:
                km+=continuous.engine.distance(pos,stop['point']);self.assertGreaterEqual(stop['arrival']+1e-3,t+continuous.engine.distance(pos,stop['point'])/.5)
                if stop['id']=='W':cargo.update(stop['load_ids']);self.assertAlmostEqual(stop['finish']-stop['arrival'],8)
                else:
                    self.assertIn(stop['id'],cargo);cargo.remove(stop['id']);o=orders[stop['id']]
                    self.assertLessEqual(stop['arrival'],o['close']);self.assertGreaterEqual(stop['arrival'],o['open'])
                w,vol=normal.totals(cargo,orders);self.assertLessEqual(w,v['max_weight_kg']);self.assertLessEqual(vol,v['max_volume_m3'])
                pos=stop['point'];t=stop['finish']
            self.assertFalse(cargo);km+=continuous.engine.distance(pos,[0,0]);total+=v['fixed_cost']+km*v['cost_per_km']
        self.assertEqual(round(total,2),p['metrics']['cost'])

    def test_scope_is_explicit(self):
        self.urgent()
        with self.assertRaisesRegex(ValueError,'一个新增'):
            self.act('event',kind='urgent',event=dict(time=506,store='S07',quantity=10,due=540,close=690))
        with self.assertRaisesRegex(ValueError,'故障仍为独立'):
            self.act('event',kind='failure',event=dict(time=506))

    def test_receiving_change_on_truck_with_warehouse_node(self):
        self.urgent()
        self.act('event',kind='window',event=dict(time=506,store='S04',open=530,due=545,close=690))
        p=self.state['versions'][-1]['plan'];r=p['routes']['V2']
        self.assertEqual(r['sequence'].count('W'),1)
        self.assertLess(r['sequence'].index('W'),r['sequence'].index('E01'))
        w=next(s for s in r['stops'] if s['id']=='W');e=next(s for s in r['stops'] if s['id']=='E01')
        self.assertEqual(w['load_ids'],['E01']);self.assertGreaterEqual(e['arrival'],w['finish'])
        self.assertNotIn('E01',r['anchor']['cargo'])
        self.act('adopt',candidate=self.state['candidate']);self.window()
        self.assertEqual(self.state['versions'][-1]['base'],3)
        self.assertEqual(len(self.state['versions'][-1]['plan']['data']['orders']),13)

    def test_same_state_comparison_and_read_only(self):
        self.urgent();self.act('advance',time=506);self.window();self.act('adopt',candidate=3)
        before=self.store.read();result=self.store.compare();self.assertEqual(self.store.read(),before)
        parent=self.state['versions'][1]['plan']
        for method in ('baseline','recovered'):audit(result[method],result['states'],parent)
        b=result['baseline']['metrics'];r=result['recovered']['metrics']
        self.assertEqual((b['served'],r['served']),(13,13))
        self.assertEqual((b['late_minutes'],r['late_minutes']),(51.32,15.66))
        self.assertEqual((b['cost'],r['cost']),(219.48,226.16))

    def test_unchanged_window_does_not_invent_improvement(self):
        self.urgent();p=self.plan();o=next(x for x in p['data']['orders'] if x['id']=='S08')
        self.act('event',kind='window',event=dict(time=506,store='S08',**{k:o[k] for k in ('open','due','close')}))
        r=self.store.compare();self.assertEqual(r['baseline']['metrics'],r['recovered']['metrics'])

    def test_infeasible_comparison_keeps_goods_and_reports_missing(self):
        self.urgent();self.act('event',kind='window',event=dict(time=506,store='S08',open=506,due=506,close=506))
        r=self.store.compare()
        for method in ('baseline','recovered'):
            self.assertEqual(r[method]['unserved_ids'],['S08'])
            audit(r[method],r['states'],self.state['versions'][1]['plan'])

    def test_comparison_refuses_saved_plan_drift(self):
        self.urgent();self.window();bad=self.store.read();bad['versions'][-1]['plan']['metrics']['cost']=0
        with self.assertRaisesRegex(ValueError,'不一致'):continuous.comparison(bad)
        self.assertFalse(continuous.comparison(None)['available'])
