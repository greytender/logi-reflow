"""Versioned local field/unit/identity drafts; no conversion to dispatch tasks."""
from collections import defaultdict
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timezone
import json
from math import isfinite
from pathlib import Path
import sqlite3
import central_orders

from store import Conflict

FIELDS=('order_id','store_id','store_name','address','regular_quantity','regular_weight','regular_volume',
        'cold_quantity','cold_weight','cold_volume')
UNIT_OPTIONS={'regular_weight':('kg','g'),'regular_volume':('m3','L','cm3'),
              'cold_weight':('kg','g'),'cold_volume':('m3','L','cm3')}
TOP_KEYS={'sheet_index','rows','fields','units','identities','batch_id','delivery_date','note'}
IDENTITY_KEYS={'order_id','store_id','store_name','address','batch_id','note'}
MAX_VERSIONS=50


def timestamp():return datetime.now(timezone.utc).isoformat(timespec='seconds')


def text(value,label,limit=120):
    if not isinstance(value,str) or len(value)>limit:
        raise ValueError(label+'需要长度范围内的文本')
    return value.strip() if label!='备注' else value


def default_config(document):
    audit=document['audit'];index=0
    if audit.get('profile')=='official_order_headers':
        index=next((i for i,s in enumerate(document['sheets']) if s['name']==audit.get('sheet')),0)
        rows=list(audit['source_rows'])
        fields=dict(order_id=None,store_id=None,store_name=3,address=4,regular_quantity=5,
            regular_weight=7,regular_volume=6,cold_quantity=8,cold_weight=10,cold_volume=9)
    else:
        rows=[r['row'] for r in document['sheets'][0]['rows'] if any(c.get('value') is not None or c.get('formula') is not None for c in r['cells'])]
        fields={field:None for field in FIELDS}
    config = dict(sheet_index=index,rows=rows,fields=fields,units={k:None for k in UNIT_OPTIONS},
                  identities={},batch_id='',delivery_date='',note='')
    return central_orders.configure(document, config)


def validate(document,raw):
    if not isinstance(raw,dict) or set(raw)!=TOP_KEYS:
        raise ValueError('映射草稿字段不完整或包含未知字段')
    config=deepcopy(raw)
    index=config['sheet_index']
    if type(index) is not int or not 0<=index<len(document['sheets']):
        raise ValueError('请选择原始资料中的工作表')
    source_rows={r['row'] for r in document['sheets'][index]['rows']}
    rows=config['rows']
    if not isinstance(rows,list) or any(type(r) is not int or r not in source_rows for r in rows) or len(rows)!=len(set(rows)):
        raise ValueError('选中行必须是本工作表真实原行，且不能重复')
    fields=config['fields']
    if not isinstance(fields,dict) or set(fields)!=set(FIELDS):raise ValueError('字段映射不完整或含未知字段')
    for column in fields.values():
        if column is not None and (type(column) is not int or not 1<=column<=16384):
            raise ValueError('字段列号需要1—16384的整数或空值')
    units=config['units']
    if not isinstance(units,dict) or set(units)!=set(UNIT_OPTIONS):raise ValueError('单位确认字段不完整或含未知字段')
    for key,value in units.items():
        if value is not None and value not in UNIT_OPTIONS[key]:raise ValueError('不支持的单位：'+key)
    identities=config['identities']
    if not isinstance(identities,dict):raise ValueError('逐行身份草稿必须是对象')
    for row,identity in list(identities.items()):
        if not isinstance(row,str) or not row.isdecimal() or str(int(row))!=row or int(row) not in source_rows:
            raise ValueError('身份草稿需引用当前工作表的原行号')
        if not isinstance(identity,dict) or not set(identity)<=IDENTITY_KEYS:raise ValueError('身份草稿包含未知字段')
        identities[row]={k:text(identity.get(k,''),'备注' if k=='note' else k,
                                500 if k in ('note','address') else 120) for k in IDENTITY_KEYS}
    config['batch_id']=text(config['batch_id'],'批次编号')
    delivery=text(config['delivery_date'],'配送日期',10)
    if delivery:
        try:
            parsed=date.fromisoformat(delivery)
            if parsed.isoformat()!=delivery:raise ValueError()
        except ValueError as error:raise ValueError('配送日期需要YYYY-MM-DD或留空待补') from error
    config['delivery_date']=delivery
    config['note']=text(config['note'],'备注',1000)
    return config


