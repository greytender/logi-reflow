from copy import deepcopy
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch

import application
import server
from store import Conflict


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'application.sqlite3'
        self.store = application.ApplicationStore(self.path)
        self.task = self.store.create('小批次', application.sample())

    def tearDown(self):
        self.temp.cleanup()

    def solve(self, task=None):
        task = task or self.task
        return self.store.solve(task['id'], task['revision'])

    def test_real_calculation_confirmation_restart_and_export(self):
        task = self.solve()
        plan = task['plans'][-1]
        self.assertEqual(plan['result']['optimized']['metrics']['served'], 3)
        self.assertTrue(plan['result']['audit']['optimized']['passed'])
        self.assertIsNone(task['adopted_plan_id'])
        task = self.store.adopt(task['id'], task['revision'], plan['id'], True)
        restored = application.ApplicationStore(self.path).read(task['id'])
        self.assertEqual(restored, task)
        self.assertEqual(self.store.list()[0]['status'], '已确认方案')
        text = self.store.export_csv(task['id'], task['revision'])
        self.assertEqual(len(text.splitlines()), 4)
        for order in task['data']['orders']:
            self.assertIn(order['id'], text)
        self.assertIn('未派单', text)

    def test_changed_quantities_really_change_routes_and_cost(self):
        original = self.solve()
        data = deepcopy(original['data'])
        data['orders'][0]['lines'][0]['quantity'] = 28
        updated = self.store.save(original['id'], original['revision'], original['name'], data)
        updated = self.solve(updated)
        old, new = original['plans'][-1]['result'], updated['plans'][-1]['result']
        self.assertNotEqual(old['optimized']['metrics']['cost'], new['optimized']['metrics']['cost'])
        self.assertEqual(new['optimized']['metrics']['served'], 3)
        self.assertEqual(sum(bool(r['sequence']) for r in new['optimized']['routes'].values()), 2)
        self.assertTrue(new['audit']['optimized']['passed'])

    def test_partial_plan_retained_but_cannot_be_confirmed(self):
        data = deepcopy(self.task['data'])
        data['orders'][0]['lines'][0]['quantity'] = 1000
        task = self.store.save(self.task['id'], self.task['revision'], '超容', data)
        task = self.solve(task)
        self.assertEqual(task['plans'][-1]['result']['optimized']['metrics']['unserved'], 1)
        with self.assertRaisesRegex(ValueError, '未安排'):
            self.store.adopt(task['id'], task['revision'], task['plans'][-1]['id'], True)
        self.assertEqual(len(self.store.read(task['id'])['plans']), 1)

    def test_old_tab_and_old_input_cannot_adopt_even_after_reverting(self):
        task = self.solve()
        plan_id = task['plans'][-1]['id']
        with self.assertRaises(Conflict):
            self.store.adopt(task['id'], self.task['revision'], plan_id, True)
        original = deepcopy(task['data'])
        data = deepcopy(original)
        data['orders'][0]['lines'][0]['quantity'] += 1
        task = self.store.save(task['id'], task['revision'], task['name'], data)
        with self.assertRaises(Conflict):
            self.store.adopt(task['id'], task['revision'], plan_id, True)
        task = self.store.save(task['id'], task['revision'], task['name'], original)
        with self.assertRaises(Conflict):
            self.store.adopt(task['id'], task['revision'], plan_id, True)

    def test_review_required_and_stale_confirmed_export_rejected(self):
        task = self.solve()
        with self.assertRaisesRegex(ValueError, '核对'):
            self.store.adopt(task['id'], task['revision'], task['plans'][-1]['id'], False)
        task = self.store.adopt(task['id'], task['revision'], task['plans'][-1]['id'], True)
        data = deepcopy(task['data'])
        data['vehicles'][0]['fixed_cost'] += 1
        task = self.store.save(task['id'], task['revision'], task['name'], data)
        with self.assertRaises(Conflict):
            self.store.export_csv(task['id'], task['revision'])

    def test_no_change_save_preserves_input_version_and_tasks_isolated(self):
        task = self.solve()
        task = self.store.save(task['id'], task['revision'], '改个名称', task['data'])
        self.assertEqual(task['input_revision'], 1)
        self.store.adopt(task['id'], task['revision'], task['plans'][-1]['id'], True)
        other = self.store.create('另一任务', application.sample())
        self.assertEqual(len(self.store.list()), 2)
        self.assertEqual(other['plans'], [])
        self.assertIsNone(other['adopted_plan_id'])

    def test_bad_inputs_do_not_change_saved_task(self):
        for kind in ['missing_road', 'nan', 'duplicate_stop', 'negative_volume', 'unknown_vehicle']:
            data = deepcopy(self.task['data'])
            if kind == 'missing_road': data['legs'].pop(next(iter(data['legs'])))
            if kind == 'nan': data['orders'][0]['lines'][0]['quantity'] = float('nan')
            if kind == 'duplicate_stop': data['orders'].append(deepcopy(data['orders'][0]))
            if kind == 'negative_volume': data['orders'][0]['lines'][0]['unit_volume_m3'] = -1
            if kind == 'unknown_vehicle': data['orders'][0]['allowed_vehicles'] = ['UNKNOWN']
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.store.save(self.task['id'], self.task['revision'], self.task['name'], data)
        self.assertEqual(self.store.read(self.task['id']), self.task)

    def test_import_preserves_actual_sku_rows_and_rejects_wrong_bundle(self):
        files = {name: (application.ROOT / 'examples/continuous-small' / name).read_text(encoding='utf-8') for name in application.FILES}
        task = self.store.create('CSV导入', application.load_files(files))
        self.assertEqual(sum(len(o['lines']) for o in task['data']['orders']), 30)
        self.assertTrue(self.solve(task)['plans'][-1]['result']['audit']['optimized']['passed'])
        files.pop('travel.csv')
        with self.assertRaises(ValueError): application.load_files(files)


class ApplicationHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.db = application.ApplicationStore(Path(self.temp.name) / 'application.sqlite3')
        self.patch = patch.object(server, 'application_store', self.db)
        self.patch.start()
        class Handler(server.Handler):
            def log_message(self, *args): pass
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join()
        self.patch.stop(); self.temp.cleanup()

    def request(self, path, body=None, headers=None):
        connection = HTTPConnection('127.0.0.1', self.http.server_port, timeout=10)
        h = {'Content-Type': 'application/json', **(headers or {})}
        try:
            connection.request('POST' if body is not None else 'GET', path,
                json.dumps(body) if body is not None else None, h)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally: connection.close()

    def test_http_create_solve_adopt_export_and_new_home(self):
        status, _, page = self.request('/')
        self.assertEqual(status, 200); self.assertIn('href="/workspace.html"', page.decode())
        status, _, workbench = self.request('/workspace.html')
        self.assertEqual(status, 200); self.assertIn('工作台', workbench.decode())
        status, _, body = self.request('/api/application/create', {'name': '网页任务', 'data': application.sample()})
        self.assertEqual(status, 200); task = json.loads(body)['task']
        status, _, body = self.request('/api/application/solve', {'id': task['id'], 'revision': task['revision']})
        self.assertEqual(status, 200); task = json.loads(body)['task']
        status, _, body = self.request('/api/application/adopt', {'id': task['id'], 'revision': task['revision'], 'plan_id': task['plans'][-1]['id'], 'reviewed': True})
        self.assertEqual(status, 200); task = json.loads(body)['task']
        status, headers, body = self.request(f'/api/application/export?id={task["id"]}&revision={task["revision"]}&format=csv')
        self.assertEqual(status, 200); self.assertIn('attachment', headers['Content-Disposition'])
        self.assertIn('STORE_001', body.decode('utf-8-sig'))

    def test_cross_origin_writes_and_stale_exports_blocked(self):
        status, _, _ = self.request('/api/application/create', {'name': '不应创建', 'data': application.sample()}, {'Origin': 'https://unrelated.example'})
        self.assertEqual(status, 403); self.assertEqual(self.db.list(), [])
        task = self.db.create('正常任务', application.sample())
        self.db.save(task['id'], task['revision'], '新名称', task['data'])
        status, _, _ = self.request(f'/api/application/export?id={task["id"]}&revision={task["revision"]}')
        self.assertEqual(status, 409)


if __name__ == '__main__':
    unittest.main()
