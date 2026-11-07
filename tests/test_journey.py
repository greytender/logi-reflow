"""Driving presentation must use the exact candidate and never write execution."""
from copy import deepcopy
from tempfile import TemporaryDirectory
from pathlib import Path
from threading import Thread
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
import unittest
import multiwarehouse as mw
import server
from store import Conflict


class JourneyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=TemporaryDirectory()
        cls.store=mw.MultiWarehouseStore(Path(cls.temp.name)/'journey.db')
        task=cls.store.create('全程示例',mw.operational_sample())
        cls.task=cls.store.solve(task['id'],task['revision'])
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_exact_route_frames_legs_and_read_only(self):
        before=deepcopy(self.task);p=mw.current_plan(self.task)
        result=mw.journey_view(self.task,'WH','TRUCK_1',p['id'])
        route=p['warehouses']['WH']['optimized']['routes']['TRUCK_1']
        expected=['WH',*route['sequence'],'WH']
        self.assertEqual([result['legs'][0]['from_id']]+[x['to_id'] for x in result['legs']],expected)
        self.assertEqual(result['map']['routes'][0]['route']['sequence'],route['sequence'])
        self.assertAlmostEqual(sum(x['distance_km'] for x in result['legs']),route['distance'])
        cargo=result['spatial']['scene']['routes'][0]
        self.assertEqual([f['index'] for f in cargo['frames']],list(range(len(cargo['frames']))))
        self.assertEqual(cargo['frames'][-1]['phase'],'return')
        self.assertTrue(cargo['frames'][-1]['boxes'])
        self.assertEqual(result['spatial']['display_names']['orders']['STORE_003'],'门店3')
        self.assertEqual(self.task,before)

    def test_stale_unknown_idle_and_missing_candidate(self):
        with self.assertRaises(Conflict):mw.journey_view(self.task,'WH','TRUCK_1','old')
        with self.assertRaises(ValueError):mw.journey_view(self.task,'WH','OTHER')
        with self.assertRaisesRegex(ValueError,'没有配送路线'):mw.journey_view(self.task,'WH','TRUCK_2')
        empty=deepcopy(self.task);empty['plans']=[]
        with self.assertRaises(ValueError):mw.journey_view(empty)

    def test_default_selects_used_vehicle_and_is_detached(self):
        result=mw.journey_view(self.task)
        self.assertEqual(result['vehicle_id'],'TRUCK_1')
        result['map']['routes'][0]['route']['sequence'].clear()
        result['spatial']['scene']['routes'][0]['frames'].clear()
        self.assertTrue(mw.current_plan(self.task)['warehouses']['WH']['optimized']['routes']['TRUCK_1']['sequence'])

    def test_departure_only_geometry_cannot_claim_a_complete_journey(self):
        task=self.store.create('出发装载示例',mw.sample(physical_loading=True))
        task=self.store.solve(task['id'],task['revision'])
        with self.assertRaisesRegex(ValueError,'完整流程需要装车和返仓步骤'):
            mw.journey_view(task)

    def test_http_context_and_public_navigation_keep_api_protected(self):
        previous=server.multiwarehouse_store;server.multiwarehouse_store=self.store
        self.addCleanup(setattr,server,'multiwarehouse_store',previous)
        http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread=Thread(target=http.serve_forever,daemon=True);thread.start()
        def finish():http.shutdown();http.server_close();thread.join()
        self.addCleanup(finish)
        def get(path,headers=None):
            c=HTTPConnection('127.0.0.1',http.server_port)
            c.request('GET',path,headers=headers or {});r=c.getresponse();body=r.read();c.close();return r.status,body
        path='/api/multiwarehouse/journey?id='+self.task['id']
        status,body=get(path);self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['plan_id'],mw.current_plan(self.task)['id'])
        nav={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'}
        self.assertEqual(get('/journey.html',nav)[0],200)
        self.assertEqual(get(path,nav)[0],403)
