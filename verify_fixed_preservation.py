"""Read-only logical database hash; explicit backup mode uses SQLite backup."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parent

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--backup',action='store_true');args=parser.parse_args()
    dest=ROOT/'outputs/fixed-loading-backup-20261012';dest.mkdir(exist_ok=True)
    with sqlite3.connect(f'file:{(ROOT/"data/sessions.sqlite3").as_posix()}?mode=ro',uri=True) as db:
        result={}
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            rows=db.execute('SELECT * FROM "'+name+'" ORDER BY rowid').fetchall()
            result[name]=dict(rows=len(rows),sha256=hashlib.sha256(json.dumps(rows,ensure_ascii=False).encode()).hexdigest())
        if args.backup:
            with sqlite3.connect(dest/'sessions.sqlite3') as target:db.backup(target)
            with ZipFile(ROOT/'outputs/logireflow-loading-checkpoint-20261011.zip') as archive:
                for name in ('continuous.py','matrix_dispatch.py','web/loading.html','web/loading.js','web/continuous.js','README.md'):
                    target=dest/'checkpoint-source'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.read(name))
            (dest/'database-before.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        else:
            before=json.loads((dest/'database-before.json').read_text(encoding='utf-8'))
            assert before==result,'Runtime database changed'
            output=dict(passed=True,before=before,after=result)
            (ROOT/'evidence/fixed-loading-preservation.json').write_text(json.dumps(output,indent=2),encoding='utf-8')
    print(json.dumps(result if args.backup else output))

if __name__=='__main__':main()
