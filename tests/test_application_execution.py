from copy import deepcopy
import csv
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import application
import execution
from store import Conflict
import test_application


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name)/'tasks.sqlite3'
        self.store = application.ApplicationStore(self.path)
        t = self.store.create('回收演练', application.sample())
        t = self.store.solve(t['id'], t['revision'])
        self.task = self.store.adopt(t['id'], t['revision'], t['plans'][-1]['id'], True)
        self.vid = next(v for v, r in execution.view(self.task)['routes'].items() if r['next_stop'])

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, task=None, **changes):
        state = execution.view(task or self.task)['routes'][self.vid]
        result = dict(vehicle=self.vid, kind='stop', stop_id=state['next_stop'],
            finish=600, pickup=dict(cartons=20, totes=3), source='simulated', note='构造演练')
        result.update(changes)
        return result

    def record(self, task=None, **changes):
        t = task or self.task
        return self.store.receipt(t['id'], t['revision'], self.payload(t, **changes), True)

    def test_partial_receipt_keeps_plan_and_updates_only_remaining_forecast(self):
        original = deepcopy(self.task['plans'])
        t = self.record(pickup=dict(cartons=30, totes=5))
        state = execution.view(t)['routes'][self.vid]
        self.assertEqual(t['plans'], original)
        self.assertEqual(state['completed'], 1)
        self.assertEqual(len(state['remaining']), 2)
        self.assertNotIn(t['receipts'][0]['stop_id'], [s['id'] for s in state['remaining']])
        # Independent arithmetic: original goods 280kg/2.88m3, minus signed goods,
        # plus 30*.4 + 5*1.5 kg and 30*.003 + .06 + 4*.012 m3.
        order = next(o for o in t['data']['orders'] if o['id'] == t['receipts'][0]['stop_id'])
        ow = sum(l['quantity']*l['unit_weight_kg'] for l in order['lines'])
        ov = sum(l['quantity']*l['unit_volume_m3'] for l in order['lines'])
        self.assertAlmostEqual(state['onboard_weight_kg'], 280-ow+19.5)
        self.assertAlmostEqual(state['onboard_volume_m3'], 2.88-ov+.198)
        next_stop = state['next_stop']
        leg = t['data']['legs'][order['id']+'>'+next_stop]
        self.assertEqual(state['remaining'][0]['arrival'], 600+leg['travel_minutes'])
        self.assertEqual(self.store.list()[0]['status'], '执行记录中')
        self.assertEqual(application.ApplicationStore(self.path).read(t['id']), t)

    def test_overload_is_preserved_and_warned_instead_of_erasing_fact(self):
        t = self.record(pickup=dict(cartons=10000, totes=0))
        self.assertEqual(t['receipts'][0]['pickup']['cartons'], 10000)
        self.assertTrue(any('超重或超容' in w for w in t['receipts'][0]['warnings_after']))
        self.assertTrue(execution.view(t)['routes'][self.vid]['warnings'])

    def test_late_actual_completion_invalidates_future_time_forecast(self):
        t = self.record(finish=1100)
        warnings = execution.view(t)['routes'][self.vid]['warnings']
        self.assertTrue(any('收货时间' in w for w in warnings))
        self.assertTrue(any('收班' in w for w in warnings))

    def test_existing_facts_cannot_be_rewritten_or_reassigned(self):
        t = self.record()
        with self.assertRaises(Conflict): self.record()
        with self.assertRaises(ValueError): self.record(t, stop_id=t['receipts'][0]['stop_id'])
        with self.assertRaises(ValueError): self.record(t, finish=599)
        changed = deepcopy(t['data']); changed['orders'][0]['pickup']['cartons'] += 1
        with self.assertRaises(Conflict): self.store.save(t['id'], t['revision'], t['name'], changed)
        with self.assertRaises(Conflict): self.store.solve(t['id'], t['revision'])
        with self.assertRaises(Conflict): self.store.adopt(t['id'], t['revision'], t['adopted_plan_id'], True)
        self.assertEqual(self.store.read(t['id']), t)

    def test_bad_receipts_leave_database_unchanged(self):
        for changes in [dict(pickup={}), dict(pickup=dict(cartons=None, totes=0)),
                        dict(pickup=dict(cartons=True, totes=0)), dict(pickup=dict(cartons=1.5, totes=0)),
                        dict(pickup=dict(cartons=-1, totes=0)), dict(vehicle='NO'), dict(kind='delete'),
                        dict(source='manual'), dict(finish=float('nan')), dict(finish=True), dict(finish=0),
                        dict(kind='return'), dict(note='x'*501)]:
            with self.subTest(changes=changes), self.assertRaises(ValueError): self.record(**changes)
        with self.assertRaises(ValueError):
            self.store.receipt(self.task['id'], self.task['revision'], self.payload(), False)
        self.assertEqual(self.store.read(self.task['id']), self.task)

    def test_complete_requires_return_and_export_separates_forecasts(self):
        t = self.task
        while execution.view(t)['routes'][self.vid]['next_stop']:
            t = self.record(t)
        self.assertFalse(execution.view(t)['complete'])
        t = self.record(t, kind='return', pickup=None, finish=700)
        self.assertTrue(execution.view(t)['complete'])
        self.assertEqual(self.store.list()[0]['status'], '已回仓交接')
        with self.assertRaises(ValueError): self.record(t)
        rows = list(csv.DictReader(io.StringIO(self.store.export_receipts(t['id'], t['revision']).lstrip('\ufeff'))))
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[-1]['类型'], '回仓交接')
        self.assertTrue(all(r['来源']=='模拟演练' for r in rows))
        self.assertIn('预计纸箱数', self.store.export_csv(t['id'], t['revision']))

    def test_unconfirmed_task_and_missing_business_cannot_fake_pickups(self):
        raw = application.sample(); raw.pop('business')
        for o in raw['orders']: o.pop('pickup')
        t = self.store.create('旧任务', raw)
        with self.assertRaises(ValueError): self.store.receipt(t['id'], t['revision'], self.payload(), True)
        t = self.store.solve(t['id'], t['revision'])
        t = self.store.adopt(t['id'], t['revision'], t['plans'][-1]['id'], True)
        vid = next(v for v,r in execution.view(t)['routes'].items() if r['next_stop'])
        payload = dict(vehicle=vid, kind='stop', stop_id=execution.view(t)['routes'][vid]['next_stop'],
            finish=600, pickup=dict(cartons=1, totes=0), source='simulated')
        with self.assertRaises(ValueError): self.store.receipt(t['id'], t['revision'], payload, True)
        payload.pop('pickup')
        t = self.store.receipt(t['id'], t['revision'], payload, True)
        self.assertEqual(t['receipts'][0]['pickup'], {})

    def test_manual_source_requires_note_and_export_keeps_formula_as_text(self):
        raw=application.sample(); raw['settings']['data_kind']='enterprise'
        t=self.store.create('来源声明测试',raw)
        t=self.store.solve(t['id'],t['revision'])
        t=self.store.adopt(t['id'],t['revision'],t['plans'][-1]['id'],True)
        with self.assertRaises(ValueError): self.record(t,source='manual',note='')
        t=self.record(t,source='manual',note='=1+1')
        self.assertEqual(t['receipts'][0]['source'],'manual')
        self.assertIn("'=1+1",self.store.export_receipts(t['id'],t['revision']))

    def test_other_vehicle_receipts_do_not_change_this_vehicle(self):
        raw=application.sample();raw['orders'][0]['lines'][0]['quantity']=28
        t=self.store.create('两车隔离',raw)
        t=self.store.solve(t['id'],t['revision'])
        t=self.store.adopt(t['id'],t['revision'],t['plans'][-1]['id'],True)
        before=execution.view(t)['routes']
        self.assertEqual(len(before),2)
        first,other=list(before)
        self.vid=first
        t=self.record(t)
        self.assertEqual(execution.view(t)['routes'][other],before[other])


