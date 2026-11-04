from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import unittest

import application
import execution
import remaining
import test_application
import test_application_execution
from store import Conflict


class RemainingTests(unittest.TestCase):
    setUp = test_application_execution.ExecutionTests.setUp
    tearDown = test_application_execution.ExecutionTests.tearDown
    payload = test_application_execution.ExecutionTests.payload
    record = test_application_execution.ExecutionTests.record

    def test_candidate_does_not_change_execution_then_adoption_preserves_facts(self):
        t=self.record()
        original_plans=deepcopy(t['plans']);facts=deepcopy(t['receipts'])
        seq=remaining.sequences(t)
        # Valid prior same-car arrangement, deliberately longer for this fixture.
        seq[self.vid]=list(reversed(seq[self.vid]))
        t['remaining_sequences']=seq
        t=self.store.commit(t,t['revision'])
        old_next=execution.view(t)['routes'][self.vid]['next_stop']
        t=self.store.replan(t['id'],t['revision']);c=t['remaining_plans'][-1]
        self.assertTrue(c['audit']['passed']);self.assertTrue(c['changed'])
        self.assertLess(c['distance_after'],c['distance_before'])
        self.assertEqual(execution.view(t)['routes'][self.vid]['next_stop'],old_next)
        with self.assertRaises(ValueError):self.store.adopt_remaining(t['id'],t['revision'],c['id'],False)
        t=self.store.adopt_remaining(t['id'],t['revision'],c['id'],True)
        self.assertEqual(t['plans'],original_plans);self.assertEqual(t['receipts'],facts)
        self.assertNotEqual(execution.view(t)['routes'][self.vid]['next_stop'],old_next)
        rows=list(csv.DictReader(io.StringIO(self.store.export_remaining(t['id'],t['revision']).lstrip('\ufeff'))))
        self.assertEqual([r['门店'] for r in rows],c['sequences'][self.vid])
        self.assertTrue(all(r['剩余路线版本']==c['id'] for r in rows))
        self.assertEqual(application.ApplicationStore(self.path).read(t['id']),t)
        # Next receipt must follow the adopted order and retains the version ID.
        with self.assertRaises(ValueError):self.record(t,stop_id=old_next)
        t=self.record(t)
        self.assertEqual(t['receipts'][-1]['remaining_plan_id'],c['id'])

    def test_receipt_invalidates_candidate_even_with_latest_revision(self):
        t=self.record();t=self.store.replan(t['id'],t['revision']);c=t['remaining_plans'][-1]
        t=self.record(t)
        with self.assertRaises(Conflict):self.store.adopt_remaining(t['id'],t['revision'],c['id'],True)
        self.assertEqual(self.store.read(t['id']),t)

    def test_overloaded_or_late_candidate_retained_but_cannot_adopt(self):
        for changes in [dict(pickup=dict(cartons=10000,totes=0)),dict(finish=1430)]:
            t=self.record(**changes)
            t=self.store.replan(t['id'],t['revision']);c=t['remaining_plans'][-1]
            self.assertFalse(c['audit']['passed'])
            with self.assertRaises(ValueError):self.store.adopt_remaining(t['id'],t['revision'],c['id'],True)
            self.assertEqual(t['receipts'][0]['pickup'],changes.get('pickup',dict(cartons=20,totes=3)))
            # Each subcase uses a separate task, not a rollback of receipt facts.
            if 'pickup' in changes:
                raw=self.store.create('late',application.sample())
                raw=self.store.solve(raw['id'],raw['revision'])
                self.task=self.store.adopt(raw['id'],raw['revision'],raw['plans'][-1]['id'],True)

    def test_independent_audit_rejects_wrong_membership_and_vehicle(self):
        t=self.record();seq=remaining.sequences(t)
        for bad in [{}, {self.vid:[]}, {self.vid:[seq[self.vid][0]]*2},
                    {self.vid:['STORE_001']+seq[self.vid]}, {self.vid:['UNKNOWN']}]:
            with self.subTest(bad=bad),self.assertRaises(ValueError):remaining.audit(t,bad)
        t=self.store.replan(t['id'],t['revision'])
        t['remaining_plans'][-1]['sequences'][self.vid]=[]
        t=self.store.commit(t,t['revision'])
        with self.assertRaises(ValueError):self.store.adopt_remaining(t['id'],t['revision'],t['remaining_plans'][-1]['id'],True)

    def test_repeated_adoption_and_old_tab_are_blocked(self):
        t=self.record();old_revision=t['revision']
        t=self.store.replan(t['id'],t['revision']);c=t['remaining_plans'][-1]
        with self.assertRaises(Conflict):self.store.adopt_remaining(t['id'],old_revision,c['id'],True)
        t=self.store.adopt_remaining(t['id'],t['revision'],c['id'],True)
        with self.assertRaises(Conflict):self.store.adopt_remaining(t['id'],t['revision'],c['id'],True)

    def test_requires_receipts_and_completed_task_has_no_replan(self):
        with self.assertRaises(ValueError):self.store.replan(self.task['id'],self.task['revision'])
        t=self.task
        while execution.view(t)['routes'][self.vid]['next_stop']:t=self.record(t)
        t=self.record(t,kind='return',pickup=None)
        with self.assertRaises(ValueError):self.store.replan(t['id'],t['revision'])

    def test_independent_time_and_distance_match_projection(self):
        t=self.record(pickup=dict(cartons=30,totes=5))
        c=remaining.solve(t)
        check=c['audit']['checks'][0];r=c['after']['routes'][self.vid]
        # Sample roads from STORE_001 -> STORE_002 -> STORE_003 -> WH.
        self.assertEqual(r['remaining_distance_km'],24)
        self.assertEqual(r['estimated_return'],646)
        self.assertEqual(check['distance_km'],r['remaining_distance_km'])
        self.assertEqual(check['return_at'],r['estimated_return'])

    def test_pickup_increase_requires_longer_route_to_avoid_future_overload(self):
        fixture=json.loads((application.ROOT/'examples/remaining-pickup/scenario.json').read_text(encoding='utf-8'))
        t=self.store.create('回收偏差',fixture['data']);t=self.store.solve(t['id'],t['revision'])
        t=self.store.adopt(t['id'],t['revision'],t['plans'][-1]['id'],True)
        vid=fixture['vehicle']
        t=self.store.receipt(t['id'],t['revision'],dict(kind='stop',vehicle=vid,stop_id=fixture['first'],
            finish=600,pickup=dict(cartons=400,totes=0),source='simulated'),True)
        self.assertEqual(execution.view(t)['routes'][vid]['onboard_weight_kg'],314)
        self.assertTrue(execution.view(t)['routes'][vid]['warnings'])
        t=self.store.replan(t['id'],t['revision']);c=t['remaining_plans'][-1]
        self.assertEqual((c['distance_before'],c['distance_after']),(10,27))
        self.assertTrue(c['audit']['passed'])
        self.assertEqual(c['sequences'][vid],['STORE_001','STORE_003'])
        self.assertEqual(c['after']['routes'][vid]['remaining'][0]['onboard_weight_kg'],230)
        t=self.store.adopt_remaining(t['id'],t['revision'],c['id'],True)
        self.assertEqual(execution.view(t)['routes'][vid]['warnings'],[])
        self.assertEqual(t['receipts'][0]['pickup']['cartons'],400)

    def test_larger_route_uses_bounded_search(self):
        raw=application.sample()
        template=raw['orders'][0]
        raw['orders']=[]
        for i in range(9):
            o=deepcopy(template);o['id']=f'STOP_{i}';o['lines'][0]['quantity']=1
            o['allowed_vehicles']=['TRUCK_2'];o['pickup']=dict(cartons=0,totes=0)
            raw['orders'].append(o)
        nodes=['WH']+[o['id'] for o in raw['orders']]
        raw['legs']={a+'>'+b:dict(distance_km=1,travel_minutes=1) for a in nodes for b in nodes if a!=b}
        t=self.store.create('九店搜索预算',raw);t=self.store.solve(t['id'],t['revision'])
        t=self.store.adopt(t['id'],t['revision'],t['plans'][-1]['id'],True)
        r=execution.view(t)['routes']['TRUCK_2']
        t=self.store.receipt(t['id'],t['revision'],dict(vehicle='TRUCK_2',kind='stop',stop_id=r['next_stop'],
            finish=600,pickup=dict(cartons=0,totes=0),source='simulated'),True)
        c=remaining.solve(t)
        self.assertTrue(c['audit']['passed'])
        self.assertGreater(c['evaluations'],0);self.assertLessEqual(c['evaluations'],6*28)
        self.assertEqual(len(c['sequences']['TRUCK_2']),8)


