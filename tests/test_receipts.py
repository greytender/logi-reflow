"""Progress receipts, cargo conservation, historical compatibility, persistence."""
from copy import deepcopy
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
import continuous
import normal
from store import Conflict


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'scene.db';self.store=continuous.DispatchStore(self.path)
        self.scene=self.store.act('start',dict(data=normal.defaults()))
        self.vid='V1';self.stop=self.scene['live_plan']['routes'][self.vid]['stops'][0]
        self.oid=self.stop['id'];self.time=max(480,self.stop['arrival'])

    def act(self,action,**extra):
        self.scene=self.store.act(action,dict(id=self.scene['id'],revision=self.scene['revision'],**extra))
        return self.scene

    def receipt(self,kind,time=None,**extra):
        return self.act('receipt',vehicle=self.vid,order=self.oid,receipt=kind,
            time=self.time if time is None else time,source='manual_simulation',**extra)

    def unload(self):
        self.receipt('arrived');self.receipt('unloading')

    def test_advance_never_signs_even_far_past_eta(self):
        self.act('advance',time=600)
        self.assertEqual(len([v for v in self.scene['cargo'].values() if v=='delivered']),0)
        self.assertEqual(self.scene['states'][self.vid]['point'],self.stop['point'])
        self.assertEqual(self.scene['cargo'][self.oid],self.vid)

    def test_three_stages_and_actual_times(self):
        self.receipt('arrived');self.assertEqual(self.scene['states'][self.vid]['pending']['status'],'arrived')
        self.receipt('unloading');self.assertEqual(self.scene['cargo'][self.oid],self.vid)
        self.receipt('signed',self.time+7)
        self.assertEqual(self.scene['cargo'][self.oid],'delivered')
        stop=self.scene['states'][self.vid]['completed'][0]
        self.assertEqual(stop['arrival'],self.time);self.assertEqual(stop['signed_at'],self.time+7)
        self.assertEqual(stop['progress_source'],'manual_simulation')
        self.assertNotIn(self.oid,self.scene['states'][self.vid]['cargo'])

    def test_early_arrival_rejected_and_rollback(self):
        before=self.store.read()
        with self.assertRaises(ValueError): self.receipt('arrived',480)
        self.assertEqual(before,self.store.read())

    def test_skip_repeat_and_short_unload_rejected(self):
        with self.assertRaises(ValueError): self.receipt('signed')
        self.receipt('arrived')
        with self.assertRaises(ValueError): self.receipt('arrived')
        self.receipt('unloading');before=self.store.read()
        with self.assertRaises(ValueError): self.receipt('signed',self.time+4)
        self.assertEqual(before,self.store.read())

    def test_wrong_vehicle_and_order_and_source(self):
        for extra in [dict(vehicle='V2',order=self.oid),dict(vehicle=self.vid,order='S99'),dict(vehicle=self.vid,order=self.oid,source='driver')]:
            b=dict(vehicle=self.vid,order=self.oid,receipt='arrived',time=self.time,source='manual_simulation');b.update(extra)
            with self.assertRaises(ValueError): self.act('receipt',**b)
        self.assertEqual(self.scene,self.store.read())

    def test_restart_preserves_unloading_and_cargo(self):
        self.unload();self.assertEqual(continuous.DispatchStore(self.path).read(),self.scene)
        self.act('advance',time=self.time+15)
        self.assertEqual(self.scene['states'][self.vid]['pending']['status'],'unloading')
        self.assertEqual(self.scene['cargo'][self.oid],self.vid)

    def test_signed_history_immutable_and_window_rejected(self):
        self.unload();self.receipt('signed',self.time+5);done=deepcopy(self.scene['states'][self.vid]['completed'])
        with self.assertRaises(ValueError): self.receipt('signed',self.time+6)
        with self.assertRaisesRegex(ValueError,'已交付'):
            self.act('event',kind='window',event=dict(time=self.time+6,store=self.oid,open=550,due=570,close=690))
        self.act('advance',time=self.time+6)
        self.assertEqual(done,self.scene['states'][self.vid]['completed'])

    def test_arrived_stop_cannot_move_or_remove(self):
        self.receipt('arrived');q=self.scene['states'][self.vid]
        p=self.scene['live_plan'];orders={o['id']:o for o in p['data']['orders']}
        v=next(v for v in p['data']['vehicles'] if v['id']==self.vid)
        for seq in [q['remaining'][1:]+[self.oid],q['remaining'][1:]]:
            self.assertIsNone(continuous.future(seq,q,v,orders,p['routes'][self.vid]))

    def test_receipt_invalidates_candidate_and_stale_tab(self):
        self.act('event',kind='window',event=dict(time=self.time,store='S08',open=550,due=570,close=690))
        stale=deepcopy(self.scene);self.receipt('arrived')
        self.assertIsNone(self.scene['candidate'])
        with self.assertRaises(Conflict): self.store.act('receipt',dict(id=stale['id'],revision=stale['revision'],time=self.time))

    def test_urgent_adoption_does_not_sign_or_teleport_new_cargo(self):
        self.act('event',kind='urgent',event=dict(time=505,store='S07',quantity=10,due=540,close=690))
        self.assertNotIn('E01',self.scene['cargo']);self.assertEqual(self.scene['clock'],480)
        self.act('adopt',candidate=self.scene['candidate'])
        self.assertEqual(self.scene['cargo']['E01'],'warehouse')
        self.assertFalse(any(x=='delivered' for x in self.scene['cargo'].values()))
        self.assertEqual(self.scene['live_plan']['progress_mode'],'manual_receipts')

    def test_legacy_without_schema_flag_keeps_original_history(self):
        self.act('start',data=normal.defaults(),progress_mode='legacy_eta')
        old=deepcopy(self.scene);old.pop('progress_mode');old.pop('live_plan');old.pop('receipts')
        old['versions'][0]['plan'].pop('progress_mode')
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute('UPDATE dispatch_scenes SET body=? WHERE id=?',(json.dumps(old),old['id']))
        self.scene=old;self.act('advance',time=505)
        self.assertTrue(any(x=='delivered' for x in self.scene['cargo'].values()))
        before=self.store.read()
        with self.assertRaisesRegex(ValueError,'旧场景'):self.receipt('arrived',505)
        self.assertEqual(before,self.store.read())

    def test_plan_versions_stay_immutable_after_receipts(self):
        plan=deepcopy(self.scene['versions'][0]['plan']);self.unload();self.receipt('signed',self.time+5)
        self.assertEqual(plan,self.scene['versions'][0]['plan'])

    def test_late_receipt_is_saved_even_if_route_needs_replan(self):
        self.receipt('arrived',1000);self.receipt('unloading',1000);self.receipt('signed',1005)
        self.assertEqual(self.scene['cargo'][self.oid],'delivered')
        self.assertTrue(self.scene['live_plan']['needs_replan'])

    def test_window_after_signature_preserves_actual_history_and_replays(self):
        self.unload();self.receipt('signed',self.time+5)
        done=deepcopy(self.scene['states'][self.vid]['completed'])
        self.act('event',kind='window',event=dict(time=505,store='S08',open=550,due=570,close=690))
        result=self.store.compare();self.assertTrue(result['available'])
        for method in ('baseline','recovered'):
            self.assertEqual(result[method]['routes'][self.vid]['anchor']['completed'],done)
        self.act('adopt',candidate=self.scene['candidate'])
        self.assertEqual(self.scene['states'][self.vid]['completed'],done)
        self.assertEqual(self.scene['cargo'][self.oid],'delivered')

    def test_entire_urgent_route_pickup_then_signature(self):
        self.act('event',kind='urgent',event=dict(time=480,store='S07',quantity=10,due=540,close=690))
        self.act('adopt',candidate=self.scene['candidate'])
        pickup_seen=False
        for _ in range(16):
            plan=self.scene['live_plan'];options=[]
            for vid,q in self.scene['states'].items():
                if not q['remaining']:continue
                stop=next((s for s in plan['routes'][vid]['stops'][len(q['completed']):] if s['id']!='W'),None)
                if stop:options.append((max(self.scene['clock'],stop['arrival']),vid,stop['id']))
            if not options:break
            now,vid,oid=min(options);self.vid=vid;self.oid=oid;self.time=now
            self.act('advance',time=now)
            owner=self.scene['cargo']['E01']
            if owner not in ('warehouse','delivered'):
                pickup_seen=True
                self.assertTrue(any(s['id']=='W' for s in self.scene['states'][owner]['completed']))
            if oid=='E01':self.assertEqual(owner,vid)
            self.unload();self.receipt('signed',now+5)
        self.assertTrue(pickup_seen)
        self.assertEqual(set(self.scene['cargo'].values()),{'delivered'})
        self.assertEqual(len(self.scene['receipts']),39)

    def test_delayed_warehouse_loading_keeps_eight_minutes_and_resumes(self):
        p=deepcopy(self.scene['live_plan']);o=p['data']['orders'][0]
        p['routes']={'V1':dict(sequence=['W',o['id']],anchor=dict(point=[0,0],available=600,traveled=0,cargo=[],completed=[]),
            stops=[dict(id='W',point=[0,0],arrival=500,finish=508,load_ids=[o['id']]),
                dict(id=o['id'],point=o['point'],arrival=510,finish=515)])}
        q=continuous.snapshot(p,600)['V1'];self.assertNotIn(o['id'],q['cargo'])
        self.assertEqual(q['loading']['finish'],608)
        p['routes']['V1']['anchor']=q
        q=continuous.snapshot(p,603)['V1'];self.assertEqual(q['loading']['finish'],608)
        self.assertNotIn(o['id'],q['cargo'])
        v=p['data']['vehicles'][0];orders={x['id']:x for x in p['data']['orders']}
        r=continuous.future(['W',o['id']],q,v,orders,p['routes']['V1'])
        self.assertEqual(r['stops'][0]['finish'],608)
        q=continuous.snapshot(p,608)['V1'];self.assertIn(o['id'],q['cargo'])
        self.assertEqual(q['completed'][0]['finish'],608)

    def test_arrival_deadline_does_not_become_signature_deadline(self):
        self.unload();p=self.scene['live_plan'];q=deepcopy(self.scene['states'][self.vid])
        q['available']=self.time+10;orders={o['id']:deepcopy(o) for o in p['data']['orders']}
        orders[self.oid]['close']=self.time
        v=next(v for v in p['data']['vehicles'] if v['id']==self.vid)
        r=continuous.future([self.oid],q,v,orders,p['routes'][self.vid])
        self.assertIsNotNone(r);self.assertEqual(r['stops'][0]['arrival'],self.time)

    def test_window_cannot_rewrite_started_unloading(self):
        self.unload(); before=self.store.read()
        with self.assertRaisesRegex(ValueError,'已开始卸货'):
            self.act('event',kind='window',event=dict(time=self.time,store=self.oid,open=550,due=570,close=690))
        self.assertEqual(before,self.store.read())

    def test_unstarted_loading_cannot_borrow_old_finish(self):
        p=self.scene['live_plan'];v=p['data']['vehicles'][0];o=p['data']['orders'][0]
        q=dict(point=[0,0],available=504,traveled=0,cargo=[],completed=[],progress_mode='manual_receipts')
        old=dict(stops=[dict(id='W',arrival=500,finish=508,load_ids=[o['id']])])
        r=continuous.future(['W',o['id']],q,v,{o['id']:o},old)
        self.assertEqual(r['stops'][0]['finish'],512)
