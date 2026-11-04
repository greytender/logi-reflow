from copy import deepcopy
import csv
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import application
import execution
import observation
import remaining
from store import Conflict


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name)/'tasks.sqlite3'
        self.store = application.ApplicationStore(self.path)
        task = self.store.create('已知节点位置演练', application.sample())
        task = self.store.solve(task['id'], task['revision'])
        self.task = self.store.adopt(task['id'], task['revision'], task['plans'][-1]['id'], True)
        self.vid = next(iter(execution.view(self.task)['routes']))

    def tearDown(self):
        self.temp.cleanup()

    def observed(self, task=None, **changes):
        task = task or self.task
        payload = dict(vehicle=self.vid, kind='position', node='STORE_001',
            observed_at=600, source='simulated', note='构造观察')
        payload.update(changes)
        return self.store.observe(task['id'], task['revision'], payload, True)

    def received(self, task, **changes):
        route = execution.view(task)['routes'][self.vid]
        payload = dict(vehicle=self.vid, kind='stop', stop_id=route['next_stop'],
            finish=610, pickup=dict(cartons=30, totes=5), source='simulated', note='构造签收')
        payload.update(changes)
        return self.store.receipt(task['id'], task['revision'], payload, True)

    def test_position_anchor_changes_forecast_but_not_cargo_or_delivery(self):
        before = execution.view(self.task)['routes'][self.vid]
        t = self.observed()
        route = execution.view(t)['routes'][self.vid]
        self.assertEqual(route['anchor']['node'], 'STORE_001')
        self.assertEqual(route['anchor']['time'], 600)
        self.assertEqual(route['anchor']['kind'], 'observation')
        self.assertEqual(route['remaining'][0]['arrival'], 600)
        self.assertEqual(route['completed'], 0)
        self.assertEqual(route['next_stop'], 'STORE_001')
        self.assertEqual(route['onboard_weight_kg'], before['onboard_weight_kg'])
        self.assertEqual(route['onboard_volume_m3'], before['onboard_volume_m3'])
        self.assertEqual(t.get('receipts', []), [])
        self.assertEqual(t['plans'], self.task['plans'])
        self.assertEqual(self.store.list()[0]['status'], '执行记录中')
        self.assertEqual(application.ApplicationStore(self.path).read(t['id']), t)

    def test_wait_updates_time_at_same_node_and_independent_audit_matches(self):
        t = self.observed()
        t = self.observed(t, kind='wait', observed_at=630)
        r = execution.view(t)['routes'][self.vid]
        self.assertEqual(r['remaining'][0]['arrival'], 630)
        c = remaining.solve(t)
        check = c['audit']['checks'][0]
        self.assertEqual(check['return_at'], r['estimated_return'])
        self.assertEqual(check['distance_km'], r['remaining_distance_km'])
        self.assertEqual(c['observation_count'], 2)
        with self.assertRaises(ValueError): self.observed(t, kind='wait', node='WH', observed_at=640)

    def test_observation_after_receipt_preserves_collected_packaging_and_removes_self_leg(self):
        t = self.received(self.task)
        facts = deepcopy(t['receipts'])
        before = execution.view(t)['routes'][self.vid]
        t = self.observed(t, node='STORE_002', observed_at=650)
        r = execution.view(t)['routes'][self.vid]
        self.assertEqual(t['receipts'], facts)
        self.assertEqual(r['completed'], 1)
        self.assertEqual(r['collected_cartons'], 30)
        self.assertEqual(r['collected_totes'], 5)
        self.assertEqual(r['onboard_weight_kg'], before['onboard_weight_kg'])
        self.assertEqual(r['onboard_volume_m3'], before['onboard_volume_m3'])
        self.assertEqual(r['remaining'][0]['arrival'], 650)
        self.assertEqual(t['observations'][0]['vehicle_receipt_count'], 1)

    def test_new_observation_invalidates_candidate_with_latest_revision(self):
        t = self.observed()
        t = self.store.replan(t['id'], t['revision'])
        candidate = deepcopy(t['remaining_plans'][-1])
        t = self.observed(t, kind='wait', observed_at=610)
        with self.assertRaises(Conflict):
            self.store.adopt_remaining(t['id'], t['revision'], candidate['id'], True)
        self.assertEqual(t['remaining_plans'][-1], candidate)

    def test_receipt_cannot_precede_observation_and_supersedes_anchor_after_save(self):
        t = self.observed(observed_at=620)
        with self.assertRaises(ValueError): self.received(t, finish=619)
        t = self.received(t, finish=625)
        r = execution.view(t)['routes'][self.vid]
        self.assertEqual(r['anchor']['kind'], 'receipt')
        self.assertEqual(r['anchor']['time'], 625)
        self.assertEqual(r['remaining'][0]['arrival'], 632)
        with self.assertRaises(ValueError): self.observed(t, observed_at=624)

    def test_observation_only_locks_inputs_and_original_plan(self):
        t = self.observed()
        changed = deepcopy(t['data']); changed['orders'][0]['lines'][0]['quantity'] += 1
        with self.assertRaises(Conflict): self.store.save(t['id'], t['revision'], t['name'], changed)
        with self.assertRaises(Conflict): self.store.solve(t['id'], t['revision'])
        with self.assertRaises(Conflict): self.store.adopt(t['id'], t['revision'], t['adopted_plan_id'], True)
        t = self.store.replan(t['id'], t['revision'])
        self.assertTrue(t['remaining_plans'][-1]['audit']['passed'])

    def test_invalid_observations_do_not_modify_saved_task(self):
        for change in [dict(node='GPS:1,2'), dict(node='MISSING'), dict(vehicle='MISSING'),
                       dict(kind='arrival'), dict(observed_at=True), dict(observed_at=float('nan')),
                       dict(observed_at=-1), dict(observed_at=1439.5), dict(observed_at=0),
                       dict(source='manual'), dict(note='x'*501), dict(note=None)]:
            with self.subTest(change=change), self.assertRaises(ValueError): self.observed(**change)
        with self.assertRaises(ValueError):
            self.store.observe(self.task['id'], self.task['revision'], {}, False)
        self.assertEqual(self.store.read(self.task['id']), self.task)
        t = self.observed()
        with self.assertRaises(Conflict): self.observed(self.task)
        self.assertEqual(self.store.read(t['id']), t)

    def test_manual_source_requires_note_and_returned_vehicle_rejects_observation(self):
        raw=application.sample(); raw['settings']['data_kind']='enterprise'
        t=self.store.create('人工来源',raw);t=self.store.solve(t['id'],t['revision'])
        t=self.store.adopt(t['id'],t['revision'],t['plans'][-1]['id'],True)
        with self.assertRaises(ValueError):self.observed(t,source='manual',note='')
        t=self.observed(t,source='manual',note='司机消息编号 M001')
        while execution.view(t)['routes'][self.vid]['next_stop']:
            t=self.received(t,finish=700)
        t=self.received(t,kind='return',pickup=None,finish=730)
        with self.assertRaises(ValueError):self.observed(t,observed_at=740)
        self.assertEqual(execution.view(t)['routes'][self.vid]['anchor']['time'],730)
        self.assertEqual(execution.view(t)['routes'][self.vid]['remaining_distance_km'],0)

    def test_depot_anchor_without_return_does_not_remove_cargo(self):
        t=self.observed(node='WH')
        r=execution.view(t)['routes'][self.vid]
        self.assertEqual(r['onboard_weight_kg'],280)
        self.assertFalse(r['returned'])
        self.assertFalse(execution.view(t)['complete'])
        c=remaining.solve(t)
        self.assertTrue(c['audit']['passed'])

    def test_observation_time_cannot_cure_real_overload_or_lateness(self):
        t=self.received(self.task,pickup=dict(cartons=10000,totes=0))
        t=self.observed(t,observed_at=650)
        self.assertEqual(execution.view(t)['routes'][self.vid]['collected_cartons'],10000)
        c=remaining.solve(t)
        self.assertFalse(c['audit']['passed'])
        fresh=self.store.create('观察过晚反例',application.sample())
        fresh=self.store.solve(fresh['id'],fresh['revision'])
        fresh=self.store.adopt(fresh['id'],fresh['revision'],fresh['plans'][-1]['id'],True)
        t=self.observed(fresh,observed_at=1430)
        c=remaining.solve(t)
        self.assertFalse(c['audit']['passed'])
        self.assertTrue(any('最晚收货' in w for w in execution.view(t)['routes'][self.vid]['warnings']))

    def test_remaining_export_exposes_observation_provenance(self):
        t=self.observed(observed_at=630)
        rows=list(csv.DictReader(io.StringIO(self.store.export_remaining(t['id'],t['revision']).lstrip('\ufeff'))))
        self.assertTrue(rows)
        self.assertTrue(all(r['预测起算节点']=='STORE_001' and r['预测起算分钟']=='630' for r in rows))
        self.assertTrue(all(r['预测起算类型']=='observation' and r['预测起算来源']=='simulated' for r in rows))

    def test_old_candidate_stamp_without_observations_stays_compatible(self):
        expected=application.fingerprint(dict(data=self.task['data'],plan=self.task['adopted_plan_id'],
            receipts=self.task.get('receipts',[]),sequences={},active_remaining_plan_id=None))
        self.assertEqual(remaining.stamp(self.task),expected)
        with_empty={**self.task,'observations':[]}
        self.assertEqual(remaining.stamp(with_empty),expected)
        t=self.observed()
        self.assertNotEqual(remaining.stamp(t),expected)

    def test_other_vehicle_receipt_does_not_discard_observation_anchor(self):
        raw=application.sample();raw['orders'][0]['lines'][0]['quantity']=28
        t=self.store.create('两车观察隔离',raw);t=self.store.solve(t['id'],t['revision'])
        t=self.store.adopt(t['id'],t['revision'],t['plans'][-1]['id'],True)
        vehicles=list(execution.view(t)['routes'])
        self.assertEqual(len(vehicles),2)
        observed_vid,other_vid=vehicles
        t=self.store.observe(t['id'],t['revision'],dict(vehicle=observed_vid,kind='wait',node='WH',
            observed_at=600,source='simulated'),True)
        other_next=execution.view(t)['routes'][other_vid]['next_stop']
        t=self.store.receipt(t['id'],t['revision'],dict(vehicle=other_vid,kind='stop',stop_id=other_next,
            finish=610,pickup=dict(cartons=0,totes=0),source='simulated'),True)
        a=execution.view(t)['routes'][observed_vid]['anchor']
        self.assertEqual((a['kind'],a['node'],a['time']),('observation','WH',600))
