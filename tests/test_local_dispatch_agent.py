"""Offline requests exercise the same guarded tools as the connected planner."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest

import agent_actions
from agent_jobs import ACTIVE, JobRegistry
from local_dispatch_agent import LocalDispatchPlanner
import multiwarehouse as mw
from platform_agent import AgentWorkflow
from platform_agent import numerical_issue
from store import Conflict


class LocalDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = mw.MultiWarehouseStore(Path(self.directory.name) / 'tasks.sqlite3')
        self.task = self.store.create('仓库配送', mw.sample())

    def test_no_provider_request_still_computes_and_reviews_selected_vehicle(self):
        registry = JobRegistry(self.store)
        request = registry.start(self.task['id'], self.task['revision'], '计算配送方案', planner=None)
        deadline = monotonic() + 10
        while monotonic() < deadline:
            job = registry.get(request['job']['id'], request['job_token'])['job']
            if job['status'] not in ACTIVE:
                break
            sleep(.01)
        self.assertEqual(job['status'], 'completed')
        result = job['result']
        self.assertFalse(result['agent_run']['model_connected'])
        self.assertEqual(result['agent_run']['model_calls'], [])
        self.assertIsNone(result['task']['adopted_plan_id'])
        self.assertIn('comparison', mw.current_plan(result['task']))
        self.assertNotIn('model_started', {event['type'] for event in job['events']})
        current = mw.current_plan(result['task'])
        vehicle = next(v for v in result['task']['data']['vehicles']
            if current['warehouses'][v['warehouse_id']]['optimized']['routes'][v['id']]['sequence'])
        task, run = AgentWorkflow(self.store, LocalDispatchPlanner(),
            selection={'warehouse_id': vehicle['warehouse_id'], 'vehicle_id': vehicle['id']}).run(
                self.task['id'], result['task']['revision'], 'request', '核对本车')
        review = run['result']['route_review']
        self.assertEqual(review['status'], 'available')
        route = mw.current_plan(task)['warehouses'][vehicle['warehouse_id']]['optimized']['routes'][vehicle['id']]
        self.assertEqual((review['sequence'], review['cost']), (route['sequence'], route['cost']))
        self.assertIsNone(task['adopted_plan_id'])

    def test_pickup_request_proposes_exact_change_and_needs_explicit_application(self):
        task, run = AgentWorkflow(self.store, LocalDispatchPlanner()).run(
            self.task['id'], self.task['revision'], 'request', '把门店1的预计回收改为3个周转箱')
        draft = run['result']['rule_draft']
        self.assertEqual(task['data'], self.task['data'])
        self.assertEqual(draft['changes'][0]['field'], 'totes')
        self.assertEqual(draft['changes'][0]['after'], 3)
        with self.assertRaises(ValueError):
            agent_actions.apply_rule_draft(self.store, task['id'], task['revision'], run['id'], False)
        newer = agent_actions.apply_rule_draft(self.store, task['id'], task['revision'], run['id'], True)
        self.assertEqual(newer['data']['orders'][0]['pickup']['totes'], 3)
        self.assertIsNone(newer['adopted_plan_id'])

    def test_ambiguous_commands_and_cross_warehouse_selection_do_not_change_data(self):
        original = deepcopy(self.task)
        after, reply = AgentWorkflow(self.store, LocalDispatchPlanner()).run(
            self.task['id'], self.task['revision'], 'request', '把某一家店的回收量改一下')
        self.assertTrue(reply['ephemeral'])
        self.assertEqual(after, original)
        self.assertEqual(self.store.read(after['id']), original)
        other_vehicle = next(v for v in original['data']['vehicles'] if v['warehouse_id'] == 'WH_2')
        with self.assertRaises(ValueError):
            agent_actions.selected_context(original, {'warehouse_id': 'WH_1', 'vehicle_id': other_vehicle['id']})

    def test_archived_batches_keep_facts_and_restore_with_current_revision(self):
        before = self.store.solve(self.task['id'], self.task['revision'])
        archived = self.store.organize(before['id'], before['revision'], 'archive')
        self.assertEqual(archived['plans'], before['plans'])
        self.assertEqual(self.store.list(), [])
        self.assertEqual(len(self.store.list('archived')), 1)
        with self.assertRaises(Conflict):
            self.store.solve(archived['id'], archived['revision'])
        with self.assertRaises(Conflict):
            self.store.organize(archived['id'], before['revision'], 'restore')
        restored = self.store.organize(archived['id'], archived['revision'], 'restore')
        trashed = self.store.organize(restored['id'], restored['revision'], 'trash')
        self.assertEqual(trashed['plans'], before['plans'])
        self.assertEqual(len(self.store.list('trash')), 1)
        final = self.store.organize(trashed['id'], trashed['revision'], 'restore')
        self.assertEqual(self.store.list()[0]['id'], final['id'])

    def test_explanation_numbers_are_limited_to_tool_results(self):
        evidence = [('compare', {'cost': 185, 'weight_utilization': .9, 'rehandle_moves': 4})]
        self.assertIsNone(numerical_issue('试算185元，载重利用率90%，搬倒4次。', evidence))
        self.assertIsNotNone(numerical_issue('已经节省999元。', evidence))
