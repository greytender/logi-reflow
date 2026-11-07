"""Local XLSX source intake. Keeps raw cells; never builds dispatch inputs."""
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO
import json
from math import isfinite
from pathlib import Path
import posixpath
import re
import sqlite3
from uuid import uuid4
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile, ZIP_DEFLATED

MAX_UPLOAD = 5_000_000
MAX_UNPACKED = 32_000_000
MAX_ENTRIES = 3000
MAX_ROWS = 10000
MAX_CELLS = 200000
MAX_SHEETS = 30
MAX_MERGE_WORK = 5_000_000
NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'
DOC_REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def local(tag):
    return tag.rsplit('}', 1)[-1]


def cell_position(ref):
    match = re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]{0,6})', ref or '')
    if not match:
        raise ValueError('单元格引用无效')
    col = 0
    for char in match[1]:
        col = col*26+ord(char)-64
    row = int(match[2])
    if col > 16384 or row > 1048576:
        raise ValueError('单元格引用超过Excel范围')
    return row, col


def safe_part(base, target):
    if not isinstance(target, str) or '\\' in target or ':' in target:
        raise ValueError('工作簿部件路径无效')
    result = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(base,target))
    if result.startswith('../') or result in ('.','..'):
        raise ValueError('工作簿部件不能离开压缩包')
    return result