class ReceiptHTTPTests(unittest.TestCase):
    setUp = test_application.ApplicationHTTPTests.setUp
    tearDown = test_application.ApplicationHTTPTests.tearDown
    request = test_application.ApplicationHTTPTests.request
    def test_receipt_api_read_restart_export_conflict(self):
        import json
        t = self.db.create('HTTP回执', application.sample())
        t = self.db.solve(t['id'], t['revision'])
        t = self.db.adopt(t['id'], t['revision'], t['plans'][-1]['id'], True)
        vid, route = next(iter(execution.view(t)['routes'].items()))
        body = dict(id=t['id'], revision=t['revision'], reviewed=True, receipt=dict(
            vehicle=vid, kind='stop', stop_id=route['next_stop'], finish=600,
            pickup=dict(cartons=50,totes=2),source='simulated'))
        status, _, raw = self.request('/api/application/receipt', body)
        self.assertEqual(status, 200)
        result = json.loads(raw); self.assertEqual(result['execution']['routes'][vid]['completed'], 1)
        self.assertEqual(self.request('/api/application/receipt', body)[0], 409)
        saved = result['task']
        status, _, raw = self.request('/api/application/task?id='+t['id'])
        self.assertEqual(json.loads(raw)['task'], saved)
        status, headers, raw = self.request(f'/api/application/export?id={t["id"]}&revision={saved["revision"]}&format=receipts')
        self.assertEqual(status, 200); self.assertIn('receipts.csv', headers['Content-Disposition'])
        self.assertIn('模拟演练', raw.decode('utf-8-sig'))
