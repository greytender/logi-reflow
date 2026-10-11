"""Reproducible small-case comparison. Writes evidence, not a claim of enterprise benefit."""
import json
from pathlib import Path
import engine

out=Path(__file__).resolve().parent/'evidence'; out.mkdir(exist_ok=True)
default=engine.run(); (out/'default-result.json').write_text(json.dumps(default,ensure_ascii=False,indent=2),encoding='utf-8')
rows=[]
for vehicle in ('V1','V2','V3'):
    for time in ('08:16','08:25','09:00'):
        r=engine.run(f'{vehicle} 在 {time} 故障')
        rows.append(dict(vehicle=vehicle,time=time,simulated=True,impacted=r['impacted'],
            baseline={**r['baseline']['metrics'],'solve_ms':r['baseline']['solve_ms']},
            recovered={**r['recovered']['metrics'],'solve_ms':r['recovered']['solve_ms']}))
(out/'benchmark.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(dict(cases=len(rows),simulated=True,max_recovery_ms=max(r['recovered']['solve_ms'] for r in rows)),ensure_ascii=False))
