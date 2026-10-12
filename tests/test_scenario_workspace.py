from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
from urllib import request, error
import continuous
import loading
import scenario_demo
import scenario_workspace
import server
from store import Conflict


class InteractiveScenarioTests(unittest.TestCase):
    def setUp(self):
        self.ws = scenario_workspace.Workspace()
        self.addCleanup(self.ws.close)

    def begin(self, mode='fixed', payload=None):
        self.result = self.ws.start(dict(input=payload or loading.sample(), mode=mode))
        return self.result['scene']

    def identity(self):
        s = self.result['scene']
        return dict(id=s['id'], revision=s['revision'])

    def act(self, action, **extra):
        self.result = self.ws.act(dict(**self.identity(), action=action, **extra))
        return self.result['scene']

    def window(self):
        return dict(type='time_window_change',time='08:00',store='SHOP_A',
                    open='09:20',due='10:00',close='11:30')

    def urgent(self):
        return dict(type='urgent_order',time='08:00',store='SHOP_A',
                    pickup_location='warehouse',due='11:00',close='12:00',
                    lines=[dict(sku='NEW',quantity=1,unit_weight_kg=1,unit_volume_m3=.001)])

    def test_fixed_draft_candidate_adoption_receipts_no_repacking(self):
        s=self.begin();frozen=deepcopy(s['live_plan']['fixed_loading'])
        draft=self.window()
        with patch('loading.constructive',side_effect=AssertionError('repacking')):
            check=self.ws.draft(dict(**self.identity(),draft=draft))
            self.assertTrue(check['ready']);self.assertEqual(self.result['scene'],s)
            with self.assertRaises(ValueError):self.act('event',draft=draft)
            candidate=self.act('event',draft=draft,reviewed=True)
            self.assertEqual(candidate['active'],1)
            with self.assertRaises(ValueError):self.act('adopt',candidate=candidate['candidate'])
            self.act('adopt',candidate=candidate['candidate'],reviewed=True)
            self.assertEqual(self.result['scene']['active'],2)
            while any(q['remaining'] for q in self.result['scene']['states'].values()):
                s=self.result['scene'];vid=next(v for v,q in s['states'].items() if q['remaining'])
                oid=s['states'][vid]['remaining'][0]
                stop=next(x for x in s['live_plan']['routes'][vid]['stops'] if x['id']==oid)
                o=next(o for o in s['live_plan']['data']['orders'] if o['id']==oid)
                t=max(s['clock'],stop['arrival'])
                for kind,time in [('arrived',t),('unloading',max(t,o['open'])),
                                  ('signed',max(t,o['open'])+o['service_minutes'])]:
                    self.act('receipt',vehicle=vid,order=oid,receipt=kind,time=time,source='manual_simulation')
                    self.assertEqual(self.result['scene']['live_plan']['fixed_loading'],frozen)
            self.assertEqual(len(self.result['scene']['receipts']),6)
            self.assertTrue(all(x=='delivered' for x in self.result['scene']['cargo'].values()))
            self.assertTrue(all(g['boxes']==0 for g in self.result['geometry'].values()))

    def test_two_csv_urgent_batches_are_warehouse_owned_until_pickup(self):
        self.begin('csv')
        for _ in range(2):
            s=self.act('event',draft=self.urgent(),reviewed=True)
            s=self.act('adopt',candidate=s['candidate'],reviewed=True)
        s=self.result['scene'];new=[o for o in s['live_plan']['data']['orders'] if o.get('store')]
        self.assertEqual(len(new),2)
        self.assertTrue(all(s['cargo'][o['id']]=='warehouse' for o in new))
        self.assertEqual(self.result['geometry'],{})
        with self.assertRaises(ValueError):self.act('event',draft=self.urgent(),reviewed=True)

    def test_fixed_new_cargo_rejected_without_state_change(self):
        before=deepcopy(self.begin())
        with self.assertRaisesRegex(ValueError,'新货|紧急'):
            self.act('event',draft=self.urgent(),reviewed=True)
        current=self.ws.act(dict(**self.identity(),action='refresh'))
        self.assertEqual(current['scene'],before)

    def test_stale_candidate_and_ai_race_rejected_refresh_recovers(self):
        self.begin();s=self.act('event',draft=self.window(),reviewed=True)
        stale=self.identity();cid=s['candidate'];self.act('advance',time=490)
        with self.assertRaises(Conflict):self.ws.act(dict(**stale,action='adopt',candidate=cid,reviewed=True))
        fresh=self.ws.act(dict(**stale,action='refresh'))
        self.assertIsNone(fresh['scene']['candidate'])
        self.assertEqual(fresh,self.result)
        identity=self.identity()
        def raced(*args):
            self.act('advance',time=491)
            return {'ready':True}
        with patch('csv_ai_events.interpret',side_effect=raced):
            with self.assertRaises(Conflict):self.ws.interpret(dict(**identity,message='构造消息'))

    def test_ai_missing_fields_then_supplement_does_not_save_candidate(self):
        before=deepcopy(self.begin());draft=self.window();draft['close']=None
        with patch('ai_events.call_api',return_value=draft):
            answer=self.ws.interpret(dict(**self.identity(),message='构造改窗消息'))
        self.assertFalse(answer['ready']);self.assertTrue(answer['questions'])
        with patch('ai_events.call_api',return_value=self.window()):
            answer=self.ws.interpret(dict(**self.identity(),message='截止11:30',context=answer['context']))
        self.assertTrue(answer['ready'])
        self.assertEqual(self.ws.act(dict(**self.identity(),action='refresh'))['scene'],before)

    def test_tab_isolation_expiry_and_limit(self):
        one=self.begin();two=self.begin()
        self.assertNotEqual(one['id'],two['id'])
        self.ws.limit=2
        with self.assertRaises(ValueError):self.begin()
        self.ws.entries[one['id']]['touched']=-1e20
        with self.assertRaises(Conflict):self.ws.act(dict(id=one['id'],revision=1,action='refresh'))
        self.assertEqual(self.ws.act(dict(id=two['id'],revision=1,action='refresh'))['scene'],two)

    def test_http_interactive_actions_do_not_write_historical_database(self):
        with TemporaryDirectory() as tmp:
            history=continuous.DispatchStore(Path(tmp)/'history.sqlite3')
            original=history.act('start',dict(csv_files=scenario_demo.sample()['csv_files']))
            with patch.object(server,'dispatch',history),patch.object(server,'workspace',self.ws):
                http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
                thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
                def post(operation,body):
                    req=request.Request(f'http://127.0.0.1:{http.server_port}/api/scenario/live/'+operation,
                        data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
                    with request.urlopen(req) as response:return json.load(response)
                try:
                    response=post('start',dict(input=loading.sample(),mode='fixed'))
                    self.result=response
                    response=post('action',dict(**self.identity(),action='event',draft=self.window(),reviewed=True))
                    self.result=response
                    post('action',dict(**self.identity(),action='adopt',candidate=response['scene']['candidate'],reviewed=True))
                    self.assertEqual(history.read(),original)
                    with self.assertRaises(error.HTTPError) as caught:post('start',{})
                    self.assertEqual(caught.exception.code,400);caught.exception.close()
                finally:http.shutdown();http.server_close();thread.join()
