import json
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError
import ai_events
import normal


class AITests(unittest.TestCase):
    def test_missing_fields_are_questions_not_defaults(self):
        r=ai_events.validate_draft(dict(type='vehicle_failure',vehicle='V2'),normal.defaults())
        self.assertFalse(r['ready']);self.assertNotIn('event',r);self.assertIn('发生时间',r['questions'][0])
        r=ai_events.validate_draft(dict(type='unknown'),normal.defaults());self.assertFalse(r['ready'])

    def test_valid_events_and_malformed_rejected(self):
        data=normal.defaults()
        urgent=dict(type='urgent_order',time='08:25',store='S07',quantity=10,due='09:00',close='11:30')
        r=ai_events.validate_draft(urgent,data);self.assertEqual(r['event']['time'],505)
        self.assertTrue(ai_events.validate_draft(dict(type='vehicle_failure',time='08:25',vehicle='V2'),data)['ready'])
        self.assertTrue(ai_events.validate_draft(dict(type='time_window_change',time='08:25',store='S08',open='09:15',due='09:30',close='11:30'),data)['ready'])
        for changes in (dict(routes=[]),dict(quantity=True),dict(quantity=1.2),dict(time=505),dict(time='09:25'),dict(time='08:99'),dict(close='08:30'),dict(store='S99'),dict(type=[])):
            with self.subTest(changes=changes),self.assertRaises(ValueError):ai_events.validate_draft({**urgent,**changes},data)
        for raw in ('{"type":"unknown","type":"urgent_order"}','{"quantity":NaN}'):
            with self.assertRaises(ValueError):ai_events.strict_json(raw)

    def test_missing_key_never_calls_network(self):
        with patch.dict('os.environ',{},clear=True),patch('urllib.request.urlopen') as call:
            with self.assertRaises(ai_events.AIError):ai_events.interpret(normal.defaults(),'V2故障')
            call.assert_not_called();self.assertFalse(ai_events.status()['configured'])

    def test_mock_transport_and_followup_context(self):
        response=MagicMock();response.__enter__.return_value=response
        response.read.return_value=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'{"type":"vehicle_failure","time":"08:25","vehicle":"V2"}'}}]}).encode()
        with patch.dict('os.environ',{'LOGISTICS_DEEPSEEK_API_KEY':'unit-test-only'},clear=True),patch('urllib.request.urlopen',return_value=response) as call:
            r=ai_events.interpret(normal.defaults(),'08:25',['V2故障']);self.assertTrue(r['ready'])
            req=call.call_args.args[0];sent=json.loads(req.data)
            self.assertEqual(sent['model'],'deepseek-flash');self.assertEqual(sent['thinking'],{'type':'disabled'})
            self.assertEqual(sent['response_format'],{'type':'json_object'})
            self.assertEqual([x['content'] for x in sent['messages'][1:]],['V2故障','08:25'])
            self.assertNotIn('orders',sent);self.assertNotIn('unit-test-only',json.dumps(r))
            self.assertEqual(call.call_args.kwargs['timeout'],12)

    def test_failures_are_sanitized_and_not_retried(self):
        for error in (HTTPError(ai_events.ENDPOINT,401,'secret-detail',None,None),HTTPError(ai_events.ENDPOINT,429,'secret-detail',None,None),TimeoutError('secret-detail'),URLError('secret-detail')):
            with patch.dict('os.environ',{'LOGISTICS_DEEPSEEK_API_KEY':'unit-test-only'},clear=True),patch('urllib.request.urlopen',side_effect=error) as call:
                with self.assertRaises(ai_events.AIError) as ctx:ai_events.call_api([])
                self.assertNotIn('secret-detail',str(ctx.exception));self.assertNotIn('unit-test-only',str(ctx.exception));self.assertEqual(call.call_count,1)

    def test_empty_truncated_bad_json_and_bad_context(self):
        for payload in ({'choices':[{'finish_reason':'length','message':{'content':'{}'}}]},
                        {'choices':[{'finish_reason':'stop','message':{'content':''}}]},
                        {'choices':[{'finish_reason':'stop','message':{'content':'not json'}}]},{}):
            response=MagicMock();response.__enter__.return_value=response;response.read.return_value=json.dumps(payload).encode()
            with patch.dict('os.environ',{'LOGISTICS_DEEPSEEK_API_KEY':'unit-test-only'},clear=True),patch('urllib.request.urlopen',return_value=response):
                with self.assertRaises(ai_events.AIError):ai_events.call_api([])
        with patch('ai_events.call_api',return_value={'type':'urgent_order','routes':[]}):
            with self.assertRaises(ai_events.AIError):ai_events.interpret(normal.defaults(),'追加一单')
        for context in ([1],['x']*5,'bad'):
            with self.assertRaises(ValueError):ai_events.interpret(normal.defaults(),'V2故障',context)


if __name__=='__main__':unittest.main()
