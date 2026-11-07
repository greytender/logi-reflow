from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import adaptive_planning as ap
import application
import forecast_learning as learning
import multiwarehouse as mw
import operational_constraints as op
from scripts.diagnose_high_load_operational import fixtures
from scripts.validate_adaptive_upgrade import activate_model,record_days,restricted_staging,learning_preemption,DECISION_AT,instant


class AdaptivePlanningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data,cls.policy=op.sample_data()
        cls.wide=ap.plan_warehouse(cls.data,cls.policy,decision_at=DECISION_AT)

    def test_guarded_witness_round_trip_and_input_immutability(self):
        source=deepcopy((self.data,self.policy))
        restored=json.loads(application.encode(self.wide))
        self.assertTrue(ap.revalidate(restored));self.assertTrue(restored['guarded_candidate_available'])
        self.assertEqual((self.data,self.policy),source)

    def test_input_reference_frames_and_derived_time_tampering_rejected(self):
        for mutation in ('input','reference','frames','time'):
            result=deepcopy(self.wide)
            if mutation=='input':result['planning_input']['orders'][0]['pickup']['totes']+=1
            elif mutation=='reference':result['reference']['cost']+=1
            else:
                report=next(r for r in result['candidate']['reports'].values() if r['frames'])
                if mutation=='time':report['adjusted_route']['return_at']-=1
                else:report['frames'][0]['boxes'][0]['x']+=1
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):ap.revalidate(result)

    def test_staging_limit_retains_immediate_fallback(self):
        case=restricted_staging()
        self.assertTrue(case['legacy']['passed']);self.assertFalse(case['batch']['passed'])
        self.assertTrue(case['adaptive']['passed']);self.assertEqual(case['adaptive']['handling_variant'],'immediate')
        self.assertLessEqual(case['adaptive']['adaptive_search']['placement_checks'],op.MAX_PLACEMENT_CHECKS)

    def test_four_high_load_cases_stay_complete_and_under_guardrails(self):
        for case in fixtures():
            with self.subTest(case=case['id']):
                result=ap.plan_warehouse(case['data'],case['policy'],decision_at=DECISION_AT)
                self.assertTrue(result['guarded_candidate_available']);self.assertTrue(ap.revalidate(result))
                candidate=result['candidate'];self.assertEqual(candidate['cost'],result['reference']['cost'])
                self.assertEqual(set(candidate['used_vehicles']),set(result['reference']['used_vehicles']))
                for load in candidate['load_by_vehicle'].values():
                    self.assertAlmostEqual(load['weight'],.9);self.assertAlmostEqual(load['volume'],.9)

    def test_learning_changes_only_future_planning_input_and_time_gate(self):
        with TemporaryDirectory() as directory:
            store=learning.ForecastStore(Path(directory)/'learn.sqlite3');source=deepcopy(self.data)
            model,_=activate_model(store,self.data)
            result=ap.plan_warehouse(self.data,self.policy,model=model,decision_at=DECISION_AT)
            self.assertNotEqual(result['source_sha256'],result['planning_sha256'])
            self.assertTrue(any(f['source']=='learned' for f in result['forecasts']))
            self.assertEqual(self.data,source);self.assertTrue(ap.revalidate(json.loads(application.encode(result))))
            for at in (model['available_after'],instant(6,8)):
                with self.subTest(at=at),self.assertRaises(ValueError):ap.plan_warehouse(self.data,self.policy,model=model,decision_at=at)

    def test_learning_overload_warns_and_same_actual_breaks_nominal_plan(self):
        with TemporaryDirectory() as directory:
            result=learning_preemption(learning.ForecastStore(Path(directory)/'overload.sqlite3'))
        self.assertFalse(result['nominal_plan_on_same_actual']['passed'])
        self.assertEqual(result['learned_result']['status'],'NEEDS_MANUAL_REVIEW')
        self.assertFalse(result['learned_result']['guarded_candidate_available'])
        self.assertFalse(result['learned_result']['witness_verified'])

    def test_coverage_warning_requires_confirmation_despite_complete_alternative(self):
        with TemporaryDirectory() as directory:
            store=learning.ForecastStore(Path(directory)/'monitor.sqlite3');activate_model(store,self.data)
            record_days(store,self.data,range(8,10),bias=(8,4))
            monitor=store.monitor(self.data['settings']['depot_id'],'simulated',instant(8,0),instant(9))
            self.assertTrue(monitor['coverage_warning']);self.assertFalse(monitor['rolled_back'])
            model=store.active_model(self.data['settings']['depot_id'],'simulated')
            result=ap.plan_warehouse(self.data,self.policy,model=model,decision_at=instant(10,8))
            self.assertTrue(result['forecast_confirmation_required']);self.assertIsNone(result['candidate'])
            self.assertEqual(result['status'],'NEEDS_MANUAL_REVIEW')
            self.assertIsNotNone(result['alternative']);self.assertTrue(result['witness_verified'])

    def test_multiwarehouse_requires_policy_on_every_warehouse(self):
        raw=mw.sample()
        with self.assertRaises(ValueError):ap.plan_batch(raw,decision_at=DECISION_AT)
        groups=mw.split(raw)
        for warehouse in raw['warehouses']:warehouse['operational_policy']=op.sample_policy(groups[warehouse['id']])
        result=ap.plan_batch(raw,decision_at=DECISION_AT)
        self.assertEqual(len(result['warehouses']),2);self.assertTrue(result['all_guarded_candidates'])


if __name__=='__main__':unittest.main()
