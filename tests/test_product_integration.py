from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep
import unittest
from unittest.mock import patch

import multiwarehouse as mw
import server


class Planner:
    key = 'sk-test-only-not-a-real-key'
    calls = []
    def plan(self, **kwargs): return ['dispatch', 'risks']
    def explain(self, **kwargs): return '已核验当前候选，请核对后确认。'


class ProductIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = mw.MultiWarehouseStore(Path(self.temp.name)/'tasks.db')
        self.state_patch = patch.object(server, 'multiwarehouse_store', self.store); self.state_patch.start()
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = Thread(target=self.http.serve_forever, daemon=True); self.thread.start()

    def tearDown(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join()
        self.state_patch.stop(); self.temp.cleanup()

    def request(self, path, body=None, headers=None):
        conn = HTTPConnection('127.0.0.1', self.http.server_port, timeout=10)
        conn.request('GET' if body is None else 'POST', path, None if body is None else json.dumps(body),
                     {'Content-Type': 'application/json', **(headers or {})})
        response = conn.getresponse(); status, raw = response.status, response.read(); conn.close()
        return status, raw

    def test_product_home_and_workbench_are_separate(self):
        status, raw = self.request('/api/product/status')
        self.assertEqual(status, 200); self.assertEqual(json.loads(raw)['workspace'], '/workspace.html')
        status, raw = self.request('/')
        self.assertEqual(status, 200); self.assertIn('href="/workspace.html"', raw.decode())
        self.assertNotIn('id="editor"', raw.decode())
        status, _ = self.request('/workspace.html'); self.assertEqual(status, 200)
        status, _ = self.request('/api/product/status', headers={'Origin': 'https://other.invalid'})
        self.assertEqual(status, 403)

    def test_async_agent_http_token_state_and_credential_scope(self):
        task = self.store.create('HTTP联调', mw.sample())
        with patch.object(server.deepseek_agent, 'planner', return_value=Planner()):
            status, raw = self.request('/api/multiwarehouse/agent/start',
                dict(id=task['id'], revision=task['revision'], message='排车并检查风险', request_id='first-request'))
        self.assertEqual(status, 200); started = json.loads(raw)
        job_id, token = started['job']['id'], started['job_token']
        path = '/api/multiwarehouse/agent/job?job_id='+job_id
        status, _ = self.request(path); self.assertEqual(status, 400)
        deadline = monotonic()+8
        while monotonic()<deadline:
            status, raw = self.request(path, headers={'X-Agent-Job-Token': token})
            self.assertEqual(status, 200); job = json.loads(raw)['job']
            if job['status'] not in ('queued', 'running', 'committing'): break
            sleep(.03)
        self.assertEqual(job['status'], 'completed', job)
        self.assertIsNone(job['result']['task']['adopted_plan_id'])
        self.assertTrue(job['result']['agent_run']['result']['compare']['audit']['passed'])
        kinds = {e['type'] for e in job['events']}
        self.assertTrue({'tool_started', 'tool_completed', 'completed'} <= kinds)
        self.assertNotIn('sk-test-only-not-a-real-key', json.dumps(job))
        status, raw = self.request(path+'&after='+str(job['cursor']), headers={'X-Agent-Job-Token': token})
        self.assertEqual(json.loads(raw)['job']['events'], [])

    def test_physical_loading_template_is_explicit(self):
        status, raw = self.request('/api/multiwarehouse/sample?physical=1')
        self.assertEqual(status, 200)
        data = json.loads(raw)['data']
        self.assertTrue(all('loading_profile' in w for w in data['warehouses']))


if __name__ == '__main__': unittest.main()
