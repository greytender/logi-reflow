from copy import deepcopy
import json
import unittest

import test_parallel_http


class ReviewHTTPTests(unittest.TestCase):
    setUp=test_parallel_http.ParallelHTTPTests.setUp
    tearDown=test_parallel_http.ParallelHTTPTests.tearDown
    request=test_parallel_http.ParallelHTTPTests.request

    def create(self):
        status,_,raw=self.request('/api/intake/create',dict(sample=True,name='映射草稿演练'))
        self.assertEqual(status,200)
        return json.loads(raw)['document']

    def test_incomplete_draft_save_conflict_export_and_original_is_immutable(self):
        d=self.create()
        status,_,raw=self.request('/api/intake/review?id='+d['id'])
        self.assertEqual(status,200);review=json.loads(raw)['review']
        self.assertEqual(review['revision'],0);self.assertEqual(review['config']['rows'],[6,7,8])
        self.assertTrue(all(u is None for u in review['config']['units'].values()))
        config=deepcopy(review['config']);config['units']['regular_weight']='kg';config['units']['regular_volume']='L'
        config['identities']['6']=dict(order_id='ORDER_A',store_id='STORE_A',batch_id='BATCH_A',note='构造手工确认')
        status,_,raw=self.request('/api/intake/review',dict(id=d['id'],revision=0,config=config))
        self.assertEqual(status,200);saved=json.loads(raw)['review'];self.assertEqual(saved['revision'],1)
        self.assertFalse(saved['assessment']['can_solve'])
        status,_,_=self.request('/api/intake/review',dict(id=d['id'],revision=0,config=config))
        self.assertEqual(status,409)
        status,_,_=self.request('/api/intake/review-export?id='+d['id']+'&revision=0')
        self.assertEqual(status,409)
        status,headers,raw=self.request('/api/intake/review-export?id='+d['id']+'&revision=1')
        self.assertEqual(status,200);self.assertIn('review-',headers['Content-Disposition'])
        package=json.loads(raw);self.assertEqual(package['revision'],1);self.assertEqual(len(package['versions']),1)
        self.assertEqual(package['source_sha256'],d['sha256']);self.assertEqual(self.intakes.read(d['id']),d)
        self.assertEqual(self.db.list(),[])

    def test_malformed_and_cross_origin_review_writes_cannot_change_draft(self):
        d=self.create()
        status,_,raw=self.request('/api/intake/review?id='+d['id']);initial=json.loads(raw)['review']
        invalid=deepcopy(initial['config']);invalid['rows']=[6,999]
        status,_,_=self.request('/api/intake/review',dict(id=d['id'],revision=0,config=invalid))
        self.assertEqual(status,400)
        status,_,_=self.request('/api/intake/review',dict(id=d['id'],revision=0,config=initial['config']),{'Origin':'https://unrelated.invalid'})
        self.assertEqual(status,403)
        status,_,raw=self.request('/api/intake/review?id='+d['id'])
        self.assertEqual(json.loads(raw)['review']['revision'],0)