def assess(document,config):
    sheet=document['sheets'][config['sheet_index']]
    lookup={row['row']:row for row in sheet['rows']}
    result=[];issues=[]
    def issue(code,message,rows=None,fields=None):
        return dict(code=code,message=message,rows=rows or [],fields=fields or [])
    source_ids=defaultdict(list);stores=defaultdict(list)
    columns=defaultdict(list)
    for field,column in config['fields'].items():
        if column is not None:columns[column].append(field)
    for column,fields in columns.items():
        if len(fields)>1:issues.append(issue('shared_mapping_column','多个业务字段使用同一原列，请核对语义，未自动拆分',fields=fields))
    for number in config['rows']:
        row=lookup[number];bycol={c['column']:c for c in row['cells']}
        refs={field:deepcopy(bycol.get(column)) if column is not None else None for field,column in config['fields'].items()}
        row_issues=[];missing=[]
        def mapped(field,identity=False):
            cell=refs[field]
            if not cell:return None
            if cell.get('formula') is not None and cell.get('cache_status')=='missing':
                row_issues.append(issue('formula_without_cache','该映射来源公式无缓存，不执行公式也不据此运算',[number],[field]));return None
            if identity and cell.get('type')=='n' and cell.get('raw_value') is not None:
                return cell['raw_value']
            return cell.get('effective_value',cell.get('value'))
        saved=config['identities'].get(str(number),{})
        def identity_text(field):
            manual=saved.get(field,'')
            if manual:return manual
            value=mapped(field,True)
            return '' if value is None else str(value).strip()
        identity=dict(order_id=identity_text('order_id'),store_id=identity_text('store_id'),
            store_name=identity_text('store_name'),address=identity_text('address'),
            batch_id=saved.get('batch_id') or config['batch_id'],delivery_date=config['delivery_date'])
        for field,label in [('order_id','订单ID'),('store_id','门店ID'),('store_name','门店名称'),('address','地址'),('batch_id','批次'),('delivery_date','配送日期')]:
            if not identity[field]:missing.append(label)
        normalized={}
        for field in FIELDS[4:]:
            cell=refs[field]
            key=field if field.endswith('quantity') else field+('_kg' if field.endswith('weight') else '_m3')
            value=mapped(field)
            if cell and cell.get('merged_from'):
                value=None
                row_issues.append(issue('merged_numeric_ambiguous','合并数值未自动分摊或重复当作每行总量',[number],[field]))
            if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not isfinite(value) or value<0):
                row_issues.append(issue('invalid_numeric_value','映射数量/重量/体积不是可安全使用的非负数字',[number],[field]));value=None
            if cell and cell.get('formula') is not None and cell.get('cache_status')=='cached':
                row_issues.append(issue('formula_cache_unverified','使用源文件公式缓存值预览，未重算或证明缓存最新',[number],[field]))
            if field.endswith('quantity'):
                normalized[key]=value
                if value is None:missing.append(field)
            else:
                unit=config['units'][field]
                if unit is None:
                    normalized[key]=None;missing.append(field+'单位')
                elif value is None:
                    normalized[key]=None;missing.append(field)
                else:
                    factor={'kg':1,'g':.001,'m3':1,'L':.001,'cm3':.000001}[unit]
                    normalized[key]=value*factor
        if all(normalized[k] is None for k in ('cold_quantity','cold_weight_kg','cold_volume_m3')):
            row_issues.append(issue('cold_unknown','冷链字段未知或尚未确认单位，不按0处理',[number],['cold_quantity','cold_weight','cold_volume']))
        if row.get('hidden'):row_issues.append(issue('hidden_source_row','选中了原始隐藏行，需人工核对',[number]))
        item=dict(source_row=number,source_refs=refs,identity=identity,normalized=normalized,
            manual_identity_fields=[field for field in ('order_id','store_id','store_name','address','batch_id') if saved.get(field)],
            missing=missing,issues=row_issues,status='待人工核对' if missing or row_issues else '映射字段已补充，仍不能求解')
        item=central_orders.normalize(document,config,item)
        result.append(item)
        if identity['order_id']:source_ids[identity['order_id']].append(number)
        if identity['store_id']:stores[identity['store_id']].append((number,identity['address']))
    for rows in source_ids.values():
        if len(rows)>1:issues.append(issue('duplicate_order_id','选中行的订单ID重复，需核对，未自动去重',rows,['order_id']))
    for members in stores.values():
        addresses={address for _,address in members if address}
        if len(addresses)>1:issues.append(issue('store_id_address_conflict','同一门店ID对应不同地址，不能自动认定同一门店',[r for r,_ in members],['store_id','address']))
    for entry in issues:
        for item in result:
            if item['source_row'] in entry['rows']:
                item['issues'].append(deepcopy(entry));item['status']='待人工核对'
    global_missing=['车辆及能力参数','仓库与道路距离/时间','业务规则、回收预测与人工确认的求解输入合同']
    if not result:global_missing.insert(0,'尚未选择原始数据行')
    return dict(can_solve=False,selected_rows=len(result),selected_source_rows=list(config['rows']),
        missing=global_missing,issues=issues,rows=result,
        summary='仅映射、单位与身份核对草稿；不创建配送任务，不补造缺项或证明原单真实正确')


