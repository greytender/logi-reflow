"""Keep official source read-only; default reproduction uses synthetic bytes."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import intake

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/intake-reproduction')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    raw=args.source.read_bytes() if args.source else intake.sample_xlsx()
    d=intake.parse_xlsx(raw,args.source.name if args.source else 'synthetic.xlsx')
    if args.source:
        assert sha256(args.source.read_bytes()).hexdigest()==d['sha256']
    else:
        assert d['audit']['record_rows']==3 and d['audit']['missing_sequence']==[2]
        s=intake.IntakeStore(args.output/'intake.sqlite3');saved=s.create('构造原表核对','synthetic.xlsx',raw)
        assert intake.IntakeStore(args.output/'intake.sqlite3').read(saved['id'])==saved
        (args.output/'synthetic.xlsx').write_bytes(raw)
    # Do not copy official customer names/addresses or source bytes to evidence.
    record={k:d[k] for k in ('filename','sha256','size_bytes','audit')}
    record['source_kind']='supplied_local_source_read_only' if args.source else 'synthetic'
    record['sheets']=[dict(name=s['name'],source_rows=len(s['rows']),merges=len(s['merges'])) for s in d['sheets']]
    (args.output/'verification.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(rows=d['audit']['record_rows'],profile=d['audit']['profile'],
        unique_names=d['audit'].get('unique_store_name_strings'),blank_cold_rows=d['audit'].get('blank_cold_rows'),
        missing_sequence=d['audit'].get('missing_sequence'),can_solve=False),ensure_ascii=False))
if __name__=='__main__':main()
