"""Local temporary video sink. No application database or remote upload."""
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
import json,secrets,time
ROOT=Path(__file__).resolve().parents[1]/'outputs/video-work'
TOKEN=secrets.token_urlsafe(32)
ROOT.mkdir(parents=True,exist_ok=True)
(ROOT/'sink-config.json').write_text(json.dumps({'port':18780,'token':TOKEN}),encoding='utf-8')
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,status,body=b''):
        self.send_response(status)
        self.send_header('Access-Control-Allow-Origin','http://127.0.0.1:18779')
        self.send_header('Access-Control-Allow-Headers','Content-Type,X-Recording-Token')
        self.send_header('Access-Control-Allow-Methods','POST,OPTIONS')
        self.send_header('Content-Type','application/json')
        self.end_headers();self.wfile.write(body)
    def do_OPTIONS(self):self.reply(204)
    def do_POST(self):
        length=int(self.headers.get('Content-Length','0'))
        if self.path not in ('/probe','/test','/full') or self.headers.get('Origin')!='http://127.0.0.1:18779' or not secrets.compare_digest(self.headers.get('X-Recording-Token',''),TOKEN) or not 0<length<=512*1024*1024:
            self.reply(403);return
        target=ROOT/(self.path[1:]+'-'+str(time.time_ns())+'.webm')
        remaining=length
        with target.open('xb') as output:
            while remaining:
                chunk=self.rfile.read(min(1024*1024,remaining))
                if not chunk:raise ConnectionError('Incomplete local video upload')
                output.write(chunk);remaining-=len(chunk)
        self.reply(200,json.dumps({'path':str(target),'bytes':length}).encode('utf-8'))
HTTPServer(('127.0.0.1',18780),Handler).serve_forever()
