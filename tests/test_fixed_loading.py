from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import continuous
import fixed_loading
import loading
import fixed_loading_benchmark
import matrix_dispatch
from test_loading import box, pos, vehicle


class FixedGeometryTests(unittest.TestCase):
    def frozen(self, items, placements, sequence):
        m=dict(schema='fixed_loading_v1',items=items,vehicles={'V':vehicle()},
               placements={'V':placements},sequences={'V':sequence})
        return dict(manifest=m,sha256=fixed_loading.digest(m))

    def test_reorder_blocks_rear_door_and_keeps_retained_cargo(self):
        f=self.frozen([box('a','A'),box('b','B')],[pos('a',x=50),pos('b')],['A','B'])
        before=deepcopy(f)
        self.assertTrue(fixed_loading.check(f,'V',['A','B'],[],['A','B'])['passed'])
        self.assertFalse(fixed_loading.check(f,'V',['A','B'],[],['B','A'])['passed'])
        self.assertFalse(fixed_loading.check(f,'V',['A','B'],[],['B'])['passed'])
        self.assertEqual(f,before)

    def test_stack_cannot_remove_support_before_top(self):
        f=self.frozen([box('a','A'),box('b','B')],[pos('b'),pos('a',z=50)],['A','B'])
        self.assertTrue(fixed_loading.check(f,'V',['A','B'],[],['A','B'])['passed'])
        self.assertFalse(fixed_loading.check(f,'V',['A','B'],[],['B','A'])['passed'])
        self.assertTrue(fixed_loading.check(f,'V',['B'],[{'id':'A'}],['B'])['passed'])

    def test_side_by_side_can_reorder_and_same_store_top_first(self):
        f=self.frozen([box('a','A'),box('b','B')],[pos('a'),pos('b',y=50)],['A','B'])
        self.assertTrue(fixed_loading.check(f,'V',['A','B'],[],['B','A'])['passed'])
        f=self.frozen([box('a','A'),box('b','A')],[pos('a'),pos('b',z=50)],['A'])
        self.assertEqual(fixed_loading.check(f,'V',['A'],[],['A'])['unload_steps'][0]['box_ids'],['b','a'])

    def test_hash_and_cargo_mismatch_rejected(self):
        f=self.frozen([box('a','A')],[pos('a')],['A'])
        with self.assertRaises(ValueError):fixed_loading.check(f,'V',[],[],[])
        f['manifest']['placements']['V'][0]['x']=1
        with self.assertRaises(ValueError):fixed_loading.check(f,'V',['A'],[],['A'])


class FixedDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=continuous.DispatchStore(Path(self.tmp.name)/'isolated.db')
        self.sample=loading.sample()
        self.scene=self.store.act('start',self.sample)
        self.frozen=deepcopy(self.scene['live_plan']['fixed_loading'])
        self.vid='TRUCK'

    def act(self,action,**body):
        self.scene=self.store.act(action,dict(id=self.scene['id'],revision=self.scene['revision'],**body))
        self.assertEqual(self.scene['live_plan']['fixed_loading'],self.frozen)
        return self.scene

    def event(self,store='SHOP_A',opening=560):
        return dict(time=480,store=store,open=opening,due=580,close=690)

    def test_joint_start_persist_and_no_repacking_on_event_or_adoption(self):
        self.assertEqual(self.scene['cargo']['SHOP_A'],'TRUCK')
        self.assertEqual(self.scene['cargo']['SHOP_B'],'TRUCK')
        with patch('loading.constructive',side_effect=AssertionError('free repack')):
            self.act('event',kind='window',event=self.event())
            p=self.scene['versions'][-1]['plan']
            self.assertEqual(p['routes'][self.vid]['sequence'],['SHOP_A','SHOP_B'])
            self.assertTrue(p['routes'][self.vid]['fixed_loading_check']['passed'])
            self.act('adopt',candidate=self.scene['candidate'])
            self.assertEqual(self.scene,self.store.read())
            continuous.DispatchStore(self.store.path).compare()

    def test_unconstrained_reorder_is_rejected_under_original_coordinates(self):
        payload=fixed_loading_benchmark.three()
        original=matrix_dispatch.start(payload['csv_files'],payload['loading_profile'])
        for r in original['routes'].values():r['fixed_loading']=deepcopy(original['fixed_loading'])
        base=deepcopy(original);base.pop('fixed_loading')
        for r in base['routes'].values():r.pop('fixed_loading')
        event=dict(time=480,store='B',open=600,due=620,close=690)
        free,_=continuous.calculate(base,event,'window',480)
        self.assertEqual(free['routes']['V1']['sequence'],['A','C','B'])
        constrained,_=continuous.calculate(original,event,'window',480)
        self.assertEqual(constrained['routes']['V1']['sequence'],['A','B','C'])

    def test_receipts_release_only_signed_boxes_and_complete_return(self):
        for oid in ('SHOP_A','SHOP_B'):
            r=self.scene['live_plan']['routes'][self.vid]
            stop=next(s for s in r['stops'] if s['id']==oid)
            t=max(self.scene['clock'],stop['arrival'])
            self.act('receipt',vehicle=self.vid,order=oid,receipt='arrived',time=t,source='manual_simulation')
            self.act('receipt',vehicle=self.vid,order=oid,receipt='unloading',time=t,source='manual_simulation')
            q=self.scene['states'][self.vid]
            self.assertIn(oid,q['cargo'])
            self.act('receipt',vehicle=self.vid,order=oid,receipt='signed',time=t+5,source='manual_simulation')
            r=self.scene['live_plan']['routes'][self.vid]
            self.assertNotIn(oid,self.scene['states'][self.vid]['cargo'])
            self.assertTrue(r['fixed_loading_check']['passed'])
            self.assertEqual(r['fixed_loading_check']['boxes'],1 if oid=='SHOP_A' else 0)
        self.act('advance',time=800)
        self.assertEqual(self.scene['states'][self.vid]['node'],self.scene['live_plan']['data']['settings']['depot_id'])
        self.assertEqual(self.scene,continuous.DispatchStore(self.store.path).read())

    def test_urgent_and_missing_rules_rollback(self):
        before=self.store.read()
        with self.assertRaisesRegex(ValueError,'剩余空间'):
            self.act('event',kind='urgent',event={'time':480})
        self.assertEqual(self.store.read(),before)
        bad=deepcopy(self.sample);bad['loading_profile']['items'].pop()
        with self.assertRaises(ValueError):self.act('start',**bad)
        self.assertEqual(self.store.read(),before)
        with self.assertRaises(ValueError):self.act('start',csv_files=self.sample['csv_files'],loading_profile=None)
        self.assertEqual(self.store.read(),before)

    def test_adoption_rechecks_frozen_manifest_and_rolls_back(self):
        self.act('event',kind='window',event=self.event())
        scene=self.store.read();scene['versions'][-1]['plan']['fixed_loading']['manifest']['placements']['TRUCK'][0]['x']+=1
        with closing(__import__('sqlite3').connect(self.store.path)) as db, db:
            db.execute('UPDATE dispatch_scenes SET body=? WHERE id=?',(json.dumps(scene),scene['id']))
        with self.assertRaises(ValueError):self.act('adopt',candidate=scene['candidate'])
        self.assertEqual(self.store.read(),scene)

    def test_ordinary_csv_scene_remains_available_and_history_preserved(self):
        oldid=self.scene['id'];old=self.store.read()
        new=self.store.act('start',dict(id=oldid,revision=old['revision'],csv_files=self.sample['csv_files']))
        self.assertNotIn('fixed_loading',new['live_plan'])
        self.assertEqual(self.store.read(oldid),old)

    def test_source_mismatch_and_incomplete_loading_never_replace_scene(self):
        before=self.store.read()
        with self.assertRaises(ValueError):self.act('start',**self.sample,loading_input_sha256={})
        self.assertEqual(self.store.read(),before)
        bad=deepcopy(self.sample)
        for v in bad['loading_profile']['vehicles']:
            if v['id']=='TRUCK':v['length_cm']=120
        # Fix max volume to avoid rejecting only the input volume contract.
        bad['csv_files']['vehicles.csv']=bad['csv_files']['vehicles.csv'].replace('TRUCK,800,2,','TRUCK,800,1.2,')
        bad['csv_files']['orders.csv']=bad['csv_files']['orders.csv'].replace('VAN|TRUCK','TRUCK')
        with self.assertRaisesRegex(ValueError,'不完整'):self.act('start',**bad)
        self.assertEqual(self.store.read(),before)

    def test_blocked_reorder_cannot_hide_retained_boxes_or_accept_incomplete_plan(self):
        payload=fixed_loading_benchmark.three()
        payload['csv_files']['orders.csv']=payload['csv_files']['orders.csv'].replace('09:30,11:30','09:30,09:30')
        scene=self.store.act('start',dict(id=self.scene['id'],revision=self.scene['revision'],**payload))
        before=self.store.read()
        with self.assertRaises(ValueError):
            self.store.act('event',dict(id=scene['id'],revision=scene['revision'],kind='window',
                event=dict(time=480,store='B',open=600,due=620,close=690)))
        self.assertEqual(self.store.read(),before)

    def test_late_signed_fact_refreshes_boxes_even_when_time_forecast_fails(self):
        for kind,tm in [('arrived',800),('unloading',800),('signed',805)]:
            self.act('receipt',vehicle=self.vid,order='SHOP_A',receipt=kind,time=tm,source='manual_simulation')
        plan=self.scene['live_plan']
        self.assertTrue(plan['needs_replan'])
        self.assertEqual(self.scene['cargo']['SHOP_A'],'delivered')
        check=plan['routes'][self.vid]['fixed_loading_check']
        self.assertTrue(check['passed']);self.assertEqual(check['boxes'],1)
        self.assertEqual([s['order'] for s in check['unload_steps']],['SHOP_B'])


if __name__=='__main__':unittest.main()
