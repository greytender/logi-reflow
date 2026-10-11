from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import json
import engine
import normal
import dynamic
import urgent
import timewindow
import ai_events
import continuous
from store import Store, Conflict

ROOT=Path(__file__).resolve().parent
store=Store(ROOT/'data'/'sessions.sqlite3')
dispatch=continuous.DispatchStore(ROOT/'data'/'sessions.sqlite3')
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw): super().__init__(*a,directory=str(ROOT/'web'),**kw)
    def end_headers(self):
        self.send_header('Cache-Control','no-store')
        super().end_headers()
    def reply(self,result,status=200):
        raw=json.dumps(result,ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        if self.path=='/api/dispatch/comparison':
            try: self.reply(dispatch.compare())
            except ValueError as e: self.reply(dict(error=str(e)),409)
        elif self.path=='/api/dispatch': self.reply(dict(scene=dispatch.read()))
        elif self.path=='/api/normal': self.reply(normal.solve(normal.defaults()))
        elif self.path=='/api/ai/status': self.reply(ai_events.status())
        elif self.path=='/api/session': self.reply(dict(session=store.read()))
        elif self.path=='/api/sessions': self.reply(dict(sessions=store.archive()))
        else: super().do_GET()
    def do_POST(self):
        if self.path not in ('/api/dispatch/start','/api/dispatch/event','/api/dispatch/adopt','/api/dispatch/advance','/api/normal','/api/failure','/api/urgent','/api/window','/api/interpret','/api/run','/api/adopt','/api/withdraw','/api/reset'): self.send_error(404); return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<=length<=16384: raise ValueError('消息长度无效')
            body=json.loads(self.rfile.read(length),parse_constant=lambda x: (_ for _ in ()).throw(ValueError('不允许非有限数字')))
            if not isinstance(body,dict): raise ValueError('请求必须是 JSON 对象')
            if self.path.startswith('/api/dispatch/'): result=dict(scene=dispatch.act(self.path.rsplit('/',1)[1],body))
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
        except ai_events.AIError as e: result={'error':str(e),'ready':False}; status=503
        except (ValueError,TypeError,KeyError) as e: result={'error':str(e)}; status=400
        self.reply(result,status)

if __name__=='__main__':
    print('Local demo: http://127.0.0.1:8766',flush=True)
    ThreadingHTTPServer(('127.0.0.1',8766),Handler).serve_forever()
