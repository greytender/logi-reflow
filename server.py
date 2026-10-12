from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import argparse
import json
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
from urllib.parse import urlsplit, parse_qs
from store import Store, Conflict

ROOT=Path(__file__).resolve().parent
store=None
dispatch=None
workspace=scenario_workspace.Workspace()
key_vault=session_keys.KeyVault()
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw): super().__init__(*a,directory=str(ROOT/'web'),**kw)
    def end_headers(self):
        self.send_header('Cache-Control','no-store')
        super().end_headers()
    def reply(self,result,status=200):
        raw=json.dumps(result,ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        if self.path in ('/api/ai/key/status','/api/ai/status'):
            try:
                self.check_local()
                with key_vault.using(self.headers.get('Cookie')):
                    self.reply(self.key_status() if self.path=='/api/ai/key/status' else ai_events.status())
            except ValueError as e:self.reply(dict(error=str(e)),403)
            return
        url=urlsplit(self.path)
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

    def key_status(self):
        # Provider credentials are never included, even masked or hashed.
        import os
        return dict(session_configured=bool(key_vault.get(self.headers.get('Cookie'))),
                    local_configured=bool(os.environ.get('LOGISTICS_DEEPSEEK_API_KEY')),
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
    args=parser.parse_args()
    store=Store(args.database);dispatch=continuous.DispatchStore(args.database)
    print(f'Local demo: http://127.0.0.1:{args.port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
