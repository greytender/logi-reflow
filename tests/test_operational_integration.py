"""Operational policy cannot be lost between input, search, review and scene."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
import unittest

import multiwarehouse as mw
import operational_constraints as op
import server
from store import Conflict


class OperationalIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store=mw.MultiWarehouseStore(Path(self.temp.name)/'tasks.db')
        self.data=mw.operational_sample()

    def solved(self):
        t=self.store.create('专项构造测试',self.data)
        return self.store.solve(t['id'],t['revision'])

    def test_search_shared_group_scene_and_scope(self):
        t=self.solved();plan=mw.current_plan(t);g=plan['warehouses']['WH']
        self.assertEqual(plan['summary']['optimized']['served'],3)
        self.assertEqual(plan['summary']['optimized']['trips'],1)
        self.assertTrue(g['operational_verification']['passed'])
        self.assertGreater(g['operational_verification']['search']['shared_group_assignments_checked'],0)
        scene=mw.spatial_view(t,'WH','TRUCK_1',plan['id'])
        self.assertEqual(scene['task_ref']['input_sha256'],mw.app.fingerprint(t['data']))
        self.assertEqual(scene['classification'],'candidate_simulation_not_live_cargo')
        self.assertEqual(scene['display_names']['vehicles']['TRUCK_1'],'车辆1')
        self.assertEqual(scene['display_names']['orders']['STORE_003'],'门店3')
        self.assertTrue(scene['scene']['routes'])
        with self.assertRaises(Conflict):mw.spatial_view(t,'WH','TRUCK_1','old-plan')
        with self.assertRaises(ValueError):mw.spatial_view(t,'WH','OTHER')

    def test_parameter_changes_are_local_and_stale_candidate_rejected(self):
        t=self.solved();other=self.store.create('独立批次',self.data)
        p=mw.current_plan(t);changed=deepcopy(t['data'])
        changed['warehouses'][0]['operational_policy']['cold_boxes'][0]['valid_minutes']=15
        t=self.store.save(t['id'],t['revision'],t['name'],changed)
        with self.assertRaises(Conflict):self.store.adopt(t['id'],t['revision'],p['id'],True)
        self.assertEqual(self.store.read(other['id'])['data'],other['data'])
        t=self.store.solve(t['id'],t['revision'])
        self.assertGreater(mw.current_plan(t)['summary']['optimized']['unserved'],0)
        with self.assertRaises(ValueError):self.store.adopt(t['id'],t['revision'],mw.current_plan(t)['id'],True)

    def test_confirmation_reaudits_policy_and_remaining_cannot_bypass(self):
        t=self.solved();p=mw.current_plan(t)
        g=p['warehouses']['WH'];vid=next(v for v,r in g['optimized']['routes'].items() if r['sequence'])
        t=self.store.adopt(t['id'],t['revision'],p['id'],True)
        import csv, io
        rows=list(csv.DictReader(io.StringIO(self.store.export_csv(t['id'],t['revision']).lstrip('\ufeff'))))
        adjusted=g['optimized']['routes'][vid]['operational']['adjusted_route']['stops']
        self.assertEqual([float(r['预计完成分钟']) for r in rows],[s['finish'] for s in adjusted])
        child=t['execution_tasks']['WH']
        with self.assertRaisesRegex(ValueError,'共享冷链箱'):
            self.store.calculate_remaining(child)
        source=op.evaluate_plan(child['data'],child['plans'][0]['result']['optimized'],child['data']['operational_policy'])
        self.assertTrue(source['passed'])

    def test_spatial_http_and_navigation_security(self):
        t=self.solved();previous=server.multiwarehouse_store;server.multiwarehouse_store=self.store
        self.addCleanup(setattr,server,'multiwarehouse_store',previous)
        http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread=Thread(target=http.serve_forever,daemon=True);thread.start()
        def finish():http.shutdown();http.server_close();thread.join()
        self.addCleanup(finish)
        def get(path,headers=None):
            c=HTTPConnection('127.0.0.1',http.server_port)
            c.request('GET',path,headers=headers or {});r=c.getresponse();raw=r.read();c.close();return r.status,raw
        path='/api/multiwarehouse/spatial?id='+t['id']+'&warehouse_id=WH&plan_id='+mw.current_plan(t)['id']
        code,raw=get(path);self.assertEqual(code,200);self.assertIn('scene',json.loads(raw))
        navigation={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'}
        self.assertEqual(get('/spatial.html',navigation)[0],200)
        self.assertEqual(get(path,navigation)[0],403)
        self.assertEqual(get('/workspace.html',{'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'cors','Sec-Fetch-Dest':'empty'})[0],403)
        self.assertEqual(get('/workspace.html',{'Host':'untrusted.example',**navigation})[0],403)

    def test_temperature_endpoint_locks_inputs_preserves_history_and_agent_risk(self):
        import platform_agent
        t=self.solved();p=mw.current_plan(t)
        t=self.store.adopt(t['id'],t['revision'],p['id'],True)
        before=mw.view(t)['execution']['WH']['routes']['TRUCK_1']['anchor']
        previous=server.multiwarehouse_store;server.multiwarehouse_store=self.store
        self.addCleanup(setattr,server,'multiwarehouse_store',previous)
        http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread=Thread(target=http.serve_forever,daemon=True);thread.start()
        def finish():http.shutdown();http.server_close();thread.join()
        self.addCleanup(finish)
        def post(task,record):
            c=HTTPConnection('127.0.0.1',http.server_port)
            c.request('POST','/api/multiwarehouse/temperature',body=json.dumps(dict(id=task['id'],revision=task['revision'],warehouse_id='WH',temperature=record,reviewed=True)),headers={'Content-Type':'application/json'})
            response=c.getresponse();data=json.loads(response.read());c.close()
            self.assertEqual(response.status,200,data);return data['task']
        reading=dict(kind='reading',vehicle='TRUCK_1',cold_box='SHARED_COLD_1',phase='loading',time=505,
                     temperature_c=10,source='simulated',note='测试温度计记录',action='')
        t=post(t,reading)
        self.assertTrue(mw.has_facts(t))
        state=mw.view(t);route=state['execution']['WH']['routes']['TRUCK_1']
        self.assertEqual(state['temperature_records'],1)
        self.assertEqual(route['anchor'],before)
        self.assertEqual(route['temperature_status'],'EXCURSION_OPEN')
        self.assertTrue(any(x['kind']=='temperature_excursion' for x in platform_agent.risks(t)['items']))
        changed=deepcopy(t['data']);changed['vehicles'][0]['max_weight_kg']+=1
        with self.assertRaises(Conflict):self.store.save(t['id'],t['revision'],t['name'],changed)
        first=t['execution_tasks']['WH']['temperature_records'][0]
        t=post(t,dict(kind='action',vehicle='TRUCK_1',cold_box='SHARED_COLD_1',target_id=first['id'],time=510,
                     source='simulated',note='调度处理记录',action='隔离货物并联系调度'))
        self.assertEqual(mw.view(t)['execution']['WH']['routes']['TRUCK_1']['temperature_status'],'EXCURSION_HANDLED')
        package=self.store.export_package(t['id'],t['revision'])
        self.assertEqual(package['task']['execution_tasks']['WH']['temperature_records'][0],first)
        self.assertEqual(len(package['task']['execution_tasks']['WH']['temperature_records']),2)


if __name__=='__main__':unittest.main()
