from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from zipfile import ZipFile,ZIP_DEFLATED
import intake


def edit_part(source,part,transform):
    out=BytesIO()
    with ZipFile(BytesIO(source)) as original,ZipFile(out,'w',ZIP_DEFLATED) as edited:
        for name in original.namelist():
            raw=original.read(name)
            edited.writestr(name,transform(raw) if name==part else raw)
    return out.getvalue()


class IntakeTests(unittest.TestCase):
    def setUp(self):self.raw=intake.sample_xlsx()

    def test_all_original_rows_merged_sources_and_unknowns_preserved(self):
        d=intake.parse_xlsx(self.raw,'sample.xlsx');audit=d['audit']
        self.assertEqual(d['sha256'],sha256(self.raw).hexdigest())
        self.assertEqual(audit['record_rows'],3);self.assertEqual(audit['unique_store_name_strings'],2)
        self.assertEqual(audit['missing_sequence'],[2]);self.assertEqual(audit['blank_cold_rows'],1)
        self.assertFalse(audit['can_solve'])
        cells={c['ref']:c for row in d['sheets'][0]['rows'] for c in row['cells']}
        self.assertIsNone(cells['D7']['value']);self.assertEqual(cells['D7']['merged_from'],'D6')
        self.assertEqual(cells['D7']['effective_value'],'构造地址甲')
        self.assertNotIn('D8',[ref for ref,c in cells.items() if c['merged_from']])
        self.assertTrue(any(i['code']=='cold_volume_unit_review' for i in audit['issues']))

    def test_persistence_reopen_and_source_bytes_not_mutated(self):
        snapshot=bytes(self.raw)
        with TemporaryDirectory() as folder:
            path=Path(folder)/'intake.db';store=intake.IntakeStore(path)
            d=store.create('原始核对','original.xlsx',self.raw)
            self.assertEqual(intake.IntakeStore(path).read(d['id']),d)
            self.assertEqual(store.list()[0]['record_rows'],3)
            with store.connect() as connection:
                self.assertEqual(connection.execute('SELECT source FROM intake_documents WHERE id=?',(d['id'],)).fetchone()[0],self.raw)
        self.assertEqual(snapshot,self.raw)

    def test_formula_cached_and_uncached_are_not_executed(self):
        raw=edit_part(self.raw,'xl/worksheets/sheet1.xml',lambda s:s.replace(b'</sheetData>',b'<row r="9"><c r="E9"><f>1+1</f><v>2</v></c><c r="F9"><f>99*99</f></c></row></sheetData>'))
        d=intake.parse_xlsx(raw)
        cells={c['ref']:c for row in d['sheets'][0]['rows'] for c in row['cells']}
        self.assertEqual(cells['E9']['value'],2);self.assertEqual(cells['E9']['cache_status'],'cached')
        self.assertIsNone(cells['F9']['value']);self.assertEqual(cells['F9']['cache_status'],'missing')
        self.assertTrue(any(i['code']=='formula_without_cache' for i in d['audit']['issues']))

    def test_generic_headers_preserve_rows_without_fake_mapping(self):
        raw=edit_part(self.raw,'xl/worksheets/sheet1.xml',lambda s:s.replace('收货单位'.encode(),'客户名称'.encode()))
        d=intake.parse_xlsx(raw)
        self.assertEqual(d['audit']['profile'],'generic_raw');self.assertIsNone(d['audit']['record_rows'])
        self.assertEqual(len(d['sheets'][0]['rows']),6)

    def test_external_relationship_and_macro_rejected(self):
        raw=edit_part(self.raw,'xl/_rels/workbook.xml.rels',lambda s:s.replace(b'Target="worksheets/sheet1.xml"',b'TargetMode="External" Target="https://example.invalid/data"'))
        with self.assertRaisesRegex(ValueError,'外部'):intake.parse_xlsx(raw)
        with self.assertRaises(ValueError):intake.parse_xlsx(self.raw,'sample.xlsm')

    def test_dtd_and_sparse_overlapping_merges_rejected(self):
        raw=edit_part(self.raw,'xl/worksheets/sheet1.xml',lambda s:b'<!DOCTYPE worksheet [<!ENTITY x "data">]>'+s)
        with self.assertRaisesRegex(ValueError,'DTD'):intake.parse_xlsx(raw)
        raw=edit_part(self.raw,'xl/worksheets/sheet1.xml',lambda s:s.replace(b'</mergeCells>',b'<mergeCell ref="D7:E8"/></mergeCells>'))
        with self.assertRaisesRegex(ValueError,'重叠'):intake.parse_xlsx(raw)

    def test_utf16_dtd_is_rejected_before_xml_expansion(self):
        def encoded(raw):
            text='<!DOCTYPE worksheet [<!ENTITY x "data">]>'+raw.decode('utf-8')
            return text.encode('utf-16')
        raw=edit_part(self.raw,'xl/worksheets/sheet1.xml',encoded)
        with self.assertRaisesRegex(ValueError,'DTD'):intake.parse_xlsx(raw)

    def test_formula_only_uncached_source_row_is_counted_for_review(self):
        raw=edit_part(self.raw,'xl/worksheets/sheet1.xml',lambda s:s.replace(b'</sheetData>',b'<row r="9"><c r="A9"><f>1+1</f></c></row></sheetData>'))
        document=intake.parse_xlsx(raw)
        self.assertEqual(document['audit']['record_rows'],4)
        self.assertIn(9,document['audit']['source_rows'])
        self.assertTrue(any(i['code']=='formula_without_cache' and i['rows']==[9] for i in document['audit']['issues']))

    def test_limits_reject_entire_file_without_silent_truncation(self):
        with patch.object(intake,'MAX_ROWS',2),self.assertRaisesRegex(ValueError,'行'):intake.parse_xlsx(self.raw)
        with patch.object(intake,'MAX_CELLS',2),self.assertRaisesRegex(ValueError,'单元格'):intake.parse_xlsx(self.raw)
        with patch.object(intake,'MAX_UNPACKED',10),self.assertRaisesRegex(ValueError,'32MB'):intake.parse_xlsx(self.raw)
        with patch.object(intake,'MAX_MERGE_WORK',1),self.assertRaisesRegex(ValueError,'复杂度'):intake.parse_xlsx(self.raw)

    def test_sample_is_complete_office_document_package(self):
        with ZipFile(BytesIO(self.raw)) as archive:
            root=ET.fromstring(archive.read('_rels/.rels'))
            self.assertTrue(any(n.get('Target')=='xl/workbook.xml' for n in root))
            content_types=ET.fromstring(archive.read('[Content_Types].xml'))
            self.assertTrue(any(n.get('PartName')=='/xl/worksheets/sheet1.xml' for n in content_types))

    def test_large_and_high_precision_numbers_preserve_exact_raw_text(self):
        raw=edit_part(self.raw,'xl/worksheets/sheet1.xml',lambda s:s.replace(b'</sheetData>',b'<row r="9"><c r="A9"><v>9007199254740993</v></c><c r="B9"><v>9.007199254740993E15</v></c><c r="C9"><v>1.234567890123456789</v></c><c r="D9"><v>1e3</v></c></row></sheetData>'))
        document=intake.parse_xlsx(raw)
        cells={c['ref']:c for row in document['sheets'][0]['rows'] for c in row['cells']}
        self.assertEqual(cells['A9']['value'],'9007199254740993')
        self.assertEqual(cells['B9']['value'],'9.007199254740993E15')
        self.assertEqual(cells['C9']['value'],'1.234567890123456789')
        self.assertEqual(cells['D9']['value'],1000)
        self.assertEqual(cells['D9']['raw_value'],'1e3')
        self.assertEqual(len([i for i in document['audit']['issues'] if i['code']=='numeric_precision_review']),3)

    def test_invalid_files_rejected_without_creating_document(self):
        with TemporaryDirectory() as folder:
            store=intake.IntakeStore(Path(folder)/'t.db')
            for raw,name in [(b'not zip','broken.xlsx'),(self.raw,'sample.xls'),(self.raw,'sample.xlsm')]:
                with self.subTest(name=name),self.assertRaises(ValueError):store.create('invalid',name,raw)
            self.assertEqual(store.list(),[])
