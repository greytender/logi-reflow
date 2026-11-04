from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
import application
import execution
import integration_contract as contract


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.store=application.ApplicationStore(Path(self.temp.name)/'t.db')
        self.task=self.store.create('交接演练',application.sample())
    def tearDown(self):self.temp.cleanup()
    def confirmed(self):
        t=self.store.solve(self.task['id'],self.task['revision'])
        return self.store.adopt(t['id'],t['revision'],t['plans'][-1]['id'],True)

    def test_deterministic_readonly_and_unconfirmed_is_blocked(self):
        before=deepcopy(self.task);p=contract.export_handoff(self.task)
        self.assertEqual(p,contract.export_handoff(self.task));self.assertEqual(self.task,before)
        self.assertEqual(p['dispatch_status'],'not_sent');self.assertEqual(p['integration_status'],'not_connected')
        self.assertFalse(p['readiness']['local_checks_passed'])
        self.assertEqual(p['readiness']['status'],'blocked');self.assertTrue(contract.validate_handoff(p)['passed'])

    def test_local_confirmation_is_not_enterprise_readiness(self):
        t=self.confirmed();p=contract.export_handoff(t)
        self.assertTrue(p['readiness']['local_checks_passed']);self.assertEqual(p['readiness']['status'],'blocked')
        self.assertEqual(p['enterprise_mapping']['status'],'pending')
        self.assertTrue(all(o['enterprise_store_id'] is None for o in p['enterprise_mapping']['orders']))

    def test_stale_original_plan_not_current_forecast(self):
        t=self.confirmed();data=deepcopy(t['data']);data['orders'][0]['lines'][0]['quantity']+=1
        t=self.store.save(t['id'],t['revision'],t['name'],data);p=contract.export_handoff(t)
        self.assertEqual(p['readiness']['local_plan_status'],'stale');self.assertIsNone(p['current_remaining_forecast'])

    def test_receipts_and_observations_are_separate_facts_and_change_identity(self):
        t=self.confirmed();first=contract.export_handoff(t)
        vid,r=next(iter(execution.view(t)['routes'].items()))
        t=self.store.receipt(t['id'],t['revision'],dict(kind='stop',vehicle=vid,stop_id=r['next_stop'],finish=600,
            pickup=dict(cartons=20,totes=3),source='simulated',note='SECRET_NOTE'),True)
        t=self.store.observe(t['id'],t['revision'],dict(kind='wait',vehicle=vid,node=r['next_stop'],observed_at=630,
            source='simulated',note='SECRET_OBSERVATION'),True)
        t['api_key']='DO_NOT_EXPORT';p=contract.export_handoff(t)
        self.assertNotEqual(first['handoff_id'],p['handoff_id'])
        self.assertEqual(len(p['recorded_facts']['receipts']),1);self.assertEqual(len(p['recorded_facts']['observations']),1)
        self.assertEqual(p['current_remaining_forecast']['routes'][vid]['anchor']['time'],630)
        remaining=[s['id'] for x in p['current_remaining_forecast']['routes'].values() for s in x['remaining']]
        self.assertNotIn(r['next_stop'],remaining)
        serialized=json.dumps(p);self.assertNotIn('SECRET_',serialized);self.assertNotIn('DO_NOT_EXPORT',serialized)

    def test_mutation_or_false_dispatch_claim_rejected(self):
        p=contract.export_handoff(self.confirmed())
        for field,value in [('dispatch_status','sent'),('integration_status','connected'),('handoff_id','wrong')]:
            bad=deepcopy(p);bad[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):contract.validate_handoff(bad)
        bad=deepcopy(p);bad['enterprise_mapping']['orders'][0]['enterprise_store_id']='FAKE'
        with self.assertRaises(ValueError):contract.validate_handoff(bad)

    @staticmethod
    def rehash(package):
        # A content hash is public, so semantic checks must survive recomputation.
        from hashlib import sha256
        payload={k:v for k,v in package.items() if k!='handoff_id'}
        package['handoff_id']=sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,
            separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()
        return package

    def test_recomputed_hash_cannot_hide_omitted_delivery_or_false_next_store(self):
        p=contract.export_handoff(self.confirmed())
        vid=next(iter(p['current_remaining_forecast']['routes']))
        bad=deepcopy(p);bad['current_remaining_forecast']['routes'][vid]['remaining'].pop()
        with self.assertRaisesRegex(ValueError,'不能漏单或换车'):contract.validate_handoff(self.rehash(bad))
        bad=deepcopy(p);bad['current_remaining_forecast']['routes'][vid]['next_stop']='UNKNOWN'
        with self.assertRaisesRegex(ValueError,'下一门店'):contract.validate_handoff(self.rehash(bad))

    def test_recomputed_hash_cannot_claim_enterprise_mapping_or_pass_stale_input(self):
        p=contract.export_handoff(self.confirmed())
        bad=deepcopy(p);bad['enterprise_mapping']['orders'][0]['enterprise_store_id']='FAKE'
        with self.assertRaisesRegex(ValueError,'待确认映射'):contract.validate_handoff(self.rehash(bad))
        bad=deepcopy(p);bad['task_ref']['input_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'对应当前输入'):contract.validate_handoff(self.rehash(bad))

    def test_observation_time_changes_identity_and_anchor_must_match_fact(self):
        t=self.confirmed();vid=next(iter(execution.view(t)['routes']))
        t=self.store.observe(t['id'],t['revision'],dict(kind='wait',vehicle=vid,node='WH',
            observed_at=600,source='simulated'),True)
        first=contract.export_handoff(t)
        changed=deepcopy(t);changed['observations'][-1]['observed_at']=601
        second=contract.export_handoff(changed)
        self.assertNotEqual(first['handoff_id'],second['handoff_id'])
        self.assertEqual(second['current_remaining_forecast']['routes'][vid]['anchor']['time'],601)
        bad=deepcopy(first);bad['current_remaining_forecast']['routes'][vid]['anchor']['time']=602
        with self.assertRaisesRegex(ValueError,'锚点的位置'):contract.validate_handoff(self.rehash(bad))
        bad=deepcopy(first);bad['current_remaining_forecast']['routes'][vid]['anchor']['kind']='gps'
        with self.assertRaisesRegex(ValueError,'GPS'):contract.validate_handoff(self.rehash(bad))

    def test_overload_is_exported_for_review_but_local_checks_are_blocked(self):
        t=self.confirmed();vid,r=next(iter(execution.view(t)['routes'].items()))
        t=self.store.receipt(t['id'],t['revision'],dict(kind='stop',vehicle=vid,stop_id=r['next_stop'],finish=600,
            pickup=dict(cartons=10000,totes=0),source='simulated'),True)
        p=contract.export_handoff(t)
        self.assertFalse(p['readiness']['local_checks_passed'])
        self.assertEqual(p['readiness']['local_plan_status'],'constraint_risk')
        self.assertEqual(p['recorded_facts']['receipts'][0]['pickup']['cartons'],10000)
        self.assertTrue(p['current_remaining_forecast']['routes'][vid]['warnings'])

    def test_unknown_fields_and_free_notes_are_not_copied_into_package(self):
        t=self.confirmed();t['raw_file']='SECRET_FILE';t['authorization']='SECRET_TOKEN'
        plan=next(p for p in t['plans'] if p['id']==t['adopted_plan_id'])
        for r in plan['result']['optimized']['routes'].values():
            if 'fee' in r:r['fee']['api_key']='SECRET_NESTED'
        p=contract.export_handoff(t)
        self.assertNotIn('SECRET_',json.dumps(p))
