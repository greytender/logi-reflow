from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
from urllib import request,error
import continuous
import loading_benchmark
import scenario_demo
import server


class WholeScenarioTests(unittest.TestCase):
    def test_whole_scene_reuses_geometry_receipts_and_keeps_inputs(self):
        payload=scenario_demo.sample();before=deepcopy(payload);result=scenario_demo.run(payload)
        self.assertEqual(payload,before);self.assertTrue(result['loading']['complete'])
        self.assertEqual(len(result['loading']['items']),12)
        replay=result['replay'];self.assertEqual(replay['signed_orders'],6)
        self.assertEqual(len(replay['receipts']),18)
        for k in ('returned','coordinates_unchanged','restart_equal'):self.assertTrue(replay[k])
        self.assertTrue(all(x['provenance']=='fixed_script_simulation' for x in replay['receipts']))
        self.assertFalse(result['ai_called']);self.assertTrue(result['local_isolated'])
        for row in result['vehicles']:
            self.assertLessEqual(row['weight_use_percent'],100)
            self.assertLessEqual(row['cuboid_space_use_percent'],100)
        self.assertEqual(sum(r['boxes'] for r in result['vehicles']),12)
        self.assertTrue(result['loading']['independent_route_audit']['passed'])

    def test_incomplete_loading_preserves_missing_orders_without_replay(self):
        result=scenario_demo.run(loading_benchmark.stack(True))
        self.assertFalse(result['loading']['complete']);self.assertIsNone(result['replay'])
        self.assertGreater(result['loading']['optimized']['metrics']['unserved'],0)
        self.assertIn('不完整',result['blocked'])

    def test_missing_or_extra_input_is_rejected(self):
        for payload in ({},None,dict(csv_files={},loading_profile={},scene_id='existing')):
            with self.assertRaises(ValueError):scenario_demo.run(payload)

    def test_http_whole_replay_never_mutates_current_scene(self):
        with TemporaryDirectory() as tmp:
            store=continuous.DispatchStore(Path(tmp)/'test.db')
            before=store.act('start',dict(csv_files=scenario_demo.sample()['csv_files']))
            with patch.object(server,'dispatch',store):
                http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
                worker=threading.Thread(target=http.serve_forever,daemon=True);worker.start()
                try:
                    url=f'http://127.0.0.1:{http.server_port}'
                    with request.urlopen(url+'/api/scenario/sample') as response:payload=json.load(response)
                    req=request.Request(url+'/api/scenario/run',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
                    with request.urlopen(req) as response:result=json.load(response)
                    self.assertEqual(result['replay']['signed_orders'],6)
                    self.assertEqual(store.read(),before)
                    bad=request.Request(url+'/api/scenario/run',data=b'{}',headers={'Content-Type':'application/json'})
                    with self.assertRaises(error.HTTPError) as raised:request.urlopen(bad)
                    self.assertEqual(raised.exception.code,400);raised.exception.close()
                    self.assertEqual(store.read(),before)
                finally:http.shutdown();http.server_close();worker.join()
