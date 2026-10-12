"""Create a local source/evidence package; never includes live data or credentials.

Uses git's tracked+unignored file listing, then applies a stricter local filter.
No commit, push, submission or network. --verify executes offline tests in the
extracted package to verify self-contained delivery (no model calls).
"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from zipfile import ZipFile, ZIP_DEFLATED

ROOT=Path(__file__).resolve().parent
BLOCKED={'data','outputs','.git','node_modules','__pycache__'}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--verify',action='store_true')
    parser.add_argument('--output',type=Path);parser.add_argument('--label',default='CSV-AI-20261011-local')
    parser.add_argument('--tracked-only',action='store_true',help='Package reviewed tracked files while unfinished untracked work stays local')
    args=parser.parse_args()
    listing=['git','ls-files','--cached','-z'] if args.tracked_only else ['git','ls-files','--cached','--others','--exclude-standard','-z']
    raw=subprocess.check_output(listing,cwd=ROOT)
    names=sorted({n.decode('utf-8') for n in raw.split(b'\0') if n})
    files=[]
    for name in names:
        relative=Path(name);source=ROOT/relative
        if not source.is_file() or any(p in BLOCKED for p in relative.parts):continue
        if source.is_symlink() or relative.is_absolute() or '..' in relative.parts:raise ValueError('Unexpected package path')
        if relative.name.startswith('.env') or any(x in relative.name.lower() for x in ('.dpapi','.sqlite','.bak','.log')):continue
        blob=source.read_bytes()
        if re.search(rb'sk-[A-Za-z0-9_-]{24,}',blob):raise ValueError('Credential-like content found; package stopped without printing it')
        files.append(dict(path=relative.as_posix(),size=len(blob),sha256=sha256(blob).hexdigest()))
    output=args.output.resolve() if args.output else ROOT/'outputs/logireflow-csv-ai-checkpoint-20261011.zip'
    output.parent.mkdir(parents=True,exist_ok=True)
    manifest=dict(checkpoint=args.label,files=files,
                  data_kind='constructed data and manual simulation only',
                  excluded='runtime SQLite, credentials, environment files, backups, logs, raw unapproved screenshots, node packages, outputs and git internals',
                  limits='Constructed simulation only; package creation is not competition submission or acceptance of terms; official source verification and submission status are recorded in docs/比赛核对.md; team identity confirmed in README')
    with ZipFile(output,'w',ZIP_DEFLATED) as archive:
        for item in files:archive.write(ROOT/item['path'],item['path'])
        archive.writestr('CHECKPOINT-MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    validation={}
    if args.verify:
        # Keep offline localhost checks out of inherited Windows/system proxies.
        verify_env = os.environ.copy()
        verify_env['NO_PROXY'] = ','.join(filter(None, [verify_env.get('NO_PROXY', ''), '127.0.0.1', 'localhost', '::1']))
        verify_env['no_proxy'] = verify_env['NO_PROXY']
        verify_env['PYTHONUTF8'] = '1'
        with TemporaryDirectory(prefix='logireflow-package-') as tmp:
            with ZipFile(output) as archive:archive.extractall(tmp)
            for item in files:
                assert sha256((Path(tmp)/item['path']).read_bytes()).hexdigest()==item['sha256']
            checks=[['python','-m','unittest','discover','-s','tests','-v'],
                    ['python','reproduce_csv_checkpoint.py'],
                    ['python','fixed_loading_benchmark.py'],
                    ['python','scenario_demo.py']]
            for command in checks:
                result=subprocess.run(command,cwd=tmp,env=verify_env,capture_output=True,text=True,encoding='utf-8',errors='replace')
                assert result.returncode==0,(command,result.stderr[-2000:])
                validation[' '.join(command)]=dict(passed=True,summary=(result.stderr or result.stdout)[-400:])
            validation['all_manifest_hashes_match']=True
    record=dict(archive=str(output),zip_sha256=sha256(output.read_bytes()).hexdigest(),
                bytes=output.stat().st_size,files=len(files),validation=validation)
    audit_path=output.with_suffix('.audit.json') if args.output else ROOT/'outputs/checkpoint-package-audit.json'
    audit_path.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(record,ensure_ascii=False))


if __name__=='__main__':main()