class ReviewStore:
    def __init__(self,path,intake_store):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.intake_store=intake_store
        with self.connect() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS intake_review_current (document_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, document TEXT NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS intake_review_history (document_id TEXT NOT NULL, revision INTEGER NOT NULL, document TEXT NOT NULL, PRIMARY KEY(document_id,revision))')

    @contextmanager
    def connect(self):
        connection=sqlite3.connect(self.path,timeout=10)
        try:
            with connection:yield connection
        finally:connection.close()

    def source(self,document_id):return self.intake_store.read(document_id)

    def _history(self,connection,document_id):
        return [json.loads(row[0]) for row in connection.execute('SELECT document FROM intake_review_history WHERE document_id=? ORDER BY revision',(document_id,))]

    def _snapshot(self,document_id,include_versions=False):
        source=self.source(document_id)
        with self.connect() as connection:
            connection.execute('BEGIN')
            row=connection.execute('SELECT document FROM intake_review_current WHERE document_id=?',(document_id,)).fetchone()
            history=self._history(connection,document_id)
        if row:
            result=json.loads(row[0])
            if result['source_sha256']!=source['sha256']:raise Conflict('原始资料哈希已变化，不能把旧映射套到新来源')
        else:
            config=default_config(source)
            result=dict(document_id=document_id,revision=0,source_sha256=source['sha256'],saved_at=None,
                        config=config,assessment=assess(source,config))
        result['history']=[{k:version[k] for k in ('revision','saved_at','source_sha256')} for version in history]
        if result['revision'] and (not history or history[-1]['revision']!=result['revision']):
            raise Conflict('当前映射修订与历史不一致，请保留资料并核查存储')
        if include_versions:result['versions']=history
        result['business_rules']=central_orders.summary(source,result['config'])
        return result

    def read(self,document_id):return self._snapshot(document_id)

    @contextmanager
    def confirm_current(self, document_id, revision, source_sha256):
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            current = connection.execute('SELECT revision,document FROM intake_review_current WHERE document_id=?',
                                         (document_id,)).fetchone()
            if current is None or current[0] != revision or json.loads(current[1])['source_sha256'] != source_sha256:
                raise Conflict('核对版本已更新，请刷新后再建任务')
            yield connection

    def save(self,document_id,revision,config,*,agent_provenance=None):
        if type(revision) is not int or revision<0:raise Conflict('映射修订号无效，请刷新资料')
        source=self.source(document_id);config=validate(source,config)
        result=dict(document_id=document_id,revision=revision+1,source_sha256=source['sha256'],
            saved_at=timestamp(),config=config,assessment=assess(source,config))
        if agent_provenance is not None:
            result['agent_provenance']=deepcopy(agent_provenance)
        payload=json.dumps(result,ensure_ascii=False,allow_nan=False)
        with self.connect() as connection:
            existing=connection.execute('SELECT revision,document FROM intake_review_current WHERE document_id=?',(document_id,)).fetchone()
            if (existing is None and revision!=0) or (existing is not None and existing[0]!=revision):
                raise Conflict('映射草稿已被更新，请刷新后核对；旧页面没有覆盖新版本')
            if existing is not None and json.loads(existing[1])['source_sha256']!=source['sha256']:
                raise Conflict('原始资料哈希已变化，旧映射没有覆盖新来源')
            if revision>=MAX_VERSIONS:raise ValueError('此资料已有50份映射修订，请导出历史后另行处理')
            if existing is None:
                try:connection.execute('INSERT INTO intake_review_current VALUES (?,?,?)',(document_id,1,payload))
                except sqlite3.IntegrityError as error:raise Conflict('映射草稿已在另一页面保存，请刷新') from error
            else:
                changed=connection.execute('UPDATE intake_review_current SET revision=?,document=? WHERE document_id=? AND revision=?',
                    (revision+1,payload,document_id,revision)).rowcount
                if changed!=1:raise Conflict('映射保存期间已更新，请刷新')
            connection.execute('INSERT INTO intake_review_history VALUES (?,?,?)',(document_id,revision+1,payload))
        return self.read(document_id)

    def export(self,document_id):
        result=self._snapshot(document_id,include_versions=True)
        result['export_kind']='intake_mapping_draft_not_dispatch_input'
        return result
