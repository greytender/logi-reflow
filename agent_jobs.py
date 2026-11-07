"""Durable, token-scoped cooperative AI jobs for the local task workbench."""
from copy import deepcopy
from hashlib import sha256
import hmac
import json
from secrets import token_urlsafe
from threading import Event, RLock, Thread
from time import monotonic
from uuid import uuid4

import application as app
import multiwarehouse as mw
import agent_actions
from platform_agent import AgentWorkflow
from agent_control import WorkflowCancelled
from local_dispatch_agent import LocalDispatchPlanner
from store import Conflict

ACTIVE = {'queued','running','cancelling','committing'}


class JobRegistry:
    def __init__(self, store):
        self.store=store; self.lock=RLock(); self.stops={}; self.threads={}
        self.one_time_replies={}
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS platform_agent_jobs (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            rows=list(c.execute('SELECT id, document FROM platform_agent_jobs'))
        # Recovery never reruns a paid/model/tool request. Reconcile a commit whose final job write was interrupted.
        for job_id,raw in rows:
            job=json.loads(raw)
            if job['status'] not in ACTIVE:
                previous=job.pop('result',None)
                if previous:
                    job['run_id']=previous['agent_run']['id']
                if previous or any(event['type']=='answer_delta' for event in job['events']):
                    self._discard_drafts(job)
                    self._write(job)
                continue
            try:
                task=store.read(job['task_id'])
                run=next((r for r in task.get('agent_runs',[]) if r.get('operation_id')==job_id),None)
            except ValueError: task=run=None
            if run:
                job.pop('result', None)
                job.update(status='completed',run_id=run['id'])
            else:
                job.update(status='interrupted',error='服务在本轮完成前退出；未自动重试。请刷新任务后重新发起。')
            self._discard_drafts(job)
            self._append(job,job['status'],'恢复此前作业状态');self._write(job)

    def _read(self,job_id):
        with self.store.connect() as c:
            row=c.execute('SELECT document FROM platform_agent_jobs WHERE id=?',(job_id,)).fetchone()
        if not row: raise ValueError('助手作业不存在，请重新发起')
        return json.loads(row[0])

    def _write(self,job):
        job['updated_at']=app.now()
        with self.store.connect() as c:
            c.execute('INSERT INTO platform_agent_jobs VALUES (?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document',
                (job['id'],app.encode(job)))

    @staticmethod
    def _append(job,kind,title,details=None):
        job['cursor']+=1
        event=dict(seq=job['cursor'],type=kind,title=title,created_at=app.now())
        if details is not None: event['details']=details
        job['events'].append(event)
        # A bounded stream retains every terminal state and the newest content. Full final answer is in agent_run.
        if len(job['events'])>2500: job['events']=job['events'][-2500:]

    @staticmethod
    def _discard_drafts(job):
        job['events']=[event for event in job['events'] if event['type']!='answer_delta']

    def _publish(self,job_id,kind,**payload):
        with self.lock:
            job=self._read(job_id)
            if job['status'] not in ACTIVE: return
            self._append(job,kind,payload.get('title',''),payload.get('details'))
            self._write(job)

    def _check(self,job_id):
        if self.stops[job_id].is_set(): raise WorkflowCancelled('本轮已停止，尚未提交的候选和草稿未保存。已发生的履约记录保留。')

    def _begin_commit(self,job_id):
        with self.lock:
            self._check(job_id)
            job=self._read(job_id); job['status']='committing'
            self._append(job,'committing','正在保存核验结果');self._write(job)

    def start(self,task_id,revision,message,*,planner,selection=None,request_id=None):
        if planner is None: planner=LocalDispatchPlanner()
        if not isinstance(message,str) or not 1<=len(message.strip())<=2000: raise ValueError('请求需要1—2000字')
        if request_id is not None and (not isinstance(request_id,str) or not 1<=len(request_id)<=100):
            raise ValueError('请求编号格式无效')
        with self.lock:
            task=self.store.read(task_id);self.store.guard(task,revision)
            agent_actions.selected_context(task,selection)
            with self.store.connect() as c:
                jobs=[json.loads(r[0]) for r in c.execute('SELECT document FROM platform_agent_jobs')]
            active=next((j for j in jobs if j['task_id']==task_id and j['status'] in ACTIVE),None)
            if active: raise Conflict('该批次已有助手作业在进行，请等待或停止当前作业后再发起。')
            if request_id and any(j.get('request_id')==request_id and j['task_id']==task_id for j in jobs):
                raise Conflict('这条请求已经提交过，请恢复原作业；不会重复调用模型。')
            if sum(j['status'] in ACTIVE for j in jobs)>=8: raise ValueError('当前助手作业较多，请稍后重试')
            token=token_urlsafe(32); job_id=uuid4().hex
            job=dict(id=job_id,task_id=task_id,input_revision=revision,status='queued',created_at=app.now(),
                updated_at=app.now(),cursor=0,events=[],request_id=request_id,
                mode='local' if getattr(planner,'is_local',False) else 'model',
                token_hash=sha256(token.encode()).hexdigest())
            self._append(job,'queued','请求已接收');self._write(job);self.stops[job_id]=Event()
            thread=Thread(target=self._run,args=(job_id,task_id,revision,message,selection,planner),daemon=True)
            self.threads[job_id]=thread;thread.start()
            return dict(job=self._public(job,0),job_token=token)

    def _run(self,job_id,task_id,revision,message,selection,planner):
        try:
            with self.lock:
                job=self._read(job_id);job['status']='running';self._write(job)
            self._check(job_id)
            planner.stream_answer=True
            planner.check_cancel=lambda:self._check(job_id)
            planner.on_delta=lambda text:self._publish(job_id,'answer_delta',title='模型解释草稿 · 待事实核对',
                details=dict(text=text,draft=True,grounding_status='pending'))
            runtime=AgentWorkflow(self.store,planner,selection=selection,operation_id=job_id,
                emit=lambda kind,**kw:self._publish(job_id,kind,**kw),check_cancel=lambda:self._check(job_id),
                before_commit=lambda:self._begin_commit(job_id))
            task,run=runtime.run(task_id,revision,'request',message)
            with self.lock:
                job=self._read(job_id);job.update(status='completed')
                self._discard_drafts(job)
                if run.get('ephemeral'):
                    now=monotonic()
                    self.one_time_replies={k:v for k,v in self.one_time_replies.items() if v[0]>now}
                    self.one_time_replies[job_id]=(now+300,run['message'])
                    job['ephemeral']=True
                    title='已说明当前能力范围'
                else:
                    job['run_id']=run['id']
                    title='本轮完成，结果已保存'
                self._append(job,'completed',title);self._write(job)
        except WorkflowCancelled as error:
            self._finish_error(job_id,'cancelled',str(error))
        except (ValueError,Conflict) as error:
            # Only controlled domain errors enter the public job. Provider response bodies/credentials never do.
            text=str(error).replace(getattr(planner,'key','') or '\x00','[密钥已隐藏]')
            self._finish_error(job_id,'failed',text)
        except Exception:
            self._finish_error(job_id,'failed','助手运行发生异常，未报告成功；请刷新任务，核对已保存结果后重试。')
        finally:
            with self.lock:
                self.stops.pop(job_id,None);self.threads.pop(job_id,None)

    def _finish_error(self,job_id,status,error):
        with self.lock:
            job=self._read(job_id);job.update(status=status,error=error)
            self._discard_drafts(job)
            self._append(job,status,'本轮已停止' if status=='cancelled' else '本轮未完成',dict(error=error))
            self._write(job)

    def _public(self,job,after):
        result={k:deepcopy(v) for k,v in job.items() if k not in ('token_hash','events','result')}
        result['events']=[deepcopy(e) for e in job['events'] if e['seq']>after]
        run_id=job.get('run_id') or (job.get('result') or {}).get('agent_run',{}).get('id')
        if job['status']=='completed' and run_id:
            task=self.store.read(job['task_id'])
            run=next((r for r in task.get('agent_runs',[]) if r['id']==run_id),None)
            if run and not run.get('chat_deleted'):
                result['result']=dict(task=task,state=mw.view(task),agent_run=run)
            else:
                result['events']=[event for event in result['events'] if event['type']!='answer_delta']
        return result

    @staticmethod
    def _authorize(job,token):
        if not isinstance(token,str) or not hmac.compare_digest(job['token_hash'],sha256(token.encode()).hexdigest()):
            raise ValueError('无法访问此助手作业，请从发起它的浏览器恢复')

    def get(self,job_id,token,after=0):
        if not isinstance(after,int) or isinstance(after,bool) or after<0: raise ValueError('无效的事件游标')
        with self.lock:
            job=self._read(job_id);self._authorize(job,token)
            result=self._public(job,after)
            reply=self.one_time_replies.pop(job_id,None)
            if reply and reply[0]>monotonic():
                result['ephemeral_message']=reply[1]
            return dict(job=result)

    def cancel(self,job_id,token):
        with self.lock:
            job=self._read(job_id);self._authorize(job,token)
            if job['status']=='committing':
                return dict(job=self._public(job,0),cancel_accepted=False,
                    message='核验结果正在保存，无法撤销本次保存；不会自动确认或派单。')
            if job['status'] in ACTIVE:
                stop=self.stops.get(job_id)
                if stop: stop.set()
                job['status']='cancelling';self._append(job,'cancelling','停止已请求，等待当前调用返回');self._write(job)
            return dict(job=self._public(job,0),cancel_accepted=job['status']=='cancelling')
