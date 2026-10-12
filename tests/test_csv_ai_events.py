from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch, MagicMock
from urllib import request, error
from http.server import ThreadingHTTPServer

import ai_events
import csv_ai_events as cai
import continuous
import matrix_dispatch as md
import server
from store import Store
from verify_matrix_dispatch import audit

ROOT=Path(__file__).resolve().parents[1]


def urgent_draft():
    return dict(type='urgent_order',time='07:00',store='STORE_002',pickup_location='warehouse',
                due='13:00',close='16:00',lines=[dict(sku='NEW_A',quantity=2,unit_weight_kg=7,unit_volume_m3=.03)])


def window_draft():
    return dict(type='time_window_change',time='07:00',store='STORE_002',open='10:00',due='11:00',close='16:00')


class CSVAIEventsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=continuous.DispatchStore(Path(self.tmp.name)/'scene.sqlite3')
        files={name:(ROOT/'examples/continuous-small'/name).read_text(encoding='utf-8') for name in md.FILES}
        self.scene=self.store.act('start',dict(csv_files=files))

    def act(self,action,**body):
        self.scene=self.store.act(action,dict(id=self.scene['id'],revision=self.scene['revision'],**body))
        audit(self.scene)
        return self.scene

    def validate(self,draft):return cai.validate_draft(draft,self.scene)

    def test_actual_csv_identifiers_before_eight_and_multi_sku(self):
        d=urgent_draft();d['lines'].append(dict(sku='NEW_B',quantity=3,unit_weight_kg=9,unit_volume_m3=.05))
        r=self.validate(d);self.assertEqual(r['event']['time'],420)
        self.assertNotIn('pickup_location',r['event']);self.assertEqual(len(r['event']['lines']),2)
        self.assertTrue(self.validate(window_draft())['ready'])

    def test_partial_lines_and_times_ask_without_defaults(self):
        d=urgent_draft();d['time']=None;d['lines'][0]['unit_weight_kg']=None;d['lines'][0]['unit_volume_m3']=None
        r=self.validate(d);self.assertFalse(r['ready']);self.assertNotIn('event',r)
        self.assertIn('第1行每箱重量',r['questions'][0]);self.assertIsNone(r['draft']['time'])
        for field in ('pickup_location','lines'):
            d=urgent_draft();d[field]=None;self.assertFalse(self.validate(d)['ready'])

    def test_field_schema_unknown_destinations_and_bad_numbers(self):
        for changes in [dict(store='S07'),dict(store='E01'),dict(time='06:59'),dict(time=420),
                        dict(time='24:00'),dict(pickup_location='vehicle'),dict(quantity=10),
                        dict(type='vehicle_failure'),dict(type=[]),dict(routes=[])]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.validate({**urgent_draft(),**changes})
        for key,value in [('quantity',True),('quantity',1.2),('unit_weight_kg',0),('unit_volume_m3',float('nan')),('sku',[]),('sku','X:Y')]:
            d=urgent_draft();d['lines'][0][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):self.validate(d)
        d=urgent_draft();d['lines']*=2
        with self.assertRaisesRegex(ValueError,'重复'):self.validate(d)
        d=urgent_draft();d['lines'][0]['total_weight']=14
        with self.assertRaises(ValueError):self.validate(d)

    def test_contradictory_times_and_unknown_do_not_execute(self):
        d=window_draft();d['close']='09:00'
        with self.assertRaises(ValueError):self.validate(d)
        d=urgent_draft();d['close']='12:00'
        with self.assertRaises(ValueError):self.validate(d)
        self.assertFalse(self.validate(dict(type='unknown'))['ready'])
        with self.assertRaises(ValueError):self.validate(dict(type='unknown',time=None))

    def test_signed_and_unloading_facts_block_bad_window(self):
        stop=self.scene['live_plan']['routes']['TRUCK_1']['stops'][0];oid=stop['id'];t=stop['arrival']
        self.act('receipt',vehicle='TRUCK_1',order=oid,receipt='arrived',time=t,source='manual_simulation')
        self.act('receipt',vehicle='TRUCK_1',order=oid,receipt='unloading',time=t,source='manual_simulation')
        d=dict(type='time_window_change',time='07:03',store=oid,open='08:00',due='11:00',close='16:00')
        with self.assertRaisesRegex(ValueError,'已开始卸货'):self.validate(d)
        self.act('receipt',vehicle='TRUCK_1',order=oid,receipt='signed',time=t+11,source='manual_simulation')
        d.update(time='07:14',open='07:00')
        with self.assertRaisesRegex(ValueError,'已交付'):self.validate(d)

    def test_two_order_limit_and_urgent_order_is_not_destination(self):
        r=self.validate(urgent_draft());event={k:v for k,v in r['event'].items() if k!='type'}
        self.act('event',kind='urgent',event=event);self.act('adopt',candidate=self.scene['candidate'])
        d=window_draft();d['store']='E01';self.assertTrue(self.validate(d)['ready'])
        d=urgent_draft();d['store']='E01'
        with self.assertRaises(ValueError):self.validate(d)
        self.act('event',kind='urgent',event=event);self.act('adopt',candidate=self.scene['candidate'])
        with self.assertRaisesRegex(ValueError,'最多两个'):self.validate(urgent_draft())

    def test_mock_followup_sends_only_identifier_reference(self):
        before=self.store.read()
        def respond(messages,metadata,max_tokens):
            metadata.update(elapsed_ms=12,usage=dict(total_tokens=123))
            self.assertEqual(max_tokens,4096)
            system=messages[0]['content'];self.assertIn('STORE_002',system)
            for forbidden in ('travel_minutes','max_weight_kg','SKU_001','TRUCK_1','unit-test-only-key'):
                self.assertNotIn(forbidden,system)
            self.assertEqual([m['content'] for m in messages[1:]],['07:00追加一单','补充明细'])
            return urgent_draft()
        with patch('ai_events.call_api',side_effect=respond):
            result=cai.interpret(self.scene,'补充明细',['07:00追加一单'])
        self.assertEqual(result['scene_revision'],before['revision']);self.assertEqual(result['usage']['total_tokens'],123)
        self.assertEqual(before,self.store.read())

    def test_missing_credential_and_invalid_context_do_not_use_network(self):
        with patch.dict('os.environ',{},clear=True),patch('urllib.request.urlopen') as call:
            with self.assertRaises(ai_events.AIError):cai.interpret(self.scene,'一单')
            call.assert_not_called()
        with patch('ai_events.call_api') as call:
            for context in ([1],['x']*5,'bad'):
                with self.assertRaises(ValueError):cai.interpret(self.scene,'消息',context)
            call.assert_not_called()

    def test_transport_usage_sanitized_and_no_credential_echo(self):
        response=MagicMock();response.__enter__.return_value=response
        response.read.return_value=json.dumps(dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps(urgent_draft())))],
                                                  usage=dict(total_tokens=222,prompt_tokens=100,secret='unit-test-only-key'))).encode()
        with patch.dict('os.environ',{'LOGISTICS_DEEPSEEK_API_KEY':'unit-test-only-key'},clear=True),patch('urllib.request.urlopen',return_value=response):
            r=cai.interpret(self.scene,'明确的测试消息')
        self.assertEqual(r['usage'],dict(total_tokens=222,prompt_tokens=100))
        self.assertNotIn('unit-test-only-key',json.dumps(r));self.assertGreaterEqual(r['elapsed_ms'],0)

    def test_rejected_draft_keeps_sanitized_usage_for_cost_review(self):
        def respond(messages,metadata,max_tokens):
            metadata.update(elapsed_ms=10,usage=dict(total_tokens=222))
            return {**urgent_draft(),'store':'UNKNOWN'}
        with patch('ai_events.call_api',side_effect=respond),self.assertRaises(ai_events.AIError) as caught:
            cai.interpret(self.scene,'测试不合法编号')
        self.assertEqual(caught.exception.metadata['usage']['total_tokens'],222)


