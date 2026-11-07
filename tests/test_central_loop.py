from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import central_orders
import decision_changes
import intake
import intake_dispatch
import intake_review
import multiwarehouse as mw
from store import Conflict


class CentralOrdersTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        self.sources=intake.IntakeStore(self.path/'source.db')
        self.reviews=intake_review.ReviewStore(self.sources.path,self.sources)

    def test_confirmed_interpretation_keeps_all_rows_and_original_formula(self):
        source=Path(__file__).resolve().parents[1]/'docs/reference-materials/单日城配托运单信息（10月）.xlsx'
        document=self.sources.create('中央仓','orders.xlsx',source.read_bytes())
        original=deepcopy(document)
        review=self.reviews.read(document['id'])
        review=self.reviews.save(document['id'],0,review['config'])
        rows=review['assessment']['rows']
        self.assertEqual(len(rows),116)
        self.assertEqual(len({r['identity']['store_id'] for r in rows}),103)
        self.assertEqual(sum(r['normalized']['regular_quantity']+r['normalized']['cold_quantity']for r in rows),1467)
        self.assertEqual(sum(r.get('cold_zero_confirmed',False) for r in rows),32)
        self.assertFalse(any(r['missing'] or r['issues'] for r in rows))
        first=rows[0]
        self.assertEqual(first['normalized']['cold_volume_m3'],.001)
        self.assertEqual(first['source_refs']['cold_volume']['value'],.01)
        parameters=central_orders.parameters(document,review)
        preview,data,provenance=intake_dispatch.prepare(document,review,parameters)
        self.assertTrue(preview['ready'],preview['issues'])
        self.assertEqual(len(data['orders']),103)
        self.assertEqual(len(data['warehouses']),1)
        self.assertAlmostEqual(sum(o['unit_volume_m3']for o in data['orders']),98.95)
        self.assertEqual(len(provenance['rows']),116)
        self.assertEqual(provenance['rows'][0]['volume_basis']['source_volume_m3'],.01)
        self.assertEqual(self.sources.read(document['id']),original)
        legacy=deepcopy(review['config'])
        legacy['identities']['6']['store_id']='OLDER_NAME_ADDRESS_ID'
        self.assertFalse(central_orders.matches(document,legacy))

    def test_similar_headers_do_not_authorize_business_defaults(self):
        document=self.sources.create('其他订单','other.xlsx',intake.sample_xlsx())
        review=self.reviews.read(document['id'])
        self.assertIsNone(review['business_rules'])
        self.assertIsNone(review['config']['units']['cold_volume'])
        self.assertFalse(central_orders.matches(document))

    def test_change_preview_then_recalculation_retains_both_plans(self):
        store=mw.MultiWarehouseStore(self.path/'tasks.db')
        task=store.create('配送',mw.sample())
        task=store.solve(task['id'],task['revision'])
        before=mw.current_plan(task)
        order=task['data']['orders'][0]
        proposal=decision_changes.preview(task,dict(order_id=order['id'],pickup=dict(cartons=3,totes=2)))
        self.assertEqual(task['data']['orders'][0]['pickup'],order['pickup'])
        self.assertEqual(proposal['impact']['before_plan_id'],before['id'])
        self.assertTrue(proposal['impact']['affected_routes'])
        task=store.save(task['id'],task['revision'],task['name'],proposal['data'])
        self.assertIsNone(mw.current_plan(task))
        task=store.solve(task['id'],task['revision'])
        after=mw.current_plan(task)
        self.assertEqual(after['parent_plan_id'],before['id'])
        self.assertEqual(len(task['plans']),2)
        self.assertTrue(after['audit']['passed'])
        self.assertFalse(mw.view(task)['confirmed'])
        task=store.adopt(task['id'],task['revision'],after['id'],True)
        self.assertTrue(mw.view(task)['confirmed'])

    def test_bad_time_and_execution_facts_cannot_be_overwritten(self):
        store=mw.MultiWarehouseStore(self.path/'tasks.db')
        task=store.create('配送',mw.sample())
        with self.assertRaises(ValueError):
            decision_changes.preview(task,dict(order_id=task['data']['orders'][0]['id'],open='20:00',close='09:00'))
        task=store.solve(task['id'],task['revision'])
        task=store.adopt(task['id'],task['revision'],mw.current_plan(task)['id'],True)
        wid=task['data']['warehouses'][0]['id']
        child=task['execution_tasks'][wid]
        vid=next(v for v,r in mw.view(task)['execution'][wid]['routes'].items() if r['remaining'])
        route=mw.view(task)['execution'][wid]['routes'][vid]
        stop=route['remaining'][0]
        receipt=dict(vehicle=vid,kind='stop',stop_id=stop['id'],finish=stop['finish'],
                     pickup=dict(cartons=2,totes=1),source='simulated',note='本地登记')
        task=store.action(task['id'],task['revision'],wid,'receipt',receipt,True)
        with self.assertRaises(Conflict):
            decision_changes.preview(task,dict(order_id=stop['id'],pickup=dict(cartons=0,totes=0)))
