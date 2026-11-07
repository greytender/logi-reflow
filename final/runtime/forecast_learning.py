"""Local, versioned pickup calibration with chronological promotion gates.

Only reviewed observations train models. Simulation and enterprise realms are
separate. Model activation changes forecasts only, never facts or dispatch.
"""
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import ceil, isfinite
from pathlib import Path
import sqlite3

TARGETS = ('cartons', 'totes')
ALGORITHM_VERSION = 'residual-ewma-v2'
PARAMETERS = dict(window=20,alpha=.35,shrinkage=2,min_site_count=3,upper_quantile=.9,upper_margin=0)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def timestamp(value):
    if not isinstance(value, str):raise ValueError('时间必须为带时区ISO字符串')
    try:dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:raise ValueError('时间必须为带时区ISO字符串') from None
    if dt.tzinfo is None:raise ValueError('时间必须带时区')
    return dt.astimezone(timezone.utc).isoformat(timespec='microseconds')


def quantities(value):
    if not isinstance(value,dict) or set(value)!=set(TARGETS):raise ValueError('回收字段需为cartons/totes')
    for n in value.values():
        if isinstance(n,bool) or not isinstance(n,(int,float)) or not isfinite(n) or int(n)!=n or not 0<=n<=100000:
            raise ValueError('回收数量需为0至100000的整数')
    return {k:int(value[k]) for k in TARGETS}


def observation(raw):
    required={'id','warehouse_id','store_id','data_kind','source','verified','reference',
              'decision_at','observed_at','available_at','nominal','actual'}
    if not isinstance(raw,dict) or set(raw)!=required:raise ValueError('学习回执字段不完整或未映射')
    row=deepcopy(raw)
    for key in ('id','warehouse_id','store_id'):
        if not isinstance(row[key],str) or not row[key].strip() or len(row[key])>120:raise ValueError('学习记录身份无效')
    if row['data_kind'] not in ('simulated','enterprise'):raise ValueError('学习数据域无效')
    if row['source'] != ('simulated' if row['data_kind']=='simulated' else 'manual'):raise ValueError('学习来源与数据域不一致')
    if type(row['verified']) is not bool:raise ValueError('核对标记需为布尔值')
    if not isinstance(row['reference'],str) or len(row['reference'])>1000:raise ValueError('需填写回执来源说明')
    if row['verified'] and not row['reference'].strip():raise ValueError('已核对记录需要来源依据，标记本身不证明真实性')
    for key in ('decision_at','observed_at','available_at'):row[key]=timestamp(row[key])
    if not row['decision_at']<=row['observed_at']<=row['available_at']:raise ValueError('预测、实际发生与可取得时间顺序错误')
    row['nominal']=quantities(row['nominal']);row['actual']=quantities(row['actual'])
    return row


def predict(model, warehouse_id, data_kind, store_id, nominal, at):
    nominal=quantities(nominal);at=timestamp(at)
    result=dict(nominal=nominal,expected=deepcopy(nominal),risk_upper=deepcopy(nominal),
                model_id=None,sample_count=0,source='nominal',reason='尚无有效历史模型',requires_review=False)
    if model is None:return result
    if model.get('algorithm_version')!=ALGORITHM_VERSION or model.get('parameters')!=PARAMETERS:
        raise ValueError('预测算法或参数版本不匹配，需要显式迁移模型')
    if model['warehouse_id']!=warehouse_id or model['data_kind']!=data_kind:raise ValueError('禁止跨仓或跨模拟/企业域套用模型')
    if max(model['trained_until'],model.get('available_after',model['trained_until']))>=at:
        raise ValueError('预测时刻必须晚于训练及模型激活验证截止，防止未来信息泄漏')
    site=model['sites'].get(store_id)
    result['model_id']=model['id']
    result['requires_review']=bool(model.get('requires_review',False))
    if site is None or site['count']<PARAMETERS['min_site_count']:
        result['reason']='本门店样本不足，保留输入预测';return result
    result.update(sample_count=site['count'],source='learned',reason='近期残差校正；区间为经验值，不保证未来覆盖')
    for key in TARGETS:
        values=site['residuals'][key]
        ewma=values[0]
        for val in values[1:]:ewma=(1-PARAMETERS['alpha'])*ewma+PARAMETERS['alpha']*val
        correction=ewma*len(values)/(len(values)+PARAMETERS['shrinkage'])
        expected=max(0,min(100000,round(nominal[key]+correction)))
        ordered=sorted(values);upper=ordered[min(len(ordered)-1,ceil(PARAMETERS['upper_quantile']*(len(ordered)-1)))]+PARAMETERS['upper_margin']
        result['expected'][key]=expected
        result['risk_upper'][key]=max(expected,min(100000,max(0,ceil(nominal[key]+upper))))
    return result


