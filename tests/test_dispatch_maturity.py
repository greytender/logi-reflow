"""User decisions, historical reads and repeated event/execution boundaries."""
from copy import deepcopy
import unittest
import tempfile
import sqlite3
from pathlib import Path
import continuous
import normal
from store import Conflict
from verify_comparison import audit


class DispatchMaturityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'scene.sqlite3';self.store=continuous.DispatchStore(self.path)
        self.scene=self.store.act('start',dict(data=normal.defaults(),progress_mode='legacy_eta'))

    def act(self,action,**extra):
        self.scene=self.store.act(action,dict(id=self.scene['id'],revision=self.scene['revision'],**extra))
        return self.scene

    def urgent(self):
        self.act('event',kind='urgent',event=dict(time=505,store='S07',quantity=10,due=540,close=690))
        self.act('adopt',candidate=self.scene['candidate'])

    def window(self,time=506,store='S08',opening=555,due=570,close=690):
        return self.act('event',kind='window',event=dict(time=time,store=store,open=opening,due=due,close=close))

    def test_candidate_review_explains_new_order_and_immutable_history(self):
        before=self.store.read()
        self.act('event',kind='urgent',event=dict(time=505,store='S07',quantity=10,due=540,close=690))
        stored=self.store.read();review=continuous.review_version(stored)
        self.assertEqual(review['frozen_delivery_count'],6)
        self.assertTrue(review['adoptable']);self.assertEqual(review['base'],before['active'])
        row=next(x for x in review['rows'] if x['id']=='E01')
        self.assertEqual(row['reasons'],['新增订单']);self.assertIsNone(row['before'])
        self.assertEqual(row['cargo_owner'],'warehouse');self.assertGreater(row['after']['late_minutes'],0)
        for row in review['rows']:
            if row['delivered']:self.assertEqual(row['before'],{k:row['after'][k] for k in ('vehicle','eta')})
        self.assertEqual(len(review['pickups']),1)
        self.assertAlmostEqual(review['pickups'][0]['finish']-review['pickups'][0]['arrival'],8)
        self.assertEqual(self.store.read(),stored)

    def test_discard_preserves_adopted_and_execution_but_keeps_candidate_history(self):
        self.urgent();before=self.store.read();self.window();number=self.scene['candidate']
        event=deepcopy(self.scene['events'][-1]);self.act('discard',candidate=number)
        for key in ('active','clock','states','cargo','execution'):
            self.assertEqual(self.scene[key],before[key])
        self.assertIsNone(self.scene['candidate']);self.assertEqual(self.scene['events'][-1],event)
        v=self.scene['versions'][-1];self.assertEqual(v['status'],'discarded')
        self.assertIsNone(v['executed_until']);self.assertIn('未采用',v['reason'])
        with self.assertRaises(Conflict):self.act('adopt',candidate=number)
        with self.assertRaises(Conflict):self.act('discard',candidate=number)

    def test_incomplete_review_blocked_and_can_be_discarded(self):
        self.urgent();self.window(opening=506,due=506,close=506)
        r=continuous.review_version(self.scene);self.assertFalse(r['adoptable']);self.assertTrue(r['blocked_reason'])
        row=next(x for x in r['rows'] if x['id']=='S08')
        self.assertIsNone(row['after']);self.assertEqual(row['cargo_owner'],'V1')
        self.act('discard',candidate=self.scene['candidate']);self.assertEqual(self.scene['active'],2)

    def test_history_reads_do_not_make_an_archived_scene_current(self):
        self.urgent();old=deepcopy(self.scene);self.act('start',data=normal.defaults());latest=self.store.read()
        archived=self.store.read(old['id']);self.assertEqual(archived,old)
        summaries=self.store.archive();self.assertEqual(summaries['total'],2)
        self.assertEqual([s['current'] for s in summaries['scenes']],[True,False])
        self.assertEqual(summaries['scenes'][1]['orders'],13)
        continuous.review_version(archived,1);continuous.review_version(archived,2)
        self.assertEqual(self.store.read(),latest);self.assertIsNone(self.store.read('missing'))
        with self.assertRaises(Conflict):self.store.act('advance',dict(id=old['id'],revision=old['revision'],time=506))

    def test_stale_discard_cannot_remove_a_newer_candidate(self):
        self.urgent();self.window();old=dict(id=self.scene['id'],revision=self.scene['revision'],candidate=self.scene['candidate'])
        self.window(time=507);stored=self.store.read()
        with self.assertRaises(Conflict):self.store.act('discard',old)
        self.assertEqual(self.store.read(),stored)
        self.assertIn('新的事件',self.scene['versions'][-2]['reason'])
        self.act('advance',time=508);self.assertIn('进度已变化',self.scene['versions'][-1]['reason'])

    def test_adopt_receiving_change_while_loading_does_not_restart_loading(self):
        self.urgent();self.window();self.act('adopt',candidate=self.scene['candidate'])
        parent=self.scene['versions'][-1]['plan'];w=next(s for s in parent['routes']['V2']['stops'] if s['id']=='W')
        when=w['arrival']+2;self.assertLess(when,540)
        self.act('advance',time=when);self.assertEqual(self.scene['cargo']['E01'],'warehouse')
        self.window(time=when,opening=570,due=585,close=700)
        compared=self.store.compare()
        for method in ('baseline','recovered'):
            audit(compared[method],compared['states'],parent)
            new_w=next(s for s in compared[method]['routes']['V2']['stops'] if s['id']=='W')
            self.assertEqual(new_w['arrival'],w['arrival']);self.assertEqual(new_w['finish'],w['finish'])
        self.act('adopt',candidate=self.scene['candidate']);self.act('advance',time=w['finish'])
        self.assertEqual(self.scene['cargo']['E01'],'V2')
        self.assertEqual(sum(s['id']=='W' for s in self.scene['states']['V2']['completed']),1)

    def test_multiple_adopted_changes_on_three_vehicles_are_auditable(self):
        self.urgent()
        for i,target in enumerate(('S08','S03','S11')):
            with self.subTest(target=target):
                parent=deepcopy(next(v['plan'] for v in self.scene['versions'] if v['id']==self.scene['active']))
                self.window(time=506+i,store=target,opening=530+i*5,due=550+i*5,close=690)
                r=self.store.compare()
                for method in ('baseline','recovered'):audit(r[method],r['states'],parent)
                self.act('adopt',candidate=self.scene['candidate'])
                self.assertEqual(len(self.scene['cargo']),13)
                self.assertGreaterEqual(sum(x=='delivered' for x in self.scene['cargo'].values()),6)
                for q in r['states'].values():
                    for stop in q['completed']:
                        if stop['id']!='W': self.assertEqual(self.scene['cargo'][stop['id']],'delivered')
        self.assertEqual(continuous.DispatchStore(self.path).read(),self.scene)

    def test_comparison_matrix_keeps_same_event_and_goods(self):
        self.urgent();parent=deepcopy(self.scene['versions'][-1]['plan'])
        for target in ('S08','S03','S11'):
            for opening in (506,555,600):
                for close in (opening,690):
                    with self.subTest(target=target,opening=opening,close=close):
                        self.window(store=target,opening=opening,due=opening,close=close)
                        r=self.store.compare()
                        for method in ('baseline','recovered'):audit(r[method],r['states'],parent)
                        self.assertEqual(self.scene['active'],2);self.assertEqual(self.scene['clock'],505)
                        if not self.scene['versions'][-1]['plan']['metrics']['unserved']:
                            self.assertLessEqual(continuous.urgent.key(r['recovered']),continuous.urgent.key(r['baseline']))


if __name__=='__main__':unittest.main()
