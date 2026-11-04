from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import argparse
import csv
import json
import re
import base64
import binascii
import engine
import normal
import dynamic
import urgent
import timewindow
import ai_events
import csv_ai_events
import continuous
import matrix_dispatch
import dispatch_handoff
import loading
import scenario_demo
import scenario_workspace
import session_keys
import evidence_cases
import application
import multiwarehouse
import platform_agent
import deepseek_agent
import agent_jobs
import agent_actions
from threading import RLock
from urllib.parse import urlsplit, parse_qs
from store import Store, Conflict

ROOT=Path(__file__).resolve().parent
store=None
dispatch=None
application_store=None
multiwarehouse_store=None
intake_store=None
workspace=scenario_workspace.Workspace()
key_vault=session_keys.KeyVault()
PRODUCT_BUILD='20261104-rounded-fit-v1'
class LocalHTTPServer(ThreadingHTTPServer):
    # On Windows, SO_REUSEADDR can let an old and new server share one port.
    allow_reuse_address=False
_agent_registry_lock=RLock()

def agent_registry():
    if multiwarehouse_store is None:
        raise ValueError('配送任务存储尚未初始化')
    with _agent_registry_lock:
        if not hasattr(multiwarehouse_store, '_agent_registry'):
            multiwarehouse_store._agent_registry=agent_jobs.JobRegistry(multiwarehouse_store)
        return multiwarehouse_store._agent_registry

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw): super().__init__(*a,directory=str(ROOT/'web'),**kw)
    def end_headers(self):
        self.send_header('Cache-Control','no-store')
        super().end_headers()
    def reply(self,result,status=200):
        raw=json.dumps(result,ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def send_head(self):
        # Browser seeking needs byte ranges; keep ordinary static/API behavior.
        self._video_remaining=None
        if urlsplit(self.path).path!='/media/logireflow-walkthrough.mp4':
            return super().send_head()
        path=Path(self.translate_path(self.path))
        if not path.is_file():return super().send_head()
        source=path.open('rb');stat=path.stat();size=stat.st_size
        start,end=0,size-1;requested=self.headers.get('Range')
        if requested:
            match=re.fullmatch(r'bytes=(\d*)-(\d*)',requested.strip())
            try:
                if not match or not any(match.groups()):raise ValueError()
                first,last=match.groups()
                if first:
                    start=int(first);end=min(int(last),size-1) if last else size-1
                else:
                    suffix=int(last)
                    if suffix<=0:raise ValueError()
                    start=max(0,size-suffix)
                if start>=size or end<start:raise ValueError()
            except ValueError:
                source.close();self.send_response(416)
                self.send_header('Content-Range',f'bytes */{size}')
                self.send_header('Content-Length','0');self.end_headers();return None
        self.send_response(206 if requested else 200)
        self.send_header('Content-Type','video/mp4')
        self.send_header('Accept-Ranges','bytes')
        self.send_header('Content-Length',str(end-start+1))
        self.send_header('Last-Modified',self.date_time_string(stat.st_mtime))
        if requested:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        self.end_headers();source.seek(start);self._video_remaining=end-start+1
        return source
    def copyfile(self,source,outputfile):
        remaining=getattr(self,'_video_remaining',None)
        if remaining is None:return super().copyfile(source,outputfile)
        while remaining:
            chunk=source.read(min(65536,remaining))
            if not chunk:break
            outputfile.write(chunk);remaining-=len(chunk)
    def do_GET(self):
        # Apply the local-origin boundary before both current and legacy routes.
        # Otherwise DNS rebinding can read old saved sessions via unguarded GETs.
        try:
            self.check_local()
        except ValueError as error:
            self.reply(dict(error=str(error)), 403)
            return
        if self.path == '/api/product/status':
            try:
                self.check_local()
                self.reply(dict(product='LogiReflow', build=PRODUCT_BUILD, home='/home.html', workspace='/workspace.html'))
            except ValueError as error:
                self.reply(dict(error=str(error)), 403)
            return
        if self.path in ('/api/ai/key/status','/api/ai/status'):
            try:
                self.check_local()
                with key_vault.using(self.headers.get('Cookie')):
                    self.reply(self.key_status() if self.path=='/api/ai/key/status' else ai_events.status())
            except ValueError as e:self.reply(dict(error=str(e)),403)
            return
        url=urlsplit(self.path)
        if url.path.startswith('/api/multiwarehouse/'):
            self.multiwarehouse_get(url)
            return
        if url.path.startswith('/api/intake/'):
            self.intake_get(url)
            return
        if url.path.startswith('/api/application/'):
            self.application_get(url)
            return
        if url.path=='/':
            self.path='/home.html'
            super().do_GET()
            return
        if url.path=='/api/dispatch/handoff':
            try:
                query=parse_qs(url.query);scene=dispatch.read()
                if not scene: raise Conflict('尚无保存场景')
                revision=query.get('revision',[''])[0]
                if not revision.isdecimal(): raise ValueError('交接需要场景编号和修订号，请刷新')
                dispatch.guard(scene,dict(id=query.get('id',[''])[0],revision=int(revision)))
                result=dispatch_handoff.report(scene)
                if query.get('format',['json'])[0]=='csv':
                    raw=dispatch_handoff.csv_text(result).encode('utf-8')
                    self.send_response(200);self.send_header('Content-Type','text/csv; charset=utf-8')
                    self.send_header('Content-Disposition',f'attachment; filename="handoff-P{scene["active"]}-r{scene["revision"]}.csv"')
                    self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
                else: self.reply(result)
            except Conflict as e: self.reply(dict(error=str(e)),409)
            except (ValueError,KeyError,TypeError) as e: self.reply(dict(error=str(e)),400)
        elif self.path=='/api/dispatch/history': self.reply(dispatch.archive())
        elif self.path.startswith('/api/dispatch/history/'):
            scene=dispatch.read(self.path.rsplit('/',1)[1])
            self.reply(dict(scene=scene,read_only=True),200 if scene else 404)
        elif self.path=='/api/dispatch/review': self.reply(continuous.review_version(dispatch.read()))
        elif self.path=='/api/dispatch/comparison':
            try: self.reply(dispatch.compare())
            except ValueError as e: self.reply(dict(error=str(e)),409)
        elif self.path=='/api/dispatch': self.reply(dict(scene=dispatch.read()))
        elif self.path=='/api/scenario/sample': self.reply(scenario_demo.sample())
        elif self.path=='/api/loading/sample': self.reply(loading.sample())
        elif self.path=='/api/dispatch/sample-files':
            self.reply({name:(ROOT/'examples'/'continuous-small'/name).read_text(encoding='utf-8') for name in matrix_dispatch.FILES})
        elif self.path=='/api/normal': self.reply(normal.solve(normal.defaults()))
        elif self.path=='/api/ai/status': self.reply(ai_events.status())
        elif self.path=='/api/session': self.reply(dict(session=store.read()))
        elif self.path=='/api/sessions': self.reply(dict(sessions=store.archive()))
        else: super().do_GET()
    def do_POST(self):
        if self.path in ('/api/ai/key/set','/api/ai/key/clear'):
            self.key_post()
            return
        try:
            self.check_local()
        except ValueError as e:
            self.reply(dict(error=str(e)),403);return
        with key_vault.using(self.headers.get('Cookie')):
            self.dispatch_post()

    def dispatch_post(self):
        if self.path.startswith('/api/multiwarehouse/'):
            self.multiwarehouse_post()
            return
        if self.path.startswith('/api/intake/'):
            self.intake_post()
            return
        if self.path.startswith('/api/application/'):
            self.application_post()
            return
        if self.path=='/api/evidence/run':
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<=length<=2048:raise ValueError('案例请求长度无效')
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict) or set(body)!={'case'}:raise ValueError('请选择一个案例')
                self.reply(evidence_cases.run(body['case']))
            except (ValueError,TypeError,KeyError) as e:self.reply(dict(error=str(e)),400)
            return
        if self.path.startswith('/api/scenario/live/'):
            self.live_post()
            return
        if self.path not in ('/api/scenario/run','/api/loading/solve','/api/dispatch/interpret','/api/dispatch/start','/api/dispatch/event','/api/dispatch/adopt','/api/dispatch/discard','/api/dispatch/advance','/api/dispatch/receipt','/api/normal','/api/failure','/api/urgent','/api/window','/api/interpret','/api/run','/api/adopt','/api/withdraw','/api/reset'): self.send_error(404); return
        try:
            length=int(self.headers.get('Content-Length','0'))
            limit=4_000_000 if self.path in ('/api/dispatch/start','/api/loading/solve','/api/scenario/run') else 16384
            if not 0<=length<=limit: raise ValueError('消息长度无效')
            body=json.loads(self.rfile.read(length),parse_constant=lambda x: (_ for _ in ()).throw(ValueError('不允许非有限数字')))
            if not isinstance(body,dict): raise ValueError('请求必须是 JSON 对象')
            if self.path=='/api/scenario/run':
                result=scenario_demo.run(body)
            elif self.path=='/api/loading/solve':
                if set(body)!={'csv_files','loading_profile'}:raise ValueError('需要四份CSV文件与loading_profile，不读取当前场景')
                result=loading.solve(body['csv_files'],body['loading_profile'])
            elif self.path=='/api/dispatch/interpret':
                scene=dispatch.read()
                if not scene: raise Conflict('尚未开始CSV连续场景')
                dispatch.guard(scene,body)
                result=csv_ai_events.interpret(scene,body.get('message'),body.get('context'))
                latest=dispatch.read()
                if not latest or latest['id']!=scene['id'] or latest['revision']!=scene['revision']:
                    raise Conflict('理解期间保存状态已变化；请刷新后重新理解')
            elif self.path.startswith('/api/dispatch/'): result=dict(scene=dispatch.act(self.path.rsplit('/',1)[1],body))
            elif self.path=='/api/normal': result=normal.solve(body.get('data'))
            elif self.path=='/api/failure': result=dynamic.run(body.get('data'),body.get('message',''))
            elif self.path=='/api/urgent': result=urgent.run(body.get('data'),body.get('event'))
            elif self.path=='/api/window': result=timewindow.run(body.get('data'),body.get('event'))
            elif self.path=='/api/interpret': result=ai_events.interpret(body.get('data'),body.get('message'),body.get('context'))
            elif self.path=='/api/run':
                text=body.get('message','')
                if not isinstance(text,str): raise ValueError('message 必须是字符串')
                result=store.calculate(text,body)
            elif self.path=='/api/reset': result=store.reset(body)
            else: result=store.decide(self.path.rsplit('/',1)[1],body)
            status=200
        except Conflict as e: result={'error':str(e)}; status=409
        except ai_events.AIError as e: result={'error':str(e),'ready':False,**getattr(e,'metadata',{})}; status=503
        except (ValueError,TypeError,KeyError) as e: result={'error':str(e)}; status=400
        self.reply(result,status)

    def check_local(self, require_origin=False):
        host=self.headers.get('Host','')
        port=self.server.server_port
        if host not in (f'127.0.0.1:{port}',f'localhost:{port}'):
            raise ValueError('仅接受本机地址访问')
        origin=self.headers.get('Origin')
        if (require_origin and not origin) or (origin is not None and origin!='http://'+host):
            raise ValueError('仅接受本机页面的同源请求')
        if self.headers.get('Sec-Fetch-Site') in ('cross-site','same-site'):
            raise ValueError('拒绝跨来源请求')

    def multiwarehouse_get(self, url):
        try:
            self.check_local()
            if multiwarehouse_store is None: raise ValueError('多仓存储尚未初始化')
            query = parse_qs(url.query)
            operation = url.path.rsplit('/', 1)[-1]
            if url.path == '/api/multiwarehouse/agent/job':
                cursor=query.get('after', ['0'])[0]
                if not cursor.isdecimal(): raise ValueError('事件游标无效')
                self.reply(agent_registry().get(query.get('job_id', [''])[0], self.headers.get('X-Agent-Job-Token'), after=int(cursor)))
            elif operation == 'tasks': self.reply(dict(tasks=multiwarehouse_store.list()))
            elif operation == 'agent-status':
                with key_vault.using(self.headers.get('Cookie')):
                    self.reply(platform_agent.status(deepseek_agent.status()))
            elif operation == 'sample': self.reply(dict(data=multiwarehouse.sample(physical_loading=query.get('physical', ['0'])[0] == '1')))
            elif operation == 'task':
                task = multiwarehouse_store.read(query.get('id', [''])[0])
                self.reply(dict(task=task, state=multiwarehouse.view(task)))
            elif operation == 'export':
                task_id = query.get('id', [''])[0]; rev = query.get('revision', [''])[0]
                if not rev.isdecimal(): raise ValueError('导出需要当前修订号，请刷新')
                if query.get('format', ['json'])[0] == 'csv':
                    raw = multiwarehouse_store.export_csv(task_id, int(rev)).encode('utf-8')
                    mime, suffix = 'text/csv; charset=utf-8', 'csv'
                else:
                    raw = json.dumps(multiwarehouse_store.export_package(task_id, int(rev)), ensure_ascii=False, indent=2).encode('utf-8')
                    mime, suffix = 'application/json; charset=utf-8', 'json'
                self.send_response(200); self.send_header('Content-Type', mime)
                self.send_header('Content-Disposition', 'attachment; filename="multiwarehouse.'+suffix+'"')
                self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
            else: self.send_error(404)
        except Conflict as e: self.reply(dict(error=str(e)), 409)
        except (ValueError, TypeError, KeyError) as e: self.reply(dict(error=str(e)), 400)

    def multiwarehouse_post(self):
        try:
            if multiwarehouse_store is None: raise ValueError('多仓存储尚未初始化')
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json': raise ValueError('需要JSON请求')
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 3_000_000: raise ValueError('多仓请求需要1字节—3MB')
            body = json.loads(self.rfile.read(length), parse_constant=lambda x: (_ for _ in ()).throw(ValueError('不允许非有限数字')))
            if not isinstance(body, dict): raise ValueError('请求必须是对象')
            operation = self.path.rsplit('/', 1)[-1]
            if self.path == '/api/multiwarehouse/agent/start':
                self.reply(agent_registry().start(body['id'], body['revision'], body.get('message'),
                    planner=deepseek_agent.planner(), selection=body.get('selection'), request_id=body.get('request_id')))
                return
            if self.path == '/api/multiwarehouse/agent/cancel':
                self.reply(agent_registry().cancel(body.get('job_id'), body.get('job_token')))
                return
            if self.path == '/api/multiwarehouse/agent/chat-record':
                task=agent_actions.organize_chat(multiwarehouse_store, body['id'], body['revision'],
                    body.get('run_id'), body.get('action'))
                self.reply(dict(task=task, state=multiwarehouse.view(task)))
                return
            if self.path == '/api/multiwarehouse/agent/apply-rules':
                task=agent_actions.apply_rule_draft(multiwarehouse_store, body['id'], body['revision'],
                    body.get('run_id'), body.get('reviewed'))
                self.reply(dict(task=task, state=multiwarehouse.view(task)))
                return
            if operation == 'validate':
                self.reply(dict(data=multiwarehouse.canonicalize(body.get('data'))))
                return
            if operation == 'agent':
                planner = deepseek_agent.planner() if body.get('workflow') == 'request' else None
                task, result = platform_agent.AgentWorkflow(multiwarehouse_store, planner).run(body['id'], body['revision'], body.get('workflow'), body.get('message'))
                self.reply(dict(task=task, state=multiwarehouse.view(task), agent_run=result))
                return
            if operation == 'create': task = multiwarehouse_store.create(body.get('name'), body.get('data'))
            elif operation == 'save': task = multiwarehouse_store.save(body['id'], body['revision'], body.get('name'), body.get('data'))
            elif operation == 'solve': task = multiwarehouse_store.solve(body['id'], body['revision'])
            elif operation == 'manual': task = multiwarehouse_store.manual(body['id'], body['revision'], body.get('sequences'))
            elif operation == 'adopt': task = multiwarehouse_store.adopt(body['id'], body['revision'], body.get('plan_id'), body.get('reviewed'))
            elif operation in ('receipt', 'observe', 'replan', 'adopt-remaining'):
                payload = body.get('receipt') if operation == 'receipt' else body.get('observation') if operation == 'observe' else body.get('candidate_id')
                task = multiwarehouse_store.action(body['id'], body['revision'], body.get('warehouse_id'), operation, payload, body.get('reviewed'))
            else: self.send_error(404); return
            self.reply(dict(task=task, state=multiwarehouse.view(task)))
        except Conflict as e: self.reply(dict(error=str(e)), 409)
        except (ValueError, TypeError, KeyError, csv.Error) as e: self.reply(dict(error=str(e)), 400)

    def application_get(self, url):
        try:
            self.check_local()
            if application_store is None:
                raise ValueError('应用任务存储尚未初始化')
            if url.path=='/api/application/tasks':
                self.reply(dict(tasks=application_store.list()))
            elif url.path=='/api/application/sample':
                self.reply(dict(data=application.sample()))
            elif url.path in ('/api/application/task','/api/application/export'):
                query=parse_qs(url.query)
                task_id=query.get('id',[''])[0]
                if url.path.endswith('/task'):
                    task=application_store.read(task_id)
                    self.reply(dict(task=task, execution=application.execution.view(task) if task['adopted_plan_id'] and any(p['id']==task['adopted_plan_id'] and p['input_revision']==task['input_revision'] for p in task['plans']) else None))
                else:
                    revision=query.get('revision',[''])[0]
                    if not revision.isdecimal():raise ValueError('导出需要当前任务修订号，请刷新')
                    task=application_store.read(task_id)
                    application_store.guard(task,int(revision))
                    if query.get('format',['json'])[0]=='handoff':
                        import integration_contract
                        package=integration_contract.export_handoff(task)
                        integration_contract.validate_handoff(package)
                        raw=json.dumps(package,ensure_ascii=False,indent=2).encode('utf-8')
                        mime,extension='application/json; charset=utf-8','handoff.json'
                    elif query.get('format',['json'])[0]=='remaining':
                        raw=application_store.export_remaining(task_id,int(revision)).encode('utf-8')
                        mime,extension='text/csv; charset=utf-8','remaining.csv'
                    elif query.get('format',['json'])[0]=='receipts':
                        raw=application_store.export_receipts(task_id,int(revision)).encode('utf-8')
                        mime,extension='text/csv; charset=utf-8','receipts.csv'
                    elif query.get('format',['json'])[0]=='csv':
                        raw=application_store.export_csv(task_id,int(revision)).encode('utf-8')
                        mime,extension='text/csv; charset=utf-8','csv'
                    else:
                        raw=json.dumps(dict(task=task,export_kind='planning_task',
                            boundaries=['本机试算与人工/模拟回执，来源不等于履约证明','回收载荷按箱规估算；同车改序需人工采用；未实现冷链、3D、换车补车或正式合同结算']),ensure_ascii=False,indent=2).encode('utf-8')
                        mime,extension='application/json; charset=utf-8','json'
                    self.send_response(200)
                    self.send_header('Content-Type',mime)
                    self.send_header('Content-Disposition',f'attachment; filename="task-{task["id"]}.{extension}"')
                    self.send_header('Content-Length',str(len(raw)))
                    self.end_headers();self.wfile.write(raw)
            else:self.send_error(404)
        except Conflict as error:self.reply(dict(error=str(error)),409)
        except (ValueError,TypeError,KeyError) as error:self.reply(dict(error=str(error)),400)

    def application_post(self):
        try:
            if application_store is None:raise ValueError('应用任务存储尚未初始化')
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('需要JSON请求')
            length=int(self.headers.get('Content-Length','0'))
            if not 0<=length<=3_000_000:raise ValueError('任务请求不能超过3MB')
            body=json.loads(self.rfile.read(length),parse_constant=lambda x: (_ for _ in ()).throw(ValueError('不允许非有限数字')))
            if not isinstance(body,dict):raise ValueError('任务请求必须是对象')
            operation=self.path.rsplit('/',1)[-1]
            if operation=='create':
                data=application.load_files(body['csv_files']) if 'csv_files' in body else body.get('data')
                task=application_store.create(body.get('name'),data)
            elif operation=='save':task=application_store.save(body['id'],body['revision'],body.get('name'),body.get('data'))
            elif operation=='solve':task=application_store.solve(body['id'],body['revision'])
            elif operation=='adopt':task=application_store.adopt(body['id'],body['revision'],body.get('plan_id'),body.get('reviewed'))
            elif operation=='receipt':task=application_store.receipt(body['id'],body['revision'],body.get('receipt'),body.get('reviewed'))
            elif operation=='observe':task=application_store.observe(body['id'],body['revision'],body.get('observation'),body.get('reviewed'))
            elif operation=='replan':task=application_store.replan(body['id'],body['revision'])
            elif operation=='adopt-remaining':task=application_store.adopt_remaining(body['id'],body['revision'],body.get('candidate_id'),body.get('reviewed'))
            else:self.send_error(404);return
            self.reply(dict(task=task, execution=application.execution.view(task) if task['adopted_plan_id'] and any(p['id']==task['adopted_plan_id'] and p['input_revision']==task['input_revision'] for p in task['plans']) else None))
        except Conflict as error:self.reply(dict(error=str(error)),409)
        except (ValueError,TypeError,KeyError,csv.Error) as error:self.reply(dict(error=str(error)),400)

    def intake_get(self, url):
        try:
            self.check_local()
            if intake_store is None:raise ValueError('原始表格核对存储尚未初始化')
            if url.path=='/api/intake/list':
                self.reply(dict(documents=intake_store.list()));return
            query=parse_qs(url.query)
            if url.path in ('/api/intake/review','/api/intake/review-export'):
                import intake_review
                reviews=intake_review.ReviewStore(intake_store.path,intake_store)
                document_id=query.get('id',[''])[0]
                if url.path.endswith('/review'):
                    self.reply(dict(review=reviews.read(document_id)))
                else:
                    revision=query.get('revision',[''])[0]
                    if not revision.isdecimal():raise ValueError('映射草稿导出需要当前修订号')
                    review=reviews.export(document_id)
                    if review['revision']!=int(revision):raise Conflict('映射草稿已经更新，请刷新后导出当前版本')
                    raw=json.dumps(review,ensure_ascii=False,indent=2).encode('utf-8')
                    self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8')
                    self.send_header('Content-Disposition',f'attachment; filename="review-{document_id}-r{revision}.json"')
                    self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
                return
            document=intake_store.read(query.get('id',[''])[0])
            if url.path=='/api/intake/document':
                self.reply(dict(document=document))
            elif url.path=='/api/intake/export':
                raw=json.dumps(document,ensure_ascii=False,indent=2).encode('utf-8')
                self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8')
                self.send_header('Content-Disposition',f'attachment; filename="intake-{document["id"]}.json"')
                self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            else:self.send_error(404)
        except Conflict as error:self.reply(dict(error=str(error)),409)
        except (ValueError,TypeError,KeyError) as error:self.reply(dict(error=str(error)),400)

    def intake_post(self):
        try:
            if intake_store is None:raise ValueError('原始表格核对存储尚未初始化')
            if self.path not in ('/api/intake/create','/api/intake/review'):self.send_error(404);return
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('需要JSON请求')
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=3_000_000:raise ValueError('上传请求不能超过3MB')
            body=json.loads(self.rfile.read(length),parse_constant=lambda x: (_ for _ in ()).throw(ValueError('不允许非有限数字')))
            if not isinstance(body,dict):raise ValueError('上传格式无效')
            if self.path=='/api/intake/review':
                import intake_review
                reviews=intake_review.ReviewStore(intake_store.path,intake_store)
                self.reply(dict(review=reviews.save(body.get('id'),body.get('revision'),body.get('config'))));return
            if body.get('sample') is True:
                import intake
                raw=intake.sample_xlsx();filename='intake-simulated.xlsx'
                name=body.get('name','构造表格核对样例')
            else:
                encoded=body.get('content_base64')
                if not isinstance(encoded,str):raise ValueError('缺少XLSX文件内容')
                raw=base64.b64decode(encoded,validate=True);filename=body.get('filename')
                name=body.get('name')
            if len(raw)>2_000_000:raise ValueError('本轮原始XLSX文件不超过2MB，不会截取内容')
            self.reply(dict(document=intake_store.create(name,filename,raw)))
        except Conflict as error:self.reply(dict(error=str(error)),409)
        except (ValueError,TypeError,KeyError,binascii.Error) as error:self.reply(dict(error=str(error)),400)

    def key_status(self):
        # Provider credentials are never included, even masked or hashed.
        import os
        return dict(session_configured=bool(key_vault.get(self.headers.get('Cookie'))),
                    local_configured=bool(os.environ.get('LOGISTICS_DEEPSEEK_API_KEY') or os.environ.get('DEEPSEEK_API_KEY')),
                    lifetime_seconds=key_vault.ttl, model=ai_events.status()['model'],
                    validation='not_checked_by_this_endpoint')

    def key_post(self):
        try:
            self.check_local(require_origin=True)
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                raise ValueError('需要JSON请求')
            length=int(self.headers.get('Content-Length','0'))
            if not 0<=length<=2048:raise ValueError('配置请求长度无效')
            body=json.loads(self.rfile.read(length))
            if not isinstance(body,dict):raise ValueError('配置请求无效')
            if self.path.endswith('/set'):
                if set(body)!={'api_key'}:raise ValueError('仅接受api_key字段')
                token=key_vault.put(self.headers.get('Cookie'),body['api_key'])
                cookie=f'{session_keys.COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={key_vault.ttl}'
            else:
                if body:raise ValueError('清除请求不需要字段')
                key_vault.clear(self.headers.get('Cookie'))
                cookie=f'{session_keys.COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'
            self.send_response(200);self.send_header('Set-Cookie',cookie)
            self.send_header('Cache-Control','no-store');self.send_header('Content-Type','application/json')
            # Request cookies still refer to the old token on first creation.
            raw=json.dumps(dict(session_configured=self.path.endswith('/set'),
                local_configured=self.key_status()['local_configured'],lifetime_seconds=key_vault.ttl,
                model=ai_events.status()['model'],validation='not_checked_by_this_endpoint')).encode()
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        except ValueError as e:self.reply(dict(error=str(e)),403 if '本机' in str(e) or '跨来源' in str(e) else 400)

    def live_post(self):
        operation=self.path.rsplit('/',1)[1]
        if operation not in ('start','action','interpret','draft'):
            self.send_error(404);return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<=length<=(4_000_000 if operation=='start' else 16384):
                raise ValueError('消息长度无效')
            body=json.loads(self.rfile.read(length),parse_constant=lambda x: (_ for _ in ()).throw(ValueError('不允许非有限数字')))
            if not isinstance(body,dict):raise ValueError('请求必须是JSON对象')
            result=getattr(workspace,'act' if operation=='action' else operation)(body)
            status=200
        except Conflict as e:result={'error':str(e)};status=409
        except ai_events.AIError as e:result={'error':str(e),'ready':False,**getattr(e,'metadata',{})};status=503
        except (ValueError,TypeError,KeyError) as e:result={'error':str(e)};status=400
        self.reply(result,status)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--database',type=Path,default=ROOT/'data'/'sessions.sqlite3')
    parser.add_argument('--application-database',type=Path,default=ROOT/'data'/'application.sqlite3')
    parser.add_argument('--intake-database',type=Path,default=ROOT/'data'/'intake.sqlite3')
    args=parser.parse_args()
    store=Store(args.database);dispatch=continuous.DispatchStore(args.database)
    application_store=application.ApplicationStore(args.application_database)
    multiwarehouse_store=multiwarehouse.MultiWarehouseStore(args.application_database)
    import intake
    intake_store=intake.IntakeStore(args.intake_database)
    print(f'Local demo: http://127.0.0.1:{args.port}',flush=True)
    LocalHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
