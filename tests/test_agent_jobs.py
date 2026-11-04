import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
import unittest

import agent_actions
import application as app
import agent_jobs
import deepseek_agent
import multiwarehouse as mw
import platform_agent
from store import Conflict


class Planner:
    def __init__(self, flows=None, actions=None, gate=None):
        self.flows=flows or []; self.actions=actions or []; self.gate=gate
        self.key='sk-testing-not-a-real-credential'; self.last_answer='请先核对当前方案。'; self.calls=[]
    def plan(self,**kw):
        if self.gate: self.gate.wait(5)
        return self.flows
    def explain(self,**kw): return '候选已生成，请核对后确认。'


class AgentJobsTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.store=mw.MultiWarehouseStore(Path(self.temp.name)/'a.db')
        self.task=self.store.create('作业测试',mw.sample());self.registry=agent_jobs.JobRegistry(self.store)
    def tearDown(self):
        for thread in list(self.registry.threads.values()): thread.join(6)
        self.temp.cleanup()
    def terminal(self,start):
        deadline=monotonic()+7
        while monotonic()<deadline:
            job=self.registry.get(start['job']['id'],start['job_token'])['job']
            if job['status'] not in agent_jobs.ACTIVE:return job
            sleep(.02)
        self.fail('job did not finish')
    def test_real_tools_emit_events_and_keep_human_confirmation(self):
        started=self.registry.start(self.task['id'],1,'排车',planner=Planner(['dispatch']))
        job=self.terminal(started)
        self.assertEqual(job['status'],'completed')
        self.assertIsNone(job['result']['task']['adopted_plan_id'])
        kinds=[e['type'] for e in job['events']]
        self.assertLess(kinds.index('tool_started'),kinds.index('tool_completed'))
        self.assertIn('committing',kinds)
        self.assertEqual(job['events'][-1]['type'],'completed')
        self.assertNotIn('token_hash',job)
        self.assertEqual(self.registry.get(job['id'],started['job_token'],job['cursor'])['job']['events'],[])
    def test_job_token_required(self):
        started=self.registry.start(self.task['id'],1,'你好',planner=Planner())
        self.terminal(started)
        for token in ('wrong',None):
            with self.assertRaisesRegex(ValueError,'无法访问'):
                self.registry.get(started['job']['id'],token)
            with self.assertRaises(ValueError):self.registry.cancel(started['job']['id'],token)
    def test_unsupported_reply_is_consumed_once_without_saving_conversation(self):
        planner=Planner()
        planner.disposition='unsupported'
        planner.last_answer='仅能生成本地候选。'
        started=self.registry.start(self.task['id'],1,'直接派单',planner=planner)
        job=self.terminal(started)
        self.assertEqual(job['status'],'completed')
        self.assertTrue(job['ephemeral'])
        self.assertEqual(job['ephemeral_message'],planner.last_answer)
        self.assertEqual(self.store.read(self.task['id']),self.task)
        self.assertNotIn('ephemeral_message',self.registry._read(job['id']))
        self.assertNotIn('ephemeral_message',self.registry.get(job['id'],started['job_token'])['job'])

    def test_archive_restore_and_delete_chat_preserve_candidate(self):
        started=self.registry.start(self.task['id'],1,'生成候选',planner=Planner(['dispatch']))
        job=self.terminal(started)
        task,run=job['result']['task'],job['result']['agent_run']
        self.assertTrue(task['plans'])
        for action,expected in [('archive',True),('restore',False)]:
            task=agent_actions.organize_chat(self.store,task['id'],task['revision'],run['id'],action)
            self.assertIs(task['agent_runs'][0]['chat_archived'],expected)
        deleted=agent_actions.organize_chat(self.store,task['id'],task['revision'],run['id'],'delete')
        self.assertEqual(deleted['plans'],task['plans'])
        self.assertTrue(deleted['agent_runs'][0]['chat_deleted'])
        self.assertNotIn('message',deleted['agent_runs'][0])
        self.assertNotIn('user_message',deleted['agent_runs'][0])
        self.assertNotIn('result',self.registry.get(job['id'],started['job_token'])['job'])
        planner=Planner()
        planner.history=[]
        platform_agent.AgentWorkflow(self.store,planner).run(task['id'],deleted['revision'],'request','你好')
        self.assertEqual(planner.history,[])
    def test_duplicate_active_job_rejected_and_cancel_keeps_original_task(self):
        gate=Event();started=self.registry.start(self.task['id'],1,'排车',planner=Planner(['dispatch'],gate=gate))
        with self.assertRaises(Conflict):self.registry.start(self.task['id'],1,'再排',planner=Planner())
        response=self.registry.cancel(started['job']['id'],started['job_token'])
        self.assertEqual(response['job']['status'],'cancelling')
        gate.set();job=self.terminal(started)
        self.assertEqual(job['status'],'cancelled')
        self.assertEqual(self.store.read(self.task['id']),self.task)
    def test_concurrent_edit_wins_and_job_does_not_overwrite(self):
        gate=Event();started=self.registry.start(self.task['id'],1,'你好',planner=Planner(gate=gate))
        newer=self.store.save(self.task['id'],1,'人工修改',self.task['data'])
        gate.set();job=self.terminal(started)
        self.assertEqual(job['status'],'failed')
        self.assertEqual(self.store.read(self.task['id']),newer)
    def test_restart_reconciles_completed_jobs_and_interrupts_uncommitted(self):
        started=self.registry.start(self.task['id'],1,'你好',planner=Planner())
        job=self.terminal(started)
        with self.registry.lock:
            raw=self.registry._read(job['id']);raw['status']='committing';self.registry._write(raw)
        recovered=agent_jobs.JobRegistry(self.store)
        self.assertEqual(recovered.get(job['id'],started['job_token'])['job']['status'],'completed')
        with recovered.lock:
            raw=recovered._read(job['id']);raw.update(status='running',id='uncommitted',task_id=self.task['id'])
            recovered._write(raw)
        again=agent_jobs.JobRegistry(self.store)
        self.assertEqual(again.get('uncommitted',started['job_token'])['job']['status'],'interrupted')
    def test_rule_draft_requires_application_and_stale_draft_rejected(self):
        old=self.task['data']['warehouses'][0]['business']['tariff']['base']
        p=Planner(['rules'],[dict(kind='tariff',warehouse_id='WH_1',field='base',value=160)])
        task,run=platform_agent.AgentWorkflow(self.store,p).run(self.task['id'],1,'request','起步费改160')
        self.assertEqual(task['data']['warehouses'][0]['business']['tariff']['base'],old)
        with self.assertRaises(ValueError):agent_actions.apply_rule_draft(self.store,task['id'],task['revision'],run['id'],False)
        applied=agent_actions.apply_rule_draft(self.store,task['id'],task['revision'],run['id'],True)
        self.assertEqual(applied['data']['warehouses'][0]['business']['tariff']['base'],160)
        with self.assertRaises(Conflict):agent_actions.apply_rule_draft(self.store,applied['id'],applied['revision'],run['id'],True)
    def test_adjustment_preserves_all_orders_and_rejects_crosswarehouse(self):
        task=self.store.solve(self.task['id'],1)
        plan=mw.current_plan(task);g=plan['warehouses']['WH_1'];vehicle=next(v for v,r in g['optimized']['routes'].items() if r['sequence'])
        seq=g['optimized']['routes'][vehicle]['sequence']
        p=Planner(['adjust'],[dict(kind='reorder_route',vehicle_id=vehicle,sequence=list(reversed(seq)))])
        task,run=platform_agent.AgentWorkflow(self.store,p).run(task['id'],task['revision'],'request','倒过来送')
        self.assertTrue(run['result']['adjustment']['audit']['passed'])
        self.assertIsNone(task['adopted_plan_id'])
        with self.assertRaises(ValueError):agent_actions.reorder(task,[dict(kind='move_order',order_id=seq[0],vehicle_id='WH_2_TRUCK_1')])
    def test_enterprise_context_does_not_send_all_order_rows_or_routes(self):
        task=self.store.solve(self.task['id'],1)
        plan=mw.current_plan(task)
        for w in task['data']['warehouses']:w['settings']['data_kind']='enterprise'
        plan['input_sha256']=app.fingerprint(task['data'])
        for g in plan['warehouses'].values():g['data']['settings']['data_kind']='enterprise'
        context=agent_actions.selected_context(task)
        self.assertEqual(context['routes'],[])
        self.assertNotIn('orders',context)
        self.assertNotIn('legs',context)
    def test_provider_stream_parses_real_chunks_and_usage(self):
        frames=[dict(choices=[dict(delta=dict(content='方案'),finish_reason=None)]),
                dict(choices=[dict(delta=dict(content='完成'),finish_reason='stop')]),
                dict(choices=[],usage=dict(prompt_tokens=10,completion_tokens=2,total_tokens=12))]
        raw=''.join('data: '+json.dumps(f)+'\n\n' for f in frames)+'data: [DONE]\n\n'
        def transport(*a,**kw):return io.BytesIO(raw.encode())
        p=deepseek_agent.DeepSeekPlanner('sk-test',transport);p.stream_answer=True;chunks=[];p.on_delta=chunks.append
        self.assertEqual(p.complete([]),'方案完成')
        self.assertEqual(''.join(chunks),'方案完成')
        self.assertEqual(p.calls[0]['usage']['total_tokens'],12)

    def test_split_credential_never_reaches_stream_or_persisted_events(self):
        key='sk-example-private-token';published=[]
        frames=[dict(choices=[dict(delta=dict(content='结果 '+piece),finish_reason=None)])
            for piece in ('sk-example','-private-token')]
        # No separating text between the two credential fragments.
        frames[1]['choices'][0]['delta']['content']='-private-token'
        frames.append(dict(choices=[dict(delta={},finish_reason='stop')]))
        raw=''.join('data: '+json.dumps(f)+'\n\n' for f in frames)+'data: [DONE]\n\n'
        p=deepseek_agent.DeepSeekPlanner(key,lambda *a,**kw:io.BytesIO(raw.encode()))
        p.stream_answer=True;p.on_delta=published.append
        answer=p.complete([])
        self.assertNotIn(key,''.join(published));self.assertNotIn(key,answer)
        self.assertIn('[密钥已隐藏]',''.join(published))
        start=self.registry.start(self.task['id'],1,'检查',planner=Planner())
        self.terminal(start)
        # Exercise the exact event persistence callback used by the streaming job runner.
        job=self.registry._read(start['job']['id']);job['status']='running';self.registry._write(job)
        p.on_delta=lambda text:self.registry._publish(job['id'],'answer_delta',title='解释',details=dict(text=text))
        p.complete([])
        persisted=self.registry._read(job['id'])
        self.assertNotIn(key,json.dumps(persisted))


if __name__=='__main__':unittest.main()
