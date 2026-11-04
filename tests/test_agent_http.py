"""Local HTTP contract checks; provider is a fake and no paid requests are made."""
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep
import unittest
from unittest.mock import patch

import deepseek_agent
import multiwarehouse as mw
import server


class RulesPlanner:
    key='sk-local-http-only-fake-key'
    history=[]
    calls=[]
    actions=[dict(kind='tariff',warehouse_id='WH_1',field='base',value=160)]
    last_answer='准备规则草稿。'
    def plan(self,**kw):return ['rules']
    def explain(self,**kw):return '已生成起步费草稿，原输入未改变。'


class AgentHttpTests(unittest.TestCase):
    def test_session_key_job_token_rule_application_and_stale_rejection(self):
        with TemporaryDirectory() as temp:
            store=mw.MultiWarehouseStore(Path(temp)/'a.db');task=store.create('HTTP作业',mw.sample())
            old=server.multiwarehouse_store;server.multiwarehouse_store=store
            httpd=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            thread=Thread(target=httpd.serve_forever,daemon=True);thread.start()
            port=httpd.server_port;cookie=''
            def request(path,body=None,token=None):
                headers={'Content-Type':'application/json','Origin':f'http://127.0.0.1:{port}'}
                if cookie:headers['Cookie']=cookie
                if token:headers['X-Agent-Job-Token']=token
                c=HTTPConnection('127.0.0.1',port,timeout=5)
                c.request('GET' if body is None else 'POST',path,None if body is None else json.dumps(body),headers)
                r=c.getresponse();status=r.status;set_cookie=r.getheader('Set-Cookie')
                payload=json.loads(r.read());c.close()
                return status,payload,set_cookie
            try:
                code,_,set_cookie=request('/api/ai/key/set',dict(api_key=RulesPlanner.key))
                self.assertEqual(code,200);cookie=set_cookie.split(';')[0]
                seen=[]
                def factory():
                    seen.append(deepseek_agent.credential());return RulesPlanner()
                with patch.object(deepseek_agent,'planner',factory):
                    code,start,_=request('/api/multiwarehouse/agent/start',dict(id=task['id'],revision=1,message='起步费改160'))
                self.assertEqual(code,200);self.assertEqual(seen,[RulesPlanner.key])
                job_id=start['job']['id'];token=start['job_token'];deadline=monotonic()+4
                while True:
                    code,data,_=request('/api/multiwarehouse/agent/job?job_id='+job_id,token=token)
                    self.assertEqual(code,200)
                    if data['job']['status'] not in ('queued','running','cancelling','committing'):break
                    if monotonic()>deadline:self.fail('HTTP作业未及时结束')
                    sleep(.02)
                job=data['job'];self.assertEqual(job['status'],'completed')
                self.assertNotIn(RulesPlanner.key,json.dumps(job));self.assertNotIn('token_hash',job)
                code,_,_=request('/api/multiwarehouse/agent/job?job_id='+job_id)
                self.assertEqual(code,400)
                code,_,_=request('/api/multiwarehouse/agent/job?job_id='+job_id+'&after=invalid',token=token)
                self.assertEqual(code,400)
                saved=job['result']['task'];run=job['result']['agent_run']
                self.assertEqual(saved['data']['warehouses'][0]['business']['tariff']['base'],140)
                payload=dict(id=saved['id'],revision=saved['revision'],run_id=run['id'],reviewed=True)
                code,applied,_=request('/api/multiwarehouse/agent/apply-rules',payload)
                self.assertEqual(code,200)
                self.assertEqual(applied['task']['data']['warehouses'][0]['business']['tariff']['base'],160)
                payload['revision']=applied['task']['revision']
                code,_,_=request('/api/multiwarehouse/agent/apply-rules',payload)
                self.assertEqual(code,409)
                code,stopped,_=request('/api/multiwarehouse/agent/cancel',dict(job_id=job_id,job_token=token))
                self.assertEqual(code,200);self.assertFalse(stopped['cancel_accepted'])
                record=dict(id=saved['id'],revision=applied['task']['revision'],run_id=run['id'],action='archive')
                code,archived,_=request('/api/multiwarehouse/agent/chat-record',record)
                self.assertEqual(code,200)
                self.assertTrue(archived['task']['agent_runs'][0]['chat_archived'])
                record.update(action='delete',revision=archived['task']['revision'])
                code,deleted,_=request('/api/multiwarehouse/agent/chat-record',record)
                self.assertEqual(code,200)
                self.assertTrue(deleted['task']['agent_runs'][0]['chat_deleted'])
                self.assertNotIn('message',deleted['task']['agent_runs'][0])
                self.assertEqual(deleted['task']['data']['warehouses'][0]['business']['tariff']['base'],160)
            finally:
                httpd.shutdown();httpd.server_close();thread.join();server.multiwarehouse_store=old


if __name__=='__main__':unittest.main()
