"""Final presentation shell backed by the retained central-warehouse engine."""
from pathlib import Path
import sys, json, argparse, sqlite3
from contextlib import closing
from urllib.parse import urlsplit, unquote
from threading import Timer
from webbrowser import open as open_browser

ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT/'web'
sys.path.insert(0, str(ROOT/'runtime'))
import server as engine_server
import multiwarehouse, application, intake, continuous
from store import Store
from assistant_gateway import ModelGateway, ProviderError, COOKIE

GATEWAY=ModelGateway()

PROVIDERS = [
    dict(id='deepseek', name='DeepSeek', model='deepseek-chat', base_url='https://api.deepseek.com/v1'),
    dict(id='qwen', name='Qwen', model='qwen-plus', base_url='https://dashscope.aliyuncs.com/compatible-mode/v1'),
    dict(id='glm', name='GLM', model='glm-4-plus', base_url='https://open.bigmodel.cn/api/paas/v4'),
    dict(id='kimi', name='Kimi', model='moonshot-v1-8k', base_url='https://api.moonshot.cn/v1'),
]

class FinalHandler(engine_server.Handler):
    def translate_path(self, path):
        clean = unquote(urlsplit(path).path).lstrip('/')
        if clean in ('', 'home.html'): clean = 'index.html'
        candidate = (WEB_ROOT/clean).resolve()
        allowed = {'.html','.css','.js','.svg','.png','.jpg','.webp','.woff','.woff2','.ttf','.ico'}
        if (WEB_ROOT not in candidate.parents or candidate.suffix.lower() not in allowed
                or clean.split('/')[0] in ('runtime', '.revisions')):
            return str(ROOT/'__unavailable__')
        return str(candidate)

    def do_GET(self):
        if urlsplit(self.path).path == '/api/assistant/providers':
            try:
                self.check_local()
                items=[]
                for preset in PROVIDERS:
                    config=GATEWAY.config(self.headers.get('Cookie'),preset['id'])
                    items.append({**preset,**({k:config[k] for k in ('base_url','model')} if config else {}),'configured':bool(config)})
                self.reply(dict(providers=items))
            except ValueError as e: self.reply(dict(error=str(e)),403)
            return
        super().do_GET()

    def do_POST(self):
        if urlsplit(self.path).path not in ('/api/assistant/chat','/api/assistant/config'):
            return super().do_POST()
        try:
            self.check_local(require_origin=True)
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=24000: raise ValueError('消息过长或为空')
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                raise ValueError('需要 JSON 请求')
            body=json.loads(self.rfile.read(size))
            if not isinstance(body,dict): raise ValueError('请求必须是对象')
            if urlsplit(self.path).path=='/api/assistant/config':
                token=GATEWAY.save(self.headers.get('Cookie'),body)
                raw=json.dumps(dict(saved=True,provider=body['provider'])).encode()
                self.send_response(200)
                self.send_header('Set-Cookie',f'{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/api/assistant; Max-Age=86400')
                self.send_header('Content-Type','application/json; charset=utf-8')
                self.send_header('Content-Length',str(len(raw)))
                self.end_headers();self.wfile.write(raw)
                return
            provider=next((p for p in PROVIDERS if p['id']==body.get('provider')),None)
            if provider is None: raise ValueError('请选择模型提供方')
            message=str(body.get('message','')).strip()
            if not message or len(message)>3000: raise ValueError('请输入3000字以内的问题')
            task=engine_server.multiwarehouse_store.read(body['id'])
            engine_server.multiwarehouse_store.guard(task,body['revision'])
            plan=multiwarehouse.current_plan(task)
            if plan is None: raise ValueError('当前输入还没有候选方案，请先计算')
            group=next(iter(plan['warehouses'].values()))
            routes=group['optimized']['routes']
            vid=body.get('vehicle_id') or next(iter(routes))
            if vid not in routes: raise ValueError('车辆不属于当前批次')
            route=routes[vid]; m=plan['summary']['optimized']; fee=route.get('fee',{})
            adjusted=route.get('operational',{}).get('adjusted_route',route)
            context=dict(batch=dict(source_rows=len(task.get('intake_source',{}).get('rows',[])),
                stores=len(task['data']['orders']),summary=m),vehicle_id=vid,
                route=dict(stops=len(route['sequence']),distance_km=route['distance'],cost=route['cost'],
                    fee=fee,weight_kg=route['weight_kg'],volume_m3=route['volume_m3'],
                    return_at=adjusted['return_at'],operational_passed=route.get('operational',{}).get('passed'),
                    schedule=[dict(stop_number=i+1,arrival=x['arrival'],finish=x['finish'],pickup=x.get('pickup'))
                              for i,x in enumerate(adjusted['stops'])]))
            result=GATEWAY.complete(self.headers.get('Cookie'),provider['id'],message,context)
            self.reply(dict(mode='model',connected=True,provider=provider['id'],**result,
                tools=[dict(name='batch.context',status='completed',task_revision=task['revision'],plan_id=plan['id'])],
                selection=dict(vehicle_id=vid),task_revision=task['revision']))
        except ProviderError as e:
            self.reply(dict(error=str(e)),502)
        except engine_server.Conflict as e:
            self.reply(dict(error=str(e)),409)
        except (ValueError,KeyError,TypeError,json.JSONDecodeError) as e:
            self.reply(dict(error=str(e)),400)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--data-dir',type=Path,default=ROOT/'runtime/data')
    args=parser.parse_args()
    data=args.data_dir.resolve()
    data.mkdir(parents=True, exist_ok=True)
    for name in ('application.sqlite3', 'intake.sqlite3'):
        seed = ROOT/'demo'/name
        destination = data/name
        if not destination.exists() and seed.exists():
            temporary = destination.with_suffix('.initializing')
            try:
                with closing(sqlite3.connect(f'file:{seed.as_posix()}?mode=ro', uri=True)) as source, closing(sqlite3.connect(temporary)) as target:
                    source.backup(target)
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
    engine_server.store=Store(data/'sessions.sqlite3')
    engine_server.dispatch=continuous.DispatchStore(data/'sessions.sqlite3')
    engine_server.application_store=application.ApplicationStore(data/'application.sqlite3')
    engine_server.multiwarehouse_store=multiwarehouse.MultiWarehouseStore(data/'application.sqlite3')
    engine_server.intake_store=intake.IntakeStore(data/'intake.sqlite3')
    with engine_server.LocalHTTPServer(('127.0.0.1',args.port),FinalHandler) as service:
        url=f'http://127.0.0.1:{service.server_port}/'
        print(url,flush=True)
        if not args.no_browser:
            print('Keep this window open during the demonstration. Press Ctrl+C to stop.', flush=True)
        if not args.no_browser: Timer(.25,open_browser,args=(url,)).start()
        try: service.serve_forever()
        except KeyboardInterrupt: pass

if __name__=='__main__': main()
