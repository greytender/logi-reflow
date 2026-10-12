from copy import deepcopy
import unittest
import json,threading
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib import request,error
from http.server import ThreadingHTTPServer
from unittest.mock import patch
import continuous,server
import loading
import loading_audit as audit
import matrix_dispatch as md


def box(id,order,length=50,width=50,height=50,weight=5,fragile=False,limit=100,rotate=False):
    return dict(id=id,order=order,sku=id,length_cm=length,width_cm=width,height_cm=height,
                weight_kg=weight,fragile=fragile,max_top_load_kg=limit,rotate_base=rotate)


def vehicle(length=100,width=100,height=100):
    return dict(length_cm=length,width_cm=width,height_cm=height,max_weight_kg=100)


def pos(id,x=0,y=0,z=0,dx=50,dy=50,dz=50):return dict(id=id,x=x,y=y,z=z,dx=dx,dy=dy,dz=dz)


class LoadingAuditTests(unittest.TestCase):
    def test_two_box_exact_geometry_bound_does_not_claim_stacking_feasibility(self):
        items=[box('a','A',length=100,width=70,height=70),box('b','B',length=100,width=70,height=70)]
        self.assertEqual(audit.geometry_bound(items,vehicle(120,100,100))['status'],'PROVEN_GEOMETRY_INFEASIBLE')
        self.assertEqual(audit.geometry_bound(items,vehicle(200,100,100))['status'],'GEOMETRY_ONLY_FEASIBLE')
        self.assertEqual(audit.geometry_bound(items+[box('c','C')],vehicle(200,100,100))['status'],'UNKNOWN')

    def test_overlap_bounds_missing_and_orientation(self):
        items=[box('a','A'),box('b','B')]
        for placements in ([pos('a'),pos('b')],[pos('a',x=80),pos('b')],[pos('a')],
                           [pos('a',dz=60),pos('b',x=50)]):
            self.assertFalse(audit.audit(items,vehicle(),['A','B'],placements)['passed'])
        self.assertTrue(audit.audit(items,vehicle(),['A','B'],[pos('a',x=50),pos('b')])['passed'])

    def test_lifo_blocks_other_store_but_allows_same_store(self):
        placements=[pos('a'),pos('b',x=50)]
        self.assertFalse(audit.audit([box('a','A'),box('b','B')],vehicle(),['A','B'],placements)['passed'])
        self.assertTrue(audit.audit([box('a','A'),box('b','A')],vehicle(),['A'],placements)['passed'])

    def test_full_support_fragility_and_delivery_support_order(self):
        bottom=box('b','B');top=box('a','A')
        placement=[pos('b'),pos('a',z=50)]
        self.assertTrue(audit.audit([bottom,top],vehicle(),['A','B'],placement)['passed'])
        for variant in ([{**bottom,'fragile':True},top],[{**bottom,'max_top_load_kg':4},top]):
            self.assertFalse(audit.audit(variant,vehicle(),['A','B'],placement)['passed'])
        self.assertFalse(audit.audit([bottom,top],vehicle(),['B','A'],placement)['passed'])
        self.assertFalse(audit.audit([bottom,top],vehicle(),['A','B'],[pos('b'),pos('a',x=1,z=50)])['passed'])

    def test_cumulative_stack_weight_reaches_bottom(self):
        items=[box('b','C',height=30,weight=1,limit=8),box('m','B',height=30,weight=5),box('t','A',height=30,weight=5)]
        p=[pos('b',dz=30),pos('m',z=30,dz=30),pos('t',z=60,dz=30)]
        r=audit.audit(items,vehicle(),['A','B','C'],p)
        self.assertFalse(r['passed']);self.assertEqual(r['top_loads_kg']['b'],10)

    def test_nonfinite_geometry_and_overweight(self):
        self.assertFalse(audit.audit([box('a','A')],vehicle(),['A'],[pos('a',x=float('nan'))])['passed'])
        self.assertFalse(audit.audit([box('a','A',weight=101)],vehicle(),['A'],[pos('a')])['passed'])

    def test_step_replay_rejects_lower_first_and_blocked_same_store(self):
        items=[box('b','A'),box('t','A')];p=[pos('b'),pos('t',z=50)]
        self.assertTrue(audit.audit_steps(items,vehicle(),['A'],p,['b','t'],[dict(order='A',box_ids=['t','b'])])['passed'])
        self.assertFalse(audit.audit_steps(items,vehicle(),['A'],p,['b','t'],[dict(order='A',box_ids=['b','t'])])['passed'])
        self.assertFalse(audit.audit_steps(items,vehicle(),['A'],p,['t','b'],[dict(order='A',box_ids=['t','b'])])['passed'])
        p=[pos('b'),pos('t',x=50)]
        self.assertFalse(audit.audit_steps(items,vehicle(),['A'],p,['b','t'],[dict(order='A',box_ids=['b','t'])])['passed'])


