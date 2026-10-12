from copy import deepcopy
import csv
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from urllib import request,error
from http.server import ThreadingHTTPServer
import continuous
import dispatch_handoff as handoff
import matrix_dispatch as md
import server
from verify_matrix_dispatch import audit

ROOT=Path(__file__).resolve().parents[1]


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=continuous.DispatchStore(Path(self.tmp.name)/'test.sqlite3')
        files={n:(ROOT/'examples/continuous-small'/n).read_text(encoding='utf-8') for n in md.FILES}
        self.scene=self.store.act('start',dict(csv_files=files))

    def act(self,action,**body):
        self.scene=self.store.act(action,dict(id=self.scene['id'],revision=self.scene['revision'],**body))
        if not self.scene['live_plan'].get('needs_replan'):audit(self.scene)

    def urgent(self):
        self.act('event',kind='urgent',event=dict(time=420,store='STORE_002',due=780,close=960,
            lines=[dict(sku='NEW',quantity=2,unit_weight_kg=7,unit_volume_m3=.03)]))

    def first(self):
        return min(((vid,r['stops'][0]) for vid,r in self.scene['live_plan']['routes'].items() if r['stops']),key=lambda p:p[1]['arrival'])

    def receipt(self,vid,oid,kind,time):
        self.act('receipt',vehicle=vid,order=oid,receipt=kind,time=time,source='manual_simulation')

    def test_candidate_never_leaks_into_adopted_handoff_and_read_is_pure(self):
        self.urgent();before=deepcopy(self.scene);r=handoff.report(self.scene)
        self.assertEqual(r['candidate_version'],2);self.assertEqual(r['adopted_version'],1)
        self.assertNotIn('E01',[x['order'] for x in r['rows']]);self.assertEqual(r['pickups'],[])
        self.assertEqual(before,self.scene);self.assertEqual(before,self.store.read())
        self.act('adopt',candidate=2);r=handoff.report(self.scene)
        row=next(x for x in r['rows'] if x['order']=='E01')
        self.assertEqual(row['cargo_owner'],'warehouse');self.assertEqual(row['pickup_task'],'warehouse:E01')
        self.assertEqual(row['receipt_source'],'none');self.assertIsNone(row['signed_at'])
        self.assertEqual(r['metrics']['signed_orders'],0)

    def test_estimated_arrival_is_not_a_receipt(self):
        vid,stop=self.first();self.act('advance',time=stop['arrival']+1)
        r=handoff.report(self.scene);row=next(x for x in r['rows'] if x['order']==stop['id'])
        self.assertIsNone(row['arrived_at']);self.assertIsNone(row['waiting_minutes'])
        self.assertEqual(r['metrics']['signed_orders'],0)

    def test_actual_wait_service_and_lateness_follow_receipts(self):
        vid,stop=self.first();oid=stop['id'];arrival=stop['arrival']+2
        self.receipt(vid,oid,'arrived',arrival)
        self.act('advance',time=arrival+3)
        row=next(x for x in handoff.report(self.scene)['rows'] if x['order']==oid)
        self.assertEqual(row['waiting_minutes'],3);self.assertIsNone(row['completed_service_minutes'])
        self.receipt(vid,oid,'unloading',arrival+4)
        order=next(x for x in self.scene['live_plan']['data']['orders'] if x['id']==oid)
        signed=arrival+4+order['service_minutes']+1
        self.receipt(vid,oid,'signed',signed)
        r=handoff.report(self.scene);row=next(x for x in r['rows'] if x['order']==oid)
        self.assertEqual(row['waiting_minutes'],4);self.assertEqual(row['completed_service_minutes'],order['service_minutes']+1)
        deadline=signed if row['window_rule']=='finish' else arrival
        self.assertEqual(row['actual_late_minutes'],round(max(0,deadline-order['due']),2))
        self.assertEqual(r['metrics']['signed_orders'],1);self.assertIsNone(row['predicted_finish'])
        self.assertEqual(row['cargo_owner'],'delivered');self.assertEqual(row['signed_at'],signed)

    def test_failed_reforecast_suppresses_stale_eta_and_keeps_ownership(self):
        self.act('advance',time=1400);self.assertTrue(self.scene['live_plan']['needs_replan'])
        r=handoff.report(self.scene);self.assertFalse(r['prediction_valid'])
        self.assertTrue(all(x['predicted_arrival'] is None and x['predicted_finish'] is None for x in r['rows']))
        self.assertTrue(all(x['next_actor']=='调度' for x in r['rows']))
        self.assertEqual(r['metrics']['signed_orders'],0);self.assertEqual(r['metrics']['overdue_without_rule_receipt'],3)

    def test_csv_roundtrip_source_and_unconfirmed_nulls(self):
        r=handoff.report(self.scene);text=handoff.csv_text(r)
        rows=list(csv.DictReader(io.StringIO(text.lstrip('\ufeff'))))
        self.assertEqual(len(rows),3);self.assertTrue(all(x['signed_at']=='' for x in rows))
        self.assertEqual(rows[0]['adopted_version'],'1');self.assertEqual(len(r['input_source_sha256']),4)
        r['rows'][0]['next_action']='=2+3';self.assertIn("'=2+3",handoff.csv_text(r))

    def test_old_mode_unavailable_and_missing_signature_not_invented(self):
        self.assertFalse(handoff.report(None)['available'])
        s=deepcopy(self.scene);s['progress_mode']='legacy_eta';self.assertFalse(handoff.report(s)['available'])
        s=deepcopy(self.scene);s['cargo']['STORE_001']='delivered'
        with self.assertRaises(ValueError):handoff.report(s)

    def test_http_revision_guard_csv_and_no_state_mutation(self):
        old=server.dispatch;server.dispatch=self.store
        http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{http.server_port}/api/dispatch/handoff?id={self.scene["id"]}&revision='
        try:
            before=self.store.read()
            with self.assertRaises(error.HTTPError) as caught:request.urlopen(base+'0')
            self.assertEqual(caught.exception.code,409);caught.exception.close()
            with request.urlopen(base+str(self.scene['revision'])) as res:r=json.load(res)
            self.assertEqual(r['revision'],self.scene['revision'])
            with request.urlopen(base+str(self.scene['revision'])+'&format=csv') as res:
                self.assertIn('attachment',res.headers['Content-Disposition'])
                self.assertEqual(len(list(csv.DictReader(io.StringIO(res.read().decode('utf-8-sig'))))),3)
            self.assertEqual(before,self.store.read())
        finally:http.shutdown();http.server_close();thread.join();server.dispatch=old


if __name__=='__main__':unittest.main()