def parse_xlsx(content, filename='orders.xlsx'):
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_UPLOAD:
        raise ValueError('请选择不超过5MB的.xlsx文件')
    if not isinstance(filename,str) or not filename.lower().endswith('.xlsx') or len(filename)>200:
        raise ValueError('只支持.xlsx原始订单，不执行宏或旧版.xls')
    issues=[]
    def issue(code,message,sheet=None,rows=None,cells=None,severity='review'):
        issues.append(dict(code=code,message=message,sheet=sheet,rows=rows or [],cells=cells or [],severity=severity))
    try:
        archive=ZipFile(BytesIO(content))
    except BadZipFile as error:
        raise ValueError('文件不是有效的.xlsx压缩包') from error
    with archive:
        entries=archive.infolist();names=[i.filename for i in entries]
        if len(entries)>MAX_ENTRIES or len(names)!=len(set(names)):
            raise ValueError('工作簿部件过多或包含重复路径')
        if sum(i.file_size for i in entries)>MAX_UNPACKED:
            raise ValueError('工作簿解压后超过32MB')
        if any(i.flag_bits & 1 for i in entries):
            raise ValueError('不支持加密工作簿')
        if any('vba' in n.lower() or n.lower().startswith('xl/externallinks/') for n in names):
            raise ValueError('不支持宏或外部工作簿链接')
        def xml(name):
            try:
                raw=archive.read(name)
            except (KeyError,RuntimeError,BadZipFile) as error:
                raise ValueError('工作簿缺少或损坏部件：'+name) from error
            probe=raw.replace(b'\x00',b'').upper()
            if b'<!DOCTYPE' in probe or b'<!ENTITY' in probe:
                raise ValueError('不支持DTD或实体声明')
            try:return ET.fromstring(raw)
            except ET.ParseError as error:raise ValueError('工作簿XML格式无效') from error
        # Do not access relationship destinations, even if they look like URLs.
        for name in names:
            if name.endswith('.rels'):
                for relation in xml(name):
                    if relation.get('TargetMode','').lower()=='external':
                        raise ValueError('包含外部关系，当前导入不访问外部文件或网址')
        if '[Content_Types].xml' not in names:
            raise ValueError('缺少.xlsx内容类型声明')
        if any('macroEnabled' in (x.get('ContentType') or '') for x in xml('[Content_Types].xml')):
            raise ValueError('不支持含宏的工作簿')
        shared=[]
        if 'xl/sharedStrings.xml' in names:
            for si in xml('xl/sharedStrings.xml'):
                shared.append(''.join(t.text or '' for t in si.iter() if local(t.tag)=='t'))
            if len(shared)>MAX_CELLS:
                raise ValueError('共享字符串过多')
        styles=[]
        if 'xl/styles.xml' in names:
            style_root=xml('xl/styles.xml')
            formats={int(n.get('numFmtId')):n.get('formatCode') for n in style_root.iter() if local(n.tag)=='numFmt'}
            for group in style_root:
                if local(group.tag)=='cellXfs':
                    styles=[dict(num_fmt_id=int(x.get('numFmtId','0')),format_code=formats.get(int(x.get('numFmtId','0')))) for x in group]
        rels={r.get('Id'):r for r in xml('xl/_rels/workbook.xml.rels')}
        workbook=xml('xl/workbook.xml');sheets=[];total_cells=0;total_rows=0
        declared=[n for n in workbook.iter() if local(n.tag)=='sheet']
        if not 1<=len(declared)<=MAX_SHEETS:
            raise ValueError('工作簿需包含1—30张工作表')
        for sheet in declared:
            name=sheet.get('name','');rid=sheet.get('{'+DOC_REL+'}id')
            relation=rels.get(rid)
            if relation is None or not (relation.get('Type') or '').endswith('/worksheet'):
                raise ValueError('工作表关系缺失或不是普通单元格工作表')
            part=safe_part('xl',relation.get('Target'))
            root=xml(part);rows=[];lookup={};seen_rows=set()
            for row_node in root.iter():
                if local(row_node.tag)!='row':continue
                try:row_number=int(row_node.get('r','0'))
                except ValueError as error:raise ValueError('行号无效') from error
                if not 1<=row_number<=1048576 or row_number in seen_rows:
                    raise ValueError('工作表包含重复或无效行号')
                seen_rows.add(row_number);total_rows+=1
                if total_rows>MAX_ROWS:raise ValueError('原始预览最多保留10000个显式行，请明确拆分源文件')
                cells=[]
                for node in row_node:
                    if local(node.tag)!='c':continue
                    ref=node.get('r');r,c=cell_position(ref)
                    if r!=row_number or ref in lookup:raise ValueError('单元格行号不一致或引用重复')
                    total_cells+=1
                    if total_cells>MAX_CELLS:raise ValueError('原始预览超过200000个单元格')
                    v=next((x for x in node if local(x.tag)=='v'),None)
                    formula=next((x for x in node if local(x.tag)=='f'),None)
                    raw=v.text if v is not None else None
                    kind=node.get('t','n');value=raw
                    if kind=='s' and raw is not None:
                        try:index=int(raw);value=shared[index] if index>=0 else (_ for _ in ()).throw(IndexError())
                        except (ValueError,IndexError) as error:raise ValueError('共享字符串引用无效') from error
                    elif kind=='inlineStr':
                        value=''.join(t.text or '' for t in node.iter() if local(t.tag)=='t');raw=value
                    elif kind=='b' and raw is not None:
                        value={'0':False,'1':True}.get(raw,raw)
                    elif kind=='n' and raw is not None:
                        try:
                            exact=Decimal(raw)
                            if not exact.is_finite():raise ValueError()
                            number=float(exact)
                            if abs(exact)>2**53-1 or not isfinite(number) or Decimal(str(number))!=exact:
                                value=raw
                                issue('numeric_precision_review','数值超出安全精度或转换会舍入；保留原始数值文本，需人工核对',name,[r],[ref])
                            else:
                                value=int(number) if number.is_integer() else number
                        except (ValueError,InvalidOperation):
                            issue('invalid_number','数字单元格内容无法解释；保留原值',name,[r],[ref])
                    try:style_index=int(node.get('s','0'))
                    except ValueError as error:raise ValueError('单元格样式引用无效') from error
                    item=dict(ref=ref,column=c,type=kind,raw_value=raw,value=value,
                        formula=None if formula is None else dict(text=formula.text or '',attributes=dict(formula.attrib)),
                        cache_status='not_formula' if formula is None else 'cached' if v is not None and v.text is not None else 'missing',
                        style_index=style_index,merged_from=None,effective_value=value)
                    if formula is not None and item['cache_status']=='missing':
                        issue('formula_without_cache','公式无缓存值；本地预览不执行公式',name,[r],[ref])
                    cells.append(item);lookup[ref]=item
                rows.append(dict(row=row_number,hidden=row_node.get('hidden')=='1',cells=cells))
            merges=[];covered=set()
            for merge in root.iter():
                if local(merge.tag)!='mergeCell':continue
                ref=merge.get('ref','');parts=ref.split(':')
                if len(parts)!=2:raise ValueError('合并区域无效')
                start=cell_position(parts[0]);end=cell_position(parts[1])
                if end[0]<start[0] or end[1]<start[1] or (end[0]-start[0]+1)*(end[1]-start[1]+1)>MAX_CELLS:
                    raise ValueError('合并区域过大或方向无效')
                if len(merges)>=2000:raise ValueError('合并区域过多')
                if (len(merges)+1)*(len(lookup)+len(rows))>MAX_MERGE_WORK:
                    raise ValueError('合并预览复杂度超过本地核对上限，请明确拆分源文件')
                for earlier in merges:
                    left,right=earlier['ref'].split(':');a,b=cell_position(left),cell_position(right)
                    if start[0]<=b[0] and a[0]<=end[0] and start[1]<=b[1] and a[1]<=end[1]:
                        raise ValueError('工作表存在重叠合并区域')
                merges.append(dict(ref=ref,anchor=parts[0]))
                for existing_ref,item in lookup.items():
                    r,c=cell_position(existing_ref)
                    if start[0]<=r<=end[0] and start[1]<=c<=end[1]:
                        if existing_ref in covered:raise ValueError('工作表存在重叠合并区域')
                        covered.add(existing_ref)
                        if existing_ref!=parts[0]:
                            item['merged_from']=parts[0]
                            item['effective_value']=lookup.get(parts[0],{}).get('value')
                            if item['value'] is not None:
                                issue('merged_value_conflict','合并区域非首格仍有原值，需人工核对',name,[r],[existing_ref])
            # Sparse XLSX can omit merged child cells: materialize only within
            # existing source rows, marking that no source cell was present.
            for merge in merges:
                a,b=merge['ref'].split(':');r1,c1=cell_position(a);r2,c2=cell_position(b)
                anchor=lookup.get(a,{}).get('value')
                for row in rows:
                    if not r1<=row['row']<=r2:continue
                    present={c['column'] for c in row['cells']}
                    for col in range(c1,c2+1):
                        if col in present:continue
                        total_cells+=1
                        if total_cells>MAX_CELLS:raise ValueError('合并预览超过200000个单元格')
                        letters='';num=col
                        while num:num,k=divmod(num-1,26);letters=chr(65+k)+letters
                        row['cells'].append(dict(ref=letters+str(row['row']),column=col,type='blank',raw_value=None,
                            value=None,formula=None,cache_status='not_formula',style_index=0,merged_from=a,
                            effective_value=anchor,source_cell_present=False))
                if sum(len(r['cells']) for r in rows)>MAX_CELLS:raise ValueError('合并预览超过200000个单元格')
            for row in rows:row['cells'].sort(key=lambda c:c['column'])
            sheet_result=dict(name=name,state=sheet.get('state','visible'),part=part,rows=rows,merges=merges)
            sheets.append(sheet_result)
        result=dict(filename=filename,sha256=sha256(content).hexdigest(),size_bytes=len(content),
            sheets=sheets,styles=styles,formula_policy='保留公式与缓存；不执行、不重算、不访问外部资源')
        audit=_audit(sheets,issues)
        result['audit']=audit
        return result