class RemainingHTTPTests(unittest.TestCase):
    setUp=test_application.ApplicationHTTPTests.setUp
    tearDown=test_application.ApplicationHTTPTests.tearDown
    request=test_application.ApplicationHTTPTests.request

    def test_http_replan_adopt_and_current_remaining_export(self):
        t=self.db.create('HTTP剩余',application.sample());t=self.db.solve(t['id'],t['revision'])
        t=self.db.adopt(t['id'],t['revision'],t['plans'][-1]['id'],True)
        vid,r=next(iter(execution.view(t)['routes'].items()))
        t=self.db.receipt(t['id'],t['revision'],dict(kind='stop',vehicle=vid,stop_id=r['next_stop'],
            finish=600,pickup=dict(cartons=20,totes=3),source='simulated'),True)
        status,_,raw=self.request('/api/application/replan',dict(id=t['id'],revision=t['revision']))
        self.assertEqual(status,200);t=json.loads(raw)['task']
        status,_,raw=self.request('/api/application/adopt-remaining',dict(id=t['id'],revision=t['revision'],
            candidate_id=t['remaining_plans'][-1]['id'],reviewed=True))
        self.assertEqual(status,200);t=json.loads(raw)['task']
        status,headers,raw=self.request(f'/api/application/export?id={t["id"]}&revision={t["revision"]}&format=remaining')
        self.assertEqual(status,200);self.assertIn('remaining.csv',headers['Content-Disposition'])
        rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
        self.assertNotIn('STORE_001',[row['门店'] for row in rows])
