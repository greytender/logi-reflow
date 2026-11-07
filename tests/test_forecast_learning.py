"""Temporal provenance, realm isolation and promotion safety for pickup learning."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import forecast_learning as learning


def instant(day,hour=23,minute=59):
    return (datetime(2026,11,1,tzinfo=timezone(timedelta(hours=8)))+
            timedelta(days=day-1,hours=hour,minutes=minute)).isoformat()


def receipt(day,site='S1',kind='simulated',warehouse='WH_A',bias=(8,4),**overrides):
    row=dict(id=f'{warehouse}-{kind}-{site}-{day}',warehouse_id=warehouse,
        store_id=site,data_kind=kind,source='simulated' if kind=='simulated' else 'manual',
        verified=True,reference='constructed fixture, not an enterprise receipt',
        decision_at=instant(day,8),observed_at=instant(day,16),available_at=instant(day,17),
        nominal=dict(cartons=10,totes=4),actual=dict(cartons=10+bias[0],totes=4+bias[1]))
    row.update(overrides)
    return row


class ForecastLearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'learning.sqlite3'
        self.store=learning.ForecastStore(self.path)

    def add_days(self,first,last,**options):
        for day in range(first,last+1):
            for site in ('S1','S2'):self.store.record(receipt(day,site,**options))

    def good_active(self):
        self.add_days(1,4)
        model=self.store.train('WH_A','simulated',instant(4))
        self.add_days(5,7)
        report=self.store.validate(model['id'],instant(5,0),instant(7))
        self.assertTrue(report['passed'],report)
        self.store.activate(model['id'])
        return self.store.active_model('WH_A','simulated')

    def test_simulation_enterprise_and_warehouse_are_separate(self):
        self.add_days(1,4)
        self.add_days(1,4,kind='enterprise',bias=(20,10))
        self.add_days(1,4,warehouse='WH_B',bias=(40,20))
        model=self.store.train('WH_A','simulated',instant(4))
        self.assertEqual(model['training_count'],8)
        self.assertTrue(all('WH_A-simulated' in record for record in model['training_record_ids']))
        for warehouse,kind in (('WH_B','simulated'),('WH_A','enterprise')):
            with self.subTest(warehouse=warehouse,kind=kind),self.assertRaises(ValueError):
                learning.predict(model,warehouse,kind,'S1',dict(cartons=10,totes=4),instant(5,8))
        with self.assertRaises(ValueError):self.store.record(receipt(8,kind='enterprise',source='simulated'))

    def test_unreviewed_and_late_arriving_records_do_not_train_early(self):
        self.store.record(receipt(1))
        self.store.record(receipt(2,verified=False,reference=''))
        self.store.record(receipt(3,available_at=instant(8,17)))
        model=self.store.train('WH_A','simulated',instant(4))
        self.assertEqual(model['training_count'],1)
        self.assertEqual(model['training_record_ids'],['WH_A-simulated-S1-1'])
        later=self.store.train('WH_A','simulated',instant(8))
        self.assertEqual(later['training_count'],2)
        self.assertNotIn('WH_A-simulated-S1-2',later['training_record_ids'])

    def test_duplicate_receipt_is_idempotent_but_actual_cannot_be_overwritten(self):
        row=receipt(1)
        expected=self.store.record(row)
        self.assertEqual(self.store.record(deepcopy(row)),expected)
        changed=deepcopy(row);changed['actual']['cartons']+=1
        with self.assertRaises(ValueError):self.store.record(changed)
        self.assertEqual(len(self.store.records('WH_A','simulated',instant(2))),1)

    def test_same_business_event_cannot_be_duplicated_with_new_receipt_id(self):
        row=receipt(1);self.store.record(row)
        duplicate=deepcopy(row);duplicate['id']='different-id'
        with self.assertRaises(ValueError):self.store.record(duplicate)
        self.assertEqual(self.store.train('WH_A','simulated',instant(2))['training_count'],1)

    def test_invalid_temporal_order_timezone_and_quantities_are_rejected(self):
        invalid=(dict(decision_at=instant(2,8)),dict(available_at=instant(1,15)),
                 dict(decision_at='2026-11-01T08:00:00'),
                 dict(actual=dict(cartons=True,totes=4)),dict(verified=True,reference=''))
        for override in invalid:
            with self.subTest(override=override),self.assertRaises(ValueError):
                self.store.record(receipt(1,**override))

    def test_sparse_and_unknown_sites_preserve_nominal(self):
        self.store.record(receipt(1));self.store.record(receipt(2))
        model=self.store.train('WH_A','simulated',instant(2))
        for site in ('S1','UNKNOWN'):
            result=learning.predict(model,'WH_A','simulated',site,dict(cartons=10,totes=4),instant(3,8))
            self.assertEqual(result['source'],'nominal')
            self.assertEqual(result['expected'],dict(cartons=10,totes=4))

    def test_training_and_validation_windows_cannot_overlap(self):
        self.add_days(1,4)
        model=self.store.train('WH_A','simulated',instant(4))
        with self.assertRaises(ValueError):
            learning.predict(model,'WH_A','simulated','S1',dict(cartons=10,totes=4),instant(4))
        with self.assertRaises(ValueError):self.store.validate(model['id'],instant(4),instant(7))
        with self.assertRaises(ValueError):self.store.activate(model['id'])

    def test_active_model_is_unavailable_until_after_validation_data(self):
        active=self.good_active()
        self.assertEqual(active['available_after'],learning.timestamp(instant(7)))
        for at in (instant(6,8),instant(7)):
            with self.subTest(at=at),self.assertRaises(ValueError):
                learning.predict(active,'WH_A','simulated','S1',dict(cartons=10,totes=4),at)
        result=learning.predict(active,'WH_A','simulated','S1',dict(cartons=10,totes=4),instant(8,8))
        self.assertEqual(result['source'],'learned')
        self.assertGreater(result['expected']['cartons'],10)

    def test_failed_gate_keeps_incumbent_and_retrain_does_not_promote(self):
        active=self.good_active()
        self.add_days(8,13,bias=(0,0))
        result=self.store.retrain('WH_A','simulated',instant(10),instant(11,0),instant(13))
        self.assertFalse(result['validation']['passed'])
        self.assertFalse(result['activated'])
        self.assertEqual(self.store.active_model('WH_A','simulated')['id'],active['id'])
        with self.assertRaises(ValueError):self.store.activate(result['candidate']['id'])

    def test_activation_restarts_and_rollback_persist_and_reject_wrong_realm(self):
        active=self.good_active()
        restarted=learning.ForecastStore(self.path)
        self.assertEqual(restarted.active_model('WH_A','simulated')['id'],active['id'])
        restarted.rollback('WH_A','simulated')
        self.assertIsNone(learning.ForecastStore(self.path).active_model('WH_A','simulated'))
        restarted.rollback('WH_A','simulated',active['id'])
        self.assertEqual(learning.ForecastStore(self.path).active_model('WH_A','simulated')['id'],active['id'])
        with self.assertRaises(ValueError):restarted.rollback('WH_A','enterprise',active['id'])
        unknown=restarted.train('WH_A','enterprise',instant(4))
        with self.assertRaises(ValueError):restarted.rollback('WH_A','enterprise',unknown['id'])

    def test_drift_monitor_rollback_changes_only_subsequent_predictions(self):
        active=self.good_active()
        before=learning.predict(active,'WH_A','simulated','S1',dict(cartons=10,totes=4),instant(8,8))
        self.assertEqual(before['source'],'learned')
        self.add_days(8,10,bias=(-4,-2))
        report=self.store.monitor('WH_A','simulated',instant(8,0),instant(10),auto_rollback=True)
        self.assertTrue(report['rollback_recommended'])
        self.assertTrue(report['rolled_back'])
        self.assertIsNone(self.store.active_model('WH_A','simulated'))
        after=learning.predict(self.store.active_model('WH_A','simulated'),'WH_A','simulated',
                               'S1',dict(cartons=10,totes=4),instant(11,8))
        self.assertEqual(after['source'],'nominal')
        self.assertEqual(before['source'],'learned')
        self.assertEqual(len(self.store.records('WH_A','simulated',instant(10))),20)

    def test_revalidation_does_not_rewrite_active_availability_snapshot(self):
        active=self.good_active()
        self.add_days(8,10)
        report=self.store.validate(active['id'],instant(8,0),instant(10))
        self.assertTrue(report['passed'])
        still_active=self.store.active_model('WH_A','simulated')
        self.assertEqual(still_active['available_after'],active['available_after'])
        learned=learning.predict(still_active,'WH_A','simulated','S1',
                                  dict(cartons=10,totes=4),instant(8,8))
        self.assertEqual(learned['source'],'learned')

    def test_validation_set_change_requires_new_validation_before_activation(self):
        self.add_days(1,4)
        candidate=self.store.train('WH_A','simulated',instant(4))
        self.add_days(5,7)
        report=self.store.validate(candidate['id'],instant(5,0),instant(7))
        self.assertTrue(report['passed'])
        self.store.record(receipt(6,id='late-added-heldout',store_id='S1',decision_at=instant(6,9)))
        with self.assertRaises(ValueError):self.store.activate(candidate['id'])
        self.assertIsNone(self.store.active_model('WH_A','simulated'))

    def test_upward_coverage_warning_persists_and_is_forwarded_to_predictions(self):
        active=self.good_active()
        self.assertFalse(active['requires_review'])
        self.add_days(8,10,bias=(30,20))
        report=self.store.monitor('WH_A','simulated',instant(8,0),instant(10),auto_rollback=True)
        self.assertTrue(report['coverage_warning'])
        self.assertEqual(report['risk_upper_coverage'],0)
        self.assertFalse(report['rollback_recommended'])
        self.assertFalse(report['rolled_back'])
        restarted=learning.ForecastStore(self.path)
        flagged=restarted.active_model('WH_A','simulated')
        self.assertTrue(flagged['requires_review'])
        prediction=learning.predict(flagged,'WH_A','simulated','S1',dict(cartons=10,totes=4),instant(11,8))
        self.assertTrue(prediction['requires_review'])
        self.assertFalse(active['requires_review'])
        self.add_days(11,13)
        recovered=self.store.monitor('WH_A','simulated',instant(11,0),instant(13))
        self.assertFalse(recovered['coverage_warning'])
        self.assertTrue(learning.ForecastStore(self.path).active_model('WH_A','simulated')['requires_review'])


if __name__=='__main__':unittest.main()
