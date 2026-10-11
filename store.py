"""SQLite-backed, single-workstation simulation sessions with optimistic concurrency."""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4
import json
import sqlite3
import engine

class Conflict(ValueError):
    pass

def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')

def log(state, action, detail):
    state['logs'].append(dict(at=now(),action=action,detail=detail))

def add_candidate(state,result,message):
    for version in state['versions']:
        if version['status']=='pending': version['status']='superseded'
    current=next((e for e in state['events'] if e['status']=='current'),None)
    same=current and current['message']==message and all(current[k]==v for k,v in result['parsed']['event'].items())
    pid=f"P{len(state['versions'])}"
    if same: eid=current['id']
    else:
        for event in state['events']:
            if event['status']=='current': event['status']='superseded'
        eid=f"E{len(state['events'])+1}"
        state['events'].append(dict(id=eid,status='current',message=message,at=now(),**result['parsed']['event']))
    state['versions'].append(dict(id=pid,base='P0',event_id=eid,status='pending',at=now(),metrics=result['recovered']['metrics'],plan=result['recovered']))
    state.update(result=result,candidate_id=pid,active_plan=None,status='pending')
    log(state,'candidate',f'{eid} 生成恢复候选 {pid}；原计划 P0 仅供比较，尚未采用恢复方案。')

def new_session():
    result=engine.run(); state=dict(session_id=uuid4().hex,revision=1,created_at=now(),active_plan=None,
        candidate_id=None,status='pending',events=[],logs=[],versions=[dict(id='P0',base=None,event_id=None,status='reference',at=now(),metrics=result['initial']['metrics'],plan=result['initial'])])
    log(state,'initial','生成模拟初始计划 P0。')
    add_candidate(state,result,'V2 在 08:25 故障')
    return state

class Store:
    def __init__(self,path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('BEGIN IMMEDIATE')
            if not db.execute("SELECT value FROM meta WHERE key='current'").fetchone():
                self._save(db,new_session())
            db.commit()

    @contextmanager
    def connect(self):
        db=sqlite3.connect(str(self.path),timeout=15,isolation_level=None)
        try: yield db
        finally: db.close()

    def _save(self,db,state):
        db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?)',(state['session_id'],json.dumps(state,ensure_ascii=False)))
        db.execute("INSERT OR REPLACE INTO meta VALUES ('current',?)",(state['session_id'],))

    def _read(self,db):
        sid=db.execute("SELECT value FROM meta WHERE key='current'").fetchone()[0]
        state=json.loads(db.execute('SELECT document FROM sessions WHERE id=?',(sid,)).fetchone()[0])
        state['archived_count']=db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]-1
        return state

    def read(self):
        with self.connect() as db: return self._read(db)

    @contextmanager
    def transaction(self,expected):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state=self._read(db)
            if expected.get('session_id')!=state['session_id'] or type(expected.get('revision')) is not int or expected['revision']!=state['revision']:
                db.rollback(); raise Conflict('页面版本已过期，请刷新后核对当前方案，再操作。')
            try:
                yield db,state
                state['revision']+=1
                self._save(db,state); db.commit()
            except Exception:
                db.rollback(); raise

    def calculate(self,message,expected):
        parsed=engine.parse_message(message)
        if not parsed['ready']: return dict(parsed=parsed)
        # Calculation cannot change state until the version check passes in the transaction.
        result=engine.run(message)
        with self.transaction(expected) as (_,state):
            if state['status']=='adopted': raise Conflict('已采用方案的场景不能从头重算故障；请新建模拟场景。')
            add_candidate(state,result,message)
        return dict(session=state)

    def decide(self,action,expected):
        with self.transaction(expected) as (_,state):
            pid=expected.get('candidate_id')
            if pid!=state['candidate_id']: raise Conflict('候选方案已变化，请刷新并核对。')
            version=next(v for v in state['versions'] if v['id']==pid)
            if action=='adopt':
                if state['status']!='pending' or version['status']!='pending': raise Conflict('该候选当前不能采用。')
                if version['metrics']['unserved']>0: raise Conflict('仍有未安排订单，不能采用为完整恢复方案。')
                version['status']='adopted'; state.update(active_plan=pid,status='adopted')
                log(state,'adopt',f'本地模拟确认采用 {pid}；未通知司机，未产生执行回执。')
            elif action=='withdraw':
                if state['status']!='adopted' or version['status']!='adopted': raise Conflict('只有已采用且尚未实际执行的演示方案可以撤回。')
                version['status']='withdrawn'; state.update(active_plan=None,status='withdrawn')
                log(state,'withdraw',f'撤回 {pid}。故障仍有效，目前无已采用恢复方案；P0 不可直接恢复执行。')
            else: raise ValueError('未知操作')
        return dict(session=state)

    def reset(self,expected):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=self._read(db)
            if expected.get('session_id')!=current['session_id'] or type(expected.get('revision')) is not int or expected['revision']!=current['revision']:
                db.rollback(); raise Conflict('场景已被其他操作更新，请刷新。')
            try:
                fresh=new_session()
                log(current,'archive','新建独立模拟场景；当前记录已保留。')
                current['revision']+=1; self._save(db,current); self._save(db,fresh); db.commit()
                fresh['archived_count']=current['archived_count']+1
                return dict(session=fresh)
            except Exception:
                db.rollback(); raise

    def archive(self):
        with self.connect() as db:
            return [dict(session_id=s['session_id'],created_at=s['created_at'],status=s['status'],revision=s['revision'])
                    for (doc,) in db.execute('SELECT document FROM sessions') for s in [json.loads(doc)]]
