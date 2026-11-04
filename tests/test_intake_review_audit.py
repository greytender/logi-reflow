"""Cross-check review output against original OOXML with independent Decimal math."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZIP_DEFLATED, ZipFile

import intake
import intake_review
from scripts import verify_intake_review as independent


def edit_sheet(raw,transform):
    output=BytesIO()
    with ZipFile(BytesIO(raw)) as source,ZipFile(output,'w',ZIP_DEFLATED) as target:
        for name in source.namelist():
            value=source.read(name)
            target.writestr(name,transform(value) if name=='xl/worksheets/sheet1.xml' else value)
    return output.getvalue()


class IndependentReviewAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.path=Path(self.temp.name)
        self.sources=intake.IntakeStore(self.path/'sources.db')
        self.reviews=intake_review.ReviewStore(self.path/'reviews.db',self.sources)
    def tearDown(self):self.temp.cleanup()

    def review(self,raw,config=None):
        source=self.sources.create('构造独立验收','audit.xlsx',raw)
        initial=self.reviews.read(source['id'])
        return self.reviews.save(source['id'],initial['revision'],config or independent.config_for(range(2,7)))

    def test_independent_fixture_checks_units_unknowns_and_business_identity(self):
        result=independent.verify_synthetic()
        self.assertEqual(result['selected_rows'],5);self.assertFalse(result['can_solve'])
        self.assertEqual(independent.synthetic_xlsx(),independent.synthetic_xlsx())

    def test_auditor_detects_wrong_conversion_missing_row_or_changed_source_hash(self):
        raw=independent.synthetic_xlsx();review=self.review(raw)
        self.assertTrue(independent.verify_normalized_values(raw,review))
        bad=deepcopy(review);bad['assessment']['rows'][0]['normalized']['regular_weight_kg']=1250
        with self.assertRaises(AssertionError):independent.verify_normalized_values(raw,bad)
        bad=deepcopy(review);bad['assessment']['rows'].pop()
        with self.assertRaises(AssertionError):independent.verify_source_agreement(raw,bad,list(range(2,7)))
        bad=deepcopy(review);bad['source_sha256']='0'*64
        with self.assertRaises(AssertionError):independent.verify_source_agreement(raw,bad,list(range(2,7)))

    def test_cm3_is_explicit_and_changes_only_derived_preview(self):
        raw=independent.synthetic_xlsx();config=independent.config_for(range(2,7))
        config['units']['regular_volume']='cm3';review=self.review(raw,config)
        self.assertAlmostEqual(review['assessment']['rows'][0]['normalized']['regular_volume_m3'],.0000025)
        self.assertTrue(independent.verify_normalized_values(raw,review))
        self.assertTrue(independent.verify_source_agreement(raw,review,list(range(2,7))))
        self.assertFalse(review['assessment']['can_solve'])

    def test_merged_numeric_child_is_not_duplicated_across_rows(self):
        def merged(value):
            value=value.replace(b'<c r="F3"><v>2500</v></c>',b'')
            return value.replace(b'</worksheet>',b'<mergeCells><mergeCell ref="F2:F3"/></mergeCells></worksheet>')
        raw=edit_sheet(independent.synthetic_xlsx(),merged);review=self.review(raw)
        row3=review['assessment']['rows'][1]
        self.assertEqual(row3['source_refs']['regular_weight']['merged_from'],'F2')
        self.assertIsNone(row3['normalized']['regular_weight_kg'])
        self.assertTrue(any(i['code']=='merged_numeric_ambiguous' for i in row3['issues']))
        self.assertTrue(independent.verify_normalized_values(raw,review))
        self.assertFalse(review['assessment']['can_solve'])

    def test_fully_filled_identifiers_and_units_do_not_become_solver_ready(self):
        raw=independent.synthetic_xlsx();config=independent.config_for([2])
        review=self.review(raw,config)
        row=review['assessment']['rows'][0]
        self.assertEqual(row['identity']['order_id'],'ORDER-2')
        self.assertEqual(row['identity']['store_id'],'STORE-1')
        self.assertFalse(review['assessment']['can_solve'])
        self.assertTrue(review['assessment']['missing'])
