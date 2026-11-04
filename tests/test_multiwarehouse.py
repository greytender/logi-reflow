from copy import deepcopy
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

import multiwarehouse as mw
import server
from store import Conflict


class MultiWarehouseTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'multi.db'
        self.store = mw.MultiWarehouseStore(self.path)
        self.task = self.store.create('双仓批次', mw.sample())

    def tearDown(self):
        self.temp.cleanup()

    def solve(self):
        return self.store.solve(self.task['id'], self.task['revision'])

    def adopt(self):
        t = self.solve()
        return self.store.adopt(t['id'], t['revision'], t['plans'][-1]['id'], True)

    def test_two_warehouses_complete_execution_restart_and_export(self):
        task = self.adopt()
        p = task['plans'][-1]
        self.assertEqual(p['summary']['optimized']['served'], 6)
        self.assertEqual(p['summary']['optimized']['cost'], 370)
        self.assertTrue(p['audit']['passed'])
        self.assertEqual(len(self.store.export_csv(task['id'], task['revision']).splitlines()), 7)
        for wid in ('WH_1', 'WH_2'):
            while not mw.view(task)['execution'][wid]['complete']:
                state = mw.view(task)['execution'][wid]
                vid, r = next((v, r) for v, r in state['routes'].items() if not r['returned'])
                stop = r['remaining'][0] if r['remaining'] else None
                record = dict(vehicle=vid, kind='stop' if stop else 'return',
                    stop_id=stop['id'] if stop else None,
                    finish=int(stop['finish'] if stop else r['estimated_return'])+1,
                    pickup=dict(cartons=20, totes=3) if stop else {}, source='simulated', note='QA')
                task = self.store.action(task['id'], task['revision'], wid, 'receipt', record, True)
        self.assertTrue(mw.view(task)['complete'])
        self.assertEqual(mw.view(task)['receipts'], 8)
        self.assertEqual(mw.MultiWarehouseStore(self.path).read(task['id']), task)
        package = self.store.export_package(task['id'], task['revision'])
        self.assertEqual(package['dispatch_status'], 'not_sent')
        self.assertEqual(package['integration_status'], 'not_connected')

    def test_invalid_and_missing_allocations_never_silently_split(self):
        for field, change in [('orders', 'missing'), ('vehicles', 'duplicate'), ('orders', 'duplicate')]:
            d = deepcopy(self.task['data'])
            if change == 'missing': del d[field][0]['warehouse_id']
            else: d[field][-1]['id'] = d[field][0]['id']
            with self.assertRaises(ValueError): self.store.create('错误', d)
        d = deepcopy(self.task['data']); d['orders'][0]['allowed_vehicles'] = ['WH_2_TRUCK_1']
        with self.assertRaisesRegex(ValueError, '未知'): self.store.create('跨仓借车', d)
        d = deepcopy(self.task['data']); del d['legs'][next(iter(d['legs']))]
        with self.assertRaisesRegex(ValueError, '缺方向'): self.store.create('缺道路', d)
        d = deepcopy(self.task['data']); d['orders'][0]['enterprise_order_id'] = 'source-123'
        with self.assertRaisesRegex(ValueError, '未映射'): self.store.create('未知字段', d)

    def test_partial_is_preserved_and_batch_confirmation_is_atomic(self):
        d = deepcopy(self.task['data']); d['orders'][-1]['lines'][0]['quantity'] = 1000
        t = self.store.create('南仓超容', d); t = self.store.solve(t['id'], t['revision'])
        self.assertEqual(t['plans'][-1]['summary']['optimized']['unserved'], 1)
        with self.assertRaisesRegex(ValueError, '未安排'):
            self.store.adopt(t['id'], t['revision'], t['plans'][-1]['id'], True)
        saved = self.store.read(t['id'])
        self.assertFalse(saved['execution_tasks'])
        self.assertIsNone(saved['adopted_plan_id'])
        self.assertEqual(len(saved['plans']), 1)

    def test_manual_reorder_is_audited_and_requires_fresh_confirmation(self):
        t = self.adopt(); plan = t['plans'][-1]
        sequences = {wid: {vid: list(reversed(r['sequence'])) for vid, r in p['optimized']['routes'].items()}
                     for wid, p in plan['warehouses'].items()}
        t = self.store.manual(t['id'], t['revision'], sequences)
        self.assertIsNone(t['adopted_plan_id'])
        self.assertTrue(t['plans'][-1]['audit']['passed'])
        self.assertEqual(t['plans'][-1]['method'], 'manual_reorder')
        self.assertEqual(t['plans'][-1]['summary']['optimized']['volume_utilization'], .822857)
        bad = deepcopy(sequences); seq = next(s for s in bad['WH_1'].values() if s); seq.append(seq[0])
        with self.assertRaisesRegex(ValueError, '重复'): self.store.manual(t['id'], t['revision'], bad)
        bad = deepcopy(sequences); seq = next(s for s in bad['WH_1'].values() if s); seq[0] = 'WH_2_S01'
        with self.assertRaisesRegex(ValueError, '跨仓'): self.store.manual(t['id'], t['revision'], bad)

    def test_stale_revisions_input_changes_and_receipt_locks(self):
        t = self.adopt(); p = t['adopted_plan_id']
        with self.assertRaises(Conflict): self.store.solve(t['id'], 1)
        d = deepcopy(t['data']); d['warehouses'][0]['business']['tariff']['base'] += 1
        changed = self.store.save(t['id'], t['revision'], t['name'], d)
        with self.assertRaises(Conflict): self.store.adopt(changed['id'], changed['revision'], p, True)
        with self.assertRaises(Conflict): self.store.export_csv(changed['id'], changed['revision'])
        self.task = changed; t = self.adopt()
        wid, state = next(iter(mw.view(t)['execution'].items())); vid, route = next(iter(state['routes'].items()))
        sid = route['next_stop']
        r = dict(vehicle=vid, kind='stop', stop_id=sid, finish=600,
                 pickup=dict(cartons=20, totes=3), source='simulated', note='QA')
        with self.assertRaises(ValueError): self.store.action(t['id'], t['revision'], wid, 'receipt', r, False)
        t = self.store.action(t['id'], t['revision'], wid, 'receipt', r, True)
        with self.assertRaisesRegex(Conflict, '执行'): self.store.solve(t['id'], t['revision'])
        with self.assertRaisesRegex(ValueError, '顺序'): self.store.action(t['id'], t['revision'], wid, 'receipt', r, True)
        d = deepcopy(t['data']); d['orders'][0]['lines'][0]['quantity'] += 1
        with self.assertRaisesRegex(Conflict, '事实'): self.store.save(t['id'], t['revision'], t['name'], d)

    def test_observation_invalidates_remaining_candidate_and_cannot_cross_warehouse(self):
        t = self.adopt()
        state = mw.view(t)['execution']['WH_1']; vid = next(iter(state['routes']))
        r = dict(vehicle=vid, kind='wait', node='WH_1', observed_at=530, source='simulated', note='排队等待')
        t = self.store.action(t['id'], t['revision'], 'WH_1', 'observe', r, True)
        t = self.store.action(t['id'], t['revision'], 'WH_1', 'replan', None)
        candidate = t['execution_tasks']['WH_1']['remaining_plans'][-1]['id']
        state = mw.view(t)['execution']['WH_1']; vid = next(iter(state['routes']))
        r = dict(vehicle=vid, kind='wait', node='WH_1', observed_at=540, source='simulated', note='排队等待')
        t = self.store.action(t['id'], t['revision'], 'WH_1', 'observe', r, True)
        with self.assertRaises(Conflict): self.store.action(t['id'], t['revision'], 'WH_1', 'adopt-remaining', candidate, True)
        with self.assertRaises(ValueError): self.store.action(t['id'], t['revision'], 'WH_2', 'observe', r, True)

    def test_http_flow_and_local_origin_guard(self):
        old = server.multiwarehouse_store; server.multiwarehouse_store = self.store
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = Thread(target=httpd.serve_forever, daemon=True); thread.start()
        port = httpd.server_port
        def request(path, body=None, origin=None):
            c = HTTPConnection('127.0.0.1', port)
            headers = {'Content-Type': 'application/json'}
            if origin: headers['Origin'] = origin
            c.request('GET' if body is None else 'POST', path, None if body is None else json.dumps(body), headers)
            r = c.getresponse(); status, raw = r.status, r.read(); c.close()
            return status, json.loads(raw)
        try:
            status, sample = request('/api/multiwarehouse/sample'); self.assertEqual(status, 200)
            status, result = request('/api/multiwarehouse/create', dict(name='HTTP', data=sample['data']))
            self.assertEqual(status, 200)
            task = result['task']
            status, result = request('/api/multiwarehouse/solve', dict(id=task['id'], revision=task['revision']))
            self.assertEqual(status, 200)
            self.assertEqual(result['task']['plans'][-1]['summary']['optimized']['served'], 6)
            solved = result['task']
            status, result = request('/api/multiwarehouse/agent', dict(id=solved['id'], revision=solved['revision'], workflow='compare'))
            self.assertEqual(status, 200)
            self.assertFalse(result['agent_run']['model_connected'])
            status, result = request('/api/multiwarehouse/agent-status'); self.assertEqual(status, 200)
            self.assertFalse(result['model_connected'])
            status, _ = request('/api/multiwarehouse/solve', dict(id=task['id'], revision=1)); self.assertEqual(status, 409)
            status, _ = request('/api/multiwarehouse/create', dict(name='bad', data=sample['data']), 'https://external.invalid')
            self.assertEqual(status, 403)
        finally:
            httpd.shutdown(); httpd.server_close(); thread.join(); server.multiwarehouse_store = old


if __name__ == '__main__': unittest.main()