def _audit(sheets,issues):
    official=None
    for sheet in sheets:
        cells={c['ref']:c for row in sheet['rows'] for c in row['cells']}
        if all(cells.get(ref,{}).get('value')==value for ref,value in [('A4','序号'),('C4','收货单位'),('D4','收货地址'),('E5','件数'),('H5','件数')]):
            official=sheet;break
    def add(code,message,rows=None,cells=None):
        issues.append(dict(code=code,message=message,sheet=official['name'] if official else None,
            rows=rows or [],cells=cells or [],severity='review'))
    if official is None:
        add('mapping_required','未识别主办方订单表头；全部原行已保留，需人工明确字段、单位和数据起始行')
        return dict(profile='generic_raw',record_rows=None,issues=issues,can_solve=False,
                    summary='仅原始预览；不能直接作为配送任务求解')
    rows=[r for r in official['rows'] if r['row']>=6 and any(c['value'] is not None or c['formula'] is not None for c in r['cells'] if c['column']<=10)]
    def cell(row,col):return next((c for c in row['cells'] if c['column']==col),{})
    names={};numbers=[];blank_cold=[];inherited=[];cold_ratio=[];regular_count=0;cold_count=0
    for row in rows:
        n=row['row'];name=cell(row,3).get('effective_value')
        if name not in (None,''):names.setdefault(str(name),[]).append(n)
        else:add('missing_store_name','收货单位为空，不能自动生成门店身份',[n],[f'C{n}'])
        if isinstance(name,str) and '推断' in name:add('inferred_store_name','收货单位包含推断标记，需核对主数据',[n],[f'C{n}'])
        seq=cell(row,1).get('value')
        if isinstance(seq,(int,float)) and not isinstance(seq,bool) and int(seq)==seq:numbers.append(int(seq))
        else:add('invalid_sequence','序号为空或不是整数；不自动补造订单',[n],[f'A{n}'])
        address=cell(row,4)
        if address.get('merged_from') and address.get('value') is None and address.get('effective_value') is not None:inherited.append(n)
        if address.get('effective_value') in (None,''):add('missing_address','地址为空，不能用上行非合并地址填补',[n],[f'D{n}'])
        cold=[cell(row,c).get('value') for c in (8,9,10)]
        if all(v is None for v in cold):blank_cold.append(n)
        elif any(v is None for v in cold):add('partial_cold_fields','冷链数量/体积/重量部分缺失，不能默认零',[n],[f'{c}{n}' for c in 'HIJ'])
        if isinstance(cold[0],(int,float)) and not isinstance(cold[0],bool):cold_count+=cold[0]
        q,v=cell(row,8).get('value'),cell(row,9).get('value')
        if isinstance(q,(int,float)) and isinstance(v,(int,float)) and q>0 and abs(v/q-.001)>1e-8:cold_ratio.append(n)
        q=cell(row,5).get('value')
        if isinstance(q,(int,float)) and not isinstance(q,bool):regular_count+=q
        for col in range(5,11):
            value=cell(row,col).get('value')
            if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or value<0):
                add('invalid_quantity','数量、重量或体积不是非负数字；保留原值',[n],[cell(row,col).get('ref',str(col))])
    duplicate_names=[rs for rs in names.values() if len(rs)>1]
    for group in duplicate_names:add('duplicate_store_name','同名收货单位多行；不自动合并或删除，需确认订单/批次/地址',group)
    counts=Counter(numbers);duplicate_seq=[n for n,count in counts.items() if count>1]
    missing=sorted(set(range(min(numbers),max(numbers)+1))-set(numbers)) if numbers and max(numbers)-min(numbers)<=MAX_ROWS else []
    if duplicate_seq:add('duplicate_sequence','存在重复序号：'+','.join(map(str,duplicate_seq)))
    if missing:add('sequence_gap','序号存在缺口：'+','.join(map(str,missing))+'；不补造原始记录')
    if blank_cold:add('blank_cold_fields','这些行冷链数量、体积和重量同时为空；未知不等于0',blank_cold)
    if inherited:add('merged_address','地址仅在原合并区域内继承，原值与来源首格均保留',inherited)
    if cold_ratio:add('cold_volume_unit_review','若10×10×10为厘米、体积列为m³，应为每件0.001m³；这些行与该条件值不同，须确认单位/包装层级，未改写数值',cold_ratio)
    add('missing_dispatch_inputs','附件未给稳定门店ID、仓址、车辆、道路、批次日期与回收量；此处仅保存核对，不自动添加模拟输入')
    return dict(profile='official_order_headers',sheet=official['name'],record_rows=len(rows),
        source_rows=[r['row'] for r in rows],unique_store_name_strings=len(names),
        missing_sequence=missing,duplicate_sequence=duplicate_seq,duplicate_name_groups=len(duplicate_names),
        blank_cold_rows=len(blank_cold),merged_address_rows=len(inherited),regular_quantity=regular_count,
        cold_quantity_nonblank=cold_count,issues=issues,can_solve=False,
        summary='全部原行已保留；名称不等于已确认门店，未知冷链不按零，尚不可直接求解')


