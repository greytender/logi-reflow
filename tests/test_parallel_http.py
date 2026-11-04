import base64
import json
from pathlib import Path
from unittest.mock import patch
import unittest
import application
import execution
import intake
import integration_contract
import server
import test_application


class ParallelHTTPTests(unittest.TestCase):
    request=test_application.ApplicationHTTPTests.request
    def setUp(self):
        test_application.ApplicationHTTPTests.setUp(self)
        self.intakes=intake.IntakeStore(Path(self.temp.name)/'intake.sqlite3')
        self.intake_patch=patch.object(server,'intake_store',self.intakes);self.intake_patch.start()
    def tearDown(self):
        self.intake_patch.stop();test_application.ApplicationHTTPTests.tearDown(self)

    def test_intake_create_read_export_and_invalid_upload(self):
        status,_,raw=self.request('/api/intake/create',dict(sample=True,name='构造核对'))
        self.assertEqual(status,200);d=json.loads(raw)['document'];self.assertEqual(d['audit']['record_rows'],3)
        status,_,raw=self.request('/api/intake/document?id='+d['id']);self.assertEqual(json.loads(raw)['document'],d)
        status,headers,raw=self.request('/api/intake/export?id='+d['id'])
        self.assertEqual(status,200);self.assertIn('attachment',headers['Content-Disposition'])
        self.assertEqual(json.loads(raw)['sha256'],d['sha256'])
        status,_,_=self.request('/api/intake/create',dict(name='bad',filename='bad.xlsx',content_base64='not-base64'))
        self.assertEqual(status,400);self.assertEqual(len(self.intakes.list()),1)
        self.assertEqual(self.db.list(),[])

    def test_cross_origin_intake_write_is_blocked(self):
        status,_,_=self.request('/api/intake/create',dict(sample=True,name='blocked'),{'Origin':'https://other.invalid'})
        self.assertEqual(status,403);self.assertEqual(self.intakes.list(),[])

    def test_observe_and_handoff_export_are_traceable_and_not_sent(self):
        t=self.db.create('演练',application.sample());t=self.db.solve(t['id'],t['revision'])
        t=self.db.adopt(t['id'],t['revision'],t['plans'][-1]['id'],True)
        vid=next(iter(execution.view(t)['routes']))
        status,_,raw=self.request('/api/application/observe',dict(id=t['id'],revision=t['revision'],reviewed=True,
            observation=dict(vehicle=vid,kind='wait',node='WH',observed_at=600,source='simulated')))
        self.assertEqual(status,200);t=json.loads(raw)['task']
        status,headers,raw=self.request(f'/api/application/export?id={t["id"]}&revision={t["revision"]}&format=handoff')
        self.assertEqual(status,200);self.assertIn('handoff.json',headers['Content-Disposition'])
        p=json.loads(raw);self.assertTrue(integration_contract.validate_handoff(p)['passed'])
        self.assertEqual(p['dispatch_status'],'not_sent');self.assertEqual(p['recorded_facts']['observations'][0]['observed_at'],600)
        status,_,_=self.request(f'/api/application/export?id={t["id"]}&revision=1&format=handoff')
        self.assertEqual(status,409)
