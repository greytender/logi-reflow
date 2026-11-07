"""Provider protocol and HTTP configuration checks; no paid model calls."""
import sys,json,threading,io
from pathlib import Path
from unittest.mock import patch
from http.cookiejar import CookieJar
from urllib.request import build_opener,HTTPCookieProcessor,Request
from urllib.error import HTTPError
root=Path(r'D:\桌面文件\logi-reflow-final')
sys.path.insert(0,str(root))
import launch
import assistant_gateway as gateway
checks=[]
launch.engine_server.multiwarehouse_store=launch.multiwarehouse.MultiWarehouseStore(root/'runtime/data/application.sqlite3')
server=launch.engine_server.LocalHTTPServer(('127.0.0.1',0),launch.FinalHandler)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
base=f'http://127.0.0.1:{server.server_port}'
client=build_opener(HTTPCookieProcessor(CookieJar()))
def post(path,data):
    return client.open(Request(base+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json','Origin':base}))
try:
    raw=dict(id='953c6f26280f84964c6d1ed6ab62d0b1',revision=5,provider='deepseek',vehicle_id='V01',message='费用')
    try:post('/api/assistant/chat',raw)
    except HTTPError as e:assert e.code==400
    else:raise AssertionError('Missing key returned a fabricated reply')
    checks.append('missing credentials produce an actionable error, without a generated substitute')
    calls=[]
    class Transport:
        def open(self,request,timeout):
            calls.append((request,timeout))
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':'provider response'}}],'usage':{'total_tokens':12}}).encode())
    for preset in launch.PROVIDERS:
        secret='sk-contract-test-only-00000000'
        with post('/api/assistant/config',dict(provider=preset['id'],base_url=preset['base_url'],model=preset['model'],api_key=secret)) as response:
            result=json.load(response)
            assert result['saved'] and secret not in json.dumps(result)
            assert 'HttpOnly' in response.headers['Set-Cookie']
        with client.open(base+'/api/assistant/providers') as response:
            status=json.load(response)
        assert secret not in json.dumps(status)
        assert next(p for p in status['providers'] if p['id']==preset['id'])['configured']
        with patch.object(gateway,'build_opener',return_value=Transport()):
            with post('/api/assistant/chat',{**raw,'provider':preset['id']}) as response:answer=json.load(response)
        assert answer['answer']=='provider response' and answer['mode']=='model'
        request,timeout=calls[-1];body=json.loads(request.data)
        assert request.full_url==preset['base_url']+'/chat/completions'
        assert request.get_header('Authorization')=='Bearer '+secret
        assert body['model']==preset['model'] and not body['stream']
        assert 'source_rows' in body['messages'][0]['content'] and '103' in body['messages'][0]['content']
        checks.append(preset['id']+': configuration cookie, secret redaction, Bearer auth, endpoint, context and provider-response passthrough')
    with patch.object(gateway,'build_opener') as transport:
        transport.return_value.open.side_effect=HTTPError('https://api.deepseek.com/v1/chat/completions',401,'Unauthorized',{},None)
        try:post('/api/assistant/chat',raw)
        except HTTPError as error:
            assert error.code==502 and 'API Key' in json.load(error)['error']
        else:raise AssertionError('Unauthorized request did not fail')
    checks.append('provider authentication failures remain errors; no fabricated success')
    report={'checks':checks,'scope':'HTTP/configuration and provider protocol with isolated transport doubles; no live provider credentials used'}
    out=Path(r'D:\桌面文件\logi-reflow-main\outputs\final-upgrade-20261107')
    (out/'real-models-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True))
finally:
    server.shutdown();server.server_close();thread.join()