class IntakeStore:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS intake_documents (id TEXT PRIMARY KEY, document TEXT NOT NULL, source BLOB NOT NULL)')

    @contextmanager
    def connect(self):
        connection=sqlite3.connect(self.path,timeout=10)
        try:
            with connection:yield connection
        finally:connection.close()

    def create(self,name,filename,content_bytes):
        if not isinstance(name,str) or not 1<=len(name.strip())<=80:raise ValueError('核对任务名称需要1—80字')
        parsed=parse_xlsx(content_bytes,filename)
        document=dict(id=uuid4().hex,name=name.strip(),created_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
            status='待人工核对，不能直接求解',**parsed)
        with self.connect() as connection:
            connection.execute('INSERT INTO intake_documents VALUES (?,?,?)',(document['id'],json.dumps(document,ensure_ascii=False,allow_nan=False),content_bytes))
        return document

    def read(self,document_id):
        with self.connect() as connection:
            row=connection.execute('SELECT document FROM intake_documents WHERE id=?',(document_id,)).fetchone()
        if row is None:raise ValueError('原始核对任务不存在')
        return json.loads(row[0])

    def list(self):
        with self.connect() as connection:
            documents=[json.loads(row[0]) for row in connection.execute('SELECT document FROM intake_documents')]
        return [dict(id=d['id'],name=d['name'],filename=d['filename'],sha256=d['sha256'],created_at=d['created_at'],
            status=d['status'],sheets=len(d['sheets']),record_rows=d['audit']['record_rows'],issues=len(d['audit']['issues']))
            for d in sorted(documents,key=lambda d:(d['created_at'],d['id']),reverse=True)]


