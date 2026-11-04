"""Independent source-cell audit of mapping drafts; no solver or external calls.

Default input is synthetic. Optional supplied XLSX is opened read-only and only
aggregate counts/hashes are written to evidence, never customer names/addresses.
"""
import argparse
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO
import json
from math import isclose
from pathlib import Path
import posixpath
import sys
from tempfile import TemporaryDirectory
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def synthetic_xlsx():
    """Deliberately includes identity conflicts, absent cold cells and formula."""
    def text(ref,value):return f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
    def num(ref,value):return f'<c r="{ref}"><v>{value}</v></c>'
    records=[
        (2,'ORDER-2','STORE-1','构造同名店','构造地址甲',2,1250,'2.5',None,None,None),
        (3,'ORDER-DUP','STORE-1','构造同名店','构造地址乙',3,2500,10,1,500,1),
        (4,'ORDER-DUP','STORE-2','构造同名店','构造地址乙',4,1000,20,2,250,2),
        (5,9007199254740993,'STORE-3','构造第三店','构造地址丙',1,100,1,None,None,None),
        (6,'ORDER-6','STORE-4','构造第四店','构造地址丁',None,1000,1,None,None,None),
    ]
    rows=['<row r="1">'+''.join(text(chr(65+i)+'1',v) for i,v in enumerate(
        ['订单ID','门店ID','门店名称','收货地址','常温件数','常温重量','常温体积','冷链件数','冷链重量','冷链体积']))+'</row>']
    for record in records:
        row=record[0];values=record[1:];cells=[]
        for i,value in enumerate(values):
            if value is None:continue
            ref=chr(65+i)+str(row)
            cells.append(text(ref,value) if isinstance(value,str) and i<4 else num(ref,value))
        if row==6:cells.append('<c r="E6"><f>2+2</f></c>')
        rows.append(f'<row r="{row}">'+''.join(cells)+'</row>')
    ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    rel='http://schemas.openxmlformats.org/package/2006/relationships'
    doc='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    parts={
        '[Content_Types].xml':'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        '_rels/.rels':f'<Relationships xmlns="{rel}"><Relationship Id="rId1" Type="{doc}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml':f'<workbook xmlns="{ns}" xmlns:r="{doc}"><sheets><sheet name="构造映射审计" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels':f'<Relationships xmlns="{rel}"><Relationship Id="rId1" Type="{doc}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        'xl/worksheets/sheet1.xml':f'<worksheet xmlns="{ns}"><sheetData>'+''.join(rows)+'</sheetData></worksheet>',
    }
    output=BytesIO()
    with ZipFile(output,'w',ZIP_DEFLATED) as archive:
        for name,value in parts.items():
            info=ZipInfo(name,date_time=(2026,11,1,0,0,0));info.compress_type=ZIP_DEFLATED
            archive.writestr(info,value.encode('utf-8'))
    return output.getvalue()


