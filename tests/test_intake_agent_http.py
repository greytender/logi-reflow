import json
import time
import unittest

import test_parallel_http


class IntakeAgentHTTPTests(unittest.TestCase):
    setUp=test_parallel_http.ParallelHTTPTests.setUp
    tearDown=test_parallel_http.ParallelHTTPTests.tearDown
    request=test_parallel_http.ParallelHTTPTests.request

    def test_local_job_adoption_conflicts_and_source_export(self):
        status,_,raw=self.request('/api/intake/create',dict(sample=True,name='Agent HTTP fixture'))
        self.assertEqual(status,200);document=json.loads(raw)['document'];id=document['id']
        _,_,raw=self.request('/api/intake/review?id='+id);review=json.loads(raw)['review']
        body=dict(id=id,revision=0,config=review['config'],mode='local',request_id='http-fixture-job')
        status,_,raw=self.request('/api/intake/agent-start',body);self.assertEqual(status,200)
        job=json.loads(raw)['job']
        for _ in range(40):
            _,_,raw=self.request('/api/intake/agent-job?id='+id+'&job_id='+job['id']);job=json.loads(raw)['job']
            if job['status']!='running':break
            time.sleep(.02)
        self.assertEqual(job['status'],'succeeded',job.get('error'))
        status,_,raw=self.request('/api/intake/agent-start',body)
        self.assertEqual(status,200);self.assertEqual(json.loads(raw)['job']['id'],job['id'])
        adopt=dict(id=id,job_id=job['id'],config=review['config'],reviewed=True,change_ids=[],include_store_ids=True,
            unit_overrides=dict(regular_weight='kg',cold_weight='kg',regular_volume='m3',cold_volume='m3'))
        status,_,_=self.request('/api/intake/agent-apply',adopt,{'Origin':'https://unrelated.invalid'})
        self.assertEqual(status,403)
        status,_,raw=self.request('/api/intake/agent-apply',adopt);self.assertEqual(status,200)
        saved=json.loads(raw)['review'];self.assertEqual(saved['revision'],1)
        self.assertEqual(saved['assessment']['rows'][0]['normalized']['regular_weight_kg'],14)
        status,_,raw=self.request('/api/intake/review-export?id='+id+'&revision=1')
        self.assertEqual(status,200);self.assertEqual(json.loads(raw)['agent_provenance']['job_id'],job['id'])
        status,_,_=self.request('/api/intake/agent-start',{**body,'request_id':'new-old-revision'})
        self.assertEqual(status,409)
        self.assertEqual(self.intakes.read(id),document);self.assertEqual(self.db.list(),[])

    def test_invalid_job_and_cross_origin_start_are_rejected(self):
        status,_,_=self.request('/api/intake/agent-start',{}, {'Origin':'https://unrelated.invalid'})
        self.assertEqual(status,403)
        status,_,_=self.request('/api/intake/agent-job?id=unknown&job_id=unknown')
        self.assertEqual(status,400)


if __name__=='__main__':unittest.main()
