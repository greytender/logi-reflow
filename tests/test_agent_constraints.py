"""Read-only Agent constraints and scene evidence; no provider requests."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import io
import json
import unittest
import multiwarehouse as mw
import platform_agent as agent
import deepseek_agent as ds
import agent_actions

class AgentConstraintsTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.store=mw.MultiWarehouseStore(Path(self.temp.name)/'tasks.db')
        self.task=self.store.create('专项核验',mw.sample(physical_loading=True))
    def tearDown(self):self.temp.cleanup()
    def solve(self):return self.store.solve(self.task['id'],self.task['revision'])
    def test_display_aliases_follow_full_batch_order(self):
        data=dict(orders=[dict(id='WH2-O', warehouse_id='WH2'), dict(id='WH1-O', warehouse_id='WH1')],
                  vehicles=[dict(id='WH2-V'), dict(id='WH1-V')], warehouses=[dict(id='WH1'), dict(id='WH2')])
        before=deepcopy(data)
        aliases=agent.display_aliases(dict(data=data))
        self.assertEqual(aliases['orders'], [dict(label='门店1', id='WH2-O'), dict(label='门店2', id='WH1-O')])
        self.assertEqual(aliases['vehicles'][1], dict(label='车辆2', id='WH1-V'))
        self.assertEqual(aliases['warehouses'][0], dict(label='仓库1', id='WH1'))
        self.assertEqual(data, before)

    def test_missing_policy_never_means_full_verification(self):
        task=self.solve(); before=deepcopy(task)
        result=agent.coverage_audit(task)
        self.assertFalse(result['all_business_constraints_verified'])
        self.assertEqual(result['configured_operational_warehouses'],0)
        self.assertTrue(all(w['operational']['status']=='NOT_CONFIGURED' for w in result['warehouses']))
        self.assertEqual(result['evidence_ref']['input_sha256'],mw.app.fingerprint(task['data']))
        self.assertEqual(task,before)
    def test_spatial_selection_is_local_read_only_and_versioned(self):
        task=self.solve();before=deepcopy(task)
        group=mw.current_plan(task)['warehouses']['WH_1']
        vid,route=next((v,r) for v,r in group['optimized']['routes'].items() if r['sequence'])
        result=agent.spatial_inspect(task,dict(warehouse_id='WH_1',vehicle_id=vid,order_id=route['sequence'][0]))
        self.assertEqual(result['status'],'available')
        self.assertTrue(result['read_only'])
        self.assertTrue(result['departure_loading']['placements'])
        self.assertEqual(result['evidence_ref']['plan_id'],mw.current_plan(task)['id'])
        self.assertEqual(result['visual_context']['vehicle_id'],vid)
        self.assertEqual(task,before)
    def test_mismatched_selection_rejects_instead_of_guessing(self):
        task=self.solve()
        vehicle=next(v for v in task['data']['vehicles'] if v['warehouse_id']=='WH_2')
        with self.assertRaisesRegex(ValueError,'同一仓'):
            agent.spatial_inspect(task,dict(warehouse_id='WH_1',vehicle_id=vehicle['id']))
    def test_no_candidate_or_geometry_is_explicit(self):
        self.assertEqual(agent.spatial_inspect(self.task)['status'],'needs_candidate')
        task=self.store.create('未声明箱规',mw.sample());task=self.store.solve(task['id'],task['revision'])
        result=agent.spatial_inspect(task)
        self.assertEqual(result['status'],'NOT_CONFIGURED')
        self.assertEqual(result['summary']['departure_boxes'],0)
    def test_new_allowlisted_workflows_do_not_create_candidate_or_receipt(self):
        task=self.solve()
        class Planner:
            def plan(self,**kw):return ['coverage_audit','spatial_inspect']
        after,run=agent.AgentWorkflow(self.store,Planner(),selection={'warehouse_id':'WH_1'}).run(task['id'],task['revision'],'request','检查冷链与装卸空间')
        self.assertEqual(after['plans'],task['plans'])
        self.assertEqual(after['execution_tasks'],task['execution_tasks'])
        self.assertIsNone(after['adopted_plan_id'])
        self.assertIn('coverage_audit',run['result']);self.assertIn('spatial_inspect',run['result'])
    def test_provider_gets_summary_not_scene_or_coordinates(self):
        sent=[]
        def transport(request,timeout):
            sent.append(json.loads(request.data));return io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'所选车辆有声明装卸数据，温控仍需核对。'}}]}).encode())
        planner=ds.DeepSeekPlanner('test-key',transport)
        task=self.solve();coverage=agent.coverage_audit(task);spatial=agent.spatial_inspect(task)
        planner.explain(message='装卸情况',outputs=[('coverage_audit',coverage),('spatial_inspect',spatial)],fallback='')
        evidence=json.loads(sent[0]['messages'][-1]['content'])['evidence']
        encoded=json.dumps(evidence)
        self.assertNotIn('placements',encoded);self.assertNotIn('local_scene',encoded)
        self.assertNotIn('loading_profile',encoded);self.assertNotIn('legs',encoded)
        self.assertEqual(evidence['coverage_audit']['warehouses'][0]['operational_status'],'NOT_CONFIGURED')
    def test_compare_keeps_unknown_metrics_unknown(self):
        task=self.solve();result=agent.compare(task)
        self.assertIsNone(result['tradeoffs']['candidate']['cold_chain_model_passed'])
        self.assertIsNone(result['tradeoffs']['candidate']['extra_handling_actions'])
        self.assertTrue(result['tradeoffs']['same_complete_task_set'])
    def test_operational_tradeoffs_use_action_adjusted_times(self):
        task=self.store.create('声明冷链模型',mw.operational_sample())
        task=self.store.solve(task['id'],task['revision'])
        result=agent.compare(task)
        metrics=result['tradeoffs']['candidate']
        self.assertTrue(metrics['cold_chain_model_passed'])
        self.assertGreater(metrics['cold_box_openings'],0)
        self.assertGreater(metrics['extra_operation_minutes'],0)
        self.assertIsNotNone(metrics['latest_model_return_at'])
        coverage=result['constraint_coverage']['warehouses'][0]['operational']
        self.assertTrue(any(a['evidence']=='assumed' for a in coverage['assumptions']))
        spatial=agent.spatial_inspect(task)
        self.assertGreater(spatial['summary']['departure_boxes'],0)
        self.assertGreater(spatial['summary']['timeline_frames'],0)
        self.assertEqual(len(spatial['local_scene']['routes']),1)
        summary=agent.loading_summary(task)
        self.assertTrue(summary['all_declared_departure_boxes_verified'])
        self.assertNotIn('均未配置',summary['message'])
    def test_infeasible_special_policy_baseline_cannot_claim_savings(self):
        task=self.store.create('基线专项冲突',mw.operational_sample())
        task=self.store.solve(task['id'],task['revision'])
        group=next(iter(mw.current_plan(task)['warehouses'].values()))
        route=next(r for r in group['baseline']['routes'].values() if r['sequence'])
        route['sequence'].append(route['sequence'][0])
        result=agent.compare(task)
        self.assertFalse(result['same_verified_constraint_set'])
        self.assertIsNone(result['delta_cost_vs_simple_baseline'])
        self.assertIn('基线未通过',result['comparison_limit'])
    def test_grounding_does_not_promote_model_to_cold_chain_certificate(self):
        self.assertEqual(agent.grounding_issue('冷链已合规。',self.task),'冷链已合规')
        self.assertIsNone(agent.grounding_issue('这不代表冷链已合规。',self.task))

    def test_geometry_baseline_must_pass_same_model(self):
        task=self.solve();before=deepcopy(task)
        route=next(r for r in mw.current_plan(task)['warehouses']['WH_1']['baseline']['routes'].values() if r['sequence'])
        route['sequence'].append(route['sequence'][0])
        comparison=agent.compare(task)
        self.assertFalse(comparison['same_verified_constraint_set'])
        self.assertIsNone(comparison['delta_cost_vs_simple_baseline'])
        self.assertFalse(comparison['tradeoffs']['baseline']['geometry_model_passed'])

    def test_mixed_warehouse_does_not_skip_operational_constraints(self):
        data=mw.sample();operational=mw.operational_sample()
        for key in ('warehouses','orders','vehicles'):data[key].extend(operational[key])
        data['legs'].update(operational['legs'])
        task=self.store.create('部分仓有冷链政策',data);task=self.store.solve(task['id'],task['revision'])
        route=next(r for r in mw.current_plan(task)['warehouses']['WH']['baseline']['routes'].values() if r['sequence'])
        route['sequence'].append(route['sequence'][0])
        result=agent.compare(task)
        self.assertFalse(result['same_verified_constraint_set'])
        self.assertIsNone(result['delta_cost_vs_simple_baseline'])
        self.assertEqual(result['tradeoffs']['baseline']['operational_warehouse_count'],1)

    def test_operational_recovery_trial_replays_new_rules_without_mutating_plan(self):
        task=self.store.create('含专项约束的回收敏感性',mw.operational_sample())
        task=self.store.solve(task['id'],task['revision']);before=deepcopy(task)
        result=agent.recovery_trial(task,{'warehouse_id':'WH'},lambda:None)
        self.assertEqual(result['status'],'operational_sensitivity')
        self.assertEqual(result['scenario_count'],3)
        self.assertFalse(result['creates_candidate'])
        self.assertEqual(result['validation_scope'],'declared_cold_chain_and_along_route_spatial_replay')
        self.assertEqual(task,before)
        self.assertIn('未修改当前方案',agent.report([('recovery_trial',result)]))

    def test_ai_single_store_move_cannot_duplicate_shared_transport_box(self):
        task=self.store.create('共享箱不能拆车',mw.operational_sample())
        task=self.store.solve(task['id'],task['revision']);before=deepcopy(task)
        group=mw.current_plan(task)['warehouses']['WH']['optimized']
        used,route=next((v,r) for v,r in group['routes'].items() if r['sequence'])
        empty=next(v for v,r in group['routes'].items() if not r['sequence'])
        with self.assertRaisesRegex(ValueError,'共享冷链箱|冷链'):
            agent_actions.reorder(task,[dict(kind='move_order',order_id=route['sequence'][0],vehicle_id=empty)])
        self.assertEqual(task,before)

if __name__=='__main__':unittest.main()
