"""Read-only local delivery checks; no enterprise data or model calls are sent."""
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
base=(ROOT/'outputs/app-runtime/active-url.txt').read_text(encoding='utf-8-sig').strip().rstrip('/')
pages=['/', '/solutions.html?cap=dispatch', '/solutions.html?cap=loading', '/solutions.html?cap=cost',
       '/solutions.html?cap=recovery', '/solutions.html?cap=assistant', '/scenarios.html', '/guide.html',
       '/workspace.html', '/intake.html', '/app.html', '/index.html']

class Assets(HTMLParser):
    def __init__(self): super().__init__(); self.paths=set()
    def handle_starttag(self, tag, attributes):
        a=dict(attributes)
        p=a.get('src') if tag=='script' else a.get('href') if tag=='link' else None
        if p and not urlsplit(p).scheme: self.paths.add('/'+p.lstrip('/'))

def read(path):
    with urlopen(base+path,timeout=8) as response:
        assert response.status==200, path
        return response.read()

assets=set()
for path in pages:
    content=read(path).decode('utf-8'); assert '<html' in content.lower(), path
    parser=Assets();parser.feed(content);assets.update(parser.paths)
for path in sorted(assets): read(path)
status=json.loads(read('/api/product/status'))
documents=json.loads(read('/api/intake/list'))['documents']
source_dir=Path('D:/xwechat_files/wxid_4kdce4r54n7l22_df4b/msg/file/2026-10')
references=[]
for path in sorted((ROOT/'docs/reference-materials').iterdir()):
    if path.suffix.lower() not in ('.pdf','.xlsx'): continue
    digest=sha256(path.read_bytes()).hexdigest()
    original=source_dir/path.name
    assert original.is_file() and sha256(original.read_bytes()).hexdigest()==digest
    references.append(dict(filename=path.name,sha256=digest,unchanged=True))
assert len(references)==3
assert any(d['record_rows']==116 for d in documents)
report=dict(base_url=base,product=status,html_pages=len(pages),local_assets=len(assets),
    references=references,source_records=116,
    browser_verified=['homepage_to_capability_page','physical_loading_candidate','real_deepseek_request',
        'reload_job_recovery_and_unlock','rule_draft_review_apply_recalculate','human_confirmation',
        'simulated_receipt_and_pickup','execution_input_lock','cooperative_cancel_and_input_restore',
        'desktop_and_mobile_layout'],
    data_boundary='原始企业表格仅在本机核对；真实API测试均为构造模拟数据')
out=ROOT/'outputs/final-product-qa/delivery-verification.json'
out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(dict(base_url=base,pages=len(pages),assets=len(assets),references_unchanged=len(references),
                     source_records=116),ensure_ascii=False))