class ForecastStore:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS models(id TEXT PRIMARY KEY,payload TEXT NOT NULL,validation TEXT);
            CREATE TABLE IF NOT EXISTS active(warehouse_id TEXT,data_kind TEXT,model_id TEXT,
                available_after TEXT,requires_review INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(warehouse_id,data_kind));
            CREATE TABLE IF NOT EXISTS events(event_key TEXT PRIMARY KEY,observation_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(sequence INTEGER PRIMARY KEY AUTOINCREMENT,action TEXT,payload TEXT);''')
            if 'available_after' not in {r[1] for r in db.execute('PRAGMA table_info(active)')}:
                db.execute('ALTER TABLE active ADD COLUMN available_after TEXT')
            if 'requires_review' not in {r[1] for r in db.execute('PRAGMA table_info(active)')}:
                db.execute('ALTER TABLE active ADD COLUMN requires_review INTEGER NOT NULL DEFAULT 0')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(str(self.path),timeout=15)
        try:
            with db:yield db
        finally:db.close()

    def record(self,raw):
        row=observation(raw);payload=encode(row)
        event_key=sha256(encode([row[k] for k in ('warehouse_id','data_kind','store_id','decision_at')]).encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT payload FROM observations WHERE id=?',(row['id'],)).fetchone()
            if old and old[0]!=payload:raise ValueError('相同回执ID不能改写；更正需先撤销原学习事件，不能叠加训练')
            event=db.execute('SELECT observation_id FROM events WHERE event_key=?',(event_key,)).fetchone()
            if event and event[0]!=row['id']:raise ValueError('同仓同店同一决策批次已有学习回执，禁止换ID重复训练或叠加更正')
            db.execute('INSERT OR IGNORE INTO observations VALUES (?,?)',(row['id'],payload))
            db.execute('INSERT OR IGNORE INTO events VALUES (?,?)',(event_key,row['id']))
        return row

    def records(self,warehouse_id,data_kind,until):
        until=timestamp(until)
        with self.connect() as db:rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM observations')]
        return sorted((r for r in rows if r['warehouse_id']==warehouse_id and r['data_kind']==data_kind
                       and r['verified'] and r['available_at']<=until),key=lambda r:(r['observed_at'],r['id']))

    def train(self,warehouse_id,data_kind,until):
        if data_kind not in ('simulated','enterprise'):raise ValueError('学习数据域无效')
        cutoff=timestamp(until);rows=self.records(warehouse_id,data_kind,cutoff)
        sites={}
        for row in rows:
            site=sites.setdefault(row['store_id'],dict(count=0,residuals={k:[] for k in TARGETS}))
            site['count']+=1
            for key in TARGETS:
                site['residuals'][key].append(row['actual'][key]-row['nominal'][key])
                site['residuals'][key]=site['residuals'][key][-PARAMETERS['window']:]
        model=dict(schema='logireflow.pickup-calibration.v1',warehouse_id=warehouse_id,data_kind=data_kind,
                   algorithm_version=ALGORITHM_VERSION,parameters=deepcopy(PARAMETERS),
                   trained_until=cutoff,sites=sites,training_record_ids=[r['id'] for r in rows],
                   training_sha256=sha256(encode(rows).encode()).hexdigest(),training_count=len(rows))
        model['id']=sha256(encode(model).encode()).hexdigest()[:24]
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO models VALUES (?,?,NULL)',(model['id'],encode(model)))
            db.execute('INSERT INTO audit(action,payload) VALUES (?,?)',('train',encode(dict(model_id=model['id'],count=len(rows)))))
        return model

    def get_model(self,model_id):
        with self.connect() as db:row=db.execute('SELECT payload FROM models WHERE id=?',(model_id,)).fetchone()
        if row is None:raise ValueError('模型版本不存在')
        return json.loads(row[0])

    def active_model(self,warehouse_id,data_kind):
        with self.connect() as db:row=db.execute('SELECT model_id,available_after,requires_review FROM active WHERE warehouse_id=? AND data_kind=?',(warehouse_id,data_kind)).fetchone()
        if not row:return None
        if row[1] is None:raise ValueError('旧激活缺少时间快照，请回退后重新验证')
        model=self.get_model(row[0]);model['available_after']=row[1];model['requires_review']=bool(row[2])
        return model

    def validate(self,model_id,start,until):
        model=self.get_model(model_id);start=timestamp(start);until=timestamp(until)
        if not model['trained_until']<start<=until:raise ValueError('验证窗口必须严格晚于训练窗口')
        rows=[r for r in self.records(model['warehouse_id'],model['data_kind'],until)
              if start<=r['decision_at']<=until]
        incumbent=self.active_model(model['warehouse_id'],model['data_kind'])
        errors=[];sites={};covered=0
        for row in rows:
            candidate=predict(model,row['warehouse_id'],row['data_kind'],row['store_id'],row['nominal'],row['decision_at'])
            old=predict(incumbent,row['warehouse_id'],row['data_kind'],row['store_id'],row['nominal'],row['decision_at'])
            c=sum(abs(candidate['expected'][k]-row['actual'][k]) for k in TARGETS)
            b=sum(abs(row['nominal'][k]-row['actual'][k]) for k in TARGETS)
            o=sum(abs(old['expected'][k]-row['actual'][k]) for k in TARGETS)
            covered+=sum(row['actual'][k]<=candidate['risk_upper'][k] for k in TARGETS)
            errors.append((c,b,o));sites.setdefault(row['store_id'],[]).append((c,o))
        n=len(rows);mae=[sum(x[i] for x in errors)/(2*n) if n else None for i in range(3)]
        no_site_regression=all(sum(c for c,o in values)<=sum(o for c,o in values) for values in sites.values())
        passed=bool(n>=6 and model['training_count']>=6 and mae[0]<.95*mae[1]
                    and mae[0]<=mae[2] and no_site_regression and covered/(2*n)>=.8)
        report=dict(model_id=model_id,passed=passed,validation_count=n,start=start,until=until,
                    candidate_mae_units=mae[0],nominal_mae_units=mae[1],incumbent_mae_units=mae[2],
                    risk_upper_coverage=covered/(2*n) if n else None,no_site_regression=no_site_regression,
                    incumbent_id=incumbent['id'] if incumbent else None,
                    validation_record_ids=[r['id'] for r in rows],
                    validation_sha256=sha256(encode(rows).encode()).hexdigest(),
                    scope='时间留出回收数量验证，不是调度收益或未来不退化保证')
        with self.connect() as db:db.execute('UPDATE models SET validation=? WHERE id=?',(encode(report),model_id))
        return report

    def activate(self,model_id):
        model=self.get_model(model_id)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT validation FROM models WHERE id=?',(model_id,)).fetchone()
            validation=json.loads(row[0]) if row and row[0] else None
            if not validation or not validation['passed']:raise ValueError('模型尚未通过时间留出验证，保留当前版本')
            current_rows=[r for r in self.records(model['warehouse_id'],model['data_kind'],validation['until'])
                          if validation['start']<=r['decision_at']<=validation['until']]
            if sha256(encode(current_rows).encode()).hexdigest()!=validation['validation_sha256']:
                raise ValueError('验证窗口的回执集合已变化，请重新验证')
            current=db.execute('SELECT model_id FROM active WHERE warehouse_id=? AND data_kind=?',
                               (model['warehouse_id'],model['data_kind'])).fetchone()
            if validation['incumbent_id']!=(current[0] if current else None):raise ValueError('当前模型已变化，请重新验证后再激活')
            db.execute('INSERT OR REPLACE INTO active VALUES (?,?,?,?,0)',(model['warehouse_id'],model['data_kind'],model_id,validation['until']))
            db.execute('INSERT INTO audit(action,payload) VALUES (?,?)',('activate',encode(dict(
                model_id=model_id,previous_id=current[0] if current else None,validation=validation))))
        return self.active_model(model['warehouse_id'],model['data_kind'])

    def rollback(self,warehouse_id,data_kind,model_id=None):
        if model_id is not None:
            model=self.get_model(model_id)
            if (model['warehouse_id'],model['data_kind'])!=(warehouse_id,data_kind):raise ValueError('回退模型数据域不一致')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if model_id is not None:
                history=[json.loads(r[0]) for r in db.execute("SELECT payload FROM audit WHERE action='activate'")]
                activations=[r for r in history if r['model_id']==model_id]
                if not activations:raise ValueError('只能回退至曾激活的版本')
                db.execute('INSERT OR REPLACE INTO active VALUES (?,?,?,?,0)',(warehouse_id,data_kind,model_id,activations[-1]['validation']['until']))
            else:db.execute('DELETE FROM active WHERE warehouse_id=? AND data_kind=?',(warehouse_id,data_kind))
            db.execute('INSERT INTO audit(action,payload) VALUES (?,?)',('rollback',encode(dict(
                warehouse_id=warehouse_id,data_kind=data_kind,model_id=model_id))))

    def retrain(self,warehouse_id,data_kind,training_until,validation_start,validation_until):
        model=self.train(warehouse_id,data_kind,training_until)
        active=self.active_model(warehouse_id,data_kind)
        if active and active['id']==model['id'] and active['available_after']==timestamp(validation_until):
            with self.connect() as db:
                history=db.execute("SELECT payload FROM audit WHERE action='activate' ORDER BY sequence DESC")
                for (payload,) in history:
                    event=json.loads(payload)
                    validation=event['validation']
                    if (event['model_id']==model['id'] and validation['start']==timestamp(validation_start)
                            and validation['until']==active['available_after']):
                        return dict(candidate=model,validation=validation,activated=True,reused_activation=True)
        validation=self.validate(model['id'],validation_start,validation_until)
        if validation['passed']:self.activate(model['id'])
        return dict(candidate=model,validation=validation,activated=validation['passed'])

    def monitor(self,warehouse_id,data_kind,start,until,*,auto_rollback=False):
        if type(auto_rollback) is not bool:raise ValueError('自动回退开关需为布尔值')
        model=self.active_model(warehouse_id,data_kind);start=timestamp(start);until=timestamp(until)
        if start>until:raise ValueError('监控窗口顺序无效')
        if model is None:return dict(model_id=None,observation_count=0,rollback_recommended=False,rolled_back=False)
        rows=[r for r in self.records(warehouse_id,data_kind,until)
              if max(start,model['available_after'])<r['decision_at']<=until]
        learned=nominal=covered=0
        for row in rows:
            estimate=predict(model,warehouse_id,data_kind,row['store_id'],row['nominal'],row['decision_at'])
            learned+=sum(abs(estimate['expected'][k]-row['actual'][k]) for k in TARGETS)
            nominal+=sum(abs(row['nominal'][k]-row['actual'][k]) for k in TARGETS)
            covered+=sum(row['actual'][k]<=estimate['risk_upper'][k] for k in TARGETS)
        n=len(rows);recommended=n>=6 and learned>1.1*nominal+.2*n
        coverage_warning=bool(n>=6 and covered/(2*n)<.8)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            key=(warehouse_id,data_kind,model['id'],model['available_after'])
            rolled=False
            if recommended and auto_rollback:
                rolled=bool(db.execute('DELETE FROM active WHERE warehouse_id=? AND data_kind=? AND model_id=? AND available_after=?',key).rowcount)
                if rolled:
                    db.execute('INSERT INTO audit(action,payload) VALUES (?,?)',('rollback',encode(dict(
                        warehouse_id=warehouse_id,data_kind=data_kind,model_id=None))))
            elif coverage_warning:
                db.execute('UPDATE active SET requires_review=1 WHERE warehouse_id=? AND data_kind=? AND model_id=? AND available_after=?',key)
            report=dict(model_id=model['id'],observation_count=n,
                    learned_mae_units=learned/(2*n) if n else None,
                    nominal_mae_units=nominal/(2*n) if n else None,
                    risk_upper_coverage=covered/(2*n) if n else None,
                    coverage_warning=coverage_warning,
                    rollback_recommended=recommended,rolled_back=rolled,
                    scope='已取得回执的后验漂移监控；回退仅影响后续预测')
            db.execute('INSERT INTO audit(action,payload) VALUES (?,?)',('monitor',encode(report)))
        return report