class CSVAIHTTPTests(unittest.TestCase):
    act=CSVAIEventsTests.act
    def setUp(self):
        CSVAIEventsTests.setUp(self)
        self.old_store,self.old_dispatch=server.store,server.dispatch
        server.store=Store(self.store.path);server.dispatch=self.store
        self.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close_http)

    def close_http(self):
        self.http.shutdown();self.http.server_close();self.thread.join()
        server.store,server.dispatch=self.old_store,self.old_dispatch

    def post(self,body):
        req=request.Request(f'http://127.0.0.1:{self.http.server_port}/api/dispatch/interpret',
                            data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
        try:
            with request.urlopen(req,timeout=5) as response:return response.status,json.load(response)
        except error.HTTPError as e:
            with e:return e.code,json.load(e)

    def test_http_stale_rejected_before_ai_and_during_ai(self):
        body=dict(id=self.scene['id'],revision=self.scene['revision'],message='测试')
        with patch('csv_ai_events.interpret') as call:
            code,_=self.post({**body,'revision':0});self.assertEqual(code,409);call.assert_not_called()
        def slow(scene,message,context):
            r=cai.validate_draft(window_draft(),scene)
            self.store.act('advance',dict(id=scene['id'],revision=scene['revision'],time=421))
            return r
        with patch('csv_ai_events.interpret',side_effect=slow):
            code,result=self.post(body);self.assertEqual(code,409);self.assertNotIn('event',result)

    def test_http_interpret_only_then_explicit_event_and_adopt(self):
        before=self.store.read();body=dict(id=self.scene['id'],revision=self.scene['revision'],message='07:00追加一单')
        with patch('ai_events.call_api',return_value=urgent_draft()):code,result=self.post(body)
        self.assertEqual(code,200);self.assertEqual(before,self.store.read());self.assertTrue(result['ready'])
        event={k:v for k,v in result['event'].items() if k!='type'}
        self.act('event',kind='urgent',event=event)
        self.assertEqual(self.scene['active'],1);self.assertNotIn('E01',self.scene['cargo'])
        self.act('adopt',candidate=self.scene['candidate']);self.assertEqual(self.scene['cargo']['E01'],'warehouse')


if __name__=='__main__':unittest.main()
