import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import deepseek_agent as ds
import multiwarehouse as mw
import platform_agent
from store import Conflict


class DeepSeekAgentTests(unittest.TestCase):
    def adapter(self, answers):
        self.sent = []
        def transport(request, timeout):
            self.sent.append(json.loads(request.data))
            return io.BytesIO(json.dumps(dict(choices=[dict(finish_reason='stop', message=dict(content=answers.pop(0)))],
                usage=dict(prompt_tokens=20, completion_tokens=10, total_tokens=30))).encode())
        return ds.DeepSeekPlanner('sk-test-only-not-a-real-key', transport)

    def test_allowlisted_workflow_and_rejection(self):
        p = self.adapter(['{"workflows":["dispatch","risks"],"answer":"检查后排车"}'])
        self.assertEqual(p.plan(context={}, message='排车', tools=platform_agent.TOOLS), ['dispatch', 'risks'])
        self.assertEqual(self.sent[0]['thinking'], {'type': 'disabled'})
        for flows in (['adopt'], ['dispatch', 'dispatch'], ['dispatch', 'remaining']):
            p = self.adapter([json.dumps(dict(workflows=flows, answer='不能执行'))])
            with self.assertRaisesRegex(ValueError, '未执行'):
                p.plan(context={}, message='请求', tools=platform_agent.TOOLS)

    def test_blank_truncated_and_malformed_replies_fail_before_execution(self):
        for answer in ('', '   ', 'not json', '{}', '{"workflows":[],"answer":42}'):
            p = self.adapter([answer])
            with self.assertRaises(ValueError): p.plan(context={}, message='请求', tools=platform_agent.TOOLS)

    def test_disposition_distinguishes_conversation_from_unsupported_operations(self):
        planner = self.adapter([
            '{"workflows":[],"answer":"你好","disposition":"supported"}',
            '{"workflows":[],"answer":"请手动确认","disposition":"unsupported"}',
            '{"workflows":["dispatch"],"answer":"请手动确认","disposition":"unsupported"}',
        ])
        self.assertEqual(planner.plan(context={}, message='你好', tools=platform_agent.TOOLS), [])
        self.assertEqual(planner.disposition, 'supported')
        self.assertEqual(planner.plan(context={}, message='直接确认方案', tools=platform_agent.TOOLS), [])
        self.assertEqual(planner.disposition, 'unsupported')
        with self.assertRaises(ValueError):
            planner.plan(context={}, message='直接确认方案', tools=platform_agent.TOOLS)

    def test_http_error_is_sanitized(self):
        def fail(*a, **kw): raise HTTPError(ds.ENDPOINT, 401, 'secret-provider-body', {}, None)
        with self.assertRaisesRegex(ValueError, '密钥无效') as error:
            ds.DeepSeekPlanner('sk-secret', fail).complete([])
        self.assertNotIn('secret', str(error.exception))

    def test_live_tool_execution_summary_failure_fallback_and_history(self):
        with TemporaryDirectory() as temp:
            store = mw.MultiWarehouseStore(Path(temp)/'test.db')
            task = store.create('模拟', mw.sample())
            p = self.adapter(['{"workflows":["dispatch"],"answer":"排车"}', '费用试算370元，待核对。'])
            task, run = platform_agent.AgentWorkflow(store, p).run(task['id'], task['revision'], 'request', '排车')
            self.assertTrue(run['result']['compare']['audit']['passed'])
            self.assertIsNone(task['adopted_plan_id'])
            self.assertEqual(len(run['model_calls']), 2)
            payload = json.loads(self.sent[1]['messages'][-1]['content'])
            self.assertNotIn('orders', payload['evidence'])
            self.assertNotIn('legs', payload['evidence'])
            self.assertNotEqual(payload['evidence']['inspect']['status'], '待准备与计算')
            self.assertEqual(payload['evidence']['compare']['candidate_plan']['scheduled_tasks'],6)
            self.assertNotIn('served',payload['evidence']['compare']['candidate_plan'])
            self.assertIn('forecast_late_tasks',payload['evidence']['compare']['candidate_plan'])
            p = self.adapter(['{"workflows":[],"answer":"你可以先核对方案。"}'])
            task, chat = platform_agent.AgentWorkflow(store, p).run(task['id'], task['revision'], 'request', '接下来呢')
            self.assertEqual(len(task['plans']), 1)
            self.assertEqual(chat['message'], '你可以先核对方案。')
            self.assertTrue(any(m['content']=='排车' for m in p.history))
            p = self.adapter(['{"workflows":["compare"],"answer":"解释费用"}', ''])
            task, fallback = platform_agent.AgentWorkflow(store, p).run(task['id'], task['revision'], 'request', '费用呢')
            self.assertIn('费用试算', fallback['message'])
            self.assertEqual(fallback['traces'][-1]['status'], 'unavailable')

    def test_stale_version_does_not_call_provider(self):
        with TemporaryDirectory() as temp:
            store = mw.MultiWarehouseStore(Path(temp)/'test.db')
            task = store.create('模拟', mw.sample()); p = self.adapter([])
            with self.assertRaises(Conflict):
                platform_agent.AgentWorkflow(store, p).run(task['id'], 0, 'request', '排车')
            self.assertEqual(self.sent, [])

    def test_credentials_not_saved_even_if_echoed(self):
        with TemporaryDirectory() as temp:
            store = mw.MultiWarehouseStore(Path(temp)/'test.db')
            task = store.create('模拟', mw.sample()); key='sk-test-only-not-a-real-key'
            p = self.adapter([json.dumps(dict(workflows=[], answer=key))])
            task, run = platform_agent.AgentWorkflow(store, p).run(task['id'], task['revision'], 'request', key)
            self.assertNotIn(key, json.dumps(task))

    def test_no_credentials_means_no_adapter(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertIsNone(ds.planner())
            self.assertFalse(ds.status()['configured'])

    def test_risk_explanation_keeps_specific_warning(self):
        p = self.adapter(['请核对回收超重预警。'])
        p.explain(message='为什么超重', outputs=[('risks', dict(items=[dict(kind='execution_forecast',
            vehicle='V1', message='预计回收后超重', evidence='local_records_and_static_travel')],
            missing_capabilities=[]))], fallback='')
        sent = json.loads(self.sent[0]['messages'][-1]['content'])
        self.assertEqual(sent['evidence']['risks']['items'][0]['message'], '预计回收后超重')

    def test_explanation_receives_loading_counts_without_coordinates(self):
        summary=dict(warehouses=2,configured_warehouses=2,verified_warehouses=2,
            declared_boxes=12,checked_boxes=12,all_declared_departure_boxes_verified=True,
            scope='declared_departure_delivery_cuboids_only')
        p=self.adapter(['全部所声明的12箱已在有限模型内通过核验。'])
        p.explain(message='装载核验呢',outputs=[('risks',dict(items=[],missing_capabilities=[],physical_loading=summary)),
            ('compare',dict(status='review_required',physical_loading=summary))],fallback='')
        sent=json.loads(self.sent[0]['messages'][-1]['content'])
        self.assertEqual(sent['evidence']['risks']['physical_loading'],summary)
        self.assertEqual(sent['evidence']['compare']['physical_loading'],summary)
        self.assertNotIn('placements',json.dumps(sent))

    def test_unrecorded_delivery_claim_is_rejected_without_losing_candidate(self):
        with TemporaryDirectory() as temp:
            store=mw.MultiWarehouseStore(Path(temp)/'test.db');task=store.create('模拟',mw.sample())
            p=self.adapter(['{"workflows":["dispatch"],"answer":"排车"}',
                '尚未签收；候选与基线均为6单全部送达。'])
            task,run=platform_agent.AgentWorkflow(store,p).run(task['id'],task['revision'],'request','排车')
            self.assertEqual(run['traces'][-1]['status'],'grounding_rejected')
            self.assertNotIn('全部送达',run['message'])
            self.assertIn('安排6个任务',run['message'])
            self.assertTrue(run['result']['compare']['audit']['passed'])
            self.assertIsNone(task['adopted_plan_id'])

    def test_negative_delivery_phrases_are_not_false_grounding_errors(self):
        with TemporaryDirectory() as temp:
            store=mw.MultiWarehouseStore(Path(temp)/'test.db');task=store.create('模拟',mw.sample())
            for text in ('没有已签收记录，也没有已派单记录。','尚未签收，不代表全部送达。','不能把已安排说成全部送达。'):
                self.assertIsNone(platform_agent.grounding_issue(text,task),text)
            for text in ('订单已签收。','订单均已送达。','没有已签收记录，但已派单。'):
                self.assertIsNotNone(platform_agent.grounding_issue(text,task),text)


if __name__ == '__main__': unittest.main()
