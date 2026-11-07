"""Check the extracted release using only its bundled interpreter and CMD launcher."""
from pathlib import Path
from contextlib import closing
from hashlib import sha256
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError
from urllib.parse import urlencode
from html.parser import HTMLParser
import argparse
import json
import os
import re
import socket
import sqlite3
import subprocess
import tempfile
import time
import zipfile

ROOT=Path(__file__).resolve().parents[1]

def check_launchers(archive):
    checks=[]
    with tempfile.TemporaryDirectory(prefix='启动验收 空格 ') as temporary:
        extraction=Path(temporary)
        with zipfile.ZipFile(archive) as package: package.extractall(extraction)
        app=extraction/'城配续航'
        env=dict(os.environ)
        env['PATH']=str(Path(os.environ['SystemRoot'])/'System32')
        for key in list(env):
            if key.startswith(('PYTHON','CONDA','VIRTUAL_ENV')):env.pop(key)
        client=build_opener(ProxyHandler({}))
        launchers=[app/'打开城配续航.cmd',app/'备用启动.cmd',ROOT/'打开城配续航.cmd']
        for index,launcher in enumerate(launchers):
            assert launcher.read_bytes().isascii(), 'Launcher content depends on the Windows code page'
            with socket.socket() as probe:
                probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
            command=f'"{launcher}" --no-browser --port {port} --data-dir "{extraction / ("work data "+str(index))}"'
            log=extraction/f'launcher-{index}.log'
            with log.open('w',encoding='utf-8') as output:
                process=subprocess.Popen(f'"{os.environ["COMSPEC"]}" /d /s /c "{command}"',cwd=extraction,env=env,stdout=output,stderr=subprocess.STDOUT)
                try:
                    deadline=time.monotonic()+30
                    while True:
                        try:
                            with client.open(f'http://127.0.0.1:{port}/',timeout=2) as response:
                                assert '城配续航' in response.read().decode('utf-8')
                            break
                        except OSError:
                            if process.poll() is not None or time.monotonic()>deadline:
                                raise AssertionError(log.read_text(encoding='utf-8',errors='replace'))
                            time.sleep(.2)
                    with client.open(f'http://127.0.0.1:{port}/api/multiwarehouse/tasks',timeout=15) as response:
                        assert json.load(response)['tasks'][0]['orders']==103
                    checks.append(f'{launcher.name}: clean CMD startup, bundled Python, 103-store batch, ASCII-safe command content')
                finally:
                    subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True)
                    process.wait(timeout=15)
    report=dict(passed=True,sha256=sha256(archive.read_bytes()).hexdigest(),checks=checks)
    (ROOT/'outputs/releases/launcher-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True,indent=2))

class References(HTMLParser):
    def __init__(self): super().__init__(); self.urls=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag in ('script','img') and attrs.get('src'): self.urls.append(attrs['src'])
        if tag=='link' and attrs.get('rel') in ('stylesheet','icon'): self.urls.append(attrs.get('href',''))

def check(archive):
    checks=[]
    with tempfile.TemporaryDirectory(prefix='城配验收 空格 ') as temporary:
        extraction=Path(temporary)
        with zipfile.ZipFile(archive) as package:
            assert package.testzip() is None
            package.extractall(extraction)
        app=extraction/'城配续航'
        baseline=sha256((app/'demo/application.sqlite3').read_bytes()).hexdigest()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        env=dict(os.environ)
        env['PATH']=str(Path(os.environ['SystemRoot'])/'System32')
        for key in list(env):
            if key.startswith(('PYTHON','CONDA','VIRTUAL_ENV')): env.pop(key)
        log=extraction/'server.log'
        launcher=app/'打开城配续航.cmd'
        command=f'"{launcher}" --no-browser --port {port}'
        with log.open('w',encoding='utf-8') as output:
            process=subprocess.Popen(f'"{os.environ["COMSPEC"]}" /d /s /c "{command}"',cwd=extraction,env=env,stdout=output,stderr=subprocess.STDOUT)
            base=f'http://127.0.0.1:{port}'
            client=build_opener(ProxyHandler({}))
            def get(path):
                with client.open(base+path,timeout=120) as response: return response.read()
            def post(path,body):
                request=Request(base+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Origin':base})
                with client.open(request,timeout=180) as response:return json.load(response)
            try:
                deadline=time.monotonic()+30
                while True:
                    try: get('/');break
                    except OSError:
                        if process.poll() is not None or time.monotonic()>deadline:
                            raise AssertionError('CMD startup failed: '+log.read_text(encoding='utf-8',errors='replace'))
                        time.sleep(.2)
                checks.append('ZIP extracted to a new Chinese/space path; CMD starts with system Python removed from PATH')
                for page in sorted((app/'web').glob('*.html')):
                    parser=References();parser.feed(get('/'+page.name).decode('utf-8'))
                    for url in parser.urls:
                        assert not url.startswith(('http:','https:','//')), (page.name,url)
                        if url and not url.startswith(('data:','#')):get('/'+url.lstrip('/'))
                for asset in (app/'web').rglob('*'):
                    if asset.is_file() and asset.suffix in {'.html','.css','.js','.svg','.png','.jpg','.webp','.woff','.woff2','.ttf','.ico'}:
                        get('/'+asset.relative_to(app/'web').as_posix())
                checks.append('All HTML pages and every bundled web resource return HTTP 200, without remote page dependencies')
                tid='953c6f26280f84964c6d1ed6ab62d0b1'
                current=json.loads(get('/api/multiwarehouse/task?id='+tid))
                task=current['task']; plan=next(p for p in task['plans'] if p['id']==current['state']['current_plan_id'])
                assert len(task['data']['orders'])==103 and len(task['intake_source']['rows'])==116
                assert plan['summary']['optimized']['served']==103 and plan['summary']['optimized']['trips']==16 and plan['audit']['passed']
                checks.append('Seed initialized automatically: 116 orders, 103 stores, 16 trips, complete audit')
                assert json.loads(get('/api/multiwarehouse/sample?operational=1'))['data']
                checks.append('Small operational example loads with all required sample files')
                for wid,group in plan['warehouses'].items():
                    for vid,route in group['optimized']['routes'].items():
                        if not route['sequence']:continue
                        query=urlencode(dict(id=tid,warehouse_id=wid,vehicle_id=vid,plan_id=plan['id']))
                        for operation in ('spatial','journey'):json.loads(get('/api/multiwarehouse/'+operation+'?'+query))
                checks.append('All 16 vehicle spatial and journey APIs load from the current plan')
                query=urlencode(dict(id=tid,revision=task['revision']))
                assert len(get('/api/multiwarehouse/export?'+query+'&format=csv'))>100
                assert json.loads(get('/api/multiwarehouse/export?'+query))
                imported=post('/api/intake/create',dict(central=True))
                assert imported
                checks.append('Confirmed CSV/JSON exports and original Excel import work in the clean extracted copy')
                for path in ('/runtime/server.py','/demo/application.sqlite3','/python/python.exe','/launch.py','/../README.md'):
                    try:get(path)
                    except HTTPError as error:assert error.code==404
                    else:raise AssertionError('Private file exposed: '+path)
                checks.append('Runtime, Python executable, seed databases and traversal paths are not downloadable')
                assert sha256((app/'demo/application.sqlite3').read_bytes()).hexdigest()==baseline
                checks.append('Baseline stays unchanged after startup and operations')
            finally:
                subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True)
                process.wait(timeout=15)
    report=dict(passed=True,archive=str(archive),checks=checks)
    output=ROOT/'outputs/releases/portable-check.json';output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--archive',type=Path)
    parser.add_argument('--launchers-only',action='store_true')
    args=parser.parse_args()
    if args.archive is None:
        from build_final_release import build
        args.archive=ROOT/'outputs/releases/城配续航-决赛离线演示-Windows-x64.zip'
        build(args.archive)
    if args.launchers_only:check_launchers(args.archive.resolve())
    else:
        check(args.archive.resolve())
        check_launchers(args.archive.resolve())
