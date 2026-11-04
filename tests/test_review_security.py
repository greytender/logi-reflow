from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
import os
import threading
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
import ai_events
import evidence_cases
import loading
import scenario_workspace
import server
import session_keys


class KeyVaultTests(unittest.TestCase):
    def test_expiry_rotation_clear_and_no_global_environment_mutation(self):
        vault=session_keys.KeyVault()
        with patch('session_keys.monotonic',return_value=100):
            token=vault.put(None,'fake-test-key-alpha')
            cookie=session_keys.COOKIE+'='+token
            with vault.using(cookie):
                self.assertEqual(session_keys.current_key.get(),'fake-test-key-alpha')
            self.assertIsNone(session_keys.current_key.get())
            self.assertEqual(vault.put(cookie,'fake-test-key-beta'),token)
            self.assertEqual(vault.get(cookie),'fake-test-key-beta')
        with patch('session_keys.monotonic',return_value=3700):
            self.assertIsNone(vault.get(cookie))
        token=vault.put(None,'fake-test-key-gamma');cookie=session_keys.COOKIE+'='+token
        vault.clear(cookie);self.assertIsNone(vault.get(cookie))

    def test_request_context_isolated_between_concurrent_clients(self):
        vault=session_keys.KeyVault();barrier=threading.Barrier(2)
        keys=['fake-test-client-alpha','fake-test-client-beta']
        cookies=[session_keys.COOKIE+'='+vault.put(None,k) for k in keys]
        before=os.environ.get('LOGISTICS_DEEPSEEK_API_KEY')
        def run(index):
            with vault.using(cookies[index]):
                barrier.wait(timeout=5)
                return session_keys.current_key.get()
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(list(pool.map(run,range(2))),keys)
        self.assertIsNone(session_keys.current_key.get())
        self.assertEqual(os.environ.get('LOGISTICS_DEEPSEEK_API_KEY'),before)

    def test_invalid_keys_and_capacity_never_echo_input(self):
        vault=session_keys.KeyVault(limit=1)
        for value in (None,{},'short','fake-secret-with\nnewline','密钥不应被回显'*8,'a'*257):
            with self.assertRaises(ValueError) as caught:vault.put(None,value)
            if isinstance(value,str):self.assertNotIn(value,str(caught.exception))
        vault.put(None,'fake-test-valid-alpha')
        with self.assertRaises(ValueError):vault.put(None,'fake-test-valid-beta')