def sample_xlsx():
    """Small synthetic official-shaped workbook; no real names or addresses."""
    def inline(ref,value):
        from xml.sax.saxutils import escape
        return f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
    def number(ref,value):return f'<c r="{ref}"><v>{value}</v></c>'
    rows=[]
    rows.append('<row r="2">'+inline('B2','构造示例：冷链尺寸10*10*10，单位仍待核对')+'</row>')
    rows.append('<row r="4">'+''.join(inline(r,v) for r,v in [('A4','序号'),('C4','收货单位'),('D4','收货地址')])+'</row>')
    rows.append('<row r="5">'+inline('E5','件数')+inline('H5','件数')+'</row>')
    for row,sequence,name,address,cold in [(6,1,'构造门店甲','构造地址甲',True),(7,3,'构造门店甲',None,False),(8,4,'构造门店乙',None,True)]:
        cells=number(f'A{row}',sequence)+inline(f'C{row}',name)+(inline(f'D{row}',address) if address else '')
        cells+=number(f'E{row}',2)+number(f'F{row}',.144)+number(f'G{row}',14)
        if cold:cells+=number(f'H{row}',1)+number(f'I{row}',.01)+number(f'J{row}',.5)
        rows.append(f'<row r="{row}">{cells}</row>')
    parts={
        '[Content_Types].xml':'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        '_rels/.rels':f'<Relationships xmlns="{REL_NS}"><Relationship Id="rId1" Type="{DOC_REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml':f'<workbook xmlns="{NS}" xmlns:r="{DOC_REL}"><sheets><sheet name="构造托运单" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels':f'<Relationships xmlns="{REL_NS}"><Relationship Id="rId1" Type="{DOC_REL}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        'xl/worksheets/sheet1.xml':f'<worksheet xmlns="{NS}"><sheetData>'+''.join(rows)+'</sheetData><mergeCells><mergeCell ref="D6:D7"/></mergeCells></worksheet>'}
    output=BytesIO()
    with ZipFile(output,'w',ZIP_DEFLATED) as archive:
        for name,content in parts.items():archive.writestr(name,content.encode('utf-8'))
    return output.getvalue()
