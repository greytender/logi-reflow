from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import business
import intake
import intake_dispatch
import intake_review
import multiwarehouse
import operational_constraints as operational
import server
import test_parallel_http
from store import Conflict
from test_intake import edit_part


def source_xlsx():
    return edit_part(intake.sample_xlsx(), 'xl/worksheets/sheet1.xml', lambda sheet: sheet.replace(
        b'<c r="E8">', '<c r="D8" t="inlineStr"><is><t>构造地址乙</t></is></c><c r="E8">'.encode()))


def batch_review(document):
    config = intake_review.default_config(document)
    config.update(batch_id='BATCH_A', delivery_date='2026-11-06')
    config['units'].update(regular_weight='kg', regular_volume='m3', cold_weight='kg', cold_volume='m3')
    config['identities'] = {str(row): {'store_id': store_id} for row, store_id in [(6,'S_A'),(7,'S_A'),(8,'S_B')]}
    return config


def batch_parameters():
    stores = {'S_A': [6,7], 'S_B': [8]}
    goods = [
        dict(order=sid, sku=f'REG_R{row}', kind='regular', length_cm=60, width_cm=40,
             height_cm=30, fragile=False, max_top_load_kg=70)
        for sid, rows in stores.items() for row in rows
    ]
    for sid, row in [('S_A',6), ('S_B',8)]:
        goods.append(dict(order=sid, sku=f'COLD_R{row}', kind='cold', length_cm=40,
                          width_cm=25, height_cm=10, fragile=False, max_top_load_kg=20,
                          cold_box_id='BOX', temperature_range=[2,8]))
    policy = dict(schema=operational.SCHEMA, data_kind='simulated', goods=goods,
        cold_boxes=[dict(id='BOX', length_cm=60, width_cm=40, height_cm=45,
            inner_length_cm=40, inner_width_cm=25, inner_height_cm=30,
            tare_weight_kg=8, inner_volume_m3=.03, max_content_kg=25,
            temperature_range=[2,8], valid_minutes=180, max_openings=3, opening_minutes=2,
            allowed_vehicles=['V1'], requires_cold_zone=False, activated_at_minutes=490)],
        vehicle_geometry=[dict(id='V1', length_cm=420, width_cm=210, height_cm=210)],
        pickup_geometry=dict(carton=dict(length_cm=60, width_cm=40, height_cm=1.25),
            tote=dict(length_cm=60, width_cm=40, height_cm=25, nested_height_cm=5)),
        handling=dict(allow_rehandling=True, move_minutes=.35, temporary_slots=8,
            pickup_minutes_per_unit=.03, temporary_area=dict(length_cm=300, width_cm=200, height_cm=210)))
    return dict(warehouse=dict(id='WH',name='长沙仓',settings=dict(depot_id='WH',
        data_kind='simulated',travel_source='仅用于构造测试的闭合有向矩阵',window_rule='finish'),
        business=business.defaults(),operational_policy=policy),
        vehicles=[dict(id='V1',max_weight_kg=900,max_volume_m3=18,fixed_cost=140,cost_per_km=2,
                       shift_start='08:30',shift_end='15:00')],
        stop_defaults=dict(open='08:30',due='12:00',close='13:00',service_minutes=4,
                           allowed_vehicles=['V1'],pickup=dict(cartons=2,totes=1)),
        stops={'S_B': {'pickup': {'totes':2}}},
        legs={f'{a}>{b}':dict(distance_km=distance,travel_minutes=15)
              for a,b,distance in [('WH','S_A',35),('S_A','WH',35),('WH','S_B',50),
                                   ('S_B','WH',50),('S_A','S_B',17),('S_B','S_A',17)]},
        zero_cold_rows=[7],accept_cached_totals=False)


class IntakeDispatchTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.intakes = intake.IntakeStore(root/'intake.sqlite3')
        self.reviews = intake_review.ReviewStore(self.intakes.path,self.intakes)
        self.tasks = multiwarehouse.MultiWarehouseStore(root/'tasks.sqlite3')
        self.document = self.intakes.create('门店配送','batch.xlsx',source_xlsx())
        self.review = self.reviews.save(self.document['id'],0,batch_review(self.document))

    def test_review_create_solve_confirm_and_source_rows_remain_linked(self):
        template = intake_dispatch.template(self.document,self.review)
        self.assertEqual(template['scope']['source_rows'],[6,7,8])
        self.assertEqual(template['rows'][0]['suggested_store_id'],template['rows'][1]['suggested_store_id'])
        self.assertTrue(template['rows'][1]['cold_blank'])
        self.assertFalse(template['parameters']['zero_cold_rows'])
        self.assertFalse(template['parameters']['legs'])
        self.assertFalse(template['parameters']['vehicles'])
        request = dict(id=self.document['id'],revision=1,confirmed=True,parameters=batch_parameters())
        payload,status = intake_dispatch.dispatch_request(self.reviews,self.tasks,request,create=True)
        self.assertEqual(status,200,payload)
        task = payload['task']
        source = task['intake_source']
        self.assertEqual(source['source_sha256'],self.document['sha256'])
        self.assertEqual(source['stores'][0]['source_rows'],[6,7])
        self.assertEqual([row['row_key'] for row in source['rows']],['ROW-1-6','ROW-1-7','ROW-1-8'])
        self.assertEqual(sum(line['quantity'] for order in task['data']['orders'] for line in order['lines']),8)
        self.assertEqual(sum(order['unit_weight_kg'] for order in task['data']['orders']),43)
        self.assertEqual(task['data']['orders'][1]['pickup'],{'cartons':2,'totes':2})
        self.assertTrue(source['rows'][1]['cold_zero_confirmed'])
        self.assertEqual(self.intakes.read(self.document['id']),self.document)
        task = self.tasks.solve(task['id'],task['revision'])
        plan = task['plans'][-1]
        self.assertTrue(plan['audit']['passed'])
        self.assertEqual(plan['summary']['optimized']['served'],2)
        route = plan['warehouses']['WH']['optimized']['routes']['V1']
        self.assertEqual((route['distance'],route['cost']),(102,174))
        self.assertEqual(task['intake_source'],source)
        task = self.tasks.adopt(task['id'],task['revision'],plan['id'],True)
        self.assertTrue(multiwarehouse.view(task)['confirmed'])
        self.assertEqual(self.tasks.read(task['id'])['intake_source'],source)

    def test_invalid_scope_unconfirmed_blanks_and_cold_kind_are_blocked(self):
        parameters = batch_parameters()
        parameters['zero_cold_rows'] = [6]
        goods = parameters['warehouse']['operational_policy']['goods']
        next(good for good in goods if good['sku']=='COLD_R6')['kind'] = 'regular'
        preview,_,_ = intake_dispatch.prepare(self.document,self.review,parameters)
        self.assertFalse(preview['ready'])
        codes = {entry['code'] for entry in preview['issues']}
        self.assertTrue({'zero_cold_scope','cold_totals_required','cold_policy_mismatch'} <= codes)
        config = deepcopy(self.review['config'])
        config['rows'] = [6,8]
        review = self.reviews.save(self.document['id'],1,config)
        preview,_,_ = intake_dispatch.prepare(self.document,review,batch_parameters())
        self.assertEqual(preview['scope']['omitted_rows'],[7])
        self.assertIn('partial_store',[entry['code'] for entry in preview['issues']])
        self.assertFalse(self.tasks.list())

    def test_same_submission_retries_are_atomic_and_stale_first_creation_rejected(self):
        request = dict(id=self.document['id'],revision=1,confirmed=True,parameters=batch_parameters())
        def submit():
            return intake_dispatch.dispatch_request(self.reviews,self.tasks,request,create=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = [future.result() for future in (pool.submit(submit),pool.submit(submit))]
        self.assertEqual([status for _,status in outcomes],[200,200])
        self.assertEqual({data['task']['id'] for data,_ in outcomes},{self.tasks.list()[0]['id']})
        self.assertEqual(sorted(data['reused'] for data,_ in outcomes),[False,True])
        self.reviews.save(self.document['id'],1,self.review['config'])
        self.assertTrue(submit()[0]['reused'])
        with self.assertRaises(Conflict):
            intake_dispatch.dispatch_request(self.reviews,self.tasks,{**request,'name':'另一份任务'},create=True)
        self.assertEqual(len(self.tasks.list()),1)

    def test_review_changed_during_preflight_blocks_creation_including_shared_store(self):
        shared = multiwarehouse.MultiWarehouseStore(self.intakes.path)
        request = dict(id=self.document['id'],revision=1,confirmed=True,parameters=batch_parameters())
        prepare = intake_dispatch.prepare
        def update_after_preflight(*args):
            output = prepare(*args)
            self.reviews.save(self.document['id'],1,self.review['config'])
            return output
        with patch.object(intake_dispatch,'prepare',side_effect=update_after_preflight), self.assertRaises(Conflict):
            intake_dispatch.dispatch_request(self.reviews,shared,request,create=True)
        self.assertFalse(shared.list())
        payload,status = intake_dispatch.dispatch_request(self.reviews,shared,{**request,'revision':2},create=True)
        self.assertEqual(status,200,payload)
        self.assertEqual(shared.read(payload['task']['id'])['intake_source']['review_revision'],2)

    def test_missing_address_can_be_reviewed_without_changing_source(self):
        document = self.intakes.create('待补地址','missing-address.xlsx',intake.sample_xlsx())
        config = batch_review(document)
        config['identities']['8']['address'] = '核对后补入的门店地址'
        review = self.reviews.save(document['id'],0,config)
        request = dict(id=document['id'],revision=1,confirmed=True,parameters=batch_parameters())
        payload,status = intake_dispatch.dispatch_request(self.reviews,self.tasks,request,create=True)
        self.assertEqual(status,200,payload)
        self.assertEqual(payload['source']['stores'][1]['address'],'核对后补入的门店地址')
        self.assertIn('address',payload['source']['rows'][2]['manual_identity_fields'])
        self.assertIsNone(review['assessment']['rows'][2]['source_refs']['address'])
        self.assertEqual(self.intakes.read(document['id']),document)


class IntakeDispatchHTTPTests(unittest.TestCase):
    setUp = test_parallel_http.ParallelHTTPTests.setUp
    tearDown = test_parallel_http.ParallelHTTPTests.tearDown
    request = test_parallel_http.ParallelHTTPTests.request

    def test_template_preview_create_and_reuse_http(self):
        document = self.intakes.create('门店配送','batch.xlsx',source_xlsx())
        reviews = intake_review.ReviewStore(self.intakes.path,self.intakes)
        reviews.save(document['id'],0,batch_review(document))
        tasks = multiwarehouse.MultiWarehouseStore(Path(self.temp.name)/'warehouse.sqlite3')
        query = f'?id={document["id"]}&revision=1'
        request = dict(id=document['id'],revision=1,parameters=batch_parameters())
        with patch.object(server,'multiwarehouse_store',tasks):
            status,_,raw = self.request('/api/intake/dispatch-template'+query)
            self.assertEqual(status,200)
            self.assertEqual(json.loads(raw)['scope']['selected_rows'],[6,7,8])
            status,_,raw = self.request('/api/intake/dispatch-preview',request)
            self.assertEqual(status,200)
            self.assertTrue(json.loads(raw)['ready'])
            self.assertFalse(tasks.list())
            status,_,_ = self.request('/api/intake/dispatch',request)
            self.assertEqual(status,400)
            status,_,raw = self.request('/api/intake/dispatch',{**request,'confirmed':True})
            self.assertEqual(status,200,raw)
            saved = json.loads(raw)
            status,_,raw = self.request('/api/intake/dispatch',{**request,'confirmed':True})
            self.assertEqual(status,200)
            self.assertEqual(json.loads(raw)['task']['id'],saved['task']['id'])
            self.assertTrue(json.loads(raw)['reused'])
            self.assertEqual(len(tasks.list()),1)