class KeyHttpTests(unittest.TestCase):
    def setUp(self):
        self.vault=session_keys.KeyVault();self.workspace=scenario_workspace.Workspace()
        self.addCleanup(self.workspace.close)
        for name,value in [('key_vault',self.vault),('workspace',self.workspace)]:
            handle=patch.object(server,name,value);handle.start();self.addCleanup(handle.stop)
        self.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.finish)

    def finish(self):
        self.http.shutdown();self.http.server_close();self.thread.join()

    def request(self,path,body=None,headers=None):
        connection=HTTPConnection('127.0.0.1',self.http.server_port)
        values={'Content-Type':'application/json',**(headers or {})}
        connection.request('GET' if body is None else 'POST',path,
            body=None if body is None else json.dumps(body),headers=values)
        response=connection.getresponse();raw=response.read()
        result=(response.status,dict(response.getheaders()),json.loads(raw))
        connection.close();return result

    def origin(self):return 'http://127.0.0.1:'+str(self.http.server_port)

    def test_cross_origin_missing_origin_and_dns_rebinding_blocked(self):
        for headers in ({},{'Origin':'https://untrusted.example'},
                        {'Origin':self.origin(),'Sec-Fetch-Site':'cross-site'},
                        {'Host':'untrusted.example','Origin':'http://untrusted.example'}):
            code,_,body=self.request('/api/ai/key/set',{'api_key':'fake-test-key-protected'},headers)
            self.assertEqual(code,403);self.assertNotIn('fake-test-key-protected',json.dumps(body))
            self.assertFalse(self.vault.values)
        code,_,_=self.request('/api/ai/key/status',headers={'Host':'untrusted.example'})
        self.assertEqual(code,403)

    def test_legacy_reads_reject_untrusted_hosts_before_accessing_storage(self):
        # The legacy stores are deliberately unset here. A rejected request must
        # never reach them or reveal previously saved dispatcher/session data.
        routes = ('/api/session', '/api/sessions', '/api/dispatch',
                  '/api/dispatch/history', '/api/dispatch/history/example',
                  '/api/dispatch/review', '/api/dispatch/comparison',
                  '/api/dispatch/handoff?id=example&revision=1')
        for route in routes:
            for headers in ({'Host': 'untrusted.example'},
                            {'Origin': 'https://untrusted.example'},
                            {'Sec-Fetch-Site': 'cross-site'}):
                with self.subTest(route=route, headers=headers):
                    code, _, body = self.request(route, headers=headers)
                    self.assertEqual(code, 403)
                    self.assertEqual(set(body), {'error'})

    def test_key_uses_opaque_httponly_cookie_and_clear_preserves_local_config(self):
        with patch.dict(os.environ,{'LOGISTICS_DEEPSEEK_API_KEY':'fake-local-config'},clear=True),patch('urllib.request.urlopen') as transport:
            code,headers,body=self.request('/api/ai/key/set',{'api_key':'fake-test-key-browser'}, {'Origin':self.origin()})
            self.assertEqual(code,200);self.assertTrue(body['session_configured'])
            transport.assert_not_called()
            cookie=headers['Set-Cookie'];self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=Strict',cookie)
            self.assertNotIn('fake-test-key-browser',cookie+json.dumps(body))
            cookie=cookie.split(';')[0]
            code,_,body=self.request('/api/ai/key/status',headers={'Cookie':cookie})
            self.assertTrue(body['session_configured'])
            code,_,body=self.request('/api/ai/key/status')
            self.assertFalse(body['session_configured'])
            code,_,body=self.request('/api/ai/key/clear',{}, {'Cookie':cookie,'Origin':self.origin()})
            self.assertFalse(body['session_configured']);self.assertTrue(body['local_configured'])
            self.assertFalse(self.vault.values);transport.assert_not_called()

    def test_user_key_applied_only_to_own_interpret_request_and_not_exported(self):
        with patch.dict(os.environ,{},clear=True):
            _,headers,_=self.request('/api/ai/key/set',{'api_key':'fake-browser-auth-secret'}, {'Origin':self.origin()})
            cookie=headers['Set-Cookie'].split(';')[0]
            _,_,state=self.request('/api/ai/status',headers={'Cookie':cookie})
            self.assertTrue(state['configured'])
            _,_,state=self.request('/api/ai/status')
            self.assertFalse(state['configured'])
            code,_,report=self.request('/api/scenario/live/start',{'input':loading.sample(),'mode':'fixed'})
            s=report['scene'];body=dict(id=s['id'],revision=s['revision'],message='构造收货变化')
            answer=MagicMock();answer.__enter__.return_value=answer
            answer.read.return_value=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(dict(type='time_window_change',time='08:00',store='SHOP_A',open='09:20',due='10:00',close='11:30'))}}]}).encode()
            with patch('urllib.request.urlopen',return_value=answer) as transport:
                code,_,result=self.request('/api/scenario/live/interpret',body,{'Cookie':cookie,'Origin':self.origin()})
                self.assertEqual(code,200);self.assertTrue(result['ready'])
                auth=transport.call_args.args[0].get_header('Authorization')
                self.assertEqual(auth,'Bearer fake-browser-auth-secret')
                self.assertNotIn('fake-browser-auth-secret',json.dumps(result)+json.dumps(report))
                code,_,_=self.request('/api/scenario/live/interpret',body,{'Origin':self.origin()})
                self.assertEqual(code,503);self.assertEqual(transport.call_count,1)
            self.assertNotIn('LOGISTICS_DEEPSEEK_API_KEY',os.environ)

    def test_provider_auth_error_never_returns_key_or_provider_body(self):
        token=self.vault.put(None,'fake-browser-auth-secret');cookie=session_keys.COOKIE+'='+token
        with self.vault.using(cookie),patch('urllib.request.urlopen',side_effect=HTTPError(ai_events.ENDPOINT,401,'provider detail contains secret',{},None)):
            with self.assertRaises(ai_events.AIError) as caught:ai_events.call_api([])
        self.assertNotIn('secret',str(caught.exception));self.assertIn('认证失败',str(caught.exception))


class EvidencePageTests(unittest.TestCase):
    def test_three_evidence_cases_preserve_invalid_baselines_and_unserved(self):
        geometry=evidence_cases.run('geometry')
        self.assertFalse(geometry['baseline']['executable']);self.assertTrue(geometry['checked']['executable'])
        self.assertEqual(geometry['baseline']['metrics']['served'],geometry['checked']['metrics']['served'])
        self.assertGreater(geometry['checked']['metrics']['cost'],geometry['baseline']['metrics']['cost'])
        frozen=evidence_cases.run('fixed_route')
        self.assertFalse(frozen['baseline']['executable']);self.assertTrue(frozen['checked']['executable'])
        self.assertEqual(frozen['baseline']['metrics']['cost'],frozen['checked']['metrics']['cost'])
        self.assertEqual(frozen['geometry']['sequence'],['A','B','C'])
        fragile=evidence_cases.run('fragile')
        self.assertFalse(fragile['checked']['executable']);self.assertGreater(fragile['checked']['metrics']['unserved'],0)
        self.assertTrue(fragile['unserved'])
        with self.assertRaises(ValueError):evidence_cases.run('arbitrary_file')