class LoadingSolverTests(unittest.TestCase):
    def setUp(self):
        self.sample=loading.sample();self.data=md.load_files(self.sample['csv_files'])

    def test_volume_feasible_pair_cannot_fit_small_vehicle_but_joint_changes_fleet(self):
        r=loading.solve(self.sample['csv_files'],self.sample['loading_profile'])
        self.assertTrue(r['complete']);self.assertTrue(r['same_served_orders'])
        self.assertEqual(r['baseline_loading']['VAN']['status'],'PROVEN_GEOMETRY_INFEASIBLE')
        # Two 100x70x70 boxes cannot stack in height100 and cannot share floor120x100.
        self.assertEqual(len(r['baseline']['routes']['VAN']['sequence']),2)
        self.assertEqual(len(r['optimized']['routes']['TRUCK']['sequence']),2)
        self.assertTrue(r['optimized']['routes']['TRUCK']['loading']['audit']['passed'])
        self.assertGreater(r['optimized']['metrics']['cost'],r['baseline']['metrics']['cost'])
        self.assertFalse(r['dispatchable']);self.assertTrue(r['departure_only'])

    def test_missing_rules_and_dimensions_never_inferred(self):
        for change in ('missing','volume','vehicle','extra','flag','kind','dimension'):
            raw=deepcopy(self.sample['loading_profile'])
            if change=='missing':raw['items'].pop()
            elif change=='volume':raw['items'][0]['length_cm']=99
            elif change=='vehicle':raw['vehicles'].pop()
            elif change=='extra':raw['items'][0]['guess']=1
            elif change=='flag':raw['items'][0]['fragile']='true'
            elif change=='kind':raw['data_kind']='enterprise'
            else:raw['items'][0]['length_cm']=True
            with self.subTest(change=change),self.assertRaises(ValueError):loading.profile(self.data,raw)

    def test_rotations_and_budget_result_not_forged(self):
        item=box('a','A',length=80,width=40,height=40)
        work=dict(placement_checks=0,budget_exhausted=False)
        self.assertIsNone(loading.constructive([item],vehicle(40,80,40),['A'],work))
        item['rotate_base']=True
        r=loading.constructive([item],vehicle(40,80,40),['A'],dict(placement_checks=0,budget_exhausted=False))
        self.assertTrue(r['audit']['passed']);self.assertEqual(r['placements'][0]['dx'],40)
        work=dict(placement_checks=200000,budget_exhausted=False)
        self.assertIsNone(loading.constructive([item],vehicle(),['A'],work));self.assertTrue(work['budget_exhausted'])

    def test_deterministic_output_geometry_and_inputs_not_mutated(self):
        before=deepcopy(self.sample);a=loading.solve(**dict(files=self.sample['csv_files'],raw=self.sample['loading_profile']))
        b=loading.solve(self.sample['csv_files'],self.sample['loading_profile'])
        self.assertEqual(self.sample,before);self.assertEqual(a['optimized']['routes'],b['optimized']['routes'])
        for vid,route in a['optimized']['routes'].items():
            items=[x for x in a['items'] if x['order'] in route['sequence']]
            self.assertTrue(audit.audit(items,a['vehicles'][vid],route['sequence'],route['loading']['placements'])['passed'])

    def test_sidecar_limit_counts_boxes_not_sku_rows(self):
        data=deepcopy(self.data);data['orders'][0]['lines'][0]['quantity']=81
        with self.assertRaises(ValueError):loading.profile(data,self.sample['loading_profile'])

    def test_http_solves_without_changing_continuous_scene_and_rejects_missing_fields(self):
        with TemporaryDirectory() as tmp:
            store=continuous.DispatchStore(Path(tmp)/'test.sqlite3')
            before=store.act('start',dict(csv_files=self.sample['csv_files']))
            with patch.object(server,'dispatch',store):
                http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
                try:
                    url=f'http://127.0.0.1:{http.server_port}/api/loading/solve'
                    req=request.Request(url,data=json.dumps(self.sample).encode(),headers={'Content-Type':'application/json'})
                    with request.urlopen(req) as response:r=json.load(response)
                    self.assertTrue(r['complete']);self.assertEqual(store.read(),before)
                    req=request.Request(url,data=json.dumps(dict(csv_files=self.sample['csv_files'])).encode(),headers={'Content-Type':'application/json'})
                    with self.assertRaises(error.HTTPError) as caught:request.urlopen(req)
                    self.assertEqual(caught.exception.code,400);caught.exception.close();self.assertEqual(store.read(),before)
                finally:http.shutdown();http.server_close();thread.join()


if __name__=='__main__':unittest.main()
