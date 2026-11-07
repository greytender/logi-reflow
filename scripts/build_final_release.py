"""Create a portable ZIP from the versioned final application."""
from pathlib import Path
from hashlib import sha256
import argparse
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def build(destination):
    app = ROOT/'final'
    assert (app/'python/python.exe').is_file(), 'Missing bundled Python'
    assert (app/'demo/application.sqlite3').is_file(), 'Missing demonstration baseline'
    destination.parent.mkdir(parents=True, exist_ok=True)
    files=[]
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for source in sorted(app.rglob('*')):
            if not source.is_file(): continue
            relative=source.relative_to(app)
            if any(x in relative.parts for x in ('__pycache__', '.impeccable', 'data')): continue
            if source.suffix in ('.pyc', '.log', '.bak', '.dpapi') or source.name.startswith('.env'): continue
            archive.write(source, Path('城配续航')/relative)
            files.append(dict(path=relative.as_posix(),sha256=sha256(source.read_bytes()).hexdigest()))
    digest=sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix('.sha256.txt').write_text(digest+'  '+destination.name+'\n',encoding='utf-8')
    destination.with_suffix('.manifest.json').write_text(json.dumps(dict(sha256=digest,files=files),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(zip=str(destination),bytes=destination.stat().st_size,files=len(files),sha256=digest),ensure_ascii=False))
    return destination

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/releases/城配续航-决赛离线演示-Windows-x64.zip')
    build(parser.parse_args().output.resolve())