def raw_cells(content,sheet_index=0):
    """Read original OOXML independently from intake and review converters."""
    local=lambda tag:tag.rsplit('}',1)[-1]
    with ZipFile(BytesIO(content)) as archive:
        workbook=ET.fromstring(archive.read('xl/workbook.xml'))
        sheets=[n for n in workbook.iter() if local(n.tag)=='sheet']
        rid=next(v for k,v in sheets[sheet_index].attrib.items() if local(k)=='id')
        rels=ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        target=next(n.get('Target') for n in rels if n.get('Id')==rid)
        path=target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('xl',target))
        shared=[]
        if 'xl/sharedStrings.xml' in archive.namelist():
            shared=[''.join(t.text or '' for t in n.iter() if local(t.tag)=='t')
                    for n in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
        result={}
        for node in ET.fromstring(archive.read(path)).iter():
            if local(node.tag)!='c':continue
            value=next((n.text for n in node if local(n.tag)=='v'),None)
            kind=node.get('t','n')
            if kind=='s' and value is not None:value=shared[int(value)]
            elif kind=='inlineStr':value=''.join(n.text or '' for n in node.iter() if local(n.tag)=='t')
            result[node.get('r')]=dict(value=value,type=kind,
                formula=next((n.text or '' for n in node if local(n.tag)=='f'),None))
        return result


def config_for(source_rows):
    return dict(sheet_index=0,rows=list(source_rows),fields=dict(order_id=1,store_id=2,store_name=3,address=4,
        regular_quantity=5,regular_weight=6,regular_volume=7,cold_quantity=8,cold_weight=9,cold_volume=10),
        units=dict(regular_weight='g',regular_volume='L',cold_weight='g',cold_volume='L'),
        identities={},batch_id='SYNTHETIC-BATCH',delivery_date='2026-11-01',note='构造独立验算')


def verify_source_agreement(content,review,expected_rows):
    cells=raw_cells(content,review['config']['sheet_index'])
    assessment=review['assessment']
    assert review['source_sha256']==sha256(content).hexdigest(),'source hash changed'
    assert assessment['can_solve'] is False,'review draft must never become solver-ready'
    assert assessment['selected_rows']==len(expected_rows),'selected row count changed'
    assert assessment['selected_source_rows']==list(expected_rows),'selected source rows truncated or reordered'
    assert [r['source_row'] for r in assessment['rows']]==list(expected_rows),'assessment omitted source rows'
    for row in assessment['rows']:
        for field,column in review['config']['fields'].items():
            snapshot=row['source_refs'].get(field)
            if column is None:continue
            letters='';n=column
            while n:n,k=divmod(n-1,26);letters=chr(65+k)+letters
            ref=letters+str(row['source_row']);original=cells.get(ref)
            if original is None:
                assert snapshot is None or snapshot.get('raw_value') is None,'absent source became a value'
                continue
            assert snapshot['ref']==ref,'source ref changed'
            if original['type']=='n':
                assert snapshot['raw_value']==original['value'],'raw numeric text changed'
            elif original['type'] in ('inlineStr','s'):
                assert snapshot['value']==original['value'],'original source text changed'
    return True


def verify_normalized_values(content,review):
    """Calculate unit results from original OOXML values, not parsed totals."""
    cells=raw_cells(content,review['config']['sheet_index'])
    factors={'kg':Decimal(1),'g':Decimal('.001'),'m3':Decimal(1),
             'L':Decimal('.001'),'cm3':Decimal('.000001')}
    for row in review['assessment']['rows']:
        for field in ('regular_quantity','regular_weight','regular_volume','cold_quantity','cold_weight','cold_volume'):
            snapshot=row['source_refs'].get(field)
            source=cells.get(snapshot['ref']) if snapshot else None
            target=field if field.endswith('quantity') else field+('_kg' if field.endswith('weight') else '_m3')
            expected=None
            if source and source['type']=='n' and source['value'] is not None and not snapshot.get('merged_from'):
                try:number=Decimal(source['value'])
                except InvalidOperation:number=Decimal('NaN')
                if (number.is_finite() and number>=0 and abs(number)<=2**53-1
                        and Decimal(str(float(number)))==number):
                    if field.endswith('quantity'):expected=float(number)
                    else:
                        unit=review['config']['units'][field]
                        if unit is not None:expected=float(number*factors[unit])
            actual=row['normalized'][target]
            if expected is None:assert actual is None,f'unknown or ambiguous source converted at row {row["source_row"]}, {target}'
            else:assert actual is not None and isclose(actual,expected,rel_tol=1e-12,abs_tol=1e-12),f'original OOXML unit conversion differs at row {row["source_row"]}, {target}'
    return True


def verify_synthetic(output=None):
    import intake
    import intake_review
    content=synthetic_xlsx();source=raw_cells(content)
    with TemporaryDirectory() as folder:
        originals=intake.IntakeStore(Path(folder)/'source.db')
        document=originals.create('构造映射审计','synthetic-audit.xlsx',content)
        store=intake_review.ReviewStore(Path(folder)/'reviews.db',originals)
        initial=store.read(document['id'])
        review=store.save(document['id'],initial['revision'],config_for(range(2,7)))
        verify_source_agreement(content,review,list(range(2,7)))
        verify_normalized_values(content,review)
        by_row={r['source_row']:r for r in review['assessment']['rows']}
        # Independent Decimal factors, based on original OOXML text, not review helpers.
        conversions={'regular_weight_kg':('F',Decimal('.001')),
            'regular_volume_m3':('G',Decimal('.001')),'cold_weight_kg':('I',Decimal('.001')),
            'cold_volume_m3':('J',Decimal('.001'))}
        for number,row in by_row.items():
            for field,(column,factor) in conversions.items():
                raw=source.get(column+str(number),{}).get('value')
                expected=None if raw is None else float(Decimal(raw)*factor)
                assert row['normalized'][field]==expected,f'unit conversion differs at row {number}, {field}'
        assert by_row[2]['normalized']['cold_quantity'] is None,'unknown cold count was changed to zero'
        assert by_row[6]['normalized']['regular_quantity'] is None,'uncached formula was executed'
        assert by_row[5]['identity']['order_id']=='9007199254740993','large integer business identity rounded'
        assert len(by_row)==5,'duplicate identities silently merged source rows'
        assert by_row[2]['identity']['store_id']==by_row[3]['identity']['store_id']=='STORE-1'
        assert by_row[2]['identity']['address']!=by_row[3]['identity']['address']
        assert by_row[3]['identity']['order_id']==by_row[4]['identity']['order_id']=='ORDER-DUP'
        codes={i['code'] for i in review['assessment']['issues']}
        assert 'duplicate_order_id' in codes and 'store_id_address_conflict' in codes,'identity conflicts were not flagged'
        # An unspecified unit must retain unknown normalization instead of guessing.
        no_units=deepcopy(review['config']);no_units['units']['regular_weight']=None
        unknown=store.save(document['id'],review['revision'],no_units)
        assert all(r['normalized']['regular_weight_kg'] is None for r in unknown['assessment']['rows'])
        # Explicit kg differs from g by 1000; retaining original unit is essential.
        kg=deepcopy(unknown['config']);kg['units']['regular_weight']='kg'
        changed=store.save(document['id'],unknown['revision'],kg)
        assert changed['assessment']['rows'][0]['normalized']['regular_weight_kg']==1250
        verify_normalized_values(content,changed)
        assert originals.read(document['id'])['sha256']==sha256(content).hexdigest()
        exported=store.export(document['id'])
        assert exported['assessment']['can_solve'] is False
        record=dict(source_kind='synthetic',selected_rows=5,source_sha256=sha256(content).hexdigest(),
            can_solve=False,checks=['raw cells and source hash retained','all selected source rows retained',
                'g/kg and L/m3 independently converted','unspecified units stay unknown',
                'blank cold cells stay unknown','uncached formula stays unknown',
                'large integer order ID retained','identity conflicts do not merge source rows'])
        if output:
            output.mkdir(parents=True,exist_ok=True)
            (output/'synthetic-verification.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        return record


def verify_supplied_source(path,output=None):
    import intake
    import intake_review
    content=path.read_bytes();before=sha256(content).hexdigest();cells=raw_cells(content)
    # Count original data rows directly from raw sheet cells; no customer text leaves this function.
    rows=sorted({int(''.join(c for c in ref if c.isdigit())) for ref,value in cells.items()
        if int(''.join(c for c in ref if c.isdigit()))>=6
        and ''.join(c for c in ref if c.isalpha()) in tuple('ABCDEFGHIJ')
        and (value['value'] is not None or value['formula'] is not None)})
    with TemporaryDirectory() as folder:
        originals=intake.IntakeStore(Path(folder)/'source.db')
        document=originals.create('本地附件只读验算',path.name,content)
        review=intake_review.ReviewStore(Path(folder)/'reviews.db',originals).read(document['id'])
        verify_source_agreement(content,review,rows)
        verify_normalized_values(content,review)
        assert len(rows)==116,'supplied source does not match the expected official 116-row workbook'
        assert sha256(path.read_bytes()).hexdigest()==before,'source file was modified'
        record=dict(source_kind='supplied_local_source_read_only',source_sha256=before,
            independently_counted_rows=len(rows),default_selected_rows=review['assessment']['selected_rows'],
            can_solve=False,customer_detail_exported=False,checks=['raw XML all rows counted',
                'all 116 source rows selected by default','no 12-row truncation','source bytes unchanged'])
        if output:
            output.mkdir(parents=True,exist_ok=True)
            (output/'supplied-source-verification.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        return record


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/intake-review-independent')
    args=parser.parse_args()
    print(json.dumps(verify_synthetic(args.output),ensure_ascii=False))
    if args.source:print(json.dumps(verify_supplied_source(args.source,args.output),ensure_ascii=False))


if __name__=='__main__':main()
