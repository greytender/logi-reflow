from copy import deepcopy
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile,ZIP_DEFLATED

import intake
import intake_review
from store import Conflict


def edit_sheet(source,transform):
    output=BytesIO()
    with ZipFile(BytesIO(source)) as old,ZipFile(output,'w',ZIP_DEFLATED) as new:
        for name in old.namelist():
            raw=old.read(name)
            new.writestr(name,transform(raw) if name=='xl/worksheets/sheet1.xml' else raw)
    return output.getvalue()


class IntakeReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();root=Path(self.temp.name)
        self.source_store=intake.IntakeStore(root/'source.sqlite3')
        self.source=self.source_store.create('构造核对','source.xlsx',intake.sample_xlsx())
        self.path=root/'reviews.sqlite3'
        self.store=intake_review.ReviewStore(self.path,self.source_store)

    def tearDown(self):self.temp.cleanup()

    def test_default_keeps_all_rows_and_does_not_confirm_units_or_identities(self):
        review=self.store.read(self.source['id'])
        self.assertEqual(review['revision'],0)
        self.assertEqual(review['config']['rows'],[6,7,8])
        self.assertTrue(all(v is None for v in review['config']['units'].values()))
        self.assertIsNone(review['config']['fields']['order_id'])
        self.assertFalse(review['assessment']['can_solve'])
        self.assertEqual(review['assessment']['selected_rows'],3)
        self.assertEqual(review['history'],[])

    def test_save_restore_history_and_immutable_raw_source(self):
        review=self.store.read(self.source['id']);config=deepcopy(review['config'])
        config['note']='第一版待补'
        first=self.store.save(self.source['id'],0,config)
        config['note']='第二版核对'
        second=self.store.save(self.source['id'],1,config)
        self.assertEqual(second['revision'],2)
        self.assertEqual(intake_review.ReviewStore(self.path,self.source_store).read(self.source['id']),second)
        exported=self.store.export(self.source['id'])
        self.assertEqual([v['revision'] for v in exported['versions']],[1,2])
        self.assertEqual(exported['versions'][0]['config']['note'],'第一版待补')
        self.assertEqual(exported['versions'][-1]['revision'],exported['revision'])
        self.assertEqual(self.source_store.read(self.source['id']),self.source)
        self.assertEqual(first['source_sha256'],self.source['sha256'])

    def test_old_revision_cannot_overwrite_current_or_history(self):
        review=self.store.read(self.source['id'])
        saved=self.store.save(self.source['id'],0,review['config'])
        with self.assertRaises(Conflict):self.store.save(self.source['id'],0,review['config'])
        self.assertEqual(self.store.read(self.source['id']),saved)

    def test_units_convert_only_after_explicit_selection_and_blank_stays_unknown(self):
        config=self.store.read(self.source['id'])['config']
        before=self.store.save(self.source['id'],0,config)
        self.assertIsNone(before['assessment']['rows'][0]['normalized']['regular_weight_kg'])
        config['units']=dict(regular_weight='g',regular_volume='L',cold_weight='g',cold_volume='cm3')
        after=self.store.save(self.source['id'],1,config)
        first,second=after['assessment']['rows'][:2]
        self.assertAlmostEqual(first['normalized']['regular_weight_kg'],.014)
        self.assertAlmostEqual(first['normalized']['regular_volume_m3'],.000144)
        self.assertAlmostEqual(first['normalized']['cold_volume_m3'],.00000001)
        self.assertIsNone(second['normalized']['cold_quantity'])
        self.assertIsNone(second['normalized']['cold_weight_kg'])
        self.assertEqual(second['source_refs']['address']['merged_from'],'D6')
        self.assertFalse(after['assessment']['can_solve'])

    def test_duplicate_order_and_same_store_different_address_not_merged(self):
        config=self.store.read(self.source['id'])['config']
        config['identities']={str(row):dict(order_id='ORDER-DUP' if row in (6,8) else 'OTHER',store_id='STORE-DUP',batch_id='B',note='待核对') for row in (6,7,8)}
        # Row8 blank address is kept unknown; explicitly map name as a distinct
        # source value to exercise identity conflict without modifying source.
        config['fields']['address']=3
        review=self.store.save(self.source['id'],0,config)
        codes={i['code'] for i in review['assessment']['issues']}
        self.assertIn('duplicate_order_id',codes)
        self.assertIn('store_id_address_conflict',codes)
        self.assertEqual(len(review['assessment']['rows']),3)

    def test_unselected_identity_draft_retained_but_not_assessed(self):
        config=self.store.read(self.source['id'])['config']
        config['rows']=[6]
        config['identities']={'7':dict(order_id='UNSELECTED',store_id='S7',batch_id='',note='保留草稿')}
        saved=self.store.save(self.source['id'],0,config)
        self.assertEqual(saved['config']['identities']['7']['order_id'],'UNSELECTED')
        self.assertEqual(saved['assessment']['selected_source_rows'],[6])

    def test_merged_number_not_repeated_and_uncached_formula_not_computed(self):
        raw=edit_sheet(intake.sample_xlsx(),lambda x:x.replace(b'<mergeCell ref="D6:D7"/>',b'<mergeCell ref="D6:D7"/><mergeCell ref="E6:E7"/>').replace(b'<c r="F8"><v>0.144</v></c>',b'<c r="F8"><f>1+1</f></c>'))
        source=self.source_store.create('合并数值公式','merged.xlsx',raw)
        config=self.store.read(source['id'])['config'];config['units']={k:('kg' if k.endswith('weight') else 'm3') for k in intake_review.UNIT_OPTIONS}
        saved=self.store.save(source['id'],0,config)
        second,third=saved['assessment']['rows'][1:]
        self.assertIsNone(second['normalized']['regular_quantity'])
        self.assertTrue(any(i['code']=='merged_numeric_ambiguous' for i in second['issues']))
        self.assertIsNone(third['normalized']['regular_volume_m3'])
        self.assertTrue(any(i['code']=='formula_without_cache' for i in third['issues']))

    def test_bad_config_rejected_without_creating_revision(self):
        original=self.store.read(self.source['id'])['config']
        cases=[]
        for field,value in [('sheet_index',True),('rows',[6,6]),('rows',[999]),('delivery_date','2026-02-30')]:
            c=deepcopy(original);c[field]=value;cases.append(c)
        c=deepcopy(original);c['fields']['typo']=1;cases.append(c)
        c=deepcopy(original);c['fields']['order_id']=False;cases.append(c)
        c=deepcopy(original);c['units']['regular_weight']='lb';cases.append(c)
        c=deepcopy(original);c['identities']={'999':dict(order_id='x')};cases.append(c)
        for config in cases:
            with self.subTest(config=config),self.assertRaises(ValueError):self.store.save(self.source['id'],0,config)
        self.assertEqual(self.store.read(self.source['id'])['revision'],0)

    def test_source_hash_change_rejects_old_draft_instead_of_remapping_silently(self):
        config=self.store.read(self.source['id'])['config'];self.store.save(self.source['id'],0,config)
        changed=deepcopy(self.source);changed['sha256']='0'*64
        with patch.object(self.source_store,'read',return_value=changed):
            with self.assertRaises(Conflict):self.store.read(self.source['id'])
            with self.assertRaises(Conflict):self.store.save(self.source['id'],1,config)

    def test_large_numeric_source_identity_uses_exact_original_text(self):
        raw=edit_sheet(intake.sample_xlsx(),lambda x:x.replace(b'<c r="A6"><v>1</v></c>',b'<c r="A6"><v>9007199254740993</v></c>'))
        source=self.source_store.create('大ID','large.xlsx',raw)
        config=self.store.read(source['id'])['config'];config['fields']['order_id']=1
        review=self.store.save(source['id'],0,config)
        self.assertEqual(review['assessment']['rows'][0]['identity']['order_id'],'9007199254740993')

    def test_export_history_is_single_read_snapshot(self):
        config=self.store.read(self.source['id'])['config'];self.store.save(self.source['id'],0,config)
        observed=[];old_history=self.store._history
        def check(connection,document_id):
            observed.append(connection.in_transaction)
            return old_history(connection,document_id)
        with patch.object(self.store,'_history',side_effect=check):
            exported=self.store.export(self.source['id'])
        self.assertEqual(observed,[True])
        self.assertEqual(exported['versions'][-1]['revision'],exported['revision'])
